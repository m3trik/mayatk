# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils, Preview


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
            # Get the parent of the original object
            parent_node = node.getParent()
            copies = []  # List to hold copies of current node
            original_scale = node.scale.get()
            scale_steps = [
                (final_scale - init_scale)
                for final_scale, init_scale in zip(scale, original_scale)
            ]

            for i in range(num_copies):
                dup = pm.instance(node)[0]

                # Set the parent of the copy to the same as the original object
                pm.parent(dup, parent_node)

                x = (i + 1) / num_copies  # Adjusted line
                f_x = ptk.lerp(
                    x,
                    x**weight_curve
                    if weight_bias >= 0.5
                    else 1 - (1 - x) ** weight_curve,
                    weight_factor,
                )

                # Now transformations are adjusted directly by f_x
                dup.translate.set(dup.translate.get() + (pm.dt.Vector(translate) * f_x))
                dup.rotate.set(dup.rotate.get() + (pm.dt.Vector(rotate) * f_x))
                dup.scale.set(
                    [
                        init_scale + (b * f_x)
                        for init_scale, b in zip(original_scale, scale_steps)
                    ]
                )

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

    def perform_operation(self, objects):
        """Perform the linear duplication operation."""
        num_copies = self.ui.s009.value()
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
