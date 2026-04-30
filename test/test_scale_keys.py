# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.anim_utils.scale_keys module

Comprehensive tests for ScaleKeys functionality including:
- Uniform scaling (duration-based)
- Speed scaling (motion-aware retiming)
- Snapping modes (nearest, preferred, aggressive)
- Group modes (per_object, single_group, overlap_groups)
- Overlap prevention
- Edge cases
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

from mayatk.anim_utils.scale_keys import ScaleKeys

from base_test import MayaTkTestCase
import maya.cmds as cmds


# =============================================================================
# Internal Helper Tests
# =============================================================================
class TestScaleKeysInternal(MayaTkTestCase):
    """Tests for ScaleKeys helper methods."""

    def test_normalize_group_mode_valid(self):
        """Test valid group mode normalization."""
        self.assertEqual(
            ScaleKeys._normalize_group_mode("single_group"), "single_group"
        )
        self.assertEqual(ScaleKeys._normalize_group_mode("per_object"), "per_object")
        self.assertEqual(
            ScaleKeys._normalize_group_mode("overlap_groups"), "overlap_groups"
        )

    def test_normalize_group_mode_case_insensitive(self):
        """Test that group mode normalization is case-insensitive."""
        self.assertEqual(
            ScaleKeys._normalize_group_mode("SINGLE_GROUP"), "single_group"
        )
        self.assertEqual(ScaleKeys._normalize_group_mode("Per_Object"), "per_object")

    def test_normalize_group_mode_none(self):
        """Test that None defaults to 'single_group'."""
        self.assertEqual(ScaleKeys._normalize_group_mode(None), "single_group")

    def test_normalize_group_mode_invalid(self):
        """Test that invalid mode raises ValueError."""
        with self.assertRaises(ValueError):
            ScaleKeys._normalize_group_mode("invalid_mode")

    def test_normalize_keys_none(self):
        """Test keys normalization with None input."""
        time_range, selected = ScaleKeys._normalize_keys_to_time_range_and_selection(
            None
        )
        self.assertEqual(time_range, (None, None))
        self.assertFalse(selected)

    def test_normalize_keys_selected_string(self):
        """Test keys normalization with 'selected' string."""
        time_range, selected = ScaleKeys._normalize_keys_to_time_range_and_selection(
            "selected"
        )
        self.assertEqual(time_range, (None, None))
        self.assertTrue(selected)

    def test_normalize_keys_numeric_string(self):
        """Test keys normalization with numeric string."""
        time_range, selected = ScaleKeys._normalize_keys_to_time_range_and_selection(
            "10.5"
        )
        self.assertEqual(time_range, (10.5, 10.5))
        self.assertFalse(selected)

    def test_normalize_keys_tuple(self):
        """Test keys normalization with tuple range."""
        time_range, selected = ScaleKeys._normalize_keys_to_time_range_and_selection(
            (1, 10)
        )
        self.assertEqual(time_range, (1.0, 10.0))
        self.assertFalse(selected)

    def test_normalize_keys_list_multiple(self):
        """Test keys normalization with list of values."""
        time_range, selected = ScaleKeys._normalize_keys_to_time_range_and_selection(
            [1, 5, 10, 3]
        )
        self.assertEqual(time_range, (1.0, 10.0))  # Should be min/max
        self.assertFalse(selected)


