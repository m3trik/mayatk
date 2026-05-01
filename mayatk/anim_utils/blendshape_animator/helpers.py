# !/usr/bin/python
# coding=utf-8
"""Shared helpers internal to the blendshape_animator subpackage."""
from typing import List, Optional

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)


def list_history(node: str, type_filter: Optional[str] = None) -> List[str]:
    """List the construction history of a node, optionally filtered by node type."""
    history = cmds.listHistory(node) or []
    if type_filter is not None:
        history = cmds.ls(history, type=type_filter) or []
    return history
