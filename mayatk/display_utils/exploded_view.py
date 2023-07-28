# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)
try:
    import maya.OpenMaya as om
except ModuleNotFoundError as error:
    print(__file__, error)

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.xform_utils import XformUtils
from mayatk.node_utils import NodeUtils


class ExplodedView:
    exploded_objects = []

    @staticmethod
    def calculate_repulsive_force(centroid1, size1, centroid2, size2, scale=0.05):
        """Calculates the repulsive force between two objects based on their centroids and sizes.

        Parameters:
            centroid1 (tuple): A tuple of floats representing the centroid of the first object in 3D space.
            size1 (float): The size of the first object.
            centroid2 (tuple): A tuple of floats representing the centroid of the second object in 3D space.
            size2 (float): The size of the second object.
            scale (float, optional): The scaling factor to apply to the force magnitude. Defaults to 0.05.

        Returns:
            om.MVector: The force vector resulting from the repulsion.
        """
        distance = (
            (centroid1[0] - centroid2[0]) ** 2
            + (centroid1[1] - centroid2[1]) ** 2
            + (centroid1[2] - centroid2[2]) ** 2
        ) ** 0.5

        epsilon = 1e-6
        force_magnitude = (size1 * size2) / ((distance * distance) + epsilon)
        force_magnitude *= scale

        force_vector = om.MVector(
            centroid1[0] - centroid2[0],
            centroid1[1] - centroid2[1],
            centroid1[2] - centroid2[2],
        )
        force_vector.normalize()
        force_vector *= force_magnitude

        return force_vector

    @classmethod
    def arrange_objects(
        cls, nodes, convergence_threshold=1e-4, max_iterations=1000, max_movement=1.0
    ):
        """Arranges a list of objects in 3D space to avoid overlap.

        Parameters:
            nodes (list): A list of objects to arrange.
            convergence_threshold (float, optional): The threshold at which to consider the system as having converged.
            Defaults to 1e-4.
            max_iterations (int, optional): The maximum number of iterations to run before stopping.
            Defaults to 10000.
            max_movement (float, optional): The maximum distance an object can move during a single iteration.
            Defaults to 1.0.

        Returns:
            int: The number of iterations required for the system to converge.
        """
        iteration_count = 0
        converged = False

        node_data = [
            XformUtils.get_bounding_box(node, "center|maxsize") for node in nodes
        ]

        while not converged and iteration_count < max_iterations:
            total_system_force = om.MVector(0, 0, 0)

            for idx1, node1 in enumerate(nodes):
                total_force = om.MVector(0, 0, 0)

                for idx2, node2 in enumerate(nodes):
                    if node1 != node2:
                        repulsive_force = cls.calculate_repulsive_force(
                            node_data[idx1][0],
                            node_data[idx1][1],
                            node_data[idx2][0],
                            node_data[idx2][1],
                        )
                        total_force += repulsive_force

                movement_vector = om.MVector(
                    total_force.x, total_force.y, total_force.z
                )

                if movement_vector.length() > max_movement:
                    movement_vector.normalize()
                    movement_vector *= max_movement

                pm.move(
                    movement_vector.x,
                    movement_vector.y,
                    movement_vector.z,
                    node1,
                    relative=True,
                )
                total_system_force += movement_vector

                # Update centroid
                new_centroid = [
                    node_data[idx1][0][i] + movement_vector[i] for i in range(3)
                ]
                node_data[idx1] = (new_centroid, node_data[idx1][1])

            if total_system_force.length() < convergence_threshold:
                converged = True

            iteration_count += 1

        cls.exploded_objects.append(nodes)
        return iteration_count

    def explode_selected(self):
        """Explode selected"""
        selection = NodeUtils.get_unique_children(pm.ls(sl=True))
        for obj in selection:
            if obj.hasAttr("original_position"):
                selection.remove(obj)
                continue
            pos = pm.xform(obj, query=True, translation=True, worldSpace=True)
            NodeUtils.set_node_attributes(obj, original_position=pos)

        iterations = self.arrange_objects(selection)

    def un_explode_selected(self):
        """Un-explode selected"""
        selection = NodeUtils.get_unique_children(pm.ls(sl=True))
        for obj in selection:
            if pm.attributeQuery("original_position", node=obj, exists=True):
                pos = pm.getAttr(obj.original_position)
                pm.move(pos[0], pos[1], pos[2], obj, absolute=True)
                pm.deleteAttr(obj, attribute="original_position")

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
        selection = NodeUtils.get_unique_children(pm.ls(sl=True))
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


def launch_gui(move_to_cursor=False, frameless=False):
    """Launch the UI"""
    from PySide2 import QtCore
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    sb = Switchboard(
        parent, ui_location="exploded_view.ui", slots_location=ExplodedViewSlots
    )
    if frameless:
        sb.ui.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        sb.ui.setAttribute(QtCore.Qt.WA_TranslucentBackground)
    else:
        sb.ui.setWindowTitle("Exploded View")

    if move_to_cursor:
        sb.center_widget(sb.ui, "cursor")
    else:
        sb.center_widget(sb.ui)

    sb.ui.centralWidget().setProperty("class", "translucentBgWithBorder")
    sb.ui.set_style(theme="dark")
    sb.ui.stays_on_top = True
    sb.ui.show()


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    launch_gui()

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
