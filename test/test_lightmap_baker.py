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

import contextlib
import os
import json
import shutil
import tempfile
import unittest
from unittest import mock

import base_test  # noqa: F401 — sys.path bootstrap for the sibling repos

import maya.cmds as cmds
import pythontk as ptk
from base_test import MayaTkTestCase
from mayatk.light_utils.lightmap_baker import lightmap_baker as lmb_module
from mayatk.light_utils.lightmap_baker import lightmap_baker_slots as slots_module
from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
from mayatk.light_utils.lightmap_baker.lightmap_baker_slots import LightmapBakerSlots
from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics
from mayatk.mat_utils.bake_sets import LightmapExcludeSet


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
        self.called_size = None
        self.called_uv_set = None
        self.called_stem = None
        self.called_on_progress = None
        self.called_shader = None
        self.called_batch = None
        self.called_claims = None
        self.card_seen_at_bake = False
        self.card_color = None
        self.card_diffuse = None

    def bake(
        self,
        objects,
        output_dir=None,
        prefix="",
        suffix="",
        backend="",
        uv_set=None,
        on_progress=None,
        stem=None,
        size=None,
        shader=None,
        batch=False,
        claims=None,
    ):
        self.called_size = size
        self.called_claims = claims
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
        img[0, 0, 3] = 0.5  # ... at half coverage
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
        # MEASURED (ROOM_ENV walls, mtoa 5.5): RTT can write alpha == 1.0
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
        # THE production artifact (shipped ROOM_ENV room, profiled from the
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
        self.assertLessEqual(float(out.max()), LightmapBaker.HALF_FLOAT_MAX)
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
        # Regression (production): real meshes reuse a pre-existing lightmap set under
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

        def spy(cls, path, alpha_threshold, iterations, uv_triangles=None, **kw):
            seen["tris"] = uv_triangles
            return real(cls, path, alpha_threshold, iterations, uv_triangles, **kw)

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
        cmds.setAttr(f"{cmds.listRelatives(light, parent=True)[0]}.rotateX", -90)
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
        cmds.setAttr(f"{cmds.listRelatives(light, parent=True)[0]}.rotateX", -90)
        means = []
        for name, color in (
            ("albDark", (0.1, 0.1, 0.1)),
            ("albBright", (0.9, 0.9, 0.9)),
        ):
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
            means[0],
            means[1],
            delta=0.03,
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
        red = float(bounced[..., 2].mean())  # cv2 is BGR
        green = float(bounced[..., 1].mean())
        self.assertGreater(red, 1e-3, "no indirect light reached the floor")
        self.assertGreater(
            red,
            3.0 * max(green, 1e-6),
            "bounce is not red -- neighbor materials were not preserved "
            "during the floor's bake (per-object carding regression)",
        )

        dark = LightmapBaker(
            resolution=32, samples=3, gi_depth=0, gi_samples=2
        ).bake_separated([floor], output_dir=os.path.join(self.tmp, "d0"))
        red0 = float(_read(next(iter(dark.values())))[..., 2].mean())
        self.assertLess(
            red0,
            0.25 * red,
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

    def test_the_white_card_leaves_no_shading_group_behind(self):
        """REGRESSION (2026-09-22): the per-object path wears the card by
        ASSIGNMENT, which wraps it in a shading group, and the teardown deleted
        the lambert alone -- one empty, shaderless ``lm_whitecardSG`` more per
        bake (a production room held three). The card's group goes with it."""
        from mayatk.mat_utils._mat_utils import MatUtils

        cube, shape, sg = self._cube_with_known_material("cardTarget")
        before = set(cmds.ls(type="shadingEngine"))
        baker = LightmapBaker()
        card = baker._create_white_card()
        MatUtils.assign_mat(cube, card)  # what _forced_shader does...
        cmds.sets(shape, edit=True, forceElement=sg)  # ...and its restore
        baker._delete_white_card(card)
        self.assertEqual(set(cmds.ls(type="shadingEngine")), before)
        self.assertFalse(cmds.ls("lm_whitecard*"))
        self.assertEqual(self._sgs(shape), [sg])

    def test_a_white_card_still_holding_a_face_is_kept_and_reported(self):
        """A restore that did not land must not leave faces with no material:
        the card's group survives with them, and the log says so."""
        from mayatk.mat_utils._mat_utils import MatUtils

        cube, shape, _sg = self._cube_with_known_material("cardStuck")
        baker = LightmapBaker()
        card = baker._create_white_card()
        MatUtils.assign_mat(cube, card)  # no restore
        card_sg = (
            cmds.listConnections(f"{card}.outColor", type="shadingEngine") or [None]
        )[0]
        with self.assertLogs(baker.logger, level="WARNING"):
            baker._delete_white_card(card)
        self.assertTrue(cmds.objExists(card_sg))
        self.assertEqual(self._sgs(shape), [card_sg])
        # The shader too: deleted first, it left the kept group shaderless.
        self.assertTrue(cmds.objExists(card), "the card stays with its faces")

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
        LightmapBaker(resolution=64, baker=fake).bake_separated([long], output_dir=tmp)

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

        def cb(done, total, name):
            return True

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
        cmds.setAttr(f"{fn}.fileTextureName", f"C:/tex/{tex_basename}", type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)
        return cmds.ls(cube, long=True)[0]

    def test_stem_from_material_texture_set(self):
        long = self._cube_with_texture("nodeName", "Plants_Metal_Base_01_BaseColor.dds")
        self.assertEqual(LightmapBaker._texture_set_stem(long), "Plants_Metal_Base_01")

    def _add_texture(self, obj, node_name, basename, plug="incandescence"):
        """Wire a SECOND file texture onto *obj*'s material.

        Which of an object's textures ``get_texture_paths`` returns first is not
        something the baker controls, so the stem must not depend on it.
        """
        shape = cmds.listRelatives(obj, shapes=True, fullPath=True)[0]
        sg = cmds.listConnections(shape, type="shadingEngine")[0]
        mat = cmds.listConnections(f"{sg}.surfaceShader")[0]
        fn = cmds.shadingNode("file", asTexture=True, name=node_name)
        cmds.setAttr(f"{fn}.fileTextureName", f"C:/tex/{basename}", type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.{plug}", force=True)
        return fn

    def test_stem_ignores_a_texture_that_is_not_a_material_map(self):
        """Regression: the stem was whatever texture happened to be found first.

        Measured on PROPS_ASSEMBLY: the object ``TABLE`` (material
        ``ROOM_ENV:Work_Table``) had its committed lightmap written as
        ``diffuse_cube_LightMap.exr`` -- ``diffuse_cube`` being Maya's
        StingrayPBS ENVIRONMENT texture, not any object or material in the
        scene, while the other 46 baked objects shared a correctly named
        ``ROOM_ENV_LightMap.exr``.

        The deliverable still rendered, so this is a naming defect rather than a
        delivery one -- but a name derived from a SHARED environment map is a
        collision waiting to happen: a second object resolving the same way
        overwrites the first one's bake. A real material map carries a map-type
        token; an environment cube does not, which is the discriminator.
        """
        obj = self._cube_with_texture("tableNode", "Work_Table_BaseColor.png")
        # ...and an environment cube, which carries no map-type token at all.
        self._add_texture(obj, "diffuse_cube", "diffuse_cube.dds")

        stem = LightmapBaker._texture_set_stem(obj)
        self.assertEqual(
            stem,
            "Work_Table",
            f"stem came from a non-material texture: {stem!r}",
        )

    def test_stem_is_none_when_no_texture_is_a_material_map(self):
        """Better an object-derived name than a shared one.

        Returning ``None`` falls the caller back to the object leaf name, which
        is unique per object. Naming the bake after a shared environment map is
        the one outcome that can silently overwrite another object's result.
        """
        cube = cmds.polyCube(name="envOnlyCube")[0]
        obj = cmds.ls(cube, long=True)[0]
        shape = cmds.listRelatives(obj, shapes=True, fullPath=True)[0]
        mat = cmds.shadingNode("lambert", asShader=True, name="envOnly_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="envOnly_SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(shape, edit=True, forceElement=sg)
        fn = cmds.shadingNode("file", asTexture=True, name="envOnly_file")
        cmds.setAttr(f"{fn}.fileTextureName", "C:/tex/diffuse_cube.dds", type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)

        self.assertIsNone(LightmapBaker._texture_set_stem(obj))

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

        raw = ptk.SceneRecords.LIGHTMAPS.read_text(DataNodes)
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
        with self.assertWarns(DeprecationWarning):  # bake(intensity=) replaces it
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
        with mock.patch.object(LightmapRecords.logger, "warning") as warn:
            baker.commit_lightmap({long: self.tex})
        objs = self._manifest()["objects"]
        self.assertEqual(objs[0]["uvIndex"], 2)
        warned = _rendered_warnings(warn)
        self.assertTrue(
            any("UV index 2" in m for m in warned),
            f"expected a uv-index warning, got: {warned}",
        )

    def test_manifest_notes_duplicate_leaf_names_without_warning(self):
        # Every record carries its hierarchy and both readers -- the GLB
        # applier and unitytk's controller -- tell same-named objects apart
        # by it, so a recurring leaf is a note for anyone on an older Unity
        # helper, not a warning asking for a rename.
        a, _, _ = self._cube_with_material("dupLeaf")
        cmds.group(a, name="dupGrpA")  # reparent -> the long name changes
        la = cmds.ls("dupGrpA|dupLeaf", long=True)[0]
        _, _, lb = self._cube_with_material("dupLeaf")  # same leaf, root level
        UvUtils.create_lightmap_uvs([la, lb], map_size=64, quiet=True)

        baker = LightmapBaker(resolution=64)
        with (
            mock.patch.object(LightmapRecords.logger, "warning") as warn,
            mock.patch.object(LightmapRecords.logger, "info") as info,
        ):
            baker.commit_lightmap({la: self.tex, lb: self.tex})
        self.assertFalse(
            [m for m in _rendered_warnings(warn) if "dupLeaf" in m],
            "a recurring leaf name is not a warning",
        )
        noted = _rendered_warnings(info)
        self.assertTrue(
            any("recur" in m and "dupLeaf" in m and "hierarchy" in m for m in noted),
            f"expected the recurring-name note, got: {noted}",
        )

    def test_manifest_publishes_each_objects_hierarchy(self):
        """FBX carries leaf names only, so two objects sharing one arrive
        downstream as two same-named nodes -- the production room's
        machine bodies are both ``BODY``, and its WebXR GLB bound one machine's
        lightmap onto both. Each record carries its scene path, root first
        (namespaces kept, like ``name``), which the GLB applier matches
        against where each node sits."""
        a, _, _ = self._cube_with_material("hierLeaf")
        cmds.group(a, name="hierGrpA")
        la = cmds.ls("hierGrpA|hierLeaf", long=True)[0]
        _, _, lb = self._cube_with_material("hierLeaf")  # same leaf, root level
        UvUtils.create_lightmap_uvs([la, lb], map_size=64, quiet=True)

        LightmapBaker(resolution=64).commit_lightmap({la: self.tex, lb: self.tex})

        hierarchies = sorted(tuple(o["hierarchy"]) for o in self._manifest()["objects"])
        self.assertEqual(hierarchies, [("hierGrpA", "hierLeaf"), ("hierLeaf",)])

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
        with mock.patch.object(LightmapRecords.logger, "warning") as warn:
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

        raw = ptk.SceneRecords.LIGHTMAPS.read_text(DataNodes)
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
        self.assertEqual(LightmapRecords._marker_info(src)["scaleOffset"], rect_a)
        self.assertEqual(LightmapRecords._marker_info(copy)["scaleOffset"], rect_b)
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
        LightmapRecords._set_string_attr(
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
        LightmapRecords._publish()
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
        self.assertEqual(LightmapRecords._marker_node(long), long)
        self.assertEqual(len(self._manifest_objects()), 1)

        baker.revert_lightmap([long])
        self.assertIsNone(LightmapRecords._marker_node(long))
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


class TestMarkerScan(MayaTkTestCase):
    """The marker scan behind ``_publish_lightmap_metadata`` / ``revert_lightmap``.

    The scan used to run ``cmds.attributeQuery(..., exists=True)`` on EVERY
    scene transform and mesh -- seconds on a production scene (measured: 4.3 s
    for 3,020 transforms + 1,511 meshes; 0.11 s after). It is now one
    attribute-scoped :meth:`LightmapRecords._marked_nodes` lookup plus an O(1)
    set test, so this class pins the equivalence rather than the speed: the
    scan must still find exactly what the walk found across namespaces,
    references, intermediate shapes, DAG instances, duplicate short names and
    a marker that exists but was never set.
    """

    ATTR = LightmapBaker.LIGHTMAP_INFO_ATTR

    def setUp(self):
        super().setUp()
        self.store = ptk.TempArtifacts("mayatk_marker_scan")
        self.addCleanup(self.store.cleanup)

    # -- fixtures ---------------------------------------------------------
    def _stamp(self, node, payload=None):
        """Add the marker to *node*; leave it UNSET when *payload* is None."""
        if not cmds.attributeQuery(self.ATTR, node=node, exists=True):
            cmds.addAttr(node, longName=self.ATTR, dataType="string")
        if payload is not None:
            cmds.setAttr(f"{node}.{self.ATTR}", json.dumps(payload), type="string")

    def _info(self, name):
        return {
            "map": f"{name}.exr",
            "uv_set": "lightmap",
            "intensity": 1.0,
            "scaleOffset": [1.0, 1.0, 0.0, 0.0],
            "mode": "separated",
        }

    def _reference_file(self):
        """A one-marked-mesh .ma to reference back in (the namespace case)."""
        path = self.store.path(".ma", name="lm_marker_ref")
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="refCube")[0]
        self._stamp(cmds.ls(cube, long=True)[0], self._info("ref"))
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        return path

    def _build_scene(self):
        """Every case the scoped lookup has to reproduce, in one scene."""
        ref = self._reference_file()

        plain = cmds.ls(cmds.polyCube(name="plainCube")[0], long=True)[0]
        self._stamp(plain, self._info("plain"))
        cmds.polyCube(name="unmarkedCube")  # must NOT be collected
        cmds.spaceLocator(name="unmarkedLoc")  # a transform that is not a mesh

        cmds.namespace(add="NS")
        cmds.namespace(set="NS")
        ns = cmds.ls(cmds.polyCube(name="nsCube")[0], long=True)[0]
        cmds.namespace(set=":")
        self._stamp(ns, self._info("ns"))

        cmds.namespace(add=":NS:INNER")
        cmds.namespace(set=":NS:INNER")
        inner = cmds.ls(cmds.polyCube(name="innerCube")[0], long=True)[0]
        cmds.namespace(set=":")
        self._stamp(inner, self._info("inner"))

        cmds.file(ref, reference=True, namespace="REF")

        inst = cmds.ls(cmds.polyCube(name="instCube")[0], long=True)[0]
        self._stamp(inst, self._info("inst"))
        # cmds.instance copies the transform's dynamic attrs, so the copy is
        # already marked -- re-stamp only to give it its own payload.
        inst_copy = cmds.ls(cmds.instance(inst)[0], long=True)[0]
        self._stamp(inst_copy, self._info("instCopy"))

        # Legacy home: the marker on the mesh SHAPE, not the transform.
        legacy = cmds.ls(cmds.polyCube(name="legacyCube")[0], long=True)[0]
        self._stamp(
            cmds.listRelatives(legacy, shapes=True, fullPath=True)[0],
            self._info("legacy"),
        )

        # Intermediate (deformer orig) shape carrying the marker.
        inter = cmds.ls(cmds.polyCube(name="interCube")[0], long=True)[0]
        cmds.cluster(inter)
        for shape in cmds.listRelatives(inter, shapes=True, fullPath=True) or []:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                self._stamp(shape, self._info("inter"))

        # An instanced GROUP: ONE member transform NODE reachable by TWO DAG
        # paths (unlike instCube above, where cmds.instance made a second
        # transform node). This is the case the long-name membership test can
        # actually get wrong -- the scoped lookup and the type-scoped listing
        # must name the member by the SAME path, or a marked mesh drops out of
        # the manifest silently. Both report only |grpSrc|member.
        src_group = cmds.group(empty=True, name="grpSrc")
        member = cmds.ls(
            cmds.parent(cmds.polyCube(name="member")[0], src_group)[0], long=True
        )[0]
        self._stamp(member, self._info("member"))
        cmds.instance(src_group, name="grpCopy")

        # Two marked meshes sharing a leaf name under different parents.
        for parent in ("grpA", "grpB"):
            group = cmds.group(empty=True, name=parent)
            cmds.parent(cmds.polyCube(name="dupLeaf")[0], group)
            self._stamp(cmds.ls(f"{group}|*", long=True)[0], self._info(parent))

        # Marker present but never set -- found by the scan, dropped later by
        # _marker_info (unparsable -> {}), so it must not reach the manifest.
        unset = cmds.ls(cmds.polyCube(name="unsetCube")[0], long=True)[0]
        self._stamp(unset, None)

        # Marked nodes of a type the scan does not collect.
        self._stamp(
            cmds.shadingNode("lambert", asShader=True, name="markedLambert"),
            self._info("lambert"),
        )
        nurbs = cmds.ls(cmds.sphere(name="nurbsBall")[0], long=True)[0]
        self._stamp(
            cmds.listRelatives(nurbs, shapes=True, fullPath=True)[0],
            self._info("nurbs"),
        )

    # -- the pre-optimization implementation, kept as the oracle -----------
    @classmethod
    def _legacy_walk(cls):
        """The O(scene) walk this scan replaced, verbatim (transform + mesh)."""
        return [
            node
            for kind in ("transform", "mesh")
            for node in (cmds.ls(type=kind, long=True) or [])
            if cmds.attributeQuery(cls.ATTR, node=node, exists=True)
        ]

    @classmethod
    def _scoped_candidates(cls, baker):
        """The same listing, driven by the scoped lookup."""
        marked = LightmapRecords._marked_nodes()
        return [
            node
            for kind in ("transform", "mesh")
            for node in (cmds.ls(type=kind, long=True) or [])
            if node in marked
        ]

    # -- tests ------------------------------------------------------------
    def test_scoped_lookup_matches_the_legacy_walk(self):
        """Same nodes, same order, on the scene that holds every hard case."""
        self._build_scene()
        baker = LightmapBaker(resolution=16)

        legacy = self._legacy_walk()
        scoped = self._scoped_candidates(baker)

        self.assertEqual(scoped, legacy)
        # A namespaced or referenced marker is exactly what a non-recursive
        # pattern drops, so prove they are actually in there.
        self.assertTrue(any(n.startswith("|NS:nsCube") for n in legacy))
        self.assertTrue(any("NS:INNER:innerCube" in n for n in legacy))
        self.assertTrue(any("REF:refCube" in n for n in legacy))

    def test_marked_nodes_pins_the_collected_set(self):
        """The raw lookup: every marked node, whatever its type or home."""
        self._build_scene()
        marked = LightmapRecords._marked_nodes()

        expected = {
            "|plainCube",
            "|NS:nsCube",
            "|NS:INNER:innerCube",
            "|REF:refCube",
            "|instCube",
            "|instCube1",
            "|legacyCube|legacyCubeShape",
            "|interCube|interCubeShapeOrig",
            "|grpSrc|member",
            "|grpA|dupLeaf",
            "|grpB|dupLeaf",
            "|unsetCube",
            "markedLambert",
            "|nurbsBall|nurbsBallShape",
        }
        self.assertEqual(marked, expected)
        # Unmarked siblings must never appear.
        self.assertNotIn("|unmarkedCube", marked)
        self.assertNotIn("|unmarkedLoc", marked)
        # The instanced group's SECOND path is named by neither listing, which
        # is precisely why the membership test is exact.
        self.assertNotIn("|grpCopy|member", marked)
        self.assertNotIn("|grpCopy|member", cmds.ls(type="transform", long=True))

    def test_manifest_records_survive_the_scoped_scan(self):
        """End to end: the published manifest is what the walk would publish."""
        self._build_scene()
        LightmapRecords._publish()

        from mayatk.node_utils.data_nodes import DataNodes

        raw = ptk.SceneRecords.LIGHTMAPS.read_text(DataNodes)
        names = [o["name"] for o in json.loads(raw)["objects"]]

        # Namespaces are PUBLISHED (the engine matches the exported name), the
        # DAG path is not, and the unset marker parses to {} and is skipped.
        self.assertEqual(
            sorted(names),
            [
                "NS:INNER:innerCube",
                "NS:nsCube",
                "REF:refCube",
                "dupLeaf",
                "dupLeaf",
                "instCube",
                "instCube1",
                "legacyCube",
                "member",
                "plainCube",
            ],
        )
        self.assertNotIn("unsetCube", names)
        self.assertNotIn("nurbsBall", names)
        self.assertNotIn("markedLambert", names)
        # PRE-EXISTING GAP, pinned deliberately (unchanged by the scoped scan):
        # a marker on an INTERMEDIATE shape is collected by the scan but
        # dropped by _marker_node, which resolves a shape through
        # NodeUtils.get_shape -- that returns the deformed (non-intermediate)
        # shape, so the orig's marker is unreachable. It therefore publishes
        # nothing here and revert cannot clear it either (see
        # test_revert_all_clears_every_marked_node). Reachable in the wild: a
        # deformer's orig shape is a copy, dynamic attributes included, so a
        # legacy shape-stamped mesh that later gets a deformer grows one.
        self.assertNotIn("interCube", names)

    def test_revert_all_clears_every_marked_node(self):
        """``revert_lightmap()`` with no argument uses the same scan."""
        self._build_scene()
        baker = LightmapBaker(resolution=16)
        expected = self._legacy_walk()

        cleared = baker.revert_lightmap()

        # Everything the scan found is cleared EXCEPT the intermediate-shape
        # marker -- the same pre-existing _marker_node gap the manifest test
        # pins; the scoped scan changed neither side of it.
        orig = "|interCube|interCubeShapeOrig"
        self.assertIn(orig, expected)
        self.assertEqual(sorted(set(cleared)), sorted(set(expected) - {orig}))
        self.assertEqual(self._scoped_candidates(baker), [orig])


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
            for u, v in zip(
                *[iter(cmds.polyEditUV(f"{shape}.map[*]", query=True))] * 2
            ):
                us.append(u)
                vs.append(v)
            return min(us), max(us), min(vs), max(vs)
        finally:
            if prev and prev != uv_set:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev)

    def test_an_unlaid_out_map_is_kept_rather_than_dropped(self):
        # pack_atlas resolves its layout through atlas_plan, which resolves
        # meshes -- so a name that no longer IS one (deleted between bake and
        # pack) would fall out of the layout walk and vanish. The contract is
        # that a bake is never lost: it comes back as its own map.
        sg, _mat = self._make_sg("packOrphan")
        kept = self._cube_on_sg("packOrphanKept", sg)
        doomed = self._cube_on_sg("packOrphanGone", sg)
        mapping = {
            kept: self._solid_exr("packOrphanKept.exr", 0.5),
            doomed: self._solid_exr("packOrphanGone.exr", 0.25),
        }
        cmds.delete(doomed)  # its map is on disk; its node is not

        baker = LightmapBaker(resolution=32)
        with mock.patch.object(baker, "logger") as log:
            out = baker.pack_atlas(mapping, output_dir=self.tmp)

        self.assertEqual(set(out), {kept, doomed})
        for path, rect in out.values():
            self.assertTrue(os.path.exists(path))
            self.assertEqual(os.path.dirname(path), self.tmp)
        self.assertEqual(out[doomed][1], [1.0, 1.0, 0.0, 0.0])
        self.assertTrue(
            any("not in the layout" in str(c) for c in log.warning.call_args_list),
            log.warning.call_args_list,
        )

    def test_packs_into_a_destination_that_does_not_exist_yet(self):
        # bake_atlas stages its tiles in a temp dir, so the atlas is the FIRST
        # thing ever written to the output dir. cv2 reports a missing parent as
        # "can't write data: unknown exception", which the group guard then
        # caught -- the pack silently degraded to nine per-object maps instead
        # of one atlas (measured on the production room).
        sg, _mat = self._make_sg("packMkdir")
        a = self._cube_on_sg("packMkdirA", sg)
        b = self._cube_on_sg("packMkdirB", sg)
        mapping = {
            a: self._solid_exr("packMkdirA.exr", 0.5),
            b: self._solid_exr("packMkdirB.exr", 0.25),
        }
        dest = os.path.join(self.tmp, "not", "there", "yet")
        out = LightmapBaker(resolution=32).pack_atlas(mapping, output_dir=dest)

        self.assertEqual(len(out), 2)
        paths = {p for p, _rect in out.values()}
        self.assertEqual(len(paths), 1, "the group must share ONE atlas")
        atlas = paths.pop()
        self.assertEqual(os.path.dirname(atlas), dest)
        self.assertTrue(os.path.exists(atlas))

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
        self.assertEqual(atlas_a, atlas_b)  # same material -> consolidated
        self.assertNotEqual(atlas_a, atlas_c)  # different material -> own map
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
                        float(texel[ch]),
                        colors[obj][ch],
                        places=2,
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
        colors = [
            (0.2, 0.4, 0.8),
            (0.8, 0.4, 0.2),
            (0.1, 0.9, 0.3),
            (0.9, 0.1, 0.5),
            (0.5, 0.5, 0.5),
            (0.3, 0.7, 0.2),
        ]
        mapping = {}
        for i, color in enumerate(colors):
            obj = self._cube_on_sg(f"full{i}", sg, "Full_Base_BaseColor.png")
            mapping[obj] = self._solid_exr(f"full{i}.exr", color)
        out = LightmapBaker(resolution=32).pack_atlas(mapping, output_dir=self.tmp)
        atlas_path = next(iter(out.values()))[0]
        atlas = cv2.imread(atlas_path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        zero = ~(atlas.max(axis=2) > 0)
        self.assertEqual(
            int(zero.sum()),
            0,
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
                    px % 1.0,
                    0.5,
                    places=4,
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
            float(cropped.max()),
            1.0,
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
            for b in names[i + 1 :]:
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
            int(zero.sum()),
            0,
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
            self.assertTrue(os.path.exists(src), f"{obj}'s source map was consumed")
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
        with self.assertWarns(DeprecationWarning):  # removed in 0.20.0
            baker.commit_lightmap(
                {a: self._solid_exr("rectC.exr", (1, 1, 1))}, uv_rects={a: rect}
            )
        info = LightmapRecords._marker_info(a)  # marker home is the transform now
        self.assertEqual(info["uvRect"], rect)
        self.assertEqual(info["scaleOffset"], [1.0, 1.0, 0.0, 0.0])
        raw = ptk.SceneRecords.LIGHTMAPS.read_text(DataNodes)
        rec = next(o for o in json.loads(raw)["objects"] if o["name"] == "rectC")
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
        LightmapRecords._transform_lightmap_uvs(shape, lm, rect)
        with self.assertWarns(DeprecationWarning):
            baker.commit_lightmap(
                {a: self._solid_exr("revU.exr", (1, 1, 1))}, uv_rects={a: rect}
            )
        baker.revert_lightmap([a])
        for got, want in zip(self._uv_bounds(a), pre):
            self.assertAlmostEqual(got, want, places=5)
        self.assertIsNone(LightmapRecords._marker_node(a))  # cleared from BOTH homes

    def test_migration_restores_a_legacy_remap_losslessly(self):
        """LEGACY-scene safety: a bake over an old squeezed-UV atlas commit
        restores the unit square first -- and folds the rect into the binding,
        so the object still samples its own cell of the old atlas. Restored
        WITHOUT the fold (what the pre-bake guard did), the marker kept an
        identity binding over unsqueezed UVs: the WHOLE atlas, i.e. every
        other object's lighting, the moment that object's re-bake failed."""
        sg, _ = self._make_sg("Guard")
        a = self._cube_on_sg("guardA", sg)
        pre = self._uv_bounds(a)
        baker = LightmapBaker(resolution=16)
        rect = [0.25, 0.25, 0.5, 0.5]
        shape = cmds.listRelatives(a, shapes=True, fullPath=True)[0]
        lm = UvDiagnostics.find_lightmap_uv_set(shape)
        LightmapRecords._transform_lightmap_uvs(shape, lm, rect)
        with self.assertWarns(DeprecationWarning):
            baker.commit_lightmap(
                {a: self._solid_exr("guard.exr", (1, 1, 1))}, uv_rects={a: rect}
            )
        self.assertEqual(LightmapRecords.migrate_legacy([a]), [a])
        for got, want in zip(self._uv_bounds(a), pre):
            self.assertAlmostEqual(got, want, places=5)
        info = LightmapRecords._marker_info(a)
        self.assertNotIn("uvRect", info)
        self.assertEqual(info["scaleOffset"], rect)  # the old cell, as a binding
        self.assertEqual(LightmapRecords.migrate_legacy([a]), [])  # idempotent
        for got, want in zip(self._uv_bounds(a), pre):
            self.assertAlmostEqual(got, want, places=5)

    def test_migration_moves_a_shape_marker_to_its_transform(self):
        a = self._cube_on_sg("shapeHome", self._make_sg("ShapeHome")[0])
        shape = cmds.listRelatives(a, shapes=True, fullPath=True)[0]
        info = {"map": "old.exr", "uv_set": "lightmap", "scaleOffset": [1, 1, 0, 0]}
        LightmapRecords._set_string_attr(
            shape, LightmapRecords.LIGHTMAP_INFO_ATTR, json.dumps(info)
        )
        self.assertEqual(LightmapRecords.migrate_legacy(), [a])
        self.assertEqual(LightmapRecords._marker_node(a), a)
        self.assertFalse(
            cmds.attributeQuery(
                LightmapRecords.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            )
        )
        self.assertEqual(LightmapRecords._marker_info(a)["map"], "old.exr")

    def test_migration_drops_a_remap_whose_uv_set_is_gone(self):
        """A legacy ``uvRect`` whose UV set was deleted (or renamed) since has no
        remap left to restore: migration drops the record and leaves the
        binding and every UV set alone. Left in place, the next bake's commit
        carried the rect forward onto the lightmap set the bake itself built,
        and the next migration inverted it over that fresh unwrap. Nothing is
        folded: whatever UVs the object has, the identity binding samples them
        as the old map was baked."""
        a = self._cube_on_sg("goneSet", self._make_sg("GoneSet")[0])
        pre = {s: self._uv_bounds(a, s) for s in ("map1", None)}
        identity = [1.0, 1.0, 0.0, 0.0]
        LightmapRecords._write_marker(
            a,
            {
                "map": "old.exr",
                "uv_set": "deletedLightmapSet",
                "scaleOffset": identity,
                "uvRect": [0.25, 0.25, 0.5, 0.5],
            },
        )
        self.assertEqual(LightmapRecords.migrate_legacy([a]), [a])
        for uv_set, bounds in pre.items():
            got = self._uv_bounds(a, uv_set)
            for g, w in zip(got, bounds):
                self.assertAlmostEqual(g, w, places=5, msg=f"{uv_set}: {got}")
        info = LightmapRecords._marker_info(a)
        self.assertNotIn("uvRect", info)
        self.assertEqual(info["scaleOffset"], identity)

    def test_a_remap_is_never_inverted_over_a_set_it_was_not_applied_to(self):
        """``polyUVSet -currentUVSet`` ignores a set the shape lacks, and
        ``polyEditUV`` then edits whichever set IS current: reverting a legacy
        rect whose set was gone inverted it over the live lightmap set (bounds
        0..1 -> -2..1.8). The UV transform refuses a missing set instead."""
        a = self._cube_on_sg("goneRevert", self._make_sg("GoneRevert")[0])
        pre = {s: self._uv_bounds(a, s) for s in ("map1", None)}
        LightmapRecords._write_marker(
            a,
            {
                "map": "old.exr",
                "uv_set": "deletedLightmapSet",
                "uvRect": [0.25, 0.25, 0.5, 0.5],
            },
        )
        LightmapRecords.revert([a])
        self.assertIsNone(LightmapRecords._marker_node(a))
        for uv_set, bounds in pre.items():
            got = self._uv_bounds(a, uv_set)
            for g, w in zip(got, bounds):
                self.assertAlmostEqual(g, w, places=5, msg=f"{uv_set}: {got}")

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
        with (
            mock.patch.object(
                ptk.ImgUtils, "assemble_atlas", side_effect=RuntimeError("boom")
            ),
            mock.patch.object(baker.logger, "warning") as warn,
        ):
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
        LightmapRecords._transform_lightmap_uvs(shape, lm, rect)
        LightmapRecords._transform_lightmap_uvs(shape, lm, rect, invert=True)
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

        objs = json.loads(ptk.SceneRecords.LIGHTMAPS.read_text(DataNodes))["objects"]
        self.assertEqual(len(objs), 2)
        # The atlased objects carry real (non-identity) scaleOffset rects.
        self.assertTrue(any(o["scaleOffset"] != [1.0, 1.0, 0.0, 0.0] for o in objs))


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestDenoise(unittest.TestCase):
    """Every map is denoised where it SHIPS: a per-object map at its own size,
    an atlas tile at the cell it is shrunk into.

    Arnold's bake has no denoiser (RTT ignores imagers), so a map shipped its
    sampling noise: measured on a production floor, 9% per texel in the cell,
    read as splotches in the WebXR preview. Added: 2026-09-21
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lm_denoise_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @staticmethod
    def _map(size=64, sigma=0.15, seed=3):
        """An RGBA bake: noisy light over the island (left 3/4), a shadow block
        inside it with a few texels at ~zero (a starved contact shadow), and
        an empty gutter on the right."""
        _cv2_, np = _cv2()
        rng = np.random.default_rng(seed)
        img = np.zeros((size, size, 4), np.float32)
        island = np.zeros((size, size), bool)
        island[:, : size * 3 // 4] = True
        light = np.full((size, size), 1.0, np.float32)
        light[20:30, 20:30] = 0.3
        light *= np.exp(rng.normal(0.0, sigma, (size, size))).astype(np.float32)
        light[24, 24] = light[25, 26] = 1e-6
        img[..., :3] = (light * island)[..., None]
        img[..., 3] = island
        return img, island

    def _write(self, img):
        cv2, _np = _cv2()
        path = os.path.join(self.tmp, f"map_{len(os.listdir(self.tmp))}.exr")
        cv2.imwrite(path, img)
        return path

    @staticmethod
    def _grain(rgb, where):
        """Per-texel noise: log residual against a 5x5 mean, over *where*."""
        cv2, np = _cv2()
        log = np.log(np.maximum(rgb.mean(axis=2), 1e-6)).astype(np.float32)
        return float((log - cv2.blur(log, (5, 5)))[where].std())

    def test_a_per_object_map_is_denoised_before_its_refill(self):
        _cv2_, np = _cv2()
        img, island = self._map()
        raw, clean = self._write(img), self._write(img)
        LightmapBaker._dilate_lightmap(raw, alpha_threshold=0.05, iterations=8)
        LightmapBaker._dilate_lightmap(
            clean, alpha_threshold=0.05, iterations=8, denoise=True
        )
        raw, clean = _read(raw), _read(clean)
        lit = np.zeros(island.shape, bool)
        lit[35:60, 5:40] = True
        self.assertLess(self._grain(clean, lit), 0.4 * self._grain(raw, lit))
        # The shadow block is kept, not refilled from the lit floor around it.
        self.assertLess(clean[22:28, 21:23].mean(), 0.45)

    def test_a_tile_keeps_its_islands_geometry_as_coverage(self):
        """Near-zero texels in a contact shadow are refilled at bake size (the
        dead-texel rescue) but stay the island's OWN in the coverage the pack
        reads -- a mask that dropped them would refill the shadow later."""
        _cv2_, np = _cv2()
        img, island = self._map()
        path = self._write(img)
        LightmapBaker._dilate_lightmap(
            path, alpha_threshold=0.05, iterations=8, keep_coverage=True
        )
        out = _read(path)
        self.assertEqual(out.shape[2], 4)
        np.testing.assert_array_equal(out[..., 3] > 0.5, island)
        self.assertEqual(float(out[24, 24, 3]), 1.0)

    def test_a_tile_is_finished_opaque_at_its_cell(self):
        _cv2_, np = _cv2()
        img, island = self._map(size=128)
        baker = LightmapBaker(resolution=128)
        tile = baker._finish_tile(img, (32, 32))
        self.assertEqual(tile.shape, (32, 32, 3))
        # The shipped cell carries less grain than a plain shrink of the tile.
        cv2, _np = _cv2()
        shrunk = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)[..., :3]
        where = np.zeros((32, 32), bool)
        where[18:30, 2:20] = True
        self.assertLess(self._grain(tile, where), 0.6 * self._grain(shrunk, where))

    def test_off_is_the_path_as_it_was(self):
        _cv2_, np = _cv2()
        img, _island = self._map()
        off = LightmapBaker(resolution=64, denoise=False)
        np.testing.assert_array_equal(off._finish_tile(img, (16, 16)), img[..., :3])
        rgb = img[..., :3].copy()
        np.testing.assert_array_equal(
            LightmapBaker(resolution=64)._finish_tile(rgb, (16, 16)), rgb
        )

    def test_the_preset_carries_the_setting(self):
        self.assertTrue(LightmapBaker.from_preset("quest").denoise)
        self.assertFalse(LightmapBaker.from_preset("quest", denoise=False).denoise)


class TestDilateRingScalesWithTheMap(unittest.TestCase):
    """The gutter ring is a width in TEXELS, so it is sized from the image.

    An atlas bake renders every object at its own footprint, so one figure
    taken from the baker's resolution would over-dilate every small tile --
    and under a duck-typed injected baker there is no resolution to read at
    all. Deriving it where the image is loaded removes both problems.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lm_ring_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _exr(self, size):
        """A half-covered RGBA map of *size*, so there is a gutter to fill."""
        cv2, np = _cv2()
        path = os.path.join(self.tmp, f"ring_{size}.exr")
        img = np.zeros((size, size, 4), np.float32)
        img[:, : size // 2, :3] = 0.5
        img[:, : size // 2, 3] = 1.0
        cv2.imwrite(path, img)
        return path

    def _ring_for(self, size, **kwargs):
        """The ``iterations`` the dilate primitive is handed for a *size* map.

        The primitive is stubbed to return its input: what is under test is the
        WIDTH chosen, and running a 16-pass dilate over a megapixel map to read
        one integer back would make this the slowest test in the suite.
        """
        seen = {}

        def spy(image, mask=None, iterations=1, **kw):
            seen.setdefault("iterations", iterations)
            return image

        with mock.patch.object(ptk.ImgUtils, "dilate_image", spy):
            LightmapBaker._dilate_lightmap(
                self._exr(size), alpha_threshold=0.05, **kwargs
            )
        return seen.get("iterations")

    def test_the_ring_tracks_the_maps_own_size(self):
        self.assertEqual(self._ring_for(1024), 16)  # 1024 // 64
        self.assertEqual(self._ring_for(640), 10)  # and continuously between

    def test_small_maps_keep_the_floor(self):
        # A footprint tile must still get a mip-safe gutter, not a 2px one.
        self.assertEqual(self._ring_for(128), 8)

    def test_an_explicit_ring_still_wins(self):
        self.assertEqual(self._ring_for(1024, iterations=3), 3)


class TestIncludeEnvironment(MayaTkTestCase):
    """The bake can leave the scene's HDRI environment out.

    An HDRI is often a backdrop or a look-dev convenience rather than the
    room's real lighting, and baking one in is a flat ambient lift that cannot
    be taken back out of the map afterwards -- so it is a toggle, not a path
    field: the dome is already placed and oriented in the scene.
    Twin of blendertk's, which detaches the world instead.
    """

    def _skydome(self, name):
        cmds.loadPlugin("mtoa", quiet=True)
        shape = cmds.createNode("aiSkyDomeLight", name=f"{name}Shape")
        transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        return transform, cmds.ls(shape, long=True)[0]

    def test_environment_lights_finds_the_dome(self):
        _transform, shape = self._skydome("probeDome")
        self.assertIn(shape, lmb_module.LightUtils.environment_lights())
        # ...and it is a light like any other to the general enumeration.
        self.assertIn(shape, lmb_module.LightUtils.all_lights())

    def test_off_hides_the_dome_for_the_bake_and_restores_it(self):
        transform, _shape = self._skydome("mutedDome")
        baker = LightmapBaker(include_environment=False)
        self.assertTrue(cmds.getAttr(f"{transform}.visibility"))
        with baker._muted_environment():
            self.assertFalse(cmds.getAttr(f"{transform}.visibility"))
        self.assertTrue(cmds.getAttr(f"{transform}.visibility"))

    def test_on_is_the_scene_as_authored(self):
        transform, _shape = self._skydome("keptDome")
        with LightmapBaker()._muted_environment():
            self.assertTrue(cmds.getAttr(f"{transform}.visibility"))
        self.assertTrue(cmds.getAttr(f"{transform}.visibility"))

    def test_a_failed_bake_still_restores_the_dome(self):
        transform, _shape = self._skydome("boomDome")
        with self.assertRaises(RuntimeError):
            with LightmapBaker(include_environment=False)._muted_environment():
                raise RuntimeError("bake blew up")
        self.assertTrue(cmds.getAttr(f"{transform}.visibility"))

    def test_an_hdri_only_scene_warns_when_the_environment_is_left_out(self):
        # A dome the bake is about to mute is not a light source FOR that bake.
        _transform, dome = self._skydome("onlyDome")
        with (
            mock.patch.object(lmb_module.LightUtils, "all_lights", return_value=[dome]),
            mock.patch.object(
                lmb_module.LightUtils, "environment_lights", return_value=[dome]
            ),
        ):
            kept = LightmapBaker()
            kept._warn_if_unlit_scene()
            self.assertFalse(kept._warned_no_lights)

            left_out = LightmapBaker(include_environment=False)
            with self.assertLogs(left_out.logger, level="WARNING") as caught:
                left_out._warn_if_unlit_scene()
        self.assertTrue(left_out._warned_no_lights)
        self.assertIn("environment", "\n".join(caught.output).lower())

    def test_from_preset_carries_the_non_numeric_overrides(self):
        # Filtering the overrides to the int keys silently DROPPED these, so
        # from_preset("quest", device="GPU") built a baker on the scene's own
        # device and said nothing.
        baker = LightmapBaker.from_preset(
            "quest", device="CPU", include_environment=False
        )
        self.assertEqual(baker.device, "CPU")
        self.assertFalse(baker.include_environment)
        self.assertEqual(baker.resolution, 1024)  # the tier still applies

    def test_an_explicit_device_wins_over_an_injected_bakers_own(self):
        # device= is read back through the baker, so an injected one silently
        # answering something else would make the argument and the property
        # disagree.
        from mayatk.mat_utils.texture_baker import TextureBaker

        injected = TextureBaker(resolution=16, device="CPU")
        baker = LightmapBaker(baker=injected, device="GPU")
        self.assertEqual(baker.device, "GPU")
        self.assertEqual(injected.device, "GPU")
        # ...and an injected baker with no device= asked for keeps its own.
        kept = TextureBaker(resolution=16, device="CPU")
        self.assertEqual(LightmapBaker(baker=kept).device, "CPU")

    def test_the_device_reaches_the_bake_primitive(self):
        baker = LightmapBaker(device="GPU")
        self.assertEqual(baker.baker.device, "GPU")
        baker.device = "CPU"
        self.assertEqual(baker.baker.device, "CPU")


class TestAtlasPlanFirst(MayaTkTestCase):
    """The atlas layout is decided BEFORE the bake, and sizes the bake.

    The two-call form (bake_separated -> pack_atlas) renders every object at
    the full atlas resolution and then resizes it into an area-weighted rect,
    so N objects sharing one atlas pay N times the rays the atlas can hold.
    Measured in a production room (Arnold, 8 objects): 19.1s of scene
    translation per RTT call plus ~0.11ms per sample-texel, i.e. ~6.7 hours for
    50 objects on a 1024 atlas at 4 samples against ~10 minutes baked to plan.

    No renderer here: what is pinned is that the plan exists before any ray is
    traced, that it is what the bake is sized from, and that the SAME plan is
    handed to the pack -- so the rect a map was rendered for cannot disagree
    with the rect it is placed in.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_plan_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @staticmethod
    def _sg(name):
        mat = cmds.shadingNode("lambert", asShader=True, name=f"{name}_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        return sg

    def _cube(self, name, sg, size=1.0):
        cube = cmds.polyCube(name=name, w=size, h=size, d=size)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.sets(shape, edit=True, forceElement=sg)
        return cmds.ls(cube, long=True)[0]

    def test_plan_groups_by_material_and_weights_by_area(self):
        sg = self._sg("planShared")
        big = self._cube("planBig", sg, size=4.0)
        small = self._cube("planSmall", sg, size=1.0)
        solo = self._cube("planSolo", self._sg("planOwn"))

        plan = LightmapBaker(resolution=64).atlas_plan([big, small, solo])

        # One entry per material; the solo group keeps the identity rect (it is
        # already its own atlas, so its bake is a full map).
        self.assertEqual(len(plan), 2)
        solo_entries = [e for e in plan.values() if len(e) == 1][0]
        self.assertEqual(solo_entries[0][0], solo)
        self.assertEqual(solo_entries[0][1], [1.0, 1.0, 0.0, 0.0])

        shared = [e for e in plan.values() if len(e) == 2][0]
        rects = dict(shared)
        # 4x the edge is 16x the area: the big cube must earn more texels.
        area = lambda rect: rect[0] * rect[1]  # noqa: E731
        self.assertGreater(area(rects[big]), area(rects[small]))
        # Nothing was baked, read or written to get here.
        self.assertEqual(os.listdir(self.tmp), [])

    def test_plan_sizes_are_the_cells_the_pack_will_place_into(self):
        sg = self._sg("planSized")
        a = self._cube("planSizedA", sg)
        b = self._cube("planSizedB", sg)
        baker = LightmapBaker(resolution=256)

        plan = baker.atlas_plan([a, b])
        sizes = baker.plan_sizes(plan)
        entries = plan[list(plan)[0]]
        rects = ptk.ImgUtils.atlas_pixel_rects(
            [rect for _n, rect in entries], baker.resolution
        )
        for (name, _rect), (row0, row1, col0, col1) in zip(entries, rects):
            self.assertEqual(sizes[name], (col1 - col0, row1 - row0))
        # Two equal cubes share the atlas: neither may be sized at the whole of
        # it, which is exactly what bake-full-then-pack used to render.
        self.assertTrue(all(max(wh) < baker.resolution for wh in sizes.values()))

    def test_bake_size_is_quantized_and_capped(self):
        baker = LightmapBaker(resolution=2048)
        quantum = LightmapBaker._ATLAS_BAKE_QUANTUM
        with mock.patch.object(LightmapBaker, "_lightmap_uv_bbox", return_value=None):
            sizes = baker._plan_bake_sizes({"a": (100, 60), "b": (1024, 1024)})
        self.assertEqual(sizes["a"] % quantum, 0)
        self.assertGreaterEqual(sizes["a"], 100)  # the longer axis, rounded up
        self.assertLess(sizes["a"], baker.resolution)
        self.assertEqual(sizes["b"], baker.resolution)  # never above a full map

    def test_an_atlas_tile_renders_above_its_cell_because_nothing_denoises_it(self):
        """The assembler's INTER_AREA resize into the cell is the only noise
        filter an Arnold atlas gets: RTT ignores imagers, so nothing denoises
        the map. Tiles rendered AT their cell kept every sample's noise --
        measured on a production room at quest (1024 / 4 samples), the floor
        cells shipped 2.3x the shadow noise of the pre-plan-first full-size
        bake and read as splotches in the WebXR preview. A tile renders at a
        multiple of its cell, never above the full map."""
        baker = LightmapBaker(resolution=1024)
        with mock.patch.object(LightmapBaker, "_lightmap_uv_bbox", return_value=None):
            sizes = baker._plan_bake_sizes(
                {"prop": (100, 100), "floor": (256, 218), "wall": (600, 300)}
            )
        self.assertGreaterEqual(sizes["prop"], 4 * 100)
        # The production floor cell: back to the full-size render it had
        # before plan-first, i.e. exactly the pre-regression noise.
        self.assertEqual(sizes["floor"], baker.resolution)
        self.assertEqual(sizes["wall"], baker.resolution)  # capped at a full map

    def test_partial_island_coverage_raises_the_bake_size(self):
        # _pack_group crops a partial-coverage map to its island bbox and folds
        # the crop into the published rect, so only that fraction of the map's
        # texels reach the cell. Rendering the cell size flat would hand the
        # assembler a tile to UPSCALE -- softer than bake-full-then-pack for
        # exactly the unwraps that need it most.
        baker = LightmapBaker(resolution=1024)
        with mock.patch.object(
            LightmapBaker, "_lightmap_uv_bbox", return_value=(0.0, 0.0, 0.5, 1.0)
        ):
            half = baker._plan_bake_sizes({"a": (100, 100)})["a"]
        with mock.patch.object(LightmapBaker, "_lightmap_uv_bbox", return_value=None):
            full = baker._plan_bake_sizes({"a": (100, 100)})["a"]
        self.assertGreaterEqual(half, 2 * full - LightmapBaker._ATLAS_BAKE_QUANTUM)

    def test_bake_atlas_sizes_the_bake_from_the_plan_and_reuses_it(self):
        sg = self._sg("bakePlan")
        a = self._cube("bakePlanA", sg)
        b = self._cube("bakePlanB", sg)
        baker = LightmapBaker(resolution=128)
        plan = baker.atlas_plan([a, b])
        expected = baker.plan_sizes(plan)

        seen = {}

        def fake_bake(_self, objects, output_dir=None, size=None, **kwargs):
            seen["size"] = size
            seen["work_dir"] = output_dir
            seen["create_uvs"] = kwargs.get("create_uvs")
            return {
                o: os.path.join(self.tmp, f"{o.rsplit('|', 1)[-1]}.exr")
                for o in objects
            }

        def fake_pack(_self, mapping, output_dir=None, plan=None, **kwargs):
            seen["plan"] = plan
            return {o: (p, [1.0, 1.0, 0.0, 0.0]) for o, p in mapping.items()}

        with (
            mock.patch.object(LightmapBaker, "_bake_white_card", fake_bake),
            mock.patch.object(LightmapBaker, "pack_atlas", fake_pack),
            mock.patch.object(UvUtils, "create_lightmap_uvs", return_value={}),
        ):
            out = baker.bake_atlas([a, b], output_dir=self.tmp)

        self.assertEqual(set(out), {a, b})
        # Every object is sized from its own footprint (supersampled, never
        # above a full map) -- not blanket-rendered at the atlas size.
        self.assertEqual(set(seen["size"]), {a, b})
        for name, px in seen["size"].items():
            self.assertLessEqual(px, baker.resolution)
            self.assertGreaterEqual(px, max(expected[name]))
        # The UVs are built ahead of the bake (at each object's own size), so
        # the bake itself must not rebuild them.
        self.assertIs(seen["create_uvs"], False)
        # The pack receives the SAME plan the sizes came from.
        self.assertEqual(seen["plan"], plan)

    def test_a_held_destination_takes_an_adjacent_name_rather_than_the_bake(self):
        # The un-consolidated maps come out of a work dir the caller sweeps on
        # exit, so refusing the move would not "keep" the bake -- it would lose
        # it. Same policy as TextureBaker._place_output.
        baker = LightmapBaker(resolution=32)
        work = tempfile.mkdtemp(prefix="lm_held_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        src = os.path.join(work, "heldLM.exr")
        with open(src, "wb") as fh:
            fh.write(b"x")
        blocked = os.path.join(self.tmp, "heldLM.exr")
        with open(blocked, "wb") as fh:  # stands in for a locked prior map
            fh.write(b"old")

        real_remove = os.remove

        def refuse(path, *a, **k):
            if os.path.abspath(path) == os.path.abspath(blocked):
                raise OSError(13, "held by another process")
            return real_remove(path, *a, **k)

        with mock.patch("os.remove", refuse):
            out = baker._place_unpacked(
                {"obj": (src, list(LightmapBaker._IDENTITY_SCALE_OFFSET))}, self.tmp
            )

        path, _rect = out["obj"]
        self.assertEqual(os.path.dirname(path), self.tmp)
        self.assertNotEqual(os.path.abspath(path), os.path.abspath(blocked))
        self.assertTrue(os.path.exists(path))
        self.assertFalse(os.path.exists(src))  # the bake left the work dir

    def test_bake_atlas_places_unpacked_maps_before_the_workdir_is_swept(self):
        # A pack that cannot consolidate (no cv2, a failed group) hands back
        # per-object maps -- which live in a temp dir bake_atlas is about to
        # sweep. Losing a finished bake to a packing problem is not acceptable.
        sg = self._sg("drainPlan")
        a = self._cube("drainPlanA", sg)
        b = self._cube("drainPlanB", sg)
        baker = LightmapBaker(resolution=64)
        work = tempfile.mkdtemp(prefix="lm_work_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)

        def fake_bake(_self, objects, output_dir=None, **kwargs):
            out = {}
            for o in objects:
                path = os.path.join(work, f"{o.rsplit('|', 1)[-1]}.exr")
                with open(path, "wb") as fh:
                    fh.write(b"x")
                out[o] = path
            return out

        with (
            mock.patch.object(LightmapBaker, "_bake_white_card", fake_bake),
            mock.patch.object(
                LightmapBaker, "pack_atlas", side_effect=ImportError("no cv2")
            ),
            mock.patch.object(UvUtils, "create_lightmap_uvs", return_value={}),
        ):
            out = baker.bake_atlas([a, b], output_dir=self.tmp)

        self.assertEqual(set(out), {a, b})
        for path, rect in out.values():
            self.assertEqual(os.path.dirname(path), self.tmp)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(rect, [1.0, 1.0, 0.0, 0.0])


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

    def _store_with(self, name, data):
        """Point the preset store at a scratch user tier holding *data* as *name*.

        The built-in tier stays the shipped one; nothing is written where the
        user's own presets live.
        """
        user = tempfile.mkdtemp(prefix="lm_presets_")
        self.addCleanup(shutil.rmtree, user, ignore_errors=True)
        shipped = LightmapBaker.preset_store()
        store = ptk.PresetStore(
            "lightmap", builtin_dir=shipped.builtin_dir, user_dir=user
        )
        store.save(name, data)
        patcher = mock.patch.object(
            LightmapBaker, "preset_store", staticmethod(lambda: store)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return store

    #: What the panel's preset template saves (its _preset_values keys), plus
    #: a device a hand-edited file might carry.
    _PANEL_PRESET = {
        "packing": "atlas",
        "resolution": 512,
        "samples": 3,
        "gi_samples": 2,
        "gi_depth": 1,
        "adaptive": False,
        "include_environment": False,
        "denoise": False,
        "beside_textures": True,
        "device": "CPU",
    }

    def test_from_preset_builds_what_a_panel_saved_preset_says(self):
        """A preset saved from the panel is a headless bake recipe too: every
        switch it stores reaches the baker, and the panel-only ``packing``
        is ignored rather than rejected."""
        self._store_with("roomPass", self._PANEL_PRESET)
        baker = LightmapBaker.from_preset("roomPass")
        self.assertEqual((baker.resolution, baker.samples), (512, 3))
        self.assertEqual((baker.gi_depth, baker.gi_samples), (1, 2))
        self.assertIs(baker.adaptive, False)
        self.assertIs(baker.baker.adaptive, False)
        self.assertIs(baker.include_environment, False)
        self.assertIs(baker.denoise, False)
        self.assertIs(baker.beside_textures, True)
        # The device names one machine's hardware; a preset never picks it.
        self.assertIsNone(baker.device)

    def test_from_preset_overrides_still_win_over_saved_switches(self):
        self._store_with("roomPass", self._PANEL_PRESET)
        baker = LightmapBaker.from_preset(
            "roomPass", denoise=True, adaptive=True, device="GPU"
        )
        self.assertIs(baker.denoise, True)
        self.assertIs(baker.adaptive, True)
        self.assertEqual(baker.device, "GPU")

    def test_adaptive_reaches_an_injected_baker_only_when_asked(self):
        """Like device=: an injected baker keeps its own setting unless the
        argument names one -- then the argument and the property agree."""
        injected = lmb_module.TextureBaker(adaptive=False)
        self.assertIs(LightmapBaker(baker=injected).adaptive, False)
        self.assertIs(LightmapBaker(baker=injected, adaptive=True).adaptive, True)
        self.assertIs(injected.adaptive, True)


class TestExcludeSet(MayaTkTestCase):
    """The scene's Exclude set: no map of their own, still in the render.

    Every bake entry point resolves through ``LightmapBaker.bake_targets``, so
    what is pinned is that one definition -- a group excludes what is under
    it, faces exclude their mesh alone -- and that the per-object and atlas
    paths both honor it.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_exclude_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @staticmethod
    def _cube(name):
        return cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]

    def test_bake_targets_skips_members_and_everything_under_a_group(self):
        a = self._cube("tgtA")
        b = self._cube("tgtB")
        group = cmds.group(self._cube("tgtC"), name="tgtGroup")
        c = cmds.ls("tgtC", long=True)[0]

        LightmapExcludeSet.define([b, group])

        self.assertEqual(sorted(LightmapExcludeSet.meshes()), sorted([b, c]))
        self.assertEqual(LightmapBaker.bake_targets([a, b, c]), [a])
        LightmapExcludeSet.clear()
        self.assertEqual(LightmapBaker.bake_targets([a, b, c]), [a, b, c])

    def test_a_hidden_mesh_is_left_out_and_named(self):
        """Arnold renders no hidden object, so its bake writes no map -- which
        a bake reads as the render having been STOPPED: one hidden mesh in a
        Scene-scope bake ended the whole bake at that mesh."""
        shown = self._cube("tgtShown")
        by_flag = self._cube("tgtHidden")
        cmds.setAttr(f"{by_flag}.visibility", False)
        group = cmds.group(self._cube("tgtUnder"), name="tgtHiddenGroup")
        under = cmds.ls("tgtUnder", long=True)[0]
        cmds.setAttr(f"{group}.visibility", False)

        with self.assertLogs(LightmapBaker.logger, level="WARNING") as caught:
            targets = LightmapBaker.bake_targets([shown, by_flag, under])

        self.assertEqual(targets, [shown])
        self.assertTrue(
            any("tgtHidden" in m and "tgtUnder" in m for m in caught.output)
        )

    def test_faces_exclude_their_mesh_not_the_children_under_it(self):
        parent = self._cube("faceParent")
        child = cmds.ls(cmds.parent(self._cube("faceChild"), parent)[0], long=True)[0]
        LightmapExcludeSet.define([f"{parent}.f[0:2]"])
        self.assertEqual(LightmapBaker.bake_targets([parent, child]), [child])

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_bake_separated_gives_an_excluded_object_no_map(self):
        keep = self._cube("sepKeep")
        skip = self._cube("sepSkip")
        LightmapExcludeSet.define([skip])
        out = LightmapBaker(resolution=64, baker=_FakeBaker()).bake_separated(
            [keep, skip], output_dir=self.tmp
        )
        self.assertEqual(list(out), [keep])

    def test_atlas_plan_gives_an_excluded_object_no_cell(self):
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        keep = self._cube("planKeep")
        skip = self._cube("planSkip")
        for cube in (keep, skip):
            shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
            cmds.sets(shape, edit=True, forceElement=sg)
        LightmapExcludeSet.define([skip])
        plan = LightmapBaker(resolution=64).atlas_plan([keep, skip])
        self.assertEqual(
            [name for entries in plan.values() for name, _rect in entries], [keep]
        )


class TestBesideTextures(MayaTkTestCase):
    """``beside_textures``: each map in its texture set's folder.

    The folder comes from the same vote that names the map
    (``LightmapBaker._texture_set``), so ``<set>_Lightmap.exr`` sits beside
    ``<set>_BaseColor.png``; the bake's output_dir takes any object whose
    material has no texture folder on this machine.
    """

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="lm_beside_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.out = os.path.join(self.root, "out")

    def _textured(self, name, folder, set_name, sg=None, make_folder=True):
        """A cube wearing a lambert whose color is ``<folder>/<set>_BaseColor.png``.

        Only the FOLDER has to exist -- the file's is what is read -- so the
        texture itself is never written.
        """
        if make_folder:
            os.makedirs(folder, exist_ok=True)
        cube = cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        if sg is None:
            mat = cmds.shadingNode("lambert", asShader=True, name=f"{name}_mat")
            sg = cmds.sets(
                renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_SG"
            )
            cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
            fn = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
            path = os.path.join(folder, f"{set_name}_BaseColor.png").replace("\\", "/")
            cmds.setAttr(f"{fn}.fileTextureName", path, type="string")
            cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)
        cmds.sets(shape, edit=True, forceElement=sg)
        return cube, sg

    @staticmethod
    def _same(a, b):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
            os.path.normpath(b)
        )

    def test_the_texture_set_names_the_map_and_picks_its_folder(self):
        folder = os.path.join(self.root, "tex", "crate")
        cube, _sg = self._textured("setCrate", folder, "Crate_Wood_01")
        stem, found = LightmapBaker._texture_set(cube)
        self.assertEqual(stem, "Crate_Wood_01")
        self.assertTrue(self._same(found, folder), found)
        # The stem resolver the bake names files with reads the same answer.
        self.assertEqual(LightmapBaker._texture_set_stem(cube), "Crate_Wood_01")

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_each_map_lands_beside_its_textures_the_rest_in_output_dir(self):
        crate_dir = os.path.join(self.root, "tex", "crate")
        floor_dir = os.path.join(self.root, "tex", "floor")
        crate, _ = self._textured("bsCrate", crate_dir, "Crate_Wood_01")
        floor, _ = self._textured("bsFloor", floor_dir, "Floor_Tile_02")
        plain = cmds.ls(cmds.polyCube(name="bsPlain")[0], long=True)[0]

        out = LightmapBaker(
            resolution=64, baker=_FakeBaker(), beside_textures=True
        ).bake_separated([crate, floor, plain], output_dir=self.out)

        self.assertEqual(set(out), {crate, floor, plain})
        self.assertTrue(self._same(os.path.dirname(out[crate]), crate_dir))
        self.assertTrue(self._same(os.path.dirname(out[floor]), floor_dir))
        self.assertTrue(self._same(os.path.dirname(out[plain]), self.out))
        for path in out.values():
            self.assertTrue(os.path.isfile(path), path)
        # Placed, not baked there: each folder holds its map and nothing else.
        self.assertEqual(os.listdir(crate_dir), [os.path.basename(out[crate])])
        self.assertEqual(os.listdir(self.out), [os.path.basename(out[plain])])

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_a_texture_folder_missing_here_falls_back_and_is_never_created(self):
        gone = os.path.join(self.root, "moved", "library")
        cube, _ = self._textured("bsGone", gone, "Gone_Set", make_folder=False)
        out = LightmapBaker(
            resolution=64, baker=_FakeBaker(), beside_textures=True
        ).bake_separated([cube], output_dir=self.out)
        self.assertTrue(self._same(os.path.dirname(out[cube]), self.out))
        self.assertFalse(os.path.exists(gone))

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_an_atlas_lands_beside_its_material_groups_textures(self):
        folder = os.path.join(self.root, "tex", "shelf")
        a, sg = self._textured("bsShelfA", folder, "Shelf_Metal_01")
        b, _ = self._textured("bsShelfB", folder, "Shelf_Metal_01", sg=sg)

        packed = LightmapBaker(
            resolution=64, baker=_FakeBaker(), beside_textures=True
        ).bake_atlas([a, b], output_dir=self.out)

        paths = {path for path, _rect in packed.values()}
        self.assertEqual(len(paths), 1)  # one shared atlas...
        atlas = paths.pop()
        self.assertTrue(self._same(os.path.dirname(atlas), folder))  # ...there
        self.assertEqual(os.path.basename(atlas), "Shelf_Metal_01_Lightmap.exr")
        self.assertTrue(os.path.isfile(atlas))
        self.assertFalse(os.path.exists(self.out))  # nothing fell back


class TestNeverWritesOverAnotherObjectsMap(MayaTkTestCase):
    """A bake never lands on a map an object outside it still samples.

    Maps are named after their texture set, and a bake only kept names unique
    within itself -- so re-baking one crate of ten that share a texture set,
    or baking a room around an excluded hero prop that shares one, wrote over
    another object's file while that object's marker went on naming it: it
    shipped this bake's lighting. Those names are reserved now, and the new
    map takes the next free one.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_claimed_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @staticmethod
    def _cube(name):
        return cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]

    def _baked(self, obj, basename):
        """Commit *obj* as already baked into *basename* (a real file)."""
        path = os.path.join(self.tmp, basename)
        with open(path, "wb") as fh:
            fh.write(b"theirs")
        LightmapBaker().commit_lightmap({obj: path})
        return path

    def test_claims_name_the_readers_of_every_map(self):
        a, b = self._cube("clA"), self._cube("clB")
        self._baked(a, "Crate_Lightmap.exr")
        self._baked(b, "Crate_Lightmap_1.exr")
        # Each name is its own reader's: B may land on its own old name (its
        # marker is about to be rewritten), never on A's.
        self.assertEqual(
            LightmapRecords.claims(),
            {
                "crate_lightmap.exr": frozenset({a}),
                "crate_lightmap_1.exr": frozenset({b}),
            },
        )

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_a_partial_rebake_hands_the_bake_every_claim(self):
        a, b = self._cube("rbA"), self._cube("rbB")
        self._baked(a, "Crate_Lightmap.exr")
        fake = _FakeBaker()
        LightmapBaker(resolution=64, baker=fake).bake_separated(
            [b], output_dir=self.tmp
        )
        self.assertEqual(fake.called_claims, {"crate_lightmap.exr": frozenset({a})})

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_an_excluded_objects_map_is_claimed_from_the_room_bake(self):
        hero, room = self._cube("exHero"), self._cube("exRoom")
        self._baked(hero, "Crate_Lightmap.exr")
        LightmapExcludeSet.define([hero])
        fake = _FakeBaker()
        out = LightmapBaker(resolution=64, baker=fake).bake_separated(
            [hero, room], output_dir=self.tmp
        )
        self.assertEqual(list(out), [room])
        self.assertEqual(
            fake.called_claims, {"crate_lightmap.exr": frozenset({hero})}
        )

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_an_object_whose_rebake_fails_keeps_its_map(self):
        """The pre-bake revert is gone, and this is what makes that safe.

        Two crates share a texture set: A reads ``Crate_Lightmap.exr``, B
        ``Crate_Lightmap_1.exr``. Both are re-baked and A's render fails. B
        must not take A's name -- nothing reverted A, so its marker still
        names that file -- and A keeps the map it had instead of coming out of
        the bake with no lightmap at all, as it did when the panel reverted
        the scope first."""
        from mayatk.mat_utils.texture_baker import TextureBaker

        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        mat = cmds.shadingNode("lambert", asShader=True, name="crateMat")
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        fn = cmds.shadingNode("file", asTexture=True, name="crateFile")
        cmds.setAttr(
            f"{fn}.fileTextureName", "C:/tex/Crate_BaseColor.png", type="string"
        )
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)
        a, b = self._cube("keepA"), self._cube("keepB")
        for cube in (a, b):
            shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
            cmds.sets(shape, edit=True, forceElement=sg)
        theirs = self._baked(a, "Crate_Lightmap.exr")
        self._baked(b, "Crate_Lightmap_1.exr")

        def render(obj, output_dir, shader=None, uv_set=None, resolution=None):
            if obj == a:
                raise RuntimeError("render failed")  # A's bake fails outright
            path = os.path.join(output_dir, "rtt_raw.exr")
            _write_half_covered_exr(path)
            return path

        baker = LightmapBaker(resolution=64)
        with (
            mock.patch.object(TextureBaker, "ensure_arnold", return_value=True),
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(
                baker.baker, "_pinned_render_settings",
                return_value=contextlib.nullcontext(),
            ),
            mock.patch.object(baker.baker, "_bake_with_arnold", side_effect=render),
        ):
            result = baker.bake(
                [b, a], packing="per_object", output_dir=self.tmp, batch=False
            )

        self.assertEqual(list(result.maps), [b])
        self.assertEqual(result.unbaked, [a])
        self.assertEqual(os.path.basename(result.maps[b]), "Crate_Lightmap_1.exr")
        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), b"theirs", "B's bake landed on A's map")
        self.assertEqual(LightmapRecords._marker_info(a)["map"], "Crate_Lightmap.exr")

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_a_partial_group_rebake_gets_an_atlas_of_its_own(self):
        """The member left out still samples the group's atlas, so the packed
        subset takes the next name and that atlas is left as it was."""
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        mat = cmds.shadingNode("lambert", asShader=True, name="sharedMat")
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        fn = cmds.shadingNode("file", asTexture=True, name="sharedFile")
        cmds.setAttr(
            f"{fn}.fileTextureName", "C:/tex/Shared_BaseColor.png", type="string"
        )
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)
        a, b = self._cube("grpA"), self._cube("grpB")
        for cube in (a, b):
            shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
            cmds.sets(shape, edit=True, forceElement=sg)
        theirs = self._baked(a, "Shared_Lightmap.exr")
        work = tempfile.mkdtemp(prefix="lm_claimed_tile_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        tile = os.path.join(work, "grpB_tile.exr")
        _write_half_covered_exr(tile)

        packed = LightmapBaker(resolution=64).pack_atlas({b: tile}, output_dir=self.tmp)

        self.assertEqual(os.path.basename(packed[b][0]), "Shared_Lightmap_1.exr")
        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), b"theirs")

    @unittest.skipUnless(os.name == "nt", "needs Windows' delete-while-open lock")
    def test_a_locked_map_never_falls_back_onto_another_objects_file(self):
        """Placing over a map held open (a viewer, a sync client) falls back to
        an adjacent name -- which used to be DELETED first when it existed,
        destroying whichever object's map lived there."""
        own = os.path.join(self.tmp, "Floor_Lightmap.exr")
        theirs = os.path.join(self.tmp, "Floor_Lightmap_1.exr")
        for path, data in ((own, b"old"), (theirs, b"theirs")):
            with open(path, "wb") as fh:
                fh.write(data)
        work = tempfile.mkdtemp(prefix="lm_claimed_work_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        source = os.path.join(work, "Floor_Lightmap.exr")
        with open(source, "wb") as fh:
            fh.write(b"new")

        with open(own, "rb"):  # held: it cannot be replaced
            placed = LightmapBaker()._place_unpacked({"|x": (source, None)}, self.tmp)

        self.assertEqual(os.path.basename(placed["|x"][0]), "Floor_Lightmap_2.exr")
        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), b"theirs")

    def _work_map(self, basename, data=b"new"):
        work = tempfile.mkdtemp(prefix="lm_claimed_work_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        source = os.path.join(work, basename)
        with open(source, "wb") as fh:
            fh.write(data)
        return source

    def test_a_map_that_cannot_be_placed_keeps_the_old_one(self):
        """The old map was DELETED before the new one moved in; with every
        move failing (a full disk, an unwritable folder) the object lost its
        map, and its marker was pointed at the work-dir copy the caller was
        about to sweep. Now nothing is removed first, and the object is left
        out -- reported unbaked, keeping the map it had."""
        obj = self._cube("clFail")
        own = self._baked(obj, "Fail_Lightmap.exr")
        source = self._work_map("Fail_Lightmap.exr")
        claims = LightmapRecords.claims()

        with mock.patch(
            "mayatk.light_utils.lightmap_baker.lightmap_baker.shutil.move",
            side_effect=OSError(28, "No space left on device"),
        ):
            placed = LightmapBaker()._place_unpacked(
                {obj: (source, None)}, self.tmp, claims=claims
            )

        self.assertNotIn(obj, placed)
        with open(own, "rb") as fh:
            self.assertEqual(fh.read(), b"theirs", "the object's map was deleted")
        self.assertEqual(
            sorted(os.listdir(self.tmp)), ["Fail_Lightmap.exr"], "a staged file leaked"
        )

    def test_a_file_no_marker_claims_is_someone_elses(self):
        """Beside Material Textures puts a map in its texture set's folder, and
        a library folder other scenes share keeps THEIR maps under the same
        names. A file no marker in this scene claims was replaced as though it
        were this bake's own -- the other scene then shipped this lighting."""
        other_scene = os.path.join(self.tmp, "Crate_Lightmap.exr")
        with open(other_scene, "wb") as fh:
            fh.write(b"roomA")
        source = self._work_map("Crate_Lightmap.exr")

        placed = LightmapBaker()._place_unpacked(
            {"|roomB|crate": (source, None)}, self.tmp, claims={}
        )

        self.assertEqual(
            os.path.basename(placed["|roomB|crate"][0]), "Crate_Lightmap_1.exr"
        )
        with open(other_scene, "rb") as fh:
            self.assertEqual(fh.read(), b"roomA")

    def test_a_rebake_still_replaces_its_own_map(self):
        obj = self._cube("clOwn")
        own = self._baked(obj, "Own_Lightmap.exr")
        source = self._work_map("Own_Lightmap.exr", b"rebaked")

        placed = LightmapBaker()._place_unpacked(
            {obj: (source, None)}, self.tmp, claims=LightmapRecords.claims()
        )

        self.assertEqual(os.path.abspath(placed[obj][0]), os.path.abspath(own))
        with open(own, "rb") as fh:
            self.assertEqual(fh.read(), b"rebaked")


class _NoArnoldBaker(_FakeBaker):
    """A bake backend that cannot load Arnold (mtoa missing)."""

    @staticmethod
    def ensure_arnold():
        return False


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestBakeWorkflow(MayaTkTestCase):
    """:meth:`LightmapBaker.bake` -- the whole workflow, the same for a script
    as for the panel.

    Every check here used to live on the panel's Bake button alone, so a
    scripted bake -- the docs' own recipe -- skipped all of them: the
    authored-light upgrade, the all-lights-off refusal, and the unlit verdict,
    each added after a production bake that paid its full cost for nothing.
    A fake backend stands in for Arnold (it writes a small EXR per object).
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_workflow_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _cube(self, name="wfCube"):
        return cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]

    @staticmethod
    def _make_light(intensity=110):
        """An area light, as ``(shape, transform)`` full paths."""
        node = cmds.shadingNode("areaLight", asLight=True)
        shape = (cmds.ls(node, dag=True, shapes=True, long=True) or [node])[0]
        transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [node])[0]
        cmds.setAttr(f"{shape}.intensity", intensity)
        return shape, transform

    def _exr(self, name, value):
        cv2, np = _cv2()
        path = os.path.join(self.tmp, name)
        cv2.imwrite(path, np.full((8, 8, 3), value, np.float32))
        return path

    # -- the whole run ----------------------------------------------------

    def test_a_bake_records_its_maps_and_rects(self):
        a, b = self._cube("wfA"), self._cube("wfB")
        fake = _FakeBaker()
        result = LightmapBaker(resolution=64, baker=fake).bake(
            [a, b], packing="per_object", output_dir=self.tmp
        )
        self.assertEqual(set(result.maps), {a, b})
        self.assertEqual(result.rects[a], [1.0, 1.0, 0.0, 0.0])
        self.assertIsNone(result.refused)
        # Recorded: the markers name the maps the result names.
        for obj in (a, b):
            self.assertEqual(
                LightmapRecords._marker_info(obj)["map"],
                os.path.basename(result.maps[obj]),
            )

    def test_packing_must_be_one_the_workflow_knows(self):
        with self.assertRaises(ValueError):
            LightmapBaker(baker=_FakeBaker()).bake([self._cube()], packing="udim")

    def test_a_bake_names_what_it_left_out(self):
        kept, left_out = self._cube("wfKept"), self._cube("wfLeftOut")
        LightmapExcludeSet.define([left_out])
        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake(
            [kept, left_out], packing="per_object", output_dir=self.tmp
        )
        self.assertEqual(list(result.maps), [kept])
        self.assertEqual(result.excluded, [left_out])

        LightmapExcludeSet.define([kept, left_out])
        refused = LightmapBaker(baker=_FakeBaker()).bake([kept, left_out])
        self.assertFalse(refused)
        self.assertIn("Exclude set", refused.refused)

    # -- preflight: Arnold, the lights ----------------------------------------

    def test_without_arnold_nothing_in_the_scene_changes(self):
        """Refused before anything touches the scene: an earlier map stays."""
        cube = self._cube("wfNoArnold")
        LightmapRecords.commit({cube: self._exr("earlier.exr", 1.0)})
        self._make_light()
        backend = _NoArnoldBaker()
        result = LightmapBaker(baker=backend).bake([cube], output_dir=self.tmp)
        self.assertIn("Arnold", result.refused)
        self.assertNotIn("LDR", result.refused, "there is no LDR fallback")
        self.assertIsNone(backend.called_uv_set, "never baked")
        self.assertEqual(LightmapRecords._marker_info(cube)["map"], "earlier.exr")

    def test_arnold_is_loaded_on_demand_not_just_probed(self):
        """Preflight asks ``ensure_arnold`` (load if installed), never the
        non-loading ``arnold_available``: mtoa is often not auto-loaded, and
        asking the probe turned a fresh session into a refusal."""
        from mayatk.mat_utils.texture_baker import TextureBaker

        with (
            mock.patch.object(TextureBaker, "arnold_available", return_value=False),
            mock.patch.object(TextureBaker, "ensure_arnold", return_value=True),
        ):
            self.assertIsNone(LightmapBaker().preflight())

    def test_the_tools_own_lights_are_upgraded_before_anything_renders(self):
        """Lights authored before per-area emission reopen NORMALIZED and bake
        ~100x dim; the upgrade runs ahead of the render."""
        fake = _FakeBaker()
        order = []
        with mock.patch.object(
            lmb_module.LightUtils,
            "upgrade_authored_lights",
            side_effect=lambda: order.append(fake.called_uv_set) or [],
        ):
            LightmapBaker(resolution=64, baker=fake).bake(
                [self._cube()], packing="per_object", output_dir=self.tmp
            )
        self.assertEqual(order, [None], "upgraded before the bake ran")

    def test_every_light_off_is_refused_before_the_bake(self):
        """Reported from ROOM_ENV 2026-08-12: four correctly configured area
        lights whose TRANSFORMS all carried ``.v no``. Arnold renders no hidden
        light, so the bake spent its full cost on an atlas 147x dimmer than
        the same room's previous one."""
        _shape, transform = self._make_light(intensity=110)
        cmds.setAttr(f"{transform}.visibility", False)
        fake = _FakeBaker()
        result = LightmapBaker(baker=fake).bake([self._cube()], output_dir=self.tmp)
        self.assertIn("hidden", result.refused.lower())
        self.assertIsNone(fake.called_uv_set, "never spent a bake")

    def test_a_contributing_light_bakes(self):
        self._make_light(intensity=110)
        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake(
            [self._cube()], packing="per_object", output_dir=self.tmp
        )
        self.assertIsNone(result.refused)
        self.assertTrue(result)

    def test_a_visible_dome_is_no_light_with_the_environment_off(self):
        """Include Environment off mutes every dome for the render, so hidden
        fixtures beside a visible HDRI dome were not refused and the bake ran
        at full cost, unlit. With the environment on, the dome lights it."""
        dome, fixture = "|dome|domeShape", "|lamp|lampShape"
        lights = lmb_module.LightUtils
        with (
            mock.patch.object(lights, "all_lights", return_value=[dome, fixture]),
            mock.patch.object(lights, "contributing_lights", return_value=[dome]),
            mock.patch.object(lights, "environment_lights", return_value=[dome]),
            mock.patch.object(lights, "upgrade_authored_lights", return_value=[]),
        ):
            off = LightmapBaker(include_environment=False, baker=_FakeBaker())
            on = LightmapBaker(include_environment=True, baker=_FakeBaker())
            self.assertIn("hidden", (off.preflight() or "").lower())
            self.assertIsNone(on.preflight())

    def test_no_lights_at_all_is_not_refused(self):
        """Emissive materials light an Arnold bake with an empty light list,
        so only the unambiguous case -- lights exist, none contribute -- is."""
        for light in cmds.ls(lights=True, long=True) or []:
            parent = cmds.listRelatives(light, parent=True, fullPath=True)
            cmds.delete(parent[0] if parent else light)
        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake(
            [self._cube()], packing="per_object", output_dir=self.tmp
        )
        self.assertIsNone(result.refused)

    @unittest.skipUnless(_arnold_loadable(), "mtoa unavailable")
    def test_an_arnold_lit_scene_holding_a_dead_native_light_bakes(self):
        """An ``aiAreaLight`` inherits THlocatorShape, not ``light``, so a
        native-only query would read this room as "lights exist, none
        contribute" and refuse the scene the bake is FOR."""
        _shape, transform = self._make_light(intensity=110)
        cmds.setAttr(f"{transform}.visibility", False)  # the legacy leftover
        cmds.createNode("aiAreaLight")  # what actually lights the room
        self.assertIsNone(LightmapBaker(baker=_FakeBaker()).preflight())

    # -- intensity --------------------------------------------------------------

    def test_intensity_is_baked_into_the_new_maps_once(self):
        """``bake(intensity=)`` scales the maps it just wrote -- once. The
        deprecated ``commit_lightmap(intensity=)`` scaled whatever it was
        handed, so recording a map twice scaled it twice."""
        cv2, np = _cv2()

        class _Flat(_FakeBaker):
            def bake(self, objects, output_dir=None, **kwargs):
                out = {}
                for obj in objects:
                    path = os.path.join(output_dir, f"{obj.rsplit('|', 1)[-1]}.exr")
                    cv2.imwrite(path, np.full((4, 4, 3), 0.25, np.float32))
                    out[cmds.ls(obj, long=True)[0]] = path
                return out

        cube = self._cube("wfIntensity")
        result = LightmapBaker(resolution=64, baker=_Flat()).bake(
            [cube], packing="per_object", output_dir=self.tmp, intensity=2.0, dilate=False
        )
        path = result.maps[cube]
        self.assertAlmostEqual(float(_read(path).mean()), 0.5, places=3)
        self.assertEqual(LightmapRecords._marker_info(cube)["intensity"], 2.0)
        LightmapRecords.commit(result.maps, intensity=2.0)  # recorded again...
        self.assertAlmostEqual(float(_read(path).mean()), 0.5, places=3)  # ...once

    def test_an_atlas_bake_records_one_shared_map_with_each_objects_cell(self):
        """The DEFAULT packing, driven end to end -- no other test takes
        ``bake(packing="atlas")`` to the markers. Two objects on one material
        share ONE file, each marker binds its own (non-identity) cell of it,
        and ``intensity`` scales that shared file once, not once per object
        that reads it."""
        cv2, np = _cv2()

        class _Flat(_FakeBaker):
            def bake(self, objects, output_dir=None, **kwargs):
                out = {}
                for obj in objects:
                    path = os.path.join(output_dir, f"{obj.rsplit('|', 1)[-1]}.exr")
                    cv2.imwrite(path, np.full((8, 8, 3), 0.25, np.float32))
                    out[cmds.ls(obj, long=True)[0]] = path
                return out

        mat = cmds.shadingNode("lambert", asShader=True, name="wfAtlas_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="wfAtlas_SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        a, b = self._cube("wfAtlasA"), self._cube("wfAtlasB")
        cmds.sets([a, b], edit=True, forceElement=sg)

        result = LightmapBaker(resolution=64, baker=_Flat()).bake(
            [a, b], packing="atlas", output_dir=self.tmp, intensity=2.0
        )

        self.assertIsNone(result.refused)
        self.assertEqual(set(result.maps), {a, b})
        self.assertEqual(result.maps[a], result.maps[b], "one shared atlas")
        self.assertNotEqual(result.rects[a], result.rects[b])
        for obj in (a, b):
            self.assertNotEqual(result.rects[obj], [1.0, 1.0, 0.0, 0.0])
            info = LightmapRecords._marker_info(obj)
            self.assertEqual(info["map"], os.path.basename(result.maps[obj]))
            self.assertEqual(info["scaleOffset"], result.rects[obj])
        # 0.25 tiles at intensity 2: 0.5 where lit. Scaled once per object
        # that reads the file, the shared atlas would read 1.0.
        lit = float(_read(result.maps[a])[..., :3].max())
        self.assertAlmostEqual(lit, 0.5, places=2)

    # -- the verdict -------------------------------------------------------------

    def test_the_verdict_catches_an_unlit_bake_only(self):
        """A black bake is FAITHFUL rendering of an unlit scene, so nothing
        upstream errors -- the verdict is the last place that can tell the
        artist before the map ships to a black preview (measured: a room
        whose generated lights sat at intensity 1 baked 0.008 and shipped)."""
        baker = LightmapBaker()
        black, lit = self._exr("black.exr", 0.001), self._exr("lit.exr", 1.0)
        self.assertIn("UNLIT", baker.bake_verdict([black]))
        self.assertIsNone(baker.bake_verdict([lit]))
        # One healthy map among dark ones clears it (the scene HAS light).
        self.assertIsNone(baker.bake_verdict([black, lit]))
        # Unreadable/missing maps must never break a finished bake.
        self.assertIsNone(baker.bake_verdict([os.path.join(self.tmp, "missing.exr")]))

    def test_the_verdict_catches_a_collapsed_not_black_bake(self):
        """Regression, measured on ROOM_ENV 2026-08-12: the same room baked a
        4.14-mean atlas at 13:07 and a 0.0283-mean one at 13:22 -- 147x
        dimmer. The line separates the measured UNLIT population (0.008,
        0.0283) from the measured LIT one (1.0+, 4.14)."""
        baker = LightmapBaker()
        self.assertIn("UNLIT", baker.bake_verdict([self._exr("dim.exr", 0.0283)]))
        self.assertIsNone(baker.bake_verdict([self._exr("good.exr", 4.14)]))
        self.assertIn("UNLIT", baker.bake_verdict([self._exr("norm.exr", 0.008)]))
        self.assertIsNone(baker.bake_verdict([self._exr("ok.exr", 1.0)]))

    def test_the_verdict_catches_a_blown_out_bake(self):
        """blendertk's twin check, now Maya's too: a light that reached the bake
        at a broken unit saturates the maps and reports success."""
        baker = LightmapBaker()
        self.assertIn("BLOWN", baker.bake_verdict([self._exr("hot.exr", 100.0)]))
        peak = baker.peak_level([self._exr("a.exr", 1.0), self._exr("b.exr", 3.0)])
        self.assertTrue(peak[0].endswith("b.exr"))
        self.assertAlmostEqual(peak[1], 3.0, places=3)

    # -- the deprecated spellings still answer -----------------------------------

    def test_the_baker_s_old_record_spellings_warn_and_delegate(self):
        cube = self._cube("wfAlias")
        LightmapRecords.commit({cube: self._exr("alias.exr", 1.0)})
        baker = LightmapBaker()
        with self.assertWarns(DeprecationWarning):
            deps = baker.lightmap_dependencies()
        self.assertEqual([d["map"] for d in deps], ["alias.exr"])
        with self.assertWarns(DeprecationWarning):
            self.assertEqual(LightmapBaker.search_dirs(), LightmapRecords.search_dirs())
        # The workflow verbs are delegates, not deprecations.
        self.assertEqual(baker.baked_objects(), [cube])
        self.assertEqual(baker.revert(), [cube])


class TestRevertIsOneUndo(MayaTkTestCase):
    """Revert to Source promises "one Undo restores the wiring" -- so it is one
    chunk, and ``baked_objects`` counts what it will clear beforehand."""

    def test_baked_objects_counts_what_a_revert_would_clear(self):
        a = cmds.ls(cmds.polyCube(name="undoA")[0], long=True)[0]
        b = cmds.ls(cmds.polyCube(name="undoB")[0], long=True)[0]
        baker = LightmapBaker()
        baker.commit_lightmap({a: "C:/lm/undoA_Lightmap.exr"})
        self.assertEqual(baker.baked_objects([a, b]), [a])
        self.assertEqual(baker.baked_objects(), [a])

    def test_one_undo_restores_every_marker_a_revert_cleared(self):
        was = cmds.undoInfo(query=True, state=True)
        cmds.undoInfo(state=True, infinity=True)
        self.addCleanup(cmds.undoInfo, state=was)
        a = cmds.ls(cmds.polyCube(name="undoC")[0], long=True)[0]
        b = cmds.ls(cmds.polyCube(name="undoD")[0], long=True)[0]
        baker = LightmapBaker()
        baker.commit_lightmap(
            {a: "C:/lm/undoC_Lightmap.exr", b: "C:/lm/undoD_Lightmap.exr"}
        )

        baker.revert()
        self.assertEqual(baker.baked_objects(), [])
        cmds.undo()

        self.assertEqual(sorted(baker.baked_objects()), sorted([a, b]))


# ---------------------------------------------------------------------------
# UI slots: dispatch logic only (the panel itself can't load headlessly under
# the offscreen QPA). A fake workflow stands in for LightmapBaker so the tests
# verify each button routes to the right workflow method with the dials' values
# -- no Arnold, no Qt.
# ---------------------------------------------------------------------------


class _Toggle:
    """Enough of uitk's ToggleOption for the panel's switches."""

    def __init__(self, on=False):
        self.is_on = bool(on)

    def set_on(self, value, *, emit=True):
        self.is_on = bool(value)


class _SwitchBox:
    """Enough of uitk's OptionBoxManager for a field's switch.

    The panel's switches ride the option box of the field they qualify
    (``LightmapBakerSlots._TOGGLES``) and are read back by type, so a stub
    field answers ``find_option`` with the one toggle it carries -- or with
    ``None``, which is the before-the-panel-is-wired case the readers fall
    back to their shipped default for.
    """

    def __init__(self, on=None):
        self.toggle = None if on is None else _Toggle(on)
        self.wired = None

    def find_option(self, _option_type):
        return self.toggle

    def set_toggle(self, *, initial=True, **kwargs):
        """Mirror ``OptionBoxManager.set_toggle``: replace the field's toggle,
        which starts at *initial*. The arguments are kept so a test can read
        back what the panel asked for."""
        self.toggle = _Toggle(initial)
        self.wired = dict(kwargs, initial=initial)
        return self


class _Spin:
    def __init__(self, v, switch=None):
        self._v = v
        self.option_box = _SwitchBox(switch)

    def value(self):
        return self._v

    def setValue(self, v):
        self._v = v

    def blockSignals(self, _b):
        pass


class _ItemCombo:
    """Enough of QComboBox for an ``_init`` that populates by NAME.

    ``addItems`` + ``setCurrentIndex``, so what a populating slot selects is
    observable without Qt -- and readable back through the panel's own reader
    (``_packing`` / ``_scope``), which is the half that would drift.
    """

    def __init__(self):
        self.items = []
        self._index = -1
        self.option_box = _SwitchBox()

    def clear(self):
        self.items = []
        self._index = -1

    def addItems(self, items):
        self.items.extend(items)
        if self._index < 0 and self.items:
            self._index = 0

    def findText(self, text):
        return self.items.index(text) if text in self.items else -1

    def setCurrentIndex(self, index):
        self._index = index

    def currentIndex(self):
        return self._index

    def currentText(self):
        return self.items[self._index] if 0 <= self._index < len(self.items) else ""


class _PackingCombo:
    """Packing combobox stub. Per-Object is the FIXTURE's default (it keeps the
    b000 tests on the one-map-each branch unless they ask for the other); the
    PANEL's default is Atlas by Material, which ``cmb002_init`` selects and
    ``TestQualityAndScopeSwitches`` pins."""

    _LABELS = LightmapBakerSlots._PACKING_LABELS

    def __init__(self, text="Per-Object (one map each)"):
        self._text = text

    def currentText(self):
        return self._text

    def setCurrentIndex(self, index):
        self._text = self._LABELS[index]


class _ScopeCombo:
    """Scope combobox stub: defaults to Selected, matching cmb_scope_init's
    setCurrentIndex(0) (the prior selection-only behavior). Carries the
    Include Environment switch, as the real one does."""

    def __init__(self, text="Selected", environment=True):
        self._text = text
        self.option_box = _SwitchBox(environment)

    def currentText(self):
        return self._text


class _ResolutionCombo:
    """Resolution combobox stub: mirrors cmb_resolution_init's item-data model
    (currentData() is the actual pixel size, not the display text) so
    _resolution()/_set_resolution() round-trip without a real Qt widget.
    """

    _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

    def __init__(self, resolution=1024, denoise=True):
        self._data = resolution  # tolerate an out-of-list placeholder value
        self.option_box = _SwitchBox(denoise)  # the Denoise switch rides here

    def currentData(self):
        return self._data

    def setCurrentIndex(self, index):
        self._data = self._RESOLUTIONS[index]


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

    class _OptionBox(_SwitchBox):
        def __init__(self, widget, switch=None):
            super().__init__(switch)
            self.menu = _LineEdit._Menu()
            self._widget = widget

        def resolve_affix(self, text=None, *, default="prefix"):
            if text is None:
                text = self._widget.text()
            return ptk.StrUtils.split_affix(text, mode="auto", default=default)

    def __init__(self, text="_Lightmap", placeholder="_Lightmap", switch=None):
        self._text = text
        self._placeholder = placeholder
        self.option_box = _LineEdit._OptionBox(self, switch)

    def setPlaceholderText(self, text):
        self._placeholder = text

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def placeholderText(self):
        return self._placeholder


class _DeviceCombo:
    """Enough of the Device ComboBox for _device() (the value is item data)."""

    def __init__(self, value="AUTO"):
        self._value = value

    def currentData(self):
        return self._value


class _Label:
    """Enough of QLabel for the Exclude row's live count."""

    def __init__(self, text="Exclude:"):
        self._text = text

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text


class _Sb:
    """Enough of the switchboard for a confirmation: records each question and
    answers it with *answer*, a Qt standard-button name -- ``confirm`` is True
    when that is its *yes* button, as the real one is."""

    def __init__(self, answer="Ok"):
        self.answer = answer
        self.asked = []

    def confirm(self, question, yes="Yes", no="No"):
        self.asked.append((question, (yes, no)))
        return self.answer == yes


class _SlotUi:
    def __init__(
        self,
        res=1024,
        samples=4,
        affix="_Lightmap",
        packing="Per-Object (one map each)",
        scope="Selected",
        output_dir="",
        device="AUTO",
        environment=True,
        denoise=True,
        gi_samples=4,
        bounces=2,
        adaptive=True,
        beside=False,
    ):
        self.footer = _Footer()
        self.cmb_device = _DeviceCombo(device)
        # Each switch rides the option box of the field it qualifies.
        self.cmb_resolution = _ResolutionCombo(res, denoise=denoise)
        self.spn_samples = _Spin(samples, switch=adaptive)
        self.spn_gi_samples = _Spin(gi_samples)
        self.spn_bounces = _Spin(bounces)
        self.txt000 = _LineEdit(affix)
        # Optional output-dir field: empty means "the project's sourceimages".
        self.txt_output_dir = _LineEdit(
            output_dir, placeholder="sourceimages", switch=beside
        )
        self.cmb002 = _PackingCombo(packing)
        self.cmb_scope = _ScopeCombo(scope, environment=environment)
        self.lbl_exclude = _Label()


class _FakeWorkflow:
    """Records each call; stands in for LightmapBaker (no Arnold/UV work).

    ``bake`` keeps the engine's contract rather than just recording: the REAL
    exclusion filter, then a ``LightmapBakeResult``. Class attributes set
    what the next bake returns -- a refusal, an empty bake, a verdict -- so a
    panel test states the outcome it reports on.
    """

    instances: list = []

    #: What baked_objects() reports (None = every node it is given).
    baked = None
    #: The next bake's ``refused`` sentence (None: it bakes).
    refusal = None
    #: Whether the next bake writes nothing at all.
    empty = False
    #: The next bake's ``verdict`` sentence.
    verdict = None

    def __init__(
        self,
        resolution=None,
        samples=None,
        device=None,
        include_environment=True,
        denoise=True,
        gi_depth=None,
        gi_samples=None,
        adaptive=None,
        beside_textures=False,
        **kwargs,
    ):
        self.resolution = resolution
        self.samples = samples
        self.device = device
        self.include_environment = include_environment
        self.denoise = denoise
        self.gi_depth = gi_depth
        self.gi_samples = gi_samples
        self.adaptive = adaptive
        self.beside_textures = beside_textures
        self.calls: list = []
        self.last_result = None
        _FakeWorkflow.instances.append(self)

    # The REAL exclusion filter: it only reads the scene's set, and the panel's
    # promise -- an excluded object is neither touched nor baked -- is only
    # worth pinning against the definition the workflow itself uses.
    bake_targets = staticmethod(LightmapBaker.bake_targets)

    def baked_objects(self, objects=None):
        self.calls.append(("baked_objects", tuple(objects) if objects else None))
        if self.baked is not None:
            return list(self.baked)
        return list(objects) if objects else ["|marked"]

    def revert(self, objects=None):
        self.calls.append(("revert", tuple(objects) if objects else None))
        return list(objects) if objects else []

    def bake(
        self,
        objects,
        packing="atlas",
        output_dir=None,
        prefix="",
        suffix="_Lightmap",
        on_progress=None,
        **kwargs,
    ):
        targets = self.bake_targets(objects)
        result = lmb_module.LightmapBakeResult(
            excluded=[o for o in objects if o not in targets]
        )
        self.calls.append(("bake", tuple(targets), packing))
        self.bake_output_dir = output_dir
        self.bake_prefix = prefix
        self.bake_suffix = suffix
        self.last_result = result
        if not targets:
            result.refused = "Nothing to bake: all objects are in the Exclude set."
            return result
        if self.refusal:
            result.refused = self.refusal
            return result
        if on_progress:  # exercise the per-object progress wiring
            for i, o in enumerate(targets):
                on_progress(i, len(targets), o.rsplit("|", 1)[-1])
        if self.empty:
            result.unbaked = list(targets)
            return result
        folder = output_dir or "C:/out"
        if packing == "atlas":
            # The plan-first path: one shared atlas, each object its own rect.
            atlas = os.path.join(folder, f"Mat{suffix}.exr")
            for i, o in enumerate(targets):
                result.maps[o] = atlas
                result.rects[o] = [1.0, 1.0 / len(targets), 0.0, i / len(targets)]
        else:
            for o in targets:
                result.maps[o] = os.path.join(
                    folder, f"{prefix}{o.rsplit('|', 1)[-1]}{suffix}.exr"
                )
                result.rects[o] = [1.0, 1.0, 0.0, 0.0]
        result.verdict = self.verdict
        return result


class TestPresetTemplate(unittest.TestCase):
    """The Preset combo is uitk's preset template over the SHARED store.

    Semantic mode: a preset is the store's ``{key: value}`` dict, the same file
    :meth:`LightmapBaker.from_preset` reads -- so what is pinned here is the
    panel's half of that contract: Save reads every setting through one map,
    a load writes back through the same map, a shipped tier (which stores only
    the quality dials) leaves the switches alone, and every key the panel
    writes is one the headless path understands. The combo wiring itself is
    uitk's (``PresetManager.wire_combo``, covered by its own suite).

    No Maya (plain TestCase): the slot methods under test touch only widgets.
    """

    def _slots(self, ui):
        # __new__ skips the Qt-touching __init__ (loaded_ui access, QTimer).
        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s.ui = ui
        return s

    def test_save_reads_every_bake_setting_under_its_store_key(self):
        ui = _SlotUi(
            res=2048,
            samples=6,
            gi_samples=5,
            bounces=3,
            adaptive=False,
            environment=False,
            denoise=False,
            packing="Atlas by Material (shared map)",
            beside=True,
        )
        self.assertEqual(
            self._slots(ui)._preset_values(),
            {
                "packing": "atlas",
                "resolution": 2048,
                "samples": 6,
                "gi_samples": 5,
                "gi_depth": 3,
                "adaptive": False,
                "include_environment": False,
                "denoise": False,
                "beside_textures": True,
            },
        )

    def test_a_saved_preset_loads_back_onto_every_widget(self):
        saved = self._slots(
            _SlotUi(
                res=512,
                samples=3,
                gi_samples=7,
                bounces=1,
                adaptive=False,
                environment=False,
                denoise=False,
                packing="Atlas by Material (shared map)",
                beside=True,
            )
        )._preset_values()
        target = self._slots(_SlotUi())  # the panel's defaults

        applied = target._apply_preset_values(dict(saved, description="ignored"))

        self.assertEqual(applied, len(saved))
        self.assertEqual(target._preset_values(), saved)

    def test_a_shipped_tier_moves_the_dials_and_leaves_the_switches(self):
        """Overlay semantics: a built-in stores only the quality dials, so
        picking one must not flip Denoise / Packing / Beside Textures back."""
        store = LightmapBaker.preset_store()
        ui = _SlotUi(
            denoise=False,
            environment=False,
            adaptive=False,
            packing="Atlas by Material (shared map)",
            beside=True,
        )
        s = self._slots(ui)

        s._apply_preset_values(store.load("desktop"))

        values = s._preset_values()
        self.assertEqual(
            (
                values["resolution"],
                values["samples"],
                values["gi_depth"],
                values["gi_samples"],
            ),
            (2048, 8, 3, 6),
        )
        self.assertEqual(
            (
                values["denoise"],
                values["include_environment"],
                values["adaptive"],
                values["packing"],
                values["beside_textures"],
            ),
            (False, False, False, "atlas", True),
        )

    def test_every_key_the_panel_saves_is_one_the_headless_path_reads(self):
        """Drift guard: a key only the panel knew would save, load in the
        panel, and silently do nothing in ``from_preset``."""
        keys = set(self._slots(_SlotUi())._preset_fields())
        self.assertEqual(
            keys - {"packing"},
            set(LightmapBaker.PRESET_INT_KEYS) | set(LightmapBaker.PRESET_BOOL_KEYS),
        )

    def test_a_bad_value_is_skipped_not_fatal(self):
        s = self._slots(_SlotUi(samples=4))
        with self.assertLogs(s.logger, level="WARNING"):
            applied = s._apply_preset_values({"samples": "lots", "gi_depth": 2})
        self.assertEqual(applied, 1)
        self.assertEqual(s.ui.spn_samples.value(), 4)
        self.assertEqual(s.ui.spn_bounces.value(), 2)


class TestLightmapBakerSlots(MayaTkTestCase):
    """The panel's half: what it hands :meth:`LightmapBaker.bake`, and how it
    reports what comes back. The bake's own checks (Arnold, the lights, the
    verdict) are the engine's, pinned in :class:`TestBakeWorkflow`."""

    def setUp(self):
        super().setUp()
        # b000 builds LightmapBaker(...) from the panel module's globals --
        # swap in the recorder, and put back what a test set on it.
        self._orig_cls = slots_module.LightmapBaker
        slots_module.LightmapBaker = _FakeWorkflow
        _FakeWorkflow.instances = []
        self.addCleanup(setattr, slots_module, "LightmapBaker", self._orig_cls)
        for knob in ("baked", "refusal", "empty", "verdict"):
            self.addCleanup(setattr, _FakeWorkflow, knob, getattr(_FakeWorkflow, knob))

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

    def _make_light(self, intensity=110):
        """An area light, as ``(shape, transform)`` full paths.

        ``shadingNode(asLight=True)`` hands back the TRANSFORM, so the shape is
        resolved rather than assumed -- ``.intensity`` lives on the shape and
        the visibility that decides contribution is inherited from the
        transform, and the guard under test reads one of each.
        """
        node = cmds.shadingNode("areaLight", asLight=True)
        shape = (cmds.ls(node, dag=True, shapes=True, long=True) or [node])[0]
        transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [node])[0]
        cmds.setAttr(f"{shape}.intensity", intensity)
        return shape, transform

    def test_b000_carries_the_device_and_environment_rows_to_the_bake(self):
        # The rows are bake INPUTS, not cosmetics: a Device row the bake never
        # reads would silently keep rendering on the scene's own device, an
        # unchecked Include Environment would still bake the skydome in, and
        # an unchecked Denoise would still denoise.
        self._select_cube()
        s = self._slots(_SlotUi(device="CPU", environment=False, denoise=False))
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.device, "CPU")
        self.assertFalse(baker.include_environment)
        self.assertFalse(baker.denoise)

        _FakeWorkflow.instances.clear()
        s = self._slots(_SlotUi())  # the panel's defaults
        s.b000()
        default = _FakeWorkflow.instances[0]
        self.assertEqual(default.device, "AUTO")
        self.assertTrue(default.include_environment)
        self.assertTrue(default.denoise)

    def test_b000_hands_the_scope_and_dials_to_bake_and_reverts_nothing(self):
        # ONE call: the engine checks the scene, bakes and records. Nothing is
        # reverted first -- an object the bake does not finish keeps its map.
        long = self._select_cube()
        ui = _SlotUi(res=2048, samples=8)
        s = self._slots(ui)
        s.b000()

        self.assertEqual(len(_FakeWorkflow.instances), 1)
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.resolution, 2048)  # dials drive the workflow
        self.assertEqual(baker.samples, 8)
        self.assertEqual(baker.calls, [("bake", (long,), "per_object")])
        # The bake is directed at the project's sourceimages (or the workflow
        # default when there's no project) -- same resolver the slot uses.
        self.assertEqual(baker.bake_output_dir, LightmapBakerSlots._sourceimages_dir())
        self.assertIn("Baked", ui.footer.text)

    def test_b000_atlas_packing_bakes_the_atlas_and_says_so(self):
        # ONE atlas bake, not bake_separated + pack_atlas: the layout is
        # planned before any ray is traced (the engine's bake_atlas).
        long = self._select_cube()
        ui = _SlotUi(packing="Atlas by Material (shared map)")
        s = self._slots(ui)
        s.b000()

        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.calls, [("bake", (long,), "atlas")])
        self.assertIn("atlas", ui.footer.text.lower())

    def test_b000_per_object_packing_skips_atlas(self):
        # Per-Object packing bakes one full map each.
        self._select_cube()
        ui = _SlotUi(packing="Per-Object (one map each)")
        s = self._slots(ui)
        s.b000()
        self.assertEqual(_FakeWorkflow.instances[0].calls[0][2], "per_object")

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
        self.assertEqual(s._output_dir(), os.path.join(self._SRC, "lightmaps"))
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
        result = _FakeWorkflow.instances[0].last_result
        atlas = next(iter(result.maps.values()))
        self.assertEqual(os.path.dirname(atlas), os.path.join(self._SRC, "lightmaps"))

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
    def test_b000_reports_a_refusal_as_the_engine_words_it(self):
        """The panel no longer holds the checks; it shows what they said."""
        self._select_cube()
        _FakeWorkflow.refusal = (
            "Bake skipped: all 4 scene light(s) are hidden or at intensity 0 "
            "(see Script Editor)."
        )
        ui = _SlotUi()
        s = self._slots(ui)
        s.b000()
        self.assertEqual(ui.footer.text, _FakeWorkflow.refusal)
        self.assertIsNone(s._last_output_dir)

    def test_b000_reports_an_empty_bake(self):
        self._select_cube()
        _FakeWorkflow.empty = True
        s = self._slots(_SlotUi())
        s.b000()
        self.assertIn("no output", s.ui.footer.text)

    def test_b000_reports_what_it_left_alone_and_the_verdict(self):
        """Excluded and unbaked objects keep their maps -- the footer says so
        -- and a bad level rides the summary as a WARNING."""
        kept = self._select_cube("reportKept")
        left_out = self._select_cube("reportLeftOut")
        LightmapExcludeSet.define([left_out])
        cmds.select([kept, left_out], replace=True)
        _FakeWorkflow.verdict = "bake is essentially UNLIT — check light intensities."
        ui = _SlotUi()
        s = self._slots(ui)
        s.b000()
        self.assertIn("Baked 1 object", ui.footer.text)
        self.assertIn("1 excluded", ui.footer.text)
        self.assertIn("WARNING: bake is essentially UNLIT", ui.footer.text)

    def test_revert_to_source_asks_first_and_cancel_changes_nothing(self):
        """The header item reaches every baked object when nothing is selected,
        one row from the panel's other actions -- it must say so and wait."""
        long = self._select_cube()
        s = self._slots(_SlotUi())
        s.sb = _Sb(answer="Cancel")

        s.revert_to_source()

        baker = _FakeWorkflow.instances[0]
        self.assertEqual(len(s.sb.asked), 1)
        text, buttons = s.sb.asked[0]
        self.assertIn("1 selected object", text)
        self.assertEqual(buttons, ("Ok", "Cancel"))
        self.assertNotIn("revert", [c[0] for c in baker.calls])
        self.assertIn("cancelled", s.ui.footer.text)
        self.assertTrue(cmds.objExists(long))

    def test_revert_to_source_routes_selection_once_confirmed(self):
        long = self._select_cube()
        s = self._slots(_SlotUi())
        s.sb = _Sb(answer="Ok")
        s.revert_to_source()
        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.calls, [("baked_objects", (long,)), ("revert", (long,))])
        self.assertIn("Reverted 1 object", s.ui.footer.text)

    def test_revert_to_source_all_when_no_selection(self):
        cmds.select(clear=True)
        s = self._slots(_SlotUi())
        s.sb = _Sb(answer="Ok")
        s.revert_to_source()
        baker = _FakeWorkflow.instances[0]
        self.assertIn("all of them", s.sb.asked[0][0])
        self.assertEqual(baker.calls[-1], ("revert", None))  # None -> all marked

    def test_revert_to_source_reads_a_face_selection_as_its_object(self):
        """Faces name their mesh, as they do for a bake. Read as transforms
        only, a face selection was "nothing selected", and the dialog offered
        to revert EVERY baked object."""
        long = self._select_cube("revertFace")
        cmds.select(f"{long}.f[0]", replace=True)
        s = self._slots(_SlotUi())
        s.sb = _Sb(answer="Ok")
        s.revert_to_source()
        baker = _FakeWorkflow.instances[0]
        self.assertIn("1 selected object", s.sb.asked[0][0])
        self.assertEqual(baker.calls, [("baked_objects", (long,)), ("revert", (long,))])

    def test_revert_to_source_with_only_an_empty_group_selected_asks_nothing(self):
        """A group bakes nothing (``TextureBaker.resolve_meshes``), so it has
        no lightmap to take off -- the footer says so, and nothing is asked."""
        cmds.select(cmds.group(empty=True, name="revertGroup"), replace=True)
        s = self._slots(_SlotUi())
        s.sb = _Sb(answer="Ok")
        s.revert_to_source()
        self.assertEqual(s.sb.asked, [])
        self.assertIn("has a lightmap", s.ui.footer.text)

    def test_open_output_folder_opens_where_the_last_bake_wrote(self):
        """Beside Material Textures sends the maps away from the Output
        Directory, which then showed nothing new."""
        wrote = tempfile.mkdtemp(prefix="lm_last_bake_")
        self.addCleanup(shutil.rmtree, wrote, ignore_errors=True)
        s = self._slots(_SlotUi())
        s._last_output_dir = wrote
        with mock.patch.object(
            ptk.FileUtils, "open_explorer", return_value=True
        ) as opened:
            s.open_sourceimages()
        self.assertEqual(opened.call_args.args[0], wrote)

    def test_revert_to_source_with_nothing_baked_never_asks(self):
        self._select_cube()
        _FakeWorkflow.baked = []
        self.addCleanup(setattr, _FakeWorkflow, "baked", None)
        s = self._slots(_SlotUi())
        s.sb = _Sb(answer="Ok")
        s.revert_to_source()
        self.assertEqual(s.sb.asked, [])
        self.assertNotIn("revert", [c[0] for c in _FakeWorkflow.instances[0].calls])
        self.assertIn("has a lightmap", s.ui.footer.text)

    def test_b000_carries_the_gi_adaptive_and_beside_rows_to_the_bake(self):
        # Inputs, not cosmetics: a Bounces spinbox the bake never read would
        # bake the preset's depth no matter what it showed.
        self._select_cube()
        s = self._slots(_SlotUi(gi_samples=7, bounces=4, adaptive=False, beside=True))
        s.b000()
        baker = _FakeWorkflow.instances[0]
        self.assertEqual((baker.gi_depth, baker.gi_samples), (4, 7))
        self.assertIs(baker.adaptive, False)
        self.assertIs(baker.beside_textures, True)

    # ------------------------------------------------------------- Exclude

    def test_b000_never_bakes_an_excluded_object(self):
        """An excluded object keeps the map it has: it is filtered out before
        anything touches the scene, and nothing is reverted either way."""
        kept = self._select_cube("keptCube")
        left_out = self._select_cube("leftOutCube")
        LightmapExcludeSet.define([left_out])
        cmds.select([kept, left_out], replace=True)
        ui = _SlotUi()
        s = self._slots(ui)

        s.b000()

        baker = _FakeWorkflow.instances[0]
        self.assertEqual(baker.calls, [("bake", (kept,), "per_object")])
        self.assertIn("1 excluded", ui.footer.text)

    def test_b000_with_everything_excluded_bakes_nothing_and_says_why(self):
        cube = self._select_cube()
        LightmapExcludeSet.define([cube])
        cmds.select(cube, replace=True)
        ui = _SlotUi()
        s = self._slots(ui)
        s.b000()
        self.assertEqual(_FakeWorkflow.instances[0].calls, [("bake", (), "per_object")])
        self.assertIn("Exclude set", ui.footer.text)

    def test_exclude_row_sets_selects_and_clears_the_scene_set(self):
        group = cmds.group(empty=True, name="excludeGroup")
        a = cmds.polyCube(name="exA")[0]
        b = cmds.polyCube(name="exB")[0]
        cmds.parent(a, b, group)
        ui = _SlotUi()
        s = self._slots(ui)

        cmds.select(group, replace=True)
        s.set_exclusions()
        # A group counts the meshes under it: that is what the bake skips.
        self.assertEqual(ui.lbl_exclude.text(), "Exclude (2):")
        self.assertIn("2 meshes excluded", ui.footer.text)

        cmds.select(clear=True)
        s.select_exclusions()
        self.assertEqual(cmds.ls(selection=True, long=True), cmds.ls(group, long=True))

        s.clear_exclusions()
        self.assertFalse(LightmapExcludeSet.exists())
        self.assertEqual(ui.lbl_exclude.text(), "Exclude:")

        cmds.select(clear=True)
        LightmapExcludeSet.define([a])
        s.set_exclusions()  # nothing selected: clears, and says so
        self.assertFalse(LightmapExcludeSet.exists())
        self.assertIn("cleared", ui.footer.text)

        # A selection with no mesh in it (a locator) excludes nothing -- and
        # must say so rather than report "0 meshes excluded".
        cmds.select(cmds.spaceLocator(name="exNoMesh")[0], replace=True)
        s.set_exclusions()
        self.assertEqual(ui.lbl_exclude.text(), "Exclude:")
        self.assertIn("holds no meshes", ui.footer.text)

    def test_exclude_hover_lists_the_meshes_the_label_counts(self):
        """A group in the set must read as the meshes it keeps from baking --
        the label's count -- not as one opaque group name."""
        from types import SimpleNamespace

        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        group = cmds.group(empty=True, name="hoverGroup")
        cmds.parent(cmds.polyCube(name="hoverA")[0], group)
        cmds.parent(cmds.polyCube(name="hoverB")[0], group)
        LightmapExcludeSet.define([group])
        s = self._slots(_SlotUi())
        s.sb = SimpleNamespace(tooltip=TooltipFormat)

        tip = s._exclusions_tooltip("Exclude help.")

        self.assertIn("Exclude help.", tip)
        self.assertIn("hoverA", tip)
        self.assertIn("hoverB", tip)
        LightmapExcludeSet.clear()
        self.assertIn("Nothing is excluded", s._exclusions_tooltip("Exclude help."))


