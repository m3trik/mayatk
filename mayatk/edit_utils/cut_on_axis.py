# !/usr/bin/python
# coding=utf-8
# from this package:
import maya.cmds as cmds
from uitk.widgets.mixins.tooltip_mixin import TooltipFormat
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.core_utils.preview import Preview
from mayatk.xform_utils.pivot_watcher import PivotWatcher


class CutOnAxis:
    @staticmethod
    @CoreUtils.undoable
    def perform_cut_on_axis(
        objects,
        axis="-x",
        cuts=0,
        cut_offset=0,
        cut_spacing=0.0,
        distribution="linear",
        weight_bias=0.5,
        weight_curve=2.0,
        delete=False,
        mirror=False,
        pivot="manip",
        use_object_axes=True,
    ):
        """Iterates over provided objects and performs cut or delete operations based on the axis specified.

        Parameters:
            objects (list): The list of mesh objects to be processed.
            axis (str): The axis to cut or delete along ('x', '-x', 'y', '-y', 'z', '-z'). Default is '-x'.
            cuts (int): The number of cuts to make. Default is 0.
            cut_offset (float): Offset amount from the center for the cut. Default is 0.
            cut_spacing (float): Distance between adjacent cuts. ``0`` (default)
                auto-fills the axis; ``>0`` fixes the span between cuts.
            distribution (str): Progression curve distributing the cuts across
                the span ("linear", "ease_in", "ease_out", "weighted", …).
            weight_bias (float): Bias for the "weighted" distribution (0..1).
            weight_curve (float): Curve strength for non-linear distributions.
            delete (bool): If True, delete the faces on the specified axis. Default is False.
            mirror (bool): After deleting, mirror the object(s).
            pivot (str): Pivot type string ("manip", "object", "world", "center"). Default is "manip".
            use_object_axes (bool): If True, uses object's local axes when using object-space pivots.
        """
        if cuts:
            axis = axis.lower()  # Assure lower case.

            EditUtils.cut_along_axis(
                objects,
                axis=axis,
                pivot=pivot,
                amount=cuts,
                mirror=mirror,
                offset=cut_offset,
                spacing=cut_spacing,
                distribution=distribution,
                weight_bias=weight_bias,
                weight_curve=weight_curve,
                delete=delete,
                use_object_axes=use_object_axes,
            )

            cmds.select(objects)


