# !/usr/bin/python
# coding=utf-8
# try:
#     import pymel.core as pm
# except ImportError as error:
#     print(__file__, error)
# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.core_utils import preview
from mayatk.edit_utils import EditUtils


class MirrorSlots:
    def __init__(self):
        # Initialize the switchboard and UI here
        self.sb = self.switchboard()
        self.ui = self.sb.mirror
        self.preview = preview.Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # Connect sliders and checkboxes to preview refresh function
        self.sb.connect_multi(
            self.ui, "cmb000-1", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(self.ui, "chk001-7", "clicked", self.preview.refresh)

    def perform_operation(self, objects):
        # Read values from UI and execute mirror operation
        kwargs = {
            "axis": self.sb.get_axis_from_checkboxes("chk001-4", self.ui),
            "mirrorAxis": self.ui.cmb000.currentIndex(),
            "mergeMode": self.ui.cmb001.currentIndex(),
            "cutMesh": self.ui.chk005.isChecked(),
            "delete_history": self.ui.chk006.isChecked(),
            "uninstance": self.ui.chk007.isChecked(),
        }
        EditUtils.mirror(objects, **kwargs)
        # ex. # polyMirrorFace  -cutMesh 1 -axis 0 -axisDirection 1 -mergeMode 1 -mergeThresholdType 0 -mergeThreshold 0.001 -mirrorAxis 0 -mirrorPosition 0 -smoothingAngle 30 -flipUVs 0 -ch 1 S102_BOOST_PUMP_CANISTER_B;


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "mirror.ui")
    sb = Switchboard(parent, ui_source=ui_file, slot_source=MirrorSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(
        Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True
    )
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")

    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
