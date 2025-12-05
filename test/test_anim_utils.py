# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.anim_utils module

Tests for AnimUtils class functionality including:
- Keyframe operations
- Animation curve queries
- Key optimization
- Key snapping and scaling
"""
import unittest
import pymel.core as pm
import mayatk as mtk

from base_test import MayaTkTestCase


class TestAnimUtils(MayaTkTestCase):
    """Tests for AnimUtils class."""

    def setUp(self):
        """Set up test scene with animated object."""
        super().setUp()
        self.cube = pm.polyCube(name="test_anim_cube")[0]

        # Create simple animation
        pm.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateX", time=10, value=10)
        pm.setKeyframe(self.cube, attribute="translateY", time=1, value=0)
        pm.setKeyframe(self.cube, attribute="translateY", time=10, value=5)

    def tearDown(self):
        """Clean up."""
        if pm.objExists("test_anim_cube"):
            pm.delete("test_anim_cube")
        super().tearDown()

    def test_get_anim_curves(self):
        """Test getting animation curves from object."""
        curves = mtk.get_anim_curves([self.cube])
        self.assertIsInstance(curves, list)
        self.assertGreater(len(curves), 0)

    def test_get_keyframe_times(self):
        """Test getting keyframe times."""
        times = mtk.get_keyframe_times([self.cube])
        self.assertIn(1.0, times)
        self.assertIn(10.0, times)

    def test_snap_keys_to_frames(self):
        """Test snapping keys to whole frame values."""
        # Add a key at fractional time
        pm.setKeyframe(self.cube, attribute="translateX", time=5.5, value=5)

        count = mtk.snap_keys_to_frames([self.cube])
        self.assertGreater(count, 0)

    def test_set_current_frame(self):
        """Test setting current timeline frame."""
        frame = mtk.set_current_frame(5.0)
        current = pm.currentTime(query=True)
        self.assertEqual(current, 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
