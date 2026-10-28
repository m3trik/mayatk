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
import errno
import os
import json
import shutil
import tempfile
import unittest
import warnings
from unittest import mock

import base_test  # noqa: F401 — sys.path bootstrap for the sibling repos

import maya.cmds as cmds
import pythontk as ptk
from base_test import MayaTkTestCase
from mayatk.light_utils.lightmap_baker import lightmap_baker as lmb_module
from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.light_utils.lightmap_baker._probe_placement import ProbePlacement
from mayatk.light_utils._light_utils import LightUtils
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics
from mayatk.mat_utils.bake_sets import LightmapExcludeSet, LightmapIsolateSet


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


def _hidden_meshes():
    """The scene's mesh shapes switched off by their own ``visibility``."""
    return {
        shape
        for shape in cmds.ls(type="mesh", long=True, noIntermediate=True) or []
        if not cmds.getAttr(f"{shape}.visibility")
    }


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
        self.called_camera_shader = None
        self.called_batch = None
        self.called_claims = None
        self.called_region = None
        self.card_seen_at_bake = False
        self.card_color = None
        self.card_diffuse = None
        self.called_panorama = None
        self.hidden_at_bake = None
        # The lights switched on as the bake rendered (LightUtils.contributing_lights).
        self.lit_at_bake = None
        # Called with no arguments as the bake renders; what it returns is kept
        # (a test reads the scene mid-bake through it).
        self.at_bake = None
        self.seen_at_bake = None
        # One (objects, hidden meshes) per call: an isolated object's own pass.
        self.passes = []

    # The real default: an unsaved test scene has no project, and says so.
    default_output_dir = staticmethod(lmb_module.TextureBaker.default_output_dir)

    def render_panorama(self, position, path, width=1024, hide=None, lights=None):
        """A panorama as ``TextureBaker.render_panorama`` writes one: 2:1, every
        direction a hit (alpha 1), the call recorded -- with the lights that
        were on, and the meshes that were off, as it rendered."""
        self.called_panorama = {
            "position": [float(c) for c in position],
            "width": width,
            "hide": list(hide or []),
            "lights": list(lights or []),
            "lit": LightUtils.contributing_lights(),
            "hidden": _hidden_meshes(),
        }
        cv2, np = _cv2()
        cv2.imwrite(path, np.full((width // 2, width, 4), 0.5, np.float32))
        return path

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
        region=None,
        camera_shader=None,
    ):
        shader = shader or camera_shader  # the card, whichever way it rides
        self.called_camera_shader = camera_shader
        self.called_size = size
        self.called_region = region
        self.called_claims = claims
        self.called_uv_set = uv_set
        self.called_stem = stem
        self.called_on_progress = on_progress
        self.called_shader = shader
        self.called_batch = batch
        self.hidden_at_bake = _hidden_meshes()
        self.lit_at_bake = LightUtils.contributing_lights()
        if self.at_bake is not None:
            self.seen_at_bake = self.at_bake()
        self.passes.append(
            (sorted(cmds.ls(objects, long=True) or []), self.hidden_at_bake)
        )
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

    def test_an_island_running_to_the_frame_loses_its_outer_ring_too(self):
        """Reported on a production room: baseboard joints in the atlas read 7-23x
        their strip (925 in a scratch bake). The strip's layout runs to u/v 0
        and 1, so its end texels sit on the render frame, and the coverage
        erode -- there to drop the ring whose filter tail collects what lies
        past an island -- exempted the frame. The end is buried ~1 cm in the
        wall it meets: its row leaked the light (16787 at one sample), a ROW of
        spikes the denoiser's 3x3 median cannot see, stitched across the joint
        and dilated into the gutter. Beyond the frame is not island: the frame
        ring is eroded and continued from inside, as every border is."""
        cv2, np = _cv2()
        p = os.path.join(self.tmp, "frame.exr")
        img = np.zeros((16, 16, 4), np.float32)
        img[..., 3] = 1.0
        img[..., :3] = 1.6
        img[:, :5, :3] = 0.05  # a real contact shadow, 32x under the lit level
        img[15, :, :3] = 4000.0  # the buried end row, leaking
        cv2.imwrite(p, img)
        whole = [  # a layout filling the square, edge to edge
            [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
            [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        ]
        self.assertTrue(
            LightmapBaker._dilate_lightmap(
                p, alpha_threshold=0.05, iterations=8, uv_triangles=whole
            )
        )
        out = _read(p)
        self.assertLess(float(out[15].max()), 4.0)  # the leaking row continued
        self.assertAlmostEqual(float(out[15, 10, 0]), 1.6, delta=0.2)
        # The shadow beside the light is signal, however sharp the step.
        self.assertAlmostEqual(float(out[7, 2, 0]), 0.05, places=3)
        self.assertAlmostEqual(float(out[7, 10, 0]), 1.6, places=3)
        # An atlas tile carries the same: the frame ring is no longer its own.
        p2 = os.path.join(self.tmp, "frame_tile.exr")
        cv2.imwrite(p2, img)
        LightmapBaker._dilate_lightmap(
            p2,
            alpha_threshold=0.05,
            iterations=8,
            uv_triangles=whole,
            keep_coverage=True,
        )
        tile = _read(p2)
        self.assertEqual(float(tile[15, 10, 3]), 0.0)
        self.assertEqual(float(tile[7, 10, 3]), 1.0)
        self.assertLess(float(tile[15, :, :3].max()), 4.0)

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

    def test_coverage_mask_keeps_every_island_however_small(self):
        # The erosion (and the full-coverage cut) are per-TEXEL rules, and an
        # island under ~3 texels wide has nothing left after them. With any big
        # island surviving, the mask then dropped every small one, and the
        # refill painted them with whatever island was nearest: on the
        # production device (hundreds of key-sized islands at 1024) the
        # Arnold map lost the keys' lighting to 45-degree dilation blobs while
        # the Cycles bake kept it. Each island keeps its own best texels.
        _, np = _cv2()
        size = 64

        def quad(u0, u1, v0=0.25, v1=0.75):
            return [[(u0, v0), (u1, v0), (u1, v1)], [(u0, v0), (u1, v1), (u0, v1)]]

        big = quad(0.0, 1 / 3, 0.0, 1.0)
        strip = quad(40 / size, 42 / size)  # two full columns: erosion empties it
        sliver = quad(50.5 / size, 51.5 / size)  # no fully covered texel at all
        mask = LightmapBaker._coverage_mask(big + strip + sliver, (size, size))
        self.assertIsNotNone(mask)
        row = size // 2
        self.assertTrue(bool(mask[row, 8]), "the big island lost its interior")
        self.assertFalse(bool(mask[row, 21]), "the big island's border stays eroded")
        self.assertTrue(mask[row, 40:42].any(), "a 2-texel island vanished")
        self.assertTrue(mask[row, 50:52].any(), "a 1-texel island vanished")
        self.assertFalse(mask[row, 44:49].any(), "background joined the mask")

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

    def test_the_records_old_key_names_are_deprecated_aliases(self):
        """``LIGHTMAP_METADATA`` held the lightmaps record's key under its
        old name, on the records and the baker: retired, each warns and
        answers the declaration's. Added: 2026-10-20"""
        for owner in (LightmapRecords, LightmapBaker):
            with self.subTest(owner=owner.__name__):
                with self.assertWarns(DeprecationWarning):
                    key = owner.LIGHTMAP_METADATA
                self.assertEqual(key, ptk.SceneRecords.LIGHTMAPS.key)


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

    def test_an_excluded_object_still_shadows_the_objects_that_bake(self):
        """The Exclude set's promise, rendered: an excluded object gets no map,
        but it stays in the render. A box excluded over a floor darkens the
        floor's map where its shadow falls; with the box gone, it does not."""
        _, np = _cv2()
        floor = cmds.ls(
            cmds.polyPlane(name="exFloor", w=2, h=2, sx=1, sy=1)[0], long=True
        )[0]
        box = cmds.ls(cmds.polyCube(name="exBox", w=1, h=0.2, d=1)[0], long=True)[0]
        cmds.setAttr(f"{box}.translateY", 1.0)
        light = cmds.directionalLight(intensity=1.0)
        cmds.setAttr(f"{cmds.listRelatives(light, parent=True)[0]}.rotateX", -90)
        LightmapExcludeSet.define([box])

        def bake_floor():
            result = LightmapBaker(resolution=32, samples=2).bake(
                cmds.ls(floor, box), packing="per_object", output_dir=self.tmp
            )
            self.assertEqual(list(result.maps), [floor], result.refused)
            lum = _read(result.maps[floor]).astype(np.float64).mean(axis=2)
            return result, np.percentile(lum, 5), np.percentile(lum, 95)

        result, shadowed_dark, shadowed_lit = bake_floor()
        self.assertEqual(result.excluded, [box], "the Exclude set left the box out")

        cmds.delete(box)
        _, open_dark, open_lit = bake_floor()

        self.assertLess(
            shadowed_dark,
            0.5 * open_dark,
            f"no shadow from the excluded box: darkest texels {shadowed_dark:.4f} "
            f"with it, {open_dark:.4f} without",
        )
        # Where no shadow falls the two bakes agree: the box took light away
        # only where it stands between the light and the floor.
        self.assertAlmostEqual(shadowed_lit / open_lit, 1.0, delta=0.2)

    def test_what_the_bake_leaves_out_casts_no_shadow(self):
        """The converse, as production met it (2026-10-07): props on a cart,
        interactive and so never baked, shadowed the cart's map. A mesh that
        is not baked, not baked before and not in the Exclude set moves, so it
        is out of the render: the floor bakes as if the box were not there."""
        _, np = _cv2()
        floor = cmds.ls(
            cmds.polyPlane(name="mvFloor", w=2, h=2, sx=1, sy=1)[0], long=True
        )[0]
        box = cmds.ls(cmds.polyCube(name="mvBox", w=1, h=0.2, d=1)[0], long=True)[0]
        cmds.setAttr(f"{box}.translateY", 1.0)
        light = cmds.directionalLight(intensity=1.0)
        cmds.setAttr(f"{cmds.listRelatives(light, parent=True)[0]}.rotateX", -90)

        def bake_floor(folder):
            result = LightmapBaker(resolution=32, samples=2).bake(
                [floor], packing="per_object", output_dir=os.path.join(self.tmp, folder)
            )
            self.assertEqual(list(result.maps), [floor], result.refused)
            lum = _read(result.maps[floor]).astype(np.float64).mean(axis=2)
            return np.percentile(lum, 5)

        with_box = bake_floor("with")
        self.assertTrue(cmds.getAttr(f"{box}.visibility"), "handed back as found")
        self.assertTrue(
            all(
                cmds.getAttr(f"{s}.visibility")
                for s in cmds.listRelatives(box, shapes=True, fullPath=True)
            ),
            "handed back as found",
        )
        cmds.delete(box)
        without = bake_floor("without")
        self.assertAlmostEqual(
            with_box / without,
            1.0,
            delta=0.2,
            msg=f"the unbaked box shadowed the floor: darkest texels "
            f"{with_box:.4f} with it, {without:.4f} without",
        )

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

        # The wall bakes too: one the bake leaves out is out of the render, and
        # would take its bounce with it whatever the pin did.
        dark = LightmapBaker(
            resolution=32, samples=3, gi_depth=0, gi_samples=2
        ).bake_separated([floor, wall], output_dir=os.path.join(self.tmp, "d0"))
        red0 = float(_read(dark[floor_key])[..., 2].mean())
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
        # The card rides the bake as TextureBaker's camera_shader -- the bake's
        # own rays see it, every bounce the real materials (as the per-shape
        # -shader override, a concave object bounced light onto itself as if
        # it were white) -- the scene's assignments are NEVER touched and the
        # card is deleted afterward. Kd must
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
        self.assertTrue(fake.called_camera_shader, "the card rode as an override")
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
        recorded = LightmapRecords.commit({long: self.tex}, intensity=1.5)
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
        # The intensity stays on the marker; the record ships none: its
        # texels carry it, the map applied as stored (2026-10-20).
        self.assertNotIn("intensity", rec)
        self.assertEqual(LightmapRecords._marker_info(long)["intensity"], 1.5)
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
        # multiplier), so the bake scales it into the texels -- once per unique
        # file, even when several objects share one atlas.
        cv2, np = _cv2()
        shared = os.path.join(self.tmp, "shared_Lightmap.exr")
        cv2.imwrite(shared, np.full((4, 4, 3), 0.25, np.float32))

        LightmapBaker(resolution=64)._apply_intensity([shared, shared], 2.0)

        out = cv2.imread(shared, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        self.assertAlmostEqual(float(out.mean()), 0.5, places=3)  # x2, not x4

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

    def test_a_scoped_export_names_only_the_export_sets_objects(self):
        """The record is export-scoped: a marker outside the export set ships
        its map with nothing to bind it to. Measured 2026-10-09: a production
        FBX named five objects of a hidden TEMP group, the post-write embed
        shipped their outdated maps, and two it could not find failed the
        verifier. The scope is roots, descendants included; none is the
        scene. Added: 2026-10-09"""
        a, _, _ = self._cube_with_material("shipLeaf")
        cmds.group(a, name="SHIP_GRP")
        ship = cmds.ls("SHIP_GRP|shipLeaf", long=True)[0]
        _, _, stale = self._cube_with_material("staleLeaf")
        UvUtils.create_lightmap_uvs([ship, stale], map_size=64, quiet=True)
        LightmapBaker(resolution=64).commit_lightmap({ship: self.tex, stale: self.tex})

        def names(scope):
            record = LightmapRecords.export_record(ptk.ExportContext(scope=scope))
            return (
                sorted(o["name"] for o in record.payload["objects"]) if record else []
            )

        self.assertEqual(names(("|SHIP_GRP",)), ["shipLeaf"])
        self.assertEqual(names(None), ["shipLeaf", "staleLeaf"])
        self.assertEqual(names(()), [])

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

    @staticmethod
    def _commit_legacy(obj, path, rect):
        """Record *obj* as a pre-0.17 atlas pack left it: committed, with the
        squeeze it applied to the UVs recorded as the marker's ``uvRect`` (the
        legacy shape :meth:`LightmapRecords.migrate_legacy` reads)."""
        LightmapRecords.commit({obj: path})
        info = LightmapRecords._marker_info(obj)
        info["uvRect"] = [float(v) for v in rect]
        LightmapRecords._write_marker(LightmapRecords._marker_node(obj), info)

    @staticmethod
    def _panel(name, sg, x0, width=3.94, depth=1.31):
        """A wall-like panel spanning world x [x0, x0 + width], its lightmap = map1."""
        plane = cmds.polyPlane(
            name=name, width=width, height=depth, subdivisionsX=1, subdivisionsY=1
        )[0]
        cmds.move(x0 + width / 2.0, 0, 0, plane)
        cmds.makeIdentity(plane, apply=True, translate=True)
        shape = cmds.listRelatives(plane, shapes=True, fullPath=True)[0]
        cmds.sets(shape, edit=True, forceElement=sg)
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        cmds.addAttr(shape, longName=UvDiagnostics.LIGHTMAP_UV_TAG, dataType="string")
        cmds.setAttr(
            f"{shape}.{UvDiagnostics.LIGHTMAP_UV_TAG}", "lightmap", type="string"
        )
        return cmds.ls(plane, long=True)[0]

    @staticmethod
    def _uv_at(obj, x, z):
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(obj)
        fn = om.MFnMesh(sel.getDagPath(0).extendToShape())
        u, v, _face = fn.getUVAtPoint(om.MPoint(x, 0, z), om.MSpace.kWorld, "lightmap")
        return u, v

    def _panel_tile(self, obj, x0, width, field, render=256, noise=0.0, seed=0):
        """A tile of *field* (a function of world x) as _dilate_lightmap hands one on.

        The coverage erode drops the outermost ring and the refill copies its
        neighbour into it; *noise* is iid relative sampling noise per texel.
        """
        cv2, np = _cv2()
        u0, _v = self._uv_at(obj, x0 + 1e-3, 0.0)
        u1, _v = self._uv_at(obj, x0 + width - 1e-3, 0.0)
        centres = (np.arange(render) + 0.5) / render
        x = x0 + (centres - u0) / (u1 - u0) * width
        rgb = np.tile(field(x)[None, :], (render, 1)).astype(np.float32)
        if noise:
            rng = np.random.default_rng(seed)
            rgb *= (1.0 + noise * rng.standard_normal(rgb.shape)).astype(np.float32)
        alpha = np.ones((render, render), np.float32)
        alpha[[0, -1], :] = 0.0
        alpha[:, [0, -1]] = 0.0
        rgb[:, 0], rgb[:, -1] = rgb[:, 1], rgb[:, -2]
        path = os.path.join(self.tmp, f"{obj.rsplit('|', 1)[-1]}.exr")
        cv2.imwrite(path, np.dstack([rgb, rgb, rgb, alpha]))
        return path

    def _packed_reader(self, mapping, resolution=64):
        """Pack *mapping*; return ``read(obj, x, y, z)``: the atlas through obj's rect."""
        import maya.api.OpenMaya as om

        cv2, np = _cv2()
        packed = LightmapBaker(resolution=resolution).pack_atlas(
            mapping, output_dir=self.tmp
        )
        atlas = _read(next(iter(packed.values()))[0])[..., :3].astype(np.float32)
        h, w = atlas.shape[:2]

        def read(obj, x, y, z):
            sel = om.MSelectionList()
            sel.add(obj)
            fn = om.MFnMesh(sel.getDagPath(0).extendToShape())
            u, v, _f = fn.getUVAtPoint(om.MPoint(x, y, z), om.MSpace.kWorld, "lightmap")
            sx, sy, ox, oy = packed[obj][1]
            px = np.array([[(u * sx + ox) * w - 0.5]], np.float32)
            py = np.array([[(1.0 - (v * sy + oy)) * h - 0.5]], np.float32)
            return float(cv2.remap(atlas, px, py, cv2.INTER_LINEAR)[0, 0, 0])

        return read

    def _seam_steps(self, read, a, b, x, zs, y=0.0, eps=1e-4):
        return [
            abs(read(a, x - eps, y, z) - read(b, x + eps, y, z))
            / ((read(a, x - eps, y, z) + read(b, x + eps, y, z)) / 2)
            for z in zs
        ]

    def test_coplanar_panels_meet_without_a_step(self):
        """Two panels that share a 3D edge show ONE light level along it.

        The production room (PLAYGROUND, 2026-10-02) read as a patchwork: under a
        smooth light field each wall panel met its neighbour with a step (median
        5.4%, p90 12%). The pack put it there, independent of the bake: the
        cell's border ring, partly covered after the shrink, was refilled from
        the ring INSIDE it, the tile was resized edge to edge while its rect is
        published with its edges on border-texel CENTERS, and the denoise window
        an edge truncates averaged the light from inside -- each side read its
        lighting texels in from the edge. Synthetic tiles, no render: the light
        field is known, so any step at the seam is the pack's.
        """
        _cv2_, np = _cv2()
        sg, _mat = self._make_sg("seamPanels")
        width, sigma, hot = 3.94, 0.7, 3.94 + 0.35  # a fixture just past the edge

        def field(x):
            return 1.0 + 1.5 * np.exp(-((x - hot) ** 2) / (2 * sigma**2))

        panels = [self._panel(f"seamPanel{i}", sg, i * width) for i in range(2)]
        mapping = {
            obj: self._panel_tile(obj, i * width, width, field)
            for i, obj in enumerate(panels)
        }
        read = self._packed_reader(mapping)
        steps = self._seam_steps(read, *panels, width, np.linspace(-0.4, 0.4, 9) * 1.31)
        self.assertLess(
            max(steps),
            0.01,
            f"seam step {100 * max(steps):.1f}% between coplanar panels",
        )

    def test_noisy_coplanar_panels_read_one_value_along_their_edge(self):
        """Each panel is its own cell with its own sampling noise, so however well
        each is finished they disagree along the edge they share -- the seam is
        stitched: both sides read one value there."""
        _cv2_, np = _cv2()
        sg, _mat = self._make_sg("noisyPanels")
        width = 3.94
        panels = [self._panel(f"noisyPanel{i}", sg, i * width) for i in range(2)]
        mapping = {
            obj: self._panel_tile(
                obj, i * width, width, lambda x: 1.0 + 0.0 * x, noise=0.08, seed=i
            )
            for i, obj in enumerate(panels)
        }
        read = self._packed_reader(mapping)
        steps = self._seam_steps(
            read, *panels, width, np.linspace(-0.4, 0.4, 17) * 1.31
        )
        self.assertLess(max(steps), 0.01, f"seam step {100 * max(steps):.1f}%")

    def test_a_corner_is_not_stitched(self):
        """A wall meeting a floor shares an edge but not a plane: their light differs."""
        _cv2_, np = _cv2()
        sg, _mat = self._make_sg("cornerPanels")
        width = 3.94
        floor = self._panel("cornerFloor", sg, 0.0)
        wall = self._panel("cornerWall", sg, width)
        cmds.rotate(0, 0, 90, wall, pivot=(width, 0, 0), relative=True)
        cmds.makeIdentity(wall, apply=True, rotate=True)
        mapping = {
            floor: self._panel_tile(floor, 0.0, width, lambda x: 1.0 + 0.0 * x),
            wall: self._wall_tile(wall, 2.0),
        }
        read = self._packed_reader(mapping)
        for z in np.linspace(-0.4, 0.4, 5) * 1.31:
            self.assertAlmostEqual(read(floor, width - 1e-3, 0.0, z), 1.0, delta=0.02)
            self.assertAlmostEqual(read(wall, width, 1e-3, z), 2.0, delta=0.04)

    def test_a_panel_standing_in_front_is_not_stitched(self):
        """A baseboard runs along a wall's edge a little IN FRONT of it: coplanar
        in normal, never sharing the edge."""
        _cv2_, np = _cv2()
        sg, _mat = self._make_sg("frontPanels")
        width = 3.94
        back = self._panel("frontBack", sg, 0.0)
        front = self._panel("frontFront", sg, width)
        cmds.move(0, 0.02, 0, front, relative=True)
        cmds.makeIdentity(front, apply=True, translate=True)
        mapping = {
            back: self._panel_tile(back, 0.0, width, lambda x: 1.0 + 0.0 * x),
            front: self._panel_tile(front, width, width, lambda x: 2.0 + 0.0 * x),
        }
        read = self._packed_reader(mapping)
        for z in np.linspace(-0.4, 0.4, 5) * 1.31:
            self.assertAlmostEqual(read(back, width - 1e-3, 0.0, z), 1.0, delta=0.02)
            self.assertAlmostEqual(read(front, width + 1e-3, 0.02, z), 2.0, delta=0.04)

    def test_region_tiles_pack_onto_their_layout(self):
        """A tile rendered over only its island's region lands where the layout
        samples it, and two such coplanar panels still meet in one value."""
        cv2, np = _cv2()
        sg, _mat = self._make_sg("regionPanels")
        width, sigma, hot = 3.94, 0.7, 3.94 + 0.35

        def field(x):
            return 1.0 + 1.5 * np.exp(-((x - hot) ** 2) / (2 * sigma**2))

        panels = [self._panel(f"regionPanel{i}", sg, i * width) for i in range(2)]
        for obj in panels:  # the production walls: islands at u 0..1/3
            self._squeeze_lightmap_u(obj, 1.0 / 3.0)
        baker = LightmapBaker(resolution=64)
        plan = baker.atlas_plan(panels)
        bake_sizes = baker._plan_bake_sizes(baker.plan_sizes(plan))
        regions = baker._plan_regions(bake_sizes)
        self.assertEqual(set(regions), set(panels))
        mapping = {}
        for i, obj in enumerate(panels):
            u0, v0, u1, v1 = regions[obj]
            render = bake_sizes[obj]
            # What RTT writes for the region: column c samples layout u at
            # u0 + (u1 - u0) * (c + 0.5) / render (measured, mtoa 5.4.5).
            lu = u0 + (u1 - u0) * (np.arange(render) + 0.5) / render
            x = i * width + lu * 3.0 * width  # squeezed: layout u 1/3 spans the panel
            rgb = np.tile(field(x)[None, :], (render, 1)).astype(np.float32)
            inside = (lu > 0.0) & (lu < 1.0 / 3.0)
            alpha = np.tile(inside[None, :], (render, 1)).astype(np.float32)
            alpha[[0, -1], :] = 0.0
            path = os.path.join(self.tmp, f"regionPanel{i}.exr")
            cv2.imwrite(path, np.dstack([rgb, rgb, rgb, alpha]))
            mapping[obj] = path
        packed = baker.pack_atlas(
            mapping, output_dir=self.tmp, plan=plan, regions=regions
        )
        atlas = _read(next(iter(packed.values()))[0])[..., :3].astype(np.float32)
        h, w = atlas.shape[:2]

        def read(obj, x, z):
            import maya.api.OpenMaya as om

            sel = om.MSelectionList()
            sel.add(obj)
            fn = om.MFnMesh(sel.getDagPath(0).extendToShape())
            u, v, _f = fn.getUVAtPoint(om.MPoint(x, 0, z), om.MSpace.kWorld, "lightmap")
            sx, sy, ox, oy = packed[obj][1]
            px = np.array([[(u * sx + ox) * w - 0.5]], np.float32)
            py = np.array([[(1.0 - (v * sy + oy)) * h - 0.5]], np.float32)
            return float(cv2.remap(atlas, px, py, cv2.INTER_LINEAR)[0, 0, 0])

        for i, obj in enumerate(panels):
            for x in np.linspace(0.25, 0.75, 5) * width + i * width:
                self.assertAlmostEqual(read(obj, x, 0.0) / field(x), 1.0, delta=0.04)
        steps = [
            abs(read(panels[0], width - 1e-4, z) - read(panels[1], width + 1e-4, z))
            for z in np.linspace(-0.4, 0.4, 9) * 1.31
        ]
        self.assertLess(max(steps) / field(width), 0.01)

    def test_a_panel_lying_on_another_along_its_border_is_not_stitched(self):
        """A decal along a panel's border shares the edge's LINE, not the edge:
        both faces lie on the same side of it."""
        _cv2_, np = _cv2()
        sg, _mat = self._make_sg("decalPanels")
        width = 3.94
        base = self._panel("decalBase", sg, 0.0)
        decal = self._panel("decalTop", sg, width / 2.0, width=width / 2.0)
        mapping = {
            base: self._wall_tile(base, 1.0),
            decal: self._wall_tile(decal, 2.0),
        }
        read = self._packed_reader(mapping)
        for z in np.linspace(-0.4, 0.4, 5) * 1.31:
            self.assertAlmostEqual(read(base, width - 1e-3, 0.0, z), 1.0, delta=0.02)
            self.assertAlmostEqual(read(decal, width - 1e-3, 0.0, z), 2.0, delta=0.04)

    def _wall_tile(self, obj, value, render=256):
        """A uniform tile (the coverage-eroded shape _dilate_lightmap hands on)."""
        cv2, np = _cv2()
        rgb = np.full((render, render), value, np.float32)
        alpha = np.ones((render, render), np.float32)
        alpha[[0, -1], :] = 0.0
        alpha[:, [0, -1]] = 0.0
        path = os.path.join(self.tmp, f"{obj.rsplit('|', 1)[-1]}.exr")
        cv2.imwrite(path, np.dstack([rgb, rgb, rgb, alpha]))
        return path

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
        # Two materials share a texture set (same stem) but are different
        # groups. The multi-object group's atlas name must not overwrite the
        # single-object group's not-yet-consumed source map (which is
        # deliberately named to collide). Regression. N differs in a setting
        # (diffuse), so it is no duplicate of M: a duplicate is one atlas group
        # with what it duplicates (_atlas_keys) and would share M's atlas.
        sgM, _ = self._make_sg("DupM")
        sgN, matN = self._make_sg("DupN")
        cmds.setAttr(f"{matN}.diffuse", 0.5)
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
        baker = LightmapBaker(resolution=64)
        out = baker.pack_atlas(mapping, output_dir=self.tmp)
        cells = dict(next(iter(baker.atlas_plan([a, b]).values())))
        atlas = cv2.imread(out[a][0], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        h, w = atlas.shape[:2]

        for obj in (a, b):
            _path, (sx, sy, ox, oy) = out[obj]
            # The crop engaged: the 1/3-wide island spans its WHOLE cell (to
            # the border-texel centres the rect is published at), not a third
            # of a cell left two-thirds dead.
            self.assertAlmostEqual(
                sx * frac,
                cells[obj][0],
                delta=2.0 / w,
                msg=f"{obj}: rect not crop-composed",
            )
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

    def test_a_failed_group_keeps_its_maps_in_the_output_folder_on_their_regions(self):
        """A group that cannot be packed falls back to its per-object maps:
        each lands in the output folder (copied, with keep_sources), so it
        outlives the caller's staging, and is bound through its region -- a
        map cut from part of a layout (Texture Combine's re-pack) is sampled
        there, not across the whole layout. Both were handed back as they
        came: a path into a folder swept on exit, with the identity rect."""
        cv2, np = _cv2()
        sg, _ = self._make_sg("failGroup")
        a = self._cube_on_sg("failGroupA", sg)
        b = self._cube_on_sg("failGroupB", sg)
        dest = os.path.join(self.tmp, "out")
        regions = {a: (0.0, 0.0, 0.5, 0.5), b: (0.25, 0.5, 1.25, 1.0)}
        baker = LightmapBaker(resolution=32)
        with ptk.TempArtifacts("lm_fail_group", policy="scoped") as staging:
            mapping = {}
            for obj, value in ((a, 0.5), (b, 0.25)):
                path = staging.path(extension=".exr")
                cv2.imwrite(path, np.full((8, 8, 3), value, np.float32))
                mapping[obj] = path
            with mock.patch.object(
                LightmapBaker, "_pack_group", side_effect=RuntimeError("boom")
            ):
                out = baker.pack_atlas(
                    mapping, output_dir=dest, keep_sources=True, regions=regions
                )
        self.assertEqual(set(out), {a, b})
        for obj, (path, rect) in out.items():
            self.assertTrue(os.path.isfile(path), f"{obj}: {path} was swept")
            self.assertEqual(
                os.path.normcase(os.path.dirname(path)), os.path.normcase(dest)
            )
            # The region's corners land on the map's own 0-1 corners.
            u0, v0, u1, v1 = regions[obj]
            sx, sy, ox, oy = rect
            for got, want in (
                (u0 * sx + ox, 0.0),
                (u1 * sx + ox, 1.0),
                (v0 * sy + oy, 0.0),
                (v1 * sy + oy, 1.0),
            ):
                self.assertAlmostEqual(got, want, places=6, msg=f"{obj} {rect}")
        # Without keep_sources the map is moved -- once, for every object
        # reading it -- and a map with no region keeps the identity rect.
        src = self._solid_exr("failGroupMoved.exr", 0.5)
        with mock.patch.object(
            LightmapBaker, "_pack_group", side_effect=RuntimeError("boom")
        ):
            out = baker.pack_atlas({a: src, b: src}, output_dir=dest)
        self.assertEqual(set(out), {a, b})
        self.assertEqual(out[a][0], out[b][0])
        path, rect = out[a]
        self.assertEqual(
            os.path.normcase(os.path.dirname(path)), os.path.normcase(dest)
        )
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(os.path.exists(src))
        self.assertEqual(rect, [1.0, 1.0, 0.0, 0.0])

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
        # Current packs never write a uvRect; this pins the old scenes' path.
        from mayatk.node_utils.data_nodes import DataNodes

        sg, _ = self._make_sg("RectC")
        a = self._cube_on_sg("rectC", sg)
        rect = [0.5, 0.5, 0.25, 0.25]
        self._commit_legacy(a, self._solid_exr("rectC.exr", (1, 1, 1)), rect)
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
        self._commit_legacy(a, self._solid_exr("revU.exr", (1, 1, 1)), rect)
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
        rect = [0.25, 0.25, 0.5, 0.5]
        shape = cmds.listRelatives(a, shapes=True, fullPath=True)[0]
        lm = UvDiagnostics.find_lightmap_uv_set(shape)
        LightmapRecords._transform_lightmap_uvs(shape, lm, rect)
        self._commit_legacy(a, self._solid_exr("guard.exr", (1, 1, 1)), rect)
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

    def test_off_skips_only_the_denoise(self):
        """Off means no denoise -- the tile still lands in its cell the way its
        rect is published. That placement is not a quality knob: the old off-path
        (the tile handed back whole for the assembler's edge-to-edge resize) is
        the one that put a step on every shared panel edge (2026-10-02)."""
        _cv2_, np = _cv2()
        img, _island = self._map()
        expected, covered = ptk.ImgUtils.resize_into_cell(
            img[..., :3], (16, 16), coverage=img[..., 3], edge_centers=True
        )
        own = covered > LightmapBaker._COVERAGE_OWN
        off = LightmapBaker(resolution=64, denoise=False)
        got = off._finish_tile(img, (16, 16), edge_centers=True)
        np.testing.assert_allclose(got[own], expected[own], rtol=1e-6)
        on = LightmapBaker(resolution=64)._finish_tile(img, (16, 16), edge_centers=True)
        self.assertFalse(np.allclose(on[own], expected[own]), "the denoise must act")
        # A map without coverage (finished at its own size) is only resampled.
        rgb = img[..., :3].copy()
        np.testing.assert_allclose(
            LightmapBaker(resolution=64)._finish_tile(rgb, (16, 16)),
            ptk.ImgUtils.resize_into_cell(rgb, (16, 16), edge_centers=False)[0],
            rtol=1e-6,
        )
        np.testing.assert_array_equal(
            LightmapBaker(resolution=64)._finish_tile(rgb, (64, 64)), rgb
        )

    def test_the_preset_carries_the_setting(self):
        self.assertTrue(LightmapBaker.from_preset("mobile").denoise)
        self.assertFalse(LightmapBaker.from_preset("mobile", denoise=False).denoise)


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
        # from_preset("mobile", device="GPU") built a baker on the scene's own
        # device and said nothing.
        baker = LightmapBaker.from_preset(
            "mobile", device="CPU", include_environment=False
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

    def test_a_baker_made_here_defaults_to_auto(self):
        """The scene's own device was the CPU on the production office (saved so),
        and a scripted bake ran 48 minutes for 9 of its 49 objects while the panel,
        defaulting to AUTO, baked all 49 on the GPU in 17. "SCENE" still asks for
        the scene's own.
        Added: 2026-10-01
        """
        self.assertEqual(LightmapBaker().device, "AUTO")
        self.assertEqual(LightmapBaker.from_preset("mobile").device, "AUTO")
        self.assertEqual(LightmapBaker(device="SCENE").baker._device_settings(), {})

    def test_the_device_reaches_the_bake_primitive(self):
        baker = LightmapBaker(device="GPU")
        self.assertEqual(baker.baker.device, "GPU")
        baker.device = "CPU"
        self.assertEqual(baker.baker.device, "CPU")


class TestIncludeSceneLighting(MayaTkTestCase):
    """Which of the scene's lights light a bake -- the same three switches in
    mayatk, blendertk and the Blender bridge's bake.

    * **Include Scene Lighting** (on): every scene light, wherever it sits.
      Off: only the lights in the objects handed to the bake or under them;
      the rest are switched off for the bake and handed back as found.
    * **Include Environment** (the HDRI, on): the sky dome, by its own switch
      alone -- outside the Scope or not, it is the world.
    * **Include Hidden Lights** (off): the hidden lights of that set are
      switched on for the bake -- a light under a hidden group too, without
      showing anything else the group hides -- and switched back off after.

    The fake backend records the lights that were ON as it rendered: an Arnold
    bake in mayapy renders a watermark, so the lights at render time, not the
    pixels, are what is measured here. Twin of blendertk's.
    """

    def setUp(self):
        super().setUp()
        self.tmp = self.enterContext(
            ptk.TempArtifacts("lm_scene_lights", policy="scoped")
        ).dir_path()

    @staticmethod
    def _light(name, parent=None, intensity=10.0):
        """A point light named *name* (under *parent*): ``(transform, shape)``."""
        transform = cmds.listRelatives(
            cmds.pointLight(name=name), parent=True, fullPath=True
        )[0]
        if parent:
            transform = cmds.parent(transform, parent)[0]
        transform = cmds.ls(transform, long=True)[0]
        shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        cmds.setAttr(f"{shape}.intensity", intensity)
        return transform, shape

    @staticmethod
    def _module(name):
        """A group holding a cube: ``(group, cube)`` long names."""
        group = cmds.group(empty=True, name=name)
        cube = cmds.parent(cmds.polyCube(name=f"{name}Cube")[0], group)[0]
        return cmds.ls(group, long=True)[0], cmds.ls(cube, long=True)[0]

    def _bake(self, objects, fake=None, **switches):
        fake = fake or _FakeBaker()
        baker = LightmapBaker(
            resolution=32, baker=fake, reflection_probe=False, **switches
        )
        result = baker.bake(objects, packing="per_object", output_dir=self.tmp)
        return result, fake

    def test_the_switches_default_to_every_light_and_no_hidden_one(self):
        baker = LightmapBaker()
        self.assertIs(baker.include_scene_lights, True)
        self.assertIs(baker.include_hidden_lights, False)
        self.assertIs(baker.include_environment, True)

    def test_every_scene_light_lights_the_bake_by_default(self):
        _group, cube = self._module("slDefault")
        _t, outside = self._light("slOutside")
        result, fake = self._bake([cube])
        self.assertTrue(result)
        self.assertIn(outside, fake.lit_at_bake)

    def test_off_switches_a_light_outside_the_scope_off_and_restores_it(self):
        group, _cube = self._module("slModule")
        _t, inside = self._light("slInside", parent=group)
        out_t, outside = self._light("slOutsideOff")
        result, fake = self._bake([group], include_scene_lights=False)
        self.assertTrue(result)
        self.assertIn(inside, fake.lit_at_bake, "the Scope's own light")
        self.assertNotIn(outside, fake.lit_at_bake, "switched off for the bake")
        self.assertTrue(cmds.getAttr(f"{out_t}.visibility"), "handed back as found")

    def test_a_light_handed_to_the_bake_is_in_its_scope(self):
        """``TextureBaker.resolve_meshes`` drops lights from the targets, so the
        Scope's lights are read off what the bake was HANDED, not its meshes."""
        _group, cube = self._module("slHanded")
        light_t, handed = self._light("slHandedLight")
        _o, other = self._light("slNotHanded")
        _result, fake = self._bake([cube, light_t], include_scene_lights=False)
        self.assertIn(handed, fake.lit_at_bake)
        self.assertNotIn(other, fake.lit_at_bake)

    @unittest.skipUnless(_arnold_loadable(), "mtoa unavailable")
    def test_the_environment_answers_only_to_its_own_switch(self):
        _group, cube = self._module("slWorld")
        dome = cmds.ls(
            cmds.createNode("aiSkyDomeLight", name="slDomeShape"), long=True
        )[0]
        _r, fake = self._bake([cube], include_scene_lights=False)
        self.assertIn(dome, fake.lit_at_bake, "outside the Scope, still the world")
        _r, fake = self._bake([cube], include_environment=False)
        self.assertNotIn(dome, fake.lit_at_bake)

    def test_hidden_lights_are_switched_on_for_the_bake_and_back_off_after(self):
        """Its own flag, or a hidden group's: visibility is INHERITED. The group
        is shown for the bake, so what else it hides is switched off with it."""
        from mayatk.display_utils._display_utils import DisplayUtils

        _group, cube = self._module("slHidden")
        own_t, own = self._light("slOwnHidden")
        cmds.setAttr(f"{own_t}.visibility", False)
        rig = cmds.ls(cmds.group(empty=True, name="slHiddenRig"), long=True)[0]
        _g, grouped = self._light("slGrouped", parent=rig)
        prop = cmds.ls(
            cmds.parent(cmds.polyCube(name="slRigProp")[0], rig)[0], long=True
        )[0]
        cmds.setAttr(f"{rig}.visibility", False)
        fake = _FakeBaker()
        fake.at_bake = lambda: DisplayUtils.is_visible(prop)

        result, fake = self._bake([cube], fake=fake, include_hidden_lights=True)

        self.assertTrue(result, "not refused: the hidden lights light it")
        self.assertIn(own, fake.lit_at_bake)
        self.assertIn(grouped, fake.lit_at_bake)
        self.assertFalse(fake.seen_at_bake, "what the group hides stays hidden")
        self.assertFalse(cmds.getAttr(f"{own_t}.visibility"), "handed back hidden")
        self.assertFalse(cmds.getAttr(f"{rig}.visibility"))
        self.assertTrue(cmds.getAttr(f"{prop}.visibility"), "its own flag untouched")

    def test_hidden_lights_stay_off_by_default(self):
        _group, cube = self._module("slStaysOff")
        _v, visible = self._light("slVisible")
        hidden_t, hidden = self._light("slStaysHidden")
        cmds.setAttr(f"{hidden_t}.visibility", False)
        _result, fake = self._bake([cube])
        self.assertIn(visible, fake.lit_at_bake)
        self.assertNotIn(hidden, fake.lit_at_bake)

    def test_only_the_scopes_hidden_lights_are_switched_on_with_the_master_off(self):
        group, _cube = self._module("slScopeHidden")
        in_t, inside = self._light("slScopeHiddenIn", parent=group)
        cmds.setAttr(f"{in_t}.visibility", False)
        out_t, outside = self._light("slScopeHiddenOut")
        cmds.setAttr(f"{out_t}.visibility", False)
        _r, fake = self._bake(
            [group], include_scene_lights=False, include_hidden_lights=True
        )
        self.assertIn(inside, fake.lit_at_bake)
        self.assertNotIn(outside, fake.lit_at_bake)

    def test_every_light_hidden_bakes_when_hidden_lights_are_on(self):
        """The refusal reads the set the bake renders: a hidden light it will
        switch on is a light."""
        light_t, _shape = self._light("slOnlyHidden")
        cmds.setAttr(f"{light_t}.visibility", False)
        refused = LightmapBaker(baker=_FakeBaker()).preflight()
        self.assertIn("hidden", (refused or "").lower())
        self.assertIsNone(
            LightmapBaker(baker=_FakeBaker(), include_hidden_lights=True).preflight()
        )

    def test_with_the_master_off_the_refusal_reads_the_scopes_lights(self):
        """A visible light outside the Scope is switched off for the bake, so it
        cannot stand in for the Scope's hidden one -- and with the master on it
        does light the bake."""
        group, _cube = self._module("slRefuse")
        in_t, _inside = self._light("slRefuseIn", parent=group)
        cmds.setAttr(f"{in_t}.visibility", False)
        self._light("slRefuseOut")
        self.assertIsNone(LightmapBaker(baker=_FakeBaker()).preflight([group]))
        off = LightmapBaker(baker=_FakeBaker(), include_scene_lights=False)
        with self.assertLogs(off.logger, level="WARNING") as caught:
            refused = off.preflight([group])
        self.assertIn("hidden", (refused or "").lower())
        audit = "\n".join(caught.output)
        self.assertIn("slRefuseIn:", audit, "the audit lists the bake's lights")
        self.assertNotIn("slRefuseOut:", audit, "not the ones it leaves out")

    def test_a_hidden_light_that_cannot_be_switched_on_is_named_and_left(self):
        _group, cube = self._module("slLocked")
        self._light("slLockedLit")
        locked_t, locked = self._light("slLockedLight")
        cmds.setAttr(f"{locked_t}.visibility", False, lock=True)
        fake = _FakeBaker()
        baker = LightmapBaker(
            resolution=32,
            baker=fake,
            reflection_probe=False,
            include_hidden_lights=True,
        )
        with self.assertLogs(baker.logger, level="WARNING") as caught:
            result = baker.bake([cube], packing="per_object", output_dir=self.tmp)
        self.assertTrue(result, "a light it cannot switch on never fails the bake")
        self.assertNotIn(locked, fake.lit_at_bake)
        self.assertIn("slLockedLight", "\n".join(caught.output))
        self.assertFalse(cmds.getAttr(f"{locked_t}.visibility"))

    def test_a_light_its_group_cannot_open_for_switches_nothing_else_off(self):
        """A hidden group holding a child whose visibility is locked is never
        opened for its light -- and then nothing else in it is switched off
        either: the children the opening would have hidden were, for a light
        left off."""
        _group, cube = self._module("slBlocked")
        self._light("slBlockedLit")
        rig = cmds.ls(cmds.group(empty=True, name="slBlockedRig"), long=True)[0]
        _g, grouped = self._light("slBlockedGrouped", parent=rig)
        locked = cmds.ls(
            cmds.parent(cmds.polyCube(name="slBlockedLocked")[0], rig)[0], long=True
        )[0]
        cmds.setAttr(f"{locked}.visibility", lock=True)
        free = cmds.ls(
            cmds.parent(cmds.polyCube(name="slBlockedFree")[0], rig)[0], long=True
        )[0]
        cmds.setAttr(f"{rig}.visibility", False)
        fake = _FakeBaker()
        fake.at_bake = lambda: cmds.getAttr(f"{free}.visibility")

        result, fake = self._bake([cube], fake=fake, include_hidden_lights=True)

        self.assertTrue(result)
        self.assertNotIn(grouped, fake.lit_at_bake)
        self.assertTrue(fake.seen_at_bake, "switched off for a light left off")

    def test_a_failed_bake_hands_every_light_back(self):
        class _Boom(_FakeBaker):
            def bake(self, *args, **kwargs):
                raise RuntimeError("bake blew up")

        group, _cube = self._module("slBoom")
        out_t, _outside = self._light("slBoomOut")
        hid_t, _hidden = self._light("slBoomHidden", parent=group)
        cmds.setAttr(f"{hid_t}.visibility", False)
        with self.assertRaises(RuntimeError):
            self._bake(
                [group],
                fake=_Boom(),
                include_scene_lights=False,
                include_hidden_lights=True,
            )
        self.assertTrue(cmds.getAttr(f"{out_t}.visibility"))
        self.assertFalse(cmds.getAttr(f"{hid_t}.visibility"))

    @unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
    def test_the_reflection_probe_renders_the_bakes_lights(self):
        room, table, _prop = TestReflectionProbe._room(self)
        group = cmds.ls(cmds.group(room, table, name="slProbeRoom"), long=True)[0]
        _i, inside = self._light("slProbeIn", parent=group)
        _o, outside = self._light("slProbeOut")
        fake = _FakeBaker()
        LightmapBaker(resolution=1024, baker=fake, include_scene_lights=False).bake(
            [group], packing="per_object", output_dir=self.tmp
        )
        self.assertIsNotNone(fake.called_panorama, "the bake captured no probe")
        self.assertIn(inside, fake.called_panorama["lit"])
        self.assertNotIn(outside, fake.called_panorama["lit"])

    # -- a bake re-bakes and ships the way it was lit ------------------------
    def _scoped(self, fake):
        """A Scope-lit baker (Include Scene Lighting off) over *fake*."""
        return LightmapBaker(
            resolution=32,
            baker=fake,
            reflection_probe=False,
            include_scene_lights=False,
        )

    def test_changed_relights_the_way_the_bake_lit_and_settles(self):
        """A loose light handed beside the cubes is the Scope's own. The dry
        run reads that light -- not the lights under what is baked -- a
        Changed re-bake of one cube is lit by it alone, and a second Changed
        finds nothing. Before, the dry run called the room's lighting changed
        straight after the bake, and the re-bake lit by the lights under its
        targets: none."""
        _ga, cube_a = self._module("slSettleA")
        gb, cube_b = self._module("slSettleB")
        cmds.move(10, 0, 0, gb)
        lamp_t, lamp = self._light("slSettleLamp")
        _o, other = self._light("slSettleOther")
        fake = _FakeBaker()
        baker = self._scoped(fake)
        baker.bake([cube_a, cube_b, lamp_t], packing="per_object", output_dir=self.tmp)
        rule = next(iter(LightmapRecords.bake_state()["lightings"].values()))["@rule"]
        self.assertEqual(
            rule,
            {
                "include_scene_lights": False,
                "include_environment": True,
                "include_hidden_lights": False,
                "scope": [lamp_t],
            },
            "the rule the bake lit by (blendertk records the same shape)",
        )
        report = baker.stale_report()
        self.assertEqual(report.targets, (), report.lines())

        # Changed from a panel whose switch was flipped since: the recorded
        # rule lights the re-bake, not the baker's own switches.
        changed = LightmapBaker(resolution=32, baker=fake, reflection_probe=False)
        self.assertEqual(changed.stale_report().targets, ())
        cmds.move(0, 0.5, 0, cube_a, relative=True)
        first = changed.rebake_changed(output_dir=self.tmp)
        self.assertEqual(list(first.maps), [cube_a], first.refused)
        self.assertIn(lamp, fake.lit_at_bake, "re-lit by the light the bake used")
        self.assertNotIn(other, fake.lit_at_bake)
        self.assertIs(changed.include_scene_lights, True, "its switch handed back")
        second = changed.rebake_changed(output_dir=self.tmp)
        self.assertEqual(second.maps, {}, second.report.lines())
        self.assertEqual(baker.stale_report().targets, ())

    def test_changed_reads_each_bakes_own_lights(self):
        """Two modules baked apart, each lit by its own lamp: a change to one
        lamp is a lighting change for what it lit alone, and so is a rename
        (the rule names its lights)."""
        ga, cube_a = self._module("slOwnA")
        gb, cube_b = self._module("slOwnB")
        cmds.move(10, 0, 0, gb)
        _ta, lamp_a = self._light("slOwnLampA", parent=ga)
        tb, _lamp_b = self._light("slOwnLampB", parent=gb)
        baker = self._scoped(_FakeBaker())
        baker.bake([ga], packing="per_object", output_dir=self.tmp)
        baker.bake([gb], packing="per_object", output_dir=self.tmp)
        self.assertEqual(baker.stale_report().targets, ())
        cmds.setAttr(f"{lamp_a}.intensity", 20.0)
        report = baker.stale_report()
        self.assertEqual(report.targets, (cube_a,), report.lines())
        cmds.setAttr(f"{lamp_a}.intensity", 10.0)
        cmds.rename(tb, "slOwnLampB2")
        report = baker.stale_report()
        self.assertEqual(report.targets, (cube_b,), report.lines())

    def test_the_manifest_ships_the_lights_the_bake_used(self):
        """Its ``lights`` (drawn into the GLB's probe copies as highlights) are
        exactly the bake's: not a light outside the Scope, and a hidden light
        Include Hidden Lights switched on for the bake -- hidden again when the
        manifest is written."""
        group, _cube = self._module("slShips")
        in_t, _inside = self._light("slShipsIn", parent=group)
        hid_t, _hidden = self._light("slShipsHidden", parent=group)
        cmds.setAttr(f"{hid_t}.visibility", False)
        out_t, _outside = self._light("slShipsOut")
        # Each at its own place: the record names no light (a light's name is
        # the scene's bookkeeping, which no reader of the record used), so a
        # shipped light is told by where it stands. Updated: 2026-10-20
        for transform, x in ((in_t, 10.0), (hid_t, 20.0), (out_t, 30.0)):
            cmds.xform(transform, worldSpace=True, translation=(x, 0.0, 0.0))
        result, _fake = self._bake(
            [group], include_scene_lights=False, include_hidden_lights=True
        )
        self.assertTrue(result.maps, result.refused)
        shipped = LightmapRecords._record().payload.get("lights", [])
        self.assertFalse(any("name" in light for light in shipped), shipped)
        self.assertEqual(
            sorted(round(light["position"][0]) for light in shipped), [10, 20]
        )
        self.assertFalse(cmds.getAttr(f"{hid_t}.visibility"), "handed back hidden")


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

    @staticmethod
    def _gap(a, b):
        """Texels between two (row0, row1, col0, col1) cells; <= 0 when they touch."""
        ar0, ar1, ac0, ac1 = a
        br0, br1, bc0, bc1 = b
        return max(bc0 - ac1, ac0 - bc1, br0 - ar1, ar0 - br1)

    def test_every_cell_keeps_its_gutter_at_a_low_resolution(self):
        """Reported: at a lower resolution the atlas's cells overlap. The tiled
        layout insets each cell by a gutter its cell can afford, so small cells
        met their neighbours with none (bilinear taps and every mip read the
        next object's light), and a cell under a texel was never placed while
        its object still sampled the atlas there."""
        sg = self._sg("planGutter")
        cubes = [
            self._cube(f"planGutter{i}", sg, size=0.2 + (i % 7) ** 2 * 0.35)
            for i in range(36)
        ]
        baker = LightmapBaker(resolution=256)
        plan = baker.atlas_plan(cubes)
        entries = plan[list(plan)[0]]
        cells = ptk.ImgUtils.atlas_pixel_rects(
            [rect for _n, rect in entries], baker.resolution
        )
        gutter = baker._atlas_gutter()
        for i, (row0, row1, col0, col1) in enumerate(cells):
            self.assertGreaterEqual(min(row1 - row0, col1 - col0), 2, cells[i])
            for j in range(i):
                self.assertGreaterEqual(
                    self._gap(cells[i], cells[j]), 2 * gutter, (cells[i], cells[j])
                )

    def test_copies_of_one_mesh_take_equal_cells_in_name_order(self):
        """Reported on a production room: baseboards traded lighting between bakes.
        Copies of one mesh are one size, but Maya measures their areas in
        single precision (rotated copies of one baseboard: 0.8500002 to
        0.8499908), and the tiled layout ordered cells by that noise -- the
        same room laid its eight baseboards out differently in two bakes.
        Equal copies must take equal cells, in name order, every time."""
        sg = self._sg("planCopies")
        plane = cmds.polyPlane(name="planCopyA", w=5, h=0.17, sx=1, sy=1)[0]
        copies = [cmds.ls(plane, long=True)[0]]
        for name, angle in (("planCopyB", 90), ("planCopyC", 180), ("planCopyD", 270)):
            node = cmds.instance(plane, name=name)[0]
            cmds.xform(node, worldSpace=True, rotation=(0, angle, 0))
            copies.append(cmds.ls(node, long=True)[0])
        # Every copy's own path: a shared shape assigned once binds one copy.
        cmds.sets(copies, edit=True, forceElement=sg)
        other = self._cube("planCopyOther", sg, size=2.0)
        baker = LightmapBaker(resolution=512)
        plan = baker.atlas_plan(copies + [other])
        rects = dict(plan[list(plan)[0]])
        cells = dict(
            zip(copies, ptk.ImgUtils.atlas_pixel_rects([rects[c] for c in copies], 512))
        )
        sizes = {(r1 - r0, c1 - c0) for r0, r1, c0, c1 in cells.values()}
        self.assertEqual(len(sizes), 1, cells)
        reading = sorted(copies, key=lambda c: (cells[c][0], cells[c][2]))
        self.assertEqual(reading, sorted(copies))

    def _plan_cell_aspect(self, obj, others):
        """Width over height of *obj*'s planned cell, packed beside *others*."""
        baker = LightmapBaker(resolution=512)
        plan = baker.atlas_plan([obj] + others)
        rect = dict(plan[list(plan)[0]])[obj]
        ((row0, row1, col0, col1),) = ptk.ImgUtils.atlas_pixel_rects([rect], 512)
        return (col1 - col0) / (row1 - row0), baker

    def test_a_cell_takes_its_surfaces_shape(self):
        """A layout spanning a quarter of its square's width is placed in a
        cell of that box's shape ON THE SURFACE: a cell bent to tile the square
        gives one object two texel densities, and the cell is only as good as
        the coarser (a production floor: 0.6 x 1.2 cm per texel). Here the
        quarter-width layout is the cube's own, squeezed 4x across u, so its
        box measures four times wider on the surface than in uv."""
        sg = self._sg("planAspect")
        strip = self._cube("planAspectStrip", sg, size=2.0)
        shape = cmds.listRelatives(strip, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        cmds.polyAutoProjection(
            f"{shape}.f[*]", uvSetName="lightmap", layout=2, scaleMode=1, ch=False
        )
        cmds.polyEditUV(
            f"{shape}.map[*]",
            uvSetName="lightmap",
            pivotU=0,
            pivotV=0,
            scaleU=0.25,
            scaleV=1.0,
        )
        others = [self._cube(f"planAspect{i}", sg, size=1.0) for i in range(3)]
        aspect, baker = self._plan_cell_aspect(strip, others)
        bbox = baker._lightmap_uv_bbox(strip)
        expected = 4.0 * (bbox[2] - bbox[0]) / (bbox[3] - bbox[1])
        self.assertAlmostEqual(aspect, expected, delta=0.08 * expected)

    def test_a_stretched_layout_gets_a_cell_of_its_surfaces_shape(self):
        """Reported on a production room: every wall and baseboard's lightmap layout
        fills its square whatever its shape (WALL_N 131 x 394 cm, BASEBOARD_A
        13 x 395 cm), so a square cell shipped their stretch -- 6 texels/cm up a
        baseboard and 0.2 along it, where the cabinets' shadows fall. A 4 x 1
        panel laid out over the whole square gets a 4:1 cell."""
        sg = self._sg("planStretch")
        panel = cmds.polyPlane(name="planStretchPanel", w=4, h=1, sx=1, sy=1)[0]
        panel = cmds.ls(panel, long=True)[0]
        cmds.sets(panel, edit=True, forceElement=sg)
        shape = cmds.listRelatives(panel, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        # The panel's own uvs fill the square along u (its 4-unit side) and v.
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="lightmap")
        coords = cmds.polyEditUV(f"{shape}.map[*]", query=True)
        us, vs = coords[0::2], coords[1::2]
        for i, (u, v) in enumerate(zip(us, vs)):
            cmds.polyEditUV(
                f"{shape}.map[{i}]",
                relative=False,
                uValue=(u - min(us)) / (max(us) - min(us)),
                vValue=(v - min(vs)) / (max(vs) - min(vs)),
            )
        others = [self._cube(f"planStretch{i}", sg, size=1.0) for i in range(3)]
        aspect, _baker = self._plan_cell_aspect(panel, others)
        self.assertAlmostEqual(aspect, 4.0, delta=0.3)

    def test_bake_size_is_quantized_and_capped(self):
        baker = LightmapBaker(resolution=2048)
        quantum = LightmapBaker._ATLAS_BAKE_QUANTUM
        with mock.patch.object(LightmapBaker, "_lightmap_uv_bbox", return_value=None):
            sizes = baker._plan_bake_sizes({"a": (100, 60), "b": (1024, 1024)})
        self.assertEqual(sizes["a"] % quantum, 0)
        self.assertGreaterEqual(sizes["a"], 100)  # the longer axis, rounded up
        self.assertLess(sizes["a"], baker.resolution)
        self.assertEqual(sizes["b"], baker.resolution)  # never above a full map

    def test_an_elongated_cell_renders_its_area_not_its_long_side(self):
        """The pack keeps a cropped layout's aspect (a 1:3 wall panel), and RTT
        renders one SQUARE frame: supersampled by its long side, a 1:3 cell
        would render 3x the texels a square cell of its area does. It gets the
        same supersample^2 render texels per cell texel, never fewer than one
        per texel along its long side."""
        baker = LightmapBaker(resolution=4096)
        quantum = LightmapBaker._ATLAS_BAKE_QUANTUM
        ss = LightmapBaker._ATLAS_SUPERSAMPLE
        with mock.patch.object(LightmapBaker, "_lightmap_uv_bbox", return_value=None):
            sizes = baker._plan_bake_sizes(
                {"square": (200, 200), "wall": (116, 346), "strip": (20, 600)}
            )
        self.assertEqual(sizes["square"], -(-200 * ss // quantum) * quantum)
        self.assertLess(sizes["wall"], 346 * ss)
        self.assertGreaterEqual(sizes["wall"] ** 2, ss**2 * 116 * 346)
        self.assertLess(sizes["wall"] ** 2, ss**2 * 116 * 346 * 1.2 + quantum**2)
        self.assertGreaterEqual(sizes["strip"], 600)  # its long side, at least

    def test_an_atlas_tile_renders_above_its_cell(self):
        """The assembler's INTER_AREA resize averages a tile's noise before the
        cell is denoised: tiles rendered AT their cell kept every sample's noise
        and read as splotches in the WebXR preview (2026-09-21). A tile renders
        :attr:`_ATLAS_SUPERSAMPLE` squared texels for every texel of its cell,
        never above the full map. 2 since 2026-10-01: on a production floor at
        four bounces, 4x took 75s for 0.39% shadow mottle and 2x 22s for 0.52%
        (two seeds, shipped cell)."""
        baker = LightmapBaker(resolution=1024)
        ss = baker._ATLAS_SUPERSAMPLE
        self.assertGreaterEqual(ss, 2)
        with mock.patch.object(LightmapBaker, "_lightmap_uv_bbox", return_value=None):
            sizes = baker._plan_bake_sizes(
                {"prop": (100, 100), "floor": (256, 218), "room": (700, 600)}
            )
        self.assertGreaterEqual(sizes["prop"], ss * 100)
        self.assertGreaterEqual(sizes["floor"] ** 2, ss**2 * 256 * 218)
        self.assertGreaterEqual(sizes["floor"], 256)
        self.assertLessEqual(sizes["floor"], baker.resolution)
        self.assertEqual(sizes["room"], baker.resolution)  # capped at a full map

    def test_a_partial_island_renders_its_region_not_a_magnified_square(self):
        """A layout covering part of its square renders JUST its island's region.

        It used to render the whole square magnified by the axis it covered least
        and crop the rest away: a production room's reused wall layouts (u 0..0.33)
        drew 3x the texels they shipped and its baseboard strips (u 0..0.05) a
        full map each -- 342 of 624 render-seconds (2026-10-02)."""
        baker = LightmapBaker(resolution=1024)
        with mock.patch.object(
            LightmapBaker, "_lightmap_uv_bbox", return_value=(0.0, 0.0, 0.33, 1.0)
        ):
            sizes = baker._plan_bake_sizes({"a": (100, 100)})
            regions = baker._plan_regions(sizes)
        with mock.patch.object(LightmapBaker, "_lightmap_uv_bbox", return_value=None):
            full = baker._plan_bake_sizes({"a": (100, 100)})
        self.assertEqual(sizes, full)  # sized by its cell, not magnified
        self.assertIn("a", regions)  # ...and renders its region of the layout

    def test_every_side_of_a_region_keeps_its_full_pad(self):
        """The island sits exactly the pad -- at least 4 render texels -- from every frame edge.

        At 1, Arnold's pixel filter corrupted the island's outermost row near
        the frame (+3.3% / -1.4% on a production room's wall tiles: a 1-2%
        dip-and-ridge on every panel seam at 2K, where Arnold's own camera
        render is smooth). At 4 the side the unit square did not clamp came
        back clean, but the clamped sides kept a FRACTION of a texel and the
        seams no better. RTT renders a region past the unit square (measured:
        u/v_start -0.25, scale 1.5 maps exactly), so nothing is clamped --
        even for an island touching the square's edge, as the walls' do."""
        baker = LightmapBaker(resolution=1024)
        island = (0.0, 0.0, 0.33, 1.0)
        with mock.patch.object(LightmapBaker, "_lightmap_uv_bbox", return_value=island):
            px = baker._plan_bake_sizes({"a": (100, 100)})["a"]
            u0, v0, u1, v1 = baker._plan_regions({"a": px})["a"]
        self.assertGreaterEqual(baker._REGION_PAD, 4)
        su, sv = (u1 - u0) / px, (v1 - v0) / px  # one render texel, in uv
        for gap in (
            (island[0] - u0) / su,
            (u1 - island[2]) / su,
            (island[1] - v0) / sv,
            (v1 - island[3]) / sv,
        ):
            self.assertAlmostEqual(gap, baker._REGION_PAD, delta=1e-6)

    def test_a_full_island_renders_whole(self):
        baker = LightmapBaker(resolution=1024)
        with mock.patch.object(
            LightmapBaker,
            "_lightmap_uv_bbox",
            return_value=(0.004, 0.004, 0.996, 0.996),
        ):
            self.assertEqual(baker._plan_regions({"a": 256}), {})

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

    def test_a_layout_the_bake_rebuilds_is_planned_as_rebuilt(self):
        """The plan is drawn before the bake builds missing lightmap UVs and
        rebuilds failing ones (an overlapping set), so it sized and shaped
        those objects from a layout that never renders: a panel whose two
        faces share one square-filling, 2:1-stretched layout was given a 2:1
        cell, then rebuilt in its own proportions and squeezed into it. The
        pack must receive the plan of the layouts that render."""
        sg = self._sg("bakeRebuilt")
        panel = cmds.polyPlane(name="bakeRebuiltPanel", w=4, h=1, sx=2, sy=1)[0]
        panel = cmds.ls(panel, long=True)[0]
        cmds.sets(panel, edit=True, forceElement=sg)
        shape = cmds.listRelatives(panel, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, create=True, uvSet="lightmap")
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="lightmap")
        cmds.polyProjection(
            f"{shape}.f[0]", type="Planar", md="y", uvSetName="lightmap"
        )
        cmds.polyProjection(
            f"{shape}.f[1]", type="Planar", md="y", uvSetName="lightmap"
        )
        coords = cmds.polyEditUV(f"{shape}.map[*]", query=True)
        us, vs = coords[0::2], coords[1::2]
        for i, (u, v) in enumerate(zip(us, vs)):  # both faces over the whole square
            cmds.polyEditUV(
                f"{shape}.map[{i}]",
                relative=False,
                uValue=0.0 if u <= sum(us) / len(us) else 1.0,
                vValue=0.0 if v <= sum(vs) / len(vs) else 1.0,
            )
        cubes = [self._cube(f"bakeRebuilt{i}", sg, size=1.0) for i in range(3)]
        objects = [panel] + cubes
        baker = LightmapBaker(resolution=256)
        seen = {}

        def fake_bake(_self, objects, output_dir=None, size=None, **kwargs):
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
        ):
            baker.bake_atlas(objects, output_dir=self.tmp)

        rendered = baker.atlas_plan(objects)  # the layouts as they now are
        self.assertEqual(seen["plan"], rendered)

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

    def test_an_unpacked_region_tile_is_bound_through_its_region(self):
        # A tile rendered over only its island's region (_plan_regions) maps
        # that REGION onto its 0-1; kept as it is when the pack fails, its
        # binding must say so, or the engine samples it across the whole
        # layout.
        sg = self._sg("drainRegion")
        a = self._cube("drainRegionA", sg)
        b = self._cube("drainRegionB", sg)
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
            mock.patch.object(
                LightmapBaker,
                "_plan_regions",
                lambda _self, sizes: {a: (0.0, 0.0, 0.5, 0.25)},
            ),
            mock.patch.object(UvUtils, "create_lightmap_uvs", return_value={}),
        ):
            out = baker.bake_atlas([a, b], output_dir=self.tmp)

        self.assertEqual(out[a][1], [2.0, 4.0, 0.0, 0.0])
        self.assertEqual(out[b][1], [1.0, 1.0, 0.0, 0.0])


class TestLightmapPresets(unittest.TestCase):
    """Quality-tier presets via pythontk PresetStore (no Maya/Arnold needed)."""

    def test_builtin_tiers_listed(self):
        names = LightmapBaker.preset_store().list()
        for tier in ("preview", "mobile", "desktop", "hero"):
            self.assertIn(tier, names)

    def test_every_tier_bakes_the_bounces_the_blender_twin_does(self):
        """Both engines count bounces alike -- a grey calibration room baked by
        Arnold and by Cycles agreed with each other and the analytic value to
        2%, at 0 and at 2 bounces (2026-10-01) -- so the tiers carry ONE depth
        each, and a room bakes to the same level on either side. Four is the
        default: 95% of a grey room's converged light, against 81% at the old 2.
        Added: 2026-10-01
        """
        depths = {
            n: LightmapBaker.from_preset(n).gi_depth
            for n in ("preview", "mobile", "desktop", "hero")
        }
        self.assertEqual(depths, {"preview": 2, "mobile": 4, "desktop": 6, "hero": 8})

    def test_a_baker_that_names_no_tier_bakes_the_default_one(self):
        """A script that never names a tier gets the panel's default (mobile).
        Added: 2026-10-01
        """
        bare, mobile = LightmapBaker(), LightmapBaker.from_preset("mobile")
        for dial in ("resolution", "samples", "gi_depth", "gi_samples"):
            self.assertEqual(getattr(bare, dial), getattr(mobile, dial), dial)

    def test_from_preset_sets_resolution_and_samples(self):
        baker = LightmapBaker.from_preset("desktop")
        self.assertEqual(baker.resolution, 2048)
        self.assertEqual(baker.samples, 4)
        # The injected default baker inherits the resolution.
        self.assertEqual(baker.baker.resolution, 2048)

    def test_from_preset_pins_gi_render_settings(self):
        # GI depth / samples are scene render settings, not RTT flags: the
        # preset must reach the bake via the baker's pinned render_settings,
        # or every bake silently runs at Arnold's 1-bounce scene default.
        baker = LightmapBaker.from_preset("desktop")
        self.assertEqual(baker.gi_depth, 6)
        self.assertEqual(baker.gi_samples, 4)
        self.assertEqual(
            baker.baker.render_settings,
            {"GIDiffuseDepth": 6, "GIDiffuseSamples": 4},
        )

    def test_overrides_win_over_preset(self):
        baker = LightmapBaker.from_preset("mobile", resolution=1536, gi_depth=5)
        self.assertEqual(baker.resolution, 1536)  # override
        self.assertEqual(baker.samples, 4)  # from preset
        self.assertEqual(baker.gi_depth, 5)  # override
        self.assertEqual(baker.gi_samples, 2)  # from preset

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            LightmapBaker.from_preset("does_not_exist")

    def test_the_renamed_quest_tier_still_builds_mobile_with_a_notice(self):
        """``quest`` was renamed ``mobile``: a script that names the old tier
        keeps baking at the same dials, and is told the new name."""
        with self.assertWarns(DeprecationWarning) as caught:
            baker = LightmapBaker.from_preset("quest", resolution=1536)
        self.assertIn("mobile", str(caught.warning))
        mobile = LightmapBaker.from_preset("mobile")
        self.assertEqual(baker.resolution, 1536)  # an override still wins
        self.assertEqual(
            (baker.samples, baker.gi_depth, baker.gi_samples),
            (mobile.samples, mobile.gi_depth, mobile.gi_samples),
        )

    def test_a_user_preset_named_like_a_renamed_tier_shadows_the_alias(self):
        """The alias only stands in for a name the store no longer has."""
        self._store_with("quest", {"resolution": 512, "samples": 3})
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            baker = LightmapBaker.from_preset("quest")
        self.assertEqual((baker.resolution, baker.samples), (512, 3))

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
        # The device names one machine's hardware; a preset never picks it --
        # the baker keeps its own default.
        self.assertEqual(baker.device, LightmapBaker().device)

    def test_the_lighting_switches_round_trip_through_a_preset(self):
        """Include Scene Lighting and Include Hidden Lights are switches a
        preset stores, like Include Environment -- and a preset saved before
        they existed builds the defaults (every light, no hidden one)."""
        self.assertIn("include_scene_lights", LightmapBaker.PRESET_BOOL_KEYS)
        self.assertIn("include_hidden_lights", LightmapBaker.PRESET_BOOL_KEYS)
        self._store_with(
            "lightPass",
            {
                **self._PANEL_PRESET,
                "include_scene_lights": False,
                "include_hidden_lights": True,
            },
        )
        baker = LightmapBaker.from_preset("lightPass")
        self.assertIs(baker.include_scene_lights, False)
        self.assertIs(baker.include_hidden_lights, True)

        self._store_with("olderPass", dict(self._PANEL_PRESET))
        older = LightmapBaker.from_preset("olderPass")
        self.assertIs(older.include_scene_lights, True)
        self.assertIs(older.include_hidden_lights, False)

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

    def test_a_templated_mesh_is_left_out_like_a_hidden_one(self):
        """Arnold renders no templated object either -- "not exported to Arnold
        world", by the attribute or by a Template display layer -- so its bake
        wrote no map and stopped the bake exactly as a hidden one did. The
        production room's Scene scope held a templated rig of 23 meshes."""
        shown = self._cube("tgtShownT")
        by_attr = self._cube("tgtTemplated")
        cmds.setAttr(f"{by_attr}.template", True)
        by_layer = self._cube("tgtLayerTemplated")
        layer = cmds.createDisplayLayer(by_layer, name="tgtTemplate", noRecurse=True)
        cmds.setAttr(f"{layer}.displayType", 1)

        with self.assertLogs(LightmapBaker.logger, level="WARNING") as caught:
            targets = LightmapBaker.bake_targets([shown, by_attr, by_layer])

        self.assertEqual(targets, [shown])
        self.assertTrue(
            any("tgtTemplated" in m and "tgtLayerTemplated" in m for m in caught.output)
        )

    def test_a_mesh_hidden_at_its_shape_is_left_out_too(self):
        """Hidden or templated at its SHAPE under a shown transform (Hide with
        the shape picked in the Outliner, an import that hid it), a mesh
        renders nothing either. The gate walked up from the transform and
        never read the shape, so its bake stopped the bake as a hidden
        transform's did."""
        shown = self._cube("tgtShownS")
        hidden = self._cube("tgtShapeHidden")
        templated = self._cube("tgtShapeTemplated")
        shape = cmds.listRelatives(hidden, shapes=True, fullPath=True)[0]
        cmds.setAttr(f"{shape}.visibility", False)
        shape = cmds.listRelatives(templated, shapes=True, fullPath=True)[0]
        cmds.setAttr(f"{shape}.template", True)

        with self.assertLogs(LightmapBaker.logger, level="WARNING") as caught:
            targets = LightmapBaker.bake_targets([shown, hidden, templated])

        self.assertEqual(targets, [shown])
        self.assertTrue(
            any(
                "tgtShapeHidden" in m and "tgtShapeTemplated" in m
                for m in caught.output
            )
        )

    def test_each_instance_is_read_through_its_own_path(self):
        """One shape under a hidden transform and a shown one: the shown copy
        bakes, the hidden one is left out. The shape is a single node, so the
        answer comes from the path each copy is reached by -- a walk that took
        the shape's FIRST parent would leave the shown copy out too."""
        source = self._cube("tgtInstSrc")
        copy = cmds.ls(cmds.instance(source, name="tgtInstCopy")[0], long=True)[0]
        cmds.setAttr(f"{source}.visibility", False)

        with self.assertLogs(LightmapBaker.logger, level="WARNING"):
            targets = LightmapBaker.bake_targets([source, copy])

        self.assertEqual(targets, [copy])

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


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestWhatMovesIsOutOfTheRender(MayaTkTestCase):
    """What a bake renders: what it bakes, what was baked before, and the
    Exclude set. Any other visible mesh moves -- an interactive prop, never
    baked -- and a shadow it cast would stay in the maps wherever it went
    (production, 2026-10-07: props on a cart shadowed the cart's map). It is
    out of the render for the bake and handed back as found. A fake backend
    records which meshes were off as it rendered.
    """

    def setUp(self):
        super().setUp()
        self.tmp = self.enterContext(
            ptk.TempArtifacts("lm_movers", policy="scoped")
        ).dir_path()

    @staticmethod
    def _cube(name):
        return cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]

    @staticmethod
    def _shapes(node):
        return cmds.listRelatives(node, shapes=True, fullPath=True) or []

    def _bake(self, objects, packing="per_object"):
        fake = _FakeBaker()
        result = LightmapBaker(resolution=64, baker=fake).bake(
            objects, packing=packing, output_dir=self.tmp
        )
        self.assertTrue(result.maps, result.refused)
        return fake

    def test_only_what_moves_is_out_of_the_render(self):
        floor, prop = self._cube("mvFloor"), self._cube("mvProp")
        wall, crate = self._cube("mvWall"), self._cube("mvCrate")
        cv2, np = _cv2()
        earlier = os.path.join(self.tmp, "earlier.exr")
        cv2.imwrite(earlier, np.full((8, 8, 3), 0.5, np.float32))
        LightmapRecords.commit({wall: earlier})  # baked before: static
        LightmapExcludeSet.define([crate])  # static by the set's word

        fake = self._bake([floor])

        self.assertEqual(fake.hidden_at_bake, set(self._shapes(prop)))
        self.assertFalse(_hidden_meshes(), "every mesh is handed back as found")

    def test_an_atlas_bake_renders_every_target_at_once(self):
        """Left out per bake, not per tile: each target is in the render of
        every other's, so they shade one another."""
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        a, b, prop = self._cube("mvA"), self._cube("mvB"), self._cube("mvAtlasProp")
        for cube in (a, b):
            cmds.sets(self._shapes(cube), edit=True, forceElement=sg)

        fake = self._bake([a, b], packing="atlas")

        self.assertEqual(fake.hidden_at_bake, set(self._shapes(prop)))

    def test_an_instance_of_a_baked_mesh_stays_in(self):
        """The render hides a mesh, not an instance: hiding an unbaked
        instance would take its baked twin out with it."""
        floor = self._cube("mvInstanced")
        cmds.instance(floor, name="mvTwin")

        fake = self._bake([floor])

        self.assertEqual(fake.hidden_at_bake, set())

    def test_a_baked_mesh_under_one_that_moves_stays_in(self):
        """Its own mesh goes, never a child's: a baked shelf under a moving
        drawer's transform still renders."""
        drawer = self._cube("mvDrawer")
        shelf = cmds.ls(cmds.parent(self._cube("mvShelf"), drawer)[0], long=True)[0]

        fake = self._bake([shelf])

        self.assertEqual(fake.hidden_at_bake, set(self._shapes(drawer)))

    def test_a_failed_bake_still_hands_the_scene_back(self):
        floor, prop = self._cube("mvFailFloor"), self._cube("mvFailProp")

        class _Failing(_FakeBaker):
            def bake(self, *args, **kwargs):
                raise RuntimeError("render failed")

        with self.assertRaises(RuntimeError):
            LightmapBaker(resolution=64, baker=_Failing()).bake(
                [floor], packing="per_object", output_dir=self.tmp
            )
        self.assertFalse(_hidden_meshes(), f"{prop} left hidden")


class TestIsolatedObjectsBakeAlone(MayaTkTestCase):
    """The Isolate set: a part that moves against what is baked around it (a
    locker door on its cabinet, production 2026-10-15) bakes in a pass of its
    own -- the static scene in its render, every other isolated part out --
    and is out of every other pass's render, so the cabinet keeps no
    door-shaped shadow. A member brings the meshes under it (a door's glass,
    in the door's pass whether or not it bakes).
    """

    def setUp(self):
        super().setUp()
        self.tmp = self.enterContext(
            ptk.TempArtifacts("lm_isolate", policy="scoped")
        ).dir_path()
        self.addCleanup(LightmapIsolateSet.clear)

    @staticmethod
    def _cube(name):
        return cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]

    @staticmethod
    def _shapes(*nodes):
        return {
            s for n in nodes for s in cmds.listRelatives(n, shapes=True, fullPath=True)
        }

    def _bake(self, objects, packing="per_object"):
        fake = _FakeBaker()
        result = LightmapBaker(resolution=64, baker=fake).bake(
            objects, packing=packing, output_dir=self.tmp
        )
        self.assertTrue(result.maps, result.refused)
        return fake, result

    def _scene(self):
        cabinet, door, lid = (
            self._cube("isoCabinet"),
            self._cube("isoDoor"),
            self._cube("isoLid"),
        )
        glass = cmds.ls(cmds.parent(self._cube("isoGlass"), door)[0], long=True)[0]
        LightmapIsolateSet.define([door, lid])
        return cabinet, door, glass, lid

    def test_each_member_bakes_alone_and_out_of_the_others_render(self):
        cabinet, door, glass, lid = self._scene()

        fake, result = self._bake([cabinet, door, lid])

        self.assertEqual(
            fake.passes,
            [
                ([cabinet], self._shapes(door, glass, lid)),
                ([door], self._shapes(lid)),
                ([lid], self._shapes(door, glass)),
            ],
        )
        self.assertEqual(set(result.maps), {cabinet, door, lid})
        self.assertFalse(_hidden_meshes(), "every mesh is handed back as found")

    def test_an_atlas_bake_isolates_the_same_way(self):
        cabinet, door, glass, lid = self._scene()

        fake, _result = self._bake([cabinet, door], packing="atlas")

        self.assertEqual(
            [objects for objects, _hidden in fake.passes],
            [[cabinet], [door]],
        )
        self.assertEqual(fake.passes[0][1], self._shapes(door, glass, lid))

    def test_a_member_baked_before_stays_out_of_a_later_bake(self):
        """A map recorded earlier makes the door static to the other bakes'
        render rule -- the Isolate set must still take it out."""
        cabinet, door, glass, lid = self._scene()
        self._bake([door])

        fake, _result = self._bake([cabinet])

        self.assertEqual(fake.passes, [([cabinet], self._shapes(door, glass, lid))])

    def test_a_later_pass_never_takes_an_earlier_passs_map_name(self):
        """A door sharing its cabinet's texture set is named like the cabinet:
        one call keeps names apart, two passes must too (blendertk found it)."""
        cabinet, door, _glass, _lid = self._scene()

        fake, result = self._bake([cabinet, door])

        cabinet_map = os.path.basename(result.maps[cabinet]).lower()
        self.assertIn(cabinet, fake.called_claims.get(cabinet_map, ()))

    def test_no_isolated_target_is_one_pass_as_before(self):
        cabinet = self._cube("isoAlone")
        prop = self._cube("isoProp")

        fake, _result = self._bake([cabinet])

        self.assertEqual(fake.passes, [([cabinet], self._shapes(prop))])


class TestBesideTextures(MayaTkTestCase):
    """``beside_textures``: each map in its texture set's folder.

    The folder comes from the same vote that names the map
    (``LightmapBaker._texture_set``), so ``<set>_Lightmap.exr`` sits beside
    ``<set>_BaseColor.png``; the bake's output_dir takes any object whose
    material has no texture folder on this machine -- or whose folder lies
    outside the scene's project.
    """

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="lm_beside_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.out = os.path.join(self.root, "out")
        # The temp root IS the project the textures live in.
        previous = cmds.workspace(query=True, rootDirectory=True)
        cmds.workspace(self.root, openWorkspace=True)
        self.addCleanup(cmds.workspace, previous, openWorkspace=True)

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
    def test_a_texture_folder_outside_the_project_is_never_written_to(self):
        """Another project's (or Downloads') texture folder is not this bake's to write.

        A texture library two projects share took both projects' lightmaps
        under one name: a production soldering assembly's room maps beside
        another project's room textures, where that project's own room lightmap
        lives, and a PLAYGROUND bake's table atlas in the soldering project's
        texture folder (2026-10-03). Each bake then silently replaced the
        other's. Outside the project, the map takes the bake's output_dir."""
        inside = os.path.join(self.root, "sourceimages", "crate")
        library = tempfile.mkdtemp(prefix="lm_library_")
        self.addCleanup(shutil.rmtree, library, ignore_errors=True)
        ours, _ = self._textured("bsOurs", inside, "Crate_Wood_01")
        shared, _ = self._textured("bsShared", library, "Office_Wall_01")

        out = LightmapBaker(
            resolution=64, baker=_FakeBaker(), beside_textures=True
        ).bake_separated([ours, shared], output_dir=self.out)

        self.assertTrue(self._same(os.path.dirname(out[ours]), inside))
        self.assertTrue(self._same(os.path.dirname(out[shared]), self.out))
        self.assertEqual(os.listdir(library), [])  # never written to

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
        self.assertEqual(fake.called_claims, {"crate_lightmap.exr": frozenset({hero})})

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
                baker.baker,
                "_pinned_render_settings",
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

        with (
            mock.patch(
                "mayatk.light_utils.lightmap_baker.lightmap_baker.shutil.move",
                side_effect=OSError(28, "No space left on device"),
            ),
            mock.patch.object(
                ptk.FileUtils,
                "replace_file",
                side_effect=OSError(errno.EXDEV, "Cross-device link"),
            ),
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

    def test_a_failed_groups_map_never_lands_on_another_scenes_file(self):
        """The pack fallback (a group that fails keeps its per-object maps,
        ``_keep_unpacked``) named by claims alone: another scene's same-named
        map in the folder -- nothing here claims it, this scene never wrote
        it -- was replaced. Named by the atlas rule now
        (``LightmapRecords.strangers``), as the atlas itself is."""
        theirs = os.path.join(self.tmp, "Crate_Lightmap.exr")
        with open(theirs, "wb") as fh:
            fh.write(b"theirs")
        baker = LightmapBaker()
        with ptk.TempArtifacts("lm_fallback_work", policy="scoped") as work:
            source = os.path.join(work.dir_path(), "Crate_Lightmap.exr")
            with open(source, "wb") as fh:
                fh.write(b"mine")
            kept = baker._keep_unpacked(source, self.tmp, set(), {}, "|crate", False)
        self.assertEqual(os.path.basename(kept), "Crate_Lightmap_1.exr")
        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), b"theirs")

        # One its own object reads is still its own to replace.
        with ptk.TempArtifacts("lm_fallback_work", policy="scoped") as work:
            source = os.path.join(work.dir_path(), "Crate_Lightmap.exr")
            with open(source, "wb") as fh:
                fh.write(b"rebaked")
            claims = {"crate_lightmap.exr": frozenset({"|crate"})}
            kept = baker._keep_unpacked(source, self.tmp, set(), claims, "|crate", False)
        self.assertEqual(os.path.abspath(kept), os.path.abspath(theirs))


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

    def test_a_selected_group_bakes_the_meshes_under_it(self):
        """Selecting a group is "bake this": production (2026-10-07) selected a
        bake set's members -- a mesh and two groups -- and the bake took the
        mesh alone, leaving the cart and the room with maps from before."""
        room = cmds.group(empty=True, name="wfRoomGrp")
        floor, wall = self._cube("wfGrpFloor"), self._cube("wfGrpWall")
        floor, wall = cmds.ls(cmds.parent(floor, wall, room), long=True)
        body = self._cube("wfGrpBody")

        self.assertEqual(
            sorted(LightmapBaker.bake_targets([room, body])),
            sorted([floor, wall, body]),
        )
        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake(
            [room, body], packing="per_object", output_dir=self.tmp
        )
        self.assertEqual(set(result.maps), {floor, wall, body}, result.refused)
        # Only a MESH names itself alone: one with a mesh parented below bakes
        # without it.
        lid = cmds.ls(cmds.parent(self._cube("wfGrpLid"), body)[0], long=True)[0]
        body = cmds.ls(body, long=True)[0]
        self.assertEqual(LightmapBaker.bake_targets([body]), [body])
        self.assertNotIn(lid, LightmapBaker.bake_targets([body]))

    def test_a_selected_locator_bakes_the_meshes_under_it(self):
        """A locator parenting meshes is a group to the artist, and to a rig:
        production (2026-10-09, SOLDERING_ASSEMBLY) nests a table's meshes
        under ``SOLDERING_TABLE_LOC``. A locator has a shape, so it was no
        group (``NodeUtils.is_group``): a re-bake of the table re-baked the
        mesh beside it and left the six under it on a six-day-old map. Any
        node that is not itself a mesh now stands for the meshes below it.
        Added: 2026-10-09"""
        loc = cmds.spaceLocator(name="wfTableLoc")[0]
        legs = self._cube("wfLegs")
        top, body = self._cube("wfTop"), self._cube("wfBody")
        ctrl = cmds.circle(name="wfScaleCtrl")[0]
        cmds.parent(body, ctrl)
        cmds.parent(top, ctrl, loc)
        top, body = cmds.ls("wfTop", "wfBody", long=True)

        self.assertEqual(sorted(LightmapBaker.bake_targets([loc])), sorted([top, body]))
        self.assertEqual(
            sorted(LightmapBaker.bake_targets([legs, loc])), sorted([legs, top, body])
        )
        self.assertEqual(LightmapBaker.bake_targets(["wfScaleCtrl"]), [body])

    def test_a_selected_bake_set_bakes_its_members_and_the_exclude_set_overrules(self):
        """The production workflow (SOLDERING_ASSEMBLY, 2026-10-09): an include
        set -- the room group, a locator nesting the table, a mesh, the lights
        -- and the Exclude set, which overrules it. Selecting the set node
        (Outliner) handed the bake the set itself, which is no mesh: "Select
        one or more mesh objects". A set stands for its members like a group
        for its descendants, through the one resolver every scope uses.
        Added: 2026-10-09"""
        room = cmds.group(empty=True, name="wfSetRoom")
        floor = cmds.ls(cmds.parent(self._cube("wfSetFloor"), room)[0], long=True)[0]
        loc = cmds.spaceLocator(name="wfSetLoc")[0]
        top, button = self._cube("wfSetTop"), self._cube("wfSetButton")
        cmds.parent(top, button, loc)
        top, button = cmds.ls("wfSetTop", "wfSetButton", long=True)
        legs = self._cube("wfSetLegs")
        light = cmds.shadingNode("areaLight", asLight=True)  # its transform
        include = cmds.sets([room, loc, legs, light], name="wfLightBake_SET")
        LightmapExcludeSet.define([button])

        self.assertEqual(
            sorted(LightmapBaker.bake_targets([include])), sorted([floor, top, legs])
        )
        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake(
            [include], packing="per_object", output_dir=self.tmp
        )
        self.assertEqual(set(result.maps), {floor, top, legs}, result.refused)
        self.assertEqual(result.excluded, [button])
        # A shading group is a set too (NodeUtils.expand_sets): it stands for
        # the meshes it shades -- never for itself, which is what reached the
        # renderer before -- and the Exclude set still comes off.
        sg = cmds.listConnections(
            cmds.listRelatives(floor, shapes=True)[0], type="shadingEngine"
        )[0]
        self.assertEqual(
            sorted(LightmapBaker.bake_targets([sg])), sorted([floor, top, legs])
        )

    def test_a_hidden_mesh_is_named_hidden_not_excluded(self):
        """``excluded`` is the Exclude set's, and a hidden mesh is left out for
        a reason of its own. Counted together, a Scene bake of the production
        room reported "14 excluded" against a set of 12 -- its two hidden
        props counted in."""
        kept, left_out = self._cube("wfKeptH"), self._cube("wfOutH")
        hidden = self._cube("wfHiddenH")
        cmds.setAttr(f"{hidden}.visibility", False)
        LightmapExcludeSet.define([left_out])

        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake(
            [kept, left_out, hidden], packing="per_object", output_dir=self.tmp
        )

        self.assertEqual(list(result.maps), [kept])
        self.assertEqual(result.excluded, [left_out])
        self.assertEqual(result.hidden, [hidden])

    def test_a_bake_of_nothing_visible_says_so(self):
        """Refused for what it is: the Exclude set was not why."""
        hidden = self._cube("wfOnlyHidden")
        cmds.setAttr(f"{hidden}.visibility", False)
        refused = LightmapBaker(baker=_FakeBaker()).bake([hidden])
        self.assertFalse(refused)
        self.assertIn("hidden", refused.refused)
        self.assertNotIn("Exclude set", refused.refused)

        left_out = self._cube("wfOnlyOut")
        LightmapExcludeSet.define([left_out])
        both = LightmapBaker(baker=_FakeBaker()).bake([hidden, left_out])
        self.assertIn("hidden", both.refused)
        self.assertIn("Exclude set", both.refused)

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

    def test_a_bake_meant_to_be_lit_by_emission_alone_is_not_refused(self):
        """refuse_unlit=False: the caller says every light off is intended -- an
        emission-only light layer (mayatk's LightLayerBaker) switches every
        light to 0 on purpose, and the guard refused it."""
        _shape, transform = self._make_light(intensity=0)
        fake = _FakeBaker()
        result = LightmapBaker(resolution=64, baker=fake, refuse_unlit=False).bake(
            [self._cube()], packing="per_object", output_dir=self.tmp
        )
        self.assertIsNone(result.refused)
        self.assertIsNotNone(fake.called_uv_set, "the bake ran")

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
        retired ``commit_lightmap(intensity=)`` scaled whatever it was
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
            [cube],
            packing="per_object",
            output_dir=self.tmp,
            intensity=2.0,
            dilate=False,
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

    # -- the record lives on LightmapRecords --------------------------------------

    def test_the_workflow_verbs_delegate_to_the_record(self):
        """The record's calls live on ``LightmapRecords`` alone (the baker's
        spellings were removed in 0.28.0); the workflow verbs stay delegates."""
        cube = self._cube("wfAlias")
        LightmapRecords.commit({cube: self._exr("alias.exr", 1.0)})
        self.assertEqual(
            [d["map"] for d in LightmapRecords.lightmap_dependencies()], ["alias.exr"]
        )
        self.assertFalse(hasattr(LightmapBaker, "lightmap_dependencies"))
        baker = LightmapBaker()
        self.assertEqual(baker.baked_objects(), [cube])
        self.assertEqual(baker.revert(), [cube])


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestReflectionProbe(MayaTkTestCase):
    """A bake captures the room it lit as a reflection probe, and records it.

    What a lightmap cannot hold: a baked METAL has no diffuse, so all it shows
    is what it reflects, and an object the bake left out is lit by nothing of
    the room. Measured on a production soldering table against Arnold's render
    of the same room (2026-10-03): under the WebXR preview's studio its metal
    housing read 0.12 of Arnold's luminance and its unbaked magnifier up to
    2.8x too bright; reflecting and lit by the room's own probe, 0.62 and
    0.65-1.45x. A fake backend stands in for Arnold.
    """

    def setUp(self):
        super().setUp()
        self.tmp = self.enterContext(
            ptk.TempArtifacts("lm_probe", policy="scoped")
        ).dir_path()

    def _room(self):
        """A 10 x 4 x 10 m room (cm) on the floor, a table in it and an
        unbaked prop on the table, off-centre: ``(room, table, prop)``."""
        room = cmds.polyCube(name="pbRoom", width=1000, height=400, depth=1000)[0]
        cmds.move(0, 200, 0, room)
        table = cmds.polyCube(name="pbTable", width=240, height=90, depth=100)[0]
        cmds.move(-100, 45, -200, table)
        prop = cmds.polyCube(name="pbProp", width=20, height=20, depth=20)[0]
        cmds.move(-300, 100, -150, prop)
        return [cmds.ls(n, long=True)[0] for n in (room, table, prop)]

    def _bake(self, objects, **kwargs):
        fake = _FakeBaker()
        result = LightmapBaker(resolution=1024, baker=fake, **kwargs).bake(
            objects, packing="per_object", output_dir=self.tmp
        )
        return result, fake

    def test_the_manifest_carries_the_lights_for_their_highlights(self):
        """A lightmap holds no highlight: the bake's lights ride the manifest so
        a reader lights their specular (Arnold's look on a metal), in the
        file's unit: the record's one unit_scale (version 2)."""
        room, table, _prop = self._room()
        spot = cmds.spotLight(intensity=3.0, coneAngle=50.0)
        cmds.xform(
            cmds.listRelatives(spot, parent=True)[0],
            ws=True,
            t=(0, 380, 0),
            ro=(-90, 0, 0),
        )
        self._bake([room, table])
        manifest = json.loads(LightmapRecords._record().text)
        (light,) = manifest["lights"]
        self.assertEqual(light["type"], "spot")
        self.assertEqual(manifest["unit_scale"], 0.01)
        self.assertNotIn("unit_scale", light, "stated once, by the record")
        self.assertAlmostEqual(light["position"][1], 380.0, places=3)
        self.assertAlmostEqual(light["direction"][1], -1.0, places=5)

    def test_a_bake_captures_the_room_over_what_moves_and_records_it(self):
        room, table, prop = self._room()
        result, fake = self._bake([room, table])
        self.assertTrue(result.probe, "the bake captured no probe")
        self.assertEqual(  # waiting in the session for the save
            os.path.dirname(LightmapRecords._home(result.probe)), self.tmp, "beside its maps"
        )
        self.assertTrue(result.probe.endswith("_Probe.exr"))
        call = fake.called_panorama
        # Over the prop that moves, at the baked room's mid-height.
        self.assertAlmostEqual(call["position"][0], -300.0, places=3)
        self.assertAlmostEqual(call["position"][1], 200.0, places=3)
        self.assertAlmostEqual(call["position"][2], -150.0, places=3)
        # What moves is see-through to it: its own mesh, never a child's.
        self.assertEqual(
            call["hide"], cmds.listRelatives(prop, shapes=True, fullPath=True)
        )
        self.assertEqual(call["width"], 512, "half the bake's resolution")

        probe = LightmapRecords.probe()
        self.assertEqual(probe["map"], os.path.basename(result.probe))
        self.assertEqual(probe["position"], call["position"])
        self.assertEqual(probe["unit_scale"], 0.01)
        # The room's walls, measured from the probe: the table below it is
        # furniture, and the floor stays the floor.
        (x0, y0, z0), (x1, y1, z1) = probe["box"]
        for got, want in (
            (x0, -500),
            (y0, 0),
            (z0, -500),
            (x1, 500),
            (y1, 400),
            (z1, 500),
        ):
            self.assertAlmostEqual(got, want, delta=1.0)

        manifest = json.loads(LightmapRecords._record().text)
        # Carried to the GLB in the record's shape: its box {min, max}, its unit
        # the record's one unit_scale.
        self.assertEqual(
            manifest["probe"],
            {
                "map": probe["map"],
                "position": probe["position"],
                "box": {"min": [x0, y0, z0], "max": [x1, y1, z1]},
            },
            "the manifest carries it to the GLB",
        )
        self.assertEqual(manifest["unit_scale"], probe["unit_scale"])
        deps = {d["map"]: d for d in LightmapRecords.lightmap_dependencies()}
        self.assertEqual(deps[probe["map"]]["objects"], [], "the scene's, no object's")
        self.assertTrue(deps[probe["map"]]["path"], "a GLB build finds it")
        self.assertIn(  # where it waits for the save
            os.path.dirname(result.probe).replace("\\", "/").lower(),
            [d.replace("\\", "/").lower() for d in LightmapRecords.search_dirs()],
        )

    def test_what_moves_is_out_of_the_probe_as_it_is_out_of_the_bake(self):
        """The probe shows the room the maps lit: what moves shades neither
        (it stands elsewhere by the time anything reflects it), while an
        Exclude-set member is the room, seen and shading in both."""
        room, table, prop = self._room()
        crate = cmds.ls(
            cmds.polyCube(name="pbCrate", width=50, height=50)[0], long=True
        )[0]
        cmds.move(300, 25, 300, crate)
        LightmapExcludeSet.define([crate])
        _result, fake = self._bake([room, table])
        call = fake.called_panorama
        prop_shapes = set(cmds.listRelatives(prop, shapes=True, fullPath=True))
        self.assertEqual(call["hidden"], prop_shapes)
        self.assertEqual(set(call["hide"]), prop_shapes, "the crate is the room")
        self.assertFalse(_hidden_meshes(), "handed back as found")

    def test_what_moves_outside_the_room_does_not_pull_the_probe(self):
        """A scratch copy parked beside the room (a production scene keeps a
        TEMP copy of its table there) is neither in the room nor lit by it."""
        room, table, _prop = self._room()
        outside = cmds.polyCube(name="pbOutside", width=20, height=20, depth=20)[0]
        cmds.move(2000, 100, 1500, outside)
        _result, fake = self._bake([room, table])
        call = fake.called_panorama
        self.assertAlmostEqual(call["position"][0], -300.0, places=3)
        self.assertAlmostEqual(call["position"][2], -150.0, places=3)
        # Still see-through to it, wherever it stands: in an open scene what
        # moves is in the probe's view however far off it is parked.
        self.assertIn(
            cmds.listRelatives(outside, shapes=True, fullPath=True)[0], call["hide"]
        )

    def test_a_table_under_the_probe_is_furniture_not_the_floor(self):
        room, table, _prop = self._room()
        box = ProbePlacement([room, table]).faces([-100.0, 200.0, -200.0])
        self.assertAlmostEqual(
            box[0][1], 0.0, delta=1.0, msg="the floor, past the table"
        )

    def test_a_hidden_baked_object_is_no_wall(self):
        """The render does not see a hidden object, so the box must not: a
        baked shell around the probe, hidden since its bake, walled it in."""
        room, table, _prop = self._room()
        shell = cmds.polyCube(name="pbShell", width=200, height=200, depth=200)[0]
        cmds.move(-300, 200, -150, shell)
        shell = cmds.ls(shell, long=True)[0]
        self._bake([room, table, shell])
        cmds.setAttr(shell + ".visibility", 0)

        LightmapBaker(resolution=1024, baker=_FakeBaker()).bake_probe()

        (x0, _y0, z0), (x1, _y1, z1) = LightmapRecords.probe()["box"]
        for got, want in ((x0, -500), (z0, -500), (x1, 500), (z1, 500)):
            self.assertAlmostEqual(got, want, delta=1.0)

    def test_a_z_up_scene_gets_no_probe(self):
        """Its deliverables read a probe in Y-up axes (the WebXR preview's
        equirect, Unity's cubemap), so one captured in a Z-up scene's would
        reflect the room on its side: none, said once. Added: 2026-10-04"""
        cmds.upAxis(axis="z", rotateView=False)
        self.addCleanup(cmds.upAxis, axis="y", rotateView=False)
        room, table, _prop = self._room()
        with self.assertLogs(LightmapBaker.logger, level="WARNING") as caught:
            result, fake = self._bake([room, table])
        self.assertIsNone(result.probe)
        self.assertIsNone(fake.called_panorama)
        self.assertIsNone(LightmapRecords.probe())
        self.assertTrue(any("Z-up" in line for line in caught.output), caught.output)

    def test_the_probe_follows_its_file_when_repathed(self):
        room, table, _prop = self._room()
        result, _fake = self._bake([room, table])
        moved = os.path.join(self.tmp, "moved")
        os.makedirs(moved)
        name = os.path.basename(result.probe)
        shutil.move(result.probe, os.path.join(moved, name))
        self.assertEqual(LightmapRecords.repath_lightmaps({name.lower(): moved}, []), 1)
        deps = {d["map"]: d for d in LightmapRecords.lightmap_dependencies(walk=False)}
        self.assertEqual(
            os.path.normcase(os.path.dirname(deps[name]["path"])),
            os.path.normcase(moved),
        )

    def test_an_export_of_baked_objects_reads_the_probe_it_ships(self):
        """The manifest carries the probe whenever it carries a baked object,
        so a read scoped to an export set must list it too. Scoped reads left
        it out: the exporter's path check never saw it, and the folders its
        FBX embed and GLB build join names against lacked the probe's own --
        on a production module whose probe sat apart from its maps, the FBX
        shipped without it. Added: 2026-10-14"""
        room, table, prop = self._room()
        result, _fake = self._bake([room, table])
        name = os.path.basename(result.probe)
        apart = os.path.join(self.tmp, "apart")
        os.makedirs(apart)
        shutil.move(result.probe, os.path.join(apart, name))
        LightmapRecords.repath_lightmaps({name.lower(): apart}, [])

        scoped = {d["map"]: d for d in LightmapRecords.lightmap_dependencies([table])}
        self.assertIn(name, scoped, "the export set ships it")
        self.assertTrue(scoped[name]["path"])
        self.assertIn(
            os.path.normcase(apart),
            [os.path.normcase(d) for d in LightmapRecords.search_dirs([table])],
        )
        # A set with nothing baked ships no manifest, so no probe either.
        self.assertEqual(LightmapRecords.lightmap_dependencies([prop]), [])

    def test_a_renamed_map_is_followed_by_its_markers_and_the_probe(self):
        """A lightmap is bound by its FILE NAME -- the markers, the folder and
        writer records and the deliverable's manifest all key on it -- so a
        file renamed on disk read as a missing lightmap. ``rename_lightmap``
        re-stamps every record naming it (the Texture Path Editor's rename
        drives it). Added: 2026-10-04
        """
        room, table, _prop = self._room()
        result, _fake = self._bake([room, table])
        deps = LightmapRecords.lightmap_dependencies(walk=False)
        mapped = next(d for d in deps if d["objects"])
        old, new = mapped["map"], "renamed_" + mapped["map"]
        os.rename(mapped["path"], os.path.join(os.path.dirname(mapped["path"]), new))
        self.assertEqual(
            LightmapRecords.rename_lightmap(old, new), len(mapped["objects"])
        )

        probe_old = os.path.basename(result.probe)
        probe_new = "renamed_" + probe_old
        os.rename(result.probe, os.path.join(os.path.dirname(result.probe), probe_new))
        self.assertEqual(LightmapRecords.rename_lightmap(probe_old, probe_new), 1)

        after = {d["map"]: d for d in LightmapRecords.lightmap_dependencies(walk=False)}
        self.assertNotIn(old, after)
        self.assertEqual(sorted(after[new]["objects"]), sorted(mapped["objects"]))
        self.assertTrue(after[new]["path"], "the renamed file resolves")
        self.assertTrue(after[probe_new]["path"], "...and so does the probe")
        self.assertEqual(LightmapRecords.probe()["map"], probe_new)
        manifest = json.loads(LightmapRecords._record().text)
        self.assertIn(new, json.dumps(manifest), "the deliverable names it too")
        self.assertNotIn(f'"{old}"', json.dumps(manifest))
        self.assertEqual(LightmapRecords.rename_lightmap("nothing.exr", "x.exr"), 0)

    def test_reverting_every_bake_drops_the_probe(self):
        room, table, _prop = self._room()
        self._bake([room, table])
        LightmapRecords.revert([room])
        self.assertIsNotNone(LightmapRecords.probe(), "the table is still baked")
        LightmapRecords.revert()
        self.assertIsNone(LightmapRecords.probe())

    def test_with_the_switch_off_there_is_no_probe(self):
        room, table, _prop = self._room()
        result, fake = self._bake([room, table], reflection_probe=False)
        self.assertIsNone(result.probe)
        self.assertIsNone(fake.called_panorama)
        self.assertIsNone(LightmapRecords.probe())
        self.assertNotIn("probe", json.loads(LightmapRecords._record().text))

    def test_a_probe_that_fails_never_loses_the_bake(self):
        """The probe renders AFTER the maps are committed: an arnoldRender
        error, or a probe file held open, raised out of the finished bake and
        the panel lost its report. Logged; the bake stands. Added: 2026-10-04
        """
        room, table, _prop = self._room()
        fake = _FakeBaker()
        fake.render_panorama = mock.Mock(side_effect=RuntimeError("arnoldRender"))
        with self.assertLogs(LightmapBaker.logger, level="WARNING") as caught:
            result = LightmapBaker(resolution=1024, baker=fake).bake(
                [room, table], packing="per_object", output_dir=self.tmp
            )
        self.assertEqual(sorted(result.maps), sorted([room, table]))
        self.assertIsNone(result.probe)
        self.assertEqual(sorted(LightmapRecords.baked_objects()), sorted([room, table]))
        self.assertTrue(
            any("Reflection probe not captured" in line for line in caught.output),
            caught.output,
        )

    def test_a_cancelled_bake_captures_no_probe(self):
        """A cancel keeps the maps it finished, but must not start one more
        render -- and one the artist cannot stop. Added: 2026-10-04"""

        class _Cancelling(_FakeBaker):
            """Asks on_progress before each object, as TextureBaker does."""

            def bake(self, objects, on_progress=None, **kwargs):
                done = {}
                for i, obj in enumerate(objects):
                    if on_progress(i, len(objects), obj) is False:
                        break
                    done.update(super().bake([obj], **kwargs))
                return done

        room, table, _prop = self._room()
        fake = _Cancelling()
        result = LightmapBaker(resolution=1024, baker=fake).bake(
            [room, table],
            packing="per_object",
            output_dir=self.tmp,
            on_progress=lambda done, _total, _name: done < 1,
        )
        self.assertEqual(len(result.maps), 1, "what finished before the cancel")
        self.assertIsNone(result.probe)
        self.assertIsNone(fake.called_panorama, "a render after the cancel")

    @unittest.skipUnless(_arnold_loadable(), "mtoa unavailable")
    def test_the_probe_sees_every_light_an_arnold_one_too(self):
        """``ls(lights=True)`` lists no Arnold light (an aiAreaLight is no
        ``light``), so a room's fixtures stayed invisible to the probe's
        camera and it showed none of their radiance. Added: 2026-10-04"""
        room, table, _prop = self._room()
        fixture = cmds.listRelatives(
            cmds.shadingNode("aiAreaLight", asLight=True), shapes=True, fullPath=True
        )[0]
        native = cmds.listRelatives(
            cmds.shadingNode("pointLight", asLight=True), shapes=True, fullPath=True
        )[0]
        _result, fake = self._bake([room, table])
        self.assertIn(fixture, fake.called_panorama["lights"])
        self.assertIn(native, fake.called_panorama["lights"])

    @unittest.skipUnless(_arnold_loadable(), "mtoa unavailable")
    def test_an_environment_the_bake_leaves_out_stays_out_of_its_probe(self):
        """Include Environment off muted the sky dome for the maps alone: the
        probe still rendered it -- a sky lift on what it lights that the maps
        lack -- and its placement still counted it as sky. Added: 2026-10-04
        """
        room, table, _prop = self._room()
        dome = cmds.shadingNode("aiSkyDomeLight", asLight=True)
        dome_shape = cmds.listRelatives(dome, shapes=True, fullPath=True)[0]
        real_place = ProbePlacement.place
        skies = []

        def place(placement):
            skies.append(placement._light_points()[1])
            return real_place(placement)

        for include in (True, False):
            with self.subTest(include_environment=include):
                del skies[:]
                with mock.patch.object(
                    ProbePlacement, "place", autospec=True, side_effect=place
                ):
                    _result, fake = self._bake(
                        [room, table], include_environment=include
                    )
                self.assertEqual(
                    dome_shape in fake.called_panorama["lit"], include, "the render"
                )
                self.assertEqual(skies, [include], "the placement's sky")
                self.assertTrue(cmds.getAttr(dome + ".visibility"), "put back")

    def test_an_instance_of_a_baked_mesh_is_never_hidden(self):
        """The render hides a MESH (its shape's primaryVisibility), and with it
        every instance: hiding the unbaked instance -- the mesh's first path,
        the only one a plain listing names -- hid its baked twin from the
        probe. Added: 2026-10-04"""
        room, table, _prop = self._room()
        original = cmds.polyCube(name="pbOriginal", width=30, height=30, depth=30)[0]
        cmds.move(200, 100, 200, original)
        twin = cmds.instance(original, name="pbTwin")[0]
        cmds.move(-200, 100, 200, twin)
        twin = cmds.ls(twin, long=True)[0]
        _result, fake = self._bake([room, table, twin])
        twin_mesh = set(
            cmds.ls(cmds.listRelatives(twin, shapes=True, fullPath=True), uuid=True)
        )
        self.assertFalse(
            twin_mesh & self._hidden_meshes(fake), "the baked twin was hidden"
        )

    def test_a_baked_child_of_an_unbaked_mesh_stays_in_view(self):
        """``hide`` named the unbaked TRANSFORM, and the render hides every mesh
        under what it is given: the baked child went with its unbaked parent.
        The parent's own mesh alone is hidden. Added: 2026-10-04"""
        room, table, _prop = self._room()
        holder = cmds.polyCube(name="pbHolder", width=40, height=10, depth=40)[0]
        cmds.move(250, 100, 250, holder)
        held = cmds.polyCube(name="pbHeld", width=20, height=20, depth=20)[0]
        held = cmds.ls(cmds.parent(held, holder)[0], long=True)[0]
        holder = cmds.ls(holder, long=True)[0]
        _result, fake = self._bake([room, table, held])
        hidden = self._hidden_meshes(fake)

        def meshes(node):
            return set(
                cmds.ls(cmds.listRelatives(node, shapes=True, fullPath=True), uuid=True)
            )

        self.assertFalse(meshes(held) & hidden, "the baked child was hidden")
        self.assertTrue(meshes(holder) <= hidden, "what moves is see-through")

    @staticmethod
    def _hidden_meshes(fake):
        """The meshes (by uuid) the panorama hides, expanded as
        ``TextureBaker.render_panorama`` expands its *hide*."""
        paths = [
            shape
            for node in fake.called_panorama["hide"]
            for shape in cmds.ls(
                node, dag=True, type="mesh", noIntermediate=True, long=True
            )
            or []
        ]
        return set(cmds.ls(paths, uuid=True) or []) if paths else set()

    def test_a_bake_leaves_the_selection_as_it_found_it(self):
        """The bake's white card took the selection when it was made, and
        deleting it after left nothing selected. Added: 2026-10-04"""
        room, table, prop = self._room()
        cmds.select(prop)
        self._bake([room, table])
        self.assertEqual(cmds.ls(selection=True, long=True), [prop])


class TestProbePlacement(MayaTkTestCase):
    """Where the probe stands, in each kind of scene a generic baker meets.

    The first rule -- the middle of what is baked, at half the room's height,
    inside the room its rays found -- held in the one room it was measured in
    and put the probe where it could not see the room anywhere else (measured
    2026-10-04, these scenes run through both rules): inside the car standing at the
    bake's middle (a black probe), inside a column at a room's middle, five
    metres up in a hall, a courtyard's walls read as infinitely far for its
    open sky, a probe's ceiling taken from a shelf 30 cm over it. Each test is
    one of those scenes. ``ProbePlacement`` is the geometry -- ray casts
    against the scene, no Arnold. Added: 2026-10-04
    """

    @staticmethod
    def _cube(name, w, h, d, at, inward=False):
        node = cmds.polyCube(name=name, width=w, height=h, depth=d)[0]
        cmds.move(*at, node)
        if inward:  # a room as a runtime draws one: its faces turned in
            cmds.polyNormal(
                node, normalMode=0, userNormalMode=0, constructionHistory=False
            )
        return cmds.ls(node, long=True)[0]

    @staticmethod
    def _plane(name, w, d, at=(0, 0, 0)):
        node = cmds.polyPlane(name=name, width=w, height=d)[0]
        cmds.move(*at, node)
        return cmds.ls(node, long=True)[0]

    @staticmethod
    def _light(kind, at=(0, 0, 0)):
        node = cmds.shadingNode(kind, asLight=True)
        cmds.move(*at, node)
        return node

    def _site(self, baked, unbaked=()):
        site = ProbePlacement(baked, unbaked).place()
        self.assertIsNotNone(site)
        return site

    def assertBox(self, box, want, delta=1.0):
        for corner, wanted in zip(box, want):
            for got, value in zip(corner, wanted):
                if value is None:  # an open face: pushed out of the scene's reach
                    self.assertGreaterEqual(abs(got), 0.5e5, box)
                else:
                    self.assertAlmostEqual(got, value, delta=delta, msg=box)

    def assertClearOf(self, position, node, by=30.0):
        """*position* is at least *by* outside *node*'s world bounds."""
        x0, y0, z0, x1, y1, z1 = cmds.exactWorldBoundingBox(node)
        gap = [
            max(lo - c, 0.0, c - hi)
            for c, lo, hi in zip(position, (x0, y0, z0), (x1, y1, z1))
        ]
        self.assertGreaterEqual(
            sum(g * g for g in gap) ** 0.5, by - 0.5, (position, node)
        )

    def test_a_solid_at_the_bakes_middle_is_flown_over_never_entered(self):
        """A car on a ground plane: the bake's middle is inside the car, and a
        probe there rendered its shell from within. It hovers over the car at
        eye level instead, the ground its one face."""
        ground = self._plane("ppGround", 4000, 4000)
        car = self._cube("ppCar", 450, 140, 180, (0, 70, 0))
        self._light("pointLight", (0, 800, 0))
        site = self._site([ground, car])
        for got, want in zip(site.position, (0.0, 200.0, 0.0)):
            self.assertAlmostEqual(got, want, delta=1.0)
        self.assertClearOf(site.position, car)
        # The ground, past the car roof the probe stands over; the sky open.
        self.assertBox(site.box, [[None, 0.0, None], [None, None, None]])
        self.assertEqual(site.open_faces, 5)
        self.assertEqual(site.hide, [])

    def test_a_column_at_the_rooms_middle_is_stepped_around(self):
        room = self._cube("ppRoom", 1000, 400, 1000, (0, 200, 0), inward=True)
        column = self._cube("ppColumn", 60, 400, 60, (0, 200, 0))
        self._light("pointLight", (300, 350, 300))
        site = self._site([room, column])
        self.assertAlmostEqual(site.position[1], 200.0, delta=1.0)
        self.assertClearOf(site.position, column, by=50.0)
        self.assertLess(
            abs(site.position[0]) + abs(site.position[2]), 200.0, "near the middle"
        )
        self.assertTrue(any("ppColumn" in note for note in site.notes), site.notes)
        # The walls behind the column, not its face 30 cm off.
        self.assertBox(site.box, [[-500, 0, -500], [500, 400, 500]])

    def test_a_dark_cavity_at_the_bakes_middle_is_left_for_the_lit_room(self):
        """A cabinet's inside faces in, like a room's, but no light reaches it:
        a cabin, not the room."""
        room = self._cube("ppRoom", 1000, 400, 1000, (0, 200, 0), inward=True)
        cabinet = self._cube("ppCabinet", 100, 100, 100, (0, 200, 0), inward=True)
        self._light("pointLight", (300, 350, 300))
        site = self._site([room, cabinet])
        self.assertClearOf(site.position, cabinet)
        self.assertBox(site.box, [[-500, 0, -500], [500, 400, 500]])

    def test_a_lit_room_built_inside_out_is_still_a_room(self):
        """One cube with its faces out: from inside, the backs of its walls --
        the sign of a solid -- but a light in view says room."""
        room = self._cube("ppRoom", 600, 300, 600, (0, 150, 0))
        self._light("pointLight", (0, 250, 0))
        site = self._site([room])
        for got, want in zip(site.position, (0.0, 150.0, 0.0)):
            self.assertAlmostEqual(got, want, delta=1.0)
        self.assertEqual(site.notes, [])
        self.assertBox(site.box, [[-300, 0, -300], [300, 300, 300]])

    def test_an_open_cupboard_over_what_moves_is_left_through_its_opening(self):
        """A cabinet with its front open and the parts that move inside it: over
        what moves at eye level is inside the cabinet, and the room's lights
        show through the opening, so it read as lit open air -- the production
        probe (SOL_REPLACE_ASSEMBLY, 2026-10-15) was two-thirds cabinet
        interior, and every unbaked metal in the scene reflected it dark. It
        leaves by the opening, never through a wall."""
        room = self._cube("ppRoom", 1000, 400, 1000, (0, 200, 0), inward=True)
        slabs = [
            self._cube("ppBack", 120, 215, 3, (0, 107.5, -43.5)),
            self._cube("ppLeft", 3, 215, 90, (-58.5, 107.5, 0)),
            self._cube("ppRight", 3, 215, 90, (58.5, 107.5, 0)),
            self._cube("ppTop", 120, 3, 90, (0, 213.5, 0)),
            self._cube("ppBottom", 120, 3, 90, (0, 1.5, 0)),
        ]
        cabinet = cmds.ls(
            cmds.polyUnite(slabs, name="ppCabinet", constructionHistory=False)[0],
            long=True,
        )[0]
        prop = self._cube("ppPart", 20, 20, 20, (0, 100, 0))
        self._light("pointLight", (0, 300, 450))  # in view through the opening
        site = self._site([room, cabinet], [prop])
        self.assertEqual(site.reason, "over what moves")
        self.assertClearOf(site.position, cabinet)
        self.assertGreater(site.position[2], 45.0, "out the open front, not the back")
        self.assertTrue(any("ppCabinet" in note for note in site.notes), site.notes)

    def test_a_tall_hall_keeps_the_probe_at_eye_level(self):
        hall = self._cube("ppHall", 2000, 1200, 2000, (0, 600, 0), inward=True)
        table = self._cube("ppTable", 240, 90, 100, (200, 45, 100))
        self._light("pointLight", (0, 1100, 0))
        site = self._site([hall, table])
        self.assertAlmostEqual(
            site.position[1], 200.0, delta=1.0, msg="not half of 12 m"
        )
        self.assertBox(site.box, [[-1000, 0, -1000], [1000, 1200, 1000]])

    def test_a_courtyard_reads_its_sky_as_distant_and_projects_its_walls(self):
        """Open above and walled round: the old box was all or nothing, so
        its walls and floor reflected as if infinitely far."""
        floor = self._plane("ppFloor", 1200, 1200)
        walls = [
            self._cube("ppWallN", 1200, 400, 20, (0, 200, -600)),
            self._cube("ppWallS", 1200, 400, 20, (0, 200, 600)),
            self._cube("ppWallE", 20, 400, 1200, (600, 200, 0)),
            self._cube("ppWallW", 20, 400, 1200, (-600, 200, 0)),
        ]
        cmds.directionalLight()
        site = self._site([floor] + walls)
        self.assertEqual(site.open_faces, 1)
        self.assertBox(site.box, [[-590, 0, -590], [590, None, 590]])
        self.assertAlmostEqual(site.position[1], 200.0, delta=1.0)

    def test_a_bare_ground_projects_onto_the_ground_alone(self):
        """A side cone's lower rays meet the ground at a slant: no wall. The
        old box took the first one's landing as one, or read the ground as
        distant with the rest."""
        floor = self._plane("ppFloor", 1000, 1000)
        faces = ProbePlacement([floor]).faces([0.0, 100.0, 0.0])
        self.assertEqual(
            [[c is None for c in corner] for corner in faces],
            [[True, False, True], [True, True, True]],
            faces,
        )
        self.assertAlmostEqual(faces[0][1], 0.0, delta=0.01)
        site = self._site([floor])
        self.assertBox(site.box, [[None, 0.0, None], [None, None, None]])
        self.assertAlmostEqual(site.position[1], 200.0, delta=1.0)

    def test_a_lone_object_in_empty_space_is_read_as_distant(self):
        decal = self._plane("ppDecal", 10, 10, (0, 500, 0))
        site = self._site([decal])
        self.assertIsNone(site.box)
        self.assertEqual(site.open_faces, 6)

    def test_a_shelf_over_what_moves_is_kept_clear_of(self):
        """Over the prop at eye level is 10 cm under a shelf, which then filled
        the panorama's top -- and the box's ceiling."""
        room = self._cube("ppRoom", 1000, 400, 1000, (0, 200, 0), inward=True)
        shelf = self._cube("ppShelf", 200, 10, 100, (-300, 215, -150))
        prop = self._cube("ppProp", 20, 20, 20, (-300, 100, -150))
        self._light("pointLight", (300, 350, 300))
        site = self._site([room, shelf], [prop])
        self.assertEqual(site.reason, "over what moves")
        self.assertClearOf(site.position, shelf)
        self.assertAlmostEqual(site.position[0], -300.0, delta=1.0)
        self.assertBox(site.box, [[-500, 0, -500], [500, 400, 500]])

    def test_what_surrounds_the_bake_stays_in_view_and_what_moves_does_not(self):
        """An unbaked sky dome and ground surround the bake: the probe sees
        them, and the ground is its floor. The prop beside the building moves:
        see-through."""
        building = self._cube("ppBuilding", 800, 600, 800, (0, 300, 0))
        dome = cmds.ls(cmds.polySphere(name="ppDome", radius=5000)[0], long=True)[0]
        ground = self._plane("ppGround", 20000, 20000)
        prop = self._cube("ppProp", 20, 20, 20, (700, 50, 0))
        cmds.directionalLight()
        site = self._site([building], [dome, ground, prop])
        self.assertEqual(site.hide, [prop])
        self.assertEqual(site.reason, "over what moves")
        self.assertAlmostEqual(site.box[0][1], 0.0, delta=1.0, msg="the unbaked ground")
        self.assertClearOf(site.position, building)

    def test_a_turned_room_projects_onto_its_whole_extent(self):
        """A room turned 45 degrees has no wall square to an axis; the box is
        the room's world bounds, not one wall's slant."""
        room = self._cube("ppRoom", 800, 300, 800, (0, 150, 0), inward=True)
        cmds.rotate(0, 45, 0, room)
        self._light("pointLight", (0, 250, 0))
        site = self._site([room])
        half = 400 * 2**0.5
        self.assertBox(site.box, [[-half, 0, -half], [half, 300, half]], delta=2.0)

    def test_a_room_in_metres_measures_in_metres(self):
        cmds.currentUnit(linear="m")
        self.addCleanup(cmds.currentUnit, linear="cm")
        room = self._cube("ppRoom", 10, 4, 10, (0, 2, 0), inward=True)
        prop = self._cube("ppProp", 0.2, 0.2, 0.2, (-3, 1, -1.5))
        self._light("pointLight", (3, 3.5, 3))
        site = self._site([room], [prop])
        for got, want in zip(site.position, (-3.0, 2.0, -1.5)):
            self.assertAlmostEqual(got, want, delta=0.01)
        self.assertBox(site.box, [[-5, 0, -5], [5, 4, 5]], delta=0.01)


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestSupersededMaps(MayaTkTestCase):
    """A re-bake sets aside the maps it superseded -- and only its own.

    Changing where or how maps are written (the folder, the name affix, the
    packing) gave the objects new files and left the old ones on disk, read by
    nobody: the production project held a leftover ``_1`` atlas no scene
    named. Every keep rule below is a reader a delete would have stranded.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lm_superseded_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # The project: a re-bake sets aside nothing outside it.
        previous = cmds.workspace(query=True, rootDirectory=True)
        cmds.workspace(self.tmp, openWorkspace=True)
        self.addCleanup(cmds.workspace, previous, openWorkspace=True)
        # A re-bake sets what it superseded aside -- into this machine's
        # Recycle Bin wherever the volume has one. Pinned per test to a volume
        # with none, so the leftovers land in ``_superseded`` inside the
        # scratch folder, never in the user's bin (a module fixture is not
        # enough: the suite driver never runs one, and a run filled the bin).
        # ``ptk.FileUtils.move_to_trash`` has its own tests in pythontk.
        no_trash = mock.patch.object(ptk.FileUtils, "move_to_trash", return_value=None)
        no_trash.start()
        self.addCleanup(no_trash.stop)

    @staticmethod
    def _cube(name):
        return cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]

    @staticmethod
    def _paths(paths):
        return sorted(os.path.normcase(os.path.abspath(p)) for p in paths)

    def _bake(self, objects, folder="out", suffix="_Lightmap", packing="per_object"):
        folder = os.path.join(self.tmp, folder)
        # The fake writes with cv2, which makes no folder: none, no file.
        os.makedirs(folder, exist_ok=True)
        result = LightmapBaker(resolution=64, baker=_FakeBaker()).bake(
            objects, packing=packing, output_dir=folder, suffix=suffix
        )
        # What these rules are about happens at the save: the maps reach their
        # folder, and the superseded ones go (LightmapRecords._commit_pending).
        self._commit()
        result.maps = {o: LightmapRecords._home(p) for o, p in result.maps.items()}
        if result.probe:
            result.probe = LightmapRecords._home(result.probe)
        return result

    @staticmethod
    def _commit():
        """The maps' half of a save of the open scene (DataNodes' save hook)."""
        from mayatk.node_utils.data_nodes import DataNodes

        ptk.PendingFiles.session().saving(
            cmds.file(query=True, sceneName=True) or "",
            saved_from=DataNodes._rebase_state()["scene"],
        )

    def _save_as(self, name, write=True):
        path = os.path.join(self.tmp, name)
        cmds.file(rename=path)
        if write:
            cmds.file(save=True, type="mayaAscii")
        return path

    # -- what a re-bake leaves behind ---------------------------------------

    def test_a_rebake_under_another_affix_deletes_the_old_map(self):
        cube = self._cube("supAffix")
        old = self._bake([cube]).maps[cube]

        result = self._bake([cube], suffix="_LM")

        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(result.maps[cube]))
        self.assertEqual(self._paths(result.retired), self._paths([old]))

    def test_a_rebake_into_another_folder_deletes_the_old_map(self):
        cube = self._cube("supFolder")
        old = self._bake([cube], folder="first").maps[cube]
        new = self._bake([cube], folder="second").maps[cube]
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_per_object_maps_folded_into_an_atlas_are_deleted(self):
        a, b = self._cube("supAtlasA"), self._cube("supAtlasB")
        per_object = self._bake([a, b]).maps

        atlas = self._bake([a, b], packing="atlas")

        self.assertEqual(len(set(atlas.maps.values())), 1)
        self.assertEqual(self._paths(atlas.retired), self._paths(per_object.values()))
        for path in per_object.values():
            self.assertFalse(os.path.exists(path), path)

    def test_a_map_outside_the_project_is_never_set_aside(self):
        """A map the scene once wrote OUTSIDE its project -- beside a texture
        library another project shares -- sits in that project's folder too,
        whose scenes may read it and this one cannot see: a soldering
        assembly's re-bake sent another project's room lightmap to the Recycle Bin
        (2026-10-03). Only a map inside the scene's project is set aside."""
        cube = self._cube("supOutside")
        outside = self.enterContext(
            ptk.TempArtifacts("lm_superseded_outside", policy="scoped")
        ).dir_path()
        old = LightmapRecords._home(
            LightmapBaker(resolution=64, baker=_FakeBaker())
            .bake([cube], packing="per_object", output_dir=outside, suffix="_Lightmap")
            .maps[cube]
        )
        self._commit()  # saved there

        result = self._bake([cube])  # the re-bake, inside the project

        self.assertTrue(os.path.exists(old), "another project's map was set aside")
        self.assertEqual(result.retired, [])

    def test_a_superseded_map_is_set_aside_never_deleted(self):
        """BACKLOG 2026-09-23, decided 2026-09-27: no scene can see another
        scene file's reads -- a Save As copy's source, an Explorer copy -- so
        what a re-bake retires must stay one restore away: the Recycle Bin,
        else the ``superseded`` temp store, kept a month (pinned here) --
        never a ``_superseded`` folder left in the project (2026-10-13)."""
        import glob

        cube = self._cube("supAside")
        old = self._bake([cube]).maps[cube]
        with open(old, "rb") as fh:
            baked = fh.read()

        self._bake([cube], suffix="_LM")

        found = glob.glob(
            os.path.join(
                ptk.TempArtifacts.root(),
                ptk.FileDependencies.SUPERSEDED_STORE,
                "*",
                os.path.basename(old),
            )
        )
        self.assertTrue(found, "set aside into the temp store")
        aside = max(found, key=os.path.getmtime)
        self.assertFalse(
            os.path.exists(
                os.path.join(os.path.dirname(old), ptk.FileDependencies.SUPERSEDED_DIR)
            )
        )
        self.assertFalse(os.path.exists(old))
        with open(aside, "rb") as fh:
            self.assertEqual(fh.read(), baked)

    def test_a_map_set_aside_is_never_found_again_by_the_texture_walk(self):
        """It keeps its name beside the folder it left: a walk that found it
        would bind the stale map the re-bake retired."""
        cube = self._cube("supWalk")
        old = self._bake([cube]).maps[cube]
        self._bake([cube], suffix="_LM")
        self.assertEqual(
            LightmapRecords._find_files([os.path.basename(old)], self.tmp), []
        )

    def test_a_superseded_map_goes_to_the_trash_where_there_is_one(self):
        cube = self._cube("supTrash")
        old = self._bake([cube]).maps[cube]
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)

        def trash(path):
            target = os.path.join(bin_dir, os.path.basename(path))
            os.replace(path, target)
            return target

        with mock.patch.object(ptk.FileUtils, "move_to_trash", side_effect=trash):
            result = self._bake([cube], suffix="_LM")

        self.assertEqual(self._paths(result.retired), self._paths([old]))
        self.assertTrue(os.path.isfile(os.path.join(bin_dir, os.path.basename(old))))
        self.assertFalse(
            os.path.exists(
                os.path.join(os.path.dirname(old), ptk.FileDependencies.SUPERSEDED_DIR)
            )
        )

    def test_maps_baked_before_the_first_save_are_its_own_once_saved(self):
        """BACKLOG 2026-09-23: a map committed while the scene was unsaved
        was stamped ``""``, the scene's own only while it still is -- so once
        saved, a re-bake never retired it (measured: the old map stayed,
        ``retired == []``). The first save stamps it with the file written."""
        cube = self._cube("supFirstSave")
        old = self._bake([cube]).maps[cube]
        self.assertEqual(set(LightmapRecords._writers().values()), {""})
        self._save_as("first.ma")
        self.assertEqual(set(LightmapRecords._writers().values()), {"first.ma"})

        result = self._bake([cube], suffix="_LM")

        self.assertFalse(os.path.exists(old))
        self.assertEqual(self._paths(result.retired), self._paths([old]))

    def test_a_same_place_rebake_deletes_nothing(self):
        cube = self._cube("supSame")
        path = self._bake([cube]).maps[cube]
        result = self._bake([cube])
        self.assertEqual(result.retired, [])
        self.assertTrue(os.path.exists(path))

    # -- the readers a delete would strand ------------------------------------

    def test_a_map_another_object_still_reads_is_kept(self):
        a, b = self._cube("supSharedA"), self._cube("supSharedB")
        shared = self._bake([a]).maps[a]
        LightmapRecords.commit({b: shared})

        self._bake([a], suffix="_LM")
        self.assertTrue(os.path.exists(shared), "b still reads it")

        self._bake([b], suffix="_LM")
        self.assertFalse(os.path.exists(shared), "now nothing does")

    def test_the_maps_a_save_as_copys_source_reads_are_kept(self):
        """A copy carries its source's markers and records, and the source
        still reads the maps it wrote."""
        cube = self._cube("supCopy")
        self._save_as("source.ma")
        old = self._bake([cube]).maps[cube]
        self._save_as("copy.ma", write=False)

        self._bake([cube], suffix="_LM")

        self.assertTrue(os.path.exists(old))

    def test_a_save_as_copys_bake_never_writes_over_the_maps_its_source_reads(self):
        """A copy carries its source's markers, so they name the source's maps
        as its own objects' -- a re-bake into the same folder replaced the
        file the source still reads, which then showed the copy's lighting.
        The writer record says the source wrote it: every naming path -- a
        render's name, a placed map, an atlas cell written in place -- keeps
        off it. (The fake renderer names files without the claims, so each
        path is asked directly.)"""
        cube = self._cube("supCopyWrite")
        self._save_as("source.ma")
        old = self._bake([cube]).maps[cube]
        with open(old, "rb") as fh:
            before = fh.read()
        self._save_as("copy.ma", write=False)
        claims = LightmapRecords.claims()
        folder, name = os.path.split(old)

        self.assertTrue(claims[name.lower()] - {cube}, "nothing claims it for the source")
        named = ptk.FileUtils.unique_path(
            folder, os.path.splitext(name)[0], ".exr", claims=claims, owners=[cube]
        )
        self.assertNotEqual(os.path.normcase(named), os.path.normcase(old))
        work = os.path.join(self.tmp, "work")
        os.makedirs(work)
        staged = os.path.join(work, name)
        with open(staged, "wb") as fh:
            fh.write(b"copy")
        placed = LightmapBaker()._place_unpacked({cube: (staged, None)}, folder, claims=claims)
        self.assertNotEqual(os.path.normcase(placed[cube][0]), os.path.normcase(old))
        with open(old, "rb") as fh:
            self.assertEqual(fh.read(), before, "the source's map was written over")
        # An atlas cell is written into the copy's own copy of the atlas
        # (TestRebakeChanged.test_a_copys_first_rebake_copies_its_sources_atlas...).

    def test_a_copy_never_takes_back_a_name_its_source_wrote(self):
        """The claim must not depend on this scene still reading the map. A
        copy re-baked once under other names (its markers then name only its
        own maps), and a later bake back under the source's name wrote over
        the file the source reads -- PLAYGROUND_wStands over PLAYGROUND's
        office atlas (2026-10-21). The writer record still said who wrote it."""
        cube = self._cube("supCopyBack")
        self._save_as("source.ma")
        old = self._bake([cube]).maps[cube]
        self._save_as("copy.ma")
        self._bake([cube], suffix="_LM")  # the copy's own: no marker names `old` now
        claims = LightmapRecords.claims()
        folder, name = os.path.split(old)

        self.assertIn(LightmapRecords.FOREIGN, claims.get(name.lower(), frozenset()))
        named = ptk.FileUtils.unique_path(
            folder, os.path.splitext(name)[0], ".exr", claims=claims, owners=[cube]
        )
        self.assertNotEqual(os.path.normcase(named), os.path.normcase(old))

    def test_a_measurement_bake_leaves_the_scene_and_its_maps_as_they_were(self):
        """``record=False`` (light layers, a portability check): the maps land
        in the given folder and nothing else changes -- no marker re-pointed,
        no map of the scene set aside. A layer bake recorded itself, and its
        commit set the scene's real lightmaps aside as superseded (2026-10-17)."""
        cube = self._cube("supMeasure")
        self._save_as("scene.ma")
        old = self._bake([cube]).maps[cube]
        marker = LightmapRecords._marker_info(cube)
        stage = os.path.join(self.tmp, "stage")
        os.makedirs(stage)

        result = LightmapBaker(resolution=64, baker=_FakeBaker(), record=False).bake(
            [cube], packing="per_object", output_dir=stage
        )

        self.assertTrue(os.path.exists(old), "the scene's map was set aside")
        self.assertEqual(result.retired, [])
        self.assertTrue(
            os.path.normcase(result.maps[cube]).startswith(os.path.normcase(stage))
        )
        self.assertEqual(LightmapRecords._marker_info(cube), marker)

    def test_a_renamed_scenes_own_maps_are_still_its_own(self):
        """Saved under a new name with the old file gone: nothing else can
        open it to read the maps it wrote."""
        cube = self._cube("supRenamed")
        source = self._save_as("before.ma")
        old = self._bake([cube]).maps[cube]
        self._save_as("after.ma", write=False)
        os.remove(source)

        self._bake([cube], suffix="_LM")

        self.assertFalse(os.path.exists(old))

    def test_a_renamed_scene_keeps_its_maps_when_its_old_name_comes_back(self):
        """2026-10-21: renamed, the writer record still named the old file,
        own only while nothing was there. A file back at the old name (a Save
        As back to it, a restored backup) then owned the maps: this scene's
        re-bake kept off them, and that file's wrote over what this one reads.
        The renamed scene's save writes the ownership down."""
        cube = self._cube("supRenamedBack")
        source = self._save_as("before.ma")
        old = self._bake([cube]).maps[cube]
        os.remove(source)
        self._save_as("after.ma")  # the renamed scene saved

        name = os.path.basename(old).lower()
        self.assertTrue(LightmapRecords._writers()[name].endswith("after.ma"))
        with open(source, "w") as fh:  # the old name is back
            fh.write("//Maya ASCII")
        self.assertIsNone(LightmapRecords.foreign_writer(name))
        self._bake([cube], suffix="_LM")
        self.assertFalse(os.path.exists(old), "still this scene's to retire")

    def test_a_map_committed_before_writers_were_recorded_is_kept(self):
        """Nobody's to delete: it may be another scene's (a folder lifted off
        a legacy marker lands in the record the same way)."""
        cube = self._cube("supUnstamped")
        old = self._bake([cube]).maps[cube]
        LightmapRecords._save_writers({})

        self._bake([cube], suffix="_LM")

        self.assertTrue(os.path.exists(old))

    def test_a_map_a_referenced_object_reads_is_kept(self):
        """Its own file may name the same map."""
        cube = self._cube("supReferenced")
        old = self._bake([cube]).maps[cube]
        with mock.patch.object(LightmapRecords, "_referenced_nodes", side_effect=set):
            self._bake([cube], suffix="_LM")
        self.assertTrue(os.path.exists(old))

    def test_a_block_that_raises_deletes_nothing(self):
        cube = self._cube("supRaise")
        old = self._bake([cube]).maps[cube]
        new = os.path.join(self.tmp, "new_Lightmap.exr")
        shutil.copy(old, new)

        with self.assertRaises(RuntimeError):
            with LightmapRecords.superseding([cube]):
                LightmapRecords.commit({cube: new})
                raise RuntimeError("the bake failed after its commit")

        self.assertTrue(os.path.exists(old))

    def test_a_revert_deletes_nothing(self):
        cube = self._cube("supRevert")
        path = self._bake([cube]).maps[cube]
        LightmapRecords.revert([cube])
        self.assertTrue(os.path.exists(path))

    # -- the reflection probe follows the same rules ----------------------------

    def test_a_rebake_into_another_folder_sets_the_old_probe_aside(self):
        """Same scene, so the same probe NAME, in another folder: the probe was
        retired only when its name changed, and the old one stayed, read by
        nothing. Added: 2026-10-04"""
        cube = self._cube("supProbeFolder")
        old = self._bake([cube], folder="first").probe
        new = self._bake([cube], folder="second").probe
        self.assertEqual(os.path.basename(old), os.path.basename(new))
        self.assertTrue(os.path.isfile(new))
        self.assertFalse(os.path.exists(old), "the old probe stayed behind")
        import glob

        aside = glob.glob(
            os.path.join(
                ptk.TempArtifacts.root(),
                ptk.FileDependencies.SUPERSEDED_STORE,
                "*",
                os.path.basename(old),
            )
        )
        self.assertTrue(aside, "set aside, never deleted")

    def test_a_same_place_rebake_keeps_the_probe(self):
        cube = self._cube("supProbeSame")
        old = self._bake([cube]).probe
        new = self._bake([cube]).probe
        self.assertEqual(self._paths([old]), self._paths([new]))
        self.assertTrue(os.path.isfile(new))
        self.assertFalse(
            os.path.exists(
                os.path.join(os.path.dirname(new), ptk.FileDependencies.SUPERSEDED_DIR)
            )
        )

    def test_the_probe_baked_before_the_first_save_is_set_aside_once_saved(self):
        """``untitled_Probe.exr`` is the scene's own once saved (the first save
        stamps it), and the saved scene's probe takes its place."""
        cube = self._cube("supProbeSave")
        old = self._bake([cube]).probe
        self._save_as("probe_first.ma")
        new = self._bake([cube]).probe
        self.assertNotEqual(os.path.basename(old), os.path.basename(new))
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(new))

    def test_the_probe_a_save_as_copys_source_reads_is_kept(self):
        cube = self._cube("supProbeCopy")
        self._save_as("probe_source.ma")
        old = self._bake([cube]).probe
        self._save_as("probe_copy.ma", write=False)
        new = self._bake([cube]).probe
        self.assertNotEqual(os.path.basename(old), os.path.basename(new))
        self.assertTrue(os.path.isfile(old), "the source scene still reads it")


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
# The panel's API. Its UI is tentacle's shared Lightmap Baker (its test,
# ``tentacle/test/test_lightmap_baker_panel.py``, drives the panel); these pin
# what it calls on the engine.
# ---------------------------------------------------------------------------


class TestPanelApi(MayaTkTestCase):
    """What tentacle's Lightmap Baker panel calls on :class:`LightmapBaker`:
    what a Scope word gathers, the selection its rows and map picks make, the
    preset pointer's rename, its Samples range and Adaptive gate, and the
    default folders it resolves the Output Directory against."""

    def test_scene_scope_reaches_every_instance(self):
        """An instanced shape is ONE node under several transforms, and a
        listing of mesh nodes names it once: the Scene scope reached only each
        shape's first copy -- 40 of the production room's walls and props
        would have gone unbaked, and unreported."""
        cmds.instance(cmds.polyCube(name="scnInstSrc")[0], name="scnInstCopy")
        group = cmds.group(cmds.polyCube(name="scnGrpChild")[0], name="scnGrp")
        cmds.instance(group, name="scnGrpCopy")

        pool = LightmapBaker.scope_meshes("scene")

        for path in (
            "|scnInstSrc",
            "|scnInstCopy",
            "|scnGrp|scnGrpChild",
            "|scnGrpCopy|scnGrpChild",
        ):
            self.assertIn(path, pool)

    def test_visible_scope_reaches_every_instance_it_can_see(self):
        cmds.instance(cmds.polyCube(name="visInstSrc")[0], name="visInstCopy")
        group = cmds.group(cmds.polyCube(name="visGrpChild")[0], name="visGrp")
        cmds.instance(group, name="visGrpCopy")
        hidden = cmds.polyCube(name="visHiddenCube")[0]
        cmds.setAttr(f"{hidden}.visibility", False)
        templated = cmds.polyCube(name="visTemplatedCube")[0]
        cmds.setAttr(f"{templated}.template", True)

        pool = LightmapBaker.scope_meshes("visible")

        for path in (
            "|visInstSrc",
            "|visInstCopy",
            "|visGrp|visGrpChild",
            "|visGrpCopy|visGrpChild",
        ):
            self.assertIn(path, pool)
        self.assertNotIn("|visHiddenCube", pool)
        self.assertNotIn("|visTemplatedCube", pool)

    def test_selected_scope_reads_the_selection_as_meshes(self):
        """A group stands for the meshes under it and a selected light is no
        mesh; an unknown word reads the selection rather than widening."""
        group = cmds.group(empty=True, name="selScopeGroup")
        cmds.parent(cmds.polyCube(name="selScopeCube")[0], group)
        light = cmds.shadingNode("areaLight", asLight=True)
        cmds.select([group, light], replace=True)
        meshes = ["|selScopeGroup|selScopeCube"]
        self.assertEqual(LightmapBaker.scope_meshes("selected"), meshes)
        self.assertEqual(LightmapBaker.scope_meshes("bogus"), meshes)
        cmds.select(clear=True)
        self.assertEqual(LightmapBaker.scope_meshes("selected"), [])

    def test_the_scope_lights_are_its_own(self):
        """What lights a bake with Include Scene Lighting off: the selected
        group's light under Selected, every scene light under Scene."""
        group = cmds.group(empty=True, name="slotModule")
        cmds.parent(cmds.polyCube(name="slotModuleCube")[0], group)
        inside = cmds.shadingNode("areaLight", asLight=True)
        inside = cmds.ls(cmds.parent(inside, group)[0], long=True)[0]
        outside = cmds.ls(cmds.shadingNode("areaLight", asLight=True), long=True)[0]
        cmds.select(group, replace=True)

        held = set(cmds.ls(LightmapBaker.scope_lights("selected"), long=True))
        inside_shape = cmds.listRelatives(inside, shapes=True, fullPath=True)[0]
        outside_shape = cmds.listRelatives(outside, shapes=True, fullPath=True)[0]
        self.assertTrue({inside, inside_shape} & held, held)
        self.assertFalse({outside, outside_shape} & held, held)

        every = set(cmds.ls(LightmapBaker.scope_lights("scene"), long=True))
        for light in LightUtils.all_lights():
            self.assertIn(light, every)

    def test_select_replaces_the_selection_with_what_exists(self):
        """The set rows and map picks: names or paths, hidden ones too; a name
        that is gone is left out of what it returns, and nothing clears the
        selection -- a pick that finds nothing must not leave the last pick
        standing."""
        a = cmds.ls(cmds.polyCube(name="pickA")[0], long=True)[0]
        hidden = cmds.ls(cmds.polyCube(name="pickHidden")[0], long=True)[0]
        cmds.setAttr(f"{hidden}.visibility", False)
        cmds.select(clear=True)

        picked = LightmapBaker.select([a, hidden, "|noSuchNode"])

        self.assertEqual(sorted(picked), sorted([a, hidden]))
        self.assertEqual(sorted(cmds.ls(selection=True, long=True)), sorted([a, hidden]))
        self.assertEqual(LightmapBaker.select([]), [])
        self.assertEqual(cmds.ls(selection=True), [])

    def test_the_preset_pointer_follows_a_renamed_tier(self):
        self.assertEqual(LightmapBaker.preset_name("mobile"), "mobile")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.assertEqual(LightmapBaker.preset_name("quest"), "mobile")
            self.assertEqual(LightmapBaker.from_preset("quest").resolution, 1024)

    def test_the_samples_range_holds_every_shipped_tier(self):
        store = LightmapBaker.preset_store()
        low, high = LightmapBaker.SAMPLES_RANGE
        for tier in ("preview", "mobile", "desktop", "hero"):
            self.assertTrue(low <= store.load(tier)["samples"] <= high, tier)

    def test_arnold_samples_adaptively_on_the_gpu_only(self):
        self.assertEqual(LightmapBaker.ADAPTIVE_DEVICES, ("AUTO", "GPU"))

    def test_texture_baker_default_output_dir_is_absolute(self):
        # The panel leans on it as a base, so a relative return would
        # reintroduce the CWD bug one level up.
        out = lmb_module.TextureBaker.default_output_dir("lightmaps")
        self.assertTrue(os.path.isabs(out), out)
        self.assertEqual(os.path.basename(out), "lightmaps")

    def test_the_default_folders_follow_the_scenes_project_not_the_active_one(self):
        """A scene saved in project A while B is active bakes into A's
        sourceimages (its ``lightmaps`` / ``baked`` subfolder): the maps belong
        to the scene that references them. Regression: the bake defaulted to
        ``<scene dir>/baked_lighting`` and the panel to the ACTIVE project's
        sourceimages -- two places, neither the scene project's texture
        folder. Added: 2026-10-13"""
        root = os.path.realpath(tempfile.mkdtemp(prefix="lm_scene_proj_"))
        self.addCleanup(shutil.rmtree, root, True)
        ptk.Workspace.create(root, create_dirs=False)
        os.makedirs(os.path.join(root, "scenes"))
        cmds.file(rename=os.path.join(root, "scenes", "room.ma"))
        textures = os.path.join(root, "sourceimages")
        for subdir in ("lightmaps", "baked"):
            self.assertEqual(
                os.path.normcase(lmb_module.TextureBaker.default_output_dir(subdir)),
                os.path.normcase(os.path.join(textures, subdir)),
            )
        # What the panel resolves its Output Directory against.
        self.assertEqual(
            os.path.normcase(DataNodes.scene_source_images_dir()),
            os.path.normcase(textures),
        )


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
        # both read the live workspace, and the folder record is spelled from
        # the project the scene FILE lives in (2026-09-23) -- so the scene is
        # saved into it, marked as a project.
        original_ws = cmds.workspace(q=True, rd=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))
        self.root = os.path.join(self.tmp, "project")
        self.si = os.path.join(self.root, "sourceimages")
        os.makedirs(self.si, exist_ok=True)
        os.makedirs(os.path.join(self.root, "scenes"), exist_ok=True)
        with open(os.path.join(self.root, "workspace.mel"), "w") as fh:
            fh.write("//Maya 2025 Project Definition\n")
        cmds.workspace(self.root, openWorkspace=True)
        cmds.file(rename=os.path.join(self.root, "scenes", "deps.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)
        self.addCleanup(cmds.file, new=True, force=True)  # off the file first
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

    def _stored_dir(self, obj):
        """The folder exactly as the scene stores it for the map *obj* reads
        (the portable spelling): the private folder record, never the marker,
        which rides the FBX (``TestLightmapFolderHints``)."""
        info = json.loads(cmds.getAttr(f"{obj}.{LightmapBaker.LIGHTMAP_INFO_ATTR}"))
        return LightmapRecords._folder_hint(info, LightmapRecords._folder_hints(), obj)

    def _map_dir(self, obj):
        """That folder resolved on this machine (what a consumer joins)."""
        info = json.loads(cmds.getAttr(f"{obj}.{LightmapBaker.LIGHTMAP_INFO_ATTR}"))
        return LightmapRecords._resolved_dir(self._stored_dir(obj), info.get("map", ""))

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
        # "dir" is the RECORDED folder: spelled from the scene's own project
        # (a chain to this folder beside it), resolving to where the map is.
        self.assertEqual(dep["dir"], "../bake")
        self.assertTrue(
            self._same(
                LightmapRecords._resolved_dir(dep["dir"], dep["map"]),
                os.path.dirname(path),
            )
        )

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
        self.assertTrue(self._same(self._map_dir(cube), os.path.dirname(found)))
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
        self.assertTrue(self._same(self._map_dir(cube), self.si))
        self.assertTrue(self._same(self._lead_dir(), self.si))
        self.assertEqual(
            self.baker.lightmap_dependencies()[0]["found_by"],
            LightmapRecords.FOUND_BY_HINT,
        )

    def test_relocate_dry_run_plans_and_changes_nothing(self):
        cube = self._cube("planned")
        src = self._file("elsewhere", "planned_LightMap.exr")
        self._commit(cube, src)
        before = self._map_dir(cube)

        result = self.baker.relocate_lightmaps(self.si, dry_run=True)

        self.assertEqual(len(result["relocate"]), 1)
        self.assertEqual(result["copied"], [])
        self.assertEqual(result["updated"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.si, "planned_LightMap.exr")))
        self.assertEqual(self._map_dir(cube), before)

    def test_relocate_searches_the_source_folder_for_a_missing_map(self):
        cube = self._cube("searched")
        self._commit(cube, os.path.join(self.tmp, "gone", "searched_LightMap.exr"))
        self._file("archive", "deep", "searched_LightMap.exr")

        result = self.baker.relocate_lightmaps(
            self.si, source_dir=os.path.join(self.tmp, "archive")
        )

        self.assertEqual(result["missing"], [])
        self.assertTrue(os.path.isfile(os.path.join(self.si, "searched_LightMap.exr")))
        self.assertTrue(self._same(self._map_dir(cube), self.si))

    def test_relocate_move_removes_the_original(self):
        cube = self._cube("movedmap")
        src = self._file("elsewhere", "movedmap_LightMap.exr")
        self._commit(cube, src)

        self.baker.relocate_lightmaps(self.si, mode="move")

        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.isfile(os.path.join(self.si, "movedmap_LightMap.exr")))

    def test_relocate_move_copies_a_map_another_scene_reads(self):
        """2026-10-21 audit: a Find & Move in a Save As copy moved the map its
        source still reads; only this scene's records followed, and the source
        opened unlit. A map another scene wrote is copied instead."""
        cube = self._cube("sharedmap")
        src = self._file("elsewhere", "sharedmap_LightMap.exr")
        self._commit(cube, src)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(rename=os.path.join(self.root, "scenes", "deps_copy.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)

        self.baker.relocate_lightmaps(self.si, mode="move")

        self.assertTrue(os.path.isfile(src), "the source scene's map was moved")
        self.assertTrue(self._same(self._map_dir(cube), self.si))

    def test_relocate_leaves_a_map_already_at_the_destination_in_place(self):
        cube = self._cube("inplace")
        open(os.path.join(self.si, "inplace_LightMap.exr"), "wb").close()
        self._commit(cube, os.path.join(self.tmp, "gone", "inplace_LightMap.exr"))

        result = self.baker.relocate_lightmaps(self.si)

        self.assertEqual(result["copied"], [])
        self.assertEqual(len(result["in_place"]), 1)
        self.assertTrue(self._same(self._map_dir(cube), self.si))

    def test_relocate_names_what_it_could_not_find(self):
        cube = self._cube("nowhere")
        self._commit(cube, os.path.join(self.tmp, "gone", "nowhere.exr"))

        result = self.baker.relocate_lightmaps(self.si, source_dir=self.tmp)

        self.assertEqual([d["map"] for d in result["missing"]], ["nowhere.exr"])
        self.assertEqual(result["updated"], 0)

    # -- the portable spelling ---------------------------------------------

    def test_a_map_inside_the_project_is_recorded_project_relative(self):
        """Asked 2026-08-26: a teammate mounts the cloud project on another
        drive, so an absolute folder resolves nowhere there. The folder record
        stores the form relative to the scene's own project; the manifest names
        no folder at all, and what a build on THIS machine is handed
        (``search_dirs``) is the absolute one."""
        cube = self._cube("portable")
        os.makedirs(os.path.join(self.si, "lm"), exist_ok=True)
        path = os.path.join(self.si, "lm", "portable_LightMap.exr")
        open(path, "wb").close()
        self._commit(cube, path)

        self.assertEqual(self._stored_dir(cube), "sourceimages/lm")
        self.assertTrue(self._same(self._lead_dir(), os.path.dirname(path)))
        manifest = self._manifest()
        self.assertTrue(manifest.get("objects"), manifest)
        self.assertFalse({"dir", "dirs"} & set(manifest), manifest)
        (dep,) = self.baker.lightmap_dependencies()
        self.assertEqual(dep["found_by"], LightmapRecords.FOUND_BY_HINT)
        self.assertTrue(self._same(dep["path"], path))

    def test_a_map_beside_the_project_is_a_chain_from_it(self):
        """BACKLOG 2026-09-22, decided 2026-09-23: a shared folder beside the
        project is spelled from the scene's OWN project -- a ``../`` chain, so a
        teammate who mounts the project with its neighbours resolves it (only
        another drive stays absolute, ``ptk.FileUtils.portable_path``)."""
        cube = self._cube("external")
        path = self._file("elsewhere", "external_LightMap.exr")
        self._commit(cube, path)

        self.assertEqual(self._stored_dir(cube), "../elsewhere")
        self.assertTrue(self._same(self._map_dir(cube), os.path.dirname(path)))

    def test_normalize_lightmap_paths_round_trips(self):
        cube = self._cube("normalized")
        os.makedirs(os.path.join(self.si, "lm"), exist_ok=True)
        path = os.path.join(self.si, "lm", "normalized_LightMap.exr")
        open(path, "wb").close()
        self._commit(cube, path)

        self.assertEqual(self.baker.normalize_lightmap_paths(relative=False), 1)
        self.assertTrue(os.path.isabs(self._stored_dir(cube)))
        self.assertEqual(self.baker.normalize_lightmap_paths(), 1)
        self.assertEqual(self._stored_dir(cube), "sourceimages/lm")
        self.assertEqual(self.baker.normalize_lightmap_paths(), 0, "idempotent")
        self.assertTrue(self._same(self._map_dir(cube), os.path.dirname(path)))

    def test_relocating_into_the_project_stores_the_relative_spelling(self):
        cube = self._cube("landed")
        self._commit(cube, self._file("elsewhere", "landed_LightMap.exr"))

        self.baker.relocate_lightmaps(self.si)

        self.assertEqual(self._stored_dir(cube), "sourceimages")
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
        self._assert_leads(
            LightmapRecords.search_dirs(), [os.path.dirname(one), self.si]
        )

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


class TestLightmapFolderHints(MayaTkTestCase):
    """A map's folder never rides its marker (BACKLOG 2026-09-19).

    A marker is a node attribute, so Maya's FBX exporter writes it as a user
    property: its ``dir`` put the authoring machine's build folder on every
    deliverable (measured 2026-09-23: an ASCII FBX carried
    ``&quot;dir&quot;: &quot;O:/...&quot;``). The folder lives in the private
    ``LIGHTMAP_DIRS`` record on ``data_internal``; a marker baked before the
    move is lifted by a bake's migration and by every export bracket.
    """

    def setUp(self):
        super().setUp()
        self.store = ptk.TempArtifacts("mayatk_lm_folder_hints", policy="scoped")
        self.addCleanup(self.store.cleanup)
        self.maps = self.store.dir_path(name="maps")

    def _lit(self, name):
        cube = cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, create=True, uvSet="lightmap")
        path = os.path.join(self.maps, f"{name}_LightMap.exr")
        open(path, "wb").close()
        LightmapRecords.commit({cube: path})
        return cube, path

    @staticmethod
    def _marker(node):
        return json.loads(cmds.getAttr(f"{node}.{LightmapRecords.LIGHTMAP_INFO_ATTR}"))

    @staticmethod
    def _same(a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(
            os.path.abspath(b)
        )

    def _found_by(self):
        return [
            (d["map"], d["found_by"])
            for d in LightmapRecords.lightmap_dependencies(search_dirs=[], walk=False)
        ]

    def test_a_commit_writes_no_folder_onto_the_marker(self):
        cube, _path = self._lit("fh_box")
        self.assertNotIn("dir", self._marker(cube))
        folder = LightmapRecords._folder_hints().get("fh_box_lightmap.exr", "")
        self.assertTrue(
            self._same(
                LightmapRecords._resolved_dir(folder, "fh_box_LightMap.exr"), self.maps
            ),
            folder,
        )
        self.assertEqual(
            self._found_by(), [("fh_box_LightMap.exr", LightmapRecords.FOUND_BY_HINT)]
        )

    def test_an_export_ships_a_legacy_marker_without_its_folder(self):
        import maya.mel as mel

        from mayatk.env_utils.fbx_utils import FbxUtils

        cube, path = self._lit("fh_legacy")
        legacy = dict(self._marker(cube), dir=LightmapRecords._portable_dir(path))
        LightmapRecords._write_marker(cube, legacy)
        LightmapRecords._save_folder_hints({})
        self.assertEqual(
            self._found_by(),
            [("fh_legacy_LightMap.exr", LightmapRecords.FOUND_BY_HINT)],
            "a legacy marker's folder must still resolve before any lift",
        )
        prepare, finish = FbxUtils.stagers(["lightmap_folder_hints"])[
            "lightmap_folder_hints"
        ]
        self.assertTrue(callable(prepare))
        self.assertIsNone(finish, "the lift is one way: nothing to restore")

        FbxUtils.load_plugin()
        self.addCleanup(mel.eval, "FBXResetExport")
        mel.eval("FBXResetExport")
        mel.eval("FBXExportInAscii -v true")
        out = self.store.path(".fbx", name="fh_legacy")
        cmds.select(cube)
        with FbxUtils.export_prepared():
            cmds.file(
                out,
                force=True,
                options="v=0;",
                type="FBX export",
                exportSelected=True,
                preserveReferences=False,
            )
        with open(out, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        self.assertIn("fh_legacy_LightMap.exr", text, "the marker itself still ships")
        self.assertNotIn("&quot;dir&quot;", text)
        self.assertNotIn('"dir"', text)
        self.assertNotIn("dir", self._marker(cube))
        self.assertEqual(
            LightmapRecords._folder_hints().get("fh_legacy_lightmap.exr"),
            legacy["dir"],
        )

    def test_the_records_folder_wins_and_a_second_lift_is_a_no_op(self):
        cube, _path = self._lit("fh_win")
        LightmapRecords._write_marker(cube, dict(self._marker(cube), dir="stale/x"))
        self.assertEqual(LightmapRecords.migrate_folder_hints(), [cube])
        folder = LightmapRecords._folder_hints().get("fh_win_lightmap.exr", "")
        self.assertTrue(
            self._same(
                LightmapRecords._resolved_dir(folder, "fh_win_LightMap.exr"), self.maps
            ),
            folder,
        )
        self.assertEqual(LightmapRecords.migrate_folder_hints(), [])

    def test_a_bakes_migration_lifts_the_folder_too(self):
        cube, path = self._lit("fh_bake")
        LightmapRecords._write_marker(
            cube, dict(self._marker(cube), dir=LightmapRecords._portable_dir(path))
        )
        LightmapRecords._save_folder_hints({})
        self.assertIn(cube, LightmapRecords.migrate_legacy([cube]))
        self.assertNotIn("dir", self._marker(cube))
        self.assertIn("fh_bake_lightmap.exr", LightmapRecords._folder_hints())

    def test_a_revert_drops_the_reverted_maps_folders(self):
        self._lit("fh_keep")
        dropped, _path = self._lit("fh_drop")
        LightmapRecords.revert([dropped])
        self.assertEqual(
            sorted(LightmapRecords._folder_hints()), ["fh_keep_lightmap.exr"]
        )

    def test_the_folder_record_crosses_a_bridge_handoff(self):
        """Portable: the bridge's sidecar carries the record, so a scene pulled
        into Blender (or back) still finds its maps -- which the marker's own
        folder used to carry across."""
        from mayatk.node_utils.data_nodes import DataNodes

        self._lit("fh_cross")
        folder = LightmapRecords._folder_hints()["fh_cross_lightmap.exr"]
        sections = DataNodes.transfer_sections()
        self.assertEqual(
            sections.get("records", {}).get("lightmap_dirs"),
            {"fh_cross_lightmap.exr": folder},
        )
        cmds.file(new=True, force=True)
        DataNodes.receive_sections(sections)
        self.assertEqual(
            LightmapRecords._folder_hints(), {"fh_cross_lightmap.exr": folder}
        )

    def test_a_referenced_modules_own_record_resolves_its_markers(self):
        """A module baked in its own scene records its maps in ITS
        ``data_internal``; the host reads that record under the reference's
        namespace, since the markers no longer carry a folder."""
        self._lit("fh_ref")
        module = self.store.path(".ma", name="fh_module")
        cmds.file(rename=module)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(module, reference=True, namespace="MOD")
        self.assertEqual(
            self._found_by(), [("fh_ref_LightMap.exr", LightmapRecords.FOUND_BY_HINT)]
        )
        self.assertEqual(LightmapRecords._folder_hints(), {}, "the host's own record")


class _ValueBaker(_FakeBaker):
    """A fake bake whose every map is ONE flat value, its island wholly covered.

    What a cell holds after the pack is then known exactly: a test tells a
    cell written in place from one left as it was. *values* maps an object's
    leaf name to its value (1.0 by default); change it between bakes.
    """

    def __init__(self, values=None):
        super().__init__()
        self.values = dict(values or {})
        self.baked = []

    def bake(self, objects, output_dir=None, prefix="", suffix="", **_kwargs):
        cv2, np = _cv2()
        out = {}
        for obj in objects:
            long_name = cmds.ls(obj, long=True)[0]
            leaf = long_name.rsplit("|", 1)[-1]
            img = np.full((16, 16, 4), float(self.values.get(leaf, 1.0)), np.float32)
            img[..., 3] = 1.0
            path = os.path.join(output_dir, f"{prefix}{leaf}{suffix}.exr")
            cv2.imwrite(path, img)
            out[long_name] = path
            self.baked.append(long_name)
        return out


@unittest.skipUnless(HAVE_CV2, "cv2/OpenEXR unavailable")
class TestRebakeChanged(MayaTkTestCase):
    """Re-bake Changed: what a map was baked against is recorded, so a re-bake
    redoes only what changed and what it shades -- and writes those back into
    the cells they own in their atlas, each material keeping ONE atlas.

    Before it, a partial re-bake of an atlas group packed the subset into an
    atlas of its own (``_1``) at another texel density, and nothing said what
    had changed since the last bake. A fake bake renders a flat value per
    object, so a cell written in place is told from one left as it was.
    """

    ATLAS = "Crate_Lightmap.exr"

    def setUp(self):
        super().setUp()
        self.store = self.enterContext(ptk.TempArtifacts("lm_rebake", policy="scoped"))
        self.out = self.store.dir_path(name="maps")
        self.fake = _ValueBaker()
        self.addCleanup(LightmapExcludeSet.clear)

    # -- fixtures ---------------------------------------------------------
    @staticmethod
    def _crate_sg(name="rcCrate", texture="Crate"):
        """A material whose texture set names its atlas ``<texture>_Lightmap``."""
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        mat = cmds.shadingNode("lambert", asShader=True, name=f"{name}Mat")
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        fn = cmds.shadingNode("file", asTexture=True, name=f"{name}File")
        cmds.setAttr(
            f"{fn}.fileTextureName", f"C:/tex/{texture}_BaseColor.png", type="string"
        )
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)
        return sg

    def _crates(self, names=("rcA", "rcB", "rcC"), spacing=10.0, sg=None):
        """Cubes on one material, *spacing* apart: far enough that a change to
        one reaches none of the others."""
        sg = sg or self._crate_sg()
        out = []
        for i, name in enumerate(names):
            cube = cmds.polyCube(name=name)[0]
            cmds.move(i * spacing, 0, 0, cube)
            shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
            cmds.sets(shape, edit=True, forceElement=sg)
            out.append(cmds.ls(cube, long=True)[0])
        return out

    def _baker(self, **kwargs):
        return LightmapBaker(
            resolution=64, baker=self.fake, reflection_probe=False, **kwargs
        )

    def _bake(self, objects, **kwargs):
        result = self._baker().bake(
            objects, packing="atlas", output_dir=self.out, **kwargs
        )
        self.assertTrue(result.maps, result.refused)
        return result

    def _rebake(self, **kwargs):
        return self._baker().rebake_changed(output_dir=self.out, **kwargs)

    def _report(self, **kwargs):
        return self._baker().stale_report(**kwargs)

    def _atlas(self, name=None):
        """The atlas as the scene reads it: its copy waiting for the save, else
        the folder's (``LightmapRecords._stage``)."""
        path = os.path.join(self.out, name or self.ATLAS)
        staged = LightmapRecords._stage(path)
        return _read(staged if os.path.isfile(staged) else path)[..., :3]

    @staticmethod
    def _cell(atlas, obj, size=64):
        """*obj*'s cell in *atlas*, by the cell the record holds for it."""
        cell = LightmapRecords.bake_state()["objects"][obj]["cell"]
        row0, row1, col0, col1 = ptk.ImgUtils.atlas_pixel_rects([cell], size)[0]
        return atlas[row0:row1, col0:col1]

    def _files(self):
        """The maps the folder holds once the scene is saved: its own, and the
        ones waiting in the session for it."""
        session = LightmapRecords._pending().folder(self.out)
        return sorted(
            {f for d in (self.out, session) for f in os.listdir(d) if f.lower().endswith(".exr")}
        )

    def _disk(self):
        """``{file name: bytes}`` of the folder itself -- what the scene file
        on disk reads."""
        out = {}
        for name in sorted(os.listdir(self.out)):
            if name.lower().endswith(".exr"):
                with open(os.path.join(self.out, name), "rb") as fh:
                    out[name] = fh.read()
        return out

    # -- one material, two shading groups ----------------------------------
    def _split(self, same_shader: bool, legacy: bool = False):
        """rcA and rcB on one material, rcC on a second shading group -- around
        the SAME material (*same_shader*: two shading groups, one material, an
        FBX import's habit) or around a duplicate of it -- baked together.
        *legacy* bakes as the baker did before it grouped by what a group
        renders: one atlas per shading group, ``Crate_Lightmap`` and
        ``Crate_Lightmap_1``. Returns the objects and each one's shading
        group."""
        sg = self._crate_sg()
        if same_shader:
            other = cmds.sets(
                renderable=True, noSurfaceShader=True, empty=True, name="rcSplitSG"
            )
            shader = MatUtils._sg_shaders(sg)[0]
            cmds.connectAttr(f"{shader}.outColor", f"{other}.surfaceShader", force=True)
        else:
            other = self._crate_sg(name="rcDup")
        a, b = self._crates(("rcA", "rcB"), sg=sg)
        (c,) = self._crates(("rcC",), sg=other)
        cmds.move(20, 0, 0, c)
        groups = {o: LightmapRecords._primary_material(o) for o in (a, b, c)}
        with contextlib.ExitStack() as stack:
            if legacy:
                stack.enter_context(
                    mock.patch.object(
                        LightmapBaker,
                        "_atlas_keys",
                        classmethod(lambda cls, objs: dict(groups)),
                    )
                )
            self._bake([a, b, c])
        return [a, b, c], groups

    def _maps_read(self, objects):
        reads = {t: p for t, _n, p in LightmapRecords._reads()}
        return {os.path.basename(reads[o]) for o in objects}

    def test_a_split_material_bakes_one_atlas_and_keeps_its_shading_groups(self):
        """The production room's LX2000_Lightmap and LX2000_Lightmap_1: one
        material in two shading groups, or a duplicate of it, baked an atlas
        per shading group. Grouped by what a group renders, one bake packs
        them into one atlas -- and never touches the shading the exporter
        reads. Changed: 2026-10-22 (was a Merge Duplicate Materials step that
        reassigned the materials and still needed a re-bake)."""
        for same_shader in (True, False):
            with self.subTest(same_shader=same_shader):
                cmds.file(new=True, force=True)
                self.out = self.store.dir_path(name=f"fresh_{same_shader}")
                objects, groups = self._split(same_shader)
                self.assertEqual(self._maps_read(objects), {self.ATLAS})
                for obj, sg in groups.items():
                    self.assertEqual(LightmapRecords._primary_material(obj), sg)
                self.assertEqual(self._report().targets, ())

    def test_an_existing_split_is_named_and_combines_without_a_render(self):
        """A scene baked before the grouping keeps its two maps: both rows
        read *split*, naming the materials (two: a duplicate still in the
        scene), and no object re-bakes for it. Combine packs the group into
        one atlas from the lighting the maps hold -- nothing rendered, each
        cell its own light, each object still dated by its bake -- and merges
        the duplicate materials. Changed: 2026-10-23 (a split re-baked)."""
        for same_shader in (True, False):
            with self.subTest(same_shader=same_shader):
                cmds.file(new=True, force=True)
                self.out = self.store.dir_path(name=f"legacy_{same_shader}")
                self.fake.values = {"rcA": 0.25, "rcB": 0.5, "rcC": 0.75}
                (a, b, c), groups = self._split(same_shader, legacy=True)
                names = {self.ATLAS, "Crate_Lightmap_1.exr"}
                self.assertEqual(self._maps_read([a, b, c]), names)
                report = self._report()
                self.assertEqual(report.targets, (), "a split is no re-bake target")
                self.assertEqual(set(report.split), names)
                self.assertEqual(
                    len(report.split_materials[self.ATLAS]), 1 if same_shader else 2
                )
                rows = self._baker().bake_maps(report)
                self.assertEqual({r["state"] for r in rows}, {"split"}, rows)
                self.assertTrue(all("Combine" in r["hint"] for r in rows), rows)
                dated = {o: LightmapRecords.bake_state()["objects"][o]["baked_at"] for o in (a, b, c)}
                self.fake.baked.clear()
                result = self._baker().combine()
                self.assertEqual(self.fake.baked, [], "nothing rendered")
                self.assertEqual(self._maps_read([a, b, c]), {self.ATLAS})
                atlas = self._atlas()
                for obj, value in ((a, 0.25), (b, 0.5), (c, 0.75)):
                    cell = self._cell(atlas, obj, size=atlas.shape[0])
                    self.assertAlmostEqual(float(cell.mean()), value, places=2, msg=obj)
                state = LightmapRecords.bake_state()["objects"]
                self.assertEqual({o: state[o]["baked_at"] for o in (a, b, c)}, dated)
                shader = LightmapBaker._surface_shader
                self.assertEqual(
                    shader(LightmapRecords._primary_material(c)),
                    shader(LightmapRecords._primary_material(a)),
                    "the duplicate is merged",
                )
                self.assertEqual(len(result.files), 1)
                after = self._report()
                self.assertEqual((after.targets, dict(after.split)), ((), {}), after.lines())

    def test_combine_sizes_end_at_the_one_that_keeps_every_cell(self):
        """The sizes Combine offers start at the largest map the split reads
        and double until every object keeps its cell's detail. Added:
        2026-10-23"""
        self._split(True, legacy=True)
        sizes = self._baker().combine_sizes()
        self.assertEqual(sizes[0][0], 64, sizes)
        self.assertEqual([s for s, _k in sizes], sorted(s for s, _k in sizes))
        self.assertGreaterEqual(sizes[-1][1], LightmapBaker.COMBINE_KEEP, sizes)
        self.assertTrue(all(k < LightmapBaker.COMBINE_KEEP for _s, k in sizes[:-1]))
        # A map that cannot be read sizes nothing: never a 1 px atlas.
        with mock.patch.object(LightmapBaker, "_map_dims", return_value=(0, 0)):
            self.assertEqual(self._baker().combine_sizes()[0][0], 64)

    def test_combine_reports_each_cut_and_a_cancel_writes_nothing(self):
        """Combine runs under the footer's progress: each object cut is
        reported, and a cancel stops before any file or record is written.
        Added: 2026-10-23"""
        (a, b, c), _groups = self._split(True, legacy=True)
        files, reads = self._files(), self._maps_read([a, b, c])
        seen = []
        result = self._baker().combine(
            on_progress=lambda done, total, name: seen.append((done, total, name)) or False
        )
        self.assertEqual(seen, [(0, 3, "rcA")])
        self.assertIn("cancelled", result.refused)
        self.assertEqual((self._files(), self._maps_read([a, b, c])), (files, reads))

    def test_combine_never_writes_over_a_map_this_scene_did_not_write(self):
        """A map with no writer record (older than it, or another scene's)
        may be read elsewhere: Combine packs into a file of its own and
        leaves it on disk. Added: 2026-10-23"""
        (a, b, c), _groups = self._split(True, legacy=True)
        # The copy the scene reads: the bake waits in the session for a save.
        atlas = LightmapRecords._stage(os.path.join(self.out, self.ATLAS))
        before = open(atlas, "rb").read()
        with mock.patch.object(LightmapRecords, "wrote", return_value=False):
            result = self._baker().combine()
        self.assertEqual(open(atlas, "rb").read(), before, "the old atlas is untouched")
        self.assertNotIn(self.ATLAS, self._maps_read([a, b, c]))
        self.assertEqual(len(set(result.maps.values())), 1)

    def test_combine_reads_every_map_before_it_merges_a_material(self):
        """Review 2026-10-24 (S2): Combine merged the duplicate materials --
        deleting them -- and only then read the maps: one that would not
        decode (cut short, gone, no cv2) raised out of the cut with the
        duplicates already deleted and no atlas written. Every map is read
        first: one that cannot be leaves its objects their map, named, and
        no material is merged."""
        (a, b, c), _groups = self._split(False, legacy=True)
        bad = {t: p for t, _n, p in LightmapRecords._reads()}[c]
        with open(bad, "wb") as fh:
            fh.write(b"cut short")
        shader = LightmapBaker._surface_shader
        shaders = {o: shader(LightmapRecords._primary_material(o)) for o in (a, c)}
        self.assertEqual(len(set(shaders.values())), 2)

        with mock.patch.object(LightmapBaker.logger, "warning") as warn:
            result = self._baker().combine()

        for obj, mat in shaders.items():
            self.assertTrue(cmds.objExists(mat), f"{mat} was deleted")
            self.assertEqual(shader(LightmapRecords._primary_material(obj)), mat)
        self.assertEqual(sorted(result.maps), sorted([a, b]))
        self.assertEqual(self._maps_read([c]), {os.path.basename(bad)})
        said = _rendered_warnings(warn)
        self.assertTrue(any("keep their map" in m and "rcC" in m for m in said), said)

    def test_a_cancelled_combine_merges_no_material(self):
        """Review 2026-10-24: the merge ran before the first cut, so a cancel
        -- "nothing written" -- had already deleted the duplicates and moved
        their objects. It runs last; the plan groups the duplicates without
        it (_atlas_keys)."""
        (a, b, c), _groups = self._split(False, legacy=True)
        shader = LightmapBaker._surface_shader
        shaders = {o: shader(LightmapRecords._primary_material(o)) for o in (a, c)}
        self.assertEqual(len(set(shaders.values())), 2)

        result = self._baker().combine(on_progress=lambda *_args: False)

        self.assertIn("cancelled", result.refused)
        for obj, mat in shaders.items():
            self.assertTrue(cmds.objExists(mat), f"{mat} was deleted")
            self.assertEqual(shader(LightmapRecords._primary_material(obj)), mat)

    def test_a_group_an_earlier_merge_mended_is_skipped(self):
        """Review 2026-10-24: a group no longer split by the time Combine
        reached it (an earlier group's merge mended it) offered no size, and
        ``combine_sizes(...)[-1]`` raised IndexError."""
        self._split(False, legacy=True)
        real = LightmapBaker._combine_groups

        def mended(baker, maps=None):
            return real(baker, maps) if maps is None else []

        with mock.patch.object(LightmapBaker, "_combine_groups", mended):
            result = self._baker().combine()

        self.assertEqual(result.maps, {})
        self.assertIn("Nothing to combine", result.refused)

    def test_combine_takes_a_map_baked_at_intensity_zero(self):
        """Review 2026-10-24: each cell is rescaled to the atlas's one
        intensity by ``intensity / its own``, and a marker at 0 raised
        ZeroDivisionError. Such a map holds no light to rescale."""
        (a, b, c), _groups = self._split(False, legacy=True)
        node = LightmapRecords._marker_node(c)
        LightmapRecords._write_marker(
            node, dict(LightmapRecords._marker_info(c), intensity=0.0)
        )

        result = self._baker().combine()

        self.assertEqual(sorted(result.maps), sorted([a, b, c]))

    # -- saved with the scene ----------------------------------------------
    def _saved_room(self):
        """The legacy split saved as ``room.ma``, in a project that holds its
        maps; returns its objects, the scene file and the folder's bytes."""
        root = os.path.dirname(self.out)
        previous = cmds.workspace(query=True, rootDirectory=True)
        cmds.workspace(root, openWorkspace=True)
        self.addCleanup(cmds.workspace, previous, openWorkspace=True)
        # Set aside into the scratch folder, never the user's Recycle Bin.
        no_trash = mock.patch.object(ptk.FileUtils, "move_to_trash", return_value=None)
        no_trash.start()
        self.addCleanup(no_trash.stop)
        # Each its own light: a combined atlas then differs from the old one.
        self.fake.values = {"rcA": 0.25, "rcB": 0.5, "rcC": 0.75}
        objects, _groups = self._split(True, legacy=True)
        return objects, self._save_as("room"), self._disk()

    def test_an_unsaved_combine_leaves_the_saved_scene_its_maps(self):
        """Production: a Combine never saved wrote its atlas over
        LX2000_Lightmap.exr and recycled LX2000_Lightmap_1.exr, and the saved
        scene reopened with a map missing and three objects lit from the
        wrong cells. Until the save nothing reaches the folder, and the
        session's maps go with the session. Added: 2026-10-23"""
        (a, b, c), scene, disk = self._saved_room()
        result = self._baker().combine()
        self.assertEqual(self._maps_read([a, b, c]), {self.ATLAS}, "the session reads it")
        self.assertEqual(self._disk(), disk, "the folder as the saved scene left it")
        cmds.file(scene, open=True, force=True)
        self.assertEqual(self._maps_read([a, b, c]), set(disk))
        self.assertEqual(set(self._report().split), set(disk))
        self.assertEqual([p for p in result.files if os.path.exists(p)], [], "nothing left")

    def test_saving_a_combine_replaces_the_atlas_under_its_own_name(self):
        """No ``_1`` for an unsaved scene to tell apart: the save writes the
        atlas over the one the scene read and sets the split-off map aside.
        Added: 2026-10-23"""
        (a, b, c), scene, disk = self._saved_room()
        self._baker().combine()
        cmds.file(save=True, force=True)
        saved = self._disk()
        self.assertEqual(sorted(saved), [self.ATLAS], "the split-off map set aside")
        self.assertNotEqual(saved[self.ATLAS], disk[self.ATLAS])
        cmds.file(scene, open=True, force=True)
        reads = {t: p for t, _n, p in LightmapRecords._reads()}
        self.assertEqual(
            {os.path.normcase(os.path.dirname(reads[o])) for o in (a, b, c)},
            {os.path.normcase(os.path.abspath(self.out))},
        )
        self.assertEqual(self._report().split, {})

    def test_a_save_as_after_a_combine_leaves_the_first_file_its_maps(self):
        """A Save As writes a second scene file, and the first still reads
        every map as it is: the atlas the session rewrote goes to the new
        file under the next free name, the new file's own, and nothing is set
        aside. Added: 2026-10-23"""
        (a, b, c), first, disk = self._saved_room()
        self._baker().combine()
        second = self._save_as("room_v2")
        saved = self._disk()
        self.assertEqual({n: saved[n] for n in disk}, disk, "the first file's maps")
        (forked,) = self._maps_read([a, b, c])
        self.assertNotIn(forked, disk)
        self.assertTrue(LightmapRecords.wrote(forked), "the new file owns it")
        cmds.file(first, open=True, force=True)
        self.assertEqual(self._maps_read([a, b, c]), set(disk))
        cmds.file(second, open=True, force=True)
        self.assertEqual(self._maps_read([a, b, c]), {forked})

    def test_a_save_as_out_of_the_project_stamps_its_forks_as_its_own(self):
        """Review 2026-10-24: a Save As's forked map was stamped by hand,
        spelled from the re-base's project -- which a target in no project
        leaves as the OLD one, so the stamp named a file there. Stamped as
        the scene file's own writer stamp (``DataNodes.writer_stamp_of``)."""
        from mayatk.node_utils.data_nodes import DataNodes

        (a, b, c), _first, disk = self._saved_room()
        self._baker().combine()
        real = DataNodes.project_root_of
        nowhere = classmethod(
            lambda cls, path: None if "room_out" in str(path or "") else real(path)
        )
        with mock.patch.object(DataNodes, "project_root_of", nowhere):
            second = self._save_as("room_out")
            expected = DataNodes.writer_stamp_of(second)
        (forked,) = self._maps_read([a, b, c])
        self.assertNotIn(forked, disk)
        self.assertEqual(LightmapRecords._writers()[forked.lower()], expected)

    def test_an_undone_combine_is_not_saved(self):
        """The save moves only what the scene reads: a Combine undone before
        it leaves the folder as it was. Added: 2026-10-23"""
        was = cmds.undoInfo(query=True, state=True)
        cmds.undoInfo(state=True, infinity=True)
        self.addCleanup(cmds.undoInfo, state=was)
        (a, b, c), _scene, disk = self._saved_room()
        self._baker().combine()
        cmds.undo()
        self.assertEqual(self._maps_read([a, b, c]), set(disk))
        cmds.file(save=True, force=True)
        self.assertEqual(self._disk(), disk)

    def test_re_baking_either_maps_objects_mends_the_split(self):
        """The split row's tooltip says "right-click it: Re-Bake": a Re-Bake
        of either map's objects -- the main atlas's or the split-off one's --
        packs the whole group into one atlas. Added: 2026-10-22"""
        for main in (True, False):
            with self.subTest(main=main):
                cmds.file(new=True, force=True)
                self.out = self.store.dir_path(name=f"mend_{main}")
                (a, b, c), _groups = self._split(True, legacy=True)
                self._baker().rebake([a, b] if main else [c], output_dir=self.out)
                self.assertEqual(self._maps_read([a, b, c]), {self.ATLAS})
                self.assertEqual(self._report().targets, ())

    def test_merging_the_materials_after_a_bake_keeps_the_one_atlas(self):
        """Fixing the duplicates elsewhere (tentacle's Remove Duplicate
        Materials, the Scene Exporter task: ``MatUtils``' merge) after a bake
        moves objects between shading groups of ONE atlas group: nothing
        splits, nothing re-packs, the atlas stays one. Added: 2026-10-22"""
        for same_shader in (True, False):
            with self.subTest(same_shader=same_shader):
                cmds.file(new=True, force=True)
                self.out = self.store.dir_path(name=f"merged_{same_shader}")
                (a, b, c), groups = self._split(same_shader)
                MatUtils._merge_shading_groups({groups[c]: groups[a]})
                report = self._report()
                self.assertEqual(report.repack, {}, report.lines())
                # The moved object reads its material changed, and re-bakes
                # alone into the cell it owns.
                self.assertEqual(report.targets, (c,), report.lines())
                self.assertEqual(report.in_place, (c,))
                self.assertEqual(report.split, {})
                self._rebake()
                self.assertEqual(self._maps_read([a, b, c]), {self.ATLAS})
                self.assertEqual(self._report().targets, ())

    @staticmethod
    def _untracked(*objects):
        """Patch the bake record to hold no entry for *objects*: baked before
        change tracking, as the production room's split-off object was."""
        real = LightmapRecords.bake_state.__func__

        def state(cls, *args, **kwargs):
            out = real(cls, *args, **kwargs)
            out["objects"] = {
                k: e for k, e in out["objects"].items() if k not in objects
            }
            return out

        return mock.patch.object(LightmapRecords, "bake_state", classmethod(state))

    def test_an_untracked_split_off_map_beside_its_groups_atlas_is_a_split(self):
        """The production room: BODY (BODYSG, one material with CHEESE_CAKESG)
        read LX2000_Lightmap_1 alone, baked before change tracking -- no
        record says it was an atlas of one, so the split went unseen. A
        one-reader map in a group that owns an atlas is that group's split
        either way. Changed: 2026-10-23 (a split re-baked)."""
        (a, b, c), _groups = self._split(same_shader=True, legacy=True)
        with self._untracked(c):
            report = self._report()
        self.assertEqual(
            set(report.split), {self.ATLAS, "Crate_Lightmap_1.exr"}, report.lines()
        )
        self.assertEqual(report.targets, ())

    def test_tracking_one_map_leaves_the_split_and_the_rest_untracked(self):
        """Production: Start Tracking two rows turned every row "up to date"
        -- the commit took every other untracked object in as tracked, and
        the split-off map, recorded now, no longer read as split. Added:
        2026-10-23"""
        (a, b, c), _groups = self._split(True, legacy=True)
        self._forget(a, c)
        self.assertEqual(self._report().untracked, (a, c))
        self.assertEqual(self._baker().track([c]), [c])
        report = self._report()
        self.assertEqual(report.untracked, (a,), "the other stays untracked")
        self.assertEqual(set(report.split), {self.ATLAS, "Crate_Lightmap_1.exr"})

    @staticmethod
    def _forget(*objects):
        """Drop *objects* from the STORED bake record: baked before change
        tracking, for real (``_untracked`` only patches the read)."""
        record = LightmapRecords.bake_state()
        for obj in objects:
            record["objects"].pop(obj)
        ptk.SceneRecords.LIGHTMAP_BAKE_STATE.save(
            LightmapRecords._data_nodes(), ptk.BakeBaseline.encode(record)
        )

    def test_start_tracking_records_an_untracked_map_without_a_render(self):
        """An untracked object's changes go unseen; Start Tracking records it
        as baked, as it stands and under the bake's lighting, rendering
        nothing -- so its next change reads as changed. Its cell stays
        unrecorded (a re-bake re-packs its atlas whole). Added: 2026-10-22"""
        a, b, c = self._crates()
        self._bake([a, b, c])
        self._forget(c)
        self.assertEqual(self._report().untracked, (c,))
        self.fake.baked.clear()
        self.assertEqual(self._baker().track([a, c]), [c], "only the untracked")
        self.assertEqual(self.fake.baked, [], "nothing rendered")
        entry = LightmapRecords.bake_state()["objects"][c]
        self.assertIsNotNone(entry["baked_at"])
        self.assertIsNotNone(entry["lighting"])
        self.assertIsNone(entry["cell"])
        report = self._report()
        self.assertEqual((report.untracked, report.targets), ((), ()), report.lines())
        cmds.move(0, 5, 0, c, relative=True)
        self.assertIn(c, self._report().targets)
        self.assertEqual(self._baker().track([c]), [], "a tracked object stays as is")

    def test_untracked_per_object_maps_are_no_split(self):
        """Objects of one material each baked to their own map, before change
        tracking, own no atlas: their packing was per object, not a split, so
        nothing re-packs them into one. Added: 2026-10-22"""
        a, b = self._crates(("rcA", "rcB"))
        result = self._baker().bake([a, b], packing="per_object", output_dir=self.out)
        self.assertTrue(result.maps, result.refused)
        self.assertEqual(len(self._maps_read([a, b])), 2)
        with self._untracked(a, b):
            self.assertEqual(self._report().targets, ())

    # -- what is stale ------------------------------------------------------
    def test_nothing_is_stale_after_a_bake_or_a_save_and_reopen(self):
        """A save keeps ~7 digits of each float: a fingerprint that flipped on
        it would call the whole room changed after every reopen."""
        objects = self._crates()
        self._bake(objects)
        self.assertEqual(self._report().targets, ())
        for ext, kind in ((".ma", "mayaAscii"), (".mb", "mayaBinary")):
            with self.subTest(kind=kind):
                path = self.store.path(ext, name=f"rebake_room{ext.replace('.', '_')}")
                cmds.file(rename=path)
                cmds.file(save=True, type=kind, force=True)
                cmds.file(new=True, force=True)
                cmds.file(path, open=True, force=True)
                report = self._report()
                self.assertEqual(report.targets, (), report.lines())
                self.assertEqual(report.untracked, ())
                self.assertEqual(
                    report.summary(), "Nothing changed since the last bake."
                )

    def test_a_moved_object_is_stale_and_its_far_neighbours_are_not(self):
        a, b, c = self._crates()
        self._bake([a, b, c])
        cmds.move(0, 0.5, 0, a, relative=True)
        report = self._report()
        self.assertEqual(report.targets, (a,))
        self.assertEqual(report.stale[a], (ptk.BakeBaseline.Reason.MOVED,))

    def test_a_deformed_object_is_stale(self):
        a, b, c = self._crates()
        self._bake([a, b, c])
        cmds.move(0, 0.4, 0, f"{a}.vtx[0:3]", relative=True)
        report = self._report()
        self.assertEqual(report.targets, (a,))
        self.assertIn(ptk.BakeBaseline.Reason.DEFORMED, report.stale[a])

    def test_a_missing_map_stales_its_readers(self):
        objects = self._crates()
        self._bake(objects)
        os.remove(LightmapRecords._stage(os.path.join(self.out, self.ATLAS)))
        report = self._report()
        self.assertEqual(report.targets, tuple(sorted(objects)))
        for obj in objects:
            self.assertIn(ptk.BakeBaseline.Reason.MISSING_MAP, report.stale[obj])

    def test_a_hidden_object_is_stale_but_cannot_bake(self):
        a, b, c = self._crates()
        self._bake([a, b, c])
        cmds.setAttr(f"{a}.visibility", 0)
        report = self._report()
        self.assertEqual(report.unbakeable, (a,))
        self.assertEqual(report.targets, ())
        self.assertIn(ptk.BakeBaseline.Reason.VISIBILITY, report.stale[a])

    def test_an_object_added_to_the_exclude_set_stales_its_neighbour(self):
        """An excluded mesh is in every bake's render: one arriving next to B
        shadows it, though it gets no map of its own."""
        a, b, c = self._crates()
        self._bake([a, b, c])
        stool = cmds.polyCube(name="rcStool")[0]
        cmds.move(11.2, 0, 0, stool)
        stool = cmds.ls(stool, long=True)[0]
        LightmapExcludeSet.define([stool])
        report = self._report()
        self.assertEqual(report.targets, (b,))
        self.assertEqual(report.neighbours[b], (stool,))

    def test_a_changed_light_stales_the_whole_room(self):
        light = cmds.pointLight(intensity=1.0)
        transform = cmds.ls(
            cmds.listRelatives(light, parent=True, fullPath=True)[0], long=True
        )[0]
        objects = self._crates()
        self._bake(objects)
        self.assertEqual(self._report().targets, ())
        cmds.setAttr(f"{light}.intensity", 5.0)
        report = self._report()
        self.assertEqual(report.lighting, (transform,))
        self.assertEqual(report.targets, tuple(sorted(objects)))
        self.assertIn("the whole room", report.summary())
        self.assertEqual(
            self._baker().stale_report(), report, "the dry run is repeatable"
        )
        # Opted out, the lighting is not compared.
        self.assertEqual(self._rebake(lighting=False).maps, {})

    # -- written back in place ------------------------------------------------
    def test_rebake_writes_the_changed_cell_in_place_and_leaves_the_others_byte_identical(
        self,
    ):
        np = _cv2()[1]
        a, b, c = self._crates()
        self.fake.values = {"rcA": 1.0, "rcB": 2.0, "rcC": 3.0}
        self._bake([a, b, c])
        before = self._atlas()
        self.fake.values["rcA"] = 5.0
        cmds.move(0, 0.5, 0, a, relative=True)

        result = self._rebake()

        self.assertEqual(result.in_place, [a])
        self.assertEqual(result.repacked, {})
        self.assertEqual(list(result.maps), [a])
        self.assertEqual(self._files(), [self.ATLAS], "no _1 atlas")
        after = self._atlas()
        for obj in (b, c):
            self.assertTrue(
                np.array_equal(self._cell(after, obj), self._cell(before, obj)),
                f"{obj}'s cell changed",
            )
        self.assertTrue(np.allclose(self._cell(after, a), 5.0, atol=1e-3))
        self.assertEqual(self._report().targets, ())
        # One map per material in what ships.
        maps = {e["map"] for e in LightmapRecords._record().payload["objects"]}
        self.assertEqual(maps, {self.ATLAS})
        self.assertEqual(result.report.in_place, (a,))

    def test_a_measurements_pack_never_writes_the_scenes_atlas(self):
        """``record=False`` writes no file the scene reads -- its two-call
        pack too (``bake_separated``, then ``pack_atlas``), which wrote the
        measurement's maps into the cells the scene's atlas holds."""
        np = _cv2()[1]
        a, b, c = self._crates()
        self.fake.values = {"rcA": 1.0, "rcB": 2.0, "rcC": 3.0}
        self._bake([a, b, c])
        before = self._atlas()
        self.fake.values["rcA"] = 5.0
        measure = self._baker(record=False)
        tiles = measure.bake_separated(
            [a], output_dir=self.store.dir_path(name="tiles")
        )
        measure.pack_atlas(tiles, output_dir=self.store.dir_path(name="measured"))
        self.assertTrue(
            np.array_equal(self._atlas(), before), "the scene's atlas was written"
        )

    def test_intensity_is_applied_once_to_a_cell_written_in_place(self):
        np = _cv2()[1]
        a, b, c = self._crates()
        self.fake.values = {"rcA": 1.0, "rcB": 2.0, "rcC": 3.0}
        self._bake([a, b, c], intensity=np.pi)
        before = self._atlas()
        self.assertTrue(np.allclose(self._cell(before, b), 2.0 * np.pi, rtol=1e-3))
        cmds.move(0, 0.5, 0, a, relative=True)

        result = self._rebake(intensity=np.pi)

        self.assertEqual(result.in_place, [a])
        after = self._atlas()
        self.assertTrue(np.allclose(self._cell(after, a), np.pi, rtol=1e-3))
        self.assertTrue(np.array_equal(self._cell(after, b), self._cell(before, b)))

    def test_a_cell_that_no_longer_fits_repacks_its_whole_group(self):
        a, b, c = self._crates()
        self._bake([a, b, c])
        cmds.scale(2, 2, 2, a)

        with mock.patch.object(LightmapBaker.logger, "info") as info:
            result = self._rebake()

        self.assertEqual(result.in_place, [])
        self.assertEqual(sorted(result.maps), sorted([a, b, c]))
        self.assertEqual(list(result.repacked), [self.ATLAS])
        why = result.repacked[self.ATLAS]
        self.assertIn("no longer fits", why)
        self.assertIn("2 other members re-bake with it", why)
        self.assertEqual(self._files(), [self.ATLAS], "the group keeps its one atlas")
        self.assertTrue(any(why in m for m in _rendered_warnings(info)))
        self.assertEqual(self._report().targets, ())

    def _save_as(self, name, write=True):
        path = self.store.path(".ma", name=name)
        cmds.file(rename=path)
        if write:
            cmds.file(save=True, type="mayaAscii", force=True)
        return path

    def test_a_rebake_keeps_each_atlas_in_its_folder_unless_told_to_relocate(self):
        """Production PLAYGROUND_wStands (2026-10-21): its atlases sat in
        sourceimages, the re-bake wrote to sourceimages/lightmaps, and an atlas
        in another folder re-packs whole -- the dry run, blind to the folder,
        read "29 written in place"; the bake re-packed 97 maps in 50 minutes.
        An update now writes each map back where it is; *relocate* moves them,
        and its dry run says what that re-packs."""
        a, b, c = self._crates()
        self._bake([a, b, c])
        cmds.move(0, 0.5, 0, a, relative=True)
        elsewhere = self.store.dir_path(name="elsewhere")

        self.assertEqual(self._report(output_dir=elsewhere).in_place, (a,))
        moved = self._report(output_dir=elsewhere, relocate=True)
        self.assertEqual(moved.in_place, ())
        self.assertIn("another folder", moved.repack[self.ATLAS])

        result = self._baker().rebake_changed(output_dir=elsewhere)
        self.assertEqual(result.in_place, [a])
        self.assertEqual(os.listdir(elsewhere), [], "nothing was written there")

        cmds.move(0, 0.5, 0, b, relative=True)
        result = self._baker().rebake_changed(output_dir=elsewhere, relocate=True)
        self.assertEqual(list(result.repacked), [self.ATLAS])
        moving = LightmapRecords._pending().folder(elsewhere)  # there at the save
        self.assertEqual([f for f in os.listdir(moving) if f.endswith(".exr")], [self.ATLAS])
        self.assertEqual(self._report().targets, ())

    def test_the_dry_run_counts_what_a_repack_drags_in(self):
        """A re-pack re-bakes its atlas's every member: the footer counted the
        changed alone ("29 to re-bake") for a bake of 97."""
        a, b, c = self._crates()
        self._bake([a, b, c])
        cmds.scale(2, 2, 2, a)

        report = self._report(output_dir=self.out)

        self.assertEqual(report.targets, (a,))
        self.assertEqual(report.joining, tuple(sorted([b, c])))
        self.assertIn("(+2 more objects)", report.summary())

    def test_rebake_redoes_the_given_objects_as_baked_changed_or_not(self):
        """What the baseline cannot see -- an albedo or emissive edit, a noisy
        render -- redone for exactly the objects asked, each written back into
        its own cell; the rest keep every texel."""
        np = _cv2()[1]
        a, b, c = self._crates()
        self.fake.values = {"rcA": 1.0, "rcB": 2.0, "rcC": 3.0}
        self._bake([a, b, c])
        before = self._atlas()
        self.fake.values["rcB"] = 7.0
        self.assertEqual(self._report().targets, (), "unseen by the baseline")

        result = self._baker().rebake([b], output_dir=self.out)

        self.assertEqual(result.in_place, [b])
        self.assertEqual(list(result.maps), [b])
        after = self._atlas()
        self.assertTrue(np.allclose(self._cell(after, b), 7.0, atol=1e-3))
        for obj in (a, c):
            self.assertTrue(np.array_equal(self._cell(after, obj), self._cell(before, obj)))
        stool = cmds.ls(cmds.polyCube(name="rcStool")[0], long=True)[0]
        refused = self._baker().rebake([stool], output_dir=self.out)
        self.assertEqual(refused.maps, {})
        self.assertIn("None of these objects is baked yet", refused.refused)

    def test_a_renamed_atlas_keeps_its_baseline(self):
        """``rename_lightmap`` re-stamped the markers and records but not the
        bake baseline, whose entries still named the old file: the next
        partial re-bake refused in place ("recorded against another map") and
        re-packed the whole group."""
        a, b, c = self._crates()
        self._bake([a, b, c])
        staged = LightmapRecords._stage(os.path.join(self.out, self.ATLAS))  # unsaved
        os.rename(staged, os.path.join(os.path.dirname(staged), "Room_LM.exr"))
        LightmapRecords.rename_lightmap(self.ATLAS, "Room_LM.exr")
        cmds.move(0, 0.5, 0, a, relative=True)

        result = self._rebake()

        self.assertEqual(result.repacked, {})
        self.assertEqual(result.in_place, [a])

    def test_a_copys_first_rebake_copies_its_sources_atlas_then_writes_in_place(self):
        """BACKLOG 2026-10-21 (S3): a Save As copy re-packed every atlas it
        shares with its source whole -- the source's maps kept, at 64 extra
        re-bakes (PLAYGROUND_wStands). It now writes the source's atlas, its
        changed cells new, under its own next free name: the source's file
        stays byte for byte, every reader follows the copy, and the next
        re-bake goes in place into it."""
        np = _cv2()[1]
        a, b, c = self._crates()
        self.fake.values = {"rcA": 1.0, "rcB": 2.0, "rcC": 3.0}
        self._save_as("rcSource")
        self._bake([a, b, c])
        cmds.file(save=True, type="mayaAscii", force=True)  # its maps home
        source = os.path.join(self.out, self.ATLAS)
        with open(source, "rb") as fh:
            before = fh.read()
        self._save_as("rcCopy", write=False)
        self.fake.values["rcA"] = 5.0
        cmds.move(0, 0.5, 0, a, relative=True)

        report = self._report(output_dir=self.out)
        self.assertEqual(report.in_place, (a,))
        self.assertEqual(list(report.forked), [self.ATLAS])
        result = self._rebake()

        copy = "Crate_Lightmap_1.exr"
        self.assertEqual(result.in_place, [a])
        self.assertEqual(result.repacked, {})
        with open(source, "rb") as fh:
            self.assertEqual(fh.read(), before, "the source's atlas was written")
        self.assertEqual(self._files(), [self.ATLAS, copy])
        for obj in (a, b, c):
            self.assertEqual(LightmapRecords._marker_info(obj)["map"], copy)
        self.assertIn(
            LightmapRecords.FOREIGN, LightmapRecords.claims()[self.ATLAS.lower()]
        )
        after, src = self._atlas(copy), self._atlas()
        self.assertTrue(np.array_equal(self._cell(after, b), self._cell(src, b)))
        self.assertTrue(np.allclose(self._cell(after, a), 5.0, atol=1e-3))
        self.assertEqual(self._report().targets, ())
        # The next change goes in place into the copy: no third file.
        cmds.move(0, 0.5, 0, b, relative=True)
        self.assertEqual(self._rebake().in_place, [b])
        self.assertEqual(self._files(), [self.ATLAS, copy])

    def test_a_fork_and_a_new_atlas_of_its_texture_set_never_share_a_file(self):
        """Review 2026-10-24 (S2): a Save As copy's fork chose its name against
        claims read before it existed, and a group packing anew in the same
        bake under the same texture set chose it too: the new atlas replaced
        the fork, and the fork's readers sampled the new group's layout,
        nothing said. A fork's name is the bake's from the moment it is
        chosen."""
        np = _cv2()[1]
        a, b, c = self._crates()
        self.fake.values = {"rcA": 1.0, "rcB": 2.0, "rcC": 3.0, "rcD": 7.0, "rcE": 7.0}
        self._save_as("rcSource")
        self._bake([a, b, c])
        cmds.file(save=True, type="mayaAscii", force=True)  # its maps home
        self._save_as("rcCopy", write=False)
        # The same texture set on a material of its own (not a duplicate: its
        # own diffuse), so a group of its own that packs anew under that name.
        other = self._crate_sg(name="rcOther")
        cmds.setAttr(f"{MatUtils._sg_shaders(other)[0]}.diffuse", 0.3)
        d, e = self._crates(("rcD", "rcE"), sg=other)
        for obj in (d, e):
            cmds.move(0, 0, 40, obj, relative=True)
        self.assertEqual(len(set(LightmapBaker._atlas_keys([a, d]).values())), 2)
        self.fake.values["rcA"] = 5.0
        cmds.move(0, 0.5, 0, a, relative=True)

        result = self._bake([a, d, e])

        fork, new = "Crate_Lightmap_1.exr", "Crate_Lightmap_2.exr"
        self.assertEqual(result.in_place, [a])
        self.assertEqual(self._maps_read([a, b, c]), {fork})
        self.assertEqual(self._maps_read([d, e]), {new})
        self.assertEqual(self._files(), [self.ATLAS, fork, new])
        forked = self._atlas(fork)
        self.assertTrue(np.allclose(self._cell(forked, b), 2.0, atol=1e-3))
        self.assertTrue(np.allclose(self._cell(forked, a), 5.0, atol=1e-3))
        self.assertTrue(np.allclose(self._cell(self._atlas(new), d), 7.0, atol=1e-3))

    def test_another_scenes_atlas_of_the_same_name_is_never_written_over(self):
        """2026-10-21 audit: two scenes in one project whose materials share a
        texture set name the same ``<set>_Lightmap.exr``. The atlas name
        skipped only what THIS scene's markers claim, and the disk was never
        asked: the second scene's bake replaced the first's atlas, which then
        read the second's lighting through its own cells."""
        a, b, c = self._crates()
        self._save_as("rcRoomDay")
        self._bake([a, b, c])
        cmds.file(save=True, type="mayaAscii", force=True)  # its maps home
        theirs = os.path.join(self.out, self.ATLAS)
        with open(theirs, "rb") as fh:
            before = fh.read()

        cmds.file(new=True, force=True)
        self._save_as("rcRoomNight")
        self.fake.values.update(rcN1=9.0, rcN2=9.0)
        objects = self._crates(names=("rcN1", "rcN2"))
        result = self._bake(objects)

        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), before, "the other scene's atlas was replaced")
        self.assertEqual(
            {os.path.basename(p) for p in result.maps.values()},
            {"Crate_Lightmap_1.exr"},
        )
        # Its own atlas stays its own: the next bake goes back over it.
        cmds.move(0, 0.5, 0, objects[0], relative=True)
        again = self._bake(objects)
        self.assertEqual(
            {os.path.basename(p) for p in again.maps.values()},
            {"Crate_Lightmap_1.exr"},
        )
        self.assertEqual(self._files(), [self.ATLAS, "Crate_Lightmap_1.exr"])

    def test_a_map_waiting_for_the_save_is_set_aside_once_superseded(self):
        """Review 2026-10-24 (S2): what a bake supersedes was read off the
        files its objects read -- for a map re-baked in place and not saved
        yet, its session copy -- and the project filter dropped that path:
        the home file was never set aside once the next bake moved its
        readers to another file."""
        root = os.path.dirname(self.out)
        previous = cmds.workspace(query=True, rootDirectory=True)
        cmds.workspace(root, openWorkspace=True)
        self.addCleanup(cmds.workspace, previous, openWorkspace=True)
        no_trash = mock.patch.object(ptk.FileUtils, "move_to_trash", return_value=None)
        no_trash.start()
        self.addCleanup(no_trash.stop)
        a, b, c = self._crates()
        self._save_as("rcSuperseded")
        self._bake([a, b, c])
        cmds.file(save=True, type="mayaAscii", force=True)  # its maps home
        cmds.move(0, 0.5, 0, a, relative=True)
        self.assertEqual(self._rebake().in_place, [a])  # waits in the session

        # To maps of their own: an atlas bake would write the group back in place.
        result = self._baker().bake([a, b, c], packing="per_object", output_dir=self.out)

        self.assertTrue(result.maps, result.refused)
        self.assertNotIn(self.ATLAS, self._maps_read([a, b, c]))
        self.assertEqual([os.path.basename(p) for p in result.retired], [self.ATLAS])

    def test_nor_does_a_one_object_group_of_that_texture_set(self):
        """Review 2026-10-24 (S2): a group of one takes the texture set's name
        too, and blendertk's twin moved its map in without asking the disk --
        the other scene's atlas replaced at the save. Pinned on both twins."""
        a, b, c = self._crates()
        self._save_as("rcRoomDay")
        self._bake([a, b, c])
        cmds.file(save=True, type="mayaAscii", force=True)  # its maps home
        theirs = os.path.join(self.out, self.ATLAS)
        with open(theirs, "rb") as fh:
            before = fh.read()

        cmds.file(new=True, force=True)
        self._save_as("rcRoomSolo")
        self.fake.values.update(rcS1=9.0)
        result = self._bake(self._crates(names=("rcS1",)))
        cmds.file(save=True, type="mayaAscii", force=True)  # its map home

        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), before, "the other scene's atlas was replaced")
        self.assertEqual(
            {os.path.basename(p) for p in result.maps.values()},
            {"Crate_Lightmap_1.exr"},
        )
        self.assertEqual(self._files(), [self.ATLAS, "Crate_Lightmap_1.exr"])

    def test_a_hosts_bake_of_referenced_objects_never_writes_the_modules_maps(self):
        """2026-10-21 audit: a module baked in its own scene, referenced into
        a host, and baked there: its objects were the only readers, so the
        module's atlas was theirs to replace -- every scene referencing the
        module then read this host's lighting. A map a referenced object
        reads is the module's unless this scene wrote it."""
        a, b, c = self._crates()
        module = self._save_as("rcModule")
        self._bake([a, b, c])
        cmds.file(save=True, type="mayaAscii", force=True)
        theirs = os.path.join(self.out, self.ATLAS)
        with open(theirs, "rb") as fh:
            before = fh.read()

        cmds.file(new=True, force=True)
        self._save_as("rcHost")
        cmds.file(module, reference=True, namespace="mod")
        objects = cmds.ls("mod:rcA", "mod:rcB", "mod:rcC", long=True)
        self.fake.values.update({"mod:rcA": 4.0, "mod:rcB": 4.0, "mod:rcC": 4.0})
        result = self._bake(objects)

        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), before, "the module's atlas was replaced")
        self.assertNotIn(
            os.path.normcase(theirs),
            {os.path.normcase(p) for p in result.maps.values()},
        )

    def test_a_copy_in_another_project_rebakes_into_its_own(self):
        """2026-10-21 audit: saved into another project, the copy's maps still
        resolve in the source project's folder -- and a re-bake keeping folders
        wrote the copy's maps there, where its project alone does not carry
        them. A folder outside the project is never kept."""
        a, b, c = self._crates()
        self._save_as("rcFirstProject")
        self._bake([a, b, c])
        cmds.file(save=True, type="mayaAscii", force=True)  # its maps home
        theirs = os.path.join(self.out, self.ATLAS)
        with open(theirs, "rb") as fh:
            before = fh.read()
        other = self.store.dir_path(name="secondProject")
        os.makedirs(os.path.join(other, "scenes"))
        with open(os.path.join(other, "workspace.mel"), "w") as fh:
            fh.write("")
        cmds.file(rename=os.path.join(other, "scenes", "rcSecond.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)
        own = os.path.join(other, "sourceimages", "lightmaps")
        self.fake.values["rcA"] = 5.0
        cmds.move(0, 0.5, 0, a, relative=True)

        result = self._baker().rebake_changed(output_dir=own)

        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), before, "the first project's atlas was written")
        self.assertTrue(result.maps)
        for path in result.maps.values():  # its home, at the save
            home = LightmapRecords._home(path)
            self.assertTrue(ptk.FileUtils.is_under(os.path.abspath(home), other), path)

    def test_a_reverted_scenes_atlas_is_still_its_own_to_bake_over(self):
        """The rule above must not orphan a scene's own file: reverted, no
        marker names its atlas, but the scene wrote it -- the next bake takes
        the name back instead of leaving it beside a ``_1``."""
        objects = self._crates()
        self._save_as("rcReverted")
        self._bake(objects)
        LightmapRecords.revert()
        result = self._bake(objects)
        self.assertEqual(
            {os.path.basename(p) for p in result.maps.values()}, {self.ATLAS}
        )
        self.assertEqual(self._files(), [self.ATLAS])

    def test_a_copys_two_call_pack_writes_its_own_copy_of_the_sources_atlas(self):
        """2026-10-21: ``pack_atlas``'s two-call form (``bake_separated`` then
        a pack) wrote a recorded cell back in place with no fork check -- in a
        Save As copy, into the atlas its source still reads. Every in-place
        write now goes one way (``_write_in_place``)."""
        a, b, c = self._crates()
        self._save_as("rcPackSource")
        self._bake([a, b, c])
        cmds.file(save=True, type="mayaAscii", force=True)  # its maps home
        source = os.path.join(self.out, self.ATLAS)
        with open(source, "rb") as fh:
            before = fh.read()
        self._save_as("rcPackCopy", write=False)
        self.fake.values["rcA"] = 5.0
        work = self.store.dir_path(name="work")
        tiles = self.fake.bake([a], output_dir=work)

        out = self._baker().pack_atlas(tiles, output_dir=self.out)

        copy = "Crate_Lightmap_1.exr"
        self.assertEqual(os.path.basename(out[a][0]), copy)
        with open(source, "rb") as fh:
            self.assertEqual(fh.read(), before, "the source's atlas was written")
        self.assertEqual(LightmapRecords._marker_info(b)["map"], copy)

    def test_a_visible_mesh_no_bake_renders_is_named_and_stales_nothing(self):
        """BACKLOG 2026-10-21 (S2): new FLYING_STAND meshes staled nothing,
        and nothing said why. A bake switches every visible mesh that is
        neither baked nor excluded off (``_out_of_render``), so staling
        nothing is right -- the report names them instead."""
        a, b, c = self._crates()
        self._bake([a, b, c])
        stand = cmds.polyCube(name="rcStand")[0]
        cmds.move(1.2, 0, 0, stand)
        stand = cmds.ls(stand, long=True)[0]

        report = self._report()

        self.assertEqual(report.targets, ())
        self.assertEqual(report.out_of_bake, (stand,))
        self.assertIn("1 visible mesh out of the bake", report.summary())
        LightmapExcludeSet.define([stand])
        self.assertEqual(self._report().out_of_bake, ())

    def test_the_bake_breakdown_names_each_map_and_its_state(self):
        a, b, c = self._crates()
        self._bake([a, b, c])
        cmds.move(0, 0.5, 0, a, relative=True)

        doc = self._baker().bake_breakdown(output_dir=self.out)

        text = doc.to_text()
        for expected in (self.ATLAS, "64 x 64", "1 changed", "this scene"):
            self.assertIn(expected, text)
        self.assertIn("action://select?node=", doc.to_html())

    def test_a_legacy_atlas_repacks_whole_once_then_goes_in_place(self):
        """An atlas baked before change tracking holds no cells: re-baking one
        member expands to its whole group once -- which also heals a legacy
        split -- and records them, so the next is written in place."""
        a, b, c = self._crates()
        self._bake([a, b, c])
        ptk.SceneRecords.LIGHTMAP_BAKE_STATE.clear(
            lmb_module.LightmapRecords._data_nodes()
        )

        first = self._bake([a])

        self.assertEqual(sorted(first.maps), sorted([a, b, c]))
        self.assertIn("no recorded cell", first.repacked[self.ATLAS])
        self.assertEqual(self._files(), [self.ATLAS])

        second = self._bake([a])

        self.assertEqual(second.in_place, [a])
        self.assertEqual(list(second.maps), [a])
        maps = {e["map"] for e in LightmapRecords._record().payload["objects"]}
        self.assertEqual(maps, {self.ATLAS})

    def test_a_member_that_cannot_bake_keeps_its_atlas_and_the_group_takes_the_next_name(
        self,
    ):
        a, b, c = self._crates()
        self._bake([a, b, c])
        cmds.scale(2, 2, 2, a)
        LightmapExcludeSet.define([c])

        with mock.patch.object(LightmapBaker.logger, "warning") as warn:
            result = self._rebake()

        self.assertEqual(sorted(result.maps), sorted([a, b]))
        self.assertEqual(
            {os.path.basename(p) for p in result.maps.values()},
            {"Crate_Lightmap_1.exr"},
        )
        self.assertEqual(LightmapRecords._marker_info(c)["map"], self.ATLAS)
        self.assertTrue(
            any("rcC" in m and "cannot bake" in m for m in _rendered_warnings(warn)),
            _rendered_warnings(warn),
        )

    def test_a_cell_written_in_place_meets_its_held_neighbour_one_sided(self):
        """Two coplanar panels share an edge: the one re-baked is stitched to
        the one left as it was -- the free side moves the whole way, and the
        held cell keeps every texel."""
        np = _cv2()[1]
        sg = self._crate_sg("rcPanel")
        panels = [TestPackAtlas._panel(f"rcPanel{i}", sg, i * 3.94) for i in range(2)]
        self.fake.values = {"rcPanel0": 1.0, "rcPanel1": 2.0}
        self._bake(panels)
        before = self._atlas()
        self.fake.values["rcPanel1"] = 3.0

        result = self._bake([panels[1]])

        self.assertEqual(result.in_place, [panels[1]])
        after = self._atlas()
        self.assertTrue(
            np.array_equal(self._cell(after, panels[0]), self._cell(before, panels[0]))
        )
        held = self._cell(after, panels[0])
        free = self._cell(after, panels[1])
        # The free cell's edge column meets the held cell's level ...
        rect0 = LightmapRecords._marker_info(panels[0])["scaleOffset"]
        rect1 = LightmapRecords._marker_info(panels[1])["scaleOffset"]
        pairs = LightmapBaker(resolution=64)._seam_pairs(
            [(panels[0], rect0), (panels[1], rect1)], 64
        )
        self.assertTrue(len(pairs))

        def read(image, x, y):
            full = image[..., 0].astype(np.float64)
            x0, y0 = int(np.floor(x)), int(np.floor(y))
            fx, fy = x - x0, y - y0
            return (
                full[y0, x0] * (1 - fx) * (1 - fy)
                + full[y0, x0 + 1] * fx * (1 - fy)
                + full[y0 + 1, x0] * (1 - fx) * fy
                + full[y0 + 1, x0 + 1] * fx * fy
            )

        for xa, ya, xb, yb in pairs:
            # ... the whole way: the held side reads what it read before
            # (stitched halfway at the first bake), the free side meets it.
            self.assertAlmostEqual(read(after, xa, ya), read(before, xa, ya), places=6)
            self.assertAlmostEqual(read(after, xb, yb), read(after, xa, ya), delta=0.02)
        # ... and its interior keeps its own.
        self.assertAlmostEqual(float(np.median(free)), 3.0, delta=1e-3)
        self.assertAlmostEqual(float(np.median(held)), 1.0, delta=1e-3)

    def test_a_scene_bake_prunes_what_left(self):
        """A participant that leaves stales its neighbours, and is remembered
        until every map within its reach was baked after it left."""
        a, b, c = self._crates(spacing=1.5)
        far = self._crates(names=("rcFar",), sg=self._crate_sg("rcFarMat", "Far"))[0]
        cmds.move(40, 0, 0, far)
        self._bake([a, b, c, far])
        cmds.delete(c)

        report = self._report()
        self.assertEqual(report.targets, (b,))
        self.assertEqual(report.neighbours[b], (c,))

        self._bake([far])
        state = LightmapRecords.bake_state()["objects"]
        self.assertIsNotNone(state[c]["removed_at"], "b still holds its shadow")
        self.assertEqual(self._report().targets, (b,))

        self._bake([a, b, far])
        self.assertNotIn(c, LightmapRecords.bake_state()["objects"])
        self.assertEqual(self._report().targets, ())

    def test_nothing_changed_bakes_nothing(self):
        objects = self._crates()
        self._bake(objects)
        result = self._rebake()
        self.assertEqual(result.maps, {})
        self.assertEqual(result.refused, "Nothing changed since the last bake.")
        self.assertEqual(self.fake.baked, sorted(objects), "no render for nothing")

    def test_a_whole_scene_revert_clears_the_record(self):
        a, b, c = self._crates()
        self._bake([a, b, c])
        LightmapRecords.revert([a])
        self.assertIn(a, LightmapRecords.bake_state()["objects"])
        LightmapRecords.revert()
        self.assertEqual(LightmapRecords.bake_state()["objects"], {})

    def test_a_transferred_lightmap_inherits_its_sources_bake(self):
        """A target bound to its source's map and rect was baked when its
        source was, in the cell its source owns."""
        a, b = self._crates(names=("rcA", "rcB"))
        self._bake([a, b])
        twin = cmds.ls(cmds.duplicate(a, name="rcTwin")[0], long=True)[0]
        LightmapRecords.transfer_lightmaps([twin], a)
        state = LightmapRecords.bake_state()["objects"]
        for key in ("baked_at", "cell", "lighting", "packing"):
            self.assertEqual(state[twin][key], state[a][key], key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
