# !/usr/bin/python
# coding=utf-8
"""Reusable helper for resolving Maya node icons at runtime.

Usage::

    from mayatk.ui_utils.node_icons import NodeIcons

    # Get a QIcon for any Maya node
    icon = NodeIcons.get_icon("pSphere1")

    # Just get the icon filename
    name = NodeIcons.icon_name_for_node("pSphere1")  # "out_mesh.png"
"""
from typing import Optional

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None


class NodeIcons:
    """Resolve Maya node type icons as Qt QIcons."""

    @staticmethod
    def icon_name_for_type(node_type: str) -> str:
        """Return the Maya resource icon filename for a given node type.

        Parameters:
            node_type: A Maya node type string (e.g. ``"mesh"``, ``"camera"``).

        Returns:
            Icon filename such as ``"out_mesh.png"``.
        """
        return f"out_{node_type}.png"

    @staticmethod
    def icon_name_for_node(node_name: str) -> Optional[str]:
        """Return the icon filename for a specific node in the scene.

        Looks through shape children first so that a transform containing
        a mesh returns ``"out_mesh.png"`` rather than ``"out_transform.png"``.

        Parameters:
            node_name: The DAG or DG node name.

        Returns:
            Icon filename, or ``None`` if Maya is unavailable or the node
            doesn't exist.
        """
        if cmds is None or not cmds.objExists(node_name):
            return None

        # Resolve to long name so ambiguous short names don't raise
        try:
            long_names = cmds.ls(node_name, long=True)
            resolved = long_names[0] if long_names else node_name
        except (ValueError, RuntimeError):
            resolved = node_name

        try:
            # Prefer shape node type for a richer icon
            shapes = cmds.listRelatives(
                resolved, shapes=True, noIntermediate=True, fullPath=True
            )
            if shapes:
                node_type = cmds.nodeType(shapes[0])
            else:
                node_type = cmds.nodeType(resolved)
        except (ValueError, RuntimeError):
            return None

        return NodeIcons.icon_name_for_type(node_type)

    @staticmethod
    def get_icon(node_name: str, size: int = 20):
        """Return a ``QIcon`` for a Maya node, or ``None`` if unavailable.

        Parameters:
            node_name: DAG/DG node name.
            size: Desired icon size in pixels (icons are square).

        Returns:
            A ``QtGui.QIcon`` loaded from Maya's Qt resource system,
            or ``None`` if the icon cannot be resolved.
        """
        icon_file = NodeIcons.icon_name_for_node(node_name)
        if icon_file is None:
            return None

        from qtpy.QtGui import QIcon, QPixmap

        icon = QIcon(f":/{icon_file}")
        if icon.isNull():
            return None

        return icon

    @staticmethod
    def get_pixmap(node_name: str, size: int = 16):
        """Return a ``QPixmap`` for a Maya node, scaled to *size*.

        Parameters:
            node_name: DAG/DG node name.
            size: Width and height in pixels.

        Returns:
            A ``QtGui.QPixmap`` or ``None``.
        """
        icon = NodeIcons.get_icon(node_name, size)
        if icon is None:
            return None
        return icon.pixmap(size, size)
