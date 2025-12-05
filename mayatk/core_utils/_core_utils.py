# !/usr/bin/python
# coding=utf-8
import os
import sys
from typing import Union, List, Callable, Any, Tuple, Optional
from functools import wraps
import contextlib

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# Import package modules at class level to avoid circular imports.


class _CoreUtilsInternal(object):
    @staticmethod
    def _prepare_reparent(
        nodes: List[object],
    ) -> Tuple[Optional[object], Optional[object]]:
        """Prepare reparenting by using a temporary null if needed."""
        parent = pm.listRelatives(nodes[0], parent=True, fullPath=True) or None
        temp_null = None

        # Determine if any of the nodes are the only child of their parent
        for node in nodes:
            node_parent = pm.listRelatives(node, parent=True, fullPath=True) or None
            if node_parent:
                children = pm.listRelatives(node_parent, children=True) or []
                if len(children) == 1:
                    temp_null = pm.createNode("transform", n="tempTempNull")
                    pm.parent(temp_null, node_parent)
                    break

        return parent, temp_null

    @staticmethod
    def _finalize_reparent(
        new_node: Optional[object],
        parent: Optional[object],
        temp_null: Optional[object],
    ) -> None:
        """Clean up reparenting, handling the parent and temporary null."""
        if parent and new_node:
            try:
                pm.parent(new_node, parent)
            except pm.general.MayaNodeError as e:
                pm.warning(f"Failed to re-parent combined mesh: {e}")
        if temp_null:
            try:
                pm.delete(temp_null)
            except pm.general.MayaNodeError as e:
                pm.warning(f"Failed to delete temporary null: {e}")

    @staticmethod
    def _calculate_mesh_similarity(mesh1: object, mesh2: object) -> float:
        """Calculates a similarity score between two meshes based on their bounding box sizes and vertex counts.

        Parameters:
            mesh1: The first mesh to compare.
            mesh2: The second mesh to compare.

        Returns:
            A float representing the similarity score, where higher means more similar.
        """
        # Get bounding box sizes
        bbox1 = mesh1.getBoundingBox()
        bbox2 = mesh2.getBoundingBox()

        # Calculate volume of bounding boxes
        volume1 = bbox1.width() * bbox1.height() * bbox1.depth()
        volume2 = bbox2.width() * bbox2.height() * bbox2.depth()

        # Get vertex counts
        vertex_count1 = len(mesh1.getVertices())
        vertex_count2 = len(mesh2.getVertices())

        # Calculate similarity score (simple approach based on bounding box volume and vertex count)
        volume_similarity = 1 - abs(volume1 - volume2) / max(volume1, volume2)
        vertex_similarity = 1 - abs(vertex_count1 - vertex_count2) / max(
            vertex_count1, vertex_count2
        )

        # Combine similarities (here, equally weighted for simplicity)
        similarity_score = (volume_similarity + vertex_similarity) / 2

        return similarity_score

    @staticmethod
    @contextlib.contextmanager
    def _temp_reparent(nodes):
        """
        Context manager to maintain hierarchy for nodes during operations that might reparent them.

        Yields a container object with a 'result' attribute that should be set to the
        resulting node of the operation if it needs to be reparented to the original parent.
        """
        parent, temp_null = _CoreUtilsInternal._prepare_reparent(nodes)

        class Result:
            def __init__(self):
                self.result = None

        container = Result()

        try:
            yield container
        finally:
            _CoreUtilsInternal._finalize_reparent(container.result, parent, temp_null)


