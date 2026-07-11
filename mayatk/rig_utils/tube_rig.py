#!/usr/bin/env python
# coding=utf-8
import math
import re
from typing import Dict, List, Tuple, Optional
from abc import ABC, abstractmethod
from dataclasses import dataclass

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    om = None
    print(__file__, error)
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.rig_utils._rig_utils import RigUtils
from mayatk.rig_utils.controls import Controls
from mayatk.rig_utils.skinning import SkinUtils
from mayatk.edit_utils.naming._naming import Naming
from mayatk.core_utils._core_utils import leaf_name, short_name


def _xform_t_ws(node) -> List[float]:
    """World-space translation as a 3-list (replaces ``node.getTranslation(space='world')``)."""
    return cmds.xform(str(node), q=True, ws=True, t=True)


def _set_t_ws(node, pos) -> None:
    """Write world-space translation (replaces ``node.setTranslation(p, space='world')``)."""
    cmds.xform(str(node), ws=True, t=(float(pos[0]), float(pos[1]), float(pos[2])))


def _set_r_ws(node, rot) -> None:
    """Write world-space rotation in degrees (replaces ``node.setRotation(r, space='world')``)."""
    cmds.xform(str(node), ws=True, ro=(float(rot[0]), float(rot[1]), float(rot[2])))


def _long_path(node) -> Optional[str]:
    """Return the long DAG path for *node*, or the input unchanged if unresolvable."""
    if node is None:
        return None
    s = str(node)
    if not s:
        return s
    res = cmds.ls(s, long=True) or []
    return res[0] if res else s


def _resolve_mesh_shape(obj) -> Optional[str]:
    """Full path of the first non-intermediate mesh shape for *obj*, or None.

    Accepts a mesh transform, a mesh shape, a component, or a GROUP —
    outliner picks often land on the group, and ``polySelect`` /
    ``closestPointOnMesh`` reject non-mesh transforms even though
    ``polyListComponentConversion`` silently expands their descendants.
    Warns and uses the first shape when several qualify.
    """
    if obj is None:
        return None
    if isinstance(obj, (set, list, tuple)):
        obj = next(iter(obj), None)
    s = str(obj).split(".")[0] if obj is not None else ""
    if not s or not cmds.objExists(s):
        return None
    shapes = [
        sh for sh in NodeUtils.get_shapes(s) if cmds.objectType(sh) == "mesh"
    ]
    if not shapes:  # group / non-shape transform — descend to child meshes
        shapes = cmds.listRelatives(
            s, allDescendents=True, type="mesh", fullPath=True, noIntermediate=True
        ) or []
    if not shapes:
        return None
    if len(shapes) > 1:
        cmds.warning(
            f"Multiple mesh shapes under '{s}'; using {shapes[0]}."
        )
    return shapes[0]


def _parent_to(child, parent) -> str:
    """Parent ``child`` under ``parent`` (or world if ``parent is None``) and return
    the new long path. No-op if the child is already under the intended parent.
    Wraps ``cmds.parent`` and returns the new long path.
    """
    child_s = str(child)
    current = NodeUtils.get_parent(child_s, type=None, full_path=True)  # already a long path or None
    if parent is None:
        if current is None:
            return _long_path(child_s)
        result = cmds.parent(child_s, world=True)
        return result[0] if result else _long_path(child_s)
    parent_long = _long_path(parent)
    if current and parent_long and current == parent_long:
        return _long_path(child_s)
    try:
        result = cmds.parent(child_s, str(parent))
        return result[0] if result else _long_path(child_s)
    except RuntimeError as e:
        cmds.warning(f"_parent_to: could not parent {child_s} under {parent}: {e}")
        return _long_path(child_s)


def _frame_rotation(x_dir: "om.MVector") -> "om.MEulerRotation":
    """Euler rotation of a frame whose X axis is ``x_dir``, with a stable
    Y-up-ish orthogonal basis (falls back to Z-up when ``x_dir`` is near
    vertical). Shared by the anchor and spline driver builders.
    """
    x = om.MVector(x_dir).normal()
    world_up = om.MVector(0, 1, 0)
    if abs(x * world_up) > 0.99:
        world_up = om.MVector(0, 0, 1)
    z = (x ^ world_up).normal()
    y = (z ^ x).normal()
    m = om.MMatrix(
        [
            [x.x, x.y, x.z, 0],
            [y.x, y.y, y.z, 0],
            [z.x, z.y, z.z, 0],
            [0, 0, 0, 1],
        ]
    )
    return om.MTransformationMatrix(m).rotation()


def _euler_deg(euler) -> Tuple[float, float, float]:
    """Convert an ``om.MEulerRotation`` (radians) to a degrees 3-tuple."""
    return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))


def _path_end_directions(points) -> Tuple["om.MVector", "om.MVector"]:
    """Unit tangents at the start and end of a point path (local, not chord —
    correct for curved tubes)."""
    p = [
        om.MVector(pt[0], pt[1], pt[2])
        for pt in (points[0], points[1], points[-2], points[-1])
    ]
    return (p[1] - p[0]).normal(), (p[3] - p[2]).normal()


def _world_x_axis(node) -> "om.MVector":
    """World-space X axis of a transform (a joint's down-the-bone direction)."""
    m = cmds.xform(str(node), q=True, ws=True, matrix=True)
    v = om.MVector(m[0], m[1], m[2])
    return v.normal() if v.length() > 1e-6 else om.MVector(1, 0, 0)


def _control_path(nodes, group_path: str) -> str:
    """Long path of a control whose offset group was just reparented to
    *group_path*.

    ``ControlNodes.control`` is captured before we move the group, so it goes
    stale the moment the group is reparented (and outright wrong when a
    same-named control exists elsewhere). With an offset group the control is
    the group's direct child (``<group_path>|<control_leaf>``); with none,
    the callers reparent the control itself, so *group_path* already IS the
    control's post-reparent path — the stored ``nodes.control`` would be the
    stale pre-reparent address.
    """
    if nodes.group is None:
        return str(group_path)
    return f"{group_path}|{leaf_name(nodes.control)}"


