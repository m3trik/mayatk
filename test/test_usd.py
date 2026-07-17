# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.usd module.

Covers UsdUtils — plugin loading, export (usd/usda/usdz, selection and
whole-scene), and the namespace-isolated import round-trip over the native
``mayaUsd`` runtime.
"""
import os
import shutil
import tempfile
import unittest

import maya.cmds as cmds

import pythontk as ptk
from mayatk.env_utils.usd import UsdUtils

from base_test import MayaTkTestCase


class TestUsdPlugin(MayaTkTestCase):
    """load_plugin should be idempotent."""

    def test_load_plugin_idempotent(self):
        UsdUtils.load_plugin()
        self.assertTrue(cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True))
        UsdUtils.load_plugin()
        self.assertTrue(cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True))

    def test_is_usd_file_delegates(self):
        self.assertTrue(UsdUtils.is_usd_file("anything.usdz"))
        self.assertFalse(UsdUtils.is_usd_file("anything.fbx"))
        self.assertEqual(UsdUtils.EXTENSIONS, ptk.USD_EXTENSIONS)


class TestUsdExportImport(MayaTkTestCase):
    """End-to-end export + namespace-isolated import round-trip."""

    def setUp(self):
        super().setUp()
        UsdUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="usd_test_")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)
        super().tearDown()

    def _cube(self, name="usd_export_cube"):
        cube = cmds.polyCube(name=name)[0]
        cmds.select(cube)
        return cube

    def test_export_selection_with_no_selection_raises(self):
        cmds.select(clear=True)
        with self.assertRaises(RuntimeError):
            UsdUtils.export(
                os.path.join(self.tempdir, "noselection.usd"),
                selection_only=True,
            )

    def test_export_appends_usd_extension(self):
        cube = self._cube()
        result = UsdUtils.export(
            os.path.join(self.tempdir, "noext"), objects=[cube]
        )
        self.assertTrue(result.lower().endswith(".usd"))
        self.assertTrue(os.path.isfile(result))
        self.assertGreater(os.path.getsize(result), 0)

    def test_export_usda_is_text(self):
        cube = self._cube()
        result = UsdUtils.export(
            os.path.join(self.tempdir, "layer.usda"), objects=[cube]
        )
        self.assertEqual(ptk.UsdFile.sniff(result), "usda")

    def test_import_round_trip_returns_new_nodes(self):
        cube = self._cube("usd_rt_cube")
        out = UsdUtils.export(os.path.join(self.tempdir, "rt.usdc"), objects=[cube])
        cmds.delete(cube)
        new_nodes = UsdUtils.import_scene(out)
        self.assertTrue(new_nodes)
        transforms = cmds.ls(new_nodes, type="transform")
        self.assertTrue(
            any("usd_rt_cube" in t for t in transforms),
            f"expected the exported cube among {transforms}",
        )

    def test_import_into_namespace_isolates_nodes(self):
        cube = self._cube("usd_ns_cube")
        out = UsdUtils.export(os.path.join(self.tempdir, "ns.usdc"), objects=[cube])
        cmds.delete(cube)
        new_nodes = UsdUtils.import_scene(out, namespace="usd_test_ns")
        self.assertTrue(new_nodes)
        namespaced = [n for n in cmds.ls(new_nodes, type="transform")
                      if "usd_test_ns:" in n]
        self.assertTrue(
            namespaced, f"no transform under the namespace in {new_nodes}"
        )
        # Active namespace restored.
        self.assertEqual(
            cmds.namespaceInfo(currentNamespace=True, absoluteName=True), ":"
        )

    def test_import_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            UsdUtils.import_scene(os.path.join(self.tempdir, "ghost.usd"))

    def test_export_usdz_is_spec_valid_package(self):
        cube = self._cube("usd_z_cube")
        out = UsdUtils.export(
            os.path.join(self.tempdir, "pkg.usdz"), objects=[cube]
        )
        self.assertTrue(out.endswith(".usdz"))
        self.assertEqual(ptk.UsdFile.sniff(out), "usdz")
        report = ptk.UsdzPackager.verify(out)
        self.assertTrue(report["valid"], report["issues"])
        self.assertIsNotNone(ptk.UsdFile.default_layer(out))
        # And it round-trips back through the importer.
        created = UsdUtils.import_scene(out, namespace="usdz_rt")
        self.assertTrue(cmds.ls(created, type="transform"))


class TestBridgeUsdFastPath(MayaTkTestCase):
    """import_blender_scene(.usd) must import natively — no headless Blender."""

    def setUp(self):
        super().setUp()
        UsdUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="usd_fastpath_")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)
        super().tearDown()

    def test_usd_source_short_circuits_conversion(self):
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        cube = cmds.polyCube(name="usd_bridge_cube")[0]
        out = UsdUtils.export(
            os.path.join(self.tempdir, "fastpath.usdc"), objects=[cube]
        )
        cmds.delete(cube)
        # A bogus blender_path proves the point: if the bridge tried to
        # convert, require_blender would fail — USD must never reach it.
        engine = BlenderSceneImport(
            blender_path="X:/definitely/not/blender.exe", log_level="WARNING"
        )
        imported = engine.import_scene(out)
        self.assertTrue(imported)
        self.assertTrue(cmds.ls(imported, type="transform"))

    def test_missing_usd_source_raises(self):
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        engine = BlenderSceneImport(
            blender_path="X:/definitely/not/blender.exe", log_level="WARNING"
        )
        with self.assertRaises(FileNotFoundError):
            engine.import_scene(os.path.join(self.tempdir, "ghost.usdz"))

    def test_via_usd_selects_the_usd_template(self):
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        engine = BlenderSceneImport(
            blender_path="X:/definitely/not/blender.exe", log_level="WARNING"
        )
        script = engine.render_script("C:/scenes/s.blend", "C:/tmp/out.usd", via="usd")
        self.assertIn("wm.usd_export", script)
        self.assertIn("C:/scenes/s.blend", script)
        self.assertIn("C:/tmp/out.usd", script)
        fbx_script = engine.render_script("C:/scenes/s.blend", "C:/tmp/out.fbx")
        self.assertIn("export_scene.fbx", fbx_script)
        with self.assertRaises(ValueError):
            engine.render_script("a.blend", "b", via="alembic")


class TestUsdRootRegistration(MayaTkTestCase):
    def test_symbol_resolves_from_package_root(self):
        import mayatk as mtk

        self.assertIs(mtk.UsdUtils, UsdUtils)


if __name__ == "__main__":
    unittest.main(verbosity=2)
