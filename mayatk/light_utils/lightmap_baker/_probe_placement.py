# !/usr/bin/python
# coding=utf-8
"""Where a lightmap bake's reflection probe stands -- Maya's scene for the placement.

The placement itself -- open air near the bake's middle, the room around it
face by face, over what matters, at eye level, clear of every surface -- is
host-free and lives once in pythontk (:class:`pythontk.ProbePlacement`, whose
module docstring holds the rules and the scenes that forced each), shared with
blendertk's twin. This module hands it Maya's scene, as its host adapter
(:class:`MayaProbeHost`, a :class:`pythontk.ProbeHost` and the
:class:`pythontk.ProbeMesh` its rays intersect through):

* **The rays**: ``MFnMesh.closestIntersection`` with each mesh's own
  acceleration grid, against the shapes under each node.
* **The bounds**: ``exactWorldBoundingBox``.
* **The lights**: :meth:`LightUtils.contributing_lights` -- a sky
  (:attr:`MayaProbeHost.SKY_LIGHTS`) as the sky, a placeless one
  (:attr:`MayaProbeHost.PLACELESS_LIGHTS`) as nothing, an ``aiMeshLight`` by
  its own mesh, the rest by their world position.
* **The units**: everything inside is in Maya's internal unit (centimetres)
  whatever the scene's; positions cross the boundary in scene units. Y is up.

:class:`ProbePlacement` is the shared class bound to it: configuration only.
"""

from typing import List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

from mayatk.light_utils._light_utils import LightUtils
from mayatk.core_utils.units import Units

Vec = Tuple[float, float, float]
Box = List[List[float]]

#: Where a probe is captured from: released here (0.26.0), host-free now
#: (``ptk.ProbeSite``); the name stays so an import of it keeps working.
ProbeSite = ptk.ProbeSite


class MayaProbeHost:
    """What the probe placement asks of the Maya scene
    (:class:`pythontk.ProbeHost`), and how its rays intersect Maya meshes
    (:class:`pythontk.ProbeMesh`): the shapes under each node, through
    ``MFnMesh.closestIntersection`` with their own acceleration grid."""

    #: Maya's internal linear unit, centimetres, in metres -- whatever the
    #: scene's UI unit.
    internal_m: float = Units.INTERNAL_M
    #: The up axis, as an index: Y. A probe's deliverables read it in Y-up
    #: axes, so :meth:`LightmapBaker.bake_probe` captures none in a Z-up scene.
    up: int = 1
    #: Light types that stand for the sky rather than a fixture with a place.
    SKY_LIGHTS: Tuple[str, ...] = ("directionalLight", "aiSkyDomeLight")
    #: Light types that light nothing a ray can reach.
    PLACELESS_LIGHTS: Tuple[str, ...] = ("ambientLight", "aiLightPortal")

    @staticmethod
    def nodes(objects: Sequence[str]) -> List[str]:
        """The transforms as given: the placement names nodes by DAG path."""
        return list(objects or ())

    def rays(self, nodes: Sequence[str]) -> ptk.ProbeRays:
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

    def lights(self) -> Tuple[List[Tuple[Vec, Optional[str]]], bool]:
        """The placed lights, ``[(world point, own mesh or None)]`` -- a mesh
        light is in view when its own mesh is -- and whether a sky lights the
        scene."""
        import maya.api.OpenMaya as om

        points: List[Tuple[Vec, Optional[str]]] = []
        sky = False
        for shape in LightUtils.contributing_lights():
            kind = cmds.nodeType(shape)
            if kind in self.SKY_LIGHTS:
                sky = True
                continue
            if kind in self.PLACELESS_LIGHTS:
                continue
            own = None
            if kind == "aiMeshLight":
                meshes = cmds.listConnections(shape + ".inMesh", shapes=True) or []
                if not meshes:
                    continue
                own = (
                    cmds.listRelatives(meshes[0], parent=True, fullPath=True) or [None]
                )[0]
                if not own:
                    continue
                lo, hi = self.bounds([own])
                points.append((tuple((a + b) / 2.0 for a, b in zip(lo, hi)), own))
                continue
            sel = om.MSelectionList()
            sel.add(shape)
            matrix = om.MTransformationMatrix(sel.getDagPath(0).inclusiveMatrix())
            t = matrix.translation(om.MSpace.kWorld)
            points.append(((t.x, t.y, t.z), None))
        return points, sky

    @staticmethod
    def bounds(nodes: Sequence[str]) -> Box:
        """``[[min], [max]]`` of *nodes* in world space, internal units."""
        import maya.api.OpenMaya as om

        values = cmds.exactWorldBoundingBox(list(nodes))
        internal = [om.MDistance.uiToInternal(v) for v in values]
        return [internal[:3], internal[3:]]

    @staticmethod
    def to_scene(value: float) -> float:
        import maya.api.OpenMaya as om

        return om.MDistance.internalToUI(value)

    @staticmethod
    def to_internal(point: Sequence[float]) -> List[float]:
        import maya.api.OpenMaya as om

        return [om.MDistance.uiToInternal(float(c)) for c in point]


class ProbePlacement(ptk.ProbePlacement):
    """Place a lightmap bake's reflection probe in the Maya scene:
    :class:`pythontk.ProbePlacement` bound to :class:`MayaProbeHost` (Maya's
    rays, bounds, lights and units -- see the module docstring).

    Parameters:
        baked: The visible baked mesh transforms -- the room.
        unbaked: The visible mesh transforms no bake marks. Those whose
            footprint covers the bake's (a sky dome, a ground or terrain
            nobody baked) are the room's surroundings: seen, and walls. The
            rest move: the capture sees through them.
    """

    HOST_CLASS = MayaProbeHost
