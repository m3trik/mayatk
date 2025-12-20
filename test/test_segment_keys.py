# !/usr/bin/python
# coding=utf-8
"""Tests for SegmentKeys class.

This test module verifies the shared segmentation and grouping logic used by
both ScaleKeys and StaggerKeys.

Run with Maya command port on 7002:
    python test/run_tests.py segment_keys
"""
import unittest
import sys
import os
import importlib

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide2.QtWidgets import QApplication
except ImportError:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        QApplication = None

# Ensure QApplication exists before any Maya imports that might need it
if QApplication and not QApplication.instance():
    app = QApplication(sys.argv)

try:
    import pymel.core as pm
except ImportError:
    pm = None

# Force reload of modules to pick up changes
# This is needed because Maya caches modules aggressively
if "mayatk.anim_utils.segment_keys" in sys.modules:
    del sys.modules["mayatk.anim_utils.segment_keys"]
if "mayatk.anim_utils._anim_utils" in sys.modules:
    del sys.modules["mayatk.anim_utils._anim_utils"]
if "mayatk.anim_utils" in sys.modules:
    del sys.modules["mayatk.anim_utils"]

# Import directly from module
from mayatk.anim_utils.segment_keys import SegmentKeys

# Conditional import of base test class
try:
    from test.base_test import MayaTkTestCase
except ImportError:
    MayaTkTestCase = unittest.TestCase


