# !/usr/bin/python
# coding=utf-8
from typing import List, Union, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils, components
from mayatk.display_utils import DisplayUtils
from mayatk.node_utils import NodeUtils
from mayatk.xform_utils import XformUtils


class EditUtils(ptk.HelpMixin):
    """ """

    @staticmethod
    @CoreUtils.undoable
    def snap_closest_verts(obj1, obj2, tolerance=10.0, freeze_transforms=False):
        """Snap the vertices from object one to the closest verts on object two.

        Parameters:
            obj1 (obj): The object in which the vertices are moved from.
            obj2 (obj): The object in which the vertices are moved to.
            tolerance (float) = Maximum search distance.
            freeze_transforms (bool): Reset the selected transform and all of its children down to the shape level.
        """
        vertices = components.Components.get_components(obj1, "vertices")
        closestVerts = components.Components.get_closest_vertex(
            vertices, obj2, tolerance=tolerance, freeze_transforms=freeze_transforms
        )

        progressBar = "mainProgressBar"
        pm.progressBar(
            progressBar,
            edit=True,
            beginProgress=True,
            isInterruptable=True,
            status="Snapping Vertices ...",
            maxValue=len(closestVerts),
        )

        pm.undoInfo(openChunk=True)
        for v1, v2 in closestVerts.items():
            if pm.progressBar(progressBar, query=True, isCancelled=True):
                break

            v2Pos = pm.pointPosition(v2, world=True)
            pm.xform(v1, translation=v2Pos, worldSpace=True)

            pm.progressBar(progressBar, edit=True, step=1)
        pm.undoInfo(closeChunk=True)

        pm.progressBar(progressBar, edit=True, endProgress=True)

    @staticmethod
    def merge_vertices(objects, tolerance=0.001, selected_only=False):
        """Merge Vertices on the given objects.

        Parameters:
            objects (str/obj/list): The object(s) to merge vertices on.
            tolerance (float) = The maximum merge distance.
            selected_only (bool): Merge only the currently selected components.
        """
        for obj in NodeUtils.get_shape_node(ptk.make_iterable(objects)):
            if not isinstance(obj, pm.nt.Mesh):  # Ensure obj is a Mesh
                print(f"Merge Vertices: Skipping non-mesh object: {obj}")
                continue  # Skip locators, cameras, etc.

            if selected_only:  # Merge only selected components
                if pm.filterExpand(selectionMask=31):  # selectionMask=vertices
                    sel = pm.selected()
                    pm.polyMergeVertex(
                        sel,
                        distance=tolerance,
                        alwaysMergeTwoVertices=True,
                        constructionHistory=True,
                    )
                else:  # If selection type is edges or facets:
                    pm.mel.MergeToCenter()

            else:  # Merge all vertices
                vertices = obj.vtx[:]  # mel expression: select -r geometry.vtx[0:1135];
                pm.polyMergeVertex(
                    vertices,
                    distance=tolerance,
                    alwaysMergeTwoVertices=False,
                    constructionHistory=False,
                )
                pm.select(clear=True)
                pm.select(objects)

    @staticmethod
    @CoreUtils.undoable
    def merge_vertex_pairs(vertices):
        """Merge vertices in pairs by moving them to their center and merging.

        Parameters:
            vertices (list): A list of vertices to merge in pairs.
        """
        if not vertices:
            pm.warning("No vertices provided for merging.")
            return

        # Flatten the list to ensure all vertices are individual PyNodes
        vertices = pm.ls(vertices, flatten=True)
        if len(vertices) % 2 != 0:
            pm.warning(
                "An odd number of vertices was provided; the last vertex will be ignored."
            )

        vertex_pairs = [
            (vertices[i], vertices[i + 1]) for i in range(0, len(vertices) - 1, 2)
        ]

        for vtx1, vtx2 in vertex_pairs:
            try:  # Get the world-space positions of the vertices
                pos1 = vtx1.getPosition(space="world")
                pos2 = vtx2.getPosition(space="world")

                # Calculate the midpoint
                center_point = (pos1 + pos2) / 2

                # Move both vertices to the center point
                vtx1.setPosition(center_point, space="world")
                vtx2.setPosition(center_point, space="world")

            except Exception as e:
                pm.warning(f"Failed to move vertices {vtx1} and {vtx2}: {e}")

        pm.polyMergeVertex(vertices, d=0.001)  # Merge the vertices

    @staticmethod
    def get_all_faces_on_axis(obj, axis="x", pivot="center"):
        """Get all faces on the specified axis of an object.

        Parameters:
            obj (str/obj): The name of the geometry.
            axis (str): The axis, e.g. 'x', '-x', 'y', '-y', 'z', '-z'.
            pivot (str or tuple): Defines the face selection pivot:
                - `"center"` (default) → Bounding box center.
                - `"xmin"`, `"xmax"`, `"ymin"`, `"ymax"`, `"zmin"`, `"zmax"` → Bounding box min/max.
                - `"object"` → Uses the object's pivot.
                - `"world"` → Uses world origin (0,0,0).
                - A tuple `(x, y, z)` → Uses a specified world-space pivot.

        Returns:
            list: A list of faces on the specified axis.
        """
        obj_list = pm.ls(obj, type="transform")
        if not obj_list:
            raise ValueError(f"No transform node found with the name: {obj}")
        obj = obj_list[0]

        axis = XformUtils.convert_axis(axis)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]

        # 🔹 Updated to use the new get_operation_axis_pos format
        pivot_value = XformUtils.get_operation_axis_pos(obj, pivot, axis_index)

        # Decide which side of pivot_value to keep
        if axis.startswith("-"):
            compare = lambda v: v <= pivot_value + 0.00001
            bbox_values = ["xmax", "ymax", "zmax"]
        else:
            compare = lambda v: v >= pivot_value - 0.00001
            bbox_values = ["xmin", "ymin", "zmin"]

        bbox_value = bbox_values[axis_index]
        relevant_faces = []

        for shape in obj.getShapes():
            if pm.nodeType(shape) in ["mesh", "nurbsSurface", "subdiv"]:
                for face in pm.ls(shape.faces, fl=True):
                    bb_val = XformUtils.get_bounding_box(
                        face, value=bbox_value, world_space=True
                    )
                    if compare(bb_val):
                        relevant_faces.append(face)

        return relevant_faces

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
    ):
        """Cut objects along the specified axis.

        Parameters:
            objects (str/obj/list): The object(s) to cut.
            axis (str): The axis to cut along ('x', '-x', 'y', '-y', 'z', '-z'). Default is 'x'.
            amount (int): The number of cuts to make. Default is 1.
            pivot (str or tuple): Defines the cutting pivot (passed to get_operation_axis_pos).
            offset (float): The offset amount from the pivot for the cut. Default is 0.
            invert (bool): Invert the axis direction.
            ortho (bool): Use orthographic projection.
            delete (bool): If True, delete the faces on the specified axis. Default is False.
            mirror (bool): If True, mirror the result after deletion using the cut position as the pivot.
        """
        axis = XformUtils.convert_axis(axis, invert=invert, ortho=ortho)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]

        for node in pm.ls(objects, type="transform", flatten=True):
            if NodeUtils.is_group(node):
                continue

            bounding_box = XformUtils.get_bounding_box(
                node, "xmin|ymin|zmin|xmax|ymax|zmax", True
            )
            if not bounding_box or len(bounding_box) < 6:
                pm.warning(
                    f"Skipping cut_along_axis: Unable to retrieve bounding box for {node}"
                )
                continue

            axis_length = bounding_box[axis_index + 3] - bounding_box[axis_index]
            if axis_length == 0:
                pm.warning(
                    f"Skipping cut: Axis length is zero along {axis} for {node}."
                )
                continue

            # Get pivot position from get_operation_axis_pos
            pivot_value = XformUtils.get_operation_axis_pos(node, pivot, axis_index)

            # Apply offset after resolving pivot
            sign = -1 if axis.startswith("-") else 1
            pivot_value += offset * sign

            cut_spacing = axis_length / (amount + 1)

            # Rotation dictionary
            rotations = {
                "x": (0, 90, 0),
                "-x": (0, -90, 0),
                "y": (-90, 0, 0),
                "-y": (90, 0, 0),
                "z": (0, 0, 0),
                "-z": (0, 0, 180),
            }
            rotation = rotations.get(axis, (0, 0, 0))

            cut_positions = []
            for i in range(amount):
                cut_position = list(bounding_box[:3])
                cut_position[axis_index] = (
                    pivot_value - ((amount - 1) * cut_spacing / 2) + (cut_spacing * i)
                )
                cut_positions.append(cut_position[axis_index])  # Store cut positions

                pm.polyCut(node, df=False, pc=cut_position, ro=rotation, ch=True)

            if delete:
                adjusted_pivot = list(XformUtils.get_operation_axis_pos(node, pivot))
                adjusted_pivot[axis_index] = (
                    cut_positions[-1] if sign == 1 else cut_positions[0]
                )
                cls.delete_along_axis(
                    node,
                    axis,
                    pivot=tuple(adjusted_pivot),
                    delete_history=False,
                    mirror=mirror,
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
    ):
        """Delete faces along the specified axis and optionally mirror the result.

        Parameters:
            objects (str/obj/list): The object(s) to delete faces from.
            axis (str): The axis to delete along ('x', '-x', 'y', '-y', 'z', '-z'). Default is '-x'.
            pivot (str or tuple): Defines the deletion pivot (passed to get_operation_axis_pos).
            delete_history (bool): If True, delete the construction history of the object(s). Default is True.
            mirror (bool): If True, mirrors the result after deletion using the cut position as the pivot.
        """
        axis = XformUtils.convert_axis(axis)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]

        for node in pm.ls(objects, type="transform", flatten=True):
            if NodeUtils.is_group(node):
                continue

            if delete_history:
                pm.delete(node, ch=True)

            bounding_box = XformUtils.get_bounding_box(
                node, "xmin|ymin|zmin|xmax|ymax|zmax", True
            )
            if not bounding_box or len(bounding_box) < 6:
                pm.warning(
                    f"Skipping delete_along_axis: Unable to retrieve bounding box for {node}"
                )
                continue

            # Get pivot position from get_operation_axis_pos
            pivot_value = XformUtils.get_operation_axis_pos(node, pivot, axis_index)

            # Updated to use new pivot format
            faces = cls.get_all_faces_on_axis(node, axis, pivot)
            if not faces:
                pm.warning(f"No faces found along {axis} on {node}. Skipping deletion.")
                continue

            total_faces = pm.polyEvaluate(node, face=True)
            if len(faces) == total_faces:
                pm.delete(node)
            else:
                pm.delete(faces)

            if mirror:  # Mirror if enabled
                mirror_axis = axis.lstrip("-")  # Get base axis without negative sign
                mirror_pivot = list(XformUtils.get_operation_axis_pos(node, pivot))
                mirror_pivot[axis_index] = (
                    pivot_value  # Ensure pivot is at the cut plane
                )
                cls.mirror(
                    node, axis=mirror_axis, pivot=tuple(mirror_pivot), mergeMode=1
                )

    @classmethod
    @CoreUtils.undoable
    @DisplayUtils.add_to_isolation
    def mirror(
        cls,
        objects,
        axis: str = "x",
        pivot: Union[str, tuple] = "object",  # Fix: Use Union[str, tuple]
        mergeMode: int = -1,
        uninstance: bool = False,
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
            kwargs: Additional arguments for polyMirrorFace.

        Returns:
            (obj or list) The mirrored object's transform node or list of transform nodes.
        """
        kwargs["ch"] = True  # Ensure construction history
        kwargs["worldSpace"] = True  # Always force world space to avoid inconsistencies

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

        original_objects = pm.ls(objects, type="transform", flatten=True)
        results = []

        for obj in original_objects:
            if uninstance:
                NodeUtils.uninstance(obj)

            # Compute pivot position
            pivot_point = XformUtils.get_operation_axis_pos(obj, pivot)

            # Adjust pivot when mirroring in negative space
            if axis_direction == 1:  # Mirroring in negative direction
                center = XformUtils.get_bounding_box(obj, "center")
                pivot_point[axis_val] = 2 * center[axis_val] - pivot_point[axis_val]

            kwargs["pivot"] = tuple(pivot_point)

            # Handle custom separate mode
            custom_separate = mergeMode == -1
            kwargs["mergeMode"] = (
                0 if custom_separate else mergeMode
            )  # Use 0 for built-in separate if custom mode is requested

            # Execute polyMirrorFace
            mirror_nodes = pm.polyMirrorFace(obj, **kwargs)
            mirror_node = pm.PyNode(mirror_nodes[0])

            # Custom separate logic
            if custom_separate:
                try:
                    orig_obj, new_obj, sep_node = pm.ls(
                        pm.polySeparate(obj, uss=True, inp=True)
                    )
                    pm.connectAttr(
                        mirror_node.firstNewFace, sep_node.startFace, force=True
                    )
                    pm.connectAttr(
                        mirror_node.lastNewFace, sep_node.endFace, force=True
                    )
                    pm.rename(new_obj, orig_obj.name())
                    parent = pm.listRelatives(orig_obj, parent=True, path=True)
                    if parent:
                        pm.parent(new_obj, parent[0])
                except Exception as e:
                    pm.warning(f"Mirror separation failed: {e}")

            results.append(mirror_node)

        return ptk.format_return(results, objects)

    @classmethod
    def clean_geometry(
        cls,
        objects: Union[str, object, List[Union[str, object]]],
        allMeshes: bool = False,
        repair: bool = False,
        quads: bool = False,
        nsided: bool = False,
        concave: bool = False,
        holed: bool = False,
        nonplanar: bool = False,
        zeroGeom: bool = False,
        zeroGeomTol: float = 0.000010,
        zeroEdge: bool = False,
        zeroEdgeTol: float = 0.000010,
        zeroMap: bool = False,
        zeroMapTol: float = 0.000010,
        sharedUVs: bool = False,
        nonmanifold: bool = False,
        lamina: bool = False,
        invalidComponents: bool = False,
        historyOn: bool = True,
        bakePartialHistory: bool = False,
    ) -> None:
        """Select or remove unwanted geometry from a polygon mesh using polyCleanupArgList.

        Parameters:
            objects (Union[str, pm.nt.DependNode, List[Union[str, pm.nt.DependNode]]]): The polygon objects to clean.
            allMeshes (bool): Clean all geometry in the scene instead of only the current selection.
            repair (bool): Attempt to repair instead of just selecting geometry.
        """
        if allMeshes:
            objects = pm.ls(geometry=True)
        elif not isinstance(objects, list):
            objects = [objects]

        if bakePartialHistory:
            pm.bakePartialHistory(objects, prePostDeformers=True)

        # Prepare selection for cleanup
        pm.select(objects)

        # Configure cleanup options
        options = [
            int(allMeshes),
            1 if repair else 2,
            int(historyOn),
            int(quads),
            int(nsided),
            int(concave),
            int(holed),
            int(nonplanar),
            int(zeroGeom),
            float(zeroGeomTol),
            int(zeroEdge),
            float(zeroEdgeTol),
            int(zeroMap),
            float(zeroMapTol),
            int(sharedUVs),
            int(nonmanifold),
            int(lamina),
            int(invalidComponents),
        ]
        # Construct the polyCleanup command argument string
        arg_list = ",".join([f'"{option}"' for option in options])
        command = f"polyCleanupArgList 4 {{{arg_list}}}"

        # Execute the cleanup command
        pm.mel.eval(command)
        pm.select(objects)

    @staticmethod
    def get_overlapping_duplicates(
        objects=[], retain_given_objects=False, select=False, verbose=False
    ):
        """Find any duplicate overlapping geometry at the object level.

        Parameters:
            objects (list): A list of objects to find duplicate overlapping geometry for. Default is selected objects, or all if nothing is selected.
            retain_given_objects (bool): Search only for duplicates of the given objects (or any selected objects if None given), and omit them from the return results.
            select (bool): Select any found duplicate objects.
            verbose (bool): Print each found object to console.

        Returns:
            (set)

        Example:
            duplicates = get_overlapping_duplicates(retain_given_objects=True, select=True, verbose=True)
        """
        scene_objs = pm.ls(transforms=1, geometry=1)  # get all scene geometry

        # Attach a unique identifier consisting each objects polyEvaluate attributes, and it's bounding box center point in world space.
        scene_objs = {
            i: str(pm.objectCenter(i)) + str(pm.polyEvaluate(i))
            for i in scene_objs
            if not NodeUtils.is_group(i)
        }
        selected_objs = pm.ls(scene_objs.keys(), sl=1) if not objects else objects

        objs_inverted = {}  # Invert the dict, combining objects with like identifiers.
        for k, v in scene_objs.items():
            objs_inverted[v] = objs_inverted.get(v, []) + [k]

        duplicates = set()
        for k, v in objs_inverted.items():
            if len(v) > 1:
                if selected_objs:  # Limit scope to only selected objects.
                    if set(selected_objs) & set(
                        v
                    ):  # If any selected objects in found duplicates:
                        if retain_given_objects:
                            [
                                duplicates.add(i) for i in v if i not in selected_objs
                            ]  # Add any duplicated of that object, omitting the selected object.
                        else:
                            [
                                duplicates.add(i) for i in v[1:]
                            ]  # Add all but the first object to the set of duplicates.
                else:
                    [
                        duplicates.add(i) for i in v[1:]
                    ]  # Add all but the first object to the set of duplicates.

        if verbose:
            for i in duplicates:
                print("# Found: overlapping duplicate object: {} #".format(i))
        print("# {} overlapping duplicate objects found. #".format(len(duplicates)))

        if select:
            pm.select(duplicates)

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
        pm.undoInfo(openChunk=True)
        nonManifoldVerts = set()

        vertices = components.Components.get_components(objects, "vertices")
        for vertex in vertices:
            connected_faces = pm.polyListComponentConversion(
                vertex, fromVertex=1, toFace=1
            )  # pm.mel.PolySelectConvert(1) #convert to faces
            connected_faces_flat = pm.ls(
                connected_faces, flatten=1
            )  # selectedFaces = pm.ls(sl=1, flatten=1)

            # get a list of the edges of each face that is connected to the original vertex.
            edges_sorted_by_face = []
            for face in connected_faces_flat:
                connected_edges = pm.polyListComponentConversion(
                    face, fromFace=1, toEdge=1
                )  # pm.mel.PolySelectConvert(1) #convert to faces
                connected_edges_flat = [
                    str(i) for i in pm.ls(connected_edges, flatten=1)
                ]  # selectedFaces = pm.ls(sl=1, flatten=1)
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
        pm.undoInfo(closeChunk=True)

        if select == 2:
            pm.select(nonManifoldVerts, add=1)
        elif select == 1:
            pm.select(nonManifoldVerts)

        return nonManifoldVerts

    @staticmethod
    def split_non_manifold_vertex(vertex, select=True):
        """Separate a connected vertex of non-manifold geometry where the faces share a single vertex.

        Parameters:
            vertex (str/obj): A single polygon vertex.
            select (bool): Select the vertex after the operation. (default is True)
        """
        pm.undoInfo(openChunk=True)
        connected_faces = pm.polyListComponentConversion(
            vertex, fromVertex=1, toFace=1
        )  # pm.mel.PolySelectConvert(1) #convert to faces
        connected_faces_flat = pm.ls(
            connected_faces, flatten=1
        )  # selectedFaces = pm.ls(sl=1, flatten=1)

        pm.polySplitVertex(vertex)

        # get a list for the vertices of each face that is connected to the original vertex.
        verts_sorted_by_face = []
        for face in connected_faces_flat:
            connected_verts = pm.polyListComponentConversion(
                face, fromFace=1, toVertex=1
            )  # pm.mel.PolySelectConvert(1) #convert to faces
            connected_verts_flat = [
                str(i) for i in pm.ls(connected_verts, flatten=1)
            ]  # selectedFaces = pm.ls(sl=1, flatten=1)
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
            pm.polyMergeVertex(vertex_set, distance=0.001)

        # deselect the vertices that were selected during the polyMergeVertex operation.
        pm.select(vertex_set, deselect=1)
        if select:
            pm.select(vertex, add=1)
        pm.undoInfo(closeChunk=True)

    @staticmethod
    def get_ngons(objects, repair=False):
        """Get any N-Gons from the given object using selection contraints.

        Parameters:
            objects (str/obj/list): The objects to query.
            repair (bool): Repair any found N-gons.

        Returns:
            (list)
        """
        pm.select(objects)
        # Change to Component mode to retain object highlighting
        pm.mel.changeSelectMode(1)
        # Change to Face Component Mode
        pm.selectType(smp=0, sme=1, smf=0, smu=0, pv=0, pe=1, pf=0, puv=0)
        # Select Object/s and Run Script to highlight N-Gons
        pm.polySelectConstraint(mode=3, type=0x0008, size=3)
        nGons = pm.ls(sl=1)
        pm.polySelectConstraint(disable=1)

        if repair:  # convert N-Sided Faces To Quads
            pm.polyQuad(nGons, angle=30, kgb=1, ktb=1, khe=1, ws=1)

        return nGons

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
        for mfnMesh in CoreUtils.mfn_mesh_generator(objects):
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

        Example: pm.select(get_overlapping_faces(selection))
        """
        if not objects:
            return []

        if delete_history:
            pm.delete(objects, constructionHistory=True)

        def get_vertex_positions(face):
            # Convert face to vertices and get their world positions, then make a tuple to be hashable
            return tuple(
                sorted(
                    tuple(v.getPosition(space="world"))
                    for v in pm.ls(
                        pm.polyListComponentConversion(face, toVertex=True),
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

        objects = pm.ls(objects, flatten=True, type="transform")

        faces = []
        for obj in objects:
            meshes = pm.listRelatives(obj, type="mesh", fullPath=True)
            for mesh in meshes:
                all_faces = pm.ls(f"{mesh}.f[*]", flatten=True)
                faces.extend(all_faces)

        return find_duplicates(faces)

    @staticmethod
    def get_similar_mesh(obj, tolerance=0.0, inc_orig=False, **kwargs):
        """Find similar geometry objects using the polyEvaluate command.
        Default behaviour is to compare all flags.

        Parameters:
            obj (str/obj/list): The object to find similar for.
            tolerance (float) = The allowed difference in any of the given polyEvalute flag results (that return an int, float (or list of the int or float) value(s)).
            inc_orig (bool): Include the original given obj with the return results.
            kwargs (bool): Any keyword argument 'polyEvaluate' takes. Used to filter the results.
                    ex: vertex, edge, face, uvcoord, triangle, shell, boundingBox, boundingBox2d,
                    vertexComponent, boundingBoxComponent, boundingBoxComponent2d, area, worldArea
        Returns:
            (list) Similar objects.

        Example:
            get_similar_mesh(selection, vertex=True, area=True)
        """
        obj, *other = pm.ls(obj, long=True, transforms=True)

        # Ensure the evaluation results are consistently processed
        objProps = []
        for key in kwargs:
            result = pm.polyEvaluate(obj, **{key: kwargs[key]})
            objProps.append(ptk.make_iterable(result))

        otherSceneMeshes = set(
            pm.filterExpand(pm.ls(long=True, typ="transform"), selectionMask=12)
        )  # polygon selection mask.

        similar = pm.ls(
            [
                m
                for m in otherSceneMeshes
                if ptk.are_similar(
                    objProps,
                    [
                        ptk.make_iterable(pm.polyEvaluate(m, **{key: kwargs[key]}))
                        for key in kwargs
                    ],
                    tolerance=tolerance,
                )
                and m != obj
            ]
        )
        return similar + [obj] if inc_orig else similar

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
        obj, *other = pm.filterExpand(
            pm.ls(obj, long=True, tr=True), selectionMask=12
        )  # polygon selection mask.

        otherSceneMeshes = set(
            pm.filterExpand(pm.ls(long=True, typ="transform"), sm=12)
        )
        similar = pm.ls(
            [
                m
                for m in otherSceneMeshes
                if pm.polyCompare(obj, m, **kwargs) == 0 and m != obj
            ]
        )  # 0:equal,Verts:1,Edges:2,Faces:4,UVSets:8,UVIndices:16,ColorSets:32,ColorIndices:64,UserNormals=128. So a return value of 3 indicates both vertices and edges are different.
        return similar + [obj] if inc_orig else similar

    @staticmethod
    def delete_selected():
        """Delete selected components or objects in Autodesk Maya based on user's selection mode.

        Behavior:
            - If joints are selected, they are removed using `pm.removeJoint`.
            - If mesh vertices are selected and vertex mask is on, vertices are deleted.
            - If mesh edges are selected and edge mask is on, edges are deleted.
            - If no components are selected, the whole mesh object is deleted.
        """
        # Query mask settings
        maskVertex = pm.selectType(q=True, vertex=True)
        maskEdge = pm.selectType(q=True, edge=True)

        # Get currently selected objects and components
        objects = pm.ls(sl=True, objectsOnly=True)
        components = pm.ls(sl=True, flatten=True)

        for obj in objects:
            # For joints, use removeJoint
            if pm.objectType(obj, isType="joint"):
                pm.removeJoint(obj)
            # For mesh objects, look for component selections
            elif pm.objectType(obj, isType="mesh"):
                obj_long_name = obj.longName()  # Convert Mesh object to its long name
                # Check for selected components of the object
                selected_components = [
                    comp
                    for comp in components
                    if obj_long_name in comp.node().longName()
                ]

                # Delete based on selection and mask settings
                if selected_components:
                    if maskEdge:
                        pm.polyDelEdge(selected_components, cleanVertices=True)
                    elif maskVertex:
                        pm.polyDelVertex(selected_components)
                else:
                    pm.delete(obj)  # Delete entire object if no components selected

    @classmethod
    @CoreUtils.undoable
    @DisplayUtils.add_to_isolation
    def create_default_primitive(
        cls, baseType, subType, scale=False, translate=False, axis=None
    ):
        """ """
        baseType = baseType.lower()
        subType = subType.lower()

        selection = pm.selected()

        primitives = {
            "polygon": {
                "cube": "pm.polyCube(axis=axis, width=5, height=5, depth=5, subdivisionsX=1, subdivisionsY=1, subdivisionsZ=1)",
                "sphere": "pm.polySphere(axis=axis, radius=5, subdivisionsX=12, subdivisionsY=12)",
                "cylinder": "pm.polyCylinder(axis=axis, radius=5, height=10, subdivisionsX=12, subdivisionsY=1, subdivisionsZ=1)",
                "plane": "pm.polyPlane(axis=axis, width=5, height=5, subdivisionsX=1, subdivisionsY=1)",
                "circle": "cls.createCircle(axis=axis, numPoints=12, radius=5, mode=0)",
                "cone": "pm.polyCone(axis=axis, radius=5, height=5, subdivisionsX=1, subdivisionsY=1, subdivisionsZ=1)",
                "pyramid": "pm.polyPyramid(axis=axis, sideLength=5, numberOfSides=5, subdivisionsHeight=1, subdivisionsCaps=1)",
                "torus": "pm.polyTorus(axis=axis, radius=10, sectionRadius=5, twist=0, subdivisionsX=5, subdivisionsY=5)",
                "pipe": "pm.polyPipe(axis=axis, radius=5, height=5, thickness=2, subdivisionsHeight=1, subdivisionsCaps=1)",
                "geosphere": "pm.polyPrimitive(axis=axis, radius=5, sideLength=5, polyType=0)",
                "platonic solids": 'pm.mel.eval("performPolyPrimitive PlatonicSolid 0;")',
            },
            "nurbs": {
                "cube": "pm.nurbsCube(ch=1, d=3, hr=1, p=(0, 0, 0), lr=1, w=1, v=1, ax=(0, 1, 0), u=1)",
                "sphere": "pm.sphere(esw=360, ch=1, d=3, ut=0, ssw=0, p=(0, 0, 0), s=8, r=1, tolerance=0.01, nsp=4, ax=(0, 1, 0))",
                "cylinder": "pm.cylinder(esw=360, ch=1, d=3, hr=2, ut=0, ssw=0, p=(0, 0, 0), s=8, r=1, tolerance=0.01, nsp=1, ax=(0, 1, 0))",
                "cone": "pm.cone(esw=360, ch=1, d=3, hr=2, ut=0, ssw=0, p=(0, 0, 0), s=8, r=1, tolerance=0.01, nsp=1, ax=(0, 1, 0))",
                "plane": "pm.nurbsPlane(ch=1, d=3, v=1, p=(0, 0, 0), u=1, w=1, ax=(0, 1, 0), lr=1)",
                "torus": "pm.torus(esw=360, ch=1, d=3, msw=360, ut=0, ssw=0, hr=0.5, p=(0, 0, 0), s=8, r=1, tolerance=0.01, nsp=4, ax=(0, 1, 0))",
                "circle": "pm.circle(c=(0, 0, 0), ch=1, d=3, ut=0, sw=360, s=8, r=1, tolerance=0.01, nr=(0, 1, 0))",
                "square": "pm.nurbsSquare(c=(0, 0, 0), ch=1, d=3, sps=1, sl1=1, sl2=1, nr=(0, 1, 0))",
            },
            "light": {
                "ambient": "pm.ambientLight()",  # defaults: 1, 0.45, 1,1,1, "0", 0,0,0, "1"
                "directional": "pm.directionalLight()",  # 1, 1,1,1, "0", 0,0,0, 0
                "point": "pm.pointLight()",  # 1, 1,1,1, 0, 0, 0,0,0, 1
                "spot": "pm.spotLight()",  # 1, 1,1,1, 0, 40, 0, 0, 0, 0,0,0, 1, 0
                "area": 'pm.shadingNode("areaLight", asLight=True)',  # 1, 1,1,1, 0, 0, 0,0,0, 1, 0
                "volume": 'pm.shadingNode("volumeLight", asLight=True)',  # 1, 1,1,1, 0, 0, 0,0,0, 1
            },
        }
        axis = axis or [0, 90, 0]

        node = eval(primitives[baseType][subType])
        # if originally there was a selected object, move the object to that objects's bounding box center.
        if selection:
            if translate:
                XformUtils.move_to(node, selection)
                # center_pos = mtk.get_center_point(selection)
                # pm.xform(node, translation=center_pos, worldSpace=1, absolute=1)
            if scale:
                XformUtils.match_scale(node[0], selection, average=True)

        return NodeUtils.get_history_node(node[0])

    @staticmethod
    @CoreUtils.undoable
    def create_circle(
        axis="y", numPoints=5, radius=5, center=[0, 0, 0], mode=0, name="pCircle"
    ):
        """Create a circular polygon plane.

        Parameters:
            axis (str): 'x','y','z'
            numPoints(int): number of outer points
            radius=int
            center=[float3 list] - point location of circle center
            mode(int): 0 -no subdivisions, 1 -subdivide tris, 2 -subdivide quads

        Returns:
            (list) [transform node, history node] ex. [nt.Transform('polySurface1'), nt.PolyCreateFace('polyCreateFace1')]

        Example: create_circle(axis='x', numPoints=20, radius=8, mode='tri')
        """
        import math

        degree = 360 / float(numPoints)
        radian = math.radians(degree)  # or math.pi*degree/180 (pi * degrees / 180)

        vertexPoints = []
        for _ in range(numPoints):
            # print("deg:", degree,"\n", "cos:",math.cos(radian),"\n", "sin:",math.sin(radian),"\n", "rad:",radian)
            if axis == "x":  # x axis
                y = center[2] + (math.cos(radian) * radius)
                z = center[1] + (math.sin(radian) * radius)
                vertexPoints.append([0, y, z])
            if axis == "y":  # y axis
                x = center[2] + (math.cos(radian) * radius)
                z = center[0] + (math.sin(radian) * radius)
                vertexPoints.append([x, 0, z])
            else:  # z axis
                x = center[0] + (math.cos(radian) * radius)
                y = center[1] + (math.sin(radian) * radius)
                vertexPoints.append([x, y, 0])  # not working.

            # increment by original radian value that was converted from degrees
            radian = radian + math.radians(degree)
            # print(x,y,"\n")

        node = pm.ls(pm.polyCreateFacet(point=vertexPoints, name=name))
        # returns: ['Object name', 'node name']. pymel 'ls' converts those to objects.
        pm.polyNormal(node, normalMode=4)  # 4=reverse and propagate
        if mode == 1:
            pm.polySubdivideFacet(divisions=1, mode=1)
        if mode == 2:
            pm.polySubdivideFacet(divisions=1, mode=0)

        return node

    @staticmethod
    def create_curve_from_edges(edges: Optional[List[str]] = None, **kwargs):
        """Create a curve from selected polygon edges or a provided list of edges.

        Parameter:
            edges (Optional[List[str]]): A list of edges to convert to a curve.
                                        If None, uses the currently selected edges.
            **kwargs: Additional keyword arguments to override defaults for polyToCurve.

        Returns:
            pm.nt.Transform: The created curve, or None if the operation failed.
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
        edges_to_convert = edges or pm.filterExpand(selectionMask=32)
        if not edges_to_convert:
            pm.warning("No edges provided or selected.")
            return None

        # Ensure edges are passed as a single selection
        pm.select(edges_to_convert)

        try:  # Convert edges to curve
            curve = pm.polyToCurve(**curve_kwargs)
            if curve:
                pm.select(curve)
                print(f"Curve created: {curve}")
                return curve
            else:
                pm.warning("Failed to create a curve from the provided edges.")
                return None
        except Exception as e:
            pm.warning(f"Error during curve creation: {e}")
            return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
