# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.edit_utils.naming module
"""
import unittest
import pymel.core as pm
from mayatk.edit_utils.naming._naming import Naming
from base_test import MayaTkTestCase


class TestNaming(MayaTkTestCase):
    """Tests for Naming class functionality."""

    def setUp(self):
        super().setUp()
        self.grp1 = pm.group(n="TestGroup1", em=True)
        self.grp2 = pm.group(n="TestGroup2", em=True)

    def test_rename_duplicates_in_hierarchy(self):
        """Test renaming multiple objects with same short name in different hierarchies."""
        # Create hierarchy:
        # TestGroup1|Cube
        # TestGroup2|Cube
        cube1 = pm.polyCube(n="Cube")[0]
        pm.parent(cube1, self.grp1)

        cube2 = pm.polyCube(n="Cube")[0]
        pm.parent(cube2, self.grp2)

        selection = [cube1, cube2]

        # Verify setup
        self.assertEqual(cube1.nodeName(), "Cube")
        self.assertEqual(cube2.nodeName(), "Cube")

        # Rename
        Naming.rename(selection, "RenamedCube")

        # Verify
        self.assertEqual(cube1.nodeName(), "RenamedCube")
        # Cube2 might be RenamedCube or RenamedCube1 depending on Maya uniqueness
        # But since they are in different groups, they can both be RenamedCube
        self.assertEqual(cube2.nodeName(), "RenamedCube")

    def test_rename_unique_names(self):
        """Test simple rename of unique objects."""
        cube = pm.polyCube(n="UniqueCube")[0]
        Naming.rename([cube], "NewName")
        self.assertEqual(cube.nodeName(), "NewName")

    def test_rename_pattern(self):
        """Test renaming with pattern replacement."""
        cube = pm.polyCube(n="My_Cube_GEO")[0]
        Naming.rename([cube], "Sphere", "*Cube*")
        self.assertTrue("Sphere" in cube.nodeName())

    def test_rename_suffix_retention(self):
        """Test suffix retention."""
        cube = pm.polyCube(n="MyObject_GEO")[0]
        Naming.rename([cube], "NewObject", retain_suffix=True)
        self.assertEqual(cube.nodeName(), "NewObject_GEO")

    def test_append_location_based_suffix_basic(self):
        """Test append_location_based_suffix basic functionality."""
        # Create 3 cubes at different X locations
        c1 = pm.polyCube(n="BoxA")[0]
        pm.move(0, 0, 0, c1)
        c2 = pm.polyCube(n="BoxA")[0]
        pm.move(10, 0, 0, c2)
        c3 = pm.polyCube(n="BoxA")[0]
        pm.move(5, 0, 0, c3)

        # c1(0) -> _01, c3(5) -> _02, c2(10) -> _03
        Naming.append_location_based_suffix([c1, c2, c3], strip_trailing_ints=True)

        self.assertTrue(c1.nodeName().endswith("_01"))
        self.assertTrue(c3.nodeName().endswith("_02"))
        self.assertTrue(c2.nodeName().endswith("_03"))

    def test_append_location_based_suffix_independent_groups(self):
        """Test independent groups renaming."""
        # Group 1: Box
        b1 = pm.polyCube(n="Box")[0]
        pm.move(0, 0, 0, b1)
        b2 = pm.polyCube(n="Box")[0]
        pm.move(10, 0, 0, b2)

        # Group 2: Sphere
        s1 = pm.polySphere(n="Sphere")[0]
        pm.move(0, 0, 0, s1)
        s2 = pm.polySphere(n="Sphere")[0]
        pm.move(10, 0, 0, s2)

        Naming.append_location_based_suffix(
            [b1, b2, s1, s2], independent_groups=True, strip_trailing_ints=True
        )

        # Independent numbering per group
        self.assertTrue(b1.nodeName().endswith("_01"), f"Box1 is {b1.nodeName()}")
        self.assertTrue(b2.nodeName().endswith("_02"), f"Box2 is {b2.nodeName()}")
        self.assertTrue(s1.nodeName().endswith("_01"), f"Sphere1 is {s1.nodeName()}")
        self.assertTrue(s2.nodeName().endswith("_02"), f"Sphere2 is {s2.nodeName()}")

    def test_append_location_based_suffix_stripping(self):
        """Test stripping defined suffixes."""
        # Setup: Name_GRP_01
        c1 = pm.polyCube(n="Name_GRP_01")[0]

        Naming.append_location_based_suffix(
            [c1],
            strip_trailing_ints=True,
            strip_defined_suffixes=True,
            valid_suffixes=[
                "_GRP"
            ],  # Must pass this or defaults will be used (which might include _GRP if set in slots, but here purely testing Lib logic)
        )
        # Should strip _01 -> Name_GRP -> Name -> Name_01
        self.assertTrue(c1.nodeName().endswith("Name_01"), f"Got {c1.nodeName()}")

    def test_independent_groups_formatting(self):
        """Test suffix placement behavior in independent groups mode.
        If strip_defined_suffixes=False, formatted as Name_Index_Suffix (e.g. Box_01_GRP).
        If strip_defined_suffixes=True, formatted as Name_Index (e.g. Box_01).
        """
        # Create groups with suffixes
        g1 = pm.group(n="Container_GRP", em=True)
        pm.move(0, 0, 0, g1)
        g2 = pm.group(n="Container_GRP", em=True)
        pm.move(10, 0, 0, g2)

        # Test Retain (False)
        Naming.append_location_based_suffix(
            [g1, g2],
            independent_groups=True,
            strip_defined_suffixes=False,
            valid_suffixes=["_GRP"],
        )
        # Expectation: Container_01_GRP (Index BEFORE Suffix when retained)
        self.assertEqual(g1.nodeName(), "Container_01_GRP")
        self.assertEqual(g2.nodeName(), "Container_02_GRP")

        # Reset
        pm.rename(g1, "Container_GRP")
        pm.rename(g2, "Container_GRP1")

        # Test Strip (True)
        Naming.append_location_based_suffix(
            [g1, g2],
            independent_groups=True,
            strip_defined_suffixes=True,
            valid_suffixes=["_GRP"],
        )
        self.assertEqual(g1.nodeName(), "Container_01")
        self.assertEqual(g2.nodeName(), "Container_02")


if __name__ == "__main__":
    unittest.main()