class TestSegmentKeysBasic(MayaTkTestCase if pm else unittest.TestCase):
    """Basic tests for SegmentKeys functionality."""

    def test_collect_segments_empty_list(self):
        """collect_segments with empty list returns empty."""
        result = SegmentKeys.collect_segments([])
        self.assertEqual(result, [])

    def test_segment_keyframe_isolation_default_absorbs_trailing_holds(self):
        """Verify trailing holds are absorbed into segments by default.

        New default behavior (ignore_holds=False): when split_static=True, trailing
        hold keys up to the next segment start are included in the earlier segment.
        This prevents overlap/collapses during downstream shifting/staggering.
        """
        cube = pm.polyCube(name="TestCube")[0]

        # Create two distinct segments: 0-10 and 20-30
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")

        # Static gap 10-20 (flat)
        pm.setKeyframe(cube, t=20, v=10, at="tx")
        pm.setKeyframe(cube, t=30, v=20, at="tx")

        # Collect segments (default behavior absorbs trailing holds)
        # We set exclude_next_start=False to ensure the boundary key at 20 is included
        segments = SegmentKeys.collect_segments(
            [cube], split_static=True, exclude_next_start=False
        )

        self.assertEqual(len(segments), 2, "Should find 2 segments")

        # Segment 1: 0-20 (absorbs the trailing hold key at 20)
        seg1 = segments[0]
        self.assertEqual(seg1["start"], 0)
        self.assertEqual(seg1["end"], 20)
        # Includes the trailing hold boundary key (20)
        self.assertEqual(
            seg1["keyframes"],
            [0.0, 10.0, 20.0],
            f"Segment 1 should have keys [0, 10, 20], got {seg1['keyframes']}",
        )

        # Segment 2: 20-30
        seg2 = segments[1]
        self.assertEqual(seg2["start"], 20)
        self.assertEqual(seg2["end"], 30)
        # Boundary key 20 is also included here (segment start)
        self.assertEqual(
            seg2["keyframes"],
            [20.0, 30.0],
            f"Segment 2 should only have keys [20, 30], got {seg2['keyframes']}",
        )

    def test_segment_keyframe_isolation_ignore_holds_active_only(self):
        """Verify ignore_holds=True keeps active-only segments (no trailing holds)."""
        cube = pm.polyCube(name="TestCubeIgnoreHolds")[0]

        # Create two distinct segments: 0-10 and 20-30
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")

        # Static gap 10-20 (flat)
        pm.setKeyframe(cube, t=20, v=10, at="tx")
        pm.setKeyframe(cube, t=30, v=20, at="tx")

        segments = SegmentKeys.collect_segments(
            [cube], split_static=True, ignore_holds=True
        )
        self.assertEqual(len(segments), 2, "Should find 2 segments")

        seg1 = segments[0]
        self.assertEqual(seg1["start"], 0)
        self.assertEqual(seg1["end"], 10)
        self.assertEqual(seg1["keyframes"], [0.0, 10.0])

        seg2 = segments[1]
        self.assertEqual(seg2["start"], 20)
        self.assertEqual(seg2["end"], 30)
        self.assertEqual(seg2["keyframes"], [20.0, 30.0])

    def test_print_scene_info(self):
        """Test print_scene_info runs without error."""
        cube = pm.polyCube(name="print_test")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")

        # Should run without error
        SegmentKeys.print_scene_info([cube])

    def test_group_segments_empty_list(self):
        """group_segments with empty list returns empty."""
        result = SegmentKeys.group_segments([])
        self.assertEqual(result, [])

    def test_group_segments_per_segment_mode(self):
        """per_segment mode creates one group per segment."""
        # Create mock segment data
        segments = [
            {
                "obj": "cube1",
                "curves": [],
                "keyframes": [1, 10],
                "start": 1,
                "end": 10,
                "duration": 9,
                "segment_range": (1, 10),
            },
            {
                "obj": "cube2",
                "curves": [],
                "keyframes": [20, 30],
                "start": 20,
                "end": 30,
                "duration": 10,
                "segment_range": (20, 30),
            },
        ]

        result = SegmentKeys.group_segments(segments, mode="per_segment")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["start"], 1)
        self.assertEqual(result[1]["start"], 20)

    def test_group_segments_single_group_mode(self):
        """single_group mode combines all segments."""
        segments = [
            {
                "obj": "cube1",
                "curves": [],
                "keyframes": [1, 10],
                "start": 1,
                "end": 10,
                "duration": 9,
                "segment_range": (1, 10),
            },
            {
                "obj": "cube2",
                "curves": [],
                "keyframes": [20, 30],
                "start": 20,
                "end": 30,
                "duration": 10,
                "segment_range": (20, 30),
            },
        ]

        result = SegmentKeys.group_segments(segments, mode="single_group")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], 1)
        self.assertEqual(result[0]["end"], 30)
        self.assertEqual(len(result[0]["objects"]), 2)

    def test_group_segments_overlap_groups_mode_no_overlap(self):
        """overlap_groups mode keeps non-overlapping segments separate."""
        segments = [
            {
                "obj": "cube1",
                "curves": [],
                "keyframes": [1, 10],
                "start": 1,
                "end": 10,
                "duration": 9,
                "segment_range": (1, 10),
            },
            {
                "obj": "cube2",
                "curves": [],
                "keyframes": [20, 30],
                "start": 20,
                "end": 30,
                "duration": 10,
                "segment_range": (20, 30),
            },
        ]

        result = SegmentKeys.group_segments(segments, mode="overlap_groups")

        self.assertEqual(len(result), 2)

    def test_group_segments_overlap_groups_mode_with_overlap(self):
        """overlap_groups mode merges overlapping segments."""
        segments = [
            {
                "obj": "cube1",
                "curves": [],
                "keyframes": [1, 10],
                "start": 1,
                "end": 10,
                "duration": 9,
                "segment_range": (1, 10),
            },
            {
                "obj": "cube2",
                "curves": [],
                "keyframes": [5, 15],
                "start": 5,
                "end": 15,
                "duration": 10,
                "segment_range": (5, 15),
            },
        ]

        result = SegmentKeys.group_segments(segments, mode="overlap_groups")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], 1)
        self.assertEqual(result[0]["end"], 15)

    def test_group_segments_touching_keys_not_overlapping(self):
        """Touching keys (end == start) are treated as separate groups."""
        segments = [
            {
                "obj": "cube1",
                "curves": [],
                "keyframes": [1, 10],
                "start": 1,
                "end": 10,
                "duration": 9,
                "segment_range": (1, 10),
            },
            {
                "obj": "cube2",
                "curves": [],
                "keyframes": [10, 20],
                "start": 10,
                "end": 20,
                "duration": 10,
                "segment_range": (10, 20),
            },
        ]

        result = SegmentKeys.group_segments(segments, mode="overlap_groups")

        # Touching keys should NOT be merged
        self.assertEqual(len(result), 2)

    def test_group_segments_per_object_mode(self):
        """per_object mode groups segments from the same object."""
        segments = [
            {
                "obj": "cube1",
                "curves": [],
                "keyframes": [1, 10],
                "start": 1,
                "end": 10,
                "duration": 9,
                "segment_range": (1, 10),
            },
            {
                "obj": "cube1",
                "curves": [],
                "keyframes": [20, 30],
                "start": 20,
                "end": 30,
                "duration": 10,
                "segment_range": (20, 30),
            },
            {
                "obj": "cube2",
                "curves": [],
                "keyframes": [5, 15],
                "start": 5,
                "end": 15,
                "duration": 10,
                "segment_range": (5, 15),
            },
        ]

        result = SegmentKeys.group_segments(segments, mode="per_object")

        self.assertEqual(len(result), 2)  # One for cube1, one for cube2


