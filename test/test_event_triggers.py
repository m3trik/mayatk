# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.node_utils.attributes.event_triggers.EventTriggers.

Covers the per-object event-trigger attribute lifecycle used to author
named animation events keyed from the channel-box dropdown, then bake
them to a portable manifest string for FBX/engine export.

The module had no dedicated test file before (only an old .legacy
reference). Public surface covered here:

  attr_names · create · ensure · add_events · get_events · event_index ·
  set_key · clear_key · iter_keyed_events · bake_manifest · remove
"""
import unittest

import maya.cmds as cmds

from mayatk.node_utils.attributes.event_triggers import EventTriggers

from base_test import MayaTkTestCase, QuickTestCase


class TestAttrNames(QuickTestCase):
    """Pure-Python: (trigger_attr, manifest_attr) pair generation."""

    def test_default_category(self):
        trig, manifest = EventTriggers.attr_names()
        self.assertEqual(trig, "event_trigger")
        self.assertEqual(manifest, "event_manifest")

    def test_explicit_category(self):
        trig, manifest = EventTriggers.attr_names("audio")
        self.assertEqual(trig, "audio_trigger")
        self.assertEqual(manifest, "audio_manifest")

    def test_none_falls_back_to_default(self):
        trig, manifest = EventTriggers.attr_names(None)
        self.assertEqual(trig, "event_trigger")
        self.assertEqual(manifest, "event_manifest")


class TestCreateAndQuery(MayaTkTestCase):
    """create() adds the enum trigger attr; get_events / event_index read it."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="evt_cube")[0]

    def test_create_adds_trigger_attribute(self):
        EventTriggers.create([self.cube], events=["Footstep", "Jump"])
        self.assertTrue(
            cmds.attributeQuery("event_trigger", node=self.cube, exists=True)
        )

    def test_create_bakes_manifest_attribute(self):
        """create() auto-bakes — manifest_attr should exist (even if empty)."""
        EventTriggers.create([self.cube], events=["Footstep"])
        self.assertTrue(
            cmds.attributeQuery("event_manifest", node=self.cube, exists=True)
        )

    def test_get_events_returns_none_plus_user_events(self):
        EventTriggers.create([self.cube], events=["Footstep", "Jump"])
        events = EventTriggers.get_events(self.cube)
        # Index 0 is always "None".
        self.assertEqual(events[0], "None")
        self.assertIn("Footstep", events)
        self.assertIn("Jump", events)

    def test_event_index_lookups(self):
        EventTriggers.create([self.cube], events=["Footstep", "Jump", "Land"])
        self.assertEqual(EventTriggers.event_index(self.cube, "None"), 0)
        self.assertGreater(EventTriggers.event_index(self.cube, "Footstep"), 0)
        # Unknown event name returns -1
        self.assertEqual(EventTriggers.event_index(self.cube, "Unknown"), -1)

    def test_get_events_on_unattributed_node_returns_empty(self):
        # No create() called — attribute doesn't exist.
        bare = cmds.polyCube(name="evt_bare")[0]
        self.assertEqual(EventTriggers.get_events(bare), [])

    def test_create_with_custom_category(self):
        EventTriggers.create(
            [self.cube], events=["Sparks"], category="vfx"
        )
        self.assertTrue(
            cmds.attributeQuery("vfx_trigger", node=self.cube, exists=True)
        )
        self.assertTrue(
            cmds.attributeQuery("vfx_manifest", node=self.cube, exists=True)
        )
        # Default category should be absent — categories are independent
        self.assertFalse(
            cmds.attributeQuery("event_trigger", node=self.cube, exists=True)
        )


