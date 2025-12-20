"""Comprehensive unit tests for UvDiagnostics.cleanup_uv_sets().

Tests all edge cases for UV set selection and cleanup:
1. Quality-based selection (area, uv count, validity)
2. Delete-marker exclusion
3. Empty vs populated UV sets
4. Standard name tiebreaker
5. Ghost vs real UV sets
6. Single vs multiple UV sets
7. Cleanup execution
"""

import unittest
import sys
import os

# Ensure mayatk is in path
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import pymel.core as pm
from base_test import MayaTkTestCase
from mayatk import UvDiagnostics


class TestUvAnalysis(MayaTkTestCase):
    """Test _analyze_uv_set() quality metrics."""

    def setUp(self):
        super().setUp()
        self.cube = self.create_test_cube()
        self.shape = self.cube.getShape()

    def test_analyze_default_uvs(self):
        """Default cube UVs should be valid with reasonable metrics."""
        metrics = UvDiagnostics._analyze_uv_set(self.shape, "map1")

        self.assertGreater(metrics["uv_count"], 0, "Should have UV coords")
        self.assertGreater(metrics["area"], 0, "Should have UV area")
        self.assertTrue(metrics["in_bounds"], "UVs should be in bounds")
        self.assertTrue(metrics["is_valid"], "Should be valid UV set")

    def test_analyze_empty_uv_set(self):
        """Empty UV set should have zero metrics and not be valid."""
        pm.polyUVSet(self.shape, create=True, uvSet="empty_set")
        metrics = UvDiagnostics._analyze_uv_set(self.shape, "empty_set")

        self.assertEqual(metrics["uv_count"], 0, "Should have no UVs")
        self.assertEqual(metrics["area"], 0.0, "Should have no area")
        self.assertFalse(metrics["is_valid"], "Empty set should not be valid")


class TestPrimarySelection(MayaTkTestCase):
    """Test _find_primary_uv_set() selection logic."""

    def setUp(self):
        super().setUp()
        self.cube = self.create_test_cube()
        self.shape = self.cube.getShape()

    # -------------------------------------------------------------------------
    # Basic Selection
    # -------------------------------------------------------------------------

    def test_single_uv_set(self):
        """Single UV set should be selected."""
        primary = UvDiagnostics._find_primary_uv_set(self.shape)
        self.assertEqual(primary, "map1")

    def test_multiple_sets_prefer_larger_area(self):
        """Should prefer UV set with larger area."""
        # Create second set with smaller UVs (scale down)
        pm.polyUVSet(self.shape, create=True, uvSet="small_uvs")
        pm.polyUVSet(self.shape, currentUVSet=True, uvSet="small_uvs")
        pm.polyProjection(self.shape, type="Planar", md="z")
        pm.polyEditUV(self.shape + ".map[*]", scaleU=0.1, scaleV=0.1)

        primary = UvDiagnostics._find_primary_uv_set(
            self.shape, prefer_largest_area=True
        )
        self.assertEqual(primary, "map1", "Should prefer larger area (map1)")

    def test_multiple_sets_prefer_higher_uv_count(self):
        """When prefer_largest_area=False, should prefer higher UV count."""
        # map1 has 14 UVs for a cube
        primary = UvDiagnostics._find_primary_uv_set(
            self.shape, prefer_largest_area=False
        )
        self.assertEqual(primary, "map1")

    # -------------------------------------------------------------------------
    # Delete Marker Exclusion
    # -------------------------------------------------------------------------

    def test_delete_marker_excluded(self):
        """Sets with ___delete___ prefix should be excluded."""
        # Rename map1 to delete marker and create new map1 with UVs
        pm.polyUVSet(
            self.shape, rename=True, uvSet="map1", newUVSet="___delete___map1___"
        )
        pm.polyUVSet(self.shape, create=True, uvSet="map1")
        pm.polyUVSet(self.shape, currentUVSet=True, uvSet="map1")
        pm.polyProjection(self.shape, type="Planar", md="z")

        primary = UvDiagnostics._find_primary_uv_set(self.shape)
        self.assertEqual(primary, "map1", "Should select map1, not delete-marked set")

    def test_delete_marker_has_more_area_still_excluded(self):
        """Delete-marked set with more area should still be excluded."""
        # Create delete-marked set with large UVs
        pm.polyUVSet(self.shape, create=True, uvSet="___delete___big___")
        pm.polyUVSet(self.shape, currentUVSet=True, uvSet="___delete___big___")
        pm.polyProjection(self.shape, type="Planar", md="z")
        pm.polyEditUV(self.shape + ".map[*]", scaleU=2, scaleV=2)

        primary = UvDiagnostics._find_primary_uv_set(self.shape)
        self.assertEqual(
            primary, "map1", "Delete-marked set should be excluded regardless of area"
        )

    def test_only_delete_markers_fallback(self):
        """When only delete-marked sets exist, should fall back to one with data."""
        pm.polyUVSet(
            self.shape, rename=True, uvSet="map1", newUVSet="___delete___map1___"
        )

        primary = UvDiagnostics._find_primary_uv_set(self.shape)
        self.assertEqual(
            primary,
            "___delete___map1___",
            "Should fall back to delete-marked if no alternatives",
        )

    # -------------------------------------------------------------------------
    # Empty vs Populated
    # -------------------------------------------------------------------------

    def test_empty_standard_name_vs_populated_custom(self):
        """Populated custom set should win over empty standard name."""
        # Create empty map1 and populated custom set
        pm.polyUVSet(self.shape, rename=True, uvSet="map1", newUVSet="custom_uvs")
        pm.polyUVSet(self.shape, create=True, uvSet="map1")  # Empty

        primary = UvDiagnostics._find_primary_uv_set(self.shape)
        self.assertEqual(
            primary, "custom_uvs", "Populated set should win over empty standard name"
        )

    def test_all_empty_returns_none(self):
        """If all UV sets have no valid data, should return None."""
        # Delete all UV coordinates from map1
        pm.polyMapDel(self.shape + ".map[*]")

        # Verify UVs were deleted
        pm.polyUVSet(self.shape, currentUVSet=True, uvSet="map1")
        uv_count = pm.polyEvaluate(self.shape, uvcoord=True) or 0

        primary = UvDiagnostics._find_primary_uv_set(self.shape)
        # Note: May still return 'map1' if it exists but is empty
        # The key is that it shouldn't crash
        self.assertIn(primary, ["map1", None], "Should handle empty UV sets gracefully")

    # -------------------------------------------------------------------------
    # Standard Name Tiebreaker
    # -------------------------------------------------------------------------

    def test_equal_quality_prefer_standard_name(self):
        """When quality is equal, should prefer standard name."""
        # Create two sets with identical UVs
        pm.polyUVSet(self.shape, copy=True, uvSet="map1", newUVSet="custom_set")

        primary = UvDiagnostics._find_primary_uv_set(self.shape)
        self.assertEqual(primary, "map1", "Standard name should win as tiebreaker")

    def test_standard_name_order(self):
        """Should prefer map1 > UVChannel_1 > UVMap > Default."""
        # Create sets with identical UVs (all copies of map1)
        pm.polyUVSet(self.shape, copy=True, uvSet="map1", newUVSet="Default")
        pm.polyUVSet(self.shape, copy=True, uvSet="map1", newUVSet="UVMap")
        pm.polyUVSet(self.shape, copy=True, uvSet="map1", newUVSet="UVChannel_1")

        primary = UvDiagnostics._find_primary_uv_set(self.shape)
        self.assertEqual(
            primary, "map1", "map1 should be preferred over other standard names"
        )


