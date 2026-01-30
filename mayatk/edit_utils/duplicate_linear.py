# !/usr/bin/python
# coding=utf-8
try:
    from qtpy import QtWidgets
except ImportError:
    pass
try:
    import pymel.core as pm
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)
import math
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
        instance=True,
    ):
        originals_to_copies = {}

        for node in objects:
            copies = []

            # Get the pivot matrix (Orientation + Position) using the centralized utility
            mat_pivot = XformUtils.get_operation_axis_matrix(node, pivot)
            mat_pivot_inv = mat_pivot.inverse()

            for i in range(num_copies):
                if instance:
                    dup = pm.instance(node)[0]
                else:
                    dup = pm.duplicate(node, rr=True)[0]

                # After applying transformations, add the duplicate to the isolation set
                DisplayUtils.add_to_isolation_set(dup)

                # Calculate the transformation factor using the selected method
                f_x = ptk.ProgressionCurves.calculate_progression_factor(
                    i, num_copies, weight_bias, weight_curve, calculation_mode
                )

                # Calculate transformations
                translation_vector = [translate[j] * f_x for j in range(3)]
                rotation_values = [rotate[j] * f_x for j in range(3)]
                scale_factors = [(abs(s) ** f_x) * (-1 if s < 0 else 1) for s in scale]

                # 1. Local Scale
                m_dup = om.MMatrix(
                    pm.xform(dup, query=True, worldSpace=True, matrix=True)
                )
                tm_dup = om.MTransformationMatrix(m_dup)

                current_scale = tm_dup.scale(om.MSpace.kObject)
                new_scale = [current_scale[j] * scale_factors[j] for j in range(3)]
                tm_dup.setScale(new_scale, om.MSpace.kObject)

                m_scaled = tm_dup.asMatrix()

                # 2. Rotate around Pivot
                euler = om.MEulerRotation(
                    math.radians(rotation_values[0]),
                    math.radians(rotation_values[1]),
                    math.radians(rotation_values[2]),
                    om.MEulerRotation.kXYZ,
                )
                mat_rot = euler.asMatrix()

                # M_rot = M_piv_inv * R * M_piv
                # This orbits the object around the pivot frame (Pos + Ori)
                mat_orbit = mat_pivot_inv * mat_rot * mat_pivot

                m_rotated = m_scaled * mat_orbit

                # 3. Apply Translation (World Space, but respecting Pivot Orientation)
                # To support translating along the Pivot's axis (e.g. Manipulator Axis), we must rotate the translation vector.
                vec_trans_local = om.MVector(
                    translation_vector[0], translation_vector[1], translation_vector[2]
                )
                # Transforming MVector by MMatrix rotates/scales it but ignores translation (w=0)
                vec_trans_rotated = vec_trans_local * mat_pivot

                mat_trans_list = [
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    vec_trans_rotated.x,
                    vec_trans_rotated.y,
                    vec_trans_rotated.z,
                    1.0,
                ]
                mat_trans = om.MMatrix(mat_trans_list)

                m_final = m_rotated * mat_trans

                pm.xform(dup, matrix=list(m_final), worldSpace=True)

                copies.append(dup)

            originals_to_copies[node] = copies

        return originals_to_copies


class DuplicateLinearSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_linear

        # Ensure preview cleanup triggers when resetting defaults
        self.ui.chk000.block_signals_on_restore = False

        # Populate pivot combobox
        self.ui.cmb002.clear()
        self.pivot_options = XformUtils.get_pivot_options()
        self.ui.cmb002.add(self.pivot_options, prefix="Pivot:")

        # Populate calculation mode combobox
        self.ui.cmb001.clear()
        self.interpolation_modes = [
            ("Linear", "linear"),
            ("Ease In", "ease_in"),
            ("Ease Out", "ease_out"),
            ("Ease In-Out", "ease_in_out"),
            ("Exponential", "exponential"),
            ("Smooth Step", "smooth_step"),
            ("Weighted", "weighted"),
        ]
        self.ui.cmb001.add(self.interpolation_modes, prefix="Interpolation:")

        # Set default calculation mode to "Weighted" to match tool defaults
        self.ui.cmb001.setAsCurrent("weighted")

        # Set default state for instance checkbox
        self.ui.chk001.setChecked(True)

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
            "cmb002",
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
        # Connect calculation mode combobox to toggle_weight_ui logic
        self.sb.connect_multi(
            self.ui,
            "cmb001",
            "currentIndexChanged",
            self.toggle_weight_ui,
        )

        # Connect instance checkbox to preview refresh
        self.sb.connect_multi(
            self.ui,
            "chk001",
            "stateChanged",
            self.preview.refresh,
        )

        # Initialize the UI state
        self.toggle_weight_ui()

    def toggle_weight_ui(self):
        """Disable weight UI components if the current calculation mode doesn't use them."""
        # Modes that don't typically use bias/curve parameters
        # Based on pythontk.math_utils.progression.ProgressionCurves
        mode = self.ui.cmb001.currentData()

        # 'linear' uses neither
        # 'exponential' uses weight_curve
        # 'logarithmic' uses weight_curve
        # 'sine' uses weight_curve
        # 'ease_in' uses weight_curve (power)
        # 'ease_out' uses weight_curve
        # 'ease_in_out' uses weight_curve
        # 'smooth_step' uses neither (it's fixed hermite 3x^2 - 2x^3)
        # 'bounce' uses weight_curve (bounciness?)
        # 'elastic' uses weight_curve (period/amplitude?)
        # 'weighted' uses BOTH weight_bias and weight_curve

        # Define which modes need what
        # (This is a simplified assumption based on typical usage)
        uses_curve = mode not in ["linear", "smooth_step"]
        uses_bias = mode in ["weighted"]

        self.ui.s010.setEnabled(uses_bias)  # Weight Bias
        self.ui.s011.setEnabled(uses_curve)  # Weight Curve

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

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
        pivot = self.ui.cmb002.currentData()

        # Get calculation mode from dropdown
        calculation_mode = self.ui.cmb001.currentData()

        # Get instance mode from checkbox
        instance = self.ui.chk001.isChecked()

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
            instance,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.mayatk_ui_manager import UiManager

    ui = UiManager.instance().get("duplicate_linear", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
