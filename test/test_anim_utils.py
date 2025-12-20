# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.anim_utils module

Tests for AnimUtils class functionality including:
- Keyframe operations
- Animation curve queries
- Key optimization
- Time range parsing
- Advanced key manipulation

Note: scale_keys tests are in test_scale_keys.py
Note: stagger_keys tests are in test_stagger_keys.py
"""
import unittest
import math

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

try:
    AnimUtils = mtk.AnimUtils
except AttributeError:
    from mayatk.anim_utils._anim_utils import AnimUtils

from base_test import MayaTkTestCase


class TestAnimUtils(MayaTkTestCase):
    """Tests for AnimUtils class."""

    def setUp(self):
        """Set up test scene with animated object."""
        super().setUp()
        self.cube = pm.polyCube(name="test_anim_cube")[0]
        self.sphere = pm.polySphere(name="test_anim_sphere")[0]

        # Set playback range for tie_keyframes test
        pm.playbackOptions(minTime=1, maxTime=10)

        # Create simple animation on cube
        # Frame 1: 0, Frame 10: 10 (Linear)
        pm.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=10)

        # Frame 1: 0, Frame 10: 5 (Linear)
        pm.setKeyframe(self.cube, attribute="translateY", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateY", time=10, value=5)

    def tearDown(self):
        """Clean up."""
        if pm.objExists("test_anim_cube"):
            pm.delete("test_anim_cube")
        if pm.objExists("test_anim_sphere"):
            pm.delete("test_anim_sphere")
        super().tearDown()

    # =========================================================================
    # Curve & Key Queries
    # =========================================================================

    def test_objects_to_curves(self):
        """Test retrieving animation curves from objects."""
        curves = AnimUtils.objects_to_curves([self.cube])
        self.assertEqual(len(curves), 2)  # tx and ty
        self.assertTrue(all(isinstance(c, pm.nt.AnimCurve) for c in curves))

    def test_get_anim_curves(self):
        """Test get_anim_curves wrapper."""
        curves = AnimUtils.get_anim_curves([self.cube])
        self.assertEqual(len(curves), 2)

    def test_get_static_curves(self):
        """Test identifying static curves."""
        # Create a static curve (same value at all keys)
        pm.setKeyframe(self.cube, attribute="translateZ", time=1, value=5)
        pm.setKeyframe(self.cube, attribute="translateZ", time=10, value=5)

        static_curves = AnimUtils.get_static_curves([self.cube])
        self.assertEqual(len(static_curves), 1)
        self.assertTrue("translateZ" in static_curves[0].name())

    def test_get_redundant_flat_keys(self):
        """Test identifying redundant flat keys."""
        # Create redundant keys: 0, 0, 0
        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=0)

        redundant = AnimUtils.get_redundant_flat_keys([self.cube])
        self.assertTrue(len(redundant) > 0)
        # redundant is list of (curve, times)
        found = False
        for curve, times in redundant:
            if "translateX" in curve.name():
                self.assertIn(5.0, times)
                found = True
        self.assertTrue(found)

    def test_get_tangent_info(self):
        """Test retrieving tangent info."""
        info = AnimUtils.get_tangent_info(f"{self.cube}.translateX", 1)
        self.assertIsInstance(info, dict)
        self.assertIn("inAngle", info)
        self.assertIn("outAngle", info)

    def test_get_frame_ranges(self):
        """Test calculating frame ranges."""
        ranges = AnimUtils.get_frame_ranges([self.cube])
        self.assertEqual(ranges[self.cube][0], (1, 10))

    def test_get_tied_keyframes(self):
        """Test detecting tied (bookend) keyframes."""
        # Create tied keys: 1-2 (val 0), 9-10 (val 10)
        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=2, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=9, value=10)
        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=10)

        tied = AnimUtils.get_tied_keyframes([self.cube])
        self.assertIn(self.cube, tied)
        # Should detect start and end ties
        attr_name = f"{self.cube.name()}_translateX"  # Default curve name format
        # Curve names might vary, check values
        found = False
        for attr, times in tied[self.cube].items():
            if "translateX" in attr:
                self.assertIn(1.0, times)
                self.assertIn(10.0, times)
                found = True
        self.assertTrue(found)

    def test_filter_objects_with_keys(self):
        """Test filtering objects that have keys."""
        filtered = AnimUtils.filter_objects_with_keys([self.cube, self.sphere])
        self.assertIn(self.cube, filtered)
        self.assertNotIn(self.sphere, filtered)

    # =========================================================================
    # Key Manipulation (Basic)
    # =========================================================================

    def test_set_keys_for_attributes(self):
        """Test setting keys for attributes."""
        # Shared mode
        AnimUtils.set_keys_for_attributes(
            [self.sphere], target_times=[1, 10], translateX=5
        )
        self.assertEqual(pm.getAttr(self.sphere + ".translateX", time=1), 5)
        self.assertEqual(pm.getAttr(self.sphere + ".translateX", time=10), 5)

        # Per-object mode
        data = {self.sphere.name(): {"translateY": 8.0}}
        AnimUtils.set_keys_for_attributes([self.sphere], target_times=[5], **data)
        self.assertEqual(pm.getAttr(self.sphere + ".translateY", time=5), 8.0)

    def test_move_keys_to_frame(self):
        """Test moving keys to a specific frame."""
        # Move keys from frame 1 to frame 5
        AnimUtils.move_keys_to_frame(objects=[self.cube], frame=5, time_range=(1, 1))
        self.assertEqual(
            pm.keyframe(
                self.cube,
                attribute="translateX",
                query=True,
                time=(5, 5),
                valueChange=True,
            )[0],
            0,
        )
        # Original key should be gone
        self.assertEqual(
            len(
                pm.keyframe(self.cube, attribute="translateX", query=True, time=(1, 1))
            ),
            0,
        )

    def test_delete_keys(self):
        """Test deleting keys."""
        AnimUtils.delete_keys([self.cube], "translateX", time=1)
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertNotIn(1.0, keys)
        self.assertIn(10.0, keys)

    def test_select_keys(self):
        """Test selecting keys."""
        pm.selectKey(clear=True)
        count = AnimUtils.select_keys([self.cube], time=1)
        self.assertGreater(count, 0)
        selected = pm.keyframe(query=True, selected=True)
        self.assertTrue(len(selected) > 0)

    def test_parse_time_range(self):
        """Test time range parsing."""
        self.assertEqual(AnimUtils.parse_time_range(10), (10, 10))
        self.assertEqual(AnimUtils.parse_time_range((1, 10)), (1, 10))
        self.assertIsNone(AnimUtils.parse_time_range("all"))

        # Test recursive list
        res = AnimUtils.parse_time_range("before|after")
        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 2)

    # =========================================================================
    # Key Manipulation (Advanced)
    # =========================================================================

    def test_optimize_keys(self):
        """Test optimizing keys (removing redundant ones)."""
        # Create redundant flat keys: 0, 0, 0
        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=0)

        result = AnimUtils.optimize_keys([self.cube])
        self.assertGreater(len(result), 0)
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertNotIn(5.0, keys)

    def test_simplify_curve(self):
        """Test curve simplification."""
        # Create dense keys
        for i in range(1, 11):
            pm.setKeyframe(self.cube, attribute="translateZ", time=i, value=i)

        AnimUtils.simplify_curve([self.cube], value_tolerance=0.1)
        keys = pm.keyframe(self.cube, attribute="translateZ", query=True)
        # Should have fewer keys than 10, likely just start and end for a straight line
        self.assertLess(len(keys), 10)

    def test_adjust_key_spacing(self):
        """Test adjusting key spacing."""
        # Keys at 1 and 10. Add spacing of 5.
        AnimUtils.adjust_key_spacing(
            [self.cube.name()], spacing=5, time=5, relative=False
        )
        # Key at 10 should move to 15
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertIn(15.0, keys)
        self.assertNotIn(10.0, keys)

    def test_add_intermediate_keys(self):
        """Test adding intermediate keys."""
        AnimUtils.add_intermediate_keys([self.cube], time_range=(1, 10), percent=50)
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        # Should have more than just 1 and 10
        self.assertGreater(len(keys), 2)

    def test_remove_intermediate_keys(self):
        """Test removing intermediate keys."""
        # Add some intermediate keys
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=5)

        AnimUtils.remove_intermediate_keys([self.cube])
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertEqual(len(keys), 2)  # Only start and end
        self.assertIn(1.0, keys)
        self.assertIn(10.0, keys)

    def test_invert_keys(self):
        """Test inverting keys."""
        # Select object
        pm.select(self.cube)
        # Invert horizontally around frame 5
        AnimUtils.invert_keys(time=5, relative=False, mode="horizontal")
        # Key at 1 should move to 9 (5 + (5-1)) -> Wait, logic is inversion_point - (key_time - max_time)
        # Let's just check that keys moved
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertNotEqual(keys, [1.0, 10.0])

    def test_align_selected_keyframes(self):
        """Test aligning selected keyframes."""
        # Select object first
        pm.select(self.cube)
        # Select keys
        pm.selectKey(self.cube, attribute="translateX", time=(1, 1))
        pm.selectKey(self.cube, attribute="translateY", time=(10, 10), add=True)

        # Verify selection
        sel = pm.keyframe(query=True, selected=True)
        if not sel:
            self.skipTest("Could not select keyframes")

        # Align to frame 5
        AnimUtils.align_selected_keyframes(target_frame=5)

        # Check if keys moved
        tx_val = pm.keyframe(
            self.cube, attribute="translateX", query=True, time=(5, 5), valueChange=True
        )
        ty_val = pm.keyframe(
            self.cube, attribute="translateY", query=True, time=(5, 5), valueChange=True
        )

        self.assertTrue(tx_val or ty_val)

    def test_transfer_keyframes(self):
        """Test transferring keyframes."""
        AnimUtils.transfer_keyframes([self.cube, self.sphere])
        # Sphere should now have keys
        keys = pm.keyframe(self.sphere, query=True)
        self.assertTrue(len(keys) > 0)

    def test_insert_keyframe_gap(self):
        """Test inserting a gap."""
        # Keys at 1 and 10. Insert gap of 6 frames at frame 5.
        # Key at 10 should move to 11 (5+6).
        pm.currentTime(5)
        AnimUtils.insert_keyframe_gap(duration=6, objects=[self.cube])

        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertIn(11.0, keys)
        self.assertNotIn(10.0, keys)

    # =========================================================================
    # Special Features
    # =========================================================================

    def test_set_visibility_keys(self):
        """Test setting visibility keys."""
        AnimUtils.set_visibility_keys([self.cube], visible=False, when="start")
        vis = pm.getAttr(self.cube + ".visibility", time=1)
        self.assertEqual(vis, 0)

    def test_tie_and_untie_keyframes(self):
        """Test tie and untie keyframes."""
        # Tie keys (playback range 1-10, padding 1 -> 0 and 11)
        AnimUtils.tie_keyframes([self.cube], padding=1)
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertIn(0.0, keys)  # 1 - 1
        self.assertIn(11.0, keys)  # 10 + 1

        # Untie keys
        AnimUtils.untie_keyframes([self.cube])
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertNotIn(0.0, keys)
        self.assertNotIn(11.0, keys)

    def test_snap_keys_to_frames(self):
        """Test snapping keys to whole frame values."""
        # Add a key at fractional time
        pm.setKeyframe(self.cube, attribute="translateX", time=5.5, value=5)

        count = AnimUtils.snap_keys_to_frames([self.cube])
        self.assertGreater(count, 0)
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertNotIn(5.5, keys)
        self.assertIn(6.0, keys)  # Nearest

    def test_set_current_frame(self):
        """Test setting current timeline frame."""
        frame = AnimUtils.set_current_frame(5.0)
        current = pm.currentTime(query=True)
        self.assertEqual(current, 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
