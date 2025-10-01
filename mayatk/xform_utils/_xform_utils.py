# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Dict, Union, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
try:
    from maya.api import OpenMaya as om  # For MPoint, MVector, etc.
except Exception:
    om = None  # Allow module to import outside Maya; guard at call sites
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils, components
from mayatk.node_utils import NodeUtils


class XformUtils(ptk.HelpMixin):
    """ """

    @staticmethod
    def convert_axis(value, invert=False, ortho=False, to_integer=False):
        """Converts between axis representations and optionally inverts the axis or returns an orthogonal axis.

        Parameters:
            value (int/str): The axis value to convert, either an integer index or a string representation.
                        Valid values are: 0 or "x", 1 or "-x", 2 or "y", 3 or "-y", 4 or "z", 5 or "-z".
            invert (bool): When True, inverts the axis direction.
            ortho (bool): When True, returns the axis that is orthogonal to the given axis.
            to_integer (bool): If True, returns the converted axis value as an integer index.

        Returns:
            str/int: The converted axis value as a string unless to_integer is True.

        Raises:
            TypeError: If `value` is not an int or str.
            ValueError: If `value` is invalid.

        Example:
            convert_axis(0)  # Returns "x"
            convert_axis("y")  # Returns "y"
            convert_axis("x", invert=True)  # Returns "-x"
            convert_axis(2, ortho=True)  # Returns "z"
            convert_axis("z", to_integer=True) # Returns 4
        """
        index_to_axis = {0: "x", 1: "-x", 2: "y", 3: "-y", 4: "z", 5: "-z"}
        axis_to_index = {v: k for k, v in index_to_axis.items()}

        # Function to handle inversion of the axis
        def get_inverted_axis(axis):
            return axis[1:] if axis.startswith("-") else "-" + axis

        # Define orthogonal axes
        orthogonal_axis_map = {
            "x": "y",
            "-x": "y",
            "y": "z",
            "-y": "z",
            "z": "x",
            "-z": "x",
        }

        # Determine the result based on input type
        if isinstance(value, int):
            axis = index_to_axis[value]
        elif isinstance(value, str):
            axis = value
        else:
            raise TypeError(
                "Input must be an integer or a string representing an axis."
            )

        # Apply inversion if needed
        if invert:
            axis = get_inverted_axis(axis)

        # Handle orthogonal axis request
        if ortho:
            axis = orthogonal_axis_map[axis]

        # Return the result based on the to_integer flag
        if to_integer:
            return axis_to_index[axis]
        else:
            return axis  # Always returns a string unless to_integer is True

    @classmethod
    def move_to(cls, source, target, group_move=False):
        """Move source object(s) to align with the target object(s).

        This method allows the user to move a source object or a list of source objects
        to a target object or a list of target objects. The objects are moved to the
        center of the bounding box of the target by default.

        Parameters:
            source (str/obj/list): The Maya object(s) to move. Can be a single object or a list of objects.
            target (str/obj/list): The Maya object(s) to move to. Can be a single object or a list of objects.
            group_move (bool): If True, move the source objects as a single group centered around their common bounding box.

        Example:
        >>> move_to(['pCube1', 'pCube2'], 'pSphere1')
        # Moves both pCube1 and pCube2 to the center of pSphere1.

        >>> move_to(['pCube1', 'pCube2'], ['pSphere1', 'pSphere2'], group_move=True)
        # Moves both pCube1 and pCube2 as a single group to the center of the combined bounding box of pSphere1 and pSphere2.
        """

        source = pm.ls(source, flatten=True)
        target = pm.ls(target, flatten=True)

        target_pos = cls.get_bounding_box(target, "center")

        if group_move:
            group_center = cls.get_bounding_box(source, "center")
            translation_vector = [t - g for t, g in zip(target_pos, group_center)]

            for src in source:
                current_pos = pm.xform(
                    src, query=True, translation=True, worldSpace=True
                )
                new_pos = [c + t for c, t in zip(current_pos, translation_vector)]
                pm.xform(src, translation=new_pos, worldSpace=True)
        else:
            for src in source:
                pm.xform(src, translation=target_pos, worldSpace=True)

    @staticmethod
    @CoreUtils.undoable
    def drop_to_grid(
        objects, align="Mid", origin=False, center_pivot=False, freeze_transforms=False
    ):
        """Align objects to Y origin on the grid using a helper plane.

        Parameters:
            objects (str/obj/list): The objects to translate.
            align (bool): Specify which point of the object's bounding box to align with the grid. (valid: 'Max','Mid'(default),'Min')
            origin (bool): Move to world grid's center.
            center_pivot (bool): Center the object's pivot.
            freeze_transforms (bool): Reset the selected transform and all of its children down to the shape level.

        Example:
            drop_to_grid(obj, align='Min') #set the object onto the grid.
        """
        for obj in pm.ls(objects, transforms=1):
            # Save the object space obj pivot.
            osPivot = pm.xform(obj, q=True, rotatePivot=1, objectSpace=1)
            # Save the world space obj pivot.
            wsPivot = pm.xform(obj, q=True, rotatePivot=1, worldSpace=1)

            pm.xform(obj, centerPivots=1)  # center pivot
            plane = pm.polyPlane(name="temp#")

            if not origin:
                # Move the object to the pivot location
                pm.xform(
                    plane, translation=(wsPivot[0], 0, wsPivot[2]), absolute=1, ws=1
                )

            pm.align(obj, plane, atl=1, x="Mid", y=align, z="Mid")
            pm.delete(plane)

            if not center_pivot:
                # Return pivot to orig position.
                pm.xform(obj, rotatePivot=osPivot, objectSpace=1)

            if freeze_transforms:
                pm.makeIdentity(obj, apply=True)

    @classmethod
    def match_scale(cls, a, b, scale=True, average=False):
        """Scale each of the given objects in 'a' to the combined bounding box of the objects in 'b'.

        Parameters:
            a (str/obj/list): The object(s) to scale.
            b (str/obj/list): The object(s) to get a bounding box size from.
            scale (bool): Scale the objects. Else, just return the scale value.
            average (bool): Average the result across all axes.

        Returns:
            (list) scale values as [x,y,z,x,y,z...]
        """
        to_scale = pm.ls(a, flatten=True)

        # Use the get_bounding_box method to compute the bounding box of objects in `b`
        bx, by, bz = cls.get_bounding_box(b, "size", world_space=True)

        result = []
        for obj in to_scale:
            # Compute the bounding box of the current object to scale
            ax, ay, az = cls.get_bounding_box(obj, "size", world_space=True)

            try:
                diffx, diffy, diffz = [bx / ax, by / ay, bz / az]
            except ZeroDivisionError:
                diffx, diffy, diffz = [1, 1, 1]

            scaleNew = [diffx, diffy, diffz]

            if average:
                scaleNew = [sum(scaleNew) / len(scaleNew)] * 3

            if scale:
                pm.xform(obj, s=scaleNew, worldSpace=True, relative=True)

            [result.append(i) for i in scaleNew]

        return result

    @staticmethod
    @CoreUtils.selected
    @CoreUtils.undoable
    def scale_connected_edges(objects, scale_factor=1.1) -> None:
        """Scales each set of connected edges separately, either uniformly or non-uniformly.

        This function scales each set of connected edges around their center point. If a single float or int is provided
        as the scale factor, uniform scaling is applied. If a tuple or list of three numbers is provided, non-uniform
        scaling is applied to the x, y, and z axes respectively.

        Parameters:
            objects (list): A list of selected edge components to be scaled.
            scale_factor (float, int, tuple, list): The factor by which to scale the edges.
                - float/int: Apply uniform scaling.
                - tuple/list of three floats: Apply non-uniform scaling to x, y, and z axes.

        Examples:
            # Uniform scaling: Scale all connected edges by a factor of 1.5
            scale_connected_edges(pm.ls(selection=True), 1.5)

            # Non-uniform scaling: Scale connected edges by 1.5 in x, 1.0 in y, and 0.5 in z
            scale_connected_edges(pm.ls(selection=True), (1.5, 1.0, 0.5))
        """
        # Get the selected edges
        if not objects:
            pm.warning("No edges selected.")
            return

        # Group edges by connected sets using the existing method
        connected_edges_sets = components.Components.get_contigious_edges(objects)

        for edge_set in connected_edges_sets:
            # Get the vertices of the edge set
            vertices = pm.polyListComponentConversion(
                edge_set, fromEdge=True, toVertex=True
            )
            vertices = pm.ls(vertices, flatten=True)

            # Calculate the center point of the vertices
            center_point = pm.dt.Vector(0, 0, 0)
            for vertex in vertices:
                center_point += vertex.getPosition(space="world")
            center_point /= len(vertices)

            # Determine if scale_factor is a float/int (uniform scaling) or tuple/list (non-uniform scaling)
            if isinstance(scale_factor, (tuple, list)):
                scale_x, scale_y, scale_z = scale_factor
            else:
                scale_x = scale_y = scale_z = scale_factor

            # Scale each vertex around the center point
            for vertex in vertices:
                pos = vertex.getPosition(space="world")
                direction = pos - center_point
                new_pos = pm.dt.Vector(
                    center_point.x + direction.x * scale_x,
                    center_point.y + direction.y * scale_y,
                    center_point.z + direction.z * scale_z,
                )
                vertex.setPosition(new_pos, space="world")

    @staticmethod
    @CoreUtils.undoable
    def store_transforms(objects, prefix="original"):
        for obj in pm.ls(objects, type="transform"):
            # Store the world matrix and pivot points
            world_matrix = pm.xform(obj, query=True, matrix=True, worldSpace=True)
            rotate_pivot = pm.xform(obj, query=True, rotatePivot=True, worldSpace=True)
            scale_pivot = pm.xform(obj, query=True, scalePivot=True, worldSpace=True)

            # Check if attributes already exist, if not then add them
            if not pm.hasAttr(obj, f"{prefix}_worldMatrix"):
                pm.addAttr(obj, ln=f"{prefix}_worldMatrix", at="matrix", k=True)
            if not pm.hasAttr(obj, f"{prefix}_rotatePivot"):
                pm.addAttr(obj, ln=f"{prefix}_rotatePivot", dt="double3", k=True)
            if not pm.hasAttr(obj, f"{prefix}_scalePivot"):
                pm.addAttr(obj, ln=f"{prefix}_scalePivot", dt="double3", k=True)

            # Set the stored values
            pm.setAttr(f"{obj}.{prefix}_worldMatrix", type="matrix", *world_matrix)
            pm.setAttr(f"{obj}.{prefix}_rotatePivot", type="double3", *rotate_pivot)
            pm.setAttr(f"{obj}.{prefix}_scalePivot", type="double3", *scale_pivot)

    @classmethod
    @CoreUtils.undoable
    def freeze_transforms(
        cls, objects, center_pivot=False, force=True, delete_history=False, **kwargs
    ):
        """Freezes transformations on the given objects.

        Parameters:
            objects (list): List of transform nodes.
            center_pivot (bool): If True, centers the pivot.
            force (bool): If True, unlocks locked transform attributes and restores them after.
            delete_history (bool): If True, deletes construction history after freeze.
            **kwargs: Passed to pm.makeIdentity (e.g., t=True, r=True, s=True, n=0)
        """
        from mayatk.rig_utils import RigUtils

        objects = pm.ls(objects, type="transform", long=True)

        lock_state: Dict[str, Dict[str, bool]] = {}

        for obj in objects:
            if center_pivot:
                pm.xform(obj, centerPivots=True)

            # Store lock state and unlock if force
            if force:
                lock_state[obj.name()] = RigUtils.get_attr_lock_state(obj)
                RigUtils.set_attr_lock_state(
                    obj, translate=False, rotate=False, scale=False
                )

            # Delete history if requested
            if delete_history:
                pm.delete(obj, constructionHistory=True)

            # Freeze transforms
            pm.makeIdentity(obj, apply=True, **kwargs)

            # Restore lock state if needed
            if force and obj.name() in lock_state:
                RigUtils.set_attr_lock_state(obj, **lock_state[obj.name()])

    @staticmethod
    @CoreUtils.undoable
    def restore_transforms(objects, prefix="original"):
        for obj in pm.ls(objects, type="transform"):
            # Check if the transform attributes are at their default values
            if not (
                pm.xform(obj, query=True, translation=True) == [0.0, 0.0, 0.0]
                and pm.xform(obj, query=True, rotation=True) == [0.0, 0.0, 0.0]
                and pm.xform(obj, query=True, scale=True) == [1.0, 1.0, 1.0]
            ):
                print(
                    f"Attributes are not frozen for {obj}, or have been changed since being frozen, skipping."
                )
                continue  # Skip to next object if default values are not met

            # Retrieve and print the stored world matrix
            stored_world_matrix = pm.getAttr(f"{obj}.{prefix}_worldMatrix")
            # Calculate the inverse of the stored world matrix
            stored_matrix_obj = pm.dt.Matrix(stored_world_matrix)
            inverse_matrix = stored_matrix_obj.inverse()
            # Apply the inverse matrix to negate current transformations
            pm.xform(obj, matrix=inverse_matrix, worldSpace=True)
            # Freeze transformations to reset them
            pm.makeIdentity(obj, apply=True, translate=True, rotate=True, scale=True)
            # Apply the original stored world matrix
            pm.xform(obj, matrix=stored_world_matrix, worldSpace=True)
            # Restore the pivot points
            rotate_pivot = pm.getAttr(f"{obj}.{prefix}_rotatePivot")
            scale_pivot = pm.getAttr(f"{obj}.{prefix}_scalePivot")
            pm.xform(obj, rotatePivot=rotate_pivot, worldSpace=True)
            pm.xform(obj, scalePivot=scale_pivot, worldSpace=True)

    @classmethod
    @CoreUtils.undoable
    def reset_translation(cls, objects):
        """Reset the translation transformations on the given object(s).

        Parameters:
            objects (str/obj/list): The object(s) to reset the translation values for.
        """
        for obj in pm.ls(objects):
            pos = pm.objectCenter(obj)  # Get the object's current position.
            # Move to origin and center pivot.
            cls.drop_to_grid(obj, origin=1, center_pivot=1)
            pm.makeIdentity(obj, apply=1, t=1, r=0, s=0, n=0, pn=1)  # Bake transforms
            # Move the object back to it's original position.
            pm.xform(obj, translation=pos)

    @staticmethod
    def set_translation_to_pivot(node):
        """Set an object’s translation value from its pivot location.

        Parameters:
            obj (str/obj/list): The object(s) to set the translation value for.
            node (str/obj/list): An object, or it's name.
        """
        x, y, z = pm.xform(node, query=True, worldSpace=True, rotatePivot=True)
        pm.xform(node, relative=True, translation=[-x, -y, -z])
        pm.makeIdentity(node, apply=True, translate=True)
        pm.xform(node, translation=[x, y, z])

    @staticmethod
    def get_manip_pivot_matrix(
        obj: Union[str, object, list], **kwargs
    ) -> "pm.datatypes.Matrix":
        """Return the object's transform matrix using xform, allowing kwargs override.

        Parameters:
            obj (str/object/list): Object to query.
            **kwargs: Passed directly to pm.xform() in query mode.

        Returns:
            pm.datatypes.Matrix: The resulting transformation matrix.
        """
        matrix = pm.xform(obj, q=True, matrix=True, **kwargs)
        return pm.datatypes.Matrix(matrix)

    @staticmethod
    def set_manip_pivot_matrix(
        obj: Union[str, object, list],
        matrix: "pm.datatypes.Matrix",
        **kwargs,
    ) -> None:
        """Apply a transformation matrix's position and orientation to the manip pivot.

        Parameters:
            obj (str/object/list): Object to set pivot on.
            matrix (pm.datatypes.Matrix): Source matrix.
            **kwargs: Passed directly to pm.manipPivot().
        """
        tm = pm.datatypes.TransformationMatrix(matrix)
        pos = tm.getTranslation(pm.datatypes.Space.kWorld)
        rot = [pm.util.degrees(a) for a in tm.eulerRotation()]

        pm.select(obj, replace=True)
        pm.manipPivot(p=pos, o=rot, **kwargs)

    @classmethod
    def get_operation_axis_pos(cls, node, pivot, axis_index=None):
        """Determines the pivot position for mirroring/cutting along a specified axis or all axes.

        Parameters:
            node (PyNode): The object whose reference position is determined.
            pivot (str/tuple/list): Mode or explicit position.
                - `"center"` → Uses bounding box center.
                - `"object"` → Uses the object's rotate pivot.
                - `"world"` → Uses world origin `(0,0,0)`.
                - `"manip"` → Uses the manipulator/tool pivot (not the object's scale pivot).
                - `"xmin"`, `"xmax"`, etc. → Uses specific bounding box limits.
                - `"baked"` → Uses the baked (original) rotate pivot in world space.
                - `(x, y, z)` → Uses a specified world-space pivot.
            axis_index (int or None): Axis index (0=X, 1=Y, 2=Z). If `None`, returns full (x, y, z) list.

        Returns:
            float or list: The computed pivot position (single float if `axis_index` is specified, list if `None`).
        """
        # Return full vector if axis_index is None
        if axis_index is None:
            return [
                cls.get_operation_axis_pos(node, pivot, 0),
                cls.get_operation_axis_pos(node, pivot, 1),
                cls.get_operation_axis_pos(node, pivot, 2),
            ]

        # Explicit world-space point
        if isinstance(pivot, (tuple, list)) and len(pivot) == 3:
            return float(pivot[axis_index])

        # Manipulator pivot (actual manipulator/tool position in world space)
        if pivot == "manip":
            # Ensure object is selected for consistent manipulator pivot query
            current_selection = pm.selected()
            pm.select(node, replace=True)

            try:
                # Query the current manipulator pivot position
                manip_pivot_result = pm.manipPivot(q=True, p=True)

                # Normalize result to flat [x,y,z] format
                if (
                    isinstance(manip_pivot_result, (list, tuple))
                    and len(manip_pivot_result) > 0
                    and isinstance(manip_pivot_result[0], (list, tuple))
                ):
                    manip_pivot_ws = list(manip_pivot_result[0][:3])
                else:
                    manip_pivot_ws = list(manip_pivot_result[:3])

                # Ensure we have exactly 3 coordinates
                if len(manip_pivot_ws) < 3:
                    raise ValueError(
                        f"Invalid manipulator pivot result: {manip_pivot_result}"
                    )

            finally:
                # Restore original selection
                pm.select(current_selection, replace=True)

            return (
                float(manip_pivot_ws[axis_index])
                if axis_index is not None
                else manip_pivot_ws
            )

        # Object pivot (rotate pivot in world space)
        if pivot == "object":
            obj_pivot_ws = pm.xform(node, q=True, ws=True, rp=True)
            return (
                float(obj_pivot_ws[axis_index])
                if axis_index is not None
                else obj_pivot_ws
            )

        # Baked pivot (local-space rotate pivot transformed to world space)
        if pivot == "baked":
            local_rp = pm.xform(node, q=True, rp=True, os=True)
            world_rp = pm.dt.Point(local_rp) * pm.PyNode(node).getMatrix(
                worldSpace=True
            )
            return (
                float(world_rp[axis_index])
                if axis_index is not None
                else list(world_rp)
            )

        # World origin
        if pivot == "world":
            return 0.0 if axis_index is not None else [0.0, 0.0, 0.0]

        # Bounding box center
        if pivot == "center":
            center = cls.get_bounding_box(node, "center")
            return float(center[axis_index]) if axis_index is not None else list(center)

        # Bounding box limits (xmin, ymax, etc.)
        limit_pivots = {"xmin", "xmax", "ymin", "ymax", "zmin", "zmax"}
        if isinstance(pivot, str) and pivot in limit_pivots:
            # Get center only once
            center = cls.get_bounding_box(node, "center")
            limit_value = float(cls.get_bounding_box(node, pivot))  # single float
            axis_for_limit = {"x": 0, "y": 1, "z": 2}[pivot[0]]

            if axis_index is None:
                result = list(center)
                result[axis_for_limit] = limit_value
                return result
            else:
                return (
                    limit_value
                    if axis_index == axis_for_limit
                    else float(center[axis_index])
                )

        # Fallback to center
        pm.warning(
            f"Invalid pivot type '{pivot}' for {node}. Defaulting to bounding box center."
        )
        fallback = cls.get_bounding_box(node, "center")
        return float(fallback[axis_index]) if axis_index is not None else list(fallback)

    @staticmethod
    @CoreUtils.undoable
    def align_pivot_to_selection(align_from=[], align_to=[], translate=True):
        """Align one objects pivot point to another using 3 point align.

        Parameters:
            align_from (list): At minimum; 1 object, 1 Face, 2 Edges, or 3 Vertices.
            align_to (list): The object to align with.
            translate (bool): Move the object with it's pivot.
        """
        pos = pm.xform(align_to, q=1, translation=True, worldSpace=True)
        center_pos = [  # Get center by averaging of all x,y,z points.
            sum(pos[0::3]) / len(pos[0::3]),
            sum(pos[1::3]) / len(pos[1::3]),
            sum(pos[2::3]) / len(pos[2::3]),
        ]

        vertices = pm.ls(
            pm.polyListComponentConversion(align_to, toVertex=True), flatten=True
        )
        if len(vertices) < 3:
            return

        for obj in pm.ls(align_from, flatten=1):
            # Create and align helper plane.
            plane = pm.polyPlane(
                name="_hptemp#",
                width=1,
                height=1,
                subdivisionsX=1,
                subdivisionsY=1,
                axis=[0, 1, 0],
                createUVs=2,
                constructionHistory=True,
            )[0]

            pm.select("%s.vtx[0:2]" % plane, vertices[0:3])
            pm.mel.snap3PointsTo3Points(0)

            pm.xform(
                obj,
                rotation=pm.xform(plane, q=True, rotation=True, worldSpace=True),
                worldSpace=True,
            )

            if translate:
                pm.xform(obj, translation=center_pos, worldSpace=True)

            pm.delete(plane)

    @staticmethod
    def reset_pivot_transforms(
        objects: Optional[List[Union[str, object]]] = None,
    ) -> None:
        """Reset Pivot Transforms for the specified objects or selected objects.

        Parameters:
            objects (str/obj/list): List of objects to reset pivots. If None, the currently selected objects are used.
        """
        if objects is None:
            objs = pm.ls(sl=True, type="transform", flatten=True)
        else:
            objs = pm.ls(objects, type="transform", flatten=True)

        if not objs:
            pm.warning("No valid transform objects given for reset pivot transforms.")
            return

        for obj in objs:
            pm.xform(obj, centerPivots=True)
            pm.manipPivot(obj, rotatePivot=True, scalePivot=True)

    @staticmethod
    @CoreUtils.undoable
    def bake_pivot(objects, position=False, orientation=False):
        """Bake the pivot orientation and position of the given object(s).

        Parameters:
            objects (str/obj/list): The object(s) to bake the pivot orientation and position for.
            position (bool): Whether to bake the pivot position.
            orientation (bool): Whether to bake the pivot orientation
        """
        transforms = pm.ls(objects, transforms=1)
        shapes = pm.ls(objects, shapes=1)
        objects = transforms + pm.listRelatives(
            shapes, path=1, parent=1, type="transform"
        )

        ctx = pm.currentCtx()
        pivotModeActive = 0
        customModeActive = 0
        if ctx in ("RotateSuperContext", "manipRotateContext"):  # Rotate tool
            customOri = pm.manipRotateContext("Rotate", q=1, orientAxes=1)
            pivotModeActive = pm.manipRotateContext("Rotate", q=1, editPivotMode=1)
            customModeActive = pm.manipRotateContext("Rotate", q=1, mode=1) == 3
        elif ctx in ("scaleSuperContext", "manipScaleContext"):  # Scale tool
            customOri = pm.manipScaleContext("Scale", q=1, orientAxes=1)
            pivotModeActive = pm.manipScaleContext("Scale", q=1, editPivotMode=1)
            customModeActive = pm.manipScaleContext("Scale", q=1, mode=1) == 6
        else:  # use the move tool orientation
            customOri = pm.manipMoveContext(
                "Move", q=1, orientAxes=1
            )  # get custom orientation
            pivotModeActive = pm.manipMoveContext("Move", q=1, editPivotMode=1)
            customModeActive = pm.manipMoveContext("Move", q=1, mode=1) == 6

        if orientation and customModeActive:
            if not position:
                pm.mel.error(
                    (pm.mel.uiRes("m_bakeCustomToolPivot.kWrongAxisOriToolError"))
                )
                return

            from math import degrees

            cX, cY, cZ = customOri = [
                degrees(customOri[0]),
                degrees(customOri[1]),
                degrees(customOri[2]),
            ]

            pm.rotate(
                objects, cX, cY, cZ, a=1, pcp=1, pgp=1, ws=1, fo=1
            )  # Set object(s) rotation to the custom one (preserving child transform positions and geometry positions)

        if position:
            for obj in objects:
                # Get pivot in parent space
                m = pm.xform(obj, q=1, m=1)
                p = pm.xform(obj, q=1, os=1, sp=1)
                oldX, oldY, oldZ = [
                    (p[0] * m[0] + p[1] * m[4] + p[2] * m[8] + m[12]),
                    (p[0] * m[1] + p[1] * m[5] + p[2] * m[9] + m[13]),
                    (p[0] * m[2] + p[1] * m[6] + p[2] * m[10] + m[14]),
                ]

                pm.xform(obj, zeroTransformPivots=1)  # Zero out pivots

                # Translate obj(s) back to previous pivot (preserving child transform positions and geometry positions)
                newX, newY, newZ = pm.getAttr(
                    obj.name() + ".translate"
                )  # obj.translate
                pm.move(
                    obj, oldX - newX, oldY - newY, oldZ - newZ, pcp=1, pgp=1, ls=1, r=1
                )

        if pivotModeActive:
            pm.ctxEditMode()  # exit pivot mode

        # Set the axis orientation mode back to obj
        if orientation and customModeActive:
            if ctx in ("RotateSuperContext", "manipRotateContext"):
                pm.manipPivot(rotateToolOri=0)
            elif ctx in ("scaleSuperContext", "manipScaleContext"):
                pm.manipPivot(scaleToolOri=0)
            else:  # Some other tool #Set move tool to obj mode and clear the custom ori. (so the tool won't restore it when activated)
                pm.manipPivot(moveToolOri=0)
                if ctx not in ("moveSuperContext", "manipMoveContext"):
                    pm.manipPivot(ro=1)

    @staticmethod
    @CoreUtils.undoable
    def transfer_pivot(
        objects: List[Union[str, object]],
        translate: bool = False,
        rotate: bool = False,
        scale: bool = False,
        bake: bool = False,
        world_space: bool = True,
        select_targets_after_transfer: bool = False,
    ):
        """Transfer the pivot orientation from the first given object to the remaining given objects.

        Parameters:
            objects (List[Union[str, object]]): List of objects. The first object is the source, and the rest are targets.
            translate (bool): Whether to transfer the translation pivot.
            rotate (bool): Whether to transfer the rotation pivot and orientation.
            scale (bool): Whether to transfer the scale pivot.
            bake (bool): Whether to bake the pivot orientation into the transform node.
            world_space (bool): Whether to use world space for transformations.
            select_targets_after_transfer (bool): Whether to select the target objects after the transfer.
        """
        objects = pm.ls(objects, type="transform")
        if not objects or len(objects) < 2:
            pm.warning("At least two objects are required to transfer pivot.")
            return

        source = objects[0]
        targets = objects[1:]

        for target in targets:
            if translate:
                source_translate_pivot = pm.xform(
                    source, q=True, ws=world_space, rp=True
                )
                pm.xform(target, ws=world_space, rp=source_translate_pivot)

            if rotate:
                locator = None
                try:
                    # Create a locator at the source pivot
                    locator = pm.spaceLocator()
                    pm.delete(pm.pointConstraint(source, locator))
                    pm.delete(pm.orientConstraint(source, locator))

                    # Get the original target rotation
                    original_rotation = pm.xform(target, q=True, ws=True, ro=True)
                    # Orient the target to the locator's orientation
                    pm.orientConstraint(locator, target, mo=False)
                    pm.delete(pm.orientConstraint(locator, target))
                    # Restore the original target rotation
                    pm.xform(target, ws=True, ro=original_rotation)
                except Exception as e:
                    print(f"Error processing target {target}: {e}")
                finally:
                    if locator:
                        pm.delete(locator)

            if scale:
                source_scale_pivot = pm.xform(source, q=True, ws=world_space, sp=True)
                pm.xform(target, ws=world_space, sp=source_scale_pivot)

            if bake:  # Bake pivot if required
                pm.makeIdentity(
                    target, apply=True, t=translate, r=rotate, s=scale, n=0, pn=True
                )
            if select_targets_after_transfer:
                pm.select(targets, replace=True)

    @staticmethod
    @CoreUtils.undoable
    def aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0)):
        """Aim the given object(s) at the given world space position.

        Parameters:
            objects (str/obj/list): Transform node(s) of the objects to orient.
            target_pos (obj)(tuple): A point as xyz, or one or more transform nodes at which to aim the other given 'objects'.
            aim_vect (tuple): The vector in local coordinates that points at the target.
            up_vect (tuple): The vector in local coordinates that aligns with the world up vector.

        Example:
            aim_object_at_point(['cube1', 'cube2'], (0, 15, 15))
        """
        if isinstance(target_pos, (tuple, set, list)):
            target = pm.createNode("transform", name="target_helper")
            pm.xform(target, translation=target_pos, absolute=True)
        else:
            target = target_pos  # Assume it's an existing object's name

        for obj in ptk.make_iterable(objects):
            const = pm.aimConstraint(
                target, obj, aim=aim_vect, worldUpVector=up_vect, worldUpType="vector"
            )

        pm.delete(const, target)

    @staticmethod
    def orient_to_vector(
        transform: "pm.nodetypes.Transform",
        aim_vector: "pm.datatypes.Vector" = (1, 0, 0),
        up_vector: "pm.datatypes.Vector" = (0, 1, 0),
    ):
        """Orients a transform so its local +X aims along the given world-space vector."""
        transform = NodeUtils.get_transform_node(transform)
        if not transform:
            raise ValueError(f"// Error: Invalid transform node: {transform}")

        up_vector_X, up_vector_Y, up_vector_Z = up_vector
        up_vector = pm.datatypes.Vector(up_vector_X, up_vector_Y, up_vector_Z)

        aim_vector_X, aim_vector_Y, aim_vector_Z = aim_vector
        aim_vector = pm.datatypes.Vector(aim_vector_X, aim_vector_Y, aim_vector_Z)

        temp = pm.spaceLocator()
        target = pm.spaceLocator()

        pos = transform.getTranslation(space="world")
        temp.setTranslation(pos, space="world")
        target.setTranslation(pos + aim_vector, space="world")

        pm.delete(
            pm.aimConstraint(
                target,
                temp,
                aimVector=(1, 0, 0),
                upVector=up_vector,
                worldUpType="vector",
                worldUpVector=up_vector,
                maintainOffset=False,
            )
        )

        transform.setRotation(temp.getRotation(space="world"), space="world")
        pm.delete([temp, target])

    @classmethod
    @CoreUtils.undoable
    def rotate_axis(cls, objects, target_pos):
        """Aim the given object at the given world space position.
        All rotations in rotated channel, geometry is transformed so
        it does not appear to move during this transformation

        Parameters:
            objects (str/obj/list): Transform node(s) of the objects to orient.
            target_pos (obj)(tuple): A point as xyz, or one or more transform nodes at which to aim the other given 'objects'.
        """
        for obj in pm.ls(objects, type="transform"):
            cls.aim_object_at_point(obj, target_pos)

            try:
                c = obj.verts
            except TypeError:
                c = obj.cp

            wim = pm.getAttr(obj.worldInverseMatrix)
            pm.xform(c, matrix=wim)

            pos = pm.xform(
                obj, q=True, translation=True, absolute=True, worldSpace=True
            )
            pm.xform(c, translation=pos, relative=True, worldSpace=True)

    @staticmethod
    def get_orientation(objects, returned_type="point"):
        """Get an objects orientation as a point or vector.

        Parameters:
            objects (str/obj/list): The object(s) to get the orientation of.
            returned_type (str): The desired returned value type. (valid: 'point'(default), 'vector')

        Returns:
            (tuple)(list) If 'objects' given as a list, a list of tuples will be returned.
        """
        result = []
        for obj in pm.ls(objects, objectsOnly=True):
            world_matrix = pm.xform(obj, q=True, matrix=True, worldSpace=True)
            rAxis = pm.getAttr(obj.rotateAxis)
            if any((rAxis[0], rAxis[1], rAxis[2])):
                print(
                    f"# Warning: {obj} has a modified .rotateAxis of {rAxis} which is included in the result. #"
                )

            if returned_type == "vector":
                from maya.api.OpenMaya import MVector

                ori = (
                    MVector(world_matrix[0:3]),
                    MVector(world_matrix[4:7]),
                    MVector(world_matrix[8:11]),
                )

            else:
                ori = (world_matrix[0:3], world_matrix[4:7], world_matrix[8:11])
            result.append(ori)

        return ptk.format_return(result, objects)

    @staticmethod
    def get_dist_between_two_objects(a, b):
        """Get the magnatude of a vector using the center points of two given objects.

        Parameters:
            a (obj)(str): Object, object name, or point (x,y,z).
            b (obj)(str): Object, object name, or point (x,y,z).

        Returns:
            (float)
        """
        x1, y1, z1 = pm.objectCenter(a)
        x2, y2, z2 = pm.objectCenter(b)

        from math import sqrt

        distance = sqrt(pow((x1 - x2), 2) + pow((y1 - y2), 2) + pow((z1 - z2), 2))

        return distance

    @staticmethod
    def get_center_point(objects):
        """Get the bounding box center point of any given object(s).

        Parameters:
            objects (str)(obj(list): The objects or components to get the center of.

        Returns:
            (tuple) position as xyz float values.
        """
        objects = pm.ls(objects, flatten=True)
        pos = [
            i
            for sublist in [
                pm.xform(s, q=1, translation=1, worldSpace=1, absolute=1)
                for s in objects
            ]
            for i in sublist
        ]
        center_pos = (  # Get center by averaging of all x,y,z points.
            sum(pos[0::3]) / len(pos[0::3]),
            sum(pos[1::3]) / len(pos[1::3]),
            sum(pos[2::3]) / len(pos[2::3]),
        )
        return center_pos

    @staticmethod
    def get_bounding_box(objects, value="", world_space=True, return_valid_keys=False):
        """Calculate and retrieve specific properties of the bounding box for the given object(s) or component(s).

        Parameters:
            objects (str/obj/list): The object(s) or component(s) to query.
            value (str): A string representing the specific bounding box data to return.
                        Multiple properties can be requested using '|'.
            world_space (bool): If True, computes the bounding box in world space.
            return_valid_keys (bool): If True, returns all valid bbox keys instead of computing the bbox.

        Returns:
            float/tuple/list: The requested bounding box value(s), or a list of valid keys if `return_valid_keys=True`.
        """
        bbox_values = {
            "xmin": None,
            "xmax": None,
            "ymin": None,
            "ymax": None,
            "zmin": None,
            "zmax": None,
            "sizex": None,
            "sizey": None,
            "sizez": None,
            "size": None,
            "volume": None,
            "center": None,
            "centroid": None,
            "minsize": None,
            "maxsize": None,
        }

        # Return only the valid keys if requested
        if return_valid_keys:
            return list(bbox_values.keys())

        # Validate input objects
        if not objects:
            raise ValueError("No objects provided for bounding box calculation.")

        objs = objects if isinstance(objects, (list, tuple)) else [objects]
        bbox = (
            pm.exactWorldBoundingBox(objs)
            if world_space
            else pm.xform(objs, q=True, bb=True, ws=False)
        )

        # Assign real values to the dictionary
        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        size = (xmax - xmin, ymax - ymin, zmax - zmin)
        center = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
        volume = size[0] * size[1] * size[2]

        bbox_values.update(
            {
                "xmin": xmin,
                "xmax": xmax,
                "ymin": ymin,
                "ymax": ymax,
                "zmin": zmin,
                "zmax": zmax,
                "sizex": size[0],
                "sizey": size[1],
                "sizez": size[2],
                "size": size,
                "volume": volume,
                "center": center,
                "centroid": center,
                "minsize": min(size),
                "maxsize": max(size),
            }
        )

        values = value.lower().split("|")
        try:
            return (
                tuple(bbox_values[val] for val in values)
                if len(values) > 1
                else bbox_values[values[0]]
            )
        except KeyError as e:
            raise ValueError(f"Invalid value for bounding box data requested: {e}")

    @classmethod
    def sort_by_bounding_box_value(
        cls, objects, value="volume", descending=True, also_return_value=False
    ):
        """Sort the given objects by their bounding box value.

        Parameters:
            objects (str/obj/list): The objects or components to sort.
            value (str): See 'getBoundingBoxInfo' 'value' parameter.
                            ex. 'xmin', 'xmax', 'sizex', 'volume', 'center' etc.
            descending (bool): Sort the list from the largest value down.
            also_return_value (bool): Instead of just the object; return a
                            list of two element tuples as [(<value>, <obj>)].
        Returns:
            (list)
        """
        valueAndObjs = []
        for obj in pm.ls(objects, flatten=False):
            v = cls.get_bounding_box(obj, value)
            valueAndObjs.append((v, obj))

        sorted_ = sorted(valueAndObjs, key=lambda x: int(x[0]), reverse=descending)
        if also_return_value:
            return sorted_
        return [obj for v, obj in sorted_]

    @staticmethod
    def align_using_three_points(vertices):
        """Move and align the object defined by the first 3 points to the last 3 points.

        Parameters:
            vertices (list): The first 3 points must be on the same object (i.e. it is the
                    object to be transformed). The second set of points define
                    the position and plane to transform to.
        """
        import maya.api.OpenMaya as om

        vertices = pm.ls(vertices, flatten=True)
        objectToMove = pm.ls(vertices[:3], objectsOnly=True)

        p0, p1, p2 = [om.MPoint(*pm.pointPosition(v, w=True)) for v in vertices[0:3]]
        p3, p4, p5 = [om.MPoint(*pm.pointPosition(v, w=True)) for v in vertices[3:6]]

        # Translate
        pm.move(*(p3 - p0), objectToMove, r=True, ws=True)

        # First rotation
        axis1 = (p1 - p0).normal()
        axis2 = (p4 - p3).normal()
        # cross_product = axis1 ^ axis2
        angle = axis1.angle(axis2)
        rotation = om.MEulerRotation(0, 0, angle).asVector()
        pm.rotate(*rotation, objectToMove, p=p3, r=True, os=True)

        # Second rotation
        axis3 = (p2 - p0).normal()
        axis4 = (p5 - p3).normal()
        # cross_product = axis3 ^ axis4
        angle = axis3.angle(axis4)
        rotation = om.MEulerRotation(0, 0, angle).asVector()
        pm.rotate(*rotation, objectToMove, p=p4, r=True, os=True)

    @staticmethod
    def is_overlapping(a, b, tolerance=0.001):
        """Check if the vertices in a and b are overlapping within the given tolerance.

        Parameters:
            a (str/obj): The first object to check. Object can be a component.
            b (str/obj): The second object to check. Object can be a component.
            tolerance (float) = The maximum search distance before a vertex is considered not overlapping.

        Returns:
            (bool)
        """
        vert_setA = pm.ls(pm.polyListComponentConversion(a, toVertex=1), flatten=1)
        vert_setB = pm.ls(pm.polyListComponentConversion(b, toVertex=1), flatten=1)

        closestVerts = components.Components.get_closest_verts(
            vert_setA, vert_setB, tolerance=tolerance
        )

        return True if vert_setA and len(closestVerts) == len(vert_setA) else False

    @staticmethod
    def check_objects_against_plane(
        objects: List["pm.nodetypes.Transform"],
        plane_point: Tuple[float, float, float],
        plane_normal: Tuple[float, float, float],
        return_type: str = "bool",
    ) -> Union[
        List[Tuple["pm.nodetypes.Transform", bool]],
        List[Tuple["pm.nodetypes.Transform", "om.MPoint"]],
        List[Tuple["pm.nodetypes.Transform", "pm.datatypes.Vector"]],
        List[Tuple["pm.nodetypes.Transform", "pm.MeshVertex"]],
    ]:
        """General method to check if any object's geometry is below a defined plane.

        Parameters:
            objects: List of objects to check.
            plane_point: A point on the plane as a tuple (x, y, z).
            plane_normal: The normal vector of the plane as a tuple (x, y, z).
            return_type: Type of return value ("bool", "mpoint", "vector", "vertex").

        Return:
            List of objects with their status relative to the plane.
        """
        from maya.api import OpenMaya as om

        # Convert plane_point and plane_normal from tuples to MPoint and MVector
        plane_point = om.MPoint(*plane_point)
        plane_normal = om.MVector(*plane_normal).normalize()

        objects_below_threshold = []

        for obj in objects:  # Validate if object is the correct type
            if not isinstance(obj, pm.nodetypes.Transform):
                print(f"Invalid object type: {obj}. Expected Transform node.")
                continue

            # Get the MDagPath of the object
            try:
                sel_list = om.MSelectionList()
                sel_list.add(obj.name())
                dag_path = sel_list.getDagPath(0)
            except Exception as e:
                print(f"Error getting dag path for {obj}: {e}")
                continue

            # Ensure the object has a mesh shape
            dag_path_shape = dag_path.extendToShape()
            if dag_path_shape.apiType() != om.MFn.kMesh:
                continue

            # Get the world transformation matrix of the object
            world_matrix = dag_path.inclusiveMatrix()

            # Use MFnMesh to access the mesh vertices
            mesh_fn = om.MFnMesh(dag_path_shape)
            points = mesh_fn.getPoints(
                om.MSpace.kObject
            )  # Get vertices in object space

            # Prepare to collect vertices that fall behind the plane
            falling_vertices = []

            # Transform vertices to world space and check their distance to the plane
            for point in points:
                # Transform the point to world space
                transformed_point = point * world_matrix

                # Calculate the distance from the point to the plane
                distance = (transformed_point - plane_point) * plane_normal

                # Check if the point is below the plane
                if distance < 0:
                    if return_type == "bool":
                        objects_below_threshold.append((obj, True))
                        break
                    elif return_type == "mpoint":
                        falling_vertices.append(transformed_point)
                    elif return_type == "vector":
                        falling_vertices.append(
                            pm.datatypes.Vector(
                                transformed_point.x,
                                transformed_point.y,
                                transformed_point.z,
                            )
                        )
                    elif return_type == "vertex":
                        falling_vertices.append(obj.vtx[points.index(point)])
                    else:
                        print(
                            f"Invalid return_type: {return_type}. Expected 'bool', 'mpoint', 'vector', or 'vertex'."
                        )
                        return []

            if falling_vertices and return_type != "bool":
                objects_below_threshold.append((obj, falling_vertices))

            if return_type == "bool" and not objects_below_threshold:
                objects_below_threshold.append((obj, False))

        return objects_below_threshold

    @staticmethod
    def get_vertex_positions(objects, worldSpace=True):
        """Get all vertex positions for the given objects.

        Parameters:
            objects (str/obj/list): The polygon object(s).
            worldSpace (bool): Sample in world or object space.

        Returns:
            (list) Nested lists if multiple objects given.
        """
        import maya.OpenMaya as om

        space = om.MSpace.kWorld if worldSpace else om.MSpace.kObject

        result = []
        for mesh in CoreUtils.mfn_mesh_generator(objects):
            points = om.MPointArray()
            mesh.getPoints(points, space)

            result.append(
                [
                    (points[i][0], points[i][1], points[i][2])
                    for i in range(points.length())
                ]
            )
        return ptk.format_return(result, objects)

    @staticmethod
    def hash_points(points, precision=4):
        """Hash the given list of point values.

        Parameters:
            points (list): A list of point values as tuples.
            precision (int): determines the number of decimal places that are retained
                    in the fixed-point representation. For example, with a value of 4, the
                    fixed-point representation would retain 4 decimal place.
        Returns:
            (list) list(s) of hashed tuples.
        """
        nested = ptk.nested_depth(points) > 1
        sets = points if nested else [points]

        def clamp(p):
            return int(p * 10**precision)

        result = []
        for pset in sets:
            result.append([hash(tuple(map(clamp, i))) for i in pset])
        return ptk.format_return(result, nested)

    @classmethod
    def get_matching_verts(cls, a, b, world_space=False):
        """Find any vertices which point locations match between two given mesh.

        Parameters:
            a (str/obj/list): The first polygon object.
            a (str/obj/list): A second polygon object.
            world_space (bool): Sample in world or object space.

        Returns:
            (list) nested tuples with int values representing matching vertex pairs.
        """
        vert_pos_a, vert_pos_b = cls.get_vertex_positions([a, b], world_space)
        hash_a, hash_b = cls.hash_points([vert_pos_a, vert_pos_b])

        matching = set(hash_a).intersection(hash_b)
        return [
            i
            for h in matching
            for i in zip(ptk.indices(hash_a, h), ptk.indices(hash_b, h))
        ]

    @classmethod
    def order_by_distance(cls, objects, reference_point=None, reverse=False):
        """Order the given objects by their distance from the given reference point.

        Parameters:
            objects (str)(int/list): The object(s) to order.
            reference_point (list): A three value float list x, y, z.
            reverse (bool): Reverse the naming order. (Farthest object first)

        Returns:
            (list) ordered objects
        """
        if reference_point is None:
            reference_point = [0, 0, 0]

        # Create a list to store tuples of (distance, object)
        distance_object_pairs = []

        for obj in pm.ls(objects, flatten=True):
            # Get the bounding box center
            bb_center = cls.get_bounding_box(obj, "center")
            # Calculate the distance from the reference point
            distance = (
                (bb_center[0] - reference_point[0]) ** 2
                + (bb_center[1] - reference_point[1]) ** 2
                + (bb_center[2] - reference_point[2]) ** 2
            ) ** 0.5
            # Append the tuple to the list
            distance_object_pairs.append((distance, obj))

        # Sort the list based on the distance
        distance_object_pairs.sort(key=lambda x: x[0], reverse=reverse)

        # Extract the ordered list of objects from the sorted list of tuples
        ordered_objs = [pair[1] for pair in distance_object_pairs]

        return ordered_objs

    @staticmethod
    @CoreUtils.undoable
    def align_vertices(mode, average=False, edgeloop=False):
        """Align vertices.

        Parameters:
            mode (int): possible values are align: 0-YZ, 1-XZ, 2-XY, 3-X, 4-Y, 5-Z, 6-XYZ
            average (bool): align to average of all selected vertices. else, align to last selected
            edgeloop (bool): align vertices in edgeloop from a selected edge

        Example:
            align_vertices(mode=3, average=True, edgeloop=True)
        """
        selectTypeEdge = pm.selectType(query=True, edge=True)

        if edgeloop:
            pm.mel.SelectEdgeLoopSp()  # select edgeloop

        pm.mel.PolySelectConvert(3)  # convert to vertices

        selection = pm.ls(sl=True, flatten=1)
        lastSelected = pm.ls(tail=1, sl=True, flatten=1)
        align_to = pm.xform(lastSelected, q=True, translation=1, worldSpace=1)
        alignX = align_to[0]
        alignY = align_to[1]
        alignZ = align_to[2]

        if average:
            xyz = pm.xform(selection, q=True, translation=1, worldSpace=1)
            x = xyz[0::3]
            y = xyz[1::3]
            z = xyz[2::3]
            alignX = float(sum(x)) / (len(xyz) / 3)
            alignY = float(sum(y)) / (len(xyz) / 3)
            alignZ = float(sum(z)) / (len(xyz) / 3)

        if len(selection) < 2:
            if len(selection) == 0:
                return pm.inViewMessage(
                    statusMessage="<hl>No vertices selected.</hl>",
                    pos="topCenter",
                    fade=True,
                )
            return pm.inViewMessage(
                statusMessage="<hl>Selection must contain at least two vertices.</hl>",
                pos="topCenter",
                fade=True,
            )

        for vertex in selection:
            vertexXYZ = pm.xform(vertex, q=True, translation=1, worldSpace=1)
            vertX = vertexXYZ[0]
            vertY = vertexXYZ[1]
            vertZ = vertexXYZ[2]

            modes = {
                0: (vertX, alignY, alignZ),  # align YZ
                1: (alignX, vertY, alignZ),  # align XZ
                2: (alignX, alignY, vertZ),  # align XY
                3: (alignX, vertY, vertZ),
                4: (vertX, alignY, vertZ),
                5: (vertX, vertY, alignZ),
                6: (alignX, alignY, alignZ),  # align XYZ
            }

            pm.xform(vertex, translation=modes[mode], worldSpace=1)

        if selectTypeEdge:
            pm.selectType(edge=True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
