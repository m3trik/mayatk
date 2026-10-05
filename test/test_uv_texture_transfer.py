# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.uv_utils.texture_transfer (TextureTransfer -- UV-to-UV remap).

The engine's arithmetic is pinned in pythontk's ``test_uv_transfer``; these
cover what the Maya adapter adds: the triangle correspondence between two UV
sets / two meshes, material discovery (maps vs constants, per face), output
naming, normal-map convention sniffing, and assign-on-finish.
"""

import os
import unittest

import numpy as np
import maya.cmds as cmds
import pythontk as ptk
from PIL import Image

from base_test import MayaTkTestCase, QuickTestCase
from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap
from mayatk.uv_utils.texture_transfer import TextureTransfer


def _checker(size=64, cell=8):
    """RGB checker with a distinct red corner so orientation is testable."""
    img = np.zeros((size, size, 3), np.uint8)
    cells = (np.arange(size)[:, None] // cell + np.arange(size)[None, :] // cell) % 2
    img[cells == 0] = 220
    img[cells == 1] = 40
    img[:cell, :cell] = (255, 0, 0)  # top-left (u=0, v=1) red
    return img


class TestTextureTransfer(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        # Scoped, not detached: nothing reads these after the test, and a
        # detached store's cleanup() is a no-op (1,063 leftovers measured).
        self._artifacts = ptk.TempArtifacts("uv_transfer_test", policy="scoped")
        self.addCleanup(self._artifacts.cleanup)
        self.tmp = self._artifacts.dir_path()
        self.checker_path = os.path.join(self.tmp, "src_checker.png").replace("\\", "/")
        Image.fromarray(_checker()).save(self.checker_path)
        self.out_dir = os.path.join(self.tmp, "out").replace("\\", "/")

    # ------------------------------------------------------------ helpers
    def _plane(self, name="xferPlane", sx=2, sy=2):
        plane = cmds.polyPlane(name=name, sx=sx, sy=sy, w=1, h=1, ch=False)[0]
        return plane

    def _lambert(self, name, texture=None, color=None):
        mat = cmds.shadingNode("lambert", asShader=True, name=name)
        sg = cmds.sets(
            name=f"{name}SG", renderable=True, noSurfaceShader=True, empty=True
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        if texture:
            f = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
            cmds.setAttr(f"{f}.fileTextureName", texture, type="string")
            cmds.connectAttr(f"{f}.outColor", f"{mat}.color")
        if color:
            cmds.setAttr(f"{mat}.color", *color, type="double3")
        return mat, sg

    def _rotate_uv_set_copy(self, obj, new_set="map2", angle=90):
        cmds.polyUVSet(obj, copy=True, uvSet="map1", newUVSet=new_set)
        cmds.polyUVSet(obj, currentUVSet=True, uvSet=new_set)
        cmds.polyEditUV(
            f"{obj}.map[*]",
            uvSetName=new_set,
            rotation=True,
            angle=angle,
            pivotU=0.5,
            pivotV=0.5,
        )
        cmds.polyUVSet(obj, currentUVSet=True, uvSet="map1")

    @staticmethod
    def _load(path):
        return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)

    # -------------------------------------------------------------- tests
    def test_uv_set_to_uv_set_rotation(self):
        plane = self._plane()
        mat, sg = self._lambert("xferMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        out = TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=64,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
        )
        self.assertIn(mat, out)
        self.assertIn("baseColor", out[mat])
        path = out[mat]["baseColor"]
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(path.endswith("xferMat_BaseColor.png"))
        got = self._load(path)
        src = _checker().astype(np.float32)
        # map2 is map1 rotated 90 CCW about the tile center: the stored image
        # rotates CCW with it (red corner moves top-left -> bottom-left).
        self.assertLess(np.abs(got - np.rot90(src, 1)).max(), 2.0)

    def test_mesh_to_mesh_pairs_by_name_and_mirrors(self):
        src = self._plane("partA")
        mat, sg = self._lambert("srcMat", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="partA_tgt")[0]
        grp = cmds.group(tgt, name="targets")
        # Same leaf name as the source, in another group: pairing is by leaf.
        tgt = cmds.rename(f"|{grp}|{tgt}", "partA")
        cmds.polyEditUV(f"{tgt}.map[*]", scaleU=-1, pivotU=0.5)  # mirror in U
        tmat, tsg = self._lambert("tgtMat")
        cmds.sets(tgt, e=True, forceElement=tsg)

        out = TextureTransfer().transfer(
            tgt, f"|{src}", size=64, supersample=1, padding=0, output_dir=self.out_dir
        )
        got = self._load(out[tmat]["baseColor"])
        self.assertLess(np.abs(got - _checker().astype(np.float32)[:, ::-1]).max(), 2.0)

    def test_topology_mismatch_raises(self):
        a = self._plane("topoA", 2, 2)
        b = self._plane("topoB", 3, 3)
        mat, sg = self._lambert("topoMat", texture=self.checker_path)
        cmds.sets(a, e=True, forceElement=sg)
        cmds.sets(b, e=True, forceElement=sg)
        with self.assertRaises(ValueError):
            TextureTransfer().transfer(b, a, size=16, output_dir=self.out_dir)

    def test_auto_uv_sets_read_the_bound_set_and_write_the_other(self):
        # Textures bound (uvLink) to map1; map2 is the new layout. With neither
        # set named, Auto must read map1 and write map2 -- even when map2 is
        # the CURRENT set, which is the state a user leaves the mesh in after
        # editing the new layout.
        plane = self._plane("autoPlane")
        mat, sg = self._lambert("autoMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)
        cmds.polyUVSet(plane, currentUVSet=True, uvSet="map2")
        out = TextureTransfer().transfer(
            plane, size=64, supersample=1, padding=0, output_dir=self.out_dir
        )
        got = self._load(out[mat]["baseColor"])
        self.assertLess(
            np.abs(got - np.rot90(_checker().astype(np.float32), 1)).max(), 2.0
        )

    def test_same_uv_set_twice_raises(self):
        plane = self._plane()
        with self.assertRaises(ValueError):
            TextureTransfer().transfer(
                plane,
                source_uv_set="map1",
                target_uv_set="map1",
                output_dir=self.out_dir,
            )

    def test_a_source_that_wears_nothing_is_nothing_to_transfer(self):
        """A source whose shading group lost its shader wears nothing: the
        run raised an ``IndexError`` out of the triangle lookup, which the UV
        panel -- catching ``ValueError`` -- let escape as a traceback."""
        src = self._plane("nakedSrc")
        tgt = cmds.duplicate(src, name="nakedTgt")[0]
        gone, sg = self._lambert("nakedSrcMat")
        cmds.sets(src, e=True, forceElement=sg)
        cmds.delete(gone)
        _tmat, tsg = self._lambert("nakedTgtMat")
        cmds.sets(tgt, e=True, forceElement=tsg)
        with self.assertRaisesRegex(ValueError, "nothing to transfer"):
            TextureTransfer().transfer(tgt, src, size=16, output_dir=self.out_dir)

    def test_consolidation_uses_constant_for_unmapped_source(self):
        # Two source materials on one mesh: left column textured, right column
        # a plain colour. Target has ONE material -> one atlas, the right half
        # filled with the constant.
        plane = self._plane("consol", 2, 1)
        m0, sg0 = self._lambert("texMat", texture=self.checker_path)
        m1, sg1 = self._lambert("flatMat", color=(0.0, 0.0, 1.0))
        cmds.sets(f"{plane}.f[0]", e=True, forceElement=sg0)
        cmds.sets(f"{plane}.f[1]", e=True, forceElement=sg1)
        tgt = cmds.duplicate(plane, name="consol_tgt")[0]
        tmat, tsg = self._lambert("atlasMat")
        cmds.sets(tgt, e=True, forceElement=tsg)

        out = TextureTransfer().transfer(
            tgt, plane, size=32, supersample=1, padding=0, output_dir=self.out_dir
        )
        got = self._load(out[tmat]["baseColor"])
        # Right half (u > 0.5) is the flat material: pure blue.
        self.assertTrue(np.allclose(got[:, 20:], (0, 0, 255), atol=1.5))
        # Left half carries the checker (both 220 and 40 present).
        self.assertTrue((got[:, :12, 0] > 200).any() and (got[:, :12, 0] < 60).any())

    def _combined(self):
        """Two textured sources and a target combined from them, re-laid-out.

        Combined in REVERSE selection order (B first) and named like source A
        -- the shape that used to fail with "cannot pair 0 target(s) with 1
        source(s)": the name claimed A and stranded B. The target's layout
        puts B's face on the right half and A's on the left.
        """
        a = self._plane("combA", 1, 1)
        b = self._plane("combB", 1, 1)
        cmds.move(2, 0, 0, b)
        _, sg_a = self._lambert("combTexMat", texture=self.checker_path)
        _, sg_b = self._lambert("combFlatMat", color=(0.0, 0.0, 1.0))
        cmds.sets(a, e=True, forceElement=sg_a)
        cmds.sets(b, e=True, forceElement=sg_b)
        c = cmds.polyUnite(cmds.duplicate([b, a]), ch=False, name="combined")[0]
        c = cmds.rename(cmds.parent(c, cmds.group(empty=True, name="out"))[0], a)
        c = cmds.ls(c, long=True)[0]
        for face, pivot in ((0, 1.0), (1, 0.0)):  # f[0] = B -> right, f[1] = A -> left
            uvs = cmds.polyListComponentConversion(f"{c}.f[{face}]", toUV=True)
            cmds.polyEditUV(uvs, scaleU=0.5, pivotU=pivot)
        tmat, tsg = self._lambert("combAtlasMat")
        cmds.sets(c, e=True, forceElement=tsg)
        return f"|{a}", f"|{b}", c, tmat

    def test_a_combined_target_reads_every_source_in_combine_order(self):
        a, b, c, tmat = self._combined()
        self.assertEqual(TextureTransfer.pair_sources([c], [a, b]), {c: (b, a)})
        self.assertEqual(TextureTransfer.find_combined([a, c, b]), (c, (b, a)))
        self.assertIsNone(TextureTransfer.find_combined([a, b]))
        out = TextureTransfer().transfer(
            c, [a, b], size=32, supersample=1, padding=0, output_dir=self.out_dir
        )
        got = self._load(out[tmat]["baseColor"])
        # Right half: B's flat blue. Left half: A's checker (both tones).
        self.assertTrue(np.allclose(got[:, 20:], (0, 0, 255), atol=1.5))
        self.assertTrue((got[:, :12, 0] > 200).any() and (got[:, :12, 0] < 60).any())

    def test_an_output_name_shared_with_a_mesh_never_touches_the_mesh(self):
        """The re-run cleanup cleared ANY node named like the output: an Output
        Name equal to a mesh's name (tentacle derives one from the mesh itself
        on the same-mesh source) deleted that mesh, or, with two meshes of that
        name, raised "More than one object matches name"."""
        plane = self._plane("namesake")
        _, sg = self._lambert("namesakeMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)
        TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            output_dir=self.out_dir,
            output_name="namesake",
            assign=True,
        )
        self.assertTrue(cmds.objExists("|namesake"), "the mesh was deleted")
        shape = cmds.listRelatives("|namesake", shapes=True, fullPath=True)[0]
        sg = cmds.listConnections(shape, type="shadingEngine")[0]
        new_mat = cmds.listConnections(f"{sg}.surfaceShader")[0]
        self.assertTrue(new_mat.startswith("namesake"), new_mat)
        self.assertNotEqual(new_mat, "namesakeMat")

    def test_a_re_run_beside_a_namesake_node_replaces_its_own_result(self):
        """Beside a mesh holding the Output Name Maya calls the result
        ``<name>1``, which a re-run's cleanup -- looking for ``<name>`` --
        never found: every run stacked ``<name>2``, ``<name>3``... and left
        the last one's shading group behind. A result is found by its stamp,
        whatever Maya called it."""
        plane = self._plane("namesake")
        original, sg = self._lambert("namesakeMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)
        kwargs = dict(
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="namesake",
            assign=True,
        )
        for _run in range(3):
            TextureTransfer().transfer(plane, **kwargs)
        results = [m for m in cmds.ls("namesake*", type="lambert") if m != original]
        self.assertEqual(len(results), 1, results)
        self.assertEqual(TextureTransfer().face_materials(plane)[0], results)
        groups = [g for g in cmds.ls("namesake*", type="shadingEngine") if g != sg]
        self.assertEqual(groups, [f"{results[0]}SG"])

    def test_one_source_feeds_every_target(self):
        """First Selected onto several copies raised "cannot pair 2 target(s)
        with 1 source(s)": the one-to-one name pairing needs equal counts."""
        src = f"|{self._plane('feedSrc')}"
        _, sg = self._lambert("feedMat", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        t1 = f"|{cmds.duplicate(src, name='feedT1')[0]}"
        t2 = f"|{cmds.duplicate(src, name='feedT2')[0]}"
        tmat, tsg = self._lambert("feedTgtMat")
        cmds.sets([t1, t2], e=True, forceElement=tsg)
        self.assertEqual(
            TextureTransfer.pair_sources([t1, t2], [src]), {t1: src, t2: src}
        )
        out = TextureTransfer().transfer(
            [t1, t2], src, size=16, supersample=1, padding=0, output_dir=self.out_dir
        )
        self.assertIn("baseColor", out[tmat])

    def test_a_combined_target_no_source_set_builds_raises(self):
        a, _, c, _ = self._combined()
        other = self._plane("combOther", 2, 2)
        with self.assertRaisesRegex(ValueError, "no combination"):
            TextureTransfer.pair_sources([c], [a, f"|{other}"])

    def test_lightmaps_refuse_a_combined_target_by_name(self):
        a, b, c, _ = self._combined()
        with self.assertRaisesRegex(ValueError, "one mesh to one mesh"):
            LightmapRecords.transfer_lightmaps(c, [a, b])

    def test_assign_creates_copy_material_and_leaves_original(self):
        plane = self._plane("assignPlane")
        mat, sg = self._lambert("assignMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)
        TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            output_dir=self.out_dir,
            assign=True,
        )
        self.assertTrue(cmds.objExists("assignMat_TRANSFER"))
        new_file = cmds.listConnections("assignMat_TRANSFER.color", type="file")
        self.assertTrue(new_file)
        self.assertIn(
            "assignMat_BaseColor", cmds.getAttr(f"{new_file[0]}.fileTextureName")
        )
        # Original still wired to the source texture.
        self.assertEqual(
            cmds.getAttr(
                f"{cmds.listConnections('assignMat.color', type='file')[0]}.fileTextureName"
            ),
            self.checker_path,
        )
        # The plane now wears the copy.
        shape = cmds.listRelatives(plane, shapes=True, fullPath=True)[0]
        self.assertIn(
            "assignMat_TRANSFERSG", cmds.listConnections(shape, type="shadingEngine")
        )

    def test_assign_onto_maya_default_shader(self):
        """A target wearing Maya's own default shader still gets assigned.

        ``standardSurface1`` / ``lambert1`` are INTERNAL nodes: ``duplicate``
        refuses them outright, which used to abort the whole run after every
        map had already been written. Geometry with nothing assigned wears
        exactly that shader, so it is an ordinary transfer target.
        """
        src = self._plane("defaultSrc")
        mat, sg = self._lambert("defaultSrcMat", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="defaultTgt")[0]
        # Back to the default shader -- what unassigned geometry wears.
        cmds.sets(tgt, e=True, forceElement="initialShadingGroup")
        default_mat = cmds.listConnections(
            "initialShadingGroup.surfaceShader", source=True
        )[0]
        cmds.polyEditUV(f"{tgt}.map[*]", scaleU=-1, pivotU=0.5)  # mirror in U

        TextureTransfer().transfer(
            tgt,
            src,
            size=16,
            supersample=1,
            output_dir=self.out_dir,
            output_name="hero_default",
            assign=True,
        )
        self.assertTrue(cmds.objExists("hero_default"))
        # Same shader type as the default it was modelled on, and that default
        # is left alone (it is the scene's, not this run's, to modify).
        self.assertEqual(cmds.nodeType("hero_default"), cmds.nodeType(default_mat))
        self.assertTrue(cmds.objExists(default_mat))
        # Wired to the transferred maps, not merely created: a shader built
        # from scratch is only useful if the manifest restores onto it.
        wired = cmds.listConnections("hero_default", type="file") or []
        self.assertTrue(wired, "no transferred map wired to the new material")
        shape = cmds.listRelatives(tgt, shapes=True, fullPath=True)[0]
        self.assertIn(
            "hero_defaultSG", cmds.listConnections(shape, type="shadingEngine")
        )

    def test_one_transfer_material_per_shared_uv_set(self):
        # Two materials on ONE mesh / one target set = one atlas: one output
        # named after the set and ONE <set>_TRANSFER material on every face.
        plane = self._plane("sharedPlane", 2, 1)
        m0, sg0 = self._lambert("leftMat", texture=self.checker_path)
        m1, sg1 = self._lambert("rightMat", texture=self.checker_path)
        cmds.sets(f"{plane}.f[0]", e=True, forceElement=sg0)
        cmds.sets(f"{plane}.f[1]", e=True, forceElement=sg1)
        self._rotate_uv_set_copy(plane, "map2", 90)
        out = TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=32,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            assign=True,
        )
        self.assertEqual(list(out), ["map2"])
        self.assertTrue(out["map2"]["baseColor"].endswith("map2_BaseColor.png"))
        self.assertTrue(cmds.objExists("map2_TRANSFER"))
        from mayatk.mat_utils._mat_utils import MatUtils

        # Every face wears the one new material (stale empty objectGroup
        # connections may linger on the shape; membership is the truth).
        assigned = MatUtils.get_shading_assignments(plane)
        owned = {sg for sg, faces in assigned.items() if faces is None or faces}
        self.assertEqual(owned, {"map2_TRANSFERSG"})

    def _half_layout(self, name, side, uv_set=None):
        """A target plane laid out in one HALF of 0-1 (``side`` 0 = left, 1 =
        right), beside a full-square source of the same topology wearing the
        checker; *uv_set* renames the target's set."""
        src = self._plane(f"{name}_src")
        _m, sg = self._lambert(f"{name}_srcMat", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name=name)[0]
        cmds.polyEditUV(f"{tgt}.map[*]", scaleU=0.5, pivotU=0.0)
        if side:
            cmds.polyEditUV(f"{tgt}.map[*]", u=0.5, v=0.0)
        if uv_set:
            cmds.polyUVSet(tgt, rename=True, uvSet="map1", newUVSet=uv_set)
        return src, tgt

    def test_one_layout_under_two_set_names_is_one_material(self):
        """A production table whose parts came in through Maya (``map1``) and
        an FBX (``UVChannel_1``) shares ONE combined layout; the transfer
        split it into two materials by set name (2026-10-03)."""
        src_a, tgt_a = self._half_layout("tableTop", 0)
        src_b, tgt_b = self._half_layout("tableLegs", 1, uv_set="UVChannel_1")
        tmat, tsg = self._lambert("tableOld")
        cmds.sets([tgt_a, tgt_b], e=True, forceElement=tsg)

        out = TextureTransfer().transfer(
            [tgt_a, tgt_b],
            [src_a, src_b],
            size=32,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="Table",
            assign=True,
        )
        self.assertEqual(len(out), 1, out)
        self.assertTrue(
            out[next(iter(out))]["baseColor"].endswith("Table_BaseColor.png")
        )
        self.assertEqual(TextureTransfer().face_materials(tgt_a)[0], ["Table"])
        self.assertEqual(TextureTransfer().face_materials(tgt_b)[0], ["Table"])

    def test_a_target_whose_material_is_gone_is_still_transferred(self):
        """What a target wears says nothing about where its texels go. The
        production table's shading group had lost its shader, and the transfer
        refused the whole run: "nothing to transfer"."""
        src = self._plane("bareSrc")
        _m, sg = self._lambert("bareSrcMat", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="bareTgt")[0]
        tmat, tsg = self._lambert("bareTgtMat")
        cmds.sets(tgt, e=True, forceElement=tsg)
        cmds.delete(tmat)  # the shading group stays, wearing no shader
        self.assertEqual(TextureTransfer().face_materials(tgt)[0], [])

        out = TextureTransfer().transfer(
            tgt,
            src,
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="bare",
            assign=True,
        )
        self.assertEqual(len(out), 1, out)
        self.assertEqual(TextureTransfer().face_materials(tgt)[0], ["bare"])

    def test_a_shading_group_left_without_its_shader_is_cleared(self):
        """The production table sat in ``SolderingTable_MATSG`` after its
        ``SolderingTable_MAT`` was deleted. Re-run under that name, the result
        came back as ``SolderingTable_MATSG1`` beside the empty husk."""
        src = self._plane("husk_src")
        _m, sg = self._lambert("huskSrcMat", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="husk_tgt")[0]
        old, _old_sg = self._lambert("husk")  # its SG is "huskSG"
        cmds.sets(tgt, e=True, forceElement="huskSG")
        cmds.delete(old)

        TextureTransfer().transfer(
            tgt,
            src,
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="husk",
            assign=True,
        )
        self.assertEqual(cmds.ls("huskSG*", type="shadingEngine"), ["huskSG"])
        self.assertEqual(TextureTransfer().face_materials(tgt)[0], ["husk"])

    def test_a_moved_target_transfers_without_a_warning(self):
        """A material map is read through the UV layouts alone, so where the
        target stands is no concern of it -- a normal map's XY included. The
        transfer warned that normal maps were "only exact for coincident
        geometry", which read as a bake hiding inside a transfer."""
        src = self._plane("movedSrc")
        _m, sg = self._lambert("movedSrcMat", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="movedTgt")[0]
        cmds.move(5, 0, 0, tgt)
        cmds.polyMoveVertex(f"{tgt}.vtx[4]", ty=0.3)  # and reshaped
        tt = TextureTransfer()
        with self.assertNoLogs(tt.logger, "WARNING"):
            tt.transfer(
                tgt, src, size=16, supersample=1, padding=0, output_dir=self.out_dir
            )

    def test_assigning_one_layout_never_strips_another(self):
        """Two layouts kept apart, where the first output's name is the
        material the second's target wears: clearing it before the second
        resolved its faces left that target wearing nothing at all."""
        srcs = [self._plane("stripA_src"), self._plane("stripB_src")]
        _ms, ssg = self._lambert("stripSrcMat", texture=self.checker_path)
        cmds.sets(srcs, e=True, forceElement=ssg)
        a = cmds.duplicate(srcs[0], name="stripA")[0]
        b = cmds.duplicate(srcs[1], name="stripB")[0]
        _ma, sga = self._lambert("deskMat")
        _mb, sgb = self._lambert("Table_deskMat_MAT")
        cmds.sets(a, e=True, forceElement=sga)
        cmds.sets(b, e=True, forceElement=sgb)

        TextureTransfer().transfer(
            [a, b],
            srcs,
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="Table",
            assign=True,
            assign_suffix="_MAT",
        )
        for obj in (a, b):
            mats = TextureTransfer().face_materials(obj)[0]
            self.assertEqual(len(mats), 1, (obj, mats))
            self.assertTrue(mats[0].startswith("Table_"), (obj, mats))

    def test_overlapping_atlases_on_one_set_name_stay_apart(self):
        # Two meshes, each filling 0-1 under its own material (the
        # TURRETS/WIRES shape): same set name, overlapping islands -> two outputs.
        a = self._plane("atlasA")
        b = self._plane("atlasB")
        ma, sga = self._lambert("matA", texture=self.checker_path)
        mb, sgb = self._lambert("matB", texture=self.checker_path)
        cmds.sets(a, e=True, forceElement=sga)
        cmds.sets(b, e=True, forceElement=sgb)
        srcs = [
            cmds.duplicate(a, name="atlasA_src")[0],
            cmds.duplicate(b, name="atlasB_src")[0],
        ]
        for s_, sg in zip(srcs, (sga, sgb)):
            cmds.sets(s_, e=True, forceElement=sg)
        out = TextureTransfer().transfer(
            [a, b], srcs, size=16, supersample=1, padding=0, output_dir=self.out_dir
        )
        self.assertEqual(set(out), {"matA", "matB"})

    def test_normal_map_is_reencoded_for_rotated_set(self):
        # Flat +X-tilted OpenGL normal map; after a 90 CCW rotation of the
        # island the tilt must read as +Y.
        n = np.empty((16, 16, 3), np.uint8)
        n[:] = (int(round((0.6 + 1) * 127.5)), 128, int(round((0.8 + 1) * 127.5)))
        npath = os.path.join(self.tmp, "src_Normal_OpenGL.png").replace("\\", "/")
        Image.fromarray(n).save(npath)
        plane = self._plane("nrmPlane")
        mat = cmds.shadingNode("standardSurface", asShader=True, name="nrmMat")
        sg = cmds.sets(
            name="nrmMatSG", renderable=True, noSurfaceShader=True, empty=True
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        f = cmds.shadingNode("file", asTexture=True, name="nrmFile")
        cmds.setAttr(f"{f}.fileTextureName", npath, type="string")
        cmds.connectAttr(f"{f}.outColor", f"{mat}.normalCamera")
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        out = TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
        )
        got = self._load(out[mat]["normal"]) / 255.0 * 2.0 - 1.0
        self.assertTrue(np.allclose(got[..., 0], 0.0, atol=0.02))
        self.assertTrue(np.allclose(got[..., 1], 0.6, atol=0.02))
        self.assertTrue(np.allclose(got[..., 2], 0.8, atol=0.02))

    # ------------------------------------------------ explicit output name
    def test_output_name_names_the_maps(self):
        """The default names each output after the layout it came from, which
        is right for a re-bake in place and wrong for a deliverable."""
        plane = self._plane("namedPlane")
        mat, sg = self._lambert("namedMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        out = TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="hero_atlas",
        )
        path = out[mat]["baseColor"]
        self.assertTrue(
            os.path.basename(path).startswith("hero_atlas_"), os.path.basename(path)
        )
        self.assertNotIn("namedMat", os.path.basename(path))

    def test_output_name_names_the_assigned_material_without_a_suffix(self):
        """The user named the material, so nothing is appended to it -- the
        ``_TRANSFER`` suffix exists only for the layout-derived default."""
        plane = self._plane("assignPlane")
        mat, sg = self._lambert("assignMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="hero_atlas",
            assign=True,
        )
        self.assertTrue(cmds.objExists("hero_atlas"))
        self.assertFalse(cmds.objExists("hero_atlas_TRANSFER"))
        # The original is never modified.
        self.assertTrue(cmds.objExists(mat))

    def test_assign_affix_names_the_material_and_not_the_maps(self):
        """The affix is the MATERIAL's naming convention; the files keep the
        output name, so a scene convention never lands in the deliverable."""
        plane = self._plane("affixPlane")
        mat, sg = self._lambert("affixMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        out = TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="hero_atlas",
            assign=True,
            assign_suffix="_MAT",
        )
        self.assertTrue(cmds.objExists("hero_atlas_MAT"))
        self.assertFalse(cmds.objExists("hero_atlas"))
        self.assertTrue(
            os.path.basename(out[mat]["baseColor"]).startswith("hero_atlas_BaseColor")
        )

    def test_assign_prefix_prepends_instead(self):
        plane = self._plane("prefixPlane")
        _mat, sg = self._lambert("prefixMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="hero_atlas",
            assign=True,
            assign_prefix="MAT_",
            assign_suffix="",
        )
        self.assertTrue(cmds.objExists("MAT_hero_atlas"))

    def test_assign_shader_type_retypes_the_result(self):
        """The deliverable case: the assigned material is a copy of the TARGET's,
        so without this it lands on whatever that mesh happened to wear -- for
        unassigned geometry, Maya's default shader."""
        plane = self._plane("retypePlane")
        _mat, sg = self._lambert("retypeMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="hero_atlas",
            assign=True,
            assign_shader_type="stingray",
        )
        self.assertTrue(cmds.objExists("hero_atlas"))
        self.assertEqual(cmds.nodeType("hero_atlas"), "StingrayPBS")
        # The transferred map came across with the retype, and the mesh still
        # wears the result -- a retype that dropped either would be worse than
        # no retype at all.
        self.assertTrue(
            cmds.listConnections("hero_atlas.TEX_color_map", type="file"),
            "the transferred map did not survive the retype",
        )
        self.assertIn("hero_atlas", TextureTransfer().face_materials(plane)[0])
        # An opaque result lands on the plain graph. The masked one -- picked
        # while the target's DEFAULT opacity read as a channel -- samples an
        # unbound mask and discards every fragment: the mesh rendered invisible.
        self.assertFalse(
            cmds.attributeQuery("TEX_mask_map", node="hero_atlas", exists=True)
        )
        # And the retype keeps the plain path's hygiene: one shading group per
        # result, not an orphan beside a uniquified twin.
        self.assertEqual(
            len(cmds.ls("hero_atlasSG*", type="shadingEngine")),
            1,
            cmds.ls("hero_atlasSG*", type="shadingEngine"),
        )

    def test_an_unmapped_channel_has_no_constant_unless_one_is_stored(self):
        """A source with no map for a channel another source maps is filled
        with its CONSTANT -- so a literal that is no value must read as None
        (the neutral fill). An undriven ``normalCamera`` is (1, 1, 1), not a
        tangent-space normal; a lambert's ``transparency`` 0 is OPAQUE."""
        ss = cmds.shadingNode("standardSurface", asShader=True, name="constSS")
        self.assertIsNone(TextureTransfer.material_constant(ss, "normal"))
        lam = cmds.shadingNode("lambert", asShader=True, name="constLam")
        self.assertEqual(TextureTransfer.material_constant(lam, "opacity"), (1.0,) * 3)
        cmds.setAttr(f"{ss}.specularColor", 0.25, 0.5, 0.75, type="double3")
        for got, want in zip(
            TextureTransfer.material_constant(ss, "specular"), (0.25, 0.5, 0.75)
        ):
            self.assertAlmostEqual(got, want, places=5)

    def test_an_unweighted_emission_is_no_constant(self):
        """standardSurface's ``emissionColor`` defaults to WHITE behind an
        ``emission`` weight of 0: read alone, every non-emissive source's share
        of a consolidated emission map came out white."""
        ss = cmds.shadingNode("standardSurface", asShader=True, name="emitSS")
        self.assertEqual(
            TextureTransfer.material_constant(ss, "emission"), (0.0, 0.0, 0.0)
        )
        cmds.setAttr(f"{ss}.emission", 0.5)
        cmds.setAttr(f"{ss}.emissionColor", 1.0, 0.5, 0.0, type="double3")
        for got, want in zip(
            TextureTransfer.material_constant(ss, "emission"), (0.5, 0.25, 0.0)
        ):
            self.assertAlmostEqual(got, want, places=5)

    def test_a_stingray_sampler_slot_is_no_constant(self):
        """An undriven ``TEX_ao_map`` reads (0, 0, 0): black occlusion over a
        mapless source's share of the layout."""
        from mayatk.mat_utils._mat_utils import MatUtils

        try:
            sr = MatUtils.create_stingray_shader("constSR", opacity_mode="none")
        except RuntimeError as error:
            self.skipTest(f"StingrayPBS unavailable: {error}")
        self.assertIsNone(TextureTransfer.material_constant(sr, "ambientOcclusion"))
        self.assertIsNone(TextureTransfer.material_constant(sr, "normal"))
        cmds.setAttr(f"{sr}.base_color", 0.2, 0.4, 0.6, type="double3")
        for got, want in zip(
            TextureTransfer.material_constant(sr, "baseColor"), (0.2, 0.4, 0.6)
        ):
            self.assertAlmostEqual(got, want, places=5)

    def test_a_packed_metallic_map_keeps_a_non_metal_non_metallic(self):
        """The production case: a Unity-style MetallicSmoothness map (metal 0
        in RGB, smoothness in A) transferred onto a standardSurface target.
        The assigned copy read metalness off the alpha, so the table came out
        ~78% metal and rendered at a third of the source's brightness."""
        packed = np.zeros((16, 16, 4), np.uint8)
        packed[..., 3] = 200
        mpath = os.path.join(self.tmp, "src_MetallicSmoothness.png")
        Image.fromarray(packed).save(mpath)
        plane = self._plane("packedPlane")
        mat = cmds.shadingNode("standardSurface", asShader=True, name="packedMat")
        sg = cmds.sets(
            name="packedMatSG", renderable=True, noSurfaceShader=True, empty=True
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        f = cmds.shadingNode("file", asTexture=True, name="packedFile")
        cmds.setAttr(f"{f}.fileTextureName", mpath.replace("\\", "/"), type="string")
        cmds.connectAttr(f"{f}.outColorR", f"{mat}.metalness")
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="packed_out",
            assign=True,
        )
        self.assertAlmostEqual(self.sample_input("packed_out.metalness"), 0.0, places=3)

    def test_the_layout_derived_affix_does_not_stack_on_a_re_run(self):
        """The second run's TARGET material is the first run's output, so the
        name it derives from already carries the affix -- applying it again
        must not produce ``<mat>_TRANSFER_TRANSFER``."""
        plane = self._plane("stackPlane")
        _mat, sg = self._lambert("stackMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        kwargs = dict(
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            assign=True,
        )
        TextureTransfer().transfer(plane, **kwargs)
        TextureTransfer().transfer(plane, **kwargs)
        self.assertFalse(cmds.objExists("stackMat_TRANSFER_TRANSFER"))
        self.assertTrue(cmds.objExists("stackMat_TRANSFER"))

    def test_a_second_run_with_the_same_name_replaces_the_material(self):
        """Re-running a transfer under the same name is a second attempt at one
        deliverable, not a second deliverable."""
        plane = self._plane("rerunPlane")
        mat, sg = self._lambert("rerunMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        kwargs = dict(
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="hero_atlas",
            assign=True,
        )
        TextureTransfer().transfer(plane, **kwargs)
        TextureTransfer().transfer(plane, **kwargs)
        self.assertEqual(len(cmds.ls("hero_atlas", type="lambert")), 1)
        # And it is still ASSIGNED: the second run's target material IS the one
        # the first run assigned, so anything that resolves the faces after
        # clearing it finds nothing to move and leaves the mesh on lambert1.
        self.assertIn("hero_atlas", TextureTransfer().face_materials(plane)[0])
        # No orphaned shading group accumulating per run.
        self.assertEqual(len(cmds.ls("hero_atlasSG*", type="shadingEngine")), 1)

    def test_a_named_re_run_over_several_layouts_does_not_stack_the_name(self):
        """With several layouts the result is ``<name>_<layout>``, and a layout
        is named after its TARGET material -- on a re-run, the material the
        previous run assigned. Measured on a production table after four runs:
        ``SolderingTable_SolderingTable_SolderingTable_SolderingTable_TABLE_ASSETS_MAT``,
        maps and material alike (and the lightmap baker named its maps after it)."""
        a = self._plane("stackA")
        b = self._plane("stackB")
        _ma, sga = self._lambert("deskMat", texture=self.checker_path)
        _mb, sgb = self._lambert("legsMat", texture=self.checker_path)
        cmds.sets(a, e=True, forceElement=sga)
        cmds.sets(b, e=True, forceElement=sgb)
        srcs = [
            cmds.duplicate(a, name="stackA_src")[0],
            cmds.duplicate(b, name="stackB_src")[0],
        ]
        for s_, sg in zip(srcs, (sga, sgb)):
            cmds.sets(s_, e=True, forceElement=sg)

        kwargs = dict(
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="Table",
            assign=True,
            assign_suffix="_MAT",
        )
        runs = [TextureTransfer().transfer([a, b], srcs, **kwargs) for _ in range(3)]
        expected = {"Table_deskMat_MAT", "Table_legsMat_MAT"}
        self.assertEqual(set(cmds.ls("Table_*", materials=True)), expected)
        self.assertEqual(
            {TextureTransfer().face_materials(o)[0][0] for o in (a, b)}, expected
        )
        # Every run writes the SAME files: a re-run is another attempt at one
        # deliverable, never a new set of maps beside the old.
        names = [
            sorted(os.path.basename(p) for ch in run.values() for p in ch.values())
            for run in runs
        ]
        self.assertEqual(names[0], names[1])
        self.assertEqual(names[1], names[2])
        self.assertIn("Table_deskMat_BaseColor.png", names[0])

    def _surface(self, name, texture=None):
        """A standardSurface + SG with a NON-channel input (a checker node on
        ``coatColor``): the stand-in for a StingrayPBS's IBL cubes, which no
        transfer writes and the assigned copy must keep."""
        mat = cmds.shadingNode("standardSurface", asShader=True, name=name)
        sg = cmds.sets(
            name=f"{name}SG", renderable=True, noSurfaceShader=True, empty=True
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        cmds.setAttr(f"{mat}.coat", 0.5)
        extra = cmds.shadingNode("checker", asTexture=True, name=f"{name}_extra")
        cmds.connectAttr(f"{extra}.outColor", f"{mat}.coatColor")
        if texture:
            f = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
            cmds.setAttr(f"{f}.fileTextureName", texture, type="string")
            cmds.connectAttr(f"{f}.outColor", f"{mat}.baseColor")
        return mat, sg, extra

    def _wears_new_map(self, mat):
        files = cmds.listConnections(f"{mat}.baseColor", type="file") or []
        paths = [cmds.getAttr(f"{f}.fileTextureName") for f in files]
        return bool(paths) and all(
            os.path.normpath(p).startswith(os.path.normpath(self.out_dir))
            for p in paths
        )

    def test_assign_from_source_copies_the_source_material(self):
        """The look being transferred is the SOURCE's: a target wearing an
        import placeholder (here a lambert) got that placeholder's shader, and
        a StingrayPBS source's table came out visibly darker as standardSurface."""
        src = self._plane("fromSrc")
        mat, sg, extra = self._surface("fromSrcMat", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="fromTgt")[0]
        _tmat, tsg = self._lambert("fromTgtMat")
        cmds.sets(tgt, e=True, forceElement=tsg)
        cmds.move(2, 0, 0, tgt)

        TextureTransfer().transfer(
            tgt,
            src,
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="from_src",
            assign=True,
            assign_from="source",
        )
        self.assertEqual(cmds.nodeType("from_src"), "standardSurface")
        self.assertAlmostEqual(cmds.getAttr("from_src.coat"), 0.5)
        self.assertEqual(cmds.listConnections("from_src.coatColor"), [extra])
        self.assertTrue(self._wears_new_map("from_src"))
        self.assertIn("from_src", TextureTransfer().face_materials(tgt)[0])
        self.assertIn(mat, TextureTransfer().face_materials(src)[0])

    def test_the_assigned_copy_keeps_inputs_no_channel_drives(self):
        """``duplicate(inputConnections=False)`` dropped EVERY input, so a
        StingrayPBS copy lost its IBL cubes and rendered without ambient light.
        Only the channel slots are cleared; the transfer re-wires those."""
        plane = self._plane("keepInPlane")
        _mat, sg, extra = self._surface("keepInMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="keep_in",
            assign=True,
        )
        self.assertEqual(cmds.listConnections("keep_in.coatColor"), [extra])
        self.assertTrue(self._wears_new_map("keep_in"))

    def test_a_copy_relinks_array_element_inputs(self):
        """An input on an ARRAY element (a layeredShader's ``inputs[0].color``)
        is no channel slot either -- it is carried, not a crash on the plug
        name."""
        plane = self._plane("layeredPlane")
        lay = cmds.shadingNode("layeredShader", asShader=True, name="layeredMat")
        sg = cmds.sets(
            name="layeredMatSG", renderable=True, noSurfaceShader=True, empty=True
        )
        cmds.connectAttr(f"{lay}.outColor", f"{sg}.surfaceShader")
        layer = cmds.shadingNode("checker", asTexture=True, name="layeredMat_layer")
        cmds.connectAttr(f"{layer}.outColor", f"{lay}.inputs[0].color")
        cmds.sets(plane, e=True, forceElement=sg)
        copy = TextureTransfer.new_material_from(lay)
        self.assertEqual(cmds.listConnections(f"{copy}.inputs[0].color"), [layer])

    def test_a_material_the_source_wears_is_never_replaced(self):
        """The run READS the source's material, so a name it collides with is
        not a previous result to clear: an iteration whose source wears the
        last result (``<name>_MAT``) lost its material to the next run."""
        src = self._plane("heldSrc")
        mat, sg, _extra = self._surface("held_MAT", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="heldTgt")[0]
        _tmat, tsg = self._lambert("heldTgtMat")
        cmds.sets(tgt, e=True, forceElement=tsg)

        TextureTransfer().transfer(
            tgt,
            src,
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="held",
            assign=True,
            assign_suffix="_MAT",
        )
        self.assertTrue(cmds.objExists(mat))
        self.assertEqual(TextureTransfer().face_materials(src)[0], [mat])
        self.assertNotIn(mat, TextureTransfer().face_materials(tgt)[0])

    def test_a_source_named_by_its_shape_keeps_its_material(self):
        """The keep rule compared transform paths against the caller's own
        spelling: a source passed as its SHAPE matched nothing, so the
        material it wears was cleared as a previous result. A target passed
        as its shape is still the owner of its own previous result."""
        src = self._plane("shapeSrc")
        mat, sg, _extra = self._surface("held_MAT", texture=self.checker_path)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="shapeTgt")[0]
        _tmat, tsg = self._lambert("shapeTgtMat")
        cmds.sets(tgt, e=True, forceElement=tsg)
        src_shape = cmds.listRelatives(src, shapes=True, fullPath=True)[0]
        tgt_shape = cmds.listRelatives(tgt, shapes=True, fullPath=True)[0]

        for _run in range(2):
            TextureTransfer().transfer(
                tgt_shape,
                src_shape,
                size=16,
                supersample=1,
                padding=0,
                output_dir=self.out_dir,
                output_name="held",
                assign=True,
                assign_suffix="_MAT",
            )
        self.assertTrue(cmds.objExists(mat))
        self.assertEqual(TextureTransfer().face_materials(src)[0], [mat])
        self.assertEqual(TextureTransfer().face_materials(tgt)[0], ["held_1_MAT"])
        self.assertEqual(cmds.ls("held_1*", type="lambert"), ["held_1_MAT"])

    def _seat(self, chair, rgb):
        """``|<chair>|seat_GEO`` wearing a flat *rgb* map, with a rotated
        ``map2`` to transfer into: two chairs are two meshes of one leaf name,
        and tentacle's blank Output Name derives ``seat`` for both."""
        tex = os.path.join(self.tmp, f"{chair}_src.png").replace("\\", "/")
        Image.new("RGB", (16, 16), rgb).save(tex)
        grp = cmds.group(empty=True, name=chair)
        plane = cmds.parent(self._plane("seat_tmp"), grp)[0]
        cmds.rename(f"|{grp}|{plane}", "seat_GEO")
        plane = f"|{grp}|seat_GEO"
        _mat, sg = self._lambert(f"{chair}Mat", texture=tex)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)
        return plane

    def test_a_name_another_object_wears_is_never_taken_from_it(self):
        """``|chairA|seat_GEO`` then ``|chairB|seat_GEO``, both named ``seat``:
        the second run deleted chairA's material -- chairA left wearing
        nothing -- and wrote its maps over chairA's. Each keeps its own now,
        and a re-run of either replaces only its own result, in place."""
        kwargs = dict(
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="seat",
            assign=True,
        )
        a = self._seat("chairA", (255, 0, 0))
        b = self._seat("chairB", (0, 0, 255))
        for _run in range(2):  # the second round: each re-run replaces its own
            TextureTransfer().transfer(a, **kwargs)
            TextureTransfer().transfer(b, **kwargs)
            self.assertEqual(TextureTransfer().face_materials(a)[0], ["seat"])
            self.assertEqual(TextureTransfer().face_materials(b)[0], ["seat_1"])
        self.assertEqual(sorted(cmds.ls("seat*", type="lambert")), ["seat", "seat_1"])
        file_a = cmds.listConnections("seat.color", type="file")[0]
        self.assertEqual(
            os.path.basename(cmds.getAttr(f"{file_a}.fileTextureName")),
            "seat_BaseColor.png",
        )
        red = self._load(os.path.join(self.out_dir, "seat_BaseColor.png"))
        blue = self._load(os.path.join(self.out_dir, "seat_1_BaseColor.png"))
        self.assertGreater(red[..., 0].mean(), 200)
        self.assertLess(red[..., 2].mean(), 50)
        self.assertGreater(blue[..., 2].mean(), 200)
        self.assertLess(blue[..., 0].mean(), 50)

    def test_a_same_mesh_run_never_replaces_the_material_it_reads(self):
        """A UV-set transfer reads the mesh's OWN material, and that material
        may already be called what the output is: it was cleared as a previous
        result -- the material the run was reading deleted, though the
        originals are never to be modified."""
        plane = self._plane("ownPlane")
        mat, sg = self._lambert("seat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="seat",
            assign=True,
        )
        file_node = cmds.listConnections(f"{mat}.color", type="file")[0]
        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"), self.checker_path
        )
        self.assertEqual(TextureTransfer().face_materials(plane)[0], ["seat_1"])

    def test_a_map_a_kept_material_reads_is_never_written_over(self):
        """A source map that sits in the output folder under the output's own
        file name was rewritten in place, and the source material -- which the
        run keeps -- changed with it. The output is named beside it instead,
        maps and material alike."""
        os.makedirs(self.out_dir, exist_ok=True)
        held = os.path.join(self.out_dir, "hero_BaseColor.png").replace("\\", "/")
        Image.fromarray(_checker()).save(held)
        before = self._load(held)
        src = self._plane("keptSrc")
        _m, sg = self._lambert("keptSrcMat", texture=held)
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="keptTgt")[0]
        _t, tsg = self._lambert("keptTgtMat")
        cmds.sets(tgt, e=True, forceElement=tsg)
        cmds.polyEditUV(f"{tgt}.map[*]", scaleU=-1, pivotU=0.5)  # another layout

        out = TextureTransfer().transfer(
            tgt,
            src,
            size=64,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="hero",
            assign=True,
        )
        self.assertTrue(np.array_equal(self._load(held), before), "map written over")
        (maps,) = out.values()
        self.assertEqual(os.path.basename(maps["baseColor"]), "hero_1_BaseColor.png")
        self.assertEqual(TextureTransfer().face_materials(tgt)[0], ["hero_1"])

    def test_a_previous_result_maya_does_not_list_is_still_replaced(self):
        """``ls(materials=True)`` lists a shader only while it is registered
        in ``defaultShaderList1``; a production scene carried transfer results
        that were not (they are missing from the Hypershade's Materials tab
        too), so each re-run stacked ``<name>1`` beside the old one."""
        plane = self._plane("unlistedPlane")
        _mat, sg = self._lambert("unlistedMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)
        kwargs = dict(
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="unlisted",
            assign=True,
        )
        TextureTransfer().transfer(plane, **kwargs)
        for dst in (
            cmds.listConnections(
                "unlisted.message", type="defaultShaderList", plugs=True
            )
            or []
        ):
            cmds.disconnectAttr("unlisted.message", dst)
        self.assertEqual(cmds.ls("unlisted", materials=True), [])

        TextureTransfer().transfer(plane, **kwargs)
        self.assertFalse(cmds.objExists("unlisted1"))
        self.assertIn("unlisted", TextureTransfer().face_materials(plane)[0])

    def test_an_output_name_a_bystander_wears_is_named_beside_it(self):
        """The name IS a name, but a material something outside the run wears
        is that object's: it was replaced, the bystander left wearing nothing.
        The bystander keeps it now, and the output -- material AND maps -- is
        named beside it. A second attempt at one deliverable still replaces
        its own result: the stamp says which material that is
        (``test_a_second_run_with_the_same_name_replaces_the_material``)."""
        plane = self._plane("collidePlane")
        _mat, sg = self._lambert("collideMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        # A bystander wearing its own material through its own SG, whose name
        # the transfer is about to use for its output.
        other = self._plane("bystander")
        _victim, victim_sg = self._lambert("hero_atlas")
        cmds.sets(other, e=True, forceElement=victim_sg)

        out = TextureTransfer().transfer(
            plane,
            source_uv_set="map1",
            target_uv_set="map2",
            size=16,
            supersample=1,
            padding=0,
            output_dir=self.out_dir,
            output_name="hero_atlas",
            assign=True,
        )
        self.assertEqual(TextureTransfer().face_materials(other)[0], ["hero_atlas"])
        self.assertEqual(TextureTransfer().face_materials(plane)[0], ["hero_atlas_1"])
        (maps,) = out.values()
        self.assertEqual(
            os.path.basename(maps["baseColor"]), "hero_atlas_1_BaseColor.png"
        )


class TestPairByName(QuickTestCase):
    """``pair_by_name`` -- path strings only, no scene."""

    def test_meshes_of_one_leaf_name_pair_by_their_paths(self):
        """Two kits of one part list: paired by the leaf alone, the first
        target took the LAST source and the second the first -- each chair the
        other's textures (and, with Include Lightmaps, the other's lighting)."""
        tgts = ["|tgt|chairA|seat_GEO", "|tgt|chairB|seat_GEO"]
        srcs = ["|src|chairA|seat_GEO", "|src|chairB|seat_GEO"]
        want = {tgts[0]: srcs[0], tgts[1]: srcs[1]}
        self.assertEqual(TextureTransfer.pair_by_name(tgts, srcs), want)
        self.assertEqual(TextureTransfer.pair_by_name(tgts, srcs[::-1]), want)

    def test_a_tie_or_no_shared_name_pairs_by_order(self):
        pair = TextureTransfer.pair_by_name
        # One leaf under parents that match nothing: a tie, so by order.
        self.assertEqual(
            pair(["|t1|seat", "|t2|seat"], ["|s1|seat", "|s2|seat"]),
            {"|t1|seat": "|s1|seat", "|t2|seat": "|s2|seat"},
        )
        # Unique leaves pair by leaf, whatever the order.
        self.assertEqual(
            pair(["|g|partB", "|g|partA"], ["|partA", "|partB"]),
            {"|g|partB": "|partB", "|g|partA": "|partA"},
        )
        # No shared leaf: by order.
        self.assertEqual(
            pair(["|x1", "|x2"], ["|y1", "|y2"]), {"|x1": "|y1", "|x2": "|y2"}
        )


class TestRetiredNames(QuickTestCase):
    def test_constant_attrs_reads_the_shader_maps_table_for_one_release(self):
        """``TextureTransfer.CONSTANT_ATTRS`` moved to
        :attr:`ShaderAttributeMap.CONSTANT_ATTRS` with no alias left behind."""
        with self.assertWarns(DeprecationWarning):
            table = TextureTransfer.CONSTANT_ATTRS
        self.assertIs(table, ShaderAttributeMap.CONSTANT_ATTRS)


class TestLightmapTransfer(MayaTkTestCase):
    """``transfer_lightmaps`` -- a committed lightmap carried to another mesh.

    The remap itself is pinned in pythontk (``TestRemapLightmap``); these pin
    the adapter: the marker read through ``LightmapRecords.lightmap_info``,
    rebind vs resample, the rect travelling, and the commit on the target.
    """

    def setUp(self):
        super().setUp()
        from mayatk.light_utils.lightmap_baker.lightmap_records import (
            LightmapRecords,
        )

        self.records = LightmapRecords
        self._artifacts = ptk.TempArtifacts("uv_lightmap_test", policy="scoped")
        self.addCleanup(self._artifacts.cleanup)
        self.tmp = self._artifacts.dir_path()
        self.out_dir = os.path.join(self.tmp, "out").replace("\\", "/")
        # HDR content (well past 1.0) with an orientation: R ramps in U, G in V.
        size = 32
        u = (np.arange(size) + 0.5) / size
        v = 1.0 - (np.arange(size) + 0.5) / size
        self.hdr = np.zeros((size, size, 3), np.float32)
        self.hdr[..., 0] = u[None, :] * 6.0
        self.hdr[..., 1] = v[:, None] * 6.0
        self.hdr[..., 2] = 0.25

    # ------------------------------------------------------------ helpers
    def _lightmapped(self, name, image=None, rect=None, written=True):
        """A plane with a ``lightmap`` set (map1's layout) and a committed map."""
        plane = cmds.polyPlane(name=name, sx=2, sy=2, w=1, h=1, ch=False)[0]
        cmds.polyUVSet(plane, copy=True, uvSet="map1", newUVSet="lightmap")
        path = os.path.join(self.tmp, f"{name}_Lightmap.exr").replace("\\", "/")
        LightmapRecords._write_lightmap(path, self.hdr if image is None else image)
        self.records.commit(
            {plane: path},
            {plane: rect} if rect else None,
            intensity=1.5,
            written=written,
        )
        return (cmds.ls(plane, long=True) or [plane])[0], path

    def _copy(self, src, name, rotate=0):
        """*src* duplicated without its marker; its lightmap set optionally
        rotated about the tile center."""
        tgt = cmds.duplicate(src, name=name)[0]
        tgt = (cmds.ls(tgt, long=True) or [tgt])[0]
        if cmds.attributeQuery("lightmapInfo", node=tgt, exists=True):
            cmds.deleteAttr(f"{tgt}.lightmapInfo")
        if rotate:
            cmds.polyUVSet(tgt, currentUVSet=True, uvSet="lightmap")
            cmds.polyEditUV(
                f"{tgt}.map[*]",
                uvSetName="lightmap",
                rotation=True,
                angle=rotate,
                pivotU=0.5,
                pivotV=0.5,
            )
            cmds.polyUVSet(tgt, currentUVSet=True, uvSet="map1")
        return tgt

    @staticmethod
    def _read(path):
        return LightmapRecords._read_lightmap(path)

    # -------------------------------------------------------------- tests
    def test_a_matching_lightmap_layout_is_rebound_not_resampled(self):
        rect = [0.5, 0.5, 0.25, 0.25]
        src, path = self._lightmapped("lmSrc", rect=rect)
        tgt = self._copy(src, "lmTgt")

        out = LightmapRecords.transfer_lightmaps(tgt, src, output_dir=self.out_dir)

        self.assertEqual(list(out), [tgt])
        self.assertEqual(out[tgt]["how"], "rebound")
        self.assertEqual(os.path.normcase(out[tgt]["path"]), os.path.normcase(path))
        self.assertFalse(os.path.isdir(self.out_dir))  # nothing written
        info = self.records.lightmap_info(tgt)
        self.assertEqual(info["map"], os.path.basename(path))
        self.assertEqual(info["scaleOffset"], rect)  # the atlas rect travels
        self.assertEqual(info["intensity"], 1.5)
        self.assertEqual(info["uv_set"], "lightmap")

    def test_a_different_lightmap_layout_is_resampled_into_it(self):
        src, _path = self._lightmapped("lmSrc")
        tgt = self._copy(src, "lmTgt", rotate=90)

        out = LightmapRecords.transfer_lightmaps(
            tgt, src, output_dir=self.out_dir, output_name="hero", supersample=1
        )

        self.assertEqual(out[tgt]["how"], "resampled")
        written = out[tgt]["path"]
        self.assertEqual(os.path.basename(written), "hero_Lightmap.exr")
        self.assertTrue(os.path.isfile(written))
        got = self._read(written)
        self.assertGreater(got.max(), 1.0)  # HDR survives the round trip
        # The lightmap set rotated 90 CCW: the stored map rotates with it.
        self.assertLess(np.abs(got - np.rot90(self.hdr, 1)).max(), 0.05)
        info = self.records.lightmap_info(tgt)
        self.assertEqual(info["map"], "hero_Lightmap.exr")
        self.assertEqual(info["scaleOffset"], [1.0, 1.0, 0.0, 0.0])
        self.assertEqual(info["intensity"], 1.5)

    def test_a_resample_reads_only_the_sources_atlas_cell(self):
        atlas = np.full((32, 32, 3), 2.0, np.float32)
        atlas[:, 16:] = 50.0  # another object's lighting in the shared map
        src, _path = self._lightmapped("lmSrc", image=atlas, rect=[0.5, 1.0, 0.0, 0.0])
        tgt = self._copy(src, "lmTgt", rotate=90)

        out = LightmapRecords.transfer_lightmaps(tgt, src, output_dir=self.out_dir)

        got = self._read(out[tgt]["path"])
        self.assertTrue(np.allclose(got, 2.0, atol=0.01), got.max())

    def test_a_rebind_does_not_claim_the_map_was_written_here(self):
        """A map this scene did not write must stay out of the writer record,
        or a later re-bake could set aside a file another scene reads."""
        src, path = self._lightmapped("lmSrc", written=False)
        tgt = self._copy(src, "lmTgt")
        key = os.path.basename(path).lower()
        self.assertNotIn(key, self.records._writers())

        LightmapRecords.transfer_lightmaps(tgt, src, output_dir=self.out_dir)

        self.assertNotIn(key, self.records._writers())

    def test_a_source_without_a_lightmap_carries_nothing(self):
        src = cmds.polyPlane(name="lmBare", sx=2, sy=2, ch=False)[0]
        cmds.polyUVSet(src, copy=True, uvSet="map1", newUVSet="lightmap")
        tgt = cmds.duplicate(src, name="lmBareTgt")[0]

        out = LightmapRecords.transfer_lightmaps(tgt, src, output_dir=self.out_dir)

        self.assertEqual(out, {})
        self.assertEqual(self.records.lightmap_info(tgt), {})

    def test_a_target_without_a_lightmap_set_takes_the_sources(self):
        """The pair shares topology, so the source's own lightmap layout fits
        the target loop for loop: the target is given it and the lightmap is
        REBOUND -- nothing resampled, no new map. It used to be skipped, so a
        target without lightmap UVs could not receive a lightmap at all."""
        rect = [0.5, 0.5, 0.25, 0.25]
        src, path = self._lightmapped("lmSrc", rect=rect)
        cmds.polyEditUV(  # a layout that is not map1's, so a copy is provable
            f"{src}.map[*]", uvSetName="lightmap", scaleU=0.5, scaleV=0.25
        )
        tgt = self._copy(src, "lmTgt")
        cmds.polyUVSet(tgt, delete=True, uvSet="lightmap")

        out = LightmapRecords.transfer_lightmaps(tgt, src, output_dir=self.out_dir)

        self.assertEqual(out[tgt]["how"], "rebound")
        self.assertEqual(os.path.normcase(out[tgt]["path"]), os.path.normcase(path))
        self.assertFalse(os.path.isdir(self.out_dir))  # nothing written
        info = self.records.lightmap_info(tgt)
        self.assertEqual(info["scaleOffset"], rect)
        corr = TextureTransfer().correspondence(
            tgt, src, source_uv_set="lightmap", target_uv_set=info["uv_set"]
        )
        self.assertLess(np.abs(corr["src_tris"] - corr["dst_tris"]).max(), 1e-5)

    def test_a_target_elsewhere_is_carried_with_a_warning(self):
        """A lightmap is the light where the source stands: a copy moved away
        still gets it (that was asked for), but never silently."""
        src, _path = self._lightmapped("lmSrc")
        tgt = self._copy(src, "lmTgt")
        cmds.move(10, 0, 0, tgt, relative=True)
        with self.assertLogs(LightmapRecords.logger, "WARNING") as logs:
            out = LightmapRecords.transfer_lightmaps(tgt, src, output_dir=self.out_dir)
        self.assertEqual(out[tgt]["how"], "rebound")
        self.assertTrue(any("different places" in m for m in logs.output), logs)

    def test_no_source_raises(self):
        src, _path = self._lightmapped("lmSrc")
        with self.assertRaises(ValueError):
            LightmapRecords.transfer_lightmaps(src, None)


class TestOutputDirResolution(MayaTkTestCase):
    """``resolve_output_dir`` -- what the panel's Output Folder field means.

    Its rule is the shared one (``ptk.FileUtils.resolve_output_dir``): what
    this pins is the BASE the adapter resolves against, the project's
    ``sourceimages``, which is what makes a stored entry portable -- and that
    a blank entry still lands in ``default_output_dir`` rather than dumping
    every map loose in ``sourceimages``.
    """

    def setUp(self):
        super().setUp()
        self.project = ptk.TempArtifacts("uv_transfer_proj").dir_path()
        os.makedirs(os.path.join(self.project, "sourceimages"), exist_ok=True)
        # The workspace is PROCESS state, and the headless runner chunks many
        # modules into one mayapy: leaving a temp project open would re-point
        # every later module's path resolution.
        self._prev_workspace = cmds.workspace(query=True, fullName=True)
        cmds.workspace(self.project, openWorkspace=True)
        self.base = os.path.normpath(TextureTransfer.output_base_dir())

    def tearDown(self):
        if self._prev_workspace:
            cmds.workspace(self._prev_workspace, openWorkspace=True)
        super().tearDown()

    def test_the_base_is_the_projects_sourceimages(self):
        self.assertEqual(
            self.base, os.path.normpath(os.path.join(self.project, "sourceimages"))
        )

    def test_blank_is_the_default_subfolder_not_the_base(self):
        for entry in (None, "", "   "):
            self.assertEqual(
                os.path.normpath(TextureTransfer.resolve_output_dir(entry)),
                os.path.normpath(TextureTransfer.default_output_dir()),
                entry,
            )
        self.assertNotEqual(
            os.path.normpath(TextureTransfer.default_output_dir()), self.base
        )

    def test_a_relative_entry_lands_under_sourceimages(self):
        self.assertEqual(
            os.path.normpath(TextureTransfer.resolve_output_dir("bakes/v2")),
            os.path.join(self.base, "bakes", "v2"),
        )

    def test_a_full_path_wins_outright(self):
        rooted = os.path.normpath(os.path.join(self.project, "elsewhere"))
        self.assertEqual(
            os.path.normpath(TextureTransfer.resolve_output_dir(rooted)), rooted
        )

    def test_it_round_trips_the_portable_spelling_a_browse_stores(self):
        """The option box writes back ``relativize_output_dir``'s answer, so
        the pair must be inverses -- otherwise a browsed folder resolves
        somewhere else on the next run."""
        picked = os.path.join(self.base, "bakes", "v2")
        entry = ptk.FileUtils.relativize_output_dir(picked, self.base)
        self.assertFalse(os.path.isabs(entry), entry)
        self.assertEqual(
            os.path.normpath(TextureTransfer.resolve_output_dir(entry)), picked
        )

    def test_the_transfer_writes_where_a_relative_entry_says(self):
        """End to end: the field's text, not an absolute path, decides where
        the maps land."""
        checker = os.path.join(self.project, "checker.png").replace("\\", "/")
        Image.fromarray(_checker()).save(checker)
        src = cmds.polyPlane(name="relSrc", sx=2, sy=2, w=1, h=1, ch=False)[0]
        mat = cmds.shadingNode("lambert", asShader=True, name="relMat")
        sg = cmds.sets(
            name="relMatSG", renderable=True, noSurfaceShader=True, empty=True
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        node = cmds.shadingNode("file", asTexture=True, name="relFile")
        cmds.setAttr(f"{node}.fileTextureName", checker, type="string")
        cmds.connectAttr(f"{node}.outColor", f"{mat}.color")
        cmds.sets(src, e=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="relTgt")[0]

        results = TextureTransfer().transfer(
            [tgt], [src], size=32, supersample=1, output_dir="bakes/v2"
        )
        written = [p for maps in results.values() for p in maps.values()]
        self.assertTrue(written)
        for path in written:
            self.assertEqual(
                os.path.normpath(os.path.dirname(path)),
                os.path.join(self.base, "bakes", "v2"),
            )


if __name__ == "__main__":
    unittest.main()
