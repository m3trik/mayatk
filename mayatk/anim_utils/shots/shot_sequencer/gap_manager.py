# !/usr/bin/python
# coding=utf-8
"""Gap and range-highlight handlers for the shot sequencer controller.

Provides :class:`GapManagerMixin` — mixed into
:class:`~.shot_sequencer_slots.ShotSequencerController` to handle gap
resize, move, lock, and range-highlight interactions.
"""
from __future__ import annotations

try:
    import pymel.core as pm
except ImportError:
    pm = None

# Threshold for detecting meaningful time deltas (frame-level tolerance).
TIME_SNAP_EPS = 1e-3

__all__ = ["GapManagerMixin"]


class GapManagerMixin:
    """Mixin supplying gap-overlay and range-highlight handlers.

    Expects the host class to provide:

    * ``sequencer`` — :class:`ShotSequencer` instance
    * ``active_shot_id`` — property returning the current shot id
    * ``_save_shot_state()`` / ``_sync_to_widget()`` / ``_sync_combobox()``
    * ``_get_sequencer_widget()``
    * ``_syncing`` — bool flag
    * ``logger``
    """

    # ---- range highlight -------------------------------------------------

    def on_range_highlight_changed(self, start: float, end: float) -> None:
        """Update the active shot boundaries when the range highlight is dragged.

        If both edges shifted by the same delta it's a *move* — all keys
        in the shot are shifted and downstream shots are rippled.
        Otherwise it's a boundary resize — only the shot start/end is
        updated in the store.

        Holding **Shift** decouples keys from the range: a move updates
        boundaries only, leaving keyframes in place.
        """
        if self.sequencer is None or self.active_shot_id is None:
            return

        shot = self.sequencer.shot_by_id(self.active_shot_id)
        if shot is None:
            return

        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "shift_held_at_press", False)

        ds = start - shot.start
        de = end - shot.end

        self._save_shot_state()

        # Both edges moved by the same amount → translate entire shot.
        # NOTE: body-drag requires Shift (to avoid rubber-band selection
        # conflict), so shift_held is always True here.  We therefore
        # ignore it and always call move_shot() which moves keys + ripples.
        if abs(ds - de) < TIME_SNAP_EPS and abs(ds) > TIME_SNAP_EPS:
            self._syncing = True
            try:
                with pm.UndoChunk():
                    self.sequencer.move_shot(self.active_shot_id, start)
            finally:
                self._syncing = False
            self._gap_edit_epilogue()
            return

        # Edge resize
        self._syncing = True
        try:
            with pm.UndoChunk():
                if shift_held:
                    self.sequencer.store.update_shot(
                        self.active_shot_id, start=start, end=end
                    )
                else:
                    self.sequencer.resize_shot(self.active_shot_id, start, end)
        finally:
            self._syncing = False
        self._gap_edit_epilogue()

    # ---- helpers ---------------------------------------------------------

    def _find_shot_by_start(self, frame: float):
        """Return the shot whose start is closest to *frame*, or None."""
        for shot in self.sequencer.sorted_shots():
            if abs(shot.start - frame) < TIME_SNAP_EPS:
                return shot
        return None

    def _find_shot_by_end(self, frame: float):
        """Return the shot whose end is closest to *frame*, or None."""
        for shot in self.sequencer.sorted_shots():
            if abs(shot.end - frame) < TIME_SNAP_EPS:
                return shot
        return None

    def _gap_edit_epilogue(self):
        """Common cleanup after any gap edit."""
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        if self.sequencer is not None:
            self.sequencer.store.mark_dirty()
        self._sync_to_widget()
        self._sync_combobox()

    # ---- gap resize / move -----------------------------------------------

    def on_gap_resized(self, original_next_start: float, new_next_start: float) -> None:
        """Handle right-edge gap drag.

        The right edge of a gap is a shot's ``.start``.

        * **Inner** (the touched shot is the active shot) — the active
          shot is *scaled* so its start changes while its end is fixed.
        * **Outer** (the touched shot is *not* the active shot) — the
          adjacent shot is *slid* intact in the downstream direction
          and all further downstream shots follow.
        * **Shift+drag** — boundary-only update, no key movement or
          ripple regardless of inner/outer.
        """
        if self.sequencer is None:
            return

        delta = new_next_start - original_next_start
        if abs(delta) < TIME_SNAP_EPS:
            return

        target = self._find_shot_by_start(original_next_start)
        if target is None:
            return

        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "shift_held_at_press", False)

        self._save_shot_state()
        self._syncing = True
        try:
            with pm.UndoChunk():
                if shift_held:
                    self.sequencer.store.update_shot(
                        target.shot_id, start=target.start + delta
                    )
                elif self.active_shot_id is not None and target.shot_id == self.active_shot_id:
                    # Inner: scale active shot (start moves, end fixed).
                    # No ripple — the shot grows/shrinks into the gap.
                    old_s, old_e = target.start, target.end
                    for obj in target.objects:
                        self.sequencer.scale_object_keys(
                            obj, old_s, old_e, new_next_start, old_e
                        )
                    target.start = new_next_start
                    self.sequencer._enforce_gap_holds()
                else:
                    # Outer: slide adjacent shot downstream intact.
                    self.sequencer.slide_shot(
                        target.shot_id, new_next_start, direction="downstream"
                    )
        finally:
            self._syncing = False
        self._gap_edit_epilogue()

    def on_gap_left_resized(
        self, original_prev_end: float, new_prev_end: float
    ) -> None:
        """Handle left-edge gap drag.

        The left edge of a gap is a shot's ``.end``.

        * **Inner** (the touched shot is the active shot) — the active
          shot is *scaled* so its end changes while its start is fixed.
        * **Outer** (the touched shot is *not* the active shot) — the
          adjacent shot is *slid* intact in the upstream direction
          and all further upstream shots follow.
        * **Shift+drag** — boundary-only update, no key movement or
          ripple regardless of inner/outer.
        """
        if self.sequencer is None:
            return

        delta = new_prev_end - original_prev_end
        if abs(delta) < TIME_SNAP_EPS:
            return

        target = self._find_shot_by_end(original_prev_end)
        if target is None:
            return

        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "shift_held_at_press", False)

        self._save_shot_state()
        self._syncing = True
        try:
            with pm.UndoChunk():
                if shift_held:
                    self.sequencer.store.update_shot(
                        target.shot_id, end=new_prev_end
                    )
                elif self.active_shot_id is not None and target.shot_id == self.active_shot_id:
                    # Inner: scale active shot (end moves, start fixed).
                    # No ripple — the shot grows/shrinks into the gap.
                    old_s, old_e = target.start, target.end
                    for obj in target.objects:
                        self.sequencer.scale_object_keys(
                            obj, old_s, old_e, old_s, new_prev_end
                        )
                    target.end = new_prev_end
                    self.sequencer._enforce_gap_holds()
                else:
                    # Outer: slide adjacent shot upstream intact.
                    # Compute new start that preserves the shot's duration.
                    new_start = target.start + delta
                    self.sequencer.slide_shot(
                        target.shot_id, new_start, direction="upstream"
                    )
        finally:
            self._syncing = False
        self._gap_edit_epilogue()

    def on_gap_moved(
        self,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Handle body gap drag — slide the gap while preserving its width.

        Determines which gap this is relative to the active shot:

        * **Right gap of active shot** (left_shot == active):
          the active shot's end *scales* (inner edge) and the right
          shot *slides* downstream intact (outer edge).
        * **Left gap of active shot** (right_shot == active):
          the active shot's start *scales* (inner edge) and the left
          shot *slides* upstream intact (outer edge).
        * **Neither flanking shot is active**: both shots slide in
          their respective directions (outer-only behavior).
        """
        if self.sequencer is None:
            return

        delta = new_start - old_start
        if abs(delta) < TIME_SNAP_EPS:
            return

        left_shot = self._find_shot_by_end(old_start)
        right_shot = self._find_shot_by_start(old_end)

        if left_shot is None and right_shot is None:
            return

        active_id = self.active_shot_id

        self._save_shot_state()
        self._syncing = True
        try:
            with pm.UndoChunk():
                # Determine which shot is inner (active, gets scaled)
                # and which is outer (gets slid intact).
                # Order: slide the outer shot first, then resize the
                # inner shot, to avoid stale-position issues.

                left_is_active = (
                    left_shot is not None
                    and active_id is not None
                    and left_shot.shot_id == active_id
                )
                right_is_active = (
                    right_shot is not None
                    and active_id is not None
                    and right_shot.shot_id == active_id
                )

                if left_is_active:
                    # Right gap of active shot.
                    # Outer: slide right shot downstream first.
                    if right_shot is not None:
                        self.sequencer.slide_shot(
                            right_shot.shot_id,
                            right_shot.start + delta,
                            direction="downstream",
                            _enforce=False,
                        )
                    # Inner: scale active shot's end (no ripple — the
                    # outer slide already repositioned the adjacent shot).
                    old_s, old_e = left_shot.start, left_shot.end
                    new_e = old_e + delta
                    for obj in left_shot.objects:
                        self.sequencer.scale_object_keys(
                            obj, old_s, old_e, old_s, new_e
                        )
                    left_shot.end = new_e

                elif right_is_active:
                    # Left gap of active shot.
                    # Outer: slide left shot upstream first.
                    if left_shot is not None:
                        self.sequencer.slide_shot(
                            left_shot.shot_id,
                            left_shot.start + delta,
                            direction="upstream",
                            _enforce=False,
                        )
                    # Inner: scale active shot's start (no ripple).
                    old_s, old_e = right_shot.start, right_shot.end
                    new_s = old_s + delta
                    for obj in right_shot.objects:
                        self.sequencer.scale_object_keys(
                            obj, old_s, old_e, new_s, old_e
                        )
                    right_shot.start = new_s
                else:
                    # Neither flanking shot is active — outer-only.
                    if right_shot is not None:
                        self.sequencer.slide_shot(
                            right_shot.shot_id,
                            right_shot.start + delta,
                            direction="downstream",
                            _enforce=False,
                        )
                    if left_shot is not None:
                        self.sequencer.slide_shot(
                            left_shot.shot_id,
                            left_shot.start + delta,
                            direction="upstream",
                            _enforce=False,
                        )

                # Single enforce pass for the whole compound operation.
                self.sequencer._enforce_gap_holds()
        finally:
            self._syncing = False
        self._gap_edit_epilogue()

    # ---- gap lock --------------------------------------------------------

    def on_gap_lock_changed(
        self, gap_start: float, gap_end: float, locked: bool
    ) -> None:
        """Handle a single gap's lock state being toggled via context menu."""
        if self.sequencer is None:
            return

        sorted_shots = self.sequencer.sorted_shots()
        left_shot = None
        right_shot = None
        for shot in sorted_shots:
            if abs(shot.end - gap_start) < TIME_SNAP_EPS:
                left_shot = shot
            if abs(shot.start - gap_end) < TIME_SNAP_EPS:
                right_shot = shot

        if left_shot is None or right_shot is None:
            return

        store = self.sequencer.store
        if locked:
            store.lock_gap(left_shot.shot_id, right_shot.shot_id)
        else:
            store.unlock_gap(left_shot.shot_id, right_shot.shot_id)

    def on_gap_lock_all(self) -> None:
        """Lock all gaps so they are preserved during respace."""
        if self.sequencer is None:
            return
        self.sequencer.store.lock_all_gaps()
        widget = self._get_sequencer_widget()
        if widget is not None:
            widget.set_all_gap_overlays_locked(True)

    def on_gap_unlock_all(self) -> None:
        """Unlock all gaps so they follow the global gap value."""
        if self.sequencer is None:
            return
        self.sequencer.store.unlock_all_gaps()
        widget = self._get_sequencer_widget()
        if widget is not None:
            widget.set_all_gap_overlays_locked(False)
