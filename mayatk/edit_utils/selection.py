# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

from typing import List, Union, Set

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes


class Selection(ptk.LoggingMixin, ptk.HelpMixin):
    """Utilities for advanced Maya selection operations."""

    _SELECTION_CONFIG = {
        "Animation": {
            "Animated Objects": lambda objs: Selection._select_animated_objects(objs),
            "Clusters": lambda objs: NodeUtils.list_transforms(objs, type="clusterHandle"),
            "Constraints": lambda objs: cmds.ls(objs, type="constraint") or [],
            "IK Handles": lambda objs: cmds.ls(objs, type=["ikHandle", "hikEffector"]) or [],
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
            "nParticles": lambda objs: NodeUtils.list_transforms(objs, type="nParticle"),
            "nRigids": lambda objs: NodeUtils.list_transforms(objs, type="nRigid"),
            "Particles": lambda objs: NodeUtils.list_transforms(objs, type="particle"),
            "Rigid Bodies": lambda objs: NodeUtils.list_transforms(objs, type="rigidBody"),
            "Rigid Constraints": lambda objs: cmds.ls(objs, type="rigidConstraint") or [],
            "Sculpts": lambda objs: NodeUtils.list_transforms(
                objs, type=["implicitSphere", "sculpt"]
            ),
            "Strokes": lambda objs: NodeUtils.list_transforms(objs, type="stroke"),
            "Wires": lambda objs: cmds.ls(objs, type="wire") or [],
        },
        "Geometry": {
            "All Geometry": lambda objs: Selection._select_geometry(objs),
            "Hidden Geometry": lambda objs: Selection._select_hidden_geometry(objs),
            "Non-Selectable Geometry": lambda objs: Selection._select_unselectable_geometry(
                objs
            ),
            "NURBS Curves": lambda objs: NodeUtils.list_transforms(objs, type="nurbsCurve"),
            "NURBS Surfaces": lambda objs: cmds.ls(objs, type="nurbsSurface") or [],
            "Polygon Meshes": lambda objs: NodeUtils.list_transforms(objs, type="mesh"),
            "Single-Instance Geometry": lambda objs: Selection._select_single_instance_geometry(
                objs
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
            "Assets": lambda objs: cmds.ls(objs, type=["container", "dagContainer"]) or [],
            "Cameras": lambda objs: NodeUtils.list_transforms(objs, cameras=True),
            "Image Planes": lambda objs: cmds.ls(objs, type="imagePlane") or [],
            "Lights": lambda objs: NodeUtils.list_transforms(objs, lights=True),
            "Locators": lambda objs: Selection._select_locators(objs),
            "Keyed Locators": lambda objs: Selection._select_keyed_locators(objs),
            "Transforms": lambda objs: cmds.ls(objs, type="transform") or [],
        },
    }

    @staticmethod
    def select_by_type(
        selection_type: str,
        objects: List[Union[str, object]] = None,
        mode: str = "replace",
    ) -> List[object]:
        """Select objects by type with comprehensive type support.

        Parameters:
            selection_type (str): The type of objects to select
            objects (List[Union[str, object]], optional): Objects to filter from. If None, uses current selection or all scene objects
            mode (str): Selection mode - "replace", "add", or "remove"

        Returns:
            List[object]: Selected objects
        """
        if objects is None:
            objects = cmds.ls(selection=True) or cmds.ls()

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
            children = cmds.listRelatives(obj, children=True, type="transform")
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
                parent = cmds.listRelatives(current, parent=True, type="transform")
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
            children = cmds.listRelatives(obj, allDescendents=True, type="transform")
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
            if parent and (cmds.keyframe(parent, query=True, keyframeCount=True) or 0) > 0:
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
            if parent and Attributes.has_attr(parent, "template") and cmds.getAttr(f"{parent}.template"):
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
