# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
from pythontk import Iter, format_return

# from this package:
from mayatk import misc_utils, cmpt_utils


class Node:
    """ """

    @staticmethod
    def node_exists(n, search="name"):
        """Check if the node exists in the current scene.

        Parameters:
            search (str): The search parameters. valid: 'name', 'type', 'exactType'

        Returns:
            (bool)
        """
        if search == "name":
            return bool(pm.ls(n))
        elif search == "type":
            return bool(pm.ls(type=n))
        elif search == "exactType":
            return bool(pm.ls(exactType=n))

    @classmethod
    def get_type(cls, objects):
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
                typ = cmpt_utils.Cmpt.get_component_type(obj)
            if not typ:
                typ = pm.objectType(obj)
            types.append(typ)

        return format_return(types, objects)

    @classmethod
    def is_locator(cls, obj):
        """Check if the object is a locator.
        A locator is a transform node that has a shape node child.
        The shape node defines the appearance and behavior of the locator.

        Parameters:
            obj () = The object to query.

        Returns:
            (bool)
        """
        shape = cls.get_shape_node(obj)
        return pm.nodeType(shape) == "locator"

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

        return format_return(result, objects)

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

    @staticmethod
    def get_unique_children(objects):
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

        final_set = (
            {  # Combine the selected objects and their children into a single set.
                child
                for obj in objects
                for child in (
                    pm.listRelatives(obj, children=True, type="transform")
                    if obj.nodeType() == "transform"
                    and pm.listRelatives(obj, children=True, type="transform")
                    else (
                        [obj]
                        if obj.nodeType() != "transform"
                        or obj.listRelatives(children=True)
                        else []
                    )
                )
            }
        )
        return list(final_set)  # Convert the set back to a list.

    @staticmethod
    def get_transform_node(
        nodes, returned_type="obj", attributes=False, inc=[], exc=[]
    ):
        """Get transform node(s) or node attributes.

        Parameters:
            nodes (str/obj/list): A relative of a transform Node.
            returned_type (str): The desired returned object type. Not valid with the `attributes` parameter.
                    (valid: 'str'(default), 'obj').
            attributes (bool): Return the attributes of the node, rather then the node itself.

        Returns:
            (obj/list) node(s) or node attributes. A list is always returned when 'nodes' is given as a list.
        """
        result = []
        for node in pm.ls(nodes):
            transforms = pm.ls(node, type="transform")
            if not transforms:  # from shape
                shapeNodes = pm.ls(node, objectsOnly=1)
                transforms = pm.listRelatives(shapeNodes, parent=1)
                if not transforms:  # from history
                    try:
                        transforms = pm.listRelatives(
                            pm.listHistory(node, future=1), parent=1
                        )
                    except Exception:
                        transforms = []
            for n in transforms:
                result.append(n)

        if attributes:
            result = pm.listAttr(result, read=1, hasData=1)

        # convert element type.
        result = misc_utils.Misc.convert_array_type(
            result, returned_type=returned_type, flatten=True
        )
        # filter
        result = Iter.filter_list(result, inc, exc)
        # return as list if `nodes` was given as a list.
        return format_return(list(set(result)), nodes)

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
        for node in pm.ls(nodes):
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
            for n in shapes:
                result.append(n)

        if attributes:
            result = pm.listAttr(result, read=1, hasData=1)

        # convert element type.
        result = misc_utils.Misc.convert_array_type(
            result, returned_type=returned_type, flatten=True
        )
        # filter
        result = Iter.filter_list(result, inc, exc)
        # return as list if `nodes` was given as a list.
        return format_return(list(set(result)), nodes)

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
        for node in pm.ls(nodes):
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
        result = misc_utils.Misc.convert_array_type(
            result, returned_type=returned_type, flatten=True
        )
        # filter
        result = Iter.filter_list(result, inc, exc)
        # return as list if `nodes` was given as a list.
        return format_return(list(set(result)), nodes)

    @classmethod
    def create_render_node(
        cls,
        node_type,
        flag="asShader",
        secondary_flag="surfaceShader",
        name="",
        tex="",
        texture_node=False,
        post_command="",
        **kwargs,
    ):
        """Procedure to create the node classified as specified by the inputs.

        Parameters:
            node_type (str): The type of node to be created. ie. 'StingrayPBS' or 'aiStandardSurface'
            flag (str): A flag specifying which how to classify the node created.
                    valid:  as2DTexture, as3DTexture, asEnvTexture, asShader, asLight, asUtility
            secondary_flag (str): A secondary flag used to make decisions in combination with 'asType'
                    valid:  -asBump : defines a created texture as a bump
                                    -asNoShadingGroup : for materials; create without a shading group
                                    -asDisplacement : for anything; map the created node to a displacement material.
                                    -asUtility : for anything; do whatever the $as flag says, but also classify as a utility
                                    -asPostProcess : for any postprocess node
            name (str): The desired node name.
            tex (str): The path to a texture file for those nodes that support one.
            texture_node (bool): If not needed, the `place2dTexture` node will be deleted after creation.
            post_command (str): A command entered by the user when invoking create_render_node.
                            The command will substitute the string %node with the name of the
                            node it creates.  createRenderWindow will be closed if a command
                            is not the null string ("").
            kwargs () = Set additional node attributes after creation. ie. colorSpace='Raw', alphaIsLuminance=1, ignoreColorSpaceFileRules=1

        Returns:
            (obj) node

        Example:
            create_render_node('StingrayPBS')
            create_render_node('file', flag='as2DTexture', tex=f, texture_node=True, colorSpace='Raw', alphaIsLuminance=1, ignoreColorSpaceFileRules=1)
            create_render_node('aiSkyDomeLight', tex=pathToHdrMap, name='env', camera=0, skyRadius=0) #turn off skydome and viewport visibility.
        """
        node = pm.PyNode(
            pm.mel.createRenderNodeCB(
                "-" + flag, secondary_flag, node_type, post_command
            )
        )  # node = pm.shadingNode(typ, asTexture=True)

        if not texture_node:
            pm.delete(
                pm.listConnections(
                    node, type="place2dTexture", source=True, exactType=True
                )
            )

        if tex:
            try:
                node.fileTextureName.set(tex)
            except Exception as error:
                print("# Error:", __file__, error, "#")

        if name:
            try:
                pm.rename(node, name)

            except RuntimeError as error:
                print("# Error:", __file__, error, "#")

        cls.set_node_attributes(node, **kwargs)
        return node

    @staticmethod
    def get_incoming_node_by_type(node, typ, exact=True):
        """Get the first connected node of the given type with an incoming connection to the given node.

        Parameters:
            node (str/obj): A node with incoming connections.
            typ (str): The node type to search for. ie. 'StingrayPBS'
            exact (bool): Only consider nodes of the exact type. Otherwise, derived types are also taken into account.

        Returns:
            (obj)(None) node if found.

        Example: env_file_node = get_incoming_node_by_type(env_node, 'file') #get the incoming file node.
        """
        nodes = pm.listConnections(node, type=typ, source=True, exactType=exact)
        return format_return([pm.PyNode(n) for n in nodes])

    @staticmethod
    def get_outgoing_node_by_type(node, typ, exact=True):
        """Get the connected node of the given type with an outgoing connection to the given node.

        Parameters:
            node (str/obj): A node with outgoing connections.
            typ (str): The node type to search for. ie. 'file'
            exact (bool): Only consider nodes of the exact type. Otherwise, derived types are also taken into account.

        Returns:
            (list)(obj)(None) node(s)

        Example: srSG_node = get_outgoing_node_by_type(sr_node, 'shadingEngine') #get the outgoing shadingEngine node.
        """
        nodes = pm.listConnections(node, type=typ, destination=True, exactType=exact)
        return format_return([pm.PyNode(n) for n in nodes])

    @staticmethod
    def get_node_attributes(node, inc=[], exc=[], mapping=False, **kwargs):
        """Retrieves specified node's attributes along with their corresponding values, represented as a dictionary.

        Parameters:
            node (obj): The target node from which to extract attributes.
            inc (str/list): Specifies which attributes to include in the output. Any other attributes will be ignored. If there is any overlap, the exclude parameter takes priority over this.
            exc (str/list): Determines which attributes to leave out from the result.
            mapping (bool): If set to True, returns a dictionary mapping attributes to their respective values.
            kwargs: Supports additional keyword arguments that are passed to the listAttr command in Maya.

        Returns:
            dict: A dictionary that pairs each attribute (as a string) to its current value. If 'mapping' is False, only the values of the attributes are returned.
        """
        kwargs.setdefault("read", True)
        kwargs.setdefault("hasData", True)
        kwargs.setdefault("settable", True)

        attr_names = Iter.filter_list(
            pm.listAttr(node, **kwargs),
            inc,
            exc,
        )

        result = {}
        for attr_name in attr_names:
            try:
                result[attr_name] = pm.getAttr(f"{node}.{attr_name}", silent=True)
            except pm.MayaAttributeError as e:
                print(
                    f"Error encountered while extracting attribute {attr_name} from node {node}: {e}"
                )
                continue

        return result if mapping else result.values()

    @classmethod
    def set_node_attributes(cls, node, **attributes):
        """Set node attribute values. If the attribute doesn't exist, it will be created.

        Parameters:
            node (str/obj): The node to set attributes of.
            attributes (dict): Attributes and their corresponding value to set. ie. attribute_name=value
        """
        for attr, value in attributes.items():
            try:
                pm.setAttr(f"{node}.{attr}", value)
            except AttributeError:
                cls.set_node_custom_attributes(node, **{attr: value})

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
            print("attr_type", attr_type)

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


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------


