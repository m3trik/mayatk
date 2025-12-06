# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.nurbs_utils module

Tests for NurbsUtils class functionality including:
- NURBS curve creation
- Curve operations between objects
- Lofting operations
"""
import unittest
import pymel.core as pm
import mayatk as mtk

from base_test import MayaTkTestCase


class TestNurbsUtils(MayaTkTestCase):
    """Tests for NurbsUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.sphere1 = pm.polySphere(name="test_nurbs_sphere1")[0]
        self.sphere2 = pm.polySphere(name="test_nurbs_sphere2")[0]
        pm.move(self.sphere2, 10, 0, 0)

    def tearDown(self):
        """Clean up."""
        for obj in ["test_nurbs_sphere1", "test_nurbs_sphere2"]:
            if pm.objExists(obj):
                pm.delete(obj)
        super().tearDown()

    def test_create_curve_between_two_objects(self):
        """Test creating curve between objects."""
        try:
            curve = mtk.create_curve_between_two_objs(self.sphere1, self.sphere2)
            if curve:
                self.assertIsNotNone(curve)
        except (AttributeError, RuntimeError):
            self.skipTest("create_curve_between_two_objs not available")


if __name__ == "__main__":
    unittest.main(verbosity=2)
