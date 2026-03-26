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
        AnimUtils.set_keys_for_attributes([self.cube], target_times=[1], translateX=99)

        # The value must be updated
        self.assertAlmostEqual(pm.getAttr(plug, time=1), 99, places=3)

        # Stepped out-tangent must still be "step"
        ott = cmds.keyTangent(plug, query=True, time=(1, 1), outTangentType=True)
        self.assertEqual(
            ott[0], "step", "Stepped tangent was overwritten by set_keys_for_attributes"
        )

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
        count = AnimUtils.paste_keys(objects=[self.sphere], copied_data=copied)
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
        AnimUtils.paste_keys(objects=[self.cube], copied_data=copied, target_time=1)

        # Value should be updated
        self.assertAlmostEqual(pm.getAttr(plug, time=1), 77.0, places=3)

        # Stepped tangent must survive
        ott = cmds.keyTangent(plug, query=True, time=(1, 1), outTangentType=True)
        self.assertEqual(
            ott[0], "step", "Stepped tangent was overwritten by paste_keys"
        )

    def test_paste_keys_inherits_stepped_tangent_for_new_key(self):
        """Verify paste_keys inherits tangent type from the preceding key when inserting new keys.

        When no key exists at the target time, the tangent type should be
        inherited from the nearest preceding key so that stepped curves
        stay stepped.
        Fixed: 2026-02-26
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Set both existing keys (1, 10) to stepped.
        # Maya only accepts "step" for outTangentType; the in-tangent
        # equivalent is "stepnext".
        cmds.keyTangent(plug, edit=True, outTangentType="step")
        cmds.keyTangent(plug, edit=True, inTangentType="stepnext")

        # Paste at frame 5, where no key exists yet
        copied = {str(self.cube): {"translateX": 55.0}}
        AnimUtils.paste_keys(objects=[self.cube], copied_data=copied, target_time=5)

        # New key must exist
        keys_at_5 = cmds.keyframe(plug, query=True, time=(5, 5), timeChange=True)
        self.assertTrue(keys_at_5, "Key was not created at frame 5")

        # Tangent types should be inherited as stepped.
        # Maya's in-tangent equivalent of "step" is "stepnext".
        ott = cmds.keyTangent(plug, query=True, time=(5, 5), outTangentType=True)
        itt = cmds.keyTangent(plug, query=True, time=(5, 5), inTangentType=True)
        self.assertEqual(ott[0], "step", "Out-tangent not inherited as stepped")
        self.assertEqual(itt[0], "stepnext", "In-tangent not inherited as stepnext")

    def test_paste_keys_name_matching(self):
        """Verify paste_keys matches objects by short name when long path differs."""
        import maya.cmds as cmds

        pm.setKeyframe(self.sphere, attribute="translateX", time=1, value=0)

        # Store using short name
        copied = {self.sphere.nodeName(): {"translateX": 33.0}}
        cmds.currentTime(1)
        count = AnimUtils.paste_keys(objects=[self.sphere], copied_data=copied)
        self.assertEqual(count, 1)
        self.assertAlmostEqual(
            pm.getAttr(f"{self.sphere}.translateX", time=1), 33.0, places=3
        )

    def test_paste_keys_at_explicit_target_time(self):
        """Verify paste_keys places keys at an explicit target_time, not the playhead.

        Simulates the 'At Copy Frame' workflow where the user copies at
        frame 3 but the playhead has since moved to frame 8.
        """
        import maya.cmds as cmds

        pm.setKeyframe(self.sphere, attribute="translateX", time=1, value=0)
        copied = {str(self.sphere): {"translateX": 99.0}}
        copy_frame = 3

        # Move playhead away from the copy frame
        cmds.currentTime(8)
        count = AnimUtils.paste_keys(
            objects=[self.sphere], copied_data=copied, target_time=copy_frame
        )
        self.assertEqual(count, 1)
        # Key should exist at the explicit target, not the playhead
        self.assertAlmostEqual(
            pm.getAttr(f"{self.sphere}.translateX", time=copy_frame), 99.0, places=3
        )
        # No key should have been created at the playhead
        keys_at_8 = cmds.keyframe(
            f"{self.sphere}.translateX", query=True, time=(8, 8), timeChange=True
        )
        self.assertFalse(keys_at_8, "Key was incorrectly created at the playhead")

    def test_paste_keys_kwargs_forwarded(self):
        """Verify extra kwargs are forwarded to pm.setKeyframe."""
        import maya.cmds as cmds

        pm.setKeyframe(self.sphere, attribute="translateX", time=1, value=0)
        copied = {str(self.sphere): {"translateX": 50.0}}

        # Pass breakdown=True which should mark the key as a breakdown
        count = AnimUtils.paste_keys(
            objects=[self.sphere],
            copied_data=copied,
            target_time=5,
            breakdown=True,
        )
        self.assertEqual(count, 1)
        bd = cmds.keyframe(
            f"{self.sphere}.translateX",
            query=True,
            time=(5, 5),
            breakdown=True,
        )
        self.assertTrue(bd, "breakdown flag was not forwarded to setKeyframe")

    def test_copy_keys_current_frame_multiple_attrs(self):
        """copy_keys current_frame mode captures all keyed attrs, not just one."""
        import maya.cmds as cmds

        pm.select(self.cube)
        cmds.currentTime(1)
        result = AnimUtils.copy_keys(mode="current_frame")
        obj_data = result[str(self.cube)]
        self.assertIn("translateX", obj_data)
        self.assertIn("translateY", obj_data)
        self.assertAlmostEqual(obj_data["translateX"], 0.0, places=3)
        self.assertAlmostEqual(obj_data["translateY"], 0.0, places=3)

    def test_copy_keys_no_objects_returns_empty(self):
        """copy_keys returns empty dict when no objects are selected or provided."""
        pm.select(clear=True)
        result = AnimUtils.copy_keys(mode="current_frame")
        self.assertEqual(result, {})

    def test_paste_keys_no_data_returns_zero(self):
        """paste_keys returns 0 when copied_data is empty or None."""
        self.assertEqual(AnimUtils.paste_keys(copied_data=None), 0)
        self.assertEqual(AnimUtils.paste_keys(copied_data={}), 0)

    def test_paste_keys_no_objects_returns_zero(self):
        """paste_keys returns 0 when no target objects are selected."""
        pm.select(clear=True)
        result = AnimUtils.paste_keys(
            objects=[], copied_data={str(self.cube): {"translateX": 5.0}}
        )
        self.assertEqual(result, 0)

    def test_paste_keys_multiple_attrs(self):
        """paste_keys sets multiple attributes from copied data."""
        import maya.cmds as cmds

        copied = {str(self.cube): {"translateX": 100.0, "translateY": 200.0}}
        cmds.currentTime(5)
        count = AnimUtils.paste_keys(objects=[self.cube], copied_data=copied)
        self.assertEqual(count, 1)
        self.assertAlmostEqual(
            pm.getAttr(f"{self.cube}.translateX", time=5), 100.0, places=3
        )
        self.assertAlmostEqual(
            pm.getAttr(f"{self.cube}.translateY", time=5), 200.0, places=3
        )

    def test_copy_paste_roundtrip(self):
        """Copy at one frame, move playhead, paste at playhead — values match."""
        import maya.cmds as cmds

        pm.select(self.cube)
        cmds.currentTime(1)
        copied = AnimUtils.copy_keys(mode="current_frame")

        cmds.currentTime(20)
        pm.setKeyframe(self.cube, attribute="translateX", time=20, value=999)
        count = AnimUtils.paste_keys(objects=[self.cube], copied_data=copied)
        self.assertEqual(count, 1)
        # Value at 20 should now be the copied value (0.0 from frame 1), not 999
        self.assertAlmostEqual(
            pm.getAttr(f"{self.cube}.translateX", time=20), 0.0, places=3
        )

    def test_paste_keys_unmatched_object_returns_zero(self):
        """paste_keys returns 0 when copied data keys don't match target objects."""
        import maya.cmds as cmds

        copied = {"nonexistent_object": {"translateX": 5.0}}
        cmds.currentTime(1)
        count = AnimUtils.paste_keys(objects=[self.cube], copied_data=copied)
        self.assertEqual(count, 0)

    def test_copy_keys_selected_stores_all_keys(self):
        """copy_keys 'selected' mode stores ALL selected keys, not just the last.

        Bug: Previously only the last selected key's value was stored,
        discarding earlier keys.  Multi-key selection must preserve all
        time/value/tangent data.
        Fixed: 2026-03-01
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Select both keys (frames 1 and 10)
        cmds.selectKey(plug, time=(1, 10), add=True)

        copied = AnimUtils.copy_keys(objects=[self.cube], mode="selected")
        self.assertTrue(copied, "copy_keys returned empty dict")

        obj_data = list(copied.values())[0]
        attr_data = obj_data.get("translateX")
        self.assertIsInstance(
            attr_data, list, "selected mode should return list of key dicts"
        )
        self.assertEqual(len(attr_data), 2, "Should have copied 2 selected keys")
        # Check both keys are present
        self.assertAlmostEqual(attr_data[0]["time"], 1.0)
        self.assertAlmostEqual(attr_data[1]["time"], 10.0)
        # Check tangent types are stored
        self.assertIn("inTangentType", attr_data[0])
        self.assertIn("outTangentType", attr_data[0])

    def test_copy_keys_auto_cb_filter_fallback(self):
        """copy_keys auto mode falls back to all selected keys when CB filter
        eliminates everything.

        Bug: When keys were selected in the graph editor on translateX but
        the channel box highlighted rotateY, the CB intersection produced an
        empty result and the user got 'Nothing to copy'.
        Fixed: 2026-03-03
        """
        import maya.cmds as cmds

        plug_tx = f"{self.cube}.translateX"
        # Select keys on translateX
        cmds.selectKey(plug_tx, time=(1, 10), add=True)

        # Simulate a CB highlight on rotateY (disjoint from selected keys).
        # Auto mode builds cb_filter from channelBox query; we patch it by
        # calling copy_keys in "selected" mode but with an incompatible
        # cb_filter to replicate the same code path.
        result = AnimUtils.copy_keys(objects=[self.cube], mode="auto")
        # Even if channelBox sma returns nothing (headless), selected keys
        # should still be captured — the important thing is that the result
        # is NOT empty when keys are selected.
        self.assertTrue(
            result, "auto mode should not return empty when keys are selected"
        )
        obj_data = list(result.values())[0]
        self.assertIn("translateX", obj_data)

    def test_paste_keys_multi_key_with_time_offset(self):
        """Pasting multi-key data offsets all keys relative to target_time.

        Bug: Previously only one key was pasted because copy_keys only
        stored a single scalar value per attribute.
        Fixed: 2026-03-01
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Make keys stepped
        cmds.keyTangent(
            plug, edit=True, outTangentType="step", inTangentType="stepnext"
        )

        # Select both keys and copy
        cmds.selectKey(plug, time=(1, 10), add=True)
        copied = AnimUtils.copy_keys(objects=[self.cube], mode="selected")

        # Paste at frame 20 — keys should appear at 20 and 29 (offset=19)
        count = AnimUtils.paste_keys(
            objects=[self.cube],
            copied_data=copied,
            target_time=20,
            refresh_channel_box=False,
        )
        self.assertEqual(count, 1)

        all_times = cmds.keyframe(plug, q=True, timeChange=True)
        self.assertIn(20.0, all_times, "First pasted key should be at frame 20")
        self.assertIn(29.0, all_times, "Second pasted key should be at frame 29")

    def test_paste_keys_multi_key_preserves_tangent_types(self):
        """Pasting multi-key data preserves stepped tangent types.

        Bug: Pasted keys lost their tangent types and appeared with auto/
        smooth interpolation instead of stepped.
        Fixed: 2026-03-01
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Make keys stepped
        cmds.keyTangent(
            plug, edit=True, outTangentType="step", inTangentType="stepnext"
        )

        # Select both keys and copy
        cmds.selectKey(plug, time=(1, 10), add=True)
        copied = AnimUtils.copy_keys(objects=[self.cube], mode="selected")

        # Paste at frame 30
        AnimUtils.paste_keys(
            objects=[self.cube],
            copied_data=copied,
            target_time=30,
            refresh_channel_box=False,
        )

        # Check tangent types on pasted keys
        for t in (30.0, 39.0):
            ott = cmds.keyTangent(plug, q=True, time=(t, t), outTangentType=True)
            itt = cmds.keyTangent(plug, q=True, time=(t, t), inTangentType=True)
            self.assertEqual(ott[0], "step", f"Out tangent at {t} should be step")
            self.assertEqual(
                itt[0], "stepnext", f"In tangent at {t} should be stepnext"
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
        AnimUtils.move_keys_to_frame(objects=[self.cube], frame=20, align="end")
        # Last key (was 10) should now be at 20
        keys_at_20 = pm.keyframe(
            self.cube,
            attribute="translateX",
            query=True,
            time=(20, 20),
            valueChange=True,
        )
        self.assertTrue(keys_at_20, "Last key was not moved to frame 20")
        self.assertAlmostEqual(keys_at_20[0], 10.0, places=3)

        # First key (was 1) should now be at 11 (offset = 20 - 10 = +10)
        keys_at_11 = pm.keyframe(
            self.cube,
            attribute="translateX",
            query=True,
            time=(11, 11),
            valueChange=True,
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

    def test_move_keys_to_frame_align_auto_keys_before(self):
        """Verify align='auto' uses 'end' when keys are before the target frame.

        setUp creates keys at frames 1 and 10 on translateX.
        Target frame 20 is well past the key range midpoint (5.5),
        so auto should resolve to 'end' — the last key (10) lands on frame 20.
        """
        AnimUtils.move_keys_to_frame(objects=[self.cube], frame=20, align="auto")
        # Auto resolves to 'end' → last key (was 10) should now be at 20
        keys_at_20 = pm.keyframe(
            self.cube,
            attribute="translateX",
            query=True,
            time=(20, 20),
            valueChange=True,
        )
        self.assertTrue(keys_at_20, "Last key was not moved to frame 20")
        self.assertAlmostEqual(keys_at_20[0], 10.0, places=3)

        # First key (was 1) should now be at 11 (offset = 20 - 10 = +10)
        keys_at_11 = pm.keyframe(
            self.cube,
            attribute="translateX",
            query=True,
            time=(11, 11),
            valueChange=True,
        )
        self.assertTrue(keys_at_11, "First key was not moved to frame 11")

    def test_move_keys_to_frame_align_auto_keys_after(self):
        """Verify align='auto' uses 'start' when keys are after the target frame.

        We set up keys at frames 50 and 60. Target frame is 20, which is
        before the midpoint (55), so auto should resolve to 'start' — the
        first key (50) lands on frame 20.
        """
        # Clear ALL existing keys and create new ones far ahead
        pm.cutKey(self.cube, clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=50, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=60, value=10)

        AnimUtils.move_keys_to_frame(objects=[self.cube], frame=20, align="auto")
        # Auto resolves to 'start' → first key (was 50) should now be at 20
        keys_at_20 = pm.keyframe(
            self.cube,
            attribute="translateX",
            query=True,
            time=(20, 20),
            valueChange=True,
        )
        self.assertTrue(keys_at_20, "First key was not moved to frame 20")
        self.assertAlmostEqual(keys_at_20[0], 0.0, places=3)

        # Last key (was 60) should now be at 30 (offset = 20 - 50 = -30)
        keys_at_30 = pm.keyframe(
            self.cube,
            attribute="translateX",
            query=True,
            time=(30, 30),
            valueChange=True,
        )
        self.assertTrue(keys_at_30, "Last key was not moved to frame 30")

    def test_move_keys_to_frame_align_start_default(self):
        """Verify explicit align='start' moves the first key to the target frame."""
        AnimUtils.move_keys_to_frame(objects=[self.cube], frame=20, align="start")
        # First key (was 1) should now be at 20
        keys_at_20 = pm.keyframe(
            self.cube,
            attribute="translateX",
            query=True,
            time=(20, 20),
            valueChange=True,
        )
        self.assertTrue(keys_at_20, "First key was not moved to frame 20")
        self.assertAlmostEqual(keys_at_20[0], 0.0, places=3)

        # Last key (was 10) should now be at 29 (offset = 20 - 1 = +19)
        keys_at_29 = pm.keyframe(
            self.cube,
            attribute="translateX",
            query=True,
            time=(29, 29),
            valueChange=True,
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

    def test_adjust_key_spacing_collision_aborts(self):
        """Collision prevention aborts the entire operation when a destination
        frame would land on an existing unmoved key.

        Setup: keys at 1 and 10 on translateX.
        Moving keys >= frame 0 by -2 would push key at 1 to -1 (clamped to 0)
        but more importantly key at 10 to 8.  That's fine — no collision
        expected there.
        Instead, create a collision scenario: add a key at frame 3, then
        shift keys >= 5 by -8, which would move key at 10 to 2, and key at 3
        is stationary (it's below the adjusted_time), so 10->2 doesn't collide
        with 3 (dest 2 != 3).
        Better approach: keys at 1, 5, 10.  Shift keys >= 2 by -5.
        Key at 5 → 0, key at 10 → 5.  But key at 1 is stationary and dest 0 ≠ 1.
        Key at 10 → 5 — but 5 is being moved too (it's in keys_to_move), so it's
        not stationary.  Need a stationary key at the destination.

        Simplest: keys at 1, 5, 10.  Shift keys >= 6 by -5.
        Only key at 10 moves (10 → 5).  Key at 5 is stationary.  Collision!
        """
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=5)
        # Now keys at 1, 5, 10
        AnimUtils.adjust_key_spacing(
            [self.cube.name()],
            spacing=-5,
            time=6,
            relative=False,
            prevent_collisions=True,
        )
        # Key at 10 should NOT have moved because collision with 5
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertIn(10.0, keys, "Key at 10 should remain — collision aborted move")
        self.assertIn(5.0, keys, "Key at 5 should remain — stationary")

    def test_adjust_key_spacing_collision_disabled(self):
        """When prevent_collisions=False, keys are moved even if they collide.

        Same setup as collision_aborts test: keys at 1, 5, 10.
        Shift keys >= 6 by -5.  Key 10 → 5.  Without collision prevention,
        the move is attempted (may overwrite or fail silently).
        """
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=5)
        # Keys at 1, 5, 10
        AnimUtils.adjust_key_spacing(
            [self.cube.name()],
            spacing=-5,
            time=6,
            relative=False,
            prevent_collisions=False,
        )
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        # Key at 10 should have been attempted to move (may or may not succeed
        # depending on Maya's handling, but it should NOT be at 10 anymore)
        # The key at 5 may be overwritten.  Just verify 10 is gone — that means
        # the move was attempted rather than aborted.
        self.assertNotIn(10.0, keys, "Key at 10 should have been moved (no abort)")

    def test_adjust_key_spacing_no_collision_proceeds(self):
        """Normal positive spacing with no collision proceeds correctly.

        Keys at 1 and 10.  Shift keys >= 5 by +3.
        Key at 10 → 13.  Key at 1 is stationary but dest 13 ≠ 1.  No collision.
        """
        AnimUtils.adjust_key_spacing(
            [self.cube.name()],
            spacing=3,
            time=5,
            relative=False,
            prevent_collisions=True,
        )
        keys = pm.keyframe(self.cube, attribute="translateX", query=True)
        self.assertIn(13.0, keys, "Key at 10 should move to 13")
        self.assertNotIn(10.0, keys, "Key at 10 should no longer exist")
        self.assertIn(1.0, keys, "Key at 1 should be unaffected")

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

    def test_transfer_keyframes_relative_mixed_times(self):
        """Transfer keys in relative mode when attributes have different key times.

        Bug: transfer_keyframes used the global first keyframe time
        (keyframe_times[0]) for the relative offset calculation. If an
        attribute's first key didn't coincide with that global time, all
        of its keys were silently skipped.
        Fixed: 2026-03-02
        """
        # Set up source with staggered key times:
        # translateX keyed at 1 and 10, translateZ keyed at 5 and 10
        pm.setKeyframe(self.cube, attribute="translateZ", time=5, value=3)
        pm.setKeyframe(self.cube, attribute="translateZ", time=10, value=8)

        # Give target a starting translateZ so relative offset is nonzero
        pm.setAttr(self.sphere.translateZ, 10)

        AnimUtils.transfer_keyframes(
            [self.cube, self.sphere], relative=True, transfer_tangents=False
        )

        # translateZ keys MUST exist on the target — the bug caused them
        # to be silently skipped because keyframe_times[0] was 1 (from
        # translateX) and translateZ had no key at time 1.
        tz_keys = pm.keyframe(self.sphere, attribute="translateZ", query=True)
        self.assertTrue(
            len(tz_keys) > 0,
            "translateZ keys were not transferred — relative offset "
            "bug: attributes whose first key differs from the global "
            "earliest keyframe time are silently dropped.",
        )

        # Verify relative offset: at time 5 (first translateZ key on
        # source), the target should equal its initial value (10).
        tz_val = pm.keyframe(
            self.sphere,
            attribute="translateZ",
            query=True,
            time=(5, 5),
            valueChange=True,
        )
        self.assertAlmostEqual(
            tz_val[0],
            10.0,
            places=4,
            msg="At the first key time the target should match its initial value.",
        )

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
    # step_keys
    # =========================================================================

    def test_step_keys_all(self):
        """step_keys with keys=None steps every key on the object."""
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        AnimUtils.step_keys(objects=[self.cube], keys=None, tangent="out")
        for t in (1, 10):
            ott = cmds.keyTangent(plug, q=True, time=(t, t), outTangentType=True)
            self.assertEqual(ott[0], "step", f"Out-tangent at {t} should be step")

    def test_step_keys_single_time_only_affects_that_key(self):
        """step_keys with keys=<float> must only step the key at that frame.

        Bug: stepping a single key used to affect the entire curve.
        Fixed: 2026-03-01
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Ensure both keys start non-stepped
        cmds.keyTangent(plug, edit=True, outTangentType="auto")
        cmds.keyTangent(plug, edit=True, inTangentType="auto")

        # Step only the key at frame 1
        AnimUtils.step_keys(objects=[self.cube], keys=1, tangent="out")

        ott1 = cmds.keyTangent(plug, q=True, time=(1, 1), outTangentType=True)
        self.assertEqual(ott1[0], "step", "Key at 1 should be stepped")

        ott10 = cmds.keyTangent(plug, q=True, time=(10, 10), outTangentType=True)
        self.assertNotEqual(ott10[0], "step", "Key at 10 should remain unstepped")

    def test_step_keys_dict_specific_times(self):
        """step_keys with a dict of curve→times steps only those times."""
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        curves = AnimUtils.objects_to_curves([self.cube], as_strings=True)
        tx_curve = [c for c in curves if "translateX" in c][0]

        cmds.keyTangent(plug, edit=True, outTangentType="auto")

        AnimUtils.step_keys(keys={tx_curve: [1.0]}, tangent="out")

        ott1 = cmds.keyTangent(plug, q=True, time=(1, 1), outTangentType=True)
        self.assertEqual(ott1[0], "step")
        ott10 = cmds.keyTangent(plug, q=True, time=(10, 10), outTangentType=True)
        self.assertNotEqual(ott10[0], "step", "Frame 10 must NOT be stepped")

    def test_step_keys_out_only_does_not_affect_in(self):
        """tangent='out' sets only the out-tangent to step, preserving in-tangent type exactly.

        Bug: lock=False causes Maya to convert the preserved side's tangent
        type from 'auto' to 'fixed' when angle is restored.
        Fixed: 2026-03-01
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        cmds.keyTangent(plug, edit=True, outTangentType="auto", inTangentType="auto")

        AnimUtils.step_keys(objects=[self.cube], keys=None, tangent="out")

        for t in (1, 10):
            ott = cmds.keyTangent(plug, q=True, time=(t, t), outTangentType=True)
            itt = cmds.keyTangent(plug, q=True, time=(t, t), inTangentType=True)
            self.assertEqual(ott[0], "step", f"Out-tangent at {t} should be step")
            self.assertEqual(
                itt[0],
                "auto",
                f"In-tangent at {t} should remain 'auto', got '{itt[0]}'",
            )

    def test_step_keys_in_only_does_not_affect_out(self):
        """tangent='in' sets only the in-tangent to stepnext, preserving out-tangent type exactly."""
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        cmds.keyTangent(plug, edit=True, outTangentType="auto", inTangentType="auto")

        AnimUtils.step_keys(objects=[self.cube], keys=None, tangent="in")

        for t in (1, 10):
            itt = cmds.keyTangent(plug, q=True, time=(t, t), inTangentType=True)
            ott = cmds.keyTangent(plug, q=True, time=(t, t), outTangentType=True)
            self.assertEqual(
                itt[0], "stepnext", f"In-tangent at {t} should be stepnext"
            )
            self.assertEqual(
                ott[0],
                "auto",
                f"Out-tangent at {t} should remain 'auto', got '{ott[0]}'",
            )

    def test_step_keys_in_preserves_spline_out(self):
        """tangent='in' must not corrupt a 'spline' out-tangent to 'fixed'.

        Bug: restoring angle/weight on a non-fixed tangent silently
        converts the type to 'fixed', breaking auto-computation.
        Fixed: 2026-03-01
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        cmds.keyTangent(
            plug, edit=True, outTangentType="spline", inTangentType="spline"
        )

        AnimUtils.step_keys(objects=[self.cube], keys=None, tangent="in")

        for t in (1, 10):
            ott = cmds.keyTangent(plug, q=True, time=(t, t), outTangentType=True)
            self.assertEqual(
                ott[0],
                "spline",
                f"Out-tangent at {t} should remain 'spline', got '{ott[0]}'",
            )

    def test_step_keys_both(self):
        """tangent='both' sets out to step and in to stepnext."""
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        cmds.keyTangent(plug, edit=True, outTangentType="auto", inTangentType="auto")

        AnimUtils.step_keys(objects=[self.cube], keys=None, tangent="both")

        for t in (1, 10):
            ott = cmds.keyTangent(plug, q=True, time=(t, t), outTangentType=True)
            itt = cmds.keyTangent(plug, q=True, time=(t, t), inTangentType=True)
            self.assertEqual(ott[0], "step")
            self.assertEqual(itt[0], "stepnext")

    def test_step_keys_both_steps_predecessor(self):
        """tangent='both' on specific keys also steps the predecessor's out-tangent.

        Bug: inTangentType='stepnext' alone does NOT produce step interpolation.
        The predecessor key's outTangentType must be 'step' for the incoming
        segment to hold flat. Without this, the segment between an unmodified
        predecessor and the first selected key would interpolate smoothly.
        Fixed: 2026-03-01
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Reset all to auto
        cmds.keyTangent(plug, edit=True, outTangentType="auto", inTangentType="auto")

        # Get curve name and step only key at frame 10 with tangent="both"
        curves = cmds.keyframe(plug, q=True, name=True)
        curve = curves[0]
        all_times = cmds.keyframe(curve, q=True, timeChange=True)
        # Pick a key that is NOT the first key
        target_t = all_times[-1]  # last key
        prev_t = all_times[-2]  # predecessor

        AnimUtils.step_keys(keys={curve: [target_t]}, tangent="both")

        # Target key should have step/stepnext
        ott = cmds.keyTangent(
            plug, q=True, time=(target_t, target_t), outTangentType=True
        )
        itt = cmds.keyTangent(
            plug, q=True, time=(target_t, target_t), inTangentType=True
        )
        self.assertEqual(ott[0], "step")
        self.assertEqual(itt[0], "stepnext")

        # Predecessor key should have out=step (set by predecessor fix)
        ott_prev = cmds.keyTangent(
            plug, q=True, time=(prev_t, prev_t), outTangentType=True
        )
        self.assertEqual(ott_prev[0], "step", "Predecessor out-tangent should be step")

        # Predecessor's in-tangent should be preserved (NOT changed to step)
        itt_prev = cmds.keyTangent(
            plug, q=True, time=(prev_t, prev_t), inTangentType=True
        )
        self.assertNotEqual(
            itt_prev[0], "step", "Predecessor in-tangent should be preserved"
        )

        # Verify evaluated value at midpoint is stepped (holds predecessor's value)
        mid_t = (prev_t + target_t) / 2
        val = cmds.keyframe(
            plug, q=True, time=(mid_t, mid_t), eval=True, valueChange=True
        )
        prev_val = cmds.keyframe(plug, q=True, time=(prev_t, prev_t), valueChange=True)
        self.assertAlmostEqual(
            val[0],
            prev_val[0],
            places=3,
            msg="Segment between predecessor and target should be stepped (hold predecessor value)",
        )

    def test_step_tangent_as_in_tangent_is_invalid(self):
        """Verify that Maya rejects 'step' as an in-tangent type.

        ``pm.setKeyframe(inTangentType="step")`` silently fails — Maya
        only accepts 'stepnext' for in-tangents.  The behaviors system
        was passing 'step' for both in and out tangent types, which
        produced warnings and left in-tangents unchanged.

        Bug: ``apply_behavior`` passed ``inTangentType="step"`` directly
        instead of using ``step_keys`` or ``"stepnext"``.
        Fixed: 2026-03-25
        """
        import maya.cmds as cmds

        plug = f"{self.cube}.translateX"
        # Start with auto tangents so we have a known baseline
        cmds.keyTangent(plug, edit=True, inTangentType="auto", outTangentType="auto")

        # Attempt to set inTangentType="step" — this is what the bug did
        cmds.keyTangent(plug, edit=True, time=(1, 1), inTangentType="step")
        itt = cmds.keyTangent(plug, q=True, time=(1, 1), inTangentType=True)
        # Maya silently refuses — the in-tangent stays "auto", NOT "step"
        self.assertNotEqual(
            itt[0],
            "step",
            "'step' is not a valid in-tangent type; Maya should reject it",
        )

        # Correct usage: inTangentType="stepnext"
        cmds.keyTangent(plug, edit=True, time=(1, 1), inTangentType="stepnext")
        itt2 = cmds.keyTangent(plug, q=True, time=(1, 1), inTangentType=True)
        self.assertEqual(
            itt2[0], "stepnext", "stepnext should be accepted as valid in-tangent"
        )

    def test_apply_behavior_visibility_mirror_uses_stepnext(self):
        """Visibility mirror in apply_behavior must use stepnext for in-tangent.

        Bug: apply_behavior hardcoded inTangentType="step" on visibility
        mirror keys, producing Maya warnings and silently failing to set
        the tangent.
        Fixed: 2026-03-25
        """
        from mayatk.anim_utils.shots.shot_manifest.behaviors import apply_behavior

        # Add opacity attribute to trigger the visibility mirror path
        pm.addAttr(self.cube, ln="opacity", at="float", min=0, max=1, dv=1, k=True)

        apply_behavior(str(self.cube), "fade_in", start=1, end=30)

        import maya.cmds as cmds

        vis_keys = cmds.keyframe(f"{self.cube}.visibility", q=True, timeChange=True)
        self.assertTrue(vis_keys, "Visibility should have keys from mirror")
        for t in vis_keys:
            ott = cmds.keyTangent(
                f"{self.cube}.visibility",
                q=True,
                time=(t, t),
                outTangentType=True,
            )
            itt = cmds.keyTangent(
                f"{self.cube}.visibility",
                q=True,
                time=(t, t),
                inTangentType=True,
            )
            self.assertEqual(ott[0], "step", f"Out-tangent at {t} should be 'step'")
            self.assertEqual(
                itt[0],
                "stepnext",
                f"In-tangent at {t} should be 'stepnext', not 'step'",
            )

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
        """Verify tie_keyframes does NOT convert non-stepped keys to stepped
        on curves with mixed tangent types.

        Bug: tie_keyframes used ``any()`` to detect stepped curves, then
        called step_keys on the ENTIRE curve — overwriting smooth/linear
        tangents with stepped. This corrupted animation on objects like
        S00A47_PIN_LOC that had a mix of stepped and smooth tangent types.
        Fixed: 2026-03-02
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

        # Snapshot original tangent types before tie
        out_before = cmds.keyTangent(curve, q=True, outTangentType=True)
        times_before = cmds.keyframe(curve, q=True, timeChange=True)
        orig_tangents = dict(zip(times_before, out_before))

        # Tie keyframes — this adds bookend keys at playback range boundaries
        AnimUtils.tie_keyframes([self.cube])

        # After tying, the original non-stepped keys must retain their
        # tangent types. Only fully-stepped curves get bookend stepping.
        out_after = cmds.keyTangent(curve, q=True, outTangentType=True)
        times_after = cmds.keyframe(curve, q=True, timeChange=True)
        after_tangents = dict(zip(times_after, out_after))

        # Check original keys preserved their tangent types
        for t, orig_type in orig_tangents.items():
            if t in after_tangents:
                self.assertEqual(
                    after_tangents[t],
                    orig_type,
                    f"Tangent at t={t} changed from '{orig_type}' to "
                    f"'{after_tangents[t]}' — mixed tangents were corrupted",
                )

    def test_tie_keyframes_mixed_tangents_not_corrupted(self):
        """Verify tie_keyframes does not overwrite smooth tangents with stepped
        on an object that has both stepped visibility and smooth translate curves.

        Bug: S00A47_PIN_LOC had its animation tangents overwritten with stepped
        tangents during scene_exporter export. The cause was tie_keyframes
        detecting curves with ANY stepped key and then re-stepping ALL keys
        on those curves, including smooth translate/rotate animation.
        Fixed: 2026-03-02
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, clear=True)

        # Create smooth translate animation (auto tangents)
        pm.setKeyframe(self.cube, attribute="translateX", time=2, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=10)
        pm.setKeyframe(self.cube, attribute="translateX", time=9, value=3)

        # Create stepped visibility animation (like a toggle)
        pm.setKeyframe(self.cube, attribute="visibility", time=2, value=1)
        pm.setKeyframe(self.cube, attribute="visibility", time=5, value=0)
        pm.setKeyframe(self.cube, attribute="visibility", time=9, value=1)

        vis_curve = (
            cmds.listConnections(
                f"{self.cube}.visibility", type="animCurve", source=True
            )
            or [None]
        )[0]
        tx_curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(vis_curve)
        self.assertIsNotNone(tx_curve)

        # Set visibility curve to fully stepped
        cmds.keyTangent(vis_curve, outTangentType="step")

        # Verify tx curve is NOT stepped
        tx_out_before = cmds.keyTangent(tx_curve, q=True, outTangentType=True)
        self.assertFalse(
            any(t == "step" for t in tx_out_before),
            f"translateX should not be stepped before tie: {tx_out_before}",
        )

        # Tie keyframes
        AnimUtils.tie_keyframes([self.cube])

        # After tying: visibility curve should still be fully stepped
        vis_out = cmds.keyTangent(vis_curve, q=True, outTangentType=True)
        self.assertTrue(
            all(t == "step" for t in vis_out),
            f"Visibility lost stepped tangents: {vis_out}",
        )

        # After tying: translateX curve must NOT be stepped
        tx_out_after = cmds.keyTangent(tx_curve, q=True, outTangentType=True)
        self.assertFalse(
            any(t == "step" for t in tx_out_after),
            f"translateX tangents were corrupted to stepped: {tx_out_after}",
        )

    def test_tie_keyframes_preserves_boundary_stepped_on_mixed_curve(self):
        """Verify tie_keyframes preserves stepped tangents on boundary keys
        when interior keys have smooth tangent types.

        Bug: A curve with stepped first/last keys (e.g. a hold-then-animate
        pattern) had those stepped tangents corrupted to fixed/auto by
        cmds.setKeyframe when bookend keys were inserted. The all() check
        skipped restoration because the curve wasn't fully stepped.
        Fixed: 2026-03-02
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Create a curve: stepped bookend at start, smooth interior, stepped at end
        pm.setKeyframe(self.cube, attribute="translateX", time=2, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=4, value=5)
        pm.setKeyframe(self.cube, attribute="translateX", time=6, value=8)
        pm.setKeyframe(self.cube, attribute="translateX", time=9, value=10)

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Step only the first and last keys, leave interior as auto
        cmds.keyTangent(curve, time=(2, 2), outTangentType="step")
        cmds.keyTangent(curve, time=(9, 9), outTangentType="step")

        # Verify baseline: first and last are step, middle are not
        out_before = cmds.keyTangent(curve, q=True, outTangentType=True)
        times_before = cmds.keyframe(curve, q=True, timeChange=True)
        self.assertEqual(out_before[0], "step", "First key should be step")
        self.assertEqual(out_before[-1], "step", "Last key should be step")
        self.assertNotEqual(out_before[1], "step", "Interior key should not be step")

        # Tie keyframes (playback range 1-10)
        AnimUtils.tie_keyframes([self.cube])

        # After tying: original stepped boundary keys must still be stepped
        out_after = cmds.keyTangent(curve, q=True, outTangentType=True)
        times_after = cmds.keyframe(curve, q=True, timeChange=True)
        after_tangents = dict(zip(times_after, out_after))

        self.assertEqual(
            after_tangents.get(2.0),
            "step",
            f"First key at t=2 lost stepped tangent: {after_tangents}",
        )
        self.assertEqual(
            after_tangents.get(9.0),
            "step",
            f"Last key at t=9 lost stepped tangent: {after_tangents}",
        )

        # Interior keys must NOT be stepped
        for t in [4.0, 6.0]:
            self.assertNotEqual(
                after_tangents.get(t),
                "step",
                f"Interior key at t={t} was incorrectly stepped: {after_tangents}",
            )

    def test_tie_keyframes_preserves_auto_tangent_interpolation(self):
        """Verify tie_keyframes bookend keys don't corrupt auto tangent
        interpolation on boundary keys.

        Bug: cmds.setKeyframe recalculates auto tangents on neighbor keys
        when bookend keys are inserted, changing the curve's interpolation
        in the region before the last key. For example, a curve holding
        v=1.029 from frame 20438-28200 would drift to v=1.031 at frame
        26600 because the auto tangent on frame 20438 was recalculated.
        Fix converts boundary auto tangents to fixed tangent type to lock
        the original angle/weight.
        Fixed: 2026-03-05
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Create a curve with auto tangent at boundary: animated segment
        # followed by a long flat hold.
        pm.setKeyframe(self.cube, attribute="translateX", time=100, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=110, value=5)
        pm.setKeyframe(self.cube, attribute="translateX", time=120, value=10)

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Set the last key to auto tangent (the type that gets recalculated)
        cmds.keyTangent(curve, time=(120, 120), outTangentType="auto")

        # Add a distant flat hold key (simulating FBX tied animation)
        pm.setKeyframe(self.cube, attribute="translateX", time=500, value=10)
        cmds.keyTangent(curve, time=(500, 500), inTangentType="flat")

        # Sample at midpoints before tie
        sample_frames = [115, 200, 300, 400]
        before_values = {
            f: cmds.getAttr(f"{self.cube}.translateX", time=f) for f in sample_frames
        }
        before_out_angle = cmds.keyTangent(
            curve, q=True, time=(120, 120), outAngle=True
        )[0]

        # Tie keyframes with bookend OUTSIDE the range
        cmds.playbackOptions(minTime=0, maxTime=600)
        AnimUtils.tie_keyframes([self.cube])

        # Sample at same points after tie
        after_values = {
            f: cmds.getAttr(f"{self.cube}.translateX", time=f) for f in sample_frames
        }

        # No interpolation changes anywhere
        for f in sample_frames:
            self.assertAlmostEqual(
                before_values[f],
                after_values[f],
                places=6,
                msg=f"Interpolation changed at frame {f}: "
                f"{before_values[f]:.8f} -> {after_values[f]:.8f}",
            )

    def test_tie_keyframes_custom_range(self):
        """Verify tie_keyframes respects the custom_range parameter."""
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=15, value=10)

        AnimUtils.tie_keyframes([self.cube], custom_range=(0, 20))

        keys = cmds.keyframe(
            str(self.cube), attribute="translateX", q=True, timeChange=True
        )
        self.assertIn(0.0, keys, "Bookend at custom start missing")
        self.assertIn(20.0, keys, "Bookend at custom end missing")

    def test_tie_keyframes_evaluates_correct_value(self):
        """Verify bookend keys are set to the evaluated curve value at the
        bookend frame, not just the nearest key's value.

        A curve from t=5 v=0 to t=15 v=10 should produce a bookend at t=0 with
        v=0 (the evaluation of the curve at t=0, which is the pre-infinity value).
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=5, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=15, value=10)

        AnimUtils.tie_keyframes([self.cube], custom_range=(0, 20))

        vals = cmds.keyframe(
            str(self.cube), attribute="translateX", q=True, valueChange=True
        )
        keys = cmds.keyframe(
            str(self.cube), attribute="translateX", q=True, timeChange=True
        )
        kv = dict(zip(keys, vals))

        # Pre-infinity for constant (default) should give 0.0 at t=0
        self.assertAlmostEqual(
            kv[0.0], 0.0, places=3, msg=f"Bookend at t=0 has wrong value: {kv[0.0]}"
        )
        # Post-infinity for constant should give 10.0 at t=20
        self.assertAlmostEqual(
            kv[20.0], 10.0, places=3, msg=f"Bookend at t=20 has wrong value: {kv[20.0]}"
        )

    def test_tie_keyframes_idempotent(self):
        """Verify calling tie_keyframes twice doesn't produce duplicate keys
        or corrupt tangent types.
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=3, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=8, value=5)

        AnimUtils.tie_keyframes([self.cube])
        keys_first = cmds.keyframe(
            str(self.cube), attribute="translateX", q=True, timeChange=True
        )
        vals_first = cmds.keyframe(
            str(self.cube), attribute="translateX", q=True, valueChange=True
        )

        # Tie again — should not add duplicate keys or change values
        AnimUtils.tie_keyframes([self.cube])
        keys_second = cmds.keyframe(
            str(self.cube), attribute="translateX", q=True, timeChange=True
        )
        vals_second = cmds.keyframe(
            str(self.cube), attribute="translateX", q=True, valueChange=True
        )

        self.assertEqual(
            keys_first,
            keys_second,
            f"Key times changed on second tie: {keys_first} -> {keys_second}",
        )
        for v1, v2 in zip(vals_first, vals_second):
            self.assertAlmostEqual(
                v1,
                v2,
                places=6,
                msg=f"Values changed on second tie: {vals_first} -> {vals_second}",
            )

    def test_tie_keyframes_bookend_tangent_is_flat(self):
        """Verify non-stepped bookend keys have flat tangent types for clean holds."""
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)
        pm.setKeyframe(self.cube, attribute="translateX", time=3, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=8, value=10)

        AnimUtils.tie_keyframes([self.cube])

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        keys = cmds.keyframe(curve, q=True, timeChange=True)
        out_types = cmds.keyTangent(curve, q=True, outTangentType=True)
        in_types = cmds.keyTangent(curve, q=True, inTangentType=True)
        kv = dict(zip(keys, zip(in_types, out_types)))

        # Bookend at start (t=1) should be flat
        self.assertEqual(
            kv[1.0][0], "flat", f"Start bookend in-tangent should be flat: {kv[1.0]}"
        )
        self.assertEqual(
            kv[1.0][1], "flat", f"Start bookend out-tangent should be flat: {kv[1.0]}"
        )

        # Bookend at end (t=10) should be flat
        self.assertEqual(
            kv[10.0][0], "flat", f"End bookend in-tangent should be flat: {kv[10.0]}"
        )
        self.assertEqual(
            kv[10.0][1], "flat", f"End bookend out-tangent should be flat: {kv[10.0]}"
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

        # Boundary tangents should be pinned to flat
        out_type_1 = cmds.keyTangent(
            curve_name, time=(1, 1), query=True, outTangentType=True
        )
        in_type_10 = cmds.keyTangent(
            curve_name, time=(10, 10), query=True, inTangentType=True
        )
        self.assertEqual(out_type_1[0], "flat", "Boundary out-tangent not pinned")
        self.assertEqual(in_type_10[0], "flat", "Boundary in-tangent not pinned")

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

    # ------------------------------------------------------------------
    # Regression tests for optimize_keys fixes (2026-03-05)
    # ------------------------------------------------------------------

    def test_get_static_curves_skips_unconnected(self):
        """Verify get_static_curves does NOT delete unconnected static curves.

        Bug: If an animation curve had no downstream connection (driven
        list empty), it was deleted unconditionally without checking the
        default value.
        Fixed: 2026-03-05
        """
        import maya.cmds as cmds

        # Create a static curve and disconnect it
        pm.setKeyframe(self.cube, attribute="translateZ", time=1, value=5)
        pm.setKeyframe(self.cube, attribute="translateZ", time=10, value=5)

        curves = cmds.listConnections(
            f"{self.cube}.translateZ", type="animCurve", source=True
        )
        self.assertTrue(curves, "Expected a curve to exist")
        curve = curves[0]

        # Disconnect the curve from the attribute
        conns = cmds.listConnections(
            curve, source=False, destination=True, plugs=True, connections=True
        )
        if conns:
            cmds.disconnectAttr(conns[0], conns[1])

        # Now the curve has no driven attribute — should NOT be deleted
        static = AnimUtils.get_static_curves([pm.PyNode(curve)])
        self.assertEqual(
            len(static),
            0,
            "Unconnected static curve should not be flagged for deletion",
        )

    def test_flat_key_removal_preserves_transition_tangent(self):
        """Verify flat key removal preserves tangent angle at segment boundary.

        Bug: After removing interior flat keys, Maya recalculated auto
        tangent slopes at the boundary using the new (sparser) neighbors,
        changing the curve shape at flat-to-animated transitions.
        Fixed: 2026-03-05
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Create animation: flat hold then ramp up.
        # Sparse keys so the tangent change is significant.
        pm.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=20, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=30, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=40, value=10)
        pm.setKeyframe(self.cube, attribute="translateX", time=50, value=20)

        curves = cmds.listConnections(
            f"{self.cube}.translateX", type="animCurve", source=True
        )
        curve = curves[0]

        # Record the OUT tangent angle at the last flat key (t=30)
        # before optimization
        out_angle_before = cmds.keyTangent(curve, q=True, time=(30, 30), outAngle=True)[
            0
        ]

        AnimUtils.get_redundant_flat_keys(
            [pm.PyNode(curve)],
            remove=True,
        )

        # After removal, the OUT tangent at t=30 should be preserved
        # as "fixed" with the same angle
        out_angle_after = cmds.keyTangent(curve, q=True, time=(30, 30), outAngle=True)[
            0
        ]
        out_type_after = cmds.keyTangent(
            curve, q=True, time=(30, 30), outTangentType=True
        )[0]

        self.assertEqual(
            out_type_after,
            "fixed",
            "Transition tangent should be set to 'fixed' after flat key removal",
        )
        self.assertAlmostEqual(
            out_angle_before,
            out_angle_after,
            places=2,
            msg="Transition tangent angle should be preserved after flat key removal",
        )

    def test_flat_key_removal_lock_false_prevents_tangent_coupling(self):
        """Verify flat key removal uses lock=False to prevent tangent coupling.

        Bug: After removing interior flat keys, transition restoration set
        outTangentType="fixed" with outAngle=X on boundary keys.  Maya's
        tangent lock coupled the in/out handles, silently overwriting the
        boundary "flat" inTangent with the transition angle.  Over long
        flat spans (thousands of frames), even a tiny non-zero angle
        on the boundary tangent creates massive drift (up to 6.5 degrees).
        Fixed: 2026-03-05
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Create a long flat segment followed by animation.
        # The flat segment spans many frames to amplify any tangent error.
        for t in range(0, 101, 10):
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=110, value=10)
        pm.setKeyframe(self.cube, attribute="translateX", time=120, value=20)

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Sample the flat region before optimization
        sample_frames = [0, 25, 50, 75, 100]
        before = {
            f: cmds.getAttr(f"{self.cube}.translateX", time=f) for f in sample_frames
        }

        AnimUtils.get_redundant_flat_keys([self.cube], remove=True, as_strings=True)

        # All flat-region samples must remain exactly 0 (no tangent coupling drift)
        for f in sample_frames:
            val = cmds.getAttr(f"{self.cube}.translateX", time=f)
            self.assertAlmostEqual(
                val,
                before[f],
                places=4,
                msg=f"Flat region drifted at frame {f}: {before[f]} -> {val}",
            )

    def test_tie_keyframes_bookend_beyond_range_uses_flat_hold(self):
        """Verify tie_keyframes creates flat holds when bookend extends
        beyond a curve's key range.

        Bug: When tie_keyframes inserted bookend keys far beyond a curve's
        actual key range, the original first/last key's infinity-facing
        tangent now interpolated toward the bookend instead of being unused.
        The restored auto-tangent angle created drift over the long
        bookend-to-original-key region.
        Fixed: 2026-03-05
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Create animation only in a narrow range (100-200)
        pm.setKeyframe(self.cube, attribute="translateX", time=100, value=5)
        pm.setKeyframe(self.cube, attribute="translateX", time=150, value=10)
        pm.setKeyframe(self.cube, attribute="translateX", time=200, value=5)

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Sample the boundary values before tie
        val_at_100 = cmds.getAttr(f"{self.cube}.translateX", time=100)
        val_at_200 = cmds.getAttr(f"{self.cube}.translateX", time=200)

        # Tie with a much wider range (bookends at 0 and 500)
        AnimUtils.tie_keyframes([self.cube], custom_range=(0, 500))

        # Bookend region should hold constant (flat tangent)
        for f in [0, 25, 50, 75]:
            val = cmds.getAttr(f"{self.cube}.translateX", time=f)
            self.assertAlmostEqual(
                val,
                val_at_100,
                places=4,
                msg=f"Pre-animation hold drifted at frame {f}: expected {val_at_100}, got {val}",
            )

        for f in [250, 350, 450, 500]:
            val = cmds.getAttr(f"{self.cube}.translateX", time=f)
            self.assertAlmostEqual(
                val,
                val_at_200,
                places=4,
                msg=f"Post-animation hold drifted at frame {f}: expected {val_at_200}, got {val}",
            )

    def test_optimize_then_tie_preserves_values(self):
        """Verify the full optimize_keys + tie_keyframes sequence preserves
        curve values with zero drift.

        Bug: optimize_keys thinned dense flat segments, then tie_keyframes
        inserted bookend keys beyond the curve range, exposing the
        original key's infinity-facing tangent.  Both operations had
        Maya tangent lock coupling bugs that compounded into large
        drift (6.5 degrees on production scenes).
        Fixed: 2026-03-05
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, attribute="translateX", clear=True)

        # Create a realistic curve: baked flat segment + animated region
        # Flat region: hundreds of identical keys (simulates constraint bake)
        for t in range(0, 201):
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=0)
        # Animated region
        pm.setKeyframe(self.cube, attribute="translateX", time=210, value=5)
        pm.setKeyframe(self.cube, attribute="translateX", time=220, value=10)
        pm.setKeyframe(self.cube, attribute="translateX", time=230, value=5)
        pm.setKeyframe(self.cube, attribute="translateX", time=240, value=0)
        # Another flat region
        for t in range(240, 401):
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=0)

        # Dense evaluation before pipeline
        sample_frames = list(range(0, 401, 5))
        before = {
            f: cmds.getAttr(f"{self.cube}.translateX", time=f) for f in sample_frames
        }

        # Run the same pipeline as the exporter
        AnimUtils.optimize_keys(
            [self.cube],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )
        AnimUtils.snap_keys_to_frames([self.cube])
        AnimUtils.tie_keyframes([self.cube], custom_range=(0, 400))

        # No value should drift
        max_diff = 0
        worst_frame = 0
        for f in sample_frames:
            val = cmds.getAttr(f"{self.cube}.translateX", time=f)
            diff = abs(val - before[f])
            if diff > max_diff:
                max_diff = diff
                worst_frame = f
            self.assertAlmostEqual(
                val,
                before[f],
                places=3,
                msg=f"Value drifted at frame {f}: {before[f]} -> {val} (diff={diff})",
            )

    def test_tie_keyframes_preserves_fixed_tangent_type_at_boundaries(self):
        """Verify tie_keyframes preserves 'fixed' tangent type at boundary keys
        instead of converting to 'flat'.

        Bug: COPILOT_BREAK_L_LOC exported with incorrect animation curves
        because tie_keyframes changed boundary tangent types from 'fixed'
        (FBX eTangentUser) to 'flat' (FBX eTangentFlat). Although visually
        equivalent in Maya (both are zero-angle), FBX importers treat these
        tangent modes differently, corrupting curves on import.
        Fixed: 2026-06-05
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, clear=True)

        # Create animation with 'fixed' tangent types (mirroring production data)
        times_vals = [
            (10, 0.0),
            (15, 5.0),
            (20, 10.0),
            (25, 5.0),
            (30, 0.0),
        ]
        for t, v in times_vals:
            pm.setKeyframe(self.cube, attribute="translateX", time=t, value=v)

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Set all tangents to 'fixed' with specific angles
        cmds.keyTangent(curve, edit=True, inTangentType="fixed", outTangentType="fixed")
        # Set boundary-facing tangent angles to 0 (common in baked animation)
        cmds.keyTangent(curve, time=(10, 10), inAngle=0.0, lock=False)
        cmds.keyTangent(curve, time=(30, 30), outAngle=0.0, lock=False)

        # Verify setup: all tangent types should be 'fixed'
        out_types_pre = cmds.keyTangent(curve, q=True, outTangentType=True)
        in_types_pre = cmds.keyTangent(curve, q=True, inTangentType=True)
        self.assertTrue(
            all(t == "fixed" for t in out_types_pre),
            f"Setup failed: expected all fixed out types, got {out_types_pre}",
        )
        self.assertTrue(
            all(t == "fixed" for t in in_types_pre),
            f"Setup failed: expected all fixed in types, got {in_types_pre}",
        )

        # Tie with wider range (bookends will be added before/after)
        AnimUtils.tie_keyframes([self.cube], custom_range=(0, 40))

        # After tie: ALL original keys should still have 'fixed' tangent type
        # (only bookend keys themselves should be 'flat')
        times_post = cmds.keyframe(curve, q=True, timeChange=True)
        out_types_post = cmds.keyTangent(curve, q=True, outTangentType=True)
        in_types_post = cmds.keyTangent(curve, q=True, inTangentType=True)

        # Find indices of original keys (not bookend keys)
        for orig_t, _ in times_vals:
            idx = None
            for i, t in enumerate(times_post):
                if abs(t - orig_t) < 0.01:
                    idx = i
                    break
            self.assertIsNotNone(idx, f"Original key at t={orig_t} missing")

            self.assertEqual(
                in_types_post[idx],
                "fixed",
                f"Key t={orig_t} in_type changed from 'fixed' to "
                f"'{in_types_post[idx]}' — FBX tangent mode corrupted",
            )
            self.assertEqual(
                out_types_post[idx],
                "fixed",
                f"Key t={orig_t} out_type changed from 'fixed' to "
                f"'{out_types_post[idx]}' — FBX tangent mode corrupted",
            )

        # Verify the boundary-facing angles are flat (zero) for constant hold
        idx_first = next(i for i, t in enumerate(times_post) if abs(t - 10) < 0.01)
        idx_last = next(i for i, t in enumerate(times_post) if abs(t - 30) < 0.01)
        in_angles = cmds.keyTangent(curve, q=True, inAngle=True)
        out_angles = cmds.keyTangent(curve, q=True, outAngle=True)
        self.assertAlmostEqual(
            in_angles[idx_first],
            0.0,
            places=2,
            msg="First key in-angle should be flat (0) facing bookend",
        )
        self.assertAlmostEqual(
            out_angles[idx_last],
            0.0,
            places=2,
            msg="Last key out-angle should be flat (0) facing bookend",
        )

    def test_tie_keyframes_preserves_linear_tangent_type_at_boundaries(self):
        """Verify tie_keyframes preserves 'linear' tangent type at boundaries.

        Bug: Same as test_tie_keyframes_preserves_fixed_tangent_type_at_boundaries
        but verifying linear tangent types are also preserved (not converted
        to flat). Production data has linear tangent sections adjacent to
        fixed tangent sections.
        Fixed: 2026-06-05
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, clear=True)

        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=20, value=10)
        pm.setKeyframe(self.cube, attribute="translateX", time=30, value=0)

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateX", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Set boundary tangents to linear
        cmds.keyTangent(
            curve, edit=True, inTangentType="linear", outTangentType="linear"
        )

        AnimUtils.tie_keyframes([self.cube], custom_range=(0, 40))

        times_post = cmds.keyframe(curve, q=True, timeChange=True)
        in_types_post = cmds.keyTangent(curve, q=True, inTangentType=True)
        out_types_post = cmds.keyTangent(curve, q=True, outTangentType=True)

        # First original key at t=10 should keep 'linear' in-tangent
        idx_10 = next(i for i, t in enumerate(times_post) if abs(t - 10) < 0.01)
        self.assertEqual(
            in_types_post[idx_10],
            "linear",
            f"Key t=10 in_type changed from 'linear' to '{in_types_post[idx_10]}'",
        )

        # Last original key at t=30 should keep 'linear' out-tangent
        idx_30 = next(i for i, t in enumerate(times_post) if abs(t - 30) < 0.01)
        self.assertEqual(
            out_types_post[idx_30],
            "linear",
            f"Key t=30 out_type changed from 'linear' to '{out_types_post[idx_30]}'",
        )

    def test_optimize_keys_baked_auto_tangent_flat_region_no_drift(self):
        """Verify optimize_keys preserves flat-region values with baked
        per-frame auto-tangent keys (COPILOT_BREAK production pattern).

        Bug: Production objects (COPILOT_BREAK_L_LOC, R_LOC) had 27,701
        per-frame baked keys with auto tangent type. After optimize_keys
        removed redundant flat keys (~99% reduction), surviving animated
        keys kept 'auto' tangent type. FBX export maps Maya 'auto' to
        FBX eTangentAuto which uses a different algorithm, producing
        sinusoidal waves over long spans between sparse keys.
        Fix: Phase C freezes surviving auto tangents to 'fixed' (capturing
        their current angles) while skipping flat boundary tangents.
        Fixed: 2026-03-06
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, clear=True)

        # Replicate the production pattern: per-frame baked keys with
        # auto tangents, a long flat region, an animated region, then
        # another flat region.  Use shorter span than production
        # (500 frames vs 27,701) but same structure.
        flat_val = 0.717680
        # Flat region 1: frames 0-299 (300 per-frame keys, all same value)
        for t in range(0, 300):
            pm.setKeyframe(self.cube, attribute="translateY", time=t, value=flat_val)
        # Animated region: frames 300-349 (ramp up then back)
        for t in range(300, 350):
            offset = (t - 300) / 50.0
            val = flat_val + offset * 10.0  # ramp from 0.717 to ~10.717
            pm.setKeyframe(self.cube, attribute="translateY", time=t, value=val)
        # Flat region 2: frames 350-499 (150 per-frame keys)
        end_val = flat_val + 10.0
        for t in range(350, 500):
            pm.setKeyframe(self.cube, attribute="translateY", time=t, value=end_val)

        # All keys should have auto tangent type (default for setKeyframe)
        curve = (
            cmds.listConnections(
                f"{self.cube}.translateY", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)
        key_count_before = cmds.keyframe(curve, q=True, keyframeCount=True)
        self.assertEqual(key_count_before, 500)

        out_types = cmds.keyTangent(curve, q=True, outTangentType=True)
        self.assertTrue(
            all(t == "auto" for t in out_types),
            "Setup: expected all auto tangent types",
        )

        # Sample values before optimization
        sample_frames = list(range(0, 500, 5))  # every 5th frame
        before = {
            f: cmds.getAttr(f"{self.cube}.translateY", time=f) for f in sample_frames
        }

        # Run optimize_keys (same as production pipeline)
        AnimUtils.optimize_keys(
            [self.cube],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )

        # Keys should be significantly reduced
        key_count_after = cmds.keyframe(curve, q=True, keyframeCount=True)
        self.assertLess(
            key_count_after,
            key_count_before,
            "optimize_keys should remove redundant flat keys",
        )

        # Flat boundary tangents must remain flat (inward-facing)
        times_after = cmds.keyframe(curve, q=True, timeChange=True)
        flat_start = min(times_after)  # t=0
        out_type_start = cmds.keyTangent(
            curve, q=True, time=(flat_start, flat_start), outTangentType=True
        )[0]
        self.assertEqual(
            out_type_start,
            "flat",
            f"Flat segment start at t={flat_start} should have 'flat' "
            f"out-tangent (inward-facing), got '{out_type_start}'",
        )

        # Non-boundary surviving keys must NOT be 'auto' — they must be
        # 'fixed' so FBX exports them as eTangentUser (explicit angles).
        post_out_types = cmds.keyTangent(curve, q=True, outTangentType=True)
        auto_count = sum(1 for t in post_out_types if t == "auto")
        self.assertEqual(
            auto_count,
            0,
            f"After optimize_keys, {auto_count} surviving keys still have "
            f"'auto' out-tangent — FBX will reinterpret these with its own "
            f"algorithm, corrupting curve shape",
        )

        # All evaluated values must match within tight tolerance.
        max_diff = 0
        worst_frame = 0
        for f in sample_frames:
            val = cmds.getAttr(f"{self.cube}.translateY", time=f)
            diff = abs(val - before[f])
            if diff > max_diff:
                max_diff = diff
                worst_frame = f

        self.assertLess(
            max_diff,
            0.001,
            f"Value drifted after optimize_keys: max diff={max_diff:.6f} "
            f"at frame {worst_frame}",
        )

    def test_full_pipeline_baked_auto_keys_preserves_values(self):
        """Verify the full export pipeline (optimize_keys + tie_keyframes)
        preserves values for baked per-frame auto-tangent keys.

        Bug: After optimize_keys, surviving keys had 'auto' tangent type.
        FBX export maps Maya 'auto' to FBX eTangentAuto which uses a
        different algorithm, producing sinusoidal waves over sparse keys.
        Fix: Phase C freezes auto→fixed (skipping flat boundaries),
        then tie_keyframes adds bookend keys. The full pipeline must
        produce zero auto tangents and zero value drift.
        Fixed: 2026-03-06
        """
        import maya.cmds as cmds

        pm.cutKey(self.cube, clear=True)

        flat_val = 0.717680
        # Flat region 1: frames 100-399
        for t in range(100, 400):
            pm.setKeyframe(self.cube, attribute="translateY", time=t, value=flat_val)
        # Animated region: frames 400-449
        for t in range(400, 450):
            offset = (t - 400) / 50.0
            pm.setKeyframe(
                self.cube,
                attribute="translateY",
                time=t,
                value=flat_val + offset * 10.0,
            )
        # Flat region 2: frames 450-599
        end_val = flat_val + 10.0
        for t in range(450, 600):
            pm.setKeyframe(self.cube, attribute="translateY", time=t, value=end_val)

        curve = (
            cmds.listConnections(
                f"{self.cube}.translateY", type="animCurve", source=True
            )
            or [None]
        )[0]
        self.assertIsNotNone(curve)

        # Sample before pipeline
        sample_frames = list(range(100, 600, 5))
        before = {
            f: cmds.getAttr(f"{self.cube}.translateY", time=f) for f in sample_frames
        }

        # Run the same pipeline the exporter uses:
        AnimUtils.optimize_keys(
            [self.cube],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )
        AnimUtils.tie_keyframes([self.cube], custom_range=(0, 700))

        # No surviving keys should have 'auto' tangent type
        post_out = cmds.keyTangent(curve, q=True, outTangentType=True) or []
        post_in = cmds.keyTangent(curve, q=True, inTangentType=True) or []
        auto_out = sum(1 for t in post_out if t == "auto")
        auto_in = sum(1 for t in post_in if t == "auto")
        self.assertEqual(
            auto_out,
            0,
            f"{auto_out} keys still have 'auto' out-tangent after pipeline",
        )
        self.assertEqual(
            auto_in,
            0,
            f"{auto_in} keys still have 'auto' in-tangent after pipeline",
        )

        # All values in original range must match within tolerance
        max_diff = 0
        worst_frame = 0
        for f in sample_frames:
            val = cmds.getAttr(f"{self.cube}.translateY", time=f)
            diff = abs(val - before[f])
            if diff > max_diff:
                max_diff = diff
                worst_frame = f

        self.assertLess(
            max_diff,
            0.001,
            f"Pipeline corrupted values: max diff={max_diff:.6f} "
            f"at frame {worst_frame}",
        )

        # Also check bookend regions hold constant
        val_at_start = cmds.getAttr(f"{self.cube}.translateY", time=100)
        val_at_end = cmds.getAttr(f"{self.cube}.translateY", time=599)
        for f in [0, 25, 50, 75]:
            val = cmds.getAttr(f"{self.cube}.translateY", time=f)
            self.assertAlmostEqual(
                val,
                val_at_start,
                places=3,
                msg=f"Pre-range bookend drifted at frame {f}",
            )
        for f in [625, 650, 675, 700]:
            val = cmds.getAttr(f"{self.cube}.translateY", time=f)
            self.assertAlmostEqual(
                val,
                val_at_end,
                places=3,
                msg=f"Post-range bookend drifted at frame {f}",
            )

    # =========================================================================
    # Deterministic Bake → Optimize Tests
    # =========================================================================

    def test_bake_static_constraint_then_optimize(self):
        """Bake a static constraint → optimize removes interior flat keys,
        preserves held position at non-default value.

        Setup: locator at (5, 3, -2), cube point-constrained to it.
        Bake frames 1-30 → 30 identical keys per channel.
        optimize_keys → interior flat keys removed, boundary keys kept.
        Expected: value preserved exactly, key count reduced to 2 per channel.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        pm.playbackOptions(minTime=1, maxTime=30)

        loc = pm.spaceLocator(name="static_target")
        pm.setAttr(loc + ".translateX", 5)
        pm.setAttr(loc + ".translateY", 3)
        pm.setAttr(loc + ".translateZ", -2)

        driven = pm.polyCube(name="static_driven")[0]
        pm.pointConstraint(loc, driven, maintainOffset=False)

        # Bake
        baker = SmartBake(
            objects=[str(driven)],
            sample_by=1,
            optimize_keys=False,  # We'll optimize separately for clarity
            use_override_layer=False,
            delete_inputs=True,
        )
        baker.execute()

        # After bake: 30 keys per channel, all identical
        for attr in ("translateX", "translateY", "translateZ"):
            keys = cmds.keyframe(str(driven), attribute=attr, q=True, timeChange=True)
            self.assertEqual(len(keys), 30, f"{attr}: expected 30 keys after bake")

        # Optimize
        AnimUtils.optimize_keys(
            [driven],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )

        # After optimize: 2 keys per channel (boundary), values preserved
        expected = {"translateX": 5.0, "translateY": 3.0, "translateZ": -2.0}
        for attr, expected_val in expected.items():
            keys = cmds.keyframe(str(driven), attribute=attr, q=True, timeChange=True)
            self.assertEqual(
                len(keys),
                2,
                f"{attr}: expected 2 boundary keys after optimize, got {len(keys)}",
            )
            self.assertAlmostEqual(keys[0], 1.0, places=1)
            self.assertAlmostEqual(keys[-1], 30.0, places=1)
            # Value must be preserved
            for t in keys:
                val = cmds.getAttr(f"{driven}.{attr}", time=t)
                self.assertAlmostEqual(
                    val,
                    expected_val,
                    places=3,
                    msg=f"{attr} at t={t}: expected {expected_val}, got {val}",
                )

    def test_bake_moving_constraint_then_optimize(self):
        """Bake a moving constraint with animated + flat regions →
        optimize removes flat region interior, preserves animated region.

        Setup: locator.tx animated 0→10 over frames 1-20, holds at 10
        for frames 20-40. Cube point-constrained.
        Bake frames 1-40 → 40 per-frame keys.
        optimize_keys → animated keys preserved, flat hold reduced.
        Expected: animated portion intact, flat region has 2 boundary keys.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        pm.playbackOptions(minTime=1, maxTime=40)

        loc = pm.spaceLocator(name="moving_target")
        # Animate tx: linear ramp then hold
        pm.setKeyframe(loc, attribute="translateX", time=1, value=0)
        pm.setKeyframe(loc, attribute="translateX", time=20, value=10)
        pm.setKeyframe(loc, attribute="translateX", time=40, value=10)
        cmds.keyTangent(
            str(loc) + ".translateX",
            edit=True,
            inTangentType="linear",
            outTangentType="linear",
        )

        driven = pm.polyCube(name="moving_driven")[0]
        pm.pointConstraint(loc, driven, maintainOffset=False)

        # Sample evaluated values before bake (ground truth from constraint)
        sample_frames = list(range(1, 41))
        truth = {f: cmds.getAttr(f"{driven}.translateX", time=f) for f in sample_frames}

        # Bake
        baker = SmartBake(
            objects=[str(driven)],
            sample_by=1,
            optimize_keys=False,
            use_override_layer=False,
            delete_inputs=True,
        )
        baker.execute()

        keys_after_bake = cmds.keyframe(
            str(driven), attribute="translateX", q=True, timeChange=True
        )
        self.assertEqual(
            len(keys_after_bake), 40, "Expected 40 per-frame keys after bake"
        )

        # Optimize
        AnimUtils.optimize_keys(
            [driven],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )

        keys_after_opt = cmds.keyframe(
            str(driven), attribute="translateX", q=True, timeChange=True
        )
        self.assertLess(
            len(keys_after_opt),
            len(keys_after_bake),
            "optimize_keys should reduce key count in flat hold region",
        )

        # All evaluated values must match the original constraint output
        max_diff = 0
        for f in sample_frames:
            val = cmds.getAttr(f"{driven}.translateX", time=f)
            diff = abs(val - truth[f])
            if diff > max_diff:
                max_diff = diff

        self.assertLess(
            max_diff,
            0.01,
            f"Values drifted after optimize: max diff={max_diff:.6f}",
        )

        # Flat region (frames 20-40) should have boundary keys only
        flat_keys = [k for k in keys_after_opt if k >= 20.0]
        self.assertLessEqual(
            len(flat_keys),
            3,
            f"Flat region should have ≤3 keys (boundaries), got {len(flat_keys)}",
        )

    def test_bake_sine_expression_then_optimize(self):
        """Bake a sine-wave expression → optimize removes very few keys
        since all values differ.

        Setup: cube.tx = sin(time * 0.3) * 10 via expression.
        Bake frames 1-60 → 60 unique-value keys.
        optimize_keys → minimal reduction (all keys needed for shape).
        Expected: key count stays high, max value deviation < 0.5.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        pm.playbackOptions(minTime=1, maxTime=60)

        driven = pm.polyCube(name="sine_driven")[0]
        pm.expression(
            s=f"{driven}.translateX = sin(frame * 0.3) * 10;",
            name="sine_expr",
        )

        # Sample ground truth
        sample_frames = list(range(1, 61))
        truth = {f: cmds.getAttr(f"{driven}.translateX", time=f) for f in sample_frames}

        # Bake
        baker = SmartBake(
            objects=[str(driven)],
            sample_by=1,
            optimize_keys=False,
            use_override_layer=False,
            delete_inputs=True,
        )
        baker.execute()

        keys_after_bake = cmds.keyframe(
            str(driven), attribute="translateX", q=True, timeChange=True
        )
        self.assertEqual(len(keys_after_bake), 60)

        # Optimize
        AnimUtils.optimize_keys(
            [driven],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )

        keys_after_opt = cmds.keyframe(
            str(driven), attribute="translateX", q=True, timeChange=True
        )
        # Sine wave has no flat segments — almost all keys should survive
        self.assertGreater(
            len(keys_after_opt),
            50,
            f"Sine wave should retain most keys, only {len(keys_after_opt)} of 60 survived",
        )

        # Values must still match the original expression output
        max_diff = 0
        for f in sample_frames:
            val = cmds.getAttr(f"{driven}.translateX", time=f)
            diff = abs(val - truth[f])
            if diff > max_diff:
                max_diff = diff

        self.assertLess(
            max_diff,
            0.5,
            f"Sine wave values drifted: max diff={max_diff:.4f}",
        )

    def test_bake_stepped_constraint_then_optimize(self):
        """Bake a constraint driven by stepped animation → optimize preserves
        flat segments with stepped tangent types.

        Setup: locator.tx has stepped keys: 0 at f1, 5 at f10, 0 at f20.
        Cube point-constrained. Bake frames 1-20.
        optimize_keys → flat segments reduced, stepped tangents preserved.
        Expected: segments hold constant between steps, key count reduced.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        pm.playbackOptions(minTime=1, maxTime=20)

        loc = pm.spaceLocator(name="stepped_target")
        pm.setKeyframe(loc, attribute="translateX", time=1, value=0)
        pm.setKeyframe(loc, attribute="translateX", time=10, value=5)
        pm.setKeyframe(loc, attribute="translateX", time=20, value=0)
        cmds.keyTangent(str(loc) + ".translateX", edit=True, outTangentType="step")

        driven = pm.polyCube(name="stepped_driven")[0]
        pm.pointConstraint(loc, driven, maintainOffset=False)

        # Ground truth: stepped holds
        truth = {}
        for f in range(1, 21):
            truth[f] = cmds.getAttr(f"{driven}.translateX", time=f)
        # Verify stepped behavior in driver
        self.assertAlmostEqual(truth[1], 0.0, places=3)
        self.assertAlmostEqual(truth[5], 0.0, places=3)  # holds 0 until f10
        self.assertAlmostEqual(truth[10], 5.0, places=3)
        self.assertAlmostEqual(truth[15], 5.0, places=3)  # holds 5 until f20

        # Bake
        baker = SmartBake(
            objects=[str(driven)],
            sample_by=1,
            optimize_keys=False,
            use_override_layer=False,
            delete_inputs=True,
        )
        baker.execute()

        keys_after_bake = cmds.keyframe(
            str(driven), attribute="translateX", q=True, timeChange=True
        )
        self.assertEqual(len(keys_after_bake), 20)

        # Optimize
        AnimUtils.optimize_keys(
            [driven],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )

        keys_after_opt = cmds.keyframe(
            str(driven), attribute="translateX", q=True, timeChange=True
        )
        self.assertLess(
            len(keys_after_opt),
            20,
            "optimize should remove interior flat keys in stepped hold regions",
        )

        # Values must still hold at the step levels
        for f in range(1, 21):
            val = cmds.getAttr(f"{driven}.translateX", time=f)
            self.assertAlmostEqual(
                val,
                truth[f],
                places=2,
                msg=f"Stepped hold corrupted at frame {f}: "
                f"expected {truth[f]}, got {val}",
            )

    def test_bake_mixed_channels_then_optimize(self):
        """Bake object with animated X, static Y/Z → optimize handles
        each channel type correctly.

        Setup: locator animated on tx (0→10→0), static at ty=3, tz=-2.
        Cube point-constrained. Bake frames 1-30.
        optimize_keys → tx keeps animated keys, ty/tz reduced to boundaries.
        Expected: tx values match constraint, ty/tz hold positions preserved.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        pm.playbackOptions(minTime=1, maxTime=30)

        loc = pm.spaceLocator(name="mixed_target")
        # Animate tx
        pm.setKeyframe(loc, attribute="translateX", time=1, value=0)
        pm.setKeyframe(loc, attribute="translateX", time=15, value=10)
        pm.setKeyframe(loc, attribute="translateX", time=30, value=0)
        # Static ty/tz
        pm.setAttr(loc + ".translateY", 3)
        pm.setAttr(loc + ".translateZ", -2)

        driven = pm.polyCube(name="mixed_driven")[0]
        pm.pointConstraint(loc, driven, maintainOffset=False)

        # Ground truth
        sample_frames = list(range(1, 31))
        truth_tx = {
            f: cmds.getAttr(f"{driven}.translateX", time=f) for f in sample_frames
        }

        # Bake
        baker = SmartBake(
            objects=[str(driven)],
            sample_by=1,
            optimize_keys=False,
            use_override_layer=False,
            delete_inputs=True,
        )
        baker.execute()

        # Optimize
        AnimUtils.optimize_keys(
            [driven],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )

        # tx: animated keys preserved, values match
        tx_keys = cmds.keyframe(
            str(driven), attribute="translateX", q=True, timeChange=True
        )
        self.assertGreater(len(tx_keys), 2, "tx should retain animated keys")
        max_diff = 0
        for f in sample_frames:
            val = cmds.getAttr(f"{driven}.translateX", time=f)
            diff = abs(val - truth_tx[f])
            if diff > max_diff:
                max_diff = diff
        self.assertLess(
            max_diff,
            0.01,
            f"tx values drifted: max diff={max_diff:.6f}",
        )

        # ty: non-default hold at 3.0 → curve preserved with 2 boundary keys
        ty_curves = cmds.listConnections(
            f"{driven}.translateY", type="animCurve", source=True
        )
        self.assertTrue(ty_curves, "ty curve should exist (non-default hold at 3.0)")
        ty_keys = cmds.keyframe(
            str(driven), attribute="translateY", q=True, timeChange=True
        )
        self.assertEqual(
            len(ty_keys), 2, f"ty should have 2 boundary keys, got {len(ty_keys)}"
        )
        val = cmds.getAttr(f"{driven}.translateY", time=15)
        self.assertAlmostEqual(val, 3.0, places=3)

        # tz: non-default hold at -2.0 → curve preserved with 2 boundary keys
        tz_curves = cmds.listConnections(
            f"{driven}.translateZ", type="animCurve", source=True
        )
        self.assertTrue(tz_curves, "tz curve should exist (non-default hold at -2.0)")
        tz_keys = cmds.keyframe(
            str(driven), attribute="translateZ", q=True, timeChange=True
        )
        self.assertEqual(
            len(tz_keys), 2, f"tz should have 2 boundary keys, got {len(tz_keys)}"
        )
        val = cmds.getAttr(f"{driven}.translateZ", time=15)
        self.assertAlmostEqual(val, -2.0, places=3)

    def test_bake_optimize_full_pipeline_preserves_values(self):
        """Full export pipeline: bake → optimize → snap → tie, with known
        animation. Verifies zero value drift end-to-end.

        Setup: locator animated tx (sin curve), ty (linear ramp), tz (static 7).
        Cube point-constrained. Run full pipeline.
        Expected: all evaluated values match pre-pipeline truth within tolerance.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        pm.playbackOptions(minTime=1, maxTime=60)

        loc = pm.spaceLocator(name="pipe_target")
        # tx: sine wave via keys
        for f in range(1, 61):
            val = math.sin(f * 0.2) * 5
            pm.setKeyframe(loc, attribute="translateX", time=f, value=val)
        # ty: linear ramp
        pm.setKeyframe(loc, attribute="translateY", time=1, value=0)
        pm.setKeyframe(loc, attribute="translateY", time=60, value=20)
        cmds.keyTangent(
            str(loc) + ".translateY",
            edit=True,
            inTangentType="linear",
            outTangentType="linear",
        )
        # tz: static
        pm.setAttr(loc + ".translateZ", 7)

        driven = pm.polyCube(name="pipe_driven")[0]
        pm.pointConstraint(loc, driven, maintainOffset=False)

        # Ground truth before bake
        sample_frames = list(range(1, 61))
        truth = {}
        for attr in ("translateX", "translateY", "translateZ"):
            truth[attr] = {
                f: cmds.getAttr(f"{driven}.{attr}", time=f) for f in sample_frames
            }

        # Bake
        baker = SmartBake(
            objects=[str(driven)],
            sample_by=1,
            optimize_keys=False,
            use_override_layer=False,
            delete_inputs=True,
        )
        baker.execute()

        # Full pipeline (same as scene exporter)
        AnimUtils.optimize_keys(
            [driven],
            remove_static_curves=True,
            remove_flat_keys=True,
            recursive=False,
            quiet=True,
        )
        AnimUtils.snap_keys_to_frames([driven])
        AnimUtils.tie_keyframes([driven], custom_range=(0, 70))

        # Verify all channels
        for attr in ("translateX", "translateY", "translateZ"):
            max_diff = 0
            worst_frame = 0
            for f in sample_frames:
                val = cmds.getAttr(f"{driven}.{attr}", time=f)
                diff = abs(val - truth[attr][f])
                if diff > max_diff:
                    max_diff = diff
                    worst_frame = f

            self.assertLess(
                max_diff,
                0.5,
                f"{attr}: pipeline corrupted values, max diff={max_diff:.4f} "
                f"at frame {worst_frame}",
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
        # snap_keys_to_frames shifts sub-frame keys to whole frames,
        # which changes spline interpolation over long arcs (e.g. a
        # wheel with ±120° keys shifted by 0.4–0.8 frames can drift
        # ~1.1° at intermediate frames).  Use a tolerance that still
        # catches real corruption (the 6.5° tangent-lock bug) while
        # accepting inherent snap drift.
        self._compare_snapshots(before, after, tolerance=1.5, label="full-pipeline: ")

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