class TestSegmentKeysFilters(MayaTkTestCase if pm else unittest.TestCase):
    """Tests for filtering methods."""

    def test_filter_curves_by_ignore_empty_ignore(self):
        """Empty ignore returns all curves (that exist)."""
        # With ignore=None, all curves should be returned
        # But non-existent curve names won't convert to PyNodes
        result = SegmentKeys._filter_curves_by_ignore([], None)
        self.assertEqual(len(result), 0)

    def test_filter_curves_by_ignore_empty_curves(self):
        """Empty curves returns empty."""
        result = SegmentKeys._filter_curves_by_ignore([], "visibility")
        self.assertEqual(result, [])

    def test_filter_curves_by_channel_box_empty(self):
        """Empty channel_box_attrs returns all curves."""
        curves = ["curve1", "curve2"]
        result = SegmentKeys._filter_curves_by_channel_box(curves, None)
        self.assertEqual(len(result), 2)


class TestSegmentKeysMaya(MayaTkTestCase if pm else unittest.TestCase):
    """Maya-specific tests for SegmentKeys (require Maya connection)."""

    def setUp(self):
        """Set up test scene."""
        if pm is None:
            self.skipTest("PyMEL not available")
        super().setUp()
        pm.newFile(force=True)

    def test_collect_segments_no_animation(self):
        """collect_segments with no animation returns empty."""
        cube = pm.polyCube(name="testCube")[0]
        result = SegmentKeys.collect_segments([cube])
        self.assertEqual(result, [])

    def test_collect_segments_single_object(self):
        """collect_segments with single animated object."""
        cube = pm.polyCube(name="testCube")[0]
        pm.setKeyframe(cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(cube, attribute="translateX", time=10, value=10)

        result = SegmentKeys.collect_segments([cube])

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

        result = SegmentKeys.collect_segments([cube1, cube2])

        self.assertEqual(len(result), 2)

    def test_collect_segments_with_ignore(self):
        """collect_segments respects ignore parameter."""
        cube = pm.polyCube(name="testCube")[0]
        pm.setKeyframe(cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(cube, attribute="translateX", time=10, value=10)
        pm.setKeyframe(cube, attribute="visibility", time=1, value=1)
        pm.setKeyframe(cube, attribute="visibility", time=10, value=0)

        # Collect without ignore - should have segments
        result_all = SegmentKeys.collect_segments([cube])
        self.assertGreater(len(result_all), 0)

        # Collect with ignore - visibility curves excluded
        result_filtered = SegmentKeys.collect_segments([cube], ignore="visibility")
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

        # We set exclude_next_start=False to include the boundary key
        result = SegmentKeys.collect_segments(
            [cube], split_static=True, exclude_next_start=False
        )

        # Default behavior absorbs trailing holds, so segments become (1-20) and (20-30)
        self.assertEqual(len(result), 2)

        self.assertEqual(result[0]["start"], 1)
        self.assertEqual(result[0]["end"], 20)
        self.assertEqual(result[1]["start"], 20)
        self.assertEqual(result[1]["end"], 30)

    def test_collect_segments_split_static_ignore_holds_trailing_hold_after_last_segment(
        self,
    ):
        """When the final segment has a trailing hold, ignore_holds controls inclusion.

        Setup:
        - Active change from 0->10
        - Trailing hold key at 20 (same value as at 10)

        Expectation:
        - Default (ignore_holds=False): segment expands to end at 20 and includes key 20
        - ignore_holds=True: segment stays active-only (ends at 10) and excludes key 20
        """
        cube = pm.polyCube(name="hold_last_seg")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")
        pm.setKeyframe(cube, t=20, v=10, at="tx")

        segs_default = SegmentKeys.collect_segments([cube], split_static=True)
        self.assertEqual(len(segs_default), 1)
        self.assertEqual(segs_default[0]["start"], 0)
        self.assertEqual(segs_default[0]["end"], 20)
        self.assertEqual(segs_default[0]["keyframes"], [0.0, 10.0, 20.0])

        segs_ignore = SegmentKeys.collect_segments(
            [cube], split_static=True, ignore_holds=True
        )
        self.assertEqual(len(segs_ignore), 1)
        self.assertEqual(segs_ignore[0]["start"], 0)
        self.assertEqual(segs_ignore[0]["end"], 10)
        self.assertEqual(segs_ignore[0]["keyframes"], [0.0, 10.0])

    def test_collect_segments_visibility_holds_can_bridge_static_gaps(self):
        """Visibility curves can merge segments unless ignore_visibility_holds=True.

        If a visibility curve spans the full range, and ignore_visibility_holds=False
        (default), it is treated as always active and can bridge static gaps in other
        channels, producing a single merged segment.
        """
        cube = pm.polyCube(name="vis_bridge")[0]

        # Two translateX active segments with a static gap
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")
        pm.setKeyframe(cube, t=20, v=10, at="tx")
        pm.setKeyframe(cube, t=30, v=20, at="tx")

        # Visibility holds across the entire range (no change)
        pm.setKeyframe(cube, t=0, v=1, at="visibility")
        pm.setKeyframe(cube, t=30, v=1, at="visibility")

        segs_bridge = SegmentKeys.collect_segments(
            [cube],
            split_static=True,
            ignore_visibility_holds=False,
            exclude_next_start=False,
        )
        self.assertEqual(len(segs_bridge), 1)
        self.assertEqual(segs_bridge[0]["start"], 0)
        self.assertEqual(segs_bridge[0]["end"], 30)

        segs_no_bridge = SegmentKeys.collect_segments(
            [cube],
            split_static=True,
            ignore_visibility_holds=True,
            exclude_next_start=False,
        )
        # Without visibility bridging, we expect 2 segments (with trailing-hold absorption)
        self.assertEqual(len(segs_no_bridge), 2)
        self.assertEqual(segs_no_bridge[0]["start"], 0)
        self.assertEqual(segs_no_bridge[0]["end"], 20)
        self.assertEqual(segs_no_bridge[1]["start"], 20)
        self.assertEqual(segs_no_bridge[1]["end"], 30)

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
        segments = SegmentKeys.collect_segments([cube1, cube2])
        self.assertEqual(len(segments), 2)

        # Group by overlap
        groups = SegmentKeys.group_segments(segments, mode="overlap_groups")
        self.assertEqual(len(groups), 1)  # Should merge overlapping
        self.assertEqual(groups[0]["start"], 1)
        self.assertEqual(groups[0]["end"], 15)

    def test_filter_curves_by_ignore_with_real_curves(self):
        """Test filtering with real animation curves."""
        cube = pm.polyCube(name="testCube")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")

        curves = pm.listConnections(cube, type="animCurve", s=True, d=False) or []
        result = SegmentKeys._filter_curves_by_ignore(curves, None)
        self.assertEqual(len(result), len(curves))

    def test_filter_curves_by_ignore_visibility(self):
        """Test filtering visibility curves."""
        cube = pm.polyCube(name="testCube")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")
        pm.setKeyframe(cube, t=0, v=1, at="visibility")
        pm.setKeyframe(cube, t=10, v=0, at="visibility")

        curves = pm.listConnections(cube, type="animCurve", s=True, d=False) or []
        result = SegmentKeys._filter_curves_by_ignore(curves, "visibility")

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

        result = SegmentKeys._group_by_overlap(data)
        self.assertEqual(len(result), 2)

    def test_group_by_overlap_touching_inclusive(self):
        """Test grouping touching segments with inclusive=True."""
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
                "keyframes": [10, 20],
                "start": 10,
                "end": 20,
                "duration": 10,
                "curves": [],
            },
        ]

        # Default (inclusive=False) -> 2 groups
        result_exclusive = SegmentKeys._group_by_overlap(data, inclusive=False)
        self.assertEqual(len(result_exclusive), 2)

        # Inclusive=True -> 1 group
        result_inclusive = SegmentKeys._group_by_overlap(data, inclusive=True)
        self.assertEqual(len(result_inclusive), 1)
        self.assertEqual(result_inclusive[0]["start"], 0)
        self.assertEqual(result_inclusive[0]["end"], 20)

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

        result = SegmentKeys._group_by_overlap(data)
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
        result = SegmentKeys._get_active_animation_segments(curves)

        # Should have 2 segments
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], (0, 10))
        self.assertEqual(result[1], (20, 30))


