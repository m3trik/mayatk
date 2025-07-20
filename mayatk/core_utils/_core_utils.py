# !/usr/bin/python
# coding=utf-8
import os
import sys
from typing import Union, List, Callable, Any, Tuple, Optional
from functools import wraps

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# Import package modules at class level to avoid circular imports.


class CoreUtils(ptk.HelpMixin):
    """ """

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

            parent, temp_null = CoreUtils.prepare_reparent(mesh_nodes)

            try:
                if instance:
                    result_node = func(instance, *node_args, **kwargs)
                else:
                    result_node = func(*node_args, **kwargs)
            except Exception as e:  # Handle exception and perform necessary cleanup
                CoreUtils.finalize_reparent(None, parent, temp_null)
                raise e

            CoreUtils.finalize_reparent(result_node, parent, temp_null)

            return result_node

        return wrapped

    @staticmethod
    def prepare_reparent(
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
    def finalize_reparent(
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
    def mfn_mesh_generator(objects):
        """Generate mfn mesh from the given list of objects.

        Parameters:
            objects (str)(obj(list): The objects to convert to mfn mesh.

        Returns:
            (generator)
        """
        import maya.OpenMaya as om
        from mayatk.node_utils import NodeUtils

        selectionList = om.MSelectionList()
        for mesh in NodeUtils.get_shape_node(pm.ls(objects)):
            selectionList.add(mesh)

        for i in range(selectionList.length()):
            dagPath = om.MDagPath()
            selectionList.getDagPath(i, dagPath)
            # print (dagPath.fullPathName()) #debug
            mfnMesh = om.MFnMesh(dagPath)
            yield mfnMesh

    @staticmethod
    def get_array_type(array):
        """Determine the given element(s) type.
        Samples only the first element.

        Parameters:
            array (str/obj/list): The components(s) to query.

        Returns:
            (list) 'str', 'int'(valid only at sub-object level), or maya object type as string.
        """
        from mayatk.node_utils import NodeUtils

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

    @staticmethod
    def calculate_mesh_similarity(mesh1: object, mesh2: object) -> float:
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
        from mayatk.node_utils import NodeUtils

        source_group = NodeUtils.get_unique_children(source)
        target_group = NodeUtils.get_unique_children(target)

        mapping = {}
        for source_child in source_group:
            highest_similarity = 0
            best_match = None
            for target_child in target_group:
                similarity = cls.calculate_mesh_similarity(source_child, target_child)
                if similarity > highest_similarity and similarity >= tolerance:
                    highest_similarity = similarity
                    best_match = target_child

            if best_match:
                mapping[source_child.name()] = best_match.name()

        return mapping

    @staticmethod
    def get_selected_channels():
        """Get any attributes (channels) that are selected in the channel box.

        Returns:
            (str) list of any selected attributes as strings. (ie. ['tx', ry', 'sz'])
        """
        channelBox = pm.mel.eval(
            "global string $gChannelBoxName; $temp=$gChannelBoxName;"
        )  # fetch maya's main channelbox
        attrs = pm.channelBox(channelBox, q=True, sma=True)

        if attrs is None:
            attrs = []
        return attrs

    @staticmethod
    def get_channel_box_attributes(
        objects,
        *args,
        include_locked=False,
        include_nonkeyable=False,
        include_object_name=False,
    ):
        """Retrieves the current values of specified attributes from the channel box for given objects.

        Parameters:
            objects (str/obj/list): Objects to query the attributes of.
            *args (str, optional): Specific attribute(s) to query. If omitted, 'selected' attributes will be queried.
            include_locked (bool, optional): Includes locked attributes in the results.
            include_nonkeyable (bool, optional): Includes non-keyable attributes in the results.
            include_object_name (bool, optional): Returns full attribute names including the object name if True.

        Returns:
            dict: Dictionary with attribute names as keys and their current values as values.

        Example:
            selected_attributes = get_channel_box_attributes(objects, 'translateX', 'rotateY', include_object_name=True)
            selected_attributes = get_channel_box_attributes(objects, include_object_name=False)
        """
        channel_box = pm.melGlobals["gChannelBoxName"]
        attributes_dict = {}

        for obj in pm.ls(objects):
            # Determine the attributes to query
            if args:
                attrs = list(args)
            else:
                # Default to selected attributes if none are specified
                attrs = pm.channelBox(channel_box, query=True, sma=True) or []

            # Append locked and nonkeyable attributes if requested
            if include_locked:
                attrs += pm.listAttr(obj, locked=True)
            if include_nonkeyable:
                attrs += pm.listAttr(obj, keyable=False)

            # Fetch attribute values
            for attr in attrs:
                attr_name = f"{obj}.{attr}" if include_object_name else attr
                value = pm.getAttr(f"{obj}.{attr}")
                attributes_dict[attr_name] = value

        return attributes_dict

    @staticmethod
    def clear_scrollfield_reporters():
        """Clears the contents of all cmdScrollFieldReporter UI objects in the current Maya session.

        This function is useful for cleaning up the script output display in Maya's UI,
        particularly before executing scripts or operations that generate a lot of output.
        It iterates over all cmdScrollFieldReporter objects and clears them, ensuring a clean
        slate for viewing new script or command output.
        """
        # Get a list of all UI objects of type "cmdScrollFieldReporter"
        reporters = pm.lsUI(type="cmdScrollFieldReporter")

        # If any reporters are found, clear them
        for reporter in reporters:
            pm.cmdScrollFieldReporter(reporter, edit=True, clear=True)

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


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
