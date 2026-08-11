# !/usr/bin/python
# coding=utf-8
import contextlib
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception as error:
    cmds = None
    mel = None
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
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

    #: Maya's shadeable/renderable shape types -- what "geometry" means to
    #: ``cmds.ls -type``, to a shading engine, and to ``cmds.displaySurface``
    #: (which raises "No surfaces selected" on anything else). Note "geometry"
    #: itself is NOT a queryable ``ls`` type; it silently returns [].
    SURFACE_TYPES = ("mesh", "nurbsSurface", "subdiv")

    @classmethod
    def get_type(cls, objects: Union[str, Any, List[Any]]) -> Union[str, List[str]]:
        """Get the object type as a string.

        Returns:
            (str/list) The node type. A list is always returned when 'objects' is given as a list.
        """
        from mayatk import Components

        types = []
        for obj in cmds.ls(CoreUtils.as_strings(objects)) or []:
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
        objs = cmds.ls(CoreUtils.as_strings(objects), transforms=True) or []
        result = []
        for obj in objs:
            shapes = cls.get_shapes(obj, no_intermediate=True)
            is_mesh = bool(shapes) and any(cmds.objectType(s) == "mesh" for s in shapes)
            result.append(is_mesh)
        if filter:
            return [obj for obj, is_mesh in zip(objs, result) if is_mesh]
        return ptk.format_return(result, objects)

    @staticmethod
    def is_locator(objects, filter: bool = False):
        """Determine if each of the given object(s) is a locator."""
        objs = cmds.ls(CoreUtils.as_strings(objects), transforms=True) or []
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
        objs = cmds.ls(CoreUtils.as_strings(objects)) or []
        result = []
        for n in objs:
            try:
                is_transform = cmds.objectType(n) == "transform"
                # NOTE: ``noIntermediate=True`` so that orig (intermediate)
                # shapes don't make a group look like geometry.
                shapes = cmds.listRelatives(n, shapes=True, noIntermediate=True) or []
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
        objs = cmds.ls(CoreUtils.as_strings(objects), transforms=True) or []
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
            cmds.ls(CoreUtils.as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else CoreUtils.as_strings(objects)
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
            cmds.ls(CoreUtils.as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else CoreUtils.as_strings(objects)
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
            cmds.ls(CoreUtils.as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else CoreUtils.as_strings(objects)
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
            cmds.ls(CoreUtils.as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else CoreUtils.as_strings(objects)
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
            cmds.ls(CoreUtils.as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else CoreUtils.as_strings(objects)
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
            cmds.ls(CoreUtils.as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else CoreUtils.as_strings(objects)
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
            cmds.ls(CoreUtils.as_strings(objects), flatten=True) or []
            if not isinstance(objects, list)
            else CoreUtils.as_strings(objects)
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

    @classmethod
    def get_shapes(cls, node, no_intermediate=True, full_path=True):
        """Return the shape(s) associated with *node* -- flexible about input.

        Accepts whatever you have:
          * a **transform** -> its shape children,
          * a **shape** -> itself (so callers never have to pre-resolve),
          * a **component** (e.g. ``pCube1.f[0]``) -> the owning node's shapes.

        Always returns a list (never ``None``).
        """
        node = str(node).split(".")[0]  # tolerate a component/attribute suffix
        shapes = (
            cmds.listRelatives(
                node,
                shapes=True,
                noIntermediate=no_intermediate,
                fullPath=full_path,
                path=not full_path,
            )
            or []
        )
        if shapes:
            return shapes
        # listRelatives finds nothing when *node* is already a shape -- return it.
        own = cmds.ls(node, shapes=True, long=full_path) or []
        if no_intermediate:
            own = [s for s in own if not cls.is_intermediate(s)]
        return own

    @classmethod
    def get_shape(cls, node, no_intermediate=True, full_path=True):
        """Return the first shape for a transform / shape / component, or ``None``."""
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
            cmds.ls(objects, **ls_kwargs)
            if objects is not None
            else cmds.ls(**ls_kwargs)
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
        """Retrieves a unique list of objects' children (if any) in the scene, excluding the groups themselves.

        Components resolve to their owning node -- "the children of a face" is
        meaningless, and callers fed a live component selection would otherwise
        get back names like ``|pCube1.f[0]`` that no node-level command accepts.
        """
        # Strip the component suffix BEFORE ``ls``: nothing is left to flatten
        # once it's gone, and expanding a dense range like ``f[0:5000]`` into
        # 5001 names only to collapse them back to one node is pure waste.
        names = [n.split(".")[0] for n in CoreUtils.as_strings(objects)]
        objects = list(dict.fromkeys(cmds.ls(names, long=True) or []))

        def recurse_children(obj, final):
            if cls.is_group(obj):
                for child in (
                    cmds.listRelatives(
                        obj, children=True, type="transform", fullPath=True
                    )
                    or []
                ):
                    recurse_children(child, final)
            else:
                final[obj] = None

        # dict, not set -- callers key display/transfer decisions off the first
        # element, so the de-duplication must not scramble the order ``ls`` and
        # ``listRelatives`` handed back (hierarchy order, for a group).
        final = {}

        for obj in objects:
            recurse_children(obj, final)

        return list(final)

    @staticmethod
    def get_transform_node(
        nodes, returned_type="obj", attributes=False, inc=[], exc=[]
    ):
        """Get transform node(s) or node attributes.

        Returns:
            (str/list) Transform node(s) or node attributes.
        """
        result = []
        for node in cmds.ls(CoreUtils.as_strings(nodes), long=True, flatten=True) or []:
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
                    connected_transforms = (
                        cmds.listRelatives(
                            history, parent=True, type="transform", fullPath=True
                        )
                        or []
                    )
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
    def get_shape_node(cls, nodes, returned_type="obj", attributes=False, inc=[], exc=[]):
        """Get shape node(s) or node attributes."""
        result = []
        for node in cmds.ls(CoreUtils.as_strings(nodes), long=True, flatten=True) or []:
            shapes = (
                cmds.listRelatives(node, children=True, shapes=True, fullPath=True) or []
            )
            if not shapes:
                shapes = cmds.ls(node, type="shape", long=True) or []
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

        # Order-preserving dedupe. A bare `set` here made the order str-hash order,
        # so it varied PER PROCESS: a caller taking `[0]` off a deformed mesh (two
        # shapes -- the live one and its orig) got a different shape run to run,
        # which is a coin flip that reads as a scene problem, not a code one.
        # DAG order puts the renderable shape first, which is what `[0]` means.
        if attributes:
            return list(dict.fromkeys(result))

        return ptk.format_return(list(dict.fromkeys(result)), nodes)

    @staticmethod
    def get_history_node(nodes, returned_type="obj", attributes=False, inc=[], exc=[]):
        """Get history node(s) or node attributes."""
        result = []
        for node in cmds.ls(CoreUtils.as_strings(nodes), long=True, flatten=True) or []:
            shapes = (
                cmds.listRelatives(node, children=True, shapes=True, fullPath=True) or []
            )
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

    @staticmethod
    def get_classification_tokens(node_type: str) -> List[str]:
        """Role classifications of *node_type* — ``shader/surface``, ``utility/math``, …

        ``drawdb/`` (viewport draw override) and ``swatch/`` (swatch renderer)
        tokens are dropped: they describe how a node is *drawn*, not what it is,
        and their paths embed misleading substrings — Arnold's ``aiBump2d``
        carries ``drawdb/shader/surface/arnold/genericShader`` while its actual
        role is ``utility/shader``. Match against these tokens, never against
        the raw ``cmds.getClassification`` strings.

        A type classified *only* by its draw override (``adskMaterial`` is just
        ``drawdb/shader/surface/adskMaterial``) has nothing else to go on, so
        that path is returned with the ``drawdb/`` prefix stripped — the best
        role hint available rather than "no role at all".

        Returns an empty list for an unknown / unclassified type, so callers
        simply don't match instead of raising.
        """
        try:
            raw = cmds.getClassification(str(node_type)) or []
        except Exception:
            return []
        tokens = [tok for entry in raw for tok in str(entry).split(":") if tok]
        roles = [t for t in tokens if not t.startswith(("drawdb/", "swatch/"))]
        if roles:
            return roles
        return [t.split("/", 1)[1] for t in tokens if t.startswith("drawdb/")]

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
            # Role tokens only — matching the raw classification strings would
            # read a node's draw override as its role (Arnold's aiBump2d is
            # drawn as 'drawdb/shader/surface/...' but IS 'utility/shader',
            # so it would be created asShader, with a shading group).
            tokens = cls.get_classification_tokens(node_type)
            if any(t.startswith("shader/surface") for t in tokens):
                classification = classification or "asShader"
                category = category or "surfaceShader"
            elif any("texture/3d" in t for t in tokens):
                classification = classification or "as3DTexture"
                category = category or ""
            elif any("texture/environment" in t for t in tokens):
                classification = classification or "asEnvTexture"
                category = category or ""
            elif any("texture" in t for t in tokens):
                classification = classification or "as2DTexture"
                category = category or ""
            elif any("light" in t for t in tokens):
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
        cmds.optionVar(
            intValue=("createMaterialsWithShadingGroup", create_shading_group)
        )
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

            connected_nodes = (
                cmds.listConnections(current_node, s=source, d=dest, exactType=exact)
                or []
            )

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
            objects = cmds.ls(CoreUtils.as_strings(objects), long=True) or []
            shapes = cmds.listRelatives(objects, shapes=True, fullPath=True) or []
            instances = cmds.listRelatives(shapes, allParents=True, fullPath=True) or []
            if not return_parent_objects:
                obj_set = set(objects)
                instances = [i for i in instances if i not in obj_set]

        return instances

    @staticmethod
    def _local_bbox_size(node):
        """Extents of *node*'s geometry in the frame its scale channels act in
        — i.e. the SHAPE's object-space box, which (unlike a world bounding
        box, or ``MFnDagNode.boundingBox`` on the transform) carries none of
        the node's own rotation.  Intermediate shapes are skipped, so
        deformer history doesn't change the answer.

        This measures the node's OWN shape; child geometry rides along on the
        resulting scale but doesn't contribute (the uniform path, measuring
        ``exactWorldBoundingBox``, does include the subtree).  The two agree
        whenever source and target are the same hierarchy at different sizes —
        the case instancing is for.

        Returns ``None`` when no single shape resolves (a group, or a
        multi-shape transform): there is no unambiguous local frame to fit
        per-axis in, so callers fall back to uniform matching.
        """
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(str(node))
        dag = sel.getDagPath(0)
        try:
            dag.extendToShape()
        except RuntimeError:  # no shape, or more than one
            return None
        bb = om.MFnDagNode(dag).boundingBox
        return (bb.width, bb.height, bb.depth)

    @classmethod
    def _match_bbox_scale_per_axis(cls, instance, target) -> bool:
        """Scale *instance* per-axis so each of its local extents matches
        *target*'s.  Measured in the local frame (both share it — the instance
        already matched the target's rotation), which is what makes a
        non-uniform fit reproducible: per-axis ratios taken off *world* boxes
        only reconstruct the box, not the proportions, once the object is
        rotated off-axis.

        An axis with no extent on either side (flat geometry vs. solid) has no
        derivable ratio — that axis keeps the target's own scale rather than
        collapsing to zero.  Returns False when the local frame can't be
        resolved (no single shape), so the caller can fall back to uniform.
        """
        src = cls._local_bbox_size(instance)
        tgt = cls._local_bbox_size(target)
        if src is None or tgt is None:
            return False
        ratios = [
            (t / s) if (s > 1e-9 and t > 1e-9) else 1.0 for s, t in zip(src, tgt)
        ]
        if not all(r == 1.0 for r in ratios):
            scale = cmds.getAttr(f"{instance}.scale")[0]
            cmds.setAttr(
                f"{instance}.scale",
                *[v * r for v, r in zip(scale, ratios)],
                type="double3",
            )
        return True

    @classmethod
    @CoreUtils.undoable
    def replace_with_instances(
        cls,
        objects=None,
        append="",
        freeze_transforms=False,
        center_pivot=True,
        delete_history=True,
        retain_bbox_scale=False,
        retain_bbox_per_axis=False,
    ):
        """Replace target objects with instances of the source object.

        Placement: each target is first compared geometrically against the
        source (AutoInstancer's ``GeometryMatcher``).  When the meshes are
        identical, the instance is placed by registration — the shared shape
        lands exactly on the target's world geometry, preserving the target's
        apparent rotation even when it was frozen into the mesh (zeroed
        channels).  Non-identical targets fall back to ``matchTransform``
        (channel copy), where the ``retain_bbox_*`` options apply.

        Parameters:
            objects (list): Source first, then targets (selection order
                when None).
            freeze_transforms (bool): Freeze TRANSLATION only before
                instancing.  Rotation and scale are deliberately left
                unfrozen — freezing them would bake the source's pose into
                the shared shape.
            center_pivot (bool): Center pivots before instancing (and on
                each new instance).
            delete_history (bool): Delete history before instancing.
            retain_bbox_scale (bool): Fallback path only — preserve each
                target's apparent size.  ``matchTransform`` only copies the
                target's scale *channels*, so a target whose size lives in
                its geometry (frozen scale, or a differently-sized mesh)
                would come back at the source's size.  This uniformly
                rescales each instance so its world bounding box matches the
                size of the object it replaced.  (An exact registration
                already reproduces the size, so it skips this.)
            retain_bbox_per_axis (bool): With ``retain_bbox_scale``, match each
                axis independently instead of uniformly (instances carry their
                own scale channels, so a non-uniform result is legal — ignored
                on its own).  Measured in the local frame —
                see :meth:`_match_bbox_scale_per_axis`.  Prefer the uniform
                default when the source and target aren't the same proportions:
                a per-axis fit reaches the target's box by distorting the
                shared shape.

        Returns:
            list: The newly created instance objects.
        """
        from mayatk import XformUtils

        if objects is None:
            objects = cmds.ls(orderedSelection=True, long=True) or []
        else:
            objects = cmds.ls(CoreUtils.as_strings(objects), long=True) or []
        try:
            source, targets = objects[0], objects[1:]
        except IndexError:
            cmds.warning("Operation requires a selection of at least two objects.")
            return

        if freeze_transforms:
            XformUtils.freeze_transforms(
                objects,
                translate=True,
                center_pivot=center_pivot,
                delete_history=delete_history,
                force=True,
            )
        else:
            # freeze_transforms(translate=False, ...) is an explicit
            # freeze-nothing request and returns before its centering /
            # history side-steps — perform them directly.
            if delete_history:
                cmds.delete(objects, constructionHistory=True)
            if center_pivot:
                cmds.xform(objects, centerPivots=True)

        # Geometric registration (same machinery as AutoInstancer): places the
        # shared shape exactly where the target's geometry sits, so a target
        # whose orientation/scale was frozen into its mesh keeps its apparent
        # rotation — matchTransform can only reproduce the transform CHANNELS,
        # which are zero after a freeze.
        try:
            from mayatk.core_utils.auto_instancer.geometry_matcher import (
                GeometryMatcher,
            )

            matcher = GeometryMatcher()
        except Exception:
            matcher = None

        new_instances = []
        for target in targets:
            name = CoreUtils.short_name(target)
            objParent = cmds.listRelatives(target, parent=True, fullPath=True) or []
            instance = cmds.instance(source)[0]
            registered = False
            if matcher is not None:
                try:
                    registered, rel_mtx = matcher.are_meshes_identical(source, target)
                except Exception:
                    registered, rel_mtx = False, None
            if registered:
                # Object-space registration: source_local_pts * rel_mtx ≈
                # target_local_pts, so rel_mtx * target_world lands the shared
                # shape on the target's world geometry.
                target_world = XformUtils.get_object_matrix(target, world=True)
                XformUtils.set_object_matrix(
                    instance,
                    target_world if rel_mtx is None else rel_mtx * target_world,
                    world=True,
                )
                if center_pivot:
                    cmds.xform(instance, centerPivots=True)
                else:
                    rp = cmds.xform(target, q=True, ws=True, rotatePivot=True)
                    sp = cmds.xform(target, q=True, ws=True, scalePivot=True)
                    cmds.xform(instance, ws=True, rotatePivot=rp)
                    cmds.xform(instance, ws=True, scalePivot=sp)
            else:
                # Non-identical geometry (or no matcher): fall back to copying
                # the target's transform channels.
                cmds.matchTransform(
                    instance,
                    target,
                    position=True,
                    rotation=True,
                    scale=True,
                    pivots=True,
                )
            # An exact registration already reproduces the target's size —
            # only the channel-copy fallback needs the bbox fit.
            if retain_bbox_scale and not registered:
                # Run before the target is deleted (and before re-parenting, so
                # the relative world-space scale isn't filtered through a
                # parent matrix).
                if not (
                    retain_bbox_per_axis
                    and cls._match_bbox_scale_per_axis(instance, target)
                ):
                    # Averaged world-box ratio: safe under any orientation, and
                    # it never distorts the shared shape.  Also the fallback
                    # when a per-axis fit has no resolvable local frame.
                    XformUtils.match_scale(instance, target, average=True)
            if objParent:
                try:
                    parented = cmds.parent(instance, objParent[0]) or []
                except RuntimeError:
                    parented = []
                if parented:
                    instance = parented[0]
            # Delete the target BEFORE the rename — renaming against a
            # still-living sibling of the same name auto-suffixes, so the
            # replacement never took over the target's exact name.
            cmds.delete(target)
            instance = cmds.rename(instance, name + append)
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
    def get_instanced_shapes(cls, node, intermediate: bool = True) -> List[str]:
        """Every shape under *node* that is shared with another transform.

        The counterpart of :meth:`get_instances` (which answers "which
        OTHER transforms share this object's shapes"): this returns the
        shared SHAPE nodes themselves — the set any fork / uninstance /
        freeze decision actually has to act on.

        **Intermediate (orig) shapes count**, and that is load-bearing:
        ``cmds.makeIdentity`` refuses to freeze while *any* child shape is
        multiply-instanced, intermediate or not (verified — a shared
        deformer orig shape blocks the freeze exactly like a shared
        visible shape, with "Cannot freeze below transform X due to
        multiply-instanced child XShapeOrig"). A caller that forks only
        the visible shapes leaves an object that still cannot be frozen,
        which is precisely how ``uninstance(freeze=True)`` used to fail
        silently on any object carrying history.
        """
        shapes = (
            cmds.listRelatives(
                str(node),
                shapes=True,
                fullPath=True,
                noIntermediate=not intermediate,
            )
            or []
        )
        return [
            s
            for s in shapes
            if len(cmds.listRelatives(s, allParents=True, fullPath=True) or []) > 1
        ]

    @staticmethod
    def _fork_instanced_shape(transform_long: str, shape_long: str) -> Optional[str]:
        """Fork one instanced shape on *transform_long* into a unique copy.

        Grafts a duplicate of *shape_long* under the transform first — the
        transform is never momentarily shapeless — then surgically removes
        only the (transform, shape) instance edge via
        ``MFnDagNode.removeChild``.  ``cmds.parent -rm -s`` does NOT work:
        it tries to unparent the shape to world, which Maya silently
        rejects, leaving the instance link intact.  Sibling instance
        transforms keep the original shape.

        Returns:
            The new shape's long path, or None on failure (warned).
        """
        import maya.api.OpenMaya as om

        shape_short = shape_long.split("|")[-1]
        dup_xform = None
        try:
            # ``cmds.duplicate`` always forks geometry, so the new shape is
            # unique even when the source was instanced.  Duplicating the
            # shape (not the transform) avoids walking children.
            dup_xform = cmds.duplicate(
                shape_long,
                returnRootsOnly=True,
                name=f"{shape_short}__uninst_tmp",
            )[0]
            # noIntermediate would hide the duplicate of an INTERMEDIATE
            # source shape (Maya copies the flag), leaving this looking like
            # "duplicate produced no shape node".
            dup_shapes = (
                cmds.listRelatives(
                    dup_xform, shapes=True, fullPath=True, noIntermediate=False
                )
                or []
            )
            if not dup_shapes:
                raise RuntimeError("duplicate produced no shape node")
            # ``relative`` keeps the local transform — the shape is
            # positioned by the transform's matrix, which is unchanged.
            new_shape = cmds.parent(
                dup_shapes[0], transform_long, shape=True, relative=True
            )[0]
            # Resolve the long path under THIS transform — ``cmds.ls`` on the
            # returned partial path could match a same-named node elsewhere.
            new_leaf = str(new_shape).split("|")[-1]
            resolved = [
                s
                for s in (
                    cmds.listRelatives(
                        transform_long, shapes=True, fullPath=True
                    )
                    or []
                )
                if s.split("|")[-1] == new_leaf
            ]
            new_shape = (
                resolved[0]
                if resolved
                else (cmds.ls(new_shape, long=True) or [new_shape])[0]
            )
            # Keep the fork on the same side of the intermediate divide as
            # its source, so a forked orig shape stays hidden rather than
            # rendering on top of the visible one.
            if NodeUtils.is_intermediate(shape_long) and not NodeUtils.is_intermediate(
                new_shape
            ):
                cmds.setAttr(f"{new_shape}.intermediateObject", True)

            # Name it after its transform, Maya-style. The scratch name is an
            # implementation detail: left in place it accumulates across runs
            # ("...__uninst_tmpShape__uninst_tmpShape"), which is how a
            # production file ended up with 116 of them.
            leaf = transform_long.split("|")[-1]
            want = f"{leaf}Shape" + ("Orig" if NodeUtils.is_intermediate(new_shape) else "")
            try:
                renamed = cmds.rename(new_shape, want)
                new_shape = (
                    cmds.listRelatives(
                        transform_long, shapes=True, fullPath=True
                    )
                    or []
                )
                new_shape = next(
                    (s for s in new_shape if s.split("|")[-1] == renamed.split("|")[-1]),
                    (cmds.ls(renamed, long=True) or [renamed])[0],
                )
            except RuntimeError:
                pass  # name taken by something else — cosmetic only

            sel = om.MSelectionList()
            sel.add(transform_long)
            sel.add(shape_long)
            om.MFnDagNode(sel.getDependNode(0)).removeChild(sel.getDependNode(1))

            cmds.delete(dup_xform)
            dup_xform = None
            return new_shape
        except (RuntimeError, ValueError) as e:
            cmds.warning(
                f"shape fork failed for {transform_long} (shape {shape_long}): {e}"
            )
            return None
        finally:
            if dup_xform and cmds.objExists(dup_xform):
                try:
                    cmds.delete(dup_xform)
                except RuntimeError:
                    pass

    @classmethod
    def uninstance(cls, objects, freeze=False, delete_history=False, quiet=True):
        """Un-Instance the given objects.

        ``freeze`` (optional additional step): after breaking the link, bake the
        object's SCALE into the now-unique geometry. Breaking the link alone
        leaves the transform untouched — a mirrored instance still carries its
        negative scale, which is the part exporters and game engines object to.
        Baking is only possible once the shape is unique (freezing a shared
        shape would rewrite every sibling), which is why the two steps belong
        together: ``uninstance(objs, freeze=True)`` is the engine-safe finish.

        For each transform, forks every instanced shape it carries into
        a unique copy and swaps it in — without ever deleting the
        transform itself.  Name, world matrix, parent, children and any
        non-instanced shapes are preserved.  Sibling instance transforms
        retain the original shape.

        ``delete_history`` (opt-in): bake away construction history first
        when the object shares an INTERMEDIATE (orig) shape.  Such a shape
        can't be forked — the deformer reads it per instance, so dropping
        this transform's edge empties the remaining instances — and while
        it stays shared the object cannot be frozen.  Baking preserves
        every member's appearance.  Without it, a shared orig shape is
        left alone and reported.
        """
        if objects == "all":
            objects = cls.get_instances()

        results = []
        for obj in cmds.ls(CoreUtils.as_strings(objects)) or []:
            obj_long = (cmds.ls(obj, long=True) or [obj])[0]

            for shape in cls._forkable_instanced_shapes(
                obj_long, delete_history=delete_history, quiet=quiet
            ):
                cls._fork_instanced_shape(obj_long, shape)

            results.append(obj_long)

        if freeze and results:
            # Local import: xform_utils imports NodeUtils, so a module-level
            # import here would be circular (same pattern as
            # replace_with_instances).
            from mayatk import XformUtils

            # store=False: uninstance already rewrote the DAG, so a bake
            # history pointing back at the instanced state is meaningless.
            XformUtils.freeze_transforms(results, scale=True, force=True, store=False)

        return results

    @classmethod
    def _forkable_instanced_shapes(
        cls, obj: str, delete_history: bool = False, quiet: bool = True
    ) -> List[str]:
        """Which of *obj*'s shared shapes a fork may legally take.

        A shared INTERMEDIATE (orig) shape is live construction history
        feeding a deformer, and it keeps the object un-freezable even once
        the visible shape is unique.  It cannot simply be forked: the
        deformer reads the orig shape's world-space output per instance, so
        dropping this transform's instance edge invalidates that index and
        the REMAINING instances evaluate to an empty mesh (verified).  Baking
        the history away is the safe route and preserves every member's
        appearance — but it is destructive, so it is opt-in via
        ``delete_history``; otherwise the orig shape is left shared and only
        the visible shapes are offered.

        Shared by :meth:`uninstance` and :meth:`preserve_instancing` — the
        rule is a property of forking, not of either caller.
        """
        inst_shapes = cls.get_instanced_shapes(obj)
        if not any(cls.is_intermediate(s) for s in inst_shapes):
            return inst_shapes

        if delete_history:
            cmds.delete(obj, constructionHistory=True)
            return cls.get_instanced_shapes(obj)

        # Not a warning: the visible geometry IS detached, which is what
        # callers want, and XformUtils.freeze_instanced_group bakes without
        # makeIdentity so a shared orig shape no longer blocks a freeze. Only
        # a fully independent datablock needs delete_history.
        if not quiet:
            # Caller-neutral wording: this runs under uninstance AND under
            # preserve_instancing, and naming one of them misreports the other.
            print(
                f"instance fork: '{CoreUtils.short_name(obj)}' keeps a shared "
                "intermediate (history) shape — forking it would empty the other "
                "instances. Pass delete_history=True for a fully independent copy."
            )
        return [s for s in inst_shapes if not cls.is_intermediate(s)]

    @staticmethod
    def _geometry_iterator(shape):
        """``MItGeometry`` over *shape*, or None when it carries no points.

        The None case is the whole point of having this separate from
        :meth:`_geometry_points`: it answers "can a point-writing op even
        reach this shape?" (a locator / camera shape: no) without paying to
        read a dense mesh's positions just to find out.

        A shape the iterator cannot bind to raises ``ValueError``
        ("MPyMItGeometry : no matching constructor found"), NOT the
        ``RuntimeError`` an unresolvable name raises — both are just "no
        points here", and missing the first let a locator abort the restore
        from inside its ``finally``.
        """
        import maya.api.OpenMaya as om

        try:
            sel = om.MSelectionList()
            sel.add(str(shape))
            return om.MItGeometry(sel.getDagPath(0))
        except (RuntimeError, ValueError):
            return None

    @classmethod
    def _geometry_points(cls, shape) -> Optional[List[float]]:
        """Flat OBJECT-space point list for *shape*, or None when it has none.

        Object space on purpose: two instances of one shape sit at different
        world positions by definition, so a world-space compare could never
        report a match.  ``MItGeometry`` covers mesh / NURBS / lattice alike,
        so the instancing scope is not mesh-only.
        """
        import maya.api.OpenMaya as om

        it = cls._geometry_iterator(shape)
        if it is None:
            return None
        return [c for p in it.allPositions(om.MSpace.kObject) for c in (p.x, p.y, p.z)]

    @classmethod
    def _geometry_matches(
        cls, a, b, tol: float = 1e-6, cache: Optional[Dict] = None
    ) -> bool:
        """True when *a* and *b* hold the same points, index for index.

        Deliberately stricter than ``GeometryMatcher.are_meshes_identical``
        (which registers one mesh onto another through a relative matrix):
        re-linking an instance edge does not move a transform, so the shapes
        have to already agree in the frame they will be shared in.  Anything
        looser would silently teleport geometry on restore.

        ``cache`` maps a stable KEY to that shape's points; pass
        ``(key, shape)`` pairs instead of bare paths to use it.  The restore
        compares each candidate against every cluster head, which is the one
        place re-reading a dense mesh per pair would hurt — and a path is not
        a safe cache key there, since the re-links between comparisons are
        themselves DAG edits.
        """
        if cache is None:
            pa, pb = cls._geometry_points(a), cls._geometry_points(b)
        else:
            pts = []
            for key, shape in (a, b):
                if key not in cache:
                    cache[key] = cls._geometry_points(shape)
                pts.append(cache[key])
            pa, pb = pts
        if pa is None or pb is None or len(pa) != len(pb):
            return False
        return all(abs(x - y) <= tol for x, y in zip(pa, pb))

    @staticmethod
    def _shading_engines(shape) -> List[str]:
        """The shading engines *shape* is assigned to, read by PATH so an
        instance-specific assignment is the one reported."""
        return [
            s
            for s in (cmds.listSets(object=str(shape), type=1) or [])
            if cmds.nodeType(s) == "shadingEngine"
        ]

    @classmethod
    def _relink_instanced_shape(cls, transform: str, shape: str, fork: str) -> bool:
        """Put *transform* back onto the shared *shape*, dropping its *fork*.

        The inverse of :meth:`_fork_instanced_shape`, and the reason the
        instancing scope can be a no-op round trip: the transform is never
        momentarily shapeless (the shared shape is instanced in BEFORE the
        fork is deleted), and no transform channel is touched — the object
        stays exactly where the operation left it.

        Uses ``cmds.parent -add -shape`` rather than
        ``MFnDagNode.addChild``, so the re-link lands on the undo queue with
        the rest of the operation.
        """
        sgs = cls._shading_engines(fork)
        try:
            cmds.parent(shape, transform, shape=True, add=True)
        except RuntimeError as e:
            cmds.warning(f"instance re-link failed for {transform}: {e}")
            return False

        # One node cannot carry a different name per DAG path, so the new
        # instance path is exactly *transform* + the shape's leaf.  (A leaf
        # collision under *transform* would have made the ``parent`` above
        # fail rather than rename anything — the fork's own rename already
        # side-stepped the canonical name when it was created.)
        relinked = f"{transform}|{shape.split('|')[-1]}"
        try:
            cmds.delete(fork)
        except RuntimeError as e:
            cmds.warning(f"instance re-link left a stray shape on {transform}: {e}")

        # Per-instance shading: the fork may have carried an assignment the
        # shared shape does not have (adding a DAG instance edge renumbers
        # ``instObjGroups``, so this is re-applied by path, not by index).
        if sgs and set(sgs) != set(cls._shading_engines(shape)):
            for sg in sgs:
                try:
                    cmds.sets(relinked, edit=True, forceElement=sg)
                except RuntimeError:
                    pass
        return True

    @classmethod
    @contextlib.contextmanager
    def preserve_instancing(
        cls, objects, delete_history: bool = False, quiet: bool = True
    ):
        """Run a shape-editing operation on instanced objects without dragging their siblings along.

        Any op that writes to a shape's POINTS — ``move -preserveGeometryPosition``
        (which is what Maya's Bake Pivot is built on), a freeze, a
        vertex-position restore — rewrites the *shared* datablock, so every
        instance of that shape visibly jumps by the same delta while the
        operated object appears to stay put.  This scope makes the edit local:

        1. **Enter** — fork every shared shape the targets carry
           (:meth:`_fork_instanced_shape`), so each target owns its points.
        2. **Body** — the operation runs against unique geometry and can only
           affect what was selected.
        3. **Exit** — re-instance in place: a fork whose points still match
           the shape it came from is re-linked to it, and forks that changed
           together (the usual case — every member of a group was operated
           on identically) are re-instanced onto each other.  Only forks with
           no match are left unique, which is exactly the case where sharing
           one datablock could no longer represent both objects.

        Nothing here moves a transform, and a restored member is only ever
        re-linked to geometry it already matches, so world placement is
        preserved end to end.  Objects that were not instanced to begin with
        cost one shape query, produce no record, and are otherwise untouched.

        Boundary: this guards shared SHAPE data.  A transform on several DAG
        paths (a member of an instanced GROUP) cannot carry per-path channels
        at all, and is left to the caller — see
        ``XformUtils.freeze_instanced_group``.

        Undo: the re-link is a ``cmds`` call and rides the undo queue, but the
        fork underneath it is not (``_fork_instanced_shape`` has to reach for
        ``MFnDagNode.removeChild``; ``parent -rm -s`` cannot break a shape
        instance).  So undoing an operation that ended with a member left
        legitimately unique restores its transform and points but not its
        instance edge — the same pre-existing limitation as ``uninstance``.

        Parameters:
            objects: The transforms the operation will act on.
            delete_history: Forwarded to the fork step for objects sharing an
                INTERMEDIATE (orig) shape — see :meth:`uninstance`.
            quiet: Suppress the report of shapes that could not be forked.

        Yields:
            list: The resolved long paths of *objects*.
        """
        targets = cmds.ls(CoreUtils.as_strings(objects), long=True) or []
        records = cls._fork_instanced_shapes(
            targets, delete_history=delete_history, quiet=quiet
        )
        try:
            yield targets
        finally:
            cls._restore_instanced_shapes(records)

    @classmethod
    def _fork_instanced_shapes(
        cls, targets: List[str], delete_history: bool = False, quiet: bool = True
    ) -> List[Dict[str, str]]:
        """Fork every shared shape on *targets*; return the restore manifest.

        Records are keyed by UUID rather than path: the operation running
        inside the scope is free to rename or re-parent its objects, and a
        stale path would silently skip the re-link.
        """
        records: List[Dict[str, str]] = []
        for obj in dict.fromkeys(targets):  # a repeated target forks nothing twice
            # Only point-carrying shapes are worth forking: they are the ones
            # a shape edit can write through, and the only ones the restore
            # can prove identical again.  Forking a locator/camera shape would
            # be a scene change the scope could never take back.
            inst_shapes = [
                s
                for s in cls._forkable_instanced_shapes(
                    obj, delete_history=delete_history, quiet=quiet
                )
                if cls._geometry_iterator(s) is not None
            ]

            transform_uuid = (cmds.ls(obj, uuid=True) or [""])[0]
            for shape in inst_shapes:
                source_uuid = (cmds.ls(shape, uuid=True) or [None])[0]
                fork = cls._fork_instanced_shape(obj, shape)
                if not fork or not source_uuid:
                    continue
                records.append(
                    {
                        "transform": transform_uuid,
                        "source": source_uuid,
                        "fork": (cmds.ls(fork, uuid=True) or [""])[0],
                    }
                )
        return records

    @staticmethod
    def _resolve_uuid(uuid: str) -> Optional[str]:
        found = cmds.ls(uuid, long=True) if uuid else []
        return found[0] if found else None

    @classmethod
    def _restore_instanced_shapes(cls, records: List[Dict[str, str]]) -> int:
        """Re-instance what the scope forked, wherever the geometry still agrees.

        Two passes, because both outcomes are legitimate:

        - against the ORIGINAL shape, which catches the partial selection
          (some members of a group were operated on, some were not, and the
          untouched ones must not be disturbed);
        - among the forks themselves, grouped by the shape they came from,
          which catches the ordinary case where every member was operated on
          identically and the whole group can be rebuilt.

        Returns the number of transforms re-instanced.
        """
        restored = 0
        # (transform path, source uuid, fork uuid, fork path)
        leftovers: List[Tuple[str, str, str, str]] = []
        # Keyed by UUID, not path: the re-links below are DAG edits, so a path
        # captured before one is not guaranteed to name the same node after.
        points: Dict[str, Optional[List[float]]] = {}

        for rec in records:
            transform = cls._resolve_uuid(rec["transform"])
            source = cls._resolve_uuid(rec["source"])
            fork = cls._resolve_uuid(rec["fork"])
            if not (transform and fork):  # deleted by the operation
                continue
            if source and cls._geometry_matches(
                (rec["fork"], fork), (rec["source"], source), cache=points
            ):
                restored += int(cls._relink_instanced_shape(transform, source, fork))
            else:
                leftovers.append((transform, rec["source"], rec["fork"], fork))

        # Cluster the changed forks by their originating shape: two objects
        # that were never instances of each other must not become instances
        # here just because they happen to match.
        heads: Dict[str, List[Tuple[str, str]]] = {}
        for transform, source_uuid, fork_uuid, fork in leftovers:
            head = next(
                (
                    h
                    for h in heads.setdefault(source_uuid, [])
                    if cls._geometry_matches((fork_uuid, fork), h, cache=points)
                ),
                None,
            )
            if head is None:
                heads[source_uuid].append((fork_uuid, fork))
            else:
                restored += int(cls._relink_instanced_shape(transform, head[1], fork))
        return restored

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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
