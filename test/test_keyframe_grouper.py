# !/usr/bin/python
# coding=utf-8
"""Tests for KeyframeGrouper class.

This test module verifies the shared segmentation and grouping logic used by
both ScaleKeys and StaggerKeys.

Run with Maya command port on 7002:
    python test/run_tests.py keyframe_grouper
"""
import unittest
import sys
import os
import importlib

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pymel.core as pm
except ImportError:
    pm = None

# Force reload of _anim_utils to pick up new KeyframeGrouper class
# This is needed because Maya caches modules aggressively
if "mayatk.anim_utils._anim_utils" in sys.modules:
    del sys.modules["mayatk.anim_utils._anim_utils"]
if "mayatk.anim_utils" in sys.modules:
    del sys.modules["mayatk.anim_utils"]

# Import directly from module
from mayatk.anim_utils._anim_utils import KeyframeGrouper

# Conditional import of base test class
try:
    from test.base_test import MayaTkTestCase
except ImportError:
    MayaTkTestCase = unittest.TestCase


class TestKeyframeGrouperBasic(MayaTkTestCase if pm else unittest.TestCase):
    """Basic tests for KeyframeGrouper functionality."""

    def test_collect_segments_empty_list(self):
        """collect_segments with empty list returns empty."""
        result = KeyframeGrouper.collect_segments([])
        self.assertEqual(result, [])

    def test_group_segments_empty_list(self):
        """group_segments with empty list returns empty."""
        result = KeyframeGrouper.group_segments([])
        self.assertEqual(result, [])

    def test_group_segments_per_segment_mode(self):
        """per_segment mode creates one group per segment."""
        # Create mock segment data
        segments = [
            {"obj": "cube1", "curves": [], "keyframes": [1, 10], "start": 1, "end": 10, "duration": 9, "segment_range": (1, 10)},
            {"obj": "cube2", "curves": [], "keyframes": [20, 30], "start": 20, "end": 30, "duration": 10, "segment_range": (20, 30)},
        ]
        
        result = KeyframeGrouper.group_segments(segments, mode="per_segment")
        
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["start"], 1)
        self.assertEqual(result[1]["start"], 20)

    def test_group_segments_single_group_mode(self):
        """single_group mode combines all segments."""
        segments = [
            {"obj": "cube1", "curves": [], "keyframes": [1, 10], "start": 1, "end": 10, "duration": 9, "segment_range": (1, 10)},
            {"obj": "cube2", "curves": [], "keyframes": [20, 30], "start": 20, "end": 30, "duration": 10, "segment_range": (20, 30)},
        ]
        
        result = KeyframeGrouper.group_segments(segments, mode="single_group")
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], 1)
        self.assertEqual(result[0]["end"], 30)
        self.assertEqual(len(result[0]["objects"]), 2)

    def test_group_segments_overlap_groups_mode_no_overlap(self):
        """overlap_groups mode keeps non-overlapping segments separate."""
        segments = [
            {"obj": "cube1", "curves": [], "keyframes": [1, 10], "start": 1, "end": 10, "duration": 9, "segment_range": (1, 10)},
            {"obj": "cube2", "curves": [], "keyframes": [20, 30], "start": 20, "end": 30, "duration": 10, "segment_range": (20, 30)},
        ]
        
        result = KeyframeGrouper.group_segments(segments, mode="overlap_groups")
        
        self.assertEqual(len(result), 2)

    def test_group_segments_overlap_groups_mode_with_overlap(self):
        """overlap_groups mode merges overlapping segments."""
        segments = [
            {"obj": "cube1", "curves": [], "keyframes": [1, 10], "start": 1, "end": 10, "duration": 9, "segment_range": (1, 10)},
            {"obj": "cube2", "curves": [], "keyframes": [5, 15], "start": 5, "end": 15, "duration": 10, "segment_range": (5, 15)},
        ]
        
        result = KeyframeGrouper.group_segments(segments, mode="overlap_groups")
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], 1)
        self.assertEqual(result[0]["end"], 15)

    def test_group_segments_touching_keys_not_overlapping(self):
        """Touching keys (end == start) are treated as separate groups."""
        segments = [
            {"obj": "cube1", "curves": [], "keyframes": [1, 10], "start": 1, "end": 10, "duration": 9, "segment_range": (1, 10)},
            {"obj": "cube2", "curves": [], "keyframes": [10, 20], "start": 10, "end": 20, "duration": 10, "segment_range": (10, 20)},
        ]
        
        result = KeyframeGrouper.group_segments(segments, mode="overlap_groups")
        
        # Touching keys should NOT be merged
        self.assertEqual(len(result), 2)

    def test_group_segments_per_object_mode(self):
        """per_object mode groups segments from the same object."""
        segments = [
            {"obj": "cube1", "curves": [], "keyframes": [1, 10], "start": 1, "end": 10, "duration": 9, "segment_range": (1, 10)},
            {"obj": "cube1", "curves": [], "keyframes": [20, 30], "start": 20, "end": 30, "duration": 10, "segment_range": (20, 30)},
            {"obj": "cube2", "curves": [], "keyframes": [5, 15], "start": 5, "end": 15, "duration": 10, "segment_range": (5, 15)},
        ]
        
        result = KeyframeGrouper.group_segments(segments, mode="per_object")
        
        self.assertEqual(len(result), 2)  # One for cube1, one for cube2


