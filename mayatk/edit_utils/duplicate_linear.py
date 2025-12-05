# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.core_utils.preview import Preview
from mayatk.xform_utils._xform_utils import XformUtils


class DuplicateLinear:
    @staticmethod
    def duplicate_linear(
        objects,
        num_copies,
        translate=(0, 0, 0),
        rotate=(0, 0, 0),
        scale=(1, 1, 1),
        weight_bias=0.5,
        weight_curve=4,
        pivot="object",
        calculation_mode="weighted",
    ):
        weight_factor = (
            2 * (weight_bias - 0.5) if weight_bias >= 0.5 else 2 * (0.5 - weight_bias)
        )

        originals_to_copies = {}

        for node in objects:
            copies = []
            sel_list = om.MSelectionList()
            sel_list.add(str(node))
            mobj = sel_list.getDependNode(0)
            fn_transform = om.MFnTransform(mobj)
            original_matrix = fn_transform.transformation()

            # Get the pivot point for transformations
            pivot_pos = XformUtils.get_operation_axis_pos(node, pivot)
            pivot_point = om.MPoint(pivot_pos[0], pivot_pos[1], pivot_pos[2])

            for i in range(num_copies):
                dup = pm.instance(node)[0]

                # After applying transformations, add the duplicate to the isolation set
                DisplayUtils.add_to_isolation_set(dup)

                # Calculate the transformation factor using the selected method
                f_x = ptk.ProgressionCurves.calculate_progression_factor(
                    i, num_copies, weight_bias, weight_curve, calculation_mode
                )

                # Calculate transformations using PyMel (avoids OpenMaya compatibility issues)
                translation_vector = [translate[j] * f_x for j in range(3)]
                rotation_values = [rotate[j] * f_x for j in range(3)]
                scale_factors = [(abs(s) ** f_x) * (-1 if s < 0 else 1) for s in scale]

                # Apply translation
                current_pos = pm.xform(
                    dup, query=True, worldSpace=True, translation=True
                )
                new_translation = [
                    current_pos[j] + translation_vector[j] for j in range(3)
                ]
                pm.xform(dup, worldSpace=True, translation=new_translation)

                # Apply rotation (relative to current rotation)
                current_rotation = pm.xform(
                    dup, query=True, worldSpace=True, rotation=True
                )
                new_rotation = [
                    current_rotation[j] + rotation_values[j] for j in range(3)
                ]
                pm.xform(dup, worldSpace=True, rotation=new_rotation)

                # Apply scale (multiplicative)
                current_scale = pm.xform(dup, query=True, worldSpace=True, scale=True)
                new_scale = [current_scale[j] * scale_factors[j] for j in range(3)]
                pm.xform(dup, worldSpace=True, scale=new_scale)

                copies.append(dup)

            originals_to_copies[node] = copies

        return originals_to_copies


class DuplicateLinearSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_linear

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
        )
        self.sb.connect_multi(
            self.ui,
            "s000-11",
            "valueChanged",
            self.preview.refresh,
        )
        # Connect pivot combobox to preview refresh
        self.sb.connect_multi(
            self.ui,
            "cmb000",
            "currentIndexChanged",
            self.preview.refresh,
        )
        # Connect calculation mode combobox to preview refresh
        self.sb.connect_multi(
            self.ui,
            "cmb001",
            "currentIndexChanged",
            self.preview.refresh,
        )
        # Connect valueChanged signals to toggle_weight_ui using connect_multi
        self.sb.connect_multi(
            self.ui,
            "s003-8",
            "valueChanged",
            self.toggle_weight_ui,
        )
        # Initialize the UI state
        self.toggle_weight_ui()

    @staticmethod
    def _resolve_pivot(pivot_index: int) -> str:
        """Resolve pivot string from UI dropdown index."""
        pivot_mapping = {
            0: "object",  # Pivot: Object
            1: "world",  # Pivot: World
            2: "center",  # Pivot: Bounding Box (center)
        }
        return pivot_mapping.get(pivot_index, "object")

    @staticmethod
    def _resolve_calculation_mode(mode_index: int) -> str:
        """Resolve calculation mode string from UI dropdown index."""
        mode_mapping = {
            0: "linear",  # Linear - even spacing
            1: "ease_in",  # Ease In - accelerating spacing
            2: "ease_out",  # Ease Out - decelerating spacing
            3: "ease_in_out",  # Ease In-Out - S-curve spacing
            4: "exponential",  # Exponential - rapid acceleration
            5: "smooth_step",  # Smooth Step - alternative S-curve
            6: "weighted",  # Weighted - original with bias control
        }
        return mode_mapping.get(mode_index, "linear")

    def toggle_weight_ui(self):
        """Disable weight UI components if rotate values are zero and scale values are one."""
        is_rotate_zero = all(
            self.ui.__dict__[f"s00{i}"].value() == 0 for i in range(3, 6)
        )
        is_scale_one = all(
            self.ui.__dict__[f"s00{i}"].value() == 1 for i in range(6, 9)
        )

        should_disable = is_rotate_zero and is_scale_one
        self.ui.s010.setDisabled(should_disable)
        self.ui.s011.setDisabled(should_disable)

    def perform_operation(self, objects):
        """Perform the linear duplication operation."""
        num_copies = self.ui.s009.value() - 1  # Include the orig object in the count
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
        weight_bias = self.ui.s010.value()
        weight_curve = self.ui.s011.value()

        # Get pivot from dropdown
        pivot_index = self.ui.cmb000.currentIndex()
        pivot = self._resolve_pivot(pivot_index)

        # Get calculation mode from dropdown
        mode_index = self.ui.cmb001.currentIndex()
        calculation_mode = self._resolve_calculation_mode(mode_index)

        self.copies = DuplicateLinear.duplicate_linear(
            objects,
            num_copies,
            translate,
            rotate,
            scale,
            weight_bias,
            weight_curve,
            pivot,
            calculation_mode,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("duplicate_linear", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
