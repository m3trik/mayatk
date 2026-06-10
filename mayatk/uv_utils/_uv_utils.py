# !/usr/bin/python
# coding=utf-8
import os
import uuid
from typing import List, Sequence, Tuple, Union

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils, as_strings
from mayatk.core_utils.components import Components
from mayatk.node_utils._node_utils import NodeUtils


UvSnapshot = Tuple[str, str, str]  # (shape_path, original_set_name, snapshot_set_name)


class UvUtils(ptk.HelpMixin):
    @staticmethod
    def calculate_uv_padding(
        map_size: int, normalize: bool = False, factor: int = 256
    ) -> float:
        """Calculate the UV padding for a given map size to ensure consistent texture padding across different resolutions.
        Optionally return the padding as a normalized value relative to the map size.

        Parameters:
        map_size (int): The size of the map for which to calculate UV padding, typically the width or height in pixels.
        normalize (bool): If True, returns the padding as a normalized value. Default is False.
        factor (int): The factor by which to divide the map size to calculate the padding. Default is 256.

        Returns:
        float: The calculated padding in pixels or normalized units.

        Expected Output:
        - For a 1024 pixel map: 4.0 pixels of padding or 0.00390625 if normalized
        - For a 2048 pixel map: 8.0 pixels of padding or 0.00390625 if normalized
        - For a 4096 pixel map: 16.0 pixels of padding or 0.00390625 if normalized
        - For a 8192 pixel map: 32.0 pixels of padding or 0.00390625 if normalized

        Example:
            calculate_uv_padding(4096, normalize=True)
        0.00390625
        """
        padding = map_size / factor
        if normalize:
            return padding / map_size
        return padding

    @staticmethod
    def orient_shells(objects):
        """Rotate UV shells to run parallel with the most adjacent U or V axis of their bounding box.

        Parameters:
            objects (str/obj/list): Polygon mesh objects and/or components.
        """

        objects = as_strings(objects)
        for obj in cmds.ls(objects, objectsOnly=True) or []:
            # filter components for only this object.
            obj_compts = [i for i in objects if obj in (cmds.ls(i, objectsOnly=True) or [])]
            cmds.polyLayoutUV(
                obj_compts,
                flipReversed=0,
                layout=0,
                layoutMethod=1,
                percentageSpace=0.2,
                rotateForBestFit=3,
                scale=0,
                separate=0,
            )

    @staticmethod
    def move_to_uv_space(objects, u, v, relative=True):
        """Move objects to the given u and v coordinates.

        Parameters:
            objects (str/obj/list): The object(s) to move.
            u (int): u coordinate.
            v (int): v coordinate.
            relative (bool): Move relative or absolute.
        """

        objects = as_strings(objects)
        # Convert the objects to UVs
        uvs = cmds.polyListComponentConversion(objects, fromFace=True, toUV=True) or []
        uvs = cmds.ls(uvs, flatten=True) or []

        # Move the UVs to the given u and v coordinates
        cmds.polyEditUV(uvs, u=u, v=v, relative=relative)

    @classmethod
    @CoreUtils.undoable
    def mirror_uvs(
        cls,
        objects,
        axis: str = "u",
        pivot: tuple | None = None,
        per_shell: bool = True,
        preserve_position: bool = True,
    ):
        """Mirror UVs across U or V.

        By default (`preserve_position=True`), this preserves the UV shell's *footprint*
        (the exact set of UV points) by reassigning UV components to the original
        point set via a one-to-one assignment. This is different from Maya's typical
        geometric flip/mirror which changes the shell's shape in UV space.

        The common failure mode when flipping UVs on a whole object is that multiple
        shells share one pivot, so some shells translate after the flip.
        By default this method flips *each UV shell* around its own center.

        Parameters:
            objects (str/obj/list): Object(s), faces, or UVs to flip.
            axis (str): 'u'/'horizontal' or 'v'/'vertical'. Default 'u'.
            pivot (tuple, optional): (u, v) pivot to use. If None, pivot is computed
                from each shell's UV bounds (or from the selection if per_shell=False).
            per_shell (bool): If True (default), flip each UV shell independently.
            preserve_position (bool): If True (default), preserves the UV shell's
                footprint (the exact set of UV points) by reassigning UV components
                to the original point set via a one-to-one assignment.
                If False, performs a geometric flip (mirrors UV coordinates around
                the pivot), which will mirror the shell shape in UV space.
        """
        axis_norm = (axis or "").lower()
        do_flip_u = axis_norm in ("u", "h", "horizontal")
        do_flip_v = axis_norm in ("v", "vert", "vertical")
        if not do_flip_u and not do_flip_v:
            raise ValueError(
                f"Invalid axis '{axis}'. Use 'u'/'horizontal' or 'v'/'vertical'."
            )

        uv_groups = []
        if per_shell:
            shell_face_sets = cls.get_uv_shell_sets(objects, returned_type="shell")
            for face_set in shell_face_sets:
                shell_uvs = cmds.polyListComponentConversion(face_set, toUV=True) or []
                shell_uvs = cmds.ls(shell_uvs, flatten=True) or []
                if shell_uvs:
                    uv_groups.append(shell_uvs)
        else:
            uvs = cmds.polyListComponentConversion(objects, toUV=True) or []
            uvs = cmds.ls(uvs, flatten=True) or []
            if uvs:
                uv_groups.append(uvs)

        if not uv_groups:
            cmds.warning("No UVs found to flip.")
            return

        for uv_list in uv_groups:
            # 1. Get all UVs and coordinates
            coords_flat = cmds.polyEditUV(uv_list, query=True)
            if not coords_flat:
                continue

            us = coords_flat[0::2]
            vs = coords_flat[1::2]

            orig_slots = list(zip(us, vs))

            # 2. Direct mapping approach
            # We want the final UV *positions* to remain exactly the same set of points
            # (preserve footprint), while the UV *assignment* is permuted as if the
            # shell was flipped. The reliable way to do this is:
            #   - compute where each UV would go if we did a geometric flip (targets)
            #   - solve a one-to-one assignment from targets -> original slots
            #   - move each UV to its assigned original slot
            # This avoids row/col inference, which breaks on tapered/tilted shells.

            # Calculate Center for Mirroring
            if pivot:
                center_u, center_v = pivot
            else:
                center_u = (min(us) + max(us)) / 2.0
                center_v = (min(vs) + max(vs)) / 2.0

            # Compute flipped target position for each UV (geometric flip, not applied)
            targets = []
            for u, v in orig_slots:
                if do_flip_u:
                    targets.append((center_u - (u - center_u), v))
                else:
                    targets.append((u, center_v - (v - center_v)))

            n = len(uv_list)
            if n > 350:
                cmds.warning(
                    "Large UV shell detected; direct-mapping flip may take a moment."
                )

            if not preserve_position:
                # Geometric flip: actually mirrors UV coordinates around the pivot.
                for i, (u, v) in enumerate(targets):
                    cmds.polyEditUV(uv_list[i], u=u, v=v, relative=False)
                continue

            # Footprint-preserving flip:
            # Solve a one-to-one assignment from flipped targets -> original slots,
            # then move each UV to the assigned original slot.
            cost = [[0.0] * n for _ in range(n)]
            for i in range(n):
                tu, tv = targets[i]
                row = cost[i]
                for j in range(n):
                    su, sv = orig_slots[j]
                    du = tu - su
                    dv = tv - sv
                    row[j] = du * du + dv * dv

            row_ind, col_ind = ptk.MathUtils.linear_sum_assignment(cost)
            assignment = {r: c for r, c in zip(row_ind, col_ind)}

            for uv_idx in range(n):
                slot_idx = assignment.get(uv_idx)
                if slot_idx is None:
                    continue
                u, v = orig_slots[slot_idx]
                cmds.polyEditUV(uv_list[uv_idx], u=u, v=v, relative=False)

    @classmethod
    @CoreUtils.undoable
    def flip_uvs(
        cls,
        objects,
        axis: str = "u",
        pivot: tuple | None = None,
        per_shell: bool = True,
        preserve_position: bool = True,
    ):
        """Backward-compatible alias for :meth:`mirror_uvs`.

        Note: this operation is *not* a standard geometric flip when
        `preserve_position=True`.
        """
        try:
            cmds.warning(
                "UvUtils.flip_uvs is deprecated; use UvUtils.mirror_uvs instead."
            )
        except Exception:
            pass
        return cls.mirror_uvs(
            objects,
            axis=axis,
            pivot=pivot,
            per_shell=per_shell,
            preserve_position=preserve_position,
        )

    @staticmethod
    def get_uv_shell_sets(objects=None, returned_type="shell"):
        """Get UV shells and their corresponding sets of faces.

        Optimized to use the Maya API (OpenMaya) for performance and reliability,
        avoiding selection changes.

        Parameters:
            objects (obj/list): Polygon object(s) or Polygon face(s). If None,
                uses the current selection.
            returned_type (str): The desired returned type. Valid values are:
                'shell', 'id'.

        Returns:
            (list)(dict): Depending on the given returned_type arg.
        """
        import maya.api.OpenMaya as om

        if objects is None:
            objects = cmds.ls(selection=True) or []

        # Expand inputs to faces
        faces = Components.get_components(objects, "faces", flatten=True)
        if not faces:
            return [] if returned_type in ("shell", "id") else {}

        # Group faces by their shape node to batch API calls
        # Use str() on node components to get cmds-compatible strings
        mesh_faces_map = {}
        for f in faces:
            f_str = str(f)
            node_str = f_str.split(".")[0]
            # Resolve to shape node
            if cmds.objectType(node_str, isAType="transform"):
                shapes = cmds.listRelatives(node_str, shapes=True, fullPath=True) or []
                shape_str = shapes[0] if shapes else None
            else:
                shape_str = node_str
            if shape_str is None:
                continue
            if shape_str not in mesh_faces_map:
                mesh_faces_map[shape_str] = []
            mesh_faces_map[shape_str].append(f)

        shells = {}
        shell_count = 0

        for shape_str, shape_faces in mesh_faces_map.items():
            if cmds.objectType(shape_str) != "mesh":
                continue

            try:
                # Retrieve MFnMesh
                sel = om.MSelectionList()
                sel.add(shape_str)
                dag_path = sel.getDagPath(0)
                mfn_mesh = om.MFnMesh(dag_path)
                current_uv_set = mfn_mesh.currentUVSetName()

                # Get shell IDs for all UVs on the mesh
                _, uv_shell_ids = mfn_mesh.getUvShellsIds(current_uv_set)

                # Store faces by local shell ID
                local_shells = {}

                for f in shape_faces:
                    # Parse face index from string representation
                    f_str = str(f)
                    try:
                        face_idx = int(f_str.split("[")[1].rstrip("]"))
                    except (IndexError, ValueError):
                        continue
                    try:
                        # Get the UV index of the first vertex of the face.
                        if mfn_mesh.polygonVertexCount(face_idx) > 0:
                            uv_id = mfn_mesh.getPolygonUVid(face_idx, 0, current_uv_set)

                            # Look up shell ID for this UV
                            sid = uv_shell_ids[uv_id]
                            local_shells.setdefault(sid, []).append(f)
                    except Exception:
                        # Case: Face has no UVs projected
                        pass

                # Add to main results
                for sid in local_shells:
                    shells[shell_count] = local_shells[sid]
                    shell_count += 1

            except Exception as e:
                cmds.warning(f"Error processing UV shells for {shape_str}: {e}")
                continue

        if returned_type == "shell":
            return list(shells.values())
        elif returned_type == "id":
            return list(shells.keys())
        else:
            raise ValueError(
                f"Invalid returned_type: {returned_type}. Valid values are: 'shell', 'id'."
            )

    @staticmethod
    def get_uv_shell_border_edges(objects):
        """Get the edges that make up any UV islands of the given objects.

        Parameters:
            objects (str/obj/list): Polygon objects, mesh UVs, or Edges.

        Returns:
            (list): UV border edges.
        """

        objects = as_strings(objects)
        uv_border_edges = []
        for obj in cmds.ls(objects) or []:
            obj_str = str(obj)
            # Resolve transform to its shape
            if "." not in obj_str:
                try:
                    shapes = cmds.listRelatives(obj_str, shapes=True, fullPath=True) or []
                    if shapes:
                        obj_str = shapes[0]
                except Exception:
                    pass

            # Determine component or node type and get connected edges
            if "." not in obj_str and cmds.objectType(obj_str) == "mesh":
                # Mesh shape — get UV border edges
                connected_edges = cmds.polyListComponentConversion(
                    obj_str, fromUV=True, toEdge=True
                ) or []
                connected_edges = cmds.ls(connected_edges, flatten=True) or []
            elif ".e[" in obj_str:
                # Edge component — already an edge
                connected_edges = cmds.ls(obj_str, flatten=True) or []
            elif ".map[" in obj_str or ".uv[" in obj_str:
                # UV component — convert to edges
                connected_edges = cmds.polyListComponentConversion(
                    obj_str, fromUV=True, toEdge=True
                ) or []
                connected_edges = cmds.ls(connected_edges, flatten=True) or []
            else:
                raise ValueError(f"Unsupported object type: {obj_str}")

            for edge in connected_edges:
                edge_uvs = cmds.ls(
                    cmds.polyListComponentConversion(edge, tuv=True) or [], fl=True
                ) or []
                edge_faces = cmds.ls(
                    cmds.polyListComponentConversion(edge, tf=True) or [], fl=True
                ) or []
                if (
                    len(edge_uvs) > 2 or len(edge_faces) < 2
                ):  # If an edge has more than two uvs or less than 2 faces, it's a uv border edge.
                    uv_border_edges.append(edge)

        return uv_border_edges

    # --------------------------------------------------------- cylinder unwrap
    @staticmethod
    def _comp_ids(components):
        """Set of integer indices parsed from component strings (``name.e[12]``).

        Flattens first, so range components (``e[0:5]``) expand; empty / ``None``
        input yields an empty set.
        """
        return {
            int(c.split("[")[1].rstrip("]"))
            for c in (cmds.ls(components or [], flatten=True) or [])
        }

    @classmethod
    def get_cylinder_seam_edges(
        cls, mesh, sections=None, invert_seam: bool = False, cap_faces=None
    ):
        """Identify the UV seam edges for unwrapping a smooth cylinder / tube.

        Lower-level seamer for a *single, smooth* swept tube (used by Curve to
        Tube). For turned / stepped hard-surface shapes use
        :meth:`get_auto_seam_edges` instead.

        Returns ``(length_loop, cap_rings)`` -- two lists of edge component
        strings:

        - ``length_loop`` -- one edge loop running *along* the cylinder (the
          lengthwise seam that opens the body into a flat strip).
        - ``cap_rings`` -- the edges where each end cap meets the body, so
          cutting them peels every cap into its own UV shell. Empty for an open
          (uncapped) tube or a closed torus.

        Three topologies are handled:

        - **Open tube** (has boundary edges): the lengthwise edge at a rim
          vertex seeds the loop; no cap rings.
        - **Capped cylinder** (end caps): the cap faces' edges are the cap
          rings; the lengthwise edge at a cap-corner vertex seeds the loop.
        - **Closed torus** (no boundary, no caps): the loop whose edge count
          differs from ``sections`` (the around-ring count) is lengthwise.

        Parameters:
            mesh (str): A polygon cylinder / tube transform or shape.
            sections (int, optional): Sides around the cylinder. Only used to
                disambiguate the lengthwise loop on a closed torus.
            invert_seam (bool): Place the lengthwise seam on the opposite side
                of the cylinder (the diametrically opposite vertex of the start
                ring), letting the caller control where the seam lands.
            cap_faces (list, optional): Explicit cap face indices. A caller that
                just created the caps (e.g. via ``polyCloseBorder``) can pass
                them so detection is exact for any section count; otherwise caps
                are auto-detected as n-gons (reliable for >= 5 sides).
        """
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(str(mesh))
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)

        # Boundary edge ids (the open ends of an uncapped tube).
        eit = om.MItMeshEdge(dag)
        boundary_ids = set()
        while not eit.isDone():
            if eit.onBoundary():
                boundary_ids.add(eit.index())
            eit.next()

        # Caps: either supplied by the caller (exact, any section count) or
        # auto-detected as n-gons (a clean tube body is all quads / tris, so a
        # face with >4 sides is an end cap -- reliable for >= 5 sections).
        if cap_faces is None:
            cap_faces = [
                i for i in range(fn.numPolygons) if len(fn.getPolygonVertices(i)) > 4
            ]
        cap_faces = list(cap_faces)

        cap_ring_comps = []
        if cap_faces:
            cap_face_comps = [f"{mesh}.f[{i}]" for i in cap_faces]
            cap_ring_comps = (
                cmds.ls(
                    cmds.polyListComponentConversion(
                        cap_face_comps, fromFace=True, toEdge=True
                    )
                    or [],
                    flatten=True,
                )
                or []
            )

        # Closed torus: no boundary and no caps -> the lengthwise loop is the
        # one whose edge count differs from the around-ring count.
        if not boundary_ids and not cap_faces:
            return cls._torus_length_loop(mesh, sections), []

        # Seed the lengthwise loop from a start-ring vertex.
        if cap_faces:
            ring_vids = list(fn.getPolygonVertices(cap_faces[0]))
        else:  # open tube -- the vertices of one boundary loop
            first_b = next(iter(boundary_ids))
            border = cmds.polySelect(mesh, edgeBorder=first_b, ass=True) or []
            ring_vids = list(
                cls._comp_ids(
                    cmds.polyListComponentConversion(
                        border, fromEdge=True, toVertex=True
                    )
                )
            )
        if not ring_vids:
            return [], cap_ring_comps

        v0 = ring_vids[0]
        if invert_seam and len(ring_vids) > 2:
            p0 = fn.getPoint(v0, om.MSpace.kWorld)
            v0 = max(
                ring_vids,
                key=lambda v: (fn.getPoint(v, om.MSpace.kWorld) - p0).length(),
            )

        # The lengthwise edge at v0 is the one that is neither a boundary edge
        # nor a cap-ring edge (those run *around* the cylinder).
        v0_edge_ids = cls._comp_ids(
            cmds.polyListComponentConversion(
                f"{mesh}.vtx[{v0}]", fromVertex=True, toEdge=True
            )
        )
        lengthwise = sorted(v0_edge_ids - boundary_ids - cls._comp_ids(cap_ring_comps))
        if not lengthwise:
            return [], cap_ring_comps
        length_loop = cmds.polySelect(mesh, edgeLoop=lengthwise[0], ass=True) or []
        return length_loop, cap_ring_comps

    @staticmethod
    def _revolution_axis(points):
        """Axis of a body of revolution from its vertex positions.

        A revolved shape's vertex covariance has two near-equal (radial)
        eigenvalues and one distinct (axial) one; the axis is the eigenvector of
        that odd-one-out eigenvalue -- robust whether the tube is taller than it
        is wide (a column) or wider than tall (a flat flange).
        """
        import maya.api.OpenMaya as om
        import numpy as np

        pts = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
        centered = pts - pts.mean(axis=0)
        evals, evecs = np.linalg.eigh(centered.T @ centered)  # ascending
        # The lone outlier eigenvalue is the axial one (the other two are radial).
        odd = 2 if (evals[2] - evals[1]) >= (evals[1] - evals[0]) else 0
        axis = evecs[:, odd]
        return om.MVector(float(axis[0]), float(axis[1]), float(axis[2])).normal()

    @classmethod
    def get_auto_seam_edges(cls, mesh, angle: float = 45.0, invert_seam: bool = False):
        """Seam edges that auto-unwrap a turned / stepped cylinder or tube.

        Two complementary cuts peel the mesh into clean per-section UV shells:

        - **Hard creases** -- every edge whose two faces meet at >= ``angle``
          degrees. On a turned profile these are the cap rims and the ~90 degree
          step rings, so each smooth section (and each flat step / cap) becomes
          its own shell while shallow chamfers stay merged with their neighbour.
        - **One lengthwise column** -- a single column of axial edges at one
          angular position about the tube axis, which opens every tubular
          section into a flat strip. Flat steps and caps are already planar, so
          they need no opening cut and keep their shape.

        3D boundary edges (an open tube's rims) are already UV borders and are
        left uncut. Returns a flat list of edge component strings.

        Parameters:
            mesh (str): A polygon cylinder / tube / turned-profile transform or
                shape (a body of revolution -- a roughly straight axis).
            angle (float): Crease threshold in degrees. Edges whose dihedral
                angle meets or exceeds it are cut. Default 45 cuts ~90 degree
                steps while keeping shallow chamfers.
            invert_seam (bool): Land the lengthwise column on the opposite side.
        """
        import math
        import maya.api.OpenMaya as om
        from collections import defaultdict

        name = str(mesh)
        sel = om.MSelectionList()
        sel.add(name)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        pts = fn.getPoints(om.MSpace.kWorld)
        if not pts:
            return []

        center = om.MVector(
            sum(p.x for p in pts) / len(pts),
            sum(p.y for p in pts) / len(pts),
            sum(p.z for p in pts) / len(pts),
        )
        axis = cls._revolution_axis(pts)
        # An orthonormal frame perpendicular to the axis, for angular position.
        ref = (
            om.MVector(0, 1, 0)
            if abs(axis * om.MVector(1, 0, 0)) > 0.9
            else om.MVector(1, 0, 0)
        )
        u = (ref ^ axis).normal()
        w = (axis ^ u).normal()

        # One face pass: collect each edge's adjacent face normals (a boundary
        # edge ends up with a single normal).
        edge_normals = defaultdict(list)
        pit = om.MItMeshPolygon(dag)
        while not pit.isDone():
            normal = fn.getPolygonNormal(pit.index(), om.MSpace.kWorld).normal()
            for e in pit.getEdges():
                edge_normals[e].append(normal)
            pit.next()

        thresh = math.radians(angle)
        hard, axial = [], []
        for i in range(fn.numEdges):
            a, b = fn.getEdgeVertices(i)
            pa, pb = pts[a], pts[b]
            edge = pb - pa
            length = edge.length()
            if length < 1e-9:
                continue
            normals = edge_normals.get(i, [])
            if len(normals) < 2:
                continue  # a 3D boundary is already a UV seam -- nothing to cut
            # An edge running lengthwise (parallel to the axis) is part of the
            # polygon faceting, not a real crease: on a low-poly tube the facet
            # dihedral can meet the crease threshold (an 8-sided tube facets at
            # exactly 45 deg). Route such edges to the single lengthwise column;
            # only a circumferential (ring) edge with a sharp dihedral is a
            # genuine step / cap crease. Test axial-ness first so the threshold
            # collision can't shatter the tube into per-facet shells.
            if abs(edge * axis) / length > 0.5:  # lengthwise (axial) edge
                mid = om.MVector(
                    (pa.x + pb.x) / 2, (pa.y + pb.y) / 2, (pa.z + pb.z) / 2
                )
                rel = mid - center
                axial.append((i, math.atan2(rel * w, rel * u)))
                continue
            dot = max(-1.0, min(1.0, normals[0] * normals[1]))
            if math.acos(dot) >= thresh:  # ring edge, sharp bend = step / cap
                hard.append(i)

        column = cls._pick_axial_column(axial, invert_seam)
        return [f"{name}.e[{i}]" for i in sorted(set(hard) | column)]

    @staticmethod
    def _pick_axial_column(axial, invert_seam):
        """Choose one angular column from ``[(edge_id, theta), ...]``.

        Opening one column of axial edges flattens every tubular section (the
        column crosses each band once). ``invert_seam`` lands it on the far side.
        """
        import math

        if not axial:
            return set()

        def circ(x, y):  # shortest angular distance
            return abs(((x - y + math.pi) % (2 * math.pi)) - math.pi)

        target = min(t for _, t in axial)
        if invert_seam:
            target += math.pi
        columns = sorted({round(t, 4) for _, t in axial})
        if len(columns) > 1:
            gaps = [columns[k + 1] - columns[k] for k in range(len(columns) - 1)]
            gaps.append(2 * math.pi - (columns[-1] - columns[0]))  # wrap-around gap
            window = 0.4 * min(g for g in gaps if g > 1e-6)
        else:
            window = math.radians(5)
        center = min(columns, key=lambda c: circ(c, target))
        return {i for i, t in axial if circ(t, center) <= window}

    @staticmethod
    def _torus_length_loop(mesh, sections):
        """The lengthwise edge loop of a closed (torus) tube.

        The around-ring loop has ``sections`` edges; the first edge loop that
        doesn't is the lengthwise one. Without ``sections`` (standalone use)
        fall back to the longer of two perpendicular loops.
        """
        if sections:
            for cand in range(3):
                loop = cmds.polySelect(mesh, edgeLoop=cand, ass=True) or []
                if len(cmds.ls(loop, flatten=True) or []) != int(sections):
                    return loop
            return []
        loops = [
            cmds.ls(cmds.polySelect(mesh, edgeLoop=c, ass=True) or [], flatten=True)
            or []
            for c in range(2)
        ]
        return max(loops, key=len) if any(loops) else []

    @classmethod
    def _seam_cut_one(
        cls, mesh, angle=45.0, invert_seam=False, history=True, sew=True
    ):
        """Cut the auto seams on one mesh; return whether anything was cut.

        With ``sew`` (default) any pre-existing UV cuts are sewn shut first, so
        the result's shells come only from this operation's seams rather than
        stray borders left by an earlier unwrap / manual edit.
        """
        seam = cls.get_auto_seam_edges(mesh, angle=angle, invert_seam=invert_seam)
        if not seam:
            return False
        if sew:
            cmds.polyMapSew(f"{mesh}.e[*]", constructionHistory=history)
        cmds.polyMapCut(seam, constructionHistory=history)
        return True

    @classmethod
    @CoreUtils.undoable
    def cut_cylinder_seams(
        cls, objects=None, angle=45.0, invert_seam=False, history=True, sew=True
    ):
        """Cut auto UV seams for cylinder / tube unwrapping on each mesh.

        Cuts the hard creases (cap rims + ~90 degree step rings) plus one
        lengthwise column, so each smooth section, flat step, and cap peels into
        its own UV shell. Returns the list of mesh transforms that were seamed.

        Parameters:
            objects (str/obj/list): Cylinder / tube mesh(es). If None, uses the
                current selection.
            angle (float): Crease threshold in degrees (see
                :meth:`get_auto_seam_edges`).
            invert_seam (bool): Land the lengthwise column on the opposite side.
            history (bool): Keep the ``polyMapCut`` construction history.
            sew (bool): Sew any pre-existing UV cuts shut first (default) so the
                result's shells come only from this operation's seams.
        """
        meshes = cls._cylinder_meshes(objects)
        return [
            m
            for m in meshes
            if cls._seam_cut_one(
                m, angle=angle, invert_seam=invert_seam, history=history, sew=sew
            )
        ]

    @classmethod
    def _seed_shell_uvs(cls, mesh):
        """Give every UV shell a non-degenerate seed before unfolding.

        ``u3dUnfold`` collapses a shell to a point when its incoming UVs have
        zero area (e.g. a tube carrying an axis-aligned planar projection,
        where each lengthwise band projects to a line). The seed is chosen by
        the shell's 3D character relative to the revolution axis:

        - **Tubular band** (faces wrap the axis -- normals point radially):
          a *cylindrical* projection about the axis unrolls it into a flat,
          non-folded strip. A planar projection would fold the band's front and
          back onto each other -- a single-row ring degenerates completely --
          which is exactly what u3dUnfold then collapses.
        - **Cap / flat step** (faces face along the axis): a *planar*
          projection from the world axis of its thinnest 3D extent keeps the
          full area.

        The shells must already be cut open (the lengthwise column is a UV
        border) so the cylindrical seed lands its seam on the existing cut.
        """
        import maya.api.OpenMaya as om
        from collections import defaultdict

        sel = om.MSelectionList()
        sel.add(str(mesh))
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        pts = fn.getPoints(om.MSpace.kWorld)
        axis = cls._revolution_axis(pts)
        _, shell_ids = fn.getUvShellsIds()

        faces_by_shell = defaultdict(list)
        it = om.MItMeshPolygon(dag)
        while not it.isDone():
            faces_by_shell[shell_ids[it.getUVIndex(0)]].append(it.index())
            it.next()

        axes = ("x", "y", "z")
        components = (abs(axis.x), abs(axis.y), abs(axis.z))
        axis_dir = axes[components.index(max(components))]  # dominant axis
        for faces in faces_by_shell.values():
            comps = [f"{mesh}.f[{i}]" for i in faces]
            radial = (
                sum(
                    abs(fn.getPolygonNormal(f, om.MSpace.kWorld).normal() * axis)
                    for f in faces
                )
                / len(faces)
            )
            if radial < 0.5:  # band wraps the axis -> unroll cylindrically
                cmds.polyProjection(
                    comps,
                    type="Cylindrical",
                    mapDirection=axis_dir,
                    insertBeforeDeformers=False,
                )
                continue
            vids = set()
            for f in faces:
                vids.update(fn.getPolygonVertices(f))
            xs = [pts[v].x for v in vids]
            ys = [pts[v].y for v in vids]
            zs = [pts[v].z for v in vids]
            extents = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
            cmds.polyProjection(
                comps,
                type="Planar",
                mapDirection=axes[extents.index(min(extents))],
                insertBeforeDeformers=False,
            )

    @staticmethod
    def _unflip_reversed_shells(mesh):
        """Mirror any reversed (negative-winding) UV shell back in place.

        ``u3dLayout`` mirrors shells to pack them tighter, which leaves the
        texture mirrored on those sections (the hand-authored target has none).
        Flip each reversed shell about its own UV center so its winding matches
        the rest -- in place, so the packing and 0-1 fit are preserved.
        """
        import maya.api.OpenMaya as om
        from collections import defaultdict

        sel = om.MSelectionList()
        sel.add(str(mesh))
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        us, vs = fn.getUVs()
        _, shell_ids = fn.getUvShellsIds()

        signed = defaultdict(float)
        for f in range(fn.numPolygons):
            verts = fn.getPolygonVertices(f)
            uvid = [fn.getPolygonUVid(f, i) for i in range(len(verts))]
            for i in range(len(uvid)):
                j, k = uvid[i], uvid[(i + 1) % len(uvid)]
                signed[shell_ids[uvid[0]]] += us[j] * vs[k] - us[k] * vs[j]

        shell_uvs = defaultdict(list)
        for i in range(len(us)):
            shell_uvs[shell_ids[i]].append(i)

        for shell, area in signed.items():
            if area >= 0:
                continue
            idx = shell_uvs[shell]
            center_u = (min(us[i] for i in idx) + max(us[i] for i in idx)) / 2
            cmds.polyFlipUV(
                [f"{mesh}.map[{i}]" for i in idx],
                flipType=0,  # mirror U about the shell's own center
                local=True,
                usePivot=True,
                pivotU=center_u,
                pivotV=0,
            )

    @classmethod
    @CoreUtils.undoable
    def unwrap_cylinder(
        cls,
        objects=None,
        angle=45.0,
        invert_seam=False,
        unfold=True,
        orient=True,
        map_size=4096,
        sew=True,
    ):
        """Auto-unwrap cylinder / tube / turned meshes: seam, then unfold flat.

        Cuts the auto seams (hard creases + one lengthwise column) on each mesh,
        then unfolds so every smooth section lays out as a clean strip and each
        flat step / cap as its own shell. Returns the seamed mesh transforms.

        Parameters:
            objects (str/obj/list): Cylinder / tube mesh(es). If None, uses the
                current selection.
            angle (float): Crease threshold in degrees (see
                :meth:`get_auto_seam_edges`). Default 45 cuts ~90 degree steps
                while keeping shallow chamfers merged with their neighbour.
            invert_seam (bool): Land the lengthwise column on the opposite side.
            unfold (bool): Unfold (flatten) the UVs after seaming (Unfold3D),
                then pack the shells into the 0-1 square.
            orient (bool): Orient each shell to its nearest U/V axis while
                packing.
            map_size (int): Texture size the unfold optimizes spacing against.
            sew (bool): Sew any pre-existing UV cuts shut first (default) so the
                result's shells come only from this operation's seams.
        """
        meshes = cls._cylinder_meshes(objects)
        seamed = [
            m
            for m in meshes
            if cls._seam_cut_one(m, angle=angle, invert_seam=invert_seam, sew=sew)
        ]
        if unfold and seamed:
            cmds.loadPlugin("Unfold3D.mll", quiet=True)
            pad = cls.calculate_uv_padding(map_size, normalize=True)
            # u3dLayout's cost is ~quadratic in resolution (4096 -> ~1.2s,
            # 8192 -> ~4.7s) yet the packing is pixel-identical from ~256 up --
            # shellSpacing is already normalized, so resolution only sets pack
            # precision, not the gap. Cap it well below map_size to stay fast.
            pack_res = min(map_size, 1024)
            # Unfold each mesh on its own: a mesh u3dUnfold rejects (e.g. one
            # with "non-manifold UVs") then only skips itself -- a single batched
            # unfold would abort the whole selection on the first bad mesh.
            for m in seamed:
                try:
                    # Seed each cut shell with a non-degenerate projection (bands
                    # cylindrical, caps planar) so u3dUnfold neither collapses a
                    # zero-area shell nor folds a tubular band onto itself.
                    cls._seed_shell_uvs(m)
                    muvs = cmds.polyListComponentConversion(m, toUV=True) or []
                    cmds.u3dUnfold(
                        muvs,
                        iterations=1,
                        pack=0,
                        borderintersection=1,
                        triangleflip=1,
                        mapsize=map_size,
                        roomspace=0,
                    )
                    # Pack the shells into 0-1 without overlap. u3dLayout (not
                    # polyLayoutUV, which collapses cylindrically-seeded shells)
                    # packs; scaling by 3D area can overrun the square, so
                    # polyNormalizeUV collectively fits it back; then any shell
                    # u3dLayout mirrored to pack tighter is flipped back. The UV
                    # pipeline stays construction history (a consistent chain --
                    # u3dUnfold emits a polyTweakUV), so the caller's modeling /
                    # deformer history is left intact rather than baked away.
                    cmds.u3dLayout(
                        muvs,
                        resolution=pack_res,
                        shellSpacing=pad,
                        tileMargin=pad / 2,
                        preScaleMode=1,
                        preRotateMode=1 if orient else 0,
                        packBox=[0, 1, 0, 1],
                    )
                    cmds.polyNormalizeUV(
                        muvs, normalizeType=1, preserveAspectRatio=True
                    )
                    cls._unflip_reversed_shells(m)
                except Exception as error:  # plugin missing / non-unfoldable mesh
                    cmds.warning(
                        f"unwrap_cylinder: unfold skipped for {m} ({error})."
                    )
        return seamed

    @staticmethod
    def _cylinder_meshes(objects):
        """Resolve *objects* (or the selection) to a list of mesh transforms."""
        if objects is None:
            objects = cmds.ls(selection=True) or []
        shapes = (
            cmds.ls(
                as_strings(objects),
                dag=True,
                type="mesh",
                noIntermediate=True,
                long=True,
            )
            or []
        )
        meshes = []
        for s in shapes:
            parent = cmds.listRelatives(s, parent=True, fullPath=True)
            t = parent[0] if parent else s
            if t not in meshes:
                meshes.append(t)
        return meshes

    @staticmethod
    def get_texel_density(objects, map_size):
        """Calculate the texel density for the given objects' faces.

        Parameters:
            objects (str, obj, list): List of mesh objects or a single mesh object to calculate texel density for.
            map_size (int): Size of the map to calculate the texel density against.

        Returns (float):
            The texel density.
        """
        from math import sqrt

        area_3d_sum = 0.0
        area_uv_sum = 0.0

        # Convert objects to faces if they are not already
        if not isinstance(objects, list):
            objects = [objects]
        faces = cmds.polyListComponentConversion(objects, toFace=True) or []
        faces = cmds.filterExpand(
            faces, ex=True, sm=34
        )  # Now this will work, as faces are passed

        if not faces:
            cmds.warning("No faces found in the input objects.")
            return 0

        # Calculate 3D and UV areas
        for f in faces:
            world_face_area = cmds.polyEvaluate(f, worldFaceArea=True)
            uv_face_area = cmds.polyEvaluate(f, uvFaceArea=True)
            if (
                world_face_area and uv_face_area
            ):  # Check if the area lists are not empty
                area_3d_sum += world_face_area[0]
                area_uv_sum += uv_face_area[0]

        # Avoid division by zero
        if area_3d_sum == 0 or area_uv_sum == 0:
            cmds.warning("Cannot calculate texel density with zero area.")
            return 0

        # Calculate texel density
        texel_density = (sqrt(area_uv_sum) / sqrt(area_3d_sum)) * map_size
        return texel_density

    @classmethod
    @CoreUtils.undoable
    def set_texel_density(cls, objects=None, density=1.0, map_size=4096):
        """Set the texel density for the given objects.

        Parameters:
            objects (str, obj, list): List of objects or a single object to set texel density for.
                If None, the currently selected objects will be used.
            density (float): The desired texel density.
            map_size (int): Size of the map to calculate the texel density against.
        """
        # Get UV shell sets
        shells = cls.get_uv_shell_sets(
            objects or (cmds.ls(selection=True) or []), returned_type="shell"
        )

        for shell_faces in shells:
            # Convert face list to UVs
            shell_uvs = cmds.polyListComponentConversion(shell_faces, toUV=True) or []
            shell_uvs = cmds.ls(shell_uvs, flatten=True) or []  # Flatten the list of UVs

            # Calculate current density and scaling factor
            current_density = cls.get_texel_density(shell_faces, map_size)
            if current_density == 0:
                cmds.warning(
                    f"Cannot set texel density for UV shell with zero area: {shell_faces}"
                )
                continue  # Skip this shell and continue with the next one

            scale = density / current_density

            # Calculate bounding box center for UVs
            bc = cmds.polyEvaluate(shell_uvs, bc2=True)
            pU = (bc[0][0] + bc[1][0]) / 2
            pV = (bc[0][1] + bc[1][1]) / 2

            # Scale UVs
            cmds.polyEditUV(shell_uvs, pu=pU, pv=pV, su=scale, sv=scale)

    @staticmethod
    def _copy_uv_set_in_place(shape: str, source_set: str, dest_set: str) -> None:
        """Overwrite ``dest_set`` with the UVs from ``source_set`` on the same mesh.

        Uses ``cmds.polyCopyUV`` over all faces -- ``polyUVSet -copy`` only
        reliably populates a new set, and is brittle for re-populating an
        existing set after a destructive op.
        """
        face_count = cmds.polyEvaluate(shape, face=True)
        if not isinstance(face_count, int) or face_count <= 0:
            return
        cmds.polyCopyUV(
            f"{shape}.f[0:{face_count - 1}]",
            uvSetNameInput=source_set,
            uvSetName=dest_set,
            createNewMap=False,
            constructionHistory=False,
        )

    @staticmethod
    @CoreUtils.undoable
    def snapshot_uv_sets(
        objects: Sequence[Union[str, object]], prefix: str = "_uv_snap"
    ) -> List[UvSnapshot]:
        """Copy each object's active UV set into a uniquely-named backup set.

        Returns a list of ``(shape, original_set, snapshot_set)`` tuples
        that can be passed to ``restore_uv_snapshot`` or ``discard_uv_snapshot``.

        Pairs naturally with destructive UV ops (rizom bridge, auto-unwrap,
        ...) to give users an explicit "revert" path that survives the
        undo queue.

        Parameters:
            objects: Transforms or shapes to snapshot.
            prefix: Base name for the snapshot set; a short hex token is
                appended so multiple calls don't collide.
        """
        token = uuid.uuid4().hex[:8]
        snapshots: List[UvSnapshot] = []
        for obj in objects:
            shape = NodeUtils.get_shape_node(obj, returned_type="str")
            if isinstance(shape, list):
                shape = shape[0] if shape else None
            if not shape:
                continue
            shape = str(shape)
            current_list = cmds.polyUVSet(shape, query=True, currentUVSet=True) or []
            if not current_list:
                continue
            current = current_list[0]
            # Ensure the snapshot name is unique on this shape.
            existing = set(cmds.polyUVSet(shape, query=True, allUVSets=True) or [])
            candidate = f"{prefix}_{token}"
            n = 1
            while candidate in existing:
                candidate = f"{prefix}_{token}_{n}"
                n += 1
            # Create the set then explicitly populate it. `polyUVSet -copy`
            # alone leaves the new set empty on some Maya builds.
            cmds.polyUVSet(shape, create=True, uvSet=candidate)
            UvUtils._copy_uv_set_in_place(shape, current, candidate)
            snapshots.append((shape, current, candidate))
        return snapshots

    @staticmethod
    @CoreUtils.undoable
    def restore_uv_snapshot(snapshots: Sequence[UvSnapshot]) -> None:
        """Restore UVs captured by ``snapshot_uv_sets``.

        Copies the snapshot's UVs back into the original set, then
        deletes the snapshot. We can't delete-and-rename instead because
        ``polyUVSet -delete`` refuses to remove the default ``map1`` set.
        """
        for shape, original_set, snap_set in snapshots:
            if not cmds.objExists(shape):
                continue
            all_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            if snap_set not in all_sets:
                continue
            if snap_set == original_set:
                continue
            if original_set in all_sets:
                UvUtils._copy_uv_set_in_place(shape, snap_set, original_set)
            cmds.polyUVSet(shape, currentUVSet=True, uvSet=original_set)
            cmds.polyUVSet(shape, delete=True, uvSet=snap_set)

    @staticmethod
    @CoreUtils.undoable
    def discard_uv_snapshot(snapshots: Sequence[UvSnapshot]) -> None:
        """Delete the snapshot UV sets without restoring them.

        Call after a destructive UV op succeeds and the user has signaled
        they're committing to the result.
        """
        for shape, _original_set, snap_set in snapshots:
            if not cmds.objExists(shape):
                continue
            all_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            if snap_set in all_sets:
                cmds.polyUVSet(shape, delete=True, uvSet=snap_set)

    @staticmethod
    @CoreUtils.undoable
    def transfer_uvs(
        source: Union[str, object, List[Union[str, object]]],
        target: Union[str, object, List[Union[str, object]]],
        tolerance: float = 0.1,
    ) -> None:
        """Transfers UVs from source meshes to target meshes based on geometric similarity. This method is
        topology-agnostic and can work with different mesh structures.

        Parameters:
            source (Union[str, object, List]): The source mesh(es) from which to transfer UVs.
            target (Union[str, object, List]): The target mesh(es) to which UVs will be transferred.
            tolerance (float): The geometric similarity tolerance. Defaults to 0.1.
        """
        mapping = CoreUtils.build_mesh_similarity_mapping(source, target, tolerance)
        for source_name, target_name in mapping.items():
            cmds.transferAttributes(
                source_name,
                target_name,
                transferPositions=False,
                transferNormals=False,
                transferUVs=2,
                transferColors=0,
                sampleSpace=4,
                sourceUvSpace="map1",
                targetUvSpace="map1",
                searchMethod=3,
                flipUVs=False,
                colorBorders=True,
            )
            cmds.delete(target_name, ch=True)  # Clean up history on target

    @staticmethod
    def reorder_uv_sets(obj: str, new_order: list[str]) -> None:
        """Reorder UV sets of the given object to match the specified new order.
        This method will raise a ValueError if the new order does not match the existing UV sets.

        Parameters:
            obj (str): The object whose UV sets will be reordered.
            new_order (list[str]): The desired order of UV sets.
        """
        # Get shape node
        try:
            shape = NodeUtils.get_shape_node(obj, returned_type="obj")
            if isinstance(shape, list) and len(shape) > 0:
                shape = shape[0]
        except Exception:
            shapes = cmds.listRelatives(str(obj), shapes=True, fullPath=True) or []
            shape = shapes[0] if shapes else obj
        shape = str(shape)
        existing = cmds.polyUVSet(shape, query=True, allUVSets=True) or []

        if set(existing) != set(new_order):
            raise ValueError("new_order must match the set of existing UV sets")

        for i in range(1, len(new_order)):
            current = new_order[i]
            insert_after = new_order[i - 1]

            # Only reorder if order is incorrect
            if existing.index(current) < existing.index(insert_after):
                cmds.polyUVSet(shape, reorder=True, uvSet=current, newUVSet=insert_after)
                existing = cmds.polyUVSet(shape, query=True, allUVSets=True) or []

    @classmethod
    @CoreUtils.undoable
    def create_lightmap_uvs(
        cls,
        objects,
        uv_set: str = None,
        map_size: int = 1024,
        planes: int = 6,
        force: bool = False,
        freeze_history: bool = False,
        quiet: bool = False,
    ) -> dict:
        """Ensure each mesh has a packed, non-overlapping lightmap UV set.

        Native (``polyAutoProjection``) -- no RizomUV dependency. For each mesh:
        a *valid* existing lightmap (non-overlapping, within 0-1) is reused
        unless ``force``; otherwise a new set is auto-projected and packed into
        the unit square with gutter padding (:meth:`calculate_uv_padding`),
        placed at UV channel index 1 (the lightmap channel engines bind), and
        tagged on the shape (``UvDiagnostics.LIGHTMAP_UV_TAG``) so downstream
        tools detect it unambiguously and cleanup never deletes it.

        Parameters:
            objects (str/obj/list): Meshes / transforms to process.
            uv_set (str): Lightmap set name. Default ``LIGHTMAP_UV_SET``.
            map_size (int): Target lightmap resolution (drives gutter padding).
            planes (int): ``polyAutoProjection`` planes (6 = axis-aligned box).
            force (bool): Regenerate even if a valid lightmap set is present.
            freeze_history (bool): If True, bake the projection and delete
                construction history (final baked lightmap UVs, no live unwrap
                history) -- appropriate for export-bound meshes. Default False
                preserves modeling history.
            quiet (bool): Suppress logging.

        Returns:
            dict: ``{shape: {"uv_set": str, "created": bool, "reused": bool}}``.
        """
        from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics

        uv_set = uv_set or UvDiagnostics.LIGHTMAP_UV_SET
        # Normalized padding is map-size-independent (~0.39%); as a percentage
        # for polyAutoProjection's percentageSpace gutter.
        pct = cls.calculate_uv_padding(map_size, normalize=True) * 100.0

        results: dict = {}
        for obj in NodeUtils.get_transform_node(objects):
            obj = str(obj)
            shape = NodeUtils.get_shape_node(obj, returned_type="obj")
            if isinstance(shape, list):
                shape = shape[0] if shape else None
            if not shape:
                continue
            shape = str(shape)
            if not cmds.attributeQuery("uvSet", node=shape, exists=True):
                continue

            prev_current = (
                cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None]
            )[0]

            # Reuse an existing, valid lightmap unless forced.
            existing_lm = UvDiagnostics.find_lightmap_uv_set(shape)
            if (
                existing_lm
                and not force
                and UvDiagnostics.is_bakeable_lightmap(shape, existing_lm)
            ):
                if prev_current:
                    cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev_current)
                results[shape] = {
                    "uv_set": existing_lm,
                    "created": False,
                    "reused": True,
                }
                continue

            pre = list(
                dict.fromkeys(cmds.polyUVSet(shape, query=True, allUVSets=True) or [])
            )
            primary = pre[0] if pre else "map1"

            if uv_set not in pre:
                cmds.polyUVSet(shape, create=True, uvSet=uv_set)
            cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
            cmds.polyAutoProjection(
                f"{shape}.f[*]",
                layoutMethod=0,
                layout=2,  # pack into the unit (0-1) square
                optimize=1,
                planes=planes,
                percentageSpace=pct,
                createNewMap=False,
            )

            if freeze_history:
                # Bake the projection into the mesh and drop its construction
                # history -- final baked lightmap UVs with no live unwrap node,
                # for export-bound static meshes.
                cmds.delete(obj, constructionHistory=True)

            # Place the texture set at channel 0 and the lightmap at channel 1
            # (the index engines bind), keeping any other sets after it;
            # polyAutoProjection can leave the projected set at index 0.
            order = [primary, uv_set] + [s for s in pre if s not in (primary, uv_set)]
            cls.reorder_uv_sets(shape, order)

            # Tag the shape so downstream detection is unambiguous.
            if not cmds.attributeQuery(
                UvDiagnostics.LIGHTMAP_UV_TAG, node=shape, exists=True
            ):
                cmds.addAttr(
                    shape, longName=UvDiagnostics.LIGHTMAP_UV_TAG, dataType="string"
                )
            cmds.setAttr(
                f"{shape}.{UvDiagnostics.LIGHTMAP_UV_TAG}", uv_set, type="string"
            )

            # Restore the previously-current set (default to the texture primary).
            all_now = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            restore = prev_current if prev_current in all_now else primary
            if restore in all_now:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=restore)

            results[shape] = {"uv_set": uv_set, "created": True, "reused": False}
            if not quiet:
                print(f"[lightmap-uv] {shape}: {results[shape]}")
        return results

    @staticmethod
    @CoreUtils.undoable
    def remove_empty_uv_sets(objects, quiet: bool = False) -> None:
        """Remove empty UV sets from the given objects.

        Parameters:
            objects (str/obj/list): Polygon objects or components to check for empty UV sets.
            quiet (bool): If True, suppress output messages.
        """
        objects = NodeUtils.get_transform_node(objects)

        for obj in objects:
            # Get shape node
            try:
                shape = NodeUtils.get_shape_node(obj, returned_type="obj")
                if isinstance(shape, list) and len(shape) > 0:
                    shape = shape[0]
            except Exception:
                shapes = cmds.listRelatives(str(obj), shapes=True, fullPath=True) or []
                shape = shapes[0] if shapes else None
            if shape is None:
                continue
            shape = str(shape)
            if not cmds.attributeQuery("uvSet", node=shape, exists=True):
                continue

            deleted: list[str] = []
            all_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            current = cmds.polyUVSet(shape, query=True, currentUVSet=True)

            for uv_set in list(all_sets):
                try:
                    cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
                    uv_count = cmds.polyEvaluate(shape, uvcoord=True)
                    if uv_count > 0:
                        continue

                    index = all_sets.index(uv_set)
                    if index == 0 and len(all_sets) > 1:
                        cmds.polyUVSet(
                            shape, reorder=True, uvSet=all_sets[1], newUVSet=uv_set
                        )
                        all_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []

                    if uv_set == current:
                        fallback = next((s for s in all_sets if s != uv_set), None)
                        if fallback:
                            cmds.polyUVSet(shape, currentUVSet=True, uvSet=fallback)

                    cmds.polyUVSet(shape, delete=True, uvSet=uv_set)
                    deleted.append(uv_set)

                except RuntimeError:
                    continue

            remaining = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            if current in remaining:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=current)

            if deleted and not quiet:
                print(
                    f"{shape}: removed empty UV sets: {deleted} | remaining: {remaining}"
                )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
