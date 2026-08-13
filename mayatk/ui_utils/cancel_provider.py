# !/usr/bin/python
# coding=utf-8
"""Maya's answers to uitk's cancellation contract.

Maya is the host where the generic approach fails: while a ``cmds`` call runs,
the main thread holds the GIL and pumps no events, so neither a Qt shortcut nor
a background key poller can act. Maya's own answer is ``MComputation`` — the
*query* ``isInterruptRequested()`` peeks the OS input queue for Esc from the
computing thread itself, needing no event loop and no second thread. Polling it
at the operation's own checkpoints is therefore the only Esc that works during
a long synchronous stretch, and it is what Maya users already expect.

Three host capabilities, one provider:

* **source** — ``MComputation.isInterruptRequested()`` (plus uitk's
  pump-independent key-hold fallback from the base class).
* **feedback** — ``MComputation``'s progress bar, which lives in Maya's UI
  rather than the tool window. A marking-menu slot often outlives the panel
  that launched it, so the footer bar may be off screen before work starts.
* **transaction** — an undo chunk, so a cancelled run can be rolled back
  instead of leaving the scene half-mutated.

API note: ``MComputation`` exists **only in API 1.0** (``maya.OpenMaya``) — it
was never ported to API 2.0, verified against Maya 2025. This is the documented
exception to mayatk's API-2.0-first rule; everything else here uses ``cmds``.
"""
from __future__ import annotations

from typing import Any, Optional

import maya.cmds as cmds
import maya.api.OpenMaya as om

from uitk.managers.cancel_manager import CancelProvider


class _MayaBracket:
    """State for one in-flight operation (see :class:`MayaCancelProvider`)."""

    __slots__ = ("label", "computation", "chunk_name", "chunk_open", "total")

    def __init__(self, label: str, chunk_name: Optional[str] = None):
        self.label = label
        self.computation = None
        self.chunk_name = chunk_name
        self.chunk_open = False
        self.total = 0


