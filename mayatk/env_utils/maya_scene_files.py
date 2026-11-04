# !/usr/bin/python
# coding=utf-8
"""MayaSceneFiles -- the ``maya`` operation host: calls in a mayapy the tooling starts.

mayatk's runner for ``python -m pythontk ops`` and every other front end of
``ptk.OperationCatalog``: an operation declared with ``host="maya"`` (see
``mayatk/operations.py``) runs in a NEW mayapy per call, or, for a front end
that lives long (the MCP server), in the one mayapy ``ptk.SessionOperationHost``
keeps for its session; never in a Maya a person has open (the workspace's
session-safety rule, held by construction).
:meth:`MayaSceneFiles.operation_host` is the ``ptk.BatchOperationHost``
registered under the ``pythontk.operation_hosts`` entry points; the class
itself is the ``ptk.SceneFiles`` that runner builds inside mayapy: it starts
Maya's standalone session, opens the request's scene and saves it when a
finished live run changed it. Blender's twin is blendertk's
``BlenderSceneFiles``.
"""

from typing import List, Optional, Sequence

import pythontk as ptk


class MayaSceneFiles:
    """A batch run's scene, inside a fresh mayapy (see the module docstring).

    Constructing one starts Maya's standalone session, so it is built only in
    the runner's own mayapy, never in a Maya session.
    """

    def __init__(self):
        import maya.standalone

        maya.standalone.initialize(name="python")

    @classmethod
    def find_mayapy(cls) -> Optional[str]:
        """The mayapy beside the Maya that ``MayaConnection`` would launch
        (``$MAYA_EXE``, ``$MAYA_LOCATION``, PATH, then the newest install).

        Returns:
            Its path, or ``None`` when no Maya is installed.
        """
        from mayatk.env_utils.maya_connection import MayaConnection

        maya = ptk.AppLauncher.resolve_app_path(**MayaConnection.MAYA_DISCOVERY)
        return ptk.AppLauncher.companion_python(maya) if maya else None

    @classmethod
    def operation_host(cls) -> "ptk.BatchOperationHost":
        """The ``maya`` runner: a fresh mayapy per call (``pythontk.operation_hosts``)."""
        return ptk.BatchOperationHost(
            cls.find_mayapy, scene_files="mayatk:MayaSceneFiles", packages=("mayatk",)
        )

    def open(self, paths: Sequence[str]) -> None:
        """Open the one scene *paths* names (Maya holds one scene at a time).

        Raises:
            ValueError: More than one scene was asked for.
        """
        import maya.cmds as cmds

        if len(paths) > 1:
            raise ValueError(f"Maya opens one scene per run; the request names {len(paths)}")
        for path in paths:
            cmds.file(path, open=True, force=True)

    def modified(self) -> List[str]:
        """The open scene, when it has unsaved changes."""
        import maya.cmds as cmds

        name = cmds.file(query=True, sceneName=True)
        return [name] if name and cmds.file(query=True, modified=True) else []

    def unsaved(self) -> bool:
        """Whether the open scene (an untitled one included) has changes no file has."""
        import maya.cmds as cmds

        return bool(cmds.file(query=True, modified=True))

    def save(self, paths: Sequence[str]) -> None:
        """Save the open scene where it is, in its own format (``.ma`` / ``.mb``)."""
        import maya.cmds as cmds

        if paths:
            cmds.file(save=True, force=True)

    def close(self) -> None:
        """End the standalone session before mayapy exits."""
        import maya.standalone

        maya.standalone.uninitialize()
