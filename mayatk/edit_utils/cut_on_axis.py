# !/usr/bin/python
# coding=utf-8
# from this package:
import maya.cmds as cmds
from uitk.widgets.mixins.tooltip_mixin import fmt
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.node_utils._node_utils import NodeUtils
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

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.cut_on_axis

        self.preview = Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # Connect sliders and checkboxes to preview refresh function
        self.sb.connect_multi(self.ui, "chk001-6", "clicked", self.preview.refresh)
        self.sb.connect_multi(self.ui, "s000-1", "valueChanged", self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)

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
        widget.set_help_text(
            fmt(
                title="Cut on Axis",
                body="Slice selected meshes along an axis, then optionally "
                "delete or mirror the cut half.",
                steps=[
                    "Select one or more polygon transforms.",
                    "Check an <b>Axis</b> (X / -X / Y / -Y / Z / -Z).",
                    "Pick a <b>Pivot</b> — Manip / Object / World / Center.",
                    "Set <b>Cuts</b> (number of slices) and <b>Offset</b>.",
                    "Toggle <b>Preview</b>, then press <b>Cut</b> to commit.",
                ],
                sections=[
                    ("Options", [
                        "<b>Delete</b> — discard faces on the negative side of "
                        "the axis after cutting.",
                        "<b>Mirror</b> — after deleting one side, mirror the "
                        "remaining half across the axis to rebuild symmetric "
                        "geometry.",
                    ]),
                ],
            )
        )

    def perform_operation(self, objects, contract):
        axis = self.sb.get_axis_from_checkboxes("chk001-4", self.ui)
        pivot_index = self.ui.cmb000.currentIndex()
        cuts = self.ui.s000.value()
        cut_offset = self.ui.s001.value()
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
