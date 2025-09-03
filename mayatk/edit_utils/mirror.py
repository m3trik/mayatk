# !/usr/bin/python
# coding=utf-8
# try:
#     import pymel.core as pm
# except ImportError as error:
#     print(__file__, error)
# from this package:
from mayatk.core_utils import preview
from mayatk.edit_utils import EditUtils


class MirrorSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.mirror

        self.preview = preview.Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # Connect sliders and checkboxes to preview refresh function
        self.sb.connect_multi(
            self.ui, "cmb000-2", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(self.ui, "chk001-5", "clicked", self.preview.refresh)

    def perform_operation(self, objects):
        # Read values from UI
        axis = self.sb.get_axis_from_checkboxes(
            "chk001-4", self.ui
        )  # Get axis from checkboxes
        pivot_index = (
            self.ui.cmb000.currentIndex()
        )  # Get UI selection for pivot dropdown
        pivot = self._resolve_pivot(
            pivot_index, axis
        )  # Dynamically resolve correct pivot

        # Get axis mode from new UI control (default to object axes)
        use_object_axes = self.ui.cmb002.currentIndex() == 0

        mergeMode = (
            self.ui.cmb001.currentIndex() - 1
        )  # Adjust mergeMode to match Method signature (-1 for correct mapping)

        kwargs = {
            "axis": axis,
            "pivot": pivot,
            "mergeMode": mergeMode,
            "uninstance": self.ui.chk005.isChecked(),  # Uninstance objects before mirroring
            "use_object_axes": use_object_axes,  # New parameter for axis mode
        }

        EditUtils.mirror(objects, **kwargs)

    @staticmethod
    def _resolve_pivot(pivot_index: int, axis: str) -> str:
        axis_mapping = {
            "x": "xmax",
            "-x": "xmax",
            "y": "ymax",
            "-y": "ymax",
            "z": "zmax",
            "-z": "zmax",
        }

        pivot_mapping = {
            0: "manip",  # Manipulation pivot (scale pivot)
            1: "object",  # Object pivot (rotate pivot)
            2: "world",  # World origin
            3: "center",  # Bounding box center
            4: axis_mapping.get(axis, "xmax"),  # Bounding box border
        }

        return pivot_mapping.get(pivot_index, "manip")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("mirror", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