class TubePath:
    """Pure geometry analysis for tube-like meshes.

    Extracts centerline paths from polygon tube meshes using different
    algorithms. All methods are static and produce only point data —
    no Maya scene objects (curves, joints, etc.) are created.

    Use ``get_centerline`` as the main entry point, which selects the
    best algorithm based on the ``num_joints`` hint.
    """

    @staticmethod
    def get_centerline(
        mesh,
        num_joints: int = 10,
        precision: int = 10,
        edges: list = None,
        use_surface_normals: bool = True,
    ) -> Tuple[List, int]:
        """Unified centerline dispatcher — picks the best algorithm.

        Parameters:
            mesh: The tube mesh object.
            num_joints: Requested joint count. ``-1`` = auto (uses edge loops).
            precision: Bounding-box precision (only used when num_joints > 0).
            edges: Optional pre-selected edges to derive centerline from.
            use_surface_normals: When True (default), uses the surface-normal
                opposing-hit method instead of axis-aligned bounding-box slicing.
                More accurate for curved or diagonal tubes.

        Returns:
            Tuple of (centerline_points, resolved_num_joints).
            When ``num_joints == -1`` the resolved count equals the number of
            edge-loop cross-sections found.

        Note:
            Edge-loop centres are always preferred when the topology yields
            them — they are exact and include the tube's end loops. Callers
            that want fewer joints than loops resample downstream
            (``generate_joint_chain``). The samplers are fallbacks only.
        """
        if edges:
            pts = TubePath.get_centerline_using_edges(edges)
            return pts, (len(pts) if num_joints == -1 else num_joints)

        # Resolve to the actual mesh shape up front: a group pick or a
        # multi-shape transform crashes polySelect / closestPointOnMesh
        # downstream even though component conversion appears to work.
        shape = _resolve_mesh_shape(mesh)
        if not shape:
            raise ValueError(f"No polygon mesh found under '{mesh}'.")

        pts, loop_count = TubePath.get_edge_loop_centers(shape)
        if len(pts) >= 2:
            return pts, (loop_count if num_joints == -1 else num_joints)

        # Fallback when edge-loop detection fails (irregular topology).
        resolved = 10 if num_joints == -1 else num_joints
        if use_surface_normals:
            pts = TubePath.get_centerline_from_surface_normals(
                shape, num_points=resolved
            )
        else:
            pts = TubePath.get_centerline_from_bounding_box(
                shape, precision=precision, smooth=True
            )
        # Sampled end estimates get pulled inboard by the cap planes (~one
        # radius on triangulated tubes) — extend to the true cap centres, as
        # the edge-loop path already does.
        if len(pts) >= 2:
            pts = TubePath._complete_cap_ends(shape, pts)
        return pts, resolved

    # ------------------------------------------------------------------
    # Algorithm: Edge-loop centres (topology-accurate)
    # ------------------------------------------------------------------

    @staticmethod
    def _loop_is_closed(mesh, loop_edges: List[int]) -> bool:
        """True when an edge loop forms a closed cycle (#verts == #unique edges).

        Open paths — longitudinal loops that terminate at caps or open
        boundaries — carry one extra vertex. This is the topology-exact test
        for "is this loop a circumferential cross-section". Counts UNIQUE
        edges because ``polySelect -q -edgeLoop`` signals closure by repeating
        the seed edge at the end of the returned list.
        """
        unique_edges = set(loop_edges)
        edges = [f"{mesh}.e[{i}]" for i in unique_edges]
        verts = cmds.ls(
            cmds.polyListComponentConversion(edges, fromEdge=True, toVertex=True),
            flatten=True,
        )
        return len({str(v) for v in verts}) == len(unique_edges)

    @staticmethod
    def get_edge_loop_centers(mesh) -> Tuple[List[om.MPoint], int]:
        """Extract centerline by finding all edge loops (cross-sections) of a tube mesh.

        This provides a more accurate centerline than bounding box approximation,
        and the number of edge loops determines the natural joint count.

        Parameters:
            mesh: The tube mesh object.

        Returns:
            Tuple of (centerline_points, num_loops) where:
                - centerline_points: List of center points for each edge loop
                - num_loops: Number of edge loops found (natural joint count)
        """
        # Resolve to the mesh shape: polySelect rejects groups and is
        # ambiguous on multi-shape transforms (idempotent for shape input).
        mesh = _resolve_mesh_shape(mesh)
        if not mesh:
            return [], 0

        # Get all edges of the mesh
        all_edges = cmds.ls(
            cmds.polyListComponentConversion(mesh, toEdge=True), flatten=True
        )
        if not all_edges:
            return [], 0

        # Seed with a CLOSED (circumferential) edge loop. An arbitrary first
        # edge may be LONGITUDINAL (common on user-modeled pipes; polyCylinder
        # just happens to number a rim edge first) — seeding from one
        # transposes the loop/ring traversal, and every "cross-section centre"
        # becomes a longitudinal-strip centroid: a small ring of points around
        # the mesh centroid instead of a path down the tube. Closure is the
        # topology-exact discriminator: a cross-section cycle has
        # #verts == #edges, while longitudinal loops end open at caps or
        # boundaries (#verts == #edges + 1). Skip edges already covered by a
        # rejected loop, so the scan is bounded by the number of distinct
        # loops, not the edge count.
        first_loop = None
        checked_edges = set()
        for edge in all_edges:
            edge_idx = int(str(edge).rsplit("[", 1)[-1].rstrip("]"))
            if edge_idx in checked_edges:
                continue
            loop = cmds.polySelect(mesh, q=True, edgeLoop=edge_idx)
            if not loop:
                checked_edges.add(edge_idx)
                continue
            checked_edges.update(loop)
            if len(loop) >= 3 and TubePath._loop_is_closed(mesh, loop):
                first_loop = loop
                break
        if not first_loop:
            return [], 0

        # Get the edge ring from the first loop edge — yields one edge per cross-section.
        ring_edges = cmds.polySelect(mesh, q=True, edgeRing=first_loop[0])
        if not ring_edges:
            return [], 0

        visited_loops = set()
        loop_centers = []

        for edge_idx in ring_edges:
            loop_edges = cmds.polySelect(mesh, q=True, edgeLoop=edge_idx)
            # Boundary rings on capped tubes degenerate to a single edge (cap
            # fan triangles break loop traversal) — their midpoint is off-axis,
            # so skip them; _complete_cap_ends recovers the true cap centres.
            # Open paths (partial arcs from irregular topology) are skipped
            # too: an arc's centroid sits off the tube axis.
            if not loop_edges or len(loop_edges) < 3:
                continue
            if not TubePath._loop_is_closed(mesh, loop_edges):
                continue

            # Canonical key over UNIQUE edges: polySelect repeats the seed
            # edge on closed loops, and the duplicate's sort position would
            # otherwise vary with the seed, defeating the visited-set.
            loop_key = tuple(sorted(set(loop_edges)))
            if loop_key in visited_loops:
                continue
            visited_loops.add(loop_key)

            # Collect unique vertex names in this loop.
            loop_vert_names = set()
            for e_idx in loop_edges:
                # Full shape path (resolved above, matching the component
                # base used for the loop queries) — keeps the component
                # unambiguous when another mesh shares this short name.
                edge = f"{mesh}.e[{e_idx}]"
                verts = cmds.polyListComponentConversion(
                    edge, fromEdge=True, toVertex=True
                )
                for v in cmds.ls(verts, flatten=True):
                    # Store vertex name string (hashable) to avoid MeshVertex type error
                    loop_vert_names.add(str(v))

            # Calculate center of this loop. cmds.pointPosition returns a plain
            # 3-list, so accumulate via MVector and divide to get the centroid.
            if loop_vert_names:
                accum = om.MVector(0.0, 0.0, 0.0)
                for v in loop_vert_names:
                    p = cmds.pointPosition(v, world=True)
                    accum += om.MVector(p[0], p[1], p[2])
                count = len(loop_vert_names)
                center = om.MPoint(
                    accum.x / count, accum.y / count, accum.z / count
                )
                loop_centers.append(center)

        # Sort centers to form a continuous path along the tube, then filter
        # near-coincident points (bevels/high-res loops produce virtually
        # identical centres, which cause zero-length bone vectors downstream).
        if loop_centers:
            loop_centers = ptk.Polyline.order_points(loop_centers)
            loop_centers = TubePath._dedupe_consecutive(loop_centers)

        if len(loop_centers) >= 2:
            loop_centers = TubePath._complete_cap_ends(mesh, loop_centers)

        return loop_centers, len(loop_centers)

    @staticmethod
    def _complete_cap_ends(mesh, centers: List[om.MPoint]) -> List[om.MPoint]:
        """Extend a loop-centre path to the mesh's true ends.

        Tubes can lose their end rings to degenerate loop queries (cap fans,
        boundary-adjacent topology), leaving the path one band short. The
        closest surface point past each end tells how far the surface really
        extends; the appended end centre is that hit's projection ONTO THE
        TANGENT AXIS. Using the raw hit is wrong on OPEN tubes: with no cap
        for the seed to hit, the closest point is a RIM vertex one
        wall-radius off-axis (an angle-cut opening also projects past the end
        ring's centroid plane) — appending it hooks the end joint toward an
        opening vertex. Projecting keeps the recovered end on-axis for caps
        and open/angled rims alike.
        """
        shape = NodeUtils.get_shape(mesh)
        if not shape:
            return centers

        cpom = cmds.createNode("closestPointOnMesh")
        try:
            cmds.connectAttr(f"{shape}.outMesh", f"{cpom}.inMesh")
            cmds.connectAttr(f"{shape}.worldMatrix[0]", f"{cpom}.inputMatrix")
            prepend, append = None, None
            for end, neighbor in ((0, 1), (-1, -2)):
                c_end = om.MVector(centers[end][0], centers[end][1], centers[end][2])
                c_prev = om.MVector(
                    centers[neighbor][0], centers[neighbor][1], centers[neighbor][2]
                )
                tangent = (c_end - c_prev).normal()
                spacing = (c_end - c_prev).length()
                if spacing < 1e-6:
                    continue

                seed = c_end + tangent * (spacing * 2)
                cmds.setAttr(
                    f"{cpom}.inPosition", seed.x, seed.y, seed.z, type="double3"
                )
                hit = cmds.getAttr(f"{cpom}.position")[0]
                hit_v = om.MVector(hit[0], hit[1], hit[2])
                along = (hit_v - c_end) * tangent
                if along > spacing * 0.25:
                    end_v = c_end + tangent * along
                    pt = om.MPoint(end_v.x, end_v.y, end_v.z)
                    if end == 0:
                        prepend = pt
                    else:
                        append = pt

            if prepend is not None:
                centers = [prepend] + list(centers)
            if append is not None:
                centers = list(centers) + [append]
            return centers
        finally:
            cmds.delete(cpom)

    @staticmethod
    def _dedupe_consecutive(points: List, min_dist: float = 0.001) -> List:
        """Drop consecutive points closer than ``min_dist`` to their predecessor."""
        if not points:
            return []
        result = [points[0]]
        for p in points[1:]:
            prev = result[-1]
            if math.dist(
                (prev[0], prev[1], prev[2]), (p[0], p[1], p[2])
            ) > min_dist:
                result.append(p)
        return result

    # ------------------------------------------------------------------
    # Measurement: tube radius (drives proportional rig sizing)
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_radius(mesh, centerline: List) -> Optional[float]:
        """Estimate the tube's radius: median distance from interior
        centerline points to the surface.

        Points within one probe length (half the smallest bounding-box
        dimension ≈ the tube-radius upper bound) of either end are excluded —
        their nearest surface is the cap plane, which reads near zero and
        poisons the estimate. Tubes too short to have interior points fall
        back to the single arc-midpoint sample.

        Returns:
            The estimated radius, or None when no mesh/centerline is usable.
        """
        shape = _resolve_mesh_shape(mesh)
        if not shape or not centerline or len(centerline) < 2:
            return None

        pts = [om.MPoint(float(p[0]), float(p[1]), float(p[2])) for p in centerline]
        arc = [0.0]
        for a, b in zip(pts, pts[1:]):
            arc.append(arc[-1] + om.MVector(b - a).length())
        total = arc[-1]
        if total < 1e-6:
            return None

        bbox = cmds.exactWorldBoundingBox(shape)
        dims = [abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])]
        probe = 0.5 * min((d for d in dims if d > 1e-6), default=1.0)

        interior = [
            p for p, d in zip(pts, arc) if d >= probe and (total - d) >= probe
        ]
        if not interior:
            # Tube shorter than ~2 radii: interpolate the arc midpoint.
            half = total / 2
            i = next(i for i in range(1, len(arc)) if arc[i] >= half)
            t = (half - arc[i - 1]) / max(arc[i] - arc[i - 1], 1e-9)
            a, b = pts[i - 1], pts[i]
            interior = [
                om.MPoint(
                    a.x + (b.x - a.x) * t,
                    a.y + (b.y - a.y) * t,
                    a.z + (b.z - a.z) * t,
                )
            ]

        stride = max(1, len(interior) // 15)
        samples = interior[::stride]

        cpom = cmds.createNode("closestPointOnMesh")
        try:
            cmds.connectAttr(f"{shape}.outMesh", f"{cpom}.inMesh")
            cmds.connectAttr(f"{shape}.worldMatrix[0]", f"{cpom}.inputMatrix")
            dists = []
            for p in samples:
                cmds.setAttr(f"{cpom}.inPosition", p.x, p.y, p.z, type="double3")
                hit = cmds.getAttr(f"{cpom}.position")[0]
                d = om.MVector(hit[0] - p.x, hit[1] - p.y, hit[2] - p.z).length()
                if d > 1e-6:
                    dists.append(d)
            if not dists:
                return None
            dists.sort()
            return dists[len(dists) // 2]
        finally:
            cmds.delete(cpom)

    # ------------------------------------------------------------------
    # Algorithm: User-selected edges (manual override)
    # ------------------------------------------------------------------

    @staticmethod
    def get_centerline_using_edges(
        edge_selection: List[str],
    ) -> List[List[float]]:
        """Derive centerline points from selected edges of the tube.

        Selected edges lie on the tube *surface*, so each edge midpoint is
        pushed onto the central axis via opposing-surface-hit refinement
        (see ``_refine_centers``). Works for a longitudinal edge path and
        for cross-section rings alike; near-coincident results (e.g. all
        edges of one ring) collapse to a single centre.

        Returns:
            Ordered ``[x, y, z]`` centerline points.
        """
        if not edge_selection:
            return []

        mesh = str(edge_selection[0]).split(".")[0]
        mesh_shape = NodeUtils.get_shape(mesh)
        if not mesh_shape:
            raise ValueError(f"Could not resolve mesh shape from edge: {edge_selection[0]}")

        seeds = []
        for edge in edge_selection:
            vertices = cmds.ls(
                cmds.polyListComponentConversion(edge, fromEdge=True, toVertex=True),
                flatten=True,
            )
            p1 = cmds.pointPosition(vertices[0], world=True)
            p2 = cmds.pointPosition(vertices[1], world=True)
            seeds.append(
                om.MPoint(
                    (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, (p1[2] + p2[2]) / 2
                )
            )

        centers = TubePath._refine_centers(mesh_shape, seeds)
        centers = ptk.Polyline.order_points(centers)
        centers = TubePath._dedupe_consecutive(centers)
        return [[p[0], p[1], p[2]] for p in centers]

    # ------------------------------------------------------------------
    # Algorithm: Surface-normal opposing-hit averaging
    # ------------------------------------------------------------------

    @staticmethod
    def get_centerline_from_surface_normals(
        mesh,
        num_points: int = 10,
        iterations: int = 3,
    ) -> List[om.MPoint]:
        """Calculate centerline by iteratively averaging opposing surface hits.

        For each sample along the tube this method:

        1. Queries ``closestPointOnMesh`` from an interior estimate.
        2. Uses the direction to the nearest surface to infer the radial axis.
        3. Queries again from the opposite side so both tube walls are sampled.
        4. Averages the two surface points to obtain the true cross-section center.

        Multiple iterations converge the estimate even when the initial seed
        is off-center.  Unlike bounding-box slicing this works regardless of
        tube orientation or curvature.

        Parameters:
            mesh: The tube mesh object.
            num_points: Number of centerline samples to generate.
            iterations: Refinement passes (2–3 is usually sufficient).

        Returns:
            List of centerline points as ``om.MPoint``.
        """
        mesh = NodeUtils.get_transform_node(mesh)
        if not mesh:
            raise ValueError(f"Invalid object: `{mesh}` {type(mesh)}")

        bbox = cmds.exactWorldBoundingBox(mesh)
        min_pt = om.MPoint(bbox[0], bbox[1], bbox[2])
        max_pt = om.MPoint(bbox[3], bbox[4], bbox[5])
        bbox_size = max_pt - min_pt
        largest_axis = max(range(3), key=lambda i: bbox_size[i])

        # Seed: sample evenly along the largest bbox axis through bbox center,
        # spanning the full extent (endpoints included — an interior-only span
        # leaves the tube ends unrigged).
        bbox_center = om.MPoint(
            (min_pt.x + max_pt.x) / 2,
            (min_pt.y + max_pt.y) / 2,
            (min_pt.z + max_pt.z) / 2,
        )
        step = bbox_size[largest_axis] / max(num_points - 1, 1)

        seeds = []
        for i in range(num_points):
            pt = om.MPoint(bbox_center)
            pt[largest_axis] = min_pt[largest_axis] + i * step
            seeds.append(pt)

        mesh_shape = NodeUtils.get_shape(mesh)
        centers = TubePath._refine_centers(mesh_shape, seeds, iterations)
        return ptk.Polyline.order_points(centers)

    @staticmethod
    def _refine_centers(
        mesh_shape, seeds: List[om.MPoint], iterations: int = 3
    ) -> List[om.MPoint]:
        """Refine interior estimates onto the tube axis by averaging opposing
        ``closestPointOnMesh`` hits. Shared by the surface-normal sampler and
        the edge-selection path."""
        cpom = cmds.createNode("closestPointOnMesh")
        try:
            cmds.connectAttr(f"{mesh_shape}.outMesh", f"{cpom}.inMesh")
            cmds.connectAttr(f"{mesh_shape}.worldMatrix[0]", f"{cpom}.inputMatrix")

            # Upper bound for the tube radius: half the smallest bounding-box
            # dimension (≈ the tube diameter). Used to step surface-coincident
            # seeds into the interior.
            bbox = cmds.exactWorldBoundingBox(mesh_shape)
            dims = [abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])]
            probe = 0.5 * min((d for d in dims if d > 1e-6), default=1.0)

            centers = list(seeds)
            for _ in range(iterations):
                refined = []
                for center in centers:
                    cmds.setAttr(
                        f"{cpom}.inPosition",
                        center.x, center.y, center.z,
                        type="double3",
                    )
                    pos_arr = cmds.getAttr(f"{cpom}.position")[0]
                    surface_pt = om.MPoint(pos_arr[0], pos_arr[1], pos_arr[2])

                    # Direction from current estimate to nearest surface
                    to_surface = om.MVector(surface_pt - center)
                    radius_est = to_surface.length()
                    if radius_est < 1e-6:
                        # Seed sits ON the surface (e.g. an edge midpoint) —
                        # the closest point is itself. Step inward along the
                        # surface normal so opposing-hit averaging can engage.
                        n = cmds.getAttr(f"{cpom}.normal")[0]
                        n_v = om.MVector(n[0], n[1], n[2])
                        if n_v.length() < 1e-6:
                            refined.append(center)
                            continue
                        n_v = n_v.normal()
                        step = probe * 0.5
                        refined.append(
                            om.MPoint(
                                center.x - n_v.x * step,
                                center.y - n_v.y * step,
                                center.z - n_v.z * step,
                            )
                        )
                        continue

                    direction = to_surface.normal()

                    # Query from the opposite side — overshoot past the far wall
                    opposite_query = center - direction * (radius_est * 3)
                    cmds.setAttr(
                        f"{cpom}.inPosition",
                        opposite_query.x, opposite_query.y, opposite_query.z,
                        type="double3",
                    )
                    pos_arr2 = cmds.getAttr(f"{cpom}.position")[0]
                    surface_pt2 = om.MPoint(pos_arr2[0], pos_arr2[1], pos_arr2[2])

                    # Midpoint of opposing surface hits ≈ true center (component-wise).
                    refined.append(om.MPoint(
                        (surface_pt.x + surface_pt2.x) / 2,
                        (surface_pt.y + surface_pt2.y) / 2,
                        (surface_pt.z + surface_pt2.z) / 2,
                    ))

                centers = refined
            return centers
        finally:
            cmds.delete(cpom)

    # ------------------------------------------------------------------
    # Algorithm: Bounding-box slicing (approximate, works on any mesh)
    # ------------------------------------------------------------------

    @staticmethod
    def get_centerline_from_bounding_box(
        obj, precision=10, smooth=False, window_size=1
    ):
        """Calculate the centerline of an object using the cross-section of its largest bounding box axis.

        Parameters:
            obj (str/obj/list): The object to calculate the centerline for.
            precision (int): The percentage of the largest axis length to determine the number of cross-sections.
            smooth (bool): Whether to apply smoothing to the centerline points.
            window_size (int): The size of the moving window for smoothing.

        Returns:
            list: Centerline points as a list of ``om.MPoint``.
        """
        obj = NodeUtils.get_transform_node(obj)
        if not obj:
            raise ValueError(f"Invalid object: `{obj}` {type(obj)}")

        # Calculate the bounding box of the object
        bbox = cmds.exactWorldBoundingBox(obj)
        min_point = om.MPoint(bbox[0], bbox[1], bbox[2])
        max_point = om.MPoint(bbox[3], bbox[4], bbox[5])

        # Determine the largest axis of the bounding box
        bbox_size = max_point - min_point
        largest_axis = max(range(3), key=lambda i: bbox_size[i])

        # Calculate the number of slices based on the precision
        slice_count = max(1, int(bbox_size[largest_axis] * (precision / 100)))

        # Fetch every vertex position once (a per-slice re-query is
        # O(slices x verts) cmds round-trips).
        shape = NodeUtils.get_shape(obj)
        flat = cmds.xform(f"{shape}.vtx[*]", q=True, ws=True, t=True) or []
        positions = [flat[i : i + 3] for i in range(0, len(flat), 3)]

        # Generate cross-sections along the largest axis
        centerline_points = []
        step = bbox_size[largest_axis] / slice_count
        for i in range(slice_count + 1):
            slice_pos = min_point[largest_axis] + i * step

            slice_positions = [
                p for p in positions if abs(p[largest_axis] - slice_pos) < step / 2
            ]
            if not slice_positions:
                continue

            # Centroid of the slice
            accum = om.MVector(0.0, 0.0, 0.0)
            for p in slice_positions:
                accum += om.MVector(p[0], p[1], p[2])
            count = len(slice_positions)
            center_point = om.MPoint(
                accum.x / count, accum.y / count, accum.z / count
            )
            centerline_points.append(center_point)

        # Apply smoothing if requested
        if smooth and centerline_points:
            centerline_points = ptk.Polyline.smooth(centerline_points, window_size)

        return centerline_points


# ======================================================================
# Data Containers
# ======================================================================


@dataclass
class TubeRigBundle:
    rig_group: str
    joints: List[str]
    ik_handle: Optional[str] = None
    curve: Optional[str] = None
    anchors: Optional[List[str]] = None
    controls: Optional[List[str]] = None


# ======================================================================
# Build Strategies (orchestrate TubeRig methods)
# ======================================================================


class TubeStrategy(ABC):
    @abstractmethod
    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        pass


class FKChainStrategy(TubeStrategy):
    """Joints → nested FK controls → parametric skin.

    Thin composition of the same ``TubeRig`` step methods the UI's
    step-by-step buttons call — one-click and step-by-step run identical
    code with identical parameters by construction.
    """

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig.logger.info("Building FK Chain Rig...")
        centerline, num_joints = rig.resolve_centerline(
            kwargs.get("num_joints", -1), edges=kwargs.get("edges")
        )
        if kwargs.get("reverse"):
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        joints = rig.generate_joint_chain(
            centerline, num_joints=num_joints, radius=joint_radius
        )
        controls = rig.create_fk_controls(joints, size=size)
        rig.skin_mesh(joints, centerline=centerline)

        rig.logger.info("FK Chain Build Complete.")
        return TubeRigBundle(rig_group=rig.rig_group, joints=joints, controls=controls)


class SplineIKStrategy(TubeStrategy):
    """Joints → spline-IK control rig → parametric skin along the IK curve."""

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig.logger.info("Building Spline IK Rig...")
        centerline, num_joints = rig.resolve_centerline(
            kwargs.get("num_joints", -1), edges=kwargs.get("edges")
        )
        if kwargs.get("reverse"):
            # Reverse before anything derives from the path so the joints,
            # the IK curve, and the skin solve all share one direction.
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        joints = rig.generate_joint_chain(
            centerline, num_joints=num_joints, radius=joint_radius
        )
        controls, ik_handle, curve = rig.create_spline_controls(
            joints,
            centerline=centerline,
            size=size,
            num_controls=kwargs.get("num_controls", 3),
            enable_stretch=kwargs.get("enable_stretch", True),
            enable_squash=kwargs.get("enable_squash", True),
            enable_volume=kwargs.get("enable_volume", True),
            enable_twist=kwargs.get("enable_twist", True),
            enable_auto_bend=kwargs.get("enable_auto_bend", False),
        )
        rig.skin_mesh(joints, curve=curve)

        rig.logger.info("Spline IK Build Complete.")
        return TubeRigBundle(
            rig_group=rig.rig_group,
            joints=joints,
            ik_handle=ik_handle,
            curve=curve,
            controls=controls,
        )


class AnchorStrategy(TubeStrategy):
    """Two end joints → anchor controls with distance stretch → parametric skin."""

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig.logger.info("Building Anchor Rig...")
        centerline, _ = rig.resolve_centerline(2, edges=kwargs.get("edges"))
        if len(centerline) < 2:
            raise ValueError("Could not determine centerline")
        if kwargs.get("reverse"):
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        joints = rig.create_anchor_joints(centerline, radius=joint_radius)
        controls = rig.create_anchor_controls(
            joints, size=size, enable_stretch=kwargs.get("enable_stretch", True)
        )
        rig.skin_mesh(joints, centerline=centerline)

        rig.logger.info("Anchor Rig Build Complete.")
        return TubeRigBundle(
            rig_group=rig.rig_group,
            joints=joints,
            anchors=None,
            controls=controls,
        )


# ======================================================================
# Rig Engine
# ======================================================================


class TubeRig(ptk.LoggingMixin):
    """Rig engine for tube-shaped meshes: joints, IK, controls, skinning.

    Parameters:
        obj (str/obj): The polygon tube mesh to rig.
        rig_name (str): The name of the rig (auto-generated if omitted).
        rig_group (str): An existing group node to build under (auto-created
            as ``<rig_name>_GRP`` if omitted).

    Attributes:
        mesh (str): The tube mesh transform the rig binds to.
        joints (List[str]): The main joint chain (set by ``build``).
        ik_handle (Optional[str]): The IK handle, when the strategy creates one.
        skin_cluster (Optional[str]): The mesh's skinCluster, once bound.
        bundle (Optional[TubeRigBundle]): Full result of the last ``build``.

    Example:
        rig = TubeRig(mesh, rig_name="hose")
        rig.build(strategy="spline", num_joints=-1)  # -1 = joint per edge loop
        rig.bundle.controls  # animation controls

        # Later, look the rig up from the mesh or anything under the rig group:
        rig = TubeRig.for_node(selected_joint_or_mesh)

    Rebuilding on an already-rigged mesh tears the previous build down first
    (``teardown``). Instances are tracked in-session only — the registry does
    not survive a Maya restart.
    """

    # Class-level back-reference cache. cmds-based code uses plain node-path
    # strings, which can't carry an attached ``.rig`` attribute the way object
    # wrappers did. Keyed by the mesh's Maya UUID — stable across rename and
    # reparent (path strings are not).
    _instances: Dict[str, "TubeRig"] = {}

    def __init__(self, obj, rig_name: str = None, rig_group: str = None):
        if rig_name:
            # The UI's free-text field flows in verbatim. Illegal characters
            # crash the stale-sweep ``cmds.ls`` pattern ('hose-01', 'my rig'),
            # and names Maya auto-sanitizes on createNode (leading digit, '*')
            # would no longer match that pattern, accumulating chains on rerun.
            rig_name = Naming.strip_illegal_chars(rig_name)
            if rig_name[0].isdigit():
                rig_name = f"_{rig_name}"
        self._rig_name = rig_name
        self._rig_group = rig_group  # Only assigned if explicitly passed (else will be handled by property)
        if isinstance(obj, (set, list, tuple)):
            obj = next(iter(obj))
        # Prefer the mesh transform even when a GROUP was picked (common
        # outliner selection) — everything downstream (centerline, skinning)
        # needs the mesh, not its group.
        shape = _resolve_mesh_shape(obj)
        if shape:
            self.mesh = (
                NodeUtils.get_parent(shape, type=None, full_path=True) or str(shape)
            )
        else:
            # No mesh anywhere below — keep accepting plain transforms
            # (b002 constructs a TubeRig from a joint after a restart).
            resolved = NodeUtils.get_transform_node(obj)
            if not resolved:
                raise ValueError(f"Invalid object: `{obj}` {type(obj)}")
            if isinstance(resolved, (set, list, tuple)):
                resolved = resolved[0]
            self.mesh = str(resolved)
        self._mesh_uuid = TubeRig._uuid(self.mesh)
        if self._mesh_uuid:
            TubeRig._instances[self._mesh_uuid] = self
        self.joints = None
        self.ik_handle = None
        self.pole_vector = None
        self.skin_cluster = None
        self.start_loc = None
        self.end_loc = None
        self.anchors = None
        self.bundle = None

    @staticmethod
    def _uuid(node) -> Optional[str]:
        """Return the Maya UUID for *node*, or None if it doesn't exist."""
        if node is None:
            return None
        s = str(node)
        if not s or not cmds.objExists(s):
            return None
        res = cmds.ls(s, uuid=True) or []
        return res[0] if res else None

    @classmethod
    def for_mesh(cls, mesh) -> Optional["TubeRig"]:
        """Look up an existing TubeRig instance bound to *mesh*, or return None.

        Resolves *mesh* to a transform node, then to its UUID, before doing the
        cache lookup — so callers can pass a shape, a stale path, or the mesh
        transform interchangeably.
        """
        if mesh is None:
            return None
        target = NodeUtils.get_transform_node(mesh) or mesh
        uuid = cls._uuid(target)
        if not uuid:
            return None
        rig = cls._instances.get(uuid)
        if rig is None:
            return None
        # Refresh the stored path if it went stale (mesh was renamed / reparented).
        if not cmds.objExists(rig.mesh):
            refreshed = cmds.ls(uuid, long=True) or []
            if refreshed:
                rig.mesh = refreshed[0]
            else:
                cls._instances.pop(uuid, None)
                return None
        return rig

    @classmethod
    def for_node(cls, node) -> Optional["TubeRig"]:
        """Find the TubeRig owning *node* — the rigged mesh itself, or
        anything under the rig group (joints, controls, sub-groups).

        ``build`` registers the rig group alongside the mesh, so walking a
        node's ancestors resolves joints/controls back to their rig.
        """
        if node is None:
            return None
        rig = cls.for_mesh(node)
        if rig is not None:
            return rig
        # Walk from the raw node — NodeUtils.get_transform_node returns a
        # *list* of related transforms for joints, which is useless here.
        node_s = str(node)
        if cmds.objExists(node_s):
            parent = NodeUtils.get_parent(node_s, type=None, full_path=True)
            while parent:
                rig = cls.for_mesh(parent)
                if rig is not None:
                    return rig
                parent = NodeUtils.get_parent(parent, type=None, full_path=True)
        return None

    # ------------------------------------------------------------------
    # Properties / Rig Infrastructure
    # ------------------------------------------------------------------

    @property
    def rig_name(self) -> str:
        """Returns the rig name."""
        if not self._rig_name:
            self._rig_name = Naming.generate_unique_name("tube_rig_0")
        return self._rig_name

    @property
    def rig_group(self) -> str:
        # A cached group reference can go stale (undo of a build, manual
        # delete) — drop it and recreate rather than returning a dead path.
        if self._rig_group and not cmds.objExists(str(self._rig_group)):
            self.logger.info(
                f"Rig group '{self._rig_group}' no longer exists; recreating."
            )
            self._rig_group = None
        if not self._rig_group:
            rig_name = f"{self.rig_name}_GRP"
            if cmds.objExists(rig_name):
                self.logger.info(f"Found rig group: {rig_name}")
                self._rig_group = cmds.ls(rig_name)[0]
            else:
                self.logger.info(f"Creating rig group: {rig_name}")
                self._rig_group = cmds.group(empty=True, name=rig_name)
                cmds.makeIdentity(self._rig_group, apply=True, t=1, r=1, s=1, n=0)
            # Register the group so for_node() resolves joints/controls
            # parented under it back to this rig — the step workflow (b001
            # 'Create Joints' → b002) materializes the group here without
            # ever calling build().
            grp_uuid = TubeRig._uuid(self._rig_group)
            if grp_uuid:
                TubeRig._instances[grp_uuid] = self
        return str(self._rig_group)

    @rig_group.setter
    def rig_group(self, new_group: "object"):
        """Allows setting a custom rig group."""
        if new_group and cmds.objExists(str(new_group)) and cmds.objectType(
            str(new_group), isAType="transform"
        ):
            self._rig_group = new_group
            self.logger.debug(f"Rig group set to: {self._rig_group}")
        else:
            self._rig_group = None  # Will trigger auto-create if accessed
            self.logger.debug("Rig group reset (None); will be auto-created on access.")

    def teardown(self) -> None:
        """Delete everything a previous ``build`` created — the rig group and
        its contents, the mesh's skinCluster, and stray ``<rig_name>_*``
        utility (DG) nodes — so the rig can rebuild cleanly."""
        if self.mesh and cmds.objExists(str(self.mesh)):
            shape = NodeUtils.get_shape(self.mesh)
            if shape:
                for sc in cmds.ls(cmds.listHistory(shape) or [], type="skinCluster"):
                    cmds.delete(sc)

        # Utility nodes (curveInfo, multiplyDivide, blendColors, ...) are DG
        # nodes outside the group; all are prefixed with the rig name. Delete
        # them before the group so e.g. curveInfo doesn't evaluate against an
        # already-deleted curve.
        strays = set(cmds.ls(f"{self.rig_name}_*") or [])
        dag = set(cmds.ls(f"{self.rig_name}_*", type="dagNode") or [])
        for n in strays - dag:
            if cmds.objExists(n):
                cmds.delete(n)

        grp = self._rig_group or f"{self.rig_name}_GRP"
        if grp and cmds.objExists(str(grp)):
            cmds.delete(str(grp))

        self._rig_group = None
        self.joints = None
        self.ik_handle = None
        self.pole_vector = None
        self.skin_cluster = None
        self.start_loc = None
        self.end_loc = None
        self.anchors = None
        self.bundle = None

    def build(self, strategy: str = "spline", **kwargs):
        """Builds the rig using the specified strategy.

        Rebuilding on an already-rigged mesh tears the previous build down
        first (joint names would collide and the re-bind would fail).

        Args:
            strategy (str): The rigging strategy to use ("spline", "anchor", "fk").
            **kwargs: Additional arguments for the build process.
        """
        if strategy == "spline":
            strat = SplineIKStrategy()
        elif strategy == "anchor":
            strat = AnchorStrategy()
        elif strategy == "fk":
            strat = FKChainStrategy()
        else:
            raise NotImplementedError(f"Strategy '{strategy}' not implemented.")

        existing_grp = self._rig_group or f"{self.rig_name}_GRP"
        if self.bundle or cmds.objExists(str(existing_grp)):
            self.logger.info(f"Rebuilding {self.rig_name}: tearing down previous rig.")
            self.teardown()

        self.bundle = strat.build(self, **kwargs)

        # Populate legacy attributes for backward compatibility
        self.joints = self.bundle.joints

        # Bundle might have ik_handle or controls
        if self.bundle.ik_handle:
            self.ik_handle = self.bundle.ik_handle
        if self.bundle.anchors:
            self.anchors = self.bundle.anchors

        # Add controls to legacy attributes if supported in future or just use bundle
        # However, to be nice to consumers:
        if self.bundle.controls:
            # Just expose the main start/end controls
            self.start_loc = self.bundle.controls[0]
            self.end_loc = self.bundle.controls[-1]

        return self

    # ------------------------------------------------------------------
    # Measurement (shared by strategies and the step-by-step UI)
    # ------------------------------------------------------------------

    def resolve_centerline(
        self, num_joints: int = -1, edges: list = None
    ) -> Tuple[List, int]:
        """Extract this rig's tube centerline.

        Single source for the extraction parameters so the step-by-step UI
        and the one-click strategies stay in lock-step.

        Parameters:
            num_joints: Requested joint count; ``-1`` = one per edge loop.
            edges: Optional user-selected edges to derive the path from.

        Returns:
            Tuple of (centerline_points, resolved_num_joints).
        """
        return TubePath.get_centerline(
            self.mesh, num_joints=num_joints, precision=50, edges=edges
        )

    def estimate_tube_radius(self, centerline: List = None) -> Optional[float]:
        """Measure the tube's radius from the mesh surface.

        Uses the given centerline when provided, else the current joint
        positions, else a fresh centerline extraction. Returns None when the
        rig has no resolvable mesh (e.g. constructed from a bare joint).
        """
        shape = _resolve_mesh_shape(self.mesh)
        if not shape:
            return None
        pts = list(centerline) if centerline else None
        if not pts:
            joints = [str(j) for j in (self.joints or []) if cmds.objExists(str(j))]
            if len(joints) >= 2:
                pts = [_xform_t_ws(j) for j in joints]
        if not pts:
            try:
                pts, _ = TubePath.get_centerline(shape, num_joints=-1)
            except ValueError:
                return None
        return TubePath.estimate_radius(shape, pts)

    def resolve_sizes(
        self, centerline: List = None, joint_radius: float = -1.0
    ) -> Tuple[float, float]:
        """Resolve (joint_display_radius, control_base_size) from the tube.

        The control base size is the measured tube radius — every control
        multiplier is tuned against it, so rigs stay proportional to the mesh
        regardless of scene scale. ``joint_radius`` <= 0 means Auto (half the
        tube radius). Without a resolvable mesh (step mode from a bare joint
        selection) the tube radius falls back to the joints' own display
        radii, which Auto-sized joints carry at half tube radius.
        """
        tube_radius = self.estimate_tube_radius(centerline)
        if not tube_radius:
            radii = [
                cmds.getAttr(f"{j}.radius")
                for j in (self.joints or [])
                if cmds.objExists(str(j))
            ]
            tube_radius = max(radii) * 2.0 if radii else 1.0
        if joint_radius is None or joint_radius <= 0:
            joint_radius = tube_radius * 0.5
        return joint_radius, tube_radius

    # ------------------------------------------------------------------
    # Joint Creation
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def generate_joint_chain(
        self,
        centerline: List[List[float]],
        num_joints: int,
        reverse: bool = False,
        **kwargs,
    ) -> List[str]:
        """
        Generates joints along the tube's centerline.

        Parameters:
            centerline: List of points defining the tube's path.
            num_joints: Number of joints to create. If -1, creates one joint per
                centerline point (useful when centerline is from edge loops).
            reverse: If True, reverses the joint chain direction.

        Keyword Args:
            radius (float): Joint display radius.
            orientation (List[float]): Explicit jointOrient values for every
                joint. Default ``None`` auto-orients the chain (X aims at the
                child, Y up; the end joint's orient is zeroed).

        Any previous joints matching this rig's ``<rig_name>_jnt_*`` prefix
        are deleted first — stale chains anywhere in the scene otherwise make
        the short names ambiguous or collide on parenting.
        """
        radius: float = kwargs.pop("radius", 1.0)
        orientation: Optional[List[float]] = kwargs.pop("orientation", None)

        # Sweep leftover chains sharing this rig's joint prefix (reruns of
        # "Create Joints", debris from crashed builds, undo remnants).
        stale = cmds.ls(f"{self.rig_name}_jnt_*", type="joint", long=True) or []
        if stale:
            self.logger.info(
                f"Replacing {len(stale)} existing '{self.rig_name}_jnt_*' joint(s)."
            )
            for n in stale:
                if cmds.objExists(n):
                    cmds.delete(n)

        if num_joints == -1:
            # Use centerline points directly as joint positions
            joint_positions = list(centerline)
            if reverse:
                joint_positions = joint_positions[::-1]
        else:
            joint_positions = ptk.Polyline.resample(
                centerline, num_joints, reverse
            )
        joints = []
        parent_joint = None

        for i, pos in enumerate(joint_positions):
            self.logger.debug(
                f"Generating joint {i+1}, position: {pos}, radius: {radius}"
            )
            # Always clear selection before joint creation to avoid Maya's implicit parenting
            cmds.select(clear=True)
            jnt = cmds.createNode(
                "joint",
                name=f"{self.rig_name}_jnt_{i+1}",
            )
            # ``pos`` may be ``om.MPoint``; cmds.xform's ``t=`` flag wants
            # a 3-tuple of plain floats.
            t_xyz = (float(pos[0]), float(pos[1]), float(pos[2]))
            cmds.xform(jnt, ws=True, t=t_xyz)
            cmds.setAttr(f"{jnt}.radius", radius)
            if orientation:
                cmds.setAttr(f"{jnt}.jointOrient", *orientation, type="double3")
            # Parent — track the long path _parent_to returns so later ops
            # can't hit ambiguous short names (parenting renames on clash).
            jnt = _parent_to(jnt, self.rig_group if i == 0 else parent_joint)
            parent_joint = jnt
            joints.append(jnt)

        # Default orientation: X aims down the chain, Y up; the end joint has
        # no child to aim at, so its orient is zeroed.
        if orientation is None and len(joints) > 1:
            cmds.joint(joints[0], e=True, oj="xyz", sao="yup", ch=True, zso=True)
            cmds.setAttr(f"{joints[-1]}.jointOrient", 0, 0, 0, type="double3")

        self.logger.debug(f"Generated joints: {[leaf_name(j) for j in joints]}")
        self.joints = joints
        return joints

    @CoreUtils.undoable
    def create_anchor_joints(
        self, centerline: List, radius: float = 1.0
    ) -> List[str]:
        """Create the anchor rig's two end joints from the tube centerline.

        Both joints are oriented X-down-the-tube (baked into jointOrient) —
        the distance-driven stretch scales ``scaleX``, which must run along
        the tube, not world X. They are *siblings* under a joint group, not a
        chain: the start joint's stretch scale must not propagate to the end
        joint, which stays pinned to its anchor.

        Any previous ``<rig_name>_start_jnt`` / ``<rig_name>_end_jnt`` joints
        (and their group) are replaced, mirroring ``generate_joint_chain``'s
        rerun semantics.
        """
        if not centerline or len(centerline) < 2:
            raise ValueError("Anchor joints need a centerline with at least 2 points.")

        start_pos = om.MVector(centerline[0])
        end_pos = om.MVector(centerline[-1])
        dir_start, dir_end = _path_end_directions(centerline)

        stale = cmds.ls(
            f"{self.rig_name}_start_jnt*",
            f"{self.rig_name}_end_jnt*",
            type="joint",
            long=True,
        ) or []
        stale += cmds.ls(f"{self.rig_name}_joints_GRP*", type="transform", long=True) or []
        if stale:
            self.logger.info(
                f"Replacing {len(stale)} existing '{self.rig_name}' anchor node(s)."
            )
            for n in stale:
                if cmds.objExists(n):
                    cmds.delete(n)

        # Joint group (separate from controls for clean export)
        joint_grp = cmds.group(empty=True, name=f"{self.rig_name}_joints_GRP")
        joint_grp = _parent_to(joint_grp, str(self.rig_group))

        def _make_anchor_joint(suffix, pos, x_dir):
            cmds.select(clear=True)
            jnt = cmds.createNode("joint", name=f"{self.rig_name}_{suffix}_jnt")
            cmds.xform(jnt, ws=True, t=(pos.x, pos.y, pos.z))
            cmds.xform(jnt, ws=True, ro=_euler_deg(_frame_rotation(x_dir)))
            cmds.makeIdentity(jnt, apply=True, r=True)  # bake into jointOrient
            jnt = _parent_to(jnt, joint_grp)
            cmds.setAttr(f"{jnt}.radius", radius)
            return jnt

        j1 = _make_anchor_joint("start", start_pos, dir_start)
        j2 = _make_anchor_joint("end", end_pos, dir_end)

        self.joints = [j1, j2]
        return [j1, j2]

    # ------------------------------------------------------------------
    # Curves & IK
    # ------------------------------------------------------------------

    def skin_mesh(
        self,
        joints: List[str],
        curve: Optional[str] = None,
        centerline: Optional[List] = None,
        skinning_method: str = "dqs",
        mesh: Optional[str] = None,
    ) -> Optional[str]:
        """Smooth-bind the mesh to *joints* and record the skinCluster
        (``constrain_end_with_falloff`` and re-builds depend on it).

        With a *curve* or *centerline* (every strategy has one in hand at skin
        time), weights are solved analytically along it — ring-uniform, a
        C2-smooth cubic basis spanning up to 4 joints so bends spread
        smoothly instead of hinging at each joint — via
        ``SkinUtils.bind_to_curve``; if that solve fails (e.g. joints that
        don't order along the path), a geodesic-voxel bind is used instead.
        Both default to dual quaternion skinning for volume-preserving bends
        and twist.

        An existing skinCluster on the mesh is replaced (re-running the bind
        step re-binds, mirroring ``generate_joint_chain``'s rerun semantics).
        ``mesh`` defaults to the rig's own mesh.
        """
        joints = [str(j) for j in joints]
        mesh = str(mesh) if mesh else str(self.mesh)
        name = f"{self.rig_name}_skinCluster"

        existing = SkinUtils.get_skin_cluster(mesh)
        if existing:
            self.logger.info(
                f"Replacing existing skinCluster '{existing}' on {leaf_name(mesh)}."
            )
            # unbind (not delete): a bare delete leaves orphan bindPose/
            # dagPose nodes and joint lockInfluenceWeights attrs behind —
            # re-running Bind Skin N times accumulated N orphan dagPoses.
            SkinUtils.unbind(mesh)

        if curve or centerline:
            try:
                self.skin_cluster = SkinUtils.bind_to_curve(
                    mesh,
                    joints,
                    curve=str(curve) if curve else None,
                    centerline=centerline,
                    profile="smoothstep",
                    skinning_method=skinning_method,
                    name=name,
                )
                return self.skin_cluster
            except Exception as e:
                # bind_to_curve solves before binding, so a failure here
                # leaves the mesh unbound and the fallback safe.
                self.logger.warning(
                    f"Parametric skinning failed ({e}); falling back to geodesic bind."
                )
        try:
            self.skin_cluster = SkinUtils.bind(
                mesh,
                joints,
                bind_method="geodesic",
                skinning_method=skinning_method,
                max_influences=4,
                name=name,
            )
        except Exception as e:
            self.logger.warning(f"Failed to skin mesh: {e}")
            return None
        return self.skin_cluster

    @CoreUtils.undoable
    def create_logic_curve(
        self, centerline: List[List[float]]
    ) -> str:
        """Creates the logic curve for Spline IK."""
        degree = 3 if len(centerline) >= 4 else 1
        curve_name = f"{self.rig_name}_ik_curve"
        # ``centerline`` may contain ``om.MPoint`` instances; cmds.curve
        # wants flat (x, y, z) tuples. Edit points (not CVs) — the curve must
        # pass through the centerline or spline IK drags joints off-centre.
        points = [
            (float(p[0]), float(p[1]), float(p[2])) for p in centerline
        ]
        curve = cmds.curve(ep=points, d=degree, name=curve_name)
        curve = cmds.parent(curve, str(self.rig_group))[0]
        cmds.setAttr(f"{curve}.inheritsTransform", False)  # Prevent double transform
        cmds.setAttr(f"{curve}.visibility", False)
        return curve

    # ------------------------------------------------------------------
    # Spline IK Driver System (controls, tangents, up locators)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_spline_drivers(
        self, centerline: List[List[float]], radius: float = 1.0, num_controls: int = 3
    ) -> Tuple[List[str], List[str], List]:
        """Creates the driver system (controls and joints) for the Spline IK curve.

        Control positions are distributed along the centerline itself (not
        its chord), so bent tubes get on-path controls; end frames come from
        the local path tangents.
        """
        start_pos = om.MVector(centerline[0])
        end_pos = om.MVector(centerline[-1])
        tube_length = (end_pos - start_pos).length()

        dir_start, dir_end = _path_end_directions(centerline)
        start_rot = _frame_rotation(dir_start)
        end_rot = _frame_rotation(-dir_end)  # X points back into the tube

        rig_grp = str(self.rig_group)

        def _create_ctrl(
            name, pos, rot=None, scale=1.0, color=(1, 1, 0), shape="box", parent=None
        ):
            # ``Controls.create`` accepts ``size=`` for the uniform scale.
            # ``scale=``/``radius=`` are absorbed by preset-builder ``**_`` and silently no-op.
            if shape == "sphere":
                nodes = Controls.sphere(
                    name=name, size=scale, color=color, return_nodes=True
                )
            else:
                nodes = Controls.box(
                    name=name, size=scale, color=color, return_nodes=True
                )

            grp = nodes.group if nodes.group else nodes.control
            _set_t_ws(grp, (pos[0], pos[1], pos[2]))
            if rot is not None:
                _set_r_ws(grp, _euler_deg(rot))

            grp = _parent_to(grp, parent if parent else rig_grp)
            return _control_path(nodes, grp)

        driver_grp = cmds.group(empty=True, name=f"{self.rig_name}_driver_GRP")
        driver_grp = _parent_to(driver_grp, rig_grp)
        cmds.setAttr(f"{driver_grp}.visibility", False)

        controls: List[str] = []
        driver_joints: List[str] = []

        if num_controls == 3:
            # ------------------------------------------------------------------
            # Standard 3-Point System (Start, Mid, End + Tangents)
            # ------------------------------------------------------------------
            mid_pos = ptk.Polyline.resample(centerline, 3)[1]  # on-path midpoint
            start_ctrl = _create_ctrl(
                f"{self.rig_name}_start", start_pos, start_rot, radius * 3
            )
            mid_ctrl = _create_ctrl(
                f"{self.rig_name}_mid", mid_pos, None, radius * 2.5
            )
            end_ctrl = _create_ctrl(
                f"{self.rig_name}_end", end_pos, end_rot, radius * 3
            )

            controls = [start_ctrl, mid_ctrl, end_ctrl]

            # Tangent Controls — offset inboard along the local path tangents.
            tan_offset = tube_length * 0.2

            start_tan_pos = start_pos + dir_start * tan_offset
            start_tan_ctrl = _create_ctrl(
                f"{self.rig_name}_start_tan",
                start_tan_pos,
                rot=start_rot,
                scale=radius * 0.5,
                color=(1, 0.5, 0),
                shape="sphere",
                parent=start_ctrl,
            )

            end_tan_pos = end_pos - dir_end * tan_offset
            end_tan_ctrl = _create_ctrl(
                f"{self.rig_name}_end_tan",
                end_tan_pos,
                rot=end_rot,
                scale=radius * 0.5,
                color=(1, 0.5, 0),
                shape="sphere",
                parent=end_ctrl,
            )

            driver_sources = [
                (start_ctrl, "start"),
                (start_tan_ctrl, "start_tan"),
                (mid_ctrl, "mid"),
                (end_tan_ctrl, "end_tan"),
                (end_ctrl, "end"),
            ]

            for source, suffix in driver_sources:
                cmds.select(clear=True)
                jnt = cmds.createNode(
                    "joint", name=f"{self.rig_name}_driver_{suffix}_jnt"
                )
                _set_t_ws(jnt, _xform_t_ws(source))
                jnt = _parent_to(jnt, driver_grp)
                cmds.setAttr(f"{jnt}.radius", radius * 1.5)
                cmds.parentConstraint(str(source), jnt, mo=True)
                driver_joints.append(jnt)

        else:
            # ------------------------------------------------------------------
            # Distributed N-Point System
            # ------------------------------------------------------------------
            positions = ptk.Polyline.resample(centerline, num_controls)

            for i, pos in enumerate(positions):
                name = f"{self.rig_name}_ctrl_{i+1}"

                rot = None
                if i == 0:
                    rot = start_rot
                elif i == len(positions) - 1:
                    rot = end_rot

                ctrl = _create_ctrl(
                    name,
                    pos,
                    rot=rot,
                    scale=radius * 2.5,
                    color=(1, 1, 0),
                    shape="box",
                )
                controls.append(ctrl)

                cmds.select(clear=True)
                jnt = cmds.createNode(
                    "joint", name=f"{self.rig_name}_driver_{i+1}_jnt"
                )
                _set_t_ws(jnt, _xform_t_ws(ctrl))
                jnt = _parent_to(jnt, driver_grp)
                cmds.setAttr(f"{jnt}.radius", radius * 1.5)
                cmds.parentConstraint(str(ctrl), jnt, mo=True)
                driver_joints.append(jnt)

        # Natural drape: intermediate controls ride between the end controls
        # via a follow group above their offset group — point-constrained by
        # arc position, offset maintained. Carrying or posing the ends then
        # carries the whole hose (without this, the mid control stays nailed
        # to its build position and the tube body lags its end constraints).
        # The controls themselves stay free for hand-animated offsets on top.
        for i, ctrl in enumerate(controls[1:-1], start=1):
            t = i / (len(controls) - 1)
            offset_grp = (
                NodeUtils.get_parent(str(ctrl), type=None, full_path=True)
                or str(ctrl)
            )
            follow = cmds.group(
                empty=True, name=f"{self.rig_name}_follow_{i}_GRP"
            )
            cmds.delete(cmds.parentConstraint(offset_grp, follow))
            parent = NodeUtils.get_parent(offset_grp, type=None, full_path=True)
            if parent:
                follow = _parent_to(follow, parent)
            offset_grp = _parent_to(offset_grp, follow)
            # Moving the offset group changed the control's DAG path — refresh
            # the list entry (downstream consumers: auto-bend, the bundle,
            # the end-constraint resolver) to the canonical path.
            moved = f"{offset_grp}|{leaf_name(ctrl)}"
            controls[i] = (cmds.ls(moved, long=True) or [moved])[0]
            cmds.pointConstraint(str(controls[0]), follow, mo=True, weight=1.0 - t)
            cmds.pointConstraint(str(controls[-1]), follow, mo=True, weight=t)

        # Up Locators (Start/End Twist Anchors). Parented under the controls —
        # the IK handle's "Object Rotation Up" twist reads the locators'
        # *rotation*, so they must inherit it from the controls (a
        # point-constrained locator never rotates and the twist goes dead).
        up_offset = tube_length * 0.1

        start_up_loc = cmds.spaceLocator(name=f"{self.rig_name}_start_up_loc")[0]
        start_up_loc = _parent_to(start_up_loc, controls[0])
        s_pos = _xform_t_ws(controls[0])
        _set_t_ws(start_up_loc, (s_pos[0], s_pos[1] + up_offset, s_pos[2]))
        cmds.setAttr(f"{start_up_loc}.visibility", False)

        end_up_loc = cmds.spaceLocator(name=f"{self.rig_name}_end_up_loc")[0]
        end_up_loc = _parent_to(end_up_loc, controls[-1])
        e_pos = _xform_t_ws(controls[-1])
        _set_t_ws(end_up_loc, (e_pos[0], e_pos[1] + up_offset, e_pos[2]))
        cmds.setAttr(f"{end_up_loc}.visibility", False)

        return (controls, driver_joints, [start_up_loc, end_up_loc])

    @CoreUtils.undoable
    def skin_curve_to_drivers(self, curve, driver_joints):
        try:
            cmds.skinCluster(driver_joints, curve, toSelectedBones=True)
        except Exception as e:
            self.logger.warning(f"Failed to skin curve: {e}")

    # ------------------------------------------------------------------
    # Control Rigs (shared by strategies and the step-by-step UI)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_spline_controls(
        self,
        joints: List[str],
        centerline: Optional[List] = None,
        size: float = 1.0,
        num_controls: int = 3,
        enable_stretch: bool = True,
        enable_squash: bool = True,
        enable_volume: bool = True,
        enable_twist: bool = True,
        enable_auto_bend: bool = False,
    ) -> Tuple[List[str], str, str]:
        """Build the complete spline-IK control rig over an existing joint chain:
        logic curve, IK handle, driver controls, and the optional twist /
        auto-bend / stretch systems.

        Parameters:
            joints: The joint chain (root first).
            centerline: Path for the IK curve; defaults to the joint positions.
            size: Control base size — pass the tube radius for proportional
                controls (``resolve_sizes``).
            num_controls: Driver control count (3 = start/mid/end + tangents).
            enable_*: Feature toggles, matching the one-click build options.

        Returns:
            Tuple of (controls, ik_handle, curve).
        """
        joints = [str(j) for j in joints]
        if len(joints) < 2:
            raise ValueError("Spline IK needs a chain of at least 2 joints.")
        if centerline is None:
            centerline = [_xform_t_ws(j) for j in joints]

        curve = self.create_logic_curve(centerline)
        ik_handle = self.create_ik(
            joints, solver="ikSplineSolver", curve=curve, createCurve=False
        )
        cmds.setAttr(f"{ik_handle}.visibility", False)

        controls, driver_joints, up_locs = self.create_spline_drivers(
            centerline, size, num_controls
        )
        self.skin_curve_to_drivers(curve, driver_joints)

        start_ctrl = controls[0]
        end_ctrl = controls[-1]
        mid_idx = int(len(controls) / 2)
        mid_ctrl = controls[mid_idx] if len(controls) > 2 else None
        start_up_loc, end_up_loc = up_locs

        if enable_twist:
            self.setup_spline_twist(
                ik_handle, start_ctrl, end_ctrl, start_up_loc, end_up_loc
            )

        if enable_auto_bend:
            if num_controls == 3 and mid_ctrl:
                self.setup_auto_bend(start_ctrl, mid_ctrl, end_ctrl)
            else:
                self.logger.warning(
                    "Auto Bend is only available with 3 controls. Skipping."
                )

        # Hierarchy inserts above the intermediate controls (follow groups,
        # auto-bend) change their absolute DAG paths — resolve the canonical
        # paths once everything is in place, before anything records them.
        controls = [self._rig_scoped_path(c) for c in controls]
        start_ctrl = controls[0]

        if enable_stretch or enable_squash:
            self.setup_spline_stretch(
                curve,
                joints,
                enable_stretch,
                enable_squash,
                enable_volume,
                main_control=start_ctrl,
            )

        self.ik_handle = ik_handle
        return controls, ik_handle, curve

    @CoreUtils.undoable
    def create_fk_controls(self, joints: List[str], size: float = 1.0) -> List[str]:
        """Build a nested FK control hierarchy — one diamond per joint, each
        joint parent-constrained to its control.

        Parameters:
            joints: The joint chain (root first).
            size: Control base size — pass the tube radius for proportional
                controls (``resolve_sizes``).
        """
        joints = [str(j) for j in joints]
        if not joints:
            raise ValueError("FK controls need at least 1 joint.")

        controls: List[str] = []
        parent_ctrl = str(self.rig_group)

        for i, jnt in enumerate(joints):
            nodes = Controls.create(
                "diamond",
                name=f"{self.rig_name}_{i+1}_CTRL",
                size=size * 3,
                axis="x",
                color=(1, 1, 0),
                return_nodes=True,
            )
            grp = nodes.group if nodes.group else nodes.control

            # Match joint transform via parentConstraint (clean matrix transfer)
            temp_const = cmds.parentConstraint(str(jnt), str(grp))
            cmds.delete(temp_const)

            grp = _parent_to(grp, parent_ctrl)
            ctrl = _control_path(nodes, grp)

            # Standard FK: joint follows control
            cmds.parentConstraint(ctrl, str(jnt), mo=True)

            controls.append(ctrl)
            parent_ctrl = ctrl

        return controls

    @CoreUtils.undoable
    def create_anchor_controls(
        self, joints: List[str], size: float = 1.0, enable_stretch: bool = True
    ) -> List[str]:
        """Build the anchor/piston controls over the two end joints
        (``create_anchor_joints``): a box control per end constraining its
        joint, plus the optional distance-driven stretch network.

        The joints must be independent transforms — if the end joint is
        parented under the start joint, it is moved out first (a chained
        child would inherit the stretch scale and defeat the pinned-end
        behavior).

        Parameters:
            joints: Exactly two end joints, start first.
            size: Control base size — pass the tube radius for proportional
                controls (``resolve_sizes``).
            enable_stretch: Wire the distance-driven ``scaleX`` stretch.
        """
        joints = [str(j) for j in joints]
        if len(joints) != 2:
            raise ValueError(f"Anchor rigs use exactly 2 joints (got {len(joints)}).")
        j1, j2 = joints

        j1_long, j2_long = _long_path(j1), _long_path(j2)
        if j1_long and j2_long and j2_long.startswith(f"{j1_long}|"):
            self.logger.info(
                "Anchor end joint was chained under the start joint — "
                "unparenting so stretch scale can't propagate to it."
            )
            j2 = _parent_to(
                j2, NodeUtils.get_parent(j1, type=None, full_path=True)
            )
            self.joints = [j1, j2]

        start_pos = om.MVector(*_xform_t_ws(j1))
        end_pos = om.MVector(*_xform_t_ws(j2))
        # Joint X axes carry the tube-end tangents (baked by
        # create_anchor_joints); the end control's frame is mirrored so its
        # X points back into the tube.
        dir_start = _world_x_axis(j1)
        dir_end = _world_x_axis(j2)
        start_rot = _frame_rotation(dir_start)
        end_rot = _frame_rotation(-dir_end)

        rig_grp = str(self.rig_group)

        # NB: ``Controls.create`` exposes ``size=`` for the uniform scale;
        # ``scale=`` is silently absorbed by the preset builder's ``**_``
        # kwargs and does nothing.
        def _make_anchor_ctrl(suffix, pos, rot):
            nodes = Controls.box(
                name=f"{self.rig_name}_{suffix}",
                size=size * 4,
                color=(0, 1, 1),
                return_nodes=True,
            )
            target = str(nodes.group) if nodes.group else str(nodes.control)
            cmds.xform(
                target, ws=True, t=(pos.x, pos.y, pos.z), ro=_euler_deg(rot)
            )
            target = _parent_to(target, rig_grp)
            return _control_path(nodes, target)

        start_ctrl = _make_anchor_ctrl("start", start_pos, start_rot)
        end_ctrl = _make_anchor_ctrl("end", end_pos, end_rot)

        # Joints follow control position and rotation (rotatable tube ends)
        cmds.pointConstraint(start_ctrl, j1, mo=True)
        cmds.pointConstraint(end_ctrl, j2, mo=True)
        cmds.orientConstraint(start_ctrl, j1, mo=True)
        cmds.orientConstraint(end_ctrl, j2, mo=True)

        if enable_stretch:
            # Measure the control distance in the rig's local space via
            # multMatrix so scaling the rig group can't double-transform.
            start_local_mm = cmds.createNode(
                "multMatrix", name=f"{self.rig_name}_start_local_MM"
            )
            cmds.connectAttr(
                f"{str(start_ctrl)}.worldMatrix[0]",
                f"{start_local_mm}.matrixIn[0]",
                force=True,
            )
            cmds.connectAttr(
                f"{rig_grp}.worldInverseMatrix[0]",
                f"{start_local_mm}.matrixIn[1]",
                force=True,
            )

            end_local_mm = cmds.createNode(
                "multMatrix", name=f"{self.rig_name}_end_local_MM"
            )
            cmds.connectAttr(
                f"{str(end_ctrl)}.worldMatrix[0]",
                f"{end_local_mm}.matrixIn[0]",
                force=True,
            )
            cmds.connectAttr(
                f"{rig_grp}.worldInverseMatrix[0]",
                f"{end_local_mm}.matrixIn[1]",
                force=True,
            )

            dist_node = cmds.createNode(
                "distanceBetween", name=f"{self.rig_name}_dist"
            )
            cmds.connectAttr(
                f"{start_local_mm}.matrixSum", f"{dist_node}.inMatrix1", force=True
            )
            cmds.connectAttr(
                f"{end_local_mm}.matrixSum", f"{dist_node}.inMatrix2", force=True
            )

            initial_dist = (end_pos - start_pos).length()

            norm_md = cmds.createNode(
                "multiplyDivide", name=f"{self.rig_name}_scale_MD"
            )
            cmds.setAttr(f"{norm_md}.operation", 2)  # Divide
            cmds.connectAttr(f"{dist_node}.distance", f"{norm_md}.input1X", force=True)
            cmds.setAttr(f"{norm_md}.input2X", initial_dist)

            # Start joint scales to stretch toward end
            cmds.connectAttr(f"{norm_md}.outputX", f"{j1}.scaleX", force=True)

        return [start_ctrl, end_ctrl]

    @CoreUtils.undoable
    def setup_spline_twist(
        self, ik_handle, start_ctrl, end_ctrl, start_up_loc=None, end_up_loc=None
    ):
        """Setup advanced twist for IK Spline.

        Args:
            ik_handle: The IK Spline handle.
            start_ctrl: Start control transform.
            end_ctrl: End control transform.
            start_up_loc: Optional up locator for start (child of start_ctrl). If None, uses control.
            end_up_loc: Optional up locator for end (child of end_ctrl). If None, uses control.
        """
        ik_handle = str(ik_handle)
        start_ctrl = str(start_ctrl)
        end_ctrl = str(end_ctrl)

        cmds.setAttr(f"{ik_handle}.dTwistControlEnable", True)

        if start_up_loc and end_up_loc:
            # Object Rotation Up (Start/End) — more stable than control matrices
            # when controls translate.
            cmds.setAttr(f"{ik_handle}.dWorldUpType", 4)
            cmds.setAttr(f"{ik_handle}.dWorldUpAxis", 0)  # Positive Y
            cmds.setAttr(f"{ik_handle}.dWorldUpVectorY", 1)
            cmds.setAttr(f"{ik_handle}.dWorldUpVectorEndY", 1)
            cmds.connectAttr(
                f"{str(start_up_loc)}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrix",
                force=True,
            )
            cmds.connectAttr(
                f"{str(end_up_loc)}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrixEnd",
                force=True,
            )
        else:
            cmds.setAttr(f"{ik_handle}.dWorldUpType", 4)
            cmds.connectAttr(
                f"{start_ctrl}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrix",
                force=True,
            )
            cmds.connectAttr(
                f"{end_ctrl}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrixEnd",
                force=True,
            )

        if not cmds.attributeQuery("roll", node=end_ctrl, exists=True):
            cmds.addAttr(end_ctrl, ln="roll", at="double", k=True)
        cmds.connectAttr(f"{end_ctrl}.roll", f"{ik_handle}.roll", force=True)

    @CoreUtils.undoable
    def setup_auto_bend(self, start_ctrl, mid_ctrl, end_ctrl):
        """Setup automatic bending of the mid control based on compression distance."""
        start_ctrl = str(start_ctrl)
        mid_ctrl = str(mid_ctrl)
        end_ctrl = str(end_ctrl)
        rig_grp = str(self.rig_group)

        # dv 0.5: a hose compressed by d bows out by d/2 — visible, natural
        # slack out of the box (dv=0 made an "enabled" auto-bend a silent
        # no-op until the animator discovered the attribute).
        if not cmds.attributeQuery("autoBend", node=start_ctrl, exists=True):
            cmds.addAttr(
                start_ctrl, ln="autoBend", at="double", min=0, max=5, dv=0.5, k=True
            )

        # Identify the mid control's offset group (parented to rig_group).
        offset_grp = NodeUtils.get_parent(mid_ctrl, type=None, full_path=True)
        if not offset_grp or short_name(offset_grp) == short_name(rig_grp):
            offset_grp = mid_ctrl

        auto_bend_grp = cmds.group(empty=True, name=f"{self.rig_name}_mid_autoBend_GRP")

        # Match transform of the offset group (which is at mid position)
        cmds.delete(cmds.parentConstraint(offset_grp, auto_bend_grp))

        # Insert into hierarchy: RigGroup -> AutoBend -> Offset -> Control
        current_parent = NodeUtils.get_parent(offset_grp, type=None, full_path=True)
        if current_parent:
            auto_bend_grp = _parent_to(auto_bend_grp, current_parent)
        offset_grp = _parent_to(offset_grp, auto_bend_grp)

        # Logic: (Initial_Length - Current_Dist) * autoBend -> translateY
        dist_node = cmds.createNode("distanceBetween", name=f"{self.rig_name}_ab_dist")
        cmds.connectAttr(f"{start_ctrl}.worldMatrix[0]", f"{dist_node}.inMatrix1", force=True)
        cmds.connectAttr(f"{end_ctrl}.worldMatrix[0]", f"{dist_node}.inMatrix2", force=True)

        start_pos = om.MVector(*_xform_t_ws(start_ctrl))
        end_pos = om.MVector(*_xform_t_ws(end_ctrl))
        initial_length = (end_pos - start_pos).length()

        # Calculate compression: initial_length - current_dist
        pma = cmds.createNode("plusMinusAverage", name=f"{self.rig_name}_ab_sub")
        cmds.setAttr(f"{pma}.operation", 2)  # Subtract
        cmds.setAttr(f"{pma}.input1D[0]", initial_length)
        cmds.connectAttr(f"{dist_node}.distance", f"{pma}.input1D[1]", force=True)

        # Clamp min 0 (ignore stretching, only bend on compression)
        clamp = cmds.createNode("clamp", name=f"{self.rig_name}_ab_clamp")
        cmds.setAttr(f"{clamp}.minR", 0)
        cmds.setAttr(f"{clamp}.maxR", 10000)
        cmds.connectAttr(f"{pma}.output1D", f"{clamp}.inputR", force=True)

        # Multiply by autoBend factor
        md = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_ab_mult")
        cmds.connectAttr(f"{clamp}.outputR", f"{md}.input1X", force=True)
        cmds.connectAttr(f"{start_ctrl}.autoBend", f"{md}.input2X", force=True)

        # Apply to Y translation of the auto_bend_grp (Y-up is standard for this rig).
        cmds.connectAttr(f"{md}.outputX", f"{auto_bend_grp}.translateY", force=True)

    @CoreUtils.undoable
    def setup_spline_stretch(
        self,
        curve,
        joints,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        main_control=None,
    ):
        curve = str(curve)
        rig_grp = str(self.rig_group)
        main_control = str(main_control) if main_control else None

        curve_shape = NodeUtils.get_shape(curve)
        if not curve_shape:
            self.logger.warning(f"setup_spline_stretch: no shape under {curve}")
            return

        curve_info = cmds.createNode("curveInfo", name=f"{self.rig_name}_curveInfo")
        cmds.connectAttr(
            f"{curve_shape}.worldSpace[0]", f"{curve_info}.inputCurve", force=True
        )
        initial_length = cmds.getAttr(f"{curve_info}.arcLength")

        scale_comp_md = cmds.createNode(
            "multiplyDivide", name=f"{self.rig_name}_scale_comp_MD"
        )
        cmds.setAttr(f"{scale_comp_md}.operation", 2)  # Divide
        cmds.connectAttr(
            f"{curve_info}.arcLength", f"{scale_comp_md}.input1X", force=True
        )
        cmds.connectAttr(
            f"{rig_grp}.scaleX", f"{scale_comp_md}.input2X", force=True
        )

        norm_md = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_norm_MD")
        cmds.setAttr(f"{norm_md}.operation", 2)
        cmds.connectAttr(
            f"{scale_comp_md}.outputX", f"{norm_md}.input1X", force=True
        )
        cmds.setAttr(f"{norm_md}.input2X", initial_length)

        # Clamp logic for separate stretch/squash control
        min_limit = 0.001 if enable_squash else 1.0
        max_limit = 10000.0 if enable_stretch else 1.0

        scale_val_src = f"{norm_md}.outputX"
        if not enable_squash or not enable_stretch:
            clamp_node = cmds.createNode("clamp", name=f"{self.rig_name}_scale_clamp")
            cmds.setAttr(f"{clamp_node}.minR", min_limit)
            cmds.setAttr(f"{clamp_node}.maxR", max_limit)
            cmds.connectAttr(f"{norm_md}.outputX", f"{clamp_node}.inputR", force=True)
            scale_val_src = f"{clamp_node}.outputR"

        # ----------------------------------------------------------------------
        # Attribute Setup (User Controls)
        # ----------------------------------------------------------------------
        stretch_output = scale_val_src

        if main_control:
            # Add Separator if not present (shared by vol and stretch)
            if not cmds.attributeQuery("separator_opt", node=main_control, exists=True):
                cmds.addAttr(
                    main_control, ln="separator_opt", at="enum", en="____", k=True
                )
                cmds.setAttr(f"{main_control}.separator_opt", lock=True)

            # 1. Stretch blending (Animator toggles stretch effect)
            if enable_stretch or enable_squash:
                if not cmds.attributeQuery("stretchFactor", node=main_control, exists=True):
                    cmds.addAttr(
                        main_control,
                        ln="stretchFactor",
                        at="double",
                        min=0,
                        max=1,
                        dv=1.0,
                        k=True,
                    )

                # Blend between Calculated Stretch (Color1) and 1.0 (Color2)
                blend_stretch = cmds.createNode(
                    "blendColors", name=f"{self.rig_name}_stretch_BLEND"
                )
                cmds.connectAttr(
                    f"{main_control}.stretchFactor",
                    f"{blend_stretch}.blender",
                    force=True,
                )
                cmds.connectAttr(
                    scale_val_src, f"{blend_stretch}.color1R", force=True
                )
                cmds.setAttr(f"{blend_stretch}.color2R", 1.0)

                stretch_output = f"{blend_stretch}.outputR"

        # ----------------------------------------------------------------------
        # Volume Preservation: scaleY = scaleZ = scaleX ^ -0.5
        # ----------------------------------------------------------------------
        vol_output = None

        if enable_volume:
            vol_pow = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_vol_POW")
            cmds.setAttr(f"{vol_pow}.operation", 3)  # Power
            cmds.connectAttr(stretch_output, f"{vol_pow}.input1X", force=True)
            cmds.setAttr(f"{vol_pow}.input2X", -0.5)

            vol_output = f"{vol_pow}.outputX"

            if main_control:
                if not cmds.attributeQuery("volumeFactor", node=main_control, exists=True):
                    cmds.addAttr(
                        main_control,
                        ln="volumeFactor",
                        at="double",
                        min=0,
                        max=2,
                        dv=1.0,
                        k=True,
                    )

                blend_vol = cmds.createNode(
                    "blendColors", name=f"{self.rig_name}_vol_BLEND"
                )
                cmds.connectAttr(
                    f"{main_control}.volumeFactor",
                    f"{blend_vol}.blender",
                    force=True,
                )
                cmds.connectAttr(
                    f"{vol_pow}.outputX", f"{blend_vol}.color1R", force=True
                )
                cmds.setAttr(f"{blend_vol}.color2R", 1.0)

                vol_output = f"{blend_vol}.outputR"

        for jnt in joints:
            jnt = str(jnt)
            cmds.connectAttr(stretch_output, f"{jnt}.scaleX", force=True)
            if enable_volume and vol_output:
                cmds.connectAttr(vol_output, f"{jnt}.scaleY", force=True)
                cmds.connectAttr(vol_output, f"{jnt}.scaleZ", force=True)

    # ------------------------------------------------------------------
    # RP IK / Legacy Controls (Now delegated to RigUtils)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_ik(
        self, joints: List[str], **kwargs
    ) -> Optional[str]:
        # Wrapper for RigUtils.create_ik_handle to maintain API compatibility
        joints = cmds.ls(joints, type="joint", flatten=True)
        if len(joints) < 2:
            self.logger.error("Insufficient joints to create IK handle.")
            return None

        name = kwargs.pop("name", f"{self.rig_name}_ikHandle")
        return RigUtils.create_ik_handle(
            start_joint=joints[0],
            end_joint=joints[-1],
            name=name,
            parent=self.rig_group,
            **kwargs,
        )

    @CoreUtils.undoable
    def create_pole_vector(
        self, ik_handle, mid_joint: str, offset=(0, 5, 0)
    ) -> str:
        # Wrapper for RigUtils.create_pole_vector
        # Note: RigUtils uses 'distance' float while old method used vector offset tuple?
        # Old signature: offset=(0,5,0).
        # RigUtils expects distance.
        # We'll adapt.
        dist = om.MVector(offset).length()
        pv = RigUtils.create_pole_vector(
            ik_handle=ik_handle,
            mid_joint=mid_joint,
            distance=dist,
            name=f"{self.rig_name}_poleVector_LOC",
            parent=self.rig_group,
        )
        self.pole_vector = pv
        return pv

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Skinning
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def bind_joint_chain(
        self,
        obj,
        joints: List[str],
        curve: Optional[str] = None,
        centerline: Optional[List] = None,
    ) -> Optional[str]:
        """Bind the joint chain to a polygon tube with smooth skinning.

        Weights are parametric along the tube — identical to the one-click
        build's bind step. Without an explicit *curve*/*centerline*, the
        centerline is re-extracted from the mesh (falling back to the joint
        positions); if the parametric solve fails, ``skin_mesh`` falls back
        to a geodesic bind.
        """
        self.logger.debug(f"Tube mesh: {obj} ({type(obj).__name__})")
        objs = list(dict.fromkeys(cmds.ls(obj, objectsOnly=True, flatten=True) or []))
        if not objs:
            self.logger.error(f"Invalid tube mesh: {obj}")
            return None
        first = objs[0]

        transform = NodeUtils.get_transform_node(first)
        if not transform:
            self.logger.error(f"Invalid transform node: {transform}")
            return None
        transform = str(transform)

        if not joints or not isinstance(joints, (list, tuple)):
            self.logger.error(f"Invalid joint list: {joints}")
            return None
        joints = [str(j) for j in joints]
        if not all(cmds.objExists(j) and cmds.objectType(j) == "joint" for j in joints):
            self.logger.error(f"Invalid joint list: {joints}")
            return None

        # Unlock TRS so the skinCluster bind doesn't fail on locked plugs.
        for attr in ("translate", "rotate", "scale"):
            for axis in "XYZ":
                plug = f"{transform}.{attr}{axis}"
                try:
                    cmds.setAttr(plug, lock=False)
                except Exception:
                    pass

        rig_group = str(self.rig_group)
        tube_parent = NodeUtils.get_parent(transform, type=None, full_path=True)
        if tube_parent:
            rig_group = _parent_to(rig_group, tube_parent)
            transform = _parent_to(transform, None)  # unparent to world

        for j in joints:
            if not NodeUtils.get_parent(j, type=None, full_path=True):
                _parent_to(j, rig_group)

        self.logger.debug(
            f"Creating skinCluster with {len(joints)} joints on {leaf_name(transform)}"
        )

        # Parity with the one-click build: parametric ring-uniform weights
        # along the tube centerline (joint positions when extraction fails).
        if not curve and not centerline:
            try:
                centerline, _ = TubePath.get_centerline(transform, num_joints=-1)
            except Exception:
                centerline = None
            if not centerline or len(centerline) < 2:
                centerline = [_xform_t_ws(j) for j in joints]

        skin_cluster = self.skin_mesh(
            joints, curve=curve, centerline=centerline, mesh=transform
        )
        if skin_cluster:
            self.logger.debug(f"SkinCluster created: {skin_cluster}")
        return skin_cluster

    # ------------------------------------------------------------------
    # Anchor Constraints
    # ------------------------------------------------------------------

    def _rig_scoped_path(self, node) -> str:
        """Canonical long path of this rig's node named like *node* (matched
        by leaf name, scoped to the rig group).

        Hierarchy inserts (follow groups, auto-bend) invalidate absolute
        paths captured earlier; leaf names stay unique *within* the rig, so
        a rig-group-scoped leaf lookup recovers the true path even when the
        same leaf exists in other rigs.
        """
        leaf = leaf_name(node)
        matches = cmds.ls(leaf, long=True) or []
        grp = (
            _long_path(str(self._rig_group))
            if self._rig_group and cmds.objExists(str(self._rig_group))
            else None
        )
        if grp:
            scoped = [m for m in matches if m.startswith(f"{grp}|")]
            matches = scoped or matches
        return matches[0] if matches else str(node)

    def _end_control(self, index: int) -> Optional[str]:
        """Long path of the rig's start (``0``) or end (``-1``) control, or
        None when the rig has no controls (bare chain).

        Prefers the live build's records; falls back to the naming
        conventions of the three control builders so post-restart rigs
        (empty registry) still resolve. Ambiguous short-name matches are
        narrowed to this rig's group.
        """
        recorded = self.start_loc if index == 0 else self.end_loc
        from_bundle = (
            self.bundle.controls[0 if index == 0 else -1]
            if self.bundle and self.bundle.controls
            else None
        )
        for c in (recorded, from_bundle):
            if c and cmds.objExists(str(c)):
                return _long_path(str(c))

        grp = (
            _long_path(self._rig_group)
            if self._rig_group and cmds.objExists(str(self._rig_group))
            else None
        )

        def _narrow(matches):
            if not matches:
                return None
            if len(matches) > 1 and grp:
                scoped = [m for m in matches if m.startswith(f"{grp}|")]
                matches = scoped or matches
            return matches[0]

        suffix = "start" if index == 0 else "end"
        named = _narrow(
            cmds.ls(f"{self.rig_name}_{suffix}_CTRL", type="transform", long=True)
            or []
        )
        if named:
            return named

        # Numbered controls (FK chains, N-point spline drivers).
        numbered = []
        for m in cmds.ls(f"{self.rig_name}_*_CTRL", type="transform", long=True) or []:
            match = re.fullmatch(
                rf"{re.escape(self.rig_name)}_(?:ctrl_)?(\d+)_CTRL", leaf_name(m)
            )
            if match:
                numbered.append((int(match.group(1)), m))
        if numbered:
            numbered.sort(key=lambda t: t[0])
            wanted = numbered[0][0] if index == 0 else numbered[-1][0]
            return _narrow([m for n, m in numbered if n == wanted])
        return None

    @CoreUtils.undoable
    def constrain_end_with_falloff(
        self,
        joints: "List[str]",
        anchor: str,
        falloff: float = 5.0,
        joint_index: int = -1,
    ) -> "Optional[str]":
        """
        Constrains a joint in the chain to an anchor and applies distance-based skin weight falloff.

        Parameters:
            joints (List[str]): The hose joint chain.
            anchor (str): The transform the joint should follow.
            falloff (float): World-space distance over which anchor weight fades.
            joint_index (int): Index of the joint to constrain. Use 0 for start, -1 for end.

        Returns:
            str: The newly created anchor joint.
        """
        if not joints:
            self.logger.error("No joints provided.")
            return None

        constrained_joint = str(joints[joint_index])
        anchor = str(anchor)
        anchor_pos = _xform_t_ws(anchor)

        # Create anchor joint at anchor location
        joint_name = Naming.generate_unique_name(f"{self.rig_name}_anchor_jnt")
        cmds.select(clear=True)
        anchor_joint = cmds.createNode("joint", name=joint_name)
        cmds.setAttr(
            f"{anchor_joint}.translate",
            anchor_pos[0], anchor_pos[1], anchor_pos[2],
            type="double3",
        )
        cmds.setAttr(
            f"{anchor_joint}.radius",
            cmds.getAttr(f"{constrained_joint}.radius"),
        )
        cmds.makeIdentity(anchor_joint, apply=True, t=True, r=True, s=True)
        cmds.xform(anchor_joint, ws=True, t=anchor_pos)

        # Fully constrain anchor_joint to the anchor geo (position + orientation)
        cmds.parentConstraint(anchor, anchor_joint, mo=False)

        # Route the constraint through the rig's end CONTROL when one exists.
        # The chain joints are the wrong target on every built rig
        # (probe-verified): spline joints are IK-driven, so a direct
        # parentConstraint is silently overridden (and translating a spline
        # ikHandle is ignored outright by the solver); anchor joints already
        # carry control constraints, so a second driver raises 'Object is
        # already connected'; FK joints blend 50/50 against their control's
        # constraint. Driving the control moves the whole end assembly
        # (curve drivers, twist locators) coherently. Bare chains with no
        # controls keep the direct joint constraint.
        idx = joint_index % len(joints)
        target_ctrl = None
        if idx == 0:
            target_ctrl = self._end_control(0)
        elif idx == len(joints) - 1:
            target_ctrl = self._end_control(-1)
        if target_ctrl:
            # mo=True: follow the anchor's motion without snapping to it —
            # the falloff weighting below handles the contact region.
            cmds.parentConstraint(anchor_joint, target_ctrl, mo=True)
        else:
            cmds.parentConstraint(anchor_joint, constrained_joint, mo=False)

        # Add falloff skin weighting from anchor_joint to constrained_joint.
        # Resolve the skinCluster from the scene when this instance didn't
        # bind the mesh itself (e.g. joints selected in a fresh session).
        skin_cluster = str(self.skin_cluster) if self.skin_cluster else None
        if not skin_cluster:
            connected = (
                cmds.listConnections(
                    f"{constrained_joint}.worldMatrix[0]", type="skinCluster"
                )
                or []
            )
            skin_cluster = connected[0] if connected else None

        if not skin_cluster:
            self.logger.warning(
                "constrain_end_with_falloff: no skinCluster found for "
                f"{constrained_joint}; skipping falloff weighting."
            )
        else:
            try:
                # No source_influence: the anchor takes w = 1 - d/falloff and
                # the REMAINDER redistributes across the vertex's existing
                # influences (skinPercent semantics). Blending against the
                # end joint alone would zero the solver's neighbor-joint
                # weights inside the radius and snap back to them at the
                # boundary — a visible crease in the constrained end's blend
                # zone. Redistribution is continuous at the boundary (w -> 0
                # leaves the row untouched) and keeps the smooth basis intact.
                SkinUtils.apply_falloff(
                    skin_cluster,
                    target_influence=anchor_joint,
                    center=anchor_pos,
                    radius=falloff,
                    profile="linear",
                    add_influence=True,
                    undoable=True,
                )
                self.logger.debug(
                    f"Applied falloff weights from {anchor_joint} "
                    f"(joint index {joint_index}) over distance {falloff}"
                )
            except Exception as e:
                self.logger.warning(f"Skin weighting failed: {e}")

        # Ensure anchor joint is parented under the rig group
        rig_grp = str(self.rig_group)
        current_parent = NodeUtils.get_parent(anchor_joint, type=None, full_path=True)
        if not current_parent or short_name(current_parent) != short_name(rig_grp):
            anchor_joint = _parent_to(anchor_joint, rig_grp)

        return anchor_joint


