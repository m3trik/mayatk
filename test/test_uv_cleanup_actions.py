import unittest
import sys
import os

# Ensure mayatk is in path
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import pymel.core as pm
from base_test import MayaTkTestCase
import mayatk as mtk


class TestUvCleanupActions(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.cube = pm.polyCube()[0]
        self.shape = self.cube.getShape()

    def test_prefer_largest_area_with_scale(self):
        """
        Verify that we prefer the BEST LAYOUT, not just the largest raw area.
        Scales shouldn't matter if the layout is identical.
        The scaled set will be penalized for being out of bounds, so map1 should win.
        """
        # Create 'map1' with standard layout
        pm.polyUVSet(currentUVSet=True, uvSet="map1")
        pm.polyProjection(type="Planar", md="z")

        # Create 'large_set' scaled up 5x (Identical layout, just bigger)
        pm.polyUVSet(create=True, uvSet="large_set")
        pm.polyUVSet(currentUVSet=True, uvSet="large_set")
        pm.polyProjection(type="Planar", md="z")
        pm.polyEditUV(self.shape + ".map[*]", scaleU=5.0, scaleV=5.0)

        # Run cleanup
        mtk.Diagnostics.cleanup_uv_sets(
            [self.cube], prefer_largest_area=True, keep_only_primary=True, dry_run=False
        )

        all_sets = pm.polyUVSet(self.shape, query=True, allUVSets=True)

        # Assert map1 was kept (because large_set is out of bounds, and normalized area is equal)
        self.assertIn("map1", all_sets)
        self.assertNotIn("large_set", all_sets)

    def test_prefer_expanded_vs_collapsed(self):
        """
        Verify that we prefer an expanded layout over a collapsed one.
        Collapsed set has high UV count but low Fill Rate.
        """
        # Create 'collapsed_set'
        pm.polyUVSet(create=True, uvSet="collapsed_set")
        pm.polyUVSet(currentUVSet=True, uvSet="collapsed_set")
        # Collapse to point
        pm.polyEditUV(self.shape + ".map[*]", u=0, v=0, scaleU=0, scaleV=0)

        # Create 'good_set'
        pm.polyUVSet(create=True, uvSet="good_set")
        pm.polyUVSet(currentUVSet=True, uvSet="good_set")
        pm.polyProjection(type="Planar", md="z")

        # Delete default map1 for clarity
        pm.polyUVSet(currentUVSet=True, uvSet="map1")
        pm.polyMapDel(self.shape + ".map[*]")

        mtk.Diagnostics.cleanup_uv_sets(
            [self.cube], prefer_largest_area=True, keep_only_primary=True
        )

        all_sets = pm.polyUVSet(self.shape, query=True, allUVSets=True)
        self.assertIn("good_set", all_sets)
        self.assertNotIn("collapsed_set", all_sets)

    def test_reorder_to_index_0(self):
        """
        Verify that the preserved primary set is moved to index 0.
        """
        # Create sets
        pm.polyUVSet(create=True, uvSet="setA")
        pm.polyUVSet(create=True, uvSet="setB")

        # Make setB the desired one (most valid)
        pm.polyUVSet(currentUVSet=True, uvSet="setB")
        pm.polyProjection(type="Planar", md="z")

        # Make others empty
        pm.polyUVSet(currentUVSet=True, uvSet="map1")
        pm.polyMapDel(self.shape + ".map[*]")
        pm.polyUVSet(currentUVSet=True, uvSet="setA")
        pm.polyMapDel(self.shape + ".map[*]")

        # Cleanup
        mtk.Diagnostics.cleanup_uv_sets([self.cube], keep_only_primary=True)

        all_sets = pm.polyUVSet(self.shape, query=True, allUVSets=True)
        # setB should be the only one, or at least at index 0
        self.assertEqual(all_sets[0], "setB")

    def test_rename_to_map1(self):
        """
        Test if the primary set is renamed to 'map1'.
        NOTE: This test is expected to FAIL if internal logic skips renaming.
        """
        # Create a set named 'custom_set' and make it the only valid one
        pm.polyUVSet(create=True, uvSet="custom_set")
        pm.polyUVSet(currentUVSet=True, uvSet="custom_set")
        pm.polyProjection(type="Planar", md="z")

        # Empty map1
        pm.polyUVSet(currentUVSet=True, uvSet="map1")
        pm.polyMapDel(self.shape + ".map[*]")

        mtk.Diagnostics.cleanup_uv_sets(
            [self.cube],
            rename_to_map1=True,  # We request rename
            keep_only_primary=True,
        )

        all_sets = pm.polyUVSet(self.shape, query=True, allUVSets=True)
        # Check if it was renamed to 'map1'
        self.assertEqual(all_sets[0], "map1")

    def test_delete_secondary_sets(self):
        """
        Verify multiple populated sets are deleted if keep_only_primary=True.
        """
        pm.polyUVSet(create=True, uvSet="set2")
        pm.polyUVSet(create=True, uvSet="set3")

        # Populate all
        pm.polyUVSet(currentUVSet=True, uvSet="map1")
        pm.polyProjection(type="Planar", md="z")
        pm.polyUVSet(currentUVSet=True, uvSet="set2")
        pm.polyProjection(type="Planar", md="z")
        pm.polyUVSet(currentUVSet=True, uvSet="set3")
        pm.polyProjection(type="Planar", md="z")

        # Make 'set2' the optimal one
        # To make set2 optimal without scale/area tricks, let's just make sure others are worse
        # E.g. make map1 and set3 go out of bounds
        pm.polyUVSet(currentUVSet=True, uvSet="map1")
        pm.polyEditUV(self.shape + ".map[*]", u=100.0, v=100.0)
        pm.polyUVSet(currentUVSet=True, uvSet="set3")
        pm.polyEditUV(self.shape + ".map[*]", u=100.0, v=100.0)

        mtk.Diagnostics.cleanup_uv_sets(
            [self.cube], keep_only_primary=True, prefer_largest_area=True
        )

        all_sets = pm.polyUVSet(self.shape, query=True, allUVSets=True)
        self.assertEqual(len(all_sets), 1)
        self.assertEqual(all_sets[0], "set2")  # Should preserve the best one

    def test_remove_empty_sets(self):
        """
        Verify empty sets are removed while populated secondary sets are kept
        if keep_only_primary=False.
        """
        pm.polyUVSet(create=True, uvSet="populated_secondary")
        pm.polyUVSet(currentUVSet=True, uvSet="populated_secondary")
        pm.polyProjection(type="Planar", md="z")

        pm.polyUVSet(create=True, uvSet="empty_set")

        mtk.Diagnostics.cleanup_uv_sets(
            [self.cube], remove_empty=True, keep_only_primary=False
        )

        all_sets = pm.polyUVSet(self.shape, query=True, allUVSets=True)
        self.assertIn("map1", all_sets)  # Primary
        self.assertIn("populated_secondary", all_sets)  # Kept
        self.assertNotIn("empty_set", all_sets)  # Removed

    def test_dry_run_makes_no_changes(self):
        """
        Verify dry_run=True makes no changes to the object.
        """
        # Create a setup that WOULD trigger changes
        pm.polyUVSet(create=True, uvSet="empty_set")

        all_sets_before = pm.polyUVSet(self.shape, query=True, allUVSets=True)

        results = mtk.Diagnostics.cleanup_uv_sets(
            [self.cube], remove_empty=True, dry_run=True
        )

        all_sets_after = pm.polyUVSet(self.shape, query=True, allUVSets=True)

        self.assertEqual(all_sets_before, all_sets_after)
        self.assertTrue(len(results) > 0)
        self.assertTrue(results[0].success)  # Dry run is successful
        # Verify result reports what WOULD happen
        self.assertIn("empty_set", results[0].sets_to_delete)
