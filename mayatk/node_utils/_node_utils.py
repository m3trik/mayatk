# !/usr/bin/python
# coding=utf-8
from typing import Any, Union, List

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import core_utils


class NodeUtils(ptk.HelpMixin):
    """ """

    @classmethod
    def get_type(cls, objects: Union[str, Any, List[Any]]) -> Union[str, List[str]]:
        """Get the object type as a string.

        Parameters:
            objects (str/obj/list): The object(s) to query.

        Returns:
            (str/list) The node type. A list is always returned when 'objects' is given as a list.
        """
        types = []
        for obj in pm.ls(objects):
            if cls.is_group(obj):
                typ = "group"
            elif cls.is_locator(obj):
                typ = "locator"
            else:
                typ = core_utils.Components.get_component_type(obj)
            if not typ:
                typ = pm.objectType(obj)
            types.append(typ)

        return ptk.format_return(types, objects)

    @staticmethod
    def is_locator(objects):
        """Determine if each of the given object(s) is a locator.
        A locator is a transform node that has a shape node child.
        The shape node defines the appearance and behavior of the locator.

        Parameters:
            objects (str/obj/list): The object(s) to query.

        Returns:
            (bool)(list) A list is always returned when 'objects' is given as a list.
        """
        objs = pm.ls(objects, transforms=True)
        # Get all locator shapes and their corresponding transforms
        locator_shapes = pm.ls(type="locator")
        locator_transforms = set(
            pm.listRelatives(locator_shapes, parent=True, path=True)
        )
        # Determine if each object is a locator
        result = [obj in locator_transforms for obj in objs]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_group(objects):
        """Determine if each of the given object(s) is a group.
        A group is defined as a transform node with children and no shape nodes directly beneath it.

        Parameters:
            objects (str/obj/list): The object(s) to query.

        Returns:
            (bool)(list) A list is always returned when 'objects' is given as a list.
        """
        result = []
        for n in pm.ls(objects):
            try:
                # Check if the object is a transform node
                is_transform = type(n) == pm.nodetypes.Transform
                # Check if the transform node does not have any shape nodes directly beneath it
                no_shapes = len(n.getShapes(noIntermediate=True)) == 0
                q = is_transform and no_shapes
            except AttributeError:
                q = False
            result.append(q)

        return ptk.format_return(result, objects)

    @classmethod
    def get_groups(cls, empty=False):
        """Get all groups in the scene.

        Parameters:
            empty (bool): Return only empty groups.

        Returns:
            (bool)
        """
        transforms = pm.ls(type="transform")

        groups = []
        for t in transforms:
            if cls.is_group(t):
                if empty:
                    children = pm.listRelatives(t, children=True)
                    if children:
                        continue
                groups.append(t)

        return groups

    @staticmethod
    def get_parent(node, all=False):
        """List the parents of an object."""
        if all:
            objects = pm.ls(node, l=1)
            return objects[0].split("|")

        try:
            return pm.listRelatives(node, parent=True, type="transform")[0]
        except IndexError:
            return None

    @staticmethod
    def get_children(node):
        """List the children of an object."""
        try:
            return pm.listRelatives(node, children=True, type="transform")
        except IndexError:
            return []

    @classmethod
    def get_unique_children(cls, objects):
        """Retrieves a unique list of objects' children (if any) in the scene, excluding the groups themselves.

        This function takes a list of objects in the scene and, if any object is a group, retrieves
        its children. The resulting list includes the unique children of the groups, but not the groups themselves.
        If an object is not a group, it will be included in the list.

        Parameters:
            objects (str/obj/list): A string, PyNode, or list of PyNodes representing the objects in the scene.

        Returns:
            list: A list containing the unique children of the groups (if any) and other objects.

        Example:
            >>> get_unique_children(<group>) # Returns: [nt.Transform(u'pCube1'), nt.Transform(u'pCube2')]
        """
        objects = pm.ls(objects, flatten=True)

        def recurse_children(obj, final_set):
            """Recursively collects children of the given object, excluding group nodes."""
            if cls.is_group(obj):
                # If the object is a group, recurse on its children
                for child in pm.listRelatives(obj, children=True, type="transform"):
                    recurse_children(child, final_set)
            else:
                # Directly add non-group transform nodes and other types of nodes
                final_set.add(obj)

        final_set = set()

        for obj in objects:
            recurse_children(obj, final_set)

        return list(final_set)

    @staticmethod
    def get_transform_node(
        nodes, returned_type="obj", attributes=False, inc=[], exc=[]
    ):
        """Get transform node(s) or node attributes.

        This method retrieves the transform nodes associated with the given input nodes. It can also return specific attributes of the nodes if requested.

        Parameters:
            nodes (str/obj/list): The node(s) or objects for which to find the associated transform nodes.
            returned_type (str): The desired returned object type.
                (valid: 'str'(default), 'obj'(transform node), 'shape'(as string), 'int'(valid only at sub-object level)).
            attributes (bool): If True, return the attributes of the node(s) instead of the node itself.
            inc (list): A list of inclusion filters to apply to the result.
            exc (list): A list of exclusion filters to apply to the result.

        Returns:
            (obj/list) Transform node(s) or node attributes. If 'nodes' is provided as a list, a list is always returned. If a single node is provided, a single object or a list, depending on the content, is returned.
        """
        result = []
        for node in pm.ls(nodes, long=True, flatten=True):
            try:
                # Check if node is a transform and directly add it
                if isinstance(node, pm.nt.Transform):
                    result.append(node)
                elif isinstance(node, pm.nt.Mesh):
                    # For mesh nodes, add their parent transform to the result
                    parent = pm.listRelatives(node, parent=True, type="transform")
                    if parent:
                        result.extend(parent)
                else:
                    # Handle all other nodes that are not specifically transforms or meshes
                    connected_transforms = pm.listRelatives(
                        pm.listHistory(node, future=True), parent=True, type="transform"
                    )
                    if connected_transforms:
                        result.extend(connected_transforms)
            except pm.MayaNodeError as e:
                print(f"Error processing node '{node}': {e}")
                continue  # Skip this node and continue with the next one

        # Remove any duplicates and ensure only transforms are in the final result
        result = list(set(result))

        if attributes:
            result = pm.listAttr(result, read=True, hasData=True)

        # Convert element type and apply filters
        result = core_utils.CoreUtils.convert_array_type(
            result, returned_type=returned_type, flatten=True
        )
        result = ptk.filter_list(result, inc, exc)
        return ptk.format_return(result, nodes)

    @classmethod
    def get_shape_node(
        cls, nodes, returned_type="obj", attributes=False, inc=[], exc=[]
    ):
        """Get shape node(s) or node attributes.

        Parameters:
            nodes (str/obj/list): A relative of a shape Node.
            returned_type (str): The desired returned object type.
                    (valid: 'str'(default), 'obj'(shape node), 'transform'(as string), 'int'(valid only at sub-object level).
            attributes (bool): Return the attributes of the node, rather then the node itself.

        Returns:
            (obj/list) node(s) or node attributes. A list is always returned when 'nodes' is given as a list.
        """
        result = []
        for node in pm.ls(nodes, long=True, flatten=True):
            shapes = pm.listRelatives(
                node, children=1, shapes=1
            )  # get shape node from transform: returns list ie. [nt.Mesh('pConeShape1')]
            if not shapes:
                shapes = pm.ls(node, type="shape")
                if not shapes:  # get shape from transform
                    try:
                        transforms = pm.listRelatives(
                            pm.listHistory(node, future=1), parent=1
                        )
                        shapes = cls.get_shape_node(transforms)
                    except Exception:
                        shapes = []
            result.extend(shapes)

        if attributes:
            result = pm.listAttr(result, read=1, hasData=1)

        # convert element type.
        result = core_utils.CoreUtils.convert_array_type(
            result, returned_type=returned_type, flatten=True
        )
        # filter
        result = ptk.filter_list(result, inc, exc)
        # return as list if `nodes` was given as a list.
        return ptk.format_return(list(set(result)), nodes)

    @staticmethod
    def get_history_node(nodes, returned_type="obj", attributes=False, inc=[], exc=[]):
        """Get history node(s) or node attributes.

        Parameters:
            nodes (str/obj/list): A relative of a history Node.
            returned_type (str): The desired returned object type.
                    (valid: 'str'(default), 'obj'(shape node), 'transform'(as string), 'int'(valid only at sub-object level).
            attributes (bool): Return the attributes of the node, rather then the node itself.

        Returns:
            (obj/list) node(s) or node attributes. A list is always returned when 'nodes' is given as a list.
        """
        result = []
        for node in pm.ls(nodes, long=True, flatten=True):
            shapes = pm.listRelatives(
                node, children=1, shapes=1
            )  # get shape node from transform: returns list ie. [nt.Mesh('pConeShape1')]
            try:
                history = pm.listConnections(shapes, source=1, destination=0)[
                    -1
                ]  # get incoming connections: returns list ie. [nt.PolyCone('polyCone1')]
            except IndexError:
                try:
                    history = node.history()[-1]
                except AttributeError as error:
                    print(
                        "{} in get_history_node\n\t# Error: {} #".format(
                            __file__, error
                        )
                    )
                    continue
            result.append(history)

        if attributes:
            result = pm.listAttr(result, read=1, hasData=1)

        # convert element type.
        result = core_utils.CoreUtils.convert_array_type(
            result, returned_type=returned_type, flatten=True
        )
        # filter
        result = ptk.filter_list(result, inc, exc)
        # return as list if `nodes` was given as a list.
        return ptk.format_return(list(set(result)), nodes)

    @classmethod
    def create_render_node(
        cls,
        node_type,
        classification=None,
        category=None,
        name=None,
        create_placement=False,
        create_shading_group=True,
        **attributes,
    ):
        """Creates a Maya node of a specified type with enhanced control over the creation process, including decisions
        on associated shading groups and placement nodes, direct flag specifications, and optional node renaming.

        Parameters:
            node_type (str): The type of node to be created (e.g., 'StingrayPBS', 'aiStandardSurface').
            classification (str, optional): Primary flag to control the node classification (e.g., 'asShader').
            category (str, optional): Secondary flag for additional control (e.g., 'surfaceShader').
            name (str, optional): Custom name for the created node. Defaults to Maya's convention if None.
            create_shading_group (bool): Whether to create a shading group for shader nodes.
            create_placement (bool): Whether to create a placement node for texture nodes.
            **attributes: Additional attributes to set on the created node.

        Returns:
            The created node (PyNode object) or None if creation fails.
        """
        # Determine flags based on node classification if not provided
        if classification is None or category is None:
            classification_string = pm.getClassification(node_type)
            if any("shader/surface" in c for c in classification_string):
                classification = classification or "asShader"
                category = category or "surfaceShader"
            elif any("texture/3d" in c for c in classification_string):
                classification = classification or "as3DTexture"
                category = category or ""
            elif any("texture/environment" in c for c in classification_string):
                classification = classification or "asEnvTexture"
                category = category or ""
            elif any("texture" in c for c in classification_string):
                classification = classification or "as2DTexture"
                category = category or ""
            elif any("light" in c for c in classification_string):
                classification = classification or "asLight"
                category = category or "defaultLight"
            else:
                classification = classification or "asUtility"
                category = category or "utility"

        # Prepare settings for node creation
        original_shading_group = pm.optionVar(query="createMaterialsWithShadingGroup")
        original_placement = pm.optionVar(query="createTexturesWithPlacement")
        pm.optionVar(intValue=("createMaterialsWithShadingGroup", create_shading_group))
        pm.optionVar(intValue=("createTexturesWithPlacement", create_placement))

        try:
            node_name = pm.mel.eval(
                f'createRenderNodeCB "-{classification}" "{category}" "{node_type}" ""'
            )
            node = pm.PyNode(node_name)
            if name:
                node.rename(name)
            if node:  # Set attributes if the node was created successfully
                cls.set_node_attributes(node, option_quiet=False, **attributes)
            return node
        except Exception as e:
            print(f"Failed to create node of type '{node_type}'. Error: {e}")
            return None
        finally:  # Restore original settings
            pm.optionVar(
                intValue=("createMaterialsWithShadingGroup", original_shading_group)
            )
            pm.optionVar(intValue=("createTexturesWithPlacement", original_placement))

    @staticmethod
    def get_connected_nodes(
        node, node_type=None, direction=None, exact=True, first_match=False
    ):
        """Finds connected nodes of a given type and direction (incoming/outgoing).

        Parameters:
            node (PyNode): The node to start searching from.
            node_type (str, optional): The node type to look for. If None, returns all connected nodes.
            direction (str, optional): 'incoming' for incoming, 'outgoing' for outgoing, None for both.
            exact (bool): Only consider nodes of the exact type. Otherwise, derived types are also considered.
            first_match (bool): Return only the first found node that matches the criteria, if any.

        Returns:
            list or obj or None: List of connected nodes or single node based on the conditions, or None if not found.
        """
        visited = set()
        stack = [node]
        filtered_nodes = []

        source, dest = {
            "incoming": (True, False),
            "outgoing": (False, True),
        }.get(direction, (True, True))

        while stack:
            current_node = stack.pop()
            visited.add(current_node)

            connected_nodes = pm.listConnections(
                current_node, s=source, d=dest, exactType=exact
            )

            for n in connected_nodes:
                if n in visited:
                    continue

                if node_type is None or pm.nodeType(n) == node_type:
                    filtered_nodes.append(n)
                    if first_match:
                        return n

                if direction is None:
                    stack.append(n)

        return filtered_nodes if not first_match else None

    @staticmethod
    def get_node_attributes(
        node, inc=[], exc=[], exc_defaults=False, quiet=True, **kwargs
    ):
        """Retrieves specified node's attributes along with their corresponding values,
        optionally excluding those with default values by adding them to the exclusion list.

        Parameters:
            node (pm.nt.DependNode): The target node from which to extract attributes.
            inc (list, optional): Attributes to include. Others are ignored unless there's overlap with 'exc'.
            exc (list, optional): Attributes to exclude. Takes priority over 'inc'.
            exc_defaults (bool, optional): If True, attributes at their default values are added to 'exc'.
            quiet (bool, optional): If False, prints errors encountered during attribute processing.
            **kwargs: Additional keyword arguments passed to pm.listAttr.

        Returns:
            dict: A dictionary where keys are attribute names and values are the attribute values.
        """
        list_attr_kwargs = {  # Set defaults (Kwargs will overwrite these values)
            "read": True,
            "hasData": True,
            "settable": True,
            "scalarAndArray": True,
            "keyable": False,
            "multi": True,
        }
        list_attr_kwargs.update(kwargs)

        all_attr_names = pm.listAttr(node, **list_attr_kwargs)
        if exc_defaults:
            for attr_name in all_attr_names:
                try:
                    defaults = pm.attributeQuery(attr_name, node=node, listDefault=True)
                    if defaults:
                        default_value = defaults[0]
                        current_value = pm.getAttr(f"{node}.{attr_name}")
                        # Check for default value and add to 'exc' if matched
                        if current_value == default_value or (
                            isinstance(current_value, float)
                            and abs(current_value - default_value) < 1e-6
                        ):
                            exc.append(attr_name)
                except Exception:
                    continue  # Skip attribute if any error occurs

        # Apply filtering with the updated 'exc' list
        filtered_attr_names = ptk.filter_list(
            pm.listAttr(node, **list_attr_kwargs), inc, exc
        )

        result = {}
        for attr_name in filtered_attr_names:
            try:
                attr_value = pm.getAttr(f"{node}.{attr_name}")
                result[attr_name] = attr_value
            except Exception as e:
                if not quiet:
                    print(f"Error processing attribute '{attr_name}' on '{node}': {e}")

        return result

    @classmethod
    def set_node_attributes(
        cls, node, option_create=False, option_quiet=False, **attributes
    ):
        """Set node attribute values, with options to create attributes if they don't exist and to suppress errors.

        Parameters:
            node (str/obj): The node to set attributes on.
            option_create (bool): If True, creates the attribute if it doesn't exist. Defaults to False.
            option_quiet (bool): If True, suppresses any errors encountered. Defaults to False.
            **attributes: Arbitrary keyword arguments for attribute names and their values.
        """
        for attr, value in attributes.items():
            attribute_name = f"{node}.{attr}"
            try:  # Check if the attribute is locked
                if pm.getAttr(attribute_name, lock=True):
                    pm.warning(f"The attribute '{attribute_name}' is locked.")
                    continue

                # Set the attribute value
                pm.setAttr(attribute_name, value)

            except pm.MayaAttributeError:
                if option_create:  # Attempt to create the attribute and set its value
                    cls.set_node_custom_attributes(node, **{attr: value})
                elif not option_quiet:
                    pm.warning(
                        f"Attribute '{attr}' does not exist on node '{node}', and 'option_create' is False."
                    )
            except Exception as e:
                if not option_quiet:
                    pm.warning(
                        f"Failed to set attribute '{attr}' on node '{node}'. Error: {str(e)}"
                    )

    @classmethod
    def get_maya_attribute_type(cls, value):
        """Gets the corresponding Maya attribute type for a given value.

        This method determines the Maya attribute type based on the type and structure of the input value.
        It supports basic data types like bool, int, float, and str, as well as more complex types like
        lists, tuples, sets, and matrices.

        Parameters:
            value: The value to determine the Maya attribute type for.

        Returns:
            str: The corresponding Maya attribute type as a string.

        Raises:
            TypeError: If the input value's type is not supported.

        Example:
            >>> get_maya_attribute_type(True) #Returns: 'bool'
            >>> get_maya_attribute_type(42) #Returns: 'long'
            >>> get_maya_attribute_type([1.0, 2.0, 3.0]) #Returns: 'double3'
            >>> get_maya_attribute_type([["a", "b"], ["c", "d"]]) #Returns: 'stringArray'
            >>> get_maya_attribute_type(Matrix([[1.0, 0.0], [0.0, 1.0]], type='float')) #Returns: 'fltMatrix'

        Notes:
            - To support additional data types, add more cases in the method as necessary.
            - Replace 'Matrix' with the correct class for a matrix in your environment.
        """
        if isinstance(value, bool):
            return "bool"
        elif isinstance(value, int):
            return "long"
        elif isinstance(value, float):
            return "double"
        elif isinstance(value, str):
            return "string"
        elif isinstance(value, (list, tuple, set)):
            element_type = cls.get_maya_attribute_type(value[0])
            length = len(value)
            # Handle compound and array cases
            if element_type in ["double", "float", "long", "short"]:
                if length == 2:
                    return f"{element_type}2"
                elif length == 3:
                    return f"{element_type}3"
                else:
                    return f"{element_type}Array"
            elif element_type == "string":
                return "stringArray"
            elif element_type == "vector":
                return "vectorArray"
            elif element_type == "point":
                return "pointArray"
            else:
                return "compound"
        # Add more cases for other data types if necessary
        elif isinstance(
            value, pm.Matrix
        ):  # Replace Matrix with the correct class for a matrix in your environment
            if value.type == "float":
                return "fltMatrix"
            elif value.type == "double":
                return "matrix"

    @classmethod
    def set_node_custom_attributes(cls, node, **attributes):
        """Set node attribute values. If the attribute doesn't exist, it will be created.

        Parameters:
            node (str/obj): The node to set attributes of.
            attributes (dict): Attributes and their corresponding value to set. ie. attribute_name=value
        """
        if isinstance(node, str):
            node = pm.PyNode(node)

        for attr, value in attributes.items():
            attr_type = cls.get_maya_attribute_type(value)

            if not pm.attributeQuery(attr, node=node, exists=True):
                if attr_type.endswith("3") or attr_type.endswith(
                    "2"
                ):  # Check if the attribute type is a compound attribute
                    node.addAttr(
                        attr, numberOfChildren=len(value), attributeType="compound"
                    )
                    component_suffixes = (
                        ["X", "Y", "Z"] if attr_type.endswith("3") else ["X", "Y"]
                    )
                    child_attr_type = attr_type[:-1]  # remove the 2 or 3 suffix.
                    for i, component in enumerate(value):
                        component_name = f"{attr}{component_suffixes[i]}"
                        node.addAttr(
                            component_name, attributeType=child_attr_type, parent=attr
                        )
                    for i, component in enumerate(
                        value
                    ):  # Separate loop to set attribute values
                        component_name = f"{attr}{component_suffixes[i]}"
                        pm.setAttr(f"{node}.{component_name}", component)
                else:
                    node.addAttr(
                        attr, defaultValue=value, keyable=True, dataType=attr_type
                    )
                    pm.setAttr(
                        f"{node}.{attr}", value
                    )  # Set attribute value immediately after creation
            else:
                if isinstance(value, (list, tuple)):  # Handle compound attributes
                    component_suffixes = (
                        ["X", "Y", "Z"] if len(value) == 3 else ["X", "Y"]
                    )
                    for i, component in enumerate(value):
                        component_name = f"{attr}{component_suffixes[i]}"
                        pm.setAttr(f"{node}.{component_name}", component)
                else:
                    pm.setAttr(f"{node}.{attr}", value)

    @staticmethod
    def connect_attributes(attr, place, file):
        """Connects a given attribute between two nodes using a specified place and file node.

        This convenience function is designed to facilitate the linking of common attributes between nodes in Maya. It's especially useful when you need to connect several attributes which share the same name in the placement and file nodes.

        Parameters:
            attr (str): The name of the attribute to connect between the nodes.
                        For example, 'coverage', 'translateFrame', 'rotateFrame', etc.
            place (str): The name of the placement node which has the attribute to connect.
            file (str): The name of the file node where the attribute will be connected to.

        Note:
            For attributes named differently between the place and file nodes, you should use the 'connectAttr' function with the respective attribute names.

        Example:
            connect_attributes('coverage', 'place2d', 'fileNode')
            connect_attributes('translateFrame', 'place2d', 'fileNode')
            connect_attributes('rotateFrame', 'place2d', 'fileNode')
            connect_attributes('mirror', 'place2d', 'fileNode')
            ...

            pm.connectAttr(f'{place}.outUV', f'{file}.uv', f=1)
            pm.connectAttr(f'{place}.outUvFilterSize', f'{file}.uvFilterSize', f=1)
        """
        pm.connectAttr("{}.{}".format(place, attr), "{}.{}".format(file, attr), f=1)

    @staticmethod
    def connect_multi_attr(*args, force=True):
        """Connect multiple node attributes at once.

        Parameters:
            args (tuple): Attributes as two element tuples. ie. (<connect from attribute>, <connect to attribute>)

        Example:
            connect_multi_attr(
                (node1.outColor, node2.aiSurfaceShader),
                (node1.outColor, node3.baseColor),
                (node4.outNormal, node5.normalCamera),
            )
        """
        for frm, to in args:
            try:
                pm.connectAttr(frm, to)
            except Exception as error:
                print("# Error:", __file__, error, "#")

    @staticmethod
    def create_assembly(nodes, assembly_name="assembly#", duplicate=False):
        """Create an assembly by parenting the input nodes to a new assembly node.

        Parameters:
            nodes (list): A list of nodes to include in the assembly.
            assembly_name (str, optional): The name of the assembly node. Defaults to 'assembly#'.
            duplicate (bool, optional): If True, duplicates the input nodes before parenting. Defaults to False.

        Returns:
            pm.PyNode: The assembly node with added properties:
                - addChild (function): Adds a new child to the assembly node.
                - children (function): Returns the list of children under the assembly node.
        """
        assembly_node = pm.assembly(name=assembly_name)

        for node in nodes:
            if duplicate:
                node = pm.duplicate(node)[0]
            pm.parent(node, assembly_node)

        assembly_node.addChild = lambda child: pm.parent(child, assembly_node)
        assembly_node.children = lambda: pm.listRelatives(assembly_node, children=True)

        return assembly_node

    @staticmethod
    def get_instances(objects=None, return_parent_objects=False):
        """get any intances of given object, or if None given; get all instanced objects in the scene.

        Parameters:
            objects (str/obj/list): Parent object/s.
            return_parent_objects (bool): Return instances and the given parent objects together.

        Returns:
            (list)
        """
        instances = []

        if objects is None:  # get all instanced objects in the scene.
            import maya.OpenMaya as om

            iterDag = om.MItDag(om.MItDag.kBreadthFirst)
            while not iterDag.isDone():
                instanced = om.MItDag.isInstanced(iterDag)
                if instanced:
                    instances.append(iterDag.fullPathName())
                iterDag.next()
        else:
            shapes = pm.listRelatives(objects, s=1)
            instances = pm.listRelatives(shapes, ap=1)
            if not return_parent_objects:
                [instances.remove(obj) for obj in objects]

        return instances

    @classmethod
    @core_utils.CoreUtils.undoable
    def convert_to_instances(
        cls,
        objects=None,
        append="",
        freeze_transforms=False,
        center_pivot=True,
        delete_history=True,
    ):
        """The first selected object will be instanced across all other selected objects.

        Parameters:
            objects (list): A list of objects to convert to instances. The first object will be the instance parent.
            append (str): Append a string to the end of any instanced objects. ie. '_INST'
            freeze_transforms (bool): Freeze transforms on the given objects.
            center_pivot (bool): Center pivot on the given objects.
            delete_history (bool): Delete history on the given objects.

        Returns:
            (list) The instanced objects.
        """
        objects = pm.ls(objects) or pm.ls(orderedSelection=True)
        try:
            source, targets = objects[0], objects[1:]
        except IndexError:
            pm.warning("Operation requires a selection of at least two objects.")
            return

        for target in targets:
            if freeze_transforms:
                pm.makeIdentity(target, apply=True, translate=True)

            if center_pivot:
                pm.xform(target, centerPivots=True)

            if delete_history:
                pm.delete(target, constructionHistory=True)

            name = target.name()
            objParent = pm.listRelatives(target, parent=True)

            instance = pm.instance(source)
            cls.uninstance(target)

            # Move object to center of the last selected items bounding box # pm.xform(instance, translation=pos, worldSpace=1, relative=1) #move to the original objects location.
            pm.matchTransform(
                instance, target, position=True, rotation=True, scale=True, pivots=True
            )

            try:
                # Parent the instance under the original objects parent.
                pm.parent(instance, objParent)
            except RuntimeError:  # It is already a child of the parent.
                pass

            # Delete history for the object so that the namespace is cleared.
            pm.delete(target, constructionHistory=True)
            pm.delete(target)
            pm.rename(instance, name + append)
        pm.select(targets)

        return targets

    @classmethod
    def uninstance(cls, objects):
        """Un-Instance the given objects.

        Parameters:
            objects (str/obj/list): The objects to un-instance. If 'all' is given all instanced objects in the scene will be uninstanced.
        """
        if objects == "all":
            objects = cls.get_instances()

        for obj in pm.ls(objects):
            children = pm.listRelatives(obj, fullPath=1, children=1)
            parents = pm.listRelatives(children[0], fullPath=1, allParents=1)

            if len(parents) > 1:
                duplicatedObject = pm.duplicate(obj)
                pm.delete(obj)
                pm.rename(duplicatedObject[0], obj)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
