# !/usr/bin/python
# coding=utf-8
"""Regression tests for audio shifting at all shot-edit call sites.

These tests lock in the Phase 2e/step-21 bug fixes documented in the
unified-audio plan:

* `set_shot_start` — previously shifted only downstream audio, not the
  moved shot's own audio.
* `reorder_shots` — previously did not shift audio at all.
* `move_shot_to_position` — previously did not shift audio at all.
* `respace` — previously triggered one compositor sync per shot.
* `_move_shot_content` — already correct; covered here as a sanity check.

All rely on the ShotSequencer delegating to
``audio_utils.shift_keys_in_range`` via the rewired ``_shift_audio``.
"""
import os
import struct
import sys
import unittest
import wave

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    import maya.cmds as cmds  # noqa: F401
except ImportError as exc:
    raise RuntimeError("Run inside a Maya session.") from exc

from base_test import MayaTkTestCase
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.anim_utils.shots._shots import ShotStore, ShotBlock
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
    ShotSequencer,
)


_TEMP_DIR = os.path.join(scripts_dir, "mayatk", "test", "temp_tests")


def _make_wav(name: str, duration_sec: float = 0.2) -> str:
    os.makedirs(_TEMP_DIR, exist_ok=True)
    path = os.path.join(_TEMP_DIR, f"{name}.wav").replace("\\", "/")
    sr = 22050
    n = int(sr * duration_sec)
    data = struct.pack(f"<{n}h", *([0] * n))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data)
    return path


def _key_frame(track_id: str) -> float:
    """First value=1 key frame for the track (segment start)."""
    keys = audio_utils.read_keys(track_id)
    for frame, value in sorted(keys):
        if value == 1:
            return float(frame)
    raise AssertionError(f"No value=1 key found on {track_id}")


class TestSequencerAudioShift(MayaTkTestCase):
    """Audio keys shift alongside animation at every call site."""

    def setUp(self):
        super().setUp()
        ShotStore._active = None
        self.store = ShotStore()
        self.sequencer = ShotSequencer(store=self.store)

    def tearDown(self):
        ShotStore._active = None
        super().tearDown()

    # --- helpers -----------------------------------------------------------

    def _add_shot(self, start: float, end: float) -> ShotBlock:
        return self.store.define_shot(name=f"shot_{int(start)}", start=start, end=end)

    def _author_audio(self, track_id: str, frame: float) -> str:
        wav = _make_wav(f"{track_id}_src")
        with audio_utils.batch() as b:
            audio_utils.ensure_track_attr(track_id)
            audio_utils.set_path(track_id, wav)
            audio_utils.write_key(track_id, frame=frame, value=1)
            b.mark_dirty([track_id])
        return wav

    # --- set_shot_start ----------------------------------------------------

    def test_set_shot_start_shifts_this_shots_audio(self):
        shot = self._add_shot(10.0, 40.0)
        self._author_audio("narr_sss", frame=15.0)

        self.sequencer.set_shot_start(shot.shot_id, 100.0, ripple=False)

        self.assertAlmostEqual(_key_frame("narr_sss"), 105.0)

    # --- reorder_shots -----------------------------------------------------

    def test_reorder_shots_shifts_both_shots_audio(self):
        a = self._add_shot(10.0, 40.0)  # duration 30
        b = self._add_shot(60.0, 80.0)  # duration 20, gap 20
        self._author_audio("narr_a", frame=15.0)
        self._author_audio("narr_b", frame=65.0)

        self.sequencer.reorder_shots(a.shot_id, b.shot_id)

        # After swap: b takes old a start (10), a takes 10 + 20 + 20 = 50.
        # narr_a's key was at 15 (offset 5 into a) → new a.start 50 + 5 = 55.
        # narr_b's key was at 65 (offset 5 into b) → new b.start 10 + 5 = 15.
        self.assertAlmostEqual(_key_frame("narr_a"), 55.0)
        self.assertAlmostEqual(_key_frame("narr_b"), 15.0)

    # --- move_shot_to_position --------------------------------------------

    def test_move_shot_to_position_shifts_audio(self):
        s1 = self._add_shot(10.0, 30.0)  # dur 20
        s2 = self._add_shot(30.0, 50.0)  # dur 20
        s3 = self._add_shot(50.0, 80.0)  # dur 30
        self._author_audio("narr_s1", frame=15.0)
        self._author_audio("narr_s3", frame=55.0)

        # Move s3 to position 1.  New order: s3, s1, s2.
        # s3 goes to 10 (duration 30) → 10..40
        # s1 goes to 40 (duration 20) → 40..60
        # s2 goes to 60 → 60..80
        self.sequencer.move_shot_to_position(s3.shot_id, 1)

        # narr_s3 key was at 55 (offset 5 into s3 at 50) → new 10 + 5 = 15
        # narr_s1 key was at 15 (offset 5 into s1 at 10) → new 40 + 5 = 45
        self.assertAlmostEqual(_key_frame("narr_s3"), 15.0)
        self.assertAlmostEqual(_key_frame("narr_s1"), 45.0)

    # --- move_shot (already correct via _move_shot_content) ---------------

    def test_move_shot_shifts_audio(self):
        s1 = self._add_shot(10.0, 30.0)
        self._author_audio("narr_mv", frame=15.0)

        self.sequencer.move_shot(s1.shot_id, 200.0)

        self.assertAlmostEqual(_key_frame("narr_mv"), 205.0)

    # --- respace -----------------------------------------------------------

    def test_respace_shifts_audio_on_all_moved_shots(self):
        s1 = self._add_shot(10.0, 30.0)
        s2 = self._add_shot(50.0, 80.0)  # gap 20, respace collapses to 0
        self._author_audio("narr_r1", frame=15.0)
        self._author_audio("narr_r2", frame=55.0)

        # respace with gap=0, start=10 → s1 stays 10..30, s2 shifts to 30..60.
        self.sequencer.respace(gap=0, start_frame=10.0)

        self.assertAlmostEqual(_key_frame("narr_r1"), 15.0)
        self.assertAlmostEqual(_key_frame("narr_r2"), 35.0)

    # --- stop-key preservation across multi-shot ops ----------------------

    def test_reorder_preserves_stop_key_offset(self):
        """Shifting audio keys on reorder must preserve clip duration."""
        a = self._add_shot(10.0, 40.0)
        b = self._add_shot(60.0, 80.0)
        self._author_audio("narr_sk", frame=15.0)
        audio_utils.write_key("narr_sk", frame=25.0, value=0)  # 10-frame clip

        self.sequencer.reorder_shots(a.shot_id, b.shot_id)

        keys = sorted(audio_utils.read_keys("narr_sk"))
        # Both keys must have moved by the same delta.
        starts = [f for f, v in keys if v == 1]
        stops = [f for f, v in keys if v == 0]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(stops[0] - starts[0], 10.0)


if __name__ == "__main__":
    unittest.main()
