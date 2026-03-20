# !/usr/bin/python
# coding=utf-8
"""Test suite for the Audio Events system.

Covers:
- EventTriggers  — attr naming, create/ensure/remove, keying, manifest baking,
                   protect-empty-transforms locator stamping
- AudioEvents    — set management, load_tracks, node stamping, remove
- AudioEventsSlots — _require_target (locator not group), _is_tool_created_carrier,
                     _get_selected_trigger_object (long-path fix + DAG walk),
                     _hydrate_from_target, _sync_from_selection (no recursion)

Run inside Maya (e.g. via mayatk/run_tests.py or a test runner that
bootstraps a Maya standalone session).
"""
import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError as e:
    raise RuntimeError(
        "These tests must run inside a Maya session (standalone or GUI)."
    ) from e

from base_test import MayaTkTestCase
from mayatk.node_utils.attributes.event_triggers import EventTriggers
from mayatk.node_utils.attributes.audio_events._audio_events import AudioEvents

import pythontk as ptk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_slots_instance():
    """Return an AudioEventsSlots instance with a fully-mocked UI.

    Bypasses Maya UI and switchboard so the pure logic methods can be
    exercised in unit tests without opening any windows.
    """
    from mayatk.node_utils.attributes.audio_events.audio_events_slots import (
        AudioEventsSlots,
    )

    # Build minimal mocks for switchboard + UI widgets
    footer = MagicMock()
    footer.setText = MagicMock()

    cmb000 = MagicMock()
    cmb000.count.return_value = 0
    cmb000.blockSignals = MagicMock()
    cmb000.setCurrentIndex = MagicMock()
    cmb000.repaint = MagicMock()
    cmb000.clear = MagicMock()
    cmb000.addItems = MagicMock()

    ui = MagicMock()
    ui.footer = footer
    ui.cmb000 = cmb000
    ui.b002 = MagicMock()
    ui.b003 = MagicMock()
    ui.b004 = MagicMock()

    loaded_ui = MagicMock()
    loaded_ui.audio_events = ui

    sb = MagicMock()
    sb.loaded_ui = loaded_ui

    # Patch evalDeferred so __init__ doesn't try to schedule Maya callbacks
    with patch("maya.cmds.evalDeferred"):
        slots = AudioEventsSlots.__new__(AudioEventsSlots)
        slots.sb = sb
        slots.ui = ui
        slots._audio_files = {}
        slots._current_target = None
        slots._selection_sync_job_id = None
        slots._time_changed_job_id = None
        slots._scene_opened_job_id = None
        slots._new_scene_job_id = None
        slots._attr_callback_ids = []
        slots._syncing_combo = False
        slots._last_enum_idx = None
        slots._trigger_attr_path = None
        slots._sync_fingerprints = {}
        slots._deferred_sync_pending = False

    return slots


# ===========================================================================
# EventTriggers Tests
# ===========================================================================


class TestEventTriggersAttrNames(MayaTkTestCase):
    """attr_names() returns correctly prefixed pairs."""

    def test_default_category(self):
        trigger, manifest = EventTriggers.attr_names()
        self.assertEqual(trigger, "event_trigger")
        self.assertEqual(manifest, "event_manifest")

    def test_audio_category(self):
        trigger, manifest = EventTriggers.attr_names("audio")
        self.assertEqual(trigger, "audio_trigger")
        self.assertEqual(manifest, "audio_manifest")

    def test_custom_category(self):
        trigger, manifest = EventTriggers.attr_names("vfx")
        self.assertEqual(trigger, "vfx_trigger")
        self.assertEqual(manifest, "vfx_manifest")


class TestEventTriggersCreate(MayaTkTestCase):
    """create() stamps correct enum attributes on objects."""

    def setUp(self):
        super().setUp()
        loc_name = cmds.spaceLocator(name="carrier")[0]
        self.loc = pm.PyNode(loc_name)

    def test_trigger_attr_created(self):
        EventTriggers.create([self.loc], events=["A", "B"], category="audio")
        self.assertTrue(self.loc.hasAttr("audio_trigger"))

    def test_manifest_attr_created(self):
        EventTriggers.create([self.loc], events=["A"], category="audio")
        self.assertTrue(self.loc.hasAttr("audio_manifest"))

    def test_event_list_includes_none(self):
        EventTriggers.create([self.loc], events=["Footstep", "Jump"], category="audio")
        events = EventTriggers.get_events(self.loc, category="audio")
        self.assertEqual(events[0], "None")
        self.assertIn("Footstep", events)
        self.assertIn("Jump", events)

    def test_create_is_destructive_removes_prior_keys(self):
        """create() on an object that already has keys clears them."""
        EventTriggers.create([self.loc], events=["A"], category="audio")
        EventTriggers.set_key(self.loc, "A", time=10, category="audio")
        # Re-create with different events
        EventTriggers.create([self.loc], events=["B"], category="audio")
        keyed = EventTriggers.iter_keyed_events(self.loc, category="audio")
        self.assertEqual(keyed, [], "Prior keyframes should be destroyed by create()")

    def test_protects_empty_transform_with_locator(self):
        """create() adds a hidden locator shape to an empty transform."""
        grp_name = cmds.group(empty=True, name="emptyGrp")
        grp = pm.PyNode(grp_name)
        EventTriggers.create([grp], events=["A"], category="audio")
        shapes = cmds.listRelatives(str(grp), shapes=True, fullPath=True) or []
        self.assertGreater(
            len(shapes), 0, "Empty transform should gain a locator shape"
        )
        for shp in shapes:
            has_attr = cmds.attributeQuery(
                EventTriggers._LOCATOR_ATTR, node=shp, exists=True
            )
            self.assertTrue(has_attr, "Locator shape should carry the stamp attr")


class TestEventTriggersEnsure(MayaTkTestCase):
    """ensure() is non-destructive — preserves existing keyframes."""

    def setUp(self):
        super().setUp()
        loc_name = cmds.spaceLocator(name="carrier")[0]
        self.loc = pm.PyNode(loc_name)

    def test_ensure_creates_on_fresh_object(self):
        EventTriggers.ensure([self.loc], events=["A"], category="audio")
        self.assertTrue(self.loc.hasAttr("audio_trigger"))

    def test_ensure_appends_events_without_clearing_keys(self):
        EventTriggers.create([self.loc], events=["A"], category="audio")
        EventTriggers.set_key(self.loc, "A", time=10, category="audio")
        EventTriggers.ensure([self.loc], events=["B"], category="audio")
        # Original key must still be there
        keyed = EventTriggers.iter_keyed_events(self.loc, category="audio")
        times_and_labels = [(int(t), lbl) for t, lbl in keyed]
        self.assertIn((10, "A"), times_and_labels)
        # New enum field must appear
        events = EventTriggers.get_events(self.loc, category="audio")
        self.assertIn("B", events)

    def test_ensure_is_idempotent(self):
        EventTriggers.ensure([self.loc], events=["A"], category="audio")
        EventTriggers.ensure([self.loc], events=["A"], category="audio")
        events = EventTriggers.get_events(self.loc, category="audio")
        self.assertEqual(events.count("A"), 1, "Duplicate event should not be added")


class TestEventTriggersKeying(MayaTkTestCase):
    """set_key / iter_keyed_events / bake_manifest round-trip."""

    def setUp(self):
        super().setUp()
        loc_name = cmds.spaceLocator(name="carrier")[0]
        self.loc = pm.PyNode(loc_name)
        EventTriggers.create([self.loc], events=["Footstep", "Jump"], category="audio")

    def test_set_key_returns_true_for_valid_event(self):
        ok = EventTriggers.set_key(self.loc, "Footstep", time=10, category="audio")
        self.assertTrue(ok)

    def test_set_key_returns_false_for_unknown_event(self):
        ok = EventTriggers.set_key(self.loc, "NonExistent", time=10, category="audio")
        self.assertFalse(ok)

    def test_iter_keyed_events_returns_non_none_only(self):
        EventTriggers.set_key(self.loc, "Footstep", time=10, category="audio")
        EventTriggers.set_key(self.loc, "Jump", time=24, category="audio")
        keyed = EventTriggers.iter_keyed_events(self.loc, category="audio")
        labels = [lbl for _, lbl in keyed]
        self.assertIn("Footstep", labels)
        self.assertIn("Jump", labels)
        self.assertNotIn("None", labels)

    def test_bake_manifest_format(self):
        EventTriggers.set_key(self.loc, "Footstep", time=12, category="audio")
        EventTriggers.set_key(self.loc, "Jump", time=24, category="audio")
        result = EventTriggers.bake_manifest([self.loc], category="audio")
        manifest = result.get(self.loc.name(), "")
        self.assertIn("12:Footstep", manifest)
        self.assertIn("24:Jump", manifest)

    def test_bake_manifest_empty_when_no_keys(self):
        result = EventTriggers.bake_manifest([self.loc], category="audio")
        manifest = result.get(self.loc.name(), "MISSING")
        self.assertEqual(manifest, "")


class TestEventTriggersRemove(MayaTkTestCase):
    """remove() deletes trigger/manifest attrs and anim curves."""

    def setUp(self):
        super().setUp()
        loc_name = cmds.spaceLocator(name="carrier")[0]
        self.loc = pm.PyNode(loc_name)
        EventTriggers.create([self.loc], events=["A"], category="audio")
        EventTriggers.set_key(self.loc, "A", time=10, category="audio")

    def test_remove_deletes_trigger_attr(self):
        EventTriggers.remove([self.loc], category="audio")
        self.assertFalse(self.loc.hasAttr("audio_trigger"))

    def test_remove_deletes_manifest_attr(self):
        EventTriggers.remove([self.loc], category="audio")
        self.assertFalse(self.loc.hasAttr("audio_manifest"))

    def test_remove_star_clears_all_categories(self):
        EventTriggers.create([self.loc], events=["X"], category="vfx")
        EventTriggers.remove([self.loc], category="*")
        self.assertFalse(self.loc.hasAttr("audio_trigger"))
        self.assertFalse(self.loc.hasAttr("vfx_trigger"))


class TestProtectEmptyTransforms(MayaTkTestCase):
    """_protect_empty_transforms adds a hidden stamped locator shape."""

    def test_locator_shape_is_hidden(self):
        grp_name = cmds.group(empty=True, name="grp")
        grp = pm.PyNode(grp_name)
        EventTriggers._protect_empty_transforms([grp])
        shapes = cmds.listRelatives(str(grp), shapes=True, fullPath=True) or []
        self.assertTrue(shapes, "Shape should be added")
        shp = shapes[0]
        vis = cmds.getAttr(f"{shp}.visibility")
        self.assertEqual(vis, 0)

    def test_locator_shape_has_zero_scale(self):
        grp_name = cmds.group(empty=True, name="grp")
        grp = pm.PyNode(grp_name)
        EventTriggers._protect_empty_transforms([grp])
        shapes = cmds.listRelatives(str(grp), shapes=True, fullPath=True) or []
        shp = shapes[0]
        for axis in ("X", "Y", "Z"):
            self.assertEqual(cmds.getAttr(f"{shp}.localScale{axis}"), 0)

    def test_locator_shape_stamp_attr_present(self):
        grp_name = cmds.group(empty=True, name="grp")
        grp = pm.PyNode(grp_name)
        EventTriggers._protect_empty_transforms([grp])
        shapes = cmds.listRelatives(str(grp), shapes=True, fullPath=True) or []
        shp = shapes[0]
        has_attr = cmds.attributeQuery(
            EventTriggers._LOCATOR_ATTR, node=shp, exists=True
        )
        self.assertTrue(has_attr)

    def test_skips_objects_that_already_have_shapes(self):
        loc_name = cmds.spaceLocator(name="hasShape")[0]
        loc = pm.PyNode(loc_name)
        shapes_before = cmds.listRelatives(str(loc), shapes=True, fullPath=True) or []
        EventTriggers._protect_empty_transforms([loc])
        shapes_after = cmds.listRelatives(str(loc), shapes=True, fullPath=True) or []
        self.assertEqual(len(shapes_before), len(shapes_after))


# ===========================================================================
# AudioEvents Tests
# ===========================================================================


class TestAudioEventsSet(MayaTkTestCase):
    """_get_or_create_set creates and names the objectSet correctly."""

    def test_set_is_created_with_correct_name(self):
        audio_set = AudioEvents._get_or_create_set("audio")
        self.assertIsNotNone(audio_set)
        self.assertIn("audio", audio_set.name())

    def test_set_is_reused_on_second_call(self):
        s1 = AudioEvents._get_or_create_set("audio")
        s2 = AudioEvents._get_or_create_set("audio")
        self.assertEqual(s1.name(), s2.name())


class TestAudioEventsRemove(MayaTkTestCase):
    """remove() deletes audio nodes and the objectSet."""

    def test_remove_returns_zero_when_no_set(self):
        count = AudioEvents.remove(category="audio")
        self.assertEqual(count, 0)

    def test_remove_deletes_set_and_members(self):
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        # Create a dummy audio node and add it to the set
        dummy = cmds.createNode("audio", name="dummy_test_node", skipSelect=True)
        cmds.sets(dummy, addElement=audio_set.name())
        count = AudioEvents.remove(category="audio")
        self.assertGreater(count, 0)
        self.assertFalse(pm.objExists("dummy_test_node"))


class TestAudioEventsLoadTracks(MayaTkTestCase):
    """load_tracks() creates preview audio nodes from file paths.

    Uses WAV files from the test fixtures directory if available,
    otherwise skips gracefully.
    """

    FIXTURE_DIR = os.path.join(scripts_dir, "mayatk", "test", "fixtures", "audio")

    def _wav_files(self):
        if not os.path.isdir(self.FIXTURE_DIR):
            return []
        return [
            os.path.join(self.FIXTURE_DIR, f)
            for f in os.listdir(self.FIXTURE_DIR)
            if f.lower().endswith(".wav")
        ]

    def test_load_tracks_with_no_files_returns_empty(self):
        nodes = AudioEvents.load_tracks([], category="audio")
        self.assertEqual(nodes, [])

    def test_load_tracks_stamps_preview_type(self):
        wavs = self._wav_files()
        if not wavs:
            self.skipTest("No .wav fixtures in test/fixtures/audio/")
        nodes = AudioEvents.load_tracks(wavs[:1], category="audio")
        self.assertTrue(nodes)
        node = nodes[0]
        has_attr = cmds.attributeQuery(
            AudioEvents.NODE_TYPE_ATTR, node=node, exists=True
        )
        self.assertTrue(has_attr)
        ntype = cmds.getAttr(f"{node}.{AudioEvents.NODE_TYPE_ATTR}")
        self.assertEqual(ntype, "preview")


# ===========================================================================
# AudioEventsSlots Tests
# ===========================================================================