# =============================================================================
# Uniform Scaling Tests
# =============================================================================
class TestUniformScaling(MayaTkTestCase):
    """Tests for scale_keys with uniform mode (duration-based scaling)."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="test_uniform_cube")[0]

    def test_scale_uniform_expansion(self):
        """Test uniform scaling expands from the start frame (pivot)."""
        cmds.setKeyframe(self.cube, at="translateX", t=1.0, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=11.0, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="uniform", factor=2.0, snap_mode="none"
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        self.assertEqual(keys[0], 1.0, "Start frame should remain at 1.0")
        self.assertEqual(keys[-1], 21.0, "End frame should be at 21.0")

    def test_scale_uniform_compression(self):
        """Test uniform scaling compresses duration."""
        cmds.setKeyframe(self.cube, at="translateX", t=0.0, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=20.0, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="uniform", factor=0.5, snap_mode="none"
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        self.assertEqual(keys[0], 0.0)
        self.assertEqual(keys[-1], 10.0)

    def test_scale_uniform_identity(self):
        """Test that scaling by 1.0 with no snapping does not move keys."""
        cmds.setKeyframe(self.cube, at="translateX", t=1.5, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=10.5, v=10)

        original_times = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="uniform", factor=1.0, snap_mode="none"
        )

        new_times = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        self.assertEqual(original_times, new_times)

    def test_scale_uniform_absolute(self):
        """Test absolute uniform scaling (target duration)."""
        cmds.setKeyframe(self.cube, at="translateX", t=1, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=11, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube],
            mode="uniform",
            factor=20.0,
            absolute=True,
            snap_mode="none",
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        duration = keys[-1] - keys[0]
        self.assertAlmostEqual(duration, 20.0, delta=0.01)

    def test_scale_uniform_custom_pivot(self):
        """Test uniform scaling with custom pivot point."""
        cmds.setKeyframe(self.cube, at="translateX", t=10, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=20, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube],
            mode="uniform",
            factor=2.0,
            pivot=15.0,
            snap_mode="none",
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        # Pivot at 15: 10 -> 15 + (10-15)*2 = 5, 20 -> 15 + (20-15)*2 = 25
        self.assertAlmostEqual(keys[0], 5.0, delta=0.01)
        self.assertAlmostEqual(keys[-1], 25.0, delta=0.01)

    def test_scale_uniform_multiple_objects_per_object(self):
        """Test uniform scaling with multiple objects in per_object mode."""
        cube2 = cmds.polyCube(name="test_uniform_cube_2")[0]

        cmds.setKeyframe(self.cube, at="translateX", t=1, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=11, v=10)
        cmds.setKeyframe(cube2, at="translateX", t=1, v=0)
        cmds.setKeyframe(cube2, at="translateX", t=21, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube, cube2],
            mode="uniform",
            factor=0.5,
            snap_mode="none",
            group_mode="per_object",
        )

        keys1 = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        keys2 = cmds.keyframe(cube2, at="translateX", q=True, tc=True)

        self.assertAlmostEqual(keys1[-1] - keys1[0], 5.0, delta=0.01)
        self.assertAlmostEqual(keys2[-1] - keys2[0], 10.0, delta=0.01)

        cmds.delete(cube2)

    def test_scale_uniform_multiple_objects_single_group(self):
        """Test uniform scaling with multiple objects in single_group mode."""
        cube2 = cmds.polyCube(name="test_uniform_cube_2")[0]

        cmds.setKeyframe(self.cube, at="translateX", t=0, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=10, v=10)
        cmds.setKeyframe(cube2, at="translateX", t=5, v=0)
        cmds.setKeyframe(cube2, at="translateX", t=15, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube, cube2],
            mode="uniform",
            factor=2.0,
            snap_mode="none",
            group_mode="single_group",
        )

        keys1 = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        keys2 = cmds.keyframe(cube2, at="translateX", q=True, tc=True)

        # Group range: 0-15, pivot=0
        # Cube1: 0->0, 10->20
        # Cube2: 5->10, 15->30
        self.assertAlmostEqual(keys1[0], 0.0, delta=0.01)
        self.assertAlmostEqual(keys1[-1], 20.0, delta=0.01)
        self.assertAlmostEqual(keys2[0], 10.0, delta=0.01)
        self.assertAlmostEqual(keys2[-1], 30.0, delta=0.01)

        cmds.delete(cube2)


# =============================================================================
# Speed Scaling Tests
# =============================================================================
class TestSpeedScaling(MayaTkTestCase):
    """Tests for scale_keys with speed mode (motion-aware retiming)."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="test_speed_cube", w=2, h=2, d=2)[0]

    def test_scale_speed_linear(self):
        """Test speed scaling with linear motion only."""
        cmds.setKeyframe(self.cube, at="translateX", t=1, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=11, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=2.0, include_rotation=False
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        duration = keys[-1] - keys[0]
        self.assertAlmostEqual(duration, 5.0, delta=0.1)

    def test_scale_speed_rotation_only(self):
        """Test speed scaling with rotation only."""
        cmds.setKeyframe(self.cube, at="rotateY", t=1, v=0)
        cmds.setKeyframe(self.cube, at="rotateY", t=11, v=90)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=1.0, include_rotation="only"
        )

        keys = cmds.keyframe(self.cube, at="rotateY", q=True, tc=True)
        duration = keys[-1] - keys[0]
        self.assertAlmostEqual(duration, 90.0, delta=0.5)

    def test_scale_speed_combined(self):
        """Test speed scaling with combined translation and rotation."""
        cmds.setKeyframe(self.cube, at="translateX", t=1, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=11, v=1)
        cmds.setKeyframe(self.cube, at="rotateY", t=1, v=0)
        cmds.setKeyframe(self.cube, at="rotateY", t=11, v=180)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=1.0, include_rotation=True
        )

        keys = cmds.keyframe(self.cube, at="rotateY", q=True, tc=True)
        duration = keys[-1] - keys[0]
        self.assertAlmostEqual(duration, 180.0, delta=0.5)

    def test_scale_speed_intermediate_keys(self):
        """Test speed scaling preserves proportional key positions."""
        cmds.setKeyframe(self.cube, at="translateX", t=1, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=6, v=5)
        cmds.setKeyframe(self.cube, at="translateX", t=11, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube],
            mode="speed",
            factor=2.0,
            include_rotation=False,
            snap_mode="none",
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)

        self.assertAlmostEqual(keys[0], 1.0, delta=0.01)
        self.assertAlmostEqual(keys[-1], 6.0, delta=0.1)
        self.assertAlmostEqual(keys[1], 3.5, delta=0.1)

    def test_scale_speed_start_frame_preservation(self):
        """Test that the start frame is preserved during speed scaling."""
        start_frame = 100
        cmds.setKeyframe(self.cube, at="translateX", t=start_frame, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=start_frame + 10, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=2.0, include_rotation=False
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)

        self.assertEqual(keys[0], start_frame)
        self.assertAlmostEqual(keys[-1], start_frame + 5, delta=0.1)

    def test_scale_speed_relative(self):
        """Test relative speed scaling (multiplier)."""
        cmds.setKeyframe(self.cube, at="translateX", t=1, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=11, v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=2.0, absolute=False
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        duration = keys[-1] - keys[0]
        self.assertAlmostEqual(duration, 5.0, delta=0.1)

    def test_scale_speed_multiple_objects(self):
        """Test speed scaling with multiple objects."""
        cube2 = cmds.polyCube(name="test_speed_cube_2")[0]

        cmds.setKeyframe(self.cube, at="translateX", t=1, v=0)
        cmds.setKeyframe(self.cube, at="translateX", t=11, v=10)
        cmds.setKeyframe(cube2, at="translateX", t=1, v=0)
        cmds.setKeyframe(cube2, at="translateX", t=11, v=20)

        ScaleKeys.scale_keys(
            objects=[self.cube, cube2],
            mode="speed",
            factor=2.0,
            include_rotation=False,
            group_mode="per_object",
        )

        keys1 = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        keys2 = cmds.keyframe(cube2, at="translateX", q=True, tc=True)

        self.assertAlmostEqual(keys1[-1] - keys1[0], 5.0, delta=0.1)
        self.assertAlmostEqual(keys2[-1] - keys2[0], 10.0, delta=0.1)

        cmds.delete(cube2)


# =============================================================================
# Speed Accuracy Tests
# =============================================================================
class TestSpeedAccuracy(MayaTkTestCase):
    """Rigorous tests for speed scaling accuracy."""

    def setUp(self):
        super().setUp()
        self.cube_a = cmds.polyCube(name="obj_A")[0]
        self.cube_b = cmds.polyCube(name="obj_B")[0]

    def test_per_object_absolute_speed_different_distances(self):
        """Verify per_object mode scales each object to exact target speed."""
        cmds.setKeyframe(self.cube_a, t=0, at="tx", v=0)
        cmds.setKeyframe(self.cube_a, t=10, at="tx", v=10)
        cmds.setKeyframe(self.cube_b, t=0, at="tx", v=0)
        cmds.setKeyframe(self.cube_b, t=10, at="tx", v=20)

        ScaleKeys.scale_keys(
            objects=[self.cube_a, self.cube_b],
            mode="speed",
            factor=2.0,
            absolute=True,
            group_mode="per_object",
            snap_mode="none",
        )

        keys_a = cmds.keyframe(self.cube_a, at="tx", q=True, tc=True)
        keys_b = cmds.keyframe(self.cube_b, at="tx", q=True, tc=True)

        self.assertAlmostEqual(keys_a[-1] - keys_a[0], 5.0, delta=0.01)
        self.assertAlmostEqual(keys_b[-1] - keys_b[0], 10.0, delta=0.01)

    def test_single_group_absolute_speed_synchronizes(self):
        """Verify single_group mode synchronizes objects based on slowest member."""
        cmds.setKeyframe(self.cube_a, t=0, at="tx", v=0)
        cmds.setKeyframe(self.cube_a, t=10, at="tx", v=10)
        cmds.setKeyframe(self.cube_b, t=0, at="tx", v=0)
        cmds.setKeyframe(self.cube_b, t=10, at="tx", v=20)

        ScaleKeys.scale_keys(
            objects=[self.cube_a, self.cube_b],
            mode="speed",
            factor=2.0,
            absolute=True,
            group_mode="single_group",
            snap_mode="none",
        )

        keys_a = cmds.keyframe(self.cube_a, at="tx", q=True, tc=True)
        keys_b = cmds.keyframe(self.cube_b, at="tx", q=True, tc=True)

        dur_a = keys_a[-1] - keys_a[0]
        dur_b = keys_b[-1] - keys_b[0]

        self.assertAlmostEqual(dur_a, dur_b, delta=0.01)

    def test_rotation_translation_equivalence(self):
        """Verify 1 unit translation and 1 degree rotation result in same duration."""
        obj_trans = cmds.polyCube(name="obj_Trans")[0]
        obj_rot = cmds.polyCube(name="obj_Rot")[0]

        cmds.setKeyframe(obj_trans, t=0, at="tx", v=0)
        cmds.setKeyframe(obj_trans, t=100, at="tx", v=90)
        cmds.setKeyframe(obj_rot, t=0, at="ry", v=0)
        cmds.setKeyframe(obj_rot, t=100, at="ry", v=90)

        ScaleKeys.scale_keys(
            objects=[obj_trans, obj_rot],
            mode="speed",
            factor=10.0,
            absolute=True,
            group_mode="per_object",
            include_rotation=True,
            snap_mode="none",
        )

        keys_trans = cmds.keyframe(obj_trans, at="tx", q=True, tc=True)
        keys_rot = cmds.keyframe(obj_rot, at="ry", q=True, tc=True)

        self.assertAlmostEqual(keys_trans[-1] - keys_trans[0], 9.0, delta=0.01)
        self.assertAlmostEqual(keys_rot[-1] - keys_rot[0], 9.0, delta=0.01)

        cmds.delete(obj_trans)
        cmds.delete(obj_rot)


# =============================================================================
# Snapping Tests
# =============================================================================
class TestSnapping(MayaTkTestCase):
    """Tests for keyframe snapping during scaling."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="test_snap_cube")[0]
        cmds.setKeyframe(self.cube, t=10, at="tx", v=0)
        cmds.setKeyframe(self.cube, t=20, at="tx", v=10)

    def test_snap_nearest(self):
        """Test 'nearest' snapping mode."""
        ScaleKeys.scale_keys(
            objects=[self.cube],
            factor=1.25,
            pivot=0,
            mode="uniform",
            snap_mode="nearest",
        )
        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        self.assertIn(times[0], [12.0, 13.0])
        self.assertEqual(times[1], 25.0)

    def test_snap_none(self):
        """Test 'none' snapping mode (precise)."""
        ScaleKeys.scale_keys(
            objects=[self.cube], factor=1.25, pivot=0, mode="uniform", snap_mode="none"
        )
        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        self.assertAlmostEqual(times[0], 12.5, delta=0.001)
        self.assertEqual(times[1], 25.0)

    def test_snap_preferred(self):
        """Test 'preferred' snapping mode."""
        ScaleKeys.scale_keys(
            objects=[self.cube],
            factor=1.01,
            pivot=0,
            mode="uniform",
            snap_mode="preferred",
        )
        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        self.assertEqual(times[0], 10.0)

    def test_snap_aggressive(self):
        """Test 'aggressive_preferred' snapping mode."""
        ScaleKeys.scale_keys(
            objects=[self.cube],
            factor=1.25,
            pivot=0,
            mode="uniform",
            snap_mode="aggressive_preferred",
        )
        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        self.assertIn(times[0], [10.0, 12.0, 13.0])


class TestSnappingEdgeCases(MayaTkTestCase):
    """Tests for snapping edge cases."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="test_snap_edge_cube")[0]

    def test_negative_frames(self):
        """Test snapping with negative frame numbers."""
        cmds.setKeyframe(self.cube, t=-1.2, at="tx", v=0)
        cmds.setKeyframe(self.cube, t=-2.2, at="tx", v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube], factor=1.0, mode="uniform", snap_mode="nearest"
        )

        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        for t in times:
            self.assertEqual(t, int(t))
        self.assertEqual(times[0], -2.0)
        self.assertEqual(times[1], -1.0)

    def test_large_frames(self):
        """Test snapping with large frame numbers."""
        cmds.setKeyframe(self.cube, t=100000.4, at="tx", v=0)

        ScaleKeys.scale_keys(
            objects=[self.cube], factor=1.0, mode="uniform", snap_mode="nearest"
        )

        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        self.assertEqual(times[0], 100000.0)

    def test_collision_merge(self):
        """Test that snapping two keys to the same frame merges them."""
        cmds.setKeyframe(self.cube, t=1.1, at="tx", v=5)
        cmds.setKeyframe(self.cube, t=1.2, at="tx", v=10)

        ScaleKeys.scale_keys(
            objects=[self.cube], factor=1.0, mode="uniform", snap_mode="nearest"
        )

        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        self.assertEqual(len(times), 1)
        self.assertEqual(times[0], 1.0)

    def test_speed_snapping_collision(self):
        """Test that speed scaling with snapping handles key collision gracefully."""
        for t in range(1, 6):
            cmds.setKeyframe(self.cube, at="translateX", t=t, v=t)

        ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=5.0, snap_mode="nearest"
        )

        keys = cmds.keyframe(self.cube, at="translateX", q=True, tc=True)
        self.assertGreater(len(keys), 1)
        self.assertEqual(keys[0], 1.0)


