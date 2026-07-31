# !/usr/bin/python
# coding=utf-8
import uuid
from typing import List, Optional, Sequence, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components
from mayatk.node_utils._node_utils import NodeUtils


UvSnapshot = Tuple[str, str, str]  # (shape_path, original_set_name, snapshot_set_name)


class UvUtils(ptk.HelpMixin):
    @staticmethod
    def calculate_uv_padding(
        map_size: int, normalize: bool = False, factor: int = 256
    ) -> float:
        """The texture gutter for a given map size — Maya-side name for the ecosystem rule.

        Delegates to :meth:`pythontk.MathUtils.calculate_uv_padding` (the primitive is
        pure ratio math, so it lives at the bottom of the stack and blendertk mirrors
        this same name over the same rule). Every packer here feeds from it — the
        ``u3dLayout`` ``shellSpacing`` / ``tileMargin`` pair and the RizomUV bridge's
        derived ``ZomPack`` gutter — so a map packed one way keeps its gutter when
        repacked another.

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
        return ptk.MathUtils.calculate_uv_padding(
            map_size, normalize=normalize, factor=factor
        )

    @staticmethod
    def udim_to_tile(udim: int) -> Tuple[int, int]:
        """UDIM tile number to its (u, v) tile offset — Maya-side name for the
        ecosystem rule.

        Delegates to :meth:`pythontk.MathUtils.udim_to_tile` (pure integer
        math, so it lives at the bottom of the stack). Every packer here
        anchors from it — the Pack tool's ``u3dLayout`` pack box and the
        xatlas path's placement — so a tile means the same thing everywhere.

        Parameters:
            udim (int): UDIM tile number, e.g. 1001.

        Returns:
            tuple: ``(u, v)`` integer tile offsets (the tile's bottom-left
            corner in UV units).
        """
        return ptk.MathUtils.udim_to_tile(udim)

    @staticmethod
    def orient_shells(objects):
        """Rotate UV shells to run parallel with the most adjacent U or V axis of their bounding box.

        Parameters:
            objects (str/obj/list): Polygon mesh objects and/or components.
        """

        objects = CoreUtils.as_strings(objects)
        for obj in cmds.ls(objects, objectsOnly=True, long=True) or []:
            # filter components for only this object.
            obj_compts = [
                i
                for i in objects
                if obj in (cmds.ls(i, objectsOnly=True, long=True) or [])
            ]
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

        objects = CoreUtils.as_strings(objects)
        # Convert the objects to UVs. No `from*` filter, so any input type
        # resolves — a `fromFace` filter silently drops a UV/edge/vertex
        # selection, which made the move a no-op for those (and would desync
        # the caller's bounds from what actually moves).
        uvs = cmds.polyListComponentConversion(objects, toUV=True) or []
        uvs = cmds.ls(uvs, flatten=True) or []

        # Move the UVs to the given u and v coordinates
        cmds.polyEditUV(uvs, u=u, v=v, relative=relative)

    @staticmethod
    def get_uv_bounds(objects) -> Optional[Tuple[float, float, float, float]]:
        """The UV-space bounding box of *objects*, as one box over the whole input.

        Parameters:
            objects (str/obj/list): Object(s), faces, or UVs.

        Returns:
            tuple: ``(u_min, v_min, u_max, v_max)``, or None when the input
            resolves to no UVs.
        """
        objects = CoreUtils.as_strings(objects)
        # Deliberately NOT flattened: polyEvaluate reads ranged components fine,
        # and flattening would materialize one string per UV — the move pad calls
        # this on every arrow press.
        uvs = cmds.polyListComponentConversion(objects, toUV=True) or []
        if not uvs:
            return None

        # polyEvaluate returns ((u_min, u_max), (v_min, v_max)), unioned over
        # every object in the input.
        (u_min, u_max), (v_min, v_max) = cmds.polyEvaluate(
            uvs, boundingBoxComponent2d=True
        )
        return (u_min, v_min, u_max, v_max)

    @classmethod
    @CoreUtils.undoable
    def gather_to_udim(
        cls,
        objects,
        udim: Optional[int] = None,
        map_size: int = 4096,
    ) -> Optional[int]:
        """Move UV shells sitting outside the target UDIM tile into it.

        The cheap alternative to a repack: each stray shell keeps its
        sub-tile position via a whole-tile translation (a shell straddling
        the tile's border gets the minimal pull-in instead), clamped inside
        the tile's border padding — the same margin the packers use
        (:meth:`pythontk.MathUtils.uv_tile_margin`). Shells already inside
        the tile do not move; overlaps with resident shells are accepted.

        Parameters:
            objects (str/obj/list): Object(s), faces, or UVs. Shells are
                resolved over the whole input.
            udim (int, optional): Target UDIM tile number (e.g. 1001).
                None targets the tile most of the input's shells already
                occupy (majority vote by shell-bbox center).
            map_size (int): Map resolution the border padding derives
                from. Default 4096.

        Returns:
            int: The number of shells moved, or None when the input
            resolves to no UVs.
        """
        shells = []  # (uvs, (u_min, v_min, u_max, v_max)) per shell
        # Whole shells, not just the input's faces: translating a face subset of
        # a shell tears it in half. Matches the Blender twin's `_target_islands`.
        for face_set in cls.get_uv_shell_sets(
            objects, returned_type="shell", whole_shells=True
        ):
            uvs = cmds.polyListComponentConversion(face_set, toUV=True) or []
            if not uvs:
                continue
            (u_min, u_max), (v_min, v_max) = cmds.polyEvaluate(
                uvs, boundingBoxComponent2d=True
            )
            shells.append((uvs, (u_min, v_min, u_max, v_max)))
        if not shells:
            return None

        tile = (
            ptk.MathUtils.majority_tile(b for _, b in shells)
            if udim is None
            else ptk.MathUtils.udim_to_tile(udim)
        )

        margin = ptk.MathUtils.uv_tile_margin(map_size)
        moved = 0
        cmds.refresh(suspend=True)  # one edit per shell adds up on a dense scene
        try:
            for uvs, bounds in shells:
                du, dv = ptk.MathUtils.fit_into_tile(bounds, tile, margin)
                if du or dv:
                    cmds.polyEditUV(uvs, u=du, v=dv, relative=True)
                    moved += 1
        finally:
            cmds.refresh(suspend=False)
        return moved

    @staticmethod
    def get_neighbor_shell_bounds(objects) -> List[Tuple[float, float, float, float]]:
        """Per-shell UV boxes that share *objects*' UV space, excluding their own.

        The pool is every non-intermediate mesh whose **current UV set name**
        matches the input's — that name is what makes two meshes share one UV
        space, so a mesh sitting in a different set (a lightmap channel, say)
        is not a neighbour even though its UVs occupy the same numbers.
        Visibility is deliberately not consulted: a hidden mesh still owns its
        place in the layout.

        Shells are identified by API shell id rather than by bounding box,
        because stacked shells share a box and would exclude each other.

        Parameters:
            objects (str/obj/list): Object(s), faces, or UVs — the shells that
                are about to move.

        Returns:
            list: ``(u_min, v_min, u_max, v_max)`` per neighbouring shell,
            unordered. Empty when the input is alone in its UV set.
        """
        import maya.api.OpenMaya as om

        objects = CoreUtils.as_strings(objects)
        components = cmds.polyListComponentConversion(objects, toUV=True) or []
        if not components:
            return []

        # Keyed on the shape's MObject, never its path: an instanced shape has
        # one path per instance, and `cmds.ls` below reports only the first, so
        # path keys would miss a selection made through any other instance and
        # leak the input's own shells back as neighbours.
        def shape_key(dag_path):
            return om.MObjectHandle(dag_path.node()).hashCode()

        # Ranged components go into the selection list whole — no flatten, so
        # this stays cheap on dense meshes.
        selection = om.MSelectionList()
        for component in components:
            selection.add(component)

        uv_sets, own = set(), {}
        for i in range(selection.length()):
            dag_path, component = selection.getComponent(i)
            mesh = om.MFnMesh(dag_path)
            uv_set = mesh.currentUVSetName()
            uv_sets.add(uv_set)
            _, shell_ids = mesh.getUvShellsIds(uv_set)
            elements = (
                range(len(shell_ids))
                if component.isNull()
                else om.MFnSingleIndexedComponent(component).getElements()
            )
            own.setdefault(shape_key(dag_path), set()).update(
                shell_ids[e] for e in elements if e < len(shell_ids)
            )

        boxes = []
        for shape in cmds.ls(type="mesh", noIntermediate=True, long=True) or []:
            shape_list = om.MSelectionList()
            shape_list.add(shape)
            dag_path = shape_list.getDagPath(0)
            mesh = om.MFnMesh(dag_path)
            uv_set = mesh.currentUVSetName()
            if uv_set not in uv_sets:
                continue
            _, shell_ids = mesh.getUvShellsIds(uv_set)
            if not len(shell_ids):
                continue
            us, vs = mesh.getUVs(uv_set)
            skip = own.get(shape_key(dag_path), ())

            per_shell = {}
            for index, shell_id in enumerate(shell_ids):
                if shell_id in skip:
                    continue
                u, v = us[index], vs[index]
                box = per_shell.get(shell_id)
                per_shell[shell_id] = (
                    (min(box[0], u), min(box[1], v), max(box[2], u), max(box[3], v))
                    if box
                    else (u, v, u, v)
                )
            boxes.extend(per_shell.values())
        return boxes

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
    def get_uv_shell_sets(objects=None, returned_type="shell", whole_shells=False):
        """Get UV shells and their corresponding sets of faces.

        Optimized to use the Maya API (OpenMaya) for performance and reliability,
        avoiding selection changes.

        Parameters:
            objects (obj/list): Polygon object(s) or Polygon face(s). If None,
                uses the current selection.
            returned_type (str): The desired returned type. Valid values are:
                'shell', 'id'.
            whole_shells (bool): Expand each shell the input *touches* to all of
                its faces, instead of returning only the input's own faces
                grouped by shell. A shell-level op (gather, orient) needs this —
                translating a face subset of a shell tears it in half — while a
                face-level op wants the default. Expanded faces come back as
                path strings rather than the caller's component objects.

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

                if (
                    whole_shells
                    and local_shells
                    and len(shape_faces) < mfn_mesh.numPolygons
                ):
                    # Re-walk the mesh once, keeping every face whose shell the
                    # input touched — the input's own faces are only a probe.
                    # Skipped when the input already covers the mesh (the common
                    # whole-object case), where the grouping is complete already.
                    touched = set(local_shells)
                    local_shells = {sid: [] for sid in touched}
                    for face_idx in range(mfn_mesh.numPolygons):
                        try:
                            if mfn_mesh.polygonVertexCount(face_idx) <= 0:
                                continue
                            sid = uv_shell_ids[
                                mfn_mesh.getPolygonUVid(face_idx, 0, current_uv_set)
                            ]
                        except Exception:  # face has no UVs projected
                            continue
                        if sid in touched:
                            local_shells[sid].append(f"{shape_str}.f[{face_idx}]")

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

        objects = CoreUtils.as_strings(objects)
        uv_border_edges = []
        for obj in cmds.ls(objects, long=True) or []:
            obj_str = str(obj)
            # Resolve transform to its shape
            if "." not in obj_str:
                try:
                    shapes = (
                        cmds.listRelatives(obj_str, shapes=True, fullPath=True) or []
                    )
                    if shapes:
                        obj_str = shapes[0]
                except Exception:
                    pass

            # Determine component or node type and get connected edges
            if "." not in obj_str and cmds.objectType(obj_str) == "mesh":
                # Mesh shape — get UV border edges
                connected_edges = (
                    cmds.polyListComponentConversion(obj_str, fromUV=True, toEdge=True)
                    or []
                )
                connected_edges = cmds.ls(connected_edges, flatten=True) or []
            elif ".e[" in obj_str:
                # Edge component — already an edge
                connected_edges = cmds.ls(obj_str, flatten=True) or []
            elif ".map[" in obj_str or ".uv[" in obj_str:
                # UV component — convert to edges
                connected_edges = (
                    cmds.polyListComponentConversion(obj_str, fromUV=True, toEdge=True)
                    or []
                )
                connected_edges = cmds.ls(connected_edges, flatten=True) or []
            else:
                raise ValueError(f"Unsupported object type: {obj_str}")

            for edge in connected_edges:
                edge_uvs = (
                    cmds.ls(
                        cmds.polyListComponentConversion(edge, tuv=True) or [], fl=True
                    )
                    or []
                )
                edge_faces = (
                    cmds.ls(
                        cmds.polyListComponentConversion(edge, tf=True) or [], fl=True
                    )
                    or []
                )
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

    @staticmethod
    def _shortest_edge_path(starts, targets, edge_ids, ev, pts):
        """Shortest vertex-to-vertex path along the given edges (Dijkstra).

        Parameters:
            starts (set): Source vertex ids (multi-source).
            targets (set): Destination vertex ids; the search stops at the
                first one reached.
            edge_ids (iterable): Edge ids the path may travel along.
            ev (list): ``edge id -> (vertex a, vertex b)`` table.
            pts (MPointArray): Vertex positions (for edge-length weights).

        Returns:
            (list) edge ids of the path, or ``None`` when no target is
            reachable through ``edge_ids``.
        """
        import heapq
        from collections import defaultdict

        adj = defaultdict(list)
        for e in edge_ids:
            a, b = ev[e]
            w = (pts[a] - pts[b]).length()
            adj[a].append((b, e, w))
            adj[b].append((a, e, w))

        dist = {v: 0.0 for v in starts}
        prev = {}
        heap = [(0.0, v) for v in starts]
        heapq.heapify(heap)
        done = set()
        while heap:
            d, v = heapq.heappop(heap)
            if v in done:
                continue
            done.add(v)
            if v in targets:
                path = []
                while v in prev:
                    v, e = prev[v]
                    path.append(e)
                return path
            for nv, e, w in adj[v]:
                nd = d + w
                if nd < dist.get(nv, float("inf")):
                    dist[nv] = nd
                    prev[nv] = (v, e)
                    heapq.heappush(heap, (nd, nv))
        return None

    @classmethod
    def get_topology_seam_edges(cls, mesh, angle: float = 45.0, invert_seam=False):
        """Seam edges from smooth-region topology — no global axis assumed.

        Advanced alternative to :meth:`get_auto_seam_edges` for shapes that
        are *not* clean bodies of revolution: bent / swept tubes (elbows,
        pipes), toruses, and turned forms whose sections are offset from a
        straight axis. The mesh is segmented into smooth regions, and each
        region contributes exactly the cuts its own surface topology needs:

        - **Region borders** — edges where two regions meet — are cut, like
          the hard creases of the axis algorithm. When the mesh carries
          authored hard/soft shading the hard flags define the regions
          (coplanar hard edges are ignored, so triangulated flat caps don't
          shatter). Otherwise the ``angle`` dihedral threshold applies, but
          only to *ring-direction* edges — those chained across quads from a
          boundary rim or a cap border — so the sweep-direction faceting of
          a coarse tube can't shatter the band (a facet edge is cut only
          past an unambiguous ~60 degree cap).
        - **Tube regions** (two or more boundary loops) are opened with one
          lengthwise cut per extra boundary. The cut prefers a topological
          edge-loop walk (a straight seam that follows the surface, so bent
          tubes seam cleanly) and falls back to the shortest edge path.
        - **Closed regions** (no boundary at all) are opened with one edge
          loop — plus the crossing edge ring when torus-like — so a torus
          body unrolls to a single rectangle and a sphere splits in two.
        - **Disk regions** (caps, flat steps) need no opening cut.

        3D boundary edges (an open tube's rims) are already UV borders and
        are never cut. Returns a flat list of edge component strings.

        Parameters:
            mesh (str): A polygon transform or shape.
            angle (float): Crease threshold in degrees; used only when the
                mesh has no authored hard edges (all-soft / all-hard
                imports). Default 45.
            invert_seam (bool): Start the main lengthwise cut on the far
                side of its boundary loop.
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

        # --- adjacency + face normals (one polygon pass) -------------------
        edge_faces = defaultdict(list)
        face_edges = {}  # face -> edge ids in face order (for quad opposites)
        normals = {}
        pit = om.MItMeshPolygon(dag)
        while not pit.isDone():
            f = pit.index()
            normals[f] = fn.getPolygonNormal(f, om.MSpace.kWorld).normal()
            edges = list(pit.getEdges())
            face_edges[f] = edges
            for e in edges:
                edge_faces[e].append(f)
            pit.next()

        smooth = {}
        eit = om.MItMeshEdge(dag)
        while not eit.isDone():
            smooth[eit.index()] = eit.isSmooth
            eit.next()
        has_soft = any(smooth.values())

        ev = [fn.getEdgeVertices(e) for e in range(fn.numEdges)]

        # --- ring-direction edges (the crease-eligible set) ----------------
        # A smooth surface facets, and at coarse tessellation a facet's
        # dihedral reaches ordinary crease thresholds (a torus with 8 section
        # divisions facets past 45 degrees — the default angle), so a raw
        # dihedral test would shatter the band. Mirror the axis algorithm's
        # axial-edge exemption topologically: only edges running in the RING
        # direction — reachable via quad-opposite-edge chains from a 3D
        # boundary rim or a non-quad (cap) face's border — are candidate
        # creases at the user threshold. Sweep-direction facet edges never
        # chain from those seeds, so they stay exempt unless unambiguously
        # sharp (the hard cap below, for boxy all-quad closed shapes whose
        # creases have no rim/cap seed at all). Only consulted by the
        # dihedral fallback — authored shading defines its own regions.
        ring_dir = set()
        if not has_soft:
            stack = []
            for e, faces in edge_faces.items():
                if len(faces) == 1:  # boundary rim
                    stack.append(e)
                elif any(len(face_edges[f]) != 4 for f in faces):  # cap border
                    stack.append(e)
            ring_dir.update(stack)
            while stack:
                e = stack.pop()
                for f in edge_faces[e]:
                    edges = face_edges[f]
                    if len(edges) != 4:
                        continue
                    opp = edges[(edges.index(e) + 2) % 4]
                    if opp not in ring_dir:
                        ring_dir.add(opp)
                        stack.append(opp)

        # --- classify creases; region-grow faces across the soft edges -----
        thresh = math.radians(angle)
        hard_cap = max(thresh, math.radians(60.0))  # unambiguous crease
        coplanar_eps = math.radians(0.5)  # authored-hard but flat: not a crease
        parent = list(range(fn.numPolygons))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        crease = set()
        for e in range(fn.numEdges):
            faces = edge_faces.get(e, [])
            if len(faces) < 2:
                continue  # 3D boundary: already a UV border
            dot = max(-1.0, min(1.0, normals[faces[0]] * normals[faces[1]]))
            dihedral = math.acos(dot)
            if len(faces) > 2:  # non-manifold: always split
                is_crease = True
            elif has_soft:  # authored shading defines the regions
                is_crease = not smooth[e] and dihedral >= coplanar_eps
            elif e in ring_dir:
                is_crease = dihedral >= thresh
            else:  # sweep-direction facet: exempt unless unambiguously sharp
                is_crease = dihedral >= hard_cap
            if is_crease:
                crease.add(e)
            else:
                ra, rb = find(faces[0]), find(faces[1])
                if ra != rb:
                    parent[ra] = rb

        regions = defaultdict(list)
        for f in range(fn.numPolygons):
            regions[find(f)].append(f)

        cuts = set(crease)

        # --- per-region opening cuts ---------------------------------------
        # Iterate deterministically (by lowest face id) so repeat runs cut
        # the identical seam set.
        first_region = True
        for faces in sorted(regions.values(), key=min):
            in_region = set(faces)
            # Built from the region's own faces (not a scan of every mesh
            # edge per region, which would go quadratic on shattered meshes).
            region_edges = set()
            for f in faces:
                region_edges.update(face_edges[f])
            interior = {
                e
                for e in region_edges
                if e not in crease
                and len(edge_faces[e]) == 2
                and all(f in in_region for f in edge_faces[e])
            }
            border = region_edges - interior
            verts = set()
            for f in faces:
                verts.update(fn.getPolygonVertices(f))
            euler = len(verts) - len(region_edges) + len(faces)

            # Boundary loops: connected components of the border edges.
            border_adj = defaultdict(set)
            for e in border:
                for v in ev[e]:
                    border_adj[v].add(e)
            unvisited = set(border)
            loops = []  # [(vertex ids, edge ids), ...]
            while unvisited:
                e0 = unvisited.pop()
                comp, stack = {e0}, [e0]
                lverts = set(ev[e0])
                while stack:
                    e = stack.pop()
                    for v in ev[e]:
                        for ne in border_adj[v]:
                            if ne in unvisited:
                                unvisited.remove(ne)
                                comp.add(ne)
                                stack.append(ne)
                                lverts.update(ev[ne])
                loops.append((lverts, comp))
            loops.sort(key=lambda lp: min(lp[0]))

            if len(loops) == 1 and euler == 1:
                continue  # disk (cap / flat step): unfolds as-is

            if not loops:  # closed region: torus body, sphere, ...
                if not interior:
                    continue
                seed = min(interior)
                loop = cls._comp_ids(
                    cmds.polySelect(name, edgeLoop=seed, ass=True, noSelection=True)
                    or []
                ) & interior
                cuts |= loop
                if euler <= 0:  # torus-like: also the crossing ring
                    ring = cls._comp_ids(
                        cmds.polySelect(
                            name, edgeRing=seed, ass=True, noSelection=True
                        )
                        or []
                    ) & interior
                    cuts |= ring
                continue

            # Tube-like: connect the boundary loops with lengthwise cuts.
            loop_a_verts = loops[0][0]
            v0 = min(loop_a_verts)
            if invert_seam and first_region and len(loop_a_verts) > 2:
                p0 = pts[v0]
                v0 = max(loop_a_verts, key=lambda v: (pts[v] - p0).length())
            first_region = False

            connected = set(loop_a_verts)
            remaining = [set(lv) for lv, _ in loops[1:]]
            primary = True
            while remaining:
                targets = set().union(*remaining)
                path = None
                if primary:
                    # Prefer a topological edge-loop walk from v0: on quad
                    # tubes it runs a straight column even when the tube bends.
                    v0_edges = [
                        e for e in interior if v0 in ev[e]
                    ]
                    if v0_edges:
                        walk = cls._comp_ids(
                            cmds.polySelect(
                                name,
                                edgeLoop=min(v0_edges),
                                ass=True,
                                noSelection=True,
                            )
                            or []
                        ) & interior
                        path = cls._shortest_edge_path(
                            {v0}, targets, walk, ev, pts
                        )
                    if path is None:
                        path = cls._shortest_edge_path(
                            {v0}, targets, interior, ev, pts
                        )
                else:
                    path = cls._shortest_edge_path(
                        connected, targets, interior, ev, pts
                    )
                primary = False
                if path is None:  # genus cut needed, or unreachable: best effort
                    break
                cuts.update(path)
                for e in path:
                    connected.update(ev[e])
                for lv in remaining[:]:
                    if lv & connected:
                        connected |= lv
                        remaining.remove(lv)

        return [f"{name}.e[{i}]" for i in sorted(cuts)]

    # Seam-detection strategies for cylinder / tube unwrapping, keyed by the
    # ``algorithm`` parameter of the cutting entry points. ``"auto"`` picks
    # between the other two per mesh.
    SEAM_ALGORITHMS = ("auto", "axis", "topology")

    @classmethod
    def detect_seam_algorithm(cls, mesh) -> str:
        """Pick the seam strategy that suits *mesh*: ``"axis"`` or ``"topology"``.

        The axis algorithm opens a tube with a single lengthwise column about a
        fitted revolution axis. A bent tube is still a body of revolution -- an
        elbow is part of a torus -- so "is it revolved?" doesn't decide this.
        What matters is whether the tube runs *along* the axis or *around* it,
        and that shows in the two shapes an axial column can't open:

        - **Closed with a handle** (a torus): no boundary and Euler
          characteristic <= 0. One lengthwise cut leaves it still closed; it
          needs the crossing ring the topology algorithm adds.
        - **Bent / swept** (an elbow, a pipe run): its open ends don't encircle
          the fitted axis -- they sit off to the side of it -- so an axial
          column would cut across the tube instead of running down it.

        Everything else -- straight tubes, turned profiles, stepped columns,
        capped cylinders -- takes the cheaper, more predictable axis path. When
        in doubt this favours ``"axis"``; pass ``algorithm`` explicitly to
        override (a *capped* bend, having no open ends to measure, reads as
        axial).
        """
        import maya.api.OpenMaya as om
        import numpy as np
        from collections import defaultdict

        sel = om.MSelectionList()
        sel.add(str(mesh))
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)

        points = fn.getPoints(om.MSpace.kObject)
        if len(points) < 8:  # too coarse to characterize; axis is the safe default
            return "axis"

        adjacency = defaultdict(set)
        edge_it = om.MItMeshEdge(dag)
        while not edge_it.isDone():
            if edge_it.onBoundary():
                v0, v1 = edge_it.vertexId(0), edge_it.vertexId(1)
                adjacency[v0].add(v1)
                adjacency[v1].add(v0)
            edge_it.next()

        if not adjacency:
            euler = fn.numVertices - fn.numEdges + fn.numPolygons
            return "topology" if euler <= 0 else "axis"

        pts = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
        axis = cls._revolution_axis(points)
        a = np.array([axis.x, axis.y, axis.z], dtype=float)
        center = pts.mean(axis=0)

        for loop in cls._connected_groups(adjacency):
            loop_pts = pts[list(loop)]
            loop_center = loop_pts.mean(axis=0)
            # How wide the opening is, versus how far it sits off the axis.
            radius = float(np.linalg.norm(loop_pts - loop_center, axis=1).mean())
            if radius <= 1e-9:
                continue
            offset_vec = loop_center - center
            offset = float(
                np.linalg.norm(offset_vec - np.dot(offset_vec, a) * a)
            )
            if offset / radius > cls._SEAM_OFFSET_TOLERANCE:
                return "topology"
        return "axis"

    # How far an opening's centre may sit off the fitted axis, as a multiple of
    # the opening's own radius, before the mesh reads as bent rather than
    # straight. A straight tube's rings are centred on the axis (~0); an
    # elbow's sit a whole bend-radius away (>1).
    _SEAM_OFFSET_TOLERANCE = 0.5

    @staticmethod
    def _connected_groups(adjacency):
        """Group an undirected adjacency map into connected vertex sets."""
        seen = set()
        for start in adjacency:
            if start in seen:
                continue
            group, stack = set(), [start]
            seen.add(start)
            while stack:
                node = stack.pop()
                group.add(node)
                for neighbor in adjacency[node]:
                    if neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            yield group

    @classmethod
    def _seam_edges(cls, mesh, algorithm, angle, invert_seam):
        """Dispatch seam detection to the chosen algorithm."""
        if algorithm not in cls.SEAM_ALGORITHMS:
            raise ValueError(
                f"Unknown seam algorithm {algorithm!r}; expected one of "
                f"{cls.SEAM_ALGORITHMS}."
            )
        if algorithm == "auto":
            algorithm = cls.detect_seam_algorithm(mesh)
        fn = (
            cls.get_topology_seam_edges
            if algorithm == "topology"
            else cls.get_auto_seam_edges
        )
        return fn(mesh, angle=angle, invert_seam=invert_seam)

    @classmethod
    def _seam_cut_one(
        cls, mesh, angle=45.0, invert_seam=False, history=True, sew=True,
        algorithm="axis",
    ):
        """Cut the auto seams on one mesh; return whether anything was cut.

        With ``sew`` (default) any pre-existing UV cuts are sewn shut first, so
        the result's shells come only from this operation's seams rather than
        stray borders left by an earlier unwrap / manual edit.
        """
        seam = cls._seam_edges(mesh, algorithm, angle, invert_seam)
        if not seam:
            return False
        if sew:
            cmds.polyMapSew(f"{mesh}.e[*]", constructionHistory=history)
        cmds.polyMapCut(seam, constructionHistory=history)
        return True

    @classmethod
    @CoreUtils.undoable
    def cut_cylinder_seams(
        cls, objects=None, angle=45.0, invert_seam=False, history=True, sew=True,
        algorithm="auto",
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
            algorithm (str): Seam-detection strategy. ``"auto"`` (default)
                picks per mesh via :meth:`detect_seam_algorithm`. ``"axis"``
                assumes a straight revolution axis (see
                :meth:`get_auto_seam_edges`); ``"topology"`` derives the cuts
                from smooth-region topology, handling bent / swept tubes and
                toruses (see :meth:`get_topology_seam_edges`).
        """
        meshes = cls._cylinder_meshes(objects)
        return [
            m
            for m in meshes
            if cls._seam_cut_one(
                m, angle=angle, invert_seam=invert_seam, history=history, sew=sew,
                algorithm=algorithm,
            )
        ]

    @staticmethod
    @CoreUtils.undoable
    def cut_uv_edges(edges, history: bool = True):
        """Cut (split) UV shells along the given edges, spanning any number of objects.

        ``cmds.polyMapCut`` refuses a component list that spans more than one
        object ("Doesn't work with multiple objects selected"), so the edges
        are grouped per object and cut object-by-object.

        Parameters:
            edges (str/list): Edge components — any mix of objects.
            history (bool): Keep the ``polyMapCut`` construction history.

        Returns:
            (dict): ``{object: [edge, ...]}`` — the edges cut, grouped per object.
        """
        grouped = Components.map_components_to_objects(edges)
        for obj_edges in grouped.values():
            cmds.polyMapCut(obj_edges, constructionHistory=history)
        return grouped

    @classmethod
    def auto_unwrap(
        cls,
        objects=None,
        method: str = "hard",
        map_size: int = 4096,
        pack: Optional[bool] = None,
        orient: bool = True,
        engine_params: Optional[dict] = None,
    ):
        """Automatically unwrap meshes with an external unwrapping engine.

        A true alternative to Maya's built-in auto projection, which is the
        2015-era Unfold3D technology. Each mesh is exported to OBJ, unwrapped
        by the chosen engine, and its UVs transferred back — all inside one
        undo chunk, with each mesh's original UVs snapshotted so a failure
        leaves that mesh untouched.

        Both engines return the input topology unchanged, so UVs map back by
        component index exactly; no triangulation or spatial sampling is
        involved.

        Parameters:
            objects (str/obj/list): Mesh(es) to unwrap. None uses the selection.
                Instances collapse to one representative (they share a shape).
            method (str): ``"hard"`` — Ministry of Flat, for hard-surface /
                mechanical meshes; topology-aware, artist-like seam placement,
                and it packs its own result. ``"organic"`` — Boundary First
                Flattening, for sculpted / scanned / character meshes; conformal
                flattening with automatic cone singularities. The engine keys
                ``"mof"`` / ``"bff"`` are accepted directly.
            map_size (int): Texture size the packing gutter is derived from;
                also Ministry of Flat's island-spacing resolution.
            pack (bool): What to do with the engine's UVs afterwards. None
                (default) picks per engine: Ministry of Flat's own island
                arrangement is kept and merely scaled into the 0-1 tile (it
                packs into a rectangle that overruns it), while BFF — which
                only flattens — gets a full layout pass. True forces the full
                repack; False leaves the engine's UVs exactly as produced,
                tile overrun and all.
            orient (bool): Orient each shell to its nearest U/V axis when packing.
            engine_params (dict): Extra engine settings forwarded to
                :class:`pythontk.UvUnwrap` (e.g. ``{"separate_hard_edges": True}``
                for Ministry of Flat, ``{"n_cones": 8}`` for BFF).

        Returns:
            (AutoUnwrapResult): ``engine``, ``succeeded`` and ``failed``
            ``(mesh, reason)`` pairs. Truthy when at least one mesh unwrapped.

        Raises:
            FileNotFoundError: The engine executable isn't installed. The
                message carries its download URL.
            ValueError: No meshes given/selected, or an unknown *method*.
        """
        from mayatk.uv_utils._auto_unwrap import _AutoUnwrapInternal

        return _AutoUnwrapInternal.run(
            cls,
            objects=objects,
            method=method,
            map_size=map_size,
            pack=pack,
            orient=orient,
            engine_params=engine_params,
        )

    @classmethod
    def pack_uvs(
        cls,
        objects=None,
        map_size: int = 1024,
        udim: int = 1001,
        coverage: Tuple[float, float] = (1.0, 1.0),
        rotate: bool = True,
        brute_force: bool = False,
        preserve_3d: bool = True,
        padding: Optional[float] = None,
    ):
        """Pack existing UV shells with the external xatlas engine.

        The pack-only counterpart of :meth:`auto_unwrap`: shells are taken as
        they are (never re-cut) and packed together into the target UDIM tile.
        In-process round trip — UV/triangle arrays go to
        :class:`pythontk.UvPack`, and each shell's solved similarity transform
        comes back through ``cmds.polyEditUV``, so the whole pack is a single
        undoable edit and a mesh either packs whole or reports and stays put.

        Parameters:
            objects (str/obj/list): Mesh(es) *or* components to pack. None uses
                the selection. Instances collapse to one representative (they
                share a shape). A component entry (faces, UVs, verts, edges —
                so a UV-shell selection too) packs only the region it covers,
                widened to whole faces and leaving the rest of the map exactly
                where it is; a mesh named at object level packs whole even if
                components of it were also given.
            map_size (int): Target texture size; sets the engine's page size
                (so the island gutter is exact pixels) and the tile margin.
            udim (int): Target UDIM tile number, e.g. 1001.
            coverage (tuple): (u, v) fraction of the tile to fill, anchored at
                its bottom-left corner (the Pack tool's Tile Coverage). The
                box is tiled with square cells along its long axis — Full and
                Quarter pack one cell, the halves two stacked — and the engine
                scale-searches square pages to fill each cell edge-to-edge.
                Fractions off the 1:1/1:2 grid pack with reduced fill.
            rotate (bool): Allow shell re-orientation where it packs tighter.
                True also lets the engine pre-turn shells to their hull axis by
                an arbitrary angle when that packs better (it is searched, not
                assumed — no fixed choice wins on all content). False means no
                rotation at all. Like u3dLayout, the engine may still mirror
                shells either way; every case is honored on write-back.
            brute_force (bool): Exhaustive placement search — tighter, slower.
            preserve_3d (bool): Equalize per-shell texel density first
                (u3dLayout -preScaleMode 1 equivalent; the engine preserves
                relative input scale). False keeps the shells' current
                relative UV scale (Preserve UV).
            padding (float): Island gutter in pixels. None derives it from
                *map_size* via :meth:`calculate_uv_padding`.

        Returns:
            (PackUvsResult): ``engine``, ``succeeded``, ``failed``
            ``(mesh, reason)`` pairs, the packed atlas dimensions, and
            ``targets`` — what actually packed (the scoped face components, or
            the mesh) for the meshes that succeeded, ready to measure the
            resulting texel density against. Truthy when at least one mesh
            packed.

        Raises:
            RuntimeError: The xatlas Python package isn't installed in this
                interpreter. The message carries the pip install command.
            ValueError: No meshes given/selected.
        """
        from mayatk.uv_utils._uv_pack import _UvPackInternal

        return _UvPackInternal.run(
            cls,
            objects=objects,
            map_size=map_size,
            udim=udim,
            coverage=coverage,
            rotate=rotate,
            brute_force=brute_force,
            preserve_3d=preserve_3d,
            padding=padding,
        )

    @classmethod
    def _pack_shells(cls, mesh, map_size: int = 4096, orient: bool = True) -> None:
        """Lay the mesh's UV shells out into the 0-1 square without overlap.

        ``u3dLayout`` (not ``polyLayoutUV``, which collapses cylindrically-
        seeded shells) packs; scaling by 3D area can overrun the square, so
        :meth:`_fit_uvs_to_tile` collectively fits it back.

        ``u3dLayout``'s cost is ~quadratic in resolution (4096 -> ~1.2s,
        8192 -> ~4.7s) yet the packing is pixel-identical from ~256 up --
        ``shellSpacing`` is already normalized, so resolution only sets pack
        precision, not the gap. Cap it well below *map_size* to stay fast.
        """
        cmds.loadPlugin("Unfold3D.mll", quiet=True)
        pad = cls.calculate_uv_padding(map_size, normalize=True)
        uvs = cmds.polyListComponentConversion(mesh, toUV=True) or []
        if not uvs:
            return
        cmds.u3dLayout(
            uvs,
            resolution=min(map_size, 1024),
            shellSpacing=pad,
            tileMargin=pad / 2,
            preScaleMode=1,
            preRotateMode=1 if orient else 0,
            packBox=[0, 1, 0, 1],
        )
        cls._fit_uvs_to_tile(mesh)

    @staticmethod
    def _fit_uvs_to_tile(mesh) -> None:
        """Scale the mesh's UVs as a whole into 0-1, keeping their layout.

        A uniform, aspect-preserving fit -- shells keep their relative
        arrangement and can't gain new overlaps. This is what an externally
        laid-out result needs: Ministry of Flat packs into a *rectangle* whose
        extent routinely runs past 1.0 (~1.5 x 1.8 is typical), so its own
        packing is worth keeping but not its scale.
        """
        uvs = cmds.polyListComponentConversion(mesh, toUV=True) or []
        if uvs:
            cmds.polyNormalizeUV(uvs, normalizeType=1, preserveAspectRatio=True)

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
            radial = sum(
                abs(fn.getPolygonNormal(f, om.MSpace.kWorld).normal() * axis)
                for f in faces
            ) / len(faces)
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
        algorithm="auto",
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
            algorithm (str): Seam-detection strategy. ``"auto"`` (default)
                picks per mesh via :meth:`detect_seam_algorithm`. ``"axis"``
                assumes a straight revolution axis; ``"topology"`` derives the
                cuts from smooth-region topology, handling bent / swept tubes
                and toruses (see :meth:`get_topology_seam_edges`).
        """
        meshes = cls._cylinder_meshes(objects)
        seamed = [
            m
            for m in meshes
            if cls._seam_cut_one(
                m, angle=angle, invert_seam=invert_seam, sew=sew, algorithm=algorithm
            )
        ]
        if unfold and seamed:
            cmds.loadPlugin("Unfold3D.mll", quiet=True)
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
                    # Pack the shells into 0-1 without overlap, then flip back
                    # any shell u3dLayout mirrored to pack tighter. The UV
                    # pipeline stays construction history (a consistent chain --
                    # u3dUnfold emits a polyTweakUV), so the caller's modeling /
                    # deformer history is left intact rather than baked away.
                    cls._pack_shells(m, map_size=map_size, orient=orient)
                    cls._unflip_reversed_shells(m)
                except Exception as error:  # plugin missing / non-unfoldable mesh
                    cmds.warning(f"unwrap_cylinder: unfold skipped for {m} ({error}).")
        return seamed

    @staticmethod
    def _cylinder_meshes(objects):
        """Resolve *objects* (or the selection) to a list of mesh transforms."""
        if objects is None:
            objects = cmds.ls(selection=True) or []
        shapes = (
            cmds.ls(
                CoreUtils.as_strings(objects),
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

        if not isinstance(objects, list):
            objects = [objects]
        # polyListComponentConversion falls back to the current selection when
        # given nothing — guard so an empty input can't silently measure it.
        if not objects:
            cmds.warning("No faces found in the input objects.")
            return 0

        # Ranged face components ('pCube1.f[0:5]') are fine as-is for the
        # aggregate evaluate calls — no need to flatten to individual faces.
        faces = cmds.polyListComponentConversion(objects, toFace=True) or []
        if not faces:
            cmds.warning("No faces found in the input objects.")
            return 0

        # Aggregate 3D and UV areas in one evaluate call each.
        area_3d_sum = sum(cmds.polyEvaluate(faces, worldFaceArea=True) or [0.0])
        area_uv_sum = sum(cmds.polyEvaluate(faces, uvFaceArea=True) or [0.0])

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

        Native reimplementation of Maya's ``texSetTexelDensity`` (the UV
        Toolkit "Set" operation): each UV shell — or the selected portion of
        one — is scaled about its own 2D bounding-box center to the target
        density. Component input scales as the toolkit does: a partial
        face/UV selection scales those components, not the enclosing shell.

        Unlike the MEL script, a shell whose current density can't be
        measured (zero UV area or zero surface area — collapsed, unmapped or
        degenerate UVs) is skipped and summarized in a single warning,
        instead of aborting everything with the MEL's division by zero
        (``texSetTexelDensity.mel`` line 56).

        Parameters:
            objects (str, obj, list): List of objects or a single object to set texel density for.
                If None, the currently selected objects will be used.
            density (float): The desired texel density.
            map_size (int): Size of the map to calculate the texel density against.

        Returns:
            (tuple): (scaled_shell_count, skipped_shell_count)
        """
        from math import sqrt
        from collections import defaultdict
        import maya.api.OpenMaya as om

        objects = (
            CoreUtils.as_strings(objects)
            if objects
            else (cmds.ls(selection=True) or [])
        )
        if not objects:
            cmds.warning("set_texel_density: no objects given or selected.")
            return (0, 0)

        uvs = cmds.polyListComponentConversion(objects, toUV=True) or []
        if not uvs:
            cmds.warning("set_texel_density: no UVs found on the input.")
            return (0, 0)

        scaled = skipped = 0
        for node, node_uvs in Components.map_components_to_objects(uvs).items():
            sel = om.MSelectionList()
            sel.add(node)
            dag = sel.getDagPath(0)
            dag.extendToShape()
            _, shell_ids = om.MFnMesh(dag).getUvShellsIds()

            # Group the input UVs by the shell they belong to, so each shell
            # (or selected part of one) scales about its own center.
            groups = defaultdict(list)
            for comp in cmds.ls(node_uvs, flatten=True) or []:
                groups[shell_ids[int(comp.split("[")[1].rstrip("]"))]].append(comp)

            for comps in groups.values():
                faces = (
                    cmds.polyListComponentConversion(comps, fromUV=True, toFace=True)
                    or []
                )
                area_3d = sum(cmds.polyEvaluate(faces, worldFaceArea=True) or [0.0])
                area_uv = sum(cmds.polyEvaluate(faces, uvFaceArea=True) or [0.0])
                if area_3d <= 0 or area_uv <= 0:
                    skipped += 1
                    continue
                current = sqrt(area_uv / area_3d) * map_size
                (u_min, u_max), (v_min, v_max) = cmds.polyEvaluate(
                    comps, boundingBoxComponent2d=True
                )
                cmds.polyEditUV(
                    comps,
                    pivotU=(u_min + u_max) / 2,
                    pivotV=(v_min + v_max) / 2,
                    scaleU=density / current,
                    scaleV=density / current,
                )
                scaled += 1

        if skipped:
            cmds.warning(
                f"set_texel_density: skipped {skipped} shell(s) with zero UV or "
                "surface area."
            )
        return (scaled, skipped)

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
            # `-create` makes the new set current. Taking a backup must not
            # change which set is being worked on, or every UV edit that
            # follows lands in the backup and is lost when it's discarded.
            cmds.polyUVSet(shape, currentUVSet=True, uvSet=current)
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
            else:
                # A destructive op removed the original set; rename the
                # snapshot back into its place rather than selecting a set
                # that no longer exists (which raises RuntimeError and
                # would abort restoration of every remaining shape).
                cmds.polyUVSet(
                    shape, rename=True, uvSet=snap_set, newUVSet=original_set
                )
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=original_set)

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
        match_by_similarity: bool = True,
    ) -> None:
        """Transfers UVs from source meshes to target meshes. This method is
        topology-agnostic and can work with different mesh structures.

        Parameters:
            source (Union[str, object, List]): The source mesh(es) from which to transfer UVs.
            target (Union[str, object, List]): The target mesh(es) to which UVs will be transferred.
            tolerance (float): The geometric similarity tolerance, used only when
                ``match_by_similarity`` is True. Defaults to 0.1.
            match_by_similarity (bool): When True (default), ``source``/``target`` are
                treated as unordered groups and paired up by geometric similarity
                (bounding-box volume + vertex count). Set False when the caller
                already supplies verified, positionally-ordered (source, target)
                pairs -- similarity matching would then be redundant, and risks
                either rejecting a known-correct pair that falls under
                ``tolerance`` or cross-wiring two pairs of near-identical geometry
                (e.g. duplicate/mirrored parts) since matches aren't mutually exclusive.
        """
        if match_by_similarity:
            pairs = CoreUtils.build_mesh_similarity_mapping(
                source, target, tolerance
            ).items()
        else:
            src_list = CoreUtils.as_strings(source)
            dst_list = CoreUtils.as_strings(target)
            if len(src_list) != len(dst_list):
                raise ValueError(
                    "source and target must be the same length when "
                    f"match_by_similarity=False (got {len(src_list)} vs {len(dst_list)})."
                )
            pairs = zip(src_list, dst_list)

        for source_name, target_name in pairs:
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

    @classmethod
    @CoreUtils.undoable
    def transfer_uvs_to_similar(
        cls,
        source: Union[str, object],
        candidates: Optional[List[Union[str, object]]] = None,
        tolerance: float = 0.9,
    ) -> List[str]:
        """Transfer UVs from one source mesh to every geometrically similar mesh.

        Unlike ``transfer_uvs`` (which pairs two groups one-to-one), this fans a
        single source out to any number of look-alike targets: duplicate meshes
        identified by bounding-box volume + vertex count. True Maya instances of
        the source are excluded (they share its shape, so their UVs already
        match), and each instance group among the candidates receives one
        transfer via a single representative -- the shared shape covers the rest.

        Parameters:
            source (Union[str, object]): The mesh (transform) whose UVs are copied.
                Must resolve to exactly one mesh.
            candidates (Optional[List]): Pool to search for similar meshes; groups
                are resolved to their leaf meshes. None (default) searches every
                mesh in the scene.
            tolerance (float): Minimum similarity score (0-1) a candidate must
                reach. Deliberately stricter than ``transfer_uvs``'s pairing
                default: a fan-out transfer accepts every candidate over the
                threshold, so a loose value sprays UVs onto merely similar-sized
                geometry.

        Returns:
            List[str]: The target transforms (long names) that received the transfer.
        """
        src_meshes = NodeUtils.get_unique_children(source)
        if len(src_meshes) != 1:
            raise ValueError(
                f"source must resolve to exactly one mesh (got {len(src_meshes)}: "
                f"{src_meshes})."
            )
        src = src_meshes[0]
        if not cmds.listRelatives(src, shapes=True, noIntermediate=True, type="mesh"):
            raise ValueError(f"source has no polygon mesh shape: {src}")

        if candidates is None:
            pool = NodeUtils.list_transforms(type="mesh", long=True, noIntermediate=True)
        else:
            pool = NodeUtils.get_unique_children(candidates)

        # The source and its true instances share one shape -- transferring to
        # them is at best a no-op, so drop the whole instance group.
        exclude = set(
            cmds.ls(
                NodeUtils.get_instances(src, return_parent_objects=True) or [src],
                long=True,
            )
        )
        pool = [
            t
            for t in dict.fromkeys(cmds.ls(pool, long=True) or [])
            if t not in exclude
            and cmds.listRelatives(t, shapes=True, noIntermediate=True, type="mesh")
        ]
        # One representative per candidate instance group.
        pool = NodeUtils.filter_duplicate_instances(pool)

        targets = sorted(
            t
            for t in cmds.ls(pool, long=True) or []
            if CoreUtils._calculate_mesh_similarity(src, t) >= tolerance
        )
        if targets:
            cls.transfer_uvs(
                [src] * len(targets), targets, match_by_similarity=False
            )
        return targets

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
                cmds.polyUVSet(
                    shape, reorder=True, uvSet=current, newUVSet=insert_after
                )
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
            current = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[
                0
            ]

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
                        all_sets = (
                            cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                        )

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
