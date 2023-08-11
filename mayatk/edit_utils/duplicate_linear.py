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
        self.preview = Preview(
            self.sb.ui.chk000,
            self.sb.ui.b000,
            operation_func=self.perform_duplicate_linear,
            message_func=self.sb.message_box,
        )
        self.sb.connect_multi(
            self.sb.ui,
            "s000-11",
            "valueChanged",
            self.preview.refresh,
        )

    def perform_duplicate_linear(self):
        """Perform the linear duplication operation."""
        objects = pm.ls(sl=True, type="transform")
        num_copies = self.sb.ui.s009.value()
        translate = (
            self.sb.ui.s000.value(),
            self.sb.ui.s001.value(),
            self.sb.ui.s002.value(),
        )
        rotate = (
            self.sb.ui.s003.value(),
            self.sb.ui.s004.value(),
            self.sb.ui.s005.value(),
        )
        scale = (
            self.sb.ui.s006.value(),
            self.sb.ui.s007.value(),
            self.sb.ui.s008.value(),
        )
        weight_bias = self.sb.ui.s010.value()
        weight_curve = self.sb.ui.s011.value()

        self.copies = DuplicateLinear.duplicate_linear(
            objects,
            num_copies,
            translate,
            rotate,
            scale,
            weight_bias,
            weight_curve,
        )


class DuplicateLinearUI:
    @staticmethod
    def launch(move_to_cursor=False, frameless=False):
        """Launch the UI"""
        from PySide2 import QtCore
        from uitk import Switchboard

        parent = CoreUtils.get_main_window()
        sb = Switchboard(
            parent,
            ui_location="duplicate_linear.ui",
            slot_location=DuplicateLinearSlots,
        )

        if frameless:
            sb.ui.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
            sb.ui.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        else:
            sb.ui.setWindowTitle("Duplicate Linear")

        if move_to_cursor:
            sb.center_widget(sb.ui, "cursor")
        else:
            sb.center_widget(sb.ui)

        sb.ui.set_style(theme="dark", style_class="translucentBgWithBorder")
        sb.ui.set_flags("WindowStaysOnTopHint")
        sb.ui.show()


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    DuplicateLinearUI.launch(frameless=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
