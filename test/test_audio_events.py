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
        self.loc = pm.spaceLocator(name="carrier")[0]

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
        grp = pm.group(empty=True, name="emptyGrp")
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
        self.loc = pm.spaceLocator(name="carrier")[0]

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
        self.loc = pm.spaceLocator(name="carrier")[0]
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
        self.loc = pm.spaceLocator(name="carrier")[0]
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
        grp = pm.group(empty=True, name="grp")
        EventTriggers._protect_empty_transforms([grp])
        shapes = cmds.listRelatives(str(grp), shapes=True, fullPath=True) or []
        self.assertTrue(shapes, "Shape should be added")
        shp = shapes[0]
        vis = cmds.getAttr(f"{shp}.visibility")
        self.assertEqual(vis, 0)

    def test_locator_shape_has_zero_scale(self):
        grp = pm.group(empty=True, name="grp")
        EventTriggers._protect_empty_transforms([grp])
        shapes = cmds.listRelatives(str(grp), shapes=True, fullPath=True) or []
        shp = shapes[0]
        for axis in ("X", "Y", "Z"):
            self.assertEqual(cmds.getAttr(f"{shp}.localScale{axis}"), 0)

    def test_locator_shape_stamp_attr_present(self):
        grp = pm.group(empty=True, name="grp")
        EventTriggers._protect_empty_transforms([grp])
        shapes = cmds.listRelatives(str(grp), shapes=True, fullPath=True) or []
        shp = shapes[0]
        has_attr = cmds.attributeQuery(
            EventTriggers._LOCATOR_ATTR, node=shp, exists=True
        )
        self.assertTrue(has_attr)

    def test_skips_objects_that_already_have_shapes(self):
        loc = pm.spaceLocator(name="hasShape")[0]
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
        loc = pm.spaceLocator(name="carrier")[0]
        EventTriggers.create([loc], events=["A"], category="audio")
        pm.select(loc, replace=True)
        result = self.slots._require_target()
        self.assertEqual(str(result), str(loc))

    # -- 2. Selected object without trigger --------------------------------

    def test_selection_without_trigger_returns_selected(self):
        cube = pm.polyCube(name="myCube")[0]
        pm.select(cube, replace=True)
        result = self.slots._require_target()
        self.assertEqual(str(result), str(cube))

    # -- 3. Nothing selected — uses cache ---------------------------------

    def test_no_selection_returns_cached_target(self):
        loc = pm.spaceLocator(name="cached")[0]
        self.slots._current_target = loc
        pm.select(clear=True)
        result = self.slots._require_target()
        self.assertEqual(str(result), str(loc))

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
        grp = pm.group(empty=True, name="toolGrp")
        EventTriggers._protect_empty_transforms([grp])
        self.assertTrue(self.slots._is_tool_created_carrier(grp))

    def test_mesh_object_is_not_tool_created(self):
        cube = pm.polyCube(name="meshCube")[0]
        self.assertFalse(self.slots._is_tool_created_carrier(cube))

    def test_locator_from_spacelocator_cmd_is_not_tool_created(self):
        """A regular spaceLocator has no stamp attr — not tool-created."""
        loc = pm.spaceLocator(name="userLoc")[0]
        self.assertFalse(self.slots._is_tool_created_carrier(loc))

    def test_shapeless_object_is_not_tool_created(self):
        """After EventTriggers.remove() the locator shape may be gone."""
        grp = pm.group(empty=True, name="bare")
        self.assertFalse(self.slots._is_tool_created_carrier(grp))


class TestGetSelectedTriggerObject(MayaTkTestCase):
    """_get_selected_trigger_object() finds trigger attr reliably."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()

    def test_direct_selection_match(self):
        loc = pm.spaceLocator(name="carrier")[0]
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
        cube = pm.polyCube(name="cube")[0]
        pm.select(cube, replace=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNone(result)

    def test_long_path_node_still_matched(self):
        """
        Bug: cmds.attributeQuery with long-path names (|nodeName) can return
        False even when the attribute exists on some Maya versions.
        Fixed: uses short_name = long_name.rsplit('|', 1)[-1]
        """
        parent = pm.group(empty=True, name="parentGrp")
        child = pm.spaceLocator(name="childCarrier")[0]
        pm.parent(child, parent)
        EventTriggers.create([child], events=["A"], category="audio")
        pm.select(child, replace=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNotNone(
            result, "Should find trigger on nested object via short name"
        )

    def test_shape_selection_walks_to_parent_transform(self):
        """Selecting a locator *shape* should resolve to the parent transform."""
        loc = pm.spaceLocator(name="carrier")[0]
        EventTriggers.create([loc], events=["A"], category="audio")
        shape = pm.listRelatives(loc, shapes=True)[0]
        pm.select(shape, replace=True)
        result = self.slots._get_selected_trigger_object()
        self.assertIsNotNone(result)
        self.assertEqual(str(result), str(loc))

    def test_nested_trigger_walks_up_hierarchy(self):
        """Trigger on a grandparent is found by walking the full DAG upward."""
        grandparent = pm.group(empty=True, name="gp")
        parent = pm.group(empty=True, name="par")
        child = pm.spaceLocator(name="child")[0]
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
        self.loc = pm.spaceLocator(name="carrier")[0]

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
        old = pm.spaceLocator(name="old_carrier")[0]
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
        loc = pm.spaceLocator(name="carrier")[0]
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
        carrier = pm.spaceLocator(name="carrier")[0]
        EventTriggers.create([carrier], events=["A"], category="audio")
        self.slots._current_target = carrier
        cube = pm.polyCube(name="cube")[0]
        pm.select(cube, replace=True)
        self.slots._sync_from_selection()
        # Footer should mention "keeping" the old target, not clear it
        footer_text = self.slots.ui.footer.setText.call_args[0][0]
        self.assertIn("keeping", footer_text)

    def test_nothing_selected_uses_cached_target(self):
        carrier = pm.spaceLocator(name="carrier")[0]
        EventTriggers.create([carrier], events=["A"], category="audio")
        self.slots._current_target = carrier
        pm.select(clear=True)
        with patch.object(self.slots, "_hydrate_from_target") as mock_hydrate:
            self.slots._sync_from_selection()
            mock_hydrate.assert_called_once()

    def test_nothing_selected_scans_scene_for_carrier(self):
        """
        Bug: After scene reload with no selection, the tool showed 'no trigger'.
        Fixed: scans scene with cmds.ls('*.audio_trigger') when nothing selected.
        """
        carrier = pm.spaceLocator(name="scanCarrier")[0]
        EventTriggers.create([carrier], events=["A"], category="audio")
        self.slots._current_target = None
        pm.select(clear=True)
        with patch.object(self.slots, "_hydrate_from_target") as mock_hydrate:
            self.slots._sync_from_selection()
            mock_hydrate.assert_called_once()
            found_obj = mock_hydrate.call_args[0][0]
            self.assertEqual(str(found_obj), str(carrier))

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
        carrier = pm.spaceLocator(name="carrier")[0]
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
        """_ensure_sync_job registers persistent SceneOpened/NewSceneOpened jobs."""
        self.slots._scene_opened_job_id = None
        self.slots._new_scene_job_id = None
        self.slots._selection_sync_job_id = None
        self.slots._time_changed_job_id = None

        with patch("maya.cmds.evalDeferred"):
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

        # Clean up scriptJobs
        for jid in [
            self.slots._scene_opened_job_id,
            self.slots._new_scene_job_id,
            self.slots._selection_sync_job_id,
            self.slots._time_changed_job_id,
        ]:
            if jid is not None:
                try:
                    cmds.scriptJob(kill=jid, force=True)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
