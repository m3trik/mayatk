# !/usr/bin/python
# coding=utf-8
"""Tube-mesh centerline extraction — pure geometry analysis, no scene objects.

Given a tube-shaped mesh, produce an ordered list of centerline points (edge-loop
ring centres, or a sampled fallback) plus the cross-section vertex groups and the
tube radius. Everything here reads the mesh and returns plain data — the rig
engine in :mod:`mayatk.rig_utils.tube_rig` turns that data into joints, curves
and skin weights.

Split out of ``tube_rig`` so the geometry layer stands on its own (mirrors
blendertk's ``rig_utils.tube_path``). ``TubePath`` is re-exported from
``tube_rig`` for existing importers.
"""
import math
from typing import List, Tuple, Optional

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    om = None
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils.components import Components
from mayatk.node_utils._node_utils import NodeUtils


class _TubePathInternal(object):
    """Internal helpers for TubePath."""

    @staticmethod
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
        shapes = [sh for sh in NodeUtils.get_shapes(s) if cmds.objectType(sh) == "mesh"]
        if not shapes:  # group / non-shape transform — descend to child meshes
            shapes = (
                cmds.listRelatives(
                    s,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                    noIntermediate=True,
                )
                or []
            )
        if not shapes:
            return None
        if len(shapes) > 1:
            cmds.warning(f"Multiple mesh shapes under '{s}'; using {shapes[0]}.")
        return shapes[0]


