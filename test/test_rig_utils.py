# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.rig_utils module

Tests for RigUtils class functionality including:
- Locator creation and management
- Attribute locking
- Group creation
- Rigging utilities
"""
import unittest
import pymel.core as pm
import mayatk as mtk

from base_test import MayaTkTestCase


class TestRigUtils(MayaTkTestCase):
    """Tests for RigUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.loc = pm.spaceLocator(name="test_loc")
        self.cube = pm.polyCube(name="test_rig_cube")[0]

    def tearDown(self):
        """Clean up."""
        for obj in ["test_loc", "test_rig_cube", "_loc", "emptyGrp"]:
            if pm.objExists(obj):
                pm.delete(obj)
        super().tearDown()

    def test_create_locator(self):
        """Test creating a locator."""
        result = mtk.create_locator(name="_loc")
        self.assertNodeExists("_loc")

    def test_remove_locator(self):
        """Test removing a locator."""
        mtk.remove_locator("test_loc")
        self.assertFalse(pm.objExists("test_loc"))

    def test_set_attr_lock_state(self):
        """Test setting attribute lock state."""
        mtk.set_attr_lock_state(
            "test_rig_cube", translate=True, rotate=True, scale=True
        )
        # Verify attributes are locked
        is_locked = pm.getAttr("test_rig_cube.translateX", lock=True)
        self.assertTrue(is_locked)

    def test_create_group(self):
        """Test creating an empty group."""
        result = mtk.create_group(name="emptyGrp")
        self.assertNodeExists("emptyGrp")


if __name__ == "__main__":
    unittest.main(verbosity=2)
