# !/usr/bin/python
# coding=utf-8
import contextlib
from typing import Any, Union, List, Optional, Tuple, Callable
from functools import wraps

try:
    import maya.cmds as cmds
except ImportError:
    # Bind the name (house policy) so a no-Maya call site fails with a clear
    # AttributeError on None rather than a NameError on an undefined global --
    # which matters now that two error paths here report through cmds.warning.
    cmds = None
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes


class DisplayUtils(ptk.HelpMixin):
    NODES_WITH_VISIBILITY = [
        "mesh",
        "nurbsCurve",
        "nurbsSurface",
        "subdiv",
        "camera",
        "joint",
        "light",
        "locator",
        "transform",
    ]

    @staticmethod
    def add_to_isolation(func: Callable) -> Callable:
        """Decorator: add a function's newly created node(s) to every isolated viewport.

        The wrapped function's RETURN VALUE is resolved to transform(s) via
        ``NodeUtils.get_transform_node``, which forwards to ``cmds.ls`` -- and
        ``cmds.ls`` reads a stringified container as a name *pattern*, so a
        nested list or a dict return would match nothing (or raise). Containers
        are flattened and dicts reduced to their values first, and any residual
        failure is downgraded to a warning: isolation is a display convenience
        and must never abort -- or swallow the result of -- an operation that
        has already mutated the scene.

        Place it INNERMOST (directly above ``def``, below ``@CoreUtils.undoable``
        / ``@staticmethod``) so the membership write lands inside the caller's
        undo chunk rather than after it.
        """

        @wraps(func)
        def wrapped(*args, **kwargs) -> Any:
            result = func(*args, **kwargs)
            if result:
                try:
                    nodes = (
                        list(result.values()) if isinstance(result, dict) else result
                    )
                    if isinstance(nodes, (list, tuple, set)):
                        nodes = ptk.flatten(nodes, list)
                    DisplayUtils.add_to_isolation_set(
                        NodeUtils.get_transform_node(nodes, returned_type="str")
                    )
                except Exception as error:
                    cmds.warning(f"[add_to_isolation] {func.__name__}: {error}")
            return result

        return wrapped

    @staticmethod
    def is_templated(obj: Union[str, object]) -> bool:
        """Check if a given object is templated."""
        try:
            return cmds.getAttr(f"{obj}.template")
        except Exception:
            return False

    @classmethod
    @CoreUtils.undoable
    def set_visibility(
        cls,
        elements: Union[str, object, List],
        visibility: bool = True,
        include_ancestors: bool = True,
        affect_layers: bool = True,
    ) -> None:
        """Sets the visibility of specified elements in the Maya scene.
        It accepts a wide variety of inputs for the elements parameter, including strings,
        Maya nodes, or lists of nodes. It can also optionally affect the visibility of layers
        and ancestor nodes.

        Parameters:
            elements (str, object, list): A string that represents a Maya object type,
                           a wildcard expression, a single object, or a list of objects.
            visibility (bool): The visibility state to apply. If True, elements are shown; if False, elements are hidden.
            include_ancestors (bool): If True, will also set visibility for all ancestor transform nodes of the elements.
            affect_layers (bool): If True, will ensure that all layers except the default layer have their visibility set.

        Example:
            set_visibility('geometry', visibility=True)  # Shows all geometry and their ancestors, affects layers.
            set_visibility('lights', visibility=False, include_ancestors=False)  # Hides all lights without affecting their ancestors.
            set_visibility('nurbsCurves', visibility=True, affect_layers=False)  # Shows all nurbsCurves, doesn't affect layers.
            set_visibility([my_geo1, my_geo2], visibility=False)  # Hides specific geometries provided in a list.
            set_visibility('pCube*', visibility=True)  # Shows all objects with names starting with 'pCube'.
        """
        if affect_layers:
            for layer in cmds.ls(type="displayLayer") or []:
                layer_name = layer.split("|")[-1].split(":")[-1]
                try:
                    is_ref = cmds.referenceQuery(layer, isNodeReferenced=True)
                except Exception:
                    is_ref = False
                if layer_name != "defaultLayer" and not is_ref:
                    try:
                        cmds.setAttr(f"{layer}.visibility", visibility)
                    except Exception:
                        pass

        elements = CoreUtils.as_strings(elements)
        if set(elements).intersection(cls.NODES_WITH_VISIBILITY):
            scene_elements = cmds.ls(type=elements, long=True) or []
        else:
            scene_elements = cmds.ls(*elements, long=True) if elements else []

        for element in scene_elements:
            if include_ancestors:
                # Walk up the hierarchy collecting transform ancestors
                ancestors = []
                current = element
                while True:
                    parents = cmds.listRelatives(current, parent=True, fullPath=True)
                    if not parents:
                        break
                    parent = parents[0]
                    if cmds.nodeType(parent) == "transform":
                        ancestors.append(parent)
                    current = parent
                for ancestor in ancestors:
                    try:
                        cmds.setAttr(f"{ancestor}.visibility", visibility)
                    except Exception:
                        pass

            try:
                cmds.setAttr(f"{element}.visibility", visibility)
            except Exception:
                pass

    @staticmethod
    def set_hidden_in_outliner(
        elements: Union[str, object, List],
        state: bool = True,
        shapes: bool = True,
        refresh: bool = True,
    ) -> List[str]:
        """Hide (or restore) DAG nodes in the Outliner via ``hiddenInOutliner``.

        A purely visual DAG-node flag — unlike :meth:`set_visibility` it changes
        nothing about the node itself: ``ls``, ``select``, export sets and FBX
        writes all still see it, it just stops drawing an Outliner row. That's
        what keeps pipeline plumbing (the ``DataNodes`` export carrier, the HDR
        skydome) out of the user's Outliner without making it unselectable or
        unexportable.

        Parameters:
            elements: Node(s) to flag. Names resolve through ``cmds.ls``, so
                wildcards work (as in :meth:`set_visibility`) and a duplicate
                short name flags every match; shape/component names resolve to
                the node.
            state: True hides, False restores the row.
            shapes: Also flag each node's shapes — the Outliner draws them as
                child rows when *Show Shapes* is on.
            refresh: Redraw the Outliner panels when something actually
                changed (the write alone leaves the panel stale). No-ops
                without a UI.

        Returns:
            list: Full paths of the nodes whose flag changed — empty when
            everything already matched (which is also what suppresses the
            redraw).
        """
        nodes = []
        for element in CoreUtils.as_strings(elements):
            name = element.split(".")[0]  # tolerate a component/attribute suffix
            # Resolve to full paths -- a duplicate short name (an imported
            # second carrier parented under a group) makes every plug query
            # ambiguous: ``getAttr`` hands back a *list* of values, whose
            # truthiness reads as "already set", and ``setAttr`` raises.
            for node in cmds.ls(name, long=True) or []:
                nodes.append(node)
                if shapes:
                    nodes.extend(NodeUtils.get_shapes(node, no_intermediate=False))

        changed = []
        for node in dict.fromkeys(nodes):
            plug = f"{node}.hiddenInOutliner"
            if not cmds.objExists(plug):
                continue  # not a DAG node (network, shader, …)
            if bool(cmds.getAttr(plug)) == bool(state):
                continue
            # Skips a locked/connected plug rather than raising (referenced
            # scenes) -- so confirm the write instead of assuming it landed.
            Attributes.set_plug(plug, state)
            if bool(cmds.getAttr(plug)) == bool(state):
                changed.append(node)

        if changed and refresh:
            # Deferred: ui_utils imports ``maya.cmds`` unguarded, and the
            # redraw is only ever needed once a write actually landed.
            from mayatk.ui_utils._ui_utils import UiUtils

            UiUtils.refresh_outliners()
        return changed

    @classmethod
    def is_visible(
        cls,
        node: str,
        consider_templated_visible: bool = False,
        consider_animated_visible: bool = False,
    ) -> bool:
        """Whether *node* renders -- its own visibility AND every parent's.

        Maya's visibility is INHERITED, so a node's own flag answers nothing on
        its own: the reported case was four area lights whose shapes were all
        correctly configured while their transforms carried ``.v no``. Anything
        deciding "will the renderer see this" has to walk the DAG, which is why
        this is a primitive rather than a check each caller writes -- it backs
        both :meth:`get_visible_geometry` (geometry) and
        :meth:`mayatk.LightUtils.contributing_lights` (lights), and those two
        must not be able to disagree about what visible means.

        Three flags hide a node, each read on every node of the path: its
        ``.visibility``, its ``.lodVisibility``, and an enabled drawing
        override's ``.overrideVisibility`` -- the one a display layer drives.
        A drawing override set to Template (a Template layer) is a template
        like ``.template`` itself. Measured on mtoa: a mesh hidden or
        templated by any of them is "not exported to Arnold world", and a
        lightmap bake of it wrote no map and stopped there.

        Parameters:
            node: Any DAG node (transform or shape).
            consider_templated_visible: Treat templated nodes as visible.
            consider_animated_visible: Treat a node whose ``.visibility`` has an
                incoming connection (animCurve, expression) as visible
                regardless of the current frame -- it is hidden *now*, not
                hidden *always*, and a bake or export covering other frames
                still needs it.

        Returns:
            bool: True when the node and every ancestor pass.
        """
        current = node
        while current:
            if not cmds.getAttr(f"{current}.visibility"):
                if consider_animated_visible and cmds.listConnections(
                    f"{current}.visibility", source=True, destination=False
                ):
                    pass  # Treat as visible — animation will be baked
                else:
                    return False
            if not cmds.getAttr(f"{current}.lodVisibility"):
                return False
            overridden = cmds.getAttr(f"{current}.overrideEnabled")
            if overridden and not cmds.getAttr(f"{current}.overrideVisibility"):
                return False
            if not consider_templated_visible and (
                cls.is_templated(current)
                or (overridden and cmds.getAttr(f"{current}.overrideDisplayType") == 1)
            ):
                return False
            parents = cmds.listRelatives(current, parent=True, fullPath=True)
            current = parents[0] if parents else None
        return True

    @classmethod
    def get_visible_geometry(
        cls,
        shapes: bool = False,
        consider_templated_visible: bool = False,
        inherit_parent_visibility: bool = False,
        consider_animated_visible: bool = False,
    ) -> List[str]:
        """Get a list of visible geometry.

        Parameters:
            shapes (bool): Return shape nodes instead of transforms. Default is False.
            consider_templated_visible (bool): Treat templated geometry as visible.
            inherit_parent_visibility (bool): Check visibility of parent objects.
            consider_animated_visible (bool): When True, parents whose
                ``.visibility`` attribute has incoming connections (e.g.
                animCurves, expressions) are treated as visible regardless
                of the current-frame value.  This ensures geometry that
                is hidden at the current frame but animated at other
                frames is included in the result.

        Returns:
            List[str]: A list of visible node names of the specified type.
        """

        def is_node_visible(node: str) -> bool:
            return cls.is_visible(
                node,
                consider_templated_visible=consider_templated_visible,
                consider_animated_visible=consider_animated_visible,
            )

        result = []

        if shapes:
            # List the concrete renderable types — "geometry" is not a
            # queryable ls type (it silently returns []), which is why the
            # shapes branch always came back empty. Intermediate (Orig)
            # shapes are construction data, never visible geometry.
            nodes = (
                cmds.ls(type=NodeUtils.SURFACE_TYPES, noIntermediate=True, long=True)
                or []
            )
        else:
            nodes = cmds.ls(type="transform", long=True) or []

        for node in nodes:
            if not consider_templated_visible and cls.is_templated(node):
                continue

            if inherit_parent_visibility and not is_node_visible(node):
                continue

            if shapes:
                result.append(node)
            else:
                for s in cmds.listRelatives(node, shapes=True, fullPath=True) or []:
                    if cmds.nodeType(s) in NodeUtils.SURFACE_TYPES:
                        result.append(node)
                        break

        return result

    @staticmethod
    def get_isolated_panels() -> List[str]:
        """Every model panel that currently has Isolate Select turned on.

        Isolate Select is per-model-editor state, so a single pane is never the
        right question: ``paneLayout -q -pane1 viewPanes`` cannot see panes 2-4
        of a four-view layout nor a torn-off viewport, and Maya's stock
        "Hypershade/Persp" layout puts a scriptedPanel in pane 1 -- which
        ``cmds.modelEditor`` rejects outright, raising out of the middle of the
        caller's operation. ``cmds.getPanel`` enumerates them all instead,
        mirroring Maya's own ``isolateSelectAddObject``
        (``scripts/others/createModelPanelMenu.mel``).

        Doubles as the cheap gate for callers deciding whether more expensive
        work (Preview's full-scene node diff) is worth doing at all.

        Never raises: it gates a commit path (``MayaPreviewSession._replay``)
        where an exception would abort the user's operation before it even runs.

        Returns:
            (list) Panel names with ``viewSelected`` on; empty in batch, where
            ``getPanel`` returns None.
        """
        try:
            model_panels = cmds.getPanel(type="modelPanel") or []
        except Exception:
            return []
        panels = []
        for panel in model_panels:
            try:
                if cmds.modelEditor(panel, exists=True) and cmds.modelEditor(
                    panel, query=True, viewSelected=True
                ):
                    panels.append(panel)
            except RuntimeError:  # panel torn down between the query and the use
                continue
        return panels

    @classmethod
    def add_to_isolation_set(
        cls, objects: Union[str, object, List[Union[str, object]]]
    ) -> List[str]:
        """Add transform(s) to the isolation set of every isolated viewport.

        No-op when no panel has Isolate Select on, and in batch. Call it after
        creating nodes so they don't land invisible for a user working in
        "view selected".

        Membership is added in one batched ``cmds.sets`` per panel, then
        committed with ``isolateSelect -update``: a bare ``sets -add`` is off
        Maya's supported path (its own scripts only ever use
        ``isolateSelect -addDagObject`` / ``-addSelected``), and the existence
        of the ``-update`` flag is the API's admission that an out-of-band set
        edit needs an explicit refresh. Per-object ``isolateSelect`` is the
        fallback -- correct but O(n) commands, which bulk ops (DuplicateGrid at
        20x20x20) cannot afford per preview refresh. It is also the ONLY path
        for a panel isolated on an empty selection: the set is created lazily,
        so ``viewObjects`` answers "" until something is added.

        ``cmds.sets(add=...)`` is undo-recorded, so the writes are grouped into
        one chunk -- but only when recording is on: under a Preview
        ``CleanupContract`` undo is deliberately suppressed, and opening a chunk
        there would be the one thing that could leak an entry per refresh.

        Parameters:
            objects (str/obj/list): Nodes to add. Nested containers are
                flattened; non-transforms and missing nodes are dropped.

        Never raises. Isolation is a display convenience applied AFTER the work
        is done, and callers add from inside a broad ``try`` whose ``except``
        reports the OPERATION as failed (``EditUtils.separate_mirrored_mesh``
        warns "polySeparate operation failed" and returns None) or from inside a
        ``try/finally`` with no ``except`` at all (``AutoInstancer.run``,
        ``DynamicPipe``). A raise here would be misreported as the operation
        failing and would swallow its result, so failures degrade to a warning
        and an empty return instead.

        Parameters:
            objects (str/obj/list): Nodes to add. Nested containers are
                flattened; non-transforms and missing nodes are dropped.

        Returns:
            (list) The panels that were updated (empty when there was nothing
            to do, or when the attempt failed).
        """
        try:
            return cls._add_to_isolation_set(objects)
        except Exception as error:
            cmds.warning(f"[add_to_isolation_set] {error}")
            return []

    @classmethod
    def _add_to_isolation_set(cls, objects) -> List[str]:
        """Body of :meth:`add_to_isolation_set` -- see it for the contract."""
        # Gate on the viewport FIRST: nothing isolated is the overwhelmingly
        # common case, and it costs a few panel queries to rule out, where
        # resolving the names costs an objExists per node plus a full cmds.ls
        # -- which bulk callers (DuplicateGrid at 20x20x20) pay per refresh.
        panels = cls.get_isolated_panels()
        if not panels:
            return []

        # Flatten first: ``as_strings`` stringifies a nested list whole, which
        # then matches nothing and vanishes silently.
        if isinstance(objects, (list, tuple, set)):
            objects = ptk.flatten(objects, list)
        # Coerce to plain strings + drop missing nodes. ``cmds.ls`` raises
        # ``TypeError`` when passed a node that wraps a deleted MObject
        # (common when callers mirror/delete then forward the originals).
        names = [n for n in CoreUtils.as_strings(objects) if cmds.objExists(n)]
        transforms = cmds.ls(*names, type="transform", long=True) if names else []
        if not transforms:
            return []

        recording = False
        try:
            recording = bool(cmds.undoInfo(query=True, state=True))
        except Exception:
            pass
        chunk = (
            CoreUtils.undo_chunk("add_to_isolation_set")
            if recording
            else contextlib.nullcontext()
        )
        updated: List[str] = []
        with chunk:
            for panel in panels:
                iso_set = cmds.modelEditor(panel, query=True, viewObjects=True)
                if iso_set:
                    try:
                        cmds.sets(transforms, add=iso_set)
                        cmds.isolateSelect(panel, update=True)
                        updated.append(panel)
                        continue
                    except Exception:
                        pass  # fall through to the per-object path
                # Either the panel is isolated but owns no set yet, or the
                # batched add failed. `isolateSelect -state 1` alone does NOT
                # create the set -- a viewport isolated on an empty selection
                # answers `viewObjects` with "" (verified, Maya 2025), and
                # `sets(add="")` raises. `-addDagObject` is the command that
                # CREATES it, so it is both the fallback and the only way to
                # reach a freshly-isolated empty viewport.
                added = False
                for obj in transforms:
                    try:
                        cmds.isolateSelect(panel, addDagObject=obj)
                        added = True
                    except RuntimeError:  # one bad name must not drop the rest
                        continue
                if added:
                    cmds.isolateSelect(panel, update=True)
                    updated.append(panel)
        return updated

    # Smooth mesh preview -------------------------------------------------
    # ``smoothDrawType`` enum: 0 Maya Catmull-Clark, 2 OpenSubdiv Catmull-Clark,
    # 3 OpenSubdiv Catmull-Clark Adaptive (index 1 is unused -- the enum string
    # is 'Maya Catmull-Clark:OpenSubdiv Catmull-Clark=2:...').
    SMOOTH_DRAW_ADAPTIVE = 3

    @classmethod
    def set_smooth_preview(
        cls,
        objects,
        display: int = None,
        level: int = None,
        adaptive_level: int = None,
        subd_comps: bool = None,
    ) -> List[str]:
        """Configure smooth-mesh preview on the mesh shapes under *objects*.

        Every one of these lives on the MESH SHAPE. A transform tolerates
        ``getAttr``/``setAttr`` (the plug resolves down to the shape) but reports
        ``attributeQuery(exists=True)`` as **False**, so callers that guard on
        existence against a transform silently do nothing -- resolve first.

        Parameters:
            objects: Transforms, groups, shapes or components; anything without
                a mesh below it is ignored.
            display (int|None): ``displaySmoothMesh`` -- 0 off (cage), 1 cage +
                smooth, 2 smooth preview.
            level (int|None): ``smoothLevel`` -- preview division levels (0-15).
            adaptive_level (int|None): ``smoothTessLevel`` -- adaptive tessellation
                level (1-10). Only the ADAPTIVE draw type honours it, and a mesh
                follows the global draw type by default, so setting this also
                clears ``useGlobalSmoothDrawType`` and switches the shape to
                OpenSubdiv Adaptive -- otherwise the value is inert.
            subd_comps (bool|None): ``displaySubdComps`` -- draw the subdivided
                components rather than the base cage wireframe.

        Returns:
            list: The mesh shapes resolved from *objects* (empty if there were
            none). A shape whose plug is locked or connected is left alone --
            one referenced mesh must not abort the rest of the selection.
        """
        writes = {}
        if display is not None:
            writes["displaySmoothMesh"] = display
        if level is not None:
            writes["smoothLevel"] = level
        if subd_comps is not None:
            writes["displaySubdComps"] = bool(subd_comps)
        if adaptive_level is not None:
            writes["useGlobalSmoothDrawType"] = False
            writes["smoothDrawType"] = cls.SMOOTH_DRAW_ADAPTIVE
            writes["smoothTessLevel"] = adaptive_level

        meshes = NodeUtils.get_shapes(objects, descend=True, type="mesh")
        for mesh in meshes:
            for attr, value in writes.items():
                Attributes.set_plug(f"{mesh}.{attr}", value)
        return meshes

    @staticmethod
    def get_smooth_level(objects=None) -> Optional[dict]:
        """The smooth-preview levels of the first mesh under *objects* (None: the
        selection): ``{"level": smoothLevel, "adaptive_level": smoothTessLevel}``,
        or None with no mesh. What a panel seeds its level controls from. Twin of
        blendertk's ``DisplayUtils.get_smooth_level`` (a Subdivision Surface
        modifier's viewport / render levels there).
        """
        nodes = (
            cmds.ls(selection=True) if objects is None else CoreUtils.as_strings(objects)
        )
        if not nodes:
            return None
        shapes = NodeUtils.get_shapes(nodes, descend=True, type="mesh")
        if not shapes:
            return None
        return {
            "level": cmds.getAttr(f"{shapes[0]}.smoothLevel"),
            "adaptive_level": cmds.getAttr(f"{shapes[0]}.smoothTessLevel"),
        }

    @classmethod
    @CoreUtils.selected
    @CoreUtils.undoable
    def set_smooth_level(
        cls, objects=None, level: int = None, adaptive_level: int = None
    ) -> List[str]:
        """Show the smooth mesh preview on the meshes under *objects* (None: the
        selection) at *level* divisions and/or *adaptive_level* (OpenSubdiv
        adaptive tessellation; see :meth:`set_smooth_preview`), then read the
        plugs back: a locked or connected plug keeps its value, so only the meshes
        that took every value count. A *level* that took also becomes Maya's
        Subdiv Proxy division default (``proxyDivisions``). Twin of blendertk's
        ``DisplayUtils.set_smooth_level`` (a Subdivision Surface modifier's
        viewport / render levels there). One undo step.

        Returns:
            (list): The mesh shapes that took the values; ``[]`` when *objects*
                holds no mesh. Skipped meshes are warned about.

        Raises:
            ValueError: Meshes were found but none took the value (all locked
                or connected).
        """
        meshes = cls.set_smooth_preview(
            objects, display=2, level=level, adaptive_level=adaptive_level
        )
        wanted = {"smoothLevel": level, "smoothTessLevel": adaptive_level}
        applied = []
        for mesh in meshes:
            try:
                if all(
                    cmds.getAttr(f"{mesh}.{attr}") == value
                    for attr, value in wanted.items()
                    if value is not None
                ):
                    applied.append(mesh)
            except (RuntimeError, ValueError):  # the plug went with its shape
                continue
        if meshes and not applied:
            raise ValueError("no mesh accepted the change (locked or connected).")
        if len(applied) < len(meshes):
            cmds.warning(
                f"set_smooth_level: {len(meshes) - len(applied)} mesh(es) skipped "
                "(locked or connected)."
            )
        if applied and level is not None:
            cmds.optionVar(intValue=("proxyDivisions", int(level)))
        return applied

    # --- x-ray -----------------------------------------------------------
    # ``displaySurface -xRay`` is a per-SHAPE draw flag, not an attribute: it
    # is invisible to ``listAttr``, is not saved with the scene, and is dropped
    # silently by polyUnite / polySeparate / boolean / duplicate / scene reload.
    # Everything below is written around those three facts.

    @staticmethod
    def get_surface_shapes(objects: Union[str, object, List]) -> List[str]:
        """Visible (non-intermediate) surface shapes at or under the given nodes.

        Every ``displaySurface`` flag lives on the shape, and the query refuses
        to answer for more than one at a time ("Can not query culling on
        multiple objects!"), so anything the user can actually select -- a
        group, a rig locator parenting geometry, a transform carrying two
        shapes, a component selection -- has to be resolved down to shapes
        before it can be read. ``ls -dag`` descends unconditionally, which is
        the point: ``NodeUtils.get_shapes(descend=True)`` deliberately stops at
        a transform that owns a shape, and that transform is exactly the rig
        locator case.

        Parameters:
            objects: Node(s), component(s), or an iterable of either.

        Returns:
            list: Full DAG paths, de-duplicated (never None). In ``ls`` order,
            not argument order -- no caller of a set-wide flag depends on it.
        """
        nodes = list(
            dict.fromkeys(n.split(".")[0] for n in CoreUtils.as_strings(objects))
        )
        if not nodes:  # cmds reads an empty list as "everything", not "nothing"
            return []
        return (
            cmds.ls(
                nodes,
                dag=True,
                leaf=True,
                noIntermediate=True,
                long=True,
                type="surfaceShape",
            )
            or []
        )

    @classmethod
    def is_xray(cls, objects: Union[str, object, List]) -> bool:
        """True when EVERY surface shape at or under *objects* is x-rayed.

        Uniform rather than per-object on purpose: the flag is dropped silently
        by common topology ops, so a selection routinely arrives half-x-rayed
        and a first-object probe would report the whole set as x-rayed. False
        when there is no surface shape to read.
        """
        shapes = cls.get_surface_shapes(objects)
        if not shapes:
            return False
        for shape in shapes:
            result = cmds.displaySurface(shape, xRay=True, query=True)
            if not (result and result[0]):
                return False
        return True

    @classmethod
    def set_xray(
        cls, objects: Union[str, object, List], state: bool = True, resync: bool = True
    ) -> List[str]:
        """Set the x-ray flag on every surface shape at or under *objects*.

        Parameters:
            objects: Node(s), component(s), or an iterable of either.
            state (bool): The flag to apply.
            resync (bool): Re-apply the flags in the viewport afterwards (see
                `resync_viewport_xray`). Pass False when batching several calls
                and resync once at the end.

        Returns:
            list: The shapes that were set.
        """
        shapes = cls.get_surface_shapes(objects)
        for shape in shapes:
            cmds.displaySurface(shape, xRay=bool(state))
        if shapes and resync:
            cls.resync_viewport_xray()
        return shapes

    @classmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def toggle_xray(
        cls, objects: Union[str, object, List] = None
    ) -> Optional[Tuple[bool, int]]:
        """Uniform x-ray toggle: if ANY shape is off, turn them ALL on; only a
        fully x-rayed set turns off. A blind per-object invert would desync a
        set that a topology op had already knocked out of step. Twin of
        blendertk's ``DisplayUtils.toggle_xray`` (``show_in_front``).

        Parameters:
            objects: Node(s) or component(s); ``None``: the selection.

        Returns:
            tuple: (applied state, shape count), or None when there was nothing
            to operate on; ``[]`` with nothing selected.
        """
        shapes = cls.get_surface_shapes(objects)
        if not shapes:
            return None
        state = not cls.is_xray(shapes)
        cls.set_xray(shapes, state)
        return state, len(shapes)

    @staticmethod
    def resync_viewport_xray() -> None:
        """Force VP2 to re-apply every object's per-object x-ray flag.

        VP2 silently drops the x-ray draw state when it rebuilds a mesh's render
        item (renderer reset, some topology rebuilds): the flag still queries
        True but the mesh draws opaque, and no query can read the drawn state.
        Cycling the panel-level x-ray makes VP2 re-evaluate the per-object flags
        (pixel-verified, Maya 2025). A no-op in batch, where there is no panel.
        """
        for panel in cmds.getPanel(type="modelPanel") or []:
            state = cmds.modelEditor(panel, query=True, xray=True)
            cmds.modelEditor(panel, edit=True, xray=not state)
            cmds.modelEditor(panel, edit=True, xray=state)

    @classmethod
    @CoreUtils.undoable
    def clear_xray(cls) -> List[str]:
        """Turn x-ray off on every surface shape in the scene ("Un-Xray All").
        Twin of blendertk's ``DisplayUtils.clear_xray``.

        Returns:
            list: The shapes cleared.
        """
        shapes = cmds.ls(type="surfaceShape", noIntermediate=True, long=True) or []
        return cls.set_xray(shapes, False)

    @classmethod
    @CoreUtils.undoable
    def toggle_xray_others(cls, objects=None) -> Optional[Tuple[bool, int]]:
        """Uniform x-ray toggle (:meth:`toggle_xray`) over every surface shape in
        the scene EXCEPT those at or under *objects* ("X-Ray Other"). Twin of
        blendertk's ``DisplayUtils.toggle_xray_others``.

        Parameters:
            objects: Node(s) or component(s) to leave out; ``None``: the
                selection, and nothing selected toggles every shape.

        Returns:
            tuple: (applied state, shape count), or None when nothing is left.
        """
        if objects is None:
            objects = CoreUtils.selected_objects()
        every = cmds.ls(type="surfaceShape", noIntermediate=True, long=True) or []
        mine = set(cls.get_surface_shapes(objects))
        return cls.toggle_xray(sorted(set(every) - mine))

    # --- display toggles --------------------------------------------------
    # The Display menu's rows (tentacle) and the display macros' bodies. Each
    # answers what it did -- the nodes, the state it set, the mode now shown --
    # or None when there was nothing to act on, never a message: the caller
    # words it. Twins of blendertk's ``DisplayUtils`` toggles.

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def hide_objects(objects=None) -> List[str]:
        """Hide *objects* (``cmds.hide``). Twin of blendertk's ``DisplayUtils.hide_objects``.

        Parameters:
            objects: Node(s); ``None``: the selection.

        Returns:
            list: The nodes hidden; ``[]`` with nothing selected.
        """
        nodes = CoreUtils.as_strings(objects)
        cmds.hide(nodes)
        return nodes

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def show_objects(objects=None) -> List[str]:
        """Show hidden *objects* (``cmds.showHidden``) -- a selection made in the
        Outliner, where Maya selects hidden nodes. Twin of blendertk's
        ``DisplayUtils.show_objects``.

        Parameters:
            objects: Node(s); ``None``: the selection.

        Returns:
            list: The nodes shown; ``[]`` with nothing selected.
        """
        nodes = CoreUtils.as_strings(objects)
        cmds.showHidden(nodes)
        return nodes

    @staticmethod
    def cycle_component_ids() -> Optional[str]:
        """Step the selected meshes' component-ID display: vertex, edge, face,
        UV, off. Twin of blendertk's (an on / off viewport overlay there).

        Returns:
            (str/None): The display now shown: ``"vertex"``, ``"edge"``,
            ``"face"``, ``"uv"`` or ``"off"``; None with no polygon mesh selected.
        """
        # polyOptions answers None when no polygon mesh is in the selection.
        state = cmds.polyOptions(query=True, displayItemNumbers=True)
        if not state:
            return None
        current = list(state[:4])
        index = (current.index(True) + 1) % 5 if True in current else 0
        flags = [0, 0, 0, 0]
        if index < 4:
            flags[index] = 1
        cmds.polyOptions(activeObjects=True, displayItemNumbers=tuple(flags))
        return ("vertex", "edge", "face", "uv", "off")[index]

    @staticmethod
    def cycle_normals_display() -> Optional[str]:
        """Step the selected meshes' normals display: face, vertex, tangent, off.
        Twin of blendertk's (face, vertex, off: Blender draws no tangents).

        Returns:
            (str/None): The display now shown: ``"face"``, ``"vertex"``,
            ``"tangent"`` or ``"off"``; None with no polygon mesh selected.
        """
        tangent = cmds.polyOptions(query=True, displayTangent=True)
        if not tangent:
            return None
        normal = cmds.polyOptions(query=True, displayNormal=True)[0]
        if tangent[0]:
            current = 3
        elif normal and cmds.polyOptions(query=True, point=True)[0]:
            current = 2
        elif normal and cmds.polyOptions(query=True, facet=True)[0]:
            current = 1
        else:
            current = 0
        index = (current + 1) % 4
        cmds.polyOptions(
            **(
                {"displayNormal": False, "displayTangent": False},
                {"displayNormal": True, "facet": True, "point": False,
                 "displayTangent": False, "sizeNormal": 1},
                {"displayNormal": True, "point": True, "facet": False,
                 "displayTangent": False, "sizeNormal": 1},
                {"displayTangent": True, "displayNormal": False},
            )[index]
        )
        return ("off", "face", "vertex", "tangent")[index]

    @staticmethod
    def toggle_soft_edge_display() -> Optional[bool]:
        """Toggle the selected meshes between soft edges only and all edges.
        Twin of blendertk's (the viewport's sharp-edge overlay).

        Returns:
            (bool/None): True: soft edges only; False: all edges; None with no
            polygon mesh selected.
        """
        state = cmds.polyOptions(query=True, allEdges=True)
        if not state:
            return None
        if state[0]:
            cmds.polyOptions(allEdges=False, softEdge=True)
            return True
        cmds.polyOptions(softEdge=False, allEdges=True)
        return False

    @staticmethod
    @CoreUtils.selected
    def toggle_uv_border_edges(objects=None) -> Optional[bool]:
        """Uniform toggle of the UV-border-edge display on *objects*' meshes, at
        the user's border width (Preferences): any hidden turns them all on.

        Parameters:
            objects: Node(s) or component(s); ``None``: the selection.

        Returns:
            (bool/None): The state set (True: shown); None when nothing given
            is a polygon mesh; ``[]`` with nothing selected.
        """
        # polyOptions answers None when nothing given is a polygon mesh, and
        # selecting a locator / joint / curve is routine.
        state = cmds.polyOptions(objects, query=True, displayMapBorder=True)
        if not state:
            return None
        shown = not all(state)
        width = cmds.optionVar(query="displayPolyBorderEdgeSize")
        flags = {"displayMapBorder": shown}
        if isinstance(width, (list, tuple)) and len(width) > 1:
            flags["sizeBorder"] = width[1]
        cmds.polyOptions(objects, **flags)
        return shown

    @staticmethod
    def toggle_material_override() -> Optional[bool]:
        """Toggle the focused viewport's default-material override: every
        material reads as Maya's flat default, for silhouette and form. Twin of
        blendertk's (every 3D viewport's solid colour: Single).

        Returns:
            (bool/None): The state set; None with no 3D viewport.
        """
        from mayatk.ui_utils._ui_utils import UiUtils

        panel = UiUtils.get_model_panel(with_focus=True)
        if not panel:
            return None
        state = not cmds.modelEditor(panel, query=True, useDefaultMaterial=True)
        cmds.modelEditor(panel, edit=True, useDefaultMaterial=state)
        return state

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def set_wireframe(objects=None, state: bool = True) -> List[str]:
        """Draw *objects* as wireframe, or shaded again (*state* False), through
        their drawing override. Twin of blendertk's (``display_type``).

        Parameters:
            objects: Node(s); a component stands for its shape. ``None``: the selection.
            state (bool): Wireframe (True) or shaded (False).

        Returns:
            list: The nodes set; ``[]`` with nothing selected.
        """
        nodes = cmds.ls(CoreUtils.as_strings(objects), objectsOnly=True) or []
        for node in nodes:
            cmds.setAttr(f"{node}.overrideEnabled", 1)
            # overrideShading off draws the node as wireframe.
            cmds.setAttr(f"{node}.overrideShading", 0 if state else 1)
        return nodes

    @classmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def toggle_wireframe(cls, objects=None):
        """Toggle *objects* between wireframe and shaded, together: the first
        one's state decides. Twin of blendertk's ``DisplayUtils.toggle_wireframe``.

        Parameters:
            objects: Node(s); ``None``: the selection.

        Returns:
            (bool/list): True when they are now wireframe; ``[]`` with nothing selected.
        """
        nodes = cmds.ls(CoreUtils.as_strings(objects), objectsOnly=True) or []
        if not nodes:
            return []
        first = nodes[0]
        wire = not (
            cmds.getAttr(f"{first}.overrideEnabled")
            and not cmds.getAttr(f"{first}.overrideShading")
        )
        cls.set_wireframe(nodes, wire)
        return wire

    @staticmethod
    def toggle_wireframe_inactive() -> Optional[bool]:
        """Toggle the focused viewport's wireframe-on-inactive: only the selected
        objects shaded. Twin of blendertk's (every unselected mesh's
        ``display_type``).

        Returns:
            (bool/None): The state set; None with no 3D viewport.
        """
        from mayatk.ui_utils._ui_utils import UiUtils

        panel = UiUtils.get_model_panel(with_focus=True)
        if not panel:
            return None
        state = not cmds.modelEditor(panel, query=True, activeOnly=True)
        cmds.modelEditor(panel, edit=True, activeOnly=state)
        return state

    @classmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def toggle_template(cls, objects=None):
        """Template *objects* (drawn dimmed, not selectable in the viewport), or
        untemplate them when every one already is. Twin of blendertk's
        ``DisplayUtils.toggle_template``.

        Parameters:
            objects: Node(s); ``None``: the selection.

        Returns:
            (tuple/list): ``(templated, nodes)``; ``[]`` with nothing selected.
        """
        nodes = cmds.ls(CoreUtils.as_strings(objects), objectsOnly=True) or []
        if not nodes:
            return []
        state = not all(cls.is_templated(node) for node in nodes)
        for node in nodes:
            try:
                cmds.setAttr(f"{node}.template", state)
            except RuntimeError:  # a locked or connected template plug
                continue
        return state, nodes

    @staticmethod
    def _uv_editor_panel() -> Optional[str]:
        """The UV Editor's panel (it exists while hidden), or None in batch."""
        panels = cmds.getPanel(scriptType="polyTexturePlacementPanel") or []
        return panels[0] if panels else None

    @classmethod
    def toggle_uv_checker(cls) -> Optional[bool]:
        """Toggle the UV Editor's checker display. Twin of blendertk's (a
        generated UV grid in the UV editors).

        Returns:
            (bool/None): The state set; None with no UV Editor.
        """
        panel = cls._uv_editor_panel()
        if panel is None:
            return None
        state = not cmds.textureWindow(panel, query=True, displayCheckered=True)
        cmds.textureWindow(panel, edit=True, displayCheckered=state)
        return state

    @classmethod
    def toggle_uv_distortion(cls) -> Optional[str]:
        """Toggle the UV Editor's distortion shading. Twin of blendertk's, which
        steps Blender's stretch overlay through angle, area and off.

        Returns:
            (str/None): The display now shown: ``"on"`` or ``"off"``; None with
            no UV Editor.
        """
        panel = cls._uv_editor_panel()
        if panel is None:
            return None
        state = not cmds.textureWindow(panel, query=True, displayDistortion=True)
        cmds.textureWindow(panel, edit=True, displayDistortion=state)
        return "on" if state else "off"

    @staticmethod
    @CoreUtils.undoable
    def toggle_local_axes(objects=None, joints: bool = False) -> Optional[bool]:
        """Toggle the local rotation axes display (``displayLocalAxis``), the lead
        (the first) deciding the state for all. Twin of blendertk's
        ``DisplayUtils.toggle_local_axes`` (bone / object axes there).

        Parameters:
            objects (str/obj/list): Nodes; ``None``: the selection.
            joints (bool): Toggle the joints among them; called bare with no
                joint selected, every joint in the scene. False: the transforms
                themselves (joints included).

        Returns:
            (bool/None): True when the axes now show; None with nothing to toggle.
        """
        names = (
            CoreUtils.selected_objects()
            if objects is None
            else CoreUtils.as_strings(objects)
        )
        kind = "joint" if joints else "transform"
        nodes = (cmds.ls(names, type=kind, long=True) or []) if names else []
        if not nodes and joints and objects is None:
            nodes = cmds.ls(type="joint", long=True) or []
        if not nodes:
            return None
        state = not cmds.getAttr(f"{nodes[0]}.displayLocalAxis")
        for node in nodes:
            try:
                cmds.setAttr(f"{node}.displayLocalAxis", state)
            except RuntimeError:  # locked, or a locked reference edit
                continue
        return state

    #: ``{kind: (command, flags)}`` behind :meth:`joint_display_scale`.
    _JOINT_DISPLAY_SCALES = {
        "joint": ("jointDisplayScale", {}),
        "ik_handle": ("ikHandleDisplayScale", {}),
        "ik_fk": ("jointDisplayScale", {"ikfk": True}),
    }

    @classmethod
    def joint_display_scale(
        cls, kind: str = "joint", value: Optional[float] = None
    ) -> float:
        """Maya's global display scale of joints ("joint"), IK handles
        ("ik_handle") or IK/FK joints ("ik_fk"): read it, or set it to *value*
        first (Display > Animation). Maya's own: Blender has no global bone
        display scale.

        Returns:
            float: The scale, after any change.

        Raises:
            ValueError: An unknown *kind*.
        """
        if kind not in cls._JOINT_DISPLAY_SCALES:
            raise ValueError(
                f"Unknown display scale {kind!r}; expected one of "
                f"{tuple(cls._JOINT_DISPLAY_SCALES)}."
            )
        command, flags = cls._JOINT_DISPLAY_SCALES[kind]
        scale = getattr(cmds, command)
        if value is not None:
            scale(float(value), **flags)
        return float(scale(query=True, **flags))

    @staticmethod
    def reset_viewport(max_res=4096):
        """Resets Viewport 2.0 to fix graphical glitches (e.g. green scrambled textures).

        This flushes the GPU memory and restarts the OGS renderer.
        It also sets the Max Texture Resolution to prevent clamping.
        """
        try:
            if cmds.objExists("hardwareRenderingGlobals"):
                cmds.setAttr("hardwareRenderingGlobals.textureMaxResolution", max_res)

            print("Resetting Viewport 2.0...")
            cmds.ogs(reset=True)
            cmds.refresh(force=True)
            print("Viewport reset complete.")

        except Exception as e:
            print(f"Failed to reset viewport: {e}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