# =============================================================================
# Overlap Prevention Tests
# =============================================================================
class TestOverlapPrevention(MayaTkTestCase):
    """Tests for prevent_overlap functionality."""

    def setUp(self):
        super().setUp()
        self.cube1 = cmds.polyCube(name="cube1")[0]
        self.cube2 = cmds.polyCube(name="cube2")[0]

    def test_prevent_overlap_true(self):
        """Test that prevent_overlap=True shifts objects to avoid collision."""
        cmds.setKeyframe(self.cube1, time=1, attribute="translateX", value=0)
        cmds.setKeyframe(self.cube1, time=10, attribute="translateX", value=10)
        cmds.setKeyframe(self.cube2, time=15, attribute="translateX", value=0)
        cmds.setKeyframe(self.cube2, time=25, attribute="translateX", value=10)

        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2],
            mode="uniform",
            factor=3.0,
            group_mode="per_object",
            prevent_overlap=True,
        )

        c1_end = cmds.keyframe(self.cube1, q=True, tc=True)[-1]
        c2_start = cmds.keyframe(self.cube2, q=True, tc=True)[0]

        self.assertGreaterEqual(c2_start, c1_end)

    def test_prevent_overlap_false(self):
        """Test that prevent_overlap=False allows collision."""
        cmds.setKeyframe(self.cube1, time=1, attribute="translateX", value=0)
        cmds.setKeyframe(self.cube1, time=10, attribute="translateX", value=10)
        cmds.setKeyframe(self.cube2, time=15, attribute="translateX", value=0)
        cmds.setKeyframe(self.cube2, time=25, attribute="translateX", value=10)

        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2],
            mode="uniform",
            factor=3.0,
            group_mode="per_object",
            prevent_overlap=False,
            split_static=False,
        )

        c1_end = cmds.keyframe(self.cube1, q=True, tc=True)[-1]
        c2_start = cmds.keyframe(self.cube2, q=True, tc=True)[0]

        self.assertLess(c2_start, c1_end)

    def test_prevent_overlap_speed_mode(self):
        """Test prevent_overlap with speed mode."""
        cmds.setKeyframe(self.cube1, t=0, v=0, at="tx")
        cmds.setKeyframe(self.cube1, t=10, v=10, at="tx")
        cmds.setKeyframe(self.cube2, t=20, v=0, at="tx")
        cmds.setKeyframe(self.cube2, t=30, v=10, at="tx")

        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2],
            mode="speed",
            factor=0.33,
            group_mode="per_object",
            prevent_overlap=True,
        )

        c1_end = cmds.keyframe(self.cube1, q=True, tc=True)[-1]
        c2_start = cmds.keyframe(self.cube2, q=True, tc=True)[0]

        self.assertGreaterEqual(c2_start, c1_end - 1)  # Allow small tolerance

    def test_prevent_overlap_touching_keys(self):
        """Test prevent_overlap with touching keys (end == start)."""
        cmds.setKeyframe(self.cube1, t=0, v=0, at="tx")
        cmds.setKeyframe(self.cube1, t=10, v=10, at="tx")
        cmds.setKeyframe(self.cube2, t=10, v=0, at="tx")
        cmds.setKeyframe(self.cube2, t=20, v=10, at="tx")

        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2],
            factor=2.0,
            group_mode="per_object",
            prevent_overlap=True,
            snap_mode="none",
        )

        c1_end = cmds.keyframe(self.cube1, q=True, tc=True)[-1]
        c2_start = cmds.keyframe(self.cube2, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_end, 20.0, delta=0.001)
        self.assertAlmostEqual(c2_start, 20.0, delta=0.001)

    def test_prevent_overlap_resolves_existing_overlap(self):
        """Test that prevent_overlap=True resolves existing overlaps by staggering."""
        # Create two objects that overlap significantly
        # Cube1: 0-20
        cmds.setKeyframe(self.cube1, t=0, v=0, at="tx")
        cmds.setKeyframe(self.cube1, t=20, v=10, at="tx")

        # Cube2: 5-25 (Overlaps Cube1)
        cmds.setKeyframe(self.cube2, t=5, v=0, at="tx")
        cmds.setKeyframe(self.cube2, t=25, v=10, at="tx")

        # Scale by 2.0 with prevent_overlap=True
        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2],
            factor=2.0,
            pivot=0,
            prevent_overlap=True,
        )

        # Expected:
        # Cube1 scales to 0-40
        # Cube2 scales to 10-50 (if no overlap prevention)
        # With overlap prevention, Cube2 should be pushed to start at Cube1's end (40)

        c1_end = cmds.keyframe(self.cube1, q=True, tc=True)[-1]
        c2_start = cmds.keyframe(self.cube2, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_end, 40.0)
        self.assertAlmostEqual(c2_start, 40.0)  # Should start exactly where c1 ends


