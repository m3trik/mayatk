# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.anim_utils.stagger_keys module

Tests for StaggerKeys class functionality including:
- Sequential stagger with gap/overlap
- Interval stagger with avoid_overlap
- Group overlapping objects
- Split static segments
- Ignore attributes
- Invert order
"""
import unittest

# Initialize QApplication before importing mayatk to handle UI widgets created at module level
try:
    from PySide6.QtWidgets import QApplication

    if not QApplication.instance():
        app = QApplication([])
except ImportError:
    try:
        from PySide2.QtWidgets import QApplication

        if not QApplication.instance():
            app = QApplication([])
    except ImportError:
        pass

import pymel.core as pm
import mayatk as mtk
from mayatk.anim_utils.stagger_keys import StaggerKeys

from base_test import MayaTkTestCase


class TestStaggerKeyframes(MayaTkTestCase):
    """Test suite for stagger_keys functionality."""

    def setUp(self):
        super().setUp()
        self.cube1 = self.create_test_cube("cube1")
        self.cube2 = self.create_test_cube("cube2")
        self.cube3 = self.create_test_cube("cube3")
        self.objects = [self.cube1, self.cube2, self.cube3]

        # Create animation:
        # Cube 1: 0-10
        pm.setKeyframe(self.cube1, t=0, v=0, at="tx")
        pm.setKeyframe(self.cube1, t=10, v=10, at="tx")

        # Cube 2: 0-10 (same duration)
        pm.setKeyframe(self.cube2, t=0, v=0, at="tx")
        pm.setKeyframe(self.cube2, t=10, v=10, at="tx")

        # Cube 3: 0-10 (same duration)
        pm.setKeyframe(self.cube3, t=0, v=0, at="tx")
        pm.setKeyframe(self.cube3, t=10, v=10, at="tx")

    def test_sequential_stagger_gap(self):
        """Test sequential stagger with 5 frame gap."""
        StaggerKeys.stagger_keys(self.objects, spacing=5)

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = pm.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 0)
        self.assertAlmostEqual(c2_start, 15)  # 10 + 5
        self.assertAlmostEqual(c3_start, 30)  # 25 + 5

    def test_sequential_stagger_overlap(self):
        """Test sequential stagger with 2 frame overlap (-2)."""
        StaggerKeys.stagger_keys(self.objects, spacing=-2)

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = pm.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 0)
        self.assertAlmostEqual(c2_start, 8)  # 10 - 2
        self.assertAlmostEqual(c3_start, 16)  # 18 - 2

    def test_interval_stagger(self):
        """Test interval stagger with 20 frame interval."""
        StaggerKeys.stagger_keys(self.objects, spacing=20, use_intervals=True)

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = pm.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 0)
        self.assertAlmostEqual(c2_start, 20)
        self.assertAlmostEqual(c3_start, 40)

    def test_interval_stagger_avoid_overlap(self):
        """Test interval stagger with avoid_overlap=True."""
        # Interval = 5, Duration = 10
        StaggerKeys.stagger_keys(
            self.objects, spacing=5, use_intervals=True, avoid_overlap=True
        )

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = pm.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 0)
        self.assertAlmostEqual(c2_start, 10)  # Skips 5 because 5 < 10
        self.assertAlmostEqual(c3_start, 20)  # Skips 15 because 15 < 20

    def test_group_overlapping(self):
        """Test grouping overlapping objects."""
        # Setup overlapping objects
        # Cube 1: 0-10
        # Cube 2: 5-15 (overlaps with Cube 1)
        pm.cutKey(self.cube2)
        pm.setKeyframe(self.cube2, t=5, v=0, at="tx")
        pm.setKeyframe(self.cube2, t=15, v=10, at="tx")

        # Cube 3: 16-26 (does NOT overlap with Group 1 which ends at 15)
        pm.cutKey(self.cube3)
        pm.setKeyframe(self.cube3, t=16, v=0, at="tx")
        pm.setKeyframe(self.cube3, t=26, v=10, at="tx")

        # Group 1: 0-15 (Cube 1 & Cube 2)
        # Group 2: 16-26 (Cube 3)
        # Target for Group 2: 15 + 5 = 20.
        # Shift: 20 - 16 = 4.

        StaggerKeys.stagger_keys(self.objects, spacing=5, group_overlapping=True)

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = pm.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 0)
        self.assertAlmostEqual(c2_start, 5)  # Should not move relative to Cube 1
        self.assertAlmostEqual(c3_start, 20)

    def test_stagger_invert(self):
        """Test staggering in reverse order."""
        # Invert order: Cube 3, Cube 2, Cube 1
        # Cube 3: 0-10
        # Cube 2: 15-25 (10 + 5)
        # Cube 1: 30-40 (25 + 5)
        StaggerKeys.stagger_keys(self.objects, spacing=5, invert=True)

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = pm.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c3_start, 0)
        self.assertAlmostEqual(c2_start, 15)
        self.assertAlmostEqual(c1_start, 30)

    def test_stagger_ignore_attribute(self):
        """Test that ignored attributes are not moved."""
        # Add key on ty for cube 2
        pm.setKeyframe(self.cube2, t=0, v=0, at="ty")
        pm.setKeyframe(self.cube2, t=10, v=10, at="ty")

        # Stagger but ignore 'translateY'
        StaggerKeys.stagger_keys(self.objects, spacing=5, ignore="translateY")

        # tx should move
        c2_tx_start = pm.keyframe(self.cube2, at="tx", q=True, tc=True)[0]
        # ty should NOT move (stay at 0)
        c2_ty_start = pm.keyframe(self.cube2, at="ty", q=True, tc=True)[0]

        self.assertAlmostEqual(c2_tx_start, 15)
        self.assertAlmostEqual(c2_ty_start, 0)

    def test_stagger_start_frame_override(self):
        """Test overriding the start frame."""
        # Start at frame 100
        StaggerKeys.stagger_keys(self.objects, spacing=5, start_frame=100)

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = pm.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 100)
        self.assertAlmostEqual(c2_start, 115)  # 100 + 10 + 5
        self.assertAlmostEqual(c3_start, 130)  # 115 + 10 + 5

    def test_stagger_percentage_spacing(self):
        """Test spacing as percentage of duration."""
        # Spacing 0.5 = 50% of duration (10 frames) = 5 frames
        StaggerKeys.stagger_keys(self.objects, spacing=0.5)

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = pm.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 0)
        self.assertAlmostEqual(c2_start, 15)  # 10 + 5
        self.assertAlmostEqual(c3_start, 30)  # 25 + 5

    def test_stagger_no_keys(self):
        """Test staggering objects with no keys (should be ignored safely)."""
        cube_no_keys = self.create_test_cube("cube_no_keys")
        objects = [self.cube1, cube_no_keys, self.cube2]

        # Cube 1: 0-10
        # Cube No Keys: Ignored
        # Cube 2: 15-25 (10 + 5)
        StaggerKeys.stagger_keys(objects, spacing=5)

        c1_start = pm.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = pm.keyframe(self.cube2, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 0)
        self.assertAlmostEqual(c2_start, 15)


class TestStaggerSplitStatic(MayaTkTestCase):
    """Tests for split_static functionality in stagger_keys."""

    def setUp(self):
        super(TestStaggerSplitStatic, self).setUp()
        self.cube = self.create_test_cube()

    def test_split_static_basic(self):
        """Test splitting a single object with a static gap."""
        # Create animation: 0-10 active, 10-50 static, 50-60 active
        pm.setKeyframe(self.cube, t=0, v=0, at="tx")
        pm.setKeyframe(self.cube, t=10, v=10, at="tx")
        pm.setKeyframe(self.cube, t=50, v=10, at="tx")  # Static hold 10-50
        pm.setKeyframe(self.cube, t=60, v=20, at="tx")

        # Stagger with split_static=True
        StaggerKeys.stagger_keys(
            [self.cube], start_frame=0, spacing=0, split_static=True
        )

        times = pm.keyframe(self.cube, q=True, tc=True)
        self.assertIn(0.0, times)
        self.assertIn(20.0, times)
        self.assertTrue(any(abs(t - 10.0) < 0.001 for t in times))

    def test_split_static_multiple_objects(self):
        """Test splitting multiple objects with gaps."""
        cube2 = self.create_test_cube()

        # Cube 1: 0-10, gap, 50-60
        pm.setKeyframe(self.cube, t=0, v=0, at="tx")
        pm.setKeyframe(self.cube, t=10, v=10, at="tx")
        pm.setKeyframe(self.cube, t=50, v=10, at="tx")
        pm.setKeyframe(self.cube, t=60, v=20, at="tx")

        # Cube 2: 0-10
        pm.setKeyframe(cube2, t=0, v=0, at="tx")
        pm.setKeyframe(cube2, t=10, v=10, at="tx")

        StaggerKeys.stagger_keys(
            [self.cube, cube2], start_frame=0, spacing=0, split_static=True
        )

        # Check Cube 1
        c1_times = sorted(pm.keyframe(self.cube, q=True, tc=True))
        self.assertIn(0.0, c1_times)
        self.assertIn(20.0, c1_times)

        # Check Cube 2
        c2_times = sorted(pm.keyframe(cube2, q=True, tc=True))
        self.assertIn(20.0, c2_times)
        self.assertIn(30.0, c2_times)

    def test_split_static_with_overlap_grouping(self):
        """Test split static with group_overlapping=True."""
        cube2 = self.create_test_cube()

        # Cube 1: 0-10, gap, 50-60
        pm.setKeyframe(self.cube, t=0, v=0, at="tx")
        pm.setKeyframe(self.cube, t=10, v=10, at="tx")
        pm.setKeyframe(self.cube, t=50, v=10, at="tx")
        pm.setKeyframe(self.cube, t=60, v=20, at="tx")

        # Cube 2: 5-15
        pm.setKeyframe(cube2, t=5, v=0, at="tx")
        pm.setKeyframe(cube2, t=15, v=10, at="tx")

        StaggerKeys.stagger_keys(
            [self.cube, cube2],
            start_frame=0,
            spacing=0,
            split_static=True,
            group_overlapping=True,
        )

        # Check Cube 1
        c1_times = sorted(pm.keyframe(self.cube, q=True, tc=True))
        self.assertIn(0.0, c1_times)
        self.assertIn(10.0, c1_times)
        self.assertIn(15.0, c1_times)
        self.assertIn(25.0, c1_times)

        # Check Cube 2
        c2_times = sorted(pm.keyframe(cube2, q=True, tc=True))
        self.assertIn(5.0, c2_times)
        self.assertIn(15.0, c2_times)


class TestStaggerTangents(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.cube1 = self.create_test_cube("cube1")

    def test_stagger_smooth_tangents_skips_visibility(self):
        """Test that smooth_tangents skips visibility attributes."""
        # Create visibility keys (stepped)
        pm.setKeyframe(self.cube1, t=0, v=1, at="visibility")
        pm.setKeyframe(self.cube1, t=10, v=0, at="visibility")

        # Create transform keys (linear)
        pm.setKeyframe(self.cube1, t=0, v=0, at="tx")
        pm.setKeyframe(self.cube1, t=10, v=10, at="tx")

        # Ensure initial state
        pm.keyTangent(self.cube1, at="visibility", outTangentType="step")
        pm.keyTangent(self.cube1, at="tx", outTangentType="linear")

        # Stagger with smooth_tangents=True
        StaggerKeys.stagger_keys([self.cube1], spacing=5, smooth_tangents=True)

        # Check visibility tangents (should still be step)
        vis_tangent = pm.keyTangent(
            self.cube1, at="visibility", t=0, q=True, outTangentType=True
        )[0]
        tx_tangent = pm.keyTangent(
            self.cube1, at="tx", t=0, q=True, outTangentType=True
        )[0]

        # Visibility should NOT be auto/spline
        self.assertIn(vis_tangent, ["step", "stepNext"])

        # Transform should be auto/spline
        self.assertIn(tx_tangent, ["auto", "spline"])


# Note: Tests for internal helper methods (_filter_curves_by_ignore, _group_by_overlap,
# _get_active_animation_segments) are now in test_keyframe_grouper.py since that
# functionality has been moved to the shared KeyframeGrouper class.


if __name__ == "__main__":
    try:
        from PySide6.QtWidgets import QApplication

        if not QApplication.instance():
            app = QApplication([])
    except ImportError:
        try:
            from PySide2.QtWidgets import QApplication

            if not QApplication.instance():
                app = QApplication([])
        except ImportError:
            pass

    unittest.main(verbosity=2)
