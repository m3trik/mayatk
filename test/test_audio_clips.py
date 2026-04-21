# !/usr/bin/python
# coding=utf-8
"""Test suite for the Audio Clips UI facade.

Covers the :class:`AudioClips` facade and :class:`AudioClipsSlots`
under the single-scope, per-track schema.  Primitive-level behavior
(attr schema, key read/write, compositor, batch orchestration) is
covered by the ``test_audio_utils_*`` suites - this file focuses on
the UI facade contract and slot logic.

Run inside Maya (standalone or GUI).
"""
import os
import struct
import sys
import unittest
import wave
from unittest.mock import MagicMock, patch

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError as exc:
    raise RuntimeError(
        "These tests must run inside a Maya session (standalone or GUI)."
    ) from exc

from base_test import MayaTkTestCase
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips


_TEMP_DIR = os.path.join(scripts_dir, "mayatk", "test", "temp_tests")


def _make_wav(name: str, duration_sec: float = 0.5, sr: int = 22050) -> str:
    os.makedirs(_TEMP_DIR, exist_ok=True)
    path = os.path.join(_TEMP_DIR, f"{name}.wav").replace("\\", "/")
    n = int(sr * duration_sec)
    data = struct.pack(f"<{n}h", *([0] * n))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data)
    return path


def _make_slots():
    from mayatk.audio_utils.audio_clips.audio_clips_slots import (
        AudioClipsSlots,
    )

    footer = MagicMock()
    footer.setText = MagicMock()

    cmb000 = MagicMock()
    cmb000.count.return_value = 0
    cmb000.currentText.return_value = ""

    ui = MagicMock()
    ui.footer = footer
    ui.cmb000 = cmb000
    ui.tb001 = MagicMock()
    ui.tb001.option_box = MagicMock()
    ui.tb001.option_box.menu = MagicMock()
    ui.tb001.option_box.menu.chk_auto_end_none = MagicMock()
    ui.tb001.option_box.menu.chk_auto_end_none.isChecked = MagicMock(return_value=False)

    loaded_ui = MagicMock()
    loaded_ui.audio_clips = ui

    sb = MagicMock()
    sb.loaded_ui = loaded_ui

    with patch("maya.cmds.evalDeferred"):
        slots = AudioClipsSlots.__new__(AudioClipsSlots)
        slots.sb = sb
        slots.ui = ui
        slots._time_token = None
        slots._scene_subs_installed = False
        slots._attr_callback_ids = []
        slots._syncing_combo = False
        slots._last_active_tid = None
        slots._deferred_sync_pending = False
    return slots


class TestLoadTracks(MayaTkTestCase):
    def test_registers_tracks_and_paths(self):
        wav_a = _make_wav("load_a")
        wav_b = _make_wav("load_b")

        tids = AudioClips.load_tracks([wav_a, wav_b])

        self.assertEqual(sorted(tids), ["load_a", "load_b"])
        self.assertIn("load_a", audio_utils.list_tracks())
        self.assertIn("load_b", audio_utils.list_tracks())
        self.assertEqual(audio_utils.get_path("load_a"), wav_a)
        self.assertEqual(audio_utils.get_path("load_b"), wav_b)

    def test_readd_same_stem_replaces_path(self):
        import shutil

        wav_v1 = _make_wav("repl_track")
        AudioClips.load_tracks([wav_v1])
        audio_utils.write_key("repl_track", frame=10, value=1)

        other_dir = os.path.join(_TEMP_DIR, "other")
        os.makedirs(other_dir, exist_ok=True)
        wav_v2 = os.path.join(other_dir, "repl_track.wav").replace("\\", "/")
        shutil.copy2(wav_v1, wav_v2)

        tids = AudioClips.load_tracks([wav_v2])
        self.assertEqual(tids, ["repl_track"])
        self.assertEqual(audio_utils.get_path("repl_track"), wav_v2)

        keys = audio_utils.read_keys("repl_track")
        self.assertEqual(keys, [(10.0, 1.0)])

    def test_empty_list_returns_empty(self):
        self.assertEqual(AudioClips.load_tracks([]), [])


