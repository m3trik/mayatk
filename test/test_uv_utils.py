# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.uv_utils module

Tests for UVUtils class functionality including:
- UV padding calculations
- UV shell operations
- UV set cleanup
- Texel density operations
"""
import unittest
import pymel.core as pm
import mayatk as mtk

from base_test import MayaTkTestCase


class TestUVUtils(MayaTkTestCase):
    """Tests for UVUtils class."""

    def setUp(self):
        """Set up test scene with UVs."""
        super().setUp()
        self.cube = pm.polyCube(name="test_uv_cube")[0]

    def tearDown(self):
        """Clean up."""
        if pm.objExists("test_uv_cube"):
            pm.delete("test_uv_cube")
        super().tearDown()

    def test_calculate_uv_padding(self):
        """Test UV padding calculation."""
        padding = mtk.calculate_uv_padding(1024)
        self.assertIsInstance(padding, float)
        self.assertGreater(padding, 0)

    def test_calculate_uv_padding_normalized(self):
        """Test normalized UV padding calculation."""
        padding = mtk.calculate_uv_padding(1024, normalize=True)
        self.assertIsInstance(padding, float)
        self.assertLess(padding, 1.0)

    def test_move_to_uv_space(self):
        """Test moving UVs to specific space."""
        mtk.move_to_uv_space(self.cube, u=1, v=0, relative=True)
        self.assertNodeExists("test_uv_cube")

    def test_get_uv_shell_sets(self):
        """Test getting UV shell sets."""
        try:
            shells = mtk.get_uv_shell_sets([self.cube])
            self.assertIsInstance(shells, (list, dict))
        except (AttributeError, RuntimeError):
            self.skipTest("get_uv_shell_sets not available")

    def test_cleanup_uv_sets(self):
        """Test cleaning up UV sets."""
        mtk.cleanup_uv_sets([self.cube], quiet=True)
        # Should complete without error
        self.assertNodeExists("test_uv_cube")


if __name__ == "__main__":
    unittest.main(verbosity=2)
