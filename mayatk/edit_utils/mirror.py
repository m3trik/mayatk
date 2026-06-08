# !/usr/bin/python
# coding=utf-8
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

# from this package:
from mayatk.core_utils.preview import Preview
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.node_utils._node_utils import NodeUtils
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

        # Per-field reset buttons (uitk option-box) on the Pivot / Merge Mode
        # combos — Mirror has no numeric params, and the Axis checkboxes are a
        # mutually-exclusive group (a per-box reset would be confusing). Click
        # resets the combo to its default; Alt/Ctrl+click bypasses it.
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui, "cmb000-1")

        self.preview = Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        # The '-' (negative axis) toggle only changes the result for the
        # bounding-box pivots — Center symmetrizes and the sign picks which half
        # survives; Border picks the min vs max face. For Manip/Object/World the
        # mirror reflects across a fixed plane, so the sign is a no-op there.
        # Connect BEFORE the preview-refresh wiring so the enabled/checked state
        # is settled before perform_operation re-reads the axis on a pivot change.
        self.ui.cmb000.currentIndexChanged.connect(self._sync_axis_sign_enabled)

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

        # Settle the '-' toggle's enabled state for the initial (default /
        # restored) pivot before the user interacts.
        self._sync_axis_sign_enabled()

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Mirror",
                body="Mirror selected geometry across an axis, optionally "
                "merging seam vertices and discarding the original half.",
                steps=[
                    "Select one or more polygon transforms.",
                    "Check an <b>Axis</b> (X / Y / Z). The <b>—</b> toggle makes "
                    "it negative; it only applies to the Bounding Box pivots and "
                    "is disabled otherwise.",
                    "Pick a <b>Pivot</b> — Manip / Object / World, or a Bounding "
                    "Box pivot (see below).",
                    "Pick a <b>Merge Mode</b>.",
                    "Toggle <b>Preview</b> to iterate, or press <b>Mirror</b> "
                    "to commit.",
                ],
                sections=[
                    ("Bounding Box pivots", [
                        "<b>Center</b> — keep one half and mirror it across the "
                        "center to symmetrize. The <b>—</b> toggle flips which "
                        "half is kept.",
                        "<b>Border</b> — mirror across the max face of the axis; "
                        "the <b>—</b> toggle flips it to the min face.",
                    ]),
                    ("Options", [
                        "<b>Uninstance</b> — break instance links before mirroring.",
                        "<b>Delete Original Half</b> — discard the source side "
                        "after the mirror copy is created.",
                    ]),
                ],
            )
        )

    def perform_operation(self, objects, contract):
        # Read values from UI
        axis = self.sb.get_axis_from_checkboxes(
            "chk001-4", self.ui
        )  # e.g. "x" or "-x"; "-" only honored for the bounding-box pivots
        # get_axis_from_checkboxes returns "" / "-" when no X/Y/Z is selected;
        # guard here so both paths give the same clear error instead of a raw
        # KeyError downstream (cut_along_axis doesn't validate the axis).
        if axis.lstrip("-") not in ("x", "y", "z"):
            raise ValueError("Select an axis (X / Y / Z) to mirror across.")

        pivot_index = self.ui.cmb000.currentIndex()
        uninstance = self.ui.chk005.isChecked()

        # Bounding Box (center): reflecting the whole object across its own
        # center just overlaps it, so this pivot SYMMETRIZES instead — cut at
        # the center, keep one half, and mirror it across the cut plane. The
        # axis sign picks which half survives; cut_along_axis's convention is
        # inverted vs. this panel ("x" there deletes the +X half), so invert=True
        # makes the UI's "+X" keep the +X half.
        if pivot_index == 3:
            if uninstance:
                objects = NodeUtils.uninstance(objects)
            EditUtils.cut_along_axis(
                objects,
                axis=axis,
                invert=True,
                pivot="center",
                amount=1,
                delete=True,
                mirror=True,
                use_object_axes=True,
            )
            return

        pivot = self._resolve_pivot(pivot_index, axis)
        mergeMode = (
            self.ui.cmb001.currentIndex() - 1
        )  # Adjust mergeMode to match Method signature (-1 for correct mapping)

        EditUtils.mirror(
            objects,
            axis=axis,
            pivot=pivot,
            mergeMode=mergeMode,
            uninstance=uninstance,
            delete_original=self.ui.chk006.isChecked(),
        )

    @staticmethod
    def _axis_sign_relevant(pivot_index: int) -> bool:
        """Whether the '-' (negative axis) toggle changes the mirror result.

        Only the bounding-box pivots use the sign: Center (index 3) picks which
        half survives the symmetrize; Border (index 4) picks the min vs max face.
        Manip / Object / World reflect across a fixed plane, so the sign is a
        no-op there and the toggle is disabled.
        """
        return pivot_index in (3, 4)

    def _sync_axis_sign_enabled(self, *args) -> None:
        """Enable the '-' toggle only where the sign matters; uncheck it when
        disabling so a stale sign can't leak into a pivot that ignores it."""
        relevant = self._axis_sign_relevant(self.ui.cmb000.currentIndex())
        self.ui.chk001.setEnabled(relevant)
        if not relevant and self.ui.chk001.isChecked():
            self.ui.chk001.setChecked(False)

    @staticmethod
    def _resolve_pivot(pivot_index: int, axis: str) -> str:
        # Bounding-box BORDER pivot (index 4): the axis sign selects which face
        # the mirror reflects across — +axis -> max face, -axis -> min face —
        # flipping the side the geometry doubles toward. Unknown axis -> xmax.
        base = axis.lstrip("-")
        if base in ("x", "y", "z"):
            face = f"{base}min" if axis.startswith("-") else f"{base}max"
        else:
            face = "xmax"

        pivot_mapping = {
            0: "manip",
            1: "object",
            2: "world",
            3: "center",
            4: face,
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