class TubePath(_TubePathInternal):
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
        shape = TubePath._resolve_mesh_shape(mesh)
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
    def _mesh_fn(mesh) -> "om.MFnMesh":
        """``MFnMesh`` over *mesh*'s DAG path (world-space capable)."""
        sel = om.MSelectionList()
        sel.add(str(mesh))
        return om.MFnMesh(sel.getDagPath(0))

    @staticmethod
    def _loop_topology(
        fn_mesh: "om.MFnMesh", loop_edges: List[int]
    ) -> Tuple[Tuple[int, ...], set, List[Tuple[int, int]]]:
        """(unique_edges, vertex_ids, edge_pairs) for an edge loop — pure API,
        no cmds round-trips (the previous per-edge component conversions
        dominated dense-mesh extraction time).

        A loop is a closed cycle (a circumferential cross-section) exactly
        when ``len(vertex_ids) == len(unique_edges)``; open paths —
        longitudinal loops that terminate at caps or open boundaries — carry
        one extra vertex. Uniqueness matters because ``polySelect -q
        -edgeLoop`` signals closure by repeating the seed edge; the sorted
        unique-edge tuple doubles as a seed-independent visited-set key.

        ``edge_pairs`` is handed back rather than re-derived by the caller:
        ``order_cycle`` needs exactly this connectivity, and re-querying it
        would double the ``getEdgeVertices`` calls this method exists to
        minimise.
        """
        unique_edges = tuple(sorted(set(loop_edges)))
        vertex_ids = set()
        edge_pairs = []
        for e in unique_edges:
            v0, v1 = fn_mesh.getEdgeVertices(e)
            vertex_ids.add(v0)
            vertex_ids.add(v1)
            edge_pairs.append((v0, v1))
        return unique_edges, vertex_ids, edge_pairs

    @staticmethod
    def order_cycle(edge_pairs) -> List[int]:
        """Walk ``edge_pairs`` into cyclic vertex order, or ``[]``.

        A ring's vertex ids carry no ordering: they are only sequential on
        primitive-generated meshes (polyCylinder), and any consumer that reads
        a ring as a POLYGON BOUNDARY — Newell's method in ``get_end_normals``,
        notably — silently self-intersects on a user-modeled or cleaned mesh
        whose ids are scrambled, yielding a wrong-but-nonzero normal that
        passes a magnitude guard. Connectivity is the ordering that survives
        renumbering.

        Pure over an edge list (``[(v0, v1), ...]``) so the traversal is
        testable without a mesh. Returns ``[]`` unless the edges form one
        simple closed cycle (every vertex exactly two neighbours).
        """
        adjacency = {}
        for v0, v1 in edge_pairs:
            if v0 == v1:
                return []
            adjacency.setdefault(v0, []).append(v1)
            adjacency.setdefault(v1, []).append(v0)
        if not adjacency or any(len(n) != 2 for n in adjacency.values()):
            return []

        start = min(adjacency)
        ordered = [start]
        previous, current = None, start
        while True:
            a, b = adjacency[current]
            nxt = a if a != previous else b
            if nxt == start:
                break
            if len(ordered) > len(adjacency):  # defensive: not a simple cycle
                return []
            ordered.append(nxt)
            previous, current = current, nxt
        # A disjoint pair of cycles also satisfies the degree test; only a
        # full traversal proves the edges are ONE ring.
        return ordered if len(ordered) == len(adjacency) else []

    @staticmethod
    def _cross_section_vertex_ids(mesh, fn_mesh) -> List[List[int]]:
        """Vertex-id groups for every circumferential edge loop, in ring order.

        The shared traversal behind ``get_edge_loop_centers`` (which averages
        each group into a centre) and ``get_vertex_rings`` (which hands the
        groups to the skin solve).
        """
        # Seed with a CLOSED (circumferential) edge loop. An arbitrary first
        # edge may be LONGITUDINAL (common on user-modeled pipes; polyCylinder
        # just happens to number a rim edge first) — seeding from one
        # transposes the loop/ring traversal, and every "cross-section centre"
        # becomes a longitudinal-strip centroid: a small ring of points around
        # the mesh centroid instead of a path down the tube. Closure is the
        # topology-exact discriminator (see ``_loop_topology``). Skip edges
        # already covered by a rejected loop, so the scan is bounded by the
        # number of distinct loops, not the edge count.
        first_loop = None
        checked_edges = set()
        for edge_idx in range(fn_mesh.numEdges):
            if edge_idx in checked_edges:
                continue
            loop = cmds.polySelect(mesh, q=True, edgeLoop=edge_idx)
            if not loop:
                checked_edges.add(edge_idx)
                continue
            checked_edges.update(loop)
            if len(loop) >= 3:
                unique_edges, vertex_ids, _ = TubePath._loop_topology(fn_mesh, loop)
                if len(vertex_ids) == len(unique_edges):
                    first_loop = loop
                    break
        if not first_loop:
            return []

        # Get the edge ring from the first loop edge — yields one edge per cross-section.
        ring_edges = cmds.polySelect(mesh, q=True, edgeRing=first_loop[0])
        if not ring_edges:
            return []

        visited_loops = set()
        rings: List[List[int]] = []
        for edge_idx in ring_edges:
            loop_edges = cmds.polySelect(mesh, q=True, edgeLoop=edge_idx)
            # Boundary rings on capped tubes degenerate to a single edge (cap
            # fan triangles break loop traversal) — their midpoint is off-axis,
            # so skip them; _complete_cap_ends recovers the true cap centres.
            # Open paths (partial arcs from irregular topology) are skipped
            # too: an arc's centroid sits off the tube axis.
            if not loop_edges or len(loop_edges) < 3:
                continue
            unique_edges, vertex_ids, edge_pairs = TubePath._loop_topology(
                fn_mesh, loop_edges
            )
            if len(vertex_ids) != len(unique_edges):
                continue

            # The unique-edge tuple is the visited-set key: polySelect repeats
            # the seed edge on closed loops, and the duplicate's sort position
            # would otherwise vary with the seed, defeating the visited-set.
            if unique_edges in visited_loops:
                continue
            visited_loops.add(unique_edges)
            # Cyclic order, or id order on topology the walk can't order --
            # the fallback keeps set-consumers (the skin solve reads a ring as
            # a GROUP) working regardless.
            rings.append(TubePath.order_cycle(edge_pairs) or sorted(vertex_ids))
        return rings

    @staticmethod
    def get_vertex_rings(mesh) -> List[List[int]]:
        """Vertex-index groups, one per circumferential edge loop.

        The topological answer to "which vertices share a cross-section",
        which geometry can only approximate. On a BEND a ring's vertices
        project to different arc lengths — the inside of the bend lands
        short, the outside long — so a weight solve reading those
        projections gives one cross-section a spread of weights (measured 9%
        to 18% on tight bends; invisible on a straight tube, which is why a
        straight-centerline uniformity test passes regardless). Feeding
        these groups to ``CurveWeights.solve(rings=...)`` makes ring
        uniformity exact by construction.

        Returns:
            (List[List[int]]) Vertex indices per ring. Empty when the
            topology yields no closed loops — callers then fall back to
            per-vertex projection.
        """
        mesh = TubePath._resolve_mesh_shape(mesh)
        if not mesh:
            return []
        fn_mesh = TubePath._mesh_fn(mesh)
        if not fn_mesh.numEdges:
            return []
        return TubePath._cross_section_vertex_ids(mesh, fn_mesh)

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
        mesh = TubePath._resolve_mesh_shape(mesh)
        if not mesh:
            return [], 0

        # One API handle for the whole extraction: loop topology comes from
        # getEdgeVertices and positions from a single getPoints call, instead
        # of per-edge component conversions and per-vertex pointPosition
        # round-trips (which dominated dense-mesh runtime).
        fn_mesh = TubePath._mesh_fn(mesh)
        if not fn_mesh.numEdges:
            return [], 0

        rings = TubePath._cross_section_vertex_ids(mesh, fn_mesh)
        if not rings:
            return [], 0

        # World-space positions for every vertex, fetched once.
        points = fn_mesh.getPoints(om.MSpace.kWorld)

        loop_centers = []
        for vertex_ids in rings:
            accum = om.MVector(0.0, 0.0, 0.0)
            for v in vertex_ids:
                p = points[v]
                accum += om.MVector(p.x, p.y, p.z)
            count = len(vertex_ids)
            loop_centers.append(
                om.MPoint(accum.x / count, accum.y / count, accum.z / count)
            )

        # The edge-RING walk already visits cross-sections in connectivity
        # order along the tube, so the centres arrive ordered — do NOT
        # re-derive that order geometrically. ``Polyline.order_points`` is a
        # greedy nearest-neighbour walk, which is correct only while the next
        # ring is the nearest point: on a tube that passes near itself with
        # rings spaced wider than that gap (a coarsely tessellated coil, a
        # tight U), it hops to the neighbouring pass instead and scrambles
        # the path. Measured on an 11-ring coil: raw order 0 inversions,
        # after order_points 7 — and the "end" of the scrambled path sits
        # mid-tube, which is how an end CONTROL ends up built in the middle
        # of the hose. Only near-coincident filtering remains (bevels and
        # high-res loops produce virtually identical centres, which make
        # zero-length bone vectors downstream).
        if loop_centers:
            loop_centers = TubePath._dedupe_consecutive(loop_centers)

        if len(loop_centers) >= 2:
            # Recover the dropped end bands from topology first; only fall
            # back to the surface probe for ends it cannot reach (open rims,
            # irregular caps), since that probe misreads self-approaching
            # tubes the same way greedy ordering does.
            start, end = TubePath._topological_cap_centers(fn_mesh, rings)
            if start is not None:
                loop_centers = [start] + list(loop_centers)
            if end is not None:
                loop_centers = list(loop_centers) + [end]
            loop_centers = TubePath._dedupe_consecutive(loop_centers)
            if start is None or end is None:
                loop_centers = TubePath._complete_cap_ends(mesh, loop_centers)

        return loop_centers, len(loop_centers)

    @staticmethod
    def get_end_normals(mesh) -> Tuple[Optional["om.MVector"], Optional["om.MVector"]]:
        """Unit normals of the tube's two end cross-sections, pointing along
        the path (start normal into the tube, end normal out of it).

        The end frame a rig builds should be square to the tube's END FACE,
        not to the last chord of its centerline: hoses are routinely modeled
        with an angle-cut opening, where the two differ by the cut angle and
        an end control built from the chord sits visibly skew to the cap it
        is supposed to plug into.

        Falls back to ``None`` per end when the topology yields no rings, so
        callers keep their chord-derived frame.

        Returns:
            (start_normal, end_normal), or (None, None) without usable rings.
        """
        shape = TubePath._resolve_mesh_shape(mesh)
        if not shape:
            return None, None
        fn_mesh = TubePath._mesh_fn(shape)
        rings = TubePath._cross_section_vertex_ids(shape, fn_mesh)
        if len(rings) < 2:
            return None, None
        points = fn_mesh.getPoints(om.MSpace.kWorld)

        def _plane_normal(ring):
            accum = om.MVector(0.0, 0.0, 0.0)
            for v in ring:
                accum += om.MVector(points[v])
            centre = accum / len(ring)
            # Newell's method: robust for a non-planar or unevenly sampled ring.
            normal = om.MVector(0.0, 0.0, 0.0)
            for i, v in enumerate(ring):
                a = om.MVector(points[v]) - centre
                b = om.MVector(points[ring[(i + 1) % len(ring)]]) - centre
                normal += a ^ b
            return (centre, normal.normal() if normal.length() > 1e-9 else None)

        (c_first, n_first), (c_last, n_last) = (
            _plane_normal(rings[0]),
            _plane_normal(rings[-1]),
        )
        if n_first is None or n_last is None:
            return None, None
        # Newell's sign follows winding order, which carries no relation to
        # the tube's direction — orient both against the path itself.
        along = (c_last - c_first).normal()
        if n_first * along < 0:
            n_first = -n_first
        if n_last * along < 0:
            n_last = -n_last
        return n_first, n_last

    @staticmethod
    def _topological_cap_centers(fn_mesh, rings) -> Tuple[Optional[om.MPoint], Optional[om.MPoint]]:
        """Cap centres from CONNECTIVITY, for the two ends of a ring walk.

        A capped tube's end ring bounds the cap polygon, where ``polySelect
        -edgeLoop`` degenerates and drops it — so the walk stops one band
        short at each end. Those dropped vertices are exactly the ones
        adjacent to the first/last surviving ring and in no ring themselves,
        which recovers them without a single spatial query.

        That matters because the geometric alternative (probe past the end,
        take the closest surface point) asks "what is nearest?" — the same
        question that scrambles a coiled tube's ordering. On a tube that
        passes near itself the probe lands on the neighbouring pass and the
        recovered "end" is somewhere else entirely.

        Returns:
            (start_centre, end_centre); either is None when that end has no
            recoverable neighbours (an open rim, already-complete walk).
        """
        if not rings:
            return None, None
        in_a_ring = {v for ring in rings for v in ring}
        first, last = set(rings[0]), set(rings[-1]) if len(rings) > 1 else set()

        # One pass over the edges builds both ends' neighbour sets; MFnMesh
        # exposes edge -> vertices but not the reverse, and a per-vertex
        # iterator would walk the mesh twice for the same answer.
        found = {0: set(), 1: set()}
        for e in range(fn_mesh.numEdges):
            v0, v1 = fn_mesh.getEdgeVertices(e)
            for a, b in ((v0, v1), (v1, v0)):
                if b in in_a_ring:
                    continue
                if a in first:
                    found[0].add(b)
                if a in last:
                    found[1].add(b)

        points = fn_mesh.getPoints(om.MSpace.kWorld)

        def _centre(vertex_ids):
            if not vertex_ids:
                return None
            accum = om.MVector(0.0, 0.0, 0.0)
            for w in vertex_ids:
                accum += om.MVector(points[w])
            return om.MPoint(accum / len(vertex_ids))

        return _centre(found[0]), _centre(found[1])

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

        with Components.closest_point_probe(shape) as cpom:
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

    @staticmethod
    def _dedupe_consecutive(points: List, min_dist: float = 0.001) -> List:
        """Drop consecutive points closer than ``min_dist`` to their predecessor."""
        if not points:
            return []
        result = [points[0]]
        for p in points[1:]:
            prev = result[-1]
            if math.dist((prev[0], prev[1], prev[2]), (p[0], p[1], p[2])) > min_dist:
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
        shape = TubePath._resolve_mesh_shape(mesh)
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

        interior = [p for p, d in zip(pts, arc) if d >= probe and (total - d) >= probe]
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

        with Components.closest_point_probe(shape) as cpom:
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
            raise ValueError(
                f"Could not resolve mesh shape from edge: {edge_selection[0]}"
            )

        seeds = []
        for edge in edge_selection:
            vertices = cmds.ls(
                cmds.polyListComponentConversion(edge, fromEdge=True, toVertex=True),
                flatten=True,
                long=True,
            )
            p1 = cmds.pointPosition(vertices[0], world=True)
            p2 = cmds.pointPosition(vertices[1], world=True)
            seeds.append(
                om.MPoint((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, (p1[2] + p2[2]) / 2)
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
        with Components.closest_point_probe(mesh_shape) as cpom:
            # Upper bound for the tube radius: half the smallest bounding-box
            # dimension (≈ the tube diameter). Used to step surface-coincident
            # seeds into the interior.
            bbox = cmds.exactWorldBoundingBox(mesh_shape)
            dims = [
                abs(bbox[3] - bbox[0]),
                abs(bbox[4] - bbox[1]),
                abs(bbox[5] - bbox[2]),
            ]
            probe = 0.5 * min((d for d in dims if d > 1e-6), default=1.0)

            centers = list(seeds)
            for _ in range(iterations):
                refined = []
                for center in centers:
                    cmds.setAttr(
                        f"{cpom}.inPosition",
                        center.x,
                        center.y,
                        center.z,
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
                        opposite_query.x,
                        opposite_query.y,
                        opposite_query.z,
                        type="double3",
                    )
                    pos_arr2 = cmds.getAttr(f"{cpom}.position")[0]
                    surface_pt2 = om.MPoint(pos_arr2[0], pos_arr2[1], pos_arr2[2])

                    # Midpoint of opposing surface hits ≈ true center (component-wise).
                    refined.append(
                        om.MPoint(
                            (surface_pt.x + surface_pt2.x) / 2,
                            (surface_pt.y + surface_pt2.y) / 2,
                            (surface_pt.z + surface_pt2.z) / 2,
                        )
                    )

                centers = refined
            return centers

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
            center_point = om.MPoint(accum.x / count, accum.y / count, accum.z / count)
            centerline_points.append(center_point)

        # Apply smoothing if requested
        if smooth and centerline_points:
            centerline_points = ptk.Polyline.smooth(centerline_points, window_size)

        return centerline_points
