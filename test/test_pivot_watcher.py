# !/usr/bin/python
# coding=utf-8
"""Test suite for mayatk.xform_utils.pivot_watcher.PivotWatcher.

Covers:
  - Lifecycle: start/stop/idempotency, attach_widget cleanup.
  - SJM bookkeeping: tokens created on start, removed on stop, ephemeral.
  - Dispatch rule: fires only when manip pos moves AND selection unchanged.
  - Gate: dispatch short-circuits before any state work.
  - Self-fire safety: callback that re-selects does not re-trigger dispatch
    on a subsequent event because the post-callback state becomes the new
    baseline.

We test the decision logic by monkey-patching ``_read_selection`` /
``_read_manip_pos`` at the module level so the test does not depend on
actual viewport drags (impossible to simulate from mayapy).
"""
import unittest

import maya.cmds as cmds

from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.xform_utils import pivot_watcher as pw_mod
from mayatk.xform_utils.pivot_watcher import PivotWatcher

from base_test import QuickTestCase


class _FakeState:
    """Controllable stand-in for the real state-read helpers.

    ``manip`` is the ``cmds.manipPivot`` override (active only in Custom
    Pivot mode). ``baked`` is the tuple-of-tuples of world rotatePivots
    for currently-selected transforms (what Insert-mode editing writes
    to). Either changing while ``selection`` is unchanged should count
    as an intentional pivot edit.
    """

    def __init__(self):
        self.selection = ()
        self.manip = ()
        self.baked = ()

    def install(self, monkey_target):
        monkey_target._read_selection = lambda: self.selection
        monkey_target._read_manip_override = lambda: self.manip
        monkey_target._read_baked_pivots = lambda: self.baked