# def filter_components(cls, frm, inc=[], exc=[]):
#       '''Filter the given 'frm' list for the items in 'exc'.

#       Parameters:
#           frm (str/obj/list): The components(s) to filter.
#           inc (str/obj/list): The component(s) to include.
#           exc (str/obj/list): The component(s) to exclude.
#                               (exlude take precidence over include)
#       Returns:
#           (list)

#       Example: filter_components('obj.vtx[:]', 'obj.vtx[1:23]') #returns: [MeshVertex('objShape.vtx[0]'), MeshVertex('objShape.vtx[24]'), MeshVertex('objShape.vtx[25]')]
#       '''
#       exc = pm.ls(exc, flatten=True)
#       if not exc:
#           return frm

#       c, *other = components = pm.ls(frm, flatten=True)
#       #determine the type of items in 'exc' by sampling the first element.
#       if isinstance(c, str):
#           if 'Shape' in c:
#               rtn = 'transform'
#           else:
#               rtn = 'str'
#       elif isinstance(c, int):
#           rtn = 'int'
#       else:
#           rtn = 'obj'

#       if exc and isinstance(exc[0], int): #attempt to create a component list from the given integers. warning: this will only exclude from a single object.
#           obj = pm.ls(frm, objectsOnly=1)
#           if len(obj)>1:
#               return frm
#           component_type = cls.get_component_type(frm[0])
#           typ = cls.convert_alias(component_type) #get the correct component_type variable from possible args.
#           exc = ["{}.{}[{}]".format(obj[0], typ, n) for n in exc]

#       if inc and isinstance(inc[0], int): #attempt to create a component list from the given integers. warning: this will only exclude from a single object.
#           obj = pm.ls(frm, objectsOnly=1)
#           if len(obj)>1:
#               return frm
#           component_type = cls.get_component_type(frm[0])
#           typ = cls.convert_alias(component_type) #get the correct component_type variable from possible args.
#           inc = ["{}.{}[{}]".format(obj[0], typ, n) for n in inc]

#       inc = misc_utils.Misc.convert_array_type(inc, returned_type=rtn, flatten=True) #assure both lists are of the same type for comparison.
#       exc = misc_utils.Misc.convert_array_type(exc, returned_type=rtn, flatten=True)
#       return [i for i in components if i not in exc and (inc and i in inc)]
