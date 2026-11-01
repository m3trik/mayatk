# !/usr/bin/python
# coding=utf-8
"""Per-camera visibility: Maya's Display > Per Camera Visibility
(``perCameraVisibility``), by blendertk's ``CameraVisibility`` names (a rolled
engine there: Blender has no per-camera visibility of its own).

An object exclusive to a camera shows only in a view looking through it; one
hidden from a camera shows in every other view. Called bare, an operation reads
the selection as Maya's own menu does: the selected cameras are the cameras
(none selected: the current viewport's), the rest the objects.
"""
from typing import List, Tuple

try:  # guarded: the surface must import without Maya (registry, mock tests)
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.cam_utils._cam_utils import CamUtils


class _CameraVisibilityInternal(object):
    """Internal helpers for CameraVisibility."""

    @staticmethod
    def _is_camera(node: str) -> bool:
        """A camera, transform or shape: the test Maya's own menu makes."""
        try:
            cmds.camera(node, query=True, aspectRatio=True)
        except (RuntimeError, TypeError, ValueError):
            return False
        return True

    @classmethod
    def _targets(cls, camera=None, objects=None) -> Tuple[List[str], List[str]]:
        """``(cameras, objects)`` as Maya's menu reads them: the arguments given,
        else the selection split into cameras and the rest; no camera at all is
        the current viewport's.

        Raises:
            ValueError: No camera resolves.
        """
        selection = (
            (cmds.ls(selection=True) or []) if camera is None or objects is None else []
        )
        if objects is None:
            objects = [n for n in selection if not cls._is_camera(n)]
        if camera is not None:
            cameras = CoreUtils.as_strings(camera)
        else:
            cameras = [n for n in selection if cls._is_camera(n)]
            if not cameras:
                try:
                    current = CamUtils.get_current_cam()
                except Exception:  # noqa: BLE001 - no 3D view (batch) raises varied errors
                    current = None
                cameras = [current] if current else []
        if not cameras:
            raise ValueError("A camera is required: select one, or look through it.")
        return cameras, CoreUtils.as_strings(objects)

    @classmethod
    def _edit(cls, camera, objects, purpose, **flags) -> List[str]:
        """Apply ``perCameraVisibility`` *flags* to *objects* for each camera."""
        cameras, objects = cls._targets(camera, objects)
        if not objects:
            raise ValueError(f"Select the objects to {purpose} first.")
        for cam in cameras:
            cmds.perCameraVisibility(objects, camera=cam, **flags)
        return objects


class CameraVisibility(_CameraVisibilityInternal):
    """Per-camera exclusive / hidden visibility (see the module docstring). Each
    edit is one undo step; the blendertk twin answers to the same names."""

    @classmethod
    @CoreUtils.undoable
    def set_exclusive(cls, camera=None, objects=None) -> List[str]:
        """Make *objects* exclusive to *camera*: they show only in a view looking
        through it (Maya ``SetExclusiveToCamera``).

        Returns:
            list: The objects set. Maya reports no set membership back; the
                blendertk twin returns the camera's whole set.

        Raises:
            ValueError: No camera, or no object.
        """
        return cls._edit(camera, objects, "make exclusive to the camera", exclusive=True)

    @classmethod
    @CoreUtils.undoable
    def set_hidden(cls, camera=None, objects=None) -> List[str]:
        """Hide *objects* from *camera* (Maya ``SetHiddenFromCamera``).

        Returns:
            list: The objects set.

        Raises:
            ValueError: No camera, or no object.
        """
        return cls._edit(camera, objects, "hide from the camera", hide=True)

    @classmethod
    @CoreUtils.undoable
    def remove_from_exclusive(cls, camera=None, objects=None) -> List[str]:
        """Take *objects* out of *camera*'s exclusive set (Maya
        ``CameraRemoveFromExclusive``). Returns the objects."""
        return cls._edit(
            camera, objects, "remove from the exclusive set", exclusive=True, remove=True
        )

    @classmethod
    @CoreUtils.undoable
    def remove_from_hidden(cls, camera=None, objects=None) -> List[str]:
        """Take *objects* out of *camera*'s hidden set (Maya
        ``CameraRemoveFromHidden``). Returns the objects."""
        return cls._edit(
            camera, objects, "remove from the hidden set", hide=True, remove=True
        )

    @classmethod
    @CoreUtils.undoable
    def remove_all(cls, camera=None) -> None:
        """Clear both sets of *camera* (Maya ``CameraRemoveAll``).

        Raises:
            ValueError: No camera.
        """
        cameras, _objects = cls._targets(camera, objects=[])
        for cam in cameras:
            cmds.perCameraVisibility(camera=cam, removeCamera=True)

    @staticmethod
    @CoreUtils.undoable
    def remove_all_for_all() -> None:
        """Clear both sets of every camera (Maya ``CameraRemoveAllForAll``)."""
        cmds.perCameraVisibility(removeAll=True)


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