class TestRequireTarget(MayaTkTestCase):
    """_require_target() returns/creates the correct carrier object."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    # -- 1. Selected object with trigger already set -----------------------

    def test_selection_with_trigger_returns_it(self):
        loc_name = cmds.spaceLocator(name="carrier")[0]
        loc = pm.PyNode(loc_name)
        EventTriggers.create([loc], events=["A"], category="audio")
        pm.select(loc, replace=True)
        result = self.slots._require_target()
        self.assertEqual(str(result), str(loc))

    # -- 2. Selected object without trigger --------------------------------

    def test_selection_without_trigger_returns_selected(self):
        cube_name = cmds.polyCube(name="myCube")[0]
        cube = pm.PyNode(cube_name)
        pm.select(cube, replace=True)
        result = self.slots._require_target()
        self.assertEqual(str(result), str(cube))

    # -- 3. Nothing selected — uses cache ---------------------------------

    def test_no_selection_returns_cached_target(self):
        loc_name = cmds.spaceLocator(name="cached")[0]
        self.slots._current_target = pm.PyNode(loc_name)
        pm.select(clear=True)
        result = self.slots._require_target()
        self.assertEqual(str(result), loc_name)

    # -- 4. Nothing selected, no cache — creates LOCATOR, not a group -----

    def test_no_selection_no_cache_creates_locator(self):
        """
        Bug: _require_target used pm.group(empty=True) which created a plain
        transform and also created the node before files were chosen.
        Fixed: now uses cmds.spaceLocator() so the carrier has a shape child.
        """
        pm.select(clear=True)
        result = self.slots._require_target()
        self.assertIsNotNone(result)
        # Must have at least one shape (locator, not empty group)
        shapes = cmds.listRelatives(str(result), shapes=True) or []
        self.assertTrue(
            shapes, "Auto-created carrier must be a locator, not an empty group"
        )

    def test_auto_created_carrier_shape_is_hidden(self):
        """Auto-created locator's shape must have visibility=0."""
        pm.select(clear=True)
        result = self.slots._require_target()
        shapes = cmds.listRelatives(str(result), shapes=True, fullPath=True) or []
        if not shapes:
            self.skipTest("No shape found on auto-created carrier")
        vis = cmds.getAttr(f"{shapes[0]}.visibility")
        self.assertEqual(vis, 0)

    def test_auto_created_carrier_is_named_audio_events(self):
        pm.select(clear=True)
        result = self.slots._require_target()
        self.assertIn("audio_events", str(result))