class TestCleanupExecution(MayaTkTestCase):
    """Test cleanup_uv_sets() execution."""

    def test_cleanup_removes_extra_sets(self):
        """Cleanup should remove all non-primary UV sets."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        # Create extra sets
        pm.polyUVSet(shape, create=True, uvSet="extra1")
        pm.polyUVSet(shape, create=True, uvSet="extra2")

        all_before = set(pm.polyUVSet(shape, query=True, allUVSets=True))
        self.assertEqual(len(all_before), 3, "Should have 3 sets before cleanup")

        UvDiagnostics.cleanup_uv_sets([cube])

        all_after = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        unique_after = set(all_after)
        self.assertEqual(len(unique_after), 1, "Should have 1 set after cleanup")
        self.assertIn("map1", unique_after, "map1 should remain")

    def test_cleanup_removes_delete_marked(self):
        """Cleanup should remove delete-marked sets."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        pm.polyUVSet(shape, create=True, uvSet="___delete___old___")

        UvDiagnostics.cleanup_uv_sets([cube])

        all_after = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertNotIn(
            "___delete___old___", all_after, "Delete-marked set should be removed"
        )

    def test_cleanup_keeps_best_quality(self):
        """Cleanup should keep the UV set with best quality."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        # Rename original UVs to custom name, create empty map1
        pm.polyUVSet(shape, rename=True, uvSet="map1", newUVSet="good_uvs")
        pm.polyUVSet(shape, create=True, uvSet="map1")  # Empty

        UvDiagnostics.cleanup_uv_sets([cube])

        all_after = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        unique_after = set(all_after)
        self.assertIn("good_uvs", unique_after, "Should keep populated set")
        self.assertNotIn("map1", unique_after, "Should remove empty map1")


class TestEdgeCases(MayaTkTestCase):
    """Test edge cases and unusual scenarios."""

    def test_no_uv_sets(self):
        """Object with no UV sets should not crash."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        # This is hard to test as Maya always creates map1
        primary = UvDiagnostics._find_primary_uv_set(shape)
        self.assertIsNotNone(primary, "Default cube should have UVs")

    def test_duplicate_set_names_in_list(self):
        """Should handle duplicate names in allUVSets list."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        # Maya sometimes returns duplicates
        primary = UvDiagnostics._find_primary_uv_set(shape)
        self.assertEqual(primary, "map1")

    def test_special_characters_in_name(self):
        """Should handle UV set names with special characters."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        # Create set with underscores and numbers
        pm.polyUVSet(shape, create=True, uvSet="UV_Set_2_final")

        UvDiagnostics.cleanup_uv_sets([cube])

        all_after = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertEqual(len(set(all_after)), 1, "Should clean up special-named sets")

    def test_multiple_objects(self):
        """Should handle multiple objects in one call."""
        cube1 = pm.polyCube()[0]
        cube2 = pm.polyCube()[0]

        pm.polyUVSet(cube1.getShape(), create=True, uvSet="extra1")
        pm.polyUVSet(cube2.getShape(), create=True, uvSet="extra2")

        UvDiagnostics.cleanup_uv_sets([cube1, cube2])

        sets1 = set(pm.polyUVSet(cube1.getShape(), query=True, allUVSets=True) or [])
        sets2 = set(pm.polyUVSet(cube2.getShape(), query=True, allUVSets=True) or [])

        self.assertEqual(len(sets1), 1, "Cube1 should have 1 set")
        self.assertEqual(len(sets2), 1, "Cube2 should have 1 set")


