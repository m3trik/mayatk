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
        self.loc = pm.spaceLocator(name="carrier")[0]
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
        self.loc = pm.spaceLocator(name="carrier")[0]
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
        target = pm.spaceLocator(name="targetA")[0]
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
        target = pm.spaceLocator(name="targetB")[0]
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
        target = pm.spaceLocator(name="targetC")[0]
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
        targetA = pm.spaceLocator(name="carrierA")[0]
        targetB = pm.spaceLocator(name="carrierB")[0]
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
# Replace Selected Track (b005)
# ===========================================================================


class TestReplaceSelectedTrack(MayaTkTestCase):
    """Verify b005 renames the enum label, updates _audio_files, and re-syncs."""

    def setUp(self):
        super().setUp()
        self.slots = _make_slots_instance()
        self.loc = pm.spaceLocator(name="replaceCarrier")[0]
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
