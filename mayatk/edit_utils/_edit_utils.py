# !/usr/bin/python
# coding=utf-8
import math
from typing import List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    mel = None
    om = None
    print(__file__, error)
import pythontk as ptk


# From this package:
from mayatk.core_utils._core_utils import CoreUtils, as_strings
from mayatk.core_utils.components import Components
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.edit_utils.naming._naming import Naming


def _safe_rename(node: str, name: str) -> str:
    """``cmds.rename`` wrapper that warns and falls back to ``node`` on failure.

    Returns the resulting name (Maya may auto-disambiguate on conflict).
    """
    try:
        return cmds.rename(node, name)
    except Exception as e:
        cmds.warning(f"Rename '{node}' -> '{name}' failed: {e}")
        return node


def _safe_parent(node: str, parent: str) -> None:
    """``cmds.parent`` wrapper that warns and continues on failure."""
    try:
        cmds.parent(node, parent)
    except Exception as e:
        cmds.warning(f"Parent '{node}' under '{parent}' failed: {e}")


class EditUtils(ptk.HelpMixin):
    """ """

    # Backward compatibility aliases - these methods have moved to Snap class
    from mayatk.edit_utils.snap import Snap

    snap_closest_verts = staticmethod(Snap.snap_to_closest_vertex)
    conform_to_surface = staticmethod(Snap.snap_to_surface)

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.reparent
    @DisplayUtils.add_to_isolation
    def combine_objects(
        objects=None,
        group_by_material=False,
        cluster_by_distance=False,
        threshold=10000.0,
        **kwargs,
    ):
        """Combine multiple meshes.

        Parameters:
            objects (list): List of mesh objects to combine.
            group_by_material (bool): Combine objects into groups based on their assigned materials.
            cluster_by_distance (bool): Subdivide combine groups by spatial
                proximity. Works with or without ``group_by_material``.
            threshold (float): The maximum distance between objects to be considered in the same cluster.
        """
        if objects is None:
            objects = cmds.ls(selection=True, )

        # Handle legacy argument
        if "allow_multiple_mats" in kwargs:
            # Legacy: allow_multiple_mats=True -> group_by_material=False
            # Legacy: allow_multiple_mats=False -> group_by_material=True
            group_by_material = not kwargs["allow_multiple_mats"]

        if "material_mode" in kwargs:
            val = kwargs["material_mode"]
            if val == "group" or val is False:  # False meant "prevent mixed" -> group
                group_by_material = True

        if not objects or len(objects) < 2:
            cmds.inViewMessage(
                statusMessage="<hl>Insufficient selection.</hl> Operation requires at least two objects",
                fade=True,
                position="topCenter",
            )
            return None

        def unite_group(group_objs, label):
            # Get name before combine destroys the object
            name = str(group_objs[0]).split("|")[-1].split(":")[-1]
            try:
                united = cmds.polyUnite(group_objs, centerPivot=True, ch=False)
                return cmds.rename(united[0], name)
            except Exception as e:
                cmds.warning(f"Failed to combine {label}: {e}")
                return None

        # Suspend viewport refresh across the heavy work. On large selections
        # the per-command idle redraws in interactive Maya are what make this
        # appear to hang; the guard re-enables refresh even on error.
        with CoreUtils.suspended_refresh():
            if group_by_material:
                groups = MatUtils.group_objects_by_material(
                    objects,
                    cluster_by_distance=cluster_by_distance,
                    threshold=threshold,
                )
            elif cluster_by_distance:
                clusters = MatUtils._cluster_objects_by_distance(objects, threshold)
                groups = {f"cluster_{i}": c for i, c in enumerate(clusters)}
            else:
                return unite_group(objects, "selection")

            combined_meshes = []
            for key, group_objs in groups.items():
                if len(group_objs) < 2:
                    continue
                mesh = unite_group(group_objs, f"group {key}")
                if mesh:
                    combined_meshes.append(mesh)

            if not combined_meshes:
                cmds.warning("No groups found with more than 1 object to combine.")
                return None

            return combined_meshes

    @staticmethod
    @CoreUtils.undoable
    def group_objects(objects=None):
        """Group the given objects (or selection), center the pivot, and rename the group.

        Args:
            objects (list, optional): Objects to group. Defaults to selection.

        Returns:
            str: The created group.
        """
        if objects is None:
            objects = cmds.ls(selection=True, )

        objects = cmds.ls(as_strings(objects), objectsOnly=True)

        if objects:
            grp = cmds.group(objects)
            cmds.xform(grp, centerPivots=True)
            # Rename to first object's name. cmds.rename returns the
            # final name (Maya may auto-suffix on conflict).
            name = str(objects[0]).split("|")[-1].split(":")[-1]
            grp = cmds.rename(grp, name)
        else:  # If nothing selected, create empty group.
            grp = cmds.group(empty=True, name="null")

        return grp

    @staticmethod
    @CoreUtils.undoable
    def separate_objects(
        objects=None,
        by_material: bool = False,
        group_by_material: bool = False,
        center_pivots: bool = True,
        rename: bool = False,
    ) -> List:
        """Separate meshes into individual objects.

        Args:
            objects: Mesh transforms to process. Defaults to selection.
            by_material: If True, ensure each result has exactly one material —
                faces of each non-residual material are detached into their own
                shell before separation, so a connected mesh with multiple
                materials still splits cleanly.
            group_by_material: If True, parent the results under per-material
                transform groups (mirror of
                ``combine_objects(group_by_material=True)``). When this is set
                the return value is the list of new groups instead of the
                separated meshes.
            center_pivots: If True, center pivots on resulting transforms.
            rename: If True, rename resulting objects using the original name
                plus a location-based suffix.

        Returns:
            List of separated transform nodes — or, when ``group_by_material``
            is True, the list of created group transforms.
        """
        if objects is None:
            objects = cmds.ls(sl=True, objectsOnly=True)

        if not objects:
            cmds.warning("Nothing selected. Operation requires an object selection.")
            return []

        separated_objects: List[str] = []
        # Per-source results so the grouping helper can name the groups and
        # leaves after the originating object instead of after the material.
        results_by_source: List[Tuple[str, List[str]]] = []

        for obj in cmds.ls(as_strings(objects), objectsOnly=True, transforms=True):
            original_name = str(obj).split("|")[-1].split(":")[-1]
            current_results: List[str] = []
            separated = False

            # Material-based pre-pass: detach each non-residual material's
            # faces into their own shell so the subsequent polySeparate
            # produces one transform per material. Without this, a connected
            # multi-material mesh would not split, and a mesh with disjoint
            # shells that each carry multiple materials would only split per
            # shell.
            if by_material:
                mats = MatUtils.get_mats(obj, as_strings=True)
                if mats and len(mats) > 1:
                    chipped = False
                    for mat in mats[:-1]:
                        try:
                            faces = MatUtils.find_by_mat_id(mat, [obj], shell=False)
                            if faces:
                                cmds.polyChipOff(
                                    faces, dup=False, kft=True, ch=True, off=0
                                )
                                chipped = True
                        except Exception as e:
                            cmds.warning(
                                f"polyChipOff failed for '{obj}' / '{mat}': {e}"
                            )
                    if chipped:
                        cmds.delete(obj, ch=True)

            # Split disjoint shells. After the by_material pre-pass these
            # include one shell per material; otherwise it splits whatever
            # disjoint shells already existed on the input mesh.
            try:
                sep = cmds.polySeparate(obj, ch=False)
                if sep:
                    current_results = list(sep)
                    separated = True
            except Exception:
                pass

            if not separated:
                current_results = [obj]

            # Post-Processing
            if center_pivots:
                for res in current_results:
                    try:
                        cmds.xform(res, centerPivots=True)
                    except Exception:
                        pass

            # Location-suffix rename only applies on the flat-result path. The
            # group-by-material path runs its own source-name + letter/number
            # naming pass (see _group_results_by_material).
            if rename and not group_by_material and len(current_results) > 1:
                uuids = cmds.ls(current_results, uuid=True) or []
                try:
                    Naming.rename(current_results, to=original_name)
                    if uuids:
                        renamed = [
                            cmds.ls(u, long=False)[0]
                            for u in uuids
                            if cmds.ls(u)
                        ]
                        if renamed:
                            current_results = renamed
                    Naming.append_location_based_suffix(current_results)
                    if uuids:
                        renamed = [
                            cmds.ls(u, long=False)[0]
                            for u in uuids
                            if cmds.ls(u)
                        ]
                        if renamed:
                            current_results = renamed
                except Exception as e:
                    cmds.warning(f"Rename failed for {original_name}: {e}")

            results_by_source.append((original_name, current_results))
            separated_objects.extend(current_results)

        if group_by_material and separated_objects:
            return EditUtils._group_results_by_material(results_by_source)

        return separated_objects

    @staticmethod
    def _group_results_by_material(
        results_by_source: List[Tuple[str, List[str]]],
    ) -> List[str]:
        """Parent material-bucketed results under per-source groups.

        For every ``(source_name, results)`` entry, results are bucketed by
        material; one transform group is created per bucket. Groups are named
        ``{source}_{suffix}_grp`` and child meshes are renamed
        ``{source}_{suffix}`` (single child) or ``{source}_{suffix}_{inner}``
        (multiple children per bucket). The suffix scheme is letters
        (``A, B, ...``) when the count is ≤ 26, else zero-padded numerics.

        Returns:
            List of created group transforms.
        """
        groups: List[str] = []

        for source_name, results in results_by_source:
            buckets = MatUtils.group_objects_by_material(
                [r for r in results if cmds.objExists(r)]
            )
            items = [
                (k, [m for m in v if cmds.objExists(m)])
                for k, v in buckets.items()
            ]
            items = [(k, v) for k, v in items if v]
            if not items:
                continue

            grp_suffixes = ptk.StrUtils.sequential_suffixes(len(items))

            for grp_idx, (_mat_key, members) in enumerate(items):
                grp_suffix = grp_suffixes[grp_idx]
                grp = cmds.group(empty=True, name=f"{source_name}_{grp_suffix}_grp")

                if len(members) == 1:
                    inner_suffixes = [""]
                else:
                    inner_suffixes = [
                        f"_{s}"
                        for s in ptk.StrUtils.sequential_suffixes(
                            len(members), lowercase=True
                        )
                    ]
                for member, inner in zip(members, inner_suffixes):
                    new = _safe_rename(
                        member, f"{source_name}_{grp_suffix}{inner}"
                    )
                    _safe_parent(new, grp)

                try:
                    cmds.xform(grp, centerPivots=True)
                except Exception:
                    pass
                groups.append(grp)

        return groups

    @staticmethod
    def merge_vertices(objects, tolerance=0.001, selected_only=False):
        """Merge Vertices on the given objects.

        Parameters:
            objects (str/obj/list): The object(s) to merge vertices on.
            tolerance (float) = The maximum merge distance.
            selected_only (bool): Merge only the currently selected components
                (operates on the live selection; ``objects`` is not used).
        """
        if selected_only:  # Merge only selected components (selection-based;
            # runs once — this used to sit inside the per-object loop and
            # re-merged the same selection N times).
            if cmds.filterExpand(selectionMask=31):  # selectionMask=vertices
                sel = cmds.ls(selection=True)
                cmds.polyMergeVertex(
                    sel,
                    distance=tolerance,
                    alwaysMergeTwoVertices=True,
                    constructionHistory=True,
                )
            else:  # If selection type is edges or facets:
                mel.eval("MergeToCenter")
            return

        objects_str = [str(o) for o in ptk.make_iterable(objects)]
        for obj in NodeUtils.get_shape_node(objects_str):
            obj = str(obj)
            if cmds.objectType(obj) != "mesh":  # Ensure obj is a Mesh
                cmds.warning(f"Merge Vertices: Skipping non-mesh object: {obj}")
                continue  # Skip locators, cameras, etc.

            cmds.polyMergeVertex(
                f"{obj}.vtx[*]",
                distance=tolerance,
                alwaysMergeTwoVertices=False,
                constructionHistory=False,
            )

        cmds.select(clear=True)
        cmds.select(objects_str)

    @staticmethod
    @CoreUtils.undoable
    def merge_vertex_pairs(vertices):
        """Merge vertices in pairs by moving them to their center and merging.

        Parameters:
            vertices (list): A list of vertices to merge in pairs.
        """
        if not vertices:
            cmds.warning("No vertices provided for merging.")
            return

        # Flatten the list to ensure all vertices are individual nodes
        vertices = cmds.ls(vertices, flatten=True)
        if len(vertices) % 2 != 0:
            cmds.warning(
                "An odd number of vertices was provided; the last vertex will be ignored."
            )

        vertex_pairs = [
            (vertices[i], vertices[i + 1]) for i in range(0, len(vertices) - 1, 2)
        ]

        for vtx1, vtx2 in vertex_pairs:
            try:  # Get the world-space positions of the vertices
                pos1 = cmds.pointPosition(vtx1, world=True)
                pos2 = cmds.pointPosition(vtx2, world=True)

                # Calculate the midpoint
                center_point = [(a + b) / 2.0 for a, b in zip(pos1, pos2)]

                # Move both vertices to the center point
                cmds.xform(vtx1, worldSpace=True, translation=center_point)
                cmds.xform(vtx2, worldSpace=True, translation=center_point)

            except Exception as e:
                cmds.warning(f"Failed to move vertices {vtx1} and {vtx2}: {e}")

        cmds.polyMergeVertex(vertices, d=0.001)  # Merge the vertices

    @staticmethod
    @CoreUtils.undoable
    def detach_components(
        components=None,
        duplicate: bool = True,
        separate: bool = True,
        offset: bool = False,
        keep_faces_together: bool = True,
    ) -> Optional[List]:
        """Detach mesh components (vertices or faces) from their parent mesh.

        For vertices: Splits the vertex, disconnecting faces that share it.
        For faces: Extracts faces using polyChipOff, optionally duplicating and separating.
        For other components: Falls back to Maya's DetachComponent command.

        Parameters:
            components: The components to detach. If None, uses current selection.
            duplicate (bool): For faces, duplicate them leaving the original mesh unchanged.
            separate (bool): For faces, separate the detached faces into individual objects.
            offset (bool): For faces, offset/translate the extracted faces from their
                original position. If False (default), faces maintain their original shape.
            keep_faces_together (bool): If True (default), detached faces remain connected.
                If False, each face is detached individually.

        Returns:
            Optional[List]: The resulting objects after separation, or the polyChipOff node
                for faces, or None for vertices/other component types.

        Example:
            >>> # Detach selected faces as duplicates into separate objects
            >>> EditUtils.detach_components(duplicate=True, separate=True)
            >>> # Extract faces destructively without separating
            >>> EditUtils.detach_components(cmds.ls(sl=1), duplicate=False, separate=False)
        """
        if components is None:
            components = cmds.ls(sl=True)

        if not components:
            cmds.warning("Nothing selected. Operation requires a component selection.")
            return None

        # Check component selection mode
        vertex_mode = cmds.selectType(q=True, vertex=True)
        face_mode = cmds.selectType(q=True, facet=True)

        if vertex_mode:
            mel.eval("polySplitVertex")
            return None

        elif face_mode:
            # Get the parent objects before polyChipOff modifies the mesh
            parent_objects = list(set(cmds.ls(components, objectsOnly=True)))

            extract = cmds.polyChipOff(
                components,
                ch=True,
                keepFacesTogether=keep_faces_together,
                dup=duplicate,
                off=offset,
            )

            if separate:
                # polySeparate must be called on transform/mesh objects, not components
                split_objects = cmds.polySeparate(parent_objects)
                # Select the last object (typically the extracted/duplicated piece)
                if split_objects:
                    cmds.select(split_objects[-1])
                return split_objects

            return extract

        else:
            mel.eval("DetachComponent")
            return None

    @staticmethod
    @CoreUtils.undoable
    def decimate(
        objects=None,
        percentage: float = 50.0,
        preserve_borders: bool = True,
        preserve_hard_edges: bool = True,
        preserve_uv_borders: bool = True,
        preserve_quads: bool = True,
        symmetry: bool = False,
        symmetry_tolerance: float = 0.01,
        delete_history: bool = True,
    ) -> List[str]:
        """Decimate (``polyReduce``) meshes toward a target reduction percentage.

        A reusable wrapper over ``polyReduce`` with the boundary-preserving
        defaults you almost always want — keep open borders, hard/crease edges,
        UV/color borders, and bias toward quads — exposed as plain flags. Shared
        by the Subdivision panel's Decimate button and the procedural Curtain
        generator.

        Parameters:
            objects: Mesh transforms (uses the selection when ``None``).
            percentage: Percent of faces to remove (``0``–``99``; clamped).
            preserve_borders: Hold open mesh + face-group borders fixed.
            preserve_hard_edges: Hold hard and crease edges.
            preserve_uv_borders: Hold UV (map) and color borders.
            preserve_quads: Bias the solver toward keeping quads.
            symmetry: Reduce symmetrically (virtual symmetry about X).
            symmetry_tolerance: Tolerance for the symmetry plane.
            delete_history: Delete construction history afterward.

        Returns:
            The decimated mesh transforms.
        """
        objects = (
            cmds.ls(objects or cmds.ls(selection=True), objectsOnly=True, type="transform")
            or []
        )
        if not objects:
            return []
        pct = max(0.0, min(99.0, float(percentage)))
        if pct <= 0.0:  # nothing to remove — skip the no-op polyReduce node
            return objects
        # polyReduce rejects multi-object selections ("Doesn't work with multiple
        # objects selected"), so reduce each mesh independently.
        for obj in objects:
            cmds.polyReduce(
                obj,
                version=1,
                percentage=pct,
                keepBorder=preserve_borders,
                keepFaceGroupBorder=preserve_borders,
                keepHardEdge=preserve_hard_edges,
                keepCreaseEdge=preserve_hard_edges,
                keepMapBorder=preserve_uv_borders,
                keepColorBorder=preserve_uv_borders,
                keepQuadsWeight=1.0 if preserve_quads else 0.0,
                preserveTopology=True,
                useVirtualSymmetry=1 if symmetry else 0,
                symmetryTolerance=symmetry_tolerance,
                replaceOriginal=True,
                cachingReduce=True,
                constructionHistory=not delete_history,
            )
        if delete_history:
            cmds.delete(objects, constructionHistory=True)
        return objects

    @staticmethod
    @CoreUtils.undoable
    def dissolve_coplanar(
        objects=None,
        angle_tolerance: float = 1.0,
        delete_history: bool = True,
    ) -> List[str]:
        """Planar decimation (limited dissolve) — merge faces across near-coplanar edges.

        Removes every *interior* edge whose two adjacent faces are within
        ``angle_tolerance`` degrees of coplanar (merging them into larger
        n-gons), leaving feature edges — creases, corners, silhouette, open
        borders — untouched. At a small tolerance this is **lossless** on
        hard-surface meshes: it strips the interior tessellation of flat regions
        without moving any point that defines the shape. Curved/organic surfaces
        have no coplanar interior edges, so it does little there — use
        :meth:`decimate` (quadric error metric) for those.

        Unlike :meth:`decimate` (a percentage/error-budget QEM reduce that
        triangulates), this is angle-driven and keeps clean n-gons/quads.

        Note: dissolving an edge merges the UVs of its two faces, so prefer a
        small tolerance on UV'd meshes.

        Parameters:
            objects: Mesh transforms (uses the selection when ``None``).
            angle_tolerance: Max dihedral angle (degrees) treated as coplanar.
            delete_history: Delete construction history afterward.

        Returns:
            The processed mesh transforms.
        """
        objects = (
            cmds.ls(objects or cmds.ls(selection=True), objectsOnly=True, type="transform")
            or []
        )
        if not objects:
            return []
        tol = math.radians(max(0.0, float(angle_tolerance)))
        for obj in objects:
            shapes = (
                cmds.listRelatives(
                    obj, shapes=True, type="mesh", noIntermediate=True, fullPath=True
                )
                or []
            )
            if not shapes:
                continue
            sel = om.MSelectionList()
            sel.add(shapes[0])
            dag = sel.getDagPath(0)
            mesh = om.MFnMesh(dag)
            edge_it = om.MItMeshEdge(dag)
            flat_edges = []
            while not edge_it.isDone():
                # Interior edges only — boundary edges are the silhouette.
                if not edge_it.onBoundary():
                    faces = edge_it.getConnectedFaces()
                    if len(faces) == 2:
                        # Object space: coplanarity is intrinsic to the mesh, so
                        # the result shouldn't depend on the object's transform.
                        n0 = mesh.getPolygonNormal(faces[0], om.MSpace.kObject)
                        n1 = mesh.getPolygonNormal(faces[1], om.MSpace.kObject)
                        if n0.angle(n1) <= tol:
                            flat_edges.append(edge_it.index())
                edge_it.next()
            if flat_edges:
                cmds.polyDelEdge(
                    [f"{obj}.e[{i}]" for i in flat_edges],
                    cleanVertices=True,
                    constructionHistory=not delete_history,
                )
                if delete_history:
                    cmds.delete(obj, constructionHistory=True)
        return objects

    @staticmethod
    def get_all_faces_on_axis(obj, axis="x", pivot="center", use_object_axes=True):
        """Get all faces on the specified axis of an object.

        Parameters:
            obj (str/obj): The name of the geometry.
            axis (str): The axis, e.g. 'x', '-x', 'y', '-y', 'z', '-z'.
            pivot (str or tuple): Defines the face selection pivot:
                - `"center"` (default) → Bounding box center.
                - `"xmin"`, `"xmax"`, `"ymin"`, `"ymax"`, `"zmin"`, `"zmax"` → Bounding box min/max.
                - `"object"` → Uses the object's pivot. (object-space frame)
                - `"manip"` → Uses the manipulator pivot. (object-space frame)
                - `"baked"` → Uses the baked rotate pivot. (object-space frame)
                - `"world"` → Uses world origin (0,0,0).
                - A tuple `(x, y, z)` → World-space pivot. Treated as object-space
                  when ``use_object_axes`` is True (object axes were the active frame
                  upstream).
            use_object_axes (bool): When True, the pivot value is evaluated in the
                object's local frame; faces are compared in object-space coordinates.
                Only takes effect when ``pivot`` is an object-space type
                (``"object"`` / ``"manip"`` / ``"baked"``) or a tuple.

        Returns:
            list: A list of faces on the specified axis.
        """
        obj_list = cmds.ls(as_strings(obj), type="transform")
        if not obj_list:
            raise ValueError(f"No transform node found with the name: {obj}")
        obj = obj_list[0]

        axis = XformUtils.convert_axis(axis)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]

        # Pivot type controls the cutting frame. Object-y pivots and tuples
        # carried over from object-space cuts evaluate in the object frame;
        # everything else (world, center, bbox keys) stays in world space.
        is_tuple_pivot = isinstance(pivot, (tuple, list)) and len(pivot) == 3
        is_object_pivot = isinstance(pivot, str) and pivot in {
            "object",
            "manip",
            "baked",
        }
        use_object_space = use_object_axes and (is_object_pivot or is_tuple_pivot)

        if use_object_space:
            obj_matrix = om.MMatrix(cmds.xform(obj, q=True, m=True, ws=True))
            if is_tuple_pivot:
                world_pt = [float(v) for v in pivot]
            else:
                world_pt = list(XformUtils.get_operation_axis_pos(obj, pivot))
            local_pt = om.MPoint(*world_pt) * obj_matrix.inverse()
            pivot_value = float(local_pt[axis_index])
            face_world_space = False
        else:
            pivot_value = XformUtils.get_operation_axis_pos(obj, pivot, axis_index)
            face_world_space = True

        if axis.startswith("-"):
            compare = lambda v: v <= pivot_value + 1e-5
            bbox_keys = ["xmax", "ymax", "zmax"]
        else:
            compare = lambda v: v >= pivot_value - 1e-5
            bbox_keys = ["xmin", "ymin", "zmin"]

        bbox_value = bbox_keys[axis_index]
        relevant_faces = []
        for shape in NodeUtils.get_shapes(obj):
            if cmds.nodeType(shape) in ["mesh", "nurbsSurface", "subdiv"]:
                for face in cmds.ls(f"{shape}.f[*]", fl=True) or []:
                    bb_val = XformUtils.get_bounding_box(
                        face, value=bbox_value, world_space=face_world_space
                    )
                    if compare(bb_val):
                        relevant_faces.append(face)

        return relevant_faces

    @staticmethod
    def _compose_cut_rotation(axis, world_matrix=None):
        """Compose the polyCut ``ro`` (euler degrees, XYZ order) for the given axis.

        The base rotation orients the default cut plane (normal = +Z) so that its
        normal aligns with ``axis``. When ``world_matrix`` is supplied, the
        object's world rotation is composed *after* the base rotation so the cut
        plane follows object-local axes.

        Parameters:
            axis (str): One of 'x', '-x', 'y', '-y', 'z', '-z'.
            world_matrix (om.MMatrix, optional): The object's world matrix. When
                ``None`` the rotation is for world-aligned cutting.

        Returns:
            list[float]: Euler angles in degrees (XYZ order).
        """
        base_rotations = {
            "x": (0.0, 90.0, 0.0),
            "-x": (0.0, -90.0, 0.0),
            "y": (-90.0, 0.0, 0.0),
            "-y": (90.0, 0.0, 0.0),
            "z": (0.0, 0.0, 0.0),
            "-z": (0.0, 0.0, 180.0),
        }
        base = base_rotations.get(axis, (0.0, 0.0, 0.0))

        if world_matrix is None:
            return list(base)

        base_eul = om.MEulerRotation(
            math.radians(base[0]), math.radians(base[1]), math.radians(base[2])
        )
        obj_eul = om.MTransformationMatrix(world_matrix).rotation()
        # Apply base first, then object rotation. With row-vector convention:
        # point * base * obj == (point * base) * obj.
        combined_mat = base_eul.asMatrix() * obj_eul.asMatrix()
        combined = om.MTransformationMatrix(combined_mat).rotation()
        return [
            math.degrees(combined.x),
            math.degrees(combined.y),
            math.degrees(combined.z),
        ]

    @classmethod
    @CoreUtils.undoable
    def cut_along_axis(
        cls,
        objects,
        axis="x",
        pivot="center",
        amount=1,
        offset=0,
        invert=False,
        ortho=False,
        delete=False,
        mirror=False,
        use_object_axes=True,
    ):
        """Cut objects along the specified axis.

        The pivot type chooses the cutting frame:
            - ``"object"`` / ``"manip"`` / ``"baked"`` → cuts follow the object's
              local axes (so rotated objects are cut along their own X/Y/Z).
            - ``"world"`` / ``"center"`` / ``"xmin"`` / etc. → cuts use world axes.
            - tuple ``(x, y, z)`` → world-space pivot, world axes.

        Parameters:
            objects (str/obj/list): The object(s) to cut.
            axis (str): The axis to cut along ('x', '-x', 'y', '-y', 'z', '-z').
            amount (int): Number of cuts.
            pivot (str or tuple): See above.
            offset (float): Offset along the axis from the pivot.
            invert (bool): Invert the axis direction.
            ortho (bool): Use the orthogonal axis.
            delete (bool): Delete faces on the +axis half after cutting.
            mirror (bool): When delete=True, mirror the surviving half across the cut.
            use_object_axes (bool): Master switch for object-space behavior. When
                False, all cuts use world axes regardless of pivot. Default True.
        """
        axis = XformUtils.convert_axis(axis, invert=invert, ortho=ortho)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]
        sign = -1 if axis.startswith("-") else 1

        # The pivot type drives the cutting frame; ``use_object_axes`` is a
        # global override that can force world space.
        use_object_space = (
            use_object_axes
            and isinstance(pivot, str)
            and pivot in {"object", "manip", "baked"}
        )

        for node in cmds.ls(as_strings(objects), type="transform", flatten=True):
            if NodeUtils.is_group(node):
                continue

            world_matrix = (
                om.MMatrix(cmds.xform(node, q=True, m=True, ws=True))
                if use_object_space
                else None
            )

            bbox = XformUtils.get_bounding_box(
                node,
                "xmin|ymin|zmin|xmax|ymax|zmax",
                world_space=not use_object_space,
            )
            if not bbox or len(bbox) < 6:
                cmds.warning(
                    f"Skipping cut_along_axis: Unable to retrieve bounding box for {node}"
                )
                continue

            axis_length = bbox[axis_index + 3] - bbox[axis_index]
            if axis_length == 0:
                cmds.warning(
                    f"Skipping cut: Axis length is zero along {axis} for {node}."
                )
                continue

            # Pivot value in the cutting frame (object-local or world).
            if use_object_space:
                world_pivot = list(XformUtils.get_operation_axis_pos(node, pivot))
                local_pivot = om.MPoint(*world_pivot) * world_matrix.inverse()
                pivot_value = float(local_pivot[axis_index])
            elif isinstance(pivot, (tuple, list)) and len(pivot) == 3:
                pivot_value = float(pivot[axis_index])
            else:
                pivot_value = XformUtils.get_operation_axis_pos(node, pivot, axis_index)

            pivot_value += offset * sign
            cut_spacing = axis_length / (amount + 1)
            rotation = cls._compose_cut_rotation(axis, world_matrix)

            cut_positions = []
            for i in range(amount):
                # bbox min as anchor for non-axis components — any point on the
                # cut plane works since rotation alone defines orientation.
                cut_point = list(bbox[:3])
                cut_point[axis_index] = (
                    pivot_value
                    - ((amount - 1) * cut_spacing / 2)
                    + (cut_spacing * i)
                )
                cut_positions.append(cut_point[axis_index])

                if use_object_space:
                    world_cut_point = list(om.MPoint(*cut_point) * world_matrix)[:3]
                else:
                    world_cut_point = cut_point

                cmds.polyCut(
                    node, df=False, pc=world_cut_point, ro=rotation, ch=True
                )

            if delete:
                # amount==0 adds no cut lines, so fall back to the pivot
                # position (which equals the single amount==1 cut position).
                deepest_cut = (
                    pivot_value
                    if not cut_positions
                    else (cut_positions[-1] if sign == 1 else cut_positions[0])
                )
                pivot_point = list(bbox[:3])
                pivot_point[axis_index] = deepest_cut

                if use_object_space:
                    world_pivot_point = list(
                        om.MPoint(*pivot_point) * world_matrix
                    )[:3]
                else:
                    world_pivot_point = pivot_point

                cls.delete_along_axis(
                    node,
                    axis,
                    pivot=tuple(world_pivot_point),
                    delete_history=False,
                    mirror=mirror,
                    use_object_axes=use_object_axes,
                )

    @classmethod
    @CoreUtils.undoable
    def delete_along_axis(
        cls,
        objects,
        axis="-x",
        pivot="center",
        delete_history=True,
        mirror=False,
        use_object_axes=True,
    ):
        """Delete faces along the specified axis and optionally mirror the result.

        Parameters:
            objects (str/obj/list): The object(s) to delete faces from.
            axis (str): The axis to delete along ('x', '-x', 'y', '-y', 'z', '-z'). Default is '-x'.
            pivot (str or tuple): Defines the deletion pivot (passed to get_operation_axis_pos).
            delete_history (bool): If True, delete the construction history of the object(s). Default is True.
            mirror (bool): If True, mirrors the result after deletion using the cut position as the pivot.
            use_object_axes (bool): If True, uses object's local axes for face selection when pivot suggests object space.
        """
        axis = XformUtils.convert_axis(axis)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]

        for node in cmds.ls(as_strings(objects), type="transform", flatten=True):
            if NodeUtils.is_group(node):
                continue

            if delete_history:
                cmds.delete(node, ch=True)

            bounding_box = XformUtils.get_bounding_box(
                node, "xmin|ymin|zmin|xmax|ymax|zmax", True
            )
            if not bounding_box or len(bounding_box) < 6:
                cmds.warning(
                    f"Skipping delete_along_axis: Unable to retrieve bounding box for {node}"
                )
                continue

            # Get pivot position from get_operation_axis_pos
            pivot_value = XformUtils.get_operation_axis_pos(node, pivot, axis_index)

            # Updated to use new pivot format
            faces = cls.get_all_faces_on_axis(node, axis, pivot, use_object_axes)
            if not faces:
                cmds.warning(f"No faces found along {axis} on {node}. Skipping deletion.")
                continue

            total_faces = cmds.polyEvaluate(node, face=True)
            if len(faces) == total_faces:
                cmds.delete(node)
            else:
                cmds.delete(faces)

            if mirror:  # Mirror if enabled
                mirror_axis = axis.lstrip("-")  # Get base axis without negative sign
                mirror_pivot = list(XformUtils.get_operation_axis_pos(node, pivot))
                mirror_pivot[axis_index] = (
                    pivot_value  # Ensure pivot is at the cut plane
                )
                cls.mirror(
                    node,
                    axis=mirror_axis,
                    pivot=tuple(mirror_pivot),
                    mergeMode=1,
                    use_object_axes=use_object_axes,
                )

    @classmethod
    @CoreUtils.undoable
    @DisplayUtils.add_to_isolation
    def mirror(
        cls,
        objects,
        axis: str = "x",
        pivot: Union[str, tuple] = "object",
        mergeMode: int = -1,
        uninstance: bool = False,
        use_object_axes: bool = True,
        delete_original: bool = False,
        **kwargs,
    ):
        """Mirror geometry across a given axis.

        Parameters:
            objects (obj): The objects to mirror.
            axis (str): The axis to mirror across. Accepts:
                - 'x', '-x', 'y', '-y', 'z', '-z'
            pivot (str or tuple): Defines the mirror pivot:
                - `"world"` → Mirrors at the world origin.
                - `"object"` → Mirrors at the object's pivot.
                - Any valid bounding box keyword (`"xmin"`, `"ymax"`, `"center"`, etc.).
                - A tuple `(x, y, z)` → Uses a specified world-space pivot.
            mergeMode (int): Defines how the geometry is merged after mirroring. Accepts:
                - `-1` → Custom separate mode (default). valid: -1, 0, 1, 2, 3
            uninstance (bool): If True, uninstances the object before mirroring.
            use_object_axes (bool): If True, computes the mirror pivot in object-local
                space (relevant when the object is rotated and pivot is "object", "manip", or "baked").
            delete_original (bool): If True, deletes the original half after mirroring
                (only applies to ``mergeMode=-1``).
            kwargs: Additional arguments for polyMirrorFace.

        Returns:
            (obj or list) The mirrored object's transform node or list of transform nodes.
        """
        kwargs["ch"] = True  # Ensure construction history
        kwargs["worldSpace"] = True  # Always force world space

        axis_mapping = {
            "x": (0, 0),  # Mirror across X-axis, positive direction
            "-x": (0, 1),  # Mirror across X-axis, negative direction
            "y": (1, 0),  # Mirror across Y-axis, positive direction
            "-y": (1, 1),  # Mirror across Y-axis, negative direction
            "z": (2, 0),  # Mirror across Z-axis, positive direction
            "-z": (2, 1),  # Mirror across Z-axis, negative direction
        }

        if axis not in axis_mapping:
            raise ValueError(
                f"Invalid axis '{axis}'. Use one of {list(axis_mapping.keys())}"
            )

        axis_val, axis_direction = axis_mapping[axis]
        kwargs["axis"] = axis_val
        kwargs["axisDirection"] = axis_direction

        original_objects = cmds.ls(as_strings(objects), type="transform", flatten=True)
        results = []

        # Determine whether to compute pivot in object space
        use_object_space = (
            use_object_axes
            and isinstance(pivot, str)
            and pivot
            in {
                "object",
                "manip",
                "baked",
            }
        )

        for obj in original_objects:
            if uninstance:
                uninstanced_result = NodeUtils.uninstance(obj)
                if uninstanced_result:
                    obj = uninstanced_result[0]

            # Compute pivot position
            if use_object_space:
                # Compute pivot in object-local space, then transform to world
                obj_matrix = om.MMatrix(cmds.xform(str(obj), q=True, m=True, ws=True))
                if pivot == "object":
                    local_pivot = [0.0, 0.0, 0.0]
                elif pivot == "manip":
                    world_manip = XformUtils.get_operation_axis_pos(obj, "manip")
                    lp = om.MPoint(world_manip) * obj_matrix.inverse()
                    local_pivot = [float(lp[0]), float(lp[1]), float(lp[2])]
                else:  # "baked"
                    world_pt = XformUtils.get_operation_axis_pos(obj, pivot)
                    lp = om.MPoint(world_pt) * obj_matrix.inverse()
                    local_pivot = [float(lp[0]), float(lp[1]), float(lp[2])]
                # Transform local pivot back to world space for polyMirrorFace
                world_pivot = list(om.MPoint(local_pivot) * obj_matrix)
                pivot_point = world_pivot[:3]
            else:
                pivot_point = list(XformUtils.get_operation_axis_pos(obj, pivot))

            kwargs["pivot"] = tuple(pivot_point)

            # Handle custom separate mode
            custom_separate = mergeMode == -1
            # mergeMode 0 = "do not merge" in polyMirrorFace, keeps halves separable
            kwargs["mergeMode"] = 0 if custom_separate else mergeMode

            # Execute polyMirrorFace
            mirror_nodes = cmds.polyMirrorFace(obj, **kwargs)
            mirror_node = mirror_nodes[0]

            # Custom separate: use separate_mirrored_mesh for proper separation
            if custom_separate:
                new_obj = cls.separate_mirrored_mesh(
                    mirror_node, delete_original=delete_original
                )
                if new_obj is not None:
                    results.append(new_obj)
                    # Also keep the original half (unless delete was requested)
                    if not delete_original and cmds.objExists(obj):
                        results.append(obj)
                else:
                    # Separation failed, return the combined object
                    results.append(obj)
            else:
                # Conform normals to fix potential reversal from mirror
                cmds.polyNormal(obj, normalMode=2, ch=False)
                results.append(obj)

        return ptk.format_return(results, objects)

    @staticmethod
    def separate_mirrored_mesh(
        mirror_node: str,
        preserve_pivot: bool = True,
        delete_original: bool = False,
    ) -> Optional[str]:
        """Separate mirrored geometry and clean up hierarchy, history, and parenting.

        Parameters:
            mirror_node (str): The polyMirrorFace node for face connection.

        Returns:
            The cleaned, renamed transform (or None on failure).
        """
        mirror_node = str(mirror_node)
        # Get the transform node for the mirror operation
        mirror_transform = NodeUtils.get_transform_node(mirror_node)
        if not mirror_transform:
            # Try to find via connections if it's a history node
            try:
                mesh_outputs = (
                    cmds.listConnections(
                        f"{mirror_node}.output", type="mesh", source=False, destination=True
                    )
                    or []
                )
                if mesh_outputs:
                    parent_xform = (
                        cmds.listRelatives(mesh_outputs[0], parent=True, fullPath=True)
                        or [None]
                    )[0]
                    mirror_transform = parent_xform
            except Exception:
                pass

        if not mirror_transform:
            cmds.warning(f"[Mirror] No transform node found for {mirror_node}.")
            return None

        # Ensure mirror_transform is a single node
        if isinstance(mirror_transform, list):
            mirror_transform = mirror_transform[0]
        mirror_transform = str(mirror_transform)

        try:
            sep_nodes = cmds.polySeparate(mirror_transform, uss=True, inp=True)
            if len(sep_nodes) < 2:
                cmds.warning(
                    f"[Separate] polySeparate returned insufficient nodes for {mirror_transform}"
                )
                return None

            orig_obj, new_obj = sep_nodes[:2]

            # Only set up face connections if we have a polySeparate node
            if len(sep_nodes) > 2:
                sep_node = sep_nodes[-1]
                try:
                    cmds.connectAttr(
                        f"{mirror_node}.firstNewFace",
                        f"{sep_node}.startFace",
                        force=True,
                    )
                    cmds.connectAttr(
                        f"{mirror_node}.lastNewFace",
                        f"{sep_node}.endFace",
                        force=True,
                    )
                except Exception as e:
                    cmds.warning(f"[Separate] Failed to connect face attributes: {e}")

            parent = NodeUtils.get_parent(mirror_transform, type=None, full_path=True)
            temp_parent = NodeUtils.get_parent(orig_obj, type=None, full_path=True)

            if temp_parent:
                temp_parent = cmds.rename(
                    temp_parent, f"{str(temp_parent).split('|')[-1]}__TMP"
                )

                # Parent both objects (None parent means world)
                for node in [orig_obj, new_obj]:
                    if parent:
                        cmds.parent(node, parent)
                    else:
                        cmds.parent(node, world=True)

            # Pivot handling: preserve original pivot (default) or center.
            try:
                if preserve_pivot:
                    # Get original pivot(s) in world space
                    orig_rp = cmds.xform(orig_obj, q=True, ws=True, rp=True)
                    orig_sp = cmds.xform(orig_obj, q=True, ws=True, sp=True)
                    cmds.xform(new_obj, ws=True, rp=orig_rp)
                    cmds.xform(new_obj, ws=True, sp=orig_sp)
                else:
                    center = XformUtils.get_bounding_box(
                        [orig_obj, new_obj], "center", world_space=True
                    )
                    cmds.xform(new_obj, piv=center, ws=True)
            except Exception as e:
                cmds.warning(f"[Separate] Pivot handling failed for {new_obj}: {e}")

            # Conform normals to fix potential reversal from mirror+separate
            for node in [orig_obj, new_obj]:
                try:
                    cmds.polyNormal(node, normalMode=2, ch=False)
                except Exception:
                    pass

            # Cleanup - only delete construction history, not the objects themselves
            for obj in [orig_obj, new_obj]:
                try:
                    cmds.delete(obj, constructionHistory=True)
                except Exception as e:
                    cmds.warning(f"Failed to delete construction history for {obj}: {e}")

            # Capture original name before potential deletion
            orig_name = str(orig_obj).split('|')[-1]

            # Delete original half if requested
            if delete_original:
                try:
                    cmds.delete(orig_obj)
                except Exception as e:
                    cmds.warning(f"Failed to delete original object {orig_obj}: {e}")

            # Delete the temporary parent
            if temp_parent:
                try:
                    cmds.delete(temp_parent, constructionHistory=True)
                except Exception as e:
                    cmds.warning(f"Failed to delete temporary parent {temp_parent}: {e}")

            # Rename to match original object — capture the resolved name
            # since cmds.rename may mangle (e.g. when the orig_name is still
            # held by the temp parent that survived deletion).
            try:
                new_obj = cmds.rename(new_obj, orig_name)
            except Exception as e:
                cmds.warning(f"Failed to rename {new_obj} to {orig_name}: {e}")

            return new_obj

        except Exception as e:
            cmds.warning(
                f"[Separate] polySeparate operation failed for {mirror_transform}: {e}"
            )
            return None

    @staticmethod
    def get_overlapping_duplicates(
        objects: Optional[List] = None,
        retain_given_objects: bool = False,
        select: bool = False,
        verbose: bool = False,
    ) -> set:
        """Find duplicate, overlapping geometry at the object (transform) level.

        Parameters:
            objects (list): A list of objects to check for duplicates. If None, checks all transforms in the scene.
            retain_given_objects (bool): If True, retains the given objects in the result set.
            select (bool): If True, selects the found duplicates in the scene.
            verbose (bool): If True, prints detailed information about found duplicates.

        Returns:
            set: Overlapping duplicate transform names (strings).
        """
        import maya.cmds as cmds
        from collections import defaultdict

        # Get mesh transforms efficiently
        if objects is None:
            # Get all mesh transforms
            shapes = cmds.ls(type="mesh", noIntermediate=True)
            # listRelatives can return None if no parents (unlikely for shapes)
            scene_objs = list(
                set(cmds.listRelatives(shapes, parent=True, fullPath=True) or [])
            )
        else:
            # Filter provided objects
            # Ensure we have full paths for robustness
            # Handle nodes or strings
            objects = [str(o) for o in objects]
            objects = cmds.ls(as_strings(objects), long=True)
            scene_objs = []
            for obj in objects:
                shapes = cmds.listRelatives(
                    obj, shapes=True, noIntermediate=True, fullPath=True
                )
                if shapes and cmds.nodeType(shapes[0]) == "mesh":
                    scene_objs.append(obj)

        # Fingerprint by bounding box min/max (rounded), topology, and
        # sampled vertex positions.  Bbox + poly-count alone can match
        # objects that are geometrically different (same counts, same
        # extents but different shapes), producing false positives.
        _R = 5  # Rounding precision for all spatial comparisons
        obj_fingerprints = {}
        for obj in scene_objs:
            bbox = cmds.xform(obj, query=True, ws=True, bb=True)
            if not bbox:
                continue

            bbox_min = (round(bbox[0], _R), round(bbox[1], _R), round(bbox[2], _R))
            bbox_max = (round(bbox[3], _R), round(bbox[4], _R), round(bbox[5], _R))

            # Single polyEvaluate call — reuse for both topo hash and vtx count
            poly_eval = cmds.polyEvaluate(obj)
            topo = str(poly_eval)

            # Sample vertex positions for a stronger fingerprint.
            vtx_sample = ()
            try:
                vtx_count = (
                    poly_eval.get("vertex", 0) if isinstance(poly_eval, dict) else 0
                )
                if vtx_count > 0:
                    # Pick up to 8 evenly-spaced vertex indices
                    sample_count = min(8, vtx_count)
                    step = max(1, vtx_count // sample_count)
                    components = [f"{obj}.vtx[{i * step}]" for i in range(sample_count)]
                    # Batch query — single Maya round-trip for all vertices
                    flat = (
                        cmds.xform(
                            components,
                            query=True,
                            worldSpace=True,
                            translation=True,
                        )
                        or []
                    )
                    vtx_sample = tuple(
                        (
                            round(flat[i], _R),
                            round(flat[i + 1], _R),
                            round(flat[i + 2], _R),
                        )
                        for i in range(0, len(flat), 3)
                    )
            except Exception as exc:
                import logging

                logging.getLogger(__name__).debug(
                    "Vertex sampling failed for %s: %s", obj, exc
                )

            obj_fingerprints[obj] = (bbox_min, bbox_max, topo, vtx_sample)

        if objects is None:
            selected_set = set(
                cmds.ls(list(obj_fingerprints.keys()), sl=True, long=True)
            )
        else:
            selected_set = set(cmds.ls(as_strings(objects), long=True))

        fingerprint_groups = defaultdict(list)
        for obj, fingerprint in obj_fingerprints.items():
            fingerprint_groups[fingerprint].append(obj)

        duplicates = set()
        for group in fingerprint_groups.values():
            if len(group) > 1:
                if selected_set:
                    if selected_set & set(group):
                        if retain_given_objects:
                            duplicates.update(
                                obj for obj in group if obj not in selected_set
                            )
                        else:
                            duplicates.update(group[1:])
                else:
                    duplicates.update(group[1:])

        if verbose:
            for obj in sorted(duplicates):
                print(f"# Found: overlapping duplicate object: {obj} #")
        if verbose or select:
            print(f"# {len(duplicates)} overlapping duplicate objects found. #")
        if select and duplicates:
            cmds.select(list(duplicates), r=True)
        return duplicates

    @staticmethod
    def find_non_manifold_vertex(objects, select=1):
        """Locate a connected vertex of non-manifold geometry where the faces share a single vertex.

        Parameters:
            objects (str/obj/list): A polygon mesh, or a list of meshes.
            select (int): Select any found non-manifold vertices. 0=off, 1=on, 2=on while keeping any existing vertex selections. (default: 1)

        Returns:
            (set) any found non-manifold verts.
        """
        cmds.undoInfo(openChunk=True)
        nonManifoldVerts = set()

        vertices = Components.get_components(objects, "vertices")
        for vertex in vertices:
            connected_faces = cmds.polyListComponentConversion(
                vertex, fromVertex=1, toFace=1
            )  # pm.mel.PolySelectConvert(1) #convert to faces
            connected_faces_flat = cmds.ls(
                connected_faces, flatten=1
            )  # selectedFaces = cmds.ls(sl=1, flatten=1)

            # get a list of the edges of each face that is connected to the original vertex.
            edges_sorted_by_face = []
            for face in connected_faces_flat:
                connected_edges = cmds.polyListComponentConversion(
                    face, fromFace=1, toEdge=1
                )  # pm.mel.PolySelectConvert(1) #convert to faces
                connected_edges_flat = [
                    str(i) for i in cmds.ls(connected_edges, flatten=1)
                ]  # selectedFaces = cmds.ls(sl=1, flatten=1)
                edges_sorted_by_face.append(connected_edges_flat)

            out = (
                []
            )  # 1) take first set A from list. 2) for each other set B in the list do if B has common element(s) with A join B into A; remove B from list. 3) repeat 2. until no more overlap with A. 4) put A into outpup. 5) repeat 1. with rest of list.
            while len(edges_sorted_by_face) > 0:
                first, rest = (
                    edges_sorted_by_face[0],
                    edges_sorted_by_face[1:],
                )  # first list, all other lists, of the list of lists.
                first = set(first)

                lf = -1
                while len(first) > lf:
                    lf = len(first)

                    rest2 = []
                    for r in rest:
                        if len(first.intersection(set(r))) > 0:
                            first |= set(r)
                        else:
                            rest2.append(r)
                    rest = rest2

                out.append(first)
                edges_sorted_by_face = rest

            if len(out) > 1:
                nonManifoldVerts.add(vertex)
        cmds.undoInfo(closeChunk=True)

        if select == 2:
            cmds.select(nonManifoldVerts, add=1)
        elif select == 1:
            cmds.select(nonManifoldVerts)

        return nonManifoldVerts

    @staticmethod
    def split_non_manifold_vertex(vertex, select=True):
        """Separate a connected vertex of non-manifold geometry where the faces share a single vertex.

        Parameters:
            vertex (str/obj): A single polygon vertex.
            select (bool): Select the vertex after the operation. (default is True)
        """
        cmds.undoInfo(openChunk=True)
        connected_faces = cmds.polyListComponentConversion(
            vertex, fromVertex=1, toFace=1
        )  # pm.mel.PolySelectConvert(1) #convert to faces
        connected_faces_flat = cmds.ls(
            connected_faces, flatten=1
        )  # selectedFaces = cmds.ls(sl=1, flatten=1)

        cmds.polySplitVertex(vertex)

        # get a list for the vertices of each face that is connected to the original vertex.
        verts_sorted_by_face = []
        for face in connected_faces_flat:
            connected_verts = cmds.polyListComponentConversion(
                face, fromFace=1, toVertex=1
            )  # pm.mel.PolySelectConvert(1) #convert to faces
            connected_verts_flat = [
                str(i) for i in cmds.ls(connected_verts, flatten=1)
            ]  # selectedFaces = cmds.ls(sl=1, flatten=1)
            verts_sorted_by_face.append(connected_verts_flat)

        # 1) take first set A from list. 2) for each other set B in the list do if B has common element(s) with A join B into A; remove B from list. 3) repeat 2. until no more overlap with A. 4) put A into outpup. 5) repeat 1. with rest of list.
        out = []
        while len(verts_sorted_by_face) > 0:
            # first, *rest = verts_sorted_by_face
            first, rest = (
                verts_sorted_by_face[0],
                verts_sorted_by_face[1:],
            )
            first = set(first)

            lf = -1
            while len(first) > lf:
                lf = len(first)

                rest2 = []
                for r in rest:
                    if len(first.intersection(set(r))) > 0:
                        first |= set(r)
                    else:
                        rest2.append(r)
                rest = rest2

            out.append(first)
            verts_sorted_by_face = rest

        for vertex_set in out:
            cmds.polyMergeVertex(vertex_set, distance=0.001)

        # deselect the vertices that were selected during the polyMergeVertex operation.
        cmds.select(vertex_set, deselect=1)
        if select:
            cmds.select(vertex, add=1)
        cmds.undoInfo(closeChunk=True)

    @staticmethod
    def get_overlapping_vertices(objects, threshold=0.0003):
        """Query the given objects for overlapping vertices.

        Parameters:
            objects (str/obj/list): The objects to query.
            threshold (float) = The maximum allowed distance.

        Returns:
            (list)
        """
        import maya.OpenMaya as om

        result = []
        for mfnMesh in CoreUtils.get_mfn_mesh(objects, api_version=1):
            points = om.MPointArray()
            mfnMesh.getPoints(points, om.MSpace.kWorld)

            for i in range(points.length()):
                for ii in range(points.length()):
                    if i == ii:
                        continue

                    dist = points[i].distanceTo(points[ii])
                    if dist < threshold:
                        if i not in result:
                            result.append(i)

                        if ii not in result:
                            result.append(ii)
        return result

    @classmethod
    def get_overlapping_faces(cls, objects, delete_history=False):
        """Get any duplicate overlapping faces of the given objects.

        Parameters:
            objects (str/obj/list): Faces or polygon objects.
            delete_history (bool): If True, deletes the history of the objects before processing.

        Returns:
            (list) duplicate overlapping faces.

        Example: cmds.select(get_overlapping_faces(selection))
        """
        if not objects:
            return []

        if delete_history:
            cmds.delete(objects, constructionHistory=True)

        def get_vertex_positions(face):
            # Convert face to vertices and get their world positions, then make a tuple to be hashable
            return tuple(
                sorted(
                    tuple(cmds.pointPosition(v, world=True))
                    for v in cmds.ls(
                        cmds.polyListComponentConversion(face, toVertex=True),
                        flatten=True,
                    )
                )
            )

        def find_duplicates(faces):
            checked = {}
            duplicates = []
            for face in faces:
                positions = get_vertex_positions(face)
                if positions in checked:
                    duplicates.append(face)
                else:
                    checked[positions] = face
            return duplicates

        # Ensure the input is a list
        if isinstance(objects, str):
            objects = [objects]

        objects = cmds.ls(as_strings(objects), flatten=True, type="transform")

        faces = []
        for obj in objects:
            meshes = cmds.listRelatives(obj, type="mesh", fullPath=True) or []
            for mesh in meshes:
                all_faces = cmds.ls(f"{mesh}.f[*]", flatten=True)
                faces.extend(all_faces)

        return find_duplicates(faces)

    @staticmethod
    def _get_scene_polygon_transforms():
        """All polygon-mesh transforms currently in the scene.

        cmds.filterExpand returns None (not []) when nothing matches the
        mask, so callers must not set() its result directly.
        """
        return set(
            cmds.filterExpand(cmds.ls(long=True, typ="transform"), selectionMask=12)
            or []
        )

    @staticmethod
    @CoreUtils.undoable
    def get_similar_mesh(
        objects, tolerance=0.0, inc_orig=False, select=False, **kwargs
    ):
        """Find similar geometry objects using the polyEvaluate command.
        Default behaviour is to compare all flags.

        Parameters:
            objects (str/obj/list): The object(s) to find similar for.
                    Accepts a single object or a list of objects.
            tolerance (float) = The allowed difference in any of the given polyEvalute flag results (that return an int, float (or list of the int or float) value(s)).
            inc_orig (bool): Include the original given obj(s) with the return results.
            select (bool): Select the resulting similar objects.
            kwargs (bool): Any keyword argument 'polyEvaluate' takes. Used to filter the results.
                    ex: vertex, edge, face, uvcoord, triangle, shell, boundingBox, boundingBox2d,
                    vertexComponent, boundingBoxComponent, boundingBoxComponent2d, area, worldArea
        Returns:
            (obj/list) Similar object(s). Returns a single object when a single
            object is given, or a list when multiple objects are given.

        Example:
            get_similar_mesh(selection, vertex=True, area=True)
        """
        objects_list = cmds.ls(as_strings(objects), long=True, transforms=True)

        otherSceneMeshes = EditUtils._get_scene_polygon_transforms()

        all_similar = []
        originals = set()
        for obj in objects_list:
            originals.add(obj)
            # Ensure the evaluation results are consistently processed
            objProps = []
            for key in kwargs:
                result = cmds.polyEvaluate(obj, **{key: kwargs[key]})
                objProps.append(ptk.make_iterable(result))

            similar = [
                m
                for m in otherSceneMeshes
                if ptk.are_similar(
                    objProps,
                    [
                        ptk.make_iterable(cmds.polyEvaluate(m, **{key: kwargs[key]}))
                        for key in kwargs
                    ],
                    tolerance=tolerance,
                )
                and m != obj
            ]
            all_similar.extend(similar)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for m in all_similar:
            if m not in seen:
                seen.add(m)
                unique.append(m)

        result = cmds.ls(unique + list(originals) if inc_orig else unique)

        if select:
            cmds.select(result)

        return ptk.format_return(result, objects)

    @staticmethod
    def get_similar_topo(obj, inc_orig=False, **kwargs):
        """Find similar geometry objects using the polyCompare command.
        Default behaviour is to compare all flags.

        Parameters:
            obj (str/obj/list): The object to find similar for.
            inc_orig (bool): Include the original given obj with the return results.
            kwargs (bool): Any keyword argument 'polyCompare' takes. Used to filter the results.
                    ex: vertices, edges, faceDesc, uvSets, uvSetIndices, colorSets, colorSetIndices, userNormals
        Returns:
            (list) Similar objects.
        """
        polys = (
            cmds.filterExpand(
                cmds.ls(as_strings(obj), long=True, tr=True), selectionMask=12
            )
            or []
        )  # polygon selection mask.
        if not polys:
            cmds.warning(
                "get_similar_topo: no polygon object found in the given input."
            )
            return []
        obj = polys[0]

        otherSceneMeshes = EditUtils._get_scene_polygon_transforms()
        similar = cmds.ls(
            [
                m
                for m in otherSceneMeshes
                if cmds.polyCompare(obj, m, **kwargs) == 0 and m != obj
            ]
        )  # 0:equal,Verts:1,Edges:2,Faces:4,UVSets:8,UVIndices:16,ColorSets:32,ColorIndices:64,UserNormals=128. So a return value of 3 indicates both vertices and edges are different.
        return similar + [obj] if inc_orig else similar

    @staticmethod
    def invert_geometry(
        objects: Optional[List] = None, select: bool = False
    ) -> List[str]:
        """Invert selection to unselected mesh transforms.

        Parameters:
            objects (list): List of objects to check. If None, uses the current selection.
            select (bool): If True, selects the inverted objects.

        Returns:
            list: List of inverted mesh transforms.
        """
        if objects is None:
            objects = cmds.ls(selection=True, transforms=True, type="transform")
        else:
            objects = cmds.ls(as_strings(objects), transforms=True, type="transform")

        def _is_mesh_xform(obj):
            sh = NodeUtils.get_shape(obj)
            return bool(sh) and cmds.nodeType(sh) == "mesh"

        objects = [obj for obj in objects if _is_mesh_xform(obj)]

        all_transforms = [
            obj
            for obj in cmds.ls(transforms=True, type="transform")
            if _is_mesh_xform(obj)
        ]

        inverted = list(set(all_transforms) - set(objects))

        if select:
            cmds.select(inverted, replace=True)
        return inverted

    @staticmethod
    def invert_components(
        objects: Optional[List] = None, select: bool = False
    ) -> List[str]:
        """Invert selection of mesh components (verts, edges, or faces).

        Parameters:
            objects (list): List of objects to check. If None, uses the current selection.
            select (bool): If True, selects the inverted components.

        Returns:
            list: List of inverted mesh components (verts, edges, or faces).
        """
        if objects is None:
            objects = cmds.ls(selection=True, flatten=True)
        else:
            objects = cmds.ls(as_strings(objects), flatten=True)

        if not objects:
            return []

        # Detect component kind from descriptor (post-migration cmds.ls
        # returns plain strings, so type(obj[0]) is just `str` and no longer
        # distinguishes MeshVertex / MeshEdge / MeshFace).
        first_str = str(objects[0])
        if ".vtx[" in first_str:
            ct_name = "vtx"
        elif ".e[" in first_str:
            ct_name = "edge"
        elif ".f[" in first_str:
            ct_name = "face"
        else:
            ct_name = type(objects[0]).__name__.lower()
        selected_strs = {str(obj) for obj in objects}

        full_set = []
        for obj in cmds.ls(selection=True, objectsOnly=True):
            shapes = NodeUtils.get_shape_node(obj)
            if not shapes:
                continue

            if not isinstance(shapes, list):
                shapes = [shapes]

            for shape in shapes:
                shape = str(shape)
                if cmds.objectType(shape) != "mesh":
                    continue
                if "vertex" in ct_name or ct_name == "vtx":
                    full_set.extend(
                        cmds.ls(f"{shape}.vtx[*]", flatten=True) or []
                    )
                elif "edge" in ct_name or ct_name == "e":
                    full_set.extend(
                        cmds.ls(f"{shape}.e[*]", flatten=True) or []
                    )
                elif "face" in ct_name or ct_name == "f":
                    full_set.extend(
                        cmds.ls(f"{shape}.f[*]", flatten=True) or []
                    )

        inverted = [x for x in full_set if str(x) not in selected_strs]

        if select:
            cmds.select(inverted, replace=True)
        return inverted

    @staticmethod
    def delete_selected():
        """Delete selected components and/or objects in Autodesk Maya.

        Behavior:
            - Joints are removed via `removeJoint`.
            - Components are deleted using the command matching the descriptor:
              `.vtx[]` → polyDelVertex, `.e[]` → polyDelEdge, anything else
              (`.f[]`, `.map[]`, ...) → cmds.delete. polyDelVertex / polyDelEdge
              are dispatched per owning object (they reject multi-object input).
            - Objects in the selection with no selected components are deleted
              whole; objects that own selected components are spared.
        """
        all_selection = cmds.ls(sl=True, flatten=True) or []
        components = [c for c in all_selection if "." in c and "[" in c]
        objects = [str(o) for o in (cmds.ls(sl=True, objectsOnly=True) or [])]

        # Single pass: bucket each component by descriptor type per-owner
        # (polyDel* reject multi-object input) and collect the owner names.
        verts_by_owner, edges_by_owner, other_components = {}, {}, []
        owner_names = set()
        for comp in components:
            owner = comp.split(".")[0]
            owner_names.add(owner)
            if ".vtx[" in comp:
                verts_by_owner.setdefault(owner, []).append(comp)
            elif ".e[" in comp:
                edges_by_owner.setdefault(owner, []).append(comp)
            else:
                other_components.append(comp)

        # Resolve each owner to long paths covering both transform and shape —
        # cmds.ls(sl=True, objectsOnly=True) may return either form depending
        # on selection path, and a substring-style match would misclassify.
        component_owner_paths = set()
        for owner in owner_names:
            for lp in cmds.ls(owner, long=True) or [owner]:
                component_owner_paths.add(lp)
                if cmds.objectType(lp) == "transform":
                    related = cmds.listRelatives(lp, shapes=True, fullPath=True)
                else:
                    related = cmds.listRelatives(lp, parent=True, fullPath=True)
                component_owner_paths.update(related or [])

        joints, whole_objects = [], []
        for obj in objects:
            if cmds.objectType(obj) == "joint":
                joints.append(obj)
                continue
            obj_long = (cmds.ls(obj, long=True) or [obj])[0]
            if obj_long not in component_owner_paths:
                whole_objects.append(obj)

        for j in joints:
            cmds.removeJoint(j)
        for verts in verts_by_owner.values():
            cmds.polyDelVertex(verts)
        for edges in edges_by_owner.values():
            cmds.polyDelEdge(edges, cleanVertices=True)
        if other_components:
            cmds.delete(other_components)
        if whole_objects:
            cmds.delete(whole_objects)

    @staticmethod
    def create_curve_from_edges(edges: Optional[List[str]] = None, **kwargs):
        """Create a curve from selected polygon edges or a provided list of edges.

        Parameter:
            edges (Optional[List[str]]): A list of edges to convert to a curve.
                                        If None, uses the currently selected edges.
            **kwargs: Additional keyword arguments to override defaults for polyToCurve.

        Returns:
            str: The created curve, or None if the operation failed.
        """
        # Default arguments for polyToCurve
        default_kwargs = {
            "form": 2,  # Open curve
            "degree": 1,  # Linear curve
            "conformToSmoothMeshPreview": True,
        }
        # Merge provided kwargs with defaults
        curve_kwargs = {**default_kwargs, **kwargs}

        # Use provided edges or get selected edges
        edges_to_convert = edges or cmds.filterExpand(selectionMask=32)
        if not edges_to_convert:
            cmds.warning("No edges provided or selected.")
            return None

        # Ensure edges are passed as a single selection
        cmds.select(edges_to_convert)

        try:  # Convert edges to curve
            curve = cmds.polyToCurve(**curve_kwargs)
            if curve:
                cmds.select(curve)
                print(f"Curve created: {curve}")
                return curve
            else:
                cmds.warning("Failed to create a curve from the provided edges.")
                return None
        except Exception as e:
            cmds.warning(f"Error during curve creation: {e}")
            return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
