# !/usr/bin/python
# coding=utf-8
import uuid
from typing import List, Optional, Sequence, Tuple, Union

try:
    import maya.cmds as cmds
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

    @staticmethod
    def get_uv_triangles(shape: str, uv_set: Optional[str] = None):
        """``(N, 3, 2)`` array of *shape*'s UV-space triangles for *uv_set*.

        The raw geometry of a UV layout -- every polygon fan-triangulated over
        its ASSIGNED UVs -- so the result describes exactly the area the layout
        occupies. Pair it with
        :meth:`pythontk.ImgUtils.rasterize_uv_triangles` to turn a layout into
        a per-texel coverage mask (which texels a bake actually owns), or use
        it directly for UV area / overlap math.

        Fan triangulation over the assigned UVs is deliberate.
        ``MFnMesh.getTriangles`` triangulates concave faces properly but
        indexes VERTICES, and a vertex carries one position for every UV shell
        meeting at it -- so it cannot express a seam, and a lightmap layout is
        nothing but seams. The cost is that a concave n-gon can contribute a
        sliver outside its own outline; for a coverage consumer that is a
        slightly generous mask, never a missing texel.

        Parameters:
            shape: Mesh shape, or a transform (its shape is resolved).
            uv_set: UV set name. The mesh's current set when omitted.

        Returns:
            ``(N, 3, 2)`` float array of ``(u, v)`` corners; ``(0, 3, 2)`` when
            the set has no assigned UVs.
        """
        import maya.api.OpenMaya as om
        import numpy as np

        empty = np.zeros((0, 3, 2), dtype=float)
        shape = NodeUtils.get_shape(shape) or shape
        selection = om.MSelectionList()
        selection.add(str(shape))
        mesh = om.MFnMesh(selection.getDagPath(0))

        name = uv_set or mesh.currentUVSetName()
        us, vs = mesh.getUVs(name)
        counts, uv_ids = mesh.getAssignedUVs(name)
        if not len(uv_ids):
            return empty

        # Plain Python ints for the fan loop: it touches every face-vertex, and
        # indexing a numpy array there boxes each element (the packer's
        # `_uv_arrays` measured the same loop at 198ms vs 296ms on a 102k-face
        # mesh). `counts` carries one entry per polygon -- 0 for an unmapped
        # one -- so the running offsets stay aligned with face indices.
        ids = list(uv_ids)
        starts = [0, *np.cumsum(np.asarray(counts, dtype=np.int64)).tolist()]
        tris = []
        for face in range(len(counts)):
            face_ids = ids[starts[face] : starts[face + 1]]
            for k in range(1, len(face_ids) - 1):
                tris.append((face_ids[0], face_ids[k], face_ids[k + 1]))
        if not tris:
            return empty

        uvs = np.stack(
            [np.asarray(us, dtype=float), np.asarray(vs, dtype=float)], axis=1
        )
        return uvs[np.asarray(tris, dtype=np.int64)]

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
    def get_uv_pin_weights(uvs) -> List[float]:
        """Pin weight of each UV in *uvs* (flat component names), in argument order.

        One bulk ``polyPinUV -q -value`` -- probed to answer in argument order --
        with a per-UV fallback if Maya ever returns a list of another length.
        Pairs with :meth:`set_uv_pin_weights` for save / restore around an
        operation that has to move pinned UVs (``polyEditUV`` honours pins;
        the stack commands don't).
        """
        uvs = [str(uv) for uv in (ptk.make_iterable(uvs) or [])]
        if not uvs:
            return []
        weights = cmds.polyPinUV(uvs, query=True, value=True) or []
        if len(weights) != len(uvs):
            weights = [
                (cmds.polyPinUV(uv, query=True, value=True) or [0.0])[0] for uv in uvs
            ]
        return [float(w) for w in weights]

    @staticmethod
    def set_uv_pin_weights(uvs, weights) -> None:
        """Set per-UV pin weights (``zip(uvs, weights)``) with one ``polyPinUV``
        per distinct weight; UVs that no longer exist are skipped."""
        by_weight = {}
        for uv, weight in zip(ptk.make_iterable(uvs) or [], weights):
            uv = str(uv)
            if cmds.objExists(uv):
                by_weight.setdefault(float(weight), []).append(uv)
        for weight, group in by_weight.items():
            cmds.polyPinUV(group, value=weight)

    @staticmethod
    def _similar_shell_targets(items) -> List[str]:
        """Widen *items* for ``polyUVStackSimilarShells``: components pass through
        verbatim, polygon objects become ``<obj>.f[*]`` (the command silently
        ignores whole objects — Maya's own toolkit widens the same way in
        ``performStackSimilarShells``), non-mesh objects are dropped rather than
        raising on ``.f[*]``."""
        items = [str(i) for i in (ptk.make_iterable(items) or [])]
        components = [i for i in items if "." in i]
        objects = [i for i in items if "." not in i]
        meshes = (cmds.filterExpand(objects, selectionMask=12) or []) if objects else []
        return components + [f"{mesh}.f[*]" for mesh in meshes]

    @staticmethod
    def stack_similar_uv_shells(items, tolerance: float = 1.0) -> List[str]:
        """Stack shells of the same topology and shape onto the first matching
        shell — Maya's ``polyUVStackSimilarShells``: a match is rotated (and
        scaled) so it overlaps its anchor exactly, shells with no match stay put.

        Parameters:
            items: Polygon objects and/or components (faces / UVs) whose shells
                are compared. Objects widen to every shell they own; non-mesh
                objects are skipped.
            tolerance: Shape-difference allowance (Maya's ``-tolerance``; 0 =
                practically identical, higher = looser — 1.0 also absorbs the
                small drift two separately unfolded copies pick up).

        Returns:
            list: The UVs of every shell that took part (each anchor and the
            shells stacked onto it), flat; empty when nothing matched.
        """
        targets = UvUtils._similar_shell_targets(items)
        if not targets:
            return []
        stacked = cmds.polyUVStackSimilarShells(*targets, tolerance=tolerance) or []
        return [uv for entry in stacked for uv in str(entry).split()]

    @staticmethod
    @CoreUtils.undoable
    def get_similar_uv_shells(
        reference,
        candidates=None,
        tolerance: float = 1.0,
        include_reference: bool = False,
    ) -> List[List[str]]:
        """The UV shells that Stack Similar would stack together with *reference*'s
        shell(s) — same topology and shape (Maya's ``polyUVStackSimilarShells``
        similarity, so this and :meth:`stack_similar_uv_shells` always agree) —
        found WITHOUT moving anything.

        The command has no query form (``-onlyMatch`` reports one representative
        per similarity group, not a reference's matches), so this is a dry run:
        the candidates' UV positions are read through the API, the shells are
        stacked, every group member then sits on the reference shell's centre,
        and the shells the command reports are moved back UV-by-UV with
        ``polyEditUV`` (recorded, index-preserving — the UV-set snapshot primitive
        renumbers ``.map[]`` indices, which would break index-keyed pins and
        snapshots). Net scene change: none.

        Parameters:
            reference: UVs / faces (or objects) whose shell(s) are the reference.
            candidates: Objects and/or components to search. Default: the
                reference's objects, whole.
            tolerance: Shape-difference allowance (see :meth:`stack_similar_uv_shells`).
            include_reference: Also return the reference shell(s) themselves.

        Returns:
            list[list[str]]: One flat UV list per similar shell (long names, ready
            for ``cmds.select``); empty when no other shell matches.
        """
        import maya.api.OpenMaya as om

        ref_uvs = cmds.ls(
            cmds.polyListComponentConversion(reference, toUV=True) or [],
            flatten=True,
            long=True,
        )
        if not ref_uvs:
            return []
        ref_set = set(ref_uvs)
        if candidates is None:
            candidates = cmds.ls(reference, objectsOnly=True) or []
        targets = UvUtils._similar_shell_targets(candidates)
        if not targets:
            return []

        def mesh_fn(shape: str) -> om.MFnMesh:
            # Fresh function set each time -- one is read before the stack and
            # one after, and a cached set must not serve stale UV arrays.
            sel = om.MSelectionList()
            sel.add(shape)
            return om.MFnMesh(sel.getDagPath(0))

        def parse(uv_name: str):
            shape, _, rest = uv_name.rpartition(".map[")
            return shape, int(rest.rstrip("]"))

        def shape_of(node: str) -> str:
            # The renderable shape, full path (get_shapes skips intermediates).
            shape = NodeUtils.get_shape(node)
            return str(shape) if shape else node

        # Pre-stack positions, by shape, indexed by UV id (the API returns the
        # current UV set in index order).
        before = {}
        for obj in cmds.ls(targets, objectsOnly=True) or []:
            shape = shape_of(obj)
            if shape not in before:
                before[shape] = mesh_fn(shape).getUVs()

        stacked = cmds.polyUVStackSimilarShells(*targets, tolerance=tolerance) or []
        shells = []  # (long uv names, post-stack centre, touches the reference)
        after = {}
        moved = []  # (uv name, u, v) to put back
        for entry in stacked:
            names = cmds.ls(str(entry).split(), long=True) or []
            if not names:
                continue
            us, vs = [], []
            for name in names:
                node, index = parse(name)
                shape = shape_of(node)
                if shape not in after:
                    after[shape] = mesh_fn(shape).getUVs()
                pre_u, pre_v = before[shape]
                post_u, post_v = after[shape]
                us.append(post_u[index])
                vs.append(post_v[index])
                if (pre_u[index], pre_v[index]) != (post_u[index], post_v[index]):
                    moved.append((name, pre_u[index], pre_v[index]))
            centre = ((min(us) + max(us)) / 2.0, (min(vs) + max(vs)) / 2.0)
            shells.append((names, centre, any(n in ref_set for n in names)))

        if moved:
            # polyEditUV honours pin weights (a pinned UV would refuse to move
            # back), so lift the pins on the moved UVs for the restore and put
            # the exact weights back afterwards.
            names = [name for name, _, _ in moved]
            weights = UvUtils.get_uv_pin_weights(names)
            if any(weights):
                cmds.polyPinUV(names, value=0.0)
            for name, u, v in moved:
                cmds.polyEditUV(name, uValue=u, vValue=v, relative=False)
            if any(weights):
                UvUtils.set_uv_pin_weights(names, weights)

        ref_centres = [c for _, c, is_ref in shells if is_ref]
        result = []
        for names, centre, is_ref in shells:
            if is_ref and not include_reference:
                continue
            if any(
                abs(centre[0] - rc[0]) < 1e-4 and abs(centre[1] - rc[1]) < 1e-4
                for rc in ref_centres
            ):
                result.append(names)
        return result

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

    @classmethod
    def get_auto_seam_edges(
        cls,
        mesh,
        angle: float = 45.0,
        invert_seam: bool = False,
        taper_angle: float = 20.0,
        camera=None,
        flat_angle: float = 60.0,
        trim_ratio: float = 0.12,
    ):
        """Seam edges that auto-unwrap a cylinder / tube / turned mesh -- clean
        and minimal, the way a texture artist seams a turned or swept part.

        The mesh is read as rings and the quad bands between them, and each
        band is classified against its own local axis (so bent hoses and
        mitered elbows read correctly):

        - **wall** bands (cylinder-like -- lengthwise edges within
          ``taper_angle`` of the axis) and **cone** bands (chamfers, flares,
          funnels -- tilted further, but still exact sectors) are strips:
          every connected run of strips is opened with **one** lengthwise
          cut, all runs along the same edge chain so the seams line up;
        - **flat** bands (washers / steps -- near-perpendicular -- and the
          bands of a domed cap) and caps stay closed: an annulus or disc
          unfolds as-is;
        - a ring is cut wherever a strip meets a flat band, whatever the
          angle; between two strips where the profile turns by more than
          ``taper_angle`` (a chamfer never merges into its cylinders, a bent
          hose stays one strip); between two flat bands only at a fold-back;
          an authored hard ring is a seam whatever the angle;
        - trim bands (fillets / bevels a few percent of the radius tall)
          ride the strip of the wall they round off, so a rounded collar is
          one strip and its seams sit in the creases beyond the fillets.

        A closed torus additionally gets one crossing ring so it can unroll;
        3D boundary edges (an open tube's rims) are never cut; regions that
        don't fit the band structure fall back to plain crease cuts.

        Parameters:
            mesh (str): A polygon transform or shape -- a straight or bent
                tube, a turned / stepped profile, a torus, a capped cylinder.
            angle (float): Crease threshold in degrees for edges of regions
                the tube reading doesn't cover; set below ``taper_angle`` it
                also splits strips at gentler kinks. Default 45.
            invert_seam (bool): Land the lengthwise seam on the side facing
                the viewer instead of away from it.
            taper_angle (float): Taper tolerance in degrees: a band whose
                sides tilt from its axis by more than this is a cone rather
                than a wall, and two strips split where the profile turns by
                more than this. Default 20.
            camera (str/tuple, optional): The view to hide the seam from -- a
                camera transform / shape name, or a world-space eye position.
                None assumes Maya's default perspective direction, so the pick
                is deterministic without a viewport.
            flat_angle (float): Bands tilted from their axis past this many
                degrees (steps, shallow bevels) stay closed rings; steeper
                ones (chamfers, flares, funnels) are cut open on the seam as
                exact sectors. Lower it to keep more bevels as rings. Default
                60 (a ring costs at most ~15% stretch there).
            trim_ratio (float): Bands shorter than this fraction of the tube
                radius are trim -- fillets, bevels, beads -- and ride the
                strip of the wall they round off instead of becoming shells
                of their own. Default 0.12.

        Returns:
            (list) Edge component strings (``mesh.e[i]``).
        """
        seamer = cls._analyze_seams(
            mesh,
            camera=camera,
            angle=angle,
            invert_seam=invert_seam,
            taper_angle=taper_angle,
            flat_angle=flat_angle,
            trim_ratio=trim_ratio,
        )
        name = str(mesh)
        return [f"{name}.e[{i}]" for i in sorted(seamer.cuts)]

    @classmethod
    def _analyze_seams(cls, mesh, camera=None, **seam_options):
        """Run the band-based seamer on *mesh*; the returned seamer carries the
        cut set (``.cuts``) and the decomposition the unfold seed needs.
        ``seam_options`` are :meth:`_CylinderSeamsInternal.seams`' keywords
        (``angle``, ``taper_angle``, ``invert_seam``, ``flat_angle``,
        ``trim_ratio``); ``camera`` is resolved to an eye position first."""
        from mayatk.uv_utils._cylinder_seams import _CylinderSeamsInternal

        seamer = _CylinderSeamsInternal.from_mesh(str(mesh))
        seamer.seams(camera=cls._camera_eye(camera), **seam_options)
        return seamer

    @staticmethod
    def _camera_eye(camera):
        """World-space eye position for a seam-hiding view, or ``None``.

        ``camera`` may be ``None``, an ``(x, y, z)`` position, or a camera
        transform / shape name.
        """
        if camera is None:
            return None
        if isinstance(camera, (tuple, list)):
            if len(camera) != 3:
                raise ValueError(f"camera position must be (x, y, z), got {camera!r}")
            return tuple(float(c) for c in camera)
        name = str(camera)
        try:
            return tuple(cmds.camera(name, query=True, position=True))
        except (RuntimeError, TypeError):
            return tuple(
                cmds.xform(name, query=True, worldSpace=True, translation=True)
            )

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
    def _seam_cut_one(cls, mesh, history=True, sew=True, **seam_options):
        """Cut the auto seams on one mesh.

        Returns the analysed seamer (truthy; it carries the decomposition the
        unfold seed reuses), or ``None`` when there was nothing to cut. With
        ``sew`` (default) any pre-existing UV cuts are sewn shut first, so the
        result's shells come only from this operation's seams rather than
        stray borders left by an earlier unwrap / manual edit.
        ``seam_options`` go to :meth:`_analyze_seams`.
        """
        seamer = cls._analyze_seams(mesh, **seam_options)
        if not seamer.cuts:
            return None
        if sew:
            cmds.polyMapSew(f"{mesh}.e[*]", constructionHistory=history)
        cmds.polyMapCut(
            [f"{mesh}.e[{i}]" for i in sorted(seamer.cuts)], constructionHistory=history
        )
        return seamer

    @classmethod
    @CoreUtils.undoable
    def cut_cylinder_seams(
        cls,
        objects=None,
        angle=45.0,
        invert_seam=False,
        history=True,
        sew=True,
        taper_angle=20.0,
        camera=None,
        flat_angle=60.0,
        trim_ratio=0.12,
    ):
        """Cut auto UV seams for cylinder / tube unwrapping on each mesh.

        One lengthwise cut per run of strips (cylinder walls, chamfers,
        flares), a ring cut wherever a strip meets a step / cap or the profile
        kinks -- see :meth:`get_auto_seam_edges`. Returns the list of mesh
        transforms that were seamed.

        Parameters:
            objects (str/obj/list): Cylinder / tube mesh(es). If None, uses the
                current selection.
            angle (float): Crease threshold in degrees (see
                :meth:`get_auto_seam_edges`).
            invert_seam (bool): Land the lengthwise seam on the side facing
                the viewer instead of away from it.
            history (bool): Keep the ``polyMapCut`` construction history.
            sew (bool): Sew any pre-existing UV cuts shut first (default) so the
                result's shells come only from this operation's seams.
            taper_angle (float): Taper tolerance in degrees (see
                :meth:`get_auto_seam_edges`).
            camera (str/tuple, optional): View to hide the seam from (camera
                name or eye position); None = Maya's default perspective.
            flat_angle (float): Ring-vs-sector threshold in degrees (see
                :meth:`get_auto_seam_edges`).
            trim_ratio (float): Fillet size as a fraction of the radius (see
                :meth:`get_auto_seam_edges`).
        """
        meshes = cls._cylinder_meshes(objects)
        return [
            m
            for m in meshes
            if cls._seam_cut_one(
                m,
                angle=angle,
                invert_seam=invert_seam,
                history=history,
                sew=sew,
                taper_angle=taper_angle,
                camera=camera,
                flat_angle=flat_angle,
                trim_ratio=trim_ratio,
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

    @staticmethod
    def _seed_shell_uvs(mesh, seamer):
        """Give every UV shell a non-degenerate, un-folded seed before unfolding.

        ``u3dUnfold`` collapses a shell whose incoming UVs have zero area and
        can fold (or bail out on) a shell whose seed doubles back on itself --
        both routine after sewing a stale projection shut and cutting fresh
        seams. The seamer already knows the mesh as strips and rings, so the
        seed it hands over (:meth:`_CylinderSeamsInternal.seed_uvs`) is the
        developed shape itself: each strip unrolled from its seam, each
        annulus / disc unrolled radially. It is written per UV id with
        ``polyEditUV`` -- undo-captured, and folded into a single
        ``polyTweakUV`` at the head of the construction history.
        """
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(str(mesh))
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        table = {}
        for f, uvs in seamer.seed_uvs().items():
            for k, uv in enumerate(uvs):
                table[fn.getPolygonUVid(f, k)] = uv
        for uv_id, (u, v) in table.items():
            cmds.polyEditUV(
                f"{mesh}.map[{uv_id}]", relative=False, uValue=float(u), vValue=float(v)
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
        taper_angle=20.0,
        camera=None,
        flat_angle=60.0,
        trim_ratio=0.12,
    ):
        """Auto-unwrap cylinder / tube / turned meshes: seam, then unfold flat.

        Cuts the auto seams on each mesh (see :meth:`get_auto_seam_edges`:
        one lengthwise cut per run of strips -- cylinder walls, chamfers,
        flares -- and rings where a strip meets a step / cap or the profile
        kinks), then unfolds so every strip lays out clean (a rectangle, a
        sector) and each step and cap as its own annulus / disc. Returns the
        seamed mesh transforms.

        Parameters:
            objects (str/obj/list): Cylinder / tube mesh(es). If None, uses the
                current selection.
            angle (float): Crease threshold in degrees (see
                :meth:`get_auto_seam_edges`). Default 45.
            invert_seam (bool): Land the lengthwise seam on the side facing
                the viewer instead of away from it.
            unfold (bool): Unfold (flatten) the UVs after seaming (Unfold3D),
                then pack the shells into the 0-1 square.
            orient (bool): Orient each shell to its nearest U/V axis while
                packing.
            map_size (int): Texture size the unfold optimizes spacing against.
            sew (bool): Sew any pre-existing UV cuts shut first (default) so the
                result's shells come only from this operation's seams.
            taper_angle (float): Taper tolerance in degrees (see
                :meth:`get_auto_seam_edges`). Default 20.
            camera (str/tuple, optional): View to hide the seam from -- a
                camera name or a world-space eye position. None assumes Maya's
                default perspective direction.
            flat_angle (float): Ring-vs-sector threshold in degrees (see
                :meth:`get_auto_seam_edges`). Default 60.
            trim_ratio (float): Fillet size as a fraction of the radius (see
                :meth:`get_auto_seam_edges`). Default 0.12.
        """
        seamers = {}
        for m in cls._cylinder_meshes(objects):
            seamer = cls._seam_cut_one(
                m,
                angle=angle,
                invert_seam=invert_seam,
                sew=sew,
                taper_angle=taper_angle,
                camera=camera,
                flat_angle=flat_angle,
                trim_ratio=trim_ratio,
            )
            if seamer:
                seamers[m] = seamer
        seamed = list(seamers)
        if unfold and seamed:
            cmds.loadPlugin("Unfold3D.mll", quiet=True)
            # Unfold each mesh on its own: a mesh u3dUnfold rejects (e.g. one
            # with "non-manifold UVs") then only skips itself -- a single batched
            # unfold would abort the whole selection on the first bad mesh.
            for m in seamed:
                try:
                    # Seed each cut shell with its developed shape so u3dUnfold
                    # neither collapses a zero-area shell nor folds a strip.
                    cls._seed_shell_uvs(m, seamers[m])
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
            # The RENDERABLE shape: get_shape_node returns a deformed mesh's orig
            # shape and its live one in hash order, and backing up the orig means
            # the destructive op this is guarding edits the OTHER shape -- the
            # "revert" then restores nothing and the UVs are simply gone.
            shape = NodeUtils.get_shape(obj)
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

    # cmds.transferAttributes sampleSpace codes, named as Maya's own Transfer
    # Attributes option box names them (scripts/others/performTransferAttributes.mel
    # builds this radio group and passes `selection - 1` as the flag value).
    SAMPLE_SPACES = {"world": 0, "object": 1, "uv": 2, "component": 3, "topology": 4}

    @staticmethod
    def _mesh_topology_signature(node: str) -> Optional[Tuple[int, int, int]]:
        """Counts that decide whether two meshes are topologically interchangeable,
        or None if ``node`` isn't polygonal -- ``polyEvaluate`` answers a non-mesh
        with the truthy STRING 'Nothing counted : no polygonal object is selected.'
        rather than raising, so an unmeasured node must never compare equal to
        another one and claim they share a topology.
        """
        counts = [
            cmds.polyEvaluate(node, **{flag: True})
            for flag in ("vertex", "edge", "face")
        ]
        return tuple(counts) if all(isinstance(c, int) for c in counts) else None

    @staticmethod
    def _current_uv_set(node: str) -> str:
        """The mesh's current UV set -- ``transferAttributes``' uv-space arguments
        name a set explicitly, and a mesh whose primary set was renamed has no
        ``map1`` for the historical hardcoded value to resolve against."""
        current = cmds.polyUVSet(node, query=True, currentUVSet=True) or []
        return current[0] if current else "map1"

    @classmethod
    @CoreUtils.undoable
    def transfer_uvs(
        cls,
        source: Union[str, object, List[Union[str, object]]],
        target: Union[str, object, List[Union[str, object]]],
        tolerance: float = 0.1,
        match_by_similarity: bool = True,
        sample_space: str = "auto",
    ) -> List[Tuple[str, str, str]]:
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
            sample_space (str): How ``transferAttributes`` samples the source, one of
                ``SAMPLE_SPACES`` or ``"auto"`` (default). Auto picks per pair, which is
                what makes the transfer topology-agnostic: matching topology transfers in
                ``topology`` space -- exact, and independent of where either mesh sits --
                while any other pair falls back to a spatial ``object``-space sample.
                Object rather than world, because a UV donor is normally staged off to
                the side of its target, and world space would then sample the whole
                target from whichever corner of the source happens to be nearest.

        Returns:
            List[Tuple[str, str, str]]: One ``(source, target, sample_space_used)`` per
            transfer performed. Empty when similarity matching paired nothing -- the
            caller can't otherwise distinguish that from a completed run.
        """
        if sample_space != "auto" and sample_space not in cls.SAMPLE_SPACES:
            raise ValueError(
                f"sample_space must be 'auto' or one of "
                f"{sorted(cls.SAMPLE_SPACES)} (got {sample_space!r})."
            )

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

        # A fan-out (transfer_uvs_to_similar passes one source against N targets)
        # would otherwise re-probe the same source once per pair. Safe to cache:
        # this transfer writes only UVs, so no pair can change another's counts.
        topo_cache = {}

        def topology(node):
            if node not in topo_cache:
                topo_cache[node] = cls._mesh_topology_signature(node)
            return topo_cache[node]

        transferred = []
        for source_name, target_name in pairs:
            if sample_space == "auto":
                src_topo = topology(source_name)
                space = (
                    "topology"
                    if src_topo is not None and src_topo == topology(target_name)
                    else "object"
                )
            else:
                space = sample_space

            cmds.transferAttributes(
                source_name,
                target_name,
                transferPositions=False,
                transferNormals=False,
                transferUVs=2,
                transferColors=0,
                sampleSpace=cls.SAMPLE_SPACES[space],
                sourceUvSpace=cls._current_uv_set(source_name),
                targetUvSpace=cls._current_uv_set(target_name),
                searchMethod=3,
                flipUVs=False,
                colorBorders=True,
            )
            cmds.delete(target_name, ch=True)  # Clean up history on target
            transferred.append((source_name, target_name, space))

        return transferred

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
            pool = NodeUtils.list_transforms(
                type="mesh", long=True, noIntermediate=True
            )
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
            cls.transfer_uvs([src] * len(targets), targets, match_by_similarity=False)
        return targets

    @staticmethod
    def reorder_uv_sets(obj: str, new_order: list[str]) -> None:
        """Reorder UV sets of the given object to match the specified new order.
        This method will raise a ValueError if the new order does not match the existing UV sets.

        Parameters:
            obj (str): The object whose UV sets will be reordered.
            new_order (list[str]): The desired order of UV sets.
        """
        # The renderable shape, by full path -- falling back to *obj* itself so an
        # intermediate shape passed deliberately is still reordered (get_shape
        # filters those out and would answer None).
        shape = str(NodeUtils.get_shape(obj) or obj)
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

    @staticmethod
    def _lightmap_channel_order(
        all_sets: Sequence[str], lightmap_set: str
    ) -> Optional[List[str]]:
        """Channel order putting the texture set at 0 and *lightmap_set* at 1.

        Channel 1 is the index engines bind. Pure list math, and it belongs over the
        shape's CURRENT set list -- never a snapshot taken before a set was created
        or projected: :meth:`reorder_uv_sets` rejects any order that disagrees with
        the scene, so a stale one turns ordinary drift into a ValueError that takes
        a whole multi-object run down with it.

        Returns:
            The order, or None when there is nothing to order -- the lightmap is
            the only set, or it isn't on the shape at all.
        """
        sets_ = list(dict.fromkeys(all_sets or []))
        primary = next((s for s in sets_ if s != lightmap_set), None)
        if not primary or lightmap_set not in sets_:
            return None
        return [primary, lightmap_set] + [
            s for s in sets_ if s not in (primary, lightmap_set)
        ]

    @staticmethod
    def _has_live_history(shape: str) -> bool:
        """True when the shape's mesh is produced by an upstream DG node.

        A shape driven through ``inMesh`` rebuilds its output from that chain every
        time the DG evaluates, so anything written straight into the shape's mesh
        data -- ``MFnMesh.setUVs`` / ``assignUVs`` -- is discarded on the next tick.
        Measured in mayapy 2025 on a plain ``polyCube``: 24 UVs readable through the
        API right after the write, 0 once the graph is evaluated; the same write on a
        history-free shape keeps all 24, because there the shape owns its mesh data.
        """
        return bool(
            cmds.listConnections(f"{shape}.inMesh", source=True, destination=False)
        )

    @classmethod
    def _write_loop_uvs(
        cls,
        shape: str,
        uv_set: str,
        counts: Sequence[int],
        us: Sequence[float],
        vs: Sequence[float],
    ) -> bool:
        """Write per-LOOP (face-vertex) UVs into *uv_set*, history or not.

        ``us``/``vs`` are one value per LOOP, in ``MFnMesh.getVertices`` order, and
        land unshared: loop *i* gets its own UV, which is what a lightmap needs.

        A history-free shape takes the direct ``MFnMesh`` write -- fastest, and it
        adds nothing to the scene. A shape with a live chain cannot: see
        :meth:`_has_live_history`. There the values have to become part of the graph,
        WITHOUT deleting the artist's history (destructive) and without leaving a
        donor mesh behind (``transferAttributes`` transfers exactly, but the result
        reverts the moment the node or its source goes away -- verified). So:

        1. ``polyForceUV -unitize`` -- one UV per face-vertex, fully unshared, the
           layout topology the buffer is authored in. It leaves a ``polyTweakUV``
           node wired to the set's ``uvSetTweakLocation``: a DG-resident, file-saved
           home for the values that needs no external geometry.
        2. Zero that node's offsets and read the unitized baseline back.
        3. Bulk-write ``desired - baseline`` into ``uvTweak`` in ONE ``setAttr``.

        Returns:
            True when the SCENE holds the UVs afterwards -- confirmed through
            ``cmds`` after an evaluation, never through the mesh cache the broken
            write used to read back from.
        """
        import maya.api.OpenMaya as om

        shape = str(shape)
        loops = int(sum(counts))
        if not loops:
            return False

        if not cls._has_live_history(shape):
            # A freshly acquired handle: creating a UV set through cmds invalidates
            # any handle taken before it, and writing through a stale one is an
            # ACCESS VIOLATION that takes Maya down with no traceback (repro'd in
            # mayapy 2025).
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnMesh(sel.getDagPath(0))
            fn.setUVs(list(us), list(vs), uv_set)
            fn.assignUVs(list(counts), list(range(loops)), uv_set)
            return bool(
                cmds.polyEvaluate(shape, uvcoord=True, uvSetName=uv_set) == loops
            )

        return cls._write_loop_uvs_through_dg(shape, uv_set, counts, us, vs)

    @staticmethod
    def _write_loop_uvs_through_dg(
        shape: str,
        uv_set: str,
        counts: Sequence[int],
        us: Sequence[float],
        vs: Sequence[float],
    ) -> bool:
        """The history-safe half of :meth:`_write_loop_uvs`. See it for the why."""
        import maya.api.OpenMaya as om

        loops = int(sum(counts))
        prev = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
        try:
            cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
            cmds.polyForceUV(f"{shape}.f[*]", unitize=True, uvSetName=uv_set)
            # The node polyForceUV leaves behind -- nearest the shape wins, that is
            # the one whose offsets reach the output. Matched on the set name so a
            # tweak node belonging to the texture channel is never written into.
            tweak = next(
                (
                    n
                    for n in (cmds.listHistory(shape) or [])
                    if cmds.nodeType(n) == "polyTweakUV"
                    and cmds.getAttr(f"{n}.uvSetName") == uv_set
                ),
                None,
            )
            if not tweak:
                print(f"[uv-layout] {shape}: no UV tweak node to write through.")
                return False

            sel = om.MSelectionList()
            sel.add(shape)
            n_uv = om.MFnMesh(sel.getDagPath(0)).numUVs(uv_set)
            if n_uv != loops:
                print(
                    f"[uv-layout] {shape}: unitize produced {n_uv} UVs for {loops} "
                    "loops; skipped rather than scrambled."
                )
                return False

            span = f"{tweak}.uvTweak[0:{n_uv - 1}]"
            # Zero FIRST: a re-bake reuses the same tweak node, so without this the
            # baseline read below would be the previous run's offsets and the new
            # layout would land doubled.
            cmds.setAttr(span, *([0.0] * (n_uv * 2)), type="float2")
            cmds.dgdirty(shape)
            cmds.polyEvaluate(shape, face=True)

            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnMesh(sel.getDagPath(0))
            base_u, base_v = fn.getUVs(uv_set)
            _counts, ids = fn.getAssignedUVs(uv_set)
            if len(ids) != loops:
                print(
                    f"[uv-layout] {shape}: {len(ids)} UV assignments for {loops} "
                    "loops; skipped."
                )
                return False

            offsets = [0.0] * (n_uv * 2)
            for loop_i, uv_id in enumerate(ids):
                offsets[uv_id * 2] = us[loop_i] - base_u[uv_id]
                offsets[uv_id * 2 + 1] = vs[loop_i] - base_v[uv_id]
            cmds.setAttr(span, *offsets, type="float2")

            # Confirm through the SCENE. The whole defect this guards against reads
            # back perfectly through MFnMesh and holds nothing once the DG ticks, so
            # the check has to force an evaluation and go through cmds.
            cmds.dgdirty(shape)
            return bool(
                cmds.polyEvaluate(shape, uvcoord=True, uvSetName=uv_set) == loops
            )
        finally:
            if prev in (cmds.polyUVSet(shape, query=True, allUVSets=True) or []):
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev)

    @staticmethod
    @CoreUtils.undoable
    def apply_uv_layout(layouts: dict, uv_set: str = None, quiet: bool = False) -> dict:
        """Write UV layouts authored in ANOTHER application onto these meshes.

        The receiving half of ``btk.UvUtils.export_uv_layout``. It exists so a tool that
        legitimately owns a UV job end to end -- the Blender lightmap bake generates,
        packs and atlases its own layout -- can hand the finished result back, instead of
        Maya authoring a second, different layout that the baked texels would not match.

        Applied per LOOP (face-vertex) with unshared UVs, which is what a lightmap needs:
        its islands are cut at seams, where one vertex carries several distinct UVs.

        A layout is only meaningful on the topology it was made from, so each is verified
        against the mesh's polygon vertex-count sequence and vertex total before anything
        is written; a mesh edited since the hand-off is skipped rather than given
        scrambled UVs. Rejections are per object -- the rest still apply.

        **Construction history is preserved, and the write survives it.** A mesh whose
        shape is driven through ``inMesh`` rebuilds its output from that chain on every
        DG evaluation, so a raw ``MFnMesh`` write lands NOTHING -- it reads back through
        the API until the graph ticks and is gone after. Meshes with history therefore
        take a DG route (:meth:`_write_loop_uvs`) that becomes part of the chain instead
        of deleting it; history-free meshes keep the direct write. Either way the result
        is confirmed against the evaluated scene before a mesh is reported as applied.

        **Undo covers the new set, not an overwrite.** On a history-free mesh the write
        goes through ``MFnMesh``, which Maya does not record, so undoing only removes a
        UV set this created (taking its UVs with it); re-running over an EXISTING set
        replaces those UVs irreversibly. For the lightmap workflow the supported undo is
        ``LightmapBaker.revert_lightmap`` / the panel's Revert to Source.

        Parameters:
            layouts: ``{object: {"uv_set", "poly_counts", "num_verts", "uvs"}}`` where
                ``uvs`` is base64 little-endian float32 ``[u0, v0, ...]`` in loop order.
            uv_set: Override the set name to write into (default: the layout's own).
            quiet: Suppress per-object logging.

        Returns:
            dict: ``{object: uv_set}`` for each mesh actually written -- keyed by the
            caller's own key, so a rejected mesh is simply absent and the caller can
            skip it downstream without re-deriving shapes.
        """
        import array
        import base64
        import sys

        import maya.api.OpenMaya as om

        from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics

        applied = {}
        for obj, layout in (layouts or {}).items():
            shape = NodeUtils.get_shape(obj)
            if not shape or not layout:
                continue
            sel = om.MSelectionList()
            sel.add(str(shape))
            fn = om.MFnMesh(sel.getDagPath(0))
            counts, _ids = fn.getVertices()

            expected = [int(c) for c in (layout.get("poly_counts") or [])]
            if list(counts) != expected or fn.numVertices != layout.get("num_verts"):
                print(
                    f"[uv-layout] {shape}: topology changed since the layout was made "
                    f"({fn.numPolygons} polys / {fn.numVertices} verts vs "
                    f"{len(expected)} / {layout.get('num_verts')}); skipped."
                )
                continue

            buf = array.array("f")
            buf.frombytes(base64.b64decode(layout["uvs"]))
            if sys.byteorder != "little":  # the wire format is pinned little-endian
                buf.byteswap()
            loops = int(sum(counts))
            if len(buf) != loops * 2:
                print(
                    f"[uv-layout] {shape}: expected {loops * 2} floats, "
                    f"got {len(buf)}; skipped."
                )
                continue

            # Prefer the set this scene ALREADY calls its lightmap, so a layout
            # arriving from another app overwrites that channel instead of parking
            # a second one beside it. Maya UV set names are case-sensitive and the
            # two ends spell the default differently (blendertk "Lightmap",
            # mayatk "lightmap"), so honouring the incoming name blindly left the
            # previous bake's set behind on every hand-off. An explicit uv_set
            # argument still wins -- that is the caller overriding on purpose.
            detected = UvDiagnostics.find_lightmap_uv_set(shape)
            name = (
                uv_set
                or detected
                or layout.get("uv_set")
                or UvDiagnostics.LIGHTMAP_UV_SET
            )
            pre = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            prev_current = (
                cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None]
            )[0]
            created = name not in pre
            if created:
                # A DG op: the set survives an evaluation on a mesh with history,
                # unlike the UV data itself. ``_write_loop_uvs`` re-acquires its own
                # MFnMesh afterwards -- writing through a handle taken before a cmds
                # UV-set create is an ACCESS VIOLATION that takes Maya down with no
                # traceback (repro'd in mayapy 2025).
                cmds.polyUVSet(shape, create=True, uvSet=name)

            try:
                wrote = UvUtils._write_loop_uvs(
                    shape, name, list(counts), list(buf[0::2]), list(buf[1::2])
                )
            except Exception as e:  # noqa: BLE001
                # Per object, same contract as the topology rejection above: a
                # locked or referenced shape costs only itself, never the rest of a
                # hundreds-of-objects hand-off.
                print(f"[uv-layout] {shape}: UV write failed ({e}).")
                wrote = False

            if not wrote:
                print(
                    f"[uv-layout] {shape}: UV write did not reach the scene; skipped."
                )
                if created:  # never leave an empty set posing as a lightmap
                    try:
                        cmds.polyUVSet(shape, delete=True, uvSet=name)
                    except Exception as e:  # noqa: BLE001
                        print(f"[uv-layout] {shape}: could not remove '{name}' ({e}).")
                if prev_current in (
                    cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                ):
                    cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev_current)
                continue

            # `name == detected` covers a scene whose lightmap channel is named by
            # one of the other recognized conventions (UV2, UVChannel_2): it is the
            # lightmap by detection, so it earns the tag and channel 1 too.
            if name == detected or "lightmap" in name.strip().lower():
                # Mirror create_lightmap_uvs: the lightmap belongs at channel 1 (the
                # index engines bind) and carries the tag that makes detection
                # unambiguous, so a layout that arrived from outside is
                # indistinguishable downstream from one authored here.
                order = UvUtils._lightmap_channel_order(
                    cmds.polyUVSet(shape, query=True, allUVSets=True) or [], name
                )
                if order:
                    try:
                        UvUtils.reorder_uv_sets(shape, order)
                    except Exception as e:
                        print(f"[uv-layout] {shape}: could not reorder UV sets ({e}).")
                if not cmds.attributeQuery(
                    UvDiagnostics.LIGHTMAP_UV_TAG, node=shape, exists=True
                ):
                    cmds.addAttr(
                        shape, longName=UvDiagnostics.LIGHTMAP_UV_TAG, dataType="string"
                    )
                cmds.setAttr(
                    f"{shape}.{UvDiagnostics.LIGHTMAP_UV_TAG}", name, type="string"
                )

            all_now = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            restore = (
                prev_current if prev_current in all_now else (all_now or [None])[0]
            )
            if restore:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=restore)

            applied[obj] = name
            if not quiet:
                print(f"[uv-layout] {shape}: wrote {loops} UVs into '{name}'.")
        return applied

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
            dict: ``{shape: {"uv_set": str, "created": bool, "reused": bool}}``,
            keyed by the RENDERABLE shape's full DAG path (never the orig shape of
            a deformed mesh, and never a leaf name two groups could share).
            ``uv_set`` is the set that actually exists, which is not necessarily
            the one requested -- Maya uniquifies a colliding name. Rejections are
            per object: a mesh that can't take a lightmap is logged and left out,
            the rest still apply.
        """
        from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics

        uv_set = uv_set or UvDiagnostics.LIGHTMAP_UV_SET
        # Normalized padding is map-size-independent (~0.39%); as a percentage
        # for polyAutoProjection's percentageSpace gutter.
        pct = cls.calculate_uv_padding(map_size, normalize=True) * 100.0

        results: dict = {}
        for obj in NodeUtils.get_transform_node(objects):
            obj = str(obj)
            # The RENDERABLE shape, as a full path. A deformed mesh also carries an
            # orig (intermediate) shape, and ``get_shape_node`` hands back both in
            # hash order -- so which one got the lightmap was a coin flip per
            # process, and losing it wrote the set onto a shape nothing renders,
            # leaving the baked shape with no UV2. The full path matters too: the
            # short name it returns goes ambiguous the moment two groups share a
            # leaf name.
            shape = NodeUtils.get_shape(obj)
            if not shape:
                continue
            shape = str(shape)
            if not cmds.attributeQuery("uvSet", node=shape, exists=True):
                continue

            # Per object, so one mesh that can't take a lightmap -- a locked or
            # referenced shape, a projection Maya refuses -- costs only itself.
            # A whole-selection bake is the normal unit of work here (hundreds of
            # objects), and losing all of it to one bad mesh is the expensive
            # failure. Same contract as apply_uv_layout: rejections are per object.
            prev_current = None
            try:
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
                    dict.fromkeys(
                        cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                    )
                )

                # Regenerate INTO the set this scene already calls its lightmap,
                # rather than adding a second one under our own spelling. Maya UV
                # set names are case-sensitive, so a mesh carrying a bake from the
                # Blender bridge ("Lightmap") used to come out of an Arnold re-bake
                # holding BOTH -- the stale one orphaned at channel 2, still in
                # every export. It also keeps creation and the bake target on the
                # same set: _bake_to_lightmap_uvs resolves what to bake with this
                # same detection, so a differently-named channel meant creating one
                # set and baking another.
                target = existing_lm or uv_set
                if target not in pre:
                    cmds.polyUVSet(shape, create=True, uvSet=target)
                    # Maya silently uniquifies a colliding set name (asking for
                    # `map1` on a mesh that has one yields `map11`) instead of
                    # raising, so the set that now exists -- not the name we asked
                    # for -- is the authority on what to project into, tag and
                    # report.
                    added = [
                        s
                        for s in (
                            cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                        )
                        if s not in pre
                    ]
                    if target not in added and added:
                        target = added[0]
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=target)
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
                # Read from the LIVE set list rather than the pre-projection
                # snapshot: reorder_uv_sets rejects any order that doesn't match the
                # scene, so a stale one turned any drift in between -- a set added
                # by the projection, a name Maya uniquified -- into a ValueError
                # that aborted the whole bake over a single mesh.
                now = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                order = cls._lightmap_channel_order(now, target)
                if order:
                    try:
                        cls.reorder_uv_sets(shape, order)
                    except Exception as e:
                        # The set is usable even at the wrong index -- keep the tag
                        # and report it rather than dropping the mesh entirely.
                        print(
                            f"[lightmap-uv] {shape}: could not reorder UV sets ({e})."
                        )

                # Tag the shape so downstream detection is unambiguous.
                if not cmds.attributeQuery(
                    UvDiagnostics.LIGHTMAP_UV_TAG, node=shape, exists=True
                ):
                    cmds.addAttr(
                        shape, longName=UvDiagnostics.LIGHTMAP_UV_TAG, dataType="string"
                    )
                cmds.setAttr(
                    f"{shape}.{UvDiagnostics.LIGHTMAP_UV_TAG}", target, type="string"
                )

                # Restore the previously-current set (default to the texture
                # primary). Reordering moves sets, it never adds or drops one, so
                # `now` is still the membership to test against.
                restore = prev_current if prev_current in now else (order or [None])[0]
                if restore in now:
                    cmds.polyUVSet(shape, currentUVSet=True, uvSet=restore)

                results[shape] = {"uv_set": target, "created": True, "reused": False}
                if not quiet:
                    print(f"[lightmap-uv] {shape}: {results[shape]}")
            except Exception as e:  # loud even when quiet -- a silent skip is worse
                print(f"[lightmap-uv] {shape}: skipped ({e}).")
                # A half-processed mesh must not be left sitting on the lightmap
                # set -- whatever reads UVs next (export, texture work) reads the
                # current one. Best effort: this mesh is already skipped, so a
                # failed restore must not escalate into the batch's problem.
                try:
                    if prev_current:
                        cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev_current)
                except Exception:
                    pass
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
            # The RENDERABLE shape: get_shape_node hands back a deformed mesh's
            # orig shape and its live one in hash order, so cleanup landed on the
            # orig at random and left the shape that renders untouched.
            shape = NodeUtils.get_shape(obj)
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
