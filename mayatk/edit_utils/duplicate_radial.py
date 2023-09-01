# !/usr/bin/python
# coding=utf-8
import re

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils, Preview


class DuplicateRadial:
    @staticmethod
    def duplicate_radial(
        objects,
        num_copies,
        start_angle=0,
        end_angle=360,
        weight_bias=0.5,
        weight_curve=4,
        rotate_axis="y",
        pivot_point=(0, 0, 0),
        translate=(0, 0, 0),
        rotate=(0, 0, 0),
        scale=(1, 1, 1),
    ):
        if rotate_axis not in ["x", "y", "z"]:
            raise ValueError("Invalid rotation axis, expected 'x', 'y', or 'z'")

        rotation_index = {"x": 0, "y": 1, "z": 2}[rotate_axis]
        total_rotation = end_angle - start_angle

        weight_factor = (
            2 * (weight_bias - 0.5) if weight_bias >= 0.5 else 2 * (0.5 - weight_bias)
        )
        originals_to_copies = {}

        for node in objects:
            # Get the parent of the original object
            parent_node = node.getParent()

            # Apply initial rotation to the object
            pm.rotate(node, rotate, r=True)

            # Scale, translate and rotate the original object
            pm.scale(node, scale, relative=True)
            pm.move(node, translate, relative=True)

            # Group the object under a new node
            group_node = pm.group(node, absolute=True)

            # Set the parent of the new group to the same as the original object
            pm.parent(group_node, parent_node)

            bb = pm.exactWorldBoundingBox(group_node)
            center_point = [(a + b) / 2 for a, b in zip(bb[:3], bb[3:])]
            original_pivot = pm.xform(group_node, q=True, rp=True)

            new_pivot_world_space = [a + b for a, b in zip(center_point, pivot_point)]
            pm.xform(group_node, piv=new_pivot_world_space)

            rotation_start = [0, 0, 0]
            rotation_start[rotation_index] = start_angle
            pm.rotate(group_node, rotation_start, r=True, os=True, fo=True)

            copies = []
            for i in range(num_copies):
                if i == 0:  # Keep the original as the first element
                    copies.append(node)
                else:
                    copy_group = pm.instance(group_node, leaf=True)[0]
                    copy = copy_group.getChildren()[0]
                    copies.append(copy)

                    x = (i - 1) / (num_copies - 1) if num_copies > 1 else 0.5
                    f_x = ptk.lerp(
                        x,
                        x**weight_curve
                        if weight_bias >= 0.5
                        else 1 - (1 - x) ** weight_curve,
                        weight_factor,
                    )

                    current_rotation = [0, 0, 0]
                    current_rotation[rotation_index] = total_rotation * f_x
                    pm.rotate(copy_group, current_rotation, r=True, os=True, fo=True)

                    t = [x * i / (num_copies - 1) for x in translate]

                    pm.move(copy_group, t)

            pm.xform(group_node, piv=original_pivot)
            originals_to_copies[node] = copies

        return originals_to_copies


class DuplicateRadialSlots:
    def __init__(self):
        self.sb = self.switchboard()
        self.ui = self.sb.duplicate_radial

        self.preview = Preview(
            self.ui.chk000,
            self.ui.b000,
            operation_func=self.perform_duplicate_radial,
            finalize_func=self.regroup_copies,
            message_func=self.sb.message_box,
        )
        self.sb.connect_multi(
            self.ui,
            "s000-16",
            "valueChanged",
            self.preview.refresh,
        )
        self.sb.connect_multi(
            self.ui,
            "chk002-4",
            "toggled",
            self.preview.refresh,
        )

    def perform_duplicate_radial(self):
        """Perform the radial duplication operation."""
        objects = pm.ls(sl=True, type="transform")
        num_copies = self.ui.s009.value()
        start_angle = self.ui.s013.value()
        end_angle = self.ui.s014.value()
        weight_bias = self.ui.s015.value()
        weight_curve = self.ui.s016.value()
        rotate_axis = (
            "x"
            if self.ui.chk002.isChecked()
            else "y"
            if self.ui.chk003.isChecked()
            else "z"
        )
        pivot_point = (
            self.ui.s010.value(),
            self.ui.s011.value(),
            self.ui.s012.value(),
        )
        translate = (
            self.ui.s000.value(),
            self.ui.s001.value(),
            self.ui.s002.value(),
        )
        rotate = (
            self.ui.s003.value(),
            self.ui.s004.value(),
            self.ui.s005.value(),
        )
        scale = (
            self.ui.s006.value(),
            self.ui.s007.value(),
            self.ui.s008.value(),
        )

        self.copies = DuplicateRadial.duplicate_radial(
            objects,
            num_copies,
            start_angle,
            end_angle,
            weight_bias,
            weight_curve,
            rotate_axis,
            pivot_point,
            translate,
            rotate,
            scale,
        )

    def regroup_copies(self):
        """Regroup the instances under their original parent group."""
        pm.undoInfo(openChunk=True)
        for copies in self.copies.values():
            if not all(pm.objExists(copy) for copy in copies):
                # If any copy in the set doesn't exist, skip to the next set.
                continue

            first_obj_name = copies[0].name()
            name = re.sub(r"\d+$", "", first_obj_name)
            name += "_array"
            unique_name = CoreUtils.generate_unique_name(name)

            # Find the parent of the parent of the first object and use it as a parent for the new group
            original_parent = copies[0].getParent().getParent()

            for copy in copies[1:]:
                copy_group = copy.getParent()
                pm.parent(copy, world=True)
                pm.delete(copy_group)

            new_group = pm.group(copies, n=unique_name)

            # If original_parent exists then parent the new_group under original_parent
            if original_parent is not None:
                pm.parent(new_group, original_parent)

        pm.undoInfo(closeChunk=True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "duplicate_radial.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=DuplicateRadialSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(
        Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True
    )
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")

    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