# ======================================================================
# Rig Configuration (UI Logic)
# ======================================================================


@dataclass
class RigModeConfig:
    """Defines a rig mode's strategy and available options."""

    name: str
    strategy: str

    num_joints: int
    num_controls: int
    enable_stretch: bool
    enable_squash: bool
    enable_volume: bool
    enable_auto_bend: bool
    enable_twist: bool

    # UI State (Default: Editable)
    num_joints_editable: bool = True
    num_controls_editable: bool = True
    stretch_editable: bool = True
    squash_editable: bool = True
    volume_editable: bool = True
    auto_bend_editable: bool = True
    twist_editable: bool = True


# Rig Mode Registry
RIG_MODES: List[RigModeConfig] = [
    RigModeConfig(
        name="Spline (Hose/Cable)",
        strategy="spline",
        num_joints=-1,
        num_controls=3,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        enable_auto_bend=True,  # hoses bow on compression, not accordion
        enable_twist=True,
    ),
    RigModeConfig(
        name="Anchor (Piston/Hydraulic)",
        strategy="anchor",
        num_joints=2,
        num_controls=2,
        enable_stretch=True,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        num_joints_editable=False,
        num_controls_editable=False,
        twist_editable=False,
        volume_editable=False,  # AnchorStrategy implements no volume system
        squash_editable=False,  # ...nor squash
        auto_bend_editable=False,  # ...nor a mid control to bend
    ),
    RigModeConfig(
        name="FK Chain (Tail/Tentacle)",
        strategy="fk",
        num_joints=-1,
        num_controls=-1,
        enable_stretch=False,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        stretch_editable=False,
        squash_editable=False,
        volume_editable=False,
        auto_bend_editable=False,
        twist_editable=False,
        num_controls_editable=False,
    ),
]