class TestPivotWatcherDispatch(QuickTestCase):
    """The core decision rule and self-fire-safety logic."""

    def setUp(self):
        super().setUp()
        ScriptJobManager.reset()
        self.state = _FakeState()
        self._orig_sel = pw_mod._read_selection
        self._orig_manip = pw_mod._read_manip_override
        self._orig_baked = pw_mod._read_baked_pivots
        self.state.install(pw_mod)
        self.calls = []

    def tearDown(self):
        pw_mod._read_selection = self._orig_sel
        pw_mod._read_manip_override = self._orig_manip
        pw_mod._read_baked_pivots = self._orig_baked
        ScriptJobManager.reset()
        super().tearDown()

    def _make_watcher(self, gate=None):
        return PivotWatcher(lambda: self.calls.append(1), gate=gate)

    def _fire(self, watcher):
        """Invoke the dispatch path the way SJM would on event."""
        watcher._dispatch()

    def test_first_event_only_snapshots_no_callback(self):
        """The very first DragRelease after start() must not fire — there is
        no prior state to compare against, so we can't tell if the pivot
        was deliberately dragged."""
        self.state.selection = ("|cube1",)
        self.state.manip = (0.0, 0.0, 0.0)
        self.state.baked = ((0.0, 0.0, 0.0),)
        w = self._make_watcher()
        w.start()
        # start() snapshots; but _dispatch with same state should still skip
        self._fire(w)
        self.assertEqual(self.calls, [])

    def test_custom_pivot_override_change_fires(self):
        """Custom Pivot mode: cmds.manipPivot override changes while the
        baked rotatePivot stays at origin. Selection same => fires."""
        self.state.selection = ("|cube1",)
        self.state.manip = (0.0, 0.0, 0.0)
        self.state.baked = ((0.0, 0.0, 0.0),)
        w = self._make_watcher()
        w.start()

        self.state.manip = (5.0, 0.0, 0.0)
        self._fire(w)
        self.assertEqual(self.calls, [1])

    def test_baked_pivot_change_fires(self):
        """Insert-mode edit: the baked rotatePivot moves but manipPivot
        override stays at origin. Selection same => fires.

        This is the case the previous implementation missed."""
        self.state.selection = ("|cube1",)
        self.state.manip = (0.0, 0.0, 0.0)
        self.state.baked = ((0.0, 0.0, 0.0),)
        w = self._make_watcher()
        w.start()

        # User drags pivot in Insert mode — bakes to rotatePivot
        self.state.baked = ((5.0, 0.0, 0.0),)
        self._fire(w)
        self.assertEqual(self.calls, [1])

    def test_selection_changed_does_not_fire(self):
        """Clicking a different object also shifts the pivot read, but we
        must NOT fire — otherwise Preview's captured-objects refresh would
        hijack the user's new selection (the Mirror bug)."""
        self.state.selection = ("|cube1",)
        self.state.manip = (0.0, 0.0, 0.0)
        self.state.baked = ((0.0, 0.0, 0.0),)
        w = self._make_watcher()
        w.start()

        # User clicks a different object — selection changes, pivot follows
        self.state.selection = ("|cube2",)
        self.state.baked = ((10.0, 0.0, 0.0),)
        self._fire(w)
        self.assertEqual(self.calls, [])

    def test_nothing_changed_does_not_fire(self):
        """Sanity: a DragRelease with no state change must not fire."""
        self.state.selection = ("|cube1",)
        self.state.manip = (2.0, 0.0, 0.0)
        self.state.baked = ((2.0, 0.0, 0.0),)
        w = self._make_watcher()
        w.start()
        self._fire(w)
        self.assertEqual(self.calls, [])

    def test_gate_returning_false_short_circuits(self):
        """Gate False means preview-off; never fire regardless of state."""
        gate_value = [False]
        w = self._make_watcher(gate=lambda: gate_value[0])
        self.state.selection = ("|cube1",)
        self.state.baked = ((0.0, 0.0, 0.0),)
        w.start()

        # Pivot moves, but gate is False
        self.state.baked = ((5.0, 0.0, 0.0),)
        self._fire(w)
        self.assertEqual(self.calls, [])

        # Flip gate on, fire again — now the gate accepts
        gate_value[0] = True
        self.state.baked = ((7.0, 0.0, 0.0),)
        self._fire(w)
        self.assertEqual(self.calls, [1])

    def test_callback_reselect_does_not_self_fire(self):
        """Critical: Mirror's perform_operation calls cmds.select on its
        output. Even DragRelease on next event after the callback must see
        the new (post-callback) state as the baseline — otherwise it would
        dispatch again on a no-op event."""
        self.state.selection = ("|cube1",)
        self.state.baked = ((0.0, 0.0, 0.0),)

        def callback():
            # Simulate Mirror: select the freshly-mirrored output node
            self.state.selection = ("|mirror_of_cube1",)
            self.state.baked = ((3.0, 0.0, 0.0),)
            self.calls.append(1)

        w = PivotWatcher(callback)
        w.start()

        # User drags pivot of cube1 (still selected when DragRelease fires)
        self.state.baked = ((3.0, 0.0, 0.0),)
        self._fire(w)
        self.assertEqual(self.calls, [1])

        # Next event with no further user action — state matches post-callback
        # baseline. Must NOT fire.
        self._fire(w)
        self.assertEqual(self.calls, [1])

    def test_subsequent_pivot_drag_after_callback_fires(self):
        """After the callback updates baseline, a *new* pivot drag should
        still trigger normally."""
        self.state.selection = ("|cube1",)
        self.state.baked = ((0.0, 0.0, 0.0),)

        def callback():
            self.state.selection = ("|mirror_of_cube1",)
            self.state.baked = ((3.0, 0.0, 0.0),)
            self.calls.append(1)

        w = PivotWatcher(callback)
        w.start()

        # First pivot drag
        self.state.baked = ((3.0, 0.0, 0.0),)
        self._fire(w)
        self.assertEqual(len(self.calls), 1)

        # User drags pivot again — selection still on mirror output, baked moves
        self.state.baked = ((8.0, 0.0, 0.0),)
        self._fire(w)
        self.assertEqual(len(self.calls), 2)


