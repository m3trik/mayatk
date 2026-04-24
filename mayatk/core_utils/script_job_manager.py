# !/usr/bin/python
# coding=utf-8
"""Centralized Maya event subscription manager.

Two kinds of Maya event sources are unified under one cleanup path:

1. ``pm.scriptJob`` events (``SelectionChanged``, ``timeChanged``, …) —
   multiplexed so at most one ``pm.scriptJob`` exists per event name.
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
"""
from __future__ import annotations

import itertools
import logging
from typing import Any, Callable, Dict, List, Optional, Set

try:
    import pymel.core as pm
except ImportError:
    pm = None

logger = logging.getLogger(__name__)

_SCENE_CHANGE_EVENTS = frozenset({"SceneOpened", "NewSceneOpened"})


class _Subscription:
    """Internal subscription record for a ``pm.scriptJob`` listener."""

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

    Creates at most one ``pm.scriptJob`` per Maya event name and
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
        self._connected_widgets: Set[int] = set()
        self._suppressed: Dict[int, bool] = {}  # token -> True while suppressed

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
        token = next(self._counter)
        sub = _Subscription(token, event, callback, owner, ephemeral)
        self._subs[token] = sub
        self._events.setdefault(event, []).append(token)
        self._ensure_job(event)
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
        except Exception as exc:
            logger.debug(
                "ScriptJobManager.add_om_callback: %s failed (%s)",
                register_fn,
                exc,
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

        Safe to call multiple times for the same *widget* / *owner* pair.
        """
        wid = id(widget)
        if wid in self._connected_widgets:
            return
        self._connected_widgets.add(wid)
        widget.destroyed.connect(lambda: self._on_widget_destroyed(wid, owner))

    def suppress(self, token: int) -> None:
        """Temporarily silence a subscription without removing it."""
        self._suppressed[token] = True

    def resume(self, token: int) -> None:
        """Re-enable a previously suppressed subscription."""
        self._suppressed.pop(token, None)

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
        self._connected_widgets.clear()

    # -------------------------------------------------------------- internals

    def _ensure_job(self, event: str) -> None:
        """Create the shared scriptJob for *event* if it doesn't exist yet."""
        if event in self._jobs:
            return
        if pm is None:
            return
        job_id = pm.scriptJob(event=[event, lambda e=event: self._dispatch(e)])
        self._jobs[event] = job_id
        logger.debug("ScriptJobManager: created job %d for %r", job_id, event)

    def _kill_job(self, event: str) -> None:
        """Kill the scriptJob for *event* and remove it from tracking."""
        job_id = self._jobs.pop(event, None)
        if job_id is not None and pm is not None:
            try:
                if pm.scriptJob(exists=job_id):
                    pm.scriptJob(kill=job_id, force=True)
                    logger.debug(
                        "ScriptJobManager: killed job %d for %r", job_id, event
                    )
            except Exception:
                pass
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
                except Exception as exc:
                    logger.debug("ScriptJobManager: %r listener error: %s", event, exc)
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

    def _on_widget_destroyed(self, wid: int, owner: Any) -> None:
        """Handle Qt widget destruction — clean up all owner subscriptions."""
        self._connected_widgets.discard(wid)
        self.unsubscribe_all(owner)
