# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.display_utils module

Tests for DisplayUtils class functionality including:
- Visibility operations
- Template mode
- Isolation sets
- Visible geometry queries
"""
import unittest
import maya.cmds as cmds
import mayatk as mtk

from base_test import MayaTkTestCase


class TestDisplayUtils(MayaTkTestCase):
    """Tests for DisplayUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.cube = cmds.polyCube(name="test_display_cube")[0]
        self.sphere = cmds.polySphere(name="test_display_sphere")[0]

    def tearDown(self):
        """Clean up."""
        for obj in ["test_display_cube", "test_display_sphere"]:
            if cmds.objExists(obj):
                cmds.delete(obj)
        super().tearDown()

    def test_set_visibility_show(self):
        """Test making objects visible."""
        cmds.hide(self.cube)
        mtk.set_visibility(self.cube, visibility=True)
        self.assertTrue(cmds.getAttr(f"{self.cube}.visibility"))

    def test_set_visibility_hide(self):
        """Test hiding objects."""
        mtk.set_visibility(self.cube, visibility=False)
        self.assertFalse(cmds.getAttr(f"{self.cube}.visibility"))

    def test_is_templated(self):
        """Test checking if object is templated."""
        result = mtk.is_templated(self.cube)
        self.assertFalse(result)

        cmds.setAttr(f"{self.cube}.template", True)
        result = mtk.is_templated(self.cube)
        self.assertTrue(result)

    def test_get_visible_geometry(self):
        """Test getting visible geometry in scene."""
        result = mtk.get_visible_geometry()
        self.assertIsInstance(result, list)
        self.assertIn(self.cube, result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