class TestSegmentKeysEdgeCases(MayaTkTestCase if pm else unittest.TestCase):
    """Edge case tests for SegmentKeys."""

    def setUp(self):
        if pm is None:
            self.skipTest("Maya not available")
        super().setUp()
        pm.newFile(force=True)

    def test_collect_segments_time_range(self):
        """collect_segments respects time_range parameter."""
        cube = pm.polyCube(name="time_range_cube")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")
        pm.setKeyframe(cube, t=20, v=20, at="tx")
        pm.setKeyframe(cube, t=30, v=30, at="tx")

        # Range excluding 0 and 30
        segments = SegmentKeys.collect_segments([cube], time_range=(5, 25))

        self.assertEqual(len(segments), 1)
        # Should only include keys 10 and 20
        self.assertEqual(segments[0]["keyframes"], [10.0, 20.0])
        self.assertEqual(segments[0]["start"], 10.0)
        self.assertEqual(segments[0]["end"], 20.0)

    def test_collect_segments_selected_keys(self):
        """collect_segments respects selected_keys_only parameter."""
        cube = pm.polyCube(name="sel_keys_cube")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")
        pm.setKeyframe(cube, t=20, v=20, at="tx")

        # Select only the key at frame 10
        pm.selectKey(cube, t=(10, 10))

        segments = SegmentKeys.collect_segments([cube], selected_keys_only=True)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["keyframes"], [10.0])
        self.assertEqual(segments[0]["start"], 10.0)
        self.assertEqual(segments[0]["end"], 10.0)

    def test_collect_segments_channel_box(self):
        """collect_segments respects channel_box_attrs parameter."""
        cube = pm.polyCube(name="cb_cube")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=0, v=0, at="ty")

        # Filter for tx only
        segments = SegmentKeys.collect_segments(
            [cube], channel_box_attrs=["translateX"]
        )

        self.assertEqual(len(segments), 1)
        curves = segments[0]["curves"]
        self.assertEqual(len(curves), 1)
        self.assertTrue(curves[0].name().endswith("_translateX"))

    def test_collect_segments_exclude_next_start(self):
        """collect_segments respects exclude_next_start parameter."""
        cube = pm.polyCube(name="exclude_next_cube")[0]
        # Segment 1: 0-10
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")
        # Static gap 10-20
        pm.setKeyframe(cube, t=20, v=10, at="tx")
        # Segment 2: 20-30
        pm.setKeyframe(cube, t=30, v=20, at="tx")

        # Default: exclude_next_start=True
        # Segment 1 should end at 20 (trailing hold) but exclude next start if it was adjacent?
        # Here next start is 20.
        # If exclude_next_start=True, upper bound is 20 - eps. So key at 20 is NOT included in seg 1.

        segs_exclude = SegmentKeys.collect_segments(
            [cube], split_static=True, exclude_next_start=True
        )
        # Seg 1: 0-10 (key at 20 excluded)
        self.assertEqual(segs_exclude[0]["end"], 10.0)
        self.assertNotIn(20.0, segs_exclude[0]["keyframes"])

        # exclude_next_start=False
        # Upper bound is 20. Key at 20 IS included in seg 1.
        segs_include = SegmentKeys.collect_segments(
            [cube], split_static=True, exclude_next_start=False
        )
        # Seg 1: 0-20 (key at 20 included)
        self.assertEqual(segs_include[0]["end"], 20.0)
        self.assertIn(20.0, segs_include[0]["keyframes"])

    def test_collect_segments_static_tolerance(self):
        """collect_segments respects static_tolerance."""
        cube = pm.polyCube(name="tol_cube")[0]

        # Case 1: Flat curve (change = 0)
        pm.cutKey(cube)
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=0, at="tx")

        segs_flat = SegmentKeys.collect_segments(
            [cube], split_static=True, static_tolerance=1e-4
        )
        self.assertEqual(len(segs_flat), 0, "Flat curve should be static")

        # Case 2: Small change (0.1), Large tolerance (1.0) -> Static
        pm.cutKey(cube)
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=0.1, at="tx")

        segs_static = SegmentKeys.collect_segments(
            [cube], split_static=True, static_tolerance=1.0
        )
        # Debug info if it fails
        if len(segs_static) > 0:
            print(
                f"Failed Static Check: Found {len(segs_static)} segments: {segs_static}"
            )

        self.assertEqual(len(segs_static), 0, "Change < Tolerance should be static")

        # Case 3: Small change (0.1), Small tolerance (0.01) -> Active
        segs_active = SegmentKeys.collect_segments(
            [cube], split_static=True, static_tolerance=0.01
        )
        self.assertEqual(len(segs_active), 1, "Change > Tolerance should be active")

    def test_merge_groups_sharing_curves(self):
        """merge_groups_sharing_curves merges groups sharing an animation curve."""
        cube1 = pm.polyCube(name="c1")[0]
        cube2 = pm.polyCube(name="c2")[0]

        # Create a curve and connect to both
        pm.setKeyframe(cube1, t=0, v=0, at="tx")
        pm.setKeyframe(cube1, t=10, v=10, at="tx")
        curve = pm.listConnections(cube1.tx, type="animCurve")[0]
        pm.connectAttr(curve.output, cube2.tx, force=True)

        # Create separate groups with overlapping time ranges
        groups = [
            {
                "obj": cube1,
                "curves": [curve],
                "keyframes": [0, 10],
                "start": 0,
                "end": 10,
                "duration": 10,
                "sub_groups": [],
            },
            {
                "obj": cube2,
                "curves": [curve],
                "keyframes": [0, 10],
                "start": 0,
                "end": 10,
                "duration": 10,
                "sub_groups": [],
            },
        ]

        merged = SegmentKeys.merge_groups_sharing_curves(groups)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["objects"]), 2)

    def test_shift_curves_locked(self):
        """shift_curves handles locked curves gracefully."""
        cube = pm.polyCube(name="locked_cube")[0]
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        curve = pm.listConnections(cube.tx, type="animCurve")[0]

        curve.setLocked(True)

        # Should not raise exception
        try:
            SegmentKeys.shift_curves([curve], 10)
        except Exception as e:
            self.fail(f"shift_curves raised exception on locked curve: {e}")
        finally:
            curve.setLocked(False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
