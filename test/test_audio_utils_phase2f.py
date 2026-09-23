# !/usr/bin/python
# coding=utf-8
"""Tests for the new audio_utils helpers added in Phase 2f.

Covers:
- ``tracks_on_at_frame`` — playhead sampling
- ``bake_events`` — the game-export event primitive
- ``rename_track`` — attr + enum + file_map rename
- the file map's stored spelling — project-relative, resolved on read
- ``migrate_legacy_triggers`` — one-shot schema migration
"""

import json
import os
import unittest

try:
    import maya.cmds as cmds
except ImportError as exc:
    raise RuntimeError(
        "These tests must run inside a Maya session (standalone or GUI)."
    ) from exc

import pythontk as ptk
from base_test import MayaTkTestCase
from mayatk.audio_utils import migrate as _migrate
from mayatk.audio_utils._audio_utils import AudioUtils
from mayatk.node_utils.data_nodes import DataNodes

_events = _schema = _file_map = AudioUtils


# ---------------------------------------------------------------------------
# tracks_on_at_frame
# ---------------------------------------------------------------------------


class TestTracksOnAtFrame(MayaTkTestCase):
    def test_empty_carrier_returns_empty(self):
        self.assertEqual(_events.tracks_on_at_frame(10), [])

    def test_track_on_at_start_key(self):
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 10, value=1)
        self.assertEqual(_events.tracks_on_at_frame(10), ["footstep"])

    def test_track_off_before_start_key(self):
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 10, value=1)
        self.assertEqual(_events.tracks_on_at_frame(5), [])

    def test_track_off_after_stop_key(self):
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 10, value=1)
        _events.write_key("footstep", 20, value=0)
        self.assertEqual(_events.tracks_on_at_frame(25), [])

    def test_multiple_tracks_on_simultaneously(self):
        _events.ensure_track_attr("footstep")
        _events.ensure_track_attr("jump")
        _events.write_key("footstep", 10, value=1)
        _events.write_key("jump", 12, value=1)
        self.assertEqual(_events.tracks_on_at_frame(20), ["footstep", "jump"])


# ---------------------------------------------------------------------------
# bake_events
# ---------------------------------------------------------------------------


class TestBakeEvents(MayaTkTestCase):
    def test_empty_returns_empty_list(self):
        self.assertEqual(_events.bake_events(), [])

    def test_single_track_single_key(self):
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 12, value=1)
        self.assertEqual(_events.bake_events(), [(12, "footstep")])

    def test_multiple_tracks_time_ordered(self):
        _events.ensure_track_attr("footstep")
        _events.ensure_track_attr("jump")
        _events.write_key("jump", 24, value=1)
        _events.write_key("footstep", 12, value=1)
        # Output is time-ordered across tracks.
        self.assertEqual(_events.bake_events(), [(12, "footstep"), (24, "jump")])

    def test_stop_keys_excluded(self):
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 12, value=1)
        _events.write_key("footstep", 30, value=0)
        self.assertEqual(_events.bake_events(), [(12, "footstep")])

    def test_display_map_overrides_labels(self):
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 12, value=1)
        result = _events.bake_events(display_map={"footstep": "Footstep"})
        self.assertEqual(result, [(12, "Footstep")])

    def test_multiple_keys_same_track(self):
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 12, value=1)
        _events.write_key("footstep", 30, value=0)
        _events.write_key("footstep", 40, value=1)
        self.assertEqual(_events.bake_events(), [(12, "footstep"), (40, "footstep")])

    def test_frames_are_ints(self):
        """Raw Maya frames round to int — rebasing is the caller's policy."""
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 12, value=1)
        (frame, _label) = _events.bake_events()[0]
        self.assertIsInstance(frame, int)


# ---------------------------------------------------------------------------
# rename_track
# ---------------------------------------------------------------------------