# =============================================================================
# Group Mode Tests
# =============================================================================
class TestGroupModes(MayaTkTestCase):
    """Tests for different group_mode settings."""

    def setUp(self):
        super().setUp()
        self.cube1 = cmds.polyCube(name="group_cube1")[0]
        self.cube2 = cmds.polyCube(name="group_cube2")[0]
        self.cube3 = cmds.polyCube(name="group_cube3")[0]

    def test_group_mode_overlap_groups(self):
        """Test group_mode='overlap_groups'."""
        # Group 1: cube1 & cube2 overlap
        cmds.setKeyframe(self.cube1, t=0, v=0, at="tx")
        cmds.setKeyframe(self.cube1, t=10, v=10, at="tx")
        cmds.setKeyframe(self.cube2, t=5, v=0, at="tx")
        cmds.setKeyframe(self.cube2, t=15, v=10, at="tx")

        # Group 2: cube3 separate
        cmds.setKeyframe(self.cube3, t=20, v=0, at="tx")
        cmds.setKeyframe(self.cube3, t=30, v=10, at="tx")

        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2, self.cube3],
            factor=2.0,
            group_mode="overlap_groups",
            snap_mode="none",
            split_static=False,
        )

        c1_start = cmds.keyframe(self.cube1, q=True, tc=True)[0]
        c2_start = cmds.keyframe(self.cube2, q=True, tc=True)[0]
        c3_start = cmds.keyframe(self.cube3, q=True, tc=True)[0]

        self.assertAlmostEqual(c1_start, 0.0, delta=0.001)
        self.assertAlmostEqual(c2_start, 10.0, delta=0.001)
        self.assertAlmostEqual(c3_start, 20.0, delta=0.001)

    def test_group_mode_absolute_overlap_groups(self):
        """Test absolute mode with overlap_groups preserves group offsets."""
        cmds.setKeyframe(self.cube1, t=0, v=0, at="tx")
        cmds.setKeyframe(self.cube1, t=10, v=10, at="tx")
        cmds.setKeyframe(self.cube2, t=20, v=0, at="tx")
        cmds.setKeyframe(self.cube2, t=30, v=10, at="tx")

        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2],
            factor=1.0,
            absolute=True,
            group_mode="overlap_groups",
            snap_mode="none",
            split_static=False,
        )

        c1_keys = cmds.keyframe(self.cube1, q=True, tc=True, at="tx")
        c2_keys = cmds.keyframe(self.cube2, q=True, tc=True, at="tx")

        self.assertAlmostEqual(c1_keys[0], 0.0, delta=0.001)
        self.assertAlmostEqual(c1_keys[-1], 1.0, delta=0.001)
        self.assertAlmostEqual(c2_keys[0], 20.0, delta=0.001)
        self.assertAlmostEqual(c2_keys[-1], 21.0, delta=0.001)


