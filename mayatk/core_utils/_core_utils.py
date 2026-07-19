# !/usr/bin/python
# coding=utf-8
from typing import List, Callable, Any, Tuple, Optional
from functools import wraps
import contextlib
import inspect

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception as error:
    cmds = None
    mel = None
    print(__file__, error)
import pythontk as ptk

# Import package modules at class level to avoid circular imports.


def as_strings(nodes) -> List[str]:
    """Coerce a node-or-iterable-of-nodes to a list of plain DAG-path strings.

    Single string / non-container input is wrapped in a one-element list.
    Drops empty entries; preserves order.

    Note: only ``list`` / ``tuple`` / ``set`` are treated as containers — never
    duck-typed via ``__iter__``. ``cmds.*`` always returns a ``list`` or
    ``None``, so this is sufficient and avoids accidentally iterating a single
    string into characters or a node-like object into something nonsensical.
    """
    if nodes is None:
        return []
    if isinstance(nodes, (list, tuple, set)):
        return [str(n) for n in nodes if n is not None and str(n)]
    return [str(nodes)] if str(nodes) else []


def short_name(node) -> str:
    """Leaf name with namespace stripped: ``"|grp|ns:obj"`` -> ``"obj"``."""
    return str(node).split("|")[-1].split(":")[-1]


def leaf_name(node) -> str:
    """Leaf name with namespace preserved: ``"|grp|ns:obj"`` -> ``"ns:obj"``."""
    return str(node).split("|")[-1]


class BoundingBox:
    """Plain-data bounding box with ``MVector`` extents.

    ``min``, ``max``, ``center`` are ``om.MVector`` instances. ``size`` is
    ``max - min``; ``diagonal`` is ``size.length()``.
    """

    __slots__ = ("min", "max", "center", "size", "diagonal")

    def __init__(self, mn, mx):
        import maya.api.OpenMaya as om

        self.min = om.MVector(mn[0], mn[1], mn[2])
        self.max = om.MVector(mx[0], mx[1], mx[2])
        self.center = om.MVector(
            (mn[0] + mx[0]) * 0.5,
            (mn[1] + mx[1]) * 0.5,
            (mn[2] + mx[2]) * 0.5,
        )
        self.size = self.max - self.min
        self.diagonal = self.size.length()


def get_bounding_box(node, world: bool = True) -> BoundingBox:
    """Return a :class:`BoundingBox` for *node*.

    Uses ``cmds.exactWorldBoundingBox`` for world space. For object space,
    falls back to ``cmds.polyEvaluate(boundingBox=True)`` when available
    (mesh nodes), else to the world bbox.
    """
    node = str(node)
    if world:
        bb = cmds.exactWorldBoundingBox(node)
        return BoundingBox(bb[:3], bb[3:])
    bb = cmds.polyEvaluate(node, boundingBox=True)
    if bb and len(bb) == 3:
        (xmn, xmx), (ymn, ymx), (zmn, zmx) = bb
        return BoundingBox((xmn, ymn, zmn), (xmx, ymx, zmx))
    bb = cmds.exactWorldBoundingBox(node)
    return BoundingBox(bb[:3], bb[3:])


