# !/usr/bin/python
# coding=utf-8
"""Centralized Maya event subscription manager.

Two kinds of Maya event sources are unified under one cleanup path:

1. ``cmds.scriptJob`` events (``SelectionChanged``, ``timeChanged``, …) —
   multiplexed so at most one ``cmds.scriptJob`` exists per event name.
2. ``maya.api.OpenMaya.MMessage`` callbacks (``addConnectionCallback``,
   ``addAttributeChangedCallback``, …) — registered through SJM so they
   share the same ``owner`` / ``unsubscribe`` / widget-destroy machinery.

Usage::

    from mayatk.core_utils.script_job_manager import ScriptJobManager
    import maya.api.OpenMaya as om2

    mgr = ScriptJobManager.instance()
    mgr.subscribe("SceneOpened", self._on_scene, owner=self)
    mgr.subscribe("SelectionChanged", self._on_sel, owner=self, ephemeral=True)
    mgr.add_om_callback(
        om2.MDGMessage.addConnectionCallback,
        self._on_connection_change,
        owner=self,
    )
    mgr.connect_cleanup(self.ui, owner=self)  # auto-unsubscribe on widget destroy

    with mgr.suppressed(token):  # silence listeners while mutating the scene
        ...
"""
from __future__ import annotations

import maya.cmds as cmds

import contextlib
import itertools
import logging
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple


logger = logging.getLogger(__name__)

_SCENE_CHANGE_EVENTS = frozenset({"SceneOpened", "NewSceneOpened"})


class _Subscription:
    """Internal subscription record for a ``cmds.scriptJob`` listener."""

    __slots__ = ("token", "event", "callback", "owner", "ephemeral")

    def __init__(self, token, event, callback, owner, ephemeral):
        self.token = token
        self.event = event
        self.callback = callback
        self.owner = owner
        self.ephemeral = ephemeral


class _OMSubscription:
    """Internal subscription record for an ``MMessage`` callback.

    The ``cb_id`` is the value returned by the OpenMaya registration
    function (e.g. ``MDGMessage.addConnectionCallback``).  Removal goes
    through ``MMessage.removeCallback``.
    """

    __slots__ = ("token", "cb_id", "owner")

    def __init__(self, token, cb_id, owner):
        self.token = token
        self.cb_id = cb_id
        self.owner = owner


