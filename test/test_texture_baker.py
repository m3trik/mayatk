"""Tests for TextureBaker's Arnold backend (Phase 1 latent-bug fixes).

Two regressions the Phase 0b spike surfaced in Maya 2025:
  1. arnold_available() probed cmds.listCommands() -- which does NOT exist in
     Maya 2025 -- so it raised AttributeError once mtoa was loaded.
  2. arnoldRenderToTexture writes <shapeName>.<ext>, but bake() looked for the
     <transform>.<ext> name, so the file was "missing" and the object was
     dropped from the result dict (the Arnold path never actually worked).
"""
import sys
import os
import shutil
import tempfile
import unittest

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

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


class TestArnoldAvailable(MayaTkTestCase):
    def test_returns_bool_without_raising(self):
        # Regression #1: must not raise even when mtoa is loaded.
        try:
            cmds.loadPlugin("mtoa")
        except Exception:
            pass
        self.assertIsInstance(TextureBaker.arnold_available(), bool)


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
            [cube], output_dir=self.tmp, prefix="", suffix="_Lightmap",
            backend="arnold",
        )
        path = next(iter(result.values()))
        self.assertEqual(os.path.basename(path), "suffixCube_Lightmap.exr")


class TestBakeUvSetTargeting(MayaTkTestCase):
    """uv_set= makes a set current per object for the bake, then restores it.

    The set/restore mechanics need no renderer, so they run everywhere; the
    end-to-end restore-after-bake check is gated on Arnold.
    """

    @staticmethod
    def _current(shape):
        return (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]

    def _cube_with_lightmap_set(self):
        cube = cmds.polyCube(name="uvTargetCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, create=True, uvSet="lightmap")
        # map1 stays current after a create.
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


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestBakeStemEndToEnd(MayaTkTestCase):
    """End-to-end: the stem resolver names the actual file + progress reaches 100%."""

    def test_stem_names_output_and_progress_completes(self):
        cube = cmds.polyCube(name="longNodeName")[0]
        tmp = tempfile.mkdtemp(prefix="bake_stem_e2e_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        seen = []
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [cube], output_dir=tmp, prefix="", suffix="_Lightmap", backend="arnold",
            stem=lambda o: "Plants_Metal_Base_01",
            on_progress=lambda d, t, n: seen.append((d, t)) or True,
        )
        path = next(iter(result.values()))
        self.assertEqual(os.path.basename(path), "Plants_Metal_Base_01_Lightmap.exr")
        self.assertEqual(seen[0], (0, 1))   # per-object start tick
        self.assertEqual(seen[-1], (1, 1))  # final completion tick → 100%


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestArnoldAvailable))
    suite.addTests(loader.loadTestsFromTestCase(TestArnoldBakeOutputNaming))
    suite.addTests(loader.loadTestsFromTestCase(TestBakeUvSetTargeting))
    suite.addTests(loader.loadTestsFromTestCase(TestBakeProgressCallback))
    suite.addTests(loader.loadTestsFromTestCase(TestBakeNaming))
    suite.addTests(loader.loadTestsFromTestCase(TestBakeStemEndToEnd))
    return unittest.TextTestRunner(verbosity=2).run(suite)


if __name__ == "__main__":
    run_tests()