class _CoreUtilsInternal(object):
    """Internal utilities for Maya CoreUtils."""

    @staticmethod
    def _prepare_reparent(
        nodes: List[object],
    ) -> Tuple[Optional[str], List[str]]:
        """Prepare reparenting by using temporary nulls if needed.

        Creates a temporary null under EVERY distinct parent that would
        become childless after the operation consumes the given nodes (e.g.
        polyUnite deletes all children of a group, causing Maya to
        auto-delete the group) — not just the first one encountered: the
        input may span several original parents (e.g. a material-grouped
        combine sweeping leftovers from multiple source groups), and each
        one that goes fully empty is independently at risk.
        """
        node_strs = [str(n) for n in nodes]

        # Fast path: if no node has a parent (all world-rooted) then no parent
        # transform can be orphaned by the operation, so no temp null is needed.
        # A single batched query avoids one ``listRelatives`` per node, which in
        # interactive Maya is the dominant cost on large selections.
        if not (cmds.listRelatives(node_strs, parent=True, fullPath=True) or []):
            return None, []

        first_parent_list = (
            cmds.listRelatives(node_strs[0], parent=True, fullPath=True) or None
        )
        parent = first_parent_list[0] if first_parent_list else None
        temp_nulls: List[str] = []

        # Resolve to full paths so membership checks against ``children``
        # (also queried full-path) are format-agnostic regardless of whether
        # the caller passed short names or full paths.
        node_set = set(cmds.ls(node_strs, long=True) or node_strs)

        seen_parents = set()
        for node in node_strs:
            node_parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if not node_parent:
                continue
            parent_key = node_parent[0]
            if parent_key in seen_parents:
                continue
            seen_parents.add(parent_key)

            children = (
                cmds.listRelatives(parent_key, children=True, fullPath=True) or []
            )
            remaining = [c for c in children if c not in node_set]
            if not remaining:
                temp_null = cmds.createNode("transform", n="tempTempNull")
                # Multiple protected parents each get a same-named temp null
                # (Maya only disambiguates siblings, not cross-parent
                # namesakes) — track by UUID so cleanup can never delete the
                # wrong one or hit an ambiguous-name error.
                temp_null_uuid = (cmds.ls(temp_null, uuid=True) or [None])[0]
                cmds.parent(temp_null, parent_key)
                if temp_null_uuid:
                    temp_nulls.append(temp_null_uuid)

        return parent, temp_nulls

    @staticmethod
    def _finalize_reparent(
        new_node,
        parent: Optional[str],
        temp_nulls: List[str],
    ) -> None:
        """Clean up reparenting, handling the parent and temporary nulls."""
        if parent and new_node:
            target = parent[0] if isinstance(parent, (list, tuple)) else parent
            try:
                nodes = (
                    new_node if isinstance(new_node, (list, tuple)) else [new_node]
                )
                for n in nodes:
                    cmds.parent(str(n), target)
            except Exception as e:
                cmds.warning(f"Failed to re-parent combined mesh: {e}")
        for temp_null_uuid in temp_nulls:
            try:
                for resolved in cmds.ls(temp_null_uuid, long=True) or []:
                    cmds.delete(resolved)
            except Exception as e:
                cmds.warning(f"Failed to delete temporary null: {e}")

    @staticmethod
    def _calculate_mesh_similarity(mesh1, mesh2) -> float:
        """Calculate similarity between two meshes from their bounding-box volume + vertex counts."""
        m1 = str(mesh1)
        m2 = str(mesh2)

        # Bounding box volume (world space)
        def _bb_volume(node: str) -> float:
            bb = cmds.exactWorldBoundingBox(node)
            return (bb[3] - bb[0]) * (bb[4] - bb[1]) * (bb[5] - bb[2])

        volume1 = _bb_volume(m1)
        volume2 = _bb_volume(m2)

        vertex_count1 = cmds.polyEvaluate(m1, vertex=True) or 0
        vertex_count2 = cmds.polyEvaluate(m2, vertex=True) or 0

        denom_v = max(volume1, volume2) or 1.0
        denom_n = max(vertex_count1, vertex_count2) or 1
        volume_similarity = 1 - abs(volume1 - volume2) / denom_v
        vertex_similarity = 1 - abs(vertex_count1 - vertex_count2) / denom_n

        return (volume_similarity + vertex_similarity) / 2

    @staticmethod
    @contextlib.contextmanager
    def _temp_reparent(nodes):
        """Context manager to maintain hierarchy for nodes during operations that might reparent them."""
        parent, temp_nulls = _CoreUtilsInternal._prepare_reparent(nodes)

        class Result:
            def __init__(self):
                self.result = None

        container = Result()

        try:
            yield container
        finally:
            _CoreUtilsInternal._finalize_reparent(container.result, parent, temp_nulls)


