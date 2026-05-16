# !/usr/bin/python
# coding=utf-8
import pythontk as ptk

# from this package:
from mayatk.core_utils.preview import Preview
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.xform_utils.pivot_watcher import PivotWatcher


class MirrorSlots(ptk.LoggingMixin):
    # polySeparate inside EditUtils.mirror deletes the original transform.
    # MUTATES_SELECTION=True tells Preview to duplicate+hide the selection
    # before perform_operation so rollback can restore it.
    MUTATES_SELECTION = True

    def __init__(self, switchboard, log_level="INFO"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.mirror

        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[Mirror] ")

        self.preview = Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # Connect sliders and checkboxes to preview refresh function
        self.sb.connect_multi(
            self.ui, "cmb000-1", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(self.ui, "chk001-6", "clicked", self.preview.refresh)

        # Refresh preview when the viewport pivot changes (selection, tool,
        # or manipulator drag release). EditUtils.mirror deletes and
        # re-selects the transform, which fires SelectionChanged on the
        # next idle — the watcher's signature dedup absorbs that self-fire
        # to break what would otherwise be an infinite refresh loop.
        self._pivot_watcher = PivotWatcher(
            self.preview.refresh,
            gate=lambda: self.preview.is_enabled,
            owner=self,
        )
        self._pivot_watcher.start()
        self._pivot_watcher.attach_widget(self.ui)

    def header_init(self, widget):
        """Configure header menu with tool instructions."""
        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Mirror — Mirror selected geometry across an axis.\n\n"
                "• Choose axis (X, Y, Z) and pivot reference.\n"
                "• Merge border vertices option.\n"
                "• Enable Preview for non-destructive iteration."
            ),
        )

    def perform_operation(self, objects, contract):
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
            "uninstance": self.ui.chk005.isChecked(),  # Uninstance objects before mirroring
            "delete_original": self.ui.chk006.isChecked(),  # Delete original half
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
            0: "manip",
            1: "object",
            2: "world",
            3: "center",
            4: axis_mapping.get(axis, "xmax"),
        }

        return pivot_mapping.get(pivot_index, "manip")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("mirror", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
