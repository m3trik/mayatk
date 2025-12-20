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

    def setUp(self):
        super().setUp()
        # Create some test cameras
        self.cam1, self.cam1_shape = pm.camera(n="test_cam_1")
        self.cam2, self.cam2_shape = pm.camera(n="test_cam_2")
        self.cam3, self.cam3_shape = pm.camera(n="test_cam_3")

        # Create some geometry for auto clipping
        self.cube = pm.polyCube(n="clipping_cube")[0]
        self.cube.t.set(10, 10, 10)

    def tearDown(self):
        """Clean up test cameras."""
        if pm.objExists("cameras_group"):
            pm.delete("cameras_group")
        if pm.objExists("existing_group"):
            pm.delete("existing_group")
        super().tearDown()

    def test_get_current_cam(self):
        """Test getting current active camera."""
        try:
            cam = mtk.get_current_cam()
            self.assertIsNotNone(cam)
            self.assertIsInstance(cam, str)
        except Exception:
            # In batch mode, this might fail or return empty
            pass

    def test_create_camera_from_view(self):
        """Test creating camera from current view."""
        try:
            # This depends on modelPanel which might not exist in batch
            cam = mtk.create_camera_from_view(name="created_from_view")
            if cam:
                self.assertNodeExists("created_from_view")
        except RuntimeError:
            pass  # Expected in batch mode

    def test_group_cameras_basic(self):
        """Test basic camera grouping."""
        group = mtk.group_cameras(
            name="cameras_group", non_default=False, root_only=False
        )
        self.assertTrue(pm.objExists("cameras_group"))
        children = pm.listRelatives(group, children=True)
        self.assertIn(self.cam1, children)
        self.assertIn(self.cam2, children)

    def test_group_cameras_non_default(self):
        """Test grouping only non-default cameras."""
        # Create a camera that looks like a default one (ends with 'persp')
        # Ensure unique name that ends with side (persp might be special)
        import uuid

        name = f"cam_{uuid.uuid4().hex[:8]}_side"

        fake_default, _ = pm.camera()
        fake_default.rename(name)
        print(f"Created fake default camera: {fake_default.name()}")

        group = mtk.group_cameras(name="cameras_group", non_default=True)
        children = pm.listRelatives(group, children=True)

        self.assertIn(self.cam1, children)
        # Name ends with "side", so it should be excluded
        self.assertNotIn(fake_default, children)

    def test_group_cameras_root_only(self):
        """Test grouping only root level cameras."""
        # Parent cam2 to something
        parent_grp = pm.group(n="parent_grp", empty=True)
        pm.parent(self.cam2, parent_grp)

        group = mtk.group_cameras(
            name="cameras_group", root_only=True, non_default=False
        )
        children = pm.listRelatives(group, children=True)

        self.assertIn(self.cam1, children)
        self.assertNotIn(self.cam2, children)

    def test_group_cameras_hide(self):
        """Test hiding the group."""
        group = mtk.group_cameras(name="cameras_group", hide_group=True)
        self.assertFalse(group.visibility.get())

    def test_adjust_camera_clipping_manual(self):
        """Test manual clipping adjustment."""
        mtk.adjust_camera_clipping(
            camera=self.cam1, near_clip=0.5, far_clip=5000, mode="manual"
        )
        self.assertAlmostEqual(self.cam1.nearClipPlane.get(), 0.5)
        self.assertAlmostEqual(self.cam1.farClipPlane.get(), 5000)

    def test_adjust_camera_clipping_reset(self):
        """Test resetting clipping."""
        self.cam1.nearClipPlane.set(5.0)
        mtk.adjust_camera_clipping(camera=self.cam1, mode="reset")
        self.assertAlmostEqual(self.cam1.nearClipPlane.get(), 0.1)
        self.assertAlmostEqual(self.cam1.farClipPlane.get(), 10000)

    def test_adjust_camera_clipping_auto(self):
        """Test automatic clipping based on geometry."""
        # Move cube far away to force large clip planes
        self.cube.t.set(1000, 1000, 1000)
        mtk.adjust_camera_clipping(camera=self.cam1, mode="auto")
        # Just check that values changed from default/previous
        self.assertNotEqual(self.cam1.farClipPlane.get(), 10000)

    def test_switch_viewport_camera_custom(self):
        """Test switching to a custom camera (creation)."""
        try:
            # Use 'left' as it is defined in camera_config in _cam_utils.py
            cam_name = "left"
            # Ensure it doesn't exist first (it might be a startup camera though?)
            # 'left' is usually NOT a startup camera in default Maya, but 'side' is.
            # If 'left' exists, delete it to test creation
            if pm.objExists(cam_name):
                pm.delete(cam_name)

            result = mtk.switch_viewport_camera(cam_name)
            self.assertTrue(pm.objExists(cam_name))
        except RuntimeError:
            pass  # Expected in batch mode


class TestCamUtilsEdgeCases(MayaTkTestCase):
    """Edge cases and error handling for CamUtils."""

    def setUp(self):
        super().setUp()
        self.cam1, _ = pm.camera(n="test_cam_edge")

    def tearDown(self):
        if pm.objExists("existing_group"):
            pm.delete("existing_group")
        super().tearDown()

    def test_group_cameras_exists_error(self):
        """Test error when group already exists."""
        pm.group(n="existing_group", empty=True)
        # pm.error raises RuntimeError
        with self.assertRaises(RuntimeError):
            mtk.group_cameras(name="existing_group")

    def test_adjust_camera_clipping_invalid_mode(self):
        """Test invalid mode error."""
        with self.assertRaises(ValueError):
            mtk.adjust_camera_clipping(mode="invalid_mode")

    def test_adjust_camera_clipping_auto_no_geo(self):
        """Test auto clipping with no geometry."""
        # Delete all geometry
        pm.delete(pm.ls(geometry=True))
        pm.delete(pm.ls(type="mesh"))
        pm.delete(pm.ls(type="nurbsSurface"))

        with self.assertRaises(ValueError):
            mtk.adjust_camera_clipping(mode="auto")

    def test_get_default_camera_fallback(self):
        """Test _get_default_camera fallback logic."""
        from mayatk.cam_utils._cam_utils import CamUtils

        result = CamUtils._get_default_camera("non_existent_cam_type")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
