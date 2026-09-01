# !/usr/bin/python
# coding=utf-8
"""Gap and range-highlight handlers for the shot sequencer controller.

Provides :class:`GapManagerMixin` — mixed into
:class:`~.shot_sequencer_slots.ShotSequencerController` to handle gap
resize, move, lock, and range-highlight interactions.
"""

from __future__ import annotations

# Threshold for detecting meaningful time deltas (frame-level tolerance).
TIME_SNAP_EPS = 1e-3

__all__ = ["GapManagerMixin"]


class GapManagerMixin:
    """Mixin supplying gap-overlay and range-highlight handlers.

    Expects the host class to provide:

    * ``sequencer`` — :class:`ShotSequencer` instance
    * ``active_shot_id`` — property returning the current shot id
    * ``_sync_to_widget()`` / ``_sync_combobox()`` (edits bracket through
      ``sequencer.store.scene_edit()``, which records the restore point)
    * ``_get_sequencer_widget()``
    * ``_syncing`` — bool flag
    * ``_segment_cache`` / ``_sub_row_cache`` — dicts flushed by
      ``_gap_edit_epilogue`` after boundary edits
    * ``logger``
    """

    # ---- range highlight -------------------------------------------------

    def on_range_highlight_changed(self, start: float, end: float) -> None:
        """Update the active shot boundaries when the range highlight is dragged.

        If both edges shifted by the same delta it's a *move* — all keys
        in the shot are shifted and downstream shots are rippled.

        Otherwise it's an edge resize:

        * **Plain drag** — the boundary moves, keyframes stay put.  The
          shot simply covers a different span.
        * **Shift+drag** — the shot's keyframes are *scaled* into the new
          range (a retime).

        Both ripple neighbours by the edge deltas so spacing survives.
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

        # Both edges moved by the same amount → translate entire shot.
        # NOTE: body-drag requires Shift (to avoid rubber-band selection
        # conflict), so shift_held is always True here.  We therefore
        # ignore it and always call move_shot() which moves keys + ripples.
        if abs(ds - de) < TIME_SNAP_EPS and abs(ds) > TIME_SNAP_EPS:
            self._syncing = True
            try:
                with self.sequencer.store.scene_edit("shotmove"):
                    self.sequencer.move_shot(self.active_shot_id, start)
            finally:
                self._syncing = False
            self._gap_edit_epilogue()
            return

        # Edge resize — plain moves the boundary, Shift retimes the content.
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("shotresize"):
                if shift_held:
                    self.sequencer.resize_shot(self.active_shot_id, start, end)
                else:
                    self.sequencer.resize_shot_bounds(self.active_shot_id, start, end)
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
        # Boundaries just moved — Maya's playback range was set from the OLD
        # ones and is now stale (transport buttons land on the wrong frames).
        self._apply_view_playback_range()

    def _set_shot_edge(
        self, shot, new_start=None, new_end=None, scale: bool = False
    ) -> bool:
        """Move one of *shot*'s edges while the other stays fixed.

        The single chokepoint for the four inner gap edits: the raw drag
        value is snapped through the store and clamped against the
        opposite edge — an unclamped gap edge dragged past the shot's far
        edge would store inverted bounds and hand ``scale_object_keys``
        an inverted target range.

        ``scale=False`` (the plain drag) moves the boundary only, leaving
        keyframes untouched; ``scale=True`` (Shift) retimes the shot's
        keys into the new range.  Neither ripples — the shot grows or
        shrinks into the adjacent gap, so no neighbour has to move, and the edge is
        clamped at the neighbour so a drag past a zero-width gap cannot
        overlap it.

        Returns True when the shot actually changed.
        """
        store = self.sequencer.store
        old_s, old_e = shot.start, shot.end
        ns = old_s if new_start is None else store.snap(new_start)
        ne = old_e if new_end is None else store.snap(new_end)
        # Clamp against the fixed edge — zero duration is the floor.
        if new_start is not None:
            ns = min(ns, old_e)
        if new_end is not None:
            ne = max(ne, old_s)
        # Clamp against the NEIGHBOURS too.  This edit deliberately does not
        # ripple - the shot only grows or shrinks into the adjacent GAP - so
        # at zero gap there is nothing to grow into and the edge must hold.
        # Without this an inner edge drag stores overlapping shots, and two
        # shots claiming one span makes key ownership (and every envelope
        # derived from it) ambiguous.
        sorted_s = self.sequencer.sorted_shots()
        idx = next(
            (i for i, s in enumerate(sorted_s) if s.shot_id == shot.shot_id), None
        )
        if idx is not None:
            if new_start is not None and idx > 0:
                ns = max(ns, sorted_s[idx - 1].end)
            if new_end is not None and idx + 1 < len(sorted_s):
                ne = min(ne, sorted_s[idx + 1].start)
        if abs(ns - old_s) < TIME_SNAP_EPS and abs(ne - old_e) < TIME_SNAP_EPS:
            return False
        if scale:
            for obj in shot.objects:
                self.sequencer.scale_object_keys(obj, old_s, old_e, ns, ne)
        shot.start = ns
        shot.end = ne
        return True

    # ---- gap resize / move -----------------------------------------------

    def on_gap_resized(self, original_next_start: float, new_next_start: float) -> None:
        """Handle right-edge gap drag.

        The right edge of a gap is a shot's ``.start``.

        * **Inner** (the touched shot is the active shot) — the active
          shot's start moves while its end stays fixed; its keyframes are
          left in place.
        * **Outer** (the touched shot is *not* the active shot) — the
          adjacent shot is *slid* intact in the downstream direction
          and all further downstream shots follow.
        * **Shift+drag** — the touched shot's keys are *scaled* into the
          new range instead (a retime), inner or outer alike.
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

        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("gapresize"):
                is_inner = (
                    self.active_shot_id is not None
                    and target.shot_id == self.active_shot_id
                )
                if shift_held or is_inner:
                    # Inner (or any Shift+drag): the touched shot's start
                    # moves, its end stays fixed.  Shift additionally
                    # retimes its keys into the new range.  No ripple —
                    # the shot grows/shrinks into the gap.
                    if self._set_shot_edge(
                        target, new_start=new_next_start, scale=shift_held
                    ):
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

        The left edge of a gap is a shot's ``.end``.  The overlay placed
        after the LAST shot is a ``tail`` handle whose left edge is that
        shot's end, so the final shot resizes exactly like every other.

        * **Inner** (the touched shot is the active shot) — the active
          shot's end moves while its start stays fixed; its keyframes are
          left in place.
        * **Outer** (the touched shot is *not* the active shot) — the
          adjacent shot is *slid* intact in the upstream direction
          and all further upstream shots follow.
        * **Shift+drag** — the touched shot's keys are *scaled* into the
          new range instead (a retime), inner or outer alike.
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

        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("gapresize"):
                sorted_shots = self.sequencer.sorted_shots()
                is_timeline_last = bool(
                    sorted_shots and target.shot_id == sorted_shots[-1].shot_id
                )
                is_inner = (
                    self.active_shot_id is not None
                    and target.shot_id == self.active_shot_id
                )
                if shift_held or is_inner or is_timeline_last:
                    # Inner (or any Shift+drag, or the TIMELINE-LAST shot —
                    # its tail handle promises "resize the last shot", and
                    # the outer branch would slide the whole timeline
                    # upstream instead; this also covers no-active-shot):
                    # the touched shot's end moves, its start stays fixed.
                    # Shift additionally retimes its keys into the new
                    # range.  No ripple — the shot grows/shrinks into the
                    # gap.
                    if self._set_shot_edge(
                        target, new_end=new_prev_end, scale=shift_held
                    ):
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
          the active shot's end follows the gap (inner edge, keys left in
          place — Shift retimes them) and the right shot *slides*
          downstream intact (outer edge).
        * **Left gap of active shot** (right_shot == active):
          the active shot's start follows the gap (inner edge, same rule)
          and the left shot *slides* upstream intact (outer edge).
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

        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "shift_held_at_press", False)
        active_id = self.active_shot_id

        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("gapmove"):
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
                    # Inner: the active shot's end follows the gap (no
                    # ripple — the outer slide already repositioned the
                    # adjacent shot).  Shift retimes its keys with it.
                    self._set_shot_edge(
                        left_shot, new_end=left_shot.end + delta, scale=shift_held
                    )

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
                    # Inner: the active shot's start follows the gap (no
                    # ripple).  Shift retimes its keys with it.
                    self._set_shot_edge(
                        right_shot,
                        new_start=right_shot.start + delta,
                        scale=shift_held,
                    )
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
