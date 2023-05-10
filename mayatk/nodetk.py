# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

from pythontk import Iter, formatReturn

# from this package:
from mayatk import coretk, cmpttk


class Node:
    """ """

    @staticmethod
    def nodeExists(n, search="name"):
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
    def getType(cls, objects):
        """Get the object type as a string.

        Parameters:
                objects (str/obj/list): The object(s) to query.

        Returns:
                (str/list) The node type. A list is always returned when 'objects' is given as a list.
        """
        types = []
        for obj in pm.ls(objects):
            if cls.isGroup(obj):
                typ = "group"
            elif cls.isLocator(obj):
                typ = "locator"
            else:
                typ = cmpttk.Cmpt.getComponentType(obj)
            if not typ:
                typ = pm.objectType(obj)
            types.append(typ)

        return formatReturn(types, objects)

    @classmethod
    def isLocator(cls, obj):
        """Check if the object is a locator.
        A locator is a transform node that has a shape node child.
        The shape node defines the appearance and behavior of the locator.

        Parameters:
                obj () = The object to query.

        Returns:
                (bool)
        """
        shape = cls.getShapeNode(obj)
        return pm.nodeType(shape) == "locator"

    @staticmethod
    def isGroup(objects):
        """Determine if each of the given object(s) is a group.
        A group is defined as a transform with children.

        Parameters:
                nodes (str/obj/list): The object(s) to query.

        Returns:
                (bool)(list) A list is always returned when 'objects' is given as a list.
        """
        result = []
        for n in pm.ls(objects):
            try:
                q = all(
                    (
                        type(n) == pm.nodetypes.Transform,
                        all(
                            (
                                [
                                    type(c) == pm.nodetypes.Transform
                                    for c in n.getChildren()
                                ]
                            )
                        ),
                    )
                )
            except AttributeError as error:
                q = False
            result.append(q)

        return formatReturn(result, objects)

    @classmethod
    def getGroups(cls, empty=False):
        """Get all groups in the scene.

        Parameters:
                empty (bool): Return only empty groups.

        Returns:
                (bool)
        """
        transforms = pm.ls(type="transform")

        groups = []
        for t in transforms:
            if cls.isGroup(t):
                if empty:
                    children = pm.listRelatives(t, children=True)
                    if children:
                        continue
                groups.append(t)

        return groups

    @staticmethod
    def getParent(node, all=False):
        """List the parents of an object."""
        if all:
            objects = pm.ls(node, l=1)
            tokens = []
            return objects[0].split("|")

        try:
            return pm.listRelatives(node, parent=True, type="transform")[0]
        except IndexError as error:
            return None

    @staticmethod
    def getChildren(node):
        """List the children of an object."""
        try:
            return pm.listRelatives(node, children=True, type="transform")
        except IndexError as error:
            return []

    @staticmethod
    def getUniqueChildren(objects):
        """Retrieves a unique list of objects' children (if any) in the scene, excluding the groups themselves.

        This function takes a list of objects in the scene and, if any object is a group, retrieves
        its children. The resulting list includes the unique children of the groups, but not the groups themselves.
        If an object is not a group, it will be included in the list.

        Parameters:
                objects (str/obj/list): A string, PyNode, or list of PyNodes representing the objects in the scene.

        Returns:
                list: A list containing the unique children of the groups (if any) and other objects.

        Example:
                >>> getUniqueChildren(<group>) # Returns: [nt.Transform(u'pCube1'), nt.Transform(u'pCube2')]
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
    def getTransformNode(nodes, returnType="obj", attributes=False, inc=[], exc=[]):
        """Get transform node(s) or node attributes.

        Parameters:
                nodes (str/obj/list): A relative of a transform Node.
                returnType (str): The desired returned object type. Not valid with the `attributes` parameter.
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
                    except Exception as error:
                        transforms = []
            for n in transforms:
                result.append(n)

        if attributes:
            result = pm.listAttr(result, read=1, hasData=1)

        # convert element type.
        result = coretk.Core.convertArrayType(
            result, returnType=returnType, flatten=True
        )
        # filter
        result = Iter.filterList(result, inc, exc)
        # return as list if `nodes` was given as a list.
        return formatReturn(list(set(result)), nodes)

    @classmethod
    def getShapeNode(cls, nodes, returnType="obj", attributes=False, inc=[], exc=[]):
        """Get shape node(s) or node attributes.

        Parameters:
                nodes (str/obj/list): A relative of a shape Node.
                returnType (str): The desired returned object type.
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
                        shapes = cls.getShapeNode(transforms)
                    except Exception as error:
                        shapes = []
            for n in shapes:
                result.append(n)

        if attributes:
            result = pm.listAttr(result, read=1, hasData=1)

        # convert element type.
        result = coretk.Core.convertArrayType(
            result, returnType=returnType, flatten=True
        )
        # filter
        result = Iter.filterList(result, inc, exc)
        # return as list if `nodes` was given as a list.
        return formatReturn(list(set(result)), nodes)

    @staticmethod
    def getHistoryNode(nodes, returnType="obj", attributes=False, inc=[], exc=[]):
        """Get history node(s) or node attributes.

        Parameters:
                nodes (str/obj/list): A relative of a history Node.
                returnType (str): The desired returned object type.
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
            except IndexError as error:
                try:
                    history = node.history()[-1]
                except AttributeError as error:
                    print(
                        "{} in getHistoryNode\n\t# Error: {} #".format(__file__, error)
                    )
                    continue
            result.append(history)

        if attributes:
            result = pm.listAttr(result, read=1, hasData=1)

        # convert element type.
        result = coretk.Core.convertArrayType(
            result, returnType=returnType, flatten=True
        )
        # filter
        result = Iter.filterList(result, inc, exc)
        # return as list if `nodes` was given as a list.
        return formatReturn(list(set(result)), nodes)

    @classmethod
    def createRenderNode(
        cls,
        nodeType,
        flag="asShader",
        flag2="surfaceShader",
        name="",
        tex="",
        place2dTexture=False,
        postCommand="",
        **kwargs,
    ):
        """Procedure to create the node classified as specified by the inputs.

        Parameters:
                nodeType (str): The type of node to be created. ie. 'StingrayPBS' or 'aiStandardSurface'
                flag (str): A flag specifying which how to classify the node created.
                        valid:  as2DTexture, as3DTexture, asEnvTexture, asShader, asLight, asUtility
                flag2 (str): A secondary flag used to make decisions in combination with 'asType'
                        valid:  -asBump : defines a created texture as a bump
                                        -asNoShadingGroup : for materials; create without a shading group
                                        -asDisplacement : for anything; map the created node to a displacement material.
                                        -asUtility : for anything; do whatever the $as flag says, but also classify as a utility
                                        -asPostProcess : for any postprocess node
                name (str): The desired node name.
                tex (str): The path to a texture file for those nodes that support one.
                place2dTexture (bool): If not needed, the place2dTexture node will be deleted after creation.
                postCommand (str): A command entered by the user when invoking createRenderNode.
                                The command will substitute the string %node with the name of the
                                node it creates.  createRenderWindow will be closed if a command
                                is not the null string ("").
                kwargs () = Set additional node attributes after creation. ie. colorSpace='Raw', alphaIsLuminance=1, ignoreColorSpaceFileRules=1

        Returns:
                (obj) node

        Example: createRenderNode('StingrayPBS')
        Example: createRenderNode('file', flag='as2DTexture', tex=f, place2dTexture=True, colorSpace='Raw', alphaIsLuminance=1, ignoreColorSpaceFileRules=1)
        Example: createRenderNode('aiSkyDomeLight', tex=pathToHdrMap, name='env', camera=0, skyRadius=0) #turn off skydome and viewport visibility.
        """
        node = pm.PyNode(
            pm.mel.createRenderNodeCB("-" + flag, flag2, nodeType, postCommand)
        )  # node = pm.shadingNode(typ, asTexture=True)

        if not place2dTexture:
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

        cls.setNodeAttributes(node, **kwargs)
        return node

    @staticmethod
    def getIncomingNodeByType(node, typ, exact=True):
        """Get the first connected node of the given type with an incoming connection to the given node.

        Parameters:
                node (str/obj): A node with incoming connections.
                typ (str): The node type to search for. ie. 'StingrayPBS'
                exact (bool): Only consider nodes of the exact type. Otherwise, derived types are also taken into account.

        Returns:
                (obj)(None) node if found.

        Example: env_file_node = getIncomingNodeByType(env_node, 'file') #get the incoming file node.
        """
        nodes = pm.listConnections(node, type=typ, source=True, exactType=exact)
        return formatReturn([pm.PyNode(n) for n in nodes])

    @staticmethod
    def getOutgoingNodeByType(node, typ, exact=True):
        """Get the connected node of the given type with an outgoing connection to the given node.

        Parameters:
                node (str/obj): A node with outgoing connections.
                typ (str): The node type to search for. ie. 'file'
                exact (bool): Only consider nodes of the exact type. Otherwise, derived types are also taken into account.

        Returns:
                (list)(obj)(None) node(s)

        Example: srSG_node = getOutgoingNodeByType(sr_node, 'shadingEngine') #get the outgoing shadingEngine node.
        """
        nodes = pm.listConnections(node, type=typ, destination=True, exactType=exact)
        return formatReturn([pm.PyNode(n) for n in nodes])

    @staticmethod
    def getNodeAttributes(node, inc=[], exc=[], mapping=False):
        """Get node attributes and their corresponding values as a dict.

        Parameters:
                node (obj): The node to get attributes of.
                include (str/list): Attributes to include. All others will be omitted. Exclude takes precedence over include.
                exclude (str/list): Attributes to exclude from the returned dictionary.
                mapping (bool): Return a dictionary that maps the attributes to their values.

        Returns:
                (dict) {'string attribute': current value}
        """
        filtered_attrs = Iter.filterList(pm.listAttr(node, read=1, hasData=1), inc, exc)

        result = {
            attr: pm.getAttr(f"{node}.{attr}", silent=True) for attr in filtered_attrs
        }
        return result if mapping else result.values()

    @classmethod
    def setNodeAttributes(cls, node, **attributes):
        """Set node attribute values. If the attribute doesn't exist, it will be created.

        Parameters:
                node (str/obj): The node to set attributes of.
                attributes (dict): Attributes and their corresponding value to set. ie. attribute_name=value
        """
        for attr, value in attributes.items():
            try:
                pm.setAttr(f"{node}.{attr}", value)
            except Exception:
                cls.setNodeCustomAttributes(node, **{attr: value})

    @classmethod
    def getMayaAttributeType(cls, value):
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

        Examples:
                >>> getMayaAttributeType(True) #Returns: 'bool'
                >>> getMayaAttributeType(42) #Returns: 'long'
                >>> getMayaAttributeType([1.0, 2.0, 3.0]) #Returns: 'double3'
                >>> getMayaAttributeType([["a", "b"], ["c", "d"]]) #Returns: 'stringArray'
                >>> getMayaAttributeType(Matrix([[1.0, 0.0], [0.0, 1.0]], type='float')) #Returns: 'fltMatrix'

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
            element_type = cls.getMayaAttributeType(value[0])
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
            value, Matrix
        ):  # Replace Matrix with the correct class for a matrix in your environment
            if value.type == "float":
                return "fltMatrix"
            elif value.type == "double":
                return "matrix"

    @classmethod
    def setNodeCustomAttributes(cls, node, **attributes):
        """Set node attribute values. If the attribute doesn't exist, it will be created.

        Parameters:
                node (str/obj): The node to set attributes of.
                attributes (dict): Attributes and their corresponding value to set. ie. attribute_name=value
        """
        if isinstance(node, str):
            node = pm.PyNode(node)

        for attr, value in attributes.items():
            attr_type = cls.getMayaAttributeType(value)
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
    def connectAttributes(attr, place, file):
        """A convenience procedure for connecting common attributes between two nodes.

        Parameters:
                attr () =
                place () =
                file () =

        Example:
        Use convenience command to connect attributes which share
        their names for both the placement and file nodes.
                connectAttributes('coverage', 'place2d', fileNode')
                connectAttributes('translateFrame', 'place2d', fileNode')
                connectAttributes('rotateFrame', 'place2d', fileNode')
                connectAttributes('mirror', 'place2d', fileNode')
                connectAttributes('stagger', 'place2d', fileNode')
                connectAttributes('wrap', 'place2d', fileNode')
                connectAttributes('wrapV', 'place2d', fileNode')
                connectAttributes('repeatUV', 'place2d', fileNode')
                connectAttributes('offset', 'place2d', fileNode')
                connectAttributes('rotateUV', 'place2d', fileNode')

        These two are named differently.
                connectAttr -f ( $place2d + ".outUV" ) ( $fileNode + ".uv" );
                connectAttr -f ( $place2d + ".outUvFilterSize" ) ( $fileNode + ".uvFilterSize" );
        """
        pm.connectAttr("{}.{}".format(place, attr), "{}.{}".format(file, attr), f=1)

    @staticmethod
    def connectMultiAttr(*args, force=True):
        """Connect multiple node attributes at once.

        Parameters:
                args (tuple): Attributes as two element tuples. ie. (<connect from attribute>, <connect to attribute>)

        Example: connectMultiAttr(
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
    def createAssembly(nodes, assembly_name="assembly#", duplicate=False):
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


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------


# def filterComponents(cls, frm, inc=[], exc=[]):
#       '''Filter the given 'frm' list for the items in 'exc'.

#       Parameters:
#           frm (str/obj/list): The components(s) to filter.
#           inc (str/obj/list): The component(s) to include.
#           exc (str/obj/list): The component(s) to exclude.
#                               (exlude take precidence over include)
#       Returns:
#           (list)

#       Example: filterComponents('obj.vtx[:]', 'obj.vtx[1:23]') #returns: [MeshVertex('objShape.vtx[0]'), MeshVertex('objShape.vtx[24]'), MeshVertex('objShape.vtx[25]')]
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
#           componentType = cls.getComponentType(frm[0])
#           typ = cls.convertAlias(componentType) #get the correct componentType variable from possible args.
#           exc = ["{}.{}[{}]".format(obj[0], typ, n) for n in exc]

#       if inc and isinstance(inc[0], int): #attempt to create a component list from the given integers. warning: this will only exclude from a single object.
#           obj = pm.ls(frm, objectsOnly=1)
#           if len(obj)>1:
#               return frm
#           componentType = cls.getComponentType(frm[0])
#           typ = cls.convertAlias(componentType) #get the correct componentType variable from possible args.
#           inc = ["{}.{}[{}]".format(obj[0], typ, n) for n in inc]

#       inc = coretk.Core.convertArrayType(inc, returnType=rtn, flatten=True) #assure both lists are of the same type for comparison.
#       exc = coretk.Core.convertArrayType(exc, returnType=rtn, flatten=True)
#       return [i for i in components if i not in exc and (inc and i in inc)]