class TestEnsureNonDestructive(MayaTkTestCase):
    """ensure() must not stomp existing keyframes when re-invoked."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="evt_ensure")[0]

    def test_ensure_preserves_existing_keyframes(self):
        EventTriggers.create([self.cube], events=["Footstep"])
        EventTriggers.set_key(self.cube, "Footstep", time=10)

        # Re-invoke ensure with additional events — keyframes must survive.
        EventTriggers.ensure(
            [self.cube], events=["Footstep", "Jump"]
        )

        keyed = EventTriggers.iter_keyed_events(self.cube)
        # Must still have the Footstep key at t=10.
        self.assertTrue(
            any(label == "Footstep" and t == 10.0 for t, label in keyed),
            f"Expected (10.0, 'Footstep') in {keyed}",
        )

    def test_ensure_appends_new_event_names(self):
        EventTriggers.create([self.cube], events=["Footstep"])
        EventTriggers.ensure([self.cube], events=["Jump", "Land"])
        events = EventTriggers.get_events(self.cube)
        for name in ("Footstep", "Jump", "Land"):
            self.assertIn(name, events)


class TestAddEvents(MayaTkTestCase):
    """add_events appends to existing enum without disturbing keyframes."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="evt_add")[0]
        EventTriggers.create([self.cube], events=["A", "B"])

    def test_add_events_appends(self):
        EventTriggers.add_events([self.cube], events=["C", "D"])
        events = EventTriggers.get_events(self.cube)
        for name in ("A", "B", "C", "D"):
            self.assertIn(name, events)

    def test_add_events_preserves_keyframe_at_existing_event(self):
        EventTriggers.set_key(self.cube, "A", time=5)
        EventTriggers.add_events([self.cube], events=["C"])

        keyed = EventTriggers.iter_keyed_events(self.cube)
        self.assertTrue(any(label == "A" and t == 5.0 for t, label in keyed))

    def test_add_events_warns_when_node_has_no_trigger_attr(self):
        bare = cmds.polyCube(name="evt_no_trigger")[0]
        # Should silently skip (just log a warning) — not raise.
        EventTriggers.add_events([bare], events=["X"])


