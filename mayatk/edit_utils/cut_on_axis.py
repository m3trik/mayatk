# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.core_utils.preview import Preview


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

            pm.select(objects)


class CutOnAxisSlots:
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

    def perform_operation(self, objects):
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
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("cut_on_axis", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
