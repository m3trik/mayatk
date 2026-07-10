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
from unittest import mock

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import maya.cmds as cmds
import pythontk as ptk
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


def _rendered_warnings(warn_mock):
    """Interpolate a mocked logger's lazy '%s'-style calls into real messages."""
    return [
        str(c.args[0]) % tuple(c.args[1:]) if len(c.args) > 1 else str(c.args[0])
        for c in warn_mock.call_args_list
    ]


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
    """Stands in for TextureBaker: records the call, emits a synthetic EXR.

    The white card only exists during the bake (bake_separated deletes it
    after), so its attributes are snapshotted here AT bake time.
    """

    def __init__(self):
        self.called_uv_set = None
        self.called_stem = None
        self.called_on_progress = None
        self.called_shader = None
        self.called_batch = None
        self.card_seen_at_bake = False
        self.card_color = None
        self.card_diffuse = None

    def bake(
        self, objects, output_dir=None, prefix="", suffix="", backend="",
        uv_set=None, on_progress=None, stem=None, shader=None, batch=False,
    ):
        self.called_uv_set = uv_set
        self.called_stem = stem
        self.called_on_progress = on_progress
        self.called_shader = shader
        self.called_batch = batch
        self.card_seen_at_bake = bool(shader) and cmds.objExists(shader)
        if self.card_seen_at_bake:
            self.card_color = cmds.getAttr(f"{shader}.color")[0]
            self.card_diffuse = cmds.getAttr(f"{shader}.diffuse")
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

    def test_partial_coverage_texels_are_unpremultiplied(self):
        # MEASURED (mtoa 5.4.5): RTT premultiplies RGB by texel coverage --
        # an island-edge texel at alpha 0.5 carries HALF the true lighting,
        # and dilation then smears that dark fringe into the gutter. The
        # dilate pass must divide partial texels by alpha first.
        cv2, np = _cv2()
        p = os.path.join(self.tmp, "premul.exr")
        img = np.zeros((4, 4, 4), np.float32)
        img[..., :3] = 0.8
        img[..., 3] = 1.0
        img[0, 0, :3] = 0.4  # premultiplied edge texel ...
        img[0, 0, 3] = 0.5   # ... at half coverage
        img[3, 3, :3] = 0.0  # true background
        img[3, 3, 3] = 0.0
        cv2.imwrite(p, img)
        LightmapBaker._dilate_lightmap(p, alpha_threshold=1e-3, iterations=-1)
        out = _read(p)
        # The half-covered texel now carries full-strength lighting.
        self.assertAlmostEqual(float(out[0, 0, 0]), 0.8, places=3)
        # Interior untouched; background filled from full-strength values.
        self.assertAlmostEqual(float(out[1, 1, 0]), 0.8, places=3)
        self.assertAlmostEqual(float(out[3, 3, 0]), 0.8, places=3)

    def test_non_finite_texels_are_sanitized_on_write(self):
        # One bad ray (NaN / inf) in a raw bake must not survive into the
        # shipped map -- it would spread through dilation / atlas resize and
        # a float32 firefly above half-max becomes inf in the half encode.
        cv2, np = _cv2()
        p = os.path.join(self.tmp, "nan.exr")
        img = np.zeros((4, 4, 4), np.float32)
        img[..., :3] = 0.5
        img[..., 3] = 1.0  # fully covered -> no dilation, straight to write
        img[1, 1, 0] = np.nan
        img[2, 2, 1] = np.inf
        cv2.imwrite(p, img)
        self.assertTrue(
            LightmapBaker._dilate_lightmap(p, alpha_threshold=1e-3, iterations=-1)
        )
        out = _read(p)
        self.assertTrue(np.isfinite(out).all())
        self.assertLessEqual(float(out.max()), LightmapBaker._HALF_MAX)
        self.assertAlmostEqual(float(out[0, 0, 0]), 0.5, places=3)  # good texels kept


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

    @staticmethod
    def _assign_lambert(obj, name, color):
        mat = cmds.shadingNode("lambert", asShader=True, name=name)
        cmds.setAttr(f"{mat}.color", *color, type="double3")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(obj, edit=True, forceElement=sg)
        return mat

    def test_white_card_irradiance_is_albedo_normalized(self):
        # PIXEL-VALUE regression (the grey-card bug): a plane under a
        # perpendicular intensity-1 directional light must bake to ~1/pi
        # (Arnold stores radiance = albedo * E / pi; the card normalizes
        # albedo to 1) REGARDLESS of the source material's albedo. The
        # un-fixed lambert card (Kd 0.8) read ~0.25 here instead of ~0.318.
        plane = cmds.polyPlane(name="irrPlane", w=1, h=1, sx=1, sy=1)[0]
        self._assign_lambert(plane, "irrDark", (0.1, 0.1, 0.1))  # dark source
        light = cmds.directionalLight(intensity=1.0)
        cmds.setAttr(
            f"{cmds.listRelatives(light, parent=True)[0]}.rotateX", -90
        )
        result = LightmapBaker(resolution=32, samples=3).bake_separated(
            [plane], output_dir=self.tmp
        )
        out = _read(next(iter(result.values())))
        mean = float(out.mean())
        self.assertGreater(mean, 0.28, f"lightmap too dark: {mean:.4f}")
        self.assertLess(mean, 0.35, f"lightmap too bright: {mean:.4f}")

    def test_fused_equals_effective_albedo_times_separated(self):
        # END-TO-END composite invariant: Unity multiplies albedo x lightmap,
        # so for a solid-color lambert the fused bake (albedo x lighting) must
        # equal effective_albedo x the separated (lighting-only) bake per
        # channel. If any stage leaks albedo into the white-card map, applies
        # a transfer curve, or mixes the modes, these ratios break.
        plane = cmds.polyPlane(name="compPlane", w=1, h=1, sx=1, sy=1)[0]
        mat = self._assign_lambert(plane, "compMat", (0.4, 0.5, 0.6))
        light = cmds.directionalLight(intensity=1.0)
        cmds.setAttr(
            f"{cmds.listRelatives(light, parent=True)[0]}.rotateX", -90
        )
        kd = cmds.getAttr(f"{mat}.diffuse")  # lambert Kd (0.8 default)
        effective = [0.4 * kd, 0.5 * kd, 0.6 * kd]

        baker = LightmapBaker(resolution=32, samples=3)
        sep = baker.bake_separated([plane], output_dir=self.tmp)
        fus = baker.bake_fused([plane], output_dir=self.tmp)
        s = _read(next(iter(sep.values())))
        f = _read(next(iter(fus.values())))
        for ch, expected in zip(range(3), reversed(effective)):  # cv2 is BGR
            ratio = float(f[..., ch].mean()) / float(s[..., ch].mean())
            self.assertAlmostEqual(
                ratio, expected, delta=0.03,
                msg=f"channel {ch}: fused/separated={ratio:.4f}, "
                    f"expected effective albedo {expected:.4f}",
            )

    def test_gi_bounce_color_bleed_and_depth_pinning(self):
        # Two regressions in one scene: (1) per-object carding -- the red wall
        # keeps its REAL material during the floor's bake, so the floor's
        # indirect is red (an all-at-once white card bounces white); (2) the
        # GI render-settings pin -- gi_depth=0 must kill the bounce (if the
        # pin never reached the scene, Arnold's 1-bounce default would leak
        # red into the depth-0 bake too).
        def build_scene():
            floor = cmds.polyPlane(name="giFloor", w=2, h=2, sx=1, sy=1)[0]
            wall = cmds.polyPlane(name="giWall", w=2, h=2, sx=1, sy=1)[0]
            cmds.setAttr(f"{wall}.rotateX", 90)  # vertical, facing +Z
            cmds.setAttr(f"{wall}.translateZ", -1)
            cmds.setAttr(f"{wall}.translateY", 1)
            self._assign_lambert(floor, "giFloorMat", (0.5, 0.5, 0.5))
            self._assign_lambert(wall, "giWallMat", (1.0, 0.0, 0.0))
            # Default directional aims -Z: frontal on the wall, grazing
            # (zero direct) on the floor -- the floor sees only the bounce.
            cmds.directionalLight(intensity=1.0)
            return floor, wall

        floor, wall = build_scene()
        lit = LightmapBaker(
            resolution=32, samples=3, gi_depth=2, gi_samples=4
        ).bake_separated([floor, wall], output_dir=self.tmp)
        floor_key = next(k for k in lit if "giFloor" in k)
        bounced = _read(lit[floor_key])
        red = float(bounced[..., 2].mean())    # cv2 is BGR
        green = float(bounced[..., 1].mean())
        self.assertGreater(red, 1e-3, "no indirect light reached the floor")
        self.assertGreater(
            red, 3.0 * max(green, 1e-6),
            "bounce is not red -- neighbor materials were not preserved "
            "during the floor's bake (per-object carding regression)",
        )

        dark = LightmapBaker(
            resolution=32, samples=3, gi_depth=0, gi_samples=2
        ).bake_separated([floor], output_dir=os.path.join(self.tmp, "d0"))
        red0 = float(_read(next(iter(dark.values())))[..., 2].mean())
        self.assertLess(
            red0, 0.25 * red,
            "gi_depth=0 did not kill the bounce -- render_settings were "
            "not pinned onto defaultArnoldRenderOptions for the bake",
        )


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

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_bake_separated_passes_true_white_card_shader_and_cleans_up(self):
        # The card rides the bake as Arnold's per-shape -shader override
        # (measured: only the shape being baked wears it) -- the scene's
        # shading is NEVER touched and the card is deleted afterward. Kd must
        # be pinned to 1.0 (lambert defaults to 0.8 = a grey card = maps ~20%
        # dark, measured 0.8006).
        cube, shape, known_sg = self._cube_with_known_material("sepCube")
        long = cmds.ls(cube, long=True)[0]
        orig = self._sgs(shape)
        self.assertIn(known_sg, orig)

        fake = _FakeBaker()
        tmp = tempfile.mkdtemp(prefix="lm_card_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        LightmapBaker(resolution=64, baker=fake).bake_separated(
            [long], output_dir=tmp
        )

        self.assertTrue(fake.card_seen_at_bake, "no live shader reached the bake")
        self.assertEqual(tuple(fake.card_color), (1.0, 1.0, 1.0))
        self.assertAlmostEqual(fake.card_diffuse, 1.0)
        self.assertTrue(fake.called_batch)  # batched by default (7.45x)
        self.assertEqual(self._sgs(shape), orig)  # shading never touched
        self.assertFalse(cmds.ls("lm_whitecard*"))  # card cleaned up

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_bake_separated_forwards_progress_and_batch_opt_out(self):
        cube, _, _ = self._cube_with_known_material("progA")
        long = cmds.ls(cube, long=True)[0]
        tmp = tempfile.mkdtemp(prefix="lm_prog_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        fake = _FakeBaker()
        cb = lambda done, total, name: True
        LightmapBaker(resolution=64, baker=fake).bake_separated(
            [long], output_dir=tmp, on_progress=cb, batch=False
        )
        # Progress ticks come from TextureBaker now -- the callback must
        # reach it verbatim, and the batch opt-out must be honored.
        self.assertIs(fake.called_on_progress, cb)
        self.assertFalse(fake.called_batch)

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
    def test_bake_separated_stem_resolves_real_texture_set(self):
        # The -shader override never swaps materials, so the default stem
        # resolver (a callable) sees the REAL textures at bake time and must
        # resolve the material's texture-set base.
        tmp = tempfile.mkdtemp(prefix="lm_stem_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        long = self._cube_with_texture("sepStem", "Crate_Wood_01_Albedo.png")
        fake = _FakeBaker()
        LightmapBaker(resolution=64, baker=fake).bake_separated([long], output_dir=tmp)
        self.assertTrue(callable(fake.called_stem))
        self.assertEqual(fake.called_stem(long), "Crate_Wood_01")


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

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_intensity_is_applied_to_texels_once_per_file(self):
        # Unity never applies the manifest intensity (LightmapData has no
        # multiplier), so commit bakes it into the texels -- once per unique
        # file, even when several objects share one atlas.
        cv2, np = _cv2()
        _, _, la = self._cube_with_material("intA")
        _, _, lb = self._cube_with_material("intB")
        shared = os.path.join(self.tmp, "shared_Lightmap.exr")
        cv2.imwrite(shared, np.full((4, 4, 3), 0.25, np.float32))

        baker = LightmapBaker(resolution=64)
        baker.commit_lightmap({la: shared, lb: shared}, intensity=2.0)

        out = cv2.imread(shared, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        self.assertAlmostEqual(float(out.mean()), 0.5, places=3)  # x2, not x4
        objs = self._manifest()["objects"]
        self.assertEqual(len(objs), 2)
        self.assertTrue(all(o["intensity"] == 2.0 for o in objs))

    def test_manifest_publishes_real_uv_index_and_warns(self):
        # The manifest must carry the lightmap set's ACTUAL channel index --
        # Unity samples uv2 (index 1) only, so a hardcoded 1 would hide a
        # mis-ordered set instead of surfacing it.
        cube, shape, long = self._cube_with_material("lmUvIdx")
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="filler")
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="UV2")
        # Sets: [map1, filler, UV2] -> the name-matched lightmap sits at index 2.
        baker = LightmapBaker(resolution=64)
        with mock.patch.object(LightmapBaker.logger, "warning") as warn:
            baker.commit_lightmap({long: self.tex})
        objs = self._manifest()["objects"]
        self.assertEqual(objs[0]["uvIndex"], 2)
        warned = _rendered_warnings(warn)
        self.assertTrue(
            any("UV index 2" in m for m in warned),
            f"expected a uv-index warning, got: {warned}",
        )

    def test_manifest_warns_on_duplicate_leaf_names(self):
        # Unity matches renderers by GameObject name (first match wins);
        # namespace/DAG stripping makes leaf collisions plausible -- warn.
        a, _, _ = self._cube_with_material("dupLeaf")
        cmds.group(a, name="dupGrpA")  # reparent -> the long name changes
        la = cmds.ls("dupGrpA|dupLeaf", long=True)[0]
        _, _, lb = self._cube_with_material("dupLeaf")  # same leaf, root level
        UvUtils.create_lightmap_uvs([la, lb], map_size=64, quiet=True)

        baker = LightmapBaker(resolution=64)
        with mock.patch.object(LightmapBaker.logger, "warning") as warn:
            baker.commit_lightmap({la: self.tex, lb: self.tex})
        warned = _rendered_warnings(warn)
        self.assertTrue(
            any("Duplicate" in m and "dupLeaf" in m for m in warned),
            f"expected a duplicate-name warning, got: {warned}",
        )

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
    """pack_atlas — group by primary material, area-weighted atlas, UV repack.

    Needs cv2 (EXR IO/resize) but no renderer: synthetic per-object EXRs stand
    in for the bake output, so the grouping / packing / consolidation / UV
    repacking logic is exercised deterministically. The rect is baked into the
    lightmap UVs (standalone atlas), not published as an engine binding.
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
    def _cube_on_sg(name, sg, tex_basename=None, lightmap_uvs=True):
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
        long_name = cmds.ls(cube, long=True)[0]
        if lightmap_uvs:
            # Production input to pack_atlas always has a lightmap set
            # (bake_separated ensures one); the pack repacks it into the rect.
            UvUtils.create_lightmap_uvs([long_name], map_size=64, quiet=True)
        return long_name

    @staticmethod
    def _uv_bounds(obj, uv_set=None):
        """(umin, umax, vmin, vmax) of *obj*'s lightmap (or given) UV set."""
        shape = cmds.listRelatives(obj, shapes=True, fullPath=True)[0]
        uv_set = uv_set or UvDiagnostics.find_lightmap_uv_set(shape)
        prev = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
        cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
        try:
            us, vs = [], []
            for u, v in zip(*[iter(cmds.polyEditUV(f"{shape}.map[*]", query=True))] * 2):
                us.append(u)
                vs.append(v)
            return min(us), max(us), min(vs), max(vs)
        finally:
            if prev and prev != uv_set:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev)

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

    def test_atlas_rects_are_inset_and_gutters_filled(self):
        # Rects are inset by a pixel gutter (published scaleOffset == the
        # inset content region) and the freed borders are dilate-filled, so
        # mips / bilinear taps can't bleed between neighbors or sample empty
        # background.
        cv2, np = _cv2()
        sg, _ = self._make_sg("Gut")
        a = self._cube_on_sg("gutA", sg, "Gut_Base_BaseColor.png")
        b = self._cube_on_sg("gutB", sg)
        mapping = {
            a: self._solid_exr("gutA.exr", (0, 0, 1)),
            b: self._solid_exr("gutB.exr", (0, 1, 0)),
        }
        out = LightmapBaker(resolution=64).pack_atlas(mapping, output_dir=self.tmp)

        so_a, so_b = out[a][1], out[b][1]
        # Inset rects no longer tile the unit square exactly...
        self.assertLess(so_a[0] * so_a[1] + so_b[0] * so_b[1], 1.0 - 1e-6)
        # ...but stay within it.
        for so in (so_a, so_b):
            self.assertGreaterEqual(min(so[2], so[3]), 0.0)
            self.assertLessEqual(so[0] + so[2], 1.0 + 1e-9)
            self.assertLessEqual(so[1] + so[3], 1.0 + 1e-9)
        # Every atlas texel carries content after the gutter dilation.
        atlas = cv2.imread(out[a][0], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        self.assertTrue(bool((atlas.max(axis=2) > 0).all()))

    def test_atlas_samples_back_through_scale_offset(self):
        # END-TO-END sampling invariant: for every packed object, sampling the
        # atlas at uv' = uv * scale + offset (exactly where the repacked
        # lightmap UVs now sit, flip included) must return that object's own
        # texels. Catches any rect / flip / inset regression the way a
        # consumer would see it -- as the wrong object's lighting.
        cv2, np = _cv2()
        sg, _ = self._make_sg("Samp")
        a = self._cube_on_sg("sampA", sg, "Samp_Base_BaseColor.png")
        b = self._cube_on_sg("sampB", sg)
        colors = {a: (0.25, 0.5, 1.0), b: (1.0, 0.5, 0.25)}  # BGR floats
        mapping = {
            a: self._solid_exr("sampA.exr", colors[a]),
            b: self._solid_exr("sampB.exr", colors[b]),
        }
        out = LightmapBaker(resolution=64).pack_atlas(mapping, output_dir=self.tmp)
        atlas = cv2.imread(out[a][0], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        h, w = atlas.shape[:2]

        for obj in (a, b):
            _path, (sx, sy, ox, oy) = out[obj]
            for u, v in ((0.5, 0.5), (0.1, 0.1), (0.9, 0.9), (0.1, 0.9)):
                up, vp = u * sx + ox, v * sy + oy
                col = min(int(up * w), w - 1)
                row = min(int((1.0 - vp) * h), h - 1)
                texel = atlas[row, col]
                for ch in range(3):
                    self.assertAlmostEqual(
                        float(texel[ch]), colors[obj][ch], places=2,
                        msg=f"{obj} uv=({u},{v}) -> pixel ({row},{col}) "
                            f"returned {texel}, expected {colors[obj]}",
                    )

    def test_surface_area_and_primary_material(self):
        sg, _ = self._make_sg("Area")
        a = self._cube_on_sg("areaCube", sg)
        self.assertGreater(LightmapBaker._surface_area(a), 0.0)
        self.assertEqual(LightmapBaker._primary_material(a), sg)

    def test_atlas_repacks_lightmap_uvs_into_rect(self):
        # STANDALONE invariant: the rect is APPLIED to each object's lightmap
        # UVs at pack time, so the exported mesh samples the atlas directly
        # through UV2 -- correct in any engine with zero engine-side setup.
        sg, _ = self._make_sg("Remap")
        a = self._cube_on_sg("remapA", sg, "Remap_Base_BaseColor.png")
        b = self._cube_on_sg("remapB", sg)
        pre = {o: self._uv_bounds(o) for o in (a, b)}
        mapping = {
            a: self._solid_exr("remapA.exr", (0, 0, 1)),
            b: self._solid_exr("remapB.exr", (0, 1, 0)),
        }
        out = LightmapBaker(resolution=64).pack_atlas(mapping, output_dir=self.tmp)
        for obj in (a, b):
            sx, sy, ox, oy = out[obj][1]
            umin, umax, vmin, vmax = self._uv_bounds(obj)
            # Bounds land inside the rect...
            self.assertGreaterEqual(umin, ox - 1e-5)
            self.assertLessEqual(umax, ox + sx + 1e-5)
            self.assertGreaterEqual(vmin, oy - 1e-5)
            self.assertLessEqual(vmax, oy + sy + 1e-5)
            # ...and are the exact affine image of the pre-pack bounds.
            p_umin, p_umax, p_vmin, p_vmax = pre[obj]
            self.assertAlmostEqual(umin, p_umin * sx + ox, places=5)
            self.assertAlmostEqual(umax, p_umax * sx + ox, places=5)
            self.assertAlmostEqual(vmin, p_vmin * sy + oy, places=5)
            self.assertAlmostEqual(vmax, p_vmax * sy + oy, places=5)

    def test_atlas_falls_back_per_object_without_lightmap_uvs(self):
        # No lightmap UV set -> the rect can't be baked into the mesh, so the
        # object keeps its own per-object map with an identity rect (degraded
        # but never engine-wrong) and the pack warns.
        sg, _ = self._make_sg("NoLm")
        a = self._cube_on_sg("nolmA", sg, "NoLm_Base_BaseColor.png")
        b = self._cube_on_sg("nolmB", sg, lightmap_uvs=False)
        mapping = {
            a: self._solid_exr("nolmA.exr", (0, 0, 1)),
            b: self._solid_exr("nolmB.exr", (0, 1, 0)),
        }
        baker = LightmapBaker(resolution=32)
        with mock.patch.object(baker.logger, "warning") as warn:
            out = baker.pack_atlas(mapping, output_dir=self.tmp)
        self.assertEqual(out[b][0], mapping[b])  # kept, not consolidated
        self.assertEqual(out[b][1], [1.0, 1.0, 0.0, 0.0])
        self.assertTrue(os.path.exists(mapping[b]))
        self.assertNotEqual(out[a][0], mapping[b])
        self.assertTrue(any("repacked" in w for w in _rendered_warnings(warn)))

    def test_commit_uv_rects_marker_and_identity_manifest(self):
        # uv_rects is revert bookkeeping only: the marker records the applied
        # rect while the manifest publishes an identity scaleOffset (the
        # engine applies nothing -- the UVs already sample the atlas).
        from mayatk.node_utils.data_nodes import DataNodes

        sg, _ = self._make_sg("RectC")
        a = self._cube_on_sg("rectC", sg)
        rect = [0.5, 0.5, 0.25, 0.25]
        baker = LightmapBaker(resolution=16)
        baker.commit_lightmap(
            {a: self._solid_exr("rectC.exr", (1, 1, 1))}, uv_rects={a: rect}
        )
        shape = cmds.listRelatives(a, shapes=True, fullPath=True)[0]
        info = json.loads(
            cmds.getAttr(f"{shape}.{LightmapBaker.LIGHTMAP_INFO_ATTR}")
        )
        self.assertEqual(info["uvRect"], rect)
        self.assertEqual(info["scaleOffset"], [1.0, 1.0, 0.0, 0.0])
        raw = DataNodes.get_export_string(LightmapBaker.LIGHTMAP_METADATA)
        rec = next(
            o for o in json.loads(raw)["objects"] if o["name"] == "rectC"
        )
        self.assertEqual(rec["scaleOffset"], [1.0, 1.0, 0.0, 0.0])
        self.assertNotIn("uvRect", rec)  # internal bookkeeping, not published

    def test_revert_restores_atlased_uvs(self):
        # revert_lightmap inverts the recorded uvRect -- the lightmap set is
        # back at its original unit-square layout for the next bake.
        sg, _ = self._make_sg("RevU")
        a = self._cube_on_sg("revU", sg)
        pre = self._uv_bounds(a)
        baker = LightmapBaker(resolution=16)
        rect = [0.5, 0.5, 0.25, 0.25]
        shape = cmds.listRelatives(a, shapes=True, fullPath=True)[0]
        lm = UvDiagnostics.find_lightmap_uv_set(shape)
        baker._transform_lightmap_uvs(shape, lm, rect)
        baker.commit_lightmap(
            {a: self._solid_exr("revU.exr", (1, 1, 1))}, uv_rects={a: rect}
        )
        baker.revert_lightmap([a])
        for got, want in zip(self._uv_bounds(a), pre):
            self.assertAlmostEqual(got, want, places=5)
        self.assertFalse(
            cmds.attributeQuery(
                LightmapBaker.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            )
        )

    def test_bake_guard_restores_stale_remap(self):
        # Direct-API safety: baking over a prior atlas commit restores the
        # unit square first and strips uvRect from the marker (idempotent).
        sg, _ = self._make_sg("Guard")
        a = self._cube_on_sg("guardA", sg)
        pre = self._uv_bounds(a)
        baker = LightmapBaker(resolution=16)
        rect = [0.25, 0.25, 0.5, 0.5]
        shape = cmds.listRelatives(a, shapes=True, fullPath=True)[0]
        lm = UvDiagnostics.find_lightmap_uv_set(shape)
        baker._transform_lightmap_uvs(shape, lm, rect)
        baker.commit_lightmap(
            {a: self._solid_exr("guard.exr", (1, 1, 1))}, uv_rects={a: rect}
        )
        baker._restore_atlased_uvs([a])
        for got, want in zip(self._uv_bounds(a), pre):
            self.assertAlmostEqual(got, want, places=5)
        info = json.loads(
            cmds.getAttr(f"{shape}.{LightmapBaker.LIGHTMAP_INFO_ATTR}")
        )
        self.assertNotIn("uvRect", info)
        baker._restore_atlased_uvs([a])  # second pass: no-op
        for got, want in zip(self._uv_bounds(a), pre):
            self.assertAlmostEqual(got, want, places=5)

    def test_atlas_group_failure_falls_back_per_object(self):
        # A group-level packing failure (e.g. atlas assembly blowing up) must
        # not lose the bake or leave a half-consumed group: every unfinished
        # object keeps its per-object map with an identity rect, no lightmap
        # UVs move, and the sources stay on disk.
        sg, _ = self._make_sg("Boom")
        a = self._cube_on_sg("boomA", sg, "Boom_Base_BaseColor.png")
        b = self._cube_on_sg("boomB", sg)
        pre = {o: self._uv_bounds(o) for o in (a, b)}
        mapping = {
            a: self._solid_exr("boomA.exr", (0, 0, 1)),
            b: self._solid_exr("boomB.exr", (0, 1, 0)),
        }
        baker = LightmapBaker(resolution=32)
        with mock.patch.object(
            ptk.ImgUtils, "assemble_atlas", side_effect=RuntimeError("boom")
        ), mock.patch.object(baker.logger, "warning") as warn:
            out = baker.pack_atlas(mapping, output_dir=self.tmp)
        for obj in (a, b):
            self.assertEqual(out[obj], (mapping[obj], [1.0, 1.0, 0.0, 0.0]))
            self.assertTrue(os.path.exists(mapping[obj]))
            for got, want in zip(self._uv_bounds(obj), pre[obj]):
                self.assertAlmostEqual(got, want, places=5)
        self.assertTrue(any("failed" in w for w in _rendered_warnings(warn)))

    def test_transform_lightmap_uvs_roundtrip(self):
        # Forward + invert is an identity (within fp) for an arbitrary rect.
        sg, _ = self._make_sg("Rt")
        a = self._cube_on_sg("rtA", sg)
        shape = cmds.listRelatives(a, shapes=True, fullPath=True)[0]
        lm = UvDiagnostics.find_lightmap_uv_set(shape)
        pre = self._uv_bounds(a, lm)
        rect = [0.4375, 0.9, 0.03125, 0.05]
        LightmapBaker._transform_lightmap_uvs(shape, lm, rect)
        LightmapBaker._transform_lightmap_uvs(shape, lm, rect, invert=True)
        for got, want in zip(self._uv_bounds(a, lm), pre):
            self.assertAlmostEqual(got, want, places=4)

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

    def test_from_preset_pins_gi_render_settings(self):
        # GI depth / samples are scene render settings, not RTT flags: the
        # preset must reach the bake via the baker's pinned render_settings,
        # or every bake silently runs at Arnold's 1-bounce scene default.
        baker = LightmapBaker.from_preset("desktop")
        self.assertEqual(baker.gi_depth, 3)
        self.assertEqual(baker.gi_samples, 6)
        self.assertEqual(
            baker.baker.render_settings,
            {"GIDiffuseDepth": 3, "GIDiffuseSamples": 6},
        )

    def test_overrides_win_over_preset(self):
        baker = LightmapBaker.from_preset("quest", resolution=1536, gi_depth=5)
        self.assertEqual(baker.resolution, 1536)  # override
        self.assertEqual(baker.samples, 4)  # from preset
        self.assertEqual(baker.gi_depth, 5)  # override
        self.assertEqual(baker.gi_samples, 4)  # from preset

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


class _ScopeCombo:
    """Scope combobox stub: defaults to Selected, matching cmb_scope_init's
    setCurrentIndex(0) (the prior selection-only behavior)."""

    def __init__(self, text="Selected"):
        self._text = text

    def currentText(self):
        return self._text


class _ResolutionCombo:
    """Resolution combobox stub: mirrors cmb_resolution_init's item-data model
    (currentData() is the actual pixel size, not the display text) so
    _resolution()/_set_resolution() round-trip without a real Qt widget.
    """

    _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

    def __init__(self, resolution=1024):
        self._data = resolution  # tolerate an out-of-list placeholder value

    def currentData(self):
        return self._data

    def setCurrentIndex(self, index):
        self._data = self._RESOLUTIONS[index]

    def blockSignals(self, _b):
        pass


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
    """Affix-field stub: text() + an option_box exposing ``resolve_affix`` the same
    way uitk's real ``OptionBoxManager`` does when no ``AffixOption`` picker is
    attached — auto-mode split of the wrapped text via ``pythontk.StrUtils.split_affix``
    (see uitk/widgets/optionBox/utils.py::resolve_affix's no-picker fallback path)."""

    class _Menu:
        pass

    class _OptionBox:
        def __init__(self, widget):
            self.menu = _LineEdit._Menu()
            self._widget = widget

        def resolve_affix(self, *, default="prefix"):
            return ptk.StrUtils.split_affix(self._widget.text(), mode="auto", default=default)

    def __init__(self, text="_Lightmap"):
        self._text = text
        self.option_box = _LineEdit._OptionBox(self)

    def text(self):
        return self._text


class _SlotUi:
    def __init__(
        self, res=1024, samples=4, affix="_Lightmap",
        mode="Lighting Only (keep maps)", packing="Per-Object (one map each)",
        scope="Selected",
    ):
        self.footer = _Footer()
        self.cmb_resolution = _ResolutionCombo(res)
        self.spn_samples = _Spin(samples)
        self.txt000 = _LineEdit(affix)
        self.cmb001 = _ModeCombo(mode)
        self.cmb002 = _PackingCombo(packing)
        self.cmb_scope = _ScopeCombo(scope)


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

    def commit_lightmap(
        self, mapping, intensity=1.0, scale_offsets=None, uv_rects=None
    ):
        self.calls.append(("commit_lightmap", dict(mapping)))
        self.commit_scale_offsets = scale_offsets
        self.commit_uv_rects = uv_rects
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

    def test_b000_atlas_packing_consolidates_and_commits_with_uv_rects(self):
        # Lighting Only + Atlas by Material: revert → bake_separated → pack_atlas
        # → commit_lightmap. The applied rects reach the commit as uv_rects
        # (revert bookkeeping) -- NOT as scale_offsets (no engine-side binding).
        long = self._select_cube()
        ui = _SlotUi(packing="Atlas by Material (shared map)")
        s = self._slots(ui)
        s.b000()

        baker = _FakeWorkflow.instances[0]
        kinds = [c[0] for c in baker.calls]
        self.assertEqual(
            kinds, ["revert", "bake_separated", "pack_atlas", "commit_lightmap"]
        )
        self.assertIn(long, baker.commit_uv_rects)
        self.assertEqual(baker.commit_uv_rects[long], [1.0, 1.0, 0.0, 0.0])
        self.assertIsNone(baker.commit_scale_offsets)
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
        self.assertEqual(ui.cmb_resolution.currentData(), 2048)
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