class CoreUtils(ptk.CoreUtils, _CoreUtilsInternal):
    """ """

    @staticmethod
    @contextlib.contextmanager
    def undo_chunk(name: str = ""):
        """Group operations into a single Maya undo chunk.

        Drop-in replacement for ``pm.UndoChunk()`` using ``cmds.undoInfo``.
        """
        cmds.undoInfo(openChunk=True, chunkName=name) if name else cmds.undoInfo(
            openChunk=True
        )
        try:
            yield
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    @contextlib.contextmanager
    def suspended_refresh():
        """Suspend viewport refresh for the duration of a bulk operation.

        In interactive Maya, per-command idle redraws dominate the wall-clock
        time of operations that issue many ``cmds`` calls (e.g. combining a
        large selection). Suspending refresh collapses that cost; the viewport
        is always re-enabled on exit, even if the body raises.

        No-op outside an interactive session (``cmds.refresh`` is a noop in
        batch/standalone), so it is safe to wrap any code path with it.
        """
        cmds.refresh(suspend=True)
        try:
            yield
        finally:
            cmds.refresh(suspend=False)

    @staticmethod
    def selected(func: Callable) -> Callable:
        """A decorator to pass the current selection to the target parameter if None is given.

        The selection is injected into the first *non-receiver* parameter: index 0
        for static methods, index 1 for instance / class methods. The receiver is
        detected by introspecting the wrapped callable's signature (``functools.wraps``
        exposes ``__wrapped__``, so the real parameter names are visible even through
        an inner ``undoable`` / ``reparent`` wrapper) rather than guessing from the
        runtime type of the first argument, which misidentifies a data-bearing first
        positional argument as ``self``.
        """

        # Receiver detection is fixed at decoration time (the wrapped callable's
        # signature never changes), so resolve the target index once here rather
        # than re-introspecting on every call.
        try:
            params = list(inspect.signature(func).parameters.values())
            has_receiver = bool(params) and params[0].name in ("self", "cls")
        except (TypeError, ValueError):
            has_receiver = False
        target = 1 if has_receiver else 0

        @wraps(func)
        def wrapped(*args, **kwargs) -> Any:
            if len(args) <= target or args[target] is None:
                selection = cmds.ls(selection=True) or []
                if not selection:
                    return []
                args = args[:target] + (selection,) + args[target + 1 :]

            return func(*args, **kwargs)

        return wrapped

    @staticmethod
    def undoable(fn):
        """A decorator to place a function into Maya's undo chunk."""

        @wraps(fn)
        def wrapper(*args, **kwargs):
            with CoreUtils.undo_chunk():
                if args and hasattr(args[0], "__class__"):
                    self = args[0]
                    return fn(self, *args[1:], **kwargs)
                else:
                    return fn(*args, **kwargs)

        return wrapper

    @staticmethod
    def reparent(func: Callable) -> Callable:
        """A decorator to manage reparenting of Maya nodes before and after an operation."""

        @wraps(func)
        def wrapped(*args, **kwargs) -> Any:
            instance, node_args = ptk.parse_method_args(args)

            if not args or not args[0]:
                if instance:
                    return func(instance, *node_args, **kwargs)
                else:
                    return func(*node_args, **kwargs)

            mesh_nodes = []
            for arg in args[0]:
                arg_str = str(arg)
                if not cmds.objExists(arg_str):
                    raise ValueError(f"No valid Maya node found for the name: {arg}")
                mesh_nodes.append(arg_str)

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
        """Embed a Maya Native UI Object."""
        from qtpy import QtWidgets
        from shiboken6 import wrapInstance
        from maya.OpenMayaUI import MQtUtil

        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layoutName = ptk.set_case(container.objectName() + "Layout", "camel")
        layout.setObjectName(layoutName)
        cmds.setParent(layoutName)

        derivedClass = ptk.get_derived_type(container)

        ptr = MQtUtil.findControl(control_name)
        control = wrapInstance(int(ptr), derivedClass)
        layout.addWidget(control)

        return control

    @staticmethod
    def confirm_existence(objects: List[str]) -> Tuple[List[str], List[str]]:
        """Confirms the existence of each object in the provided list in Maya."""
        existing = []
        non_existing = []

        for obj in objects:
            if cmds.objExists(str(obj)):
                existing.append(obj)
            else:
                non_existing.append(obj)

        return existing, non_existing

    @staticmethod
    def get_mfn_mesh(objects, api_version: int = 2):
        """Get MFnMesh function set(s) from transform or shape node(s).

        Returns:
            api_version=1: Generator yielding MFnMesh objects (API 1.0)
            api_version=2: Single MFnMesh (API 2.0) if a single object passed,
                           else a list of MFnMesh.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        if api_version == 1:
            import maya.OpenMaya as om

            shapes = NodeUtils.get_shape_node(cmds.ls(as_strings(objects)) or [])
            if not isinstance(shapes, list):
                shapes = [shapes] if shapes else []

            selectionList = om.MSelectionList()
            for mesh in shapes:
                selectionList.add(str(mesh))

            def _generator():
                for i in range(selectionList.length()):
                    dagPath = om.MDagPath()
                    selectionList.getDagPath(i, dagPath)
                    yield om.MFnMesh(dagPath)

            return _generator()

        elif api_version == 2:
            import maya.api.OpenMaya as om2

            def _get_single(node):
                node = str(node)
                if cmds.objectType(node) == "transform":
                    shapes = (
                        cmds.listRelatives(node, shapes=True, noIntermediate=True) or []
                    )
                    if shapes:
                        node = shapes[0]
                dag = om2.MGlobal.getSelectionListByName(node).getDagPath(0)
                return om2.MFnMesh(dag)

            if isinstance(objects, (list, tuple)):
                return [_get_single(obj) for obj in objects]
            else:
                return _get_single(objects)

        else:
            raise ValueError(f"api_version must be 1 or 2, got {api_version}")

    @staticmethod
    def get_array_type(array):
        """Determine the given element(s) type.

        For a bare string or int, returns "str" / "int" (legacy behavior).
        For an iterable (list / tuple / set / generator), inspects the first
        element via NodeUtils.get_type — so a list of component strings such
        as ``["cube.vtx[0]"]`` returns ``"vtx"`` rather than ``"str"``.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        if isinstance(array, str):
            return "str"
        if isinstance(array, int):
            return "int"

        try:
            o = ptk.make_iterable(array)[0]
        except IndexError:
            return ""

        return NodeUtils.get_type(o)

    @staticmethod
    def convert_array_type(lst, returned_type="str", flatten=False):
        """Convert the given element(s) to <obj>, 'str', or int values.

        Components are returned with their owning **shape** as the prefix
        (``cmds.ls`` keeps the transform name; this helper substitutes in the shape).
        """
        lst = cmds.ls(as_strings(lst), flatten=flatten) or []
        if not lst:
            return []
        if isinstance(lst[0], int):
            return []

        # Normalize transform-prefixed components to shape-prefixed.
        def _to_shape_prefixed(comp: str) -> str:
            if "." not in comp:
                return comp
            node, rest = comp.split(".", 1)
            if not cmds.objExists(node):
                return comp
            try:
                if cmds.objectType(node) == "transform":
                    # fullPath=True keeps the prefix unique when leaf names
                    # collide (Maya allows duplicate shape leaf names under
                    # different DAG parents); the short form would later
                    # raise "No object matches name" on lookup.
                    shapes = (
                        cmds.listRelatives(
                            node,
                            shapes=True,
                            noIntermediate=True,
                            fullPath=True,
                        )
                        or []
                    )
                    if shapes:
                        return f"{shapes[0]}.{rest}"
            except Exception:
                pass
            return comp

        lst = [_to_shape_prefixed(c) for c in lst]

        if returned_type == "int":
            result = {}
            for c in lst:
                obj_list = cmds.ls(c, objectsOnly=True) or []
                if not obj_list:
                    continue
                obj = obj_list[0]
                num = c.split("[")[-1].rstrip("]")

                try:
                    if flatten:
                        componentNum = int(num)
                    else:
                        n = [int(n) for n in num.split(":")]
                        componentNum = tuple(n) if len(n) > 1 else n[0]

                    if obj in result:
                        result[obj].append(componentNum)
                    else:
                        result[obj] = [componentNum]
                except ValueError as error:
                    print(
                        f"# Error: {__file__} in convert_array_type\n#\tunable to convert {obj} {num} to int.\n#\t{error}"
                    )
                    break

            objects = set(cmds.ls(lst, objectsOnly=True) or [])
            if len(objects) == 1:
                flattened = ptk.flatten(result.values())
                result = ptk.remove_duplicates(flattened)

        elif returned_type == "str":
            result = list(map(str, lst))

        else:
            result = lst

        return result

    @staticmethod
    def get_parameter_mapping(node, cmd, parameters):
        """Query a specified Maya command and return a dict mapping parameters to their values."""
        cmd_fn = getattr(cmds, cmd)
        node_list = cmds.ls(str(node)) or []
        if not node_list:
            return {}
        node = node_list[0]
        return {p: cmd_fn(node, **{"q": True, p: True}) for p in parameters}

    @staticmethod
    def set_parameter_mapping(node, cmd, parameters):
        """Apply a set of parameter values to a specified Maya node using a given Maya command."""
        cmd_fn = getattr(cmds, cmd)
        node_list = cmds.ls(str(node)) or []
        if not node_list:
            return
        node = node_list[0]
        for p, v in parameters.items():
            cmd_fn(node, **{p: v})

    @classmethod
    def build_mesh_similarity_mapping(
        cls,
        source,
        target,
        tolerance: float = 0.1,
    ) -> dict:
        """Build a mapping of source meshes to target meshes based on geometric similarity."""
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
                mapping[short_name(source_child)] = short_name(best_match)

        return mapping

    @staticmethod
    def get_mel_globals(keyword=None, ignore_case=True):
        """Get global MEL variables."""
        env_listing = mel.eval("env") or []
        if isinstance(env_listing, str):
            env_listing = env_listing.split()
        variables = [
            v
            for v in sorted(env_listing)
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
        """Reorder a given set of objects using various sorting methods."""
        if objects is None:
            obj_list = cmds.ls(selection=True, flatten=True) or []
            if not obj_list:
                cmds.warning("No objects provided and nothing selected.")
                return []
        else:
            obj_list = cmds.ls(as_strings(objects), flatten=True) or []

        if not obj_list:
            cmds.warning("No valid objects to reorder.")
            return []

        if method == "name":
            sorted_objs = sorted(obj_list, key=leaf_name)

        elif method == "hierarchy":
            def get_hierarchy_depth(obj):
                long_paths = cmds.ls(obj, long=True) or [obj]
                return long_paths[0].count("|")

            sorted_objs = sorted(obj_list, key=get_hierarchy_depth)

        elif method in ["x", "y", "z"]:
            axis_map = {"x": 0, "y": 1, "z": 2}
            axis_index = axis_map[method]

            def get_position(obj):
                try:
                    pos = cmds.xform(obj, q=True, ws=True, t=True) or [0, 0, 0]
                    return pos[axis_index]
                except Exception:
                    return 0

            sorted_objs = sorted(obj_list, key=get_position)

        elif method == "distance":
            def get_distance(obj):
                try:
                    pos = cmds.xform(obj, q=True, ws=True, t=True) or [0, 0, 0]
                    return (pos[0] ** 2 + pos[1] ** 2 + pos[2] ** 2) ** 0.5
                except Exception:
                    return 0

            sorted_objs = sorted(obj_list, key=get_distance)

        elif method == "volume":
            def get_volume(obj):
                try:
                    bb = cmds.exactWorldBoundingBox(obj)
                    return (bb[3] - bb[0]) * (bb[4] - bb[1]) * (bb[5] - bb[2])
                except Exception:
                    return 0

            sorted_objs = sorted(obj_list, key=get_volume)

        elif method == "vertex_count":
            def get_vertex_count(obj):
                try:
                    shapes = []
                    if cmds.objectType(obj) == "transform":
                        shapes = (
                            cmds.listRelatives(obj, shapes=True, noIntermediate=True)
                            or []
                        )
                    elif cmds.nodeType(obj) in ("mesh", "nurbsCurve", "nurbsSurface"):
                        shapes = [obj]

                    if shapes:
                        shape = shapes[0]
                        node_type = cmds.nodeType(shape)
                        if node_type == "mesh":
                            return cmds.polyEvaluate(shape, vertex=True) or 0
                        elif node_type == "nurbsCurve":
                            spans = cmds.getAttr(f"{shape}.spans")
                            degree = cmds.getAttr(f"{shape}.degree")
                            form = cmds.getAttr(f"{shape}.form")
                            # form 2 = closed, form 0/1 = open/periodic open
                            return spans + degree if form != 2 else spans
                        elif node_type == "nurbsSurface":
                            # numCVsInU = spansU + degreeU
                            spans_u = cmds.getAttr(f"{shape}.spansU") or 0
                            spans_v = cmds.getAttr(f"{shape}.spansV") or 0
                            degree_u = cmds.getAttr(f"{shape}.degreeU") or 0
                            degree_v = cmds.getAttr(f"{shape}.degreeV") or 0
                            return (spans_u + degree_u) * (spans_v + degree_v)
                    return 0
                except Exception:
                    return 0

            sorted_objs = sorted(obj_list, key=get_vertex_count)

        elif method == "random":
            import random

            sorted_objs = list(obj_list)
            random.shuffle(sorted_objs)

        elif method == "creation_time":
            def get_creation_time(obj):
                try:
                    uuids = cmds.ls(obj, uuid=True) or []
                    return uuids[0] if uuids else ""
                except Exception:
                    return ""

            sorted_objs = sorted(obj_list, key=get_creation_time)

        else:
            cmds.warning(f"Unknown sorting method: '{method}'. Using 'name' instead.")
            sorted_objs = sorted(obj_list, key=leaf_name)

        if reverse:
            sorted_objs = sorted_objs[::-1]

        return sorted_objs


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