class MayaCancelProvider(CancelProvider):
    """Maya host strategy for :class:`uitk.CancelManager`.

    Install once per session::

        mtk.MayaCancelProvider.install()
    """

    name = "maya"

    #: Queued input must not dispatch while a slot holds a half-mutated scene:
    #: a nested slot on partial state is a corruption bug. Safe to exclude here
    #: precisely because Esc no longer depends on the event loop.
    exclude_user_input = True

    #: Undo chunks make a cancelled run exactly reversible, and the guard in
    #: :meth:`end` makes the reversal safe. Verified against Maya 2025.
    supports_rollback = True

    _chunk_counter = 0

    # ------------------------------------------------------------------
    # Reporting — route through Maya's own message stream, not Python logging,
    # so these land in the Script Editor the way every other mayatk message does
    # (``install`` and the bracket bookkeeping come from the base class).
    # ------------------------------------------------------------------
    @classmethod
    def report_warning(cls, message: str) -> None:
        om.MGlobal.displayWarning(message)

    @classmethod
    def report_info(cls, message: str) -> None:
        om.MGlobal.displayInfo(message)

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------
    def create_sources(self, scope, label: str = ""):
        """Maya's Esc peek first, then uitk's key-hold fallback.

        The peek reads whichever bracket is active when it is *called*, not
        when it is built — sources are wired at scope creation, before
        :meth:`begin` opens anything.
        """
        sources = [self._interrupt_requested]
        sources.extend(super().create_sources(scope, label) or ())
        return sources

    def _interrupt_requested(self) -> bool:
        """True when Maya reports an interrupt (Esc) for the active bracket."""
        bracket = self.current_bracket
        if bracket is None or bracket.computation is None:
            return False
        try:
            return bool(bracket.computation.isInterruptRequested())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Transaction + host bracket
    # ------------------------------------------------------------------
    def begin(self, scope, label: str = "", rollback: bool = False) -> Any:
        """Open the undo chunk (only when rollback was requested) and computation.

        The chunk is opt-in because opening one coalesces a slot's edits into a
        single undo entry — a behaviour change for every existing slot if it
        were unconditional. Requesting rollback is the caller declaring that
        one-entry granularity is what they want.
        """
        bracket = self.open_bracket(_MayaBracket(label))

        if rollback:
            try:
                if cmds.undoInfo(query=True, state=True):
                    MayaCancelProvider._chunk_counter += 1
                    bracket.chunk_name = (
                        f"uitkCancelable{MayaCancelProvider._chunk_counter}"
                    )
                    cmds.undoInfo(openChunk=True, chunkName=bracket.chunk_name)
                    bracket.chunk_open = True
            except Exception as e:
                self.report_warning(f"Could not open undo chunk: {e}")

        try:
            import maya.OpenMaya as om1  # API 1.0 — MComputation lives only here

            bracket.computation = om1.MComputation()
            # (showProgressBar, isInterruptable, useWaitCursor); uitk already
            # owns the wait cursor for the slot's duration.
            bracket.computation.beginComputation(True, True, False)
        except Exception as e:
            bracket.computation = None
            self.report_warning(f"Could not begin interruptible computation: {e}")

        return bracket

    def tick(
        self,
        value: Optional[int] = None,
        total: Optional[int] = None,
        text: Optional[str] = None,
    ) -> None:
        """Mirror progress into Maya's own progress bar."""
        bracket = self.current_bracket
        if bracket is None or bracket.computation is None:
            return
        try:
            if total and total > 0 and total != bracket.total:
                bracket.total = total
                bracket.computation.setProgressRange(0, int(total))
            if text:
                # ASCII only: this string reaches Maya's status line and the
                # mayapy console, which is not reliably UTF-8 on Windows.
                bracket.computation.setProgressStatus(f"{text} - Esc to cancel")
            if value is not None:
                bracket.computation.setProgress(int(value))
        except Exception:
            # Feedback is never worth failing an operation over.
            pass

    def end(self, token: Any, cancelled: bool = False, rollback: bool = False) -> None:
        """Close the computation and chunk, rolling back a cancelled run.

        Rollback is guarded, not blind. ``cmds.undo()`` pops whatever is on top
        of the undo queue — if the cancelled slot recorded nothing, that is the
        user's *previous* action, so a naive undo silently destroys unrelated
        work. Maya leaves an empty chunk off the queue entirely (verified on
        2025), so comparing the top entry against this bracket's unique chunk
        name distinguishes "our partial work" from "someone else's".
        """
        # ``close_bracket`` returns None for a junk token *and* for a double
        # close, which is the guard that keeps teardown exactly-once — an
        # unbalanced endComputation would leave Maya computing for the session.
        bracket = self.close_bracket(token)
        if bracket is None:
            return

        if bracket.computation is not None:
            try:
                bracket.computation.endComputation()
            except Exception as e:
                self.report_warning(f"Could not end computation: {e}")
            bracket.computation = None

        if bracket.chunk_open:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception as e:
                self.report_warning(f"Could not close undo chunk: {e}")
            bracket.chunk_open = False

        if not (cancelled and rollback):
            return

        if not bracket.chunk_name:
            # Undo was off when the operation started (batch/standalone Maya
            # starts that way), so nothing was recorded to roll back to. Say
            # so — the slot asked for rollback and the partial work is still
            # in the scene.
            self.report_warning(
                f"'{bracket.label}' cancelled - undo was disabled, so partial "
                "changes remain in the scene."
            )
            return

        try:
            top = cmds.undoInfo(query=True, undoName=True)
        except Exception:
            top = None

        if top == bracket.chunk_name:
            try:
                cmds.undo()
                self.report_info(f"'{bracket.label}' cancelled - changes undone.")
            except Exception as e:
                self.report_warning(f"Could not undo cancelled operation: {e}")
        else:
            # Nothing of ours on top: either the slot recorded nothing, or it
            # already cleaned up after itself. Undoing here would eat the
            # user's previous action.
            self.report_info(f"'{bracket.label}' cancelled - nothing to undo.")
