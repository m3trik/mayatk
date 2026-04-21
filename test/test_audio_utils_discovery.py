# !/usr/bin/python
# coding=utf-8
"""Discovery tests for ``audio_utils.segments.discovery``.

Validates that the per-track canonical store produces correct
:class:`AudioSegment` lists for consumers (sequencer + manifest).
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
    import maya.cmds as cmds
except ImportError as exc:
    raise RuntimeError(
        "These tests must run inside a Maya session (standalone or GUI)."
    ) from exc

from base_test import MayaTkTestCase
from mayatk.audio_utils._audio_utils import AudioUtils

_events = _file_map = AudioUtils
from mayatk.audio_utils.segments import (
    AudioSegment,
    collect_all_segments,
    collect_segments_for_track,
)


_TEMP_DIR = os.path.join(scripts_dir, "mayatk", "test", "temp_tests")


def _make_wav(name: str, duration_sec: float = 0.5) -> str:
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


class TestCollectAllSegments(MayaTkTestCase):
    def test_empty_scene_returns_empty(self):
        self.assertEqual(collect_all_segments(), [])

    def test_single_track_single_start_key(self):
        wav = _make_wav("disc_single")
        _events.write_key("disc_single", frame=20)
        _file_map.set_path("disc_single", wav)

        segs = collect_all_segments(include_waveform=False)
        self.assertEqual(len(segs), 1)
        seg = segs[0]
        self.assertIsInstance(seg, AudioSegment)
        self.assertEqual(seg.track_id, "disc_single")
        self.assertEqual(seg.start, 20.0)
        self.assertEqual(seg.file_path, wav)
        self.assertGreater(seg.duration, 0.0)
        self.assertEqual(seg.end, seg.start + seg.duration)

    def test_start_stop_pair_produces_truncated_duration(self):
        wav = _make_wav("disc_stop")
        _events.write_key("disc_stop", frame=10, value=1)
        _events.write_key("disc_stop", frame=25, value=0)
        _file_map.set_path("disc_stop", wav)

        segs = collect_all_segments(include_waveform=False)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].start, 10.0)
        self.assertEqual(segs[0].end, 25.0)
        self.assertEqual(segs[0].duration, 15.0)

    def test_multiple_events_on_one_track(self):
        wav = _make_wav("disc_multi")
        _events.write_key("disc_multi", frame=10, value=1)
        _events.write_key("disc_multi", frame=20, value=0)
        _events.write_key("disc_multi", frame=50, value=1)
        _file_map.set_path("disc_multi", wav)

        segs = collect_all_segments(include_waveform=False)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].start, 10.0)
        self.assertEqual(segs[0].end, 20.0)
        self.assertEqual(segs[1].start, 50.0)

    def test_segments_sorted_by_start(self):
        wav_a = _make_wav("disc_sort_a")
        wav_b = _make_wav("disc_sort_b")
        _events.write_key("disc_sort_a", frame=50)
        _events.write_key("disc_sort_b", frame=10)
        _file_map.set_path("disc_sort_a", wav_a)
        _file_map.set_path("disc_sort_b", wav_b)

        segs = collect_all_segments(include_waveform=False)
        starts = [s.start for s in segs]
        self.assertEqual(starts, sorted(starts))

    def test_scene_range_filters_out_of_window_clips(self):
        wav = _make_wav("disc_range")
        _events.write_key("disc_range", frame=5)
        _file_map.set_path("disc_range", wav)

        # Clip ends around frame 5 + duration; force it out of window.
        segs = collect_all_segments(
            scene_start=1000, scene_end=2000, include_waveform=False
        )
        self.assertEqual(segs, [])

    def test_scene_range_keeps_in_window_clips(self):
        wav = _make_wav("disc_keep")
        _events.write_key("disc_keep", frame=100)
        _file_map.set_path("disc_keep", wav)

        segs = collect_all_segments(
            scene_start=50, scene_end=500, include_waveform=False
        )
        self.assertEqual(len(segs), 1)

    def test_track_without_file_map_skipped(self):
        _events.write_key("disc_no_path", frame=10)
        # No set_path call.
        self.assertEqual(collect_all_segments(), [])

    def test_waveform_attached_when_requested(self):
        wav = _make_wav("disc_wf")
        _events.write_key("disc_wf", frame=0)
        _file_map.set_path("disc_wf", wav)

        segs = collect_all_segments(include_waveform=True)
        self.assertEqual(len(segs), 1)
        # Envelope should be non-empty for a valid WAV.
        self.assertTrue(len(segs[0].waveform) > 0)

    def test_waveform_empty_when_disabled(self):
        wav = _make_wav("disc_nowf")
        _events.write_key("disc_nowf", frame=0)
        _file_map.set_path("disc_nowf", wav)

        segs = collect_all_segments(include_waveform=False)
        self.assertEqual(segs[0].waveform, [])


class TestCollectSegmentsForTrack(MayaTkTestCase):
    def test_returns_only_requested_track(self):
        wav_a = _make_wav("only_a")
        wav_b = _make_wav("only_b")
        _events.write_key("only_a", frame=10)
        _events.write_key("only_b", frame=20)
        _file_map.set_path("only_a", wav_a)
        _file_map.set_path("only_b", wav_b)

        segs = collect_segments_for_track("only_a", include_waveform=False)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].track_id, "only_a")

    def test_unknown_track_returns_empty(self):
        self.assertEqual(collect_segments_for_track("nonexistent"), [])


if __name__ == "__main__":
    unittest.main()
