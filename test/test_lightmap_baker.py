"""Tests for LightmapBaker -- the fused-lightmap (UV2) orchestrator.

LightmapBaker owns no baking/UV logic; it wires together create_lightmap_uvs,
TextureBaker.bake(uv_set=), and ImgUtils.dilate_image. The tests therefore
check the *wiring*: a UV2 set is ensured, the lightmap set name is handed to
the baker, and the baked EXR is gutter-filled with its alpha coverage and
written back as opaque RGB.

  * Composition + dilation: need cv2 (EXR IO) but not a renderer -- a fake
    baker stands in for Arnold.
  * End-to-end: needs mtoa + cv2.
"""
import os
import sys
import json
import shutil
import tempfile
import unittest

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import maya.cmds as cmds
from base_test import MayaTkTestCase
from mayatk.light_utils.lightmap_baker import lightmap_baker as lmb_module
from mayatk.light_utils.lightmap_baker.lightmap_baker import (
    LightmapBaker,
    LightmapBakerSlots,
)
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics


def _cv2():
    try:
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2
        import numpy as np

        return cv2, np
    except Exception:
        return None, None


def _arnold_loadable():
    try:
        if not cmds.pluginInfo("mtoa", q=True, loaded=True):
            cmds.loadPlugin("mtoa")
        return hasattr(cmds, "arnoldRenderToTexture")
    except Exception:
        return False


HAVE_CV2 = _cv2()[0] is not None


def _write_half_covered_exr(path):
    """4x4 RGBA EXR: left half = red & covered, right half = empty (alpha 0)."""
    cv2, np = _cv2()
    img = np.zeros((4, 4, 4), dtype=np.float32)
    img[:, :2, 2] = 1.0  # R (BGR index 2) on the left
    img[:, :2, 3] = 1.0  # alpha coverage on the left
    cv2.imwrite(path, img)


def _read(path):
    cv2, _ = _cv2()
    return cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)


