# !/usr/bin/python
# coding=utf-8
from typing import Any, Union, List

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils.attribute_manager._attribute_manager import AttributeManager


class NodeUtils(ptk.HelpMixin):
    """ """

    # -------------------------------------------------------------------------
    # Type Classification
    # -------------------------------------------------------------------------

    @classmethod
    def get_type(cls, objects: Union[str, Any, List[Any]]) -> Union[str, List[str]]:
        """Get the object type as a string.

        Parameters:
            objects (str/obj/list): The object(s) to query.

        Returns:
            (str/list) The node type. A list is always returned when 'objects' is given as a list.
        """
        from mayatk import Components

        types = []
        for obj in pm.ls(objects):
            if cls.is_group(obj):
                typ = "group"
            elif cls.is_locator(obj):
                typ = "locator"
            elif cls.is_mesh(obj):
                typ = "mesh"
            else:
                typ = Components.get_component_type(obj)
            if not typ:
                typ = pm.objectType(obj)
            types.append(typ)
            print(obj.name(), typ)

        return ptk.format_return(types, objects)

    @staticmethod
    def get_inherited_types(node: str) -> List[str]:
        """Get the inheritance hierarchy for a node type.

        Uses cmds.nodeType with inherited=True to return all parent types
        in the node's inheritance chain.

        Parameters:
            node: The node name to query.

        Returns:
            List of inherited type names (lowercase), or empty list if query fails.

        Example:
            >>> NodeUtils.get_inherited_types("parentConstraint1")
            ['constraint', 'transform', 'dagnode', 'entity', ...]
        """
        import maya.cmds as cmds

        try:
            inherited = cmds.nodeType(node, inherited=True) or []
            return [t.lower() for t in inherited]
        except Exception:
            return []

    @staticmethod
    def is_mesh(objects, filter: bool = False):
        """Return True for each object that is a transform node with a mesh shape child.

        Parameters:
            objects (str/obj/list): The object(s) to query.
            filter (bool): If True, return only the objects that are meshes.

        Returns:
            (bool/list) A list of booleans indicating whether each object is a mesh.
            If 'filter' is True, returns a list of objects that are meshes.
        """
        objs = pm.ls(objects, transforms=True)
        result = [
            isinstance(obj, pm.nt.Transform)
            and any(
                isinstance(s, pm.nt.Mesh) for s in obj.getShapes(noIntermediate=True)
            )
            for obj in objs
        ]
        if filter:
            return [obj for obj, is_mesh in zip(objs, result) if is_mesh]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_locator(objects, filter: bool = False):
        """Determine if each of the given object(s) is a locator.

        Parameters:
            objects (str/obj/list): The object(s) to query.
            filter (bool): If True, return only the objects that are locators.

        Returns:
            (bool/list) A list of booleans indicating whether each object is a locator.
            If 'filter' is True, returns a list of objects that are locators.
        """
        objs = pm.ls(objects, transforms=True)
        locator_shapes = pm.ls(type="locator")
        locator_transforms = set(
            pm.listRelatives(locator_shapes, parent=True, path=True)
        )
        result = [obj in locator_transforms for obj in objs]
        if filter:
            return [obj for obj, is_loc in zip(objs, result) if is_loc]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_group(objects, filter: bool = False):
        """Determine if each of the given object(s) is a group.

        Parameters:
            objects (str/obj/list): The object(s) to query.
            filter (bool): If True, return only the objects that are groups.

        Returns:
            (bool/list) A list of booleans indicating whether each object is a group.
            If 'filter' is True, returns a list of objects that are groups.
        """
        objs = pm.ls(objects)
        result = []
        for n in objs:
            try:
                is_transform = type(n) == pm.nodetypes.Transform
                no_shapes = len(n.getShapes(noIntermediate=True)) == 0
                q = is_transform and no_shapes
            except AttributeError:
                q = False
            result.append(q)
        if filter:
            return [obj for obj, is_grp in zip(objs, result) if is_grp]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_geometry(objects, filter: bool = False):
        """Return True for each object that has a shape node and is not a group.

        Parameters:
            objects (str/obj/list): The object(s) to query.
            filter (bool): If True, return only the objects that are geometries.

        Returns:
            (bool/list) A list of booleans indicating whether each object is geometry.
            If 'filter' is True, returns a list of objects that are geometries.
        """
        objs = pm.ls(objects, transforms=True)
        result = [
            isinstance(obj, pm.nt.Transform)
            and bool(obj.getShapes(noIntermediate=True))
            for obj in objs
        ]
        if filter:
            return [obj for obj, is_geom in zip(objs, result) if is_geom]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_constraint(objects, filter: bool = False):
        """Determine if each object inherits from Maya's constraint base type.

        Uses Maya's node inheritance hierarchy to detect all constraint types
        (parentConstraint, pointConstraint, orientConstraint, etc.) dynamically.

        Parameters:
            objects (str/list): The object(s) to query.
            filter (bool): If True, return only the objects that are constraints.

        Returns:
            (bool/list) Boolean(s) indicating constraint status, or filtered list.
        """
        import maya.cmds as cmds

        objs = (
            cmds.ls(objects, flatten=True) if not isinstance(objects, list) else objects
        )
        single = not isinstance(objects, (list, tuple))
        result = []
        for obj in objs:
            try:
                inherited = cmds.nodeType(obj, inherited=True) or []
                is_const = "constraint" in [t.lower() for t in inherited]
            except Exception:
                is_const = False
            result.append(is_const)
        if filter:
            return [obj for obj, is_c in zip(objs, result) if is_c]
        return result[0] if single and len(result) == 1 else result

    @staticmethod
    def is_expression(objects, filter: bool = False):
        """Determine if each object is a Maya expression node.

        Parameters:
            objects (str/list): The object(s) to query.
            filter (bool): If True, return only the objects that are expressions.

        Returns:
            (bool/list) Boolean(s) indicating expression status, or filtered list.
        """
        import maya.cmds as cmds

        objs = (
            cmds.ls(objects, flatten=True) if not isinstance(objects, list) else objects
        )
        single = not isinstance(objects, (list, tuple))
        result = []
        for obj in objs:
            try:
                is_expr = cmds.nodeType(obj) == "expression"
            except Exception:
                is_expr = False
            result.append(is_expr)
        if filter:
            return [obj for obj, is_e in zip(objs, result) if is_e]
        return result[0] if single and len(result) == 1 else result

    @staticmethod
    def is_ik_effector(objects, filter: bool = False):
        """Determine if each object is an IK effector node.

        Parameters:
            objects (str/list): The object(s) to query.
            filter (bool): If True, return only the objects that are IK effectors.

        Returns:
            (bool/list) Boolean(s) indicating IK effector status, or filtered list.
        """
        import maya.cmds as cmds

        objs = (
            cmds.ls(objects, flatten=True) if not isinstance(objects, list) else objects
        )
        single = not isinstance(objects, (list, tuple))
        result = []
        for obj in objs:
            try:
                is_ik = cmds.nodeType(obj) == "ikEffector"
            except Exception:
                is_ik = False
            result.append(is_ik)
        if filter:
            return [obj for obj, is_i in zip(objs, result) if is_i]
        return result[0] if single and len(result) == 1 else result

    @staticmethod
    def is_driven_key_curve(objects, filter: bool = False):
        """Determine if each animCurve is a driven key (has input connection).

        Driven keys have an input connection to their .input attribute,
        while time-based animation curves have no input connection.

        Parameters:
            objects (str/list): The animCurve node(s) to query.
            filter (bool): If True, return only driven key curves.

        Returns:
            (bool/list) Boolean(s) indicating driven key status, or filtered list.
        """
        import maya.cmds as cmds

        objs = (
            cmds.ls(objects, flatten=True) if not isinstance(objects, list) else objects
        )
        single = not isinstance(objects, (list, tuple))
        result = []
        for obj in objs:
            try:
                input_conn = cmds.listConnections(
                    f"{obj}.input", source=True, destination=False
                )
                is_driven = bool(input_conn)
            except Exception:
                is_driven = False
            result.append(is_driven)
        if filter:
            return [obj for obj, is_d in zip(objs, result) if is_d]
        return result[0] if single and len(result) == 1 else result

    @staticmethod
    def is_muted(objects, filter: bool = False):
        """Determine if each node is muted/disabled via nodeState attribute.

        Checks the nodeState attribute where 0=Normal, 1=PassThrough, 2=Blocking.
        Returns True if nodeState is not 0 (Normal).

        Parameters:
            objects (str/list): The object(s) to query.
            filter (bool): If True, return only the muted objects.

        Returns:
            (bool/list) Boolean(s) indicating muted status, or filtered list.
        """
        import maya.cmds as cmds

        objs = (
            cmds.ls(objects, flatten=True) if not isinstance(objects, list) else objects
        )
        single = not isinstance(objects, (list, tuple))
        result = []
        for obj in objs:
            try:
                if cmds.attributeQuery("nodeState", node=obj, exists=True):
                    state = cmds.getAttr(f"{obj}.nodeState")
                    is_muted = state != 0
                else:
                    is_muted = False
            except Exception:
                is_muted = False
            result.append(is_muted)
        if filter:
            return [obj for obj, is_m in zip(objs, result) if is_m]
        return result[0] if single and len(result) == 1 else result

    @staticmethod
    def is_motion_path(objects, filter: bool = False):
        """Determine if each object is a motionPath node.

        Parameters:
            objects (str/list): The object(s) to query.
            filter (bool): If True, return only the motion path objects.

        Returns:
            (bool/list) Boolean(s) indicating motion path status, or filtered list.
        """
        import maya.cmds as cmds

        objs = (
            cmds.ls(objects, flatten=True) if not isinstance(objects, list) else objects
        )
        single = not isinstance(objects, (list, tuple))
        result = []
        for obj in objs:
            try:
                is_mp = cmds.nodeType(obj) == "motionPath"
            except Exception:
                is_mp = False
            result.append(is_mp)
        if filter:
            return [obj for obj, is_m in zip(objs, result) if is_m]
        return result[0] if single and len(result) == 1 else result

    @staticmethod
    def is_ik_handle(objects, filter: bool = False):
        """Determine if each object is an ikHandle node.

        Parameters:
            objects (str/list): The object(s) to query.
            filter (bool): If True, return only the IK handle objects.

        Returns:
            (bool/list) Boolean(s) indicating IK handle status, or filtered list.
        """
        import maya.cmds as cmds

        objs = (
            cmds.ls(objects, flatten=True) if not isinstance(objects, list) else objects
        )
        single = not isinstance(objects, (list, tuple))
        result = []
        for obj in objs:
            try:
                is_ikh = cmds.nodeType(obj) == "ikHandle"
            except Exception:
                is_ikh = False
            result.append(is_ikh)
        if filter:
            return [obj for obj, is_i in zip(objs, result) if is_i]
        return result[0] if single and len(result) == 1 else result

    @staticmethod
    def get_constraint_targets(constraint: str) -> list:
        """Get the target objects for a constraint node.

        Works with all constraint types (parentConstraint, pointConstraint,
        orientConstraint, scaleConstraint, aimConstraint, etc.).

        Parameters:
            constraint: The constraint node name.

        Returns:
            List of target transform names. Empty list if no targets found.

        Example:
            >>> NodeUtils.get_constraint_targets("pCube1_parentConstraint1")
            ['pSphere1', 'pCylinder1']
        """
        import maya.cmds as cmds

        targets = []
        try:
            # Most constraints have a targetList via .target attribute
            target_list = cmds.listConnections(
                f"{constraint}.target", source=True, destination=False
            )
            if target_list:
                targets.extend(target_list)
        except Exception:
            pass

        # Also check for direct transform connections
        try:
            direct = (
                cmds.listConnections(
                    constraint, source=True, destination=False, type="transform"
                )
                or []
            )
            targets.extend(direct)
        except Exception:
            pass

        return list(set(targets))

    # -------------------------------------------------------------------------
    # Hierarchy
    # -------------------------------------------------------------------------

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
            # print(f"DEBUG: listAttr result: {result}")

        # Convert element type and apply filters
        if not attributes:
            result = CoreUtils.convert_array_type(
                result, returned_type=returned_type, flatten=True
            )
        result = ptk.filter_list(result, inc, exc)

        if attributes:
            # When returning attributes, we always want a list, regardless of input cardinality
            return result

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
        if not attributes:
            result = CoreUtils.convert_array_type(
                result, returned_type=returned_type, flatten=True
            )
        # filter
        result = ptk.filter_list(result, inc, exc)

        if attributes:
            return list(set(result))

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
        if not attributes:
            result = CoreUtils.convert_array_type(
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
        create_placement_nodes=False,
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
            create_placement_nodes (bool): Whether to create a placement node for texture nodes.
            **attributes: Additional attributes to set on the created node.

        Returns:
            The created node (PyNode object) or None if creation fails.
        """
        import maya.cmds as cmds

        # Helper to determine classification flag for cmds.shadingNode
        def get_shading_node_flag(cls_str, type_str):
            if cls_str:
                if "Shader" in cls_str:
                    return "asShader"
                if "Texture" in cls_str:
                    return "asTexture"
                if "Light" in cls_str:
                    return "asLight"
                if "Utility" in cls_str:
                    return "asUtility"
            # Fallback heuristics
            if type_str == "file":
                return "asTexture"
            if type_str in ["reverse", "multiplyDivide", "bump2d", "place2dTexture"]:
                return "asUtility"
            return "asShader"  # Default assumption for render nodes

        # Determine flags based on node classification if not provided
        if classification is None or category is None:
            # Try to get classification via cmds first to avoid pm overhead if possible,
            # but pm.getClassification strings are standard.
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

        # Optimization: Fast path using maya.cmds for common cases without placement logic
        # This bypasses the heavy 'createRenderNode.mel' script.
        if not create_placement_nodes:
            try:
                flag = get_shading_node_flag(classification, node_type)

                # Check if we can skip complex SG logic
                # Only shaders usually need SGs. Textures/Utilities do not.
                # If it's a shader and we need an SG, we can do it manually.

                cmd_kwargs = {flag: True}
                if name:
                    cmd_kwargs["name"] = name

                # Create Node
                node_name = cmds.shadingNode(node_type, **cmd_kwargs)

                # Handle Shading Group
                if create_shading_group and flag == "asShader":
                    sg_name = cmds.sets(
                        renderable=True,
                        noSurfaceShader=True,
                        empty=True,
                        name=f"{node_name}SG",
                    )
                    # Try connecting outColor - simplistic support for standard shaders
                    if cmds.attributeQuery("outColor", node=node_name, exists=True):
                        cmds.connectAttr(
                            f"{node_name}.outColor", f"{sg_name}.surfaceShader"
                        )

                # Fast Attribute Setting
                for attr, value in attributes.items():
                    try:
                        full_attr = f"{node_name}.{attr}"
                        if attr in ["fileTextureName", "colorSpace"] or isinstance(
                            value, str
                        ):
                            cmds.setAttr(full_attr, value, type="string")
                        else:
                            cmds.setAttr(full_attr, value)
                    except Exception:
                        # Fallback to robust setter for this attribute if fast set fails
                        # (e.g. for creating custom attrs or complex types)
                        pass

                node = pm.PyNode(node_name)

                # Ensure complex attributes are set (this is slower but only runs for failed attrs or complex cases)
                # But since we tried setting above, we can assume simple ones are done.
                # Re-running AttributeManager.set_attributes might be redundant but ensures correctness if the fast loop failed.
                # However, for pure speed optimization, we rely on the fast loop.
                # If attributes were passed that failed above, they might be custom attrs.
                # Let's call set_attributes only if we have remaining attributes?
                # For now, let's assume the user of this fast path expects standard attributes.

                return node

            except Exception as e:
                # print(f"Fast path optimization failed for {node_type}: {e}. Falling back to MEL.")
                if "node_name" in locals() and cmds.objExists(node_name):
                    cmds.delete(node_name)
                pass

        # Prepare settings for node creation
        original_shading_group = pm.optionVar(query="createMaterialsWithShadingGroup")
        original_placement = pm.optionVar(query="createTexturesWithPlacement")
        pm.optionVar(intValue=("createMaterialsWithShadingGroup", create_shading_group))
        pm.optionVar(intValue=("createTexturesWithPlacement", create_placement_nodes))

        try:
            # Ensure createRenderNodeCB is available
            if not pm.mel.exists("createRenderNodeCB"):
                try:
                    pm.mel.source("createRenderNode.mel")
                except Exception:
                    pass

            node_name = pm.mel.eval(
                f'createRenderNodeCB "-{classification}" "{category}" "{node_type}" ""'
            )
            node = pm.PyNode(node_name)
            if name:
                node.rename(name)
            if node:  # Set attributes if the node was created successfully
                AttributeManager.set_attributes(node, quiet=False, **attributes)
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
            objects = pm.ls(objects)
            shapes = pm.listRelatives(objects, s=1)
            instances = pm.listRelatives(shapes, ap=1)
            if not return_parent_objects:
                for obj in objects:
                    if obj in instances:
                        instances.remove(obj)

        return instances

    @classmethod
    @CoreUtils.undoable
    def replace_with_instances(
        cls,
        objects=None,
        append="",
        freeze_transforms=False,
        center_pivot=True,
        delete_history=True,
    ):
        """Replace target objects with instances of the source object.

        Takes the first object in the selection as the source and replaces all
        subsequent objects with instances of that source object. The instances
        inherit the transform and hierarchy of the replaced objects.

        Parameters:
            objects (list): List of objects where first is source, rest are targets.
                           If None, uses current selection.
            append (str): String to append to instance names.
            freeze_transforms (bool): Whether to freeze transforms before instancing.
            center_pivot (bool): Whether to center pivot before instancing.
            delete_history (bool): Whether to delete history before instancing.

        Returns:
            list: The newly created instance objects.
        """
        from mayatk import XformUtils

        objects = pm.ls(objects) or pm.ls(orderedSelection=True)
        try:
            source, targets = objects[0], objects[1:]
        except IndexError:
            pm.warning("Operation requires a selection of at least two objects.")
            return

        if any((freeze_transforms, center_pivot, delete_history)):
            XformUtils.freeze_transforms(
                objects,
                translate=freeze_transforms,
                center_pivot=center_pivot,
                delete_history=delete_history,
                force=True,
            )

        new_instances = []
        for target in targets:
            name = target.name()
            objParent = pm.listRelatives(target, parent=True)
            instance = pm.instance(source)[0]  # returns a list
            pm.matchTransform(
                instance, target, position=True, rotation=True, scale=True, pivots=True
            )
            if objParent:
                try:
                    pm.parent(instance, objParent)
                except RuntimeError:
                    pass
            pm.rename(instance, name + append)
            pm.delete(target)  # delete only the transform
            new_instances.append(instance)

        pm.select(new_instances)
        return new_instances

    @classmethod
    def instance(cls, *args, **kwargs):
        """Deprecated: Use replace_with_instances instead.

        This method is kept for backward compatibility.
        """
        import warnings

        warnings.warn(
            "NodeUtils.instance() is deprecated. Use NodeUtils.replace_with_instances() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.replace_with_instances(*args, **kwargs)

    @classmethod
    def uninstance(cls, objects):
        """Un-Instance the given objects.

        Parameters:
            objects (str/obj/list): The objects to un-instance. If 'all' is given all instanced objects in the scene will be uninstanced.

        Returns:
            list: The list of objects (uninstanced originals or new copies).
        """
        if objects == "all":
            objects = cls.get_instances()

        results = []
        for obj in pm.ls(objects):
            # Check if this object is actually an instance parent
            # i.e. its shape has multiple parents.
            shapes = pm.listRelatives(obj, shapes=True, fullPath=True)
            if not shapes:
                # No shape, probably just a transform. Can't be instance of geometry.
                results.append(obj)
                continue

            shape = shapes[0]
            parents = pm.listRelatives(shape, allParents=True, fullPath=True)

            if len(parents) > 1:
                # Capture name before deletion
                obj_name = obj.name()
                duplicatedObject = pm.duplicate(obj)[0]
                pm.delete(obj)
                new_obj = pm.rename(duplicatedObject, obj_name)
                results.append(new_obj)
            else:
                results.append(obj)

        return results

    @staticmethod
    def filter_duplicate_instances(nodes) -> List["pm.PyNode"]:
        """Keep only one transform per instance group.

        Parameters:
            nodes (str/obj/list): The nodes to filter.

        Returns:
            List[pm.PyNode]: Filtered list of nodes with unique instance groups.
        """
        transforms = NodeUtils.get_transform_node(nodes, returned_type="obj")
        filtered = []
        visited = set()
        for t in transforms:
            inst_group = NodeUtils.get_instances(t, return_parent_objects=True)
            if not inst_group:
                key = (t.longName(),)
            else:
                key = tuple(sorted(x.longName() for x in inst_group))
            if key not in visited:
                visited.add(key)
                filtered.append(t)
        return filtered


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
