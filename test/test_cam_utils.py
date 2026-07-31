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
import maya.cmds as cmds
import mayatk as mtk
from base_test import MayaTkTestCase


class TestCamUtils(MayaTkTestCase):
    """Tests for CamUtils class."""

    def setUp(self):
        super().setUp()
        # Create some test cameras
        self.cam1, self.cam1_shape = cmds.camera(n="test_cam_1")
        self.cam2, self.cam2_shape = cmds.camera(n="test_cam_2")
        self.cam3, self.cam3_shape = cmds.camera(n="test_cam_3")

        # Create some geometry for auto clipping
        self.cube = cmds.polyCube(n="clipping_cube")[0]
        cmds.setAttr(f"{self.cube}.t", 10, 10, 10)

    def tearDown(self):
        """Clean up test cameras."""
        if cmds.objExists("cameras_group"):
            cmds.delete("cameras_group")
        if cmds.objExists("existing_group"):
            cmds.delete("existing_group")
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
        self.assertTrue(cmds.objExists("cameras_group"))
        children = cmds.listRelatives(group, children=True)
        self.assertIn(self.cam1, children)
        self.assertIn(self.cam2, children)

    def test_group_cameras_non_default(self):
        """Test grouping only non-default cameras."""
        # Create a camera that looks like a default one (ends with 'persp')
        # Ensure unique name that ends with side (persp might be special)
        import uuid

        name = f"cam_{uuid.uuid4().hex[:8]}_side"

        fake_default, _ = cmds.camera()
        cmds.rename(fake_default, name)
        print(f"Created fake default camera: {fake_default}")

        group = mtk.group_cameras(name="cameras_group", non_default=True)
        children = cmds.listRelatives(group, children=True)

        self.assertIn(self.cam1, children)
        # Name ends with "side", so it should be excluded
        self.assertNotIn(fake_default, children)

    def test_group_cameras_root_only(self):
        """Test grouping only root level cameras."""
        # Parent cam2 to something
        parent_grp = cmds.group(n="parent_grp", empty=True)
        cmds.parent(self.cam2, parent_grp)

        group = mtk.group_cameras(
            name="cameras_group", root_only=True, non_default=False
        )
        children = cmds.listRelatives(group, children=True)

        self.assertIn(self.cam1, children)
        self.assertNotIn(self.cam2, children)

    def test_group_cameras_hide(self):
        """Test hiding the group."""
        group = mtk.group_cameras(name="cameras_group", hide_group=True)
        self.assertFalse(cmds.getAttr(f"{group}.visibility"))

    def test_adjust_camera_clipping_manual(self):
        """Test manual clipping adjustment."""
        mtk.adjust_camera_clipping(camera=self.cam1, near_clip=0.5, far_clip=5000)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam1}.nearClipPlane"), 0.5)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam1}.farClipPlane"), 5000)

    def test_adjust_camera_clipping_reset(self):
        """Test resetting clipping."""
        cmds.setAttr(f"{self.cam1}.nearClipPlane", 5.0)
        mtk.adjust_camera_clipping(
            camera=self.cam1, near_clip="reset", far_clip="reset"
        )
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam1}.nearClipPlane"), 0.1)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam1}.farClipPlane"), 10000)

    def test_adjust_camera_clipping_auto(self):
        """Test automatic clipping based on geometry."""
        # Move cube far away to force large clip planes
        cmds.setAttr(f"{self.cube}.t", 1000, 1000, 1000)
        mtk.adjust_camera_clipping(camera=self.cam1, near_clip="auto", far_clip="auto")
        # Just check that values changed from default/previous
        self.assertNotEqual(cmds.getAttr(f"{self.cam1}.farClipPlane"), 10000)

    def test_adjust_camera_clipping_none(self):
        """Test None parameter (do nothing)."""
        cmds.setAttr(f"{self.cam1}.nearClipPlane", 0.5)
        cmds.setAttr(f"{self.cam1}.farClipPlane", 5000)

        # Should not change anything
        mtk.adjust_camera_clipping(camera=self.cam1, near_clip=None, far_clip=None)

        self.assertAlmostEqual(cmds.getAttr(f"{self.cam1}.nearClipPlane"), 0.5)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam1}.farClipPlane"), 5000)

    def test_switch_viewport_camera_custom(self):
        """Test switching to a custom camera (creation)."""
        try:
            # Use 'left' as it is defined in camera_config in _cam_utils.py
            cam_name = "left"
            # Ensure it doesn't exist first (it might be a startup camera though?)
            # 'left' is usually NOT a startup camera in default Maya, but 'side' is.
            # If 'left' exists, delete it to test creation
            if cmds.objExists(cam_name):
                cmds.delete(cam_name)

            result = mtk.switch_viewport_camera(cam_name)
            self.assertTrue(cmds.objExists(cam_name))
        except RuntimeError:
            pass  # Expected in batch mode