class TestLightmapDependencies(MayaTkTestCase):
    """The lightmap-side answer to "where are my maps NOW".

    A committed lightmap is a texture dependency with no file node: the marker
    records a basename and the folder it was baked into, and that folder is
    history. Reported 2026-08-26: a scene migrated to another module with
    every texture copied by the Texture Path Editor previewed unlit -- the
    EXRs stayed behind, and nothing listed them. These pin the engine the
    panel, the exporter's path check and the GLB conversion now share.

    No renderer: commit_lightmap only stamps markers, so an empty file is a
    lightmap for every purpose here.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_deps_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # A project of our own: the sourceimages walk and the search folders
        # both read the live workspace.
        original_ws = cmds.workspace(q=True, rd=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))
        self.root = os.path.join(self.tmp, "project")
        self.si = os.path.join(self.root, "sourceimages")
        os.makedirs(self.si, exist_ok=True)
        cmds.workspace(self.root, openWorkspace=True)
        self.baker = LightmapRecords

    # -- helpers ------------------------------------------------------------

    def _cube(self, name):
        return cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]

    def _file(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"exr")
        return path.replace("\\", "/")

    def _commit(self, obj, path):
        self.baker.commit({obj: path})

    def _marker_raw_dir(self, obj):
        """The folder exactly as the marker stores it (the portable spelling)."""
        raw = cmds.getAttr(f"{obj}.{LightmapBaker.LIGHTMAP_INFO_ATTR}")
        return json.loads(raw).get("dir", "")

    def _marker_dir(self, obj):
        """The marker's folder resolved on this machine (what a consumer joins)."""
        raw = json.loads(cmds.getAttr(f"{obj}.{LightmapBaker.LIGHTMAP_INFO_ATTR}"))
        return LightmapRecords._resolved_dir(raw.get("dir", ""), raw.get("map", ""))

    def _lead_dir(self):
        """The folder a GLB build is handed FIRST for this scene's maps
        (:meth:`LightmapRecords.search_dirs`) -- the manifest names none."""
        dirs = LightmapRecords.search_dirs()
        return dirs[0] if dirs else ""

    def _manifest(self):
        from mayatk.node_utils.data_nodes import DataNodes

        return ptk.SceneRecords.LIGHTMAPS.load(DataNodes) or {}

    @staticmethod
    def _same(a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(
            os.path.abspath(b)
        )

    # -- lightmap_dependencies ---------------------------------------------

    def test_a_map_still_in_its_recorded_folder_resolves_by_hint(self):
        cube = self._cube("hinted")
        path = self._file("bake", "hinted_LightMap.exr")
        self._commit(cube, path)

        (dep,) = self.baker.lightmap_dependencies()

        self.assertEqual(dep["map"], "hinted_LightMap.exr")
        self.assertEqual(dep["objects"], [cube])
        self.assertEqual(dep["found_by"], LightmapRecords.FOUND_BY_HINT)
        self.assertTrue(self._same(dep["path"], path))
        self.assertTrue(self._same(dep["dir"], os.path.dirname(path)))

    def test_a_moved_map_is_found_under_sourceimages(self):
        """The hint is dead; the map sits in a sourceimages SUBFOLDER -- where
        a root-only join (the GLB applier's search list) could never see it,
        so ``search_dirs`` must name that folder outright."""
        cube = self._cube("moved")
        path = self._file("old", "moved_LightMap.exr")
        self._commit(cube, path)
        os.makedirs(os.path.join(self.si, "lightmaps"), exist_ok=True)
        found = os.path.join(self.si, "lightmaps", "moved_LightMap.exr")
        shutil.move(path, found)

        (dep,) = self.baker.lightmap_dependencies()

        self.assertEqual(dep["found_by"], LightmapRecords.FOUND_BY_SEARCH)
        self.assertTrue(self._same(dep["path"], found))
        self.assertTrue(
            any(
                self._same(d, os.path.dirname(found))
                for d in LightmapRecords.search_dirs()
            ),
            LightmapRecords.search_dirs(),
        )

    def test_a_map_found_nowhere_is_reported_missing(self):
        cube = self._cube("lost")
        self._commit(cube, os.path.join(self.tmp, "gone", "lost_LightMap.exr"))

        (dep,) = self.baker.lightmap_dependencies()

        self.assertIsNone(dep["path"])
        self.assertIsNone(dep["found_by"])
        self.assertEqual(dep["note"], "")

    def test_several_same_named_files_are_not_guessed_at(self):
        cube = self._cube("ambiguous")
        self._commit(cube, os.path.join(self.tmp, "gone", "amb_LightMap.exr"))
        for sub in ("a", "b"):
            os.makedirs(os.path.join(self.si, sub), exist_ok=True)
            open(os.path.join(self.si, sub, "amb_LightMap.exr"), "wb").close()

        (dep,) = self.baker.lightmap_dependencies()

        self.assertIsNone(dep["path"])
        self.assertIn("ambiguous", dep["note"])

    def test_a_shared_atlas_is_one_record_naming_every_object(self):
        a, b = self._cube("atlas_a"), self._cube("atlas_b")
        path = self._file("bake", "atlas.exr")
        self.baker.commit({a: path, b: path})

        (dep,) = self.baker.lightmap_dependencies()

        self.assertEqual(sorted(dep["objects"]), sorted([a, b]))

    def test_scope_is_the_given_roots_and_their_descendants(self):
        inside = cmds.polyCube(name="inside")[0]
        outside = self._cube("outside")
        group = cmds.ls(cmds.group(inside, name="room"), long=True)[0]
        inside = cmds.ls(inside, long=True)[0]
        self.baker.commit(
            {
                inside: self._file("bake", "in.exr"),
                outside: self._file("bake", "out.exr"),
            }
        )

        scoped = self.baker.lightmap_dependencies(objects=[group])

        self.assertEqual([d["map"] for d in scoped], ["in.exr"])
        self.assertEqual(self.baker.lightmap_dependencies(objects=[]), [])
        self.assertEqual(len(self.baker.lightmap_dependencies()), 2)

    # -- heal_lightmap_paths -----------------------------------------------

    def test_heal_rewrites_a_stale_hint_and_republishes_the_manifest(self):
        cube = self._cube("healed")
        path = self._file("old", "healed_LightMap.exr")
        self._commit(cube, path)
        os.makedirs(os.path.join(self.si, "lm"), exist_ok=True)
        found = os.path.join(self.si, "lm", "healed_LightMap.exr")
        shutil.move(path, found)

        report = self.baker.heal_lightmap_paths()

        self.assertEqual([h[0] for h in report["healed"]], ["healed_LightMap.exr"])
        self.assertEqual(report["missing"], [])
        self.assertTrue(self._same(self._marker_dir(cube), os.path.dirname(found)))
        self.assertTrue(self._same(self._lead_dir(), os.path.dirname(found)))
        self.assertEqual([o["name"] for o in self._manifest()["objects"]], ["healed"])
        # Healed means resolved by hint from now on -- a second pass is a no-op.
        self.assertEqual(self.baker.heal_lightmap_paths()["healed"], [])
        self.assertEqual(
            self.baker.lightmap_dependencies()[0]["found_by"],
            LightmapRecords.FOUND_BY_HINT,
        )

    def test_heal_never_touches_a_file_and_names_what_stays_missing(self):
        cube = self._cube("unhealed")
        self._commit(cube, os.path.join(self.tmp, "gone", "unhealed.exr"))

        report = self.baker.heal_lightmap_paths()

        self.assertEqual(report["healed"], [])
        self.assertEqual([d["map"] for d in report["missing"]], ["unhealed.exr"])
        self.assertFalse(os.listdir(self.si))

    # -- relocate_lightmaps ------------------------------------------------

    def test_relocate_copies_into_the_destination_and_repoints_the_markers(self):
        cube = self._cube("reloc")
        src = self._file("elsewhere", "reloc_LightMap.exr")
        self._commit(cube, src)

        result = self.baker.relocate_lightmaps(self.si)

        self.assertTrue(os.path.isfile(os.path.join(self.si, "reloc_LightMap.exr")))
        self.assertTrue(os.path.isfile(src), "copy keeps the original")
        self.assertEqual(len(result["copied"]), 1)
        self.assertEqual(result["updated"], 1)
        self.assertTrue(self._same(self._marker_dir(cube), self.si))
        self.assertTrue(self._same(self._lead_dir(), self.si))
        self.assertEqual(
            self.baker.lightmap_dependencies()[0]["found_by"],
            LightmapRecords.FOUND_BY_HINT,
        )

    def test_relocate_dry_run_plans_and_changes_nothing(self):
        cube = self._cube("planned")
        src = self._file("elsewhere", "planned_LightMap.exr")
        self._commit(cube, src)
        before = self._marker_dir(cube)

        result = self.baker.relocate_lightmaps(self.si, dry_run=True)

        self.assertEqual(len(result["relocate"]), 1)
        self.assertEqual(result["copied"], [])
        self.assertEqual(result["updated"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.si, "planned_LightMap.exr")))
        self.assertEqual(self._marker_dir(cube), before)

    def test_relocate_searches_the_source_folder_for_a_missing_map(self):
        cube = self._cube("searched")
        self._commit(cube, os.path.join(self.tmp, "gone", "searched_LightMap.exr"))
        self._file("archive", "deep", "searched_LightMap.exr")

        result = self.baker.relocate_lightmaps(
            self.si, source_dir=os.path.join(self.tmp, "archive")
        )

        self.assertEqual(result["missing"], [])
        self.assertTrue(os.path.isfile(os.path.join(self.si, "searched_LightMap.exr")))
        self.assertTrue(self._same(self._marker_dir(cube), self.si))

    def test_relocate_move_removes_the_original(self):
        cube = self._cube("movedmap")
        src = self._file("elsewhere", "movedmap_LightMap.exr")
        self._commit(cube, src)

        self.baker.relocate_lightmaps(self.si, mode="move")

        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.isfile(os.path.join(self.si, "movedmap_LightMap.exr")))

    def test_relocate_leaves_a_map_already_at_the_destination_in_place(self):
        cube = self._cube("inplace")
        open(os.path.join(self.si, "inplace_LightMap.exr"), "wb").close()
        self._commit(cube, os.path.join(self.tmp, "gone", "inplace_LightMap.exr"))

        result = self.baker.relocate_lightmaps(self.si)

        self.assertEqual(result["copied"], [])
        self.assertEqual(len(result["in_place"]), 1)
        self.assertTrue(self._same(self._marker_dir(cube), self.si))

    def test_relocate_names_what_it_could_not_find(self):
        cube = self._cube("nowhere")
        self._commit(cube, os.path.join(self.tmp, "gone", "nowhere.exr"))

        result = self.baker.relocate_lightmaps(self.si, source_dir=self.tmp)

        self.assertEqual([d["map"] for d in result["missing"]], ["nowhere.exr"])
        self.assertEqual(result["updated"], 0)

    # -- the portable spelling ---------------------------------------------

    def test_a_map_inside_the_project_is_recorded_workspace_relative(self):
        """Asked 2026-08-26: a teammate mounts the cloud project on another
        drive, so an absolute marker folder resolves nowhere there. The marker
        stores the workspace-relative form (the rule textures follow); the
        manifest names no folder at all, and what a build on THIS machine is
        handed (``search_dirs``) is the absolute one."""
        cube = self._cube("portable")
        os.makedirs(os.path.join(self.si, "lm"), exist_ok=True)
        path = os.path.join(self.si, "lm", "portable_LightMap.exr")
        open(path, "wb").close()
        self._commit(cube, path)

        self.assertEqual(self._marker_raw_dir(cube), "sourceimages/lm")
        self.assertTrue(self._same(self._lead_dir(), os.path.dirname(path)))
        manifest = self._manifest()
        self.assertTrue(manifest.get("objects"), manifest)
        self.assertFalse({"dir", "dirs"} & set(manifest), manifest)
        (dep,) = self.baker.lightmap_dependencies()
        self.assertEqual(dep["found_by"], LightmapRecords.FOUND_BY_HINT)
        self.assertTrue(self._same(dep["path"], path))

    def test_a_map_outside_the_project_stays_absolute(self):
        cube = self._cube("external")
        path = self._file("elsewhere", "external_LightMap.exr")
        self._commit(cube, path)

        self.assertTrue(os.path.isabs(self._marker_raw_dir(cube)))
        self.assertTrue(self._same(self._marker_raw_dir(cube), os.path.dirname(path)))

    def test_normalize_lightmap_paths_round_trips(self):
        cube = self._cube("normalized")
        os.makedirs(os.path.join(self.si, "lm"), exist_ok=True)
        path = os.path.join(self.si, "lm", "normalized_LightMap.exr")
        open(path, "wb").close()
        self._commit(cube, path)

        self.assertEqual(self.baker.normalize_lightmap_paths(relative=False), 1)
        self.assertTrue(os.path.isabs(self._marker_raw_dir(cube)))
        self.assertEqual(self.baker.normalize_lightmap_paths(), 1)
        self.assertEqual(self._marker_raw_dir(cube), "sourceimages/lm")
        self.assertEqual(self.baker.normalize_lightmap_paths(), 0, "idempotent")
        self.assertTrue(self._same(self._marker_dir(cube), os.path.dirname(path)))

    def test_relocating_into_the_project_stores_the_relative_spelling(self):
        cube = self._cube("landed")
        self._commit(cube, self._file("elsewhere", "landed_LightMap.exr"))

        self.baker.relocate_lightmaps(self.si)

        self.assertEqual(self._marker_raw_dir(cube), "sourceimages")
        self.assertTrue(self._same(self._lead_dir(), self.si))


