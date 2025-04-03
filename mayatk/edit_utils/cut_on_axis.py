# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk import core_utils
from mayatk.edit_utils import EditUtils
from mayatk.node_utils import NodeUtils
from mayatk.core_utils import preview


class CutOnAxis:
    @staticmethod
    @core_utils.CoreUtils.undoable
    def perform_cut_on_axis(
        objects, axis="-x", cuts=0, cut_offset=0, delete=False, mirror=False, pivot=0
    ):
        """Iterates over provided objects and performs cut or delete operations based on the axis specified.

        Parameters:
            objects (list): The list of mesh objects to be processed.
            axis (str): The axis to cut or delete along ('x', '-x', 'y', '-y', 'z', '-z'). Default is '-x'.
            cuts (int): The number of cuts to make. Default is 0.
            cut_offset (float): Offset amount from the center for the cut. Default is 0.
            delete (bool): If True, delete the faces on the specified axis. Default is False.
            mirrot (bool): After deleting, mirror the object(s).
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
            )

            pm.select(objects)


class CutOnAxisSlots:
    def __init__(self, **kwargs):
        # Initialize the switchboard and UI here
        self.sb = kwargs.get("switchboard")
        self.ui = self.sb.loaded_ui.cut_on_axis

        self.preview = preview.Preview(
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

        pivot_mapping = {
            0: "object",
            1: "world",
            2: "center",
        }
        pivot = pivot_mapping.get(pivot_index, "center")

        CutOnAxis.perform_cut_on_axis(
            objects,
            axis=axis,
            pivot=pivot,
            cuts=cuts,
            cut_offset=cut_offset,
            delete=delete,
            mirror=mirror,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("cut_on_axis", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
