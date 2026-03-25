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
        shift_held = getattr(widget, "_shift_at_press", False)

        ds = start - shot.start
        de = end - shot.end

        self._save_shot_state()

        # Both edges moved by the same amount → translate entire shot
        if abs(ds - de) < TIME_SNAP_EPS and abs(ds) > TIME_SNAP_EPS:
            self._syncing = True
            try:
                with pm.UndoChunk():
                    if shift_held:
                        duration = shot.end - shot.start
                        self.sequencer.store.update_shot(
                            self.active_shot_id, start=start, end=start + duration
                        )
                    else:
                        self.sequencer.move_shot(self.active_shot_id, start)
            finally:
                self._syncing = False
            self._sync_to_widget()
            self._sync_combobox()
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
        self._sync_to_widget()
        self._sync_combobox()

    # ---- gap resize / move -----------------------------------------------

    def on_gap_resized(self, original_next_start: float, new_next_start: float) -> None:
        """Handle a gap overlay being dragged to resize.

        Shifts the shot starting at *original_next_start* and all
        downstream shots by the same delta so that every shot keeps
        its original duration.  Only the gap size changes.
        """
        if self.sequencer is None:
            return

        delta = new_next_start - original_next_start
        if abs(delta) < TIME_SNAP_EPS:
            return

        sorted_shots = self.sequencer.sorted_shots()

        target_idx = None
        for i, shot in enumerate(sorted_shots):
            if abs(shot.start - original_next_start) < 1.0:
                target_idx = i
                break

        if target_idx is None:
            return

        self._save_shot_state()

        self._syncing = True
        try:
            with pm.UndoChunk():
                for shot in sorted_shots[target_idx:]:
                    duration = shot.end - shot.start
                    self.sequencer.store.update_shot(
                        shot.shot_id,
                        start=shot.start + delta,
                        end=shot.start + delta + duration,
                    )
        finally:
            self._syncing = False
        self._sync_to_widget()
        self._sync_combobox()

    def on_gap_left_resized(
        self, original_prev_end: float, new_prev_end: float
    ) -> None:
        """Handle left-edge gap drag — resize the preceding shot's end.

        The gap's left edge is the previous shot's end frame.
        Dragging it adjusts only that shot's duration (ripple-free).
        """
        if self.sequencer is None:
            return

        delta = new_prev_end - original_prev_end
        if abs(delta) < TIME_SNAP_EPS:
            return

        sorted_shots = self.sequencer.sorted_shots()

        target = None
        for shot in sorted_shots:
            if abs(shot.end - original_prev_end) < 1.0:
                target = shot
                break

        if target is None:
            return

        self._save_shot_state()
        self._syncing = True
        try:
            with pm.UndoChunk():
                self.sequencer.store.update_shot(target.shot_id, end=new_prev_end)
        finally:
            self._syncing = False
        self._sync_to_widget()
        self._sync_combobox()

    def on_gap_moved(
        self,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Handle body gap drag — slide the cut-point between two shots.

        The gap keeps its width; the left shot's end and the right
        shot's start move by the same delta.
        """
        if self.sequencer is None:
            return

        delta = new_start - old_start
        if abs(delta) < TIME_SNAP_EPS:
            return

        sorted_shots = self.sequencer.sorted_shots()

        left_shot = None
        for shot in sorted_shots:
            if abs(shot.end - old_start) < 1.0:
                left_shot = shot
                break

        right_shot = None
        for shot in sorted_shots:
            if abs(shot.start - old_end) < 1.0:
                right_shot = shot
                break

        if left_shot is None and right_shot is None:
            return

        self._save_shot_state()
        self._syncing = True
        try:
            with pm.UndoChunk():
                if left_shot is not None:
                    self.sequencer.store.update_shot(
                        left_shot.shot_id, end=left_shot.end + delta
                    )
                if right_shot is not None:
                    self.sequencer.store.update_shot(
                        right_shot.shot_id, start=right_shot.start + delta
                    )
        finally:
            self._syncing = False
        self._sync_to_widget()
        self._sync_combobox()

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
            if abs(shot.end - gap_start) < 1.0:
                left_shot = shot
            if abs(shot.start - gap_end) < 1.0:
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
