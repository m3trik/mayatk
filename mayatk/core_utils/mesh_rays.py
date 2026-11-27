# !/usr/bin/python
# coding=utf-8
"""Rays through the scene's meshes: a :class:`pythontk.ProbeRays` over Maya's.

What any tool asks of the scene's geometry by sight -- a lightmap bake's
reflection probe (``ProbePlacement``), a helper's first pose
(``ptk.SpotPlacement``, the Shot Manifest) -- through ``MFnMesh.closestIntersection``
with each mesh's own acceleration grid, the shapes under each node it is given.
Everything is in Maya's internal unit (centimetres), whatever the scene's.
"""

from typing import Sequence, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

Vec = Tuple[float, float, float]


class MeshRays:
    """The meshes under nodes as a :class:`pythontk.ProbeRays`, and how its rays
    meet them (:class:`pythontk.ProbeMesh`): ``MFnMesh.closestIntersection``,
    both-sided, with each mesh's own acceleration grid."""

    def rays(self, nodes: Sequence[str]) -> "ptk.ProbeRays":
        """A :class:`pythontk.ProbeRays` over the mesh shapes under *nodes*."""
        import maya.api.OpenMaya as om

        rays = ptk.ProbeRays(self)
        for mesh in nodes:
            for shape in (
                cmds.listRelatives(
                    mesh, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            ):
                if cmds.nodeType(shape) != "mesh":
                    continue
                sel = om.MSelectionList()
                sel.add(shape)
                path = sel.getDagPath(0)
                fn = om.MFnMesh(path)
                box = om.MFnDagNode(path).boundingBox
                box.transformUsing(path.inclusiveMatrix())
                rays.add(
                    mesh,
                    (box.min.x, box.min.y, box.min.z),
                    (box.max.x, box.max.y, box.max.z),
                    (fn, fn.autoUniformGridParams()),
                )
        return rays

    @staticmethod
    def intersect(payload, origin, direction, far):
        import maya.api.OpenMaya as om

        fn, accel = payload
        hit = fn.closestIntersection(
            om.MFloatPoint(*origin),
            om.MFloatVector(*direction),
            om.MSpace.kWorld,
            far,
            False,
            accelParams=accel,
        )
        if not (hit and hit[2] >= 0):
            return None
        point = hit[0]
        return hit[1], (point.x, point.y, point.z), hit[2]

    @staticmethod
    def normal(payload, face) -> Vec:
        import maya.api.OpenMaya as om

        normal = payload[0].getPolygonNormal(face, om.MSpace.kWorld)
        return (normal.x, normal.y, normal.z)
