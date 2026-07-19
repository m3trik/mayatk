# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils, short_name, as_strings
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.ui_utils._ui_utils import UiUtils


class CamUtils(ptk.HelpMixin):
    """ """

    # Maya's built-in startup cameras plus common orthographic views
    # created on-demand by CamUtils helpers.
    DEFAULT_CAMERAS = frozenset(
        {
            "persp",
            "top",
            "front",
            "side",
            "back",
            "bottom",
            "left",
            "right",
            "alignToPoly",
        }
    )

    @staticmethod
    @CoreUtils.undoable
    def group_cameras(
        name="cameras", non_default=True, root_only=False, hide_group=False
    ):
        """Group cameras in the scene based on the provided parameters.

        Parameters:
                name (str): The name of the group that will contain the cameras. Default is 'cameras'.
                non_default (bool, optional): If True, only non-default cameras will be grouped. Default is True.
                root_only (bool, optional): If True, only root-level cameras (not parented to other objects) will be grouped. Default is False.
                hide_group (bool, optional): If True, the newly created group will be hidden. Default is False.

        Returns:
                str: The created group node containing the cameras.
        """
        if cmds.objExists(name):
            cmds.error(f"Group '{name}' already exists.")
            return

        # Create the group and set its visibility
        group = cmds.group(empty=True, name=name)
        cmds.setAttr(f"{group}.visibility", not hide_group)

        all_cameras = cmds.ls(type="camera") or []
        all_camera_transforms = []
        for cam in all_cameras:
            parent = NodeUtils.get_parent(cam)
            if parent:
                all_camera_transforms.append(parent)

        if root_only:
            all_camera_transforms = [
                cam for cam in all_camera_transforms if NodeUtils.get_parent(cam) is None
            ]

        # Parent cameras to the group based on the non_default flag
        for cam in all_camera_transforms:
            if non_default:
                cam_short = short_name(cam)
                if cam_short not in CamUtils.DEFAULT_CAMERAS:
                    cmds.parent(cam, group)
            else:
                cmds.parent(cam, group)

        return group

    @classmethod
    def toggle_safe_frames(cls):
        """Toggle display of the film gate for the current camera."""
        camera = cls.get_current_cam()

        state = cmds.camera(camera, q=True, displayResolution=1)
        if state:
            cmds.camera(
                camera,
                edit=1,
                displayFilmGate=False,
                displayResolution=False,
                overscan=1.0,
            )
        else:
            cmds.camera(
                camera,
                edit=1,
                displayFilmGate=False,
                displayResolution=True,
                overscan=1.3,
            )

    @staticmethod
    def get_current_cam():
        """Get the currently active camera."""
        from maya.OpenMaya import MDagPath
        from maya.OpenMayaUI import M3dView

        view = M3dView.active3dView()
        cam = MDagPath()
        view.getCamera(cam)
        camPath = cam.fullPathName()
        return camPath

    @staticmethod
    @CoreUtils.undoable
    def create_camera_from_view(name="camera#"):
        """Create a new camera based on the current view."""
        # Find the current modelPanel (viewport)
        current_panel = UiUtils.get_model_panel(with_focus=True)
        if current_panel:
            camera = cmds.modelPanel(current_panel, q=1, cam=1)
            new_camera = cmds.duplicate(camera)[0]
            cmds.showHidden(new_camera)
            new_camera = cmds.rename(new_camera, name)
            print(f"# Result: {new_camera} #")
            return new_camera
        else:
            print("No modelPanel found")

    @classmethod
    @CoreUtils.undoable
    def adjust_camera_clipping(cls, camera=None, near_clip=None, far_clip=None):
        """Adjusts the near and far clipping planes of one or multiple cameras.

        Parameters:
            camera (str/list/optional): The camera or list of cameras to adjust. If None, adjusts the current viewport camera.
            near_clip (float/str/optional): The value for the near clipping plane.
                - If None (default): Do not change.
                - If 'auto': Automatically calculated based on scene geometry.
                - If 'reset': Resets to default (0.1).
                - If float: Sets to the specific value.
            far_clip (float/str/optional): The value for the far clipping plane.
                - If None (default): Do not change.
                - If 'auto': Automatically calculated based on scene geometry.
                - If 'reset': Resets to default (10000).
                - If float: Sets to the specific value.
        """
        # Resolve camera shapes
        target_cameras = []
        if camera:

            raw_cameras = cmds.ls(*as_strings(camera)) or []
            for cam in raw_cameras:
                node_type = cmds.nodeType(cam)
                if node_type == "transform":
                    shapes = NodeUtils.get_shapes(cam)
                    if shapes and cmds.nodeType(shapes[0]) == "camera":
                        target_cameras.append(shapes[0])
                elif node_type == "camera":
                    target_cameras.append(cam)
        else:
            # If no camera specified, use the current viewport camera
            current_cam = cls.get_current_cam()
            if current_cam and cmds.objExists(current_cam):
                node_type = cmds.nodeType(current_cam)
                if node_type == "camera":
                    target_cameras.append(current_cam)
                elif node_type == "transform":
                    shapes = NodeUtils.get_shapes(current_cam)
                    if shapes and cmds.nodeType(shapes[0]) == "camera":
                        target_cameras.append(shapes[0])

        if not target_cameras:
            return

        # Check if we need auto calculation
        needs_auto = (near_clip == "auto") or (far_clip == "auto")

        # Pre-calculate scene bbox if needed for auto mode
        bbox = None
        bbox_points = None

        if needs_auto:
            all_geo = cmds.ls(dag=True, geometry=True, visible=True) or []
            if not all_geo:
                all_geo = cmds.ls(dag=True, geometry=True) or []

            if all_geo:
                bbox = cmds.exactWorldBoundingBox(all_geo)
                # bbox is [xmin, ymin, zmin, xmax, ymax, zmax]
                min_pt = om.MVector(bbox[0], bbox[1], bbox[2])
                max_pt = om.MVector(bbox[3], bbox[4], bbox[5])

                # Calculate 8 corners for far clip calculation
                bbox_points = [
                    om.MVector(x, y, z)
                    for x in (min_pt.x, max_pt.x)
                    for y in (min_pt.y, max_pt.y)
                    for z in (min_pt.z, max_pt.z)
                ]

        for cam in target_cameras:
            # Only get camera position if we are doing auto calculations
            cam_pos = None
            max_dist = 0  # Distance to furthest point of bbox

            if needs_auto:
                cam_transform = NodeUtils.get_parent(cam)
                if cam_transform:
                    pos = cmds.xform(cam_transform, q=True, ws=True, t=True)
                    cam_pos = om.MVector(pos[0], pos[1], pos[2])

                if bbox_points and cam_pos is not None:
                    for pt in bbox_points:
                        d = (pt - cam_pos).length()
                        if d > max_dist:
                            max_dist = d

            # Determine Near Clip
            if near_clip is not None:
                new_near = None
                if near_clip == "reset":
                    new_near = 0.1
                elif near_clip == "auto":
                    if bbox and cam_pos is not None:
                        # Use a safe ratio of the far distance to maintain Z-buffer precision
                        # while ensuring we don't clip foreground objects excessively.
                        # Ratio of 3000 is conservative (e.g. Far=3000 -> Near=1.0)
                        # We also ensure a minimum of 0.1 (standard Maya default)
                        estimated_far = max_dist * 1.2
                        new_near = estimated_far / 3000.0
                        new_near = max(new_near, 0.1)
                    else:
                        new_near = 0.1
                elif isinstance(near_clip, (int, float)):
                    new_near = near_clip

                if new_near is not None:
                    cmds.setAttr(f"{cam}.nearClipPlane", new_near)

            # Determine Far Clip
            if far_clip is not None:
                new_far = None
                if far_clip == "reset":
                    new_far = 10000.0
                elif far_clip == "auto":
                    if bbox_points and cam_pos is not None:
                        new_far = max_dist * 1.2
                    else:
                        new_far = 10000.0
                elif isinstance(far_clip, (int, float)):
                    new_far = far_clip

                if new_far is not None:
                    cmds.setAttr(f"{cam}.farClipPlane", new_far)

    @staticmethod
    def _get_default_camera(camera_name):
        """Get the default Maya camera by name, regardless of grouping or naming.

        Parameters:
            camera_name (str): The base name of the camera ('top', 'front', 'side', 'persp')

        Returns:
            str or None: The actual camera name to use with lookThru, or None if not found
        """
        try:
            all_cameras = cmds.ls(type="camera") or []
            startup_cameras = []

            for cam in all_cameras:
                try:
                    if cmds.camera(cam, q=True, startupCamera=True):
                        startup_cameras.append(cam)
                except Exception:
                    continue

            # Find the camera that matches our desired view
            camera_map = {
                "top": ["top", "topShape"],
                "front": ["front", "frontShape"],
                "side": ["side", "sideShape"],
                "persp": ["persp", "perspShape"],
            }

            search_names = camera_map.get(camera_name, [camera_name])

            # Look for matching startup camera
            for cam in startup_cameras:
                cam_name = cam
                transform = NodeUtils.get_parent(cam)
                transform_name = transform if transform else ""

                # Check if camera shape or transform matches our search names
                for search_name in search_names:
                    if (
                        search_name in cam_name.lower()
                        or search_name in transform_name.lower()
                        or cam_name.endswith(search_name)
                        or transform_name.endswith(search_name)
                    ):
                        return cam

            # If no startup camera found, try by name existence
            for search_name in search_names:
                if cmds.objExists(search_name):
                    return search_name

        except Exception as e:
            print(f"Error finding default camera {camera_name}: {e}")

        return None

    @classmethod
    def switch_viewport_camera(cls, camera_name):
        """Unified method to switch to a camera, creating custom ones if needed.

        Parameters:
            camera_name (str): Name of the camera to switch to

        Returns:
            str or None: The camera that was switched to, or None if switching failed
        """
        # Store initial selection to restore later
        initial_selection = cmds.ls(selection=True) or []

        # Camera configuration - simplified approach
        camera_config = {
            # Custom cameras (create if missing)
            "back": {"default": False, "view_set": "back"},
            "left": {"default": False, "view_set": "leftSide"},
            "bottom": {"default": False, "view_set": "bottom"},
        }

        # Check if it's a custom camera
        config = camera_config.get(camera_name)
        camera_used = None

        if config and not config["default"]:
            # Handle custom cameras (create if missing)
            if cmds.objExists(camera_name):
                cmds.lookThru(camera_name)
                camera_used = camera_name
            else:
                # Create the custom camera
                cam, camShape = cmds.camera()
                cmds.rename(cam, camera_name)
                cmds.lookThru(camera_name)
                cmds.hide(camera_name)
                camera_used = camera_name

                # Apply view setting if specified
                view_set = config.get("view_set")
                if view_set:
                    cmds.viewSet(**{view_set: 1})
        else:
            # Handle default Maya cameras
            default_cam = cls._get_default_camera(camera_name)
            if default_cam:
                cmds.lookThru(default_cam)
                camera_used = default_cam
            else:
                print(f"Warning: Default camera '{camera_name}' not found in scene")

        # Restore initial selection
        if initial_selection:
            cmds.select(initial_selection)
        else:
            cmds.select(clear=True)

        return camera_used


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
