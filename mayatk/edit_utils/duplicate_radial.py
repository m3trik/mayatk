# !/usr/bin/python
# coding=utf-8
import re
from typing import List, Dict, Tuple, Union
import pythontk as ptk

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.core_utils.preview import Preview
from mayatk import DisplayUtils
from mayatk import XformUtils
from mayatk.edit_utils.naming import Naming


class DuplicateRadial(ptk.LoggingMixin):

    @staticmethod
    def duplicate_radial(
        objects: List["pm.PyNode"],
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
        pivot: Union[str, Tuple[float, float, float]] = "object",
        keep_original: bool = False,
        instance: bool = False,
        combine: bool = False,
        suffix: bool = True,
    ) -> Dict["pm.PyNode", List["pm.PyNode"]]:
        """Duplicate objects in a radial pattern.

        Parameters:
            objects (List[pm.PyNode]): List of objects to duplicate.
            num_copies (int): Number of copies to create.
            start_angle (float): Starting angle for duplication.
            end_angle (float): Ending angle for duplication.
            weight_bias (float): Bias for the weight curve.
            weight_curve (float): Weight curve value.
            rotate_axis (str): Axis of rotation ('x', 'y', or 'z').
            offset (Tuple[float, float, float]): Offset for the pivot point.
            translate (Tuple[float, float, float]): Translation vector.
            rotate (Tuple[float, float, float]): Rotation vector.
            scale (Tuple[float, float, float]): Scale vector.
            pivot (Union[str, Tuple[float, float, float]]): Pivot point type or position.
            keep_original (bool): Whether to keep the original object.
            instance (bool): Whether to create instances of the duplicates.
            combine (bool): Whether to combine the duplicates into one mesh.
            suffix (bool): Whether to add a suffix to the duplicated objects.

        Returns:
            Dict[pm.PyNode, List[pm.PyNode]]: Mapping of original objects to their duplicates.

        Raises:
            ValueError: If invalid parameters are provided.
        """
        DuplicateRadial._validate_inputs(rotate_axis, weight_bias, weight_curve)
        originals_to_copies = {}

        for node in objects:
            print(f"\n[duplicate radial] Processing node: {node} ..")

            driven_group, driven_node, pivot_pos = (
                DuplicateRadial._prepare_driven_group(
                    node, rotate, scale, translate, offset, pivot, instance
                )
            )

            copies = DuplicateRadial._create_and_transform_instances(
                driven_group,
                num_copies,
                rotate_axis,
                start_angle,
                end_angle,
                translate,
                weight_bias,
                weight_curve,
            )

            pm.delete(driven_group)
            DuplicateRadial._cleanup_original(node, keep_original)

            finalized = DuplicateRadial._finalize_output(
                node, copies, keep_original, combine
            )

            if suffix:
                Naming.append_location_based_suffix(
                    finalized, first_obj_as_ref=True, alphabetical=True
                )

            originals_to_copies[node] = finalized
            print(
                f"[duplicate radial] [{node}] Created {len(finalized)} total instances"
            )

        return originals_to_copies

    def _finalize_output(
        self,
        node: "pm.PyNode",
        copies: List["pm.PyNode"],
        keep_original: bool,
        combine: bool,
    ) -> List["pm.PyNode"]:
        if combine:
            combined = pm.polyUnite(copies, ch=False, mergeUVSets=True)[0]
            combined = pm.rename(combined, f"{node}_radialCombined")
            pm.delete(
                pm.listRelatives(
                    combined, shapes=True, noIntermediate=True, type="transform"
                )
            )
            self.logger.debug(f"Combined all instances into: {combined}")
            return [combined]

        clean_copies = []
        for copy in copies:
            parent = pm.listRelatives(copy, parent=True, fullPath=True)
            if parent:
                pm.parent(copy, world=True)
                if not keep_original:
                    pm.delete(parent[0])
            clean_copies.append(copy)

        group_name = f"{node}_radialGroup"
        container_group = pm.group(clean_copies, name=group_name)
        self.logger.debug(f"Grouped all instances under: {container_group}")

        return clean_copies

    def _cleanup_original(self, node: pm.PyNode, keep_original: bool) -> None:
        if not keep_original:
            self.logger.debug(f"Deleting original node: {node}")
            pm.delete(node)

    @classmethod
    def _prepare_driven_group(
        self,
        node: "pm.PyNode",
        rotate: Tuple[float, float, float],
        scale: Tuple[float, float, float],
        translate: Tuple[float, float, float],
        offset: Tuple[float, float, float],
        pivot: Union[str, Tuple[float, float, float]],
        instance: bool = False,
    ) -> Tuple["pm.PyNode", "pm.PyNode", Tuple[float, float, float]]:
        driven_node = pm.duplicate(node, rr=True, instanceLeaf=instance)[0]
        self.logger.debug(f"[{node}] Duplicated original → driven node: {driven_node}")

        self._apply_initial_transformations(driven_node, rotate, scale, translate)

        pivot_pos = XformUtils.get_operation_axis_pos(driven_node, pivot)
        self.logger.debug(f"[{driven_node}] Rotation pivot (world-space): {pivot_pos}")

        group_node = pm.group(em=True)
        pm.xform(group_node, ws=True, t=(0, 0, 0))

        pivot_offset_pos = [pivot_pos[i] + offset[i] for i in range(3)]
        self.logger.debug(f"Setting rotate and scale pivot to: {pivot_offset_pos}")
        pm.xform(group_node, ws=True, rp=pivot_offset_pos, sp=pivot_offset_pos)

        pm.parent(driven_node, group_node)
        self.logger.debug(f"[{driven_node}] Wrapped in group: {group_node}")

        return group_node, driven_node, pivot_pos

    def _validate_inputs(
        self, rotate_axis: str, weight_bias: float, weight_curve: float
    ) -> None:
        if rotate_axis not in ["x", "y", "z"]:
            raise ValueError("Invalid rotation axis, expected 'x', 'y', or 'z'")
        if not (0.0 <= weight_bias <= 1.0):
            raise ValueError("weight_bias must be between 0.0 and 1.0")
        if not (0.0 <= weight_curve <= 1.0):
            raise ValueError("weight_curve must be between 0.0 and 1.0")

    def _calculate_final_pivot_matrix(
        self,
        manip_pivot_matrix: "pm.datatypes.Matrix",
        offset: Tuple[float, float, float],
    ) -> "pm.datatypes.Matrix":
        offset_matrix = pm.datatypes.TransformationMatrix()
        offset_matrix.translate = pm.datatypes.Vector(offset)
        final_pivot_matrix = manip_pivot_matrix * offset_matrix.asMatrix()
        self.logger.debug(f"Offset matrix: {offset_matrix}")
        return final_pivot_matrix

    def _apply_initial_transformations(
        self,
        node: "pm.PyNode",
        rotate: Tuple[float, float, float],
        scale: Tuple[float, float, float],
        translate: Tuple[float, float, float],
    ) -> None:
        self.logger.debug(f"Applying initial rotation to {node}: {rotate}")
        pm.rotate(node, rotate, r=True)
        self.logger.debug(f"Applying scale to {node}: {scale}")
        pm.scale(node, scale, relative=True)
        self.logger.debug(f"Applying translation to {node}: {translate}")
        pm.move(node, translate, relative=True)

    def _create_group_node(self, node: "pm.PyNode") -> "pm.PyNode":
        # Check the parent of the node
        parent_node = node.getParent()

        # Debug statement to show parent info
        self.logger.debug(f"Parent node for {node}: {parent_node}")

        # Create a group node for the given object
        group_node = pm.group(node, absolute=True)

        # If the group node's parent is itself, skip parenting
        if group_node == parent_node:
            self.logger.debug(
                f"{group_node} is already correctly parented, no action needed."
            )
        else:
            # Ensure parent_node is not None and group_node is not already under the parent_node
            if parent_node:
                try:
                    pm.parent(group_node, parent_node)
                    self.logger.debug(f"Parenting {group_node} under {parent_node}")
                except Exception as e:
                    self.logger.debug(
                        f"Failed to parent {group_node} under {parent_node}: {e}"
                    )
            else:
                self.logger.debug(
                    f"{node} has no valid parent node, skipping parenting operation."
                )

        return group_node

    def _create_and_transform_instances(
        self,
        group_node: "pm.PyNode",
        num_copies: int,
        rotate_axis: str,
        start_angle: float,
        end_angle: float,
        translate: Tuple[float, float, float],
        weight_bias: float,
        weight_curve: float,
        instance: bool,  # <-- add this parameter
    ) -> List["pm.PyNode"]:
        rotation_index = {"x": 0, "y": 1, "z": 2}[rotate_axis]
        total_rotation = end_angle - start_angle
        weight_factor = 2 * abs(weight_bias - 0.5)
        copies = []

        for i in range(num_copies):
            if instance:
                copy_group = pm.instance(group_node, leaf=True)[0]
            else:
                copy_group = pm.duplicate(group_node, rr=True)[0]
            copy = copy_group.getChildren()[0]
            copies.append(copy)
            self.logger.debug(
                f"Creating {'instance' if instance else 'duplicate'} {i}: {copy}"
            )

            x = i / (num_copies - 1) if num_copies > 1 else 0.0
            curve_value = (
                x ** (1 / (1 - weight_curve))
                if weight_bias >= 0.5
                else 1 - (1 - x) ** (1 / (1 - weight_curve))
            )

            f_x = (1 - weight_factor) * x + weight_factor * curve_value
            current_rotation = [0, 0, 0]
            current_rotation[rotation_index] = start_angle + total_rotation * f_x
            self.logger.debug(f"Rotation factor for instance {i}: {f_x}")
            self.logger.debug(f"Applying rotation to instance {i}: {current_rotation}")
            pm.rotate(copy_group, current_rotation, r=True, os=True, fo=True)

            t = [translate[j] * f_x for j in range(3)]
            self.logger.debug(f"Applying translation to instance {i}: {t}")
            pm.move(copy_group, t)
            DisplayUtils.add_to_isolation_set(copy)
            self.logger.debug(
                f"{'Instance' if instance else 'Duplicate'} {i} added to isolation set: {copy}"
            )

        return copies


class DuplicateRadialSlots:
    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_radial

        self.logger.setLevel(log_level)
        self.logger.set_log_prefix(f"[duplicate radial] ")

        self.preview = Preview(
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
            "chk002-8",
            "toggled",
            self.preview.refresh,
        )
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)

    def perform_operation(self, objects):
        """Perform the radial duplication operation."""
        kwargs = {
            "num_copies": self.ui.s009.value(),
            "start_angle": self.ui.s013.value(),
            "end_angle": self.ui.s014.value(),
            "weight_bias": self.ui.s015.value(),
            "weight_curve": self.ui.s016.value(),
            "instance": self.ui.chk005.isChecked(),
            "keep_original": self.ui.chk006.isChecked(),
            "combine": self.ui.chk007.isChecked(),
            "suffix": self.ui.chk008.isChecked(),
            "pivot": self._resolve_pivot(self.ui.cmb000.currentIndex()),
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

    def _resolve_pivot(self, pivot_index: int) -> str:
        """Resolve the pivot based on the index from the UI dropdown."""
        axis_mapping = {0: "object", 1: "world"}
        return axis_mapping.get(pivot_index, "object")

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
            unique_name = Naming.generate_unique_name(name)

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
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("duplicate_radial", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
