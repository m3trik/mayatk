# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.ui_utils._ui_utils import UiUtils


class CamUtils(ptk.HelpMixin):
    """ """

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
                PyNode: The created group node containing the cameras.
        """
        if pm.objExists(name):  # Check if the group already exists
            pm.error(f"Group '{name}' already exists.")
            return

        # Create the group and set it's visibility
        group = pm.group(empty=True, name=name)
        group.visibility.set(not hide_group)

        default_cameras = (
            "side",
            "front",
            "top",
            "persp",
            "back",
            "bottom",
            "left",
            "alignToPoly",
        )  # List of default cameras
        all_cameras = pm.ls(type="camera")  # Get all cameras in the scene
        all_camera_transforms = [
            cam.getParent() for cam in all_cameras
        ]  # Get the parent transform nodes of the cameras

        if root_only:  # Filter cameras based on the root_only flag
            all_camera_transforms = [
                cam for cam in all_camera_transforms if cam.getParent() is None
            ]

        # Parent cameras to the group based on the non_default flag
        for cam in all_camera_transforms:
            if non_default:
                if not any(
                    [cam.name().endswith(def_cam) for def_cam in default_cameras]
                ):
                    pm.parent(cam, group)
            else:
                pm.parent(cam, group)

        return group

    @classmethod
    def toggle_safe_frames(cls):
        """Toggle display of the film gate for the current camera."""
        camera = cls.get_current_cam()

        state = pm.camera(camera, q=True, displayResolution=1)
        if state:
            pm.camera(
                camera,
                edit=1,
                displayFilmGate=False,
                displayResolution=False,
                overscan=1.0,
            )
        else:
            pm.camera(
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
        current_panel = None
        for panel in UiUtils.get_panel(all=True):
            if UiUtils.get_panel(typeOf=panel) == "modelPanel":
                current_panel = panel
                break

        if current_panel:
            if UiUtils.get_panel(typeOf=current_panel) == "modelPanel":
                camera = pm.modelPanel(current_panel, q=1, cam=1)
                new_camera = pm.duplicate(camera)[0]
                pm.showHidden(new_camera)
                new_camera = pm.rename(new_camera, name)
                print(f"# Result: {new_camera} #")
                return new_camera
        else:
            print("No modelPanel found")

    @staticmethod
    @CoreUtils.undoable
    def adjust_camera_clipping(
        camera=None, near_clip=None, far_clip=None, mode="manual"
    ):
        """Adjusts the near and far clipping planes of one or multiple cameras in Autodesk Maya.

        Parameters:
            camera (str/list/optional): The camera or list of cameras to adjust. If None, adjusts all cameras in the scene.
            near_clip (float/optional): The value to set for the near clipping plane. Only used when mode is 'manual'.
            far_clip (float/optional): The value to set for the far clipping plane. Only used when mode is 'manual'.
            mode (str/optional): The mode to operate in. Choices are 'manual', 'auto', 'reset'. Default is 'manual'.
                - 'manual': Uses the near_clip and far_clip values provided. Ignores them if None.
                - 'auto': Automatically sets the near and far clipping based on scene geometry.
                - 'reset': Resets the near and far clipping to default values (0.1 and 10000).

        Examples:
            adjust_camera_clipping(near_clip=0.2, far_clip=1000)  # manual is default mode
            adjust_camera_clipping("persp", near_clip=0.2, far_clip=1000, mode='manual')
            adjust_camera_clipping(["persp", "top"], mode='auto')
            adjust_camera_clipping(mode='reset')
        """
        if mode == "reset":
            near_clip = 0.1
            far_clip = 10000
        elif mode == "auto":
            all_geo = pm.ls(dag=True, long=True, geometry=True)
            if not all_geo:
                raise ValueError(
                    "No geometry found in the scene for automatic clipping adjustment."
                )
            bbox = pm.exactWorldBoundingBox(all_geo)
            size = [bbox[i + 3] - bbox[i] for i in range(3)]
            max_size = max(size)
            near_clip = 0.001 * max_size
            far_clip = 5.0 * max_size
        elif mode != "manual":
            raise ValueError(
                f"Invalid mode: {mode}. Valid modes are 'manual', 'auto', 'reset'."
            )

        # Resolve camera shapes
        if camera:
            raw_cameras = pm.ls(camera)
            target_cameras = []
            for cam in raw_cameras:
                if hasattr(cam, "getShape"):  # Transform
                    shape = cam.getShape()
                    if shape and shape.nodeType() == "camera":
                        target_cameras.append(shape)
                elif cam.nodeType() == "camera":  # Shape
                    target_cameras.append(cam)
        else:
            target_cameras = pm.ls(dag=True, cameras=True)

        # Filter out startup cameras
        target_cameras = [
            cam
            for cam in target_cameras
            if not pm.camera(cam, q=True, startupCamera=True)
        ]

        for cam in target_cameras:
            if near_clip is not None:
                cam.nearClipPlane.set(near_clip)
            if far_clip is not None:
                cam.farClipPlane.set(far_clip)

    @staticmethod
    def _get_default_camera(camera_name):
        """Get the default Maya camera by name, regardless of grouping or naming.

        Parameters:
            camera_name (str): The base name of the camera ('top', 'front', 'side', 'persp')

        Returns:
            str or None: The actual camera name to use with lookThru, or None if not found
        """
        # Get all startup cameras in the scene
        try:
            all_cameras = pm.ls(type="camera")
            startup_cameras = []

            for cam in all_cameras:
                try:
                    # Check if this is a startup camera
                    if pm.camera(cam, q=True, startupCamera=True):
                        startup_cameras.append(cam)
                except:
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
                cam_name = str(cam)
                transform = cam.getParent() if hasattr(cam, "getParent") else None
                transform_name = str(transform) if transform else ""

                # Check if camera shape or transform matches our search names
                for search_name in search_names:
                    if (
                        search_name in cam_name.lower()
                        or search_name in transform_name.lower()
                        or cam_name.endswith(search_name)
                        or transform_name.endswith(search_name)
                    ):
                        # Return the shape name for lookThru (preferred)
                        return str(cam)

            # If no startup camera found, try by name existence
            for search_name in search_names:
                if pm.objExists(search_name):
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
        initial_selection = pm.ls(selection=True)

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
            if pm.objExists(camera_name):
                pm.lookThru(camera_name)
                camera_used = camera_name
            else:
                # Create the custom camera
                cam, camShape = pm.camera()
                pm.rename(cam, camera_name)
                pm.lookThru(camera_name)
                pm.hide(camera_name)
                camera_used = camera_name

                # Apply view setting if specified
                view_set = config.get("view_set")
                if view_set:
                    pm.viewSet(**{view_set: 1})
        else:
            # Handle default Maya cameras
            default_cam = cls._get_default_camera(camera_name)
            if default_cam:
                pm.lookThru(default_cam)
                camera_used = default_cam
            else:
                print(f"Warning: Default camera '{camera_name}' not found in scene")

        # Restore initial selection
        if initial_selection:
            pm.select(initial_selection)
        else:
            pm.select(clear=True)

        return camera_used


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
