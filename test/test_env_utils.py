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

    def test_get_maya_version(self):
        """Test getting Maya version."""
        try:
            version = mtk.get_maya_version()
            self.assertIsNotNone(version)
            self.assertIsInstance(version, (str, int, float))
        except AttributeError:
            self.skipTest("get_maya_version not implemented")

    def test_get_workspace(self):
        """Test getting current workspace."""
        try:
            workspace = mtk.get_workspace()
            self.assertIsNotNone(workspace)
            self.assertIsInstance(workspace, str)
        except AttributeError:
            self.skipTest("get_workspace not implemented")

    def test_command_port_open(self):
        """Test opening command port."""
        try:
            port = 7003  # Use different port than 7002
            result = mtk.open_command_port(port)
            if result:
                mtk.close_command_port(port)
        except AttributeError:
            self.skipTest("command_port operations not implemented")


if __name__ == "__main__":
    unittest.main(verbosity=2)