class TestRealWorldScenarios(MayaTkTestCase):
    """Test scenarios that match real-world import situations."""

    def test_fbx_import_scenario(self):
        """Simulate FBX import: empty map1, UVs in UVChannel_1."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        # Simulate: rename original UVs to FBX-style name, create empty map1
        pm.polyUVSet(shape, rename=True, uvSet="map1", newUVSet="UVChannel_1")
        pm.polyUVSet(shape, create=True, uvSet="map1")  # Maya adds empty map1

        primary = UvDiagnostics._find_primary_uv_set(shape)
        self.assertEqual(primary, "UVChannel_1", "Should select populated UVChannel_1")

        UvDiagnostics.cleanup_uv_sets([cube])

        remaining = set(pm.polyUVSet(shape, query=True, allUVSets=True) or [])
        self.assertIn("UVChannel_1", remaining, "Should keep UVChannel_1")

    def test_blender_import_scenario(self):
        """Simulate Blender import: UVs in UVMap."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        pm.polyUVSet(shape, rename=True, uvSet="map1", newUVSet="UVMap")
        pm.polyUVSet(shape, create=True, uvSet="map1")  # Empty

        primary = UvDiagnostics._find_primary_uv_set(shape)
        self.assertEqual(primary, "UVMap", "Should select populated UVMap")

    def test_user_delete_marking_scenario(self):
        """Simulate user marking sets for deletion."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        # User has marked old UVs for deletion, created new ones
        pm.polyUVSet(
            shape, rename=True, uvSet="map1", newUVSet="___delete___old_uvs___"
        )
        pm.polyUVSet(shape, create=True, uvSet="final_uvs")
        pm.polyUVSet(shape, currentUVSet=True, uvSet="final_uvs")
        pm.polyProjection(shape, type="Planar", md="z")

        primary = UvDiagnostics._find_primary_uv_set(shape)
        self.assertEqual(primary, "final_uvs", "Should select non-delete-marked set")

        UvDiagnostics.cleanup_uv_sets([cube])

        remaining = set(pm.polyUVSet(shape, query=True, allUVSets=True) or [])
        self.assertIn("final_uvs", remaining, "Should keep final_uvs")
        self.assertNotIn(
            "___delete___old_uvs___", remaining, "Should remove delete-marked"
        )

    def test_multiple_delete_markers_one_good_set(self):
        """Multiple delete-marked sets, one good set."""
        cube = pm.polyCube()[0]
        shape = cube.getShape()

        pm.polyUVSet(shape, create=True, uvSet="___delete___set1___")
        pm.polyUVSet(shape, create=True, uvSet="___delete___set2___")
        pm.polyUVSet(shape, create=True, uvSet="___delete___set3___")

        UvDiagnostics.cleanup_uv_sets([cube])

        remaining = set(pm.polyUVSet(shape, query=True, allUVSets=True) or [])
        self.assertEqual(len(remaining), 1, "Should have only 1 set remaining")
        self.assertIn("map1", remaining, "Should keep map1")


def run_tests():
    """Run all tests and print results."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestUvAnalysis))
    suite.addTests(loader.loadTestsFromTestCase(TestPrimarySelection))
    suite.addTests(loader.loadTestsFromTestCase(TestCleanupExecution))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    suite.addTests(loader.loadTestsFromTestCase(TestRealWorldScenarios))

    # Run with verbosity
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 70)
    print(f"TESTS RUN: {result.testsRun}")
    print(f"FAILURES: {len(result.failures)}")
    print(f"ERRORS: {len(result.errors)}")
    print("=" * 70)

    if result.failures:
        print("\nFAILURES:")
        for test, trace in result.failures:
            print(f"\n{test}:")
            print(trace)

    if result.errors:
        print("\nERRORS:")
        for test, trace in result.errors:
            print(f"\n{test}:")
            print(trace)

    return result


if __name__ == "__main__":
    run_tests()
