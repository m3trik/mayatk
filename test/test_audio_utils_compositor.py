# !/usr/bin/python
# coding=utf-8
"""Compositor tests — require a live Maya session.

Covers create / update / delete diff, marker-based matching
(rename-safe), idempotence, and orphan cleanup.
"""
import os
import struct
import sys
import wave
import unittest

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    import maya.cmds as cmds
except ImportError as exc:
    raise RuntimeError(
        "These tests must run inside a Maya session (standalone or GUI)."
    ) from exc

from base_test import MayaTkTestCase
from mayatk.audio_utils import compositor as _compositor
from mayatk.audio_utils._audio_utils import AudioUtils

_events = _schema = _file_map = AudioUtils


_TEMP_DIR = os.path.join(scripts_dir, "mayatk", "test", "temp_tests")


def _make_wav(name: str, duration_sec: float = 0.3) -> str:
    os.makedirs(_TEMP_DIR, exist_ok=True)
    path = os.path.join(_TEMP_DIR, f"{name}.wav")
    sr = 22050
    n = int(sr * duration_sec)
    data = struct.pack(f"<{n}h", *([0] * n))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data)
    return path


class TestSyncCreate(MayaTkTestCase):
    def test_creates_dg_node_for_track(self):
        wav = _make_wav("comp_create")
        _events.write_key("comp_create", frame=15)
        _file_map.set_path("comp_create", wav)

        result = _compositor.sync()
        self.assertEqual(len(result["created"]), 1)
        node = result["created"][0]
        self.assertTrue(cmds.objExists(node))
        self.assertEqual(cmds.nodeType(node), "audio")

    def test_created_node_has_marker(self):
        wav = _make_wav("comp_marker")
        _events.write_key("comp_marker", frame=0)
        _file_map.set_path("comp_marker", wav)
        _compositor.sync()
        node = _compositor.find_dg_node_for_track("comp_marker")
        self.assertIsNotNone(node)
        self.assertTrue(_compositor.is_managed_dg(node))
        self.assertEqual(cmds.getAttr(f"{node}.{_schema.MARKER_ATTR}"), "comp_marker")

    def test_offset_matches_first_start_key(self):
        wav = _make_wav("comp_offset")
        _events.write_key("comp_offset", frame=42, value=1)
        _file_map.set_path("comp_offset", wav)
        _compositor.sync()
        node = _compositor.find_dg_node_for_track("comp_offset")
        self.assertAlmostEqual(cmds.getAttr(f"{node}.offset"), 42.0)

    def test_skips_track_without_file_map(self):
        _events.write_key("no_file", frame=10)
        result = _compositor.sync()
        self.assertEqual(result["created"], [])


class TestSyncIdempotence(MayaTkTestCase):
    def test_repeated_sync_same_result(self):
        wav = _make_wav("idem")
        _events.write_key("idem", frame=10)
        _file_map.set_path("idem", wav)

        _compositor.sync()
        before = cmds.ls(type="audio")
        r2 = _compositor.sync()
        after = cmds.ls(type="audio")

        self.assertEqual(sorted(before), sorted(after))
        self.assertEqual(r2["created"], [])
        self.assertEqual(r2["updated"], [])


class TestSyncUpdate(MayaTkTestCase):
    def test_shifts_existing_node_offset(self):
        wav = _make_wav("upd_offset")
        _events.write_key("upd_offset", frame=10)
        _file_map.set_path("upd_offset", wav)
        _compositor.sync()

        # Shift the key to frame 100.
        _events.shift_keys_in_range(0, 20, delta=90)
        result = _compositor.sync()
        self.assertIn(
            _compositor.find_dg_node_for_track("upd_offset"), result["updated"]
        )
        node = _compositor.find_dg_node_for_track("upd_offset")
        self.assertAlmostEqual(cmds.getAttr(f"{node}.offset"), 100.0)

    def test_rename_safe_via_marker(self):
        wav = _make_wav("rename_safe")
        _events.write_key("rename_safe", frame=10)
        _file_map.set_path("rename_safe", wav)
        _compositor.sync()

        node = _compositor.find_dg_node_for_track("rename_safe")
        cmds.rename(node, "user_renamed_this")
        # Sync should find it via marker and not create a duplicate.
        _events.shift_keys_in_range(0, 20, delta=5)
        _compositor.sync()
        self.assertEqual(
            len([n for n in cmds.ls(type="audio") if _compositor.is_managed_dg(n)]),
            1,
        )
        self.assertEqual(
            _compositor.find_dg_node_for_track("rename_safe"),
            "user_renamed_this",
        )


class TestSyncDelete(MayaTkTestCase):
    def test_delete_on_track_removal(self):
        wav = _make_wav("del_track")
        _events.write_key("del_track", frame=10)
        _file_map.set_path("del_track", wav)
        _compositor.sync()
        node = _compositor.find_dg_node_for_track("del_track")
        self.assertTrue(cmds.objExists(node))

        _events.delete_track("del_track")
        result = _compositor.sync()
        self.assertIn(node, result["deleted"])
        self.assertFalse(cmds.objExists(node))

    def test_delete_on_all_keys_removed(self):
        wav = _make_wav("del_keys")
        _events.write_key("del_keys", frame=10)
        _file_map.set_path("del_keys", wav)
        _compositor.sync()
        node = _compositor.find_dg_node_for_track("del_keys")

        _events.remove_key("del_keys", frame=10)
        _compositor.sync()
        self.assertFalse(cmds.objExists(node))


class TestUnmanagedNodesIgnored(MayaTkTestCase):
    def test_unmarked_nodes_untouched(self):
        wav = _make_wav("user_auth")
        # User creates their own audio node (no marker).
        user_node = cmds.createNode("audio", name="user_auth", skipSelect=True)
        cmds.setAttr(f"{user_node}.filename", wav, type="string")
        cmds.setAttr(f"{user_node}.offset", 77.0)

        # Compositor sync with no tracks defined — must not delete it.
        _compositor.sync()
        self.assertTrue(cmds.objExists(user_node))
        self.assertAlmostEqual(cmds.getAttr(f"{user_node}.offset"), 77.0)


class TestTargetedSync(MayaTkTestCase):
    def test_tracks_arg_limits_scope(self):
        wav = _make_wav("target_a")
        _events.write_key("target_a", frame=10)
        _file_map.set_path("target_a", wav)
        _events.write_key("target_b", frame=20)
        _file_map.set_path("target_b", wav)

        result = _compositor.sync(tracks=["target_a"])
        self.assertEqual(len(result["created"]), 1)
        self.assertIsNotNone(_compositor.find_dg_node_for_track("target_a"))
        self.assertIsNone(_compositor.find_dg_node_for_track("target_b"))


if __name__ == "__main__":
    unittest.main()
