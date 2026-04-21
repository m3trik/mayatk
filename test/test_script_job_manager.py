# !/usr/bin/python
# coding=utf-8
"""Tests for ScriptJobManager — centralized Maya scriptJob dispatcher.

Covers:
- subscribe / unsubscribe / unsubscribe_all lifecycle
- One job per event (multiplexing)
- Ephemeral subscription pruning on scene-change events
- suppress / resume
- connect_cleanup (widget destroy → unsubscribe_all)
- teardown clears everything
- Singleton reset
- Dispatch error isolation
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

# conftest.py is auto-loaded by pytest and injects mock_pm into
# sys.modules["pymel.core"].  Grab it from there.
mock_pm = sys.modules["pymel.core"]

from mayatk.core_utils.script_job_manager import ScriptJobManager


class ScriptJobManagerTestCase(unittest.TestCase):
    """Base that resets the singleton before each test."""

    def setUp(self):
        mock_pm.reset_mock()
        mock_pm.scriptJob.return_value = 999
        mock_pm.scriptJob.side_effect = lambda **kw: 999 if "event" in kw else True
        ScriptJobManager.reset()
        self.mgr = ScriptJobManager.instance()

    def tearDown(self):
        ScriptJobManager.reset()


class TestSingleton(ScriptJobManagerTestCase):
    """Singleton access and reset."""

    def test_instance_returns_same_object(self):
        self.assertIs(ScriptJobManager.instance(), self.mgr)

    def test_reset_creates_new_instance(self):
        old = self.mgr
        ScriptJobManager.reset()
        new = ScriptJobManager.instance()
        self.assertIsNot(new, old)


class TestSubscribe(ScriptJobManagerTestCase):
    """subscribe / unsubscribe lifecycle."""

    def test_subscribe_returns_unique_tokens(self):
        t1 = self.mgr.subscribe("SceneOpened", lambda: None)
        t2 = self.mgr.subscribe("SceneOpened", lambda: None)
        self.assertNotEqual(t1, t2)

    def test_subscribe_creates_one_job_per_event(self):
        """Multiple subscribers to the same event share one scriptJob."""
        mock_pm.scriptJob.reset_mock()
        self.mgr.subscribe("SelectionChanged", lambda: None)
        self.mgr.subscribe("SelectionChanged", lambda: None)

        # pm.scriptJob(event=[...]) should be called exactly once
        event_calls = [
            c for c in mock_pm.scriptJob.call_args_list if "event" in c.kwargs
        ]
        self.assertEqual(len(event_calls), 1)

    def test_subscribe_different_events_create_separate_jobs(self):
        mock_pm.scriptJob.reset_mock()
        self.mgr.subscribe("SceneOpened", lambda: None)
        self.mgr.subscribe("SelectionChanged", lambda: None)

        event_calls = [
            c for c in mock_pm.scriptJob.call_args_list if "event" in c.kwargs
        ]
        self.assertEqual(len(event_calls), 2)

    def test_unsubscribe_removes_subscription(self):
        cb = MagicMock()
        token = self.mgr.subscribe("SceneOpened", cb)
        self.mgr.unsubscribe(token)

        # Dispatch should not call the callback
        self.mgr._dispatch("SceneOpened")
        cb.assert_not_called()

    def test_unsubscribe_unknown_token_is_noop(self):
        self.mgr.unsubscribe(99999)  # should not raise

    def test_unsubscribe_last_subscriber_kills_job(self):
        """When the last subscriber for an event is removed, the job is killed."""
        mock_pm.scriptJob.reset_mock()
        mock_pm.scriptJob.side_effect = lambda **kw: 42 if "event" in kw else True
        token = self.mgr.subscribe("SceneOpened", lambda: None)

        self.mgr.unsubscribe(token)

        # pm.scriptJob(kill=42, force=True) should have been called
        kill_calls = [
            c for c in mock_pm.scriptJob.call_args_list if "kill" in c.kwargs
        ]
        self.assertEqual(len(kill_calls), 1, "Expected exactly one kill call")
        self.assertEqual(kill_calls[0].kwargs["kill"], 42)

    def test_unsubscribe_all_by_owner(self):
        owner = object()
        cb1 = MagicMock()
        cb2 = MagicMock()
        self.mgr.subscribe("SceneOpened", cb1, owner=owner)
        self.mgr.subscribe("SelectionChanged", cb2, owner=owner)

        self.mgr.unsubscribe_all(owner)

        self.mgr._dispatch("SceneOpened")
        self.mgr._dispatch("SelectionChanged")
        cb1.assert_not_called()
        cb2.assert_not_called()

    def test_unsubscribe_all_leaves_other_owners(self):
        owner_a = object()
        owner_b = object()
        cb_a = MagicMock()
        cb_b = MagicMock()
        self.mgr.subscribe("SceneOpened", cb_a, owner=owner_a)
        self.mgr.subscribe("SceneOpened", cb_b, owner=owner_b)

        self.mgr.unsubscribe_all(owner_a)

        self.mgr._dispatch("SceneOpened")
        cb_a.assert_not_called()
        cb_b.assert_called_once()


class TestDispatch(ScriptJobManagerTestCase):
    """Event dispatch and error isolation."""

    def test_dispatch_calls_all_subscribers(self):
        cb1 = MagicMock()
        cb2 = MagicMock()
        self.mgr.subscribe("SceneOpened", cb1)
        self.mgr.subscribe("SceneOpened", cb2)

        self.mgr._dispatch("SceneOpened")

        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_dispatch_isolates_errors(self):
        """A failing callback must not prevent subsequent callbacks from firing."""
        cb_bad = MagicMock(side_effect=RuntimeError("boom"))
        cb_good = MagicMock()
        self.mgr.subscribe("SceneOpened", cb_bad)
        self.mgr.subscribe("SceneOpened", cb_good)

        self.mgr._dispatch("SceneOpened")

        cb_bad.assert_called_once()
        cb_good.assert_called_once()


class TestEphemeral(ScriptJobManagerTestCase):
    """Ephemeral subscriptions are pruned on scene-change events."""

    def test_ephemeral_pruned_on_scene_opened(self):
        cb = MagicMock()
        self.mgr.subscribe("SelectionChanged", cb, ephemeral=True)

        # Simulate a SceneOpened dispatch
        self.mgr._dispatch("SceneOpened")

        # The ephemeral sub should have been pruned
        cb.reset_mock()
        self.mgr._dispatch("SelectionChanged")
        cb.assert_not_called()

    def test_ephemeral_pruned_on_new_scene_opened(self):
        cb = MagicMock()
        self.mgr.subscribe("timeChanged", cb, ephemeral=True)

        self.mgr._dispatch("NewSceneOpened")

        cb.reset_mock()
        self.mgr._dispatch("timeChanged")
        cb.assert_not_called()

    def test_persistent_survives_scene_change(self):
        cb = MagicMock()
        self.mgr.subscribe("SceneOpened", cb, ephemeral=False)

        self.mgr._dispatch("SceneOpened")
        self.mgr._dispatch("SceneOpened")

        self.assertEqual(cb.call_count, 2)

    def test_ephemeral_fires_before_pruning(self):
        """Ephemerals subscribed to a scene-change event fire once then die."""
        cb = MagicMock()
        self.mgr.subscribe("SceneOpened", cb, ephemeral=True)

        self.mgr._dispatch("SceneOpened")
        self.assertEqual(cb.call_count, 1, "Should fire during the dispatch")

        cb.reset_mock()
        self.mgr._dispatch("SceneOpened")
        cb.assert_not_called()


class TestSuppressResume(ScriptJobManagerTestCase):
    """suppress / resume silencing."""

    def test_suppressed_callback_not_called(self):
        cb = MagicMock()
        token = self.mgr.subscribe("SceneOpened", cb)

        self.mgr.suppress(token)
        self.mgr._dispatch("SceneOpened")
        cb.assert_not_called()

    def test_resumed_callback_called(self):
        cb = MagicMock()
        token = self.mgr.subscribe("SceneOpened", cb)

        self.mgr.suppress(token)
        self.mgr.resume(token)
        self.mgr._dispatch("SceneOpened")
        cb.assert_called_once()


class TestConnectCleanup(ScriptJobManagerTestCase):
    """connect_cleanup ties widget destruction to unsubscribe_all."""

    def test_widget_destroyed_unsubscribes_owner(self):
        owner = object()
        cb = MagicMock()
        self.mgr.subscribe("SceneOpened", cb, owner=owner)

        widget = MagicMock()
        self.mgr.connect_cleanup(widget, owner)

        # Simulate Qt widget destruction — grab the connected slot
        widget.destroyed.connect.assert_called_once()
        destroy_slot = widget.destroyed.connect.call_args[0][0]
        destroy_slot()

        # Callback should no longer fire
        self.mgr._dispatch("SceneOpened")
        cb.assert_not_called()

    def test_connect_cleanup_idempotent(self):
        """Calling connect_cleanup twice for same widget is a no-op."""
        widget = MagicMock()
        owner = object()
        self.mgr.connect_cleanup(widget, owner)
        self.mgr.connect_cleanup(widget, owner)

        widget.destroyed.connect.assert_called_once()


class TestTeardown(ScriptJobManagerTestCase):
    """teardown kills all jobs and clears state."""

    def test_teardown_clears_subscriptions(self):
        cb = MagicMock()
        self.mgr.subscribe("SceneOpened", cb)
        self.mgr.subscribe("SelectionChanged", cb)

        self.mgr.teardown()

        self.assertEqual(len(self.mgr._subs), 0)
        self.assertEqual(len(self.mgr._events), 0)
        self.assertEqual(len(self.mgr._jobs), 0)

    def test_teardown_clears_suppressed(self):
        """Suppressed entries must not leak after teardown."""
        token = self.mgr.subscribe("SceneOpened", lambda: None)
        self.mgr.suppress(token)
        self.assertTrue(len(self.mgr._suppressed) > 0)

        self.mgr.teardown()

        self.assertEqual(len(self.mgr._suppressed), 0)

    def test_teardown_kills_jobs(self):
        mock_pm.scriptJob.reset_mock()
        mock_pm.scriptJob.side_effect = lambda **kw: 42 if "event" in kw else True
        self.mgr.subscribe("SceneOpened", lambda: None)

        self.mgr.teardown()

        kill_calls = [
            c for c in mock_pm.scriptJob.call_args_list if "kill" in c.kwargs
        ]
        self.assertEqual(len(kill_calls), 1, "Expected exactly one kill call")
        self.assertEqual(kill_calls[0].kwargs["kill"], 42)


# ===========================================================================
# Adoption tests — MayaScenePersistence
# ===========================================================================


class TestMayaScenePersistence(ScriptJobManagerTestCase):
    """MayaScenePersistence delegates scene lifecycle to ScriptJobManager."""

    def _make_persistence(self):
        from mayatk.anim_utils.shots._shots import MayaScenePersistence

        return MayaScenePersistence()

    def test_install_subscribes_three_events(self):
        """__init__ subscribes SceneOpened, NewSceneOpened, timeUnitChanged."""
        persistence = self._make_persistence()
        events = [s.event for s in self.mgr._subs.values() if s.owner is persistence]
        self.assertIn("SceneOpened", events)
        self.assertIn("NewSceneOpened", events)
        self.assertIn("timeUnitChanged", events)
        self.assertEqual(len(events), 3)

    def test_install_is_idempotent(self):
        """Calling _install_scene_jobs twice must not duplicate subscriptions."""
        persistence = self._make_persistence()
        persistence._install_scene_jobs()
        events = [s.event for s in self.mgr._subs.values() if s.owner is persistence]
        self.assertEqual(len(events), 3)

    def test_remove_callbacks_clears_subscriptions(self):
        """remove_callbacks must unsubscribe all and reset the flag."""
        persistence = self._make_persistence()
        persistence.remove_callbacks()
        events = [s.event for s in self.mgr._subs.values() if s.owner is persistence]
        self.assertEqual(len(events), 0)
        self.assertFalse(persistence._scene_subs_installed)

    def test_remove_callbacks_clears_om_callback(self):
        """remove_callbacks must also clear the OM kBeforeSave callback."""
        persistence = self._make_persistence()
        self.assertIsNotNone(persistence._before_save_cb_id)
        persistence.remove_callbacks()
        self.assertIsNone(persistence._before_save_cb_id)


# ===========================================================================
# Adoption tests — ChannelBox
# ===========================================================================


class TestChannelBoxSJM(ScriptJobManagerTestCase):
    """ChannelBox.watch_selection delegates to ScriptJobManager."""

    def setUp(self):
        super().setUp()
        from mayatk.ui_utils.channel_box import ChannelBox

        self.ChannelBox = ChannelBox
        # Reset classvar state between tests
        ChannelBox._selection_token = None
        ChannelBox._selection_watchers = []

    def tearDown(self):
        self.ChannelBox._selection_token = None
        self.ChannelBox._selection_watchers = []
        super().tearDown()

    def test_watch_subscribes_via_sjm(self):
        """watch_selection should create an SJM subscription."""
        token = self.ChannelBox.watch_selection(lambda attrs: None)
        self.assertIsNotNone(token)
        subs = [s for s in self.mgr._subs.values() if s.owner is self.ChannelBox]
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].event, "SelectionChanged")

    def test_watch_idempotent(self):
        """Multiple watch_selection calls share one subscription."""
        self.ChannelBox.watch_selection(lambda attrs: None)
        self.ChannelBox.watch_selection(lambda attrs: None)
        subs = [s for s in self.mgr._subs.values() if s.owner is self.ChannelBox]
        self.assertEqual(len(subs), 1)

    def test_unwatch_last_removes_subscription(self):
        """Removing all watchers unsubscribes from SJM."""
        cb = lambda attrs: None
        self.ChannelBox.watch_selection(cb)
        self.ChannelBox.unwatch_selection(cb)
        subs = [s for s in self.mgr._subs.values() if s.owner is self.ChannelBox]
        self.assertEqual(len(subs), 0)
        self.assertIsNone(self.ChannelBox._selection_token)

    def test_unwatch_none_removes_all(self):
        """unwatch_selection(None) clears all watchers and unsubscribes."""
        self.ChannelBox.watch_selection(lambda attrs: None)
        self.ChannelBox.watch_selection(lambda attrs: None)
        self.ChannelBox.unwatch_selection(None)
        self.assertEqual(len(self.ChannelBox._selection_watchers), 0)
        self.assertIsNone(self.ChannelBox._selection_token)
        subs = [s for s in self.mgr._subs.values() if s.owner is self.ChannelBox]
        self.assertEqual(len(subs), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
