# !/usr/bin/python
# coding=utf-8
"""The Shot Manifest table's Start / End column: what a step's range is
(typed, auto-filled from the scene's animation regions, or its shot's), how a
typed one is checked and cascades, and how the cells show it.

The rules are ``pythontk``'s ``RangeResolver``; this is the column's glue.  A
double-click or a committed cell is offered to Local Edits first.

Shared text: blendertk carries this module identical
(``m3trik/scripts/check_dcc_twins.py``); mayatk's is the one edited, then
copied over.  Only ``manifest_host.py`` differs between the two.
"""

from typing import List, Optional, Tuple

import pythontk as ptk
from pythontk import BuilderStep

from .manifest_data import COL_END, COL_START, PASTEL_STATUS
from .manifest_host import ManifestHost
from .range_resolver import RangeResolver


class RangeColumnMixin:
    """The Start / End column, mixed into ``ShotManifestController``.

    Uses the controller's ``ui.tbl_steps``, ``_steps``, ``_user_ranges``,
    ``_last_resolved``, ``_cached_gaps`` / ``_cached_gap_ends``, ``_editor``,
    ``_pairing()``, ``_step_is_built()``, ``_active_store()``,
    ``_detect_regions()``, ``_initial_shot_length`` and ``sb``.
    """

    @property
    def _is_detection_mode(self) -> bool:
        """True when the steps came from the scene (its shots or detected
        animation), not a CSV -- so a build never removes a shot."""
        return bool(self._steps) and not self._csv_path

    @property
    def _use_selected_keys(self) -> bool:
        """True when a selected-keys detection mode is active.

        The store's detection_mode controls this regardless of whether
        steps came from a CSV or from scene detection.  CSV defines
        step names/objects; the detection mode defines how ranges are
        inferred.
        """
        store = self._active_store()
        return store is not None and store.detection_mode != "auto"

    def _all_ranges_complete(self) -> bool:
        """True when every step has a user-supplied (start, end) pair."""
        return ptk.RangeResolver.all_ranges_complete(self._steps, self._user_ranges)

    def _on_range_double_clicked(self, item, column) -> None:
        """Edit the double-clicked cell, else toggle the row.

        A step's name or description, or an object's name, edits through
        Local Edits (:class:`ManifestEditor`); Start / End edit
        on an unbuilt step's row.  Any other cell of a step row toggles it.
        """
        if self._editor.begin_edit(item, column):
            return
        from qtpy.QtCore import Qt

        is_parent = item.parent() is None
        if is_parent and column in (COL_START, COL_END):
            step_data = item.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep) and not self._step_is_built(
                step_data.step_id
            ):
                self.ui.tbl_steps.editItem(item, column)
                return
        if is_parent:
            item.setExpanded(not item.isExpanded())

    def _on_item_changed(self, item, column) -> None:
        """Capture a committed cell: Local Edits' cells go to the editor
        (:meth:`ManifestEditor.commit_edit`); Start / End are ranges.

        Validation rules (Start/End):
        - Negative start values are rejected.
        - End must be > start when both are given.
        - Start must not precede the previous step's resolved end.

        After a valid range edit, downstream user ranges are cleared so
        the resolver can re-flow them from the new anchor, and the full
        table is refreshed.
        """
        if self._editor.commit_edit(item, column):
            return
        if column not in (COL_START, COL_END):
            return
        if item.parent() is not None:
            return
        from qtpy.QtCore import Qt

        step_data = item.data(0, Qt.UserRole)
        if not isinstance(step_data, BuilderStep):
            return

        # The edit rules (numbers, a start, start >= 0, end > start) are
        # RangeResolver.parse_range_edit's; both cells empty clears the range.
        try:
            user_range = ptk.RangeResolver.parse_range_edit(
                item.text(COL_START), item.text(COL_END)
            )
        except ValueError:
            self._revert_range_cell(item, step_data.step_id)
            return
        if user_range is None:
            self._user_ranges.pop(step_data.step_id, None)
            self._refresh_ranges()
            return
        start, end = user_range

        # Reject start before the previous step's resolved end.
        step_idx = self._step_index(step_data.step_id)
        if step_idx < 0:
            return
        prev_end = ptk.RangeResolver.previous_end(
            self._steps, self._last_resolved, step_idx
        )
        if prev_end is not None and start < prev_end:
            self._revert_range_cell(item, step_data.step_id)
            return

        # Valid — store, clear downstream, and refresh.
        self._user_ranges[step_data.step_id] = (start, end)
        self._cascade_from(step_idx)
        self._refresh_ranges(from_step_idx=step_idx)

    def _step_index(self, step_id: str) -> int:
        """Return the list index for *step_id*, or -1 if not found."""
        return ptk.RangeResolver.step_index(self._steps, step_id)

    def _refresh_ranges(self, from_step_idx: int = 0) -> list:
        """Re-resolve, auto-fill, and validate all ranges.

        This is the single entry point for updating the Range column
        after any edit, cascade, or clear operation.

        Parameters
        ----------
        from_step_idx
            Passed to :meth:`_resolve_ranges` so steps before this
            index keep their last-resolved positions.

        Returns the resolved ranges list.
        """
        resolved = self._auto_fill_ranges(
            resolved=self._resolve_ranges(from_step_idx=from_step_idx)
        )
        self._validate_range_collisions(resolved)
        return resolved

    def _cascade_from(self, step_idx: int) -> None:
        """Clear user ranges on all steps after *step_idx* so they re-flow."""
        ptk.RangeResolver.cascade_from(self._steps, self._user_ranges, step_idx)

    def _placement_on_regions(self) -> Optional[bool]:
        """Whether new shots go on the detected animation regions (``True``),
        one after another (``False``), or the build stops (``None``).

        Steps meet regions IN ORDER, which is right only when there is one
        region per step; when the counts differ that pairing is a guess, so
        the user decides with both counts in front of them.  Selected keys
        are the user's own boundaries and never ask.
        """
        regions = len(self._cached_gaps or [])
        auto = [s for s in self._steps if s.step_id not in self._user_ranges]
        if self._use_selected_keys or not regions or len(auto) in (0, regions):
            return True
        answer = self.sb.message_box(
            f"<b>{len(auto)} steps, {regions} animation regions.</b><br>"
            "New shots are placed on the scene's animation regions in order, "
            "which only lines up with one region per step.<br><br>"
            "<b>Yes</b> \u2014 place them on the regions anyway<br>"
            "<b>No</b> \u2014 place them one after another (adjust later)<br>"
            "<b>Cancel</b> \u2014 set the steps' ranges first",
            "Yes",
            "No",
            "Cancel",
        )
        if answer == "Yes":
            return True
        return False if answer == "No" else None

    def _resolve_ranges(
        self,
        from_step_idx: int = 0,
        regions: bool = True,
    ) -> List[Tuple[str, float, Optional[float], bool]]:
        """Compute a resolved (start, end) for every step.

        Detects/caches animation regions, then delegates to the
        standalone :func:`._range_resolver.resolve_ranges` algorithm.
        ``regions=False`` ignores them: steps are placed one after another.
        """
        if not self._steps:
            return []

        store = self._active_store()
        gap = store.gap if store else 0.0
        det_threshold = store.detection_threshold if store else 5.0
        use_sel = self._use_selected_keys

        # Detect animation regions for auto-fill (cached per assess cycle).
        if self._cached_gaps is not None:
            gap_starts = self._cached_gaps
        else:
            gap_starts, self._cached_gap_ends = ptk.RangeResolver.gaps_from_regions(
                self._detect_regions(det_threshold)
            )
            self._cached_gaps = gap_starts

        if not regions:
            gap_starts = []
        if use_sel and not gap_starts:
            return []

        # When no animation regions are detected (regardless of mode),
        # use uniform default durations so steps get sensible placeholder
        # ranges instead of behavior-derived micro-durations.  This also
        # covers the case where the scene has animation but the chosen
        # detection mode found no boundaries (e.g. skip_zero with no
        # zero-valued keys).  The store's initial_shot_length is the
        # user-facing policy for new-shot sizing (default 200f).
        default_dur = (
            self._initial_shot_length if (not gap_starts and not use_sel) else 0
        )

        resolved = RangeResolver.resolve_ranges(
            steps=self._steps,
            user_ranges=self._user_ranges,
            gap_starts=gap_starts,
            gap_end_map=self._cached_gap_ends or {},
            gap=gap,
            use_selected_keys=use_sel,
            last_resolved=self._last_resolved,
            from_step_idx=from_step_idx,
            default_duration=default_dur,
        )
        self._last_resolved = resolved
        return resolved

    def _set_range_to_current_frame(self, item, step_id: str) -> None:
        """Set the range start for *step_id* to the playhead's frame
        (``ManifestHost.current_frame``).

        Clears user ranges on subsequent steps so they cascade from the
        new anchor point.
        """
        frame = ManifestHost.current_frame()
        if frame is None:
            return
        self._user_ranges[step_id] = (frame, None)

        step_idx = self._step_index(step_id)
        self._cascade_from(step_idx)
        self._refresh_ranges(from_step_idx=step_idx)

    def _auto_fill_ranges(self, resolved=None) -> list:
        """Auto-fill the Range column using resolved ranges.

        User-entered values are preserved; auto-filled values appear dim
        and italic.

        Parameters
        ----------
        resolved
            Pre-computed resolved ranges.  When ``None``,
            :meth:`_resolve_ranges` is called internally.

        Returns
        -------
        list
            The resolved ranges list (for reuse by collision validation).
        """
        if resolved is None:
            resolved = self._resolve_ranges()
        if not resolved:
            return resolved

        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        tree = self.ui.tbl_steps
        dim = QBrush(QColor(PASTEL_STATUS["locked"][0]))
        step_map = {r[0]: r for r in resolved}
        # A built row shows its shot (``_refresh_timing``), never a guess.
        built = self._pairing().shots

        tree.blockSignals(True)
        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                step_data = parent.data(0, Qt.UserRole)
                if not isinstance(step_data, BuilderStep):
                    continue
                entry = step_map.get(step_data.step_id)
                if entry is None or step_data.step_id in built:
                    continue
                step_id, start, end, is_user = entry
                parent.setText(COL_START, f"{start:.0f}")
                if end is not None:
                    parent.setText(COL_END, f"{end:.0f}")
                else:
                    parent.setText(COL_END, "")
                for col in (COL_START, COL_END):
                    font = parent.font(col)
                    if not is_user:
                        parent.setForeground(col, dim)
                        font.setItalic(True)
                    else:
                        font.setItalic(False)
                    parent.setFont(col, font)
        finally:
            tree.blockSignals(False)
        return resolved

    def _validate_range_collisions(self, resolved=None) -> int:
        """Check adjacent ranges for ordering violations and color conflicts.

        Resets Start/End column foreground on all items, then recolors
        collision participants in pastel red.

        Returns the number of collisions found.
        """
        if resolved is None:
            resolved = self._resolve_ranges()
        if len(resolved) < 2:
            return 0

        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        tree = self.ui.tbl_steps
        c_fg, c_bg = PASTEL_STATUS["collision"]
        collision_fg = QBrush(QColor(c_fg))
        collision_bg = QBrush(QColor(c_bg))
        dim = QBrush(QColor(PASTEL_STATUS["locked"][0]))
        collisions = 0

        # Build a map of step_id → tree item for quick lookup
        item_map: dict = {}
        resolved_map: dict = {}
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep):
                item_map[step_data.step_id] = parent
        for r in resolved:
            resolved_map[r[0]] = r

        # Block signals to prevent _on_item_changed from firing
        # recursively while we update foregrounds/tooltips.
        tree.blockSignals(True)
        try:
            # First pass: reset foreground, background, and tooltip for all range cells
            for sid, item in item_map.items():
                entry = resolved_map.get(sid)
                is_user = entry[3] if entry else False
                brush = QBrush() if is_user else dim
                for col in (COL_START, COL_END):
                    item.setForeground(col, brush)
                    item.setBackground(col, QBrush())
                    item.setToolTip(col, "")

            # Second pass: mark collision items
            for curr_id, next_id in ptk.RangeResolver.find_collisions(resolved):
                collisions += 1
                for sid in (curr_id, next_id):
                    item = item_map.get(sid)
                    if item is not None:
                        for col in (COL_START, COL_END):
                            item.setForeground(col, collision_fg)
                            item.setBackground(col, collision_bg)
                            item.setToolTip(
                                col,
                                "Range collision: overlaps with adjacent step",
                            )
        finally:
            tree.blockSignals(False)

        return collisions

    def _revert_range_cell(self, item, step_id: str) -> None:
        """Revert Start/End cells to their last resolved values after a rejected edit."""
        tree = self.ui.tbl_steps
        tree.blockSignals(True)
        for entry in self._last_resolved:
            if entry[0] == step_id:
                _, s, e, _ = entry
                item.setText(COL_START, f"{s:.0f}")
                item.setText(COL_END, f"{e:.0f}" if e is not None else "")
                break
        else:
            item.setText(COL_START, "")
            item.setText(COL_END, "")
        tree.blockSignals(False)

    def _restore_user_ranges(self, tree) -> None:
        """Write ``_user_ranges`` values back into Start/End cells after a table rebuild."""
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        dim = QBrush(QColor(PASTEL_STATUS["locked"][0]))
        tree.blockSignals(True)
        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                step_data = parent.data(0, Qt.UserRole)
                if not isinstance(step_data, BuilderStep):
                    continue
                user_range = self._user_ranges.get(step_data.step_id)
                if user_range is None:
                    # Auto-filled values appear dim (set by assess/auto-fill later)
                    if parent.text(COL_START):
                        parent.setForeground(COL_START, dim)
                        parent.setForeground(COL_END, dim)
                    continue
                start, end = user_range
                parent.setText(COL_START, f"{start:.0f}")
                if end is not None:
                    parent.setText(COL_END, f"{end:.0f}")
                else:
                    parent.setText(COL_END, "")
        finally:
            tree.blockSignals(False)
