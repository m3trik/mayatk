# !/usr/bin/python
# coding=utf-8
"""Construction-history queries shared by the blendshape_animator modules."""

from typing import List, Optional

try:
    from maya import cmds
except ImportError:
    pass


class BlendshapeHistory:
    """BlendshapeHistory — construction-history lookups (the ``blendShape``
    deformers upstream of a mesh)."""

    @staticmethod
    def list_history(node: str, type_filter: Optional[str] = None) -> List[str]:
        """List the construction history of a node, optionally filtered by node type."""
        history = cmds.listHistory(node) or []
        if type_filter is not None:
            history = cmds.ls(history, type=type_filter) or []
        return history
