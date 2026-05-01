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
    """set_fbx_options should accept bool/int/str types and not raise on real options."""

    def test_set_real_option_does_not_raise(self):
        FbxUtils.load_plugin()
        # FBXExportSmoothingGroups is a known FBX MEL option
        FbxUtils.set_fbx_options(
            {
                "FBXExportSmoothingGroups": True,
                "FBXExportSmoothMesh": False,
                "FBXExportInAscii": True,
            }
        )


if __name__ == "__main__":
    unittest.main()
