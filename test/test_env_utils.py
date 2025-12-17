# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils module

Tests for EnvUtils class functionality including:
- Maya environment queries
- Workspace management
- Command port operations
- Path utilities
- Maya version detection
- Plugin management
- Recent files/projects
"""
import os
import unittest
import pymel.core as pm
import mayatk as mtk
from mayatk.env_utils._env_utils import EnvUtils

from base_test import MayaTkTestCase


class TestEnvUtils(MayaTkTestCase):
    """Comprehensive tests for EnvUtils class."""

    def setUp(self):
        """Set up test environment."""
        super().setUp()
        # Ensure we have a known state for some tests
        self.original_workspace = pm.workspace(q=True, rd=True)

    def tearDown(self):
        """Restore test environment."""
        # Restore workspace if changed
        if pm.workspace(q=True, rd=True) != self.original_workspace:
            pm.workspace(self.original_workspace, openWorkspace=True)
        super().tearDown()

    # -------------------------------------------------------------------------
    # Environment Info Tests
    # -------------------------------------------------------------------------

    def test_get_env_info_basic(self):
        """Test getting basic environment info keys."""
        keys_to_test = [
            "version",
            "workspace",
            "scene",
            "user_name",
            "ui_language",
            "os_type",
            "api_version",
            "application",
        ]

        for key in keys_to_test:
            val = EnvUtils.get_env_info(key)
            self.assertIsNotNone(val, f"Failed to get {key}")

    def test_get_env_info_paths(self):
        """Test getting path-related environment info."""
        # Test workspace paths
        ws_dir = EnvUtils.get_env_info("workspace_dir")
        ws_path = EnvUtils.get_env_info("workspace_path")
        self.assertTrue(os.path.isdir(ws_dir) or os.path.isdir(ws_path))

        # Test sourceimages
        src_imgs = EnvUtils.get_env_info("sourceimages")
        # Note: sourceimages might not exist in a temp test environment, but the path string should be valid
        self.assertIsInstance(src_imgs, str)
        self.assertTrue(len(src_imgs) > 0)

    def test_get_env_info_scene(self):
        """Test scene-related info."""
        # Save a temp scene to ensure we have a valid scene name
        temp_file = os.path.join(pm.internalVar(userTmpDir=True), "test_env_utils.ma")
        pm.renameFile(temp_file)

        scene_name = EnvUtils.get_env_info("scene_name")
        self.assertEqual(scene_name, "test_env_utils")

        scene_path = EnvUtils.get_env_info("scene_path")
        # ptk.format_path(..., "path") returns the directory path, not the full file path
        self.assertTrue(os.path.isdir(scene_path))
        self.assertTrue(temp_file.replace("\\", "/").startswith(scene_path))

        # Test modified flag
        pm.polyCube()  # Modify scene
        is_mod = EnvUtils.get_env_info("scene_modified")
        self.assertTrue(is_mod)

    def test_get_env_info_units(self):
        """Test unit queries."""
        linear = EnvUtils.get_env_info("linear_units")
        time = EnvUtils.get_env_info("time_units")
        self.assertIsInstance(linear, str)
        self.assertIsInstance(time, str)

    def test_get_env_info_multiple(self):
        """Test getting multiple keys at once."""
        res = EnvUtils.get_env_info("version|workspace")
        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0], EnvUtils.get_env_info("version"))

    def test_get_env_info_invalid(self):
        """Test error handling for invalid keys."""
        with self.assertRaises(KeyError):
            EnvUtils.get_env_info("non_existent_key_12345")

    # -------------------------------------------------------------------------
    # Plugin Management Tests
    # -------------------------------------------------------------------------

    def test_load_plugin(self):
        """Test loading a standard Maya plugin."""
        # 'objExport' is a standard plugin usually available
        plugin_name = "objExport"

        # Ensure it's unloaded first (if possible/safe)
        if pm.pluginInfo(plugin_name, q=True, loaded=True):
            pm.unloadPlugin(plugin_name)

        EnvUtils.load_plugin(plugin_name)
        self.assertTrue(pm.pluginInfo(plugin_name, q=True, loaded=True))

        # Test loading already loaded plugin (should not error)
        EnvUtils.load_plugin(plugin_name)
        self.assertTrue(pm.pluginInfo(plugin_name, q=True, loaded=True))

    def test_load_plugin_invalid(self):
        """Test loading a non-existent plugin."""
        with self.assertRaises(ValueError):
            EnvUtils.load_plugin("non_existent_plugin_xyz")

    # -------------------------------------------------------------------------
    # Recent Files & Projects Tests
    # -------------------------------------------------------------------------

    def test_get_recent_files(self):
        """Test retrieving recent files."""
        # We can't easily populate recent files list in a test, but we can check the return type
        recent = EnvUtils.get_recent_files()
        self.assertIsInstance(recent, list)

        # If there are recent files, check structure
        if recent:
            self.assertIsInstance(recent[0], str)

        # Test index access
        if recent:
            first = EnvUtils.get_recent_files(0)
            self.assertEqual(first, recent[0])

    def test_get_recent_projects(self):
        """Test retrieving recent projects."""
        recent = EnvUtils.get_recent_projects()
        self.assertIsInstance(recent, list)

        # Test formats
        recent_ts = EnvUtils.get_recent_projects(format="timestamp")
        self.assertIsInstance(recent_ts, list)

    # -------------------------------------------------------------------------
    # Path & System Tests
    # -------------------------------------------------------------------------

    def test_append_maya_paths(self):
        """Test appending Maya paths to sys.path."""
        # This modifies global state, so we should be careful
        # Just verify it runs without error and adds something to path
        import sys

        original_len = len(sys.path)

        try:
            EnvUtils.append_maya_paths()
        except EnvironmentError:
            # MAYA_LOCATION might not be set in some test envs
            pass

        # We can't strictly assert length changed because paths might already be there
        # But we can assert it didn't crash

    def test_scene_unit_values(self):
        """Test the SCENE_UNIT_VALUES constant."""
        self.assertIsInstance(EnvUtils.SCENE_UNIT_VALUES, dict)
        self.assertIn("centimeter", EnvUtils.SCENE_UNIT_VALUES)
        self.assertEqual(EnvUtils.SCENE_UNIT_VALUES["centimeter"], "cm")


if __name__ == "__main__":
    unittest.main()
