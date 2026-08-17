# !/usr/bin/python
# coding=utf-8
"""Batch orchestration tests — require a live Maya session."""
import unittest

try:
    import maya.cmds as cmds
except ImportError as exc:
    raise RuntimeError(
        "These tests must run inside a Maya session (standalone or GUI)."
    ) from exc

from base_test import MayaTkTestCase, make_temp_wav
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils


def _make_wav(name: str) -> str:
    return make_temp_wav(name, duration_sec=0.3)


class TestBatchAggregatesDirty(MayaTkTestCase):
    def test_single_sync_on_exit(self):
        wav = _make_wav("batch_single")
        audio_utils.set_path("batch_single", wav)
        with audio_utils.batch() as b:
            audio_utils.write_key("batch_single", frame=10)
            b.mark_dirty(["batch_single"])
            # Before exit, compositor should not have run yet.
            self.assertIsNone(audio_utils.find_dg_node_for_track("batch_single"))
        # On exit, compositor runs.
        self.assertIsNotNone(audio_utils.find_dg_node_for_track("batch_single"))

    def test_multiple_dirty_marks_merge(self):
        wav = _make_wav("batch_merge")
        audio_utils.set_path("foot", wav)
        audio_utils.set_path("jump", wav)
        with audio_utils.batch() as b:
            audio_utils.write_key("foot", frame=5)
            audio_utils.write_key("jump", frame=10)
            b.mark_dirty(["foot"])
            b.mark_dirty(["jump"])
        self.assertIsNotNone(audio_utils.find_dg_node_for_track("foot"))
        self.assertIsNotNone(audio_utils.find_dg_node_for_track("jump"))


class TestNestedBatch(MayaTkTestCase):
    def test_inner_defers_to_outer(self):
        wav = _make_wav("nested")
        audio_utils.set_path("nested", wav)
        with audio_utils.batch() as outer:
            audio_utils.write_key("nested", frame=10)
            outer.mark_dirty(["nested"])
            with audio_utils.batch() as inner:
                audio_utils.write_key("nested", frame=20)
                inner.mark_dirty(["nested"])
                # Inner exit should NOT have synced yet.
                pass
            self.assertIsNone(audio_utils.find_dg_node_for_track("nested"))
        self.assertIsNotNone(audio_utils.find_dg_node_for_track("nested"))


class TestBatchNoAutoSync(MayaTkTestCase):
    def test_suppresses_sync(self):
        wav = _make_wav("no_auto")
        audio_utils.set_path("no_auto", wav)
        with audio_utils.batch(auto_sync=False) as b:
            audio_utils.write_key("no_auto", frame=10)
            b.mark_dirty(["no_auto"])
        self.assertIsNone(audio_utils.find_dg_node_for_track("no_auto"))


class TestBatchFullSync(MayaTkTestCase):
    def test_mark_dirty_none_triggers_full_sync(self):
        wav = _make_wav("full_sync")
        audio_utils.set_path("full_sync", wav)
        audio_utils.write_key("full_sync", frame=10)
        with audio_utils.batch() as b:
            b.mark_dirty(None)
        self.assertIsNotNone(audio_utils.find_dg_node_for_track("full_sync"))


if __name__ == "__main__":
    unittest.main()
