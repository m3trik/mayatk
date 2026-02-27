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
import os

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

from base_test import MayaTkTestCase, skipUnlessExtended


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

    def test_get_static_curves_default_value(self):
        """Test identifying static curves at their default value.

        A curve holding the attribute's default value (e.g. translateZ=0)
        is safe to delete because removing it leaves the attribute at the
        same resting value.
        """
        pm.setKeyframe(self.cube, attribute="translateZ", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateZ", time=10, value=0)

        static_curves = AnimUtils.get_static_curves([self.cube])
        self.assertEqual(len(static_curves), 1)
        self.assertTrue("translateZ" in static_curves[0].name())

    def test_get_static_curves_preserves_nondefault(self):
        """Verify static curves holding non-default values are NOT returned.

        Bug: get_static_curves deleted curves where all values were
        identical, regardless of whether the constant value differed from
        the attribute's default.  When combined with delete_inputs=True
        in SmartBake, this caused baked-constraint positions to revert
        to zero.
        Fixed: 2026-02-24
        """
        # translateZ default is 0.0; constant 5.0 is non-default
        pm.setKeyframe(self.cube, attribute="translateZ", time=1, value=5)
        pm.setKeyframe(self.cube, attribute="translateZ", time=10, value=5)

        static_curves = AnimUtils.get_static_curves([self.cube])
        # Should NOT be flagged — deleting it would change the object's
        # resting position from 5 back to 0.
        tz_curves = [c for c in static_curves if "translateZ" in str(c)]
        self.assertEqual(
            len(tz_curves),
            0,
            "Static curve at non-default value should be preserved",
        )

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

    def test_set_keys_for_attributes_preserves_stepped_tangents(self):
        """Verify set_keys_for_attributes doesn't break existing stepped tangents.

        Bug: pm.setKeyframe resets tangent types to the scene default,
        overwriting stepped tangents when pasting values onto existing keys.
        Fixed: 2026-02-26
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # The cube already has keys at 1 and 10 from setUp.
        # Set them to stepped tangents.
        cmds.keyTangent(plug, time=(1, 1), edit=True, outTangentType="step")
        cmds.keyTangent(plug, time=(10, 10), edit=True, outTangentType="step")

        # Overwrite the value at frame 1 via set_keys_for_attributes
        AnimUtils.set_keys_for_attributes(
            [self.cube], target_times=[1], translateX=99
        )

        # The value must be updated
        self.assertAlmostEqual(pm.getAttr(plug, time=1), 99, places=3)

        # Stepped out-tangent must still be "step"
        ott = cmds.keyTangent(plug, query=True, time=(1, 1), outTangentType=True)
        self.assertEqual(ott[0], "step", "Stepped tangent was overwritten by set_keys_for_attributes")

    def test_copy_keys_channel_box(self):
        """Test copy_keys with channel_box mode captures selected CB attributes."""
        import maya.cmds as cmds

        # Select the cube and highlight an attribute in the channel box
        pm.select(self.cube)
        # Channel box mode requires attributes to be selected in the CB;
        # when nothing is highlighted the result should be empty.
        result = AnimUtils.copy_keys(mode="channel_box")
        # Without a live channel box selection this should be empty
        self.assertIsInstance(result, dict)

    def test_copy_keys_current_frame(self):
        """Test copy_keys with current_frame mode reads animated values at the current time."""
        import maya.cmds as cmds

        pm.select(self.cube)
        cmds.currentTime(1)
        result = AnimUtils.copy_keys(mode="current_frame")
        self.assertIn(str(self.cube), result)
        obj_data = result[str(self.cube)]
        self.assertAlmostEqual(obj_data["translateX"], 0.0, places=3)

        cmds.currentTime(10)
        result = AnimUtils.copy_keys(mode="current_frame")
        obj_data = result[str(self.cube)]
        self.assertAlmostEqual(obj_data["translateX"], 10.0, places=3)

    def test_paste_keys_basic(self):
        """Test paste_keys sets values at the current time."""
        import maya.cmds as cmds

        pm.select(self.sphere)
        # Give sphere a key so the attribute exists on the curve
        pm.setKeyframe(self.sphere, attribute="translateX", time=1, value=0)

        copied = {str(self.sphere): {"translateX": 42.0}}
        cmds.currentTime(5)
        count = AnimUtils.paste_keys(
            objects=[self.sphere], copied_data=copied
        )
        self.assertEqual(count, 1)
        self.assertAlmostEqual(
            pm.getAttr(f"{self.sphere}.translateX", time=5), 42.0, places=3
        )

    def test_paste_keys_preserves_existing_stepped_tangent(self):
        """Verify paste_keys does not overwrite existing stepped tangent types.

        Bug: pm.setKeyframe resets tangent types to scene default, destroying
        stepped tangents when pasting onto an existing key.
        Fixed: 2026-02-26
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Set frame 1 out-tangent to stepped
        cmds.keyTangent(plug, time=(1, 1), edit=True, outTangentType="step")

        # Paste a new value onto the existing key at frame 1
        copied = {str(self.cube): {"translateX": 77.0}}
        AnimUtils.paste_keys(
            objects=[self.cube], copied_data=copied, target_time=1
        )

        # Value should be updated
        self.assertAlmostEqual(pm.getAttr(plug, time=1), 77.0, places=3)

        # Stepped tangent must survive
        ott = cmds.keyTangent(plug, query=True, time=(1, 1), outTangentType=True)
        self.assertEqual(ott[0], "step", "Stepped tangent was overwritten by paste_keys")

    def test_paste_keys_inherits_stepped_tangent_for_new_key(self):
        """Verify paste_keys inherits tangent type from the preceding key when inserting new keys.

        When no key exists at the target time, the tangent type should be
        inherited from the nearest preceding key so that stepped curves
        stay stepped.
        Fixed: 2026-02-26
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Set both existing keys (1, 10) to stepped
        cmds.keyTangent(plug, edit=True, outTangentType="step")
        cmds.keyTangent(plug, edit=True, inTangentType="step")

        # Paste at frame 5, where no key exists yet
        copied = {str(self.cube): {"translateX": 55.0}}
        AnimUtils.paste_keys(
            objects=[self.cube], copied_data=copied, target_time=5
        )

        # New key must exist
        keys_at_5 = cmds.keyframe(plug, query=True, time=(5, 5), timeChange=True)
        self.assertTrue(keys_at_5, "Key was not created at frame 5")

        # Tangent types should be inherited as stepped
        ott = cmds.keyTangent(plug, query=True, time=(5, 5), outTangentType=True)
        itt = cmds.keyTangent(plug, query=True, time=(5, 5), inTangentType=True)
        self.assertEqual(ott[0], "step", "Out-tangent not inherited as stepped")
        self.assertEqual(itt[0], "step", "In-tangent not inherited as stepped")

    def test_paste_keys_name_matching(self):
        """Verify paste_keys matches objects by short name when long path differs."""
        import maya.cmds as cmds

        pm.setKeyframe(self.sphere, attribute="translateX", time=1, value=0)

        # Store using short name
        copied = {self.sphere.nodeName(): {"translateX": 33.0}}
        cmds.currentTime(1)
        count = AnimUtils.paste_keys(
            objects=[self.sphere], copied_data=copied
        )
        self.assertEqual(count, 1)
        self.assertAlmostEqual(
            pm.getAttr(f"{self.sphere}.translateX", time=1), 33.0, places=3
        )

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

    def test_move_keys_to_frame_align_end(self):
        """Test moving keys so the last key aligns to the target frame.

        setUp creates keys at frame 1 and 10 on translateX.
        With align='end', the key at frame 10 should land on the target
        frame (20) and the key at frame 1 should shift to 11.
        """
        AnimUtils.move_keys_to_frame(
            objects=[self.cube], frame=20, align="end"
        )
        # Last key (was 10) should now be at 20
        keys_at_20 = pm.keyframe(
            self.cube, attribute="translateX", query=True, time=(20, 20), valueChange=True
        )
        self.assertTrue(keys_at_20, "Last key was not moved to frame 20")
        self.assertAlmostEqual(keys_at_20[0], 10.0, places=3)

        # First key (was 1) should now be at 11 (offset = 20 - 10 = +10)
        keys_at_11 = pm.keyframe(
            self.cube, attribute="translateX", query=True, time=(11, 11), valueChange=True
        )
        self.assertTrue(keys_at_11, "First key was not moved to frame 11")
        self.assertAlmostEqual(keys_at_11[0], 0.0, places=3)

        # Original positions should be empty
        self.assertFalse(
            pm.keyframe(self.cube, attribute="translateX", query=True, time=(1, 1)),
            "Original key at frame 1 still exists",
        )
        self.assertFalse(
            pm.keyframe(self.cube, attribute="translateX", query=True, time=(10, 10)),
            "Original key at frame 10 still exists",
        )

    def test_move_keys_to_frame_align_start_default(self):
        """Verify default align='start' moves the first key to the target frame."""
        AnimUtils.move_keys_to_frame(
            objects=[self.cube], frame=20
        )
        # First key (was 1) should now be at 20
        keys_at_20 = pm.keyframe(
            self.cube, attribute="translateX", query=True, time=(20, 20), valueChange=True
        )
        self.assertTrue(keys_at_20, "First key was not moved to frame 20")
        self.assertAlmostEqual(keys_at_20[0], 0.0, places=3)

        # Last key (was 10) should now be at 29 (offset = 20 - 1 = +19)
        keys_at_29 = pm.keyframe(
            self.cube, attribute="translateX", query=True, time=(29, 29), valueChange=True
        )
        self.assertTrue(keys_at_29, "Last key was not moved to frame 29")

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
        """Test inserting an exact gap using adjust_key_spacing with exact_gap=True.

        Keys at 1 and 10. Insert exact gap of 6 frames at frame 5.
        The first key after 5 is at 10. With exact_gap, it should move to 5+6=11.
        """
        pm.currentTime(5)
        AnimUtils.adjust_key_spacing(
            [self.cube],
            spacing=6,
            time=None,  # Auto = current time = 5
            relative=False,
            exact_gap=True,
        )

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

    def test_tie_keyframes_preserves_stepped_tangents(self):
        """Verify tie_keyframes preserves stepped tangent types on bookend keys.

        Bug: cmds.setKeyframe() creates new keys with the default tangent type
        (usually 'auto'), corrupting curves that are entirely stepped (e.g.
        visibility, boolean attributes). After the fix, fully-stepped curves
        have their bookend keys set back to 'step' (or 'stepnext').
        Fixed: 2026-02-24
        """
        import maya.cmds as cmds

        # Create a visibility-style curve: entirely stepped
        pm.cutKey(self.cube, attribute="visibility", clear=True)
        pm.setKeyframe(self.cube, attribute="visibility", time=3, value=1)
        pm.setKeyframe(self.cube, attribute="visibility", time=5, value=0)
        pm.setKeyframe(self.cube, attribute="visibility", time=8, value=1)
        vis_curve = cmds.listConnections(
            str(self.cube) + ".visibility",
            type="animCurve",
            source=True,
            destination=False,
        )
        self.assertTrue(vis_curve, "No anim curve on visibility")
        vis_curve = vis_curve[0]
        cmds.keyTangent(vis_curve, outTangentType="step")

        # Verify baseline: all out-tangents are step
        out_before = cmds.keyTangent(vis_curve, q=True, outTangentType=True)
        self.assertTrue(
            all(t == "step" for t in out_before),
            f"Baseline not all-stepped: {out_before}",
        )

        # Tie keyframes (playback range 1-10 => bookend at 1 and 10)
        AnimUtils.tie_keyframes([self.cube])

        # After tying, ALL out-tangent types on the vis curve must still be step
        out_after = cmds.keyTangent(vis_curve, q=True, outTangentType=True)
        self.assertTrue(
            all(t == "step" for t in out_after),
            f"Stepped tangents corrupted after tie_keyframes: {out_after}",
        )

    def test_optimize_keys_preserves_stepped_keys_in_flat_segments(self):
        """Verify get_redundant_flat_keys removes interior flat keys and
        restores stepped tangent types on the remaining boundary keys.

        Bug: get_redundant_flat_keys either (a) refused to remove interior
        stepped keys, disabling cleanup entirely on stepped curves, or
        (b) removed them but let Maya auto-tangent change boundary keys
        from 'step' to 'auto'/'fixed', corrupting the curve.
        Fixed: 2026-02-25 (cleanup allowed, step_keys restores boundaries)
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Flat segment: all values 0.0, all keys stepped
        for t in [1, 3, 5, 7, 10]:
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=0.0)
        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Set all keys to stepped
        cmds.keyTangent(curve, outTangentType="step")

        keys_before = cmds.keyframe(curve, q=True, timeChange=True)

        # Remove redundant flat keys
        AnimUtils.get_redundant_flat_keys([self.cube], remove=True, as_strings=True)

        keys_after = cmds.keyframe(curve, q=True, timeChange=True)
        out_types = cmds.keyTangent(curve, q=True, outTangentType=True)

        # Interior keys should be removed (cleanup works)
        self.assertLess(
            len(keys_after),
            len(keys_before),
            f"No keys were removed — cleanup is broken. "
            f"Before: {keys_before}, After: {keys_after}",
        )

        # First and last boundary keys must remain
        self.assertIn(1.0, keys_after, "First boundary key removed")
        self.assertIn(10.0, keys_after, "Last boundary key removed")

        # Remaining boundary keys must retain stepped tangent type
        for t, ot in zip(keys_after, out_types):
            if t != keys_after[-1]:  # Last key's out-tangent doesn't matter
                self.assertEqual(
                    ot,
                    "step",
                    f"Boundary key at t={t} lost step tangent: {ot}",
                )

    def test_snap_keys_to_frames_preserves_stepped_tangents(self):
        """Verify snap_keys_to_frames preserves stepped tangent types.

        Bug: When fractional keys were snapped to whole frames, their tangent
        types could be corrupted — stepped tangents were replaced with
        auto/fixed, causing the character to float between poses instead
        of snapping.
        Fixed: 2026-02-25
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Keys at fractional times with stepped tangents
        for t, v in [(1.0, 0.0), (3.7, 5.0), (6.3, 5.0), (10.0, 10.0)]:
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=v)
        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)
        cmds.keyTangent(curve, outTangentType="step")

        step_count_before = sum(
            1
            for ot in cmds.keyTangent(curve, q=True, outTangentType=True)
            if ot == "step"
        )

        AnimUtils.snap_keys_to_frames([self.cube])

        # All keys should be on whole frames
        keys_after = cmds.keyframe(curve, q=True, timeChange=True)
        for k in keys_after:
            self.assertEqual(k, round(k), f"Key at {k} not on whole frame")

        # All outTangents must still be stepped
        out_after = cmds.keyTangent(curve, q=True, outTangentType=True)
        step_count_after = sum(1 for ot in out_after if ot == "step")
        self.assertEqual(
            step_count_after,
            step_count_before,
            f"Stepped tangent count changed: {step_count_before} -> {step_count_after}. "
            f"Types after snap: {out_after}",
        )

    def test_tie_keyframes_preserves_partial_stepped_tangents(self):
        """Verify tie_keyframes preserves stepped tangents on partially-stepped curves.

        When any key on a curve has a stepped tangent, tie_keyframes uses
        step_keys to re-step ALL keys (including new bookend keys).  This
        is safe because curves with even one stepped key are typically
        intended to be fully stepped (visibility, boolean, enum attributes).
        Fixed: 2026-02-25
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Create curve with most keys stepped, but first and last are auto
        pm.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        for t in range(2, 10):
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=float(t))
        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=10)

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Set interior keys (2-9) to stepped, leave 1 and 10 as auto
        for t in range(2, 10):
            cmds.keyTangent(curve, time=(t, t), outTangentType="step")

        # Tie keyframes — this adds bookend keys at playback range boundaries
        AnimUtils.tie_keyframes([self.cube])

        # After tying, step_keys re-steps entire curve since it had
        # stepped keys.  ALL out-tangents should be 'step'.
        out_after = cmds.keyTangent(curve, q=True, outTangentType=True)
        self.assertTrue(
            all(ot == "step" for ot in out_after),
            f"Expected all out-tangents to be 'step' after tie_keyframes "
            f"on curve with stepped keys: {out_after}",
        )

    def test_snap_keys_to_frames(self):
        """Test snapping keys to whole frame values."""
        # Add a key at fractional time
        pm.setKeyframe(self.cube, attribute="translateX", time=5.5, value=5)

        count = AnimUtils.snap_keys_to_frames([self.cube])
        self.assertGreater(count, 0)
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertNotIn(5.5, keys)
        self.assertIn(6.0, keys)  # Nearest

    def test_snap_keys_to_frames_preserves_values(self):
        """Verify snap_keys_to_frames preserves keyframe values after snap.

        Bug: Previous implementation deleted and recreated keys (~8 cmds per key),
        risking tangent/value loss. Now uses keyframe(edit=True, timeChange=...).
        Fixed: 2026-02-22
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=3.7, value=42.0)
        pm.setKeyframe(self.cube, attribute="translateX", time=8.3, value=-15.5)

        AnimUtils.snap_keys_to_frames([self.cube])

        keys = cmds.keyframe(
            str(self.cube), attribute="translateX", query=True, timeChange=True
        )
        vals = cmds.keyframe(
            str(self.cube), attribute="translateX", query=True, valueChange=True
        )

        # Keys should be at whole frames
        self.assertIn(4.0, keys)
        self.assertIn(8.0, keys)
        # Values must be preserved exactly
        self.assertAlmostEqual(vals[keys.index(4.0)], 42.0, places=3)
        self.assertAlmostEqual(vals[keys.index(8.0)], -15.5, places=3)

    def test_snap_keys_to_frames_multiple_fractional(self):
        """Test snapping multiple fractional keys on the same curve."""
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateY", clear=True)
        for t in [1.1, 2.9, 5.5, 10.2]:
            pm.setKeyframe(self.cube, attribute="translateY", time=t, value=t * 2)

        count = AnimUtils.snap_keys_to_frames([self.cube])
        self.assertEqual(count, 4)

        keys = cmds.keyframe(
            str(self.cube), attribute="translateY", query=True, timeChange=True
        )
        for k in keys:
            self.assertEqual(
                k, round(k), f"Key at {k} is not on a whole frame after snap"
            )

    def test_optimize_keys_returns_strings(self):
        """Verify optimize_keys returns string curve names, not PyNodes.

        Changed from PyNode return to strings to avoid expensive PyNode
        construction per curve. All callers checked — none depend on PyNode type.
        Fixed: 2026-02-22
        """
        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=0)

        result = AnimUtils.optimize_keys([self.cube])
        self.assertGreater(len(result), 0)
        for item in result:
            self.assertIsInstance(
                item, str, f"Expected string, got {type(item).__name__}"
            )

    def test_flat_key_removal_pins_boundary_tangents(self):
        """Verify flat key removal pins boundary tangents to prevent overshoot.

        Bug: After removing interior keys from a flat segment, the
        remaining boundary keys kept auto tangents.  Maya recalculated
        their slopes based on new neighbors, creating overshoot at
        flat-to-non-flat transitions.
        Fixed: 2026-02-24
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Build curve: hold at 0 for frames 1-10, rise to 10 at frame 20
        for t in range(1, 11):
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=20, value=10)

        curve_name = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve_name, "No anim curve found on translateX")

        # Remove redundant flat keys
        AnimUtils.get_redundant_flat_keys([self.cube], remove=True, as_strings=True)

        # After removal, only frames 1, 10, and 20 should remain
        keys = cmds.keyframe(curve_name, query=True, timeChange=True)
        self.assertIn(1.0, keys)
        self.assertIn(10.0, keys)
        self.assertIn(20.0, keys)

        # Boundary tangents should be pinned to linear
        out_type_1 = cmds.keyTangent(
            curve_name, time=(1, 1), query=True, outTangentType=True
        )
        in_type_10 = cmds.keyTangent(
            curve_name, time=(10, 10), query=True, inTangentType=True
        )
        self.assertEqual(out_type_1[0], "linear", "Boundary out-tangent not pinned")
        self.assertEqual(in_type_10[0], "linear", "Boundary in-tangent not pinned")

        # Evaluate mid-flat region - value must be exactly 0 (no overshoot)
        val_at_5 = pm.getAttr(f"{self.cube}.translateX", time=5)
        self.assertAlmostEqual(
            val_at_5, 0.0, places=3, msg="Overshoot in flat region after key removal"
        )

    def test_optimize_keys_preserves_constraint_hold(self):
        """Verify optimize_keys does not delete static curves at non-default values.

        Bug: A constraint holding an object at translateX=5.0 produces a
        baked curve where all values are identical (5.0).
        remove_static_curves=True deleted the curve, losing the held position.
        Fixed: 2026-02-24
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Simulate baked constraint: constant value 5.0 over 10 frames
        for t in range(1, 11):
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=5.0)

        # Run full optimize (including remove_static_curves)
        AnimUtils.optimize_keys(
            [self.cube],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
        )

        # The curve should still exist because 5.0 != default 0.0
        curves = cmds.listConnections(
            f"{self.cube}.translateX", type="animCurve", source=True
        )
        self.assertTrue(
            curves,
            "Static curve at non-default value was deleted by optimize_keys",
        )

        # Value should still be 5.0
        val = pm.getAttr(f"{self.cube}.translateX", time=5)
        self.assertAlmostEqual(val, 5.0, places=3)

    def test_optimize_keys_deletes_static_at_default(self):
        """Verify optimize_keys DOES delete static curves at default values.

        Complementary to test_optimize_keys_preserves_constraint_hold:
        curves at default value (e.g. translateZ=0) are safe to remove.
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateZ", clear=True)

        # Static curve at default value 0.0
        for t in range(1, 11):
            pm.setKeyframe(self.cube, attribute="translateZ", time=t, value=0.0)

        AnimUtils.optimize_keys(
            [self.cube],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
        )

        # Curve should be deleted since value matches default
        curves = cmds.listConnections(
            f"{self.cube}.translateZ", type="animCurve", source=True
        )
        self.assertFalse(
            curves,
            "Static curve at default value should have been deleted",
        )

    def test_smart_bake_preserves_constant_constraint(self):
        """Verify SmartBake + optimize_keys preserves constant constraint positions.

        End-to-end: create a point constraint holding an object at a fixed
        position, smart-bake, and confirm the position is retained.

        Bug: SmartBake deleted both the constraint and its baked static
        curve, causing the object to snap to origin.
        Fixed: 2026-02-24
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        # Create target (static locator) at position (7, 3, -2)
        loc = pm.spaceLocator(name="constraint_target")
        pm.setAttr(loc + ".translateX", 7)
        pm.setAttr(loc + ".translateY", 3)
        pm.setAttr(loc + ".translateZ", -2)

        # Create driven object
        driven = pm.polyCube(name="driven_cube")[0]

        # Constrain driven to target
        constraint = pm.pointConstraint(loc, driven, maintainOffset=False)

        # Verify constraint is working
        self.assertAlmostEqual(pm.getAttr(driven + ".translateX"), 7.0, places=2)
        self.assertAlmostEqual(pm.getAttr(driven + ".translateY"), 3.0, places=2)
        self.assertAlmostEqual(pm.getAttr(driven + ".translateZ"), -2.0, places=2)

        # Smart bake with delete_inputs + optimize_keys
        baker = SmartBake(
            objects=[str(driven)],
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=True,
            use_override_layer=False,
            delete_inputs=True,
        )

        analysis = baker.analyze()
        self.assertTrue(
            any(a.requires_bake for a in analysis.values()),
            "SmartBake should detect the constraint as requiring bake",
        )

        result = baker.bake(analysis)

        # Constraint should be deleted
        self.assertFalse(
            cmds.objExists(str(constraint)),
            "Constraint should be deleted after bake",
        )

        # Position must be preserved despite static curves + delete_inputs
        self.assertAlmostEqual(
            pm.getAttr(driven + ".translateX"),
            7.0,
            places=2,
            msg="Held X position lost after SmartBake",
        )
        self.assertAlmostEqual(
            pm.getAttr(driven + ".translateY"),
            3.0,
            places=2,
            msg="Held Y position lost after SmartBake",
        )
        self.assertAlmostEqual(
            pm.getAttr(driven + ".translateZ"),
            -2.0,
            places=2,
            msg="Held Z position lost after SmartBake",
        )

        # Verify curves still exist for non-default channels
        for attr in ("translateX", "translateY", "translateZ"):
            curves = cmds.listConnections(
                f"{driven}.{attr}", type="animCurve", source=True
            )
            self.assertTrue(
                curves,
                f"Anim curve for {attr} should be preserved (non-default hold value)",
            )

    def test_set_current_frame(self):
        """Test setting current timeline frame."""
        frame = AnimUtils.set_current_frame(5.0)
        current = pm.currentTime(query=True)
        self.assertEqual(current, 5.0)

    def test_smart_bake_preserves_sdk_curves_after_delete_inputs(self):
        """Verify SmartBake doesn't delete baked SDK curves when delete_inputs=True.

        Bug: bakeResults converts SDK curves (animCurveU*) to time-based
        (animCurveT*) in-place, reusing the same node name. delete_inputs
        then deleted those nodes because they were listed as source_nodes,
        destroying all baked animation.
        Fixed: 2026-02-25
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        pm.playbackOptions(minTime=1, maxTime=20)

        # Create SDK driver
        driver = pm.polyCube(name="sdk_driver")[0]
        driven = pm.polyCube(name="sdk_driven")[0]

        # Animate driver.translateX over time
        pm.setKeyframe(driver, attribute="translateX", time=1, value=0)
        pm.setKeyframe(driver, attribute="translateX", time=10, value=5)
        pm.setKeyframe(driver, attribute="translateX", time=20, value=0)

        # Create SDK: driver.tx drives driven.ty
        pm.setDrivenKeyframe(
            driven + ".translateY",
            currentDriver=driver + ".translateX",
            driverValue=0,
            value=0,
        )
        pm.setDrivenKeyframe(
            driven + ".translateY",
            currentDriver=driver + ".translateX",
            driverValue=5,
            value=10,
        )

        # Verify SDK is working
        pm.currentTime(10)
        self.assertAlmostEqual(pm.getAttr(driven + ".translateY"), 10.0, places=1)

        # Verify pre-bake curve type
        curves = (
            cmds.listConnections(
                f"{driven}.translateY", type="animCurve", source=True, destination=False
            )
            or []
        )
        self.assertTrue(curves, "SDK curve should exist")
        self.assertTrue(
            cmds.nodeType(curves[0]).startswith("animCurveU"),
            f"Pre-bake should be SDK type, got {cmds.nodeType(curves[0])}",
        )

        # SmartBake with delete_inputs=True (the bug scenario)
        baker = SmartBake(
            objects=[str(driven)],
            sample_by=1,
            optimize_keys=False,
            delete_inputs=True,
        )
        result = baker.execute()

        # Baked curve should still exist
        curves_after = (
            cmds.listConnections(
                f"{driven}.translateY", type="animCurve", source=True, destination=False
            )
            or []
        )
        self.assertTrue(
            curves_after,
            "Baked curve was deleted by delete_inputs! "
            "bakeResults converts SDK curves in-place.",
        )
        self.assertTrue(
            cmds.nodeType(curves_after[0]).startswith("animCurveT"),
            f"Post-bake should be time-based, got {cmds.nodeType(curves_after[0])}",
        )

        # Baked curve should have time-based keys with varying values
        keys = cmds.keyframe(curves_after[0], q=True, timeChange=True) or []
        vals = cmds.keyframe(curves_after[0], q=True, valueChange=True) or []
        self.assertGreater(len(keys), 1, "Baked curve should have keys")
        self.assertGreater(
            len(set(round(v, 4) for v in vals)),
            1,
            "Baked curve should have varying values",
        )