class TestRenameTrack(MayaTkTestCase):
    def test_renames_attr(self):
        _events.ensure_track_attr("footstep")
        ok = _events.rename_track("footstep", "step")
        self.assertTrue(ok)
        self.assertTrue(_events.has_track("step"))
        self.assertFalse(_events.has_track("footstep"))

    def test_preserves_keys(self):
        _events.ensure_track_attr("footstep")
        _events.write_key("footstep", 12, value=1)
        _events.write_key("footstep", 30, value=0)
        _events.rename_track("footstep", "step")
        keys = _events.read_keys("step")
        frames = [f for f, _ in keys]
        self.assertEqual(frames, [12.0, 30.0])

    def test_updates_enum_label(self):
        _events.ensure_track_attr("footstep")
        _events.rename_track("footstep", "step")
        enum_str = cmds.attributeQuery(
            "audio_clip_step", node=_schema.CARRIER_NODE, listEnum=True
        )[0]
        # Per-track enums use the unified "off:on" label since the per-track
        # ID is encoded in the attribute name (audio_clip_<track_id>).
        self.assertEqual(enum_str, "off:on")

    def test_updates_file_map(self):
        _events.ensure_track_attr("footstep")
        _file_map.set_path("footstep", "/audio/foot.wav")
        _events.rename_track("footstep", "step")
        self.assertIsNone(_file_map.get_path("footstep"))
        self.assertEqual(_file_map.get_path("step"), "/audio/foot.wav")

    def test_an_explicit_carrier_keeps_its_own_file_map(self):
        """Like every track method, the map follows *carrier*; the canonical
        record is untouched."""
        other = cmds.createNode("network", name="other_audio_carrier")
        _events.ensure_track_attr("footstep", other)
        _file_map.set_path("footstep", "/audio/foot.wav", other)
        _events.rename_track("footstep", "step", other)
        self.assertEqual(_file_map.get_path("step", other), "/audio/foot.wav")
        self.assertIsNone(_file_map.get_path("footstep", other))
        self.assertEqual(_file_map.load_file_map(), {})
        self.assertTrue(_file_map.remove_path("step", other))
        self.assertEqual(_file_map.load_file_map(other), {})

    def test_same_id_is_noop(self):
        _events.ensure_track_attr("footstep")
        self.assertTrue(_events.rename_track("footstep", "footstep"))

    def test_same_id_missing_returns_false(self):
        """Regression: same-id shortcut must still validate existence."""
        self.assertFalse(_events.rename_track("ghost", "ghost"))

    def test_missing_old_returns_false(self):
        self.assertFalse(_events.rename_track("ghost", "step"))

    def test_existing_new_id_returns_false(self):
        _events.ensure_track_attr("footstep")
        _events.ensure_track_attr("jump")
        self.assertFalse(_events.rename_track("footstep", "jump"))


# ---------------------------------------------------------------------------
# migrate_legacy_triggers
# ---------------------------------------------------------------------------


