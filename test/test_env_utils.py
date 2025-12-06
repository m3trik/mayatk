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
"""
import unittest
import mayatk as mtk

from base_test import MayaTkTestCase


class TestEnvUtils(MayaTkTestCase):
    """Tests for EnvUtils class."""

    def test_get_env_info_version(self):
        """Test getting Maya version via get_env_info."""
        version = mtk.EnvUtils.get_env_info("version")
        self.assertIsNotNone(version)
        self.assertIsInstance(version, str)

    def test_get_env_info_workspace(self):
        """Test getting workspace via get_env_info."""
        workspace = mtk.EnvUtils.get_env_info("workspace")
        self.assertIsNotNone(workspace)
        self.assertIsInstance(workspace, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
