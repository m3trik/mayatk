# !/usr/bin/python
# coding=utf-8
from typing import List, Union, Set

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package
from mayatk.node_utils._node_utils import NodeUtils


class Selection(ptk.LoggingMixin, ptk.HelpMixin):
    """Utilities for advanced Maya selection operations."""

    _SELECTION_CONFIG = {
        "Animation": {
            "Animated Objects": lambda objs: Selection._select_animated_objects(objs),
            "Clusters": lambda objs: pm.listTransforms(objs, type="clusterHandle"),
            "Constraints": lambda objs: pm.ls(objs, type="constraint"),
            "IK Handles": lambda objs: pm.ls(objs, type=["ikHandle", "hikEffector"]),
            "Joints": lambda objs: pm.ls(objs, type="joint"),
        },
        "Dynamics": {
            "Brushes": lambda objs: pm.ls(objs, type="brush"),
            "Dynamic Constraints": lambda objs: pm.listTransforms(
                objs, type="dynamicConstraint"
            ),
            "Fluids": lambda objs: pm.listTransforms(objs, type="fluidShape"),
            "Follicles": lambda objs: pm.listTransforms(objs, type="follicle"),
            "Lattices": lambda objs: pm.listTransforms(objs, type="lattice"),
            "nCloths": lambda objs: pm.listTransforms(objs, type="nCloth"),
            "nParticles": lambda objs: pm.listTransforms(objs, type="nParticle"),
            "nRigids": lambda objs: pm.listTransforms(objs, type="nRigid"),
            "Particles": lambda objs: pm.listTransforms(objs, type="particle"),
            "Rigid Bodies": lambda objs: pm.listTransforms(objs, type="rigidBody"),
            "Rigid Constraints": lambda objs: pm.ls(objs, type="rigidConstraint"),
            "Sculpt Objects": lambda objs: pm.listTransforms(
                objs, type=["implicitSphere", "sculpt"]
            ),
            "Strokes": lambda objs: pm.listTransforms(objs, type="stroke"),
            "Wires": lambda objs: pm.ls(objs, type="wire"),
        },
        "Geometry": {
            "Geometry": lambda objs: Selection._select_geometry(objs),
            "Geometry (Hidden)": lambda objs: Selection._select_hidden_geometry(objs),
            "Geometry (Polygon)": lambda objs: pm.listTransforms(objs, type="mesh"),
            "Geometry (Single Instance)": lambda objs: Selection._select_single_instance_geometry(
                objs
            ),
            "Geometry (Templated)": lambda objs: Selection._select_templated_geometry(
                objs
            ),
            "Geometry (Un-Selectable)": lambda objs: Selection._select_unselectable_geometry(
                objs
            ),
            "NURBS (Curves)": lambda objs: pm.listTransforms(objs, type="nurbsCurve"),
            "NURBS (Surfaces)": lambda objs: pm.ls(objs, type="nurbsSurface"),
        },
        "Hierarchy": {
            "Groups": lambda objs: [obj for obj in objs if NodeUtils.is_group(obj)],
            "Hierarchy (above)": lambda objs: Selection.select_hierarchy_above(objs),
            "Hierarchy (below)": lambda objs: Selection.select_hierarchy_below(objs),
        },
        "Scene": {
            "Assets": lambda objs: pm.ls(objs, type=["container", "dagContainer"]),
            "Cameras": lambda objs: pm.listTransforms(objs, cameras=1),
            "Image Planes": lambda objs: pm.ls(objs, type="imagePlane"),
            "Lights": lambda objs: pm.listTransforms(objs, lights=1),
            "Locators": lambda objs: Selection._select_locators(objs),
            "Locators (Keyed)": lambda objs: Selection._select_keyed_locators(objs),
            "Transforms": lambda objs: pm.ls(objs, type="transform"),
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
            objects = pm.selected() or pm.ls()

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
                parent = pm.listRelatives(current, parent=True, type="transform")
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
            children = pm.listRelatives(obj, allDescendents=True, type="transform")
            if children:
                result.update(children)
        return result

    @staticmethod
    def _select_geometry(objects: List[Union[str, object]]) -> List[object]:
        """Select all geometry excluding locators."""
        shapes = pm.ls(objects, geometry=True)
        rel = pm.listRelatives(shapes, parent=True, path=True)
        return [obj for obj in rel if not NodeUtils.is_locator(obj)]

    @staticmethod
    def _select_locators(objects: List[Union[str, object]]) -> Set[object]:
        """Select locator objects."""
        shapes = pm.ls(objects, exactType="locator")
        return set(pm.listRelatives(shapes, parent=True, path=True))

    @staticmethod
    def _select_keyed_locators(objects: List[Union[str, object]]) -> Set[object]:
        """Select locators that have animation keys."""
        shapes = pm.ls(objects, exactType="locator")
        return set(
            [
                obj.getParent()
                for obj in shapes
                if pm.keyframe(obj.getParent(), query=True, keyframeCount=True) > 0
            ]
        )

    @staticmethod
    def _select_hidden_geometry(objects: List[Union[str, object]]) -> Set[object]:
        """Select hidden geometry."""
        geometry = pm.ls(objects, geometry=True)
        return set(
            [
                geo.getParent()
                for geo in geometry
                if not geo.getParent().visibility.get()
            ]
        )

    @staticmethod
    def _select_templated_geometry(objects: List[Union[str, object]]) -> Set[object]:
        """Select templated geometry."""
        geometry = pm.ls(objects, geometry=True)
        return set(
            [
                geo.getParent()
                for geo in geometry
                if hasattr(geo.getParent(), "template")
                and geo.getParent().template.get()
            ]
        )

    @staticmethod
    def _select_unselectable_geometry(
        objects: List[Union[str, object]] = None,
    ) -> Set[object]:
        """Select unselectable geometry."""
        geometry = pm.ls(geometry=True)
        return set(
            [
                geo.getParent()
                for geo in geometry
                if geo.getParent().overrideEnabled.get()
                and geo.getParent().overrideDisplayType.get() == 2
            ]
        )

    @staticmethod
    def _select_single_instance_geometry(
        objects: List[Union[str, object]],
    ) -> List[object]:
        """Select geometry that has single instances."""
        geometry = pm.ls(objects, geometry=True)
        return NodeUtils.filter_duplicate_instances(geometry)

    @staticmethod
    def _select_animated_objects(objects: List[Union[str, object]]) -> Set[object]:
        """Select objects with animation keys."""
        transforms = pm.ls(objects, type="transform")
        return set(
            [
                obj
                for obj in transforms
                if pm.keyframe(obj, query=True, keyframeCount=True) > 0
            ]
        )

    @staticmethod
    def _apply_selection_mode(
        objects: Union[List[object], Set[object]], mode: str
    ) -> None:
        """Apply the selection mode to the given objects.

        Parameters:
            objects: Objects to select
            mode (str): Selection mode - "replace", "add", or "remove"
        """
        if mode == "add":
            pm.select(objects, add=True)
        elif mode == "remove":
            pm.select(objects, deselect=True)
        else:  # replace
            pm.select(objects, replace=True)

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
