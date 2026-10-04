"""Tests for TextureBaker's Arnold backend (Phase 1 latent-bug fixes).

Two regressions the Phase 0b spike surfaced in Maya 2025:
  1. arnold_available() probed cmds.listCommands() -- which does NOT exist in
     Maya 2025 -- so it raised AttributeError once mtoa was loaded.
  2. arnoldRenderToTexture writes <shapeName>.<ext>, but bake() looked for the
     <transform>.<ext> name, so the file was "missing" and the object was
     dropped from the result dict (the Arnold path never actually worked).
"""

import contextlib
import os
import shutil
import tempfile
import unittest
from unittest import mock

import maya.cmds as cmds
from base_test import MayaTkTestCase
from mayatk.mat_utils.texture_baker import TextureBaker


def _arnold_loadable():
    try:
        if not cmds.pluginInfo("mtoa", q=True, loaded=True):
            cmds.loadPlugin("mtoa")
        return hasattr(cmds, "arnoldRenderToTexture")
    except Exception:
        return False


def _write_map(path, coverage=1.0):
    """Write a map shaped like RTT's: a float RGBA EXR whose alpha is *coverage*.

    A fake RTT must write a map that reads -- the bake judges what it renders
    (an all-zero or unreadable map is a render Arnold aborted).
    """
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    import cv2
    import numpy as np

    if not cv2.imwrite(path, np.full((4, 4, 4), coverage, np.float32)):
        raise OSError(f"could not write {path}")


class TestArnoldAvailable(MayaTkTestCase):
    def test_returns_bool_without_raising(self):
        # Regression #1: must not raise even when mtoa is loaded.
        try:
            cmds.loadPlugin("mtoa")
        except Exception:
            pass
        self.assertIsInstance(TextureBaker.arnold_available(), bool)


class TestEnsureArnold(MayaTkTestCase):
    """A bake that asks for Arnold BY NAME loads it; a probe never does.

    mtoa ships with Maya but is often not auto-loaded. ``_resolve_backend``
    used to answer "arnold" with the non-loading probe, so an installed but
    unloaded plugin fell straight through to convertSolidTx -- and a lightmap
    bake, whose white card is an Arnold override, then refused outright.
    """

    def _probe(self, *answers):
        """Patch the side-effect-free probe to answer *answers* in turn."""
        return mock.patch.object(
            TextureBaker, "arnold_available", side_effect=list(answers)
        )

    def test_an_installed_but_unloaded_mtoa_is_loaded(self):
        from mayatk.core_utils.plugins._plugins import Plugins

        with (
            self._probe(False, True),
            mock.patch.object(Plugins, "load") as load,
        ):
            self.assertTrue(TextureBaker.ensure_arnold())
        load.assert_called_once_with("mtoa")

    def test_a_loaded_mtoa_is_not_loaded_again(self):
        from mayatk.core_utils.plugins._plugins import Plugins

        with self._probe(True), mock.patch.object(Plugins, "load") as load:
            self.assertTrue(TextureBaker.ensure_arnold())
        load.assert_not_called()

    def test_an_mtoa_that_cannot_load_is_no_arnold(self):
        from mayatk.core_utils.plugins._plugins import Plugins

        with (
            self._probe(False),
            mock.patch.object(
                Plugins, "load", side_effect=Plugins.LoadError("not installed")
            ),
        ):
            self.assertFalse(TextureBaker.ensure_arnold())

    def test_arnold_by_name_loads_it_but_auto_does_not(self):
        """``auto`` means "whatever this session has" -- no renderer boot."""
        baker = TextureBaker()
        with (
            self._probe(False),
            mock.patch.object(
                TextureBaker, "ensure_arnold", return_value=True
            ) as ensure,
        ):
            self.assertEqual(baker._resolve_backend("auto"), "convertSolidTx")
            ensure.assert_not_called()
            self.assertEqual(baker._resolve_backend("arnold"), "arnold")
            ensure.assert_called_once_with()


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestArnoldBakeOutputNaming(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="bake_lighting_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_bake_returns_existing_prefixed_path(self):
        # Regression #2: RTT names the file after the shape, not the transform.
        cube = cmds.polyCube(name="bakeCube")[0]
        long_name = cmds.ls(cube, long=True)[0]
        result = TextureBaker(resolution=64, samples=2, file_format="exr").bake(
            [cube], output_dir=self.tmp, prefix="bake_", backend="arnold"
        )
        self.assertIn(long_name, result)
        path = result[long_name]
        self.assertTrue(os.path.exists(path), f"missing: {path}")
        self.assertEqual(os.path.basename(path), "bake_bakeCube.exr")

    def test_bake_applies_suffix(self):
        # The <base><suffix> convention (e.g. "<object>_Lightmap").
        cube = cmds.polyCube(name="suffixCube")[0]
        result = TextureBaker(resolution=64, samples=2, file_format="exr").bake(
            [cube],
            output_dir=self.tmp,
            prefix="",
            suffix="_Lightmap",
            backend="arnold",
        )
        path = next(iter(result.values()))
        self.assertEqual(os.path.basename(path), "suffixCube_Lightmap.exr")


class _LayoutFixtures:
    """Fixtures for tests that read WHERE a bake landed in its map."""

    def _quadrant_plane(self, name):
        """Plane whose map1 fills 0-1 and whose lightmap set sits in the
        upper-right quadrant -- where the EXR's content lands names the layout
        that actually rendered."""
        plane = cmds.polyPlane(name=name, sx=1, sy=1)[0]
        shape = cmds.listRelatives(plane, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="lightmap")
        cmds.polyEditUV(
            f"{shape}.map[*]", pivotU=0.0, pivotV=0.0, scaleU=0.5, scaleV=0.5
        )
        cmds.polyEditUV(f"{shape}.map[*]", uValue=0.5, vValue=0.5, relative=True)
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="map1")
        return plane

    def _content_is_quadrant(self, path):
        """(nonzero_coverage, u_min_frac, v_row_max_frac) of *path*'s content."""
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("cv2 unavailable")
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        self.assertIsNotNone(img, f"unreadable bake output: {path}")
        nz = img[..., :3].max(axis=2) > 1e-6
        self.assertTrue(nz.any(), f"empty bake output: {path}")
        cols = np.where(nz.any(axis=0))[0]
        rows = np.where(nz.any(axis=1))[0]
        return (
            float(nz.mean()),
            cols.min() / nz.shape[1],
            rows.max() / nz.shape[0],
        )

    def _white_flat(self):
        flat = cmds.shadingNode("aiFlat", asShader=True)
        cmds.setAttr(f"{flat}.color", 1, 1, 1, type="double3")
        return flat


