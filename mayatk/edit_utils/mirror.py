# !/usr/bin/python
# coding=utf-8
# try:
#     import pymel.core as pm
# except ImportError as error:
#     print(__file__, error)
# from this package:
from mayatk.core_utils import CoreUtils, Preview
from mayatk.edit_utils import EditUtils


class Mirror:
    @staticmethod
    @CoreUtils.undo
    def perform_mirror(*args, **kwargs):
        """Mirror geometry across a given axis.

        Parameters:
            objects (obj): The objects to mirror.
            axis (string): The axis in which to perform the mirror along. case insensitive. (valid: 'x', '-x', 'y', '-y', 'z', '-z')
            axis_pivot (int): The pivot on which to mirror on. valid: 0) Bounding Box, 1) Object, 2) World.
            cut_mesh (bool): Perform a delete along specified axis before mirror.
            merge_mode (int): 0) Do not merge border edges. 1) Border edges merged. 2) Border edges extruded and connected.
            merge_threshold (float): Merge vertex distance.
            delete_original (bool): Delete the original objects after mirroring.
            deleteHistory (bool): Delete non-deformer history on the object(s) before performing the operation.
            uninstance (bool): Un-instance the object(s) before performing the operation.

        Returns:
            (obj) The polyMirrorFace history node if a single object, else None.
        """
        EditUtils.mirror(*args, **kwargs)


class MirrorSlots:
    def __init__(self):
        # Initialize the switchboard and UI here
        self.sb = self.switchboard()
        self.ui = self.sb.mirror
        self.preview = Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # Connect sliders and checkboxes to preview refresh function
        self.sb.connect_multi(self.ui, "chk001-10", "clicked", self.preview.refresh)

    def perform_operation(self, objects):
        # Read values from UI and execute mirror operation
        axis = self.sb.get_axis_from_checkboxes("chk001-4", self.ui)
        axis_pivot = 2 if self.ui.chk008.isChecked() else 1
        cut_mesh = self.ui.chk005.isChecked()
        merge = self.ui.chk007.isChecked()
        delete_original = self.ui.chk010.isChecked()
        delete_history = self.ui.chk006.isChecked()
        uninstance = self.ui.chk009.isChecked()

        Mirror.perform_mirror(
            objects,
            axis=axis,
            axis_pivot=axis_pivot,
            cut_mesh=cut_mesh,
            merge_mode=2 if merge else 0,
            delete_original=delete_original,
            deleteHistory=delete_history,
            uninstance=uninstance,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "mirror.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=MirrorSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(
        Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True
    )
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")

    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
