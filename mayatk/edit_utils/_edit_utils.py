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

    @classmethod
    @CoreUtils.undo
    def rename(cls, objects, to, fltr="", regex=False, ignore_case=False):
        """Rename scene objects based on specified patterns and filters, ensuring compliance with Maya's naming conventions.

        Parameters:
            objects (str/obj/list): The object(s) to rename. If empty, all scene objects will be renamed.
            to (str): Desired name pattern. Asterisk (*) can be used for formatting:
                    chars - replace all.
                    *chars* - replace only.
                    *chars - replace suffix.
                    **chars - append suffix.
                    chars* - replace prefix.
                    chars** - append prefix.
            fltr (str): Filter to apply on object names using wildcards or regular expressions:
                    chars - exact match.
                    *chars* - contains chars.
                    *chars - ends with chars.
                    chars* - starts with chars.
                    chars|chars - matches any of the specified patterns.
            regex (bool): Use regular expressions if True, else use default '*' and '|' modifiers for pattern matching.
            ignore_case (bool): Ignore case when filtering. Applies only to the 'fltr' parameter.

        Returns:
            None: Objects are renamed in the scene directly.

        Example:
            rename('Cube', '*001', regex=True) # Replace suffix on objects containing 'Cube' in their name, e.g., 'polyCube' becomes 'polyCube001'.
            rename('Cube', '**001', regex=True) # Append '001' to names of objects containing 'Cube', e.g., 'polyCube1' becomes 'polyCube1001'.
        """
        objects = pm.ls(objectsOnly=1) if not objects else pm.ls(objects)
        long_names = [obj.name() for obj in objects]
        short_names = [ii if ii else i for i, ii in ptk.split_at_chars(long_names)]

        names = ptk.find_str_and_format(
            short_names,
            to,
            fltr,
            regex=regex,
            ignore_case=ignore_case,
            return_orig_strings=True,
        )

        print(f"Rename: Found {len(names)} matches.")

        for i, (oldName, newName) in enumerate(names):
            # Strip illegal characters from newName
            newName = cls.strip_illegal_chars(newName)

            oldName = long_names[i]  # Use the long name to reference the object
            try:
                if pm.objExists(oldName):
                    n = pm.rename(oldName, newName)  # Rename the object
                    if not n == newName:
                        pm.warning(
                            f"'{oldName}' renamed to '{n}'' instead of '{newName}'."
                        )
            except Exception as e:
                if not pm.ls(oldName, readOnly=True) == []:  # Ignore read-only errors
                    print(f"Error renaming '{oldName}' to '{newName}': {e}")

    @staticmethod
    def strip_illegal_chars(input_data, replace_with="_"):
        """Strips illegal characters from a string or a list of strings, replacing them with a specified character, conforming to Maya naming conventions.

        Parameters:
            input_data (str/list): A single string or a list of strings to be sanitized.
            replace_with (str): The character to replace illegal characters with. Default is underscore (_).

        Returns:
            str/list: Sanitized string or list of strings, with illegal characters replaced.
        """
        import re

        def clean_string(s):
            pattern = re.compile(r"[^a-zA-Z0-9_]")
            return pattern.sub(replace_with, s)

        if isinstance(input_data, (list, tuple, set)):
            return [clean_string(s) for s in input_data]
        elif isinstance(input_data, str):
            return clean_string(input_data)
        else:
            raise TypeError(
                "Input data must be a string or a list, tuple, set of strings."
            )

    @staticmethod
    @CoreUtils.undo
    def strip_chars(
        objects: Union[str, object, List[Union[str, object]]],
        num_chars: int = 1,
        trailing: bool = False,
    ) -> List[str]:
        """Deletes leading or trailing characters from the names of the provided objects.

        Parameters:
            objects (Union[str, pm.PyNode, List[Union[str, pm.PyNode]]]): The input string, PyNode, or list of either.
            num_chars (int): The number of characters to delete.
            trailing (bool): Whether to delete characters from the rear of the name.
        """
        # Flatten the list of objects if needed
        objects = pm.ls(objects, flatten=True)
        for obj in objects:
            s = obj.shortName().split("|")[-1]
            if num_chars > len(s):
                print(
                    f'Cannot remove {num_chars} characters from "{s}" as it is shorter than {num_chars} characters.'
                )
                continue
            if trailing:
                new_name = s[:-num_chars]
            else:
                new_name = s[num_chars:]
            try:
                pm.rename(obj, new_name)
            except Exception as e:
                print(f"Unable to rename {s}: {e}")
                continue

    @staticmethod
    @CoreUtils.undo
    def set_case(objects=[], case="caplitalize"):
        """Rename objects following the given case.

        Parameters:
            objects (str/list): The objects to rename. default:all scene objects
            case (str): Desired case using python case operators.
                    valid: 'upper', 'lower', 'caplitalize', 'swapcase' 'title'. default:'caplitalize'
        Example:
            set_case(pm.ls(sl=1), 'upper')
        """
        for obj in pm.ls(objects):
            name = obj.name()

            newName = getattr(name, case)()
            try:
                pm.rename(name, newName)
            except Exception as error:
                if not pm.ls(obj, readOnly=True) == []:  # Ignore read-only errors.
                    print(name + ": ", error)

    @staticmethod
    @CoreUtils.undo
    def append_location_based_suffix(
        objects,
        first_obj_as_ref=False,
        alphabetical=False,
        strip_trailing_ints=True,
        strip_trailing_alpha=True,
        reverse=False,
    ):
        """Rename objects with a suffix defined by its location from origin.

        Parameters:
            objects (str)(int/list): The object(s) to rename.
            first_obj_as_ref (bool): When True, use the first object's bounding box center as reference_point instead of origin.
            alphabetical (str): When True use an alphabetical character as a suffix when there is less than 26 objects else use integers.
            strip_trailing_ints (bool): Strip any trailing integers. ie. 'cube123'
            strip_trailing_alpha (bool): Strip any trailing uppercase alphanumeric chars that are prefixed with an underscore.  ie. 'cube_A'
            reverse (bool): Reverse the naming order. (Farthest object first)
        """
        import string
        import re

        # Determine the reference point
        reference_point = [0, 0, 0]
        if first_obj_as_ref and objects:
            first_obj_bbox = pm.exactWorldBoundingBox(objects[0])
            reference_point = [
                (first_obj_bbox[i] + first_obj_bbox[i + 3]) / 2 for i in range(3)
            ]

        length = len(objects)
        if alphabetical:
            if length <= 26:
                suffix = string.ascii_uppercase
            else:
                suffix = [str(n).zfill(len(str(length))) for n in range(length)]
        else:
            suffix = [str(n).zfill(len(str(length))) for n in range(length)]

        ordered_objs = XformUtils.order_by_distance(
            objects, reference_point=reference_point, reverse=reverse
        )

        newNames = {}  # the object with the new name set as a key.
        for n, obj in enumerate(ordered_objs):
            current_name = obj.name()

            while (
                (current_name[-1] == "_" or current_name[-1].isdigit())
                and strip_trailing_ints
            ) or (
                (
                    len(current_name) > 1
                    and current_name[-2] == "_"
                    and current_name[-1].isupper()
                )
                and strip_trailing_alpha
            ):
                if (
                    current_name[-2] == "_" and current_name[-1].isupper()
                ) and strip_trailing_alpha:  # trailing underscore and uppercase alphanumeric char.
                    current_name = re.sub(
                        re.escape(current_name[-2:]) + "$", "", current_name
                    )

                if (
                    current_name[-1] == "_" or current_name[-1].isdigit()
                ) and strip_trailing_ints:  # trailing underscore and integers.
                    current_name = re.sub(
                        re.escape(current_name[-1:]) + "$", "", current_name
                    )

            obj_suffix = suffix[n]
            newNames[obj] = current_name + "_" + obj_suffix

        # Rename all with a placeholder first so that there are no conflicts.
        for obj in ordered_objs:
            pm.rename(obj, "p0000000000")
        for obj in ordered_objs:  # Rename all with the new names.
            pm.rename(obj, newNames[obj])

    @staticmethod
    @CoreUtils.undo
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
    @CoreUtils.undo
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
    def get_all_faces_on_axis(
        obj, axis="x", pivot="boundingBox", boundingBoxMode="center"
    ):
        """Get all faces on the specified axis of an object.

        Parameters:
            obj (str/obj): The name of the geometry.
            axis (str): The axis, e.g. 'x', '-x', 'y', '-y', 'z', '-z'.
            pivot (int/str/tuple/list): The pivot point for the operation.
                Valid values are 'boundingBox', 'object', 'world', or a tuple/list of 3 floats.
            boundingBoxMode (str): Determines which part of the bounding box is used if pivot="boundingBox".
                Valid values are 'center', 'min', 'max', 'centerMin', 'centerMax'.

        Returns:
            list: A list of faces on the specified axis.
        """
        obj_list = pm.ls(obj, type="transform")
        if not obj_list:
            raise ValueError(f"No transform node found with the name: {obj}")
        obj = obj_list[0]

        axis = XformUtils.convert_axis(axis)
        axis_index = {"x": 0, "y": 1, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]

        # Use the same method to compute pivot_value
        pivot_value = XformUtils.get_operation_axis_pos(
            obj, pivot, axis_index, boundingBoxMode
        )

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
    @CoreUtils.undo
    def cut_along_axis(
        cls,
        objects,
        axis="x",
        invert=False,
        ortho=False,
        amount=1,
        offset=0,
        delete=False,
        pivot="boundingBox",
        boundingBoxMode="center",
    ):
        """Cut objects along the specified axis.

        Parameters:
            objects (str/obj/list): The object(s) to cut.
            axis (str): The axis to cut along ('x', '-x', 'y', '-y', 'z', '-z'). Default is 'x'.
            invert (bool): Invert the axis direction.
            ortho (bool): Use orthographic projection.
            amount (int): The number of cuts to make. Default is 1.
            offset (float): The offset amount from the center for the cut. Default is 0.
            delete (bool): If True, delete the faces on the specified axis. Default is False.
            pivot (str): The pivot point for the operation. Default is 'boundingBox'.
                Valid values are 'boundingBox', 'object', 'world', or a tuple/list of 3 floats.
            boundingBoxMode (str): Determines which part of the bounding box is used if pivot="boundingBox".
                Valid values are 'center', 'min', 'max', 'centerMin', 'centerMax'.
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

            sign = -1 if axis.startswith("-") else 1
            axis_length = bounding_box[axis_index + 3] - bounding_box[axis_index]
            if axis_length == 0:
                pm.warning(
                    f"Skipping cut: Axis length is zero along {axis} for {node}."
                )
                continue

            pivot_value = XformUtils.get_operation_axis_pos(
                node, pivot, axis_index, boundingBoxMode
            )
            pivot_value = max(
                bounding_box[axis_index], min(bounding_box[axis_index + 3], pivot_value)
            )

            cut_spacing = axis_length / (amount + 1)

            # 🔹 Corrected rotation dictionary
            rotations = {
                "x": (0, 90, 0),
                "-x": (0, -90, 0),
                "y": (-90, 0, 0),
                "-y": (90, 0, 0),
                "z": (0, 0, 0),
                "-z": (0, 0, 180),
            }
            rotation = rotations.get(axis, (0, 0, 0))

            for i in range(amount):
                cut_position = list(bounding_box[:3])
                cut_position[axis_index] = (
                    pivot_value
                    - ((amount - 1) * cut_spacing / 2)
                    + (cut_spacing * i)
                    + offset * sign
                )

                pm.polyCut(node, df=False, pc=cut_position, ro=rotation, ch=True)

            if delete:
                cls.delete_along_axis(
                    node, axis, invert, pivot, boundingBoxMode, delete_history=False
                )

    @classmethod
    @CoreUtils.undo
    def delete_along_axis(
        cls,
        objects,
        axis="-x",
        invert=False,
        pivot="boundingBox",
        boundingBoxMode="center",
        delete_history=True,
    ):
        """Delete faces along the specified axis.

        Parameters:
            objects (str/obj/list): The object(s) to delete faces from.
            axis (str): The axis to delete along ('x', '-x', 'y', '-y', 'z', '-z'). Default is '-x'.
            invert (bool): Invert the axis direction.
            pivot (str): The pivot point for the operation. Default is 'boundingBox'.
                Valid values are 'boundingBox', 'object', 'world', or a tuple/list of 3 floats.
            boundingBoxMode (str): Determines which part of the bounding box is used if pivot="boundingBox".
                Valid values are 'center', 'min', 'max', 'centerMin', 'centerMax'.
            delete_history (bool): If True, delete the construction history of the object(s). Default is True.
        """
        axis = XformUtils.convert_axis(axis, invert=invert)
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

            pivot_value = XformUtils.get_operation_axis_pos(
                node, pivot, axis_index, boundingBoxMode
            )
            pivot_value = max(
                bounding_box[axis_index], min(bounding_box[axis_index + 3], pivot_value)
            )

            faces = cls.get_all_faces_on_axis(node, axis, pivot, boundingBoxMode)
            if not faces:
                pm.warning(f"No faces found along {axis} on {node}. Skipping deletion.")
                continue

            total_faces = pm.polyEvaluate(node, face=True)
            if len(faces) == total_faces:
                pm.delete(node)
            else:
                pm.delete(faces)

    @classmethod
    @CoreUtils.undo
    @DisplayUtils.add_to_isolation
    def mirror(
        cls,
        objects,
        uninstance: bool = False,
        delete_history: bool = False,
        **kwargs,
    ):
        # Always enable construction history
        kwargs["ch"] = True

        # Convert axis if given as string, adjusting direction for polyMirrorFace
        axis_map = {
            "x": (1, 0),
            "-x": (0, 0),
            "y": (1, 1),
            "-y": (0, 1),
            "z": (1, 2),
            "-z": (0, 2),
        }
        if isinstance(kwargs.get("axis"), str):
            direction, axis_val = axis_map.get(kwargs["axis"].lower(), (1, 0))
            kwargs["axisDirection"] = direction
            kwargs["axis"] = axis_val

        # Validate numeric axis
        axis_val = kwargs.get("axis", 0)
        if axis_val not in {0, 1, 2}:
            raise ValueError(
                f"Invalid axis: {axis_val}. Must be 0 (X), 1 (Y), or 2 (Z)."
            )

        # Map mirrorAxis int → (pivot, boundingBoxMode)
        mirror_axis_map = {
            0: ("boundingBox", "borderMin"),  # boundingBox border
            1: ("object", None),  # object pivot
            2: ("world", None),  # world pivot
            3: ("boundingBox", "center"),  # boundingBox center
        }

        original_objects = pm.ls(objects, type="transform", flatten=True)
        results = []

        for obj in original_objects:
            if NodeUtils.is_group(obj):
                continue

            if uninstance:
                NodeUtils.uninstance(obj)

            if delete_history and not obj.isReferenced():
                pm.delete(obj, constructionHistory=True)

            obj_kwargs = dict(kwargs)
            mirror_axis = obj_kwargs.get("mirrorAxis", 0)
            pivot, bb_mode = mirror_axis_map.get(mirror_axis, ("boundingBox", "center"))

            # -----------------------------------------------------------
            # 1) (Optional) cut along the correct pivot
            if obj_kwargs.get("cutMesh", False):
                cls.cut_along_axis(
                    obj,
                    axis=obj_kwargs.get("axis", "x"),
                    pivot=pivot,
                    boundingBoxMode=bb_mode or "center",
                    delete=True,
                )
                obj_kwargs["cutMesh"] = False  # prevent double-cut
            # -----------------------------------------------------------

            # -----------------------------------------------------------
            # 2) Use the same pivot for the mirroring pivot
            pivot_value = XformUtils.get_operation_axis_pos(
                obj,
                pivot=pivot,
                axis_index=axis_val,
                boundingBoxMode=bb_mode or "center",
            )

            # polyMirrorFace pivot
            obj_kwargs["ws"] = True
            obj_kwargs["px"] = pivot_value if axis_val == 0 else 0
            obj_kwargs["py"] = pivot_value if axis_val == 1 else 0
            obj_kwargs["pz"] = pivot_value if axis_val == 2 else 0
            # -----------------------------------------------------------

            mirror_nodes = pm.polyMirrorFace(obj, **obj_kwargs)
            mirror_node = pm.PyNode(mirror_nodes[0])

            # Optional: handle separate half
            if obj_kwargs.get("mergeMode", 1) == 0:
                orig_obj, new_obj, sep_node = pm.ls(
                    pm.polySeparate(obj, uss=True, inp=True)
                )
                pm.connectAttr(mirror_node.firstNewFace, sep_node.startFace, force=True)
                pm.connectAttr(mirror_node.lastNewFace, sep_node.endFace, force=True)
                pm.rename(new_obj, orig_obj.name())
                parent = pm.listRelatives(orig_obj, parent=True, path=True)
                if parent:
                    pm.parent(new_obj, parent[0])

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
    @CoreUtils.undo
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
    @CoreUtils.undo
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
