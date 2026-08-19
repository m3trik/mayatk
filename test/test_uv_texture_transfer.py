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

from base_test import MayaTkTestCase
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
        self.tmp = ptk.TempArtifacts("uv_transfer_test", policy="detached").dir_path()
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

    def test_an_output_name_that_collides_replaces_the_existing_material(self):
        """Pinning the destructive half of "re-running replaces it": the name
        IS a name, so an existing material wearing it is replaced and anything
        assigned through it loses that assignment. Deliberate -- the same rule
        is what makes a second attempt at one deliverable work -- so it is
        asserted here rather than left to be discovered in a scene."""
        plane = self._plane("collidePlane")
        mat, sg = self._lambert("collideMat", texture=self.checker_path)
        cmds.sets(plane, e=True, forceElement=sg)
        self._rotate_uv_set_copy(plane, "map2", 90)

        # A bystander wearing its own material through its own SG, whose name
        # the transfer is about to claim for its output.
        other = self._plane("bystander")
        _victim, victim_sg = self._lambert("hero_atlas")
        cmds.sets(other, e=True, forceElement=victim_sg)

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
        # One material by that name, and it is the transfer's output -- worn by
        # the transfer's target, not by the bystander that used to own the name.
        self.assertEqual(len(cmds.ls("hero_atlas", type="lambert")), 1)
        self.assertIn("hero_atlas", TextureTransfer().face_materials(plane)[0])
        self.assertNotIn("hero_atlas", TextureTransfer().face_materials(other)[0])


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
