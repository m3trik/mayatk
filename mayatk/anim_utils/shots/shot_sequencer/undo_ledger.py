# !/usr/bin/python
# coding=utf-8
"""The shot sequencer's undo ledger.

Provides :class:`UndoLedgerMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. Every structural
edit records the shot boundaries before it; the widget's undo/redo requests
restore / re-apply those snapshots alongside Maya's own undo queue.
"""

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None


class UndoLedgerMixin:
    """Shot-boundary restore points, and how one undo/redo splits between them and the DCC."""

    # Boundary snapshots delegate to the STORE's ledger (pythontk
    # ShotStore.push/restore/redo_boundary_snapshot): scene keyframes ride
    # Maya's undo queue but shot bounds live in the store, outside it, and
    # more than one panel mutates them — per-controller stacks desynced
    # from each other and from the queue.  One ledger per store = per
    # scene, so a scene swap isolates it for free.

    def _save_shot_state(self) -> None:
        """Record the current shot boundaries as an undo restore point.

        No production path here calls this any more: every mayatk shot edit
        brackets through :meth:`ShotStore.scene_edit`, which pushes the
        restore point BEFORE the mutation and tags it with whether the edit
        reached Maya's undo queue.  This stays as the untagged primitive (an
        untagged point deliberately keeps the pre-pairing "restore and undo"
        behaviour); blendertk's edits bracket through its own
        ``scene_edit`` too, pairing by a scene serial.  Prefer ``scene_edit``.
        """
        if self.sequencer is not None:
            self.sequencer.store.push_boundary_snapshot()

    def _discard_shot_state(self) -> None:
        """Drop the most recent restore point (the edit was a no-op)."""
        if self.sequencer is not None:
            self.sequencer.store.discard_boundary_snapshot()

    def _restore_shot_state(self) -> None:
        """Apply the most recent restore point (the undo direction).

        Membership is restored symmetrically: a shot ABSENT from the
        snapshot is removed (undoing an insert leaves no phantom), and a
        shot the snapshot names but the store lost is re-created from its
        record (undoing a delete — the keys were never deleted with it).
        """
        if self.sequencer is not None:
            self.sequencer.store.restore_boundary_snapshot()

    def _redo_shot_state(self) -> None:
        """Re-apply the state undo stepped back from (the redo direction).

        Without this, a redo re-applies the scene keys while the bounds
        stay restored — resurrecting the keys-outside-their-shot state.
        """
        if self.sequencer is not None:
            self.sequencer.store.redo_boundary_snapshot()

    def _native_event_is_ours(self, redo: bool = False) -> bool:
        """True when the undo/redo Maya JUST performed is our newest one.

        Maya fires its Undo/Redo events for every undo in the session, so the
        event alone says nothing about whose edit it was.  The entry Maya just
        moved is named by the OPPOSITE queue (verified: after ``cmds.undo()``,
        ``undoInfo -q -redoName`` returns the chunk that was undone), and our
        restore point records the marker its edit landed under — so the two
        match only when this event is that edit.  Without the test, undoing an
        unrelated scene edit consumed a restore point and reverted shot bounds
        whose keys Maya had left exactly where they were.

        An UNPAIRED restore point can never match: its edit put nothing on the
        queue, so whatever Maya just undid was somebody else's.
        """
        store = self.sequencer.store if self.sequencer is not None else None
        if store is None or not store.has_boundary_snapshot(redo=redo):
            return False
        tag = store.peek_boundary_tag(redo=redo)
        if not isinstance(tag, tuple):
            return True  # untagged (legacy push) — pre-pairing behaviour
        paired, marker = tag
        return bool(paired) and marker == store.undo_queue_top(redo=not redo)

    #: Undo entries that are BOOKKEEPING, not scene edits.
    #: Maya defers its selection-mask reset to idle, so the selection a panel
    #: refresh makes (``_select_and_show``, undo-disabled) can still put one of
    #: these on the queue moments LATER -- above whatever the user did next.
    #: Measured in a live GUI on a production scene (2026-09-10): switch shot,
    #: drag a shot bound, and two ``selectionMaskResetAll`` entries sat between
    #: the user and their drag, so reversing it took three Ctrl+Z.
    #: Stepping past these is safe because undoing one changes no scene data;
    #: anything NOT named here stops the walk, so a real edit the user made
    #: after ours is never swallowed.
    _QUEUE_NOISE = frozenset(
        {
            "selectionMaskResetAll",
            "changeSelectMode",
            "selectMode",
            "selectType",
            "selectPref",
            "hilite",
        }
    )

    def _step_past_queue_noise(self, redo: bool = False, limit: int = 8) -> bool:
        """Walk Maya's queue back to OUR chunk, over bookkeeping entries only.

        Returns True when the walk moved at all.  Every step is checked by
        NAME before it is taken (:attr:`_QUEUE_NOISE`), and the walk stops the
        moment the top is our own marker or anything unrecognised -- so the
        worst case is that nothing moves and the caller behaves exactly as it
        did before.  If the marker is never reached, everything stepped over
        is put back: those entries were not ours to consume.
        """
        store = self.sequencer.store if self.sequencer is not None else None
        if store is None or not store.has_boundary_snapshot(redo=redo):
            return False
        tag = store.peek_boundary_tag(redo=redo)
        if not isinstance(tag, tuple):
            return False
        paired, marker = tag
        if not paired or not marker:
            return False
        step = cmds.redo if redo else cmds.undo
        back = cmds.undo if redo else cmds.redo
        taken = 0
        was_syncing = self._syncing
        self._syncing = True  # our own walk must not re-enter _on_maya_undo
        try:
            while taken < limit:
                top = store.undo_queue_top(redo=redo)
                if top == marker or top not in self._QUEUE_NOISE:
                    break
                step()
                taken += 1
            if taken and store.undo_queue_top(redo=redo) != marker:
                for _ in range(taken):
                    back()
                taken = 0
        except RuntimeError:
            pass
        finally:
            self._syncing = was_syncing
        if taken:
            self.logger.debug(
                "stepped past %s bookkeeping undo entr%s to reach %s",
                taken,
                "y" if taken == 1 else "ies",
                marker,
            )
        return bool(taken)

    def _undo_plan(self, redo: bool = False) -> tuple:
        """Decide how one undo/redo splits between the ledger and Maya.

        Returns ``(apply_ledger, call_maya)``.  The two stacks are not in
        lockstep — see :meth:`ShotStore.scene_edit`:

        * no restore point of ours → pass straight through to Maya;
        * our newest restore point is no longer the newest thing on the
          queue → an unrelated edit followed it, so Maya's undo belongs to
          THAT and our restore point stays put;
        * our restore point is on top → apply it, and touch Maya's queue
          only if the edit actually recorded a step there (a bounds-only
          edit records nothing, and an unconditional ``cmds.undo()`` would
          pop the user's previous, unrelated operation).

        Which queue still has to be showing ``marker`` depends on BOTH the
        direction and the pairing.  A paired edit's chunk rides Maya's
        queues, so after its undo the marker names the top of the *redo*
        queue.  An unpaired edit never put anything on either queue — its
        marker names whatever was on the *undo* queue when it was recorded,
        and that stays true in both directions.  Comparing an unpaired entry
        against the redo queue always mismatches, which silently dropped the
        bounds restore AND redid an unrelated operation.
        """
        store = self.sequencer.store if self.sequencer is not None else None
        if store is None or not store.has_boundary_snapshot(redo=redo):
            return False, True
        tag = store.peek_boundary_tag(redo=redo)
        if not isinstance(tag, tuple):
            return True, True  # untagged (legacy push) — pre-pairing behaviour
        paired, marker = tag
        if marker != store.undo_queue_top(redo=redo and bool(paired)):
            return False, True
        return True, bool(paired)

    def on_undo(self) -> None:
        """Handle undo_requested from the widget — delegate to Maya undo."""
        if cmds is None:
            return
        self._step_past_queue_noise()
        apply_ledger, call_maya = self._undo_plan()
        self._syncing = True
        try:
            try:
                if apply_ledger:
                    self._restore_shot_state()
            except Exception:
                self.logger.debug("on_undo: _restore_shot_state failed", exc_info=True)
            if call_maya:
                cmds.undo()
        except RuntimeError:
            pass
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def on_redo(self) -> None:
        """Handle redo_requested from the widget — delegate to Maya redo."""
        if cmds is None:
            return
        self._step_past_queue_noise(redo=True)
        apply_ledger, call_maya = self._undo_plan(redo=True)
        self._syncing = True
        try:
            try:
                if apply_ledger:
                    self._redo_shot_state()
            except Exception:
                self.logger.debug("on_redo: _redo_shot_state failed", exc_info=True)
            if call_maya:
                cmds.redo()
        except RuntimeError:
            pass
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
