# !/usr/bin/python
# coding=utf-8
import numpy as np
import functools
from typing import Optional

try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.node_utils._node_utils import NodeUtils


class ExplodedView:
    exploded_objects = {}

    def __init__(self, objects: Optional[list] = None, **kwargs):
        self._objects = objects
        self._kwargs = kwargs

    @property
    def objects(self) -> list:
        """Return assigned objects or fallback to current selection."""
        return self._objects if self._objects is not None else pm.ls(sl=True)

    @objects.setter
    def objects(self, value: list):
        self._objects = value

    def _inject_objects_if_given(fn):
        """Injects 'objects' into self.objects only if explicitly provided (even if empty)."""

        @functools.wraps(fn)
        def wrapper(self, *args, objects: Optional[list] = None, **kwargs):
            if objects is not None:
                self.objects = objects
            return fn(self, *args, **kwargs)

        return wrapper

    def _get_target_objects(
        self,
        exploded: bool = False,
        unexploded: bool = False,
    ) -> list:
        """Returns filtered child objects based on explosion state.

        Parameters:
            exploded (bool): If True, return only exploded objects.
            unexploded (bool): If True, return only unexploded objects.

        Returns:
            list: Filtered list of child objects.
        """
        if not self.objects:
            pm.warning("No objects provided or selected.")
            return []

        children = NodeUtils.get_unique_children(self.objects)

        if exploded:
            result = [
                obj
                for obj in children
                if pm.attributeQuery("original_position", node=obj, exists=True)
            ]
        elif unexploded:
            result = [
                obj
                for obj in children
                if not pm.attributeQuery("original_position", node=obj, exists=True)
            ]
        else:
            result = children

        if not result:
            state = (
                "exploded" if exploded else "unexploded" if unexploded else "filtered"
            )
            pm.warning(f"No {state} target objects found.")

        return result

    @classmethod
    def calculate_repulsive_force_vectorized(cls, positions, sizes, scale=0.05):
        """Vectorized calculation of repulsive forces between objects."""
        epsilon = 1e-6
        n = len(positions)
        force_matrix = np.zeros((n, n, 3))

        for i in range(n):
            diff = positions - positions[i]
            dist_squared = np.sum(diff**2, axis=1) + epsilon
            force_magnitude = (sizes[i] * sizes) / dist_squared
            force_magnitude *= scale
            normalized_diff = diff / np.sqrt(dist_squared)[:, np.newaxis]
            force_matrix[:, i, :] = normalized_diff * force_magnitude[:, np.newaxis]

        return np.sum(force_matrix, axis=1)

    def arrange_objects(
        self,
        nodes: list,
        convergence_threshold: float = 1e-4,
        max_iterations: int = 1000,
        max_movement: float = 1.0,
    ) -> int:
        """Arranges a list of objects in 3D space to avoid overlap."""
        if not nodes:
            pm.warning("arrange_objects: no nodes to arrange.")
            return 0

        node_group_key = tuple(sorted([node.name() for node in nodes]))
        if node_group_key in self.exploded_objects:
            for node in nodes:
                cached_position = self.exploded_objects[node_group_key][node.name()]
                pm.move(
                    cached_position[0],
                    cached_position[1],
                    cached_position[2],
                    node,
                    absolute=True,
                )
            return 0

        node_data = [
            XformUtils.get_bounding_box(node, "center|maxsize") for node in nodes
        ]
        positions = np.array([data[0] for data in node_data])
        sizes = np.array([data[1] for data in node_data])

        iteration_count = 0
        converged = False

        while not converged and iteration_count < max_iterations:
            forces = self.calculate_repulsive_force_vectorized(positions, sizes)
            movements = np.clip(forces, -max_movement, max_movement)
            positions += movements

            for idx, node in enumerate(nodes):
                pm.move(
                    movements[idx][0],
                    movements[idx][1],
                    movements[idx][2],
                    node,
                    relative=True,
                )

            if np.linalg.norm(movements) < convergence_threshold:
                converged = True

            iteration_count += 1

        self.exploded_objects[node_group_key] = {
            node.name(): pm.xform(node, query=True, translation=True, worldSpace=True)
            for node in nodes
        }
        return iteration_count

    @CoreUtils.undoable
    @_inject_objects_if_given
    def explode(self):
        """Explode the objects.

        Parameters:
            objects (decorator): Decorator to inject objects if provided.
        """
        objects = self._get_target_objects(unexploded=True)

        for obj in objects:
            pos = pm.xform(obj, query=True, translation=True, worldSpace=True)
            NodeUtils.set_node_attributes(obj, create=True, original_position=pos)

        self.arrange_objects(objects)

    @CoreUtils.undoable
    @_inject_objects_if_given
    def un_explode(self):
        """Un-explode the objects.

        Parameters:
            objects (decorator): Decorator to inject objects if provided.
        """
        objects = self._get_target_objects(exploded=True)

        for obj in objects:
            pos = pm.getAttr(obj.original_position)
            pm.move(pos[0], pos[1], pos[2], obj, absolute=True)
            pm.deleteAttr(obj, attribute="original_position")

    @_inject_objects_if_given
    def toggle_explode(self):
        """Toggle explode state of the objects.

        Parameters:
            objects (decorator): Decorator to inject objects if provided.
        """
        objects = self._get_target_objects()

        if all(
            pm.attributeQuery("original_position", node=obj, exists=True)
            for obj in objects
        ):
            self.un_explode()
        else:
            self.explode()

    @CoreUtils.undoable
    def un_explode_all(self):
        """Un-explode all"""
        all_objects_with_original_position = pm.ls("*.original_position")
        for obj_attr in all_objects_with_original_position:
            obj = obj_attr.node()
            pos = pm.getAttr(obj.original_position)
            pm.move(pos[0], pos[1], pos[2], obj, absolute=True)
            pm.deleteAttr(obj, attribute="original_position")


class ExplodedViewSlots(ExplodedView):
    """Exploded View Slots"""

    def __init__(self, switchboard):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.exploded_view

    def b000(self):
        """Explode button"""
        self.explode()

    def b001(self):
        """Un-explode selected button"""
        self.un_explode()

    def b002(self):
        """Un-explode all button"""
        self.un_explode_all()

    def b003(self):
        """Toggle Exlode"""
        self.toggle_explode()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("exploded_view", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
