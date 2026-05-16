# !/usr/bin/python
# coding=utf-8
"""Real-time pivot-change notifier built on :class:`ScriptJobManager`.

Tools whose output depends on the viewport pivot (Cut On Axis, Mirror, …)
read it via ``XformUtils.get_operation_axis_pos`` at operation time. When
the user moves the pivot, that read goes stale and the previewed output
no longer matches a fresh run.

There are two distinct pivot mechanisms in Maya, both of which this
watcher must handle:

1. ``cmds.manipPivot`` — a transient gizmo-position **override**, only
   active when the user enters Custom Pivot mode (right-click on the
   pivot handle → Edit Pivot, or the pin/bake gizmo controls). Returns
   ``(0, 0, 0)`` when inactive.
2. The transform's ``rotatePivot`` / ``scalePivot`` attributes — the
   actual **baked** pivot. Default Insert-mode (D-key) editing modifies
   these on the node; ``cmds.manipPivot`` is *not* involved.

The state signature therefore reads both: the manip override AND each
selected transform's world-space rotatePivot. A ``DragRelease`` whose
composite pivot signature moved while the *selection* stayed the same
is treated as a deliberate pivot edit and dispatches the callback.
A selection change is ignored regardless (Preview locks its operated
objects at ``enable()``, so refreshing on a new selection would hijack
the user's choice).

Usage::

    from mayatk.xform_utils.pivot_watcher import PivotWatcher

    self._pivot_watcher = PivotWatcher(
        self.preview.refresh,
        gate=lambda: self.preview.is_enabled,
        owner=self,
    )
    self._pivot_watcher.start()
    self._pivot_watcher.attach_widget(self.ui)
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, List, Optional, Tuple

import maya.cmds as cmds

from mayatk.core_utils.script_job_manager import ScriptJobManager


logger = logging.getLogger(__name__)


def _read_selection() -> Tuple[str, ...]:
    try:
        return tuple(cmds.ls(selection=True, long=True) or ())
    except Exception:
        return ()


def _read_manip_override() -> Tuple[float, ...]:
    """World-space ``cmds.manipPivot`` override, or ``()`` if unavailable.

    Only carries a value while the user is in Custom Pivot mode. Returns
    ``(0, 0, 0)`` in baked-mode editing.

    ``cmds.manipPivot`` may return either ``[(x, y, z)]`` or ``[x, y, z]``
    depending on context; both shapes are normalized to a flat tuple.
    """
    try:
        raw = cmds.manipPivot(q=True, p=True) or ()
    except Exception:
        return ()
    if raw and isinstance(raw[0], (list, tuple)):
        return tuple(raw[0])
    return tuple(raw)


def _read_baked_pivots() -> Tuple[Tuple[float, ...], ...]:
    """World-space ``rotatePivot`` for each selected transform.

    Captures the baked pivot that Insert-mode editing writes to. Returned
    as a tuple-of-tuples so equality comparison works for the dedup rule.
    """
    try:
        nodes = cmds.ls(selection=True, type="transform", long=True) or []
    except Exception:
        return ()
    out = []
    for node in nodes:
        try:
            rp = cmds.xform(node, q=True, ws=True, rp=True)
        except Exception:
            continue
        if rp:
            out.append(tuple(rp))
    return tuple(out)


class PivotWatcher:
    """Fire *callback* on intentional manipulator-pivot drags.

    A ``DragRelease`` whose manip position moved while the selection was
    unchanged is treated as a deliberate pivot drag (Insert mode). A
    ``DragRelease`` accompanied by a selection change is treated as a
    selection click and ignored — refreshing on those would hijack the
    user's selection (Preview locks its operated objects at ``enable()``,
    so re-running against the captured set after a click would deselect
    whatever the user just chose).

    Subscriptions are registered as ``ephemeral=True`` so they self-prune
    on scene change. Pair with :meth:`attach_widget` to also self-prune
    on UI destruction.

    Parameters
    ----------
    callback : callable
        Invoked with no arguments when an intentional pivot drag is
        detected. Caller re-queries the pivot inside the callback.
    gate : callable, optional
        Predicate (no args -> bool). If supplied and returns ``False``,
        the dispatch is skipped before any state work. Cheap way to say
        "only refresh while previewing."
    events : iterable of str, optional
        Override the default event set (``("DragRelease",)``).
    owner : object, optional
        Grouping key for SJM cleanup. Defaults to the watcher instance.
    """

    DEFAULT_EVENTS = ("DragRelease",)

    def __init__(
        self,
        callback: Callable[[], None],
        *,
        gate: Optional[Callable[[], bool]] = None,
        events: Optional[Iterable[str]] = None,
        owner: Any = None,
    ):
        self._callback = callback
        self._gate = gate
        self._events = tuple(events) if events else self.DEFAULT_EVENTS
        self._owner = owner if owner is not None else self
        self._tokens: List[int] = []
        self._started = False
        self._last_state: Optional[Tuple[Any, ...]] = None

    @property
    def owner(self) -> Any:
        return self._owner

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        """Subscribe to the watched events (idempotent)."""
        if self._started:
            return
        mgr = ScriptJobManager.instance()
        for event in self._events:
            self._tokens.append(
                mgr.subscribe(
                    event, self._dispatch, owner=self._owner, ephemeral=True
                )
            )
        self._started = True
        self._last_state = self._read_state()

    def stop(self) -> None:
        """Unsubscribe from all watched events (idempotent)."""
        if not self._started:
            return
        mgr = ScriptJobManager.instance()
        for token in self._tokens:
            mgr.unsubscribe(token)
        self._tokens.clear()
        self._started = False
        self._last_state = None

    def attach_widget(self, widget) -> None:
        """Auto-:meth:`stop` when *widget* emits ``destroyed``."""
        sjm = ScriptJobManager.instance()
        sjm.connect_cleanup(widget, owner=self._owner)
        try:
            widget.destroyed.connect(self._on_widget_destroyed)
        except Exception:
            logger.debug("PivotWatcher: failed to chain widget destroy", exc_info=True)

    def __enter__(self) -> "PivotWatcher":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        return False

    def _dispatch(self) -> None:
        if self._gate is not None:
            try:
                if not self._gate():
                    logger.debug("PivotWatcher: gate=False, skipping")
                    return
            except Exception:
                logger.debug("PivotWatcher: gate raised", exc_info=True)
                return

        curr = self._read_state()
        prev = self._last_state

        # First observation since start(): just snapshot.
        if prev is None:
            self._last_state = curr
            logger.debug("PivotWatcher: first event, snapshot=%r", curr)
            return

        sel_prev, override_prev, baked_prev = prev
        sel_curr, override_curr, baked_curr = curr

        pivot_changed = (
            override_curr != override_prev or baked_curr != baked_prev
        )
        selection_unchanged = sel_curr == sel_prev
        intentional_pivot_drag = pivot_changed and selection_unchanged

        logger.debug(
            "PivotWatcher: prev=%r curr=%r pivot_changed=%s sel_unchanged=%s -> fire=%s",
            prev, curr, pivot_changed, selection_unchanged, intentional_pivot_drag,
        )

        if intentional_pivot_drag:
            try:
                self._callback()
            except Exception:
                logger.debug("PivotWatcher: callback raised", exc_info=True)

        # Re-read after callback so post-callback selection/pivot becomes
        # the baseline for the next event — important because the callback
        # itself may re-select (e.g. EditUtils.mirror selects its output).
        self._last_state = self._read_state()

    @staticmethod
    def _read_state() -> Tuple[Tuple[str, ...], Tuple[float, ...], Tuple[Tuple[float, ...], ...]]:
        return (_read_selection(), _read_manip_override(), _read_baked_pivots())

    def _on_widget_destroyed(self, *_args) -> None:
        self._tokens.clear()
        self._started = False
        self._last_state = None
