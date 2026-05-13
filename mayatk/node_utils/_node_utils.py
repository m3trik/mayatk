# !/usr/bin/python
# coding=utf-8
from typing import Any, Union, List

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception as error:
    cmds = None
    mel = None
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils, as_strings, short_name
from mayatk.node_utils.attributes._attributes import Attributes


class _AssemblyHandle(str):
    """A ``str`` subclass for an assembly node that retains a small legacy-compatible API.

    Returned by :meth:`NodeUtils.create_assembly`.  It behaves as the assembly
    transform's name everywhere a string is expected (``cmds.*`` calls,
    f-strings, ``.startswith``, etc.) and exposes ``.addChild`` /
    ``.children`` to mirror the legacy helper that callers used.
    """

    def addChild(self, child):
        cmds.parent(str(child), str(self))

    def children(self):
        return cmds.listRelatives(str(self), children=True, fullPath=True) or []


class NodeUtils(ptk.HelpMixin):
    """ """

    # -------------------------------------------------------------------------
    # Type Classification
    # -------------------------------------------------------------------------

    @classmethod
    def get_type(cls, objects: Union[str, Any, List[Any]]) -> Union[str, List[str]]:
        """Get the object type as a string.

        Returns:
            (str/list) The node type. A list is always returned when 'objects' is given as a list.
        """
        from mayatk import Components

        types = []
        for obj in cmds.ls(as_strings(objects)) or []:
            if cls.is_group(obj):
                typ = "group"
            elif cls.is_locator(obj):
                typ = "locator"
            elif cls.is_mesh(obj):
                typ = "mesh"
            else:
                typ = Components.get_component_type(obj)
            if not typ:
                typ = cmds.objectType(obj)
            types.append(typ)
            print(short_name(obj), typ)

        return ptk.format_return(types, objects)

    @staticmethod
    def get_inherited_types(node: str) -> List[str]:
        """Get the inheritance hierarchy for a node type."""
        try:
            inherited = cmds.nodeType(str(node), inherited=True) or []
            return [t.lower() for t in inherited]
        except Exception:
            return []

    @classmethod
    def is_mesh(cls, objects, filter: bool = False):
        """Return True for each object that is a transform node with a mesh shape child.

        Returns:
            (bool/list) A list of booleans indicating whether each object is a mesh.
            If 'filter' is True, returns a list of objects that are meshes.
        """
        objs = cmds.ls(as_strings(objects), transforms=True) or []
        result = []
        for obj in objs:
            shapes = cls.get_shapes(obj, no_intermediate=True)
            is_mesh = bool(shapes) and any(
                cmds.objectType(s) == "mesh" for s in shapes
            )
            result.append(is_mesh)
        if filter:
            return [obj for obj, is_mesh in zip(objs, result) if is_mesh]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_locator(objects, filter: bool = False):
        """Determine if each of the given object(s) is a locator."""
        objs = cmds.ls(as_strings(objects), transforms=True) or []
        locator_shapes = cmds.ls(type="locator") or []
        locator_transforms = set(
            cmds.listRelatives(locator_shapes, parent=True, path=True) or []
        )
        result = [obj in locator_transforms for obj in objs]
        if filter:
            return [obj for obj, is_loc in zip(objs, result) if is_loc]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_group(objects, filter: bool = False):
        """Determine if each of the given object(s) is a group.

        A "group" is a transform with no shape children.
        """
        objs = cmds.ls(as_strings(objects)) or []
        result = []
        for n in objs:
            try:
                is_transform = cmds.objectType(n) == "transform"
                # NOTE: ``noIntermediate=True`` so that orig (intermediate)
                # shapes don't make a group look like geometry.
                shapes = cmds.listRelatives(
                    n, shapes=True, noIntermediate=True
                ) or []
                q = is_transform and not shapes
            except Exception:
                q = False
            result.append(q)
        if filter:
            return [obj for obj, is_grp in zip(objs, result) if is_grp]
        return ptk.format_return(result, objects)

    @classmethod
    def is_geometry(cls, objects, filter: bool = False):
        """Return True for each object that has a shape node and is not a group."""
        objs = cmds.ls(as_strings(objects), transforms=True) or []
        result = []
        for obj in objs:
            shapes = cls.get_shapes(obj, no_intermediate=True)
            result.append(cmds.objectType(obj) == "transform" and bool(shapes))
        if filter:
            return [obj for obj, is_geom in zip(objs, result) if is_geom]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_constraint(objects, filter: bool = False):
        """Determine if each object inherits from Maya's constraint base type."""
        objs = (
            cmds.ls(as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else as_strings(objects)
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
        """Determine if each object is a Maya expression node."""
        objs = (
            cmds.ls(as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else as_strings(objects)
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
        """Determine if each object is an IK effector node."""
        objs = (
            cmds.ls(as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else as_strings(objects)
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
        """Determine if each animCurve is a driven key (has input connection)."""
        objs = (
            cmds.ls(as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else as_strings(objects)
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
        """Determine if each node is muted/disabled via nodeState attribute."""
        objs = (
            cmds.ls(as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else as_strings(objects)
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
        """Determine if each object is a motionPath node."""
        objs = (
            cmds.ls(as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else as_strings(objects)
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
        """Determine if each object is an ikHandle node."""
        objs = (
            cmds.ls(as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else as_strings(objects)
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
        """Get the target objects for a constraint node."""
        constraint = str(constraint)
        targets = []
        try:
            target_list = cmds.listConnections(
                f"{constraint}.target", source=True, destination=False
            )
            if target_list:
                targets.extend(target_list)
        except Exception:
            pass

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
        """Get all groups in the scene."""
        transforms = cmds.ls(type="transform") or []

        groups = []
        for t in transforms:
            if cls.is_group(t):
                if empty:
                    children = cmds.listRelatives(t, children=True)
                    if children:
                        continue
                groups.append(t)

        return groups

    @staticmethod
    def get_parent(node, all=False, full_path=False, type="transform"):
        """Return the parent of *node*.

        Parameters:
            all (bool): If True, return the full ancestor chain by splitting the
                long path. ``type`` is ignored in this mode.
            full_path (bool): When True, return the parent's full DAG path.
            type (str|None): Only return a parent of this node type. Pass
                ``None`` to return the immediate parent regardless of type.
        """
        node = str(node)
        if all:
            objects = cmds.ls(node, l=True) or []
            return objects[0].split("|") if objects else []

        kwargs = {"parent": True, "fullPath": full_path, "path": not full_path}
        if type is not None:
            kwargs["type"] = type
        parents = cmds.listRelatives(node, **kwargs) or []
        return parents[0] if parents else None

    @staticmethod
    def get_children(node, type="transform", full_path=False):
        """List the children of *node*.

        Parameters:
            type (str|None): Filter children by node type. ``None`` returns
                children of any type.
            full_path (bool): When True, return full DAG paths.
        """
        kwargs = {"children": True, "fullPath": full_path, "path": not full_path}
        if type is not None:
            kwargs["type"] = type
        return cmds.listRelatives(str(node), **kwargs) or []

    @staticmethod
    def get_shapes(node, no_intermediate=True, full_path=True):
        """Return the shape children of a transform.

        Always returns a list (never ``None``).
        """
        return cmds.listRelatives(
            str(node),
            shapes=True,
            noIntermediate=no_intermediate,
            fullPath=full_path,
            path=not full_path,
        ) or []

    @classmethod
    def get_shape(cls, node, no_intermediate=True, full_path=True):
        """Return the first shape of a transform, or ``None``."""
        shapes = cls.get_shapes(
            node, no_intermediate=no_intermediate, full_path=full_path
        )
        return shapes[0] if shapes else None

    @staticmethod
    def is_intermediate(shape):
        """Return True if *shape* is an intermediate (orig) shape."""
        try:
            return bool(cmds.getAttr(f"{shape}.intermediateObject"))
        except Exception:
            return False

    @staticmethod
    def node_is(node, type_name):
        """Return True if ``cmds.objectType(node)`` matches *type_name* exactly."""
        return cmds.objectType(str(node)) == type_name

    @staticmethod
    def list_transforms(objects=None, **ls_kwargs):
        """Transforms whose shapes match the given ``cmds.ls`` criteria.

        Replacement for ``pm.listTransforms`` — runs ``cmds.ls`` with the
        provided kwargs and walks each result up to its transform parent,
        de-duplicating while preserving order.
        """
        nodes = (
            cmds.ls(objects, **ls_kwargs) if objects is not None else cmds.ls(**ls_kwargs)
        ) or []
        seen = set()
        transforms = []
        for node in nodes:
            if cmds.nodeType(node) == "transform":
                xform = node
            else:
                parents = cmds.listRelatives(node, parent=True, path=True) or []
                xform = parents[0] if parents else None
            if xform and xform not in seen:
                seen.add(xform)
                transforms.append(xform)
        return transforms

    @classmethod
    def get_unique_children(cls, objects):
        """Retrieves a unique list of objects' children (if any) in the scene, excluding the groups themselves."""
        objects = cmds.ls(as_strings(objects), long=True, flatten=True) or []

        def recurse_children(obj, final_set):
            if cls.is_group(obj):
                for child in (
                    cmds.listRelatives(
                        obj, children=True, type="transform", fullPath=True
                    )
                    or []
                ):
                    recurse_children(child, final_set)
            else:
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

        Returns:
            (str/list) Transform node(s) or node attributes.
        """
        result = []
        for node in cmds.ls(as_strings(nodes), long=True, flatten=True) or []:
            try:
                # Strip component suffix (e.g. ".vtx[0]") to query the node.
                base = node.split(".")[0]
                node_type = cmds.objectType(base)
                if node_type == "transform":
                    long_paths = cmds.ls(base, long=True) or [base]
                    result.append(long_paths[0])
                elif node_type == "mesh":
                    parent = cmds.listRelatives(
                        base, parent=True, type="transform", fullPath=True
                    )
                    if parent:
                        result.extend(parent)
                else:
                    history = cmds.listHistory(base, future=True) or []
                    connected_transforms = cmds.listRelatives(
                        history, parent=True, type="transform", fullPath=True
                    ) or []
                    if connected_transforms:
                        result.extend(connected_transforms)
            except Exception as e:
                print(f"Error processing node '{node}': {e}")
                continue

        result = list(set(result))

        if attributes:
            result = cmds.listAttr(result, read=True, hasData=True) or []

        if not attributes:
            result = CoreUtils.convert_array_type(
                result, returned_type=returned_type, flatten=True
            )
        result = ptk.filter_list(result, inc, exc)

        if attributes:
            return result

        return ptk.format_return(result, nodes)

    @classmethod
    def get_shape_node(
        cls, nodes, returned_type="obj", attributes=False, inc=[], exc=[]
    ):
        """Get shape node(s) or node attributes."""
        result = []
        for node in cmds.ls(as_strings(nodes), long=True, flatten=True) or []:
            shapes = cmds.listRelatives(node, children=True, shapes=True) or []
            if not shapes:
                shapes = cmds.ls(node, type="shape") or []
                if not shapes:
                    try:
                        history = cmds.listHistory(node, future=True) or []
                        transforms = cmds.listRelatives(history, parent=True) or []
                        shapes = cls.get_shape_node(transforms)
                    except Exception:
                        shapes = []
            result.extend(shapes)

        if attributes:
            result = cmds.listAttr(result, read=True, hasData=True) or []

        if not attributes:
            result = CoreUtils.convert_array_type(
                result, returned_type=returned_type, flatten=True
            )
        result = ptk.filter_list(result, inc, exc)

        if attributes:
            return list(set(result))

        return ptk.format_return(list(set(result)), nodes)

    @staticmethod
    def get_history_node(nodes, returned_type="obj", attributes=False, inc=[], exc=[]):
        """Get history node(s) or node attributes."""
        result = []
        for node in cmds.ls(as_strings(nodes), long=True, flatten=True) or []:
            shapes = cmds.listRelatives(node, children=True, shapes=True) or []
            history = []
            try:
                conns = (
                    cmds.listConnections(shapes, source=True, destination=False) or []
                )
                if conns:
                    history = [conns[-1]]
            except Exception:
                pass
            if not history:
                try:
                    h = cmds.listHistory(node) or []
                    if h:
                        history = [h[-1]]
                except Exception as error:
                    print(f"{__file__} in get_history_node\n\t# Error: {error} #")
                    continue
            result.extend(history)

        if attributes:
            result = cmds.listAttr(result, read=True, hasData=True) or []

        if not attributes:
            result = CoreUtils.convert_array_type(
                result, returned_type=returned_type, flatten=True
            )
        result = ptk.filter_list(result, inc, exc)
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
        """Creates a Maya node of a specified type with enhanced control over the creation process."""

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
            if type_str == "file":
                return "asTexture"
            if type_str in ["reverse", "multiplyDivide", "bump2d", "place2dTexture"]:
                return "asUtility"
            return "asShader"

        if classification is None or category is None:
            classification_string = cmds.getClassification(node_type) or []
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

        # Optimization: fast path for common cases without placement logic.
        if not create_placement_nodes:
            try:
                flag = get_shading_node_flag(classification, node_type)

                cmd_kwargs = {flag: True}
                if name:
                    cmd_kwargs["name"] = name

                node_name = cmds.shadingNode(node_type, **cmd_kwargs)

                if create_shading_group and flag == "asShader":
                    sg_name = cmds.sets(
                        renderable=True,
                        noSurfaceShader=True,
                        empty=True,
                        name=f"{node_name}SG",
                    )
                    if cmds.attributeQuery("outColor", node=node_name, exists=True):
                        cmds.connectAttr(
                            f"{node_name}.outColor", f"{sg_name}.surfaceShader"
                        )

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
                        pass

                return node_name

            except Exception:
                if "node_name" in locals() and cmds.objExists(node_name):
                    cmds.delete(node_name)
                pass

        original_shading_group = cmds.optionVar(query="createMaterialsWithShadingGroup")
        original_placement = cmds.optionVar(query="createTexturesWithPlacement")
        cmds.optionVar(intValue=("createMaterialsWithShadingGroup", create_shading_group))
        cmds.optionVar(intValue=("createTexturesWithPlacement", create_placement_nodes))

        try:
            if not mel.eval('exists "createRenderNodeCB"'):
                try:
                    mel.eval('source "createRenderNode.mel"')
                except Exception:
                    pass

            node_name = mel.eval(
                f'createRenderNodeCB "-{classification}" "{category}" "{node_type}" ""'
            )
            if name and node_name:
                node_name = cmds.rename(node_name, name)
            if node_name:
                Attributes.set_attributes(node_name, quiet=False, **attributes)
            return node_name
        except Exception as e:
            print(f"Failed to create node of type '{node_type}'. Error: {e}")
            return None
        finally:
            cmds.optionVar(
                intValue=("createMaterialsWithShadingGroup", original_shading_group)
            )
            cmds.optionVar(intValue=("createTexturesWithPlacement", original_placement))

    @staticmethod
    def get_connected_nodes(
        node, node_type=None, direction=None, exact=True, first_match=False
    ):
        """Finds connected nodes of a given type and direction (incoming/outgoing)."""
        node = str(node)
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

            connected_nodes = cmds.listConnections(
                current_node, s=source, d=dest, exactType=exact
            ) or []

            for n in connected_nodes:
                if n in visited:
                    continue

                if node_type is None or cmds.nodeType(n) == node_type:
                    filtered_nodes.append(n)
                    if first_match:
                        return n

                if direction is None:
                    stack.append(n)

        return filtered_nodes if not first_match else None

    @staticmethod
    def create_assembly(nodes, assembly_name="assembly#", duplicate=False):
        """Create an assembly by parenting the input nodes to a new assembly node.

        Returns:
            _AssemblyHandle: A string-like handle for the assembly node.  Behaves
            like the assembly's name in all ``cmds.*`` calls and exposes
            ``.addChild`` / ``.children`` to mirror the older legacy helper.
        """
        assembly_node = cmds.assembly(name=assembly_name)

        for node in nodes:
            node = str(node)
            if duplicate:
                node = cmds.duplicate(node)[0]
            cmds.parent(node, assembly_node)

        return _AssemblyHandle(assembly_node)

    @staticmethod
    def get_instances(objects=None, return_parent_objects=False):
        """Get any instances of given object, or if None given, get all instanced objects in the scene."""
        instances = []

        if objects is None:
            import maya.OpenMaya as om1

            iterDag = om1.MItDag(om1.MItDag.kBreadthFirst)
            while not iterDag.isDone():
                instanced = om1.MItDag.isInstanced(iterDag)
                if instanced:
                    instances.append(iterDag.fullPathName())
                iterDag.next()
        else:
            objects = cmds.ls(as_strings(objects), long=True) or []
            shapes = cmds.listRelatives(objects, shapes=True, fullPath=True) or []
            instances = (
                cmds.listRelatives(shapes, allParents=True, fullPath=True) or []
            )
            if not return_parent_objects:
                obj_set = set(objects)
                instances = [i for i in instances if i not in obj_set]

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

        Returns:
            list: The newly created instance objects.
        """
        from mayatk import XformUtils

        if objects is None:
            objects = cmds.ls(orderedSelection=True) or []
        else:
            objects = cmds.ls(as_strings(objects)) or []
        try:
            source, targets = objects[0], objects[1:]
        except IndexError:
            cmds.warning("Operation requires a selection of at least two objects.")
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
            name = short_name(target)
            objParent = cmds.listRelatives(target, parent=True) or []
            instance = cmds.instance(source)[0]
            cmds.matchTransform(
                instance, target, position=True, rotation=True, scale=True, pivots=True
            )
            if objParent:
                try:
                    parented = cmds.parent(instance, objParent[0]) or []
                except RuntimeError:
                    parented = []
                if parented:
                    instance = parented[0]
            instance = cmds.rename(instance, name + append)
            cmds.delete(target)
            new_instances.append(instance)

        if new_instances:
            cmds.select(new_instances)
        return new_instances

    @classmethod
    def instance(cls, *args, **kwargs):
        """Deprecated: Use replace_with_instances instead."""
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

        For each transform, forks every instanced shape it carries into
        a unique copy and swaps it in — without ever deleting the
        transform itself.  Name, world matrix, parent, children and any
        non-instanced shapes are preserved.  Sibling instance transforms
        retain the original shape.
        """
        import maya.api.OpenMaya as om

        if objects == "all":
            objects = cls.get_instances()

        results = []
        for obj in cmds.ls(as_strings(objects)) or []:
            obj_long = (cmds.ls(obj, long=True) or [obj])[0]
            shapes = (
                cmds.listRelatives(
                    obj_long, shapes=True, fullPath=True, noIntermediate=True
                )
                or []
            )

            for shape in shapes:
                instance_parents = (
                    cmds.listRelatives(shape, allParents=True, fullPath=True) or []
                )
                if len(instance_parents) <= 1:
                    continue  # shape is not instanced on this transform

                shape_short = shape.split("|")[-1]
                dup_xform = None
                try:
                    # Duplicate the shape (not the transform — avoids
                    # walking children). ``cmds.duplicate`` always forks
                    # geometry, so the new shape is unique even when the
                    # source was instanced.
                    dup_xform = cmds.duplicate(
                        shape,
                        returnRootsOnly=True,
                        name=f"{shape_short}__uninst_tmp",
                    )[0]
                    dup_shapes = (
                        cmds.listRelatives(
                            dup_xform,
                            shapes=True,
                            fullPath=True,
                            noIntermediate=True,
                        )
                        or []
                    )
                    if not dup_shapes:
                        raise RuntimeError("duplicate produced no shape node")
                    new_shape = dup_shapes[0]

                    # Graft the unique shape under obj_long first so the
                    # transform is never momentarily shapeless.
                    # ``relative`` keeps the local transform — the shape
                    # is positioned by obj_long's matrix, which is
                    # unchanged.
                    cmds.parent(new_shape, obj_long, shape=True, relative=True)

                    # Surgically remove this transform's instance link
                    # to the original shape.  ``cmds.parent -rm -s`` does
                    # NOT work — it tries to unparent shapes to world,
                    # which Maya silently rejects, leaving the instance
                    # link intact.  MFnDagNode.removeChild removes only
                    # the (parent, child) edge — sibling instance
                    # transforms keep the shape.
                    sel = om.MSelectionList()
                    sel.add(obj_long)
                    sel.add(shape)
                    om.MFnDagNode(sel.getDependNode(0)).removeChild(
                        sel.getDependNode(1)
                    )

                    cmds.delete(dup_xform)
                    dup_xform = None
                except (RuntimeError, ValueError) as e:
                    cmds.warning(
                        f"uninstance failed for {obj_long} (shape {shape}): {e}"
                    )
                    if dup_xform and cmds.objExists(dup_xform):
                        try:
                            cmds.delete(dup_xform)
                        except RuntimeError:
                            pass

            results.append(obj_long)

        return results

    @staticmethod
    def filter_duplicate_instances(nodes) -> List[str]:
        """Keep only one transform per instance group."""
        transforms = NodeUtils.get_transform_node(nodes, returned_type="obj")
        if not isinstance(transforms, list):
            transforms = [transforms] if transforms else []
        filtered = []
        visited = set()
        for t in transforms:
            inst_group = NodeUtils.get_instances(t, return_parent_objects=True) or []
            if not inst_group:
                long_paths = cmds.ls(t, long=True) or [t]
                key = (long_paths[0],)
            else:
                long_paths = []
                for x in inst_group:
                    lp = cmds.ls(x, long=True) or [x]
                    long_paths.append(lp[0])
                key = tuple(sorted(long_paths))
            if key not in visited:
                visited.add(key)
                filtered.append(t)
        return filtered

    # -------------------------------------------------------------------------
    # Persistent Data Nodes
    # -------------------------------------------------------------------------

    @staticmethod
    def ensure_data_node(node_name: str, attr_name: str) -> str:
        """Get or create a name-locked network node with a writable string attribute.

        The node's **name** is locked to protect it from accidental
        renaming; the node itself is left unlocked so callers can
        write to data attributes without friction.  Existing nodes
        that were locked in older scenes are transparently unlocked
        and migrated.

        Returns:
            str: The (possibly newly created) network node's name.
        """
        if cmds.objExists(node_name):
            node = node_name
        else:
            node = cmds.createNode("network", name=node_name)

        # Temporarily fully-unlock if a previous version locked the
        # node entirely (migration).
        is_locked = cmds.lockNode(node, q=True, lock=True)[0]
        if is_locked:
            cmds.lockNode(node, lock=False)

        if not cmds.attributeQuery(attr_name, node=node, exists=True):
            cmds.addAttr(node, longName=attr_name, dataType="string")

        # Lock the name only — prevents renaming while keeping
        # attributes writable (Maya 2025 compatible).
        cmds.lockNode(node, lock=False, lockName=True)

        return node


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