class ScriptJobManager:
    """Centralized Maya scriptJob event dispatcher.

    Creates at most one ``cmds.scriptJob`` per Maya event name and
    multiplexes it to any number of subscriber callbacks.

    Ephemeral subscriptions (``ephemeral=True``) are automatically
    removed when a scene-change event (``SceneOpened`` or
    ``NewSceneOpened``) fires, mirroring Maya's ``killWithScene``
    behaviour without destroying the shared job.

    Parameters
    ----------
    None — obtain the singleton via :meth:`instance`.
    """

    _instance: Optional["ScriptJobManager"] = None

    @classmethod
    def instance(cls) -> "ScriptJobManager":
        """Return the module-wide singleton, creating it on first access."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Tear down the singleton and allow a fresh one to be created.

        Primarily useful for tests and module-reload scenarios.
        """
        if cls._instance is not None:
            cls._instance.teardown()
            cls._instance = None

    # ------------------------------------------------------------------ init
    def __init__(self):
        self._subs: Dict[int, _Subscription] = {}
        self._events: Dict[str, List[int]] = {}  # event -> [tokens]
        self._jobs: Dict[str, int] = {}  # event -> scriptJob id
        self._om_subs: Dict[int, _OMSubscription] = {}
        self._counter = itertools.count(1)
        # (id(widget), id(owner)) pairs — one cleanup connection per pair, so
        # several owners can share a widget without shadowing each other.
        self._cleanup_pairs: Set[Tuple[int, int]] = set()
        # token -> nested suppress count; counted so overlapping suppressed()
        # blocks (and their in-flight deferred resumes) compose correctly.
        self._suppressed: Dict[int, int] = {}

    # -------------------------------------------------------------- public API

    def subscribe(
        self,
        event: str,
        callback: Callable,
        *,
        owner: Any = None,
        ephemeral: bool = False,
    ) -> int:
        """Register *callback* for a Maya scriptJob *event*.

        Parameters
        ----------
        event : str
            Maya event name (``"SceneOpened"``, ``"SelectionChanged"``, …).
        callback : callable
            Invoked with no arguments each time the event fires.
        owner : object, optional
            Grouping key for :meth:`unsubscribe_all`.
        ephemeral : bool
            If ``True``, the subscription is automatically removed the next
            time a scene-change event fires (``SceneOpened`` or
            ``NewSceneOpened``).  Useful for ``SelectionChanged`` listeners
            whose context is invalidated by a scene switch.

        Returns
        -------
        int
            Opaque token passed to :meth:`unsubscribe`.
        """
        self._ensure_job(event)  # before bookkeeping: a failure leaves no orphan
        token = next(self._counter)
        self._subs[token] = _Subscription(token, event, callback, owner, ephemeral)
        self._events.setdefault(event, []).append(token)
        return token

    def add_om_callback(
        self,
        register_fn: Callable,
        *register_args: Any,
        owner: Any = None,
    ) -> Optional[int]:
        """Register an OpenMaya ``MMessage`` callback under SJM management.

        The callback is created by calling ``register_fn(*register_args)``.
        The returned callback id is later removed via
        ``maya.api.OpenMaya.MMessage.removeCallback`` when the
        subscription is torn down (via :meth:`unsubscribe`,
        :meth:`unsubscribe_all`, or widget destruction).

        Examples
        --------
        Register a global DG connection-changed callback::

            mgr.add_om_callback(
                om2.MDGMessage.addConnectionCallback,
                self._on_connection_change,
                owner=self,
            )

        Register a per-node attribute-changed callback::

            mgr.add_om_callback(
                om2.MNodeMessage.addAttributeChangedCallback,
                mobj,
                self._on_attr_changed,
                owner=self,
            )

        Parameters
        ----------
        register_fn : callable
            The OpenMaya registration function (e.g.
            ``om2.MDGMessage.addConnectionCallback``).
        *register_args
            Forwarded to *register_fn* in order.  The callback function is
            usually the last positional argument.
        owner : object, optional
            Grouping key for :meth:`unsubscribe_all`.

        Returns
        -------
        int or None
            Opaque token for :meth:`unsubscribe`, or ``None`` if the
            registration failed.
        """
        try:
            cb_id = register_fn(*register_args)
        except Exception:
            logger.warning(
                "ScriptJobManager.add_om_callback: %s failed",
                register_fn,
                exc_info=True,
            )
            return None
        token = next(self._counter)
        self._om_subs[token] = _OMSubscription(token, cb_id, owner)
        return token

    def unsubscribe(self, token: int) -> None:
        """Remove a single subscription by *token* (script job or OM)."""
        # OpenMaya callback subscription?
        om_sub = self._om_subs.pop(token, None)
        if om_sub is not None:
            self._remove_om_callback(om_sub.cb_id)
            return

        # ScriptJob subscription
        sub = self._subs.pop(token, None)
        self._suppressed.pop(token, None)
        if sub is None:
            return
        tokens = self._events.get(sub.event)
        if tokens:
            try:
                tokens.remove(token)
            except ValueError:
                pass
            if not tokens:
                self._kill_job(sub.event)

    def unsubscribe_all(self, owner: Any) -> None:
        """Remove every subscription registered under *owner* (both kinds)."""
        to_remove = [t for t, s in self._subs.items() if s.owner is owner]
        to_remove += [t for t, s in self._om_subs.items() if s.owner is owner]
        for token in to_remove:
            self.unsubscribe(token)

    def connect_cleanup(self, widget, owner: Any) -> None:
        """Connect *widget*.destroyed → :meth:`unsubscribe_all` for *owner*.

        Safe to call multiple times for the same *widget* / *owner* pair, and
        several owners may share one widget — each gets its own cleanup.
        """
        pair = (id(widget), id(owner))
        if pair in self._cleanup_pairs:
            return
        self._cleanup_pairs.add(pair)
        widget.destroyed.connect(lambda: self._on_widget_destroyed(pair, owner))

    def suppress(self, token: int) -> None:
        """Temporarily silence a subscription without removing it.

        Counted: each ``suppress`` needs a matching :meth:`resume`, so
        nested/overlapping suppressions compose (prefer :meth:`suppressed`).
        """
        if token in self._subs:
            self._suppressed[token] = self._suppressed.get(token, 0) + 1

    def resume(self, token: int) -> None:
        """Undo one :meth:`suppress`; the subscription re-enables at zero."""
        count = self._suppressed.get(token, 0)
        if count <= 1:
            self._suppressed.pop(token, None)
        else:
            self._suppressed[token] = count - 1

    @contextlib.contextmanager
    def suppressed(self, *tokens: Optional[int]) -> Iterator[None]:
        """Silence *tokens* for the duration of a ``with`` block.

        Replaces the manual ``suppress`` / ``try``/``finally`` ``resume``
        dance around scene-mutating code.  ``None`` tokens are skipped, and
        tokens already suppressed on entry stay suppressed on exit —
        suppression is counted, so nested and overlapping blocks (including
        an earlier block's still-pending deferred resume) compose correctly.

        Notes
        -----
        Maya runs event scriptJobs at **idle**, never inside a synchronous
        block — so an event raised inside the block dispatches only after
        the block exits (live-Maya-probed).  To actually swallow that
        queued dispatch, interactive sessions resume via
        ``cmds.evalDeferred`` — the dispatch was queued before the resume,
        so it runs (silenced) first; an immediate resume would let it
        escape, and ``lowestPriority=True`` starves and never resumes
        (both observed live).  Batch sessions resume immediately —
        scriptJobs never dispatch there and a deferred callback might
        never run.
        """
        active = [t for t in tokens if t is not None]
        for token in active:
            self.suppress(token)
        try:
            yield
        finally:
            if active:

                def _resume_all(tokens=tuple(active)):
                    for token in tokens:
                        self.resume(token)

                try:
                    if cmds.about(batch=True):
                        _resume_all()
                    else:
                        cmds.evalDeferred(_resume_all)
                except Exception:
                    _resume_all()

    def status(self) -> Dict[str, Any]:
        """Return a snapshot of managed and unmanaged Maya event listeners.

        Pairs SJM's internal bookkeeping with ``cmds.scriptJob(listJobs=True)``
        so callers can spot leaked or third-party jobs that bypass SJM.

        Returns
        -------
        dict
            ``managed_jobs`` — ``{event: job_id}`` for SJM-owned scriptJobs.
            ``subscriptions`` — list of dicts (token, event, owner, ephemeral, suppressed).
            ``om_callbacks`` — list of dicts (token, cb_id, owner) for OpenMaya callbacks.
            ``unmanaged_jobs`` — raw ``cmds.scriptJob(listJobs=True)`` entries
            whose leading id is not present in ``managed_jobs.values()``.
        """
        managed_ids = set(self._jobs.values())
        unmanaged: List[str] = []
        try:
            for entry in cmds.scriptJob(listJobs=True) or []:
                head, _, _ = entry.partition(":")
                try:
                    if int(head.strip()) not in managed_ids:
                        unmanaged.append(entry)
                except ValueError:
                    unmanaged.append(entry)
        except Exception as exc:
            logger.debug("ScriptJobManager.status: listJobs failed (%s)", exc)
        return {
            "managed_jobs": dict(self._jobs),
            "subscriptions": [
                {
                    "token": s.token,
                    "event": s.event,
                    "owner": repr(s.owner),
                    "ephemeral": s.ephemeral,
                    "suppressed": t in self._suppressed,
                }
                for t, s in self._subs.items()
            ],
            "om_callbacks": [
                {"token": s.token, "cb_id": s.cb_id, "owner": repr(s.owner)}
                for s in self._om_subs.values()
            ],
            "unmanaged_jobs": unmanaged,
        }

    def print_status(self) -> None:
        """Pretty-print :meth:`status` for interactive debugging in Maya."""
        s = self.status()
        print("ScriptJobManager status")
        print(f"  managed jobs ({len(s['managed_jobs'])}):")
        for event, job_id in s["managed_jobs"].items():
            print(f"    [{job_id}] {event}")
        print(f"  subscriptions ({len(s['subscriptions'])}):")
        for sub in s["subscriptions"]:
            flags = []
            if sub["ephemeral"]:
                flags.append("ephemeral")
            if sub["suppressed"]:
                flags.append("suppressed")
            tag = f" ({', '.join(flags)})" if flags else ""
            print(f"    #{sub['token']} {sub['event']} owner={sub['owner']}{tag}")
        print(f"  OM callbacks ({len(s['om_callbacks'])}):")
        for cb in s["om_callbacks"]:
            print(f"    #{cb['token']} cb_id={cb['cb_id']} owner={cb['owner']}")
        print(f"  unmanaged Maya scriptJobs ({len(s['unmanaged_jobs'])}):")
        for entry in s["unmanaged_jobs"]:
            print(f"    {entry}")

    def teardown(self) -> None:
        """Kill every managed scriptJob, OM callback, and subscription."""
        for event in list(self._jobs):
            self._kill_job(event)
        for sub in list(self._om_subs.values()):
            self._remove_om_callback(sub.cb_id)
        self._subs.clear()
        self._events.clear()
        self._om_subs.clear()
        self._suppressed.clear()
        self._cleanup_pairs.clear()

    # -------------------------------------------------------------- internals

    def _ensure_job(self, event: str) -> None:
        """Create the shared scriptJob for *event* if it doesn't exist yet.

        The job is ``protected`` so a stray ``scriptJob -killAll`` from
        another tool can't silently kill the dispatcher; SJM's own
        :meth:`_kill_job` uses ``force=True``, which removes protected jobs.
        """
        if event in self._jobs:
            return
        job_id = cmds.scriptJob(
            event=[event, lambda e=event: self._dispatch(e)], protected=True
        )
        self._jobs[event] = job_id
        logger.debug("ScriptJobManager: created job %d for %r", job_id, event)

    def _kill_job(self, event: str) -> None:
        """Kill the scriptJob for *event* and remove it from tracking."""
        job_id = self._jobs.pop(event, None)
        if job_id is not None:
            try:
                if cmds.scriptJob(exists=job_id):
                    cmds.scriptJob(kill=job_id, force=True)
                    logger.debug(
                        "ScriptJobManager: killed job %d for %r", job_id, event
                    )
            except Exception as exc:
                logger.debug(
                    "ScriptJobManager: failed to kill job %r for %r (%s)",
                    job_id,
                    event,
                    exc,
                )
        self._events.pop(event, None)

    def _dispatch(self, event: str) -> None:
        """Dispatch *event* to all current subscribers, then prune ephemerals."""
        tokens = list(self._events.get(event, []))
        for token in tokens:
            if token in self._suppressed:
                continue
            sub = self._subs.get(token)
            if sub is not None:
                try:
                    sub.callback()
                except Exception:
                    logger.warning(
                        "ScriptJobManager: %r listener error (owner=%r)",
                        event,
                        sub.owner,
                        exc_info=True,
                    )
        # Prune ephemeral subscriptions on scene change
        if event in _SCENE_CHANGE_EVENTS:
            self._prune_ephemerals()

    def _remove_om_callback(self, cb_id: Any) -> None:
        """Remove a single OpenMaya callback by id (best effort)."""
        try:
            import maya.api.OpenMaya as om2

            om2.MMessage.removeCallback(cb_id)
        except Exception as exc:
            logger.debug(
                "ScriptJobManager: failed to remove OM callback %s (%s)",
                cb_id,
                exc,
            )

    def _prune_ephemerals(self) -> None:
        """Remove all ephemeral subscriptions (scene changed)."""
        to_remove = [t for t, s in self._subs.items() if s.ephemeral]
        for token in to_remove:
            self.unsubscribe(token)

    def _on_widget_destroyed(self, pair: Tuple[int, int], owner: Any) -> None:
        """Handle Qt widget destruction — clean up all owner subscriptions."""
        self._cleanup_pairs.discard(pair)
        self.unsubscribe_all(owner)