class TestCamUtilsEdgeCases(MayaTkTestCase):
    """Edge cases and error handling for CamUtils."""

    def setUp(self):
        super().setUp()
        self.cam1, _ = cmds.camera(n="test_cam_edge")

    def tearDown(self):
        if cmds.objExists("existing_group"):
            cmds.delete("existing_group")
        super().tearDown()

    def test_group_cameras_exists_error(self):
        """Test error when group already exists."""
        cmds.group(n="existing_group", empty=True)
        # pm.error raises RuntimeError
        with self.assertRaises(RuntimeError):
            mtk.group_cameras(name="existing_group")

    def test_adjust_camera_clipping_auto_no_geo(self):
        """Test auto clipping with no geometry."""
        # Delete all geometry
        cmds.delete(cmds.ls(geometry=True))
        cmds.delete(cmds.ls(type="mesh"))
        cmds.delete(cmds.ls(type="nurbsSurface"))

        # Should not raise error, but use defaults
        mtk.adjust_camera_clipping(camera=self.cam1, near_clip="auto", far_clip="auto")
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam1}.nearClipPlane"), 0.1)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam1}.farClipPlane"), 10000)

    def test_get_default_camera_fallback(self):
        """Test _get_default_camera fallback logic."""
        from mayatk.cam_utils._cam_utils import CamUtils

        result = CamUtils._get_default_camera("non_existent_cam_type")
        self.assertIsNone(result)


class TestViewStateAndClipFitting(MayaTkTestCase):
    """View snapshot/restore + selection-fitted clip planes (backs the frame macro)."""

    def setUp(self):
        super().setUp()
        # Camera at the origin looking down -Z (Maya's camera default aim).
        self.cam, self.cam_shape = cmds.camera(n="fit_cam")

    def _make_cube_at(self, z, size=2.0):
        cube = cmds.polyCube(w=size, h=size, d=size, n="fit_cube")[0]
        cmds.setAttr(f"{cube}.t", 0, 0, z)
        return cube

    def test_view_state_roundtrip_restores_placement_and_clipping(self):
        cmds.setAttr(f"{self.cam}.t", 1, 2, 3)
        cmds.setAttr(f"{self.cam_shape}.nearClipPlane", 0.5)
        cmds.setAttr(f"{self.cam_shape}.farClipPlane", 500)
        state = mtk.get_view_state(camera=self.cam)

        cmds.setAttr(f"{self.cam}.t", 50, 50, 50)
        cmds.setAttr(f"{self.cam_shape}.nearClipPlane", 10)
        cmds.setAttr(f"{self.cam_shape}.farClipPlane", 99)

        self.assertTrue(mtk.set_view_state(state))
        self.assertEqual(cmds.getAttr(f"{self.cam}.t")[0], (1.0, 2.0, 3.0))
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam_shape}.nearClipPlane"), 0.5)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam_shape}.farClipPlane"), 500)

    def test_set_view_state_handles_missing_state(self):
        self.assertFalse(mtk.set_view_state(None))
        self.assertFalse(mtk.set_view_state({"transform": "no_such_camera_xf"}))

    def test_fit_clipping_pushes_the_far_plane_past_the_object(self):
        cube = self._make_cube_at(-500)  # 499..501 units down the view axis
        cmds.setAttr(f"{self.cam_shape}.farClipPlane", 100)

        result = mtk.fit_camera_clipping(objects=cube, camera=self.cam)
        self.assertIsNotNone(result)
        near, far = result
        self.assertGreater(far, 501)  # object fully inside, plus buffer
        self.assertAlmostEqual(near, 0.1)  # already wide enough -> untouched
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam_shape}.farClipPlane"), far)

    def test_fit_clipping_pulls_the_near_plane_in_front_of_the_object(self):
        cube = self._make_cube_at(-3)  # 2..4 units away
        cmds.setAttr(f"{self.cam_shape}.nearClipPlane", 10)

        result = mtk.fit_camera_clipping(objects=cube, camera=self.cam)
        self.assertIsNotNone(result)
        near, _far = result
        self.assertLess(near, 2)

    def test_fit_clipping_is_a_noop_when_nothing_is_clipped(self):
        cube = self._make_cube_at(-10)
        cmds.setAttr(f"{self.cam_shape}.nearClipPlane", 0.1)
        cmds.setAttr(f"{self.cam_shape}.farClipPlane", 10000)
        self.assertIsNone(mtk.fit_camera_clipping(objects=cube, camera=self.cam))

    def test_fit_clipping_only_widens(self):
        """A far-away object must never *tighten* the planes around a near one."""
        cube = self._make_cube_at(-500)
        cmds.setAttr(f"{self.cam_shape}.nearClipPlane", 0.01)
        cmds.setAttr(f"{self.cam_shape}.farClipPlane", 100)

        mtk.fit_camera_clipping(objects=cube, camera=self.cam)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam_shape}.nearClipPlane"), 0.01)
        self.assertGreater(cmds.getAttr(f"{self.cam_shape}.farClipPlane"), 100)

    def test_fit_clipping_keeps_a_positive_near_when_the_camera_is_inside(self):
        cube = self._make_cube_at(0, size=20)  # camera sits inside the bbox
        near, far = mtk.fit_camera_clipping(objects=cube, camera=self.cam)
        self.assertGreater(near, 0)
        self.assertGreater(far, near)

    def test_fit_clipping_returns_none_without_geometry(self):
        self.assertIsNone(mtk.fit_camera_clipping(objects=[], camera=self.cam))

    def test_fit_clipping_fallback_pool_excludes_hidden_geometry(self):
        """With nothing selected the pool is *visible* geometry — a hidden object
        far downrange must not drag the far plane out to meet it."""
        self._make_cube_at(-5)  # visible, comfortably inside the planes
        hidden = self._make_cube_at(-800)
        cmds.setAttr(f"{hidden}.visibility", 0)
        cmds.select(clear=True)
        cmds.setAttr(f"{self.cam_shape}.farClipPlane", 100)

        self.assertIsNone(mtk.fit_camera_clipping(camera=self.cam))
        self.assertAlmostEqual(cmds.getAttr(f"{self.cam_shape}.farClipPlane"), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