class TestBakeUvSetTargeting(_LayoutFixtures, MayaTkTestCase):
    """uv_set= must decide the layout the bake actually renders.

    Arnold ignores the scene's current UV set (probe-measured), so its paths
    pass RTT's own uv_set flag; the current-set switch remains as
    convertSolidTx's targeting plus the missing-set warning, and is restored
    after the bake. The content-position tests are the real contract: where
    the EXR's nonzero texels land names the layout that rendered.
    """

    @staticmethod
    def _current(shape):
        return (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]

    def _cube_with_lightmap_set(self):
        cube = cmds.polyCube(name="uvTargetCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        # A layout to bake into: an EMPTY set renders nothing, no map.
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="map1")
        return cube, shape

    def test_set_makes_current_and_returns_prior(self):
        cube, shape = self._cube_with_lightmap_set()
        baker = TextureBaker()
        prev = baker._set_current_uv_set(cube, "lightmap")
        self.assertEqual(self._current(shape), "lightmap")
        self.assertEqual(list(prev.values()), ["map1"])
        baker._restore_uv_sets(prev)
        self.assertEqual(self._current(shape), "map1")

    def test_missing_set_returns_empty_and_leaves_current(self):
        cube, shape = self._cube_with_lightmap_set()
        prev = TextureBaker()._set_current_uv_set(cube, "nope")
        self.assertEqual(prev, {})
        self.assertEqual(self._current(shape), "map1")

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_bake_into_uv_set_restores_current(self):
        cube, shape = self._cube_with_lightmap_set()
        tmp = tempfile.mkdtemp(prefix="bake_uvset_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=64, samples=2, file_format="exr").bake(
            [cube], output_dir=tmp, backend="arnold", uv_set="lightmap"
        )
        self.assertTrue(result)  # produced a file
        self.assertEqual(self._current(shape), "map1")  # scene left as found

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_bake_renders_the_requested_set_not_the_current_one(self):
        # arnoldRenderToTexture IGNORES the scene's current UV set
        # (probe-measured: with "lightmap" current and no flag, content still
        # covered map1's full 0-1) -- the target must ride the command's own
        # uv_set flag. This is the ROOM_ENV black-room bug: every wall's
        # bake landed on map1's layout while the committed atlas rect sampled
        # the lightmap layout -- bright bake, black walls.
        plane = self._quadrant_plane("uvFlagPlane")
        tmp = tempfile.mkdtemp(prefix="bake_uvflag_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # extend_edges off: this test reads which layout rendered from WHERE the
        # content lands, and edge extension deliberately fills the background,
        # which erases exactly that signal. The flag's own default is covered by
        # test_rtt_kwargs_extend_edges_by_default.
        result = TextureBaker(
            resolution=64, samples=1, file_format="exr", extend_edges=False
        ).bake(
            [plane],
            output_dir=tmp,
            backend="arnold",
            uv_set="lightmap",
            shader=self._white_flat(),
        )
        self.assertTrue(result)
        cover, u_min, v_row_max = self._content_is_quadrant(next(iter(result.values())))
        self.assertLess(cover, 0.5, "full-map content: the bake rendered map1")
        self.assertGreater(u_min, 0.3)  # right half...
        self.assertLess(v_row_max, 0.7)  # ...top rows (EXR row 0 == v 1)

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_batch_bake_carries_the_uv_set(self):
        # The batch path is one RTT call -- the one uv_set flag must reach it
        # too, and a batch whose objects agree on the target must stay a batch.
        # Planes are separated: two coincident surfaces bake to zeros (RTT
        # surface sampling cannot disambiguate coplanar twins).
        planes = [self._quadrant_plane(f"uvBatchPlane{i}") for i in range(2)]
        for i, p in enumerate(planes):
            cmds.move(i * 5.0, 0, 0, p)
        tmp = tempfile.mkdtemp(prefix="bake_uvbatch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(  # extend_edges off -- see the note above
            resolution=64, samples=1, file_format="exr", extend_edges=False
        ).bake(
            planes,
            output_dir=tmp,
            backend="arnold",
            batch=True,
            uv_set="lightmap",
            shader=self._white_flat(),
        )
        self.assertEqual(len(result), 2)
        for path in result.values():
            cover, u_min, v_row_max = self._content_is_quadrant(path)
            self.assertLess(cover, 0.5)
            self.assertGreater(u_min, 0.3)
            self.assertLess(v_row_max, 0.7)

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_batch_with_mixed_targets_partitions_and_stays_correct(self):
        # One RTT call takes ONE uv_set, so mixed per-object targets are
        # PARTITIONED into a call each rather than abandoning the batch (a
        # production room's meshes reuse differently named lightmap sets, so
        # the old all-or-nothing test cost a scene translation per object).
        # The observable that matters is unchanged: each object still bakes
        # its OWN target layout, not one another's.
        quad = self._quadrant_plane("uvMixQuad")
        full = cmds.polyPlane(name="uvMixFull", sx=1, sy=1)[0]
        cmds.move(5.0, 0, 0, full)  # coplanar twins bake to zeros
        tmp = tempfile.mkdtemp(prefix="bake_uvmix_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        long_quad = cmds.ls(quad, long=True)[0]
        long_full = cmds.ls(full, long=True)[0]
        result = TextureBaker(  # extend_edges off -- see the note above
            resolution=64, samples=1, file_format="exr", extend_edges=False
        ).bake(
            [quad, full],
            output_dir=tmp,
            backend="arnold",
            batch=True,
            uv_set={long_quad: "lightmap", long_full: "map1"},
            shader=self._white_flat(),
        )
        self.assertEqual(len(result), 2)
        cover_q, u_min_q, _ = self._content_is_quadrant(result[long_quad])
        self.assertLess(cover_q, 0.5)
        self.assertGreater(u_min_q, 0.3)
        cover_f, _, _ = self._content_is_quadrant(result[long_full])
        self.assertGreater(cover_f, 0.9)  # map1 fills the whole map

    def test_rtt_kwargs_extend_edges_by_default(self):
        """Bake past the island border unless a caller opts out.

        Without it Arnold writes partial-coverage edge texels whose RGB is
        premultiplied by that coverage -- a dark ring around every island, and a
        dark seam wherever two tiles meet, since both put their dark border on
        the same line. Measured on a lit cube at 128px: island edges 83.7%
        darker than the interior and 7.40% of the map partially covered; with
        the flag the partial texels go to 0.00% and the interior is unchanged.
        """
        self.assertIs(
            TextureBaker()._rtt_kwargs("/tmp", None).get("extend_edges"), True
        )
        off = TextureBaker(extend_edges=False)
        self.assertIs(off._rtt_kwargs("/tmp", None).get("extend_edges"), False)


class TestBakeDevice(MayaTkTestCase):
    """Which device Arnold renders the bake on -- a render option like any other.

    The GPU is the fast device at ANY size (no crossover to model, unlike
    blendertk's AUTO, which picks per object because a Cycles session is rebuilt
    for each one), so AUTO takes it wherever Arnold has one. It is not the
    25.9x once measured, though: that A/B ran one preset on both devices, and
    Arnold's GPU ignores the preset's GI samples -- it traced 1/16 of the CPU's
    rays and baked 5.1x its per-texel noise, with the means agreeing. At the
    same ray budget (see :meth:`TextureBaker._camera_samples`) a production
    floor measured 3.4s on the GPU against 24.2s on the CPU, noise 0.161 vs
    0.124 (2026-09-21).
    """

    def test_no_device_leaves_the_scene_alone(self):
        # The default must not silently change what the user renders on.
        self.assertEqual(TextureBaker()._device_settings(), {})

    def test_cpu_and_gpu_force_that_device(self):
        self.assertEqual(
            TextureBaker(device="CPU")._device_settings(), {"renderDevice": 0}
        )
        self.assertEqual(
            TextureBaker(device="GPU")._device_settings(), {"renderDevice": 1}
        )

    def test_auto_takes_the_gpu_only_where_arnold_has_one(self):
        """With a GPU: the GPU, Arnold's own CPU fallback pinned on beside it.
        Without one: the CPU, said so -- AUTO used to request the GPU blind and
        let Arnold fall back, so the baker could not know which device (and so
        which sample budget) the render would actually get."""
        baker = TextureBaker(device="auto")
        with mock.patch.object(TextureBaker, "gpu_available", return_value=True):
            self.assertEqual(
                baker._device_settings(),
                {"renderDevice": 1, "render_device_fallback": 1},
            )
        with mock.patch.object(TextureBaker, "gpu_available", return_value=False):
            self.assertEqual(baker._device_settings(), {"renderDevice": 0})

    def test_a_gpu_bake_adapts_between_the_presets_aa_and_its_gi_budget(self):
        """The sampling rule, per device and setting (no renderer needed).

        GPU + adaptive: every texel the preset's AA, Arnold's adaptive sampler
        up to AA x GI. GPU with ``adaptive`` off: AA x GI on every texel. A GPU
        budget no larger than the floor has nothing to adapt into, and the
        CPU honours the GI samples itself -- both pin adaptive sampling OFF.
        """
        baker = TextureBaker(samples=4)
        on_gpu = mock.patch.object(TextureBaker, "_renders_on_gpu", return_value=True)
        budget = mock.patch.object(TextureBaker, "_gpu_budget", return_value=16)
        with on_gpu, budget:
            self.assertEqual(
                baker._sampling_settings(),
                {
                    "enable_adaptive_sampling": True,
                    "AA_samples_max": 16,
                    "AA_adaptive_threshold": TextureBaker.ADAPTIVE_THRESHOLD,
                },
            )
            self.assertEqual(baker._camera_samples(), 4)
            fixed = TextureBaker(samples=4, adaptive=False)
            self.assertEqual(
                fixed._sampling_settings(), {"enable_adaptive_sampling": False}
            )
            self.assertEqual(fixed._camera_samples(), 16)
        with on_gpu, mock.patch.object(TextureBaker, "_gpu_budget", return_value=4):
            self.assertEqual(
                baker._sampling_settings(), {"enable_adaptive_sampling": False}
            )
            self.assertEqual(baker._camera_samples(), 4)
        with mock.patch.object(TextureBaker, "_renders_on_gpu", return_value=False):
            self.assertEqual(
                baker._sampling_settings(), {"enable_adaptive_sampling": False}
            )
            self.assertEqual(baker._camera_samples(), 4)

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_a_gpu_bake_samples_up_to_the_presets_gi_budget(self):
        """REGRESSION (2026-09-21): Arnold's GPU ignores ``GIDiffuseSamples``.

        Measured on a production floor: GI 4 and GI 8 baked BIT-IDENTICAL maps
        on the GPU, and the mobile preset (AA 4, GI 4) baked 5.1x the per-texel
        noise of the same preset on the CPU -- AA^2 = 16 first-bounce rays per
        texel against AA^2 * GI^2 = 256. Read as splotchy floors in the WebXR
        preview. The GPU takes the preset's diffuse budget in camera samples
        instead (AA x GI) -- as the ceiling of Arnold's adaptive sampler, so
        the shadows get it and a lit texel stops at the preset's AA: measured
        on the production floors, AA 16 everywhere took 381s for 1.06% shadow
        noise, adaptive 4..16 73s for 1.31%. On the real options node here;
        the CPU keeps the flags as given, adaptive sampling off.
        """
        from mtoa.core import createOptions

        createOptions()
        opts = "defaultArnoldRenderOptions"
        settings = {"GIDiffuseSamples": 4}
        gpu = TextureBaker(samples=4, device="GPU", render_settings=settings)
        with gpu._pinned_render_settings("arnold"):
            self.assertEqual(gpu._rtt_kwargs("/tmp", None)["aa_samples"], 4)
            self.assertTrue(cmds.getAttr(f"{opts}.enable_adaptive_sampling"))
            self.assertEqual(cmds.getAttr(f"{opts}.AA_samples_max"), 16)
            self.assertAlmostEqual(
                cmds.getAttr(f"{opts}.AA_adaptive_threshold"),
                TextureBaker.ADAPTIVE_THRESHOLD,
                places=6,
            )
        fixed = TextureBaker(
            samples=4, device="GPU", render_settings=settings, adaptive=False
        )
        with fixed._pinned_render_settings("arnold"):
            self.assertEqual(fixed._rtt_kwargs("/tmp", None)["aa_samples"], 16)
            self.assertFalse(cmds.getAttr(f"{opts}.enable_adaptive_sampling"))
        cpu = TextureBaker(samples=4, device="CPU", render_settings=settings)
        with cpu._pinned_render_settings("arnold"):
            self.assertEqual(cpu._rtt_kwargs("/tmp", None)["aa_samples"], 4)
            self.assertFalse(cmds.getAttr(f"{opts}.enable_adaptive_sampling"))

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_the_scenes_adaptive_sampling_never_rides_a_bake_and_comes_back(self):
        """A scene rendered with adaptive sampling on keeps it -- after the bake.

        The sampling was left to the scene before, so a user's own adaptive
        settings leaked into every bake; now each bake pins its own, and the
        scene's are restored with everything else.
        """
        from mtoa.core import createOptions

        createOptions()
        opts = "defaultArnoldRenderOptions"
        mine = {
            "enable_adaptive_sampling": True,
            "AA_samples_max": 20,
            "AA_adaptive_threshold": 0.05,
        }
        for attr, value in mine.items():
            self.addCleanup(
                cmds.setAttr, f"{opts}.{attr}", cmds.getAttr(f"{opts}.{attr}")
            )
            cmds.setAttr(f"{opts}.{attr}", value)
        with TextureBaker(samples=3, device="CPU")._pinned_render_settings("arnold"):
            self.assertFalse(cmds.getAttr(f"{opts}.enable_adaptive_sampling"))
        gpu = TextureBaker(
            samples=2, device="GPU", render_settings={"GIDiffuseSamples": 3}
        )
        with gpu._pinned_render_settings("arnold"):
            self.assertEqual(cmds.getAttr(f"{opts}.AA_samples_max"), 6)
            self.assertAlmostEqual(
                cmds.getAttr(f"{opts}.AA_adaptive_threshold"),
                TextureBaker.ADAPTIVE_THRESHOLD,
                places=6,
            )
        self.assertTrue(cmds.getAttr(f"{opts}.enable_adaptive_sampling"))
        self.assertEqual(cmds.getAttr(f"{opts}.AA_samples_max"), 20)
        self.assertAlmostEqual(
            cmds.getAttr(f"{opts}.AA_adaptive_threshold"), 0.05, places=6
        )

    def test_an_unknown_device_is_a_warning_not_a_wrong_device(self):
        baker = TextureBaker(device="quantum")
        with self.assertLogs(baker.logger, level="WARNING"):
            self.assertEqual(baker._device_settings(), {})

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_the_pinned_attributes_exist_on_the_options_node(self):
        # A misspelled attr degrades to a "not pinned" warning and bakes on
        # whatever the scene was set to -- silently the wrong device. Which is
        # exactly what happened: the fallback attribute is snake_case where
        # renderDevice beside it is camelCase, and only this check caught it.
        # The enums are pinned too, since 0/1 mean nothing without them.
        from mtoa.core import createOptions

        createOptions()
        for attr in (
            "renderDevice",
            "render_device_fallback",
            "GIDiffuseSamples",
            "enable_adaptive_sampling",
            "AA_samples_max",
            "AA_adaptive_threshold",
        ):
            self.assertTrue(
                cmds.attributeQuery(
                    attr, node="defaultArnoldRenderOptions", exists=True
                ),
                f"defaultArnoldRenderOptions.{attr} does not exist on this mtoa",
            )
        self.assertEqual(
            cmds.attributeQuery(
                "renderDevice", node="defaultArnoldRenderOptions", listEnum=True
            ),
            ["CPU:GPU"],
        )
        self.assertEqual(
            cmds.attributeQuery(
                "render_device_fallback",
                node="defaultArnoldRenderOptions",
                listEnum=True,
            ),
            ["Error:CPU"],
        )

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_the_device_is_restored_after_the_bake(self):
        # A GPU bake must not leave the user's scene rendering on the GPU.
        from mtoa.core import createOptions

        createOptions()
        before = cmds.getAttr("defaultArnoldRenderOptions.renderDevice")
        baker = TextureBaker(device="GPU")
        with baker._pinned_render_settings("arnold"):
            self.assertEqual(cmds.getAttr("defaultArnoldRenderOptions.renderDevice"), 1)
        self.assertEqual(
            cmds.getAttr("defaultArnoldRenderOptions.renderDevice"), before
        )


class TestBakeProgressCallback(MayaTkTestCase):
    """on_progress fires per object and can cancel -- no renderer needed.

    on_progress is invoked at the top of each object's iteration (before the
    render), so cancelling on the first call stops the bake before any render
    happens -- the wiring + cancel path are testable without Arnold.
    """

    def test_on_progress_called_and_cancel_stops_bake(self):
        cubes = [cmds.polyCube(name=f"progCube{i}")[0] for i in range(3)]
        tmp = tempfile.mkdtemp(prefix="bake_prog_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        seen = []

        def cb(done, total, name):
            seen.append((done, total))
            return False  # cancel immediately, before the first render

        result = TextureBaker(resolution=8).bake(
            cubes, output_dir=tmp, backend="convertSolidTx", on_progress=cb
        )
        self.assertEqual(seen, [(0, 3)])  # called once for object 0, then stopped
        self.assertEqual(result, {})  # cancelled before any file was produced


class TestBakeNaming(unittest.TestCase):
    """Output-name resolution (stem + collision) — pure logic, no render."""

    def test_resolve_stem_prefers_resolver(self):
        b = TextureBaker()
        self.assertEqual(
            b._resolve_stem(lambda o: "Mat_Base", "|grp|obj", "obj"), "Mat_Base"
        )
        self.assertEqual(
            b._resolve_stem({"|grp|obj": "Mat_Base"}, "|grp|obj", "obj"), "Mat_Base"
        )

    def test_resolve_stem_falls_back_to_leaf(self):
        b = TextureBaker()
        self.assertEqual(b._resolve_stem(None, "|obj", "obj"), "obj")  # no resolver
        self.assertEqual(b._resolve_stem(lambda o: None, "|obj", "obj"), "obj")  # empty
        self.assertEqual(b._resolve_stem({}, "|obj", "obj"), "obj")  # missing key

        def boom(_o):
            raise RuntimeError("nope")

        self.assertEqual(b._resolve_stem(boom, "|obj", "obj"), "obj")  # raised

    def test_unique_path_disambiguates_collisions(self):
        b = TextureBaker(file_format="exr")
        used = set()
        p1 = b._unique_path("/out", "Shared_Lightmap", used)
        p2 = b._unique_path("/out", "Shared_Lightmap", used)
        p3 = b._unique_path("/out", "Shared_Lightmap", used)
        self.assertEqual(os.path.basename(p1), "Shared_Lightmap.exr")
        self.assertEqual(os.path.basename(p2), "Shared_Lightmap_1.exr")
        self.assertEqual(os.path.basename(p3), "Shared_Lightmap_2.exr")

    def test_unique_path_honors_effective_format(self):
        # bake() overrides the requested format with the backend's effective
        # one (Arnold RTT has no format flag; it always writes EXR).
        b = TextureBaker(file_format="png")
        path = b._unique_path("/out", "Card", set(), "exr")
        self.assertEqual(os.path.basename(path), "Card.exr")

    def test_unique_path_skips_claimed_names_without_case(self):
        """A claimed file -- one something else still reads -- is stepped over
        exactly like a collision inside the bake."""
        b = TextureBaker(file_format="exr")
        claimed = {"shared_lightmap.exr", "shared_lightmap_1.exr"}
        path = b._unique_path("/out", "Shared_Lightmap", set(), "exr", claimed)
        self.assertEqual(os.path.basename(path), "Shared_Lightmap_2.exr")

    def test_unique_path_keeps_an_objects_own_name(self):
        """A name only the object being baked reads is its own to replace --
        a re-bake keeps its map's name -- while a name another object reads
        is stepped over, even for an object inside the same bake."""
        b = TextureBaker(file_format="exr")
        claims = {"mine_lm.exr": {"|me"}, "theirs_lm.exr": {"|them"}}
        own = b._unique_path("/out", "Mine_LM", set(), "exr", claims, owner="|me")
        other = b._unique_path("/out", "Theirs_LM", set(), "exr", claims, owner="|me")
        self.assertEqual(os.path.basename(own), "Mine_LM.exr")
        self.assertEqual(os.path.basename(other), "Theirs_LM_1.exr")


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestBakeStemEndToEnd(MayaTkTestCase):
    """End-to-end: the stem resolver names the actual file + progress reaches 100%."""

    def test_stem_names_output_and_progress_completes(self):
        cube = cmds.polyCube(name="longNodeName")[0]
        tmp = tempfile.mkdtemp(prefix="bake_stem_e2e_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        seen = []
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [cube],
            output_dir=tmp,
            prefix="",
            suffix="_Lightmap",
            backend="arnold",
            stem=lambda o: "Plants_Metal_Base_01",
            on_progress=lambda d, t, n: seen.append((d, t)) or True,
        )
        path = next(iter(result.values()))
        self.assertEqual(os.path.basename(path), "Plants_Metal_Base_01_Lightmap.exr")
        self.assertEqual(seen[0], (0, 1))  # per-object start tick
        self.assertEqual(seen[-1], (1, 1))  # final completion tick → 100%


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestPinnedRenderSettings(MayaTkTestCase):
    """render_settings are pinned on defaultArnoldRenderOptions, then restored."""

    def test_settings_set_during_bake_and_restored_after(self):
        from mtoa.core import createOptions

        createOptions()
        node = "defaultArnoldRenderOptions"
        cmds.setAttr(f"{node}.GIDiffuseDepth", 1)  # a known pre-bake state

        b = TextureBaker(render_settings={"GIDiffuseDepth": 4})
        with b._pinned_render_settings("arnold"):
            self.assertEqual(cmds.getAttr(f"{node}.GIDiffuseDepth"), 4)
        self.assertEqual(cmds.getAttr(f"{node}.GIDiffuseDepth"), 1)  # restored

    def test_unknown_attr_is_skipped_not_fatal(self):
        b = TextureBaker(render_settings={"NoSuchArnoldAttr": 7, "GIDiffuseDepth": 2})
        with b._pinned_render_settings("arnold"):
            self.assertEqual(
                cmds.getAttr("defaultArnoldRenderOptions.GIDiffuseDepth"), 2
            )

    def test_non_arnold_backend_is_a_noop(self):
        b = TextureBaker(render_settings={"GIDiffuseDepth": 4})
        with b._pinned_render_settings("convertSolidTx"):
            pass  # must not require or touch the Arnold options node

    def test_batch_bakes_all_objects_in_one_call(self):
        # Batch mode: one RTT call for the whole selection (7.45x measured);
        # per-shape files must map back to the right objects with the same
        # naming convention as the per-object loop.
        a = cmds.polyCube(name="batchA")[0]
        b = cmds.polyCube(name="batchB")[0]
        tmp = tempfile.mkdtemp(prefix="bake_batch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ticks = []
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [a, b],
            output_dir=tmp,
            prefix="",
            suffix="_LM",
            backend="arnold",
            batch=True,
            on_progress=lambda d, t, n: ticks.append((d, t)) or True,
        )
        self.assertEqual(len(result), 2)
        names = sorted(os.path.basename(p) for p in result.values())
        self.assertEqual(names, ["batchA_LM.exr", "batchB_LM.exr"])
        for p in result.values():
            self.assertTrue(os.path.exists(p))
        # One cancellable start tick + the final completion tick.
        self.assertEqual(ticks[0], (0, 2))
        self.assertEqual(ticks[-1], (2, 2))

    def test_batch_duplicate_shape_leaves_fall_back_to_loop(self):
        # RTT names batch output by shape leaf -- duplicates would overwrite
        # each other, so the batch must detect them and fall back per-object
        # (which dir-diffs between calls and stays collision-free).
        a = cmds.polyCube(name="dupBatch")[0]
        cmds.group(a, name="dupBatchGrp")
        la = cmds.ls("dupBatchGrp|dupBatch", long=True)[0]
        lb = cmds.ls(cmds.polyCube(name="dupBatch")[0], long=True)[0]
        tmp = tempfile.mkdtemp(prefix="bake_dupbatch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [la, lb],
            output_dir=tmp,
            prefix="",
            suffix="",
            backend="arnold",
            batch=True,
        )
        self.assertEqual(len(result), 2)  # both baked despite the collision
        self.assertNotEqual(result[la], result[lb])  # distinct files
        for p in result.values():
            self.assertTrue(os.path.exists(p))

    def test_instances_of_one_shape_batch_together(self):
        # THE production shape: 24 wall tiles on one mesh. They share a shape
        # leaf, but RTT writes each as "<transform>_<shapeLeaf>.exr", so they
        # do NOT collide -- the old leaf-only test rejected them anyway and
        # forced one full scene translation per object (measured: 275.4s for
        # 4 objects against 12.9s batched, because each call re-exports the
        # whole scene). They must batch, and map back to distinct files.
        a = cmds.polyCube(name="instBatchOne")[0]
        b = cmds.instance(a, name="instBatchTwo")[0]
        c = cmds.instance(a, name="instBatchThree")[0]
        cmds.move(3, 0, 0, b)
        cmds.move(6, 0, 0, c)
        longs = [cmds.ls(o, long=True)[0] for o in (a, b, c)]
        tmp = tempfile.mkdtemp(prefix="bake_instgroup_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            longs,
            output_dir=tmp,
            prefix="",
            suffix="_LM",
            backend="arnold",
            batch=True,
        )
        self.assertEqual(sorted(result), sorted(longs))
        paths = [result[o] for o in longs]
        self.assertEqual(len(set(paths)), 3, "instances collided onto one map")
        for p in paths:
            self.assertTrue(os.path.exists(p))

    def test_rtt_stem_qualifies_instances_only(self):
        # The collision test is only as good as this prediction.
        solo = cmds.polyCube(name="stemSolo")[0]
        solo_shape = cmds.listRelatives(solo, shapes=True, fullPath=True)[0]
        self.assertEqual(
            TextureBaker._rtt_stem(cmds.ls(solo, long=True)[0], solo_shape),
            "stemSoloShape",
        )
        inst = cmds.polyCube(name="stemInst")[0]
        cmds.instance(inst, name="stemInstTwin")
        inst_shape = cmds.listRelatives(inst, shapes=True, fullPath=True)[0]
        self.assertEqual(
            TextureBaker._rtt_stem(cmds.ls(inst, long=True)[0], inst_shape),
            "stemInst_stemInstShape",
        )

    def _bodies_with_one_leaf(self):
        """The production room's shape: two uninstanced bodies whose
        transform AND shape leaves recur -- ``MACHINE_A|BODY|BODYShape`` and
        ``MACHINE_B|BODY|BODY|BODYShape``."""
        # Long names throughout: from the second "BODY" on, a short one is
        # ambiguous -- which is the point of the fixture.
        machine_a = cmds.ls(cmds.group(empty=True, name="MACHINE_A"), long=True)[0]
        machine_b = cmds.ls(cmds.group(empty=True, name="MACHINE_B"), long=True)[0]
        inner = cmds.ls(
            cmds.group(empty=True, name="BODY", parent=machine_b), long=True
        )[0]
        bodies = []
        for parent in (machine_a, inner):
            cube = cmds.parent(cmds.polyCube(name="leafBody")[0], parent)[0]
            cmds.rename(f"{parent}|{cube.rsplit('|', 1)[-1]}", "BODY")
            body = f"{parent}|BODY"
            cmds.rename(
                cmds.listRelatives(body, shapes=True, fullPath=True)[0], "BODYShape"
            )
            bodies.append(body)
        return bodies

    def test_rtt_stem_qualifies_a_recurring_leaf_as_far_as_it_is_ambiguous(self):
        """RTT names a file after the shape's shortest UNIQUE path (measured on
        the production room: ``MACHINE_A_BODY_BODYShape.exr``,
        ``MACHINE_B_BODY_BODY_BODYShape.exr``) -- not its bare leaf."""
        stems = [
            TextureBaker._rtt_stem(
                b, cmds.listRelatives(b, shapes=True, fullPath=True)[0]
            )
            for b in self._bodies_with_one_leaf()
        ]
        self.assertEqual(
            stems, ["MACHINE_A_BODY_BODYShape", "MACHINE_B_BODY_BODY_BODYShape"]
        )

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_a_lone_body_whose_leaf_recurs_bakes_in_its_batch(self):
        """REGRESSION (2026-09-22): a batch of ONE such body rendered its map,
        matched it by the bare leaf, warned "produced no output" and returned
        nothing -- the panel baking one selected machine got no lightmap. (A
        batch of both fell back to per-object on a false stem collision.)"""
        body = self._bodies_with_one_leaf()[0]
        tmp = tempfile.mkdtemp(prefix="bake_leafbody_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        with self.assertLogs(baker.logger, level="INFO") as logs:
            result = baker.bake(
                [body],
                output_dir=tmp,
                prefix="",
                suffix="_LM",
                backend="arnold",
                batch=True,
            )
        # Placed by the BATCH, not rescued by the per-object retry.
        self.assertFalse(
            [line for line in logs.output if "re-bake one per call" in line]
        )
        self.assertIn(body, result)
        self.assertTrue(os.path.exists(result[body]))

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_a_map_the_batch_cannot_place_is_baked_again_per_object(self):
        """The net under any naming rule not yet met: an object the batch could
        not place goes round again through the per-object path (dir-diff, no
        name needed) instead of being dropped. The batch here places nothing,
        as it did for the production bodies."""
        cube = cmds.ls(cmds.polyCube(name="unplacedCube")[0], long=True)[0]
        tmp = tempfile.mkdtemp(prefix="bake_unplaced_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        with mock.patch.object(
            TextureBaker, "_bake_with_arnold_batch", return_value={}
        ):
            with self.assertLogs(baker.logger, level="WARNING") as logs:
                result = baker.bake(
                    [cube],
                    output_dir=tmp,
                    prefix="",
                    suffix="_LM",
                    backend="arnold",
                    batch=True,
                )
        self.assertIn(cube, result)
        self.assertTrue(os.path.exists(result[cube]))
        self.assertTrue(any("re-bake one per call" in line for line in logs.output))

    def test_a_fallback_spelling_never_takes_another_objects_file(self):
        """The older spellings are a net, not a claim: object X, whose predicted
        stem found nothing, must not take the file object Y predicted just
        because X's bare leaf spells the same -- X would ship Y's lighting in
        silence. X comes back unplaced (bake re-bakes it); Y keeps its map."""
        x = cmds.ls(cmds.polyCube(name="stealX")[0], long=True)[0]
        cmds.rename(cmds.listRelatives(x, shapes=True, fullPath=True)[0], "yStem")
        y = cmds.ls(cmds.polyCube(name="stealY")[0], long=True)[0]
        tmp = tempfile.mkdtemp(prefix="bake_steal_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def fake_rtt(**kwargs):  # RTT writes ONE file: Y's predicted name
            _write_map(os.path.join(kwargs["folder"], "yStem.exr"))

        def stem(long_name, shape):
            return "yStem" if long_name == y else "x_predicted_nothing"

        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        with (
            mock.patch.object(
                cmds, "arnoldRenderToTexture", side_effect=fake_rtt, create=True
            ),
            mock.patch.object(TextureBaker, "_rtt_stem", side_effect=stem),
        ):
            result = baker._bake_with_arnold_batch(
                [x, y], tmp, "", "_LM", None, None, None, "exr", None
            )
        self.assertEqual(sorted(result), [y])
        self.assertTrue(os.path.exists(result[y]))

    def test_a_batch_never_names_a_map_after_a_claimed_file(self):
        """``claims`` reach the batch's own naming, not just the per-object
        loop's: a file another object still reads is never written over."""
        cube = cmds.ls(cmds.polyCube(name="keepOut")[0], long=True)[0]
        tmp = tempfile.mkdtemp(prefix="bake_claimed_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        theirs = os.path.join(tmp, "keepOut_LM.exr")
        with open(theirs, "wb") as fh:
            fh.write(b"theirs")

        def fake_rtt(**kwargs):
            _write_map(os.path.join(kwargs["folder"], "keepOutRtt.exr"))

        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        with (
            mock.patch.object(
                cmds, "arnoldRenderToTexture", side_effect=fake_rtt, create=True
            ),
            mock.patch.object(TextureBaker, "_rtt_stem", return_value="keepOutRtt"),
        ):
            result = baker._bake_with_arnold_batch(
                [cube],
                tmp,
                "",
                "_LM",
                None,
                None,
                None,
                "exr",
                None,
                claims={"keepout_lm.exr": {"|someoneElse"}},
            )
        self.assertEqual(os.path.basename(result[cube]), "keepOut_LM_1.exr")
        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), b"theirs")

    def test_a_per_object_bake_never_names_a_map_after_a_claimed_file(self):
        cube = cmds.ls(cmds.polyCube(name="keepOutSolo")[0], long=True)[0]
        tmp = tempfile.mkdtemp(prefix="bake_claimed_solo_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def render(obj, output_dir, shader=None, uv_set=None, resolution=None):
            path = os.path.join(output_dir, "rtt_raw.exr")
            with open(path, "wb") as fh:
                fh.write(b"x")
            return path

        baker = TextureBaker(
            resolution=16, samples=1, file_format="exr", translation_guard=False
        )
        with (
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(baker, "_bake_with_arnold", side_effect=render),
        ):
            result = baker.bake(
                [cube],
                output_dir=tmp,
                prefix="",
                suffix="_LM",
                backend="arnold",
                claims=["KeepOutSolo_LM.exr"],  # claimed outright, any case
            )
        self.assertEqual(os.path.basename(result[cube]), "keepOutSolo_LM_1.exr")

    def test_batch_single_instanced_object_maps_qualified_stem(self):
        # An instanced shape gets a path-qualified RTT filename
        # ("<transform>_<shapeLeaf>.exr") even when it is the ONLY object in
        # the call -- sibling instances elsewhere in the scene are enough to
        # force qualified Arnold node names. The batch result mapping must
        # match that spelling too (regression: it looked for the bare shape
        # leaf, found nothing, and warned "produced no output" while the
        # rendered file sat in the output dir).
        a = cmds.polyCube(name="instBatchA")[0]
        cmds.instance(a, name="instBatchB")  # sibling stays OUT of the bake
        la = cmds.ls(a, long=True)[0]
        tmp = tempfile.mkdtemp(prefix="bake_instbatch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [la],
            output_dir=tmp,
            prefix="",
            suffix="_LM",
            backend="arnold",
            batch=True,
        )
        self.assertEqual(list(result), [la])
        self.assertTrue(os.path.exists(result[la]))
        self.assertEqual(os.path.basename(result[la]), "instBatchA_LM.exr")

    def test_arnold_format_request_is_pinned_to_exr(self):
        # A png request with the Arnold backend must yield real .exr output
        # (RTT has no format flag), not EXR bytes behind a .png name.
        cube = cmds.polyCube(name="fmtCube")[0]
        tmp = tempfile.mkdtemp(prefix="bake_fmt_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=16, samples=1, file_format="png").bake(
            [cube], output_dir=tmp, backend="arnold"
        )
        self.assertTrue(result)
        path = next(iter(result.values()))
        self.assertTrue(path.endswith(".exr"), path)
        self.assertTrue(os.path.exists(path))


class TestResolveMeshes(MayaTkTestCase):
    """Only mesh transforms reach a renderer.

    A production selection routinely holds more than geometry -- the room's
    lights (the Blender-bridge bake needs those selected) and an export
    locator. Arnold RTT does not degrade on those: it raises per object
    ("quad_light nodes are not supported types", "not exported to Arnold
    world") and then reports success while writing no file.
    """

    def test_drops_lights_locators_and_empty_groups(self):
        cube = cmds.ls(cmds.polyCube(name="bakeMesh")[0], long=True)[0]
        light = cmds.ls(
            cmds.listRelatives(
                cmds.shadingNode("areaLight", asLight=True), parent=True, fullPath=True
            )
            or [cmds.shadingNode("areaLight", asLight=True)],
            long=True,
        )[0]
        locator = cmds.ls(cmds.spaceLocator(name="data_export")[0], long=True)[0]
        group = cmds.ls(cmds.group(empty=True, name="emptyGrp"), long=True)[0]

        self.assertEqual(
            TextureBaker.resolve_meshes([cube, light, locator, group]), [cube]
        )

    def test_drops_a_mesh_with_no_faces(self):
        # An empty mesh node has no surface either,
        # and Arnold does not skip one: measured 2026-09-23 (mtoa, Maya 2025), a
        # lightmap bake with one in scope crashed mayapy natively in ai.dll.
        cube = cmds.ls(cmds.polyCube(name="solidMesh")[0], long=True)[0]
        empty = cmds.createNode("transform", name="emptyMesh")
        cmds.createNode("mesh", name="emptyMeshShape", parent=empty)
        empty = cmds.ls(empty, long=True)[0]
        self.assertEqual(cmds.polyEvaluate(empty, face=True), 0)

        self.assertEqual(TextureBaker.resolve_meshes([empty, cube]), [cube])

    def test_shapes_and_components_resolve_to_their_transform(self):
        cube = cmds.ls(cmds.polyCube(name="compMesh")[0], long=True)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        # Same transform reached three ways -> one entry, not three.
        self.assertEqual(
            TextureBaker.resolve_meshes([cube, shape, f"{cube}.f[0]"]), [cube]
        )

    def test_empty_input_does_not_fall_back_to_the_selection(self):
        # [] means "nothing was in scope", NOT "use the selection" -- otherwise
        # an empty scope silently escalates into baking whatever is selected.
        cmds.select(cmds.polyCube(name="selectedMesh")[0], replace=True)
        self.assertEqual(TextureBaker.resolve_meshes([]), [])

    def test_bake_refuses_a_selection_with_no_mesh(self):
        light = cmds.shadingNode("areaLight", asLight=True)
        parent = cmds.listRelatives(light, parent=True, fullPath=True)
        result = TextureBaker(resolution=16, samples=1).bake(
            [parent[0] if parent else light], output_dir=self.tmp_dir()
        )
        self.assertEqual(result, {})

    def tmp_dir(self):
        path = tempfile.mkdtemp(prefix="bake_nonmesh_")
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestBakeDetectionSurvivesStrayOutputs(MayaTkTestCase):
    """A stray raw-named output from a failed placement must not blind the bake.

    Measured in production: a placement failure leaves the raw RTT-named file
    beside the atlas; the NEXT bake overwrites that same path, the name-set
    dir-diff sees no new file, and the object drops again -- the same meshes
    went black in consecutive pushes (deterministic, not the sync race).
    Detection has to be overwrite-aware: an mtime change is a new output.
    """

    def test_overwritten_stray_is_still_detected(self):
        plane = cmds.polyPlane(name="strayPlane", sx=1, sy=1)[0]
        flat = cmds.shadingNode("aiFlat", asShader=True)
        cmds.setAttr(f"{flat}.color", 1, 1, 1, type="double3")
        tmp = tempfile.mkdtemp(prefix="bake_stray_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # The stray a prior failed placement left behind, under the exact
        # name RTT will write again.
        with open(os.path.join(tmp, "strayPlaneShape.exr"), "wb") as fh:
            fh.write(b"stale")
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [plane], output_dir=tmp, backend="arnold", shader=flat
        )
        self.assertEqual(len(result), 1, "the overwritten output went undetected")
        out = next(iter(result.values()))
        self.assertGreater(
            os.path.getsize(out), 1000, "fresh render, not the stale stray"
        )


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestForcedShaderReachesInstancedTargets(MayaTkTestCase):
    """``shader=`` must decide what the target renders -- instances included.

    Arnold's ``-shader`` flag is silently lost on the one instance that owns a
    shared mesh's shading-group membership: it renders its assigned material,
    so a lighting-only bake comes back as albedo x lighting for that instance
    alone. Measured on a 24-instance wall (ROOM_ENV): the owning tile baked
    16% hot with a 10-17% step at every shared edge, against 25 boundaries
    continuous to 3% -- one bright rectangle with hard edges in the preview.
    """

    @staticmethod
    def _groups(obj):
        shape = cmds.listRelatives(
            obj, shapes=True, noIntermediate=True, fullPath=True
        )[0]
        return sorted(cmds.listSets(object=shape, type=1) or [])

    def test_shared_mesh_owner_bakes_with_the_override_not_its_material(self):
        from mayatk.mat_utils._mat_utils import MatUtils

        base = cmds.polyPlane(name="tile", sx=1, sy=1)[0]
        twin = cmds.instance(base, name="tileTwin")[0]
        cmds.move(3, 0, 0, twin)
        wall = cmds.shadingNode("lambert", asShader=True, name="wallMat")
        MatUtils.assign_mat([base, twin], wall)
        wall_sg = cmds.listConnections(wall, type="shadingEngine")[0]
        card = cmds.shadingNode("lambert", asShader=True, name="whiteCard")
        # Give the card its group up front so a target that never picked it up
        # reads as a plain assertion rather than a missing-node error.
        card_sg = MatUtils.create_shading_group(card)

        tmp = tempfile.mkdtemp(prefix="bake_forced_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        during = {}

        def record(
            _self,
            long_name,
            output_dir,
            shader,
            uv_set=None,
            resolution=None,
        ):
            during[long_name.rsplit("|", 1)[-1]] = self._groups(long_name)
            path = os.path.join(output_dir, f"{long_name.rsplit('|', 1)[-1]}.exr")
            with open(path, "wb") as fh:
                fh.write(b"x" * 2048)
            return path

        with mock.patch.object(TextureBaker, "_bake_with_arnold", record):
            TextureBaker(resolution=16, samples=1, file_format="exr").bake(
                [base, twin], output_dir=tmp, backend="arnold", shader=card
            )

        for leaf in ("tile", "tileTwin"):
            self.assertIn(
                card_sg, during[leaf], f"{leaf} did not render with the bake shader"
            )
            self.assertNotIn(
                wall_sg, during[leaf], f"{leaf} still carried its own material"
            )
        # ... and the scene is handed back exactly as it was found.
        for obj in (base, twin):
            self.assertEqual(
                self._groups(obj), [wall_sg], "the bake shader outlived the bake"
            )

    def test_an_instanced_target_with_an_override_bakes_per_object_and_the_rest_batch(
        self,
    ):
        """An instanced target never rides a batch that carries ``shader=``.

        Arnold drops ``-shader`` on the instance carrying a shared mesh's
        shading assignment, and the owner cannot be identified up front
        (``instObjGroups`` connections are reported relative to the DAG path
        queried through, so every instance claims ownership). The batch used
        to keep instances and re-bake afterwards the tiles whose mean strayed
        from their instance group's median -- and on a production room (46
        instanced targets, mobile), measured against an all-per-object
        reference, that test flagged 33 correct tiles and missed three hot
        ones (+13% / +29% / +54%: bright wall panels in the WebXR preview),
        while taking 428s against 281s per-object. Instanced targets now bake
        one per call, where ``_forced_shader`` guarantees the card and leaves
        every sibling on its real material; uninstanced targets still batch.
        """

        from mayatk.mat_utils._mat_utils import MatUtils

        base = cmds.polyPlane(name="batchTile", sx=1, sy=1)[0]
        sibling = cmds.instance(base, name="batchTileTwin")[0]
        cmds.move(3, 0, 0, sibling)
        other = cmds.polyPlane(name="batchOther", sx=1, sy=1)[0]
        cmds.move(0, 0, 3, other)
        other2 = cmds.polyPlane(name="batchOtherTwo", sx=1, sy=1)[0]
        cmds.move(0, 0, 6, other2)
        wall = cmds.shadingNode("lambert", asShader=True, name="batchWallMat")
        MatUtils.assign_mat([base, sibling, other, other2], wall)
        wall_sg = cmds.listConnections(wall, type="shadingEngine")[0]
        card = cmds.shadingNode("lambert", asShader=True, name="batchCard")
        card_sg = MatUtils.create_shading_group(card)

        tmp = tempfile.mkdtemp(prefix="bake_batch_forced_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        batched = []
        rebaked = []
        during = {}

        def batch_call(_self, objects, output_dir, *args, **kwargs):
            batched.append(list(objects))
            out = {}
            for o in objects:
                long_name = cmds.ls(o, long=True)[0]
                leaf = long_name.rsplit("|", 1)[-1]
                path = os.path.join(output_dir, f"{leaf}.exr")
                with open(path, "wb") as fh:
                    fh.write(b"b" * 2048)
                out[long_name] = path
            return out

        def per_object(
            _self,
            long_name,
            output_dir,
            shader,
            uv_set=None,
            resolution=None,
        ):
            leaf = long_name.rsplit("|", 1)[-1]
            rebaked.append(leaf)
            during[leaf] = self._groups(long_name)
            during["sibling"] = self._groups(sibling)
            path = os.path.join(output_dir, f"{leaf}_rebake.exr")
            with open(path, "wb") as fh:
                fh.write(b"x" * 2048)
            return path

        with (
            mock.patch.object(TextureBaker, "_bake_with_arnold_batch", batch_call),
            mock.patch.object(TextureBaker, "_bake_with_arnold", per_object),
        ):
            result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
                [base, other, other2],
                output_dir=tmp,
                backend="arnold",
                shader=card,
                batch=True,
            )

        # ONE batch call, holding the uninstanced targets only.
        self.assertEqual(len(batched), 1)
        self.assertEqual(
            sorted(o.rsplit("|", 1)[-1] for o in batched[0]),
            ["batchOther", "batchOtherTwo"],
        )
        # The instanced target baked once, on its own, under the card.
        self.assertEqual(rebaked, ["batchTile"])
        self.assertIn(
            card_sg, during["batchTile"], "the instance missed the bake shader"
        )
        self.assertEqual(
            during["sibling"],
            [wall_sg],
            "an unselected instance of the same mesh was dragged into the bake",
        )
        self.assertEqual(len(result), 3)
        for obj in (base, sibling, other, other2):
            self.assertEqual(
                self._groups(obj), [wall_sg], "the bake shader outlived the bake"
            )

    def test_an_unassigned_target_is_left_unassigned(self):
        """Restoring must not invent a material the object never had."""
        from mayatk.mat_utils._mat_utils import MatUtils

        plane = cmds.polyPlane(name="bareTile", sx=1, sy=1)[0]
        shape = cmds.listRelatives(
            plane, shapes=True, noIntermediate=True, fullPath=True
        )[0]
        for sg in cmds.listSets(object=shape, type=1) or []:
            cmds.sets(shape, edit=True, remove=sg)
        self.assertEqual(self._groups(plane), [], "test setup: expected no material")

        card = cmds.shadingNode("lambert", asShader=True, name="bareCard")
        MatUtils.create_shading_group(card)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        with baker._forced_shader(plane, card):
            pass
        self.assertEqual(
            self._groups(plane), [], "the bake shader was left behind on the object"
        )


class TestPlaceOutputSurvivesLockedDestination(MayaTkTestCase):
    """A locked destination must not cost the artist a finished bake.

    Measured in production: the project's ``sourceimages`` lives on a synced
    Dropbox share, so ``os.replace`` onto a previous map raised
    ``[WinError 32] The process cannot access the file because it is being
    used by another process`` — and the caller logged "Bake failed" and
    dropped the object, discarding a render that had already been paid for.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="bake_lock_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _src(self, name="raw.exr"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(b"x")
        return path

    def test_locked_destination_falls_back_to_an_adjacent_name(self):
        baker = TextureBaker(resolution=16, samples=1)
        src = self._src()
        dst = os.path.join(self.tmp, "ROOM_ENV_Lightmap_9.exr")
        open(dst, "wb").close()  # the "previous" map, held open elsewhere

        real_replace = os.replace
        calls = []

        def flaky_replace(a, b):
            calls.append(b)
            if os.path.abspath(b) == os.path.abspath(dst):
                raise PermissionError(32, "used by another process")
            return real_replace(a, b)

        with mock.patch("os.replace", flaky_replace):
            out = baker._place_output(src, dst, set())

        self.assertNotEqual(os.path.abspath(out), os.path.abspath(dst))
        self.assertTrue(os.path.exists(out), out)
        self.assertFalse(os.path.exists(src), "the bake should have been moved")
        self.assertEqual(len(calls), 2, "should retry exactly once under a new name")

    def test_locked_source_still_places_via_copy(self):
        # The sync client holds the SOURCE too: it indexes each freshly
        # written RTT output, and renaming a file held without delete-share
        # raises the same WinError 32 whatever destination is tried -- so the
        # adjacent-name retry cannot help. Measured in production: 4 of a
        # room's 46 maps stayed under their raw RTT names, dropped out of the
        # atlas, and rendered as BLACK objects in the preview. A read-share
        # lock still permits copying; the finished bake must land at the
        # recorded path either way.
        baker = TextureBaker(resolution=16, samples=1)
        src = self._src("DOOR_A_DOOR_AShape.exr")
        dst = os.path.join(self.tmp, "DOOR_A_Lightmap.exr")

        def source_locked(a, b):
            raise PermissionError(32, "used by another process")

        with mock.patch("os.replace", source_locked):
            out = baker._place_output(src, dst, set())

        self.assertEqual(os.path.abspath(out), os.path.abspath(dst))
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(), b"x")

    def test_unlocked_destination_is_replaced_in_place(self):
        baker = TextureBaker(resolution=16, samples=1)
        src = self._src()
        dst = os.path.join(self.tmp, "final.exr")
        out = baker._place_output(src, dst, set())
        self.assertEqual(os.path.abspath(out), os.path.abspath(dst))
        self.assertTrue(os.path.exists(dst))

    def test_a_permanently_locked_directory_raises_instead_of_looping(self):
        # A lock on the DIRECTORY refuses every candidate name equally --
        # renames AND the copy fallback alike (a rename-only refusal is the
        # locked-source case, rescued by the copy). It must terminate and
        # report: an unbounded retry loop here would hang Maya with no error.
        baker = TextureBaker(resolution=16, samples=1)
        src = self._src()
        dst = os.path.join(self.tmp, "locked.exr")
        calls = []

        def always_locked(*_a, **_kw):
            calls.append(_a)
            raise PermissionError(32, "used by another process")

        with (
            mock.patch("os.replace", always_locked),
            mock.patch("shutil.copy2", always_locked),
        ):
            with self.assertRaises(PermissionError):
                baker._place_output(src, dst, set())
        self.assertLessEqual(len(calls), TextureBaker._PLACE_ATTEMPTS + 2)

    def test_a_missing_source_still_raises(self):
        # The fallback is for a locked DESTINATION only -- a genuinely broken
        # move must not spin through adjacent names forever.
        baker = TextureBaker(resolution=16, samples=1)
        with self.assertRaises(OSError):
            baker._place_output(
                os.path.join(self.tmp, "nope.exr"),
                os.path.join(self.tmp, "out.exr"),
                set(),
            )


def _stingray_loadable():
    try:
        if not cmds.pluginInfo("shaderFXPlugin", q=True, loaded=True):
            cmds.loadPlugin("shaderFXPlugin")
        return True
    except Exception:
        return False


@unittest.skipUnless(
    _arnold_loadable() and _stingray_loadable(),
    "mtoa + shaderFXPlugin required (aiSurfaceShader attr / StingrayPBS node)",
)
class TestArnoldTranslationGuard(MayaTkTestCase):
    """Game (ShaderFX) materials must not bake as Arnold's error magenta.

    MtoA renders untranslatable shaders bright magenta, and with GI on that
    magenta BOUNCES: measured on a production room, the floor around
    StingrayPBS racks baked magenta-tinted shadows (dark-texel chroma
    3.00/0.21/2.89 -- ~85% pure (1,0,1)) while props away from the racks
    stayed neutral. The guard stands in an albedo-matched standardSurface on
    each affected shading group's aiSurfaceShader slot for the bake, exactly
    the manual workaround the room's walls already carried.
    """

    def _stingray_sg(self, name="rack"):
        shader = cmds.shadingNode("StingrayPBS", asShader=True, name=f"{name}_srp")
        # A fresh StingrayPBS node carries NO graph attributes (base_color,
        # TEX_color_map...) until its ShaderFX graph initializes -- the very
        # graph-dependence the guard probes around.
        try:
            cmds.shaderfx(sfxnode=shader, initShaderAttributes=True)
        except Exception:
            pass
        if not cmds.attributeQuery("base_color", node=shader, exists=True):
            self.skipTest("StingrayPBS default graph attrs unavailable")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        cmds.sets(cube, edit=True, forceElement=sg)
        return shader, sg, cube

    @staticmethod
    def _override_source(sg):
        src = cmds.listConnections(
            f"{sg}.aiSurfaceShader", source=True, destination=False
        )
        return src[0] if src else None

    @staticmethod
    def _second_group(shader, name):
        """Another group fed by *shader*, holding ``<name>_geo`` -- a material
        consolidated across objects keeps one group per object."""
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cmds.polyCube(name=f"{name}_geo")[0], edit=True, forceElement=sg)
        return sg

    def test_guard_covers_every_group_of_a_shared_game_material(self):
        """One StingrayPBS on two groups (a bench's top and its legs): both
        render through the stand-in. The guard bridged per MATERIAL and the
        bridge rode the material's first group, so the legs baked error
        magenta and bounced it onto the floor under the bench (production
        soldering room, 2026-09-23)."""
        shader, top, _cube = self._stingray_sg("bench")
        legs = self._second_group(shader, "legs")
        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            standins = (self._override_source(top), self._override_source(legs))
            self.assertNotIn(None, standins, "a group of the material kept no stand-in")
        self.assertEqual(
            (self._override_source(top), self._override_source(legs)), (None, None)
        )
        for node in set(standins):
            self.assertFalse(cmds.objExists(node), "guard must delete its bridge")

    def test_guard_stand_in_bakes_no_ambient_occlusion(self):
        """The stand-in leaves a game material's AO map out of its albedo: the
        bake traces the occlusion the map approximates, so multiplied in, every
        bounce darkened twice (production office ceilings: 0.77x)."""
        shader, sg, _cube = self._stingray_sg("aoRack")
        for name, attr in (
            ("aoRack_BaseColor.png", "TEX_color_map"),
            ("aoRack_AO.png", "TEX_ao_map"),
        ):
            if not cmds.attributeQuery(attr, node=shader, exists=True):
                self.skipTest(f"StingrayPBS has no {attr}")
            fn = cmds.shadingNode("file", asTexture=True)
            cmds.setAttr(f"{fn}.fileTextureName", name, type="string")
            cmds.connectAttr(f"{fn}.outColor", f"{shader}.{attr}", force=True)
        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            standin = self._override_source(sg)
            mult = cmds.listConnections(f"{standin}.baseColor", type="aiMultiply")
            self.assertTrue(mult, "the stand-in has no base-colour multiply")
            self.assertTrue(
                cmds.listConnections(
                    f"{mult[0]}.input1", source=True, destination=False
                ),
                "the stand-in lost the base colour map",
            )
            self.assertFalse(
                any(
                    cmds.listConnections(
                        f"{mult[0]}.{plug}", source=True, destination=False
                    )
                    for plug in ("input2", "input2R", "input2G", "input2B")
                ),
                "the stand-in multiplies AO into the bake's albedo",
            )

    def test_guard_fills_the_groups_an_authored_bridge_misses_and_puts_them_back(
        self,
    ):
        """An authored override on one group is respected, the material's
        other groups still bake through Arnold, and both come back exactly as
        they were. (The authored group is the material's NEWER one: Maya lists
        it first, which is the group a per-material check used to read.)"""
        shader, open_sg, _cube = self._stingray_sg("openBench")
        authored_sg = self._second_group(shader, "authoredLegs")
        authored = cmds.shadingNode(
            "standardSurface", asShader=True, name="authoredLegs_ai"
        )
        cmds.connectAttr(
            f"{authored}.outColor", f"{authored_sg}.aiSurfaceShader", force=True
        )
        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            self.assertEqual(self._override_source(authored_sg), authored)
            self.assertIsNotNone(self._override_source(open_sg), "left magenta")
        self.assertEqual(self._override_source(authored_sg), authored)
        self.assertIsNone(self._override_source(open_sg))
        self.assertTrue(cmds.objExists(authored))

    def test_a_shared_game_material_bounces_no_magenta_onto_the_floor(self):
        """The symptom, end to end: a lit wall standing on a white floor shares
        one StingrayPBS with a prop elsewhere, through a second group. The
        group the stand-in missed rendered error magenta and bounced it onto
        the floor -- on the production soldering room, ~11% of the texels of
        the two floor pieces under the bench came back over 10% magenta."""
        if not _cv2_available():
            self.skipTest("cv2/numpy unavailable for map means")
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        shader, _wall_sg, wall = self._stingray_sg("magentaWall")
        self._second_group(shader, "magentaProp")
        cmds.move(0, 0, 10, "magentaProp_geo")  # off the floor, out of the way
        cmds.scale(0.2, 2.0, 4.0, wall)
        cmds.move(-1.6, 1.0, 0, wall)
        floor = cmds.polyPlane(name="magentaFloor", w=4, h=4, sx=1, sy=1)[0]
        card = cmds.shadingNode("lambert", asShader=True, name="magentaCard")
        cmds.setAttr(f"{card}.color", 1, 1, 1, type="double3")
        # A grazing light from +X: the wall's inner face takes it nearly
        # head-on and the floor at a slant, so what the wall bounces is a large
        # part of what the floor beside it receives.
        cmds.directionalLight(intensity=2.0, rotation=(-20, 90, 0))

        tmp = tempfile.mkdtemp(prefix="bake_magenta_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        target = cmds.ls(floor, long=True)[0]
        result = TextureBaker(resolution=32, samples=2, file_format="exr").bake(
            [target],
            output_dir=tmp,
            prefix="",
            suffix="_LM",
            backend="arnold",
            shader=card,
        )
        img = cv2.imread(result[target], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        self.assertIsNotNone(img, "unreadable floor map")
        bgr = img[..., :3].reshape(-1, 3).astype(float)
        level = float(bgr.mean())
        self.assertGreater(level, 0.0, "black bake, nothing verified")
        # Magenta = red and blue over green; neutral bounce keeps it ~0. The
        # production probe's score: the share of texels over 10% of the level.
        excess = ((bgr[:, 2] + bgr[:, 0]) / 2 - bgr[:, 1]) / level
        share = float((excess > 0.10).mean())
        self.assertLess(
            share,
            0.01,
            f"{share:.1%} of the floor over 10% magenta (max {excess.max():.2f})",
        )

    def test_guard_bridges_and_restores(self):
        shader, sg, _cube = self._stingray_sg()

        baker = TextureBaker(resolution=16, samples=1)
        with baker.arnold_translation_guard():
            standin = self._override_source(sg)
            self.assertIsNotNone(standin, "guard must wire aiSurfaceShader")
            # The stand-in IS the existing ArnoldBridge tool, not a bespoke
            # shader -- one implementation of Stingray->Arnold parity.
            self.assertEqual(cmds.nodeType(standin), "aiStandardSurface")
        self.assertIsNone(
            self._override_source(sg), "guard must remove its bridge on exit"
        )
        self.assertFalse(
            cmds.objExists(standin), "guard must delete its bridge on exit"
        )
        # The exported material and its group are untouched.
        self.assertTrue(cmds.objExists(shader))
        self.assertTrue(cmds.objExists(sg))

    def test_guard_respects_an_authored_override(self):
        _shader, sg, _cube = self._stingray_sg("authored")
        authored = cmds.shadingNode(
            "standardSurface", asShader=True, name="authored_ai"
        )
        cmds.connectAttr(f"{authored}.outColor", f"{sg}.aiSurfaceShader", force=True)
        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            self.assertEqual(self._override_source(sg), authored)
        self.assertEqual(self._override_source(sg), authored)

    def test_guard_ignores_arnold_native_shaders(self):
        lam = cmds.shadingNode("lambert", asShader=True, name="native_lam")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="nativeSG"
        )
        cmds.connectAttr(f"{lam}.outColor", f"{sg}.surfaceShader", force=True)
        cube = cmds.polyCube(name="native_geo")[0]
        cmds.sets(cube, edit=True, forceElement=sg)
        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            self.assertIsNone(self._override_source(sg))

    def test_guard_bridge_carries_color_and_emissive_maps(self):
        shader, sg, _cube = self._stingray_sg("mapped")
        cfile = cmds.shadingNode("file", asTexture=True, name="mapped_color_file")
        efile = cmds.shadingNode("file", asTexture=True, name="mapped_emis_file")
        # ArnoldBridge resolves map types from FILE NAMES (MapFactory), not
        # from the graph-dependent Stingray plugs -- give it real names.
        cmds.setAttr(
            f"{cfile}.fileTextureName", "C:/tex/rack_Base_Color.png", type="string"
        )
        cmds.setAttr(
            f"{efile}.fileTextureName", "C:/tex/rack_Emissive.png", type="string"
        )
        cmds.connectAttr(f"{cfile}.outColor", f"{shader}.TEX_color_map", force=True)
        cmds.setAttr(f"{shader}.use_color_map", 1)
        cmds.connectAttr(f"{efile}.outColor", f"{shader}.TEX_emissive_map", force=True)
        cmds.setAttr(f"{shader}.use_emissive_map", 1)

        def _file_path_feeding(plug):
            src = cmds.listConnections(plug, source=True, destination=False) or []
            for node in src:
                if cmds.nodeType(node) == "file":
                    return cmds.getAttr(f"{node}.fileTextureName"), node
            return None, None

        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            standin = self._override_source(sg)
            self.assertIsNotNone(standin)
            # Base color routes through the bridge's aiMultiply into a
            # DEDICATED file node carrying the same path -- never the game
            # material's own node (Arnold and Stingray need conflicting
            # colorSpace/alphaIsLuminance on the same map).
            mult = (
                cmds.listConnections(
                    f"{standin}.baseColor", source=True, destination=False
                )
                or [None]
            )[0]
            self.assertIsNotNone(mult, "bridge baseColor must be driven")
            color_path, color_node = _file_path_feeding(f"{mult}.input1")
            self.assertEqual(color_path, "C:/tex/rack_Base_Color.png")
            self.assertNotEqual(color_node, cfile, "bridge must not share nodes")
            emis_path, emis_node = _file_path_feeding(f"{standin}.emissionColor")
            self.assertEqual(emis_path, "C:/tex/rack_Emissive.png")
            self.assertNotEqual(emis_node, efile, "bridge must not share nodes")
        # The game material's own file nodes belong to the scene, not the guard.
        self.assertTrue(cmds.objExists(cfile))
        self.assertTrue(cmds.objExists(efile))

    def test_bake_enters_the_guard_by_default(self):
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        entered = []

        @contextlib.contextmanager
        def spy():
            entered.append(True)
            yield

        tmp = tempfile.mkdtemp(prefix="guard_bake_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cube = cmds.polyCube(name="guardBakeCube")[0]
        with mock.patch.object(baker, "arnold_translation_guard", spy):
            baker.bake([cube], output_dir=tmp, backend="arnold")
        self.assertEqual(len(entered), 1)

    def test_translation_guard_false_opts_out(self):
        baker = TextureBaker(
            resolution=16, samples=1, file_format="exr", translation_guard=False
        )
        entered = []

        @contextlib.contextmanager
        def spy():
            entered.append(True)
            yield

        tmp = tempfile.mkdtemp(prefix="guard_optout_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cube = cmds.polyCube(name="guardOptOutCube")[0]
        with mock.patch.object(baker, "arnold_translation_guard", spy):
            baker.bake([cube], output_dir=tmp, backend="arnold")
        self.assertEqual(entered, [])


def _cv2_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401

        return True
    except Exception:
        return False


class TestBakeRegion(_LayoutFixtures, MayaTkTestCase):
    """``region=``: Arnold renders only part of the layout (RTT's u/v start/scale).

    Measured (mtoa 5.4.5, a UV-colour shader): the map is then exactly a map
    whose uv extent is the region, rows top-down. Added 2026-10-02 so a reused
    layout covering a third of its square renders a third of the texels.
    """

    def test_the_region_rides_the_rtt_call(self):
        tb = TextureBaker(resolution=64)
        kw = tb._rtt_kwargs("C:/out", None, region=(0.2, 0.1, 0.5, 0.7))
        self.assertAlmostEqual(kw["u_start"], 0.2)
        self.assertAlmostEqual(kw["v_start"], 0.1)
        self.assertAlmostEqual(kw["u_scale"], 0.3)
        self.assertAlmostEqual(kw["v_scale"], 0.6)
        self.assertNotIn("u_start", tb._rtt_kwargs("C:/out", None))

    def test_unresolved_whole_and_degenerate_regions_bake_the_square(self):
        tb = TextureBaker(resolution=64)
        self.assertIsNone(tb._resolve_region("|a", None))
        self.assertIsNone(tb._resolve_region("|a", {"|a": (0, 0, 1, 1)}))
        self.assertIsNone(tb._resolve_region("|a", {"|a": (0.5, 0, 0.5, 1)}))
        self.assertEqual(
            tb._resolve_region("|a", lambda _n: (0, 0, 0.5, 1)), (0.0, 0.0, 0.5, 1.0)
        )

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_regional_objects_batch_by_their_region(self):
        """Objects sharing a region share ONE call; each region is its own part.

        A region used to take its object out of the batch -- a production
        room's 42 wall instances, one layout and one region between them, paid
        42 scene translations (1.85 s each at mobile, 45% of its render time,
        2026-10-03). The region joins the uv-set flag and the bake size as what
        a call cannot vary, and each part carries its own."""
        quads = []
        for i in range(2):
            plane = self._quadrant_plane(f"regionQuad{i}")
            cmds.move(5.0 * i, 0, 0, plane)  # coplanar twins bake to zeros
            quads.append(cmds.ls(plane, long=True)[0])
        full = cmds.polyPlane(name="regionFull", sx=1, sy=1)[0]
        cmds.move(10.0, 0, 0, full)
        shape = cmds.listRelatives(full, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        full = cmds.ls(full, long=True)[0]
        import pythontk as ptk

        tmp = self.enterContext(
            ptk.TempArtifacts("bake_region_batch", policy="scoped")
        ).dir_path()
        real = cmds.arnoldRenderToTexture
        calls = []

        def rtt(*args, **kwargs):
            calls.append(kwargs.get("u_start"))
            return real(*args, **kwargs)

        with mock.patch.object(cmds, "arnoldRenderToTexture", side_effect=rtt):
            result = TextureBaker(
                resolution=32, samples=1, file_format="exr", extend_edges=False
            ).bake(
                quads + [full],
                output_dir=tmp,
                backend="arnold",
                batch=True,
                uv_set="lightmap",
                region={q: (0.5, 0.5, 1.0, 1.0) for q in quads},
                # Unlit and flat: what lands where is the only signal.
                shader=self._white_flat(),
            )
        self.assertEqual(set(result), set(quads) | {full})
        self.assertEqual(sorted(calls, key=str), [0.5, None], calls)
        for obj in quads + [full]:  # a region fills the map it renders
            cover, _u, _v = self._content_is_quadrant(result[obj])
            self.assertGreater(cover, 0.85, obj)

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_a_region_past_the_unit_square_renders_exactly(self):
        """RTT takes u/v_start below 0 and a scale past 1, and maps it linearly.

        What ``LightmapBaker._plan_regions`` relies on to give an island that
        touches its square's edge the same full pad as any other side (clamped,
        that side kept a fraction of a texel and its row baked 1-3% off).
        Measured on mtoa 5.5 (u/v_start -0.25, scale 1.5: fit -0.249 + 1.4965 x),
        so the layout's 0-1 square lands in the middle two thirds."""
        full = cmds.polyPlane(name="beyondFull", sx=1, sy=1)[0]
        shape = cmds.listRelatives(full, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        full = cmds.ls(full, long=True)[0]
        import pythontk as ptk

        tmp = self.enterContext(
            ptk.TempArtifacts("bake_region_beyond", policy="scoped")
        ).dir_path()
        result = TextureBaker(
            resolution=48, samples=1, file_format="exr", extend_edges=False
        ).bake(
            [full],
            output_dir=tmp,
            backend="arnold",
            uv_set="lightmap",
            region={full: (-0.25, -0.25, 1.25, 1.25)},
            shader=self._white_flat(),
        )
        import numpy as np

        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        img = cv2.imread(result[full], cv2.IMREAD_UNCHANGED)[..., :3].max(-1)
        # Half-max: the bake's gaussian spreads an edge over two texels; its
        # midpoint is where the square's edge lies.
        inside = img > 0.5 * img.max()
        cols = np.nonzero(inside.any(0))[0] / img.shape[1]
        rows = np.nonzero(inside.any(1))[0] / img.shape[0]
        for lo, hi in ((cols.min(), cols.max()), (rows.min(), rows.max())):
            self.assertAlmostEqual(lo, 0.25 / 1.5, delta=0.03)
            self.assertAlmostEqual(hi, 1.25 / 1.5, delta=0.03)


class TestInstancedTargetsBakePerObject(MayaTkTestCase):
    """With ``shader=``, an instanced target bakes in a call of its own."""

    def test_instanced_targets_bake_at_their_planned_size_outside_the_batch(self):
        """The routing keeps the batch's per-object sizes. The override
        verify this replaced re-rendered each suspect at the FULL resolution
        instead: measured on a production room, an instanced floor planned at
        256px came back at 1024px, one atlas mixing tiles rendered at
        different sizes (and a small tile's forced re-bake is not the same
        map as its batch render -- lights +28-53% at their planned size)."""
        a = cmds.polyCube(name="ovSizeA")[0]
        b = cmds.instance(a, name="ovSizeB")[0]
        la, lb = [cmds.ls(o, long=True)[0] for o in (a, b)]
        card = cmds.shadingNode("lambert", asShader=True, name="ovSizeCard")
        tmp = tempfile.mkdtemp(prefix="bake_inst_size_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        baker = TextureBaker(
            resolution=1024, samples=1, file_format="exr", translation_guard=False
        )
        batched, rendered = [], {}

        def batch(objects, output_dir, *args, **kwargs):
            batched.append(list(objects))
            return {}

        def render(obj, output_dir, shader=None, uv_set=None, resolution=None):
            rendered[obj] = resolution
            path = os.path.join(output_dir, f"{obj.rsplit('|', 1)[-1]}.exr")
            with open(path, "wb") as fh:
                fh.write(b"x")
            return path

        with (
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(baker, "_bake_with_arnold_batch", side_effect=batch),
            mock.patch.object(baker, "_bake_with_arnold", side_effect=render),
        ):
            result = baker.bake(
                [la, lb],
                output_dir=tmp,
                backend="arnold",
                batch=True,
                shader=card,
                size={la: 256, lb: 352},
            )

        self.assertEqual(batched, [], "an instanced target rode the batch")
        self.assertEqual(rendered, {la: 256, lb: 352})
        self.assertEqual(set(result), {la, lb})


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
@unittest.skipUnless(_cv2_available(), "cv2/numpy unavailable for map means")
class TestInstancedOverrideEndToEnd(MayaTkTestCase):
    """Instanced targets + shader override, rendered for real: every tile
    wears the card. A tile that lost ``-shader`` bakes its ASSIGNED material
    -- here pure red, so it reads as red under a white light -- which is the
    failure the batch used to ship whenever its mean-based verify missed it."""

    def test_every_instance_bakes_with_the_card(self):
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        a = cmds.polyPlane(name="ovBakeA", w=2, h=2, sx=1, sy=1)[0]
        b = cmds.instance(a, name="ovBakeB")[0]
        c = cmds.instance(a, name="ovBakeC")[0]
        cmds.move(3, 0, 0, b)
        cmds.move(6, 0, 0, c)
        # A strongly non-white assigned material, membership expressed on the
        # instances (the production shape): the tile that loses the white-card
        # override bakes THIS and reads far off its siblings.
        red = cmds.shadingNode("lambert", asShader=True, name="ovRed")
        cmds.setAttr(f"{red}.color", 1, 0, 0, type="double3")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="ovRedSG"
        )
        cmds.connectAttr(f"{red}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets([a, b, c], edit=True, forceElement=sg)
        card = cmds.shadingNode("lambert", asShader=True, name="ovCard")
        cmds.setAttr(f"{card}.color", 1, 1, 1, type="double3")
        cmds.directionalLight(intensity=1.5, rotation=(-90, 0, 0))

        longs = [cmds.ls(o, long=True)[0] for o in (a, b, c)]
        tmp = tempfile.mkdtemp(prefix="bake_override_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        baker = TextureBaker(resolution=32, samples=2, file_format="exr")
        result = baker.bake(
            longs,
            output_dir=tmp,
            prefix="",
            suffix="_LM",
            backend="arnold",
            batch=True,
            shader=card,
        )

        self.assertEqual(sorted(result), sorted(longs))
        for o in longs:
            img = cv2.imread(result[o], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
            self.assertIsNotNone(img, f"unreadable map for {o}")
            b_, g_, r_ = (float(img[..., i].mean()) for i in range(3))  # BGR
            self.assertGreater(r_, 0.0, f"black bake, nothing verified: {o}")
            # White card under a white light is neutral; the red material the
            # instances are assigned drives green to ~0.
            self.assertGreater(
                g_ / r_, 0.9, f"{o} baked its assigned red material: {(r_, g_, b_)}"
            )


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestCancelledRenderStopsTheBake(MayaTkTestCase):
    """An Arnold render stopped from its own window returns normally and
    writes nothing. The bake used to go on: a cancelled batch re-rendered
    every member one per call, and the per-object loop started the next
    object's render, each to be stopped again and each reported as "output
    missing" (measured on the production room, 2026-09-22). A call that
    writes no map now stops the bake, once, and says why.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="bake_cancel_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.calls = []

    def _planes(self, n):
        return [cmds.polyPlane(name=f"stop{i}", sx=1, sy=1)[0] for i in range(n)]

    def test_a_cancelled_batch_never_re_renders_its_members_per_object(self):
        planes = self._planes(3)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")

        def stopped(**kwargs):  # Esc on Arnold's window: returns, writes nothing
            self.calls.append(sorted(kwargs))

        with (
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(
                baker, "_pinned_render_settings", return_value=contextlib.nullcontext()
            ),
            mock.patch.object(cmds, "arnoldRenderToTexture", stopped, create=True),
        ):
            with self.assertLogs(baker.logger, level="WARNING") as caught:
                result = baker.bake(
                    planes, output_dir=self.tmp, backend="arnold", batch=True
                )
        self.assertEqual(result, {})
        self.assertEqual(
            len(self.calls), 1, "a stopped batch must not start more renders"
        )
        self.assertIn("cancelled", "\n".join(caught.output))
        self.assertNotIn("output missing", "\n".join(caught.output))

    def test_a_later_part_stopped_keeps_the_first_parts_maps_and_renders_no_more(self):
        """Two RTT parts (two bake sizes): the first writes its maps, the
        second is stopped. What rendered is kept; the second part's members
        are neither re-rendered per object nor reported missing."""
        planes = self._planes(2)
        sizes = {
            cmds.ls(planes[0], long=True)[0]: 16,
            cmds.ls(planes[1], long=True)[0]: 32,
        }
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")

        def render(**kwargs):
            self.calls.append(kwargs["resolution"])
            if kwargs["resolution"] == 16:  # the first part renders...
                for obj in cmds.ls(selection=True, long=True):
                    shape = cmds.listRelatives(obj, shapes=True)[0]
                    _write_map(os.path.join(kwargs["folder"], f"{shape}.exr"))
            # ...the second returns with nothing written (Esc).

        with (
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(
                baker, "_pinned_render_settings", return_value=contextlib.nullcontext()
            ),
            mock.patch.object(cmds, "arnoldRenderToTexture", render, create=True),
        ):
            with self.assertLogs(baker.logger, level="WARNING") as caught:
                result = baker.bake(
                    planes,
                    output_dir=self.tmp,
                    backend="arnold",
                    batch=True,
                    size=sizes,
                )
        self.assertEqual(
            sorted(self.calls), [16, 32], "each part renders once, no re-bake"
        )
        self.assertEqual(list(result), [cmds.ls(planes[0], long=True)[0]])
        self.assertNotIn("re-bake one per call", "\n".join(caught.output))

    def _rtt_writing(self, skip=lambda i, n: False, empty=lambda i, n: False):
        """A fake RTT that writes one map per selected shape, in SELECTION
        order -- measured: RTT renders a call's shapes one at a time in the
        order they were selected, each file appearing when its render
        starts. ``skip(i, n)`` leaves out the i-th of n selected members;
        ``empty(i, n)`` writes it all-zero (what an aborted render leaves)."""

        def render(**kwargs):
            selected = cmds.ls(selection=True, long=True)
            self.calls.append(kwargs["resolution"])
            for i, obj in enumerate(selected):
                if skip(i, len(selected)):
                    continue
                shape = cmds.listRelatives(obj, shapes=True)[0]
                _write_map(
                    os.path.join(kwargs["folder"], f"{shape}.exr"),
                    0.0 if empty(i, len(selected)) else 1.0,
                )

        return render

    def _bake_with(self, baker, planes, render, **kwargs):
        with (
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(
                baker, "_pinned_render_settings", return_value=contextlib.nullcontext()
            ),
            mock.patch.object(cmds, "arnoldRenderToTexture", render, create=True),
        ):
            with self.assertLogs(baker.logger, level="WARNING") as caught:
                result = baker.bake(
                    planes, output_dir=self.tmp, backend="arnold", batch=True, **kwargs
                )
        return result, "\n".join(caught.output)

    def test_a_batch_stopped_after_its_first_map_renders_no_more(self):
        """Esc after the first of three maps: the call wrote one and returned.
        That used to read as a finished part -- the two it never reached went
        round again one render (and one more Esc) per call. Nothing renders
        again, and the one map it wrote is not kept either: its file appears
        when its render STARTS, so its presence does not say it finished."""
        planes = self._planes(3)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        result, log = self._bake_with(
            baker, planes, self._rtt_writing(skip=lambda i, n: n > 1 and i > 0)
        )
        self.assertEqual(self.calls, [16], "a stopped call must not start more renders")
        self.assertEqual(result, {})
        self.assertNotIn("re-bake one per call", log)
        self.assertIn("stops here", log)

    def test_a_batch_stopped_partway_keeps_the_maps_it_finished(self):
        """Stopped during the second of three maps, in the first of two parts:
        the first map is kept, the second (cut short) is not, and neither the
        third member nor the second part renders."""
        planes = self._planes(4)
        longs = [cmds.ls(p, long=True)[0] for p in planes]
        sizes = {longs[0]: 16, longs[1]: 16, longs[2]: 16, longs[3]: 32}
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        result, log = self._bake_with(
            baker,
            planes,
            self._rtt_writing(skip=lambda i, n: n > 1 and i > 1),
            size=sizes,
        )
        self.assertEqual(self.calls, [16], "no re-bake, and the next part never starts")
        self.assertEqual(list(result), [longs[0]])
        self.assertNotIn("re-bake one per call", log)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "stop1Shape.exr")),
            "the map the stop cut short is not left behind",
        )

    def test_a_member_missing_mid_call_is_not_a_stop(self):
        """Only a stop leaves the maps a PREFIX of the call's order. A member
        missing from the middle (a map RTT wrote under a name no rule
        predicted, say) is not one: it bakes again per object as before."""
        planes = self._planes(3)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        result, log = self._bake_with(
            baker, planes, self._rtt_writing(skip=lambda i, n: n > 1 and i == 1)
        )
        self.assertEqual(self.calls, [16, 16], "the missing member re-bakes once")
        self.assertEqual(len(result), 3)
        self.assertIn("re-bake one per call", log)

    def test_a_batch_map_that_rendered_nothing_ends_the_call(self):
        """A render Arnold aborts on an error still writes each map it began,
        all-zero, and returns normally: the batch claimed them, and a
        production room would have shipped black lightmaps (2026-10-03). The
        first such map ends the call as a stop does: the maps before it are
        kept, it and every map after it go, and nothing renders again."""
        planes = self._planes(3)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        result, log = self._bake_with(
            baker, planes, self._rtt_writing(empty=lambda i, n: n > 1 and i >= 1)
        )
        self.assertEqual(
            self.calls, [16], "an aborted call must not start more renders"
        )
        self.assertEqual(list(result), [cmds.ls(planes[0], long=True)[0]])
        self.assertIn("rendered nothing", log)
        self.assertEqual(
            [f for f in os.listdir(self.tmp) if "Shape" in f],
            [],
            "no map that rendered nothing is left behind",
        )

    def test_a_per_object_map_cut_short_is_no_map(self):
        """An abort can also leave the map cut short -- an EXR header and no
        pixels, which does not read. That is no map either, and the bake
        stops there rather than starting the next object's render."""
        planes = self._planes(2)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")

        def cut_short(**kwargs):
            self.calls.append(kwargs["resolution"])
            obj = cmds.ls(selection=True, long=True)[0]
            shape = cmds.listRelatives(obj, shapes=True)[0]
            with open(os.path.join(kwargs["folder"], f"{shape}.exr"), "wb") as fh:
                fh.write(b"\x76\x2f\x31\x01" + b"\0" * 60)  # EXR magic, no image

        with (
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(
                baker, "_pinned_render_settings", return_value=contextlib.nullcontext()
            ),
            mock.patch.object(cmds, "arnoldRenderToTexture", cut_short, create=True),
        ):
            with self.assertLogs(baker.logger, level="WARNING") as caught:
                result = baker.bake(planes, output_dir=self.tmp, backend="arnold")
        self.assertEqual(result, {})
        self.assertEqual(self.calls, [16], "the next object's render must not start")
        self.assertIn("rendered nothing", "\n".join(caught.output))
        self.assertEqual(os.listdir(self.tmp), [], "the cut-short map is not kept")

    def test_a_stopped_per_object_render_ends_the_bake(self):
        planes = self._planes(3)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")

        def stopped(**kwargs):
            self.calls.append(sorted(kwargs))

        with (
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(
                baker, "_pinned_render_settings", return_value=contextlib.nullcontext()
            ),
            mock.patch.object(cmds, "arnoldRenderToTexture", stopped, create=True),
        ):
            with self.assertLogs(baker.logger, level="WARNING") as caught:
                result = baker.bake(planes, output_dir=self.tmp, backend="arnold")
        self.assertEqual(result, {})
        self.assertEqual(len(self.calls), 1, "the next object's render must not start")
        self.assertIn("2 object(s) left", "\n".join(caught.output))

    def test_a_stopped_rebake_is_not_mistaken_for_the_map_it_replaces(self):
        """A re-bake names its map after the one it replaces, so the target is
        already on disk. Success used to be judged by that name existing: a
        render stopped from Arnold's window (nothing written) was recorded as
        a fresh bake of the OLD file, and the loop went on to render the next
        object -- the Esc stopped nothing."""
        planes = self._planes(3)
        for plane in planes:
            with open(os.path.join(self.tmp, f"{plane}_LM.exr"), "wb") as fh:
                fh.write(b"old")
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")

        def stopped(**kwargs):
            self.calls.append(sorted(kwargs))

        with (
            mock.patch.object(TextureBaker, "_resolve_backend", return_value="arnold"),
            mock.patch.object(
                baker, "_pinned_render_settings", return_value=contextlib.nullcontext()
            ),
            mock.patch.object(cmds, "arnoldRenderToTexture", stopped, create=True),
        ):
            with self.assertLogs(baker.logger, level="WARNING"):
                result = baker.bake(
                    planes,
                    output_dir=self.tmp,
                    prefix="",
                    suffix="_LM",
                    backend="arnold",
                )
        self.assertEqual(result, {}, "a stopped render is not the map it would replace")
        self.assertEqual(len(self.calls), 1, "the next object's render must not start")
        with open(os.path.join(self.tmp, f"{planes[0]}_LM.exr"), "rb") as fh:
            self.assertEqual(fh.read(), b"old")


class TestOutputSpace(MayaTkTestCase):
    """Maps come out in linear Rec.709, whatever space the scene renders in.

    Arnold renders -- and RTT writes -- in the scene's rendering space, ACEScg
    by default, while everything that reads a baked map (game engines, glTF,
    the WebXR preview) reads linear Rec.709: a pure-red sRGB texture baked to
    (0.611, 0.070, 0.021), and a production room's lightmap shipped its
    lighting tint ~30% under-saturated (mtoa 5.5, 2026-10-02).
    """

    def _red_plane(self, folder):
        import numpy as np

        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        png = os.path.join(folder, "red_srgb.png")
        red = np.zeros((8, 8, 3), np.uint8)
        red[..., 2] = 255  # BGR
        cv2.imwrite(png, red)
        plane = cmds.polyPlane(name="cmPlane", width=2, height=2, sx=1, sy=1)[0]
        tex = cmds.shadingNode("file", asTexture=True, name="cmRedFile")
        cmds.setAttr(tex + ".fileTextureName", png, type="string")
        cmds.setAttr(tex + ".colorSpace", "sRGB", type="string")
        util = cmds.shadingNode("aiUtility", asShader=True, name="cmUtil")
        cmds.setAttr(util + ".shadeMode", 2)  # flat: the texture, unlit
        cmds.setAttr(util + ".colorMode", 0)
        cmds.connectAttr(tex + ".outColor", util + ".color")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="cmSG")
        cmds.connectAttr(util + ".outColor", sg + ".surfaceShader")
        cmds.sets(plane, edit=True, forceElement=sg)
        return plane

    def _baked_centre(self, **kwargs):
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2
        import pythontk as ptk

        folder = self.enterContext(
            ptk.TempArtifacts("bake_output_space", policy="scoped")
        ).dir_path()
        plane = self._red_plane(folder)
        out = os.path.join(folder, "bakes")
        result = TextureBaker(
            resolution=32, samples=1, file_format="exr", **kwargs
        ).bake([plane], output_dir=out, backend="arnold")
        img = cv2.imread(next(iter(result.values())), cv2.IMREAD_UNCHANGED)
        return img[16, 16, :3][::-1]

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_an_srgb_red_texture_bakes_rec709_red(self):
        if cmds.colorManagementPrefs(query=True, renderingSpaceName=True) != "ACEScg":
            self.skipTest("the scene does not render in ACEScg")
        r, g, b = self._baked_centre()
        self.assertAlmostEqual(r, 1.0, delta=0.01)
        self.assertLess(max(g, b), 0.01)

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_no_output_space_keeps_the_rendering_space(self):
        if cmds.colorManagementPrefs(query=True, renderingSpaceName=True) != "ACEScg":
            self.skipTest("the scene does not render in ACEScg")
        r, g, b = self._baked_centre(output_space=None)
        self.assertAlmostEqual(r, 0.61, delta=0.01)  # ACEScg's sRGB red

    def test_colour_management_off_leaves_the_map_alone(self):
        baker = TextureBaker(file_format="exr")
        with (
            mock.patch.object(TextureBaker, "_rendering_space", return_value=None),
            mock.patch.object(TextureBaker, "_write_exr") as write,
        ):
            baker._to_output_space("C:/never/read.exr")
        write.assert_not_called()

    def test_a_map_near_the_path_limit_is_written(self):
        """The write was staged as ``.<stem>.<pid>.part.exr`` beside the map,
        12 characters longer than it: a map near Windows' 260-character limit
        -- which Maya, not long-path aware, keeps to -- staged where no file
        can be, and the write failed (2026-10-03). It goes through
        ``FileUtils.atomic_write`` now, whose stage is fixed-length."""
        import numpy as np
        import pythontk as ptk

        folder = self.enterContext(
            ptk.TempArtifacts("bake_write_exr", policy="scoped")
        ).dir_path()
        path = os.path.join(folder, "m" * (252 - len(folder) - 1 - 4) + ".exr")
        TextureBaker._write_exr(path, np.ones((2, 2, 4), np.float32))
        self.assertEqual(os.listdir(folder), [os.path.basename(path)])


class TestCameraShader(MayaTkTestCase):
    """``bake(camera_shader=)``: only the bake's own rays see the card.

    The white-card lightmap used to ride ``shader=`` -- a per-shape override
    the receiver wears for EVERY ray, its own bounces included -- so a concave
    object bounced light onto itself as if it were pure white: a production
    soldering table's backsplash, above a dark mat, baked 75% brighter than
    Arnold renders it, its top 5-12% (2026-10-03). Every surface behind an
    ``aiRaySwitch`` (camera rays: the card; every other ray: the real shader)
    bakes the same point within 2% of the camera render.
    """

    def _tmp(self):
        import pythontk as ptk

        return self.enterContext(
            ptk.TempArtifacts("bake_camera_shader", policy="scoped")
        ).dir_path()

    def _card(self):
        card = cmds.shadingNode("lambert", asShader=True, name="cmsCard")
        cmds.setAttr(card + ".color", 1, 1, 1, type="double3")
        cmds.setAttr(card + ".diffuse", 1.0)
        return card

    def _lambert(self, name, rgb):
        mat = cmds.shadingNode("lambert", asShader=True, name=name)
        cmds.setAttr(mat + ".color", *rgb, type="double3")
        cmds.setAttr(mat + ".diffuse", 1.0)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=name + "SG"
        )
        cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader")
        return sg

    def _sky(self):
        cmds.shadingNode("aiSkyDomeLight", asLight=True, name="cmsSky")

    def _read(self, path):
        import numpy as np

        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        self.assertIsNotNone(img, path)
        return img[..., :3][..., ::-1].astype(np.float64)  # RGB

    def _corner(self):
        """ONE dark mesh: a 2 m floor and a 2 m wall standing on its -x edge,
        facing +x; floor uv in u 0..0.5, wall uv in u 0.5..1."""
        floor = cmds.polyPlane(name="cmsFloor", width=2, height=2, sx=1, sy=1)[0]
        cmds.polyEditUV(f"{floor}.map[*]", pivotU=0, pivotV=0, scaleU=0.5, scaleV=1.0)
        wall = cmds.polyPlane(name="cmsWall", width=2, height=2, sx=1, sy=1)[0]
        cmds.polyEditUV(f"{wall}.map[*]", pivotU=0, pivotV=0, scaleU=0.5, scaleV=1.0)
        cmds.polyEditUV(f"{wall}.map[*]", uValue=0.5, vValue=0.0, relative=True)
        cmds.xform(wall, rotation=(0, 0, -90), translation=(-1, 1, 0))
        mesh = cmds.polyUnite(floor, wall, name="cmsCorner", constructionHistory=False)[
            0
        ]
        cmds.sets(
            mesh, edit=True, forceElement=self._lambert("cmsDark", (0.05, 0.05, 0.05))
        )
        return mesh

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_the_receiver_shows_the_card_not_its_material(self):
        plane = cmds.polyPlane(name="cmsRed", width=2, height=2, sx=1, sy=1)[0]
        cmds.sets(plane, edit=True, forceElement=self._lambert("cmsRedMat", (1, 0, 0)))
        self._sky()
        result = TextureBaker(resolution=16, samples=2, file_format="exr").bake(
            [plane],
            output_dir=self._tmp(),
            backend="arnold",
            camera_shader=self._card(),
        )
        rgb = self._read(next(iter(result.values())))[4:12, 4:12].reshape(-1, 3).mean(0)
        self.assertGreater(rgb.mean(), 0.05, f"nothing baked: {rgb}")
        self.assertLess((rgb.max() - rgb.min()) / rgb.mean(), 0.05, f"tinted: {rgb}")

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_its_own_bounce_keeps_its_real_material(self):
        mesh = self._corner()
        self._sky()
        settings = {"GIDiffuseDepth": 2}

        def bake(**kwargs):
            baker = TextureBaker(
                resolution=32, samples=4, file_format="exr", render_settings=settings
            )
            out = baker.bake([mesh], output_dir=self._tmp(), backend="arnold", **kwargs)
            floor = self._read(next(iter(out.values())))[4:28, :16].mean(-1)
            return floor[:, :3].mean(), floor[:, 12:15].mean()  # by the wall, away

        card = self._card()
        near_switch, far_switch = bake(camera_shader=card)
        near_override, far_override = bake(shader=card)
        # The wall is dark: bouncing as white, it lit the floor beside it.
        self.assertLess(near_switch, 0.9 * near_override, (near_switch, near_override))
        self.assertAlmostEqual(far_switch / far_override, 1.0, delta=0.1)

    def test_a_camera_shader_and_an_override_are_one_too_many(self):
        with self.assertRaises(ValueError):
            TextureBaker().bake(["x"], shader="a", camera_shader="b")


class TestTextureConversionNeverAbortsABake(MayaTkTestCase):
    """Arnold's auto-TX converts every texture a render reads to ``.tx``
    beside its source before the first sample, and a conversion that fails
    aborts the render -- which RTT survives by writing all-zero maps. A
    production soldering assembly baked BLACK, every object in it, because
    one transferred normal map's ``.tx`` name (its source's plus ~30
    characters) passed Windows' 260-character path limit (2026-10-03). A bake
    reads its textures as they are; a render that aborts anyway writes no map.
    """

    def setUp(self):
        super().setUp()
        import pythontk as ptk

        self.tmp = self.enterContext(
            ptk.TempArtifacts("bake_autotx", policy="scoped")
        ).dir_path()
        # A conversion that fails leaves Arnold's fallback cache tree behind,
        # <temp>/TX/<the source's directory, drive dropped>.
        mirror = os.path.join(
            tempfile.gettempdir(), "TX", os.path.splitdrive(self.tmp)[1].lstrip("\\/")
        )
        self.addCleanup(self._remove_tree_and_empty_parents, mirror)
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.out)

    @staticmethod
    def _remove_tree_and_empty_parents(path):
        shutil.rmtree(path, ignore_errors=True)
        try:
            os.removedirs(os.path.dirname(path))
        except OSError:
            pass  # a parent still holds something: it stays

    def _plane_with_a_long_texture(self):
        """A lit plane whose colour is a valid image at a 250-character path:
        readable, but its ``.tx`` name is not."""
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2
        import numpy as np

        deep = self.tmp
        while len(deep) < 200:
            deep = os.path.join(deep, "d" * 30)
        if len(deep) > 235:
            self.skipTest(f"temp root too deep for the fixture: {len(self.tmp)}")
        os.makedirs(deep)
        tex = os.path.join(deep, "t" * (250 - len(deep) - 5) + ".png")
        self.assertTrue(cv2.imwrite(tex, np.full((8, 8, 3), 128, np.uint8)))
        plane = cmds.polyPlane(name="txPlane", width=2, height=2, sx=1, sy=1)[0]
        mat = cmds.shadingNode("lambert", asShader=True, name="txMat")
        node = cmds.shadingNode("file", asTexture=True, name="txFile")
        cmds.setAttr(node + ".fileTextureName", tex, type="string")
        cmds.connectAttr(node + ".outColor", mat + ".color", force=True)
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="txSG")
        cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader")
        cmds.sets(plane, edit=True, forceElement=sg)
        cmds.shadingNode("aiSkyDomeLight", asLight=True, name="txSky")
        return plane

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_a_texture_whose_tx_name_is_too_long_still_bakes(self):
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        plane = self._plane_with_a_long_texture()
        result = TextureBaker(resolution=16, samples=2, file_format="exr").bake(
            [plane], output_dir=self.out, backend="arnold"
        )
        self.assertEqual(len(result), 1, result)
        img = cv2.imread(next(iter(result.values())), cv2.IMREAD_UNCHANGED)
        self.assertGreater(float(img[..., :3].mean()), 0.05, "baked black")

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_a_render_arnold_aborts_writes_no_map(self):
        plane = self._plane_with_a_long_texture()
        # Asked for explicitly, the conversion still runs -- and aborts.
        baker = TextureBaker(
            resolution=16,
            samples=2,
            file_format="exr",
            render_settings={"autotx": True},
        )
        with self.assertLogs(baker.logger, level="WARNING") as caught:
            result = baker.bake([plane], output_dir=self.out, backend="arnold")
        self.assertEqual(result, {}, "a black map is not a bake")
        self.assertIn("rendered nothing", "\n".join(caught.output))

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def _red_map_with_green_tx(self, tx_age: float):
        """A plane wearing a red map with a green ``.tx`` under every name
        MtoA reads, stamped *tx_age* seconds from the map's time: (file node,
        map path, plane)."""
        import subprocess

        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2
        import numpy as np

        mtoa = cmds.pluginInfo("mtoa", query=True, path=True)
        maketx = os.path.join(
            os.path.dirname(os.path.dirname(mtoa)), "bin", "maketx.exe"
        )
        if not os.path.isfile(maketx):
            self.skipTest(f"no maketx beside mtoa: {maketx}")
        red = os.path.join(self.tmp, "stale_BaseColor.png").replace("\\", "/")
        green = os.path.join(self.tmp, "green.png").replace("\\", "/")
        self.assertTrue(cv2.imwrite(red, np.full((16, 16, 3), (0, 0, 255), np.uint8)))
        self.assertTrue(cv2.imwrite(green, np.full((16, 16, 3), (0, 255, 0), np.uint8)))
        plane = cmds.polyPlane(name="staleTxPlane", width=2, height=2, sx=1, sy=1)[0]
        mat = cmds.shadingNode("lambert", asShader=True, name="staleTxMat")
        node = cmds.shadingNode("file", asTexture=True, name="staleTxFile")
        cmds.setAttr(node + ".fileTextureName", red, type="string")
        cmds.connectAttr(node + ".outColor", mat + ".color", force=True)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="staleTxSG"
        )
        cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader")
        cmds.sets(plane, edit=True, forceElement=sg)
        cmds.shadingNode("aiSkyDomeLight", asLight=True, name="staleTxSky")
        # The names MtoA looks for, the colour-managed one included.
        space = cmds.getAttr(node + ".colorSpace").replace(" ", "_")
        rendering = (
            cmds.colorManagementPrefs(query=True, renderingSpaceName=True) or "ACEScg"
        ).replace(" ", "_")
        stem = red[: -len(".png")]
        stamp = os.path.getmtime(red) + tx_age
        for tx in (f"{stem}.tx", f"{red}.tx", f"{stem}_{space}_{rendering}.png.tx"):
            subprocess.run([maketx, green, "-o", tx], check=True, capture_output=True)
            os.utime(tx, (stamp, stamp))
        return node, red, plane

    def _baked_bgr(self, plane):
        import cv2

        result = TextureBaker(resolution=16, samples=2, file_format="exr").bake(
            [plane], output_dir=self.out, backend="arnold"
        )
        self.assertEqual(len(result), 1, result)
        return (
            cv2.imread(next(iter(result.values())), cv2.IMREAD_UNCHANGED)[..., :3]
            .reshape(-1, 3)
            .mean(axis=0)
        )

    def test_a_stale_tx_beside_a_texture_is_never_read(self):
        """With auto-TX off, Arnold READS a ``.tx`` it finds beside a texture,
        however old -- nothing converts it any more, so nothing sees it is out
        of date. A texture transfer re-run rewrote a production table's maps
        under the same names, with the day before's ``.tx`` of them still
        beside the new PNGs (2026-10-03). Measured: a red map with a green
        ``.tx`` under MtoA's names baked green. A ``.tx`` older than its
        texture is set aside for the bake, and the node is put back after."""
        node, red, plane = self._red_map_with_green_tx(tx_age=-3600.0)
        space = cmds.getAttr(node + ".colorSpace")
        b, g, r = self._baked_bgr(plane)
        self.assertGreater(r, 4 * g, f"baked the stale .tx: BGR mean {(b, g, r)}")
        self.assertEqual(cmds.getAttr(node + ".fileTextureName"), red)
        self.assertEqual(cmds.getAttr(node + ".colorSpace"), space)

    def test_a_current_tx_is_read_as_a_render_reads_it(self):
        """A ``.tx`` as new as its texture is what Arnold renders the scene
        through (mipmapped), so the bake reads it too: read without one, a
        production room baked 7-9% darker than the renders and the lightmaps
        baked through its ``.tx`` files (2026-10-03). The green ``.tx`` beside a
        red map is how the test sees which one was read."""
        _node, _red, plane = self._red_map_with_green_tx(tx_age=3600.0)
        b, g, r = self._baked_bgr(plane)
        self.assertGreater(
            g, 4 * r, f"did not read the current .tx: BGR mean {(b, g, r)}"
        )


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestRenderPanorama(MayaTkTestCase):
    """render_panorama: the HDR of the scene from one point a reflection probe is.

    Its layout is a contract with every reader (the GLB's probe, the WebXR
    page's equirect lookup): u = 0.5 + atan2(z, x) / 2pi, top row +Y. Arnold's
    spherical camera centres -Z instead, so a render taken as it comes would
    reflect every room a quarter turn off -- which only a scene with a wall of
    a known colour each way can see.
    """

    WALLS = {
        "pxWall": ((60, 0, 0), (0, 0, 90), (1.0, 0.0, 0.0)),  # +X red
        "nxWall": ((-60, 0, 0), (0, 0, -90), (0.0, 0.0, 1.0)),  # -X blue
        "pzWall": ((0, 0, 60), (-90, 0, 0), (0.0, 1.0, 0.0)),  # +Z green
        "nzWall": ((0, 0, -60), (90, 0, 0), (1.0, 1.0, 1.0)),  # -Z white
    }

    def _wall(self, name, position, rotation, rgb):
        plane = cmds.polyPlane(name=name, width=400, height=400, sx=1, sy=1)[0]
        cmds.xform(plane, translation=position, rotation=rotation)
        util = cmds.shadingNode("aiUtility", asShader=True, name=name + "Util")
        cmds.setAttr(util + ".shadeMode", 2)  # flat: the colour, unlit
        cmds.setAttr(util + ".color", *rgb, type="double3")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
        cmds.connectAttr(util + ".outColor", sg + ".surfaceShader")
        cmds.sets(plane, edit=True, forceElement=sg)
        return plane

    def _render(self, **kwargs):
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2
        import pythontk as ptk

        folder = self.enterContext(
            ptk.TempArtifacts("panorama_test", policy="scoped")
        ).dir_path()
        path = os.path.join(folder, "probe.exr")
        out = TextureBaker(samples=1, device="CPU").render_panorama(
            (0, 0, 0), path, width=64, **kwargs
        )
        self.assertEqual(out, path)
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        self.assertEqual(img.shape[:2], (32, 64), "2:1, the width asked for")
        return img

    @staticmethod
    def _dominant(img, u):
        """The strongest channel ('r', 'g', 'b' or 'white') at *u* on the horizon."""
        b, g, r = (float(c) for c in img[16, int(u * img.shape[1]), :3])
        if min(r, g, b) > 0.5 * max(r, g, b):
            return "white"
        return "rgb"[[r, g, b].index(max(r, g, b))]

    def test_the_panorama_is_laid_out_as_its_readers_read_it(self):
        for name, (position, rotation, rgb) in self.WALLS.items():
            self._wall(name, position, rotation, rgb)
        img = self._render()
        self.assertEqual(self._dominant(img, 0.5), "r", "+X belongs at the centre")
        self.assertEqual(self._dominant(img, 0.75), "g", "+Z a quarter right")
        self.assertEqual(self._dominant(img, 0.25), "white", "-Z a quarter left")
        self.assertEqual(self._dominant(img, 0.02), "b", "-X at the seam")
        self.assertGreater(float(img[16, 32, 3]), 0.9, "alpha: the walls were hit")

    def test_a_hidden_mesh_is_seen_through_and_the_scene_is_left_as_found(self):
        """*hide* is what moves -- a probe that carried it would show it where
        it no longer is -- and the render pins nothing it does not restore:
        the light's camera visibility, the render globals, its own camera."""
        for name, (position, rotation, rgb) in self.WALLS.items():
            self._wall(name, position, rotation, rgb)
        blocker = self._wall("blocker", (30, 0, 0), (0, 0, 90), (0.0, 0.0, 0.0))
        light = cmds.shadingNode("areaLight", asLight=True)
        light_shape = cmds.listRelatives(light, shapes=True, fullPath=True)[0]
        cmds.setAttr(light_shape + ".aiCamera", 0.0)
        prefix = cmds.getAttr("defaultRenderGlobals.imageFilePrefix")
        cameras = set(cmds.ls(type="camera"))

        seen = self._render()
        self.assertLess(float(seen[16, 32, 2]), 0.05, "the blocker hides the red wall")
        through = self._render(hide=[blocker])
        self.assertEqual(self._dominant(through, 0.5), "r", "hidden, it is seen through")

        self.assertEqual(cmds.getAttr(light_shape + ".aiCamera"), 0.0)
        # A fresh scene's prefix was never set and reads None; restored, it is
        # set empty and reads "" -- both name no prefix.
        self.assertEqual(
            cmds.getAttr("defaultRenderGlobals.imageFilePrefix") or "", prefix or ""
        )
        self.assertEqual(set(cmds.ls(type="camera")), cameras, "its camera stayed behind")
        self.assertEqual(
            cmds.getAttr(cmds.listRelatives(blocker, shapes=True, fullPath=True)[0] + ".primaryVisibility"),
            True,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
