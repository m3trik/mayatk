# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import _core_utils


class CamUtils(ptk.HelpMixin):
    """ """

    @staticmethod
    @_core_utils.CoreUtils.undo
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
    @_core_utils.CoreUtils.undo
    def create_camera_from_view(name="camera#"):
        """Create a new camera based on the current view."""
        # Find the current modelPanel (viewport)
        current_panel = None
        for panel in _core_utils.CoreUtils.get_panel(all=True):
            if _core_utils.CoreUtils.get_panel(typeOf=panel) == "modelPanel":
                current_panel = panel
                break

        if current_panel:
            if _core_utils.CoreUtils.get_panel(typeOf=current_panel) == "modelPanel":
                camera = pm.modelPanel(current_panel, q=1, cam=1)
                new_camera = pm.duplicate(camera)[0]
                pm.showHidden(new_camera)
                new_camera = pm.rename(new_camera, name)
                print(f"# Result: {new_camera} #")
                return new_camera
        else:
            print("No modelPanel found")

    @staticmethod
    @_core_utils.CoreUtils.undo
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
            near_clip = 0.0001 * max_size
            far_clip = 5.0 * max_size
        elif mode != "manual":
            raise ValueError(
                f"Invalid mode: {mode}. Valid modes are 'manual', 'auto', 'reset'."
            )

        target_cameras = pm.ls(camera) if camera else pm.ls(dag=True, cameras=True)
        target_cameras = [
            cam
            for cam in target_cameras
            if "startupCameras" not in pm.listRelatives(cam, parent=True)[0].longName()
        ]

        for cam in target_cameras:
            if near_clip is not None:
                pm.setAttr(f"{cam}.nearClipPlane", near_clip)
            if far_clip is not None:
                pm.setAttr(f"{cam}.farClipPlane", far_clip)


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