class TestFileMapStoresProjectRelative(MayaTkTestCase):
    """No machine's drive layout in scene data (maintainer rule, 2026-09-19):
    a path under the project root is STORED relative to it, so a teammate's
    synced project resolves the same file, and every reader still gets an
    absolute path back.  A path outside the root stays absolute (the texture
    and lightmap-marker rule), and a write re-spells only its own entry."""

    def setUp(self):
        super().setUp()
        store = ptk.TempArtifacts("mtk_audio_file_map_test", policy="scoped")
        self.addCleanup(store.cleanup)
        self.root = store.dir_path().replace("\\", "/")
        self.proj = f"{self.root}/proj"
        os.makedirs(self.proj, exist_ok=True)
        original = cmds.workspace(q=True, rootDirectory=True)
        self.addCleanup(lambda: cmds.workspace(original, openWorkspace=True))
        cmds.workspace(self.proj, openWorkspace=True)
        _events.ensure_track_attr("vo")

    @staticmethod
    def _stored():
        return ptk.SceneRecords.AUDIO_FILE_MAP.load(DataNodes, {})

    def test_a_path_in_the_project_is_stored_root_relative(self):
        path = f"{self.proj}/sound/vo.wav"
        _file_map.set_path("vo", path)
        self.assertEqual(self._stored(), {"vo": "sound/vo.wav"})
        self.assertEqual(_file_map.get_path("vo"), path)

    def test_a_path_outside_the_project_stays_absolute(self):
        """Never a ``../`` chain: a reader resolves against whatever project
        its session has set, and a chain walks off the drive root there."""
        path = f"{self.root}/library/vo.wav"
        _file_map.set_path("vo", path)
        self.assertEqual(self._stored(), {"vo": path})
        self.assertEqual(_file_map.get_path("vo"), path)

    def test_a_write_under_another_project_leaves_the_other_entries_alone(self):
        """Review 2026-09-22: every write re-spelled the WHOLE map against the
        session's project.  An absolute entry that happens to lie under THAT
        project came back relative to it -- and read under its own project it
        named a different file, for good.  (A relative entry re-based and
        re-spelled reads back as the same string, so it cannot show this.)"""
        elsewhere = f"{self.root}/other_proj"
        os.makedirs(elsewhere, exist_ok=True)
        inside = f"{self.proj}/sound/vo.wav"
        hit = f"{elsewhere}/sound/hit.wav"  # outside this project: absolute
        _events.ensure_track_attr("hit")
        _file_map.set_path("vo", inside)
        _file_map.set_path("hit", hit)
        before = self._stored()
        self.assertEqual(before, {"vo": "sound/vo.wav", "hit": hit})
        cmds.workspace(elsewhere, openWorkspace=True)
        _events.ensure_track_attr("sfx")
        _file_map.set_path("sfx", f"{elsewhere}/sound/sfx.wav")
        stored = self._stored()
        self.assertEqual(
            {k: stored[k] for k in before}, before, "untouched entries kept"
        )
        cmds.workspace(self.proj, openWorkspace=True)
        self.assertEqual(_file_map.get_path("vo"), inside)
        self.assertEqual(_file_map.get_path("hit"), hit)

    def test_an_explicit_carrier_stores_the_same_spelling(self):
        other = cmds.createNode("network", name="other_audio_carrier")
        _events.ensure_track_attr("vo", other)
        path = f"{self.proj}/sound/vo.wav"
        _file_map.set_path("vo", path, other)
        raw = ptk.SceneRecords.AUDIO_FILE_MAP.decode(
            cmds.getAttr(f"{other}.{_schema.FILE_MAP_ATTR}")
        )
        self.assertEqual(raw, {"vo": "sound/vo.wav"})
        self.assertEqual(_file_map.get_path("vo", other), path)

    def test_a_map_written_before_the_rule_still_reads(self):
        """An absolute entry reads unchanged, and it is re-spelled only when
        its own path is written again."""
        legacy = f"{self.proj}/sound/old.wav"
        ptk.SceneRecords.AUDIO_FILE_MAP.save(DataNodes, {"vo": legacy})
        self.assertEqual(_file_map.get_path("vo"), legacy)
        _events.ensure_track_attr("sfx")
        _file_map.set_path("sfx", f"{self.proj}/sound/sfx.wav")
        self.assertEqual(self._stored(), {"vo": legacy, "sfx": "sound/sfx.wav"})
        _file_map.set_path("vo", legacy)
        self.assertEqual(self._stored()["vo"], "sound/old.wav")