class _FakeBaker:
    """Stands in for TextureBaker: records the uv_set, emits a synthetic EXR."""

    def __init__(self):
        self.called_uv_set = None

    def bake(
        self, objects, output_dir=None, prefix="", suffix="", backend="",
        uv_set=None, on_progress=None, stem=None,
    ):
        self.called_uv_set = uv_set
        self.called_stem = stem
        out = {}
        for obj in objects:
            leaf = obj.rsplit("|", 1)[-1]
            path = os.path.join(output_dir, f"{prefix}{leaf}{suffix}.exr")
            _write_half_covered_exr(path)
            out[cmds.ls(obj, long=True)[0]] = path
        return out


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestDilateLightmap(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_dilate_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_fills_gutter_from_alpha_and_drops_alpha(self):
        p = os.path.join(self.tmp, "lm.exr")
        _write_half_covered_exr(p)
        ok = LightmapBaker._dilate_lightmap(p, alpha_threshold=1e-3, iterations=-1)
        self.assertTrue(ok)
        out = _read(p)
        self.assertEqual(out.shape[2], 3, "alpha should be dropped")
        # The empty right half is now filled from the red left half.
        self.assertGreater(float(out[0, 3, 2]), 0.5)

    def test_no_alpha_channel_is_noop(self):
        cv2, np = _cv2()
        p = os.path.join(self.tmp, "rgb.exr")
        cv2.imwrite(p, np.ones((4, 4, 3), dtype=np.float32))
        self.assertFalse(
            LightmapBaker._dilate_lightmap(p, alpha_threshold=1e-3, iterations=-1)
        )


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestLightmapBakerComposition(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_compose_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_ensures_uv2_passes_set_name_and_dilates(self):
        cube = cmds.polyCube(name="lmCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        long = cmds.ls(cube, long=True)[0]
        fake = _FakeBaker()
        result = LightmapBaker(resolution=64, baker=fake).bake_fused(
            [cube], output_dir=self.tmp
        )
        # A tagged lightmap UV2 was created.
        self.assertEqual(
            UvDiagnostics.find_lightmap_uv_set(shape), UvDiagnostics.LIGHTMAP_UV_SET
        )
        # The baker was told to bake this object into that set (per-object map).
        self.assertEqual(fake.called_uv_set[long], UvDiagnostics.LIGHTMAP_UV_SET)
        # The synthetic EXR was dilated and rewritten as opaque RGB.
        self.assertTrue(result)
        out = _read(next(iter(result.values())))
        self.assertEqual(out.shape[2], 3)

    def test_targets_reused_noncanonical_set_name(self):
        # Regression (C5M): real meshes reuse a pre-existing lightmap set under
        # a non-canonical name (UV2, UVChannel_2, ...). bake_fused must target
        # each object's ACTUAL set, not the single hardcoded "lightmap" -- or
        # the bake lands on the wrong UV channel.
        cube = cmds.polyCube(name="lmReuseCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        long = cmds.ls(cube, long=True)[0]
        # A valid (copied from the cube's non-overlapping default), untagged
        # "UV2" -- detected by name, not tag.
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="UV2")
        self.assertTrue(UvDiagnostics.is_bakeable_lightmap(shape, "UV2"))

        fake = _FakeBaker()
        LightmapBaker(resolution=64, baker=fake).bake_fused(
            [cube], output_dir=self.tmp, create_uvs=False
        )
        self.assertEqual(fake.called_uv_set[long], "UV2")
        # No canonical "lightmap" set should have been created in reuse mode.
        self.assertNotIn(
            "lightmap", cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        )

    def test_dilate_false_leaves_alpha(self):
        cube = cmds.polyCube(name="lmCubeNoDilate")[0]
        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake_fused(
            [cube], output_dir=self.tmp, dilate=False
        )
        out = _read(next(iter(result.values())))
        self.assertEqual(out.shape[2], 4, "alpha kept when dilate=False")


@unittest.skipUnless(
    HAVE_CV2 and _arnold_loadable(), "mtoa/arnoldRenderToTexture or cv2 unavailable"
)
class TestLightmapBakerArnold(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_arnold_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_end_to_end_fused_lightmap(self):
        cube = cmds.polyCube(name="lmArnoldCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        result = LightmapBaker(resolution=64, samples=2).bake_fused(
            [cube], output_dir=self.tmp
        )
        self.assertTrue(result)
        path = next(iter(result.values()))
        self.assertTrue(os.path.exists(path))
        out = _read(path)
        self.assertEqual(out.shape[2], 3, "fused lightmap is opaque RGB")
        # Lightmap UVs landed on channel index 1 (the engine-bound UV2).
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertEqual(sets.index(UvDiagnostics.LIGHTMAP_UV_SET), 1)


class TestCommitUnlit(MayaTkTestCase):
    """commit_unlit / revert_unlit -- Approach B (lightmap = primary UV0).

    Non-destructive commit: lightmap -> UV0 + unlit material, with a JSON
    restore record stamped on the shape so revert works from a *fresh* baker
    (i.e. across save/reload). No renderer needed -- create_file_node only
    stores the path, so a dummy texture file exercises the wiring.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_unlit_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tex = os.path.join(self.tmp, "fused.exr")
        open(self.tex, "wb").close()  # path only; contents irrelevant here

    @staticmethod
    def _sets(shape):
        return cmds.polyUVSet(shape, query=True, allUVSets=True) or []

    @staticmethod
    def _sgs(shape):
        return cmds.listConnections(shape, type="shadingEngine") or []

    @staticmethod
    def _has_marker(shape):
        return cmds.attributeQuery(
            LightmapBaker.COMMIT_ATTR, node=shape, exists=True
        )

    def test_commit_promotes_uv0_wires_unlit_and_marks(self):
        cube = cmds.polyCube(name="unlitCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        long = cmds.ls(cube, long=True)[0]
        UvUtils.create_lightmap_uvs([cube], map_size=64, quiet=True)
        self.assertEqual(self._sets(shape).index("lightmap"), 1)  # starts at UV2
        orig_sgs = self._sgs(shape)

        baker = LightmapBaker(resolution=64)
        wired = baker.commit_unlit({long: self.tex})

        # Lightmap is now the primary UV (UV0) so a stock unlit shader samples it.
        self.assertEqual(self._sets(shape)[0], "lightmap")
        # An unlit surfaceShader, driven by the fused EXR, now shades the mesh.
        shader = wired[long]
        self.assertEqual(cmds.nodeType(shader), "surfaceShader")
        files = [
            n for n in (cmds.listConnections(f"{shader}.outColor", source=True) or [])
            if cmds.nodeType(n) == "file"
        ]
        self.assertTrue(files)
        # HDR lightmap must be read linear, not sRGB (else a transfer curve is
        # double-applied on import).
        self.assertEqual(cmds.getAttr(f"{files[0]}.colorSpace"), "Raw")
        self.assertNotEqual(self._sgs(shape), orig_sgs)
        self.assertTrue(self._has_marker(shape))  # restore record persisted

        # Revert from a FRESH baker -- proves the record lives on the mesh.
        LightmapBaker().revert_unlit([long])
        self.assertEqual(self._sets(shape).index("lightmap"), 1)
        self.assertEqual(self._sgs(shape), orig_sgs)
        self.assertFalse(cmds.objExists(shader))
        self.assertFalse(self._has_marker(shape))  # marker cleared

    def test_revert_rebuilds_multimaterial_per_face(self):
        # The MatUtils snapshot restore must rebuild per-face (multi-material)
        # shading, not just the primary SG -- C5M/Bistro meshes rely on this.
        cube = cmds.polyCube(name="unlitMultiMat")[0]
        long = cmds.ls(cube, long=True)[0]
        for nm, faces in (("mmA", "0:2"), ("mmB", "3:5")):
            mat = cmds.shadingNode("lambert", asShader=True, name=nm)
            sg = cmds.sets(
                renderable=True, noSurfaceShader=True, empty=True, name=f"{nm}SG"
            )
            cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
            cmds.sets(f"{cube}.f[{faces}]", edit=True, forceElement=sg)
        UvUtils.create_lightmap_uvs([cube], map_size=64, quiet=True)

        before = MatUtils.get_shading_assignments(cube)
        self.assertEqual(len(before), 2)  # genuinely multi-material

        baker = LightmapBaker(resolution=64)
        baker.commit_unlit({long: self.tex})
        self.assertEqual(len(MatUtils.get_shading_assignments(cube)), 1)  # collapsed

        baker.revert_unlit([long])
        after = MatUtils.get_shading_assignments(cube)
        norm = lambda d: {k: (sorted(v) if v else None) for k, v in d.items()}
        self.assertEqual(norm(after), norm(before))  # per-face shading rebuilt

    def test_commit_is_idempotent(self):
        # Re-committing a marked shape must be a no-op -- otherwise the second
        # commit captures the unlit state as "source" and revert can't recover.
        cube = cmds.polyCube(name="unlitIdem")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        long = cmds.ls(cube, long=True)[0]
        UvUtils.create_lightmap_uvs([cube], map_size=64, quiet=True)

        baker = LightmapBaker(resolution=64)
        baker.commit_unlit({long: self.tex})
        record = cmds.getAttr(f"{shape}.{LightmapBaker.COMMIT_ATTR}")
        second = baker.commit_unlit({long: self.tex})
        self.assertEqual(second, {})  # nothing newly committed
        self.assertEqual(  # record untouched (source still recoverable)
            cmds.getAttr(f"{shape}.{LightmapBaker.COMMIT_ATTR}"), record
        )

    def test_revert_all_marked_when_objects_none(self):
        longs = []
        for nm in ("revA", "revB"):
            cube = cmds.polyCube(name=nm)[0]
            longs.append(cmds.ls(cube, long=True)[0])
        UvUtils.create_lightmap_uvs(longs, map_size=64, quiet=True)
        baker = LightmapBaker(resolution=64)
        baker.commit_unlit({l: self.tex for l in longs})

        reverted = LightmapBaker().revert_unlit()  # None -> every marked mesh
        self.assertEqual(len(reverted), 2)

    def test_no_lightmap_set_leaves_uv_order_but_still_wires(self):
        cube = cmds.polyCube(name="unlitNoLm")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        long = cmds.ls(cube, long=True)[0]
        before = self._sets(shape)

        baker = LightmapBaker(resolution=64)
        wired = baker.commit_unlit({long: self.tex})

        self.assertEqual(self._sets(shape), before)  # no lightmap -> order kept
        self.assertIn(long, wired)  # material still assigned
        baker.revert_unlit([long])
        self.assertFalse(cmds.objExists(wired[long]))


class TestSeparated(MayaTkTestCase):
    """bake_separated -- opt-in white-card (lighting-only) irradiance path."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_sep_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @staticmethod
    def _sgs(shape):
        return cmds.listConnections(shape, type="shadingEngine") or []

    def _cube_with_known_material(self, name):
        cube = cmds.polyCube(name=name)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        mat = cmds.shadingNode("lambert", asShader=True, name=f"{name}_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_matSG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(shape, edit=True, forceElement=sg)
        return cube, shape, sg

    def test_white_card_applies_to_all_then_restores(self):
        cube, shape, known_sg = self._cube_with_known_material("sepCube")
        long = cmds.ls(cube, long=True)[0]
        orig = self._sgs(shape)
        self.assertIn(known_sg, orig)

        baker = LightmapBaker(resolution=64)
        state = baker._apply_white_card([long])
        _shader, white_sg, _prev = state
        self.assertIn(white_sg, self._sgs(shape))  # now on the white card
        self.assertNotIn(known_sg, self._sgs(shape))

        baker._restore_white_card(state)
        self.assertEqual(self._sgs(shape), orig)  # original shading back
        self.assertFalse(cmds.objExists(white_sg))  # temp nodes cleaned up

    @unittest.skipUnless(
        HAVE_CV2 and _arnold_loadable(), "mtoa/arnoldRenderToTexture or cv2 unavailable"
    )
    def test_bake_separated_produces_lightmap_and_restores_material(self):
        cube, shape, known_sg = self._cube_with_known_material("sepArnoldCube")
        result = LightmapBaker(resolution=64, samples=2).bake_separated(
            [cube], output_dir=self.tmp
        )
        self.assertTrue(result)
        out = _read(next(iter(result.values())))
        self.assertEqual(out.shape[2], 3)  # dilated opaque RGB irradiance
        # Original material restored; white card removed.
        self.assertFalse(cmds.objExists("lm_whitecard"))
        self.assertIn(known_sg, self._sgs(shape))


class TestTextureSetStem(MayaTkTestCase):
    """Lightmap output is named after the material's texture set, not the node.

    The user's mesh names are long, import-namespaced (e.g.
    ``Bistro_..._Flower_Pot_01A_2442``); the lightmap should follow the existing
    texture set (``Plants_Metal_Base_01`` → ``Plants_Metal_Base_01_Lightmap``).
    """

    def _cube_with_texture(self, name, tex_basename):
        cube = cmds.polyCube(name=name)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        mat = cmds.shadingNode("lambert", asShader=True, name=f"{name}_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(shape, edit=True, forceElement=sg)
        fn = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(
            f"{fn}.fileTextureName", f"C:/tex/{tex_basename}", type="string"
        )
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)
        return cmds.ls(cube, long=True)[0]

    def test_stem_from_material_texture_set(self):
        long = self._cube_with_texture("nodeName", "Plants_Metal_Base_01_BaseColor.dds")
        self.assertEqual(
            LightmapBaker._texture_set_stem(long), "Plants_Metal_Base_01"
        )

    def test_stem_none_without_textures(self):
        cube = cmds.polyCube(name="noTexCube")[0]
        long = cmds.ls(cube, long=True)[0]
        self.assertIsNone(LightmapBaker._texture_set_stem(long))

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_bake_separated_snapshots_real_stem_before_white_card(self):
        # The white card hides the real material at bake time; the stem dict
        # passed to the baker must be snapshotted from the REAL textures first.
        tmp = tempfile.mkdtemp(prefix="lm_stem_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        long = self._cube_with_texture("sepStem", "Crate_Wood_01_Albedo.png")
        fake = _FakeBaker()
        LightmapBaker(resolution=64, baker=fake).bake_separated([long], output_dir=tmp)
        self.assertEqual(fake.called_stem, {long: "Crate_Wood_01"})


class TestCommitLightmap(MayaTkTestCase):
    """commit_lightmap / revert_lightmap — lighting-only: maps preserved.

    No renderer needed: commit_lightmap only stamps per-shape markers and
    publishes the ``data_export`` manifest, so a dummy texture path exercises
    the wiring. The key guarantee is that the material and UV order are left
    untouched (the user's complaint: fused mode threw the PBR maps away).
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_meta_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tex = os.path.join(self.tmp, "cube_Lightmap.exr")
        open(self.tex, "wb").close()  # path only; contents irrelevant here

    @staticmethod
    def _sgs(shape):
        return cmds.listConnections(shape, type="shadingEngine") or []

    @staticmethod
    def _sets(shape):
        return cmds.polyUVSet(shape, query=True, allUVSets=True) or []

    @staticmethod
    def _marked(shape):
        return cmds.attributeQuery(
            LightmapBaker.LIGHTMAP_INFO_ATTR, node=shape, exists=True
        )

    def _manifest(self):
        from mayatk.node_utils.data_nodes import DataNodes

        raw = DataNodes.get_export_string(LightmapBaker.LIGHTMAP_METADATA)
        return json.loads(raw) if raw else {"objects": []}

    def _cube_with_material(self, name):
        cube = cmds.polyCube(name=name)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        mat = cmds.shadingNode("lambert", asShader=True, name=f"{name}_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(shape, edit=True, forceElement=sg)
        return cube, shape, cmds.ls(cube, long=True)[0]

    def test_commit_keeps_material_and_uvs_and_stamps_manifest(self):
        cube, shape, long = self._cube_with_material("lmKeep")
        UvUtils.create_lightmap_uvs([cube], map_size=64, quiet=True)
        before_sgs, before_sets = self._sgs(shape), self._sets(shape)
        self.assertEqual(before_sets.index("lightmap"), 1)  # texture stays UV0

        baker = LightmapBaker(resolution=64)
        recorded = baker.commit_lightmap({long: self.tex}, intensity=1.5)
        self.assertIn(long, recorded)

        # The whole point: material + UV order are untouched (maps preserved).
        self.assertEqual(self._sgs(shape), before_sgs)
        self.assertEqual(self._sets(shape), before_sets)
        self.assertTrue(self._marked(shape))  # per-shape marker stamped

        # Scene-wide manifest on the data_export carrier (rides the FBX).
        objs = self._manifest()["objects"]
        self.assertEqual(len(objs), 1)
        rec = objs[0]
        self.assertEqual(rec["name"], "lmKeep")
        self.assertEqual(rec["map"], "cube_Lightmap.exr")
        self.assertEqual(rec["uvIndex"], 1)
        self.assertEqual(rec["intensity"], 1.5)
        self.assertEqual(rec["scaleOffset"], [1.0, 1.0, 0.0, 0.0])

        # Revert drops the marker + empties the manifest; material still intact.
        baker.revert_lightmap([long])
        self.assertFalse(self._marked(shape))
        self.assertEqual(self._manifest()["objects"], [])
        self.assertEqual(self._sgs(shape), before_sgs)

    def test_publish_is_additive_across_separate_bakes(self):
        longs = []
        for nm in ("addA", "addB"):
            _, _, long = self._cube_with_material(nm)
            longs.append(long)
        UvUtils.create_lightmap_uvs(longs, map_size=64, quiet=True)
        baker = LightmapBaker(resolution=64)
        baker.commit_lightmap({longs[0]: self.tex})  # bake A
        baker.commit_lightmap({longs[1]: self.tex})  # a later, separate bake B
        names = {o["name"] for o in self._manifest()["objects"]}
        self.assertEqual(names, {"addA", "addB"})  # both still in the manifest

    def test_unified_revert_clears_lighting_only_marker(self):
        # revert() must handle the lighting-only marker too, not just unlit
        # commits (the panel + pre-bake clear rely on this).
        cube, shape, long = self._cube_with_material("lmBoth")
        UvUtils.create_lightmap_uvs([cube], map_size=64, quiet=True)
        baker = LightmapBaker(resolution=64)
        baker.commit_lightmap({long: self.tex})
        self.assertTrue(self._marked(shape))

        self.assertTrue(baker.revert([long]))
        self.assertFalse(self._marked(shape))
        self.assertEqual(self._manifest()["objects"], [])


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestPackAtlas(MayaTkTestCase):
    """pack_atlas — group by primary material, area-weighted atlas + scaleOffset.

    Needs cv2 (EXR IO/resize) but no renderer: synthetic per-object EXRs stand
    in for the bake output, so the grouping / packing / consolidation logic is
    exercised deterministically.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_atlas_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _solid_exr(self, name, color):
        cv2, np = _cv2()
        path = os.path.join(self.tmp, name)
        img = np.zeros((8, 8, 3), np.float32)
        img[...] = color
        cv2.imwrite(path, img)
        return path

    @staticmethod
    def _make_sg(name):
        mat = cmds.shadingNode("lambert", asShader=True, name=f"{name}_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        return sg, mat

    @staticmethod
    def _cube_on_sg(name, sg, tex_basename=None):
        cube = cmds.polyCube(name=name)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.sets(shape, edit=True, forceElement=sg)
        if tex_basename:
            mat = cmds.listConnections(f"{sg}.surfaceShader")[0]
            if not cmds.listConnections(f"{mat}.color", source=True):
                fn = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
                cmds.setAttr(
                    f"{fn}.fileTextureName", f"C:/tex/{tex_basename}", type="string"
                )
                cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)
        return cmds.ls(cube, long=True)[0]

    def test_groups_by_material_one_atlas_per_group(self):
        sgM, _ = self._make_sg("MatM")
        sgN, _ = self._make_sg("MatN")
        a = self._cube_on_sg("atlasA", sgM, "Wood_Base_01_BaseColor.png")
        b = self._cube_on_sg("atlasB", sgM)
        c = self._cube_on_sg("atlasC", sgN, "Metal_Base_01_BaseColor.png")
        mapping = {
            a: self._solid_exr("atlasA.exr", (0, 0, 1)),
            b: self._solid_exr("atlasB.exr", (0, 1, 0)),
            c: self._solid_exr("atlasC.exr", (1, 0, 0)),
        }
        out = LightmapBaker(resolution=16).pack_atlas(mapping, output_dir=self.tmp)

        self.assertEqual(set(out), {a, b, c})
        atlas_a, so_a = out[a]
        atlas_b, so_b = out[b]
        atlas_c, so_c = out[c]
        self.assertEqual(atlas_a, atlas_b)        # same material -> consolidated
        self.assertNotEqual(atlas_a, atlas_c)     # different material -> own map
        self.assertTrue(os.path.exists(atlas_a))
        self.assertTrue(os.path.exists(atlas_c))
        # Two-object group -> real (non-identity) rects.
        self.assertNotEqual(so_a, [1.0, 1.0, 0.0, 0.0])
        self.assertNotEqual(so_b, [1.0, 1.0, 0.0, 0.0])
        # One-object group -> identity rect.
        self.assertEqual(so_c, [1.0, 1.0, 0.0, 0.0])
        # Atlas named after the group's texture-set base.
        self.assertEqual(os.path.basename(atlas_a), "Wood_Base_01_Lightmap.exr")
        # The consolidated per-object source maps were removed.
        self.assertFalse(os.path.exists(mapping[a]))
        self.assertFalse(os.path.exists(mapping[b]))

    def test_atlas_name_does_not_clobber_another_groups_source(self):
        # Duplicated-material scenario: two materials share a texture set (same
        # stem) but are different groups. The multi-object group's atlas name
        # must not overwrite the single-object group's not-yet-consumed source
        # map (which is deliberately named to collide). Regression.
        sgM, _ = self._make_sg("DupM")
        sgN, _ = self._make_sg("DupN")
        a1 = self._cube_on_sg("dupA1", sgM, "Shared_BaseColor.png")
        a2 = self._cube_on_sg("dupA2", sgM)
        b = self._cube_on_sg("dupB", sgN, "Shared_BaseColor.png")
        # Insertion order -> the multi group (M) is processed before single (N).
        # b's source is named to collide with M's atlas ("Shared_Lightmap.exr").
        mapping = {
            a1: self._solid_exr("dupA1.exr", (0, 0, 1)),
            a2: self._solid_exr("dupA2.exr", (0, 1, 0)),
            b: self._solid_exr("Shared_Lightmap.exr", (0.25, 0.25, 0.25)),
        }
        out = LightmapBaker(resolution=16).pack_atlas(mapping, output_dir=self.tmp)

        # b's map must still be ITS content (uniform 0.25), not M's red/green atlas.
        cv2, _ = _cv2()
        b_img = cv2.imread(out[b][0], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        self.assertAlmostEqual(float(b_img.min()), 0.25, places=3)
        self.assertAlmostEqual(float(b_img.max()), 0.25, places=3)
        # The two groups wrote distinct files.
        self.assertNotEqual(out[b][0], out[a1][0])

    def test_single_object_group_renamed_not_reencoded(self):
        sg, _ = self._make_sg("Solo")
        a = self._cube_on_sg("solo", sg, "Solo_Base_BaseColor.png")
        src = self._solid_exr("solo_raw.exr", (0.5, 0.5, 0.5))
        out = LightmapBaker(resolution=16).pack_atlas({a: src}, output_dir=self.tmp)
        atlas, so = out[a]
        self.assertEqual(so, [1.0, 1.0, 0.0, 0.0])
        self.assertEqual(os.path.basename(atlas), "Solo_Base_Lightmap.exr")
        self.assertTrue(os.path.exists(atlas))
        self.assertFalse(os.path.exists(src))  # renamed, not left behind

    def test_surface_area_and_primary_material(self):
        sg, _ = self._make_sg("Area")
        a = self._cube_on_sg("areaCube", sg)
        self.assertGreater(LightmapBaker._surface_area(a), 0.0)
        self.assertEqual(LightmapBaker._primary_material(a), sg)

    def test_scale_offsets_ride_manifest(self):
        sg, _ = self._make_sg("MatRide")
        a = self._cube_on_sg("rideA", sg)
        b = self._cube_on_sg("rideB", sg)
        UvUtils.create_lightmap_uvs([a, b], map_size=16, quiet=True)
        mapping = {
            a: self._solid_exr("rideA.exr", (0, 0, 1)),
            b: self._solid_exr("rideB.exr", (0, 1, 0)),
        }
        baker = LightmapBaker(resolution=16)
        out = baker.pack_atlas(mapping, output_dir=self.tmp)
        baker.commit_lightmap(
            {o: p for o, (p, _so) in out.items()},
            scale_offsets={o: so for o, (_p, so) in out.items()},
        )
        from mayatk.node_utils.data_nodes import DataNodes

        objs = json.loads(
            DataNodes.get_export_string(LightmapBaker.LIGHTMAP_METADATA)
        )["objects"]
        self.assertEqual(len(objs), 2)
        # The atlased objects carry real (non-identity) scaleOffset rects.
        self.assertTrue(
            any(o["scaleOffset"] != [1.0, 1.0, 0.0, 0.0] for o in objs)
        )


class TestLightmapPresets(unittest.TestCase):
    """Quality-tier presets via pythontk PresetStore (no Maya/Arnold needed)."""

    def test_builtin_tiers_listed(self):
        names = LightmapBaker.preset_store().list()
        for tier in ("preview", "quest", "desktop"):
            self.assertIn(tier, names)

    def test_from_preset_sets_resolution_and_samples(self):
        baker = LightmapBaker.from_preset("desktop")
        self.assertEqual(baker.resolution, 2048)
        self.assertEqual(baker.samples, 8)
        # The injected default baker inherits the resolution.
        self.assertEqual(baker.baker.resolution, 2048)

    def test_overrides_win_over_preset(self):
        baker = LightmapBaker.from_preset("quest", resolution=1536)
        self.assertEqual(baker.resolution, 1536)  # override
        self.assertEqual(baker.samples, 4)  # from preset

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            LightmapBaker.from_preset("does_not_exist")


# ---------------------------------------------------------------------------
# UI slots: dispatch logic only (the panel itself can't load headlessly under
# the offscreen QPA). A fake workflow stands in for LightmapBaker so the tests
# verify each button routes to the right workflow method with the dials' values
# -- no Arnold, no Qt.
# ---------------------------------------------------------------------------


class _Spin:
    def __init__(self, v):
        self._v = v

    def value(self):
        return self._v

    def setValue(self, v):
        self._v = v

    def blockSignals(self, _b):
        pass


class _PresetCombo:
    def __init__(self, name):
        self._name = name

    def currentText(self):
        return self._name


class _ModeCombo:
    """Bake-level (Mode) combobox stub: defaults to Lighting Only."""

    def __init__(self, text="Lighting Only (keep maps)"):
        self._text = text

    def currentText(self):
        return self._text


class _PackingCombo:
    """Packing combobox stub: defaults to Per-Object (the safe default)."""

    def __init__(self, text="Per-Object (one map each)"):
        self._text = text

    def currentText(self):
        return self._text


class _ProgressCtx:
    """Stub of Footer.progress(): records each update() tick."""

    def __init__(self, footer, total, text):
        self._footer = footer
        footer.progress_calls.append(("start", total, text))

    def __enter__(self):
        def update(value=None, text=None):
            self._footer.progress_calls.append(("tick", value, text))
            return True  # not cancelled

        return update

    def __exit__(self, *exc):
        return False


class _Footer:
    def __init__(self):
        self.text = ""
        self.progress_calls = []

    def setText(self, t):
        self.text = t

    def progress(self, total=None, text=""):
        return _ProgressCtx(self, total, text)


class _LineEdit:
    """Affix-field stub: text() + an empty option_box.menu (mode -> 'auto')."""

    class _Menu:
        pass

    class _OptionBox:
        def __init__(self):
            self.menu = _LineEdit._Menu()

    def __init__(self, text="_Lightmap"):
        self._text = text
        self.option_box = _LineEdit._OptionBox()

    def text(self):
        return self._text


class _SlotUi:
    def __init__(
        self, res=1024, samples=4, affix="_Lightmap",
        mode="Lighting Only (keep maps)", packing="Per-Object (one map each)",
    ):
        self.footer = _Footer()
        self.spn_resolution = _Spin(res)
        self.spn_samples = _Spin(samples)
        self.txt000 = _LineEdit(affix)
        self.cmb001 = _ModeCombo(mode)
        self.cmb002 = _PackingCombo(packing)


class _FakeWorkflow:
    """Records each call; stands in for LightmapBaker (no Arnold/UV work)."""

    instances: list = []

    def __init__(self, resolution=None, samples=None):
        self.resolution = resolution
        self.samples = samples
        self.calls: list = []
        _FakeWorkflow.instances.append(self)

    def revert(self, objects=None):  # unified revert (fused + lighting-only)
        self.calls.append(("revert", tuple(objects) if objects else None))
        return list(objects) if objects else []

    def _record_bake(self, kind, objects, output_dir, prefix, suffix, on_progress):
        self.calls.append((kind, tuple(objects)))
        self.bake_output_dir = output_dir
        self.bake_prefix = prefix
        self.bake_suffix = suffix
        if on_progress:  # exercise the per-object progress wiring
            for i, o in enumerate(objects):
                on_progress(i, len(objects), o.rsplit("|", 1)[-1])
        return {objects[0]: r"C:/out/lightmap_x.exr"}

    def bake_separated(
        self, objects, output_dir=None, prefix="", suffix="", on_progress=None,
        create_uvs=True, dilate=True,
    ):
        return self._record_bake(
            "bake_separated", objects, output_dir, prefix, suffix, on_progress
        )

    def bake_fused(
        self, objects, output_dir=None, prefix="", suffix="", on_progress=None,
        create_uvs=True, dilate=True,
    ):
        return self._record_bake(
            "bake_fused", objects, output_dir, prefix, suffix, on_progress
        )

    def commit_lightmap(self, mapping, intensity=1.0, scale_offsets=None):
        self.calls.append(("commit_lightmap", dict(mapping)))
        self.commit_scale_offsets = scale_offsets
        return mapping

    def commit_unlit(self, mapping):
        self.calls.append(("commit_unlit", dict(mapping)))
        return mapping

    def pack_atlas(self, mapping, output_dir=None, prefix="", suffix="_Lightmap"):
        # One shared atlas for every object, each with a distinct rect.
        self.calls.append(("pack_atlas", dict(mapping)))
        atlas = os.path.join(output_dir or "C:/out", f"Mat{suffix}.exr")
        objs = list(mapping)
        return {
            o: (atlas, [1.0, 1.0 / len(objs), 0.0, i / len(objs)])
            for i, o in enumerate(objs)
        }


class TestLightmapBakerSlots(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        # b000 builds LightmapBaker(...) from the module globals -- swap in the
        # recorder. Restored after each test.
        self._orig_cls = lmb_module.LightmapBaker
        lmb_module.LightmapBaker = _FakeWorkflow
        _FakeWorkflow.instances = []
        self.addCleanup(setattr, lmb_module, "LightmapBaker", self._orig_cls)

    def _slots(self, ui):
        # __new__ skips the Qt-touching __init__ (loaded_ui access, QTimer).
        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s.ui = ui
        s._last_output_dir = None
        s._baker = None
        return s

    def _select_cube(self, name="slotCube"):
        cube = cmds.polyCube(name=name)[0]
        cmds.select(cube, replace=True)
        return cmds.ls(cube, long=True)[0]

    def test_b000_default_lighting_only_reverts_bakes_commits(self):
        # Default Mode is Lighting Only: revert → bake_separated → commit_lightmap
        # (keeps the PBR maps), NOT the fused/unlit path.
        long = self._select_cube()
        ui = _SlotUi(res=2048, samples=8)
        s = self._slots(ui)
        s.b000()

        self.assertEqual(len(_FakeWorkflow.instances), 1)
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.resolution, 2048)  # dials drive the workflow
        self.assertEqual(baker.samples, 8)
        # Order matters: revert the source first, bake, then commit the result.
        self.assertEqual(
            baker.calls,
            [
                ("revert", (long,)),
                ("bake_separated", (long,)),
                ("commit_lightmap", {long: r"C:/out/lightmap_x.exr"}),
            ],
        )
        # The bake is directed at the project's sourceimages (or the workflow
        # default when there's no project) -- same resolver the slot uses.
        self.assertEqual(baker.bake_output_dir, LightmapBakerSlots._sourceimages_dir())
        self.assertIn("Baked", ui.footer.text)

    def test_b000_fused_mode_routes_to_unlit(self):
        # Fused Unlit Mode: revert → bake_fused → commit_unlit (the drop-maps
        # path), explicitly opted into via the Mode combobox.
        long = self._select_cube()
        ui = _SlotUi(mode="Fused Unlit (single map)")
        s = self._slots(ui)
        s.b000()

        baker = _FakeWorkflow.instances[0]
        self.assertEqual(
            baker.calls,
            [
                ("revert", (long,)),
                ("bake_fused", (long,)),
                ("commit_unlit", {long: r"C:/out/lightmap_x.exr"}),
            ],
        )

    def test_b000_atlas_packing_consolidates_and_commits_with_scale_offsets(self):
        # Lighting Only + Atlas by Material: revert → bake_separated → pack_atlas
        # → commit_lightmap, and the per-object scaleOffset rects reach the commit.
        long = self._select_cube()
        ui = _SlotUi(packing="Atlas by Material (shared map)")
        s = self._slots(ui)
        s.b000()

        baker = _FakeWorkflow.instances[0]
        kinds = [c[0] for c in baker.calls]
        self.assertEqual(
            kinds, ["revert", "bake_separated", "pack_atlas", "commit_lightmap"]
        )
        # The commit was given the atlas rects (not None / identity-by-omission).
        self.assertIn(long, baker.commit_scale_offsets)
        self.assertEqual(baker.commit_scale_offsets[long], [1.0, 1.0, 0.0, 0.0])
        self.assertIn("atlas", ui.footer.text.lower())

    def test_b000_per_object_packing_skips_atlas(self):
        # Default Per-Object packing must NOT call pack_atlas.
        self._select_cube()
        ui = _SlotUi(packing="Per-Object (one map each)")
        s = self._slots(ui)
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertNotIn("pack_atlas", [c[0] for c in baker.calls])

    def test_b000_fused_ignores_atlas_packing(self):
        # Atlas applies to Lighting Only only; Fused mode never packs an atlas.
        self._select_cube()
        ui = _SlotUi(mode="Fused Unlit (single map)",
                     packing="Atlas by Material (shared map)")
        s = self._slots(ui)
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertNotIn("pack_atlas", [c[0] for c in baker.calls])
        self.assertIn("commit_unlit", [c[0] for c in baker.calls])

    def test_b000_drives_footer_progress(self):
        # The bake feedback goes through the footer's progress bar (one tick
        # per object), not a popup dialog.
        longs = [self._select_cube("pbA")]
        cmds.select(longs, replace=True)
        ui = _SlotUi()
        s = self._slots(ui)
        s.b000()
        ticks = [c for c in ui.footer.progress_calls if c[0] == "tick"]
        self.assertEqual(len(ticks), len(longs))  # one footer tick per object

    def test_b000_passes_resolved_affix(self):
        # The name-affix field ("_Lightmap", leading "_" -> suffix) reaches the
        # bake as (prefix="", suffix="_Lightmap"), so output is <object>_Lightmap.
        self._select_cube()
        s = self._slots(_SlotUi(affix="_Lightmap"))
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.bake_prefix, "")
        self.assertEqual(baker.bake_suffix, "_Lightmap")

    def test_b000_affix_prefix_mode(self):
        # Trailing "_" ("LM_") resolves as a prefix.
        self._select_cube()
        s = self._slots(_SlotUi(affix="LM_"))
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.bake_prefix, "LM_")
        self.assertEqual(baker.bake_suffix, "")

    def test_b000_no_selection_is_guarded(self):
        cmds.select(clear=True)
        ui = _SlotUi()
        s = self._slots(ui)
        s.b000()
        self.assertEqual(_FakeWorkflow.instances, [])  # never built a baker
        self.assertIn("Select", ui.footer.text)

    def test_b000_no_output_skips_commit(self):
        self._select_cube()
        s = self._slots(_SlotUi())  # default Lighting Only -> bake_separated
        # Make the fake bake return nothing.
        with_empty = lambda self_, objects, **k: {}
        self.addCleanup(
            setattr, _FakeWorkflow, "bake_separated", _FakeWorkflow.bake_separated
        )
        _FakeWorkflow.bake_separated = with_empty
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertFalse([c for c in baker.calls if c[0].startswith("commit")])
        self.assertIn("no output", s.ui.footer.text)

    def test_cmb000_loads_preset_into_dials(self):
        # Uses the REAL preset store (restore the class for this test).
        lmb_module.LightmapBaker = self._orig_cls
        ui = _SlotUi(res=1, samples=1)
        s = self._slots(ui)
        s.cmb000(0, _PresetCombo("desktop"))
        self.assertEqual(ui.spn_resolution.value(), 2048)
        self.assertEqual(ui.spn_samples.value(), 8)

    def test_revert_to_source_routes_selection(self):
        long = self._select_cube()
        s = self._slots(_SlotUi())
        s.revert_to_source()
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.calls, [("revert", (long,))])
        self.assertIn("Reverted", s.ui.footer.text)

    def test_revert_to_source_all_when_no_selection(self):
        cmds.select(clear=True)
        s = self._slots(_SlotUi())
        s.revert_to_source()
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.calls, [("revert", None)])  # None -> all marked


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestDilateLightmap))
    suite.addTests(loader.loadTestsFromTestCase(TestLightmapBakerComposition))
    suite.addTests(loader.loadTestsFromTestCase(TestCommitUnlit))
    suite.addTests(loader.loadTestsFromTestCase(TestSeparated))
    suite.addTests(loader.loadTestsFromTestCase(TestTextureSetStem))
    suite.addTests(loader.loadTestsFromTestCase(TestCommitLightmap))
    suite.addTests(loader.loadTestsFromTestCase(TestPackAtlas))
    suite.addTests(loader.loadTestsFromTestCase(TestLightmapPresets))
    suite.addTests(loader.loadTestsFromTestCase(TestLightmapBakerSlots))
    suite.addTests(loader.loadTestsFromTestCase(TestLightmapBakerArnold))
    return unittest.TextTestRunner(verbosity=2).run(suite)


if __name__ == "__main__":
    run_tests()