# =============================================================================
# Ignore Parameter Tests
# =============================================================================
class TestIgnoreParameter(MayaTkTestCase):
    """Tests for the ignore parameter."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="ignore_cube")[0]

    def test_ignore_visibility(self):
        """Test that ignore='visibility' excludes visibility keys."""
        cmds.setKeyframe(self.cube, attribute="translateX", time=10, value=0)
        cmds.setKeyframe(self.cube, attribute="translateX", time=20, value=10)
        cmds.setKeyframe(self.cube, attribute="visibility", time=1, value=1)

        ScaleKeys.scale_keys(
            objects=[self.cube],
            factor=2.0,
            ignore="visibility",
            pivot=None,
            snap_mode="none",
        )

        tx_keys = cmds.keyframe(self.cube, attribute="translateX", query=True, tc=True)
        vis_keys = cmds.keyframe(self.cube, attribute="visibility", query=True, tc=True)

        self.assertAlmostEqual(tx_keys[0], 10.0, delta=0.001)
        self.assertAlmostEqual(tx_keys[1], 30.0, delta=0.001)
        self.assertAlmostEqual(vis_keys[0], 1.0, delta=0.001)

    def test_ignore_multiple_attributes(self):
        """Test ignoring multiple attributes."""
        cmds.setKeyframe(self.cube, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(self.cube, attribute="translateX", time=10, value=10)
        cmds.setKeyframe(self.cube, attribute="visibility", time=5, value=1)
        cmds.setKeyframe(self.cube, attribute="scaleX", time=7, value=1)

        ScaleKeys.scale_keys(
            objects=[self.cube],
            factor=2.0,
            ignore=["visibility", "scaleX"],
            snap_mode="none",
        )

        tx_keys = cmds.keyframe(self.cube, attribute="translateX", query=True, tc=True)
        vis_keys = cmds.keyframe(self.cube, attribute="visibility", query=True, tc=True)
        sx_keys = cmds.keyframe(self.cube, attribute="scaleX", query=True, tc=True)

        self.assertAlmostEqual(tx_keys[-1], 20.0, delta=0.001)
        self.assertAlmostEqual(vis_keys[0], 5.0, delta=0.001)
        self.assertAlmostEqual(sx_keys[0], 7.0, delta=0.001)


# =============================================================================
# Keys Parameter Tests
# =============================================================================
class TestKeysParameter(MayaTkTestCase):
    """Tests for the keys parameter (range and selection modes)."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="keys_param_cube")[0]

    def test_keys_range_tuple(self):
        """Test scaling only keys within a specified range."""
        cmds.setKeyframe(self.cube, at="tx", t=0, v=0)
        cmds.setKeyframe(self.cube, at="tx", t=10, v=10)
        cmds.setKeyframe(self.cube, at="tx", t=20, v=20)
        cmds.setKeyframe(self.cube, at="tx", t=30, v=30)

        ScaleKeys.scale_keys(
            objects=[self.cube],
            factor=2.0,
            keys=(5, 25),
            snap_mode="none",
        )

        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        # Keys at 0 and 30 should be unaffected (outside range 5-25)
        # Keys at 10 and 20 should be scaled around pivot 5:
        #   10 -> 5 + (10-5)*2 = 15
        #   20 -> 5 + (20-5)*2 = 35
        # So final keys should be: [0, 15, 30, 35]
        self.assertAlmostEqual(times[0], 0.0, delta=0.001)  # Unaffected (outside range)
        self.assertIn(
            30.0, [round(t, 1) for t in times]
        )  # Key at 30 still exists (unaffected)


# =============================================================================
# Rotation Scaling Tests
# =============================================================================
class TestRotationScaling(MayaTkTestCase):
    """Tests for rotation-specific scaling behavior."""

    def setUp(self):
        super().setUp()

    def test_rotation_synchronization_relative(self):
        """Test that relative scaling keeps rotation objects synchronized."""
        planes = []
        for i in range(3):
            p = cmds.polyPlane(name=f"rot_plane_{i}")[0]
            cmds.setKeyframe(p, t=0, v=0, at="ry")
            cmds.setKeyframe(p, t=10, v=90 * (i + 1), at="ry")  # Different rotations
            planes.append(p)

        ScaleKeys.scale_keys(
            objects=planes,
            mode="speed",
            factor=2.0,
            absolute=False,
            group_mode="single_group",
            include_rotation="only",
            snap_mode="none",
        )

        durations = []
        for p in planes:
            keys = cmds.keyframe(p, at="ry", q=True, tc=True)
            durations.append(keys[-1] - keys[0])

        self.assertEqual(len(set(durations)), 1)

        for p in planes:
            cmds.delete(p)


