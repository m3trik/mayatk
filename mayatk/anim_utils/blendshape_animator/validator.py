# !/usr/bin/python
# coding=utf-8
"""Mesh and blendShape validation for blendShape animation setup."""
import pythontk as ptk

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.node_utils._node_utils import NodeUtils


class Validator(ptk.LoggingMixin):
    """Handles validation of meshes and blendShape setups."""

    @classmethod
    def validate_meshes(cls, mesh1: str, mesh2: str) -> bool:
        """Validate that both objects are compatible meshes."""
        for i, mesh in enumerate([mesh1, mesh2], 1):
            if not mesh or not cmds.objExists(str(mesh)):
                cls.logger.error(f"Object {i} ({mesh}) does not exist")
                return False
            shape = NodeUtils.get_shape(mesh)
            if not shape or cmds.nodeType(shape) != "mesh":
                cls.logger.error(f"Object {i} ({mesh}) is not a polygon mesh")
                return False

        verts1 = cmds.polyEvaluate(mesh1, vertex=True)
        verts2 = cmds.polyEvaluate(mesh2, vertex=True)

        if verts1 != verts2:
            cls.logger.error(
                f"Vertex count mismatch - {mesh1}: {verts1}, {mesh2}: {verts2}"
            )
            return False

        cls.logger.info(f"Mesh validation passed - both have {verts1} vertices")
        return True

    @classmethod
    def validate_blendshape(cls, blendshape: str) -> bool:
        """Validate blendShape node configuration."""
        if not cmds.objExists(blendshape):
            cls.logger.error(f"BlendShape {blendshape} does not exist")
            return False

        envelope = cmds.getAttr(f"{blendshape}.envelope")
        if envelope != 1.0:
            cls.logger.warning(f"BlendShape envelope is {envelope}, should be 1.0")

        if cmds.getAttr(f"{blendshape}.weight[0]", lock=True):
            cls.logger.warning("BlendShape weight is locked")

        return True
