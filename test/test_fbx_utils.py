# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.fbx_utils module.

Covers FbxUtils — plugin loading, preset application, option setting,
and the ``export`` driver.
"""
import os
import unittest
import tempfile

import maya.cmds as cmds

from mayatk.env_utils.fbx_utils import FbxUtils

from base_test import MayaTkTestCase


class TestFbxUtilsPlugin(MayaTkTestCase):
    """load_plugin should be idempotent."""

    def test_load_plugin_idempotent(self):
        # First call may or may not have loaded already
        FbxUtils.load_plugin()
        self.assertTrue(cmds.pluginInfo("fbxmaya", query=True, loaded=True))

        # Second call should not raise
        FbxUtils.load_plugin()
        self.assertTrue(cmds.pluginInfo("fbxmaya", query=True, loaded=True))


class TestFbxUtilsLoadPreset(MayaTkTestCase):
    """load_preset validates path existence."""

    def test_missing_preset_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            FbxUtils.load_preset(r"C:/__nonexistent__/no.fbxexportpreset")


class TestFbxUtilsExport(MayaTkTestCase):
    """End-to-end export path."""

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="fbx_test_")

    def tearDown(self):
        # Best-effort cleanup
        for f in os.listdir(self.tempdir):
            try:
                os.remove(os.path.join(self.tempdir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.tempdir)
        except Exception:
            pass
        super().tearDown()

    def test_export_selection_with_no_selection_raises(self):
        cmds.select(clear=True)
        with self.assertRaises(RuntimeError):
            FbxUtils.export(
                os.path.join(self.tempdir, "noselection.fbx"),
                selection_only=True,
            )

    def test_export_appends_fbx_extension(self):
        cube = cmds.polyCube(name="fbx_export_cube")[0]
        cmds.select(cube)
        out = os.path.join(self.tempdir, "noext")
        result = FbxUtils.export(out, objects=[cube], selection_only=True)
        self.assertTrue(result.lower().endswith(".fbx"))
        self.assertTrue(os.path.isfile(result))

    def test_export_creates_intermediate_directories(self):
        cube = cmds.polyCube(name="fbx_dir_cube")[0]
        cmds.select(cube)
        nested = os.path.join(self.tempdir, "a", "b", "c", "out.fbx")
        result = FbxUtils.export(nested, objects=[cube], selection_only=True)
        self.assertTrue(os.path.isfile(result))

    def test_export_all_does_not_require_selection(self):
        cmds.polyCube(name="fbx_all_cube")
        cmds.select(clear=True)

        out = os.path.join(self.tempdir, "all.fbx")
        # Should not raise even though selection is empty
        result = FbxUtils.export(out, selection_only=False)
        self.assertTrue(os.path.isfile(result))


class TestFbxUtilsSetOptions(MayaTkTestCase):
    """set_fbx_options should accept bool/int/float/str types via the ``-v`` flag."""

    def test_set_bool_options_do_not_raise(self):
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options(
            {
                "FBXExportSmoothingGroups": True,
                "FBXExportSmoothMesh": False,
                "FBXExportInAscii": True,
            }
        )

    def test_set_string_option_does_not_raise(self):
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options({"FBXExportUpAxis": "y"})

    def test_set_quaternion_string_does_not_raise(self):
        # FBXExportQuaternion strictly requires the ``-v`` flag.
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options({"FBXExportQuaternion": "euler"})

    def test_set_int_option_does_not_raise(self):
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options({"FBXExportBakeComplexStart": 1})

    def test_set_float_option_does_not_raise(self):
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options({"FBXExportScaleFactor": 1.0})


class TestFbxUtilsExportWithOptions(MayaTkTestCase):
    """Combined preset/options/objects export path."""

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="fbx_opts_test_")

    def tearDown(self):
        for f in os.listdir(self.tempdir):
            try:
                os.remove(os.path.join(self.tempdir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.tempdir)
        except Exception:
            pass
        super().tearDown()

    def test_export_with_options_applied(self):
        """export() should accept inline options without preset."""
        cube = cmds.polyCube(name="fbx_opts_cube")[0]
        cmds.select(cube)
        out = os.path.join(self.tempdir, "with_options.fbx")
        result = FbxUtils.export(
            out,
            objects=[cube],
            options={"FBXExportSmoothingGroups": True, "FBXExportInAscii": True},
            selection_only=True,
        )
        self.assertTrue(os.path.isfile(result))

    def test_export_with_nonexistent_preset_raises(self):
        """preset_file pointing to a missing path should raise FileNotFoundError."""
        cube = cmds.polyCube(name="fbx_bad_preset_cube")[0]
        cmds.select(cube)
        out = os.path.join(self.tempdir, "with_bad_preset.fbx")
        with self.assertRaises(FileNotFoundError):
            FbxUtils.export(
                out,
                objects=[cube],
                preset_file=r"C:/__nonexistent__/bad.fbxexportpreset",
                selection_only=True,
            )


class TestFbxUtilsImport(MayaTkTestCase):
    """import_scene — round-trip and native namespace isolation."""

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="fbx_import_test_")
        # Build and export a small hierarchy, then start from an empty scene.
        root = cmds.group(em=True, name="IMP_ROOT")
        child = cmds.polyCube(name="IMP_BOX")[0]
        cmds.parent(child, root)
        self.fbx_path = os.path.join(self.tempdir, "roundtrip.fbx")
        FbxUtils.export(self.fbx_path, objects=["IMP_ROOT"], selection_only=True)
        cmds.file(new=True, force=True)

    def tearDown(self):
        for f in os.listdir(self.tempdir):
            try:
                os.remove(os.path.join(self.tempdir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.tempdir)
        except Exception:
            pass
        super().tearDown()

    def test_import_into_namespace_isolates(self):
        new_nodes = FbxUtils.import_scene(self.fbx_path, namespace="imp_ns")
        self.assertTrue(new_nodes)
        leaves = {
            t.split(":")[-1] for t in (cmds.ls("imp_ns:*", type="transform") or [])
        }
        self.assertIn("IMP_ROOT", leaves)
        self.assertIn("IMP_BOX", leaves)
        # Nothing leaked to the root namespace.
        self.assertFalse(cmds.ls("IMP_ROOT") or cmds.ls("IMP_BOX"))

    def test_import_without_namespace_at_root(self):
        FbxUtils.import_scene(self.fbx_path)
        self.assertTrue(cmds.ls("IMP_ROOT"))

    def test_import_restores_active_namespace(self):
        before = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
        FbxUtils.import_scene(self.fbx_path, namespace="imp_ns2")
        after = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
        self.assertEqual(before, after)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            FbxUtils.import_scene(r"C:/__nonexistent__/missing.fbx")


if __name__ == "__main__":
    unittest.main()