class TestLightmapSearchDirs(MayaTkTestCase):
    """Where a GLB build finds the maps -- ``LightmapRecords.search_dirs``.

    The deliverable names no folder: the GLB embeds the maps, and the manifest
    stopped publishing the absolute authoring folders (``dir`` / ``dirs``).
    The host hands every build this list instead, and its ORDER is the
    contract: a consumer that joins a basename against it takes the first
    folder holding a file of that name, and a project routinely holds an atlas
    of the same name from an earlier bake. Measured on the production room --
    46 objects bound a 17-day-old 512px atlas through rects computed for a
    fresh 1024px one, every object sampling a patch of someone else's
    lighting, silently.
    """

    def setUp(self):
        super().setUp()
        self.baker = LightmapBaker()
        self.tmp = tempfile.mkdtemp(prefix="lm_hint_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # A project of our own, so the texture folders the list ENDS with are
        # known (``EnvUtils.texture_search_dirs`` reads the live workspace).
        original_ws = cmds.workspace(q=True, rd=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))
        self.si = os.path.join(self.tmp, "project", "sourceimages")
        os.makedirs(self.si, exist_ok=True)
        cmds.workspace(os.path.dirname(self.si), openWorkspace=True)

    def _cube(self, name):
        return cmds.ls(cmds.polyCube(name=name)[0], long=False)[0]

    def _map(self, folder, name):
        path = os.path.join(self.tmp, folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(b"exr")
        return path.replace("\\", "/")

    def _manifest(self):
        from mayatk.node_utils.data_nodes import DataNodes

        return ptk.SceneRecords.LIGHTMAPS.load(DataNodes) or {}

    @staticmethod
    def _same(a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(
            os.path.abspath(b)
        )

    def _assert_leads(self, dirs, folders):
        """*dirs* begins with *folders*, in that order (compared as paths)."""
        self.assertGreaterEqual(len(dirs), len(folders), dirs)
        for got, want in zip(dirs, folders):
            self.assertTrue(self._same(got, want), f"{dirs} must lead with {folders}")

    def test_the_manifest_names_no_folder(self):
        """An absolute authoring path in a shipped file resolves nowhere but
        the machine that baked it; where the maps live is the build's
        question, answered by the scene."""
        a, b = self._cube("room_a"), self._cube("room_b")
        one = self._map("bake", "room_LightMap.exr")
        self.baker.commit_lightmap({a: one, b: one})

        manifest = self._manifest()
        self.assertEqual(
            sorted(o["name"] for o in manifest["objects"]), ["room_a", "room_b"]
        )
        self.assertNotIn("dir", manifest)
        self.assertNotIn("dirs", manifest)
        self._assert_leads(LightmapRecords.search_dirs(), [os.path.dirname(one), self.si])

    def test_every_folder_the_markers_name_comes_before_the_texture_folders(self):
        """One object keeping a marker from an earlier bake -- exactly what
        happens when a bake SKIPS it -- must not cost the folder every other
        object agrees on (the regression the single-folder hint had), and no
        marker's folder may trail the workspace's texture folders."""
        fresh_obj, stale_obj = self._cube("fresh"), self._cube("stale")
        fresh = self._map("today", "room_LightMap.exr")
        stale = self._map("last_month", "old_LightMap.exr")
        self.baker.commit_lightmap({stale_obj: stale})
        self.baker.commit_lightmap({fresh_obj: fresh})

        dirs = LightmapRecords.search_dirs()
        self.assertEqual(
            {os.path.normcase(os.path.abspath(d)) for d in dirs[:2]},
            {
                os.path.normcase(os.path.abspath(os.path.dirname(p)))
                for p in (fresh, stale)
            },
            dirs,
        )
        self.assertTrue(self._same(dirs[2], self.si), dirs)

    def test_the_folder_most_of_the_bake_used_comes_first(self):
        """The list is a PRIORITY, not a set: the reader takes the first folder
        holding a file of the right basename. Alphabetical order was the first
        cut and was wrong -- on the production paths the stale folder sorts
        first, which reinstates the exact bug the order exists to fix. Ordered
        by how many baked objects name each folder instead."""
        fresh = self._map("zzz_today", "room_LightMap.exr")
        stale = self._map("aaa_last_month", "old_LightMap.exr")
        # One object left on the old bake, three re-baked -- and the old folder
        # sorts FIRST alphabetically, so a sorted list would lead with it.
        self.baker.commit_lightmap({self._cube("kept"): stale})
        for name in ("re_a", "re_b", "re_c"):
            self.baker.commit_lightmap({self._cube(name): fresh})

        self._assert_leads(
            LightmapRecords.search_dirs(),
            [os.path.dirname(fresh), os.path.dirname(stale), self.si],
        )

    def test_a_same_named_stale_atlas_in_a_texture_folder_never_comes_first(self):
        """The production failure itself: the workspace's texture folder holds
        an atlas of the SAME name from an earlier bake. The folder the
        markers' map resolves to leads, so a basename join binds today's bake."""
        stale = os.path.join(self.si, "room_LightMap.exr")
        with open(stale, "wb") as fh:
            fh.write(b"old")
        fresh = self._map("bake", "room_LightMap.exr")
        self.baker.commit_lightmap({self._cube("room"): fresh})

        dirs = LightmapRecords.search_dirs()
        self._assert_leads(dirs, [os.path.dirname(fresh), self.si])
        bound = next(
            os.path.join(d, "room_LightMap.exr")
            for d in dirs
            if os.path.isfile(os.path.join(d, "room_LightMap.exr"))
        )
        self.assertTrue(self._same(bound, fresh), f"a basename join bound {bound}")

    def test_the_order_is_stable_across_runs(self):
        """Ties break on the path, so asking again of an unchanged scene is a
        no-op diff rather than a coin flip on set iteration order."""
        a, b = self._cube("aa"), self._cube("bb")
        self.baker.commit_lightmap({a: self._map("z_dir", "a_LightMap.exr")})
        self.baker.commit_lightmap({b: self._map("a_dir", "b_LightMap.exr")})

        first = LightmapRecords.search_dirs()
        self.baker.commit_lightmap({b: self._map("a_dir", "b_LightMap.exr")})
        self.assertEqual(first, LightmapRecords.search_dirs())
        # One object each -- the tie -- so this pair IS alphabetical.
        self.assertEqual(first[:2], sorted(first[:2]))


class _FakePresets:
    """Stands in for the wired PresetManager: the pointer and the combo sync."""

    def __init__(self, active="quest"):
        self.active_preset = active
        self.refreshed = 0

    def refresh_combo(self, select_name=None):
        self.refreshed += 1


class TestPanelSwitches(unittest.TestCase):
    """Every boolean on the panel rides the option box of the field it
    qualifies (``LightmapBakerSlots._TOGGLES``).

    No Maya, no Qt: the stubs answer ``option_box.find_option`` the way uitk's
    manager does, so what is pinned is the panel's half -- which field hosts
    which switch, that every reader goes through it, and that a preset writes
    the toggle rather than a widget that no longer exists.
    """

    def _slots(self, **kwargs):
        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s.ui = _SlotUi(**kwargs)
        return s

    def test_each_switch_reads_from_its_own_fields_option_box(self):
        s = self._slots(environment=False, adaptive=False, denoise=False, beside=True)
        self.assertFalse(s._include_environment(), "the Scope field's switch")
        self.assertFalse(s._adaptive(), "the Samples field's switch")
        self.assertFalse(s._denoise(), "the Resolution field's switch")
        self.assertTrue(s._beside_textures(), "the Output Directory's switch")

    def test_a_switch_read_before_its_field_is_wired_gives_the_shipped_default(self):
        """The preset machinery reads this map while the panel is still
        loading, so a switch with no toggle yet must answer, not raise."""
        s = self._slots()
        for field in ("cmb_scope", "spn_samples", "cmb_resolution", "txt_output_dir"):
            getattr(s.ui, field).option_box.toggle = None
        self.assertTrue(s._include_environment())
        self.assertTrue(s._adaptive())
        self.assertTrue(s._denoise())
        self.assertFalse(s._beside_textures(), "beside textures ships off")

    def test_a_preset_load_writes_a_switch_through_its_toggle(self):
        s = self._slots(environment=True, denoise=True, adaptive=True, beside=False)
        applied = s._apply_preset_values(
            {
                "include_environment": False,
                "denoise": False,
                "adaptive": False,
                "beside_textures": True,
            }
        )
        self.assertEqual(applied, 4)
        self.assertFalse(s._include_environment())
        self.assertFalse(s._denoise())
        self.assertFalse(s._adaptive())
        self.assertTrue(s._beside_textures())

    def test_the_switches_are_keyed_as_the_preset_store_keys_them(self):
        """So ``_preset_fields`` can build its entries straight from the table
        and a preset saved here is one ``from_preset`` reads."""
        self.assertEqual(
            set(LightmapBakerSlots._TOGGLES), set(LightmapBaker.PRESET_BOOL_KEYS)
        )

    def test_the_panel_opens_on_atlas_by_material(self):
        s = self._slots()
        s.ui.cmb002 = _ItemCombo()
        s.cmb002_init(s.ui.cmb002)
        self.assertEqual(s._packing(), "atlas")

    def test_the_scope_still_opens_on_the_selection_with_the_environment_in(self):
        s = self._slots()
        s.ui.cmb_scope = _ItemCombo()
        s.cmb_scope_init(s.ui.cmb_scope)
        self.assertEqual(s._scope(), "selected")
        self.assertTrue(s._include_environment())

    def test_a_field_wires_its_own_switch_when_it_initialises(self):
        """``<field>_init`` is where each switch is hung, under a panel-scoped
        settings key -- the auto-derived one is the field's bare objectName,
        which another panel in the same host would share."""
        s = self._slots()
        s.ui.spn_samples = _Spin(4)
        s.spn_samples_init(s.ui.spn_samples)
        self.assertTrue(s._adaptive(), "adaptive sampling ships on")
        self.assertEqual(
            s.ui.spn_samples.option_box.wired["settings_key"],
            "lightmap_baker_adaptive",
        )


class TestPanelLayout(unittest.TestCase):
    """The panel's shape, read straight off the ``.ui`` -- no Qt.

    Two things it pins. The action block: Preset, Reset to Defaults and Bake
    Lightmaps in one group at the bottom -- the WebXR preview panel's
    ``grp_process`` shape (2026-09-22), so the two panels are worked the same
    way. And the sections above it: what the bake gathers, then the machine it
    runs on, then the quality dials, then where the files go -- each switch
    riding the field it qualifies rather than a checkbox row of its own
    (``LightmapBakerSlots._TOGGLES``).
    """

    def setUp(self):
        import xml.etree.ElementTree as ET

        ui_path = os.path.join(
            os.path.dirname(lmb_module.__file__), "lightmap_baker.ui"
        )
        self.root = ET.parse(ui_path).getroot()

    def _main_items(self):
        return self._items("main_layout")

    def _items(self, layout_name):
        layout = next(
            item for item in self.root.iter("layout") if item.get("name") == layout_name
        )
        return [child for item in layout.findall("item") for child in item]

    def test_the_sections_read_top_to_bottom(self):
        names = [child.get("name") for child in self._main_items()]
        self.assertEqual(
            names,
            [
                "header",
                "cmb_scope",
                "exclude_layout",
                "cmb002",
                "cmb_device",
                "quality_group",
                "output_group",
                "grp_process",
                "verticalSpacer",
                "footer",
            ],
        )

    def test_the_processor_is_not_a_quality_dial(self):
        """It names one machine's hardware, so no preset stores it -- and the
        Quality group is exactly what a preset does store."""
        dials = [child.get("name") for child in self._items("quality_layout")]
        self.assertEqual(
            dials, ["cmb_resolution", "spn_samples", "spn_gi_samples", "spn_bounces"]
        )

    def test_the_output_fields_have_a_section_of_their_own(self):
        fields = [child.get("name") for child in self._items("output_layout")]
        self.assertEqual(fields, ["txt_output_dir", "txt000"])

    def test_no_switch_is_left_as_a_checkbox_row(self):
        boxes = [
            w.get("name")
            for w in self.root.iter("widget")
            if w.get("class") == "QCheckBox"
        ]
        self.assertEqual(boxes, [], "every switch rides its field's option box")

    def test_the_action_group_is_the_last_thing_before_the_footer(self):
        names = [child.get("name") for child in self._main_items()]
        self.assertIn("grp_process", names)
        below = names[names.index("grp_process") + 1 :]
        self.assertEqual(
            below, ["verticalSpacer", "footer"], f"below the group: {below}"
        )
        # The Preset combo moved INTO the group; nothing of it left up top.
        self.assertNotIn("cmb000", names)

    def test_the_group_reads_preset_then_reset_then_bake(self):
        group = next(
            w for w in self.root.iter("widget") if w.get("name") == "grp_process"
        )
        order = [
            child.get("name")
            for item in group.find("layout").findall("item")
            for child in item
        ]
        self.assertEqual(order, ["cmb000", "btn_reset_defaults", "b000"])

    def test_the_reset_button_is_a_button_and_says_so(self):
        button = next(
            w for w in self.root.iter("widget") if w.get("name") == "btn_reset_defaults"
        )
        self.assertEqual(button.get("class"), "QPushButton")
        text = next(
            p.findtext("string")
            for p in button.findall("property")
            if p.get("name") == "text"
        )
        self.assertEqual(text, "Reset to Defaults")


class _SeedPresets:
    """Enough of uitk's PresetManager for ``cmb000_init``: a store holding the
    shipped tier, and a pointer that starts where the last session left it."""

    pointer = None

    def __init__(self, **_kwargs):
        self.active_preset = _SeedPresets.pointer

    def use_logger(self, _logger):
        pass

    def exists(self, name):
        return name == LightmapBakerSlots._DEFAULT_PRESET

    def wire_combo(self, widget, placeholder=None):
        pass


class _Settings:
    """Enough of QSettings for a flag."""

    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class TestDefaultPresetIsSeededOnce(unittest.TestCase):
    """A reset clears the preset pointer (``_after_reset``). Read on the next
    open as "never set", it was seeded again -- and the reset values showed as
    that preset, modified ("quest *"), which is what the reset exists to stop."""

    def _open(self, settings):
        from types import SimpleNamespace

        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s.ui = SimpleNamespace(settings=settings)
        with mock.patch("uitk.managers.preset_manager.PresetManager", _SeedPresets):
            s.cmb000_init(SimpleNamespace(restore_state=True))
        return s._presets.active_preset

    def test_the_first_open_names_the_default_tier(self):
        _SeedPresets.pointer = None
        self.assertEqual(self._open(_Settings()), LightmapBakerSlots._DEFAULT_PRESET)

    def test_an_open_after_a_reset_keeps_the_pointer_cleared(self):
        settings = _Settings()
        _SeedPresets.pointer = None
        self._open(settings)  # the first open seeds it
        _SeedPresets.pointer = None  # ...and a reset cleared it
        self.assertIsNone(self._open(settings))


class TestResetToDefaults(unittest.TestCase):
    """A reset puts the dials at their defaults (uitk's StateManager does that
    part, and its own suite covers it); what this panel owes is the preset
    pointer -- the values are no longer the preset the combo names.

    No Maya, no Qt: ``_after_reset`` touches only the preset manager.
    """

    def _slots(self, active="quest"):
        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s._presets = _FakePresets(active)
        return s

    def test_a_reset_lets_go_of_the_active_preset(self):
        s = self._slots("quest")
        s._after_reset("reset")
        self.assertIsNone(s._presets.active_preset)
        self.assertEqual(s._presets.refreshed, 1, "the combo follows the pointer")

    def test_a_factory_reset_lets_go_of_it_too(self):
        s = self._slots("quest")
        s._after_reset("factory")
        self.assertIsNone(s._presets.active_preset)

    def test_saving_the_current_values_as_defaults_keeps_the_preset(self):
        """Shift+Click moves no dial, so the preset it was showing still holds."""
        s = self._slots("quest")
        s._after_reset("save")
        self.assertEqual(s._presets.active_preset, "quest")
        self.assertEqual(s._presets.refreshed, 0)

    def test_a_reset_before_the_combo_is_wired_is_a_no_op(self):
        s = LightmapBakerSlots.__new__(LightmapBakerSlots)
        s._after_reset("reset")  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