# ======================================================================
# UI Slots (thin event handlers — delegates to TubeRig / TubePath)
# ======================================================================


class TubeRigSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        # Bind to the UI that corresponds to this slots class (tube_rig.ui)
        self.ui = self.sb.loaded_ui.tube_rig

        # Configure SpinBox custom display
        # -1 indicates "Auto": joint count derived from edge loops; joint size
        # derived from the measured tube radius.
        self.ui.s000.setCustomDisplayValues(-1, "Auto")
        self.ui.s002.setCustomDisplayValues(-1, "Auto")

        # Populate the mode combobox. The mode names are self-describing under the
        # "Global Options" group, so the combo carries no extra label: the old
        # setTextOverlay("Mode:") floated a translucent QLabel ON TOP of the item
        # text, so the two overlapped into an unreadable smear (reported bug) —
        # removed. (A display-only prefix can't stand in: QStyleSheetStyle paints a
        # themed combo's label from its own currentText, ignoring such adornments.)
        self.ui.cmb_preset.clear()
        for mode in RIG_MODES:
            self.ui.cmb_preset.addItem(mode.name, mode)

        self.ui.cmb_preset.currentIndexChanged.connect(self.apply_mode)
        # Apply initial mode
        if len(RIG_MODES) > 0:
            self.apply_mode(0)

        # Auto-Bend needs the 3-control spline layout — keep the checkbox
        # gated as the control count changes (apply_mode syncs it per mode).
        self.ui.s001.valueChanged.connect(self._sync_auto_bend_gate)

        self._init_tooltips()

        # Keep the window tall enough for the selected step. QToolBox wraps each
        # page in a QScrollArea whose minimum under-reports its content height, so
        # the window's show-time fit (which targets minimumSizeHint) leaves a taller
        # step (e.g. Step 2) clipped behind a scrollbar. Re-fit height on page change
        # to sizeHint, which DOES reflect the current page.
        self.ui.toolbox_steps.currentChanged.connect(self._fit_window_to_step)

    def _fit_window_to_step(self, *_) -> None:
        """Fit the window's height to the newly-selected toolbox page.

        Wired to ``toolbox_steps.currentChanged``. QToolBox scroll areas
        under-report their minimum height, so switching to a taller step would
        otherwise clip the page behind a scrollbar. Resize the height to
        ``sizeHint`` — which reflects the current page, unlike the
        ``minimumSizeHint`` the show-time ``fit_height_to_content`` targets —
        while preserving width, matching the window's own height-only resize
        helpers so a user-widened panel keeps its width (plain ``adjustSize``
        would snap it back). Deferred one event-loop tick so the page-switch
        layout has settled before the window re-measures.
        """
        win = self.ui.window()
        self.sb.QtCore.QTimer.singleShot(
            0, lambda: win.resize(win.width(), win.sizeHint().height())
        )

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Tube Rig",
                body="Generate joint rigs along tube-shaped meshes. The tool "
                "auto-detects the tube's centerline via edge loops or surface "
                "normals, and sizes controls to the measured tube radius.",
                sections=[
                    (
                        "Quick start (One-Click)",
                        [
                            "Select a tube mesh.",
                            "Pick a <b>Mode</b> preset — irrelevant options "
                            "disable per mode.",
                            "Press <b>Full Rig</b> — it runs Steps 1 → 2 → 3 "
                            "with the parameter values set in each step page.",
                        ],
                    ),
                    (
                        "Step-by-step",
                        [
                            "<b>Step 1</b> creates the joints, <b>Step 2</b> "
                            "the controls, <b>Step 3</b> the skin bind — the "
                            "same operations One-Click runs, one at a time.",
                            "Each step's tooltip states exactly what to "
                            "select, and in what order.",
                        ],
                    ),
                ],
                notes=[
                    "<b>Joints = Auto</b> reads the tube's edge loops and "
                    "places one joint per loop.",
                    "Re-running a step replaces that step's previous result.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and step."""
        ui = self.ui

        ui.cmb_preset.setToolTip(
            fmt(
                title="Rig Mode",
                body="Selects the build strategy and presets the step "
                "parameters below. Options a mode doesn't support are "
                "disabled while it is active.",
                sections=[
                    (
                        "Spline (Hose/Cable)",
                        [
                            "IK-spline chain with start / mid / end controls.",
                            "Good for: hoses, cables, organic tubes.",
                            "Supports: stretch, squash, volume, twist, auto-bend.",
                        ],
                    ),
                    (
                        "Anchor (Piston)",
                        [
                            "Two independent end joints with distance-driven stretch.",
                            "Good for: pistons, hydraulics, struts.",
                            "Always exactly 2 joints; stretch only.",
                        ],
                    ),
                    (
                        "FK Chain (Tail/Tentacle)",
                        [
                            "Nested FK controls, one per joint.",
                            "Good for: tails, tentacles, hand-keyed tubes.",
                        ],
                    ),
                ],
            )
        )
        ui.txt000.setToolTip(
            fmt(
                title="Rig Name",
                body="Base name for every node the rig creates (group, "
                "joints, controls, skinCluster).",
                notes=["Empty = derived from the mesh name."],
            )
        )
        ui.s000.setToolTip(
            fmt(
                title="Number of Joints",
                body="Joint count along the tube centerline.",
                rows=[
                    ("Auto", "one joint per edge loop (most precise)"),
                    ("N", "evenly resampled along the centerline"),
                ],
                notes=["Anchor rigs always use exactly 2 end joints."],
            )
        )
        ui.s001.setToolTip(
            fmt(
                title="Number of Controls",
                body="Driver control count for the Spline rig. Increase for "
                "complex shapes.",
                notes=[
                    "<b>Auto-Bend</b> requires exactly 3 controls.",
                    "FK rigs create one control per joint instead.",
                ],
            )
        )
        ui.s002.setToolTip(
            fmt(
                title="Joint Size",
                body="Joint display radius in the viewport.",
                rows=[("Auto", "half the measured tube radius")],
                notes=[
                    "Display only — control sizes always scale to the "
                    "measured tube radius."
                ],
            )
        )
        ui.chk000.setToolTip(
            fmt(
                title="Reverse Direction",
                body="Builds the joint chain from the far end (swaps "
                "start/end).",
                notes=["Applies to Step 1 and the One-Click build."],
            )
        )
        ui.chk_stretch.setToolTip(
            fmt(
                title="Stretch",
                bullets=[
                    "<b>Spline:</b> joints scale along the tube to follow "
                    "the curve length.",
                    "<b>Anchor:</b> the start joint stretches toward the "
                    "end control.",
                ],
            )
        )
        ui.chk_twist.setToolTip(
            fmt(
                title="Twist",
                body="Advanced spline twist driven by the start/end control "
                "rotation.",
                notes=[
                    "Spline only. Adds a <b>roll</b> attribute to the end "
                    "control."
                ],
            )
        )
        ui.chk_squash.setToolTip(
            fmt(
                title="Squash",
                body="Joints compress when the curve shortens.",
                notes=["Spline only."],
            )
        )
        ui.chk_volume.setToolTip(
            fmt(
                title="Volume Preservation",
                body="Bulges when squashed, thins when stretched.",
                notes=["Spline only."],
            )
        )
        ui.chk_auto_bend.setToolTip(
            fmt(
                title="Auto-Bend (Mid)",
                body="The mid control bows outward automatically as the ends "
                "compress toward each other.",
                notes=["Spline only — requires exactly <b>3</b> controls."],
            )
        )
        ui.b001.setToolTip(
            fmt(
                title="Step 1 — Create Joints",
                body="Places this rig's joints along the tube's centerline.",
                steps=[
                    "Select the tube mesh <i>(or an edge loop running down "
                    "it)</i>.",
                    "Press <b>Create Joints</b>.",
                ],
                notes=[
                    "Anchor mode creates its 2 end joints instead of a chain.",
                    "Re-running replaces this rig's previous joints.",
                ],
            )
        )
        ui.b002.setToolTip(
            fmt(
                title="Step 2 — Create Controls",
                body="Builds the active mode's control rig on the joints "
                "from Step 1.",
                steps=[
                    "Select the root joint <i>(Anchor: either or both end "
                    "joints)</i>.",
                    "Press <b>Create IK / Controls</b>.",
                ],
                sections=[
                    (
                        "Creates",
                        [
                            "<b>Spline:</b> IK spline, start/mid/end controls, "
                            "twist, stretch, auto-bend.",
                            "<b>Anchor:</b> two end controls with distance "
                            "stretch.",
                            "<b>FK:</b> nested FK controls, one per joint.",
                        ],
                    ),
                ],
                notes=["Control size is proportional to the tube's radius."],
            )
        )
        ui.b003.setToolTip(
            fmt(
                title="Step 3 — Bind Skin",
                body="Smooth-binds the tube mesh to the joints with "
                "ring-uniform parametric weights — the same solver the "
                "One-Click build uses.",
                steps=[
                    "Select the root joint.",
                    "<b>Shift</b>-select the tube mesh <i>last</i>.",
                    "Press <b>Bind Joints to Mesh</b>.",
                ],
                notes=["Re-running replaces the mesh's existing skinCluster."],
            )
        )
        ui.b004.setToolTip(
            fmt(
                title="Constrain Ends to Anchors",
                body="Constrains both tube ends to external anchor objects "
                "(each end's control follows its anchor) with "
                "distance-falloff skin weighting at the contact points.",
                steps=[
                    "Select the root joint.",
                    "<b>Shift</b>-select the two anchor objects.",
                    "Press <b>Add End Constraints</b>.",
                ],
                notes=[
                    "Requires a bound tube — run <b>Step 3</b> first.",
                    "Anchors auto-assign to their nearest tube end — "
                    "selection order doesn't matter.",
                    "Falloff spans ≈2× the tube radius.",
                ],
            )
        )
        ui.b000.setToolTip(
            fmt(
                title="One-Click Rig",
                body="Runs <b>Step 1 → Step 2 → Step 3</b> in order, using "
                "the parameter values set in each step's page.",
                steps=[
                    "Select the tube mesh <i>(or an edge loop running down "
                    "it)</i>.",
                    "Press <b>Full Rig</b>.",
                ],
                notes=[
                    "Rebuilding on an already-rigged mesh tears the old rig "
                    "down first."
                ],
            )
        )

    def _sync_auto_bend_gate(self, *_):
        """Gate Auto-Bend on the 3-control spline layout."""
        mode = self.get_mode()
        allowed = bool(
            mode and mode.auto_bend_editable and int(self.ui.s001.value()) == 3
        )
        self.ui.chk_auto_bend.setEnabled(allowed)
        if not allowed:
            self.ui.chk_auto_bend.setChecked(False)

    def apply_mode(self, index: int):
        """Apply mode values and constraints to UI widgets."""
        mode = self.ui.cmb_preset.itemData(index)
        if not mode:
            # Fallback if somehow data is missing or index invalid (shouldn't happen with correct usage)
            mode = RIG_MODES[0] if RIG_MODES else None

        if not mode:
            return

        # Step 1: Joints
        self.ui.s000.setValue(mode.num_joints)
        self.ui.s000.setEnabled(mode.num_joints_editable)

        # Step 1.5: Controls Count
        self.ui.s001.setValue(mode.num_controls)
        self.ui.s001.setEnabled(mode.num_controls_editable)

        # Step 2: Controls
        self.ui.chk_stretch.setChecked(mode.enable_stretch)
        self.ui.chk_stretch.setEnabled(mode.stretch_editable)

        self.ui.chk_squash.setChecked(mode.enable_squash)
        self.ui.chk_squash.setEnabled(mode.squash_editable)

        self.ui.chk_volume.setChecked(mode.enable_volume)
        self.ui.chk_volume.setEnabled(mode.volume_editable)

        self.ui.chk_auto_bend.setChecked(mode.enable_auto_bend)
        self.ui.chk_auto_bend.setEnabled(mode.auto_bend_editable)

        self.ui.chk_twist.setChecked(mode.enable_twist)
        self.ui.chk_twist.setEnabled(mode.twist_editable)

        self._sync_auto_bend_gate()

    def get_mode(self) -> RigModeConfig:
        """Get the current rig mode config."""
        mode = self.ui.cmb_preset.currentData()
        return mode if mode else (RIG_MODES[0] if RIG_MODES else None)

    def get_strategy(self) -> str:
        """Get the current strategy from the mode combobox."""
        return self.get_mode().strategy

    def get_tube_rig(self, obj):
        """Get the tube rig instance for the given object (the mesh, a joint,
        a control, or anything under the rig group); create one if none exists."""
        if obj is None:
            return None
        # for_node resolves raw nodes itself — pre-resolving a joint through
        # get_transform_node yields a *list*, which defeats the lookup.
        rig = TubeRig.for_node(str(obj))
        if rig is not None:
            return rig

        # New rig: bind to the mesh transform when one resolves (tolerates
        # group picks); otherwise keep the plain transform (b002 constructs
        # from a joint after a restart, when the registry is empty).
        shape = _resolve_mesh_shape(obj)
        if shape:
            target = (
                NodeUtils.get_parent(shape, type=None, full_path=True) or str(shape)
            )
        else:
            target = NodeUtils.get_transform_node(str(obj)) or str(obj)
            if isinstance(target, (set, list, tuple)):
                target = next(iter(target), str(obj))
        rig_name = self.ui.txt000.text() or f"{short_name(target)}_RIG"
        return TubeRig(target, rig_name=rig_name)

    def _expand_step_joints(self, joints: List[str]) -> List[str]:
        """Expand a single joint to its full rig joint set (b002/b003/b004).

        A single joint expands to its chain; for Anchor rigs (sibling end
        joints, no chain) it expands to the joints sharing its parent group.
        """
        joints = [str(j) for j in joints]
        if len(joints) != 1:
            return joints
        if self.get_strategy() == "anchor":
            parent = NodeUtils.get_parent(joints[0], type=None, full_path=True)
            siblings = (
                cmds.listRelatives(parent, children=True, type="joint", fullPath=True)
                if parent
                else None
            )
            if siblings and len(siblings) == 2:
                return [str(j) for j in siblings]
            return joints
        return [str(j) for j in RigUtils.get_joint_chain_from_root(joints[0])]

    def _selected_step_joints(self) -> List[str]:
        """The selected joints for a step operation, chain-expanded."""
        sel = cmds.ls(selection=True, flatten=True) or []
        return self._expand_step_joints(cmds.ls(sel, type="joint", flatten=True) or [])

    def _existing_controls(self, tube_rig) -> Optional[str]:
        """First pre-existing control-rig node for *tube_rig*, or None.

        Delegates control lookup to ``TubeRig._end_control`` — the SSoT for
        the builders' naming conventions (a hand-rolled name check here
        previously tested ``<rig>_start``, which never exists: the builders
        suffix ``_CTRL``, so leftover anchor controls went undetected).
        """
        ik = f"{tube_rig.rig_name}_ikHandle"
        if cmds.objExists(ik):
            return ik
        return tube_rig._end_control(0) or tube_rig._end_control(-1)

    def create_joints_from_tube(self, obj):
        """Step 1 — create this rig's joints from the tube mesh (mode-aware)."""
        strategy = self.get_strategy()
        num_joints = 2 if strategy == "anchor" else self.ui.s000.value()
        edges = cmds.filterExpand(selectionMask=32)  # optional user edge selection

        tube_rig = self.get_tube_rig(obj)
        try:
            centerline, num_joints = tube_rig.resolve_centerline(
                num_joints, edges=edges
            )
        except ValueError as e:  # e.g. selection resolves to no polygon mesh
            self.sb.message_box(str(e))
            return []

        if not centerline or len(centerline) < 2:
            self.sb.message_box(
                "Failed to extract a valid centerline from the tube mesh."
            )
            return []

        if self.ui.chk000.isChecked():
            centerline = list(centerline)[::-1]

        joint_radius, _ = tube_rig.resolve_sizes(centerline, self.ui.s002.value())
        if strategy == "anchor":
            return tube_rig.create_anchor_joints(centerline, radius=joint_radius)
        return tube_rig.generate_joint_chain(
            centerline=centerline, num_joints=num_joints, radius=joint_radius
        )

    @CoreUtils.undoable
    def b000(self):
        """One-Click Rig — runs Steps 1 → 2 → 3 with the step parameters."""
        try:
            obj, *_ = cmds.ls(selection=True, objectsOnly=True, flatten=True)
        except ValueError:
            self.sb.message_box("Select a single polygon tube mesh to create a rig.")
            return

        strategy = self.get_strategy()
        tube_rig = self.get_tube_rig(obj)

        try:
            tube_rig.build(
                strategy=strategy,
                num_joints=self.ui.s000.value(),
                num_controls=self.ui.s001.value(),
                radius=self.ui.s002.value(),
                reverse=self.ui.chk000.isChecked(),
                edges=cmds.filterExpand(selectionMask=32),
                enable_stretch=self.ui.chk_stretch.isChecked(),
                enable_squash=self.ui.chk_squash.isChecked(),
                enable_volume=self.ui.chk_volume.isChecked(),
                enable_auto_bend=self.ui.chk_auto_bend.isChecked(),
                enable_twist=self.ui.chk_twist.isChecked(),
            )
            self.sb.message_box(f"Tube rig ({strategy}) created: {tube_rig.rig_name}")
        except Exception as e:
            self.sb.message_box(f"Build failed: {e}")
            self.sb.logger.error(f"Build Error: {e}", exc_info=True)

    @CoreUtils.undoable
    def b001(self):
        """Step 1: Create Joints from Tube."""
        try:
            obj, *_ = cmds.ls(selection=True, objectsOnly=True, flatten=True)
        except ValueError:
            self.sb.message_box(
                "Select the tube mesh (or an edge loop on it) to create joints."
            )
            return

        joints = self.create_joints_from_tube(obj)
        if joints:  # failures already message-boxed their reason
            self.sb.message_box(f"Joints created: {len(joints)}")

    @CoreUtils.undoable
    def b002(self):
        """Step 2: Create IK / Controls (mode dependent)."""
        strategy = self.get_strategy()

        joints = self._selected_step_joints()
        if not joints:
            self.sb.message_box(
                "Select the root joint created in Step 1.\n"
                "(Anchor: either or both end joints.)"
            )
            return
        if strategy == "anchor" and len(joints) != 2:
            self.sb.message_box(
                f"Anchor rigs use exactly 2 end joints (got {len(joints)}).\n"
                "Run Step 1 in Anchor mode to create them."
            )
            return
        if strategy == "spline" and len(joints) < 2:
            self.sb.message_box(
                "Spline IK needs a chain of at least 2 joints — select its root."
            )
            return

        tube_rig = self.get_tube_rig(joints[0])
        tube_rig.joints = joints  # step-created or manual chains alike

        existing = self._existing_controls(tube_rig)
        if existing:
            self.sb.message_box(
                f"Controls already exist for '{tube_rig.rig_name}' ({existing}).\n"
                "Delete the previous controls or rebuild with One-Click Rig."
            )
            return

        # Control size follows the measured tube radius (falls back to the
        # joints' display radii when the rig has no resolvable mesh).
        _, size = tube_rig.resolve_sizes(joint_radius=self.ui.s002.value())

        try:
            if strategy == "spline":
                controls, _, _ = tube_rig.create_spline_controls(
                    joints,
                    size=size,
                    num_controls=self.ui.s001.value(),
                    enable_stretch=self.ui.chk_stretch.isChecked(),
                    enable_squash=self.ui.chk_squash.isChecked(),
                    enable_volume=self.ui.chk_volume.isChecked(),
                    enable_twist=self.ui.chk_twist.isChecked(),
                    enable_auto_bend=self.ui.chk_auto_bend.isChecked(),
                )
                kind = "Spline IK"
            elif strategy == "anchor":
                controls = tube_rig.create_anchor_controls(
                    joints,
                    size=size,
                    enable_stretch=self.ui.chk_stretch.isChecked(),
                )
                kind = "Anchor"
            else:
                controls = tube_rig.create_fk_controls(joints, size=size)
                kind = "FK"
        except ValueError as e:
            self.sb.message_box(str(e))
            return

        ctrl_names = ", ".join(leaf_name(c) for c in controls)
        self.sb.message_box(
            f"{kind} controls created on {len(joints)} joints.\nControls: {ctrl_names}"
        )

    @CoreUtils.undoable
    def b003(self):
        """Step 3: Bind Joint Chain to Tube."""
        sel = cmds.ls(selection=True, flatten=True) or []
        if len(sel) < 2:
            self.sb.message_box(
                "Select the root joint, then Shift-select the tube mesh last.\n"
                "Usage: [Root Joint] → [Tube Mesh]"
            )
            return
        obj = sel[-1]

        if not _resolve_mesh_shape(obj):
            self.sb.message_box(
                f"'{leaf_name(obj)}' is not a polygon mesh — select the tube "
                "mesh last."
            )
            return

        joints = self._selected_step_joints()
        if len(joints) < 2:
            self.sb.message_box(
                "Select the root joint of the chain (at least 2 joints), then "
                "the tube mesh."
            )
            return

        tube_rig = self.get_tube_rig(obj)
        skin_cluster = tube_rig.bind_joint_chain(obj, joints)
        if not skin_cluster:
            self.sb.message_box(
                "Failed to bind the joint chain — see the Script Editor for "
                "details."
            )
            return
        self.sb.message_box(
            f"Skinned '{leaf_name(obj)}' to {len(joints)} joints "
            f"({leaf_name(skin_cluster)})."
        )

    @CoreUtils.undoable
    def b004(self):
        """Utility: Constrain Both Ends of Hose to Anchors."""
        sel = cmds.ls(selection=True, flatten=True) or []
        if len(sel) < 3:
            self.sb.message_box(
                "Select the root joint, then the start anchor, then the end "
                "anchor (in that order)."
            )
            return
        *joint_sel, start_anchor, end_anchor = sel

        root = cmds.ls(joint_sel, type="joint", flatten=True) or []
        if not root:
            self.sb.message_box("The first selection must be the rig's root joint.")
            return
        joints = self._expand_step_joints([root[0]])
        if len(joints) < 2:
            self.sb.message_box(
                "Could not derive the joint chain from the selection — "
                "select the rig's ROOT joint (the chain start)."
            )
            return

        tube_rig = self.get_tube_rig(joints[0])

        # Falloff weighting needs a bound mesh — fail up front with the fix.
        bound = tube_rig.skin_cluster or (
            cmds.listConnections(f"{joints[0]}.worldMatrix[0]", type="skinCluster")
        )
        if not bound:
            self.sb.message_box(
                "The joints aren't bound to a mesh yet — run Step 3 "
                "(Bind Skin) first."
            )
            return

        # Assign each anchor to its nearest tube end — selection order can't
        # cross the constraints.
        p_start = om.MVector(*_xform_t_ws(joints[0]))
        p_end = om.MVector(*_xform_t_ws(joints[-1]))
        a_first = om.MVector(*_xform_t_ws(start_anchor))
        a_second = om.MVector(*_xform_t_ws(end_anchor))
        crossed = (a_first - p_start).length() + (a_second - p_end).length() > (
            a_second - p_start
        ).length() + (a_first - p_end).length()
        if crossed:
            start_anchor, end_anchor = end_anchor, start_anchor

        # Falloff proportional to the tube: ≈2× its radius.
        _, size = tube_rig.resolve_sizes(joint_radius=self.ui.s002.value())
        falloff = size * 2.0

        start_result = tube_rig.constrain_end_with_falloff(
            joints, start_anchor, falloff=falloff, joint_index=0
        )
        end_result = tube_rig.constrain_end_with_falloff(
            joints, end_anchor, falloff=falloff, joint_index=-1
        )

        self.sb.message_box(
            "Both ends constrained:\n"
            f"  Start: {leaf_name(start_result) if start_result else 'failed'}\n"
            f"  End: {leaf_name(end_result) if end_result else 'failed'}"
        )

    # -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("tube_rig", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
