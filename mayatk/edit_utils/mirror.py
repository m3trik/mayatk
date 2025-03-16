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
    def __init__(self, **kwargs):
        # Initialize the switchboard and UI here
        self.sb = kwargs.get("switchboard")
        self.ui = self.sb.loaded_ui.mirror

        self.preview = preview.Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # Connect sliders and checkboxes to preview refresh function
        self.sb.connect_multi(
            self.ui, "cmb000-1", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(self.ui, "chk001-6", "clicked", self.preview.refresh)

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

        mergeMode = (
            self.ui.cmb001.currentIndex() - 1
        )  # Adjust mergeMode to match Method signature (-1 for correct mapping)

        kwargs = {
            "axis": axis,
            "pivot": pivot,
            "mergeMode": mergeMode,
            "cutMesh": self.ui.chk005.isChecked(),  # Valid flag for polyMirrorFace
            "uninstance": self.ui.chk006.isChecked(),  # Uninstance objects before mirroring
        }

        EditUtils.mirror(
            objects, **kwargs
        )  # Call mirror method with resolved parameters

    @staticmethod
    def _resolve_pivot(pivot_index: int, axis: str) -> str:
        """
        Resolves the correct pivot parameter for mirroring based on the axis selection.

        Parameters:
            pivot_index (int): UI dropdown index for pivot selection.
            axis (str): The chosen mirror axis ('x', '-x', 'y', '-y', 'z', '-z').

        Returns:
            str: The appropriate pivot type ('object', 'world', 'xmin', 'xmax', etc.).
        """
        # Define min/max mappings for each axis
        axis_mapping = {
            "x": ("xmin", "xmax"),
            "-x": ("xmin", "xmax"),
            "y": ("ymin", "ymax"),
            "-y": ("ymin", "ymax"),
            "z": ("zmin", "zmax"),
            "-z": ("zmin", "zmax"),
        }

        # Get the appropriate bounding box min/max keys for the selected axis
        bbox_min, bbox_max = axis_mapping.get(
            axis, ("xmin", "xmax")
        )  # Default to X if invalid

        # Pivot selection mapping based on UI input
        pivot_mapping = {
            0: "object",  # Object's pivot point
            1: "world",  # World origin (0,0,0)
            2: "center",  # Bounding box center
            3: bbox_max,  # Maximum bound of the selected axis
        }

        return pivot_mapping.get(
            pivot_index, "object"
        )  # Default to object pivot if out of range


class MirrorUi:
    def __new__(self):
        """Get the Mirror UI."""
        import os
        from mayatk.ui_utils.ui_manager import UiManager

        ui_file = os.path.join(os.path.dirname(__file__), "mirror.ui")
        ui = UiManager.get_ui(ui_source=ui_file, slot_source=MirrorSlots)
        return ui


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    MirrorUi().show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