# =========================================================================
# Real-World FBX Pipeline Tests (extended)
# =========================================================================

# Default path — override via MAYATK_TEST_FBX env var
_DEFAULT_FBX = (
    r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
    r"\_tests\audio_files\C130_FCR_Speedrun_Assembly_copy.fbx"
)


class TestAnimUtilsRealWorld(MayaTkTestCase):
    """Real-world regression tests using production FBX files.

    These tests import an actual animated FBX, run the same operations the
    export pipeline uses (SmartBake, optimize_keys, snap_keys_to_frames,
    tie_keyframes), and verify that no object positions or animation are
    corrupted.

    Gated behind ``--extended`` / ``MAYATK_EXTENDED_TESTS=1``.
    Override the test file via ``MAYATK_TEST_FBX`` env var.

    The FBX is imported **once** in setUpClass and saved as a temp .ma file.
    Each test reopens that .ma (much faster than re-importing a 700 MB FBX).
    """

    fbx_path: str = ""
    _cached_scene: str = ""
    _animated_objects: list = []

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import maya.cmds as cmds
        import random

        cls.fbx_path = os.environ.get("MAYATK_TEST_FBX", _DEFAULT_FBX)
        if not os.path.isfile(cls.fbx_path):
            raise unittest.SkipTest(f"FBX not found: {cls.fbx_path}")

        # Ensure FBX plugin is loaded
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya")

        # Import FBX once and cache as temp .ma
        print(
            f"[realworld] Importing FBX ({os.path.getsize(cls.fbx_path) // (1024*1024)} MB)..."
        )
        cmds.file(new=True, force=True)
        cmds.file(
            cls.fbx_path,
            i=True,
            type="FBX",
            ignoreVersion=True,
            mergeNamespacesOnClash=False,
            options="fbx",
        )
        print("[realworld] FBX import complete.")

        # Discover animated objects — use batch listConnections for speed
        all_transforms = cmds.ls(type="transform", long=True) or []
        all_joints = cmds.ls(type="joint", long=True) or []
        candidates = all_transforms + all_joints

        # Batch: check which candidates have anim curve connections
        cls._all_animated = []
        for obj in candidates:
            curves = cmds.listConnections(obj, type="animCurve", source=True)
            if curves:
                cls._all_animated.append(obj)

        # Cap the test sample to avoid hanging on huge scenes
        MAX_SAMPLE = 200
        if len(cls._all_animated) > MAX_SAMPLE:
            random.seed(42)  # Reproducible sample
            cls._animated_objects = sorted(random.sample(cls._all_animated, MAX_SAMPLE))
        else:
            cls._animated_objects = list(cls._all_animated)

        # Save to a temp .ma for fast re-open
        temp_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "temp_tests"
        )
        os.makedirs(temp_dir, exist_ok=True)
        cls._cached_scene = os.path.join(temp_dir, "_cached_fbx_scene.ma")
        cmds.file(rename=cls._cached_scene)
        cmds.file(save=True, type="mayaAscii", force=True)
        print(f"[realworld] Cached scene: {cls._cached_scene}")
        print(
            f"[realworld] {len(cls._all_animated)} animated objects total, "
            f"sampling {len(cls._animated_objects)} for tests"
        )

    @classmethod
    def tearDownClass(cls):
        """Clean up the cached scene file."""
        try:
            if cls._cached_scene and os.path.isfile(cls._cached_scene):
                os.remove(cls._cached_scene)
        except OSError:
            pass
        super().tearDownClass()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reload_cached_scene(self):
        """Re-open the cached scene (much faster than FBX import)."""
        import maya.cmds as cmds

        cmds.file(self._cached_scene, open=True, force=True)
        return list(self._animated_objects)

    def _snapshot_positions(self, objects, frames):
        """Record world-space positions for objects at given frames.

        Returns:
            dict: {obj: {frame: (tx, ty, tz, rx, ry, rz)}}
        """
        import maya.cmds as cmds

        attrs = ("tx", "ty", "tz", "rx", "ry", "rz")
        snap = {obj: {} for obj in objects}
        # Use getAttr(time=f) to query without changing currentTime —
        # avoids expensive scene evaluation on every time change.
        for obj in objects:
            for f in frames:
                try:
                    vals = tuple(cmds.getAttr(f"{obj}.{a}", time=f) for a in attrs)
                    snap[obj][f] = vals
                except Exception:
                    pass  # Some objects may not have all channels
        return snap

    def _compare_snapshots(self, before, after, tolerance=0.05, label=""):
        """Assert two snapshots match within tolerance."""
        drifted = []
        for obj in before:
            if obj not in after:
                continue
            for frame in before[obj]:
                if frame not in after[obj]:
                    continue
                b = before[obj][frame]
                a = after[obj][frame]
                for i, (bv, av) in enumerate(zip(b, a)):
                    if abs(bv - av) > tolerance:
                        attr = ("tx", "ty", "tz", "rx", "ry", "rz")[i]
                        drifted.append(
                            f"{obj}.{attr} frame {frame}: {bv:.4f} -> {av:.4f} "
                            f"(delta={abs(bv - av):.4f})"
                        )
        if drifted:
            summary = "\n  ".join(drifted[:20])
            extra = f"\n  ... and {len(drifted) - 20} more" if len(drifted) > 20 else ""
            self.fail(
                f"{label}Position drift detected ({len(drifted)} channels):\n"
                f"  {summary}{extra}"
            )

    def _get_sample_frames(self, count=10):
        """Return evenly spaced frames across the playback range."""
        import maya.cmds as cmds

        start = int(cmds.playbackOptions(q=True, minTime=True))
        end = int(cmds.playbackOptions(q=True, maxTime=True))
        if end <= start:
            return [start]
        step = max(1, (end - start) // (count - 1))
        frames = list(range(start, end + 1, step))
        if end not in frames:
            frames.append(end)
        return frames

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    @skipUnlessExtended
    def test_realworld_optimize_keys_no_drift(self):
        """optimize_keys must not alter evaluated positions on a real FBX.

        Imports the FBX, snapshots positions at sample frames, runs
        optimize_keys (static curve removal + flat key removal), and
        verifies no position drift exceeds tolerance.
        """
        import maya.cmds as cmds

        animated = self._reload_cached_scene()
        self.assertTrue(animated, "No animated objects found in FBX")

        frames = self._get_sample_frames(10)
        before = self._snapshot_positions(animated, frames)

        # Count curves before
        all_curves_before = cmds.ls(type="animCurve") or []

        AnimUtils.optimize_keys(
            animated,
            remove_static_curves=True,
            remove_flat_keys=True,
            simplify_keys=False,
            recursive=False,
            quiet=True,
        )

        all_curves_after = cmds.ls(type="animCurve") or []
        print(
            f"[realworld] optimize_keys: {len(all_curves_before)} curves -> "
            f"{len(all_curves_after)} curves"
        )

        after = self._snapshot_positions(animated, frames)
        self._compare_snapshots(before, after, tolerance=0.05, label="optimize_keys: ")

    @skipUnlessExtended
    def test_realworld_smart_bake_no_drift(self):
        """SmartBake + optimize_keys must not alter positions on a real FBX.

        End-to-end: imports FBX, smart-bakes constrained objects with
        delete_inputs + optimize_keys, verifies no position drift.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        animated = self._reload_cached_scene()
        self.assertTrue(animated, "No animated objects found in FBX")

        frames = self._get_sample_frames(10)
        before = self._snapshot_positions(animated, frames)

        baker = SmartBake(
            objects=[str(o) for o in animated],
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=True,
            use_override_layer=False,
            delete_inputs=True,
        )

        analysis = baker.analyze()
        needs_bake = [obj for obj, data in analysis.items() if data.requires_bake]
        print(f"[realworld] SmartBake: {len(needs_bake)} objects require baking")

        if needs_bake:
            baker.bake(analysis)

        after = self._snapshot_positions(animated, frames)
        self._compare_snapshots(before, after, tolerance=0.05, label="SmartBake: ")

    @skipUnlessExtended
    def test_realworld_snap_and_tie_no_drift(self):
        """snap_keys_to_frames + tie_keyframes must not break animation.

        Imports FBX, snapshots positions, runs snap then tie, and
        verifies positions remain within tolerance.
        """
        import maya.cmds as cmds

        animated = self._reload_cached_scene()
        self.assertTrue(animated, "No animated objects found in FBX")

        frames = self._get_sample_frames(10)
        before = self._snapshot_positions(animated, frames)

        AnimUtils.snap_keys_to_frames(animated)

        start = int(cmds.playbackOptions(q=True, minTime=True))
        end = int(cmds.playbackOptions(q=True, maxTime=True))
        AnimUtils.tie_keyframes(animated, custom_range=(start, end))

        after = self._snapshot_positions(animated, frames)
        # Slightly looser tolerance for snap+tie (sub-frame keys shift)
        self._compare_snapshots(before, after, tolerance=0.1, label="snap+tie: ")

    @skipUnlessExtended
    def test_realworld_full_export_pipeline_no_drift(self):
        """Full export pipeline (SmartBake → optimize → snap → tie) must preserve positions.

        Runs the same task sequence as TaskManager in the real exporter.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        animated = self._reload_cached_scene()
        self.assertTrue(animated, "No animated objects found in FBX")

        frames = self._get_sample_frames(15)
        before = self._snapshot_positions(animated, frames)

        obj_strs = [str(o) for o in animated]

        # Step 1: SmartBake
        baker = SmartBake(
            objects=obj_strs,
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=True,
            use_override_layer=False,
            delete_inputs=True,
        )
        analysis = baker.analyze()
        if any(d.requires_bake for d in analysis.values()):
            baker.bake(analysis)

        # Step 2: Snap keys to whole frames
        AnimUtils.snap_keys_to_frames(obj_strs)

        # Step 3: Tie keyframes
        start = int(cmds.playbackOptions(q=True, minTime=True))
        end = int(cmds.playbackOptions(q=True, maxTime=True))
        AnimUtils.tie_keyframes(obj_strs, custom_range=(start, end))

        after = self._snapshot_positions(animated, frames)
        self._compare_snapshots(before, after, tolerance=0.1, label="full-pipeline: ")

        # Also verify no sub-frame keys remain
        for obj in obj_strs:
            curves = cmds.listConnections(obj, type="animCurve", source=True) or []
            for curve in curves:
                keys = cmds.keyframe(curve, q=True, timeChange=True) or []
                for k in keys:
                    self.assertEqual(
                        k,
                        round(k),
                        f"Sub-frame key at {k} on {curve} after full pipeline",
                    )

    @skipUnlessExtended
    def test_realworld_curve_stats(self):
        """Print scene statistics for the real FBX (informational, always passes).

        Useful for verifying the file imported correctly and understanding
        the complexity of the test scene.
        """
        import maya.cmds as cmds

        animated = self._reload_cached_scene()

        all_curves = cmds.ls(type="animCurve") or []
        all_transforms = cmds.ls(type="transform", long=True) or []
        all_joints = cmds.ls(type="joint", long=True) or []
        constraints = cmds.ls(type="constraint") or []

        start = cmds.playbackOptions(q=True, minTime=True)
        end = cmds.playbackOptions(q=True, maxTime=True)

        total_keys = 0
        for curve in all_curves:
            total_keys += cmds.keyframe(curve, q=True, keyframeCount=True) or 0

        print(f"\n{'='*60}")
        print(f"Real-World FBX Stats: {os.path.basename(self.fbx_path)}")
        print(f"{'='*60}")
        print(f"  Transforms:    {len(all_transforms)}")
        print(f"  Joints:        {len(all_joints)}")
        print(f"  Animated objs: {len(animated)}")
        print(f"  Anim curves:   {len(all_curves)}")
        print(f"  Total keys:    {total_keys}")
        print(f"  Constraints:   {len(constraints)}")
        print(f"  Frame range:   {start} - {end}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