class CutOnAxisSlots:
    # polyCut mutates the mesh in place. On a historyless mesh (frozen /
    # imported) it spawns an intermediate orig-shape that holds the only
    # pristine copy, so the hermetic preview's node-diff rollback would bake
    # the cut in instead of reverting it -> cuts stack on every value change.
    # Opting into geometry preservation makes the contract snapshot the mesh
    # and restore it in place on rollback. See mayatk/core_utils/preview.py.
    PRESERVE_GEOMETRY = True

    # Interpolation modes offered for the cut distribution. Mirrors
    # DuplicateLinearSlots.interpolation_modes verbatim (same curated,
    # monotonic subset of ptk.ProgressionCurves) so the two tools present the
    # same weighting. TODO(DRY): this label->mode metadata now lives in four
    # sites across mayatk+blendertk (both duplicate_linear + both cut_on_axis);
    # the single home is ptk.ProgressionCurves — consolidate in a dedicated
    # cross-package pass.
    INTERPOLATION_MODES = [
        ("Linear", "linear"),
        ("Ease In", "ease_in"),
        ("Ease Out", "ease_out"),
        ("Ease In-Out", "ease_in_out"),
        ("Exponential", "exponential"),
        ("Smooth Step", "smooth_step"),
        ("Weighted", "weighted"),
    ]

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.cut_on_axis

        # Populate the interpolation combobox (distribution of cuts across the
        # span). Populate before add_reset_buttons so the default is captured.
        self.ui.cmb001.clear()
        self.ui.cmb001.add(self.INTERPOLATION_MODES, prefix="Interpolation:")
        self.ui.cmb001.setAsCurrent("linear")

        # Per-field reset buttons (uitk option-box): click resets a field to its
        # default; Alt/Ctrl+click bypasses it to default (greyed, restorable).
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # Connect sliders and checkboxes to preview refresh function.
        # s000 Amount, s001 Offset, s002 Spacing, s003 Weight Bias, s004 Weight Curve.
        self.sb.connect_multi(self.ui, "chk001-6", "clicked", self.preview.refresh)
        self.sb.connect_multi(self.ui, "s000-4", "valueChanged", self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb001.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb001.currentIndexChanged.connect(self.toggle_weight_ui)

        # Initialize the weight-field enabled state for the default mode.
        self.toggle_weight_ui()

        # Refresh preview when the viewport pivot changes (selection, tool,
        # or manipulator drag release). Gated to active preview only; the
        # watcher dedups by selection+context so the deferred
        # SelectionChanged fired by cmds.select inside perform_operation
        # does not re-enter.
        self._pivot_watcher = PivotWatcher(
            self.preview.refresh,
            gate=lambda: self.preview.is_enabled,
            owner=self,
        )
        self._pivot_watcher.start()
        self._pivot_watcher.attach_widget(self.ui)

    def header_init(self, widget):
        """Configure header help text."""
        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.set_help_text(
            TooltipFormat.fmt(
                title="Cut on Axis",
                body="Slice selected meshes along an axis, then optionally "
                "delete or mirror the cut half.",
                steps=[
                    "Select one or more polygon transforms.",
                    "Check an <b>Axis</b> (X / -X / Y / -Y / Z / -Z).",
                    "Pick a <b>Pivot</b> — Manip / Object / World / Center.",
                    "Set <b>Amount</b> (number of slices) and <b>Offset</b>.",
                    "Optionally set <b>Spacing</b> and an <b>Interpolation</b> "
                    "curve to control cut distribution.",
                    "Toggle <b>Preview</b>, then press <b>Cut</b> to commit.",
                ],
                sections=[
                    (
                        "Options",
                        [
                            "<b>Spacing</b> — gap between adjacent cuts. 0 fills "
                            "the axis evenly; &gt;0 fixes the span between cuts.",
                            "<b>Interpolation</b> — distribute cuts across the "
                            "span (linear keeps them even; ease/weighted bias "
                            "density toward one end). <b>Bias</b>/<b>Curve</b> "
                            "tune the selected curve.",
                            "<b>Delete</b> — discard faces on the negative side of "
                            "the axis after cutting.",
                            "<b>Mirror</b> — after deleting one side, mirror the "
                            "remaining half across the axis to rebuild symmetric "
                            "geometry.",
                        ],
                    ),
                ],
            )
        )

    def toggle_weight_ui(self):
        """Enable the weight fields only for the modes that consume them.

        Mirrors DuplicateLinearSlots.toggle_weight_ui: 'linear'/'smooth_step'
        use neither; 'weighted' uses both bias and curve; every other mode uses
        the curve only.
        """
        mode = self.ui.cmb001.currentData()
        uses_curve = mode not in ("linear", "smooth_step")
        uses_bias = mode == "weighted"
        self.ui.s003.setEnabled(uses_bias)  # Weight Bias
        self.ui.s004.setEnabled(uses_curve)  # Weight Curve

    def perform_operation(self, objects, contract):
        axis = self.sb.get_axis_from_checkboxes("chk001-4", self.ui)
        pivot_index = self.ui.cmb000.currentIndex()
        cuts = self.ui.s000.value()
        cut_offset = self.ui.s001.value()
        cut_spacing = self.ui.s002.value()
        distribution = self.ui.cmb001.currentData()
        weight_bias = self.ui.s003.value()
        weight_curve = self.ui.s004.value()
        delete = self.ui.chk005.isChecked()
        mirror = self.ui.chk006.isChecked()

        # Map UI combo box index to pivot strings
        pivot_options = ["manip", "object", "world", "center"]
        pivot = (
            pivot_options[pivot_index] if pivot_index < len(pivot_options) else "center"
        )

        CutOnAxis.perform_cut_on_axis(
            objects,
            axis=axis,
            pivot=pivot,
            cuts=cuts,
            cut_offset=cut_offset,
            cut_spacing=cut_spacing,
            distribution=distribution,
            weight_bias=weight_bias,
            weight_curve=weight_curve,
            delete=delete,
            mirror=mirror,
            use_object_axes=True,  # Default to using object axes for better behavior
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("cut_on_axis", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
