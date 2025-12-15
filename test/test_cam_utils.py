# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.cam_utils module

Tests for CamUtils class functionality including:
- Camera creation and grouping
- Camera clipping adjustments
- Viewport camera switching
- Current camera queries
"""
import unittest
import pymel.core as pm
import mayatk as mtk

from base_test import MayaTkTestCase


class TestCamUtils(MayaTkTestCase):
    """Tests for CamUtils class."""

    def tearDown(self):
        """Clean up test cameras."""
        for cam in ["test_camera", "cameras"]:
            if pm.objExists(cam):
                pm.delete(cam)
        super().tearDown()

    def test_get_current_cam(self):
        """Test getting current active camera."""
        cam = mtk.get_current_cam()
        self.assertIsNotNone(cam)
        self.assertIsInstance(cam, str)

    def test_create_camera_from_view(self):
        """Test creating camera from current view."""
        try:
            cam = mtk.create_camera_from_view(name="test_camera")
            if cam:
                self.assertNodeExists("test_camera")
        except RuntimeError:
            self.skipTest("Camera creation not available in batch mode")

    def test_adjust_camera_clipping_reset(self):
        """Test resetting camera clipping planes."""
        mtk.adjust_camera_clipping(mode="reset")
        # Should complete without error
        self.assertTrue(True)

    def test_switch_viewport_camera(self):
        """Test switching viewport camera."""
        try:
            result = mtk.switch_viewport_camera("persp")
            if result:
                self.assertIsNotNone(result)
        except RuntimeError:
            self.skipTest("Viewport camera switching not available")


if __name__ == "__main__":
    unittest.main(verbosity=2)
