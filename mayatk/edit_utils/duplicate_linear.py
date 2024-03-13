# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils, Preview
from mayatk.display_utils import DisplayUtils


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

            for i in range(num_copies):
                dup = pm.instance(node)[0]

                # After applying transformations, add the duplicate to the isolation set
                DisplayUtils.add_to_isolation_set(dup)

                x = (i + 1) / num_copies
                f_x = ptk.lerp(
                    x,
                    x**weight_curve
                    if weight_bias >= 0.5
                    else 1 - (1 - x) ** weight_curve,
                    weight_factor,
                )

                # Create new MTransformationMatrix object
                new_matrix = om.MTransformationMatrix(original_matrix)

                # Calculate new transformations
                new_translation = om.MVector(*translate) * f_x
                new_rotation = om.MEulerRotation(
                    rotate[0] * f_x, rotate[1] * f_x, rotate[2] * f_x
                )

                # Handle potential negative scale values
                new_scale = [(abs(s) ** f_x) * (-1 if s < 0 else 1) for s in scale]

                # Apply transformations
                new_matrix.setTranslation(new_translation, om.MSpace.kTransform)
                new_matrix.setRotation(new_rotation)
                new_matrix.setScale(new_scale, om.MSpace.kTransform)

                # Get existing transformation of duplicate
                sel_list.add(str(dup))
                mobj_dup = sel_list.getDependNode(1)
                fn_transform_dup = om.MFnTransform(mobj_dup)
                existing_matrix = fn_transform_dup.transformation()

                # Extract existing transformation components
                existing_translation = existing_matrix.translation(om.MSpace.kTransform)
                existing_rotation = existing_matrix.rotation()
                existing_scale = existing_matrix.scale(om.MSpace.kTransform)

                # Combine existing and new transformations
                combined_translation = existing_translation + new_translation
                combined_rotation = om.MEulerRotation(
                    existing_rotation.x + new_rotation.x,
                    existing_rotation.y + new_rotation.y,
                    existing_rotation.z + new_rotation.z,
                )
                combined_scale = [
                    existing * new for existing, new in zip(existing_scale, new_scale)
                ]

                # Create a new MTransformationMatrix for the combined transformation
                combined_matrix = om.MTransformationMatrix(existing_matrix)
                combined_matrix.setTranslation(
                    combined_translation, om.MSpace.kTransform
                )
                combined_matrix.setRotation(combined_rotation)
                combined_matrix.setScale(combined_scale, om.MSpace.kTransform)

                # Apply combined transformation
                fn_transform_dup.setTransformation(combined_matrix)

                sel_list.remove(1)
                copies.append(dup)

            originals_to_copies[node] = copies

        return originals_to_copies


class DuplicateLinearSlots:
    def __init__(self):
        self.sb = self.switchboard()
        self.ui = self.sb.duplicate_linear

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
        # Connect valueChanged signals to toggle_weight_ui using connect_multi
        self.sb.connect_multi(
            self.ui,
            "s003-8",
            "valueChanged",
            self.toggle_weight_ui,
        )
        # Initialize the UI state
        self.toggle_weight_ui()

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

        self.copies = DuplicateLinear.duplicate_linear(
            objects,
            num_copies,
            translate,
            rotate,
            scale,
            weight_bias,
            weight_curve,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "duplicate_linear.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=DuplicateLinearSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(
        Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True
    )
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")

    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