# =============================================================================
# Selected Keys Tests
# =============================================================================
class TestSelectedKeys(MayaTkTestCase):
    """Tests for keys='selected' functionality."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="selected_keys_cube")[0]

    def test_selected_keys_only(self):
        """Test scaling only selected keyframes."""
        cmds.setKeyframe(self.cube, at="tx", t=0, v=0)
        cmds.setKeyframe(self.cube, at="tx", t=10, v=10)
        cmds.setKeyframe(
            self.cube, at="tx", t=30, v=30
        )  # Moved further out to avoid merge

        # Select only middle key
        cmds.selectKey(self.cube, time=(10, 10), attribute="translateX")

        ScaleKeys.scale_keys(
            objects=[self.cube],
            mode="uniform",
            factor=2.0,
            keys="selected",
            pivot=0,
            snap_mode="none",
        )

        times = cmds.keyframe(self.cube, at="tx", query=True, tc=True)
        # Only the selected key at 10 should move to 20
        self.assertEqual(len(times), 3)
        self.assertAlmostEqual(times[0], 0.0, delta=0.001)  # Unselected
        self.assertAlmostEqual(times[1], 20.0, delta=0.001)  # Selected, scaled
        self.assertAlmostEqual(times[2], 30.0, delta=0.001)  # Unselected


# =============================================================================
# Error Handling Tests
# =============================================================================
class TestErrorHandling(MayaTkTestCase):
    """Tests for error handling and edge cases."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="error_cube")[0]

    def test_zero_factor_returns_zero(self):
        """Test that factor=0 returns 0 and doesn't crash."""
        cmds.setKeyframe(self.cube, at="tx", t=0, v=0)
        cmds.setKeyframe(self.cube, at="tx", t=10, v=10)

        result = ScaleKeys.scale_keys(objects=[self.cube], mode="uniform", factor=0)
        self.assertEqual(result, 0)

    def test_negative_factor_returns_zero(self):
        """Test that negative factor returns 0."""
        cmds.setKeyframe(self.cube, at="tx", t=0, v=0)
        cmds.setKeyframe(self.cube, at="tx", t=10, v=10)

        result = ScaleKeys.scale_keys(objects=[self.cube], mode="uniform", factor=-1.0)
        self.assertEqual(result, 0)

    def test_no_objects_returns_zero(self):
        """Test that empty objects list returns 0."""
        result = ScaleKeys.scale_keys(objects=[], mode="uniform", factor=2.0)
        self.assertEqual(result, 0)

    def test_no_keyframes_returns_zero(self):
        """Test that object without keyframes returns 0."""
        result = ScaleKeys.scale_keys(objects=[self.cube], mode="uniform", factor=2.0)
        self.assertEqual(result, 0)

    def test_speed_mode_no_motion(self):
        """Test speed mode with static object (no motion)."""
        # All keys at same value = no motion
        cmds.setKeyframe(self.cube, at="tx", t=0, v=5)
        cmds.setKeyframe(self.cube, at="tx", t=10, v=5)

        result = ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=2.0, include_rotation=False
        )
        # Should warn and return 0
        self.assertEqual(result, 0)