class TestMigrateLegacyTriggers(MayaTkTestCase):
    def _make_legacy(self, obj, events=("Footstep", "Jump"), keys=(), file_map=None):
        """Create a legacy audio_trigger attr on *obj* with given keys.

        keys: iterable of ``(frame, label)`` tuples.  ``label`` may be
        ``"None"`` for silence keys.
        """
        cmds.addAttr(
            obj,
            longName="audio_trigger",
            attributeType="enum",
            enumName="None:" + ":".join(events),
            keyable=True,
        )
        label_to_idx = {"None": 0}
        for i, e in enumerate(events, start=1):
            label_to_idx[e] = i
        for frame, label in keys:
            cmds.setKeyframe(
                f"{obj}.audio_trigger",
                time=frame,
                value=label_to_idx.get(label, 0),
            )
        if file_map:
            cmds.addAttr(obj, longName="audio_file_map", dataType="string")
            cmds.setAttr(f"{obj}.audio_file_map", json.dumps(file_map), type="string")

    def test_detect_legacy_on_plain_obj(self):
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self.assertFalse(_migrate.Migrate.detect_legacy(obj))
        self._make_legacy(obj)
        self.assertTrue(_migrate.Migrate.detect_legacy(obj))

    def test_migrates_start_keys_to_per_track(self):
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self._make_legacy(
            obj,
            events=("Footstep", "Jump"),
            keys=[(10, "Footstep"), (20, "Jump")],
        )
        tids = _migrate.Migrate.migrate_legacy_triggers(obj)
        self.assertEqual(sorted(tids), ["footstep", "jump"])
        self.assertTrue(_events.has_track("footstep"))
        self.assertTrue(_events.has_track("jump"))
        fs_keys = _events.read_keys("footstep")
        self.assertEqual(fs_keys, [(10.0, 1.0)])

    def test_migrates_none_to_stop_key(self):
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self._make_legacy(
            obj,
            events=("Footstep",),
            keys=[(10, "Footstep"), (25, "None")],
        )
        _migrate.Migrate.migrate_legacy_triggers(obj)
        keys = _events.read_keys("footstep")
        vals = [int(v) for _, v in keys]
        self.assertEqual(vals, [1, 0])

    def test_migrates_file_map(self):
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self._make_legacy(
            obj,
            events=("Footstep",),
            keys=[(10, "Footstep")],
            file_map={"footstep": "/audio/foot.wav"},
        )
        _migrate.Migrate.migrate_legacy_triggers(obj)
        self.assertEqual(_file_map.get_path("footstep"), "/audio/foot.wav")

    def test_removes_legacy_attrs(self):
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self._make_legacy(
            obj,
            events=("Footstep",),
            keys=[(10, "Footstep")],
        )
        _migrate.Migrate.migrate_legacy_triggers(obj)
        self.assertFalse(cmds.attributeQuery("audio_trigger", node=obj, exists=True))

    def test_keep_old_attrs_flag(self):
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self._make_legacy(
            obj,
            events=("Footstep",),
            keys=[(10, "Footstep")],
        )
        _migrate.Migrate.migrate_legacy_triggers(obj, keep_old_attrs=True)
        self.assertTrue(cmds.attributeQuery("audio_trigger", node=obj, exists=True))

    def test_preserves_canonical_file_map_on_data_internal(self):
        """Migrating when obj IS the canonical carrier must not delete
        the shared audio_file_map attr."""
        from mayatk.node_utils.data_nodes import DataNodes

        carrier = DataNodes.ensure_internal()
        carrier_name = str(carrier)
        self._make_legacy(
            carrier_name,
            events=("Footstep",),
            keys=[(10, "Footstep")],
            file_map={"footstep": "/audio/foot.wav"},
        )
        _migrate.Migrate.migrate_legacy_triggers(carrier_name)
        # audio_file_map must still exist (it's the new canonical home).
        self.assertTrue(
            cmds.attributeQuery(_schema.FILE_MAP_ATTR, node=carrier_name, exists=True)
        )

    def test_no_keys_returns_empty(self):
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self._make_legacy(obj, events=("Footstep",), keys=[])
        self.assertEqual(_migrate.Migrate.migrate_legacy_triggers(obj), [])

    def test_preserves_unlocked_state(self):
        """Regression: migration must not silently lock an unlocked obj."""
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self.assertFalse(cmds.lockNode(obj, q=True, lock=True)[0])
        self._make_legacy(
            obj,
            events=("Footstep",),
            keys=[(10, "Footstep")],
        )
        _migrate.Migrate.migrate_legacy_triggers(obj)
        self.assertFalse(
            cmds.lockNode(obj, q=True, lock=True)[0],
            "Migration locked a previously unlocked node.",
        )

    def test_preserves_locked_state(self):
        obj = cmds.spaceLocator(name="legacy_obj")[0]
        self._make_legacy(
            obj,
            events=("Footstep",),
            keys=[(10, "Footstep")],
        )
        cmds.lockNode(obj, lock=True, lockName=True)
        _migrate.Migrate.migrate_legacy_triggers(obj)
        # The obj still exists (wasn't deleted) and is still locked.
        self.assertTrue(cmds.objExists(str(obj)))
        self.assertTrue(cmds.lockNode(obj, q=True, lock=True)[0])


if __name__ == "__main__":
    unittest.main()
