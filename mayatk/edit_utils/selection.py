# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

from typing import List, Union, Set

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    pass
import pythontk as ptk

# From this package
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes


class Selection(ptk.LoggingMixin, ptk.HelpMixin):
    """Utilities for advanced Maya selection operations."""

    _SELECTION_CONFIG = {
        "Animation": {
            "Animated Objects": lambda objs: Selection._select_animated_objects(objs),
            "Clusters": lambda objs: NodeUtils.list_transforms(
                objs, type="clusterHandle"
            ),
            "Constraints": lambda objs: cmds.ls(objs, type="constraint") or [],
            "IK Handles": lambda objs: (
                cmds.ls(objs, type=["ikHandle", "hikEffector"]) or []
            ),
            "Joints": lambda objs: cmds.ls(objs, type="joint") or [],
        },
        "Dynamics": {
            "Brushes": lambda objs: cmds.ls(objs, type="brush") or [],
            "Dynamic Constraints": lambda objs: NodeUtils.list_transforms(
                objs, type="dynamicConstraint"
            ),
            "Fluids": lambda objs: NodeUtils.list_transforms(objs, type="fluidShape"),
            "Follicles": lambda objs: NodeUtils.list_transforms(objs, type="follicle"),
            "Lattices": lambda objs: NodeUtils.list_transforms(objs, type="lattice"),
            "nCloths": lambda objs: NodeUtils.list_transforms(objs, type="nCloth"),
            "nParticles": lambda objs: NodeUtils.list_transforms(
                objs, type="nParticle"
            ),
            "nRigids": lambda objs: NodeUtils.list_transforms(objs, type="nRigid"),
            "Particles": lambda objs: NodeUtils.list_transforms(objs, type="particle"),
            "Rigid Bodies": lambda objs: NodeUtils.list_transforms(
                objs, type="rigidBody"
            ),
            "Rigid Constraints": lambda objs: (
                cmds.ls(objs, type="rigidConstraint") or []
            ),
            "Sculpts": lambda objs: NodeUtils.list_transforms(
                objs, type=["implicitSphere", "sculpt"]
            ),
            "Strokes": lambda objs: NodeUtils.list_transforms(objs, type="stroke"),
            "Wires": lambda objs: cmds.ls(objs, type="wire") or [],
        },
        "Geometry": {
            "All Geometry": lambda objs: Selection._select_geometry(objs),
            "Hidden Geometry": lambda objs: Selection._select_hidden_geometry(objs),
            "Non-Selectable Geometry": lambda objs: (
                Selection._select_unselectable_geometry(objs)
            ),
            "NURBS Curves": lambda objs: NodeUtils.list_transforms(
                objs, type="nurbsCurve"
            ),
            "NURBS Surfaces": lambda objs: cmds.ls(objs, type="nurbsSurface") or [],
            "Polygon Meshes": lambda objs: NodeUtils.list_transforms(objs, type="mesh"),
            "Single-Instance Geometry": lambda objs: (
                Selection._select_single_instance_geometry(objs)
            ),
            "Templated Geometry": lambda objs: Selection._select_templated_geometry(
                objs
            ),
        },
        "Hierarchy": {
            "Ancestors": lambda objs: Selection.select_hierarchy_above(objs),
            "Children": lambda objs: Selection.select_children(objs),
            "Descendants": lambda objs: Selection.select_hierarchy_below(objs),
            "Groups": lambda objs: [obj for obj in objs if NodeUtils.is_group(obj)],
        },
        "Scene": {
            "Assets": lambda objs: (
                cmds.ls(objs, type=["container", "dagContainer"]) or []
            ),
            "Cameras": lambda objs: NodeUtils.list_transforms(objs, cameras=True),
            "Image Planes": lambda objs: cmds.ls(objs, type="imagePlane") or [],
            "Lights": lambda objs: NodeUtils.list_transforms(objs, lights=True),
            "Locators": lambda objs: Selection._select_locators(objs),
            "Keyed Locators": lambda objs: Selection._select_keyed_locators(objs),
            "Transforms": lambda objs: cmds.ls(objs, type="transform") or [],
        },
        # UV handlers wrap MEL UV-component selection commands. The MEL itself
        # reads Maya's live selection (the user's selected meshes/components);
        # `objs` is unused. `_select_uv_components` restores the pre-MEL
        # selection before returning so `_apply_selection_mode` can apply
        # replace/add/remove cleanly — matching the pure-handler contract used
        # by the rest of `_SELECTION_CONFIG`.
        "UV": {
            "Back-Facing": lambda objs: Selection._select_uv_components(
                "selectUVFaceOrientationComponents {} 0 2 1"
            ),
            "Front-Facing": lambda objs: Selection._select_uv_components(
                "selectUVFaceOrientationComponents {} 0 1 1"
            ),
            "Overlapping": lambda objs: Selection._select_uv_components(
                "selectUVOverlappingComponents 1 0"
            ),
            "Non-Overlapping": lambda objs: Selection._select_uv_components(
                "selectUVOverlappingComponents 0 0"
            ),
            "Texture Borders": lambda objs: Selection._select_uv_components(
                'selectUVBorderComponents {} "" 1'
            ),
            "Unmapped": lambda objs: Selection._select_uv_components(
                "selectUnmappedFaces"
            ),
        },
    }

    @staticmethod
    def select_by_type(
        selection_type: str,
        objects: List[Union[str, object]] = None,
        mode: str = "replace",
        scope: str = None,
    ) -> List[object]:
        """Select objects by type with comprehensive type support.

        Parameters:
            selection_type (str): The type of objects to select
            objects (List[Union[str, object]], optional): Objects to filter from. If None, uses current selection or all scene objects
            mode (str): Selection mode - "replace", "add", or "remove"
            scope (str, optional): With *objects* None: draw them from
                :meth:`objects_in_scope` (``"all"``, ``"selected"`` or ``"visible"``).

        Returns:
            List[object]: Selected objects
        """
        if objects is None:
            objects = (
                Selection.objects_in_scope(scope)
                if scope
                else cmds.ls(selection=True) or cmds.ls()
            )

        if not objects:
            return []

        # Check if selection_type is a category
        if selection_type in Selection._SELECTION_CONFIG:
            result = set()
            for handler in Selection._SELECTION_CONFIG[selection_type].values():
                try:
                    res = handler(objects)
                    if res:
                        result.update(res)
                except Exception:
                    continue

            Selection._apply_selection_mode(result, mode)
            return list(result)

        # Find handler in config for specific type
        handler = None
        for category in Selection._SELECTION_CONFIG.values():
            if selection_type in category:
                handler = category[selection_type]
                break

        if not handler:
            raise ValueError(f"Unknown selection type: {selection_type}")

        result = handler(objects)

        # Apply selection mode
        Selection._apply_selection_mode(result, mode)

        return list(result) if isinstance(result, set) else result

    @staticmethod
    def select_children(objects: List[Union[str, object]]) -> Set[object]:
        """Select the immediate children of the given objects.

        Unlike ``select_hierarchy_below`` which returns *all* descendants,
        this method returns only the direct children one level below.

        Parameters:
            objects (List[Union[str, object]]): Parent objects to get children from.

        Returns:
            Set[object]: Immediate child transforms.
        """
        result = set()
        for obj in objects:
            children = cmds.listRelatives(
                obj, children=True, type="transform", path=True
            )
            if children:
                result.update(children)
        return result

    @staticmethod
    def select_hierarchy_above(objects: List[Union[str, object]]) -> Set[object]:
        """Select all parent objects in the hierarchy above the given objects.

        Parameters:
            objects (List[Union[str, object]]): Objects to get parents from

        Returns:
            Set[object]: All parent objects
        """
        result = set()
        for obj in objects:
            current = obj
            while current:
                parent = cmds.listRelatives(
                    current, parent=True, type="transform", path=True
                )
                if parent:
                    parent = parent[0]
                    result.add(parent)
                    current = parent
                else:
                    break
        return result

    @staticmethod
    def select_hierarchy_below(objects: List[Union[str, object]]) -> Set[object]:
        """Select all child objects in the hierarchy below the given objects.

        Parameters:
            objects (List[Union[str, object]]): Objects to get children from

        Returns:
            Set[object]: All child objects
        """
        result = set()
        for obj in objects:
            children = cmds.listRelatives(
                obj, allDescendents=True, type="transform", path=True
            )
            if children:
                result.update(children)
        return result

    @staticmethod
    def _select_geometry(objects: List[Union[str, object]]) -> List[object]:
        """Select all geometry excluding locators."""
        shapes = cmds.ls(objects, geometry=True) or []
        rel = cmds.listRelatives(shapes, parent=True, path=True) or []
        return [obj for obj in rel if not NodeUtils.is_locator(obj)]

    @staticmethod
    def _select_locators(objects: List[Union[str, object]]) -> Set[object]:
        """Select locator objects."""
        shapes = cmds.ls(objects, exactType="locator") or []
        parents = cmds.listRelatives(shapes, parent=True, path=True) or []
        return set(parents)

    @staticmethod
    def _select_keyed_locators(objects: List[Union[str, object]]) -> Set[object]:
        """Select locators that have animation keys."""
        shapes = cmds.ls(objects, exactType="locator") or []
        result = set()
        for shape in shapes:
            parent = NodeUtils.get_parent(shape)
            if (
                parent
                and (cmds.keyframe(parent, query=True, keyframeCount=True) or 0) > 0
            ):
                result.add(parent)
        return result

    @staticmethod
    def _select_hidden_geometry(objects: List[Union[str, object]]) -> Set[object]:
        """Select hidden geometry."""
        geometry = cmds.ls(objects, geometry=True) or []
        result = set()
        for geo in geometry:
            parent = NodeUtils.get_parent(geo)
            if parent and not cmds.getAttr(f"{parent}.visibility"):
                result.add(parent)
        return result

    @staticmethod
    def _select_templated_geometry(objects: List[Union[str, object]]) -> Set[object]:
        """Select templated geometry."""
        geometry = cmds.ls(objects, geometry=True) or []
        result = set()
        for geo in geometry:
            parent = NodeUtils.get_parent(geo)
            if (
                parent
                and Attributes.has_attr(parent, "template")
                and cmds.getAttr(f"{parent}.template")
            ):
                result.add(parent)
        return result

    @staticmethod
    def _select_unselectable_geometry(
        objects: List[Union[str, object]],
    ) -> Set[object]:
        """Select unselectable geometry."""
        geometry = cmds.ls(objects, geometry=True) or []
        result = set()
        for geo in geometry:
            parent = NodeUtils.get_parent(geo)
            if (
                parent
                and cmds.getAttr(f"{parent}.overrideEnabled")
                and cmds.getAttr(f"{parent}.overrideDisplayType") == 2
            ):
                result.add(parent)
        return result

    @staticmethod
    def _select_single_instance_geometry(
        objects: List[Union[str, object]],
    ) -> List[object]:
        """Select geometry that has single instances."""
        geometry = cmds.ls(objects, geometry=True) or []
        return NodeUtils.filter_duplicate_instances(geometry)

    @staticmethod
    def _select_uv_components(mel_command: str) -> List[object]:
        """Run a MEL UV-component selection command and return the resulting components.

        UV-component selection commands (e.g. ``selectUVOverlappingComponents``)
        read Maya's live selection (the user's selected meshes) and replace it
        with the matched components. We restore the pre-MEL selection before
        returning so ``select_by_type``'s mode handling can apply
        replace/add/remove cleanly on top of the user's original selection —
        matching the pure-handler contract of other ``_SELECTION_CONFIG``
        entries.
        """
        original = cmds.ls(selection=True) or []
        mel.eval(mel_command)
        result = cmds.ls(selection=True) or []
        if original:
            cmds.select(original, replace=True)
        else:
            cmds.select(clear=True)
        return result

    @staticmethod
    def _select_animated_objects(objects: List[Union[str, object]]) -> Set[object]:
        """Select objects with animation keys."""
        transforms = cmds.ls(objects, type="transform") or []
        return {
            obj
            for obj in transforms
            if (cmds.keyframe(obj, query=True, keyframeCount=True) or 0) > 0
        }

    @staticmethod
    def _apply_selection_mode(
        objects: Union[List[object], Set[object]], mode: str
    ) -> None:
        """Apply the selection mode to the given objects.

        Parameters:
            objects: Objects to select
            mode (str): Selection mode - "replace", "add", or "remove"
        """
        objs = list(objects)
        if not objs:
            if mode == "replace":
                cmds.select(clear=True)
            return
        if mode == "add":
            cmds.select(objs, add=True)
        elif mode == "remove":
            cmds.select(objs, deselect=True)
        else:  # replace
            cmds.select(objs, replace=True)

    # ------------------------------------------------------------------
    # Rig selection mode
    # ------------------------------------------------------------------

    RIG_SELECTION_MODES = ("rig", "normal", "meshes_only")
    _CONTROL_SELECT_TYPES = ("nurbsCurve", "joint")

    @staticmethod
    def rig_locked_meshes() -> List[str]:
        """Mesh shapes a rig makes unpickable: their display type is DRIVEN
        (a rig control's display switch, e.g. the tube rig's ``meshDisplay``),
        not set by hand -- so an artist's own reference-locked mesh is never
        touched.

        One query for the whole scene: the Selection panel re-reads the mode
        each time it shows.
        """
        shapes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []
        if not shapes:
            return []
        pairs = (
            cmds.listConnections(
                [f"{s}.overrideDisplayType" for s in shapes],
                source=True,
                destination=False,
                connections=True,
            )
            or []
        )
        # connections=True: (queried plug, source) pairs, the plug's node
        # named as the ls of it would be, not necessarily long.
        driven = set(
            cmds.ls([p.split(".", 1)[0] for p in pairs[0::2]], long=True) or []
        )
        return [s for s in shapes if s in driven]

    @classmethod
    def get_rig_selection_mode(cls) -> str:
        """The live mode (see :meth:`set_rig_selection_mode`); ``"rig"`` when
        the scene holds no rig-locked mesh."""
        # selectType is a GUI-session preference: batch Maya answers None.
        if all(
            cmds.selectType(query=True, **{flag: True}) is False
            for flag in cls._CONTROL_SELECT_TYPES
        ):
            return "meshes_only"
        locked = cls.rig_locked_meshes()
        if locked and not any(cmds.getAttr(f"{s}.overrideEnabled") for s in locked):
            return "normal"
        return "rig"

    @classmethod
    @CoreUtils.undoable
    def set_rig_selection_mode(cls, mode: str) -> List[str]:
        """How object picking treats rigged parts.

        Parameters:
            mode (str): ``"rig"`` -- as the rigs author it: rigged meshes are
                display-locked so a click or marquee grabs their controls.
                ``"normal"`` -- rigged meshes pick like any mesh.
                ``"meshes_only"`` -- as normal, and control curves and joints
                are masked out of picking (Maya's global ``selectType``).

        The lock is scene state (saved with the file; one undo step puts every
        mesh's back) and a rig re-locks its mesh when it re-binds; the curve /
        joint mask is a session preference.

        Returns:
            (list) The rig-locked mesh shapes the mode was applied to.

        Raises:
            ValueError: Unknown *mode*.
        """
        if mode not in cls.RIG_SELECTION_MODES:
            raise ValueError(
                f"Invalid mode: {mode!r}. Expected one of {cls.RIG_SELECTION_MODES}."
            )
        locked = cls.rig_locked_meshes()
        for shape in locked:
            try:
                cmds.setAttr(f"{shape}.overrideEnabled", mode == "rig")
            except RuntimeError:  # locked or driven: leave it as authored
                cls.logger.warning(f"Could not change the display lock on {shape}.")
        cmds.selectType(
            **{flag: mode != "meshes_only" for flag in cls._CONTROL_SELECT_TYPES}
        )
        return locked

    # ------------------------------------------------------------------
    # The selection panel's operations (twins in blendertk's Selection)
    # ------------------------------------------------------------------

    @staticmethod
    def objects_in_scope(scope: str = "all") -> List[str]:
        """The objects a selection filter draws from. Twin of blendertk's.

        Parameters:
            scope (str): ``"all"`` (every node in the scene), ``"selected"`` or
                ``"visible"``.

        Raises:
            ValueError: An unknown *scope*.
        """
        if scope == "selected":
            return cmds.ls(selection=True) or []
        if scope == "visible":
            return cmds.ls(visible=True) or []
        if scope == "all":
            return cmds.ls() or []
        raise ValueError(
            f"Unknown scope {scope!r}: use 'all', 'selected' or 'visible'."
        )

    @staticmethod
    def reorder_selection(
        objects=None, method: str = "name", reverse: bool = False
    ) -> List[str]:
        """Reselect *objects* in the order *method* sorts them
        (:meth:`CoreUtils.reorder_objects`), so tools reading the selection
        order see it. Twin of blendertk's (which records the order on its
        ``SelectionOrder`` tracker).

        Returns:
            list: The objects in their new order; ``[]`` with none.
        """
        nodes = (
            CoreUtils.as_strings(objects)
            if objects is not None
            else cmds.ls(selection=True, flatten=True) or []
        )
        if not nodes:
            return []
        reordered = CoreUtils.reorder_objects(nodes, method=method, reverse=reverse)
        if reordered:
            cmds.select(reordered, replace=True)
        return list(reordered or [])

    #: Convert To: conversion name -> the MEL Maya's Select > Convert Selection
    #: menu runs for it (``None``: Border Edges, from ``Components``).
    _CONVERSIONS = {
        "vertices": "PolySelectConvert 3",
        "vertex_faces": "PolySelectConvert 5",
        "vertex_perimeter": "ConvertSelectionToVertexPerimeter",
        "edges": "PolySelectConvert 2",
        "edge_loop": "SelectEdgeLoopSp",
        "edge_ring": "SelectEdgeRingSp",
        "contained_edges": "PolySelectConvert 20",
        "edge_perimeter": "ConvertSelectionToEdgePerimeter",
        "border_edges": None,
        "faces": "PolySelectConvert 1",
        "face_path": 'polySelectEdges "edgeRing"',
        "contained_faces": "PolySelectConvert 10",
        "face_perimeter": "polySelectFacePerimeter",
        "uvs": "PolySelectConvert 4",
        "uv_shell": "polySelectBorderShell 0",
        "uv_shell_border": "polySelectBorderShell 1",
        "uv_perimeter": "ConvertSelectionToUVPerimeter",
        "uv_edge_loop": 'polySelectEdges "edgeUVLoopOrBorder"',
        "shell": "polyConvertToShell",
        "shell_border": "polyConvertToShellBorder",
    }

    @classmethod
    def get_conversion_types(cls) -> List[str]:
        """The conversions :meth:`convert_selection` knows, in menu order. Twin
        of blendertk's (which has no vertex-face or UV component)."""
        return list(cls._CONVERSIONS)

    @classmethod
    def convert_selection(cls, conversion: str) -> List[str]:
        """Convert the component selection (Maya's Select > Convert Selection).
        Twin of blendertk's ``Selection.convert_selection``.

        Parameters:
            conversion (str): One of :meth:`get_conversion_types`.

        Returns:
            list: The selection after; ``[]`` with nothing selected.

        Raises:
            ValueError: An unknown *conversion*, or nothing to take border
                edges of.
        """
        if conversion not in cls._CONVERSIONS:
            raise ValueError(f"Unknown conversion {conversion!r}.")
        selection = cmds.ls(selection=True) or []
        if not selection:
            return []
        command = cls._CONVERSIONS[conversion]
        if command is None:
            from mayatk.core_utils.components import Components

            edges = Components.get_components(selection, "edges")
            if not edges:
                raise ValueError("No edges to take the border of: select a mesh or faces.")
            cmds.select(Components.get_border_components(edges), replace=True)
        else:
            mel.eval(command)
        return cmds.ls(selection=True) or []

    #: Selection constraints -> the ``polySelectConstraint`` flags each owns.
    #: Each is its own flag on Maya's one global constraint state (setting one
    #: leaves the others), so several can be on at once; Border is two flags
    #: because the Modeling Toolkit sets both.
    _CONSTRAINT_FLAGS = {
        "angle": ("anglePropagation",),
        "border": ("border", "borderPropagation"),
        "edge_loop": ("loopPropagation",),
        "edge_ring": ("ringPropagation",),
        "shell": ("shell",),
        "uv_edge_loop": ("uvEdgeLoopPropagation",),
    }

    @classmethod
    def get_selection_constraints(cls) -> List[str]:
        """The selection constraints now on: Maya keeps each on, constraining
        what drag-selection picks up, until it is switched off. Twin of
        blendertk's (``None`` there: Blender keeps none on)."""
        return [
            name
            for name, flags in cls._CONSTRAINT_FLAGS.items()
            if cmds.polySelectConstraint(q=True, **{flags[0]: True})
        ]

    @classmethod
    def set_selection_constraint(cls, constraint: str, state: bool = True) -> List[str]:
        """Switch one selection constraint on or off; the others stay as they
        are. Twin of blendertk's (a one-shot expansion there).

        Parameters:
            constraint (str): ``"angle"``, ``"border"``, ``"edge_loop"``,
                ``"edge_ring"``, ``"shell"`` or ``"uv_edge_loop"``.
            state (bool): On or off.

        Returns:
            list: The constraints now on.

        Raises:
            ValueError: An unknown *constraint*.
        """
        flags = cls._CONSTRAINT_FLAGS.get(constraint)
        if flags is None:
            raise ValueError(f"Unknown selection constraint {constraint!r}.")
        cmds.polySelectConstraint(**{flag: bool(state) for flag in flags})
        # Keep the Modeling Toolkit's own constraint field honest when it is
        # up. The proc exists only once drInit.mel is sourced; probing with
        # ``exists`` first keeps the script editor quiet (a failing mel.eval
        # echoes "Error:" there even when Python catches it).
        if mel.eval('exists "dR_updateSelConstField"'):
            try:
                mel.eval("dR_updateSelConstField")
            except RuntimeError:
                pass  # its textField torn down since: nothing to refresh
        return cls.get_selection_constraints()

    #: Select Nth's paths -> ``Components.get_edge_path``'s path types.
    _EDGE_PATHS = {
        "edge_ring": "edgeRing",
        "edge_loop": "edgeLoop",
        "edge_loop_path": "edgeLoopPath",
        "shortest_path": None,
        "border_edges": None,
    }

    @classmethod
    def select_edge_path(
        cls, components=None, path: str = "edge_loop", step: int = 1
    ) -> List[str]:
        """Select the edge path through *components*, keeping every *step*-th
        component. Twin of blendertk's ``Selection.select_edge_path``.

        Parameters:
            components: Edges or vertices; ``None``: the ordered selection.
            path (str): ``"edge_ring"``, ``"edge_loop"``, ``"edge_loop_path"``
                (the loop between two picks), ``"shortest_path"`` (between two
                picks) or ``"border_edges"``.
            step (int): Keep every *step*-th component.

        Returns:
            list: The components selected; ``[]`` with nothing selected.

        Raises:
            ValueError: An unknown *path*, or a two-pick path without two picks.
        """
        from mayatk.core_utils.components import Components

        if path not in cls._EDGE_PATHS:
            raise ValueError(f"Unknown edge path {path!r}.")
        nodes = (
            CoreUtils.as_strings(components)
            if components is not None
            # the pick order where Maya tracks it, else the selection
            else cmds.ls(orderedSelection=True) or cmds.ls(selection=True) or []
        )
        if not nodes:
            return []
        if path == "shortest_path":
            result = Components.get_shortest_path(nodes)
        elif path == "border_edges":
            result = Components.get_border_components(
                Components.get_components(nodes, "edges")
            )
        else:
            result = Components.get_edge_path(nodes, cls._EDGE_PATHS[path])
        result = list(result or [])[:: max(int(step), 1)]
        if result:
            cmds.select(result, replace=True)
        return result

    #: Select Similar's object metrics -> the ``polyEvaluate`` flag each compares.
    _SIMILAR_METRICS = {
        "vertex": "vertex",
        "edge": "edge",
        "face": "face",
        "triangle": "triangle",
        "shell": "shell",
        "uvcoord": "uvcoord",
        "area": "area",
        "world_area": "worldArea",
        "bounding_box": "boundingBox",
    }

    @classmethod
    def select_similar(
        cls, objects=None, tolerance: float = 0.0, include_original: bool = False, **metrics
    ):
        """Select what is similar to the selection. Twin of blendertk's
        ``Selection.select_similar``.

        * Objects: meshes alike by the checked *metrics*
          (:meth:`EditUtils.get_similar_mesh`).
        * A UV / UV-shell selection: the shells of the same topology and shape
          across the hilited meshes (``UvUtils.get_similar_uv_shells``), what the
          UV panel's Stack (Similar) stacks; nothing moves.
        * Other components: Maya's own Select Similar (``doSelectSimilar``).

        Parameters:
            objects: The reference; ``None``: the selection.
            tolerance (float): The allowed difference.
            include_original (bool): Keep the reference selected.
            **metrics (bool): ``vertex``, ``edge``, ``face``, ``triangle``,
                ``shell``, ``uvcoord``, ``area``, ``world_area``, ``bounding_box``.

        Returns:
            tuple: ``(scope, matched)``: scope ``"objects"``, ``"uv_shells"`` or
                ``"components"``; *matched* what was found beyond the reference
                (``[]`` for none, and with nothing selected).

        Raises:
            TypeError: An unknown metric.
            ValueError: Maya's Select Similar does not take the component type.
        """
        unknown = set(metrics) - set(cls._SIMILAR_METRICS)
        if unknown:
            raise TypeError(f"Unknown Select Similar metrics: {sorted(unknown)}")
        selection = (
            CoreUtils.as_strings(objects)
            if objects is not None
            else cmds.ls(selection=True) or []
        )
        if not selection:
            return "objects", []
        if cmds.filterExpand(selection, selectionMask=35):  # UVs (UV / UV-shell mode)
            from mayatk.uv_utils._uv_utils import UvUtils

            # The meshes being edited: hilited in component mode; else the
            # shells' own objects (a UV selection made by script).
            candidates = cmds.ls(hilite=True) or cmds.ls(selection, objectsOnly=True)
            shells = UvUtils.get_similar_uv_shells(selection, candidates, tolerance=tolerance)
            uvs = [uv for shell in shells or () for uv in shell]
            if uvs:
                # one mode flag per call: add and replace are mutually exclusive
                if include_original:
                    cmds.select(uvs, add=True)
                else:
                    cmds.select(uvs, replace=True)
            return "uv_shells", uvs
        if cmds.filterExpand(selection, selectionMask=(31, 32, 34, 70)):
            try:
                mel.eval(f"doSelectSimilar 1 {{{tolerance}}};")
            except RuntimeError as error:
                # doSelectSimilar takes only some component types; it raises
                # ("Cannot convert data of type float[] ...") on the others.
                from mayatk.core_utils.components import Components

                kind = Components.get_component_type(selection[0], "plural")
                raise ValueError(
                    f"Select Similar does not take {kind or 'this selection'}: "
                    "select faces or objects."
                ) from error
            return "components", cmds.ls(selection=True) or []
        from mayatk.edit_utils._edit_utils import EditUtils

        transforms = cmds.ls(selection, objectsOnly=True, type="transform") or []
        if not transforms:
            return "objects", []
        flags = {cls._SIMILAR_METRICS[name]: True for name, on in metrics.items() if on}
        result = EditUtils.get_similar_mesh(
            transforms, tolerance=tolerance, inc_orig=include_original, select=True, **flags
        )
        # Compared against the originals, not the raw return, so a no-match reads
        # as one whether or not include_original put the reference back in.
        # Guarded: ``get_similar_mesh`` returns None for one object with no match.
        found = ptk.make_iterable(result) if result else []
        originals = set(cmds.ls(transforms, long=True) or [])
        matched = [
            node for node in (cmds.ls(found, long=True) or [] if found else [])
            if node not in originals
        ]
        return "objects", matched

    @staticmethod
    def select_island(
        faces=None, range_x: float = 0.05, range_y: float = 0.05, range_z: float = 0.05,
        delimit=None,
    ) -> List[str]:
        """Select the contiguous faces, around the selected ones, whose normals
        are within the ranges of a selected face's normal. Twin of blendertk's
        ``Selection.select_island``, which also stops at *delimit* boundaries
        (Blender's Select Linked delimiters); Maya's island has none, so
        *delimit* is accepted for the shared signature and not used.

        Returns:
            list: The faces selected; ``[]`` with no face selected.
        """
        from mayatk.core_utils.components import Components

        nodes = (
            CoreUtils.as_strings(faces)
            if faces is not None
            else cmds.ls(selection=True) or []
        )
        found = (
            Components.get_components(nodes, component_type="faces", flatten=True)
            if nodes
            else []
        )
        selected = cmds.ls(found, flatten=True) or [] if found else []
        if not selected:
            return []
        similar = Components.get_faces_with_similar_normals(
            selected, range_x=range_x, range_y=range_y, range_z=range_z
        )
        islands = Components.get_contiguous_islands(similar)
        seeds = set(selected)
        island = [face for faces_ in islands if faces_ & seeds for face in faces_]
        if island:
            cmds.select(island, replace=True)
        return island

    #: The display-override attributes Toggle Selectability writes.
    _SELECTABILITY_ATTRS = (
        "overrideEnabled",
        "overrideDisplayType",
        "useOutlinerColor",
        "outlinerColor",
    )

    @classmethod
    @CoreUtils.undoable
    def toggle_selectability(cls, objects=None):
        """Toggle whether *objects* can be picked in the viewport: Maya's
        reference display (``overrideDisplayType`` 2), with a teal outliner color
        while unselectable. One state for all: selectable again when every one
        is unselectable, else unselectable. One undo step. Twin of blendertk's
        (``hide_select`` there).

        Returns:
            (tuple/list): ``(selectable, objects)``, the new state; ``[]`` with
                nothing to toggle (objects whose attributes are locked are
                skipped).
        """
        nodes = (
            CoreUtils.as_strings(objects)
            if objects is not None
            else cmds.ls(selection=True) or []
        )
        nodes = cmds.ls(nodes, objectsOnly=True, flatten=True) or [] if nodes else []

        def writable(node, attr):
            return cmds.attributeQuery(attr, node=node, exists=True) and not cmds.getAttr(
                f"{node}.{attr}", lock=True
            )

        nodes = [n for n in nodes if all(writable(n, a) for a in cls._SELECTABILITY_ATTRS)]
        if not nodes:
            return []
        selectable = all(
            cmds.getAttr(f"{n}.overrideEnabled")
            and cmds.getAttr(f"{n}.overrideDisplayType") == 2
            for n in nodes
        )
        for n in nodes:
            if selectable:
                cmds.setAttr(f"{n}.overrideDisplayType", 0)  # normal
                cmds.setAttr(f"{n}.useOutlinerColor", 0)
            else:
                cmds.setAttr(f"{n}.overrideEnabled", 1)
                cmds.setAttr(f"{n}.overrideDisplayType", 2)  # reference
                cmds.setAttr(f"{n}.useOutlinerColor", 1)
                cmds.setAttr(f"{n}.outlinerColor", 0.3, 0.6, 0.6, type="double3")
        return selectable, nodes

    @staticmethod
    def get_ignore_backfacing() -> bool:
        """True while viewport picks skip what faces away from the camera
        (Maya's camera-based selection, ``selectPref -useDepth``). Twin of
        blendertk's (X-ray off there)."""
        return bool(cmds.selectPref(q=True, useDepth=True))

    @staticmethod
    def set_ignore_backfacing(state: bool = True) -> bool:
        """Turn camera-based selection on or off; returns the state set."""
        cmds.selectPref(useDepth=bool(state))
        return bool(state)

    @staticmethod
    def get_available_selection_types() -> List[str]:
        """Get a list of all available selection types.

        Returns:
            List[str]: Available selection type names
        """
        categories = Selection.get_selection_categories()
        return sorted([item for items in categories.values() for item in items])

    @staticmethod
    def get_selection_categories() -> dict:
        """Get a dictionary of selection types organized by category.

        Returns:
            dict: Dictionary of selection categories and their types
        """
        return {
            category: list(types.keys())
            for category, types in Selection._SELECTION_CONFIG.items()
        }


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