class TestKeyframes(MayaTkTestCase):
    """set_key / clear_key / iter_keyed_events behavior."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="evt_key")[0]
        EventTriggers.create([self.cube], events=["Footstep", "Jump", "Land"])

    def test_set_key_creates_keyframe_at_time(self):
        ok = EventTriggers.set_key(self.cube, "Footstep", time=12)
        self.assertTrue(ok)
        keyed = EventTriggers.iter_keyed_events(self.cube)
        self.assertIn((12.0, "Footstep"), keyed)

    def test_set_key_unknown_event_returns_false(self):
        ok = EventTriggers.set_key(self.cube, "Bogus", time=5)
        self.assertFalse(ok)

    def test_set_key_auto_clear_inserts_zero_at_time_minus_1(self):
        """With auto_clear, set_key at frame t>1 should also key the
        "None" (index 0) at t-1 so the trigger reads as a single pulse."""
        EventTriggers.set_key(self.cube, "Footstep", time=10, auto_clear=True)
        keyed = EventTriggers.iter_keyed_events(self.cube)
        # The "None" key at t=9 is filtered by iter_keyed_events (only
        # non-None entries returned) — verify by reading the raw curve.
        times = cmds.keyframe(
            self.cube, attribute="event_trigger", query=True, timeChange=True
        ) or []
        self.assertIn(9.0, times)
        self.assertIn(10.0, times)

    def test_set_key_at_frame_1_skips_auto_clear(self):
        """At frame 1 (or earlier) there's no room for the auto-clear key —
        it must not write a key at frame 0 that would conflict with rest pose."""
        EventTriggers.set_key(self.cube, "Footstep", time=1, auto_clear=True)
        times = cmds.keyframe(
            self.cube, attribute="event_trigger", query=True, timeChange=True
        ) or []
        self.assertNotIn(0.0, times)

    def test_clear_key_removes_keyframe(self):
        EventTriggers.set_key(self.cube, "Jump", time=20, auto_clear=False)
        EventTriggers.clear_key(self.cube, time=20)
        keyed = EventTriggers.iter_keyed_events(self.cube)
        self.assertFalse(any(t == 20.0 for t, _ in keyed))

    def test_iter_keyed_events_filters_none_index(self):
        """iter_keyed_events must skip index-0 ('None') entries."""
        # set_key with auto_clear writes a 'None' key at t-1 — verify it's filtered out.
        EventTriggers.set_key(self.cube, "Footstep", time=15, auto_clear=True)
        keyed = EventTriggers.iter_keyed_events(self.cube)
        for t, label in keyed:
            self.assertNotEqual(label, "None")
            self.assertNotEqual(label, "")

    def test_iter_keyed_events_returns_sorted(self):
        EventTriggers.set_key(self.cube, "Land", time=30, auto_clear=False)
        EventTriggers.set_key(self.cube, "Footstep", time=10, auto_clear=False)
        EventTriggers.set_key(self.cube, "Jump", time=20, auto_clear=False)

        keyed = EventTriggers.iter_keyed_events(self.cube)
        times = [t for t, _ in keyed]
        self.assertEqual(times, sorted(times))


class TestBakeManifest(MayaTkTestCase):
    """bake_manifest serializes keyed events to the "frame:event,..." string."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="evt_bake")[0]
        EventTriggers.create([self.cube], events=["Footstep", "Jump"])

    def test_bake_writes_manifest_string(self):
        EventTriggers.set_key(self.cube, "Footstep", time=10, auto_clear=False)
        EventTriggers.set_key(self.cube, "Jump", time=20, auto_clear=False)

        result = EventTriggers.bake_manifest([self.cube])

        manifest = cmds.getAttr(f"{self.cube}.event_manifest")
        self.assertIn("10:Footstep", manifest)
        self.assertIn("20:Jump", manifest)
        self.assertEqual(result[self.cube], manifest)

    def test_bake_empty_when_no_keys(self):
        """No keyed events → empty manifest string."""
        EventTriggers.bake_manifest([self.cube])
        manifest = cmds.getAttr(f"{self.cube}.event_manifest")
        self.assertEqual(manifest, "")

    def test_bake_uses_comma_separator(self):
        EventTriggers.set_key(self.cube, "Footstep", time=5, auto_clear=False)
        EventTriggers.set_key(self.cube, "Jump", time=15, auto_clear=False)

        EventTriggers.bake_manifest([self.cube])
        manifest = cmds.getAttr(f"{self.cube}.event_manifest")
        # Two entries → one comma.
        self.assertEqual(manifest.count(","), 1)


class TestRemove(MayaTkTestCase):
    """remove() cleans up trigger attrs and keyframes."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="evt_remove")[0]

    def test_remove_deletes_trigger_attribute(self):
        EventTriggers.create([self.cube], events=["X"])
        self.assertTrue(
            cmds.attributeQuery("event_trigger", node=self.cube, exists=True)
        )
        EventTriggers.remove([self.cube])
        self.assertFalse(
            cmds.attributeQuery("event_trigger", node=self.cube, exists=True)
        )

    def test_remove_specific_category_leaves_others_intact(self):
        EventTriggers.create([self.cube], events=["A"], category="audio")
        EventTriggers.create([self.cube], events=["B"], category="vfx")

        EventTriggers.remove([self.cube], category="audio")

        self.assertFalse(
            cmds.attributeQuery("audio_trigger", node=self.cube, exists=True)
        )
        self.assertTrue(
            cmds.attributeQuery("vfx_trigger", node=self.cube, exists=True)
        )

    def test_remove_wildcard_clears_all_categories(self):
        EventTriggers.create([self.cube], events=["A"], category="audio")
        EventTriggers.create([self.cube], events=["B"], category="vfx")
        EventTriggers.remove([self.cube], category="*")

        self.assertFalse(
            cmds.attributeQuery("audio_trigger", node=self.cube, exists=True)
        )
        self.assertFalse(
            cmds.attributeQuery("vfx_trigger", node=self.cube, exists=True)
        )


if __name__ == "__main__":
    unittest.main()
