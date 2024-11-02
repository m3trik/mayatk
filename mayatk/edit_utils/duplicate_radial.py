# !/usr/bin/python
# coding=utf-8
import re
from typing import List, Dict, Tuple

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk import core_utils
from mayatk import DisplayUtils
from mayatk import XformUtils


# Untested new code:
class DuplicateRadial:
    @staticmethod
    def duplicate_radial(
        objects: List[pm.PyNode],
        num_copies: int,
        start_angle: float = 0,
        end_angle: float = 360,
        weight_bias: float = 0.5,
        weight_curve: float = 0.5,
        rotate_axis: str = "y",
        offset: Tuple[float, float, float] = (0, 0, 0),
        translate: Tuple[float, float, float] = (0, 0, 0),
        rotate: Tuple[float, float, float] = (0, 0, 0),
        scale: Tuple[float, float, float] = (1, 1, 1),
    ) -> Dict[pm.PyNode, List[pm.PyNode]]:
        DuplicateRadial._validate_inputs(rotate_axis, weight_bias, weight_curve)
        originals_to_copies = {}

        for node in objects:
            print(f"Processing node: {node}")
            manip_pivot_matrix = XformUtils.get_manip_pivot_matrix(node)
            print(f"Manipulator pivot matrix for {node}: {manip_pivot_matrix}")

            final_pivot_matrix = DuplicateRadial._calculate_final_pivot_matrix(
                manip_pivot_matrix, offset
            )
            print(f"Final pivot matrix: {final_pivot_matrix}")

            DuplicateRadial._apply_initial_transformations(
                node, rotate, scale, translate
            )

            group_node = DuplicateRadial._create_group_node(node)
            print(f"Created group node: {group_node}")

            pm.xform(group_node, ws=True, m=final_pivot_matrix)
            start_rotation = [0, 0, 0]
            start_rotation[{"x": 0, "y": 1, "z": 2}[rotate_axis]] = start_angle
            print(f"Applying start rotation to {group_node}: {start_rotation}")
            pm.rotate(group_node, start_rotation, r=True, os=True, fo=True)

            copies = DuplicateRadial._create_and_transform_instances(
                group_node,
                num_copies,
                rotate_axis,
                start_angle,
                end_angle,
                translate,
                weight_bias,
                weight_curve,
            )
            originals_to_copies[node] = copies
            print(f"Original to copies mapping for {node}: {copies}")

        return originals_to_copies

    @staticmethod
    def _validate_inputs(
        rotate_axis: str, weight_bias: float, weight_curve: float
    ) -> None:
        if rotate_axis not in ["x", "y", "z"]:
            raise ValueError("Invalid rotation axis, expected 'x', 'y', or 'z'")
        if not (0.0 <= weight_bias <= 1.0):
            raise ValueError("weight_bias must be between 0.0 and 1.0")
        if not (0.0 <= weight_curve <= 1.0):
            raise ValueError("weight_curve must be between 0.0 and 1.0")

    @staticmethod
    def _calculate_final_pivot_matrix(
        manip_pivot_matrix: pm.datatypes.Matrix, offset: Tuple[float, float, float]
    ) -> pm.datatypes.Matrix:
        offset_matrix = pm.datatypes.TransformationMatrix()
        offset_matrix.translate = pm.datatypes.Vector(offset)
        final_pivot_matrix = manip_pivot_matrix * offset_matrix.asMatrix()
        print(f"Offset matrix: {offset_matrix}")
        return final_pivot_matrix

    @staticmethod
    def _apply_initial_transformations(
        node: pm.PyNode,
        rotate: Tuple[float, float, float],
        scale: Tuple[float, float, float],
        translate: Tuple[float, float, float],
    ) -> None:
        print(f"Applying initial rotation to {node}: {rotate}")
        pm.rotate(node, rotate, r=True)
        print(f"Applying scale to {node}: {scale}")
        pm.scale(node, scale, relative=True)
        print(f"Applying translation to {node}: {translate}")
        pm.move(node, translate, relative=True)

    @staticmethod
    def _create_group_node(node: pm.PyNode) -> pm.PyNode:
        # Check the parent of the node
        parent_node = node.getParent()

        # Debug statement to show parent info
        print(f"Parent node for {node}: {parent_node}")

        # Create a group node for the given object
        group_node = pm.group(node, absolute=True)

        # If the group node's parent is itself, skip parenting
        if group_node == parent_node:
            print(f"{group_node} is already correctly parented, no action needed.")
        else:
            # Ensure parent_node is not None and group_node is not already under the parent_node
            if parent_node:
                try:
                    pm.parent(group_node, parent_node)
                    print(f"Parenting {group_node} under {parent_node}")
                except Exception as e:
                    print(f"Failed to parent {group_node} under {parent_node}: {e}")
            else:
                print(f"{node} has no valid parent node, skipping parenting operation.")

        return group_node

    @staticmethod
    def _create_and_transform_instances(
        group_node: pm.PyNode,
        num_copies: int,
        rotate_axis: str,
        start_angle: float,
        end_angle: float,
        translate: Tuple[float, float, float],
        weight_bias: float,
        weight_curve: float,
    ) -> List[pm.PyNode]:
        rotation_index = {"x": 0, "y": 1, "z": 2}[rotate_axis]
        total_rotation = end_angle - start_angle
        weight_factor = 2 * abs(weight_bias - 0.5)
        copies = []

        for i in range(num_copies):
            if i == 0:
                copies.append(group_node.getChildren()[0])
                print(f"Adding original node to copies: {group_node.getChildren()[0]}")
            else:
                copy_group = pm.instance(group_node, leaf=True)[0]
                copy = copy_group.getChildren()[0]
                copies.append(copy)
                print(f"Creating instance {i}: {copy}")

                x = (i - 1) / (num_copies - 1) if num_copies > 1 else 0.5
                curve_value = (
                    x ** (1 / (1 - weight_curve))
                    if weight_bias >= 0.5
                    else 1 - (1 - x) ** (1 / (1 - weight_curve))
                )

                f_x = (1 - weight_factor) * x + weight_factor * curve_value
                current_rotation = [0, 0, 0]
                current_rotation[rotation_index] = total_rotation * f_x
                print(f"Rotation factor for instance {i}: {f_x}")
                print(f"Applying rotation to instance {i}: {current_rotation}")
                pm.rotate(copy_group, current_rotation, r=True, os=True, fo=True)

                t = [x * i / (num_copies - 1) for x in translate]
                print(f"Applying translation to instance {i}: {t}")
                pm.move(copy_group, t)
                DisplayUtils.add_to_isolation_set(copy)
                print(f"Instance {i} added to isolation set: {copy}")

        return copies


