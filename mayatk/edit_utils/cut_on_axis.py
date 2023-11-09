# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.core_utils import CoreUtils, Preview
from mayatk.edit_utils import EditUtils
from mayatk.node_utils import NodeUtils


class CutOnAxis:
    @staticmethod
    @CoreUtils.undo
    def perform_cut_on_axis(objects, axis="-x", cut=False, delete=False, mirror=False):
        """Iterates over provided objects and performs cut or delete operations based on the axis specified.

        Parameters:
            objects (list): The list of mesh objects to be processed.
            axis (str): The axis to cut or delete along ('x', '-x', 'y', '-y', 'z', '-z'). Default is '-x'.
            cut (bool): If True, perform a cut operation. Default is False.
            delete (bool): If True, delete the faces on the specified axis. Default is False.
            mirrot (bool): After deleting, mirror the object(s).

        Note:
            If both 'cut' and 'delete' are False, no operation is performed.
        """
        axis = axis.lower()  # Assure lower case.

        for obj in (o for o in objects if not NodeUtils.is_group(o)):
            if cut:
                EditUtils.cut_along_axis(obj, axis, delete)

            elif delete:
                EditUtils.delete_along_axis(obj, axis)

            if mirror:
                opposing_axis = axis.strip("-") if "-" in axis else f"-{axis}"
                EditUtils.mirror(obj, opposing_axis, axis_pivot=0)

        pm.select(objects)


class CutOnAxisSlots:
    def __init__(self):
        # Initialize the switchboard and UI here
        self.sb = self.switchboard()
        self.ui = self.sb.cut_on_axis
        self.preview = Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # Connect sliders and checkboxes to preview refresh function
        self.sb.connect_multi(self.ui, "chk001-7", "clicked", self.preview.refresh)

    def perform_operation(self, objects):
        # Read values from UI and execute mirror operation
        axis = self.sb.get_axis_from_checkboxes("chk001-4", self.ui)
        cut = self.ui.chk005.isChecked()
        delete = self.ui.chk006.isChecked()
        mirror = self.ui.chk007.isChecked()

        CutOnAxis.perform_cut_on_axis(
            objects, axis=axis, cut=cut, delete=delete, mirror=mirror
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "cut_on_axis.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=CutOnAxisSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(
        Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True
    )
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")

    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