class TestIsToolCreatedCarrier(MayaTkTestCase):
    """_is_tool_created_carrier() correctly classifies objects."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def test_stamped_locator_only_is_tool_created(self):
        grp_name = cmds.group(empty=True, name="toolGrp")
        grp = pm.PyNode(grp_name)
        EventTriggers._protect_empty_transforms([grp])
        self.assertTrue(self.slots._is_tool_created_carrier(grp))

    def test_mesh_object_is_not_tool_created(self):
        cube_name = cmds.polyCube(name="meshCube")[0]
        cube = pm.PyNode(cube_name)
        self.assertFalse(self.slots._is_tool_created_carrier(cube))

    def test_locator_from_spacelocator_cmd_is_not_tool_created(self):
        """A regular spaceLocator has no stamp attr — not tool-created."""
        loc_name = cmds.spaceLocator(name="userLoc")[0]
        loc = pm.PyNode(loc_name)
        self.assertFalse(self.slots._is_tool_created_carrier(loc))

    def test_shapeless_object_is_not_tool_created(self):
        """After EventTriggers.remove() the locator shape may be gone."""
        grp_name = cmds.group(empty=True, name="bare")
        grp = pm.PyNode(grp_name)
        self.assertFalse(self.slots._is_tool_created_carrier(grp))


class TestGetSelectedTriggerObject(MayaTkTestCase):
    """_get_selected_trigger_object() finds trigger attr reliably."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def test_direct_selection_match(self):
        loc_name = cmds.spaceLocator(name="carrier")[0]
        loc = pm.PyNode(loc_name)
        EventTriggers.create([loc], events=["A"], category="audio")
        pm.select(loc, replace=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNotNone(result)
        self.assertEqual(str(result), str(loc))

    def test_nothing_selected_returns_none(self):
        pm.select(clear=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNone(result)

    def test_selected_without_trigger_returns_none(self):
        cube_name = cmds.polyCube(name="cube")[0]
        cube = pm.PyNode(cube_name)
        pm.select(cube, replace=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNone(result)

    def test_long_path_node_still_matched(self):
        """
        Bug: cmds.attributeQuery with long-path names (|nodeName) can return
        False even when the attribute exists on some Maya versions.
        Fixed: uses short_name = long_name.rsplit('|', 1)[-1]
        """
        parent_name = cmds.group(empty=True, name="parentGrp")
        parent = pm.PyNode(parent_name)
        child_name = cmds.spaceLocator(name="childCarrier")[0]
        child = pm.PyNode(child_name)
        pm.parent(child, parent)
        EventTriggers.create([child], events=["A"], category="audio")
        pm.select(child, replace=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNotNone(
            result, "Should find trigger on nested object via short name"
        )

    def test_shape_selection_walks_to_parent_transform(self):
        """Selecting a locator *shape* should resolve to the parent transform."""
        loc_name = cmds.spaceLocator(name="carrier")[0]
        loc = pm.PyNode(loc_name)
        EventTriggers.create([loc], events=["A"], category="audio")
        shape = pm.listRelatives(loc, shapes=True)[0]
        pm.select(shape, replace=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNotNone(result)
        self.assertEqual(str(result), str(loc))

    def test_nested_trigger_walks_up_hierarchy(self):
        """Trigger on a grandparent is found by walking the full DAG upward."""
        grandparent_name = cmds.group(empty=True, name="gp")
        grandparent = pm.PyNode(grandparent_name)
        parent_name = cmds.group(empty=True, name="par")
        parent = pm.PyNode(parent_name)
        child_name = cmds.spaceLocator(name="child")[0]
        child = pm.PyNode(child_name)
        pm.parent(parent, grandparent)
        pm.parent(child, parent)
        EventTriggers.create([grandparent], events=["A"], category="audio")
        pm.select(child, replace=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNotNone(result)
        self.assertEqual(str(result), str(grandparent))


class TestHydrateFromTarget(MayaTkTestCase):
    """_hydrate_from_target() populates combo and footer correctly."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        loc_name = cmds.spaceLocator(name="carrier")[0]
        self.loc = pm.PyNode(loc_name)

    def test_no_events_sets_no_events_footer(self):
        EventTriggers.create([self.loc], events=[], category="audio")
        self.slots._hydrate_from_target(self.loc)
        call_args = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("no events", call_args)

    def test_with_events_sets_track_count_in_footer(self):
        EventTriggers.create([self.loc], events=["A", "B"], category="audio")
        self.slots._hydrate_from_target(self.loc)
        call_args = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("2 track(s)", call_args)

    def test_switching_targets_clears_audio_files(self):
        """
        When _hydrate_from_target is called with a new obj != old_target,
        _audio_files must be cleared to avoid stale stems from the prior carrier.
        """
        old_name = cmds.spaceLocator(name="old_carrier")[0]
        old = pm.PyNode(old_name)
        self.slots._current_target = old
        self.slots._audio_files = {"stem": "/old/path.wav"}
        EventTriggers.create([self.loc], events=["A"], category="audio")
        self.slots._hydrate_from_target(self.loc, old_target=old)
        self.assertEqual(self.slots._audio_files, {})

    def test_same_target_preserves_audio_files(self):
        """Re-hydrating the same target must not wipe the existing file map."""
        EventTriggers.create([self.loc], events=["A"], category="audio")
        self.slots._current_target = self.loc
        self.slots._audio_files = {"a": "/path/a.wav"}
        self.slots._hydrate_from_target(self.loc, old_target=self.loc)
        self.assertIn("a", self.slots._audio_files)


class TestSyncFromSelection(MayaTkTestCase):
    """_sync_from_selection() routes to _hydrate_from_target without recursion."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def test_carrier_selected_calls_hydrate(self):
        loc_name = cmds.spaceLocator(name="carrier")[0]
        loc = pm.PyNode(loc_name)
        EventTriggers.create([loc], events=["A"], category="audio")
        pm.select(loc, replace=True)
        with patch.object(self.slots, "_hydrate_from_target") as mock_hydrate:
            self.slots._sync_from_selection()
            mock_hydrate.assert_called_once()
            called_obj = mock_hydrate.call_args[0][0]
            self.assertEqual(str(called_obj), str(loc))

    def test_non_carrier_selected_with_valid_cache_keeps_footer(self):
        """
        Bug: Selecting an unrelated object cleared the combo and broke
        session reconnection.
        Fixed: keeps cached target when selection has no trigger.
        """
        carrier_name = cmds.spaceLocator(name="carrier")[0]
        carrier = pm.PyNode(carrier_name)
        EventTriggers.create([carrier], events=["A"], category="audio")
        self.slots._current_target = carrier
        cube_name = cmds.polyCube(name="cube")[0]
        cube = pm.PyNode(cube_name)
        pm.select(cube, replace=True)
        self.slots._sync_from_selection()
        # Footer should mention "keeping" the old target, not clear it
        footer_text = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("keeping", footer_text)

    def test_nothing_selected_uses_cached_target(self):
        carrier_name = cmds.spaceLocator(name="carrier")[0]
        carrier = pm.PyNode(carrier_name)
        EventTriggers.create([carrier], events=["A"], category="audio")
        self.slots._current_target = carrier
        pm.select(clear=True)
        self.slots._sync_from_selection()
        # Cached target should still be active after sync
        self.assertIsNotNone(self.slots._current_target)
        self.assertEqual(str(self.slots._current_target), carrier_name)

    def test_nothing_selected_scans_scene_for_carrier(self):
        """
        Bug: After scene reload with no selection, the tool showed 'no trigger'.
        Fixed: scans scene with cmds.ls('*.audio_trigger') when nothing selected.
        """
        carrier_name = cmds.spaceLocator(name="scanCarrier")[0]
        carrier = pm.PyNode(carrier_name)
        EventTriggers.create([carrier], events=["A"], category="audio")
        self.slots._current_target = None
        pm.select(clear=True)
        self.slots._sync_from_selection()
        # Scene scan should have found the carrier and hydrated it
        self.assertIsNotNone(self.slots._current_target)
        self.assertEqual(str(self.slots._current_target), carrier_name)

    def test_no_selection_no_carrier_clears_state(self):
        pm.select(clear=True)
        self.slots._current_target = None
        self.slots._audio_files = {"old": "/path"}
        self.slots._sync_from_selection()
        self.assertEqual(self.slots._current_target, None)
        self.assertEqual(self.slots._audio_files, {})

    def test_sync_from_selection_never_recurses(self):
        """
        Bug: The 'nothing selected, scene scan found carrier' branch called
        self._sync_from_selection() recursively — risk of infinite loop.
        Fixed: calls _hydrate_from_target() directly instead.
        """
        carrier_name = cmds.spaceLocator(name="carrier")[0]
        carrier = pm.PyNode(carrier_name)
        EventTriggers.create([carrier], events=["A"], category="audio")
        pm.select(clear=True)
        self.slots._current_target = None

        call_count = [0]
        original = self.slots._sync_from_selection.__func__

        def counting_sync(self_inner):
            call_count[0] += 1
            if call_count[0] > 3:
                raise RecursionError("_sync_from_selection called recursively!")
            original(self_inner)

        with patch.object(
            self.slots,
            "_sync_from_selection",
            lambda: counting_sync(self.slots),
        ):
            with patch.object(self.slots, "_hydrate_from_target"):
                # Direct invocation bypasses our patch — call the real one
                pass

        # Simpler check: call the real method and assert it completes
        # without calling itself again (no RecursionError)
        with patch.object(self.slots, "_hydrate_from_target"):
            try:
                self.slots._sync_from_selection()
            except RecursionError:
                self.fail("_sync_from_selection recursed into itself")


# ===========================================================================
# Browse-before-create Guard
# ===========================================================================


class TestBrowseCreatesNodeOnlyAfterFileSelection(MayaTkTestCase):
    """_browse_audio_files() must NOT create a Maya node on dialog cancel.

    Bug: _require_target() was called before the file dialog opened, so
    cancelling the dialog still created an 'audio_events' node.
    Fixed: dialog is shown first; _require_target only called if paths chosen.
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def test_cancel_dialog_leaves_scene_clean(self):
        pm.select(clear=True)
        node_count_before = len(cmds.ls(type="transform"))
        # Simulate user cancelling the dialog (getOpenFileNames returns [])
        with patch(
            "qtpy.QtWidgets.QFileDialog.getOpenFileNames", return_value=([], "")
        ):
            self.slots._browse_audio_files()
        node_count_after = len(cmds.ls(type="transform"))
        self.assertEqual(
            node_count_before,
            node_count_after,
            "Cancelling the browse dialog should create no Maya nodes",
        )


# ===========================================================================
# Scene-Reopen Detection Regression
# ===========================================================================


class TestSceneReopenDetection(MayaTkTestCase):
    """_sync_from_selection finds trigger objects after scene reopen.

    Bug: ScriptJobs were created with killWithScene=True but never
    recreated after File > Open — the scene-wide scan also used an
    unreliable ``cmds.ls("*.attr")`` pattern, so the UI showed
    "No audio trigger object in scene" even with a valid trigger locator.
    Fixed: 2026-02-21 — added persistent SceneOpened/NewSceneOpened
    scriptJobs, multi-strategy ``_find_trigger_in_scene``, and
    ``_on_scene_opened`` state reset.
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def test_find_trigger_in_scene_by_attribute(self):
        """_find_trigger_in_scene locates a trigger object via attributeQuery."""
        loc = cmds.spaceLocator(name="audio_events")[0]
        EventTriggers.ensure(
            objects=[pm.PyNode(loc)],
            events=["Footstep"],
            category="audio",
        )
        trigger_attr, _ = EventTriggers.attr_names("audio")

        found = self.slots._find_trigger_in_scene(trigger_attr)
        self.assertIsNotNone(found, "Should find trigger object in scene")
        self.assertEqual(str(found), loc)

    def test_find_trigger_in_scene_by_file_map(self):
        """_find_trigger_in_scene locates a trigger object via FILE_MAP_ATTR."""
        loc = cmds.spaceLocator(name="audio_events")[0]
        # Only add the file_map attr (no trigger attr) to test strategy 2
        cmds.addAttr(loc, ln="audio_file_map", dt="string")
        cmds.setAttr(f"{loc}.audio_file_map", '{"test": "test.wav"}', type="string")

        trigger_attr, _ = EventTriggers.attr_names("audio")
        found = self.slots._find_trigger_in_scene(trigger_attr)
        self.assertIsNotNone(found, "Should find object via file_map marker")
        self.assertEqual(str(found), loc)

    def test_sync_from_selection_detects_trigger_with_nothing_selected(self):
        """_sync_from_selection finds trigger when nothing is selected (scene scan).

        This is the exact scenario that fails after scene reopen: the user
        hasn't selected anything yet, but a trigger object exists in the scene.
        """
        loc = cmds.spaceLocator(name="audio_events")[0]
        EventTriggers.ensure(
            objects=[pm.PyNode(loc)],
            events=["Footstep"],
            category="audio",
        )
        pm.select(clear=True)

        self.slots._sync_from_selection()

        self.assertIsNotNone(
            self.slots._current_target,
            "Should auto-detect trigger object via scene scan",
        )
        # Footer should NOT show the "no trigger" message
        footer_text = self.slots.ui.footer.setText.call_args[0][0]
        self.assertNotIn(
            "No audio trigger object in scene",
            footer_text,
            "Should not report 'no trigger' when one exists",
        )

    def test_on_scene_opened_resets_stale_state(self):
        """_on_scene_opened clears cached state so stale PyNodes don't persist.

        After File > Open, the old _current_target PyNode is invalid.
        _on_scene_opened must clear it before _ensure_sync_job runs.
        """
        # Simulate pre-scene-open state with cached target
        loc = cmds.spaceLocator(name="old_target")[0]
        self.slots._current_target = pm.PyNode(loc)
        self.slots._audio_files = {"test": "test.wav"}
        self.slots._trigger_attr_path = f"{loc}.audio_trigger"
        self.slots._last_enum_idx = 2
        self.slots._selection_sync_job_id = 99999  # Fake stale ID

        # Patch _ensure_sync_job to prevent actual scriptJob creation
        with patch.object(self.slots, "_ensure_sync_job"):
            self.slots._on_scene_opened()

        self.assertIsNone(self.slots._current_target)
        self.assertIsNone(self.slots._trigger_attr_path)
        self.assertIsNone(self.slots._last_enum_idx)
        self.assertEqual(self.slots._audio_files, {})
        self.assertIsNone(self.slots._selection_sync_job_id)
        self.assertIsNone(self.slots._time_changed_job_id)

    def test_ensure_sync_job_creates_scene_opened_jobs(self):
        """_ensure_sync_job registers persistent SceneOpened/NewSceneOpened jobs.

        Bug: In standalone mayapy, cmds.scriptJob(event=["SceneOpened", ...])
        silently fails (no GUI event loop), so the IDs stayed None and the
        test failed.  Fixed by mocking cmds.scriptJob so the test verifies
        the method's branching logic rather than Maya's event availability.
        Fixed: 2026-03-03
        """
        self.slots._scene_opened_job_id = None
        self.slots._new_scene_job_id = None
        self.slots._selection_sync_job_id = None
        self.slots._time_changed_job_id = None

        _counter = [1000]

        def _fake_script_job(**kwargs):
            if "exists" in kwargs:
                return False
            if "kill" in kwargs:
                return None
            _counter[0] += 1
            return _counter[0]

        with (
            patch("maya.cmds.scriptJob", side_effect=_fake_script_job),
            patch("maya.cmds.evalDeferred"),
            patch.object(self.slots, "_connect_cb_signal"),
        ):
            self.slots._ensure_sync_job()

        # The persistent jobs should have been created (non-None IDs)
        self.assertIsNotNone(
            self.slots._scene_opened_job_id,
            "SceneOpened scriptJob should be created",
        )
        self.assertIsNotNone(
            self.slots._new_scene_job_id,
            "NewSceneOpened scriptJob should be created",
        )
        # Volatile jobs too
        self.assertIsNotNone(self.slots._selection_sync_job_id)
        self.assertIsNotNone(self.slots._time_changed_job_id)

        # All four IDs should be distinct
        ids = [
            self.slots._scene_opened_job_id,
            self.slots._new_scene_job_id,
            self.slots._selection_sync_job_id,
            self.slots._time_changed_job_id,
        ]
        self.assertEqual(len(set(ids)), 4, "Each scriptJob should get a unique ID")


# ===========================================================================
# Add / Overwrite Track Behavior
# ===========================================================================


class TestAddTrackOverwritesBehavior(MayaTkTestCase):
    """Adding a track with the same name must overwrite at the same enum index.

    The Add button (_browse_audio_files) merges selected files into the
    _audio_files dict keyed by lowercase stem.  Re-adding a file with
    the same stem must:

    1. Overwrite the path in _audio_files (same key).
    2. Preserve the enum index for that event (EventTriggers.ensure is
       additive — existing labels keep their indices).
    3. Not create a duplicate enum field.

    This suite verifies all three invariants without requiring audio
    fixture files on disk.
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        loc_name = cmds.spaceLocator(name="carrier")[0]
        self.loc = pm.PyNode(loc_name)
        self.slots._current_target = self.loc

    # -- 1. Dict-level overwrite ------------------------------------------

    def test_audio_files_dict_overwrites_path_on_same_stem(self):
        """Re-adding a stem replaces the path, not appends a duplicate."""
        self.slots._audio_files["footstep"] = "/old/footstep.wav"
        # Simulate the merge loop from _browse_audio_files
        new_path = "/new/footstep.wav"
        stem = "footstep"
        self.slots._audio_files[stem] = new_path.replace("\\", "/")
        self.assertEqual(self.slots._audio_files["footstep"], "/new/footstep.wav")
        self.assertEqual(
            len(self.slots._audio_files), 1, "Should be one entry, not two"
        )

    def test_audio_files_dict_adds_new_stems_alongside_existing(self):
        """New stems are added without removing existing ones."""
        self.slots._audio_files["footstep"] = "/audio/footstep.wav"
        self.slots._audio_files["jump"] = "/audio/jump.wav"
        self.assertEqual(len(self.slots._audio_files), 2)
        self.assertIn("footstep", self.slots._audio_files)
        self.assertIn("jump", self.slots._audio_files)

    # -- 2. Enum index preserved on re-add --------------------------------

    def test_ensure_preserves_enum_index_on_duplicate_event(self):
        """ensure() with an already-existing event name keeps its enum index.

        Bug scenario: User adds 'Footstep' (gets index 1), then re-adds
        'Footstep' with a different file.  The enum index must stay at 1.
        """
        EventTriggers.create([self.loc], events=["Footstep", "Jump"], category="audio")
        events_before = EventTriggers.get_events(self.loc, category="audio")
        idx_before = events_before.index("Footstep")

        # Second ensure with same + additional event
        EventTriggers.ensure(
            [self.loc], events=["Footstep", "Jump", "Land"], category="audio"
        )
        events_after = EventTriggers.get_events(self.loc, category="audio")
        idx_after = events_after.index("Footstep")

        self.assertEqual(
            idx_before,
            idx_after,
            f"Footstep index shifted from {idx_before} to {idx_after}",
        )

    def test_ensure_no_duplicate_enum_field(self):
        """ensure() with a duplicate event name does not create a second field."""
        EventTriggers.create([self.loc], events=["Footstep"], category="audio")
        EventTriggers.ensure([self.loc], events=["Footstep"], category="audio")
        events = EventTriggers.get_events(self.loc, category="audio")
        self.assertEqual(
            events.count("Footstep"),
            1,
            "Duplicate enum field created for 'Footstep'",
        )

    def test_ensure_preserves_keyframes_when_readding_event(self):
        """Keyframes on the original event survive an ensure() with the same name."""
        EventTriggers.create([self.loc], events=["Footstep", "Jump"], category="audio")
        EventTriggers.set_key(self.loc, "Footstep", time=12, category="audio")
        EventTriggers.set_key(self.loc, "Jump", time=24, category="audio")

        # Re-ensure with the same events (simulates Add with same file names)
        EventTriggers.ensure([self.loc], events=["Footstep", "Jump"], category="audio")

        keyed = EventTriggers.iter_keyed_events(self.loc, category="audio")
        times_and_labels = [(int(t), lbl) for t, lbl in keyed]
        self.assertIn(
            (12, "Footstep"), times_and_labels, "Footstep key at frame 12 lost"
        )
        self.assertIn((24, "Jump"), times_and_labels, "Jump key at frame 24 lost")

    # -- 3. All enum indices stable when new events added -----------------

    def test_existing_indices_stable_after_adding_new_event(self):
        """Adding a new event via ensure() must not shift existing indices.

        Enum indices for 'Footstep' and 'Jump' must remain unchanged
        when 'Land' is appended.
        """
        EventTriggers.create([self.loc], events=["Footstep", "Jump"], category="audio")
        events_before = EventTriggers.get_events(self.loc, category="audio")
        # Record ALL indices
        indices_before = {e: i for i, e in enumerate(events_before)}

        EventTriggers.ensure(
            [self.loc], events=["Footstep", "Jump", "Land"], category="audio"
        )
        events_after = EventTriggers.get_events(self.loc, category="audio")
        indices_after = {e: i for i, e in enumerate(events_after)}

        for event in ["None", "Footstep", "Jump"]:
            self.assertEqual(
                indices_before[event],
                indices_after[event],
                f"Index of '{event}' shifted from {indices_before[event]} "
                f"to {indices_after[event]} after adding 'Land'",
            )
        self.assertIn("Land", events_after, "New event 'Land' not added")

    # -- 4. End-to-end overwrite simulation -------------------------------

    def test_end_to_end_readd_same_track_overwrites_path_keeps_index(self):
        """Simulate the full Add flow: browse → merge dict → ensure enum.

        1. Add 'Footstep' and 'Jump' files.
        2. Key 'Footstep' at frame 12.
        3. Re-add 'Footstep' with a different file path.
        4. Verify: path updated, enum index unchanged, keyframe intact.
        """
        # Step 1 — initial add
        self.slots._audio_files["footstep"] = "/audio/v1/Footstep.wav"
        self.slots._audio_files["jump"] = "/audio/v1/Jump.wav"
        EventTriggers.ensure(
            [self.loc],
            events=["Footstep", "Jump"],
            category="audio",
        )

        events_v1 = EventTriggers.get_events(self.loc, category="audio")
        idx_footstep_v1 = events_v1.index("Footstep")
        idx_jump_v1 = events_v1.index("Jump")

        # Step 2 — key events
        EventTriggers.set_key(self.loc, "Footstep", time=12, category="audio")

        # Step 3 — re-add Footstep with new path (simulates browse merge)
        self.slots._audio_files["footstep"] = "/audio/v2/Footstep.wav"
        EventTriggers.ensure(
            [self.loc],
            events=["Footstep", "Jump"],
            category="audio",
        )

        # Step 4 — verify
        self.assertEqual(
            self.slots._audio_files["footstep"],
            "/audio/v2/Footstep.wav",
            "Path should be v2",
        )

        events_v2 = EventTriggers.get_events(self.loc, category="audio")
        self.assertEqual(
            events_v2.index("Footstep"),
            idx_footstep_v1,
            "Footstep enum index must not change",
        )
        self.assertEqual(
            events_v2.index("Jump"),
            idx_jump_v1,
            "Jump enum index must not change",
        )

        keyed = EventTriggers.iter_keyed_events(self.loc, category="audio")
        keyed_dict = {lbl: int(t) for t, lbl in keyed}
        self.assertIn("Footstep", keyed_dict, "Footstep keyframe lost after re-add")
        self.assertEqual(keyed_dict["Footstep"], 12, "Footstep keyframe time changed")

    def test_readd_only_one_of_multiple_tracks_does_not_affect_others(self):
        """Re-adding one track must leave sibling tracks completely intact."""
        self.slots._audio_files["footstep"] = "/audio/Footstep.wav"
        self.slots._audio_files["jump"] = "/audio/Jump.wav"
        self.slots._audio_files["land"] = "/audio/Land.wav"
        EventTriggers.ensure(
            [self.loc],
            events=["Footstep", "Jump", "Land"],
            category="audio",
        )
        EventTriggers.set_key(self.loc, "Jump", time=24, category="audio")

        events_before = EventTriggers.get_events(self.loc, category="audio")
        idx_jump = events_before.index("Jump")
        idx_land = events_before.index("Land")

        # Re-add only Footstep
        self.slots._audio_files["footstep"] = "/audio/v2/Footstep.wav"
        EventTriggers.ensure(
            [self.loc],
            events=["Footstep", "Jump", "Land"],
            category="audio",
        )

        events_after = EventTriggers.get_events(self.loc, category="audio")
        self.assertEqual(events_after.index("Jump"), idx_jump)
        self.assertEqual(events_after.index("Land"), idx_land)

        keyed = EventTriggers.iter_keyed_events(self.loc, category="audio")
        keyed_dict = {lbl: int(t) for t, lbl in keyed}
        self.assertIn("Jump", keyed_dict, "Jump keyframe lost when Footstep re-added")
        self.assertEqual(keyed_dict["Jump"], 24)


class TestLoadTracksPreviewNodeBehavior(MayaTkTestCase):
    """load_tracks() creates and updates preview audio nodes.

    - New stems create fresh audio nodes.
    - Existing stems with unchanged paths are skipped.
    - Existing stems with changed paths are updated in-place
      (the node's filename is reconfigured without deleting/recreating).

    Uses WAV files from the test fixtures directory if available,
    otherwise skips gracefully.
    """

    FIXTURE_DIR = os.path.join(scripts_dir, "mayatk", "test", "fixtures", "audio")

    def _wav_files(self):
        if not os.path.isdir(self.FIXTURE_DIR):
            return []
        return [
            os.path.join(self.FIXTURE_DIR, f)
            for f in os.listdir(self.FIXTURE_DIR)
            if f.lower().endswith(".wav")
        ]

    def test_second_load_tracks_unchanged_path_returns_empty(self):
        """Calling load_tracks twice with the same path returns [] (no change)."""
        wavs = self._wav_files()
        if not wavs:
            self.skipTest("No .wav fixtures in test/fixtures/audio/")
        nodes_1 = AudioEvents.load_tracks(wavs[:1], category="audio")
        nodes_2 = AudioEvents.load_tracks(wavs[:1], category="audio")
        self.assertTrue(nodes_1, "First load should create a node")
        self.assertEqual(
            nodes_2, [], "Second load with same path should skip — no change"
        )

    def test_load_tracks_creates_new_stems_alongside_existing(self):
        """New stems are added without removing existing preview nodes."""
        wavs = self._wav_files()
        if len(wavs) < 2:
            self.skipTest("Need at least 2 .wav fixtures in test/fixtures/audio/")
        nodes_1 = AudioEvents.load_tracks(wavs[:1], category="audio")
        nodes_2 = AudioEvents.load_tracks(wavs[1:2], category="audio")
        self.assertTrue(nodes_1)
        self.assertTrue(nodes_2, "Second load with different stem should create a node")
        all_members = AudioEvents.list_nodes(category="audio")
        self.assertEqual(len(all_members), 2)

    def test_load_tracks_updates_node_when_path_changes(self):
        """Re-adding a stem with a different file updates the existing node.

        Bug: load_tracks skipped existing stems unconditionally, so
        re-adding a track with a new WAV left the old audio node stale.
        Fixed: 2026-02-23 — load_tracks now detects path changes and
        reconfigures the existing node in-place.
        """
        wavs = self._wav_files()
        if len(wavs) < 2:
            self.skipTest("Need at least 2 .wav fixtures in test/fixtures/audio/")

        # Load first file under stem "TestStem"
        first_path = wavs[0].replace("\\", "/")
        second_path = wavs[1].replace("\\", "/")
        stem = os.path.splitext(os.path.basename(first_path))[0]

        nodes_1 = AudioEvents.load_tracks([first_path], category="audio")
        self.assertTrue(nodes_1)
        node = nodes_1[0]
        path_before = cmds.getAttr(f"{node}.filename").replace("\\", "/")

        # Now manually reconfigure: rename second file to same stem in a temp copy
        # Instead, directly create a second call with a different path but same stem
        # by creating a symlink or copy — but simpler: just set the node's filename
        # to something else, then call load_tracks again with the original path.
        cmds.setAttr(f"{node}.filename", "/fake/changed_path.wav", type="string")

        # Now call load_tracks with original path — should detect the difference
        nodes_2 = AudioEvents.load_tracks([first_path], category="audio")
        self.assertTrue(
            nodes_2,
            "load_tracks should return the updated node when path changed",
        )
        path_after = cmds.getAttr(f"{nodes_2[0]}.filename").replace("\\", "/")
        self.assertNotEqual(path_after, "/fake/changed_path.wav")


class TestBrowseTriggersCompositeRebuild(MayaTkTestCase):
    """_browse_audio_files rebuilds composite when keyed events exist.

    Bug: After editing tracks via the Add button, the composite WAV
    was stale because _browse_audio_files only called load_tracks()
    (preview nodes) but never sync() to rebuild the composite.
    Fixed: 2026-02-23 — _browse_audio_files now calls
    _sync_and_refresh_target() when keyed events are present.
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        loc_name = cmds.spaceLocator(name="carrier")[0]
        self.loc = pm.PyNode(loc_name)
        self.slots._current_target = self.loc

    def test_browse_calls_sync_when_keyed_events_exist(self):
        """After adding tracks, if events are already keyed, sync is triggered."""
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        EventTriggers.create([self.loc], events=["Footstep"], category="audio")
        EventTriggers.set_key(self.loc, "Footstep", time=12, category="audio")
        self.slots._audio_files["footstep"] = "/audio/Footstep.wav"

        # Simulate _browse_audio_files with mocked dialog and sync
        with patch(
            "qtpy.QtWidgets.QFileDialog.getOpenFileNames",
            return_value=(["/audio/Footstep.wav"], ""),
        ):
            with patch.object(
                self.slots,
                "_prepare_selected_paths",
                return_value=["/audio/Footstep.wav"],
            ):
                with patch.object(
                    AudioEvents, "load_tracks", return_value=["footstep"]
                ):
                    with patch.object(self.slots, "_save_file_map"):
                        with patch.object(
                            self.slots, "_sync_and_refresh_target", return_value=1
                        ) as mock_sync:
                            with patch.object(self.slots, "_repair_enum_casing"):
                                self.slots._browse_audio_files()
                                mock_sync.assert_called_once_with(self.loc)

    def test_browse_skips_sync_when_no_keyed_events(self):
        """Without keyed events, browse only calls load_tracks, not sync."""
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        EventTriggers.create([self.loc], events=["Footstep"], category="audio")
        # No set_key — no keyed events
        self.slots._audio_files["footstep"] = "/audio/Footstep.wav"

        with patch(
            "qtpy.QtWidgets.QFileDialog.getOpenFileNames",
            return_value=(["/audio/Footstep.wav"], ""),
        ):
            with patch.object(
                self.slots,
                "_prepare_selected_paths",
                return_value=["/audio/Footstep.wav"],
            ):
                with patch.object(
                    AudioEvents, "load_tracks", return_value=["footstep"]
                ):
                    with patch.object(self.slots, "_save_file_map"):
                        with patch.object(
                            self.slots, "_sync_and_refresh_target"
                        ) as mock_sync:
                            with patch.object(self.slots, "_repair_enum_casing"):
                                with patch.object(
                                    self.slots, "_refresh_combo_from_target"
                                ):
                                    self.slots._browse_audio_files()
                                    mock_sync.assert_not_called()


# ===========================================================================
# Per-Object Composite Audio
# ===========================================================================


class TestPerObjectComposite(MayaTkTestCase):
    """Verify per-object composite naming, auto-switch, and auto-sync export.

    Each trigger object should get its own ``{objName}_composite`` audio
    node and cached WAV, and selecting a different trigger object should
    activate its composite for timeline playback.
    Fixed: 2026-03-04
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        self.locA = pm.PyNode(cmds.spaceLocator(name="carrierA")[0])
        self.locB = pm.PyNode(cmds.spaceLocator(name="carrierB")[0])
        EventTriggers.create([self.locA], events=["FootstepA"], category="audio")
        EventTriggers.create([self.locB], events=["FootstepB"], category="audio")
        EventTriggers.set_key(
            self.locA, "FootstepA", time=10, auto_clear=False, category="audio"
        )
        EventTriggers.set_key(
            self.locB, "FootstepB", time=20, auto_clear=False, category="audio"
        )

    def test_sync_creates_per_object_composite_node(self):
        """Syncing object A should create 'carrierA_composite', not 'audio_composite'."""
        fake_map = {"footstepa": "/audio/FootstepA.wav"}
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value="/fake/cache/_composite_carrierA.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            results = AudioEvents.sync(
                objects=[self.locA],
                audio_file_map=fake_map,
                category="audio",
            )
        # Check that the per-object composite node was created
        nodes = AudioEvents.list_nodes(category="audio")
        comp_names = [n for n in nodes if n.endswith("_composite")]
        self.assertIn(
            "carrierA_composite",
            comp_names,
            "Per-object composite 'carrierA_composite' should exist.",
        )
        # Legacy global composite should NOT exist
        self.assertNotIn(
            "audio_composite",
            comp_names,
            "Legacy 'audio_composite' should not be created.",
        )

    def test_sync_two_objects_creates_separate_composites(self):
        """Syncing A then B should produce two distinct composites."""
        fake_map_a = {"footstepa": "/audio/FootstepA.wav"}
        fake_map_b = {"footstepb": "/audio/FootstepB.wav"}
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map_a,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value="/fake/cache/_composite_carrierA.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            AudioEvents.sync(
                objects=[self.locA],
                audio_file_map=fake_map_a,
                category="audio",
            )
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map_b,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value="/fake/cache/_composite_carrierB.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            AudioEvents.sync(
                objects=[self.locB],
                audio_file_map=fake_map_b,
                category="audio",
            )
        nodes = AudioEvents.list_nodes(category="audio")
        comp_names = [n for n in nodes if n.endswith("_composite")]
        self.assertIn("carrierA_composite", comp_names)
        self.assertIn("carrierB_composite", comp_names)

    def test_resyncing_object_replaces_only_its_composite(self):
        """Re-syncing object A should not delete object B's composite."""
        fake_map_a = {"footstepa": "/audio/FootstepA.wav"}
        fake_map_b = {"footstepb": "/audio/FootstepB.wav"}
        # First sync both
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map_a,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value="/fake/cache/_composite_carrierA.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            AudioEvents.sync(
                objects=[self.locA],
                audio_file_map=fake_map_a,
                category="audio",
            )
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map_b,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value="/fake/cache/_composite_carrierB.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            AudioEvents.sync(
                objects=[self.locB],
                audio_file_map=fake_map_b,
                category="audio",
            )
        # Re-sync A
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map_a,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value="/fake/cache/_composite_carrierA_v2.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            AudioEvents.sync(
                objects=[self.locA],
                audio_file_map=fake_map_a,
                category="audio",
            )
        nodes = AudioEvents.list_nodes(category="audio")
        comp_names = [n for n in nodes if n.endswith("_composite")]
        self.assertIn("carrierA_composite", comp_names)
        self.assertIn(
            "carrierB_composite",
            comp_names,
            "B's composite should survive A's re-sync.",
        )

    def test_activate_composite_for_switches_active_sound(self):
        """_activate_composite_for should call set_active with the per-object node."""
        # Create a fake composite node for carrierA
        comp_node = cmds.createNode("audio", name="carrierA_composite", skipSelect=True)
        AudioEvents._stamp_event_attrs(comp_node, "", "composite")
        audio_set = AudioEvents._get_or_create_set("audio")
        cmds.sets(comp_node, addElement=audio_set.name())

        self.slots._current_target = self.locA
        with patch.object(AudioEvents, "set_active") as mock_active:
            self.slots._activate_composite_for(self.locA)
            mock_active.assert_called_once_with("carrierA_composite")

    def test_hydrate_activates_correct_composite(self):
        """Switching to a target with a composite should activate it."""
        # Create composites for both objects
        for name in ["carrierA_composite", "carrierB_composite"]:
            node = cmds.createNode("audio", name=name, skipSelect=True)
            AudioEvents._stamp_event_attrs(node, "", "composite")
            audio_set = AudioEvents._get_or_create_set("audio")
            cmds.sets(node, addElement=audio_set.name())

        with patch.object(AudioEvents, "set_active") as mock_active:
            self.slots._hydrate_from_target(self.locB)
            mock_active.assert_called_once_with("carrierB_composite")

    def test_export_auto_syncs_before_finding_composite(self):
        """_export_composite should call _sync_and_refresh_target before exporting."""
        self.slots._current_target = self.locA
        self.slots._audio_files = {"footstepa": "/audio/FootstepA.wav"}

        with patch.object(
            self.slots, "_sync_and_refresh_target", return_value=1
        ) as mock_sync:
            with patch.object(AudioEvents, "list_nodes", return_value=[]):
                self.slots._export_composite()
                mock_sync.assert_called_once_with(self.locA)


# ===========================================================================
# Cross-target hydration isolation (dead-space bug)
# ===========================================================================


class TestHydrateCrossTargetIsolation(MayaTkTestCase):
    """Verify _hydrate_from_target does not bleed nodes across targets.

    Bug: The global ``audio_set`` contains nodes from ALL targets.  When
    hydrating a new target with no persisted ``audio_file_map``, the
    node-filename fallback read paths from other targets' preview nodes,
    causing wrong/stale entries in ``_audio_files``.  Downstream,
    ``build_composite_wav`` produced silence (dead spaces) because the
    paths belonged to the wrong source directory.

    Fixed: 2026-02-23  — When a persisted file-map exists, skip the
    node-filename fallback entirely.  When the fallback runs, reject
    any path containing ``_maya_audio_cache`` or ``_audio_cache``
    directory segments.
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def test_persisted_map_skips_node_fallback(self):
        """When a persisted file map exists, node filenames are NOT read."""
        target_name = cmds.spaceLocator(name="targetA")[0]
        target = pm.PyNode(target_name)
        EventTriggers.create([target], events=["Kick"], category="audio")

        # Create a preview node in the global audio_set with a wrong path
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        wrong_node = cmds.createNode("audio", name="kick", skipSelect=True)
        cmds.setAttr(f"{wrong_node}.filename", "/wrong/kick.wav", type="string")
        AudioEvents._stamp_event_attrs(wrong_node, "kick", "preview")
        cmds.sets(wrong_node, addElement=audio_set.name())

        # Persist a correct file map on the target
        self.slots._current_target = None
        self.slots._audio_files = {"kick": "/correct/kick.wav"}
        self.slots._save_file_map(target)
        self.slots._audio_files.clear()

        # Hydrate — should load persisted map, NOT the node filename
        self.slots._hydrate_from_target(target)
        self.assertEqual(
            self.slots._audio_files.get("kick"),
            "/correct/kick.wav",
            "Persisted path should take priority; node fallback should be skipped.",
        )

    def test_fallback_rejects_cache_paths(self):
        """Node filenames inside _maya_audio_cache dirs are rejected."""
        target_name = cmds.spaceLocator(name="targetB")[0]
        target = pm.PyNode(target_name)
        EventTriggers.create([target], events=["Snare"], category="audio")

        # Ensure no persisted file map on target so the fallback runs
        node_name = str(target)
        if cmds.attributeQuery(self.slots.FILE_MAP_ATTR, node=node_name, exists=True):
            cmds.deleteAttr(f"{node_name}.{self.slots.FILE_MAP_ATTR}")

        # Create a node with a cache path
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        cache_node = cmds.createNode("audio", name="snare", skipSelect=True)
        cache_path = "/project/audio/_maya_audio_cache/snare_abc123.wav"
        cmds.setAttr(f"{cache_node}.filename", cache_path, type="string")
        AudioEvents._stamp_event_attrs(cache_node, "snare", "preview")
        cmds.sets(cache_node, addElement=audio_set.name())

        self.slots._current_target = None
        self.slots._audio_files.clear()
        self.slots._hydrate_from_target(target)
        self.assertNotIn(
            "snare",
            self.slots._audio_files,
            "Cache paths from node filenames must NOT enter _audio_files.",
        )

    def test_fallback_accepts_source_paths(self):
        """Non-cache node filenames ARE accepted when no persisted map exists."""
        target_name = cmds.spaceLocator(name="targetC")[0]
        target = pm.PyNode(target_name)
        EventTriggers.create([target], events=["HiHat"], category="audio")

        # Ensure no persisted file map
        node_name = str(target)
        if cmds.attributeQuery(self.slots.FILE_MAP_ATTR, node=node_name, exists=True):
            cmds.deleteAttr(f"{node_name}.{self.slots.FILE_MAP_ATTR}")

        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        src_node = cmds.createNode("audio", name="hihat", skipSelect=True)
        src_path = "/project/audio/hihat.wav"
        cmds.setAttr(f"{src_node}.filename", src_path, type="string")
        AudioEvents._stamp_event_attrs(src_node, "hihat", "preview")
        cmds.sets(src_node, addElement=audio_set.name())

        self.slots._current_target = None
        self.slots._audio_files.clear()
        self.slots._hydrate_from_target(target)
        self.assertEqual(
            self.slots._audio_files.get("hihat"),
            src_path,
            "Valid source path should be accepted by the node fallback.",
        )

    def test_two_targets_different_audio_no_cross_contamination(self):
        """Two targets with identical event names but different audio files.

        After hydrating target B, _audio_files must reflect target B's
        persisted map — not target A's node filenames.
        """
        targetA_name = cmds.spaceLocator(name="carrierA")[0]
        targetA = pm.PyNode(targetA_name)
        targetB_name = cmds.spaceLocator(name="carrierB")[0]
        targetB = pm.PyNode(targetB_name)
        EventTriggers.create([targetA], events=["Boom"], category="audio")
        EventTriggers.create([targetB], events=["Boom"], category="audio")

        # Persist different file maps
        self.slots._current_target = None
        self.slots._audio_files = {"boom": "/dirA/boom.wav"}
        self.slots._save_file_map(targetA)
        self.slots._audio_files = {"boom": "/dirB/boom.wav"}
        self.slots._save_file_map(targetB)

        # Create a preview node (simulating that target A loaded first)
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        node = cmds.createNode("audio", name="boom", skipSelect=True)
        cmds.setAttr(f"{node}.filename", "/dirA/boom.wav", type="string")
        AudioEvents._stamp_event_attrs(node, "boom", "preview")
        cmds.sets(node, addElement=audio_set.name())

        # Hydrate target B — must get B's path, not A's
        self.slots._audio_files.clear()
        self.slots._hydrate_from_target(targetB, old_target=targetA)
        self.assertEqual(
            self.slots._audio_files.get("boom"),
            "/dirB/boom.wav",
            "Target B should hydrate from its own persisted map, not target A's node.",
        )


# ===========================================================================
# _create_new_audio_object track leakage
# ===========================================================================


class TestCreateNewAudioObjectClearsFiles(MayaTkTestCase):
    """Verify _create_new_audio_object clears _audio_files from prior target.

    Bug: _create_new_audio_object set _current_target without clearing
    _audio_files.  When _sync_from_selection ran afterward, old_target
    already equalled the new object so the clear guard was skipped.
    Subsequent browse merged old tracks into the new object.
    Fixed: 2026-02-25
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        # Simulate an active target with loaded tracks
        loc_name = cmds.spaceLocator(name="oldTarget")[0]
        self.old_loc = pm.PyNode(loc_name)
        EventTriggers.create(
            [self.old_loc], events=["Footstep", "Jump"], category="audio"
        )
        self.slots._current_target = self.old_loc
        self.slots._audio_files = {
            "footstep": "/audio/Footstep.wav",
            "jump": "/audio/Jump.wav",
        }

    def test_create_new_object_clears_audio_files(self):
        """_audio_files must be empty after creating a new audio object."""
        with patch("maya.cmds.evalDeferred"):
            with patch.object(self.slots.sb, "input_dialog", return_value="new_obj"):
                self.slots._create_new_audio_object()

        self.assertEqual(
            self.slots._audio_files,
            {},
            "Old tracks should not persist after creating a new audio object.",
        )

    def test_new_object_browse_has_no_old_tracks(self):
        """Browsing on the new object must not include old target's tracks.

        Simulates: create new object → browse for a single file → verify
        only that file appears in _audio_files (no old tracks).
        """
        with patch("maya.cmds.evalDeferred"):
            with patch.object(self.slots.sb, "input_dialog", return_value="new_obj2"):
                self.slots._create_new_audio_object()

        new_target = self.slots._current_target
        self.assertIsNotNone(new_target)

        # Now simulate browsing a single new file
        self.slots._audio_files["bark"] = "/new_audio/Bark.wav"

        self.assertNotIn(
            "footstep",
            self.slots._audio_files,
            "Old 'footstep' track leaked into the new audio object.",
        )
        self.assertNotIn(
            "jump",
            self.slots._audio_files,
            "Old 'jump' track leaked into the new audio object.",
        )
        self.assertEqual(
            list(self.slots._audio_files.keys()),
            ["bark"],
            "Only the newly added track should be present.",
        )


# ===========================================================================
# Prepare Selected Paths (Auto Convert)
# ===========================================================================


class TestPrepareSelectedPaths(MayaTkTestCase):
    """Verify _prepare_selected_paths respects the Auto Convert header option.

    The header checkbox controls whether non-playable formats (MP3, OGG,
    etc.) are silently included for conversion or silently skipped — no
    dialog prompt should appear in either case.

    Added: 2026-03-03
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def _set_auto_convert(self, enabled):
        """Wire the header.menu.chk_auto_convert mock to return *enabled*."""
        chk = MagicMock()
        chk.isChecked.return_value = enabled
        self.slots.ui.header.menu.chk_auto_convert = chk

    def test_auto_convert_enabled_includes_convertible(self):
        """With Auto Convert on + FFmpeg available, convertible files are included."""
        self._set_auto_convert(True)
        with patch(
            "pythontk.AudioUtils.resolve_ffmpeg", return_value="/usr/bin/ffmpeg"
        ):
            result = self.slots._prepare_selected_paths(["/a/clip.wav", "/a/track.mp3"])
        self.assertIn("/a/clip.wav", result)
        self.assertIn("/a/track.mp3", result)

    def test_auto_convert_enabled_no_ffmpeg_skips_convertible(self):
        """With Auto Convert on but no FFmpeg, convertible files are dropped."""
        self._set_auto_convert(True)
        with patch("pythontk.AudioUtils.resolve_ffmpeg", return_value=None):
            with patch("qtpy.QtWidgets.QMessageBox.warning"):
                result = self.slots._prepare_selected_paths(
                    ["/a/clip.wav", "/a/track.mp3"]
                )
        self.assertEqual(result, ["/a/clip.wav"])

    def test_auto_convert_disabled_skips_convertible_silently(self):
        """With Auto Convert off, convertible files are silently dropped."""
        self._set_auto_convert(False)
        result = self.slots._prepare_selected_paths(["/a/clip.wav", "/a/track.mp3"])
        self.assertEqual(result, ["/a/clip.wav"])

    def test_auto_convert_disabled_no_dialog_shown(self):
        """With Auto Convert off, no QMessageBox prompt should appear."""
        self._set_auto_convert(False)
        with patch("qtpy.QtWidgets.QMessageBox.question") as mock_q:
            self.slots._prepare_selected_paths(["/a/clip.wav", "/a/track.ogg"])
            mock_q.assert_not_called()

    def test_auto_convert_enabled_no_dialog_shown(self):
        """With Auto Convert on, no QMessageBox.question prompt should appear."""
        self._set_auto_convert(True)
        with patch(
            "pythontk.AudioUtils.resolve_ffmpeg", return_value="/usr/bin/ffmpeg"
        ):
            with patch("qtpy.QtWidgets.QMessageBox.question") as mock_q:
                self.slots._prepare_selected_paths(["/a/clip.wav", "/a/track.mp3"])
                mock_q.assert_not_called()

    def test_only_playable_files_returns_all(self):
        """When all files are playable, no conversion logic runs."""
        self._set_auto_convert(True)
        result = self.slots._prepare_selected_paths(["/a/clip.wav", "/a/other.aif"])
        self.assertEqual(result, ["/a/clip.wav", "/a/other.aif"])

    def test_unsupported_formats_always_skipped(self):
        """Files with unknown extensions are always dropped."""
        self._set_auto_convert(True)
        with patch("qtpy.QtWidgets.QMessageBox.warning"):
            with patch(
                "pythontk.AudioUtils.resolve_ffmpeg", return_value="/usr/bin/ffmpeg"
            ):
                result = self.slots._prepare_selected_paths(
                    ["/a/clip.wav", "/a/data.xyz"]
                )
        self.assertEqual(result, ["/a/clip.wav"])

    def test_mixed_playable_convertible_unsupported(self):
        """Playable + convertible + unsupported: only playable + convertible returned."""
        self._set_auto_convert(True)
        with patch("qtpy.QtWidgets.QMessageBox.warning"):
            with patch(
                "pythontk.AudioUtils.resolve_ffmpeg", return_value="/usr/bin/ffmpeg"
            ):
                result = self.slots._prepare_selected_paths(
                    ["/a/clip.wav", "/a/track.mp3", "/a/bad.xyz"]
                )
        self.assertIn("/a/clip.wav", result)
        self.assertIn("/a/track.mp3", result)
        self.assertNotIn("/a/bad.xyz", result)


# ===========================================================================
# Replace Selected Track (b005)
# ===========================================================================


class TestReplaceSelectedTrack(MayaTkTestCase):
    """Verify b005 renames the enum label, updates _audio_files, and re-syncs."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        loc_name = cmds.spaceLocator(name="replaceCarrier")[0]
        self.loc = pm.PyNode(loc_name)
        EventTriggers.create([self.loc], events=["OldClip", "Other"], category="audio")
        self.slots._current_target = self.loc
        self.slots._audio_files = {
            "oldclip": "/audio/OldClip.wav",
            "other": "/audio/Other.wav",
        }
        self.slots._save_file_map(self.loc)

        # Create a preview node for OldClip
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        node = cmds.createNode("audio", name="oldclip", skipSelect=True)
        cmds.setAttr(f"{node}.filename", "/audio/OldClip.wav", type="string")
        AudioEvents._stamp_event_attrs(node, "oldclip", "preview")
        cmds.sets(node, addElement=audio_set.name())

    def test_replace_renames_enum_label(self):
        """Replacing a track renames the enum entry, preserving keyframe index."""
        from mayatk.node_utils.attributes._attributes import Attributes

        # Get original index of OldClip
        trigger_attr, _ = EventTriggers.attr_names("audio")
        pairs_before = Attributes.parse_enum_def(str(self.loc), trigger_attr)
        old_idx = next(idx for label, idx in pairs_before if label == "OldClip")

        # Mock the combo to return the selected track
        self.slots.ui.cmb000.currentText.return_value = "OldClip"

        with patch(
            "qtpy.QtWidgets.QFileDialog.getOpenFileNames",
            return_value=(["/audio/NewClip.wav"], ""),
        ):
            with patch.object(
                self.slots,
                "_prepare_selected_paths",
                return_value=["/audio/NewClip.wav"],
            ):
                with patch.object(
                    AudioEvents,
                    "_resolve_playable_path",
                    return_value="/audio/NewClip.wav",
                ):
                    with patch.object(self.slots, "_save_file_map"):
                        with patch.object(self.slots, "_refresh_combo_from_target"):
                            self.slots.b005()

        # Verify enum was renamed
        pairs_after = Attributes.parse_enum_def(str(self.loc), trigger_attr)
        new_idx = next((idx for label, idx in pairs_after if label == "NewClip"), None)
        old_still = any(label == "OldClip" for label, _ in pairs_after)

        self.assertFalse(old_still, "OldClip should no longer exist in the enum.")
        self.assertEqual(
            new_idx,
            old_idx,
            "NewClip must occupy the same enum index as OldClip.",
        )

    def test_replace_updates_audio_files_dict(self):
        """_audio_files should drop the old key and have the new key."""
        self.slots.ui.cmb000.currentText.return_value = "OldClip"

        with patch(
            "qtpy.QtWidgets.QFileDialog.getOpenFileNames",
            return_value=(["/audio/NewClip.wav"], ""),
        ):
            with patch.object(
                self.slots,
                "_prepare_selected_paths",
                return_value=["/audio/NewClip.wav"],
            ):
                with patch.object(
                    AudioEvents,
                    "_resolve_playable_path",
                    return_value="/audio/NewClip.wav",
                ):
                    with patch.object(self.slots, "_save_file_map"):
                        with patch.object(self.slots, "_refresh_combo_from_target"):
                            self.slots.b005()

        self.assertNotIn("oldclip", self.slots._audio_files)
        self.assertEqual(self.slots._audio_files.get("newclip"), "/audio/NewClip.wav")

    def test_replace_same_stem_updates_path_only(self):
        """When the new file has the same stem, only the path changes."""
        self.slots.ui.cmb000.currentText.return_value = "OldClip"

        with patch(
            "qtpy.QtWidgets.QFileDialog.getOpenFileNames",
            return_value=(["/other_dir/OldClip.wav"], ""),
        ):
            with patch.object(
                self.slots,
                "_prepare_selected_paths",
                return_value=["/other_dir/OldClip.wav"],
            ):
                with patch.object(
                    AudioEvents,
                    "_resolve_playable_path",
                    return_value="/other_dir/OldClip.wav",
                ):
                    with patch.object(self.slots, "_save_file_map"):
                        with patch.object(self.slots, "_refresh_combo_from_target"):
                            self.slots.b005()

        self.assertEqual(
            self.slots._audio_files.get("oldclip"), "/other_dir/OldClip.wav"
        )

    def test_replace_preserves_sibling_events(self):
        """Replacing one track must not disturb other events."""
        from mayatk.node_utils.attributes._attributes import Attributes

        trigger_attr, _ = EventTriggers.attr_names("audio")
        pairs_before = Attributes.parse_enum_def(str(self.loc), trigger_attr)
        other_idx = next(idx for label, idx in pairs_before if label == "Other")

        self.slots.ui.cmb000.currentText.return_value = "OldClip"

        with patch(
            "qtpy.QtWidgets.QFileDialog.getOpenFileNames",
            return_value=(["/audio/NewClip.wav"], ""),
        ):
            with patch.object(
                self.slots,
                "_prepare_selected_paths",
                return_value=["/audio/NewClip.wav"],
            ):
                with patch.object(
                    AudioEvents,
                    "_resolve_playable_path",
                    return_value="/audio/NewClip.wav",
                ):
                    with patch.object(self.slots, "_save_file_map"):
                        with patch.object(self.slots, "_refresh_combo_from_target"):
                            self.slots.b005()

        pairs_after = Attributes.parse_enum_def(str(self.loc), trigger_attr)
        other_idx_after = next(
            (idx for label, idx in pairs_after if label == "Other"), None
        )
        self.assertEqual(other_idx_after, other_idx, "Other event must be untouched.")
        self.assertIn(
            "other", self.slots._audio_files, "Sibling file-map entry must survive."
        )

    def test_replace_triggers_sync_when_keyed(self):
        """If keyed events exist, replace should call _sync_and_refresh_target."""
        EventTriggers.set_key(self.loc, "OldClip", time=10, category="audio")
        self.slots.ui.cmb000.currentText.return_value = "OldClip"

        with patch(
            "qtpy.QtWidgets.QFileDialog.getOpenFileNames",
            return_value=(["/audio/NewClip.wav"], ""),
        ):
            with patch.object(
                self.slots,
                "_prepare_selected_paths",
                return_value=["/audio/NewClip.wav"],
            ):
                with patch.object(
                    AudioEvents,
                    "_resolve_playable_path",
                    return_value="/audio/NewClip.wav",
                ):
                    with patch.object(self.slots, "_save_file_map"):
                        with patch.object(
                            self.slots, "_sync_and_refresh_target", return_value=1
                        ) as mock_sync:
                            self.slots.b005()
                            mock_sync.assert_called_once_with(self.loc)


# ===========================================================================
# Start Anchor None Key
# ===========================================================================


class TestEnsureStartAnchor(MayaTkTestCase):
    """Verify _ensure_start_anchor keys a None value at frame 0 when the
    first audio event is keyed on an object.

    Bug: Without an anchor key, Maya's stepped tangent holds the first
    event value backward to frame 0.  A "tie keyframe" script evaluating
    at frame 0 would then bind the event value, triggering unwanted
    audio at the animation clip start.
    Fixed: 2026-03-04
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        loc_name = cmds.spaceLocator(name="anchorCarrier")[0]
        self.loc = pm.PyNode(loc_name)
        EventTriggers.create([self.loc], events=["Footstep", "Jump"], category="audio")
        self.slots._current_target = self.loc
        self.slots._audio_files = {
            "footstep": "/audio/Footstep.wav",
            "jump": "/audio/Jump.wav",
        }

    def _get_key_at_frame(self, frame):
        """Return the keyed enum index at the given frame, or None."""
        trigger_attr, _ = EventTriggers.attr_names("audio")
        attr_path = f"{self.loc.name()}.{trigger_attr}"
        vals = cmds.keyframe(
            attr_path, query=True, time=(frame, frame), valueChange=True
        )
        if vals:
            return int(round(vals[0]))
        return None

    def _has_any_keys(self):
        """Return True if the trigger attr has any keyframes."""
        trigger_attr, _ = EventTriggers.attr_names("audio")
        attr_path = f"{self.loc.name()}.{trigger_attr}"
        return bool(cmds.keyframe(attr_path, query=True))

    def test_anchor_keyed_at_frame_0_for_first_event(self):
        """When no keyframes exist, anchors None at frame 0."""
        self.assertFalse(self._has_any_keys())
        self.slots._ensure_start_anchor(self.loc, event_frame=10)
        val = self._get_key_at_frame(0)
        none_idx = EventTriggers.event_index(self.loc, "None", category="audio")
        self.assertEqual(val, none_idx, "None key should be anchored at frame 0.")

    def test_noop_when_keys_already_exist(self):
        """If keyframes already exist, no additional anchor is added."""
        EventTriggers.set_key(
            self.loc, "Footstep", time=10, auto_clear=False, category="audio"
        )
        key_count_before = len(
            cmds.keyframe(f"{self.loc.name()}.audio_trigger", query=True)
        )
        self.slots._ensure_start_anchor(self.loc, event_frame=20)
        key_count_after = len(
            cmds.keyframe(f"{self.loc.name()}.audio_trigger", query=True)
        )
        self.assertEqual(
            key_count_before,
            key_count_after,
            "No new key should be added when keys already exist.",
        )

    def test_anchor_at_frame_0_when_event_at_frame_1(self):
        """Event at frame 1 should still anchor None at frame 0."""
        self.slots._ensure_start_anchor(self.loc, event_frame=1)
        val = self._get_key_at_frame(0)
        none_idx = EventTriggers.event_index(self.loc, "None", category="audio")
        self.assertEqual(val, none_idx)

    def test_anchor_at_negative_one_when_event_at_frame_0(self):
        """When the event is at frame 0, anchor should be placed at frame -1.

        Bug: Previously the code clamped to max(event_frame - 1, 0) which
        produced anchor_frame == 0 == event_frame, causing a silent no-op.
        Fixed: 2026-03-05 — removed the max(0) clamp so frame -1 is used.
        """
        self.slots._ensure_start_anchor(self.loc, event_frame=0)
        val = self._get_key_at_frame(-1)
        none_idx = EventTriggers.event_index(self.loc, "None", category="audio")
        self.assertEqual(
            val,
            none_idx,
            "None key should be anchored at frame -1 when event is at frame 0.",
        )

    def test_idempotent_multiple_calls(self):
        """Calling _ensure_start_anchor multiple times should not add extra keys."""
        self.slots._ensure_start_anchor(self.loc, event_frame=10)
        keys_after_first = cmds.keyframe(f"{self.loc.name()}.audio_trigger", query=True)
        self.slots._ensure_start_anchor(self.loc, event_frame=20)
        keys_after_second = cmds.keyframe(
            f"{self.loc.name()}.audio_trigger", query=True
        )
        self.assertEqual(
            len(keys_after_first),
            len(keys_after_second),
            "Second call should be a no-op (keys already exist).",
        )


# ===========================================================================
# Realign None End-Keys After Track Swap
# ===========================================================================


class TestRealignNoneEndKeys(MayaTkTestCase):
    """Verify _realign_none_end_keys relocates stale None end-keys
    after an audio track is swapped to a file with a different duration.

    Bug: When b005 (Replace) or _browse_audio_files (Add) swapped an
    audio file, previously-keyed None end-markers stayed at the old
    clip's end frame, causing premature cutoff or silent gaps.
    Fixed: 2026-03-03
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        loc_name = cmds.spaceLocator(name="realignCarrier")[0]
        self.loc = pm.PyNode(loc_name)
        EventTriggers.create([self.loc], events=["ClipA", "ClipB"], category="audio")
        self.slots._current_target = self.loc
        self.slots._audio_files = {
            "clipa": "/audio/ClipA.wav",
            "clipb": "/audio/ClipB.wav",
        }
        self.slots._save_file_map(self.loc)

    def _key_timeline(self, entries):
        """Key a sequence of (frame, label) pairs on self.loc."""
        for frame, label in entries:
            EventTriggers.set_key(
                self.loc,
                event=label,
                time=frame,
                auto_clear=False,
                category="audio",
            )

    def _get_none_key_times(self):
        """Return sorted list of frames where a None key exists."""
        trigger_attr, _ = EventTriggers.attr_names("audio")
        none_idx = EventTriggers.event_index(self.loc, "None", category="audio")
        attr_path = f"{self.loc.name()}.{trigger_attr}"
        key_times = cmds.keyframe(attr_path, query=True) or []
        result = []
        for t in sorted(float(k) for k in key_times):
            vals = cmds.keyframe(attr_path, query=True, time=(t, t), valueChange=True)
            if vals and int(round(vals[0])) == none_idx:
                result.append(int(t))
        return result

    def test_none_key_moves_to_new_duration(self):
        """A None end-key at the old clip's end should move to the new
        clip's end frame after realignment.

        Setup: ClipA keyed at frame 10, None at frame 34 (24-frame clip).
        Swap ClipA to a 48-frame clip -> None should move to frame 58.
        """
        self._key_timeline([(10, "ClipA"), (34, "None")])

        # Simulate a swap: update file path and mock new duration.
        self.slots._audio_files["clipa"] = "/audio/ClipA_long.wav"
        with patch.object(self.slots, "_get_clip_length_frames", return_value=48.0):
            self.slots._realign_none_end_keys(self.loc, "ClipA")

        none_times = self._get_none_key_times()
        self.assertEqual(none_times, [58], "None key should be at frame 58.")

    def test_clamps_before_next_event(self):
        """If the new clip is longer than the gap to the next event,
        the None key should be clamped to the next event's start frame.

        Setup: ClipA at 10, None at 34, ClipB at 40.
        Swap ClipA to a 60-frame clip -> None should clamp to frame 40.
        """
        self._key_timeline([(10, "ClipA"), (34, "None"), (40, "ClipB")])

        self.slots._audio_files["clipa"] = "/audio/ClipA_verylong.wav"
        with patch.object(self.slots, "_get_clip_length_frames", return_value=60.0):
            self.slots._realign_none_end_keys(self.loc, "ClipA")

        none_times = self._get_none_key_times()
        self.assertEqual(none_times, [40], "None key should be clamped to frame 40.")

    def test_no_none_key_is_noop(self):
        """When no None end-key exists (Auto End None was off), the method
        should not add one — it only relocates existing None keys.
        """
        self._key_timeline([(10, "ClipA")])  # No None key placed.

        self.slots._audio_files["clipa"] = "/audio/ClipA_long.wav"
        with patch.object(self.slots, "_get_clip_length_frames", return_value=48.0):
            self.slots._realign_none_end_keys(self.loc, "ClipA")

        none_times = self._get_none_key_times()
        self.assertEqual(none_times, [], "No None key should be created.")

    def test_already_correct_is_noop(self):
        """When the None key is already at the correct frame, no keyframe
        edits should occur (idempotent).
        """
        self._key_timeline([(10, "ClipA"), (58, "None")])

        self.slots._audio_files["clipa"] = "/audio/ClipA.wav"
        with patch.object(self.slots, "_get_clip_length_frames", return_value=48.0):
            self.slots._realign_none_end_keys(self.loc, "ClipA")

        none_times = self._get_none_key_times()
        self.assertEqual(none_times, [58], "None key should remain at frame 58.")

    def test_multiple_occurrences_all_realigned(self):
        """If the same event is keyed at multiple frames, each occurrence's
        None end-key should be relocated independently.

        Setup: ClipA at 10 (None at 34), ClipA at 100 (None at 124).
        Swap to 48-frame clip -> None keys at 58 and 148.
        """
        self._key_timeline(
            [
                (10, "ClipA"),
                (34, "None"),
                (100, "ClipA"),
                (124, "None"),
            ]
        )

        self.slots._audio_files["clipa"] = "/audio/ClipA_long.wav"
        with patch.object(self.slots, "_get_clip_length_frames", return_value=48.0):
            self.slots._realign_none_end_keys(self.loc, "ClipA")

        none_times = self._get_none_key_times()
        self.assertEqual(none_times, [58, 148], "Both None keys should be relocated.")

    def test_shorter_clip_moves_none_earlier(self):
        """Swapping to a shorter clip should move the None key to an
        earlier frame.

        Setup: ClipA at 10, None at 58 (48-frame clip).
        Swap to 12-frame clip -> None should move to frame 22.
        """
        self._key_timeline([(10, "ClipA"), (58, "None")])

        self.slots._audio_files["clipa"] = "/audio/ClipA_short.wav"
        with patch.object(self.slots, "_get_clip_length_frames", return_value=12.0):
            self.slots._realign_none_end_keys(self.loc, "ClipA")

        none_times = self._get_none_key_times()
        self.assertEqual(none_times, [22], "None key should move to frame 22.")


# ===========================================================================
# Key All + Stagger Tests
# ===========================================================================


def _make_tb001_widget(key_all=False, next_event=False, auto_end=False, stagger=0):
    """Build a mock that mimics tb001's option_box.menu for tests."""
    widget = MagicMock()
    widget.option_box.menu.chk_key_all.isChecked.return_value = key_all
    widget.option_box.menu.chk_next_event.isChecked.return_value = next_event
    widget.option_box.menu.chk_auto_end_none.isChecked.return_value = auto_end
    widget.option_box.menu.spn_stagger.value.return_value = stagger
    return widget


class TestKeyAllEvents(MayaTkTestCase):
    """tb001 Key All mode keys every track sequentially from the current frame.

    Tests exercise ``_key_all_events`` directly to avoid dependency on
    the full tb001 UI mocking stack. The method accepts a widget mock,
    target PyNode, event list, and auto_end flag.

    Added: 2026-02-24
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

        # Create locator and capture its string name immediately so
        # that PyMEL reference corruption in later tearDown cycles
        # cannot affect our assertions.
        loc_name = cmds.spaceLocator(name="key_all_loc")[0]
        self.loc_name = loc_name  # string, not PyNode
        self.loc = pm.PyNode(loc_name)

        events = ["ClipA", "ClipB", "ClipC"]
        EventTriggers.create([self.loc], events=events, category="audio")
        self.slots._current_target = self.loc

        # Fake audio files — use dummy paths (never actually read)
        self.slots._audio_files = {
            "clipa": "/audio/ClipA.wav",
            "clipb": "/audio/ClipB.wav",
            "clipc": "/audio/ClipC.wav",
        }

        # Wire up ui.tb001 mock so tb001() can read auto_end_none via
        # self.ui.tb001.option_box.menu.chk_auto_end_none
        self.slots.ui.tb001 = MagicMock()

    def _run_key_all(self, auto_end=False, stagger=0):
        """Helper: call _key_all_events directly with mocked clip lengths."""
        widget = _make_tb001_widget(key_all=True, auto_end=auto_end, stagger=stagger)
        clip_length = 24.0  # simulate 1-second clip at 24fps
        events = ["ClipA", "ClipB", "ClipC"]

        with patch.object(
            self.slots,
            "_get_clip_length_frames",
            return_value=clip_length,
        ):
            with patch.object(
                self.slots,
                "_sync_and_refresh_target",
                return_value=3,
            ):
                pm.currentTime(10)
                self.slots._key_all_events(widget, self.loc, events, auto_end)

        return clip_length

    def _get_keyed(self):
        """Retrieve keyed events using stable string name via cmds."""
        # Check locator still exists in the scene
        self.assertTrue(
            cmds.objExists(self.loc_name),
            f"Locator '{self.loc_name}' was deleted during test.",
        )
        loc = pm.PyNode(self.loc_name)
        return EventTriggers.iter_keyed_events(loc, category="audio")

    def test_key_all_places_events_at_correct_frames(self):
        """Each track should be keyed at cursor, spaced by clip_length."""
        self._run_key_all(auto_end=False, stagger=0)

        keyed = self._get_keyed()
        keyed_non_none = [(t, l) for t, l in keyed if l != "None"]

        self.assertEqual(len(keyed_non_none), 3)
        # ClipA at 10, ClipB at 10+24=34, ClipC at 34+24=58
        self.assertEqual(keyed_non_none[0], (10.0, "ClipA"))
        self.assertEqual(keyed_non_none[1], (34.0, "ClipB"))
        self.assertEqual(keyed_non_none[2], (58.0, "ClipC"))

    def test_key_all_with_stagger(self):
        """Stagger adds extra frames between each clip."""
        self._run_key_all(auto_end=False, stagger=5)

        keyed = self._get_keyed()
        keyed_non_none = [(t, l) for t, l in keyed if l != "None"]

        self.assertEqual(len(keyed_non_none), 3)
        # ClipA at 10, ClipB at 10+24+5=39, ClipC at 39+24+5=68
        self.assertEqual(keyed_non_none[0], (10.0, "ClipA"))
        self.assertEqual(keyed_non_none[1], (39.0, "ClipB"))
        self.assertEqual(keyed_non_none[2], (68.0, "ClipC"))

    def test_key_all_with_auto_end_none(self):
        """Auto End None should place None keys at clip ends.

        Note: iter_keyed_events intentionally filters out None (index 0),
        so we query cmds.keyframe directly to verify None keys exist.
        """
        self._run_key_all(auto_end=True, stagger=0)

        trigger_attr, _ = EventTriggers.attr_names("audio")
        attr_path = f"{self.loc_name}.{trigger_attr}"
        key_times = cmds.keyframe(attr_path, query=True) or []

        none_index = EventTriggers.event_index(
            pm.PyNode(self.loc_name), "None", category="audio"
        )

        none_key_times = []
        for t in sorted(key_times):
            vals = cmds.keyframe(attr_path, query=True, time=(t, t), valueChange=True)
            if vals and any(int(round(v)) == none_index for v in vals):
                none_key_times.append(float(t))

        self.assertIn(82.0, none_key_times, "Expected None key at frame 82")

    def test_key_all_disables_next_event_path(self):
        """When Key All is active, the Next Event logic should NOT execute."""
        widget = _make_tb001_widget(key_all=True, next_event=True, stagger=0)
        self.slots.ui.tb001.option_box.menu.chk_auto_end_none.isChecked.return_value = (
            False
        )

        with patch.object(
            self.slots,
            "_get_clip_length_frames",
            return_value=24.0,
        ):
            with patch.object(
                self.slots,
                "_sync_and_refresh_target",
                return_value=3,
            ):
                with patch.object(
                    self.slots,
                    "_resolve_next_event",
                ) as mock_next:
                    with patch.object(
                        self.slots,
                        "_event_names_from_files",
                        return_value=["ClipA", "ClipB", "ClipC"],
                    ):
                        pm.currentTime(0)
                        self.slots.tb001(widget=widget)
                        mock_next.assert_not_called()

    def test_key_all_status_message(self):
        """Footer should report Key All summary."""
        self._run_key_all(auto_end=False, stagger=5)

        footer_text = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("Key All", footer_text)
        self.assertIn("3 track(s)", footer_text)
        self.assertIn("stagger=5", footer_text)

    def test_key_all_auto_end_status_message(self):
        """Footer should include end-None info when auto_end is on."""
        self._run_key_all(auto_end=True, stagger=0)

        footer_text = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("end-None", footer_text)


# ===========================================================================
# Dirty tracking / fingerprint-based sync
# ===========================================================================


class TestDirtyTracking(MayaTkTestCase):
    """Verify fingerprint-based dirty tracking skips redundant syncs.

    The ``_compute_sync_fingerprint`` / ``_sync_if_dirty`` mechanism lets
    tb000 and the deferred attr-changed callback avoid rebuilding the
    composite when the keyed-event state has not changed since the last
    sync.
    Fixed: 2026-06-23
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        self.loc = pm.PyNode(cmds.spaceLocator(name="fpLoc")[0])
        EventTriggers.create([self.loc], events=["Walk", "Run"], category="audio")
        self.slots._current_target = self.loc
        self.slots._audio_files = {
            "walk": "/audio/Walk.wav",
            "run": "/audio/Run.wav",
        }

    # -- _compute_sync_fingerprint -------------------------------------------

    def test_fingerprint_empty_when_no_keys(self):
        """No keyframes → key portion of fingerprint is 'empty'."""
        fp = self.slots._compute_sync_fingerprint(self.loc)
        self.assertTrue(fp.startswith("empty|"))

    def test_fingerprint_changes_after_keying(self):
        """Keying an event should produce a different fingerprint."""
        fp_before = self.slots._compute_sync_fingerprint(self.loc)
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        fp_after = self.slots._compute_sync_fingerprint(self.loc)
        self.assertNotEqual(fp_before, fp_after)

    def test_fingerprint_stable_without_changes(self):
        """Same keyed state should always produce the same fingerprint."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        fp1 = self.slots._compute_sync_fingerprint(self.loc)
        fp2 = self.slots._compute_sync_fingerprint(self.loc)
        self.assertEqual(fp1, fp2)

    def test_fingerprint_differs_for_different_frames(self):
        """Keying the same event at a different frame → different fingerprint."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        fp_10 = self.slots._compute_sync_fingerprint(self.loc)
        EventTriggers.clear_key(self.loc, time=10, category="audio")
        EventTriggers.set_key(
            self.loc, "Walk", time=20, auto_clear=False, category="audio"
        )
        fp_20 = self.slots._compute_sync_fingerprint(self.loc)
        self.assertNotEqual(fp_10, fp_20)

    # -- _sync_if_dirty -------------------------------------------------------

    def test_sync_if_dirty_syncs_when_no_fingerprint(self):
        """First sync should always proceed (no stored fingerprint)."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        with patch.object(
            self.slots, "_sync_and_refresh_target", return_value=1
        ) as mock_sync:
            result = self.slots._sync_if_dirty(self.loc)
            mock_sync.assert_called_once_with(self.loc)
            self.assertEqual(result, 1)

    def test_sync_if_dirty_skips_when_up_to_date(self):
        """When fingerprint matches stored value, sync should be skipped."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        # Simulate a prior sync by storing the current fingerprint
        self.slots._sync_fingerprints[self.loc.name()] = (
            self.slots._compute_sync_fingerprint(self.loc)
        )
        with patch.object(
            self.slots, "_sync_and_refresh_target", return_value=1
        ) as mock_sync:
            result = self.slots._sync_if_dirty(self.loc)
            mock_sync.assert_not_called()
            self.assertEqual(result, -1)

    def test_sync_if_dirty_syncs_after_new_key(self):
        """Adding a new key should invalidate the fingerprint."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        # Store fingerprint from prior sync
        self.slots._sync_fingerprints[self.loc.name()] = (
            self.slots._compute_sync_fingerprint(self.loc)
        )
        # Now add a new key — fingerprint should differ
        EventTriggers.set_key(
            self.loc, "Run", time=30, auto_clear=False, category="audio"
        )
        with patch.object(
            self.slots, "_sync_and_refresh_target", return_value=2
        ) as mock_sync:
            result = self.slots._sync_if_dirty(self.loc)
            mock_sync.assert_called_once_with(self.loc)
            self.assertEqual(result, 2)

    # -- _sync_and_refresh_target stores fingerprint -------------------------

    def test_sync_stores_fingerprint(self):
        """After a successful sync, the fingerprint should be cached."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        self.assertNotIn(self.loc.name(), self.slots._sync_fingerprints)

        with patch.object(AudioEvents, "sync", return_value={"fpLoc": ["Walk_10"]}):
            self.slots._sync_and_refresh_target(self.loc)

        self.assertIn(self.loc.name(), self.slots._sync_fingerprints)
        # Stored fingerprint should match the current state
        expected_fp = self.slots._compute_sync_fingerprint(self.loc)
        self.assertEqual(self.slots._sync_fingerprints[self.loc.name()], expected_fp)

    def test_sync_activates_target_composite(self):
        """_sync_and_refresh_target should activate the synced target's composite."""
        with (
            patch.object(AudioEvents, "sync", return_value={"fpLoc": ["Walk_10"]}),
            patch.object(self.slots, "_activate_composite_for") as mock_activate,
        ):
            self.slots._sync_and_refresh_target(self.loc)
            mock_activate.assert_called_once_with(self.loc)

    # -- tb000 smart sync ----------------------------------------------------

    def test_tb000_reports_up_to_date(self):
        """tb000 should report 'up to date' when fingerprint matches."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        # Store matching fingerprint
        self.slots._sync_fingerprints[self.loc.name()] = (
            self.slots._compute_sync_fingerprint(self.loc)
        )

        with patch.object(
            self.slots,
            "_event_names_from_files",
            return_value=["Walk", "Run"],
        ):
            self.slots.tb000()

        footer = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("up to date", footer.lower())

    def test_tb000_syncs_when_dirty(self):
        """tb000 should sync and report clip count when state is dirty."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        # No stored fingerprint → dirty
        with (
            patch.object(
                self.slots,
                "_event_names_from_files",
                return_value=["Walk", "Run"],
            ),
            patch.object(
                self.slots,
                "_sync_and_refresh_target",
                return_value=1,
            ),
        ):
            self.slots.tb000()

        footer = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("Synced", footer)
        self.assertIn("1", footer)

    # -- _schedule_deferred_sync coalescing -----------------------------------

    def test_schedule_deferred_sync_coalesces(self):
        """Rapid calls should result in only one evalDeferred."""
        with patch("maya.cmds.evalDeferred") as mock_defer:
            self.slots._schedule_deferred_sync()
            self.slots._schedule_deferred_sync()
            self.slots._schedule_deferred_sync()
            # Only the first call goes through
            mock_defer.assert_called_once()
            self.assertTrue(self.slots._deferred_sync_pending)

    def test_run_deferred_sync_clears_pending_flag(self):
        """After _run_deferred_sync, the pending flag should be cleared."""
        self.slots._deferred_sync_pending = True
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        with patch.object(self.slots, "_sync_and_refresh_target", return_value=1):
            self.slots._run_deferred_sync()
        self.assertFalse(self.slots._deferred_sync_pending)

    # -- Scene open clears fingerprints --------------------------------------

    def test_scene_open_clears_fingerprints(self):
        """_on_scene_opened should clear the fingerprint cache."""
        self.slots._sync_fingerprints["someObj"] = "old_fp"
        self.slots._deferred_sync_pending = True

        with patch("maya.cmds.scriptJob", return_value=999):
            self.slots._on_scene_opened()

        self.assertEqual(self.slots._sync_fingerprints, {})
        self.assertFalse(self.slots._deferred_sync_pending)

    # -- Audio-file changes invalidate fingerprint ---------------------------

    def test_fingerprint_changes_after_track_replace(self):
        """Replacing a track (same stem, different path) should dirty the fingerprint."""
        EventTriggers.set_key(
            self.loc, "Walk", time=10, auto_clear=False, category="audio"
        )
        fp_before = self.slots._compute_sync_fingerprint(self.loc)
        # Replace the path for the "walk" stem
        self.slots._audio_files["walk"] = "/audio/Walk_v2.wav"
        fp_after = self.slots._compute_sync_fingerprint(self.loc)
        self.assertNotEqual(fp_before, fp_after)

    def test_fingerprint_changes_after_adding_track(self):
        """Adding a new track should dirty the fingerprint."""
        fp_before = self.slots._compute_sync_fingerprint(self.loc)
        self.slots._audio_files["jump"] = "/audio/Jump.wav"
        fp_after = self.slots._compute_sync_fingerprint(self.loc)
        self.assertNotEqual(fp_before, fp_after)

    def test_fingerprint_changes_after_removing_track(self):
        """Removing a track should dirty the fingerprint."""
        fp_before = self.slots._compute_sync_fingerprint(self.loc)
        del self.slots._audio_files["run"]
        fp_after = self.slots._compute_sync_fingerprint(self.loc)
        self.assertNotEqual(fp_before, fp_after)

    # -- _run_deferred_sync edge cases --------------------------------------

    def test_run_deferred_sync_noop_when_target_deleted(self):
        """_run_deferred_sync should silently no-op if target was deleted."""
        self.slots._deferred_sync_pending = True
        pm.delete(self.loc)
        # Should not raise
        self.slots._run_deferred_sync()
        self.assertFalse(self.slots._deferred_sync_pending)

    def test_run_deferred_sync_noop_when_no_audio_files(self):
        """_run_deferred_sync should no-op if _audio_files is empty."""
        self.slots._deferred_sync_pending = True
        self.slots._audio_files = {}
        with patch.object(self.slots, "_sync_and_refresh_target") as mock_sync:
            self.slots._run_deferred_sync()
            mock_sync.assert_not_called()
        self.assertFalse(self.slots._deferred_sync_pending)

    # -- _export_composite uses _sync_if_dirty -------------------------------

    def test_export_uses_sync_if_dirty(self):
        """_export_composite should use _sync_if_dirty, not unconditional sync."""
        self.slots._current_target = self.loc
        self.slots._audio_files = {"walk": "/audio/Walk.wav"}

        with (
            patch.object(self.slots, "_sync_if_dirty", return_value=-1) as mock_dirty,
            patch.object(AudioEvents, "list_nodes", return_value=[]),
        ):
            self.slots._export_composite()
            mock_dirty.assert_called_once_with(self.loc)

    # -- _schedule_deferred_sync skips during combo writeback ----------------

    def test_schedule_deferred_sync_skips_during_combo_writeback(self):
        """_schedule_deferred_sync should be suppressed while _syncing_combo is True."""
        self.slots._syncing_combo = True
        with patch("maya.cmds.evalDeferred") as mock_defer:
            self.slots._schedule_deferred_sync()
            mock_defer.assert_not_called()
        self.assertFalse(self.slots._deferred_sync_pending)

    # -- _find_preview_node --------------------------------------------------

    def test_find_preview_node_returns_matching_stem(self):
        """_find_preview_node should find a preview node by its stamped stem."""
        slots = _make_slots_instance()
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        node = cmds.createNode("audio", name="walk_preview", skipSelect=True)
        AudioEvents._stamp_event_attrs(node, "walk", "preview")
        cmds.sets(node, addElement=audio_set.name())

        result = slots._find_preview_node("walk")
        self.assertEqual(result, node)

    def test_find_preview_node_returns_none_for_synced(self):
        """_find_preview_node should NOT return synced nodes, only preview."""
        slots = _make_slots_instance()
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        node = cmds.createNode("audio", name="walk_10", skipSelect=True)
        AudioEvents._stamp_event_attrs(node, "walk", "synced")
        cmds.sets(node, addElement=audio_set.name())

        result = slots._find_preview_node("walk")
        self.assertIsNone(result)

    def test_find_preview_node_returns_none_when_missing(self):
        """_find_preview_node should return None when no match exists."""
        slots = _make_slots_instance()
        AudioEvents._get_or_create_set("audio", clear=True)
        result = slots._find_preview_node("nonexistent")
        self.assertIsNone(result)

    # -- _activate_composite_for single-pass ---------------------------------

    def test_activate_composite_prefers_per_object(self):
        """_activate_composite_for should prefer {objName}_composite over legacy."""
        slots = _make_slots_instance()
        loc = pm.PyNode(cmds.spaceLocator(name="compTestObj")[0])
        slots._current_target = loc

        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        legacy = cmds.createNode("audio", name="audio_composite", skipSelect=True)
        cmds.sets(legacy, addElement=audio_set.name())
        per_obj = cmds.createNode(
            "audio", name="compTestObj_composite", skipSelect=True
        )
        cmds.sets(per_obj, addElement=audio_set.name())

        with patch.object(AudioEvents, "set_active") as mock_active:
            slots._activate_composite_for(loc)
            mock_active.assert_called_once_with("compTestObj_composite")

    def test_activate_composite_falls_back_to_legacy(self):
        """_activate_composite_for should fall back to {cat}_composite."""
        slots = _make_slots_instance()
        loc = pm.PyNode(cmds.spaceLocator(name="fallbackObj")[0])
        slots._current_target = loc

        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        legacy = cmds.createNode("audio", name="audio_composite", skipSelect=True)
        cmds.sets(legacy, addElement=audio_set.name())

        with patch.object(AudioEvents, "set_active") as mock_active:
            slots._activate_composite_for(loc)
            mock_active.assert_called_once_with("audio_composite")

    # -- Export trim failure message ------------------------------------------

    def test_export_trim_failure_shows_in_footer(self):
        """When trim_silence fails, the footer should indicate the failure."""
        import tempfile

        slots = _make_slots_instance()
        loc = pm.PyNode(cmds.spaceLocator(name="trimTestObj")[0])
        slots._current_target = loc
        slots._audio_files = {"walk": "/audio/Walk.wav"}

        # Create a composite node so the export path is found
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        comp = cmds.createNode("audio", name="trimTestObj_composite", skipSelect=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(b"\x00" * 100)
        tmp.close()
        try:
            cmds.setAttr(f"{comp}.filename", tmp.name, type="string")
            cmds.sets(comp, addElement=audio_set.name())

            # Mock trim to fail, save dialog to succeed, chk_trim_silence checked
            chk = MagicMock()
            chk.isChecked.return_value = True
            slots.ui.header.menu.chk_trim_silence = chk
            slots.sb.save_file_dialog.return_value = tmp.name

            with (
                patch.object(slots, "_sync_if_dirty", return_value=-1),
                patch("shutil.copy2"),
                patch.object(
                    ptk.AudioUtils, "trim_silence", side_effect=RuntimeError("fail")
                ),
            ):
                slots._export_composite()

            footer = slots.ui.footer.setText.call_args[0][0]
            self.assertIn("trim failed", footer)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


# ===========================================================================
# Export Audio Clips
# ===========================================================================


class TestExportClips(MayaTkTestCase):
    """Verify _export_clips exports individual audio clips."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        self.loc = pm.PyNode(cmds.spaceLocator(name="clipExportObj")[0])
        EventTriggers.create([self.loc], events=["Walk", "Jump"], category="audio")
        self.slots._current_target = self.loc

    # -- _export dispatcher ---------------------------------------------------

    def test_export_dispatch_composite(self):
        """_export should delegate to _export_composite when mode is 'Composite'."""
        combo = MagicMock()
        combo.currentText.return_value = "Composite"
        self.slots.ui.header.menu.cmb_export_mode = combo
        with patch.object(self.slots, "_export_composite") as mock_comp:
            self.slots._export()
            mock_comp.assert_called_once()

    def test_export_dispatch_keyed_tracks(self):
        """_export should delegate to _export_clips(keyed_only=True) for 'Keyed Tracks'."""
        combo = MagicMock()
        combo.currentText.return_value = "Keyed Tracks"
        self.slots.ui.header.menu.cmb_export_mode = combo
        with patch.object(self.slots, "_export_clips") as mock_clips:
            self.slots._export()
            mock_clips.assert_called_once_with(keyed_only=True)

    def test_export_dispatch_all_tracks(self):
        """_export should delegate to _export_clips(keyed_only=False) for 'All Tracks'."""
        combo = MagicMock()
        combo.currentText.return_value = "All Tracks"
        self.slots.ui.header.menu.cmb_export_mode = combo
        with patch.object(self.slots, "_export_clips") as mock_clips:
            self.slots._export()
            mock_clips.assert_called_once_with(keyed_only=False)

    # -- _export_clips edge cases --------------------------------------------

    def test_export_clips_no_target(self):
        """_export_clips should show message when no target is set."""
        self.slots._current_target = None
        self.slots._audio_files = {"walk": "/audio/Walk.wav"}
        self.slots._export_clips()
        footer = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("Select an audio-trigger object", footer)

    def test_export_clips_no_files(self):
        """_export_clips should show message when no audio files are loaded."""
        self.slots._audio_files = {}
        self.slots._export_clips()
        footer = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("No audio clips", footer)

    def test_export_clips_no_keyed_events(self):
        """_export_clips should show message when no events are keyed."""
        self.slots._audio_files = {"walk": "/audio/Walk.wav"}
        self.slots._export_clips()
        footer = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("No keyed events", footer)

    def test_export_clips_copies_keyed_files_only(self):
        """_export_clips should copy only keyed clips, not unused ones."""
        import tempfile

        tmp_src = tempfile.mkdtemp()
        tmp_dst = tempfile.mkdtemp()
        try:
            # Create fake source files
            walk_path = os.path.join(tmp_src, "Walk.wav")
            jump_path = os.path.join(tmp_src, "Jump.wav")
            for p in (walk_path, jump_path):
                with open(p, "wb") as f:
                    f.write(b"\x00" * 44)

            self.slots._audio_files = {
                "walk": walk_path,
                "jump": jump_path,
            }

            # Only key Walk — Jump should NOT be exported
            EventTriggers.set_key(self.loc, event="Walk", time=5, category="audio")

            # No suffix checkbox
            self.slots.ui.header.menu.chk_suffix_time_range = None
            self.slots.sb.dir_dialog.return_value = tmp_dst

            with patch.object(
                self.slots,
                "_get_clip_length_frames",
                return_value=10.0,
            ):
                self.slots._export_clips()

            exported = os.listdir(tmp_dst)
            self.assertIn("Walk.wav", exported)
            self.assertNotIn("Jump.wav", exported)
            footer = self.slots.ui.footer.setText.call_args[0][0]
            self.assertIn("1 clip(s)", footer)
        finally:
            import shutil

            shutil.rmtree(tmp_src, ignore_errors=True)
            shutil.rmtree(tmp_dst, ignore_errors=True)

    def test_export_clips_with_time_range_suffix(self):
        """_export_clips should suffix time ranges when checkbox is enabled."""
        import tempfile

        tmp_src = tempfile.mkdtemp()
        tmp_dst = tempfile.mkdtemp()
        try:
            walk_path = os.path.join(tmp_src, "Walk.wav")
            with open(walk_path, "wb") as f:
                f.write(b"\x00" * 44)

            self.slots._audio_files = {"walk": walk_path}

            # Enable suffix checkbox
            chk = MagicMock()
            chk.isChecked.return_value = True
            self.slots.ui.header.menu.chk_suffix_time_range = chk

            # Key an event so iter_keyed_events returns data
            EventTriggers.set_key(self.loc, event="Walk", time=10, category="audio")

            self.slots.sb.dir_dialog.return_value = tmp_dst

            with patch.object(
                self.slots,
                "_get_clip_length_frames",
                return_value=30.0,
            ):
                self.slots._export_clips()

            exported = os.listdir(tmp_dst)
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0], "Walk_10-40.wav")
        finally:
            import shutil

            shutil.rmtree(tmp_src, ignore_errors=True)
            shutil.rmtree(tmp_dst, ignore_errors=True)

    def test_export_clips_multiple_occurrences(self):
        """When a clip is keyed multiple times, one copy per occurrence is exported."""
        import tempfile

        tmp_src = tempfile.mkdtemp()
        tmp_dst = tempfile.mkdtemp()
        try:
            walk_path = os.path.join(tmp_src, "Walk.wav")
            with open(walk_path, "wb") as f:
                f.write(b"\x00" * 44)

            self.slots._audio_files = {"walk": walk_path}

            chk = MagicMock()
            chk.isChecked.return_value = True
            self.slots.ui.header.menu.chk_suffix_time_range = chk

            EventTriggers.set_key(self.loc, event="Walk", time=10, category="audio")
            EventTriggers.set_key(self.loc, event="Walk", time=50, category="audio")

            self.slots.sb.dir_dialog.return_value = tmp_dst

            with patch.object(
                self.slots,
                "_get_clip_length_frames",
                return_value=20.0,
            ):
                self.slots._export_clips()

            exported = sorted(os.listdir(tmp_dst))
            self.assertEqual(len(exported), 2)
            self.assertEqual(exported[0], "Walk_10-30.wav")
            self.assertEqual(exported[1], "Walk_50-70.wav")
        finally:
            import shutil

            shutil.rmtree(tmp_src, ignore_errors=True)
            shutil.rmtree(tmp_dst, ignore_errors=True)

    def test_export_clips_dialog_cancel(self):
        """Cancelling the directory dialog should be a no-op."""
        self.slots._audio_files = {"walk": "/audio/Walk.wav"}

        # Need a keyed event to get past the early gate
        EventTriggers.set_key(self.loc, event="Walk", time=5, category="audio")

        self.slots.sb.dir_dialog.return_value = ""

        with patch.object(
            self.slots,
            "_get_clip_length_frames",
            return_value=10.0,
        ):
            self.slots._export_clips()

        # Footer should not have been updated with an export message
        calls = self.slots.ui.footer.setText.call_args_list
        has_export_msg = any("clip(s)" in str(c) for c in calls)
        self.assertFalse(has_export_msg)

    # -- All Tracks mode (keyed_only=False) -----------------------------------

    def test_export_all_tracks_includes_unkeyed(self):
        """keyed_only=False should export every loaded clip, even unkeyed ones."""
        import tempfile

        tmp_src = tempfile.mkdtemp()
        tmp_dst = tempfile.mkdtemp()
        try:
            walk_path = os.path.join(tmp_src, "Walk.wav")
            jump_path = os.path.join(tmp_src, "Jump.wav")
            for p in (walk_path, jump_path):
                with open(p, "wb") as f:
                    f.write(b"\x00" * 44)

            self.slots._audio_files = {
                "walk": walk_path,
                "jump": jump_path,
            }

            # Only key Walk — but keyed_only=False so both should export
            EventTriggers.set_key(self.loc, event="Walk", time=5, category="audio")

            self.slots.ui.header.menu.chk_suffix_time_range = None
            self.slots.sb.dir_dialog.return_value = tmp_dst

            self.slots._export_clips(keyed_only=False)

            exported = sorted(os.listdir(tmp_dst))
            self.assertIn("Walk.wav", exported)
            self.assertIn("Jump.wav", exported)
            self.assertEqual(len(exported), 2)
        finally:
            import shutil

            shutil.rmtree(tmp_src, ignore_errors=True)
            shutil.rmtree(tmp_dst, ignore_errors=True)

    def test_export_all_tracks_ignores_suffix(self):
        """keyed_only=False should never apply time-range suffix."""
        import tempfile

        tmp_src = tempfile.mkdtemp()
        tmp_dst = tempfile.mkdtemp()
        try:
            walk_path = os.path.join(tmp_src, "Walk.wav")
            with open(walk_path, "wb") as f:
                f.write(b"\x00" * 44)

            self.slots._audio_files = {"walk": walk_path}

            # Enable suffix checkbox — should still be ignored
            chk = MagicMock()
            chk.isChecked.return_value = True
            self.slots.ui.header.menu.chk_suffix_time_range = chk

            EventTriggers.set_key(self.loc, event="Walk", time=10, category="audio")

            self.slots.sb.dir_dialog.return_value = tmp_dst

            self.slots._export_clips(keyed_only=False)

            exported = os.listdir(tmp_dst)
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0], "Walk.wav")
        finally:
            import shutil

            shutil.rmtree(tmp_src, ignore_errors=True)
            shutil.rmtree(tmp_dst, ignore_errors=True)


# ===========================================================================
# Owner-attr stamping and owner-based cleanup
# ===========================================================================


class TestOwnerAttrStamping(MayaTkTestCase):
    """Verify audio_event_owner attr is stamped on synced/composite nodes.

    New owner-attr approach replaces fragile name-prefix matching for
    identifying which trigger object owns each audio node.
    Added: 2026-03-13
    """

    def setUp(self):
        super().setUp()
        self.loc = pm.PyNode(cmds.spaceLocator(name="ownerTestLoc")[0])
        EventTriggers.create(
            [self.loc], events=["Kick", "Snare"], category="audio"
        )
        EventTriggers.set_key(
            self.loc, "Kick", time=5, auto_clear=False, category="audio"
        )

    def test_sync_stamps_owner_on_synced_nodes(self):
        """Synced audio nodes must have audio_event_owner = trigger object name."""
        fake_map = {"kick": "/audio/Kick.wav"}
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value="/fake/_composite_ownerTestLoc.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            AudioEvents.sync(
                objects=[self.loc],
                audio_file_map=fake_map,
                category="audio",
            )
        for node_name in AudioEvents.list_nodes(category="audio"):
            if cmds.attributeQuery(
                AudioEvents.NODE_TYPE_ATTR, node=node_name, exists=True
            ):
                ntype = cmds.getAttr(f"{node_name}.{AudioEvents.NODE_TYPE_ATTR}") or ""
                if ntype == "synced":
                    self.assertTrue(
                        cmds.attributeQuery(
                            AudioEvents.NODE_OWNER_ATTR,
                            node=node_name,
                            exists=True,
                        ),
                        f"Synced node '{node_name}' missing owner attr.",
                    )
                    owner = cmds.getAttr(
                        f"{node_name}.{AudioEvents.NODE_OWNER_ATTR}"
                    )
                    self.assertEqual(owner, "ownerTestLoc")

    def test_sync_stamps_owner_on_composite_nodes(self):
        """Composite audio nodes must have audio_event_owner = trigger object name."""
        fake_map = {"kick": "/audio/Kick.wav"}
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value="/fake/_composite_ownerTestLoc.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            AudioEvents.sync(
                objects=[self.loc],
                audio_file_map=fake_map,
                category="audio",
            )
        comp_node = "ownerTestLoc_composite"
        self.assertTrue(
            pm.objExists(comp_node), "Composite node should exist."
        )
        self.assertTrue(
            cmds.attributeQuery(
                AudioEvents.NODE_OWNER_ATTR, node=comp_node, exists=True
            ),
        )
        self.assertEqual(
            cmds.getAttr(f"{comp_node}.{AudioEvents.NODE_OWNER_ATTR}"),
            "ownerTestLoc",
        )

    def test_preview_nodes_have_no_owner_attr(self):
        """Preview nodes (from load_tracks) should NOT have audio_event_owner."""
        import tempfile

        tmp = tempfile.mkdtemp()
        try:
            wav = os.path.join(tmp, "TestClip.wav")
            with open(wav, "wb") as f:
                f.write(b"\x00" * 44)
            with patch.object(
                AudioEvents,
                "_resolve_playable_path",
                return_value=wav,
            ):
                created = AudioEvents.load_tracks([wav], category="audio")
            self.assertTrue(len(created) > 0, "Should create preview node.")
            for node_name in created:
                self.assertFalse(
                    cmds.attributeQuery(
                        AudioEvents.NODE_OWNER_ATTR,
                        node=node_name,
                        exists=True,
                    ),
                    f"Preview node '{node_name}' should NOT have owner attr.",
                )
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stamp_event_attrs_backward_compatible(self):
        """Calling _stamp_event_attrs without owner should work (no owner attr)."""
        node = cmds.createNode("audio", name="legacyNode", skipSelect=True)
        AudioEvents._stamp_event_attrs(node, "test", "preview")
        self.assertFalse(
            cmds.attributeQuery(
                AudioEvents.NODE_OWNER_ATTR, node=node, exists=True
            ),
            "No owner attr should be stamped when owner is omitted.",
        )


class TestOwnerBasedCleanup(MayaTkTestCase):
    """Verify sync() uses owner attr for cleanup instead of name-prefix matching.

    Added: 2026-03-13
    """

    def setUp(self):
        super().setUp()
        self.locA = pm.PyNode(cmds.spaceLocator(name="cleanupA")[0])
        self.locB = pm.PyNode(cmds.spaceLocator(name="cleanupB")[0])
        EventTriggers.create([self.locA], events=["Hit"], category="audio")
        EventTriggers.create([self.locB], events=["Hit"], category="audio")
        EventTriggers.set_key(
            self.locA, "Hit", time=10, auto_clear=False, category="audio"
        )
        EventTriggers.set_key(
            self.locB, "Hit", time=20, auto_clear=False, category="audio"
        )

    def _sync_object(self, obj, obj_name):
        fake_map = {"hit": f"/audio/{obj_name}_Hit.wav"}
        with (
            patch.object(
                AudioEvents,
                "_build_audio_map_from_file_map",
                return_value=fake_map,
            ),
            patch(
                "pythontk.AudioUtils.build_composite_wav",
                return_value=f"/fake/_composite_{obj_name}.wav",
            ),
            patch.object(AudioEvents, "set_active"),
        ):
            return AudioEvents.sync(
                objects=[obj],
                audio_file_map=fake_map,
                category="audio",
            )

    def test_resync_cleans_only_owned_nodes(self):
        """Re-syncing A should remove A's old synced nodes but preserve B's."""
        self._sync_object(self.locA, "cleanupA")
        self._sync_object(self.locB, "cleanupB")

        nodes_before = AudioEvents.list_nodes(category="audio")
        b_nodes_before = [
            n for n in nodes_before
            if cmds.attributeQuery(
                AudioEvents.NODE_OWNER_ATTR, node=n, exists=True
            )
            and cmds.getAttr(f"{n}.{AudioEvents.NODE_OWNER_ATTR}") == "cleanupB"
        ]

        # Re-sync A
        self._sync_object(self.locA, "cleanupA")

        nodes_after = AudioEvents.list_nodes(category="audio")
        b_nodes_after = [
            n for n in nodes_after
            if cmds.attributeQuery(
                AudioEvents.NODE_OWNER_ATTR, node=n, exists=True
            )
            and cmds.getAttr(f"{n}.{AudioEvents.NODE_OWNER_ATTR}") == "cleanupB"
        ]

        self.assertEqual(
            len(b_nodes_before),
            len(b_nodes_after),
            "B's nodes must survive A's re-sync.",
        )

    def test_orphan_nodes_cleaned_on_sync(self):
        """Nodes whose owner was deleted should be cleaned up on next sync."""
        self._sync_object(self.locA, "cleanupA")
        self._sync_object(self.locB, "cleanupB")

        # Delete owner B
        pm.delete(self.locB)

        # Re-sync A — should also clean up B's orphaned nodes
        self._sync_object(self.locA, "cleanupA")

        nodes = AudioEvents.list_nodes(category="audio")
        for n in nodes:
            if cmds.attributeQuery(
                AudioEvents.NODE_OWNER_ATTR, node=n, exists=True
            ):
                owner = cmds.getAttr(f"{n}.{AudioEvents.NODE_OWNER_ATTR}") or ""
                self.assertNotEqual(
                    owner,
                    "cleanupB",
                    f"Orphan node '{n}' owned by deleted 'cleanupB' should be removed.",
                )

    def test_legacy_nodes_without_owner_still_cleaned(self):
        """Synced nodes without owner attr (legacy) should still be cleaned up."""
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        # Create a legacy synced node (no owner attr).
        # Use frame 99 (not keyed) so the name won't collide with
        # newly synced nodes created by sync().
        legacy = cmds.createNode("audio", name="Hit_99", skipSelect=True)
        AudioEvents._stamp_event_attrs(legacy, "hit", "synced")
        cmds.sets(legacy, addElement=audio_set.name())

        # Sync A — legacy synced node should be cleaned
        self._sync_object(self.locA, "cleanupA")

        nodes = AudioEvents.list_nodes(category="audio")
        self.assertNotIn(
            "Hit_99",
            nodes,
            "Legacy synced node without owner should be cleaned on sync.",
        )


class TestHydrateFallbackOwnerFilter(MayaTkTestCase):
    """Verify hydration fallback filters by owner attr.

    When no persisted file-map exists, the fallback reads node filenames
    from the global audio_set.  Nodes stamped with a different owner
    should be skipped.
    Added: 2026-03-13
    """

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def test_fallback_skips_nodes_owned_by_other_target(self):
        """Node with owner=targetA should not hydrate targetB."""
        targetA = pm.PyNode(cmds.spaceLocator(name="filterA")[0])
        targetB = pm.PyNode(cmds.spaceLocator(name="filterB")[0])
        EventTriggers.create([targetA], events=["Clap"], category="audio")
        EventTriggers.create([targetB], events=["Clap"], category="audio")

        # Ensure no persisted file map on B
        node_str = str(targetB)
        if cmds.attributeQuery(
            self.slots.FILE_MAP_ATTR, node=node_str, exists=True
        ):
            cmds.deleteAttr(f"{node_str}.{self.slots.FILE_MAP_ATTR}")

        # Create a preview node owned by A
        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        node = cmds.createNode("audio", name="clap", skipSelect=True)
        cmds.setAttr(f"{node}.filename", "/dirA/clap.wav", type="string")
        AudioEvents._stamp_event_attrs(node, "clap", "preview", owner="filterA")
        cmds.sets(node, addElement=audio_set.name())

        self.slots._current_target = None
        self.slots._audio_files.clear()
        self.slots._hydrate_from_target(targetB)
        self.assertNotIn(
            "clap",
            self.slots._audio_files,
            "Node owned by filterA should NOT hydrate filterB.",
        )

    def test_fallback_accepts_unowned_preview_nodes(self):
        """Preview nodes without owner attr should still be accepted."""
        target = pm.PyNode(cmds.spaceLocator(name="filterC")[0])
        EventTriggers.create([target], events=["Tap"], category="audio")

        node_str = str(target)
        if cmds.attributeQuery(
            self.slots.FILE_MAP_ATTR, node=node_str, exists=True
        ):
            cmds.deleteAttr(f"{node_str}.{self.slots.FILE_MAP_ATTR}")

        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        node = cmds.createNode("audio", name="tap", skipSelect=True)
        cmds.setAttr(f"{node}.filename", "/audio/tap.wav", type="string")
        AudioEvents._stamp_event_attrs(node, "tap", "preview")  # No owner
        cmds.sets(node, addElement=audio_set.name())

        self.slots._current_target = None
        self.slots._audio_files.clear()
        self.slots._hydrate_from_target(target)
        self.assertEqual(
            self.slots._audio_files.get("tap"),
            "/audio/tap.wav",
            "Unowned preview node should be accepted in fallback.",
        )

    def test_fallback_accepts_nodes_owned_by_same_target(self):
        """Nodes with matching owner should be accepted."""
        target = pm.PyNode(cmds.spaceLocator(name="filterD")[0])
        EventTriggers.create([target], events=["Pop"], category="audio")

        node_str = str(target)
        if cmds.attributeQuery(
            self.slots.FILE_MAP_ATTR, node=node_str, exists=True
        ):
            cmds.deleteAttr(f"{node_str}.{self.slots.FILE_MAP_ATTR}")

        audio_set = AudioEvents._get_or_create_set("audio", clear=True)
        node = cmds.createNode("audio", name="pop", skipSelect=True)
        cmds.setAttr(f"{node}.filename", "/audio/pop.wav", type="string")
        AudioEvents._stamp_event_attrs(node, "pop", "preview", owner="filterD")
        cmds.sets(node, addElement=audio_set.name())

        self.slots._current_target = None
        self.slots._audio_files.clear()
        self.slots._hydrate_from_target(target)
        self.assertEqual(
            self.slots._audio_files.get("pop"),
            "/audio/pop.wav",
            "Node owned by same target should be accepted.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