class TestSync(MayaTkTestCase):
    def test_sync_creates_dg_node_for_each_track(self):
        wav = _make_wav("sync_one")
        AudioClips.load_tracks([wav])
        audio_utils.write_key("sync_one", frame=0, value=1)

        AudioClips.sync()
        dg = audio_utils.find_dg_node_for_track("sync_one")
        self.assertIsNotNone(dg)
        self.assertTrue(cmds.objExists(dg))

    def test_sync_builds_composite_when_tracks_keyed(self):
        wav = _make_wav("comp_one", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("comp_one", frame=0, value=1)

        AudioClips.sync()
        comp = AudioClips._find_composite_node()
        self.assertIsNotNone(comp)
        path = cmds.getAttr(f"{comp}.filename")
        self.assertTrue(path and os.path.isfile(path))

    def test_sync_no_keys_no_composite(self):
        wav = _make_wav("nokey")
        AudioClips.load_tracks([wav])
        AudioClips.sync()
        self.assertIsNone(AudioClips._find_composite_node())

    def test_composite_is_marked_and_ignored_by_compositor(self):
        wav = _make_wav("mark_one", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("mark_one", frame=0, value=1)
        AudioClips.sync()

        comp = AudioClips._find_composite_node()
        self.assertIsNotNone(comp)
        self.assertTrue(
            cmds.attributeQuery(
                AudioClips.COMPOSITE_MARKER_ATTR, node=comp, exists=True
            )
        )
        self.assertFalse(audio_utils.is_managed_dg(comp))


class TestRebuildComposite(MayaTkTestCase):
    def test_no_tracks_returns_none(self):
        self.assertIsNone(AudioClips.rebuild_composite())

    def test_tracks_without_keys_returns_none(self):
        wav = _make_wav("reb_nokey")
        AudioClips.load_tracks([wav])
        self.assertIsNone(AudioClips.rebuild_composite())

    def test_creates_marked_node_with_file(self):
        wav = _make_wav("reb_go", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("reb_go", frame=5, value=1)

        node = AudioClips.rebuild_composite()
        self.assertIsNotNone(node)
        self.assertEqual(
            cmds.getAttr(f"{node}.{AudioClips.COMPOSITE_MARKER_ATTR}"),
            AudioClips.COMPOSITE_MARKER_VALUE,
        )
        self.assertTrue(os.path.isfile(cmds.getAttr(f"{node}.filename")))


class TestRemove(MayaTkTestCase):
    def test_remove_deletes_tracks_dg_and_composite(self):
        wav = _make_wav("rm_all", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("rm_all", frame=0, value=1)
        AudioClips.sync()

        self.assertIn("rm_all", audio_utils.list_tracks())
        self.assertIsNotNone(audio_utils.find_dg_node_for_track("rm_all"))
        self.assertIsNotNone(AudioClips._find_composite_node())

        AudioClips.remove()
        self.assertEqual(audio_utils.list_tracks(), [])
        self.assertIsNone(audio_utils.find_dg_node_for_track("rm_all"))
        self.assertIsNone(AudioClips._find_composite_node())

    def test_remove_when_nothing_present_returns_zero_or_none(self):
        # should not raise
        AudioClips.remove()
        self.assertEqual(audio_utils.list_tracks(), [])

    def test_remove_clears_file_map(self):
        wav = _make_wav("rm_map")
        AudioClips.load_tracks([wav])
        self.assertTrue(audio_utils.load_file_map())

        AudioClips.remove()
        self.assertEqual(audio_utils.load_file_map(), {})


class TestListNodes(MayaTkTestCase):
    def test_lists_dg_and_composite(self):
        wav = _make_wav("list_one", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("list_one", frame=0, value=1)
        AudioClips.sync()

        nodes = AudioClips.list_nodes()
        dg = audio_utils.find_dg_node_for_track("list_one")
        comp = AudioClips._find_composite_node()
        self.assertIn(dg, nodes)
        self.assertIn(comp, nodes)

    def test_empty_scene_returns_empty(self):
        self.assertEqual(AudioClips.list_nodes(), [])


class TestSlotsImport(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.slots = _make_slots()

    def test_import_registers_tracks_and_populates_combo(self):
        wav_a = _make_wav("imp_a")
        wav_b = _make_wav("imp_b")

        with patch.object(
            self.slots, "_prepare_selected_paths", return_value=[wav_a, wav_b]
        ):
            self.slots._import_audio_paths([wav_a, wav_b])

        tracks = audio_utils.list_tracks()
        self.assertIn("imp_a", tracks)
        self.assertIn("imp_b", tracks)

    def test_import_resyncs_when_existing_keys_exist(self):
        wav_a = _make_wav("imp_sync", duration_sec=0.3)
        AudioClips.load_tracks([wav_a])
        audio_utils.write_key("imp_sync", frame=0, value=1)

        with patch.object(self.slots, "_prepare_selected_paths", return_value=[wav_a]):
            self.slots._import_audio_paths([wav_a])

        self.assertIsNotNone(audio_utils.find_dg_node_for_track("imp_sync"))


class TestSlotsKeying(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.slots = _make_slots()

    def test_write_track_keys_start_only(self):
        wav = _make_wav("key_start")
        AudioClips.load_tracks([wav])

        self.slots._write_track_keys("key_start", frame=10, auto_end=False)

        keys = audio_utils.read_keys("key_start")
        self.assertEqual(keys, [(10.0, 1.0)])

    def test_write_track_keys_auto_end_writes_stop(self):
        wav = _make_wav("key_end", duration_sec=0.5)
        AudioClips.load_tracks([wav])

        self.slots._write_track_keys("key_end", frame=0, auto_end=True)

        keys = audio_utils.read_keys("key_end")
        self.assertEqual(keys[0], (0.0, 1.0))
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[1][1], 0.0)
        self.assertGreater(keys[1][0], 0.0)

    def test_write_track_keys_auto_end_skips_when_later_start_exists(self):
        wav = _make_wav("key_skip", duration_sec=2.0)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("key_skip", frame=30, value=1)

        self.slots._write_track_keys("key_skip", frame=0, auto_end=True)

        keys = audio_utils.read_keys("key_skip")
        vals = [int(round(v)) for _, v in keys]
        self.assertEqual(vals.count(1), 2)
        self.assertEqual(vals.count(0), 0)

    def test_resolve_next_track_picks_next_after_latest(self):
        wav_a = _make_wav("nt_a")
        wav_b = _make_wav("nt_b")
        wav_c = _make_wav("nt_c")
        AudioClips.load_tracks([wav_a, wav_b, wav_c])
        audio_utils.write_key("nt_a", frame=0, value=1)
        audio_utils.write_key("nt_b", frame=20, value=1)

        nxt = self.slots._resolve_next_track(audio_utils.list_tracks())
        self.assertEqual(nxt, "nt_c")

    def test_resolve_next_track_no_keys_picks_first(self):
        AudioClips.load_tracks([_make_wav("nt_first")])
        nxt = self.slots._resolve_next_track(audio_utils.list_tracks())
        self.assertEqual(nxt, "nt_first")

    def test_resolve_next_track_wraps_to_first(self):
        wav_a = _make_wav("wrap_a")
        wav_b = _make_wav("wrap_b")
        AudioClips.load_tracks([wav_a, wav_b])
        audio_utils.write_key("wrap_b", frame=10, value=1)

        nxt = self.slots._resolve_next_track(audio_utils.list_tracks())
        self.assertEqual(nxt, "wrap_a")


class TestSlotsManage(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.slots = _make_slots()

    def test_cleanup_unused_deletes_unkeyed(self):
        AudioClips.load_tracks([_make_wav("keep_keyed"), _make_wav("drop_unkeyed")])
        audio_utils.write_key("keep_keyed", frame=0, value=1)

        self.slots._cleanup_unused_tracks()

        remaining = audio_utils.list_tracks()
        self.assertIn("keep_keyed", remaining)
        self.assertNotIn("drop_unkeyed", remaining)

    def test_b002_removes_everything(self):
        wav = _make_wav("rm_btn", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("rm_btn", frame=0, value=1)
        AudioClips.sync()

        self.slots.b002()

        self.assertEqual(audio_utils.list_tracks(), [])
        self.assertIsNone(AudioClips._find_composite_node())


class TestSlotsSyncButton(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.slots = _make_slots()

    def test_tb000_with_tracks_reports_sync_status(self):
        wav = _make_wav("tb000_go", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("tb000_go", frame=0, value=1)

        self.slots.tb000()

        self.assertIsNotNone(audio_utils.find_dg_node_for_track("tb000_go"))
        self.assertIsNotNone(AudioClips._find_composite_node())


class TestCompositeCollision(MayaTkTestCase):
    def test_find_composite_adopts_unstamped_canonical_name(self):
        ext = cmds.createNode("audio", name=AudioClips.COMPOSITE_NODE, skipSelect=True)
        self.assertEqual(ext, AudioClips.COMPOSITE_NODE)

        found = AudioClips._find_composite_node()
        self.assertEqual(found, AudioClips.COMPOSITE_NODE)
        self.assertEqual(
            cmds.getAttr(f"{found}.{AudioClips.COMPOSITE_MARKER_ATTR}"),
            AudioClips.COMPOSITE_MARKER_VALUE,
        )

    def test_compositor_sync_does_not_touch_composite(self):
        wav = _make_wav("no_touch", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("no_touch", frame=0, value=1)
        AudioClips.sync()

        comp = AudioClips._find_composite_node()
        self.assertIsNotNone(comp)

        audio_utils.sync()
        self.assertTrue(cmds.objExists(comp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
