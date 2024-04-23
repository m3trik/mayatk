# !/usr/bin/python
# coding=utf-8
import numpy as np

try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)

# from this package:
from mayatk.core_utils import _core_utils
from mayatk import xform_utils
from mayatk import node_utils


class ExplodedView:
    exploded_objects = {}

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

    @classmethod
    def arrange_objects(
        cls, nodes, convergence_threshold=1e-4, max_iterations=1000, max_movement=1.0
    ):
        """Arranges a list of objects in 3D space to avoid overlap."""
        node_group_key = tuple(sorted([node.name() for node in nodes]))
        if node_group_key in cls.exploded_objects:
            for node in nodes:
                cached_position = cls.exploded_objects[node_group_key][node.name()]
                pm.move(
                    cached_position[0],
                    cached_position[1],
                    cached_position[2],
                    node,
                    absolute=True,
                )
            return 0

        node_data = [
            xform_utils.XformUtils.get_bounding_box(node, "center|maxsize")
            for node in nodes
        ]
        positions = np.array([data[0] for data in node_data])
        sizes = np.array([data[1] for data in node_data])

        iteration_count = 0
        converged = False

        while not converged and iteration_count < max_iterations:
            forces = cls.calculate_repulsive_force_vectorized(positions, sizes)
            movements = np.clip(forces, -max_movement, max_movement)
            positions += movements

            # Apply movements to nodes
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

        cls.exploded_objects[node_group_key] = {
            node.name(): pm.xform(node, query=True, translation=True, worldSpace=True)
            for node in nodes
        }
        return iteration_count

    @_core_utils.CoreUtils.undo
    def explode_selected(self):
        """Explode selected"""
        selection = node_utils.NodeUtils.get_unique_children(pm.ls(sl=True))
        for obj in selection:
            if obj.hasAttr("original_position"):
                selection.remove(obj)
                continue
            pos = pm.xform(obj, query=True, translation=True, worldSpace=True)
            node_utils.NodeUtils.set_node_attributes(
                obj, option_create=True, original_position=pos
            )

        self.arrange_objects(selection)

    @_core_utils.CoreUtils.undo
    def un_explode_selected(self):
        """Un-explode selected"""
        selection = node_utils.NodeUtils.get_unique_children(pm.ls(sl=True))
        for obj in selection:
            if pm.attributeQuery("original_position", node=obj, exists=True):
                pos = pm.getAttr(obj.original_position)
                pm.move(pos[0], pos[1], pos[2], obj, absolute=True)
                pm.deleteAttr(obj, attribute="original_position")

    @_core_utils.CoreUtils.undo
    def un_explode_all(self):
        """Un-explode all"""
        all_objects_with_original_position = pm.ls("*.original_position")
        for obj_attr in all_objects_with_original_position:
            obj = obj_attr.node()
            pos = pm.getAttr(obj.original_position)
            pm.move(pos[0], pos[1], pos[2], obj, absolute=True)
            pm.deleteAttr(obj, attribute="original_position")

    def toggle_explode(self):
        """Toggle explode"""
        selection = node_utils.NodeUtils.get_unique_children(pm.ls(sl=True))
        if selection:
            if pm.attributeQuery("original_position", node=selection[0], exists=True):
                self.un_explode_selected()
            else:
                self.explode_selected()


class ExplodedViewSlots(ExplodedView):
    def b000(self):
        """Explode button"""
        self.explode_selected()

    def b001(self):
        """Un-explode selected button"""
        self.un_explode_selected()

    def b002(self):
        """Un-explode all button"""
        self.un_explode_all()

    def b003(self):
        """Toggle Exlode"""
        self.toggle_explode()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = _core_utils.CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "exploded_view.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=ExplodedViewSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(
        Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True
    )
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(minimize_button=True, hide_button=True)

    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