class DuplicateRadialSlots:
    def __init__(self):
        self.sb = self.switchboard()
        self.ui = self.sb.duplicate_radial

        self.preview = core_utils.preview.Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
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

    def perform_operation(self, objects):
        """Perform the radial duplication operation."""
        kwargs = {
            "num_copies": self.ui.s009.value(),
            "start_angle": self.ui.s013.value(),
            "end_angle": self.ui.s014.value(),
            "weight_bias": self.ui.s015.value(),
            "weight_curve": self.ui.s016.value(),
            "rotate_axis": (
                "x"
                if self.ui.chk002.isChecked()
                else "y" if self.ui.chk003.isChecked() else "z"
            ),
            "offset": (
                self.ui.s010.value(),
                self.ui.s011.value(),
                self.ui.s012.value(),
            ),
            "translate": (
                self.ui.s000.value(),
                self.ui.s001.value(),
                self.ui.s002.value(),
            ),
            "rotate": (
                self.ui.s003.value(),
                self.ui.s004.value(),
                self.ui.s005.value(),
            ),
            "scale": (
                self.ui.s006.value(),
                self.ui.s007.value(),
                self.ui.s008.value(),
            ),
        }

        self.copies = DuplicateRadial.duplicate_radial(objects, **kwargs)

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
            unique_name = core_utils.CoreUtils.generate_unique_name(name)

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

    parent = core_utils.CoreUtils.get_main_window()
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
