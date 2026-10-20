# !/usr/bin/python
# coding=utf-8
"""Where a lightmap bake's reflection probe stands -- Maya's scene for the placement.

The placement itself -- open air near the bake's middle, the room around it
face by face, over what matters, at eye level, clear of every surface -- is
host-free and lives once in pythontk (:class:`pythontk.ProbePlacement`, whose
module docstring holds the rules and the scenes that forced each), shared with
blendertk's twin. This module hands it Maya's scene:

* **The rays** (:class:`_Rays`): ``MFnMesh.closestIntersection`` with each
  mesh's own acceleration grid, against the shapes under each node.
* **The bounds**: ``exactWorldBoundingBox``.
* **The lights**: :meth:`LightUtils.contributing_lights` -- a sky
  (:attr:`ProbePlacement.SKY_LIGHTS`) as the sky, a placeless one
  (:attr:`ProbePlacement.PLACELESS_LIGHTS`) as nothing, an ``aiMeshLight`` by
  its own mesh, the rest by their world position.
* **The units**: everything inside is in Maya's internal unit (centimetres)
  whatever the scene's; positions cross the boundary in scene units. Y is up.
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


class _Rays(ptk.ProbeRays):
    """:class:`pythontk.ProbeRays` over Maya meshes: the shapes under each node,
    intersected through ``MFnMesh.closestIntersection`` with their own
    acceleration grid."""

    def __init__(self, meshes: Sequence[str]):
        super().__init__()
        import maya.api.OpenMaya as om

        self._om = om
        for mesh in meshes:
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
                self.add(
                    mesh,
                    (box.min.x, box.min.y, box.min.z),
                    (box.max.x, box.max.y, box.max.z),
                    (fn, fn.autoUniformGridParams()),
                )

    def _intersect(self, payload, origin, direction, far):
        om = self._om
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

    def _normal(self, payload, face) -> Vec:
        normal = payload[0].getPolygonNormal(face, self._om.MSpace.kWorld)
        return (normal.x, normal.y, normal.z)


class ProbePlacement(ptk.ProbePlacement):
    """Place a lightmap bake's reflection probe in the Maya scene
    (:class:`pythontk.ProbePlacement`, with Maya's rays, bounds, lights and
    units -- see the module docstring).

    Parameters:
        baked: The visible baked mesh transforms -- the room.
        unbaked: The visible mesh transforms no bake marks. Those whose
            footprint covers the bake's (a sky dome, a ground or terrain
            nobody baked) are the room's surroundings: seen, and walls. The
            rest move: the capture sees through them.
    """

    #: Maya's internal linear unit, centimetres, in metres -- whatever the
    #: scene's UI unit.
    INTERNAL_M: float = Units.INTERNAL_M
    #: The up axis, as an index: Y. A probe's deliverables read it in Y-up
    #: axes, so :meth:`LightmapBaker.bake_probe` captures none in a Z-up scene.
    UP: int = 1
    #: Light types that stand for the sky rather than a fixture with a place.
    SKY_LIGHTS: Tuple[str, ...] = ("directionalLight", "aiSkyDomeLight")
    #: Light types that light nothing a ray can reach.
    PLACELESS_LIGHTS: Tuple[str, ...] = ("ambientLight", "aiLightPortal")

    def _make_rays(self, nodes: Sequence[str]) -> _Rays:
        return _Rays(nodes)

    def _read_lights(self) -> Tuple[List[Tuple[Vec, Optional[str]]], bool]:
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
                centre = self._centre(self._extent(own)) if own else None
                if centre is None:
                    continue
                points.append((tuple(centre), own))
                continue
            sel = om.MSelectionList()
            sel.add(shape)
            matrix = om.MTransformationMatrix(sel.getDagPath(0).inclusiveMatrix())
            t = matrix.translation(om.MSpace.kWorld)
            points.append(((t.x, t.y, t.z), None))
        return points, sky

    @staticmethod
    def _bounds(nodes: Sequence[str]) -> Box:
        """``[[min], [max]]`` of *nodes* in world space, internal units."""
        import maya.api.OpenMaya as om

        values = cmds.exactWorldBoundingBox(list(nodes))
        internal = [om.MDistance.uiToInternal(v) for v in values]
        return [internal[:3], internal[3:]]

    @staticmethod
    def _ui(value: float) -> float:
        import maya.api.OpenMaya as om

        return om.MDistance.internalToUI(value)

    @staticmethod
    def _to_internal(point: Sequence[float]) -> List[float]:
        import maya.api.OpenMaya as om

        return [om.MDistance.uiToInternal(float(c)) for c in point]