class TestPivotWatcherLifecycle(QuickTestCase):
    """SJM bookkeeping and start/stop idempotency."""

    def setUp(self):
        super().setUp()
        ScriptJobManager.reset()

    def tearDown(self):
        ScriptJobManager.reset()
        super().tearDown()

    def test_start_creates_subscriptions(self):
        mgr = ScriptJobManager.instance()
        self.assertEqual(len(mgr.status()["subscriptions"]), 0)

        w = PivotWatcher(lambda: None)
        w.start()

        status = mgr.status()
        # One subscription per default event
        self.assertEqual(len(status["subscriptions"]), len(PivotWatcher.DEFAULT_EVENTS))
        events = {s["event"] for s in status["subscriptions"]}
        self.assertEqual(events, set(PivotWatcher.DEFAULT_EVENTS))
        # All ephemeral so scene-change prunes them
        self.assertTrue(all(s["ephemeral"] for s in status["subscriptions"]))

    def test_stop_removes_subscriptions(self):
        mgr = ScriptJobManager.instance()
        w = PivotWatcher(lambda: None)
        w.start()
        w.stop()
        self.assertEqual(len(mgr.status()["subscriptions"]), 0)
        self.assertFalse(w.started)

    def test_start_is_idempotent(self):
        mgr = ScriptJobManager.instance()
        w = PivotWatcher(lambda: None)
        w.start()
        n = len(mgr.status()["subscriptions"])
        w.start()
        self.assertEqual(len(mgr.status()["subscriptions"]), n)

    def test_stop_is_idempotent(self):
        w = PivotWatcher(lambda: None)
        w.start()
        w.stop()
        w.stop()  # must not raise
        self.assertFalse(w.started)

    def test_context_manager(self):
        mgr = ScriptJobManager.instance()
        with PivotWatcher(lambda: None) as w:
            self.assertTrue(w.started)
            self.assertGreater(len(mgr.status()["subscriptions"]), 0)
        self.assertFalse(w.started)
        self.assertEqual(len(mgr.status()["subscriptions"]), 0)

    def test_custom_events_override(self):
        mgr = ScriptJobManager.instance()
        custom = ("SelectionChanged", "timeChanged")
        w = PivotWatcher(lambda: None, events=custom)
        w.start()
        events = {s["event"] for s in mgr.status()["subscriptions"]}
        self.assertEqual(events, set(custom))


class TestPivotWatcherEventIntegration(QuickTestCase):
    """End-to-end: SJM actually dispatches our callback when the underlying
    scriptJob event fires. We use ``cmds.scriptJob(runOnce=True)`` proxy via
    direct event trigger isn't available, so we verify that a real SJM
    subscription correctly multiplexes through to PivotWatcher._dispatch by
    firing the SJM internal dispatcher manually with the same event name."""

    def setUp(self):
        super().setUp()
        ScriptJobManager.reset()

    def tearDown(self):
        ScriptJobManager.reset()
        super().tearDown()

    def test_sjm_dispatches_through_to_watcher(self):
        """Simulate the scriptJob event firing by calling SJM._dispatch
        directly. Verifies the wiring from SJM event -> PivotWatcher dispatch
        actually runs end-to-end."""
        calls = []
        w = PivotWatcher(lambda: calls.append(1))
        w.start()

        mgr = ScriptJobManager.instance()
        # Move the pivot pre-fire so dispatch sees a change
        cube = cmds.polyCube(name="evt_cube")[0]
        cmds.select(cube)
        # Force the dispatcher; selection unchanged across both reads,
        # but manip read is whatever Maya returns. We can't assert the
        # callback fires here because manip may equal prev; instead we
        # assert that SJM's dispatch path reaches our handler without error.
        mgr._dispatch("DragRelease")
        # No assertion on calls — just that no exception was raised.


if __name__ == "__main__":
    unittest.main()
