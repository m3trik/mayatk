# !/usr/bin/python
# coding=utf-8
"""Target Weld: Maya's Merge Vertex Tool, set up to drag one vertex onto another.

Twin of blendertk's ``TargetWeld`` (an interactive modal tool built there); the
call a panel shared by both hosts makes is :meth:`TargetWeld.activate`.
"""

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    cmds = mel = None

import pythontk as ptk


class TargetWeld(ptk.HelpMixin):
    """Maya's interactive target weld (``MergeVertexTool``)."""

    @staticmethod
    def activate(merge_to_center: bool = False) -> bool:
        """Start the Merge Vertex Tool in vertex component mode with nothing
        selected: drag a vertex onto another and they weld, at the target or,
        with *merge_to_center*, at their midpoint. Twin of blendertk's
        ``TargetWeld.activate``.

        Returns:
            bool: True; Maya's tool needs no selection to start.
        """
        cmds.selectMode(component=True)
        cmds.selectType(vertex=True)
        cmds.select(clear=True)
        cmds.targetWeldCtx(
            "polyMergeVertexContext", edit=True, mergeToCenter=merge_to_center
        )
        mel.eval("MergeVertexTool")
        return True


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
