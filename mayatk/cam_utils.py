# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

# from this package:
from mayatk import core_utils


class CamUtils(object):
    """ """

    @staticmethod
    @core_utils.CoreUtils.undo
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
            cam.get_parent() for cam in all_cameras
        ]  # Get the parent transform nodes of the cameras

        if root_only:  # Filter cameras based on the root_only flag
            all_camera_transforms = [
                cam for cam in all_camera_transforms if cam.get_parent() is None
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
    @core_utils.CoreUtils.undo
    def create_camera_from_view(name="camera#"):
        """Create a new camera based on the current view."""
        # Find the current modelPanel (viewport)
        current_panel = None
        for panel in core_utils.CoreUtils.get_panel(all=True):
            if core_utils.CoreUtils.get_panel(typeOf=panel) == "modelPanel":
                current_panel = panel
                break

        if current_panel:
            if core_utils.CoreUtils.get_panel(typeOf=current_panel) == "modelPanel":
                camera = pm.modelPanel(current_panel, q=1, cam=1)
                new_camera = pm.duplicate(camera)[0]
                pm.showHidden(new_camera)
                new_camera = pm.rename(new_camera, name)
                print(f"# Result: {new_camera} #")
                return new_camera
        else:
            print("No modelPanel found")


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