class CoreUtils(ptk.CoreUtils, _CoreUtilsInternal):
    """ """

    @staticmethod
    @contextlib.contextmanager
    def temporarily_unlock_attributes(objects, attributes=None):
        """
        Context manager to temporarily unlock attributes on objects and restore their state afterwards.

        Parameters:
            objects (str/obj/list): The object(s) to unlock attributes on.
            attributes (list): List of specific attributes to unlock (e.g. ['tx', 'ry']).
                             If None, unlocks all standard transform attributes.
        """
        from mayatk.rig_utils._rig_utils import RigUtils

        # Get current lock state and unlock
        lock_state = RigUtils.get_attr_lock_state(objects, unlock=True)

        try:
            yield
        finally:
            RigUtils.set_attr_lock_state(objects, lock_state=lock_state)

    def selected(func: Callable) -> Callable:
        """A decorator to pass the current selection to the first parameter if None is given."""

        @wraps(func)
        def wrapped(*args, **kwargs) -> Any:
            # Check if it's a method (class or regular) by looking at the first argument
            if args and (hasattr(args[0], "__class__") or isinstance(args[0], type)):
                if (
                    len(args) < 2 or args[1] is None
                ):  # Skip the 'cls' or 'self' parameter for class/regular methods
                    selection = pm.selected()
                    if not selection:
                        return []
                    args = (args[0], selection) + args[2:]
            else:
                if not args or args[0] is None:
                    selection = pm.selected()
                    if not selection:
                        return []
                    args = (selection,) + args[1:]

            return func(*args, **kwargs)

        return wrapped

    def undoable(fn):
        """A decorator to place a function into Maya's undo chunk.
        Prevents the undo queue from breaking entirely if an exception is raised within the given function.

        Parameters:
            fn (obj): The decorated python function that will be placed into the undo que as a single entry.
        """

        @wraps(fn)
        def wrapper(*args, **kwargs):
            with pm.UndoChunk():
                if args and hasattr(args[0], "__class__"):
                    self = args[0]
                    return fn(self, *args[1:], **kwargs)
                else:
                    return fn(*args, **kwargs)

        return wrapper

    def reparent(func: Callable) -> Callable:
        """A decorator to manage reparenting of Maya nodes before and after an operation."""

        @wraps(func)
        def wrapped(*args, **kwargs) -> Any:
            instance, node_args = ptk.parse_method_args(args)

            if not args or not args[0] or len(args[0]) < 2:
                raise ValueError(
                    "Insufficient arguments provided. At least two Maya nodes are required."
                )

            mesh_nodes = []
            for arg in args[0]:
                try:
                    node = pm.PyNode(arg)
                    mesh_nodes.append(node)
                except pm.MayaNodeError:
                    raise ValueError(f"No valid Maya node found for the name: {arg}")

            if not mesh_nodes:
                raise ValueError("No valid Maya nodes provided.")

            with CoreUtils._temp_reparent(mesh_nodes) as context:
                if instance:
                    context.result = func(instance, *node_args, **kwargs)
                else:
                    context.result = func(*node_args, **kwargs)
                return context.result

        return wrapped

    @staticmethod
    def wrap_control(control_name, container):
        """Embed a Maya Native UI Object.

        Parameters:
            control_name (str): The name of an existing maya control. ie. 'cmdScrollFieldReporter1'
            container (obj): A widget instance in which to wrap the control.

        Example:
            modelPanelName = pm.modelPanel("embeddedModelPanel#", cam='persp')
            wrap_control(modelPanelName, QtWidgets.QtWidget())
        """
        from qtpy import QtWidgets

        # import wrapInstance
        from shiboken6 import wrapInstance
        from maya.OpenMayaUI import MQtUtil

        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layoutName = ptk.set_case(
            container.objectName() + "Layout", "camel"
        )  # results in '<objectName>Layout' or 'layout' if container objectName is ''
        layout.setObjectName(layoutName)
        pm.setParent(layoutName)

        derivedClass = ptk.get_derived_type(container)

        ptr = MQtUtil.findControl(
            control_name
        )  # get a pointer to the maya api paneLayout.
        control = wrapInstance(int(ptr), derivedClass)
        layout.addWidget(control)

        return control

    @staticmethod
    def confirm_existence(objects: List[str]) -> Tuple[List[str], List[str]]:
        """Confirms the existence of each object in the provided list in Maya.

        Parameters:
            objects (List[str]): List of object names to confirm existence.

        Returns:
            Tuple[List[str], List[str]]: A tuple containing two lists - the first list
            contains names of existing objects, and the second list contains names of non-existing objects.
        """
        existing = []
        non_existing = []

        for obj in objects:
            if pm.objExists(obj):
                existing.append(obj)
            else:
                non_existing.append(obj)

        return existing, non_existing

    @staticmethod
    def get_mfn_mesh(objects, api_version: int = 2):
        """Get MFnMesh function set(s) from transform or shape node(s).

        Parameters:
            objects: A mesh transform, shape node, string name, or list of these.
            api_version: Which Maya API to use:
                - 1: Maya API 1.0 (maya.OpenMaya) - legacy, returns generator
                - 2: Maya API 2.0 (maya.api.OpenMaya) - modern, better performance

        Returns:
            If api_version=1: Generator yielding MFnMesh objects (API 1.0)
            If api_version=2: Single MFnMesh (API 2.0) if single object passed,
                             or list of MFnMesh if multiple objects passed.

        Raises:
            RuntimeError: If the node is not a valid mesh.
            ValueError: If api_version is not 1 or 2.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        if api_version == 1:
            import maya.OpenMaya as om

            selectionList = om.MSelectionList()
            for mesh in NodeUtils.get_shape_node(pm.ls(objects)):
                selectionList.add(mesh)

            def _generator():
                for i in range(selectionList.length()):
                    dagPath = om.MDagPath()
                    selectionList.getDagPath(i, dagPath)
                    yield om.MFnMesh(dagPath)

            return _generator()

        elif api_version == 2:
            import maya.api.OpenMaya as om2

            def _get_single(node):
                node = pm.PyNode(node)
                if node.type() == "transform":
                    shapes = node.getShapes(noIntermediate=True)
                    if shapes:
                        node = shapes[0]
                dag = om2.MGlobal.getSelectionListByName(str(node)).getDagPath(0)
                return om2.MFnMesh(dag)

            # Handle single vs multiple objects
            if isinstance(objects, (list, tuple)):
                return [_get_single(obj) for obj in objects]
            else:
                return _get_single(objects)

        else:
            raise ValueError(f"api_version must be 1 or 2, got {api_version}")

    @staticmethod
    def get_array_type(array):
        """Determine the given element(s) type.
        Samples only the first element.

        Parameters:
            array (str/obj/list): The components(s) to query.

        Returns:
            (list) 'str', 'int'(valid only at sub-object level), or maya object type as string.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        try:
            o = ptk.make_iterable(array)[0]
        except IndexError:
            # print (f'# Error: {__file__} in get_array_type:\n#\tOperation requires at least one object.\n#\t{error}')
            return ""

        return (
            "str"
            if isinstance(o, str)
            else "int" if isinstance(o, int) else NodeUtils.get_type(o)
        )

    @staticmethod
    def convert_array_type(lst, returned_type="str", flatten=False):
        """Convert the given element(s) to <obj>, 'str', or int values.

        Parameters:
            lst (str/obj/list): The components(s) to convert.
            returned_type (str): The desired returned array element type.
                    valid: 'str'(default), 'obj', 'int'(valid only at sub-object level).
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list)(dict) return a dict only with a return type of 'int' and more that one object given.

        Example:
        convert_array_type('obj.vtx[:2]', 'str') #returns: ['objShape.vtx[0:2]']
        convert_array_type('obj.vtx[:2]', 'str', True) #returns: ['objShape.vtx[0]', 'objShape.vtx[1]', 'objShape.vtx[2]']
        convert_array_type('obj.vtx[:2]', 'obj') #returns: [MeshVertex('objShape.vtx[0:2]')]
        convert_array_type('obj.vtx[:2]', 'obj', True) #returns: [MeshVertex('objShape.vtx[0]'), MeshVertex('objShape.vtx[1]'), MeshVertex('objShape.vtx[2]')]
        convert_array_type('obj.vtx[:2]', 'int')) #returns: {nt.Mesh('objShape'): [(0, 2)]}
        convert_array_type('obj.vtx[:2]', 'int', True)) #returns: {nt.Mesh('objShape'): [0, 1, 2]}
        """
        lst = pm.ls(lst, flatten=flatten)
        if not lst or isinstance(lst[0], int):
            return []

        if returned_type == "int":
            result = {}
            for c in lst:
                obj = pm.ls(c, objectsOnly=1)[0]
                num = c.split("[")[-1].rstrip("]")

                try:
                    if flatten:
                        componentNum = int(num)
                    else:
                        n = [int(n) for n in num.split(":")]
                        componentNum = tuple(n) if len(n) > 1 else n[0]

                    if obj in result:  # append to existing object key.
                        result[obj].append(componentNum)
                    else:
                        result[obj] = [componentNum]
                except ValueError as error:  # incompatible object type.
                    print(
                        f"# Error: {__file__} in convert_array_type\n#\tunable to convert {obj} {num} to int.\n#\t{error}"
                    )
                    break

            objects = set(pm.ls(lst, objectsOnly=True))
            if (
                len(objects) == 1
            ):  # flatten the dict values from 'result' and remove any duplicates.
                flattened = ptk.flatten(result.values())
                result = ptk.remove_duplicates(flattened)

        elif returned_type == "str":
            result = list(map(str, lst))

        else:
            result = lst

        return result

    @staticmethod
    def get_parameter_mapping(node, cmd, parameters):
        """Queries a specified Maya command and returns a dictionary mapping the provided parameters to their values.

        This function helps to retrieve the values of different parameters or attributes associated with a given Maya node (like transformLimits). The node can be a string name, an object or a list of nodes.

        Parameters:
            node (str/obj): The node for which the attributes need to be queried.
            cmd (str): The name of the Maya command that is to be executed. For example, 'transformLimits'.
            parameters (list): A list of strings representing the parameters of the command to query. For example, ['enableTranslationX','translationX'].

        Returns:
            dict: A dictionary where each key is a queried parameter name and the corresponding value is the returned attribute value from the query. For example, {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]}.

        Example:
            >>> get_parameter_mapping(obj, 'transformLimits', ['enableTranslationX','translationX'])
            {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]}
        """
        cmd = getattr(pm, cmd)
        node = pm.ls(node)[0]

        return {p: cmd(node, **{"q": True, p: True}) for p in parameters}

    @staticmethod
    def set_parameter_mapping(node, cmd, parameters):
        """Applies a set of parameter values to a specified Maya node using a given Maya command.

        Parameters:
            node (str/obj/list): The object to query attributes of.
            parameters (dict): The command's parameters and their desired values. ie. {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]}

        Example:
            >>> apply_parameter_mapping(obj, 'transformLimits', {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]})
        """
        cmd = getattr(pm, cmd)
        node = pm.ls(node)[0]

        for p, v in parameters.items():
            cmd(node, **{p: v})

    @classmethod
    def build_mesh_similarity_mapping(
        cls,
        source: Union[str, object, List[Union[str, object]]],
        target: Union[str, object, List[Union[str, object]]],
        tolerance: float = 0.1,
    ) -> dict:
        """Builds a mapping of source meshes to target meshes based on geometric similarity within a specified tolerance.
        This method identifies the most similar target mesh for each source mesh to facilitate targeted UV transfer.

        Parameters:
            source (Union[str, pm.nt.Transform, List[Union[str, pm.nt.Transform]]]): The source mesh(es) for
                which to find matching target mesh(es). Can be a string name, a PyNode object, or a list of these.
            target (Union[str, pm.nt.Transform, List[Union[str, pm.nt.Transform]]]): The target mesh(es) to be
                matched with the source mesh(es). Can be a string name, a PyNode object, or a list of these.
            tolerance (float): The similarity tolerance within which two meshes are considered similar.
                Defaults to 0.1.

        Returns:
            dict: A dictionary mapping the names of source meshes to their most similar target mesh names.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        source_group = NodeUtils.get_unique_children(source)
        target_group = NodeUtils.get_unique_children(target)

        mapping = {}
        for source_child in source_group:
            highest_similarity = 0
            best_match = None
            for target_child in target_group:
                similarity = cls._calculate_mesh_similarity(source_child, target_child)
                if similarity > highest_similarity and similarity >= tolerance:
                    highest_similarity = similarity
                    best_match = target_child

            if best_match:
                mapping[source_child.name()] = best_match.name()

        return mapping

    @staticmethod
    def filter_attributes(
        attributes: List[str],
        exclude: Union[str, List[str], None] = None,
        include: Union[str, List[str], None] = None,
        case_sensitive: bool = False,
    ) -> List[str]:
        """Filter attribute names based on inclusion and/or exclusion patterns.

        This is a general-purpose utility for filtering attribute lists that can be used
        throughout the toolkit. Supports both exact matching and pattern matching.

        Parameters:
            attributes (list): List of attribute names to filter.
            exclude (str/list, optional): Attribute name(s) or pattern(s) to exclude.
                Can be exact names or patterns with wildcards (* and ?).
                E.g., 'visibility', ['visibility', 'translate*'], or ['*X', '*Y'].
            include (str/list, optional): Attribute name(s) or pattern(s) to include.
                If specified, only attributes matching these patterns will be kept.
                Can be exact names or patterns with wildcards (* and ?).
            case_sensitive (bool): Whether to use case-sensitive matching. Default is False.

        Returns:
            list: Filtered list of attribute names.

        Example:
            >>> attrs = ['translateX', 'translateY', 'translateZ', 'rotateX', 'visibility']

            # Exclude specific attributes
            >>> filter_attributes(attrs, exclude='visibility')
            ['translateX', 'translateY', 'translateZ', 'rotateX']

            # Exclude using patterns
            >>> filter_attributes(attrs, exclude='translate*')
            ['rotateX', 'visibility']

            # Include only specific patterns
            >>> filter_attributes(attrs, include='translate*')
            ['translateX', 'translateY', 'translateZ']

            # Combine include and exclude
            >>> filter_attributes(attrs, include='translate*', exclude='*Z')
            ['translateX', 'translateY']

            # Multiple patterns
            >>> filter_attributes(attrs, exclude=['visibility', '*Z'])
            ['translateX', 'translateY', 'rotateX']
        """
        import fnmatch

        if not attributes:
            return []

        # Normalize exclude parameter to a list
        if exclude is None:
            exclude_patterns = []
        elif isinstance(exclude, str):
            exclude_patterns = [exclude]
        else:
            exclude_patterns = list(exclude)

        # Normalize include parameter to a list
        if include is None:
            include_patterns = []
        elif isinstance(include, str):
            include_patterns = [include]
        else:
            include_patterns = list(include)

        # Helper function for pattern matching
        def matches_pattern(attr_name: str, pattern: str) -> bool:
            """Check if attribute name matches the pattern."""
            if not case_sensitive:
                attr_name = attr_name.lower()
                pattern = pattern.lower()

            # Use fnmatch for wildcard support
            if "*" in pattern or "?" in pattern:
                return fnmatch.fnmatch(attr_name, pattern)
            else:
                # Exact match
                return attr_name == pattern

        # Filter attributes
        filtered = []
        for attr in attributes:
            # Check include patterns first (if specified)
            if include_patterns:
                if not any(
                    matches_pattern(attr, pattern) for pattern in include_patterns
                ):
                    continue

            # Check exclude patterns
            if exclude_patterns:
                if any(matches_pattern(attr, pattern) for pattern in exclude_patterns):
                    continue

            filtered.append(attr)

        return filtered

    @staticmethod
    def get_mel_globals(keyword=None, ignore_case=True):
        """Get global MEL variables."""
        variables = [
            v
            for v in sorted(pm.mel.eval("env"))
            if not keyword
            or (
                v.count(keyword)
                if not ignore_case
                else v.lower().count(keyword.lower())
            )
        ]
        return variables

    @staticmethod
    def reorder_objects(objects=None, method="name", reverse=False):
        """Reorder a given set of objects using various sorting methods.

        Parameters:
            objects (str, list, pm.PyNode, None): Objects to reorder.
                Can be a string, list, PyNode, or None. If None, uses current selection.
            method (str): Sorting method to use. Options:
                'name' - Sort alphabetically by object name
                'hierarchy' - Sort by hierarchy depth (root to leaf)
                'x', 'y', 'z' - Sort by position along specified axis
                'distance' - Sort by distance from origin
                'volume' - Sort by bounding box volume
                'vertex_count' - Sort by number of vertices
                'random' - Randomize order
                'creation_time' - Sort by creation time (oldest to newest)
            reverse (bool): If True, reverse the sorting order. Default is False.

        Returns:
            list: List of reordered PyMEL objects

        Example:
            # Sort selected objects by name
            sorted_objs = reorder_objects()

            # Sort specific objects by Y position, reversed
            sorted_objs = reorder_objects(['pCube1', 'pSphere1', 'pCylinder1'], method='y', reverse=True)

            # Sort by hierarchy depth
            sorted_objs = reorder_objects(method='hierarchy')

            # Randomize selection order
            sorted_objs = reorder_objects(method='random')
        """
        # Get objects - use pm.ls to handle strings, lists, etc.
        if objects is None:
            obj_list = pm.ls(selection=True, flatten=True)
            if not obj_list:
                pm.warning("No objects provided and nothing selected.")
                return []
        else:
            obj_list = pm.ls(objects, flatten=True)

        if not obj_list:
            pm.warning("No valid objects to reorder.")
            return []

        # Sort based on method
        if method == "name":
            sorted_objs = sorted(obj_list, key=lambda x: x.nodeName())

        elif method == "hierarchy":
            # Sort by hierarchy depth (number of parents)
            def get_hierarchy_depth(obj):
                depth = 0
                parent = obj.getParent()
                while parent:
                    depth += 1
                    parent = parent.getParent()
                return depth

            sorted_objs = sorted(obj_list, key=get_hierarchy_depth)

        elif method in ["x", "y", "z"]:
            # Sort by position along specified axis
            axis_map = {"x": 0, "y": 1, "z": 2}
            axis_index = axis_map[method]

            def get_position(obj):
                try:
                    # Try to get world space translation
                    if hasattr(obj, "getTranslation"):
                        return obj.getTranslation(space="world")[axis_index]
                    else:
                        return 0
                except:
                    return 0

            sorted_objs = sorted(obj_list, key=get_position)

        elif method == "distance":
            # Sort by distance from origin
            def get_distance(obj):
                try:
                    if hasattr(obj, "getTranslation"):
                        pos = obj.getTranslation(space="world")
                        return (pos.x**2 + pos.y**2 + pos.z**2) ** 0.5
                    else:
                        return 0
                except:
                    return 0

            sorted_objs = sorted(obj_list, key=get_distance)

        elif method == "volume":
            # Sort by bounding box volume
            def get_volume(obj):
                try:
                    if hasattr(obj, "getBoundingBox"):
                        bbox = obj.getBoundingBox(space="world")
                        width = bbox.width()
                        height = bbox.height()
                        depth = bbox.depth()
                        return width * height * depth
                    else:
                        return 0
                except:
                    return 0

            sorted_objs = sorted(obj_list, key=get_volume)

        elif method == "vertex_count":
            # Sort by number of vertices
            def get_vertex_count(obj):
                try:
                    # Try to get shape node if this is a transform
                    shapes = []
                    if hasattr(obj, "getShapes"):
                        shapes = obj.getShapes()
                    elif obj.nodeType() in ["mesh", "nurbsCurve", "nurbsSurface"]:
                        shapes = [obj]

                    if shapes:
                        # Get vertex count from first shape
                        shape = shapes[0]
                        if shape.nodeType() == "mesh":
                            return shape.numVertices()
                        elif shape.nodeType() == "nurbsCurve":
                            return shape.numCVs()
                        elif shape.nodeType() == "nurbsSurface":
                            return shape.numCVsInU() * shape.numCVsInV()
                    return 0
                except:
                    return 0

            sorted_objs = sorted(obj_list, key=get_vertex_count)

        elif method == "random":
            # Randomize order
            import random

            sorted_objs = list(obj_list)
            random.shuffle(sorted_objs)

        elif method == "creation_time":
            # Sort by creation time (oldest to newest)
            def get_creation_time(obj):
                try:
                    # Use the object's UUID as a proxy for creation order
                    # Objects created earlier typically have lower UUID values
                    return obj.uuid()
                except:
                    return ""

            sorted_objs = sorted(obj_list, key=get_creation_time)

        else:
            pm.warning(f"Unknown sorting method: '{method}'. Using 'name' instead.")
            sorted_objs = sorted(obj_list, key=lambda x: x.nodeName())

        # Reverse if requested
        if reverse:
            sorted_objs = sorted_objs[::-1]

        return sorted_objs


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
