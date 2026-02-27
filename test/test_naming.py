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

    def test_rename_suffix_retention_replaces_new_suffix(self):
        """Verify retain_suffix replaces newName's suffix with each object's original suffix.

        Bug: Renaming S00B6_TAG_GRP, S00B6_TAG_LOC, S00B6_TAG_GEO with
        to='S00B8_TAG_LOC' and retain_suffix=True produced S00B8_TAG_LOC_GRP
        instead of S00B8_TAG_GRP because the old suffix was appended without
        first stripping the new name's suffix.
        Fixed: 2026-02-26
        """
        grp = pm.group(n="S00B6_TAG_GRP", em=True)
        loc = pm.spaceLocator(n="S00B6_TAG_LOC")
        geo = pm.polyCube(n="S00B6_TAG_GEO")[0]

        valid_suffixes = ["_GRP", "_LOC", "_GEO"]
        Naming.rename(
            [grp, loc, geo],
            "S00B8_TAG_LOC",
            retain_suffix=True,
            valid_suffixes=valid_suffixes,
        )

        self.assertEqual(grp.nodeName(), "S00B8_TAG_GRP")
        self.assertEqual(loc.nodeName(), "S00B8_TAG_LOC")
        self.assertEqual(geo.nodeName(), "S00B8_TAG_GEO")

    def test_rename_suffix_retention_no_valid_suffixes(self):
        """Verify retain_suffix with valid_suffixes=None treats any _XXX as a suffix."""
        grp = pm.group(n="Foo_GRP", em=True)
        geo = pm.polyCube(n="Foo_GEO")[0]

        Naming.rename(
            [grp, geo],
            "Bar_GEO",
            retain_suffix=True,
            valid_suffixes=None,
        )

        self.assertEqual(grp.nodeName(), "Bar_GRP")
        self.assertEqual(geo.nodeName(), "Bar_GEO")

    def test_rename_suffix_retention_strips_trailing_digits(self):
        """Verify trailing digits are stripped when matching suffixes.

        Objects with numbered suffixes like _GRP1, _GRP2 should match
        the base suffix _GRP and be retained without the digits.
        """
        grp1 = pm.group(n="Asset_GRP1", em=True)
        grp2 = pm.group(n="Asset_GRP2", em=True)
        loc = pm.spaceLocator(n="Asset_LOC3")

        valid_suffixes = ["_GRP", "_LOC", "_GEO"]
        Naming.rename(
            [grp1, grp2, loc],
            "NewAsset_LOC",
            retain_suffix=True,
            valid_suffixes=valid_suffixes,
        )

        # Trailing digits should be stripped: _GRP1 -> _GRP, _LOC3 -> _LOC
        self.assertEqual(grp1.nodeName(), "NewAsset_GRP")
        self.assertEqual(grp2.nodeName(), "NewAsset_GRP")
        self.assertEqual(loc.nodeName(), "NewAsset_LOC")

    def test_rename_suffix_retention_unknown_new_suffix_not_stripped(self):
        """Verify newName's suffix is NOT stripped if not in valid_suffixes.

        Prevents corrupting names where the tail segment is meaningful
        (e.g. 'HIGH' in 'Detail_HIGH').
        """
        geo = pm.polyCube(n="Part_GEO")[0]

        Naming.rename(
            [geo],
            "Detail_HIGH",
            retain_suffix=True,
            valid_suffixes=["_GRP", "_LOC", "_GEO"],
        )

        # _HIGH is not in valid_suffixes so it should NOT be stripped.
        # _GEO from oldName gets appended instead.
        self.assertEqual(geo.nodeName(), "Detail_HIGH_GEO")

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

    # ------------------------------------------------------------------
    # suffix_by_type: strip_trailing_padding
    # ------------------------------------------------------------------

    def test_suffix_by_type_padding_preserves_underscore_number(self):
        """Verify strip_trailing_padding=True keeps intentional '_02' numbering.

        Bug: When strip_trailing_ints and strip_trailing_underscores were both
        True (old UI defaults), names like 'Foo_02' were reduced to 'Foo'
        because ints were stripped first ('Foo_'), then the orphan underscore
        was stripped ('Foo').
        Fixed: 2026-02-20 — added strip_trailing_padding option.
        """
        cube = pm.polyCube(n="Cube_02")[0]
        Naming.suffix_by_type(
            [cube],
            strip_trailing_ints=False,
            strip_trailing_underscores=False,
            strip_trailing_padding=True,
        )
        # '_02' is intentional — should be preserved.
        self.assertEqual(cube.nodeName(), "Cube_02_GEO")

    def test_suffix_by_type_padding_strips_orphan_underscores(self):
        """Verify strip_trailing_padding cleans up bare trailing underscores."""
        cube = pm.polyCube(n="Cube_")[0]
        Naming.suffix_by_type(
            [cube],
            strip_trailing_ints=False,
            strip_trailing_underscores=False,
            strip_trailing_padding=True,
        )
        self.assertEqual(cube.nodeName(), "Cube_GEO")

    def test_suffix_by_type_padding_strips_orphan_underscore_digits(self):
        """Verify strip_trailing_padding cleans orphaned '_' + digits left
        after removing a wrong suffix (e.g. 'Foo_01_' -> 'Foo')."""
        cube = pm.polyCube(n="Cube_01_")[0]
        Naming.suffix_by_type(
            [cube],
            strip_trailing_ints=False,
            strip_trailing_underscores=False,
            strip_trailing_padding=True,
        )
        # Trailing '_' triggers cascade: strip '_' -> 'Cube_01'
        # then strip digits '01' -> 'Cube_' -> strip '_' -> 'Cube'
        self.assertEqual(cube.nodeName(), "Cube_GEO")

    def test_suffix_by_type_padding_no_trailing_artifact(self):
        """Verify strip_trailing_padding is a no-op when name is clean."""
        cube = pm.polyCube(n="CleanName")[0]
        Naming.suffix_by_type(
            [cube],
            strip_trailing_ints=False,
            strip_trailing_underscores=False,
            strip_trailing_padding=True,
        )
        self.assertEqual(cube.nodeName(), "CleanName_GEO")


if __name__ == "__main__":
    unittest.main()