class TestKeyframeGrouperFilters(MayaTkTestCase if pm else unittest.TestCase):
    """Tests for filtering methods."""

    def test_filter_curves_by_ignore_empty_ignore(self):
        """Empty ignore returns all curves (that exist)."""
        # With ignore=None, all curves should be returned
        # But non-existent curve names won't convert to PyNodes
        result = KeyframeGrouper._filter_curves_by_ignore([], None)
        self.assertEqual(len(result), 0)

    def test_filter_curves_by_ignore_empty_curves(self):
        """Empty curves returns empty."""
        result = KeyframeGrouper._filter_curves_by_ignore([], "visibility")
        self.assertEqual(result, [])

    def test_filter_curves_by_channel_box_empty(self):
        """Empty channel_box_attrs returns all curves."""
        curves = ["curve1", "curve2"]
        result = KeyframeGrouper._filter_curves_by_channel_box(curves, None)
        self.assertEqual(len(result), 2)


class TestKeyframeGrouperMaya(MayaTkTestCase if pm else unittest.TestCase):
    """Maya-specific tests for KeyframeGrouper (require Maya connection)."""

    def setUp(self):
        """Set up test scene."""
        if pm is None:
            self.skipTest("PyMEL not available")
        super().setUp()
        pm.newFile(force=True)

    def test_collect_segments_no_animation(self):
        """collect_segments with no animation returns empty."""
        cube = pm.polyCube(name="testCube")[0]
        result = KeyframeGrouper.collect_segments([cube])
        self.assertEqual(result, [])

    def test_collect_segments_single_object(self):
        """collect_segments with single animated object."""
        cube = pm.polyCube(name="testCube")[0]
        pm.setKeyframe(cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(cube, attribute="translateX", time=10, value=10)
        
        result = KeyframeGrouper.collect_segments([cube])
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], 1)
        self.assertEqual(result[0]["end"], 10)
        self.assertIn(cube, [result[0]["obj"]])

    def test_collect_segments_multiple_objects(self):
        """collect_segments with multiple animated objects."""
        cube1 = pm.polyCube(name="cube1")[0]
        cube2 = pm.polyCube(name="cube2")[0]
        
        pm.setKeyframe(cube1, attribute="translateX", time=1, value=0)
        pm.setKeyframe(cube1, attribute="translateX", time=10, value=10)
        
        pm.setKeyframe(cube2, attribute="translateY", time=20, value=0)
        pm.setKeyframe(cube2, attribute="translateY", time=30, value=20)
        
        result = KeyframeGrouper.collect_segments([cube1, cube2])
        
        self.assertEqual(len(result), 2)

    def test_collect_segments_with_ignore(self):
        """collect_segments respects ignore parameter."""
        cube = pm.polyCube(name="testCube")[0]
        pm.setKeyframe(cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(cube, attribute="translateX", time=10, value=10)
        pm.setKeyframe(cube, attribute="visibility", time=1, value=1)
        pm.setKeyframe(cube, attribute="visibility", time=10, value=0)
        
        # Collect without ignore - should have segments
        result_all = KeyframeGrouper.collect_segments([cube])
        self.assertGreater(len(result_all), 0)
        
        # Collect with ignore - visibility curves excluded
        result_filtered = KeyframeGrouper.collect_segments([cube], ignore="visibility")
        self.assertGreater(len(result_filtered), 0)
        
        # Verify visibility curves are excluded
        for seg in result_filtered:
            for curve in seg["curves"]:
                curve_name = str(curve).lower()
                self.assertNotIn("visibility", curve_name)

    def test_collect_segments_split_static(self):
        """collect_segments with split_static splits on static gaps."""
        cube = pm.polyCube(name="testCube")[0]
        
        # First animation segment
        pm.setKeyframe(cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(cube, attribute="translateX", time=10, value=10)
        
        # Static gap (same value)
        pm.setKeyframe(cube, attribute="translateX", time=20, value=10)
        
        # Second animation segment
        pm.setKeyframe(cube, attribute="translateX", time=30, value=20)
        
        result = KeyframeGrouper.collect_segments([cube], split_static=True)
        
        # Should have 2 segments (1-10 and 20-30)
        self.assertEqual(len(result), 2)

    def test_full_pipeline(self):
        """Test full collect -> group pipeline."""
        cube1 = pm.polyCube(name="cube1")[0]
        cube2 = pm.polyCube(name="cube2")[0]
        
        # Overlapping animation
        pm.setKeyframe(cube1, attribute="translateX", time=1, value=0)
        pm.setKeyframe(cube1, attribute="translateX", time=10, value=10)
        
        pm.setKeyframe(cube2, attribute="translateY", time=5, value=0)
        pm.setKeyframe(cube2, attribute="translateY", time=15, value=20)
        
        # Collect
        segments = KeyframeGrouper.collect_segments([cube1, cube2])
        self.assertEqual(len(segments), 2)
        
        # Group by overlap
        groups = KeyframeGrouper.group_segments(segments, mode="overlap_groups")
        self.assertEqual(len(groups), 1)  # Should merge overlapping
        self.assertEqual(groups[0]["start"], 1)
        self.assertEqual(groups[0]["end"], 15)

    def test_filter_curves_by_ignore_with_real_curves(self):
        """Test filtering with real animation curves."""
        cube = pm.polyCube(name="testCube")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")

        curves = pm.listConnections(cube, type="animCurve", s=True, d=False) or []
        result = KeyframeGrouper._filter_curves_by_ignore(curves, None)
        self.assertEqual(len(result), len(curves))

    def test_filter_curves_by_ignore_visibility(self):
        """Test filtering visibility curves."""
        cube = pm.polyCube(name="testCube")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")
        pm.setKeyframe(cube, t=0, v=1, at="visibility")
        pm.setKeyframe(cube, t=10, v=0, at="visibility")

        curves = pm.listConnections(cube, type="animCurve", s=True, d=False) or []
        result = KeyframeGrouper._filter_curves_by_ignore(curves, "visibility")
        
        # Should have 1 curve (tx), visibility filtered out
        self.assertEqual(len(result), 1)
        self.assertNotIn("visibility", str(result[0]).lower())

    def test_group_by_overlap_no_overlap(self):
        """Test grouping non-overlapping segments creates separate groups."""
        obj1 = pm.polyCube(name="obj1")[0]
        obj2 = pm.polyCube(name="obj2")[0]

        data = [
            {
                "obj": obj1,
                "keyframes": [0, 10],
                "start": 0,
                "end": 10,
                "duration": 10,
                "curves": [],
            },
            {
                "obj": obj2,
                "keyframes": [20, 30],
                "start": 20,
                "end": 30,
                "duration": 10,
                "curves": [],
            },
        ]

        result = KeyframeGrouper._group_by_overlap(data)
        self.assertEqual(len(result), 2)

    def test_group_by_overlap_with_overlap(self):
        """Test grouping overlapping segments into single group."""
        obj1 = pm.polyCube(name="obj1")[0]
        obj2 = pm.polyCube(name="obj2")[0]

        data = [
            {
                "obj": obj1,
                "keyframes": [0, 10],
                "start": 0,
                "end": 10,
                "duration": 10,
                "curves": [],
            },
            {
                "obj": obj2,
                "keyframes": [5, 15],
                "start": 5,
                "end": 15,
                "duration": 10,
                "curves": [],
            },
        ]

        result = KeyframeGrouper._group_by_overlap(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["objects"]), 2)

    def test_get_active_animation_segments_with_static_gap(self):
        """Test detecting active segments with static gaps."""
        cube = pm.polyCube(name="testCube")[0]
        
        # Animated segment 1
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")
        # Static gap
        pm.setKeyframe(cube, t=20, v=10, at="tx")
        # Animated segment 2
        pm.setKeyframe(cube, t=30, v=20, at="tx")

        curves = pm.listConnections(cube, type="animCurve", s=True, d=False) or []
        result = KeyframeGrouper._get_active_animation_segments(curves)
        
        # Should have 2 segments
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], (0, 10))
        self.assertEqual(result[1], (20, 30))


if __name__ == "__main__":
    unittest.main(verbosity=2)