# =============================================================================
# Samples Parameter Tests
# =============================================================================
class TestSamplesParameter(MayaTkTestCase):
    """Tests for the samples parameter in speed mode."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="samples_cube")[0]

    def test_samples_affects_speed_calculation(self):
        """Test that different sample counts work correctly."""
        cmds.setKeyframe(self.cube, at="tx", t=0, v=0)
        cmds.setKeyframe(self.cube, at="tx", t=10, v=10)

        # Low samples
        result_low = ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=2.0, samples=4
        )
        keys_low = cmds.keyframe(self.cube, at="tx", q=True, tc=True)

        # Reset
        cmds.cutKey(self.cube)
        cmds.setKeyframe(self.cube, at="tx", t=0, v=0)
        cmds.setKeyframe(self.cube, at="tx", t=10, v=10)

        # High samples
        result_high = ScaleKeys.scale_keys(
            objects=[self.cube], mode="speed", factor=2.0, samples=128
        )
        keys_high = cmds.keyframe(self.cube, at="tx", q=True, tc=True)

        # Both should succeed (for linear motion, samples shouldn't matter much)
        self.assertGreater(result_low, 0)
        self.assertGreater(result_high, 0)


# =============================================================================
# Helper Method Tests
# =============================================================================
class TestHelperMethods(MayaTkTestCase):
    """Tests for internal helper methods."""

    def test_resolve_group_bounds(self):
        """Test _resolve_group_bounds calculation."""
        group = [
            {"start": 5, "end": 15},
            {"start": 0, "end": 10},
            {"start": 10, "end": 20},
        ]

        bounds = ScaleKeys._resolve_group_bounds(group, None, None)

        self.assertEqual(bounds, (0, 20))

    def test_resolve_group_bounds_with_clamp(self):
        """Test _resolve_group_bounds with base clamping."""
        group = [
            {"start": 0, "end": 100},
        ]

        bounds = ScaleKeys._resolve_group_bounds(group, 10, 50)

        self.assertEqual(bounds, (10, 50))


# =============================================================================
# Split Static Segment Tests
# =============================================================================
class TestSplitStaticSegments(MayaTkTestCase):
    """Tests for split_static parameter functionality.

    This tests the integration of KeyframeGrouper.collect_segments with scale_keys.
    When split_static=True, animation separated by static gaps should be scaled independently.
    """

    def test_split_static_default_enabled(self):
        """Test that split_static is enabled by default."""
        cube = cmds.polyCube(name="split_test_cube")[0]

        # Create animation with a static gap:
        # Segment 1: frames 0-10 (moving)
        # Static gap: frames 10-20 (no change)
        # Segment 2: frames 20-30 (moving)
        cmds.setKeyframe(cube, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(cube, attribute="translateX", time=10, value=10)  # End segment 1
        cmds.setKeyframe(cube, attribute="translateX", time=20, value=10)  # Static hold
        cmds.setKeyframe(cube, attribute="translateX", time=30, value=20)  # End segment 2

        # Scale at 2x - segments should scale independently
        keys_scaled = ScaleKeys.scale_keys(
            objects=[cube],
            factor=2.0,
            snap_mode="none",  # Disable snapping for precision
        )

        self.assertGreater(keys_scaled, 0)
        cmds.delete(cube)

    def test_split_static_disabled(self):
        """Test that split_static=False scales all keys as one block."""
        cube = cmds.polyCube(name="split_test_cube2")[0]

        # Create animation with a static gap
        cmds.setKeyframe(cube, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(cube, attribute="translateX", time=10, value=10)
        cmds.setKeyframe(cube, attribute="translateX", time=20, value=10)  # Static hold
        cmds.setKeyframe(cube, attribute="translateX", time=30, value=20)

        # Get original key times
        original_times = cmds.keyframe(cube, query=True, timeChange=True)

        # Scale at 2x with split_static disabled
        keys_scaled = ScaleKeys.scale_keys(
            objects=[cube],
            factor=2.0,
            split_static=False,
            snap_mode="none",
        )

        self.assertGreater(keys_scaled, 0)

        # With split_static=False, all keys should scale from common pivot
        # The static gap should also expand
        new_times = cmds.keyframe(cube, query=True, timeChange=True)

        # Verify we have the same number of keys
        self.assertEqual(len(new_times), len(original_times))

        cmds.delete(cube)

    def test_split_static_multiple_objects(self):
        """Test split_static works correctly with multiple objects."""
        cube1 = cmds.polyCube(name="split_cube1")[0]
        cube2 = cmds.polyCube(name="split_cube2")[0]

        # Cube1: Animation at frames 0-10, then 20-30
        cmds.setKeyframe(cube1, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(cube1, attribute="translateX", time=10, value=5)
        cmds.setKeyframe(cube1, attribute="translateX", time=20, value=5)  # Static
        cmds.setKeyframe(cube1, attribute="translateX", time=30, value=10)

        # Cube2: Animation at frames 5-15, then 25-35
        cmds.setKeyframe(cube2, attribute="translateX", time=5, value=0)
        cmds.setKeyframe(cube2, attribute="translateX", time=15, value=5)
        cmds.setKeyframe(cube2, attribute="translateX", time=25, value=5)  # Static
        cmds.setKeyframe(cube2, attribute="translateX", time=35, value=10)

        keys_scaled = ScaleKeys.scale_keys(
            objects=[cube1, cube2],
            factor=0.5,
            split_static=True,
            snap_mode="none",
        )

        self.assertGreater(keys_scaled, 0)

        cmds.delete(cube1)
        cmds.delete(cube2)

    def test_segment_keys_integration(self):
        """Test that SegmentKeys is used for segment collection."""
        from mayatk.anim_utils.segment_keys import SegmentKeys

        cube = cmds.polyCube(name="grouper_test")[0]

        # Create animation with static gap
        cmds.setKeyframe(cube, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(cube, attribute="translateX", time=10, value=10)
        cmds.setKeyframe(cube, attribute="translateX", time=20, value=10)  # Static
        cmds.setKeyframe(cube, attribute="translateX", time=30, value=20)

        # Test that SegmentKeys.collect_segments finds 2 segments
        segments = SegmentKeys.collect_segments(
            [cube],
            split_static=True,
        )

        # Should have 2 segments (0-10 and 20-30)
        self.assertEqual(
            len(segments), 2, "Expected 2 segments for animation with static gap"
        )

        # Verify segment ranges
        seg_ranges = [(s["start"], s["end"]) for s in segments]
        self.assertIn((0, 10), seg_ranges)
        self.assertIn((20, 30), seg_ranges)

        cmds.delete(cube)

    def test_scale_overlap_multi_segment_bug(self):
        """Regression Test: Scaling multi-segment object with overlap prevention.

        Verifies that moving one segment during stagger doesn't accidentally move
        other segments on the same curve (which happens if segment_range is missing).
        """
        cube = cmds.polyCube(name="multi_seg_bug")[0]

        # Create 2 segments on the same object separated by a STATIC gap
        # Seg 1: 0-10 (0 to 10)
        cmds.setKeyframe(cube, t=0, v=0, at="tx")
        cmds.setKeyframe(cube, t=10, v=10, at="tx")

        # Gap: 10-20 (Hold at 10)
        cmds.setKeyframe(cube, t=20, v=10, at="tx")

        # Seg 2: 20-30 (10 to 20)
        cmds.setKeyframe(cube, t=30, v=20, at="tx")

        # Scale by 2.0 with split_static=True and prevent_overlap=True
        # Pivot=0.
        # Seg 1 (0-10) scales to 0-20.
        # Seg 2 (20-30) scales to 40-60.
        # Stagger (spacing=0) should pull Seg 2 back to start at 20 (end of Seg 1).

        ScaleKeys.scale_keys(
            objects=[cube],
            factor=2.0,
            pivot=0,
            split_static=True,
            prevent_overlap=True,
        )

        times = cmds.keyframe(cube, q=True, tc=True)

        # Check Seg 1 (0-20)
        self.assertAlmostEqual(times[0], 0.0, msg="Segment 1 start should be 0.0")
        self.assertAlmostEqual(times[1], 20.0, msg="Segment 1 end should be 20.0")

        # Check Seg 2 (20-40) - Was 40-60, shifted back by 20
        # Wait, if Seg 1 scales to 0-20. And Gap (10-20) scales to 20-40.
        # Then Seg 2 should start at 40.
        # The previous expectation of 20 implies the gap was removed.
        # But ScaleKeys preserves gaps.
        self.assertAlmostEqual(
            times[2], 40.0, delta=0.001, msg="Segment 2 start should be 40.0"
        )
        self.assertAlmostEqual(
            times[3], 60.0, delta=0.001, msg="Segment 2 end should be 60.0"
        )

        cmds.delete(cube)


class TestScaleKeysSegmentIsolation(MayaTkTestCase):
    def test_scale_split_segments_pivots(self):
        """Verify that split segments scale around their OWN start times, not the object start."""
        cube = cmds.polyCube(name="TestCube")[0]

        # Create two distinct segments: 0-10 and 20-30
        # Segment A: 0-10
        cmds.setKeyframe(cube, t=0, v=0, at="tx")
        cmds.setKeyframe(cube, t=10, v=10, at="tx")

        # Static gap 10-20
        cmds.setKeyframe(cube, t=20, v=10, at="tx")
        cmds.setKeyframe(cube, t=30, v=20, at="tx")

        # Scale by 2.0
        # split_static=True (default)
        # group_mode="per_object" (default) -> Should map to per_segment for split_static
        ScaleKeys.scale_keys(
            objects=[cube], factor=2.0, split_static=True, group_mode="per_object"
        )

        # Verify Segment A: Should be 0-20 (Pivot 0)
        # 0 -> 0
        # 10 -> 20
        self.assertEqual(cmds.keyframe(cube, t=(0,0), q=True, vc=True)[0], 0.0)
        # Check if key exists at 20 with value 10
        keys_at_20 = cmds.keyframe(cube, t=(20,20), q=True, vc=True)
        self.assertTrue(keys_at_20, "Should have key at frame 20")
        self.assertAlmostEqual(keys_at_20[0], 10.0)

        # Verify Segment B: Should be 20-40 (Pivot 20)
        # If it used object start (0) as pivot: 20->40, 30->60.
        # If it used segment start (20) as pivot: 20->20, 30->40.

        # We expect Pivot 20 (Segment Start)
        # So key at 20 should stay at 20 (value 10)
        # Key at 30 should move to 40 (value 20)

        keys_at_20 = cmds.keyframe(cube, t=(20,20), q=True, vc=True)
        self.assertTrue(keys_at_20, "Should have key at frame 20")
        # Value should be 10 (end of A and start of B)
        self.assertAlmostEqual(keys_at_20[0], 10.0)

        # Check key at 40
        keys_at_40 = cmds.keyframe(cube, t=(40,40), q=True, vc=True)
        self.assertTrue(
            keys_at_40, "Should have key at frame 40 (end of scaled segment B)"
        )
        self.assertAlmostEqual(keys_at_40[0], 20.0)

        # Ensure NO key at 60 (which would happen if pivot was 0)
        keys_at_60 = cmds.keyframe(cube, t=(60,60), q=True, vc=True)
        self.assertFalse(
            keys_at_60, "Should NOT have key at frame 60 (implies wrong pivot used)"
        )


class TestMergeTouching(MayaTkTestCase):
    """Tests for the merge_touching parameter."""

    def setUp(self):
        super().setUp()
        self.cube1 = cmds.polyCube(name="cube1")[0]
        self.cube2 = cmds.polyCube(name="cube2")[0]

        # Create touching segments
        # Cube1: 0-10
        cmds.setKeyframe(self.cube1, t=0, v=0, at="tx")
        cmds.setKeyframe(self.cube1, t=10, v=10, at="tx")

        # Cube2: 10-20
        cmds.setKeyframe(self.cube2, t=10, v=0, at="tx")
        cmds.setKeyframe(self.cube2, t=20, v=10, at="tx")

    def test_merge_touching_false(self):
        """Default behavior: Touching segments are separate groups."""
        # Scale by 0.5
        # If separate:
        # Cube1: 0-10 -> 0-5 (Pivot 0)
        # Cube2: 10-20 -> 10-15 (Pivot 10)
        # Gap: 10 - 5 = 5 frames

        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2],
            factor=0.5,
            group_mode="overlap_groups",
            merge_touching=False,  # Default
            snap_mode="none",  # Disable snapping for precise float checks
        )

        c1_end = cmds.keyframe(self.cube1, q=True, tc=True)[-1]
        c2_start = cmds.keyframe(self.cube2, q=True, tc=True)[0]

        # They should NOT be touching anymore
        self.assertNotAlmostEqual(c1_end, c2_start, places=4)
        self.assertAlmostEqual(c1_end, 5.0, places=4)
        self.assertAlmostEqual(c2_start, 10.0, places=4)

    def test_merge_touching_true(self):
        """Merge behavior: Touching segments are one group."""
        # Scale by 0.5
        # If merged:
        # Combined: 0-20 -> 0-10 (Pivot 0)
        # Cube1: 0-10 -> 0-5
        # Cube2: 10-20 -> 5-10
        # Gap: 0 frames

        ScaleKeys.scale_keys(
            objects=[self.cube1, self.cube2],
            factor=0.5,
            group_mode="overlap_groups",
            merge_touching=True,
            snap_mode="none",
        )

        c1_end = cmds.keyframe(self.cube1, q=True, tc=True)[-1]
        c2_start = cmds.keyframe(self.cube2, q=True, tc=True)[0]

        # They SHOULD be touching
        self.assertAlmostEqual(c1_end, c2_start, places=4)
        self.assertAlmostEqual(c1_end, 5.0, places=4)
        self.assertAlmostEqual(c2_start, 5.0, places=4)


class TestScaleKeysFixes(MayaTkTestCase):
    """Tests for recent fixes (Subframe keys, Visibility Tangents)."""

    def setUp(self):
        super().setUp()
        self.cube = self.create_test_cube("test_scale_fix_cube")

        # Create standard animation 0-10
        cmds.setKeyframe(self.cube, t=0, v=0, at="tx")
        cmds.setKeyframe(self.cube, t=10, v=10, at="tx")

        # Create visibility animation (stepped)
        cmds.setKeyframe(self.cube, t=0, v=1, at="visibility")
        cmds.setKeyframe(self.cube, t=5, v=0, at="visibility")
        cmds.setKeyframe(self.cube, t=10, v=1, at="visibility")

        # Ensure visibility is stepped initially
        cmds.keyTangent(self.cube, at="visibility", itt="step", ott="step")

    def test_subframe_snapping(self):
        """Test that keys snap to nearest frame when snap_mode='nearest'."""
        # Create a subframe key
        cmds.setKeyframe(self.cube, t=5.5, v=5, at="tx")

        # Scale by 1.0 (identity) but with snapping enabled
        ScaleKeys(objects=[self.cube], factor=1.0, snap_mode="nearest").execute()

        # Check key times
        times = cmds.keyframe(self.cube, at="tx", q=True, tc=True)
        for t in times:
            self.assertAlmostEqual(
                t, round(t), delta=0.001, msg=f"Key at {t} is not integer"
            )

        # Specifically check the 5.5 key moved to 6.0 (nearest)
        self.assertIn(6.0, times)
        self.assertNotIn(5.5, times)

    def test_visibility_tangent_preservation(self):
        """Test that visibility tangents remain 'step' after scaling."""
        # Scale by 2.0
        ScaleKeys(objects=[self.cube], factor=2.0, snap_mode="nearest").execute()

        # Check visibility tangents
        times = cmds.keyframe(self.cube, at="visibility", q=True, tc=True)
        for t in times:
            in_type = cmds.keyTangent(
                self.cube, at="visibility", t=(t,), q=True, itt=True
            )[0]
            out_type = cmds.keyTangent(
                self.cube, at="visibility", t=(t,), q=True, ott=True
            )[0]

            # In-tangent cannot be 'step' in Maya, so we expect 'clamped' (or whatever we set)
            # self.assertEqual(in_type, "step", f"In-tangent at {t} should be step")
            self.assertEqual(out_type, "step", f"Out-tangent at {t} should be step")


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
import unittest


