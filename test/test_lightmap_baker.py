"""Tests for LightmapBaker -- the lighting-only lightmap (UV2) orchestrator.

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

    def test_rendered_dead_texels_are_rescued(self):
        # MEASURED (OFFICE_ENV walls, mtoa 5.5): RTT can write alpha == 1.0
        # across the WHOLE frame -- alpha is then no coverage signal at all --
        # and geometry buried below the floor slab / behind a baseboard or
        # door leaf renders with full coverage and ~zero radiance. Packed and
        # downscaled, those texels smear into visible dark borders at the
        # junctions they hide behind. The dilate pass must
        # treat them as empty (fill from lit neighbors), keyed on radiance
        # relative to the map's own lit level -- real near-black shadow stays.
        cv2, np = _cv2()
        p = os.path.join(self.tmp, "dead.exr")
        img = np.zeros((8, 64, 4), np.float32)
        img[..., 3] = 1.0  # saturated alpha, frame-wide
        img[:, 8:56, :3] = 2.0  # lit island interior
        img[:, 6, :3] = 0.002  # occluded corridor's faint GI leak column
        # cols 0..8 (minus the leak) and 56.. stay exact zero: rendered-dead
        # corridor and RTT background, both wearing full alpha.
        cv2.imwrite(p, img)
        self.assertTrue(
            LightmapBaker._dilate_lightmap(p, alpha_threshold=0.05, iterations=8)
        )
        out = _read(p)
        # Corridor and background now carry neighbor lighting, not black.
        self.assertGreater(float(out[:, 0:8].min()), 1.0)
        self.assertGreater(float(out[:, 56:].min()), 1.0)
        # Interior untouched.
        self.assertAlmostEqual(float(out[4, 30, 0]), 2.0, places=3)

    def test_edge_extension_texels_are_refilled_from_uv_coverage(self):
        # THE production artifact (shipped OFFICE_ENV room, profiled from the
        # delivered glb + its source EXR): RTT with -extend_edges RENDERS a
        # ring past the island border at full alpha, and on a wall panel that
        # ring is coplanar with the neighbouring panel, so its rays hit that
        # panel and it bakes dark. Island border texels measured 0.015x-1.09x
        # their interior, which the atlas resample turned into a dashed
        # outline around every panel in the headset. Alpha cannot see it
        # (1.0 frame-wide); the UV layout can.
        cv2, np = _cv2()
        size = 64
        p = os.path.join(self.tmp, "extension.exr")
        img = np.zeros((size, size, 4), np.float32)
        img[..., 3] = 1.0  # saturated alpha, frame-wide
        img[:, : size // 2, :3] = 2.0  # island: exactly the left half
        # 0.03 == the 0.015x ratio measured on the worst shipped panel. Note
        # it sits ABOVE the rendered-dead cut (1% of the 2.0 median), so the
        # radiance rescue cannot claim it -- as on the real room, where border
        # texels ran 0.10x-0.83x. Only coverage separates this from content.
        img[:, size // 2 : size // 2 + 3, :3] = 0.03
        cv2.imwrite(p, img)

        left_half = [
            [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0)],
            [(0.0, 0.0), (0.5, 1.0), (0.0, 1.0)],
        ]
        LightmapBaker._dilate_lightmap(
            p, alpha_threshold=0.05, iterations=8, uv_triangles=left_half
        )
        out = _read(p)
        # The extension ring now carries the island's own lighting.
        self.assertGreater(float(out[:, size // 2 : size // 2 + 3].min()), 1.0)
        # ... and the interior is untouched.
        self.assertAlmostEqual(float(out[size // 2, 8, 0]), 2.0, places=3)

    def test_extension_ring_survives_without_the_uv_layout(self):
        # The contrast case that pins WHICH signal does the work: same image,
        # no layout. Alpha is frame-wide 1.0 and the ring is far above the
        # rendered-dead cut, so nothing else in the pass can reject it and the
        # dark ring must survive. If this ever starts passing by itself, the
        # test above has stopped proving anything.
        cv2, np = _cv2()
        size = 64
        p = os.path.join(self.tmp, "extension_nolayout.exr")
        img = np.zeros((size, size, 4), np.float32)
        img[..., 3] = 1.0
        img[:, : size // 2, :3] = 2.0
        img[:, size // 2 : size // 2 + 3, :3] = 0.03
        cv2.imwrite(p, img)
        LightmapBaker._dilate_lightmap(p, alpha_threshold=0.05, iterations=8)
        out = _read(p)
        self.assertLess(float(out[size // 2, size // 2, 0]), 0.1)

    def test_coverage_mask_tracks_the_island_at_every_resolution(self):
        # The mask is rasterized at the MAP's size, so a rounding error that
        # only bites at one preset would silently trust extension texels
        # there and nowhere else. Sweep every size the panel offers.
        _, np = _cv2()
        # u1 = 1/3 puts the island's border mid-texel at all four sizes.
        island = [
            [(0.0, 0.0), (1 / 3, 0.0), (1 / 3, 1.0)],
            [(0.0, 0.0), (1 / 3, 1.0), (0.0, 1.0)],
        ]
        # 4096 is included to exercise the reduced-supersample branch
        # (_COVERAGE_SUPERSAMPLE_MAX_SIZE): a quarter-texel sampling rate must
        # still resolve a fully-covered texel from a partial one.
        for size in (256, 512, 1024, 2048, 4096):
            with self.subTest(size=size):
                mask = LightmapBaker._coverage_mask(island, (size, size))
                self.assertIsNotNone(mask)
                edge = size / 3.0  # island border, in texels
                # Column c is fully covered iff c + 1 <= edge.
                last_full = int(edge // 1) - 1
                row = size // 2
                # Partially covered -> never trusted.
                self.assertFalse(bool(mask[row, last_full + 1]))
                # Fully covered but within the reconstruction filter's reach
                # of the border -> eroded off.
                self.assertFalse(bool(mask[row, last_full]))
                # One further in -> kept.
                self.assertTrue(bool(mask[row, last_full - 1]))
                self.assertTrue(bool(mask[row, 8]))

    def test_real_shadow_texels_survive_the_rescue(self):
        # The rescue cut is RELATIVE (1% of median lit): genuine contact
        # shadow -- dark but far above the occluded-corridor level -- must
        # never be repainted with neighbor lighting.
        cv2, np = _cv2()
        p = os.path.join(self.tmp, "shadow.exr")
        img = np.zeros((8, 32, 4), np.float32)
        img[..., 3] = 1.0
        img[:, :, :3] = 2.0
        img[:, 10:14, :3] = 0.1  # 5% of median: real shadow, keep
        img[:, 20:22, :3] = 0.0  # rendered-dead: rescue
        cv2.imwrite(p, img)
        LightmapBaker._dilate_lightmap(p, alpha_threshold=0.05, iterations=8)
        out = _read(p)
        self.assertAlmostEqual(float(out[4, 11, 0]), 0.1, places=3)
        self.assertGreater(float(out[:, 20:22].min()), 1.0)

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
        result = LightmapBaker(resolution=64, baker=fake).bake_separated(
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
        # a non-canonical name (UV2, UVChannel_2, ...). The bake must target
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
        LightmapBaker(resolution=64, baker=fake).bake_separated(
            [cube], output_dir=self.tmp, create_uvs=False
        )
        self.assertEqual(fake.called_uv_set[long], "UV2")
        # No canonical "lightmap" set should have been created in reuse mode.
        self.assertNotIn(
            "lightmap", cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        )

    def test_dilate_false_leaves_alpha(self):
        cube = cmds.polyCube(name="lmCubeNoDilate")[0]
        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake_separated(
            [cube], output_dir=self.tmp, dilate=False
        )
        out = _read(next(iter(result.values())))
        self.assertEqual(out.shape[2], 4, "alpha kept when dilate=False")

    def test_bake_hands_the_uv_layout_to_the_dilate_pass(self):
        # The coverage refill only engages if bake() can resolve the keys of its
        # OWN result back to objects -- and those keys come from the baker, not
        # from the caller's list. A key that arrived as a shape or a filename
        # stem would resolve to no layout, silently disabling the refill while
        # the bake still succeeded and the map still looked plausible. So assert
        # the layout actually reaches the pass, not merely that a bake ran.
        cube = cmds.polyCube(name="lmWiring")[0]
        seen = {}
        real = LightmapBaker._dilate_lightmap.__func__

        def spy(cls, path, alpha_threshold, iterations, uv_triangles=None):
            seen["tris"] = uv_triangles
            return real(cls, path, alpha_threshold, iterations, uv_triangles)

        with mock.patch.object(LightmapBaker, "_dilate_lightmap", classmethod(spy)):
            LightmapBaker(resolution=64, baker=_FakeBaker()).bake_separated(
                [cube], output_dir=self.tmp
            )
        self.assertIsNotNone(seen.get("tris"), "bake() resolved no UV layout")
        self.assertEqual(len(seen["tris"]), 12)  # 6 quads, fan-triangulated


@unittest.skipUnless(
    HAVE_CV2 and _arnold_loadable(), "mtoa/arnoldRenderToTexture or cv2 unavailable"
)
class TestLightmapBakerArnold(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_arnold_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_end_to_end_lightmap(self):
        cube = cmds.polyCube(name="lmArnoldCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        result = LightmapBaker(resolution=64, samples=2).bake_separated(
            [cube], output_dir=self.tmp
        )
        self.assertTrue(result)
        path = next(iter(result.values()))
        self.assertTrue(os.path.exists(path))
        out = _read(path)
        self.assertEqual(out.shape[2], 3, "lightmap is opaque RGB")
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

    def test_lightmap_is_albedo_independent(self):
        # The composite invariant that survives the fused removal: the engine
        # multiplies albedo x lightmap, so the lightmap itself must NOT vary
        # with the surface's albedo. Two planes under the same light, one dark
        # one bright, must bake to the same irradiance -- if any stage leaks
        # albedo into the white-card map this diverges.
        light = cmds.directionalLight(intensity=1.0)
        cmds.setAttr(
            f"{cmds.listRelatives(light, parent=True)[0]}.rotateX", -90
        )
        means = []
        for name, color in (("albDark", (0.1, 0.1, 0.1)), ("albBright", (0.9, 0.9, 0.9))):
            plane = cmds.polyPlane(name=f"{name}Plane", w=1, h=1, sx=1, sy=1)[0]
            self._assign_lambert(plane, name, color)
            result = LightmapBaker(resolution=32, samples=3).bake_separated(
                [plane], output_dir=self.tmp
            )
            means.append(float(_read(next(iter(result.values()))).mean()))
            # One plane in the light at a time: polyPlane spawns at the origin,
            # so leaving the first in place would shadow the second and the
            # comparison would measure occlusion instead of albedo.
            cmds.delete(plane)
        self.assertAlmostEqual(
            means[0], means[1], delta=0.03,
            msg=f"lightmap tracked albedo: dark={means[0]:.4f} bright={means[1]:.4f}",
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

    No renderer needed: commit_lightmap only stamps per-TRANSFORM markers
    (per-instance, so every copy of a shared shape can hold its own atlas
    rect) and publishes the ``data_export`` manifest, so a dummy texture path
    exercises the wiring. The key guarantee is that the material and UV order
    are left untouched -- the whole point of lightmapping over flattening.
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
        self.assertTrue(self._marked(long))  # marker on the TRANSFORM (per-instance)
        self.assertFalse(self._marked(shape))  # never on the shared shape

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
        self.assertFalse(self._marked(long))
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

    def test_manifest_keeps_the_namespace_the_export_carries(self):
        """The published name must equal the exported node name.

        Maya writes ``NS:leaf`` as the FBX Model name and FBX2glTF preserves the
        colon into the glTF node name (both measured), so a namespace-stripped
        key matches nothing downstream -- Unity's FindRenderer compares against
        ``NS:leaf`` -- and collapses distinct objects from two referenced modules
        into a false duplicate.
        """
        for ns in ("NS_X", "NS_Y"):
            if not cmds.namespace(exists=ns):
                cmds.namespace(add=ns)
        longs = []
        for ns in ("NS_X", "NS_Y"):
            cmds.namespace(set=ns)
            cube, _shape, long = self._cube_with_material("nsLeaf")
            cmds.namespace(set=":")
            longs.append(long)
        UvUtils.create_lightmap_uvs(longs, map_size=64, quiet=True)

        baker = LightmapBaker(resolution=64)
        with mock.patch.object(LightmapBaker.logger, "warning") as warn:
            baker.commit_lightmap({longs[0]: self.tex, longs[1]: self.tex})

        names = {o["name"] for o in self._manifest()["objects"]}
        self.assertEqual(names, {"NS_X:nsLeaf", "NS_Y:nsLeaf"})
        # ...and they are not a duplicate: they are distinct downstream.
        self.assertFalse(
            [m for m in _rendered_warnings(warn) if "Duplicate" in m],
            "objects that differ only by namespace are not duplicates",
        )

    def test_unified_revert_clears_lighting_only_marker(self):
        # revert() is what the panel and the pre-bake clear call; it must clear
        # the lighting-only marker.
        cube, shape, long = self._cube_with_material("lmBoth")
        UvUtils.create_lightmap_uvs([cube], map_size=64, quiet=True)
        baker = LightmapBaker(resolution=64)
        baker.commit_lightmap({long: self.tex})
        self.assertTrue(self._marked(long))

        self.assertTrue(baker.revert([long]))
        self.assertFalse(self._marked(long))
        self.assertFalse(self._marked(shape))
        self.assertEqual(self._manifest()["objects"], [])


class TestPerInstanceMarkers(MayaTkTestCase):
    """The marker lives on the TRANSFORM so every instance of a shared shape
    carries its own atlas rect — Unity's per-renderer ``lightmapScaleOffset``
    model. A shape-level marker physically cannot hold per-instance data:
    24 walls wearing one shape have 24 rects and one shape node.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_inst_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tex = os.path.join(self.tmp, "shared_Lightmap.exr")
        open(self.tex, "wb").close()

    def _manifest_objects(self):
        from mayatk.node_utils.data_nodes import DataNodes

        raw = DataNodes.get_export_string(LightmapBaker.LIGHTMAP_METADATA)
        return json.loads(raw)["objects"] if raw else []

    def _instanced_pair(self):
        src = cmds.polyCube(name="instWall")[0]
        copy = cmds.instance(src)[0]
        cmds.move(3, 0, 0, copy)
        return cmds.ls(src, long=True)[0], cmds.ls(copy, long=True)[0]

    def test_each_instance_carries_its_own_rect(self):
        src, copy = self._instanced_pair()
        rect_a, rect_b = [0.5, 1.0, 0.0, 0.0], [0.5, 1.0, 0.5, 0.0]
        baker = LightmapBaker(resolution=16)
        recorded = baker.commit_lightmap(
            {src: self.tex, copy: self.tex},
            scale_offsets={src: rect_a, copy: rect_b},
        )
        self.assertEqual(set(recorded), {src, copy})
        self.assertEqual(baker._marker_info(src)["scaleOffset"], rect_a)
        self.assertEqual(baker._marker_info(copy)["scaleOffset"], rect_b)
        recs = {o["name"]: o for o in self._manifest_objects()}
        self.assertEqual(set(recs), {"instWall", "instWall1"})
        self.assertEqual(recs["instWall"]["scaleOffset"], rect_a)
        self.assertEqual(recs["instWall1"]["scaleOffset"], rect_b)
        # One shared atlas, two rects into it.
        self.assertEqual(recs["instWall"]["map"], recs["instWall1"]["map"])

    def test_legacy_shape_marker_still_publishes_and_reverts(self):
        cube = cmds.polyCube(name="legacyLm")[0]
        long = cmds.ls(cube, long=True)[0]
        shape = cmds.listRelatives(long, shapes=True, fullPath=True)[0]
        baker = LightmapBaker(resolution=16)
        LightmapBaker._set_string_attr(
            shape,
            LightmapBaker.LIGHTMAP_INFO_ATTR,
            json.dumps(
                {
                    "map": "legacy.exr",
                    "uv_set": "lightmap",
                    "intensity": 1.0,
                    "scaleOffset": [1.0, 1.0, 0.0, 0.0],
                    "mode": "separated",
                }
            ),
        )
        baker._publish_lightmap_metadata()
        recs = self._manifest_objects()
        self.assertEqual([r["name"] for r in recs], ["legacyLm"])
        self.assertEqual(recs[0]["scaleOffset"], [1.0, 1.0, 0.0, 0.0])

        # A re-commit migrates the marker to the transform and clears the
        # shape, so the publisher can never double-count the object.
        baker.commit_lightmap({long: self.tex})
        self.assertFalse(
            cmds.attributeQuery(
                LightmapBaker.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            )
        )
        self.assertEqual(baker._marker_node(long), long)
        self.assertEqual(len(self._manifest_objects()), 1)

        baker.revert_lightmap([long])
        self.assertIsNone(baker._marker_node(long))
        self.assertEqual(self._manifest_objects(), [])

    def test_commit_revert_commit_is_idempotent(self):
        src, copy = self._instanced_pair()
        baker = LightmapBaker(resolution=16)
        for _ in range(2):
            baker.commit_lightmap({src: self.tex, copy: self.tex})
            baker.revert_lightmap([src, copy])
        baker.commit_lightmap({src: self.tex, copy: self.tex})
        self.assertEqual(len(self._manifest_objects()), 2)
        shape = cmds.listRelatives(src, shapes=True, fullPath=True)[0]
        self.assertFalse(
            cmds.attributeQuery(
                LightmapBaker.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            )
        )


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestPackAtlas(MayaTkTestCase):
    """pack_atlas — group by primary material, area-weighted atlas, rect binding.

    Needs cv2 (EXR IO/resize) but no renderer: synthetic per-object EXRs stand
    in for the bake output, so the grouping / packing / consolidation logic is
    exercised deterministically. The rect is the DELIVERABLE (engine binding via
    commit_lightmap's scale_offsets → Unity lightmapScaleOffset / glTF
    KHR_texture_transform); lightmap UVs are never edited, which is what lets
    instanced transforms (one shared UV set) each own a distinct rect.
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

    def _partial_exr(self, name, color, u_frac):
        """A map lit only across the left *u_frac* of its width -- what an RTT
        render of a partial-coverage lightmap island produces (black
        elsewhere)."""
        cv2, np = _cv2()
        path = os.path.join(self.tmp, name)
        img = np.zeros((24, 24, 3), np.float32)
        img[:, : max(1, int(24 * u_frac))] = color
        cv2.imwrite(path, img)
        return path

    @staticmethod
    def _squeeze_lightmap_u(obj, frac):
        """Scale *obj*'s lightmap set into the left *frac* of UV space -- the
        production wall shape (their islands span u 0..1/3)."""
        shape = cmds.listRelatives(obj, shapes=True, fullPath=True)[0]
        uv_set = UvDiagnostics.find_lightmap_uv_set(shape)
        prev = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
        cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
        cmds.polyEditUV(
            f"{shape}.map[*]", pivotU=0.0, pivotV=0.0, scaleU=frac, scaleV=1.0
        )
        if prev and prev != uv_set:
            cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev)

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
        # atlas at uv' = uv * scale + offset (what the engine computes from the
        # committed scaleOffset, flip included) must return that object's own
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

    def test_many_object_atlas_leaves_no_background_texels(self):
        # The production shape: many small cells whose freed borders exceed
        # the bounded ``gutter+1`` dilation reach. The shipped room's 256px
        # atlas kept 2.53% zero texels that way, and every zero texel is what
        # the GPU's coarser mips average into content -- a dark halo on each
        # tile at distance (measured: rect-edge luminance -15% vs interior).
        # After the nearest-fill pass, no background may survive.
        cv2, np = _cv2()
        sg, _ = self._make_sg("Full")
        colors = [(0.2, 0.4, 0.8), (0.8, 0.4, 0.2), (0.1, 0.9, 0.3),
                  (0.9, 0.1, 0.5), (0.5, 0.5, 0.5), (0.3, 0.7, 0.2)]
        mapping = {}
        for i, color in enumerate(colors):
            obj = self._cube_on_sg(f"full{i}", sg, "Full_Base_BaseColor.png")
            mapping[obj] = self._solid_exr(f"full{i}.exr", color)
        out = LightmapBaker(resolution=32).pack_atlas(mapping, output_dir=self.tmp)
        atlas_path = next(iter(out.values()))[0]
        atlas = cv2.imread(atlas_path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        zero = ~(atlas.max(axis=2) > 0)
        self.assertEqual(
            int(zero.sum()), 0,
            f"{int(zero.sum())} background texel(s) survived the fill",
        )

    def test_published_rects_sample_border_texel_centers(self):
        # The engine samples the PUBLISHED rect while the assembler writes at
        # rounded pixel edges. An island edge published ON a texel boundary
        # makes every bilinear tap along a shared 3D edge split onto the
        # NEIGHBORING cell's gutter -- up to half its weight on another
        # object's lighting. The island's bbox must therefore map to the
        # CENTERS of its border texels, where an edge tap reads the object's
        # own texel pure. (Verified in production NOT to be the cause of that
        # room's visible panel seams -- those are baked into the source map --
        # but a real sampling defect on its own.)
        sg, _ = self._make_sg("Grid")
        a = self._cube_on_sg("gridA", sg, "Grid_Base_BaseColor.png")
        b = self._cube_on_sg("gridB", sg)
        c = self._cube_on_sg("gridC", sg)
        mapping = {
            a: self._solid_exr("gridA.exr", (0, 0, 1)),
            b: self._solid_exr("gridB.exr", (0, 1, 0)),
            c: self._solid_exr("gridC.exr", (1, 0, 0)),
        }
        res = 64
        out = LightmapBaker(resolution=res).pack_atlas(mapping, output_dir=self.tmp)
        for obj, (_path, (sx, sy, ox, oy)) in out.items():
            # These unwraps are near-full coverage, so no crop is taken and
            # the uv range mapping onto the cell is the unit square.
            for uv_edge, px in (
                (0.0, ox * res),
                (1.0, (ox + sx) * res),
                (0.0, oy * res),
                (1.0, (oy + sy) * res),
            ):
                self.assertAlmostEqual(
                    px % 1.0, 0.5, places=4,
                    msg=f"{obj}: cell edge uv={uv_edge} -> {px}px is not a "
                    "texel center",
                )

    def test_crop_admits_no_edge_extension_texel(self):
        # The production seam: the crop used to pad a whole texel past the
        # island's high edge, and an edge-extension texel is NOT this
        # object's lighting -- Arnold renders the extension physically, and
        # a point just past a wall panel's edge is coplanar with the
        # neighbouring panel, so it bakes dark. The pad was also asymmetric
        # (the low edge clamped at 0), which is why every tile's TOP edge
        # measured -5% against its own interior while its bottom read ~0%.
        # A source whose island region is uniform must therefore crop to
        # island texels ONLY -- no neighbouring value may enter the crop.
        cv2, np = _cv2()
        w = h = 24
        img = np.zeros((h, w, 3), np.float32)
        # island = u[0.25, 0.75] -> cols 6..18, v[0.25, 0.75] -> rows 6..18
        img[...] = 9.0  # everything outside the island: an extreme value
        img[6:18, 6:18] = 1.0
        cropped, rect, bounds = LightmapBaker._crop_to_island(
            img, (0.25, 0.25, 0.75, 0.75), [0.5, 0.5, 0.0, 0.0]
        )
        self.assertEqual(
            float(cropped.max()), 1.0,
            "an edge-extension texel leaked into the crop",
        )
        self.assertEqual(bounds, (0.25, 0.25, 0.75, 0.75))
        # The rect still maps the cropped region onto the whole cell.
        sx, sy, ox, oy = rect
        self.assertAlmostEqual(ox + sx * 0.25, 0.0, places=6)
        self.assertAlmostEqual(ox + sx * 0.75, 0.5, places=6)

    def test_cropped_islands_sample_disjoint_atlas_regions(self):
        # A crop-composed rect legally extends past its own cell (that is the
        # fold), so the invariant is not "inside the rect" but that no two
        # objects' SAMPLED regions overlap: the island's uv bbox must not
        # reach into a neighbour's texels. Cropping inward (to fully-covered
        # texels) breaks this -- the island then overhangs the crop by a
        # sub-texel sliver and samples the neighbouring gutter -- which is
        # why the crop takes the texels the island TOUCHES.
        sg, _ = self._make_sg("Inside")
        a = self._cube_on_sg("insideA", sg, "Inside_Base_BaseColor.png")
        b = self._cube_on_sg("insideB", sg)
        frac = 1.0 / 3.0
        for o in (a, b):
            self._squeeze_lightmap_u(o, frac)
        mapping = {
            a: self._partial_exr("insideA.exr", (0.25, 0.5, 1.0), frac),
            b: self._partial_exr("insideB.exr", (1.0, 0.5, 0.25), frac),
        }
        res = 64
        out = LightmapBaker(resolution=res).pack_atlas(mapping, output_dir=self.tmp)
        boxes = {}
        for obj, (_p, (sx, sy, ox, oy)) in out.items():
            u0, u1, v0, v1 = self._uv_bounds(obj)
            xs = sorted(((ox + sx * u) * res for u in (u0, u1)))
            ys = sorted(((oy + sy * v) * res for v in (v0, v1)))
            boxes[obj] = (xs[0], xs[1], ys[0], ys[1])
        names = sorted(boxes)
        for i, a in enumerate(names):
            ax0, ax1, ay0, ay1 = boxes[a]
            for b in names[i + 1:]:
                bx0, bx1, by0, by1 = boxes[b]
                overlap = (ax0 < bx1 and bx0 < ax1) and (ay0 < by1 and by0 < ay1)
                self.assertFalse(
                    overlap,
                    f"{a} {boxes[a]} and {b} {boxes[b]} sample overlapping "
                    "atlas regions",
                )

    def test_exact_zero_cell_content_is_healed(self):
        # Rendered-dead content (geometry below the floor slab / behind trim
        # bakes full-coverage black; also legacy no-alpha sources) arrives as
        # exact-zero texels INSIDE a cell. Blanket-trusting cell rects as
        # content shipped them (the 12:45 room atlas: 1440 exact-zero texels
        # banded along the wall/floor junctions; 0 in the post-fix bake). The
        # atlas fill must heal every exact-zero texel, in-cell or not;
        # near-black real shadow (> 0) stays.
        cv2, np = _cv2()
        sg, _ = self._make_sg("Heal")
        colors = [(0.2, 0.4, 0.8), (0.8, 0.4, 0.2), (0.1, 0.9, 0.3)]
        mapping = {}
        for i, color in enumerate(colors):
            obj = self._cube_on_sg(f"heal{i}", sg, "Heal_Base_BaseColor.png")
            mapping[obj] = self._solid_exr(f"heal{i}.exr", color)
        # One source carries a rendered-dead half: exact zero, no alpha.
        p = next(iter(mapping.values()))
        img = cv2.imread(p, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        img[:, : img.shape[1] // 2] = 0.0
        cv2.imwrite(p, img)
        out = LightmapBaker(resolution=32).pack_atlas(mapping, output_dir=self.tmp)
        atlas = cv2.imread(
            next(iter(out.values()))[0], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH
        )
        zero = ~(atlas.max(axis=2) > 0)
        self.assertEqual(
            int(zero.sum()), 0,
            f"{int(zero.sum())} exact-zero texel(s) shipped in the atlas",
        )

    def test_one_object_group_zeros_are_healed(self):
        # A solo group used to adopt its map with a bare os.replace -- black
        # background and rendered-dead texels shipped untouched (the room's
        # diffuse_cube map: 686 zeros), and every mip level averages those
        # into the island as a dark halo. The adopt path must heal them.
        cv2, np = _cv2()
        sg, _ = self._make_sg("Solo")
        obj = self._cube_on_sg("soloA", sg, "Solo_Base_BaseColor.png")
        p = self._solid_exr("soloA.exr", (0.3, 0.6, 0.9))
        img = cv2.imread(p, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        img[:2, :] = 0.0  # background band the bake left black
        cv2.imwrite(p, img)
        out = LightmapBaker(resolution=32).pack_atlas({obj: p}, output_dir=self.tmp)
        path, rect = out[obj]
        self.assertEqual(rect, list(LightmapBaker._IDENTITY_SCALE_OFFSET))
        healed = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        self.assertEqual(int((~(healed.max(axis=2) > 0)).sum()), 0)

    def test_pack_crops_dead_uv_space_and_composes_the_rect(self):
        # A lightmap island covering only part of 0-1 used to waste its cell
        # on dead black space -- which sits INSIDE the coverage mask, so
        # bilinear taps at every content border sampled black and each tile
        # wore a dark edge band; the lit signal also got only
        # coverage-fraction of the cell's texels (measured: the production
        # walls' islands span u 0..1/3, so 2/3 of every wall cell was dead).
        # The pack now crops each source to its lightmap-UV bbox and folds
        # the crop into the published rect: sampling through the rect still
        # returns the object's own texels, at ~3x the effective density.
        cv2, _np = _cv2()
        sg, _ = self._make_sg("Crop")
        a = self._cube_on_sg("cropA", sg, "Crop_Base_BaseColor.png")
        b = self._cube_on_sg("cropB", sg)
        frac = 1.0 / 3.0
        for o in (a, b):
            self._squeeze_lightmap_u(o, frac)
        colors = {a: (0.25, 0.5, 1.0), b: (1.0, 0.5, 0.25)}  # BGR floats
        mapping = {
            a: self._partial_exr("cropA.exr", colors[a], frac),
            b: self._partial_exr("cropB.exr", colors[b], frac),
        }
        out = LightmapBaker(resolution=64).pack_atlas(mapping, output_dir=self.tmp)
        atlas = cv2.imread(out[a][0], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        h, w = atlas.shape[:2]

        for obj in (a, b):
            _path, (sx, sy, ox, oy) = out[obj]
            # The crop engaged: the 1/3-wide island is stretched across the
            # whole cell, so the published u-scale exceeds a full cell's.
            self.assertGreater(sx, 1.0, f"{obj}: rect not crop-composed ({sx})")
            # Sampling INSIDE the island through the published rect returns
            # this object's own texels (the engine's exact computation).
            for u, v in ((0.05, 0.1), (0.30, 0.5), (0.16, 0.9)):
                up, vp = u * sx + ox, v * sy + oy
                col = min(int(up * w), w - 1)
                row = min(int((1.0 - vp) * h), h - 1)
                texel = atlas[row, col]
                for ch in range(3):
                    self.assertAlmostEqual(
                        float(texel[ch]),
                        colors[obj][ch],
                        places=2,
                        msg=f"{obj} uv=({u},{v}) -> ({row},{col}) returned "
                        f"{texel}, expected {colors[obj]}",
                    )

    def test_keep_sources_leaves_the_per_object_maps_for_a_free_repack(self):
        # The per-object maps are the EXPENSIVE half of a bake (production
        # room: 37.6 min of Arnold against seconds to assemble an atlas from
        # maps already rendered), and nothing about them depends on the atlas
        # resolution or affix. Kept, a re-pack costs nothing -- which is what
        # makes "re-pack at a different size" and "re-run after a packing
        # fix" possible without re-baking.
        sg, _ = self._make_sg("Keep2")
        a = self._cube_on_sg("keepSrcA", sg, "Keep2_Base_BaseColor.png")
        b = self._cube_on_sg("keepSrcB", sg)
        solo_sg, _ = self._make_sg("Keep2Solo")
        c = self._cube_on_sg("keepSrcC", solo_sg, "Solo2_Base_BaseColor.png")
        mapping = {
            a: self._solid_exr("keepSrcA.exr", (0, 0, 1)),
            b: self._solid_exr("keepSrcB.exr", (0, 1, 0)),
            c: self._solid_exr("keepSrcC.exr", (1, 0, 0)),
        }
        out = LightmapBaker(resolution=32).pack_atlas(
            mapping, output_dir=self.tmp, keep_sources=True
        )
        self.assertEqual(set(out), {a, b, c})
        for obj, src in mapping.items():
            self.assertTrue(
                os.path.exists(src), f"{obj}'s source map was consumed"
            )
        # And the same mapping re-packs, at a different resolution, with no
        # re-bake -- the point of keeping them.
        again = LightmapBaker(resolution=64).pack_atlas(
            mapping, output_dir=self.tmp, keep_sources=True
        )
        self.assertEqual(set(again), {a, b, c})
        for src in mapping.values():
            self.assertTrue(os.path.exists(src))

    def test_surface_area_and_primary_material(self):
        sg, _ = self._make_sg("Area")
        a = self._cube_on_sg("areaCube", sg)
        self.assertGreater(LightmapBaker._surface_area(a), 0.0)
        self.assertEqual(LightmapBaker._primary_material(a), sg)

    def test_atlas_leaves_lightmap_uvs_untouched(self):
        # The rect is the deliverable, NOT a UV edit: after packing, every
        # object's lightmap unwrap is bit-identical to its pre-pack layout.
        # (An engine applies the rect at sample time via scaleOffset — the only
        # representation that lets instances of one shared UV set differ.)
        sg, _ = self._make_sg("Keep")
        a = self._cube_on_sg("keepA", sg, "Keep_Base_BaseColor.png")
        b = self._cube_on_sg("keepB", sg)
        pre = {o: self._uv_bounds(o) for o in (a, b)}
        mapping = {
            a: self._solid_exr("keepA.exr", (0, 0, 1)),
            b: self._solid_exr("keepB.exr", (0, 1, 0)),
        }
        out = LightmapBaker(resolution=64).pack_atlas(mapping, output_dir=self.tmp)
        for obj in (a, b):
            self.assertNotEqual(out[obj][1], [1.0, 1.0, 0.0, 0.0])  # real rect
            for got, want in zip(self._uv_bounds(obj), pre[obj]):
                self.assertAlmostEqual(got, want, places=6)

    def test_instances_get_distinct_rects_and_one_atlas(self):
        # THE instance guarantee (regression: instances used to be deduped to
        # one shared rect, so every copy showed the first copy's lighting).
        # Two instances share one shape / one lightmap UV set, but each carries
        # its OWN bake and must earn its OWN rect in the shared atlas — and
        # sampling each rect must return that instance's own texels.
        cv2, _np = _cv2()
        sg, _ = self._make_sg("Inst")
        a = self._cube_on_sg("instA", sg, "Inst_Base_BaseColor.png")
        b = cmds.ls(cmds.instance(a, name="instB")[0], long=True)[0]
        colors = {a: (0.25, 0.5, 1.0), b: (1.0, 0.5, 0.25)}  # BGR floats
        mapping = {
            a: self._solid_exr("instA.exr", colors[a]),
            b: self._solid_exr("instB.exr", colors[b]),
        }
        pre = self._uv_bounds(a)
        out = LightmapBaker(resolution=64).pack_atlas(mapping, output_dir=self.tmp)

        self.assertEqual(set(out), {a, b})  # BOTH instances packed
        self.assertEqual(out[a][0], out[b][0])  # one shared atlas
        self.assertNotEqual(out[a][1], out[b][1])  # distinct rects
        for obj in (a, b):
            self.assertNotEqual(out[obj][1], [1.0, 1.0, 0.0, 0.0])
        # The shared UV set was not touched (it cannot express per-instance
        # placement; the rect carries it instead).
        for got, want in zip(self._uv_bounds(a), pre):
            self.assertAlmostEqual(got, want, places=6)
        # Each instance's rect samples back ITS OWN lighting.
        atlas = cv2.imread(out[a][0], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        h, w = atlas.shape[:2]
        for obj in (a, b):
            sx, sy, ox, oy = out[obj][1]
            up, vp = 0.5 * sx + ox, 0.5 * sy + oy
            texel = atlas[min(int((1.0 - vp) * h), h - 1), min(int(up * w), w - 1)]
            for ch in range(3):
                self.assertAlmostEqual(float(texel[ch]), colors[obj][ch], places=2)

    def test_commit_uv_rects_marker_and_identity_manifest(self):
        # LEGACY-format compat: uv_rects is revert bookkeeping only (a remap an
        # old pack already physically APPLIED to the UVs). The marker records
        # the applied rect while the manifest publishes an identity scaleOffset
        # (the engine applies nothing -- the UVs already sample the atlas).
        # Current packs never pass uv_rects; this pins the old scenes' path.
        from mayatk.node_utils.data_nodes import DataNodes

        sg, _ = self._make_sg("RectC")
        a = self._cube_on_sg("rectC", sg)
        rect = [0.5, 0.5, 0.25, 0.25]
        baker = LightmapBaker(resolution=16)
        baker.commit_lightmap(
            {a: self._solid_exr("rectC.exr", (1, 1, 1))}, uv_rects={a: rect}
        )
        info = baker._marker_info(a)  # marker home is the transform now
        self.assertEqual(info["uvRect"], rect)
        self.assertEqual(info["scaleOffset"], [1.0, 1.0, 0.0, 0.0])
        raw = DataNodes.get_export_string(LightmapBaker.LIGHTMAP_METADATA)
        rec = next(
            o for o in json.loads(raw)["objects"] if o["name"] == "rectC"
        )
        self.assertEqual(rec["scaleOffset"], [1.0, 1.0, 0.0, 0.0])
        self.assertNotIn("uvRect", rec)  # internal bookkeeping, not published

    def test_revert_restores_atlased_uvs(self):
        # LEGACY-scene guarantee: revert_lightmap inverts a recorded uvRect
        # (a physical remap an old pack applied) -- the lightmap set is back
        # at its original unit-square layout for the next bake.
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
        self.assertIsNone(baker._marker_node(a))  # cleared from BOTH homes

    def test_bake_guard_restores_stale_remap(self):
        # LEGACY-scene safety: baking over an old physical-remap atlas commit
        # restores the unit square first and strips uvRect from the marker
        # (idempotent). Current packs never write uvRect, so this guard is a
        # no-op on current-format scenes.
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
        info = baker._marker_info(a)
        self.assertTrue(info)
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
    """Affix-field stub: text()/placeholderText() + an option_box exposing
    ``resolve_affix`` the same way uitk's real ``OptionBoxManager`` does when no
    ``AffixOption`` picker is attached — auto-mode split of the given (or wrapped)
    text via ``pythontk.StrUtils.split_affix`` (see
    uitk/widgets/optionBox/utils.py::resolve_affix's no-picker fallback path)."""

    class _Menu:
        pass

    class _OptionBox:
        def __init__(self, widget):
            self.menu = _LineEdit._Menu()
            self._widget = widget

        def resolve_affix(self, text=None, *, default="prefix"):
            if text is None:
                text = self._widget.text()
            return ptk.StrUtils.split_affix(text, mode="auto", default=default)

    def __init__(self, text="_Lightmap", placeholder="_Lightmap"):
        self._text = text
        self._placeholder = placeholder
        self.option_box = _LineEdit._OptionBox(self)

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def placeholderText(self):
        return self._placeholder


class _SlotUi:
    def __init__(
        self, res=1024, samples=4, affix="_Lightmap",
        packing="Per-Object (one map each)", scope="Selected", output_dir="",
    ):
        self.footer = _Footer()
        self.cmb_resolution = _ResolutionCombo(res)
        self.spn_samples = _Spin(samples)
        self.txt000 = _LineEdit(affix)
        # Optional output-dir field: empty means "the project's sourceimages".
        self.txt_output_dir = _LineEdit(output_dir, placeholder="sourceimages")
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

    def revert(self, objects=None):
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

    def commit_lightmap(
        self, mapping, intensity=1.0, scale_offsets=None, uv_rects=None
    ):
        self.calls.append(("commit_lightmap", dict(mapping)))
        self.commit_scale_offsets = scale_offsets
        self.commit_uv_rects = uv_rects
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
        # revert -> bake_separated -> commit_lightmap (the PBR maps are kept).
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

    def test_b000_atlas_packing_consolidates_and_commits_with_scale_offsets(self):
        # Lighting Only + Atlas by Material: revert → bake_separated → pack_atlas
        # → commit_lightmap. The rects reach the commit as scale_offsets — THE
        # engine binding (Unity lightmapScaleOffset / glTF KHR_texture_transform),
        # which is what lets every instance of a shared mesh own a distinct rect.
        # NOT as uv_rects (that key is legacy already-applied-remap bookkeeping).
        long = self._select_cube()
        ui = _SlotUi(packing="Atlas by Material (shared map)")
        s = self._slots(ui)
        s.b000()

        baker = _FakeWorkflow.instances[0]
        kinds = [c[0] for c in baker.calls]
        self.assertEqual(
            kinds, ["revert", "bake_separated", "pack_atlas", "commit_lightmap"]
        )
        self.assertIn(long, baker.commit_scale_offsets)
        self.assertEqual(baker.commit_scale_offsets[long], [1.0, 1.0, 0.0, 0.0])
        self.assertIsNone(baker.commit_uv_rects)
        self.assertIn("atlas", ui.footer.text.lower())

    def test_b000_upgrades_authored_lights_before_baking(self):
        # A saved scene's tool-authored lights can reopen NORMALIZED (the
        # pre-per-area authoring; a manual Normalize fix evaporates with every
        # reopen) -- b000 must upgrade them BEFORE the bake renders them ~100x
        # dim. Recording the instance count proves it ran ahead of the baker
        # even existing, i.e. ahead of revert/bake.
        self._select_cube()
        ui = _SlotUi()
        s = self._slots(ui)
        seen = []
        with mock.patch.object(
            lmb_module.LightUtils,
            "upgrade_authored_lights",
            side_effect=lambda: seen.append(len(_FakeWorkflow.instances)) or [],
        ):
            s.b000()
        self.assertEqual(seen, [0])

    def test_b000_per_object_packing_skips_atlas(self):
        # Default Per-Object packing must NOT call pack_atlas.
        self._select_cube()
        ui = _SlotUi(packing="Per-Object (one map each)")
        s = self._slots(ui)
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertNotIn("pack_atlas", [c[0] for c in baker.calls])

    def test_b000_drives_footer_progress_and_reports_the_result(self):
        # Feedback is OUR footer: an indeterminate marquee ticked once per
        # object with per-object text, then the result summary. (mtoa opens a
        # popup of its own during the render; that one is not ours to drive.)
        longs = [self._select_cube("pbA")]
        cmds.select(longs, replace=True)
        ui = _SlotUi()
        s = self._slots(ui)
        s.b000()
        ticks = [c for c in ui.footer.progress_calls if c[0] == "tick"]
        self.assertEqual(len(ticks), len(longs))  # one footer tick per object
        self.assertIn("Baking pbA", ticks[0][2])  # names the object in flight
        self.assertIn("Baked", ui.footer.text)  # and the summary lands after

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

    def test_b000_empty_affix_falls_back_to_placeholder(self):
        # A cleared field bakes with the placeholder default ("_Lightmap" from
        # the .ui, its single source) — never affix-less files that could
        # collide with source texture names.
        self._select_cube()
        s = self._slots(_SlotUi(affix=""))
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.bake_prefix, "")
        self.assertEqual(baker.bake_suffix, "_Lightmap")

    # ---------------------------------------------------------------- output dir

    _SRC = os.path.normpath(r"C:/proj/sourceimages")

    def _slots_with_src(self, ui, src=_SRC):
        """A slots stub whose sourceimages base is pinned (no Maya project needed)."""
        s = self._slots(ui)
        s._sourceimages_dir = lambda: src
        return s

    def test_output_dir_empty_uses_sourceimages(self):
        # The default: an untouched field bakes into the project's sourceimages
        # (the conventional home for material-referenced textures).
        s = self._slots_with_src(_SlotUi(output_dir=""))
        self.assertEqual(s._output_dir(), self._SRC)

    def test_output_dir_relative_resolves_under_sourceimages(self):
        # THE feature: a relative entry is joined onto sourceimages, so the
        # setting survives a project move instead of pinning one machine's path.
        s = self._slots_with_src(_SlotUi(output_dir="lightmaps"))
        self.assertEqual(
            s._output_dir(), os.path.join(self._SRC, "lightmaps")
        )
        s.ui.txt_output_dir.setText("bake/lm")  # nested, forward slashes
        self.assertEqual(
            s._output_dir(), os.path.normpath(os.path.join(self._SRC, "bake/lm"))
        )

    def test_output_dir_absolute_is_used_as_is(self):
        s = self._slots_with_src(_SlotUi(output_dir=r"D:/bakes/lm"))
        self.assertEqual(s._output_dir(), os.path.normpath(r"D:/bakes/lm"))

    def test_output_dir_is_trimmed_and_expanded(self):
        # Paths pasted from Explorer arrive quoted and/or padded; env vars are a
        # normal way to write a shared drive. Neither may reach os.path.join raw.
        s = self._slots_with_src(_SlotUi(output_dir='  " lightmaps "  '))
        self.assertEqual(s._output_dir(), os.path.join(self._SRC, "lightmaps"))
        with mock.patch.dict(os.environ, {"LM_OUT": r"D:/shared"}):
            s.ui.txt_output_dir.setText("%LM_OUT%")
            self.assertEqual(s._output_dir(), os.path.normpath(r"D:/shared"))

    def test_output_dir_driveless_entry_stays_under_sourceimages(self):
        # "/lightmaps" is a separator-spelled SUBDIRECTORY, but os.path.isabs
        # calls it absolute on Windows -- resolving it to the current drive's
        # root, silently outside the project. The strict rooted test is what
        # keeps it under sourceimages.
        s = self._slots_with_src(_SlotUi(output_dir="/lightmaps"))
        self.assertEqual(s._output_dir(), os.path.join(self._SRC, "lightmaps"))
        s.ui.txt_output_dir.setText("/bake/lm/")
        self.assertEqual(
            s._output_dir(), os.path.normpath(os.path.join(self._SRC, "bake/lm"))
        )

    def test_output_dir_without_a_project_never_returns_a_relative_path(self):
        # No sourceimages -> the base the bake itself would have used, NOT the
        # bare entry: os.makedirs would create that against the process CWD,
        # which for Maya is wherever the app was launched from.
        fallback = os.path.normpath(r"C:/scenes/baked_lighting")
        with mock.patch.object(
            lmb_module.TextureBaker, "default_output_dir", return_value=fallback
        ):
            s = self._slots_with_src(_SlotUi(output_dir="lightmaps"), src=None)
            self.assertEqual(s._output_dir(), os.path.join(fallback, "lightmaps"))
            s.ui.txt_output_dir.setText("")
            self.assertEqual(s._output_dir(), fallback)

    def test_texture_baker_default_output_dir_is_absolute(self):
        # The slot leans on it as a base, so a relative return would reintroduce
        # the CWD bug one level up.
        out = lmb_module.TextureBaker.default_output_dir("baked_lighting")
        self.assertTrue(os.path.isabs(out), out)
        self.assertEqual(os.path.basename(out), "baked_lighting")

    def test_b000_bakes_into_the_resolved_output_dir(self):
        # The resolved dir -- not bare sourceimages -- is what reaches the bake.
        self._select_cube()
        ui = _SlotUi(output_dir="lightmaps")
        s = self._slots_with_src(ui)
        s.b000()
        self.assertEqual(
            _FakeWorkflow.instances[0].bake_output_dir,
            os.path.join(self._SRC, "lightmaps"),
        )

    def test_b000_atlas_packs_into_the_resolved_output_dir(self):
        # The atlas branch must honour it too, or a custom dir would bake the
        # per-object maps in one place and consolidate them into another.
        self._select_cube()
        ui = _SlotUi(output_dir="lightmaps", packing="Atlas by Material (shared map)")
        s = self._slots_with_src(ui)
        s.b000()
        atlas = next(iter(_FakeWorkflow.instances[0].calls[-1][1].values()))
        self.assertEqual(
            os.path.dirname(atlas), os.path.join(self._SRC, "lightmaps")
        )

    def test_relativize_stores_a_browsed_subfolder_relative(self):
        # The dialog can only return an absolute path; a pick inside
        # sourceimages is rewritten to the portable relative form.
        ui = _SlotUi()
        s = self._slots_with_src(ui)
        s._relativize_output_dir(os.path.join(self._SRC, "lightmaps", "hero"))
        self.assertEqual(ui.txt_output_dir.text(), "lightmaps/hero")
        # sourceimages itself is the default -> the field goes back to empty.
        s._relativize_output_dir(self._SRC)
        self.assertEqual(ui.txt_output_dir.text(), "")

    def test_relativize_leaves_a_dir_outside_sourceimages_absolute(self):
        # Nothing shorter would be honest, and "../.." is not portable either --
        # the absolute path the browse dialog already wrote must survive.
        outside = os.path.normpath(r"D:/bakes")
        ui = _SlotUi(output_dir=outside)  # as BrowseOption left it
        s = self._slots_with_src(ui)
        s._relativize_output_dir(outside)
        self.assertEqual(ui.txt_output_dir.text(), outside)
        # A sibling of sourceimages must not be rewritten to "../scenes" either.
        sibling = os.path.normpath(r"C:/proj/sourceimages_old")
        ui.txt_output_dir.setText(sibling)
        s._relativize_output_dir(sibling)
        self.assertEqual(ui.txt_output_dir.text(), sibling)

    def test_b000_no_selection_is_guarded(self):
        cmds.select(clear=True)
        ui = _SlotUi()
        s = self._slots(ui)
        s.b000()
        self.assertEqual(_FakeWorkflow.instances, [])  # never built a baker
        self.assertIn("Select", ui.footer.text)

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_black_bake_warning_fires_only_for_unlit_maps(self):
        # A black bake is FAITHFUL rendering of an unlit scene, so nothing
        # upstream errors -- the panel is the last place that can tell the
        # artist before the map ships to a black preview (measured: a room
        # whose generated lights sat at intensity 1 baked 0.008 and shipped).
        import numpy as np

        cv2, _ = _cv2()
        tmp = tempfile.mkdtemp(prefix="lm_black_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def exr(name, value):
            path = os.path.join(tmp, name)
            img = np.full((8, 8, 3), value, np.float32)
            cv2.imwrite(path, img)
            return path

        s = self._slots(_SlotUi())
        black = exr("black.exr", 0.001)
        lit = exr("lit.exr", 1.0)
        self.assertIn("BLACK", s._black_bake_warning({"a": black}))
        self.assertEqual(s._black_bake_warning({"a": lit}), "")
        # One healthy map among dark ones clears it (the scene HAS light).
        self.assertEqual(s._black_bake_warning({"a": black, "b": lit}), "")
        # Unreadable/missing maps must never break a finished bake.
        self.assertEqual(
            s._black_bake_warning({"a": os.path.join(tmp, "missing.exr")}), ""
        )

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
