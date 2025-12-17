# !/usr/bin/python
# coding=utf-8
from typing import List, Union, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import XformUtils


class EditUtils(ptk.HelpMixin):
    """ """

    # Backward compatibility aliases - these methods have moved to Snap class
    from mayatk.edit_utils.snap import Snap

    snap_closest_verts = staticmethod(Snap.snap_to_closest_vertex)
    conform_to_surface = staticmethod(Snap.snap_to_surface)

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
    def get_all_faces_on_axis(obj, axis="x", pivot="center", use_object_axes=True):
        """Get all faces on the specified axis of an object.

        Parameters:
            obj (str/obj): The name of the geometry.
            axis (str): The axis, e.g. 'x', '-x', 'y', '-y', 'z', '-z'.
            pivot (str or tuple): Defines the face selection pivot:
                - `"center"` (default) → Bounding box center.
                - `"xmin"`, `"xmax"`, `"ymin"`, `"ymax"`, `"zmin"`, `"zmax"` → Bounding box min/max.
                - `"object"` → Uses the object's pivot.
                - `"manip"` → Uses the manipulator pivot.
                - `"world"` → Uses world origin (0,0,0).
                - A tuple `(x, y, z)` → Uses a specified world-space pivot.
            use_object_axes (bool): If True, uses object's local axes for face selection when pivot suggests object space.

        Returns:
            list: A list of faces on the specified axis.
        """
        obj_list = pm.ls(obj, type="transform")
        if not obj_list:
            raise ValueError(f"No transform node found with the name: {obj}")
        obj = obj_list[0]

        axis = XformUtils.convert_axis(axis)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]

        # Determine if we should use object space
        use_object_space = use_object_axes and pivot in {"object", "manip", "baked"}

        if use_object_space:
            # For object space, we need to work in local coordinates
            if pivot == "object":
                # Object pivot in object space is at origin
                pivot_value = 0.0
            elif pivot == "manip":
                # Get manip pivot in world space, then transform to object space
                world_manip = XformUtils.get_operation_axis_pos(obj, "manip")
                obj_matrix = pm.PyNode(obj).getMatrix(worldSpace=True)
                local_manip = pm.dt.Point(world_manip) * obj_matrix.inverse()
                pivot_value = float(local_manip[axis_index])
            else:  # "baked" or other
                # Transform world space pivot to object space
                world_pivot = XformUtils.get_operation_axis_pos(obj, pivot)
                obj_matrix = pm.PyNode(obj).getMatrix(worldSpace=True)
                local_pivot = pm.dt.Point(world_pivot) * obj_matrix.inverse()
                pivot_value = float(local_pivot[axis_index])

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
                        # Get face bounding box in object space
                        bb_val = XformUtils.get_bounding_box(
                            face, value=bbox_value, world_space=False
                        )
                        if compare(bb_val):
                            relevant_faces.append(face)

        else:
            # Original world space logic
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
        use_object_axes=True,
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
            use_object_axes (bool): If True, uses object's local axes for cutting direction when pivot is "object", "manip", or "baked".
                If False, uses world axes (legacy behavior).
        """
        axis = XformUtils.convert_axis(axis, invert=invert, ortho=ortho)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]

        # Determine if we should use object axes for cutting
        use_object_space = use_object_axes and pivot in {"object", "manip", "baked"}

        for node in pm.ls(objects, type="transform", flatten=True):
            if NodeUtils.is_group(node):
                continue

            if use_object_space:
                # For object space cutting, we need to work in the object's local coordinate system
                # Get the object's bounding box in object space
                local_bbox = XformUtils.get_bounding_box(
                    node, "xmin|ymin|zmin|xmax|ymax|zmax", world_space=False
                )
                if not local_bbox or len(local_bbox) < 6:
                    pm.warning(
                        f"Skipping cut_along_axis: Unable to retrieve local bounding box for {node}"
                    )
                    continue

                axis_length = local_bbox[axis_index + 3] - local_bbox[axis_index]
                if axis_length == 0:
                    pm.warning(
                        f"Skipping cut: Local axis length is zero along {axis} for {node}."
                    )
                    continue

                # Get pivot position in object space
                if pivot == "object":
                    # Object pivot in object space is at origin
                    pivot_value = 0.0
                elif pivot == "manip":
                    # Get manip pivot in world space, then transform to object space
                    world_manip = XformUtils.get_operation_axis_pos(node, "manip")
                    obj_matrix = pm.PyNode(node).getMatrix(worldSpace=True)
                    local_manip = pm.dt.Point(world_manip) * obj_matrix.inverse()
                    pivot_value = float(local_manip[axis_index])
                else:  # "baked" or other object-space pivots
                    # Transform world space pivot to object space
                    world_pivot = XformUtils.get_operation_axis_pos(node, pivot)
                    obj_matrix = pm.PyNode(node).getMatrix(worldSpace=True)
                    local_pivot = pm.dt.Point(world_pivot) * obj_matrix.inverse()
                    pivot_value = float(local_pivot[axis_index])

                # Apply offset in object space
                sign = -1 if axis.startswith("-") else 1
                pivot_value += offset * sign

                cut_spacing = axis_length / (amount + 1)

                # For object space, use object-aligned cutting planes
                # The rotation should be relative to the object's orientation
                cut_positions = []
                for i in range(amount):
                    # Calculate cut position in object space
                    local_cut_position = list(local_bbox[:3])
                    local_cut_position[axis_index] = (
                        pivot_value
                        - ((amount - 1) * cut_spacing / 2)
                        + (cut_spacing * i)
                    )
                    cut_positions.append(local_cut_position[axis_index])

                    # Transform cut position to world space for polyCut
                    obj_matrix = pm.PyNode(node).getMatrix(worldSpace=True)
                    world_cut_position = list(
                        pm.dt.Point(local_cut_position) * obj_matrix
                    )

                    # Calculate rotation for object-aligned cutting plane
                    # Get the object's rotation matrix components
                    obj_rotation = pm.PyNode(node).getRotation(space="world")

                    # Base rotations for each axis (in object space)
                    base_rotations = {
                        "x": (0, 90, 0),
                        "-x": (0, -90, 0),
                        "y": (-90, 0, 0),
                        "-y": (90, 0, 0),
                        "z": (0, 0, 0),
                        "-z": (0, 0, 180),
                    }
                    base_rotation = base_rotations.get(axis, (0, 0, 0))

                    # Combine base rotation with object rotation
                    combined_rotation = [
                        base_rotation[i] + obj_rotation[i] for i in range(3)
                    ]

                    pm.polyCut(
                        node,
                        df=False,
                        pc=world_cut_position,
                        ro=combined_rotation,
                        ch=True,
                    )

            else:
                # Original world space cutting logic
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

                # Rotation dictionary for world space
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
                        pivot_value
                        - ((amount - 1) * cut_spacing / 2)
                        + (cut_spacing * i)
                    )
                    cut_positions.append(
                        cut_position[axis_index]
                    )  # Store cut positions

                    pm.polyCut(node, df=False, pc=cut_position, ro=rotation, ch=True)

            if delete:
                if use_object_space:
                    # For object space, create a tuple for the adjusted pivot
                    adjusted_pivot = [0.0, 0.0, 0.0]  # Object space origin
                    adjusted_pivot[axis_index] = (
                        cut_positions[-1] if sign == 1 else cut_positions[0]
                    )
                    # Transform to world space for the delete operation
                    obj_matrix = pm.PyNode(node).getMatrix(worldSpace=True)
                    world_adjusted_pivot = list(
                        pm.dt.Point(adjusted_pivot) * obj_matrix
                    )
                else:
                    # Original world space logic
                    adjusted_pivot = list(
                        XformUtils.get_operation_axis_pos(node, pivot)
                    )
                    adjusted_pivot[axis_index] = (
                        cut_positions[-1] if sign == 1 else cut_positions[0]
                    )
                    world_adjusted_pivot = adjusted_pivot

                cls.delete_along_axis(
                    node,
                    axis,
                    pivot=tuple(world_adjusted_pivot),
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
            faces = cls.get_all_faces_on_axis(node, axis, pivot, use_object_axes)
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
        pivot: Union[str, tuple] = "object",  # Fix: Use Union[str, tuple]
        mergeMode: int = -1,
        uninstance: bool = False,
        use_object_axes: bool = True,
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
            use_object_axes (bool): If True, uses object's local axes (consumed by caller, ignored here but accepted to prevent kwargs error).
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

    @staticmethod
    def separate_mirrored_mesh(
        mirror_node: "pm.nt.PolyMirrorFace",
        preserve_pivot: bool = True,
    ) -> Optional["pm.nt.Transform"]:
        """Separate mirrored geometry and clean up hierarchy, history, and parenting.

        Parameters:
            mirror_node (pm.nt.PolyMirrorFace): The polyMirrorFace node for face connection.

        Returns:
            The cleaned, renamed transform (or None on failure).
        """
        # Get the transform node for the mirror operation
        mirror_transform = NodeUtils.get_transform_node(mirror_node)
        if not mirror_transform:
            # Try to find via connections if it's a history node
            try:
                outputs = mirror_node.output.outputs(type="mesh")
                if outputs:
                    mirror_transform = outputs[0].getParent()
            except Exception:
                pass

        if not mirror_transform:
            pm.warning(f"[Mirror] No transform node found for {mirror_node}.")
            return None

        # Ensure mirror_transform is a single node
        if isinstance(mirror_transform, list):
            mirror_transform = mirror_transform[0]

        try:
            sep_nodes = pm.polySeparate(mirror_transform, uss=True, inp=True)
            if len(sep_nodes) < 2:
                pm.warning(
                    f"[Separate] polySeparate returned insufficient nodes for {mirror_transform}"
                )
                return None

            orig_obj, new_obj = sep_nodes[:2]

            # Only set up face connections if we have a polySeparate node
            if len(sep_nodes) > 2:
                sep_node = sep_nodes[-1]
                try:
                    pm.connectAttr(
                        mirror_node.firstNewFace, sep_node.startFace, force=True
                    )
                    pm.connectAttr(
                        mirror_node.lastNewFace, sep_node.endFace, force=True
                    )
                except Exception as e:
                    pm.warning(f"[Separate] Failed to connect face attributes: {e}")

            parent = mirror_transform.getParent()
            temp_parent = orig_obj.getParent()

            if temp_parent:
                temp_parent.rename(f"{temp_parent.nodeName()}__TMP")

                # Parent both objects
                for node in [orig_obj, new_obj]:
                    pm.parent(node, parent or None)

            # Pivot handling: preserve original pivot (default) or center.
            try:
                if preserve_pivot:
                    # Get original pivot(s) in world space
                    orig_rp = pm.xform(orig_obj, q=True, ws=True, rp=True)
                    orig_sp = pm.xform(orig_obj, q=True, ws=True, sp=True)
                    pm.xform(new_obj, ws=True, rp=orig_rp)
                    pm.xform(new_obj, ws=True, sp=orig_sp)
                else:
                    center = XformUtils.get_bounding_box(
                        [orig_obj, new_obj], "center", world_space=True
                    )
                    pm.xform(new_obj, piv=center, ws=True)
            except Exception as e:
                pm.warning(f"[Separate] Pivot handling failed for {new_obj}: {e}")

            # Cleanup - only delete construction history, not the objects themselves
            for obj in [orig_obj, new_obj]:
                try:
                    pm.delete(obj, constructionHistory=True)
                except Exception as e:
                    pm.warning(f"Failed to delete construction history for {obj}: {e}")

            # Delete the temporary parent
            if temp_parent:
                try:
                    pm.delete(temp_parent, constructionHistory=True)
                except Exception as e:
                    pm.warning(f"Failed to delete temporary parent {temp_parent}: {e}")

            # Rename to match original object
            try:
                pm.rename(new_obj, orig_obj.nodeName())
            except Exception as e:
                pm.warning(f"Failed to rename {new_obj} to {orig_obj.nodeName()}: {e}")

            print(f"new_obj: {new_obj}, orig_obj: {orig_obj}")
            return new_obj

        except Exception as e:
            pm.warning(
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
            set: Overlapping duplicate transform PyNodes.
        """
        from collections import defaultdict

        scene_objs = NodeUtils.is_mesh(objects, filter=True)

        # Fingerprint by bounding box min/max (rounded) and topology
        obj_fingerprints = {}
        for obj in scene_objs:
            bbox = obj.getBoundingBox(space="world")
            bbox_min = tuple(round(x, 6) for x in bbox.min())
            bbox_max = tuple(round(x, 6) for x in bbox.max())
            topo = str(pm.polyEvaluate(obj))
            obj_fingerprints[obj] = (bbox_min, bbox_max, topo)

        if objects is None:
            selected_set = set(pm.ls(obj_fingerprints.keys(), sl=True))
        else:
            selected_set = set(pm.ls(objects))

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
            for obj in sorted(duplicates, key=lambda x: x.name()):
                print(f"# Found: overlapping duplicate object: {obj} #")
        if verbose or select:
            print(f"# {len(duplicates)} overlapping duplicate objects found. #")
        if select and duplicates:
            pm.select(list(duplicates), r=True)
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

        vertices = Components.get_components(objects, "vertices")
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
    def invert_geometry(
        objects: Optional[List] = None, select: bool = False
    ) -> List["pm.nt.Transform"]:
        """Invert selection to unselected mesh transforms.

        Parameters:
            objects (list): List of objects to check. If None, uses the current selection.
            select (bool): If True, selects the inverted objects.

        Returns:
            list: List of inverted mesh transforms.
        """
        if objects is None:
            objects = pm.ls(selection=True, transforms=True, type="transform")
        else:
            objects = pm.ls(objects, transforms=True, type="transform")

        objects = [
            obj for obj in objects if obj.getShape() and obj.getShape().type() == "mesh"
        ]

        all_transforms = [
            obj
            for obj in pm.ls(transforms=True, type="transform")
            if obj.getShape() and obj.getShape().type() == "mesh"
        ]

        inverted = list(set(all_transforms) - set(objects))

        if select:
            pm.select(inverted, replace=True)
        return inverted

    @staticmethod
    def invert_components(
        objects: Optional[List] = None, select: bool = False
    ) -> List[Union["pm.MeshVertex", "pm.MeshEdge", "pm.MeshFace"]]:
        """Invert selection of mesh components (verts, edges, or faces).

        Parameters:
            objects (list): List of objects to check. If None, uses the current selection.
            select (bool): If True, selects the inverted components.

        Returns:
            list: List of inverted mesh components (verts, edges, or faces).
        """
        if objects is None:
            objects = pm.ls(selection=True, flatten=True)
        else:
            objects = pm.ls(objects, flatten=True)

        if not objects:
            return []

        component_type = type(objects[0])
        selected_strs = {str(obj) for obj in objects}

        full_set = []
        for obj in pm.ls(selection=True, objectsOnly=True):
            shape = NodeUtils.get_shape_node(obj)
            if not shape or shape.type() != "mesh":
                continue

            if issubclass(component_type, pm.MeshVertex):
                full_set.extend(shape.verts)
            elif issubclass(component_type, pm.MeshEdge):
                full_set.extend(shape.edges)
            elif issubclass(component_type, pm.MeshFace):
                full_set.extend(shape.faces)

        inverted = [x for x in full_set if str(x) not in selected_strs]

        if select:
            pm.select(inverted, replace=True)
        return inverted

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
        all_selection = pm.ls(sl=True, flatten=True)
        # Filter components to ensure they are actual components
        components = [c for c in all_selection if isinstance(c, pm.Component)]

        for obj in objects:
            # For joints, use removeJoint
            if pm.objectType(obj, isType="joint"):
                pm.removeJoint(obj)
            # For mesh objects, look for component selections
            elif NodeUtils.is_mesh(obj):
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
