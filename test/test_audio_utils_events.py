# !/usr/bin/python
# coding=utf-8
"""Event primitive tests — require a live Maya session.

Covers per-track attr creation, key read/write/shift/remove, track
lifecycle, and the visibility escape hatch.
"""
import os
import sys
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
from mayatk.audio_utils._audio_utils import AudioUtils

_events = _schema = _carriers = AudioUtils


class TestEnsureTrackAttr(MayaTkTestCase):
    def test_creates_carrier_if_missing(self):
        self.assertFalse(cmds.objExists(_schema.CARRIER_NODE))
        _events.ensure_track_attr("footstep")
        self.assertTrue(cmds.objExists(_schema.CARRIER_NODE))

    def test_creates_attr_with_prefix(self):
        _events.ensure_track_attr("footstep")
        self.assertTrue(
            cmds.attributeQuery(
                "audio_clip_footstep",
                node=_schema.CARRIER_NODE,
                exists=True,
            )
        )

    def test_idempotent(self):
        first = _events.ensure_track_attr("footstep")
        second = _events.ensure_track_attr("footstep")
        self.assertEqual(first, second)
        # Should still be a single attr.
        attrs = _carriers.list_track_attrs(_schema.CARRIER_NODE)
        self.assertEqual(attrs.count("audio_clip_footstep"), 1)

    def test_attr_is_enum(self):
        _events.ensure_track_attr("footstep")
        labels = cmds.attributeQuery(
            "audio_clip_footstep",
            node=_schema.CARRIER_NODE,
            listEnum=True,
        )
        self.assertIsNotNone(labels)
        self.assertIn("off", labels[0])
        self.assertIn("footstep", labels[0])

    def test_rejects_invalid_track_id(self):
        with self.assertRaises(ValueError):
            _events.ensure_track_attr("BadId")


class TestHasTrack(MayaTkTestCase):
    def test_returns_false_without_carrier(self):
        self.assertFalse(_events.has_track("footstep"))

    def test_returns_true_after_create(self):
        _events.ensure_track_attr("footstep")
        self.assertTrue(_events.has_track("footstep"))

    def test_returns_false_for_other_track(self):
        _events.ensure_track_attr("footstep")
        self.assertFalse(_events.has_track("jump"))


class TestListTracks(MayaTkTestCase):
    def test_empty_when_no_carrier(self):
        self.assertEqual(_events.list_tracks(), [])

    def test_lists_tracks_sorted(self):
        for tid in ("zebra", "alpha", "mango"):
            _events.ensure_track_attr(tid)
        self.assertEqual(_events.list_tracks(), ["alpha", "mango", "zebra"])


class TestWriteReadKeys(MayaTkTestCase):
    def test_write_creates_key(self):
        _events.write_key("footstep", frame=10, value=1)
        keys = _events.read_keys("footstep")
        self.assertEqual(len(keys), 1)
        self.assertAlmostEqual(keys[0][0], 10.0)
        self.assertAlmostEqual(keys[0][1], 1.0)

    def test_write_multiple_sorted(self):
        _events.write_key("footstep", frame=30, value=1)
        _events.write_key("footstep", frame=10, value=1)
        _events.write_key("footstep", frame=20, value=0)
        keys = _events.read_keys("footstep")
        frames = [f for f, _ in keys]
        self.assertEqual(frames, [10.0, 20.0, 30.0])

    def test_write_auto_creates_attr(self):
        self.assertFalse(_events.has_track("ambient"))
        _events.write_key("ambient", frame=5)
        self.assertTrue(_events.has_track("ambient"))

    def test_read_keys_empty_for_missing_track(self):
        self.assertEqual(_events.read_keys("missing"), [])


class TestReadEvents(MayaTkTestCase):
    def test_paired_start_stop(self):
        _events.write_key("voice", frame=10, value=1)
        _events.write_key("voice", frame=30, value=0)
        events = _events.read_events("voice")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start, 10.0)
        self.assertEqual(events[0].stop, 30.0)

    def test_start_only_has_none_stop(self):
        _events.write_key("voice", frame=10, value=1)
        events = _events.read_events("voice")
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].stop)

    def test_multiple_starts_without_stops(self):
        _events.write_key("voice", frame=10, value=1)
        _events.write_key("voice", frame=50, value=1)
        events = _events.read_events("voice")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].start, 10.0)
        self.assertIsNone(events[0].stop)
        self.assertEqual(events[1].start, 50.0)
        self.assertIsNone(events[1].stop)


class TestRemoveKey(MayaTkTestCase):
    def test_remove_existing(self):
        _events.write_key("footstep", frame=10)
        self.assertTrue(_events.remove_key("footstep", frame=10))
        self.assertEqual(_events.read_keys("footstep"), [])

    def test_remove_missing_returns_false(self):
        _events.write_key("footstep", frame=10)
        self.assertFalse(_events.remove_key("footstep", frame=20))

    def test_remove_on_nonexistent_track(self):
        self.assertFalse(_events.remove_key("ghost", frame=10))


class TestShiftKeysInRange(MayaTkTestCase):
    def test_shifts_keys_in_range(self):
        _events.write_key("footstep", frame=10)
        _events.write_key("footstep", frame=20)
        _events.write_key("footstep", frame=30)
        shifted = _events.shift_keys_in_range(old_start=15, old_end=25, delta=100)
        self.assertIn("footstep", shifted)
        frames = [f for f, _ in _events.read_keys("footstep")]
        self.assertIn(10.0, frames)
        self.assertIn(120.0, frames)  # 20 + 100
        self.assertIn(30.0, frames)

    def test_noop_for_zero_delta(self):
        _events.write_key("footstep", frame=10)
        self.assertEqual(_events.shift_keys_in_range(0, 100, 0), [])

    def test_restrict_to_specific_tracks(self):
        _events.write_key("foot", frame=10)
        _events.write_key("jump", frame=10)
        shifted = _events.shift_keys_in_range(
            old_start=5, old_end=15, delta=50, track_ids=["foot"]
        )
        self.assertEqual(shifted, ["foot"])
        self.assertEqual([f for f, _ in _events.read_keys("foot")], [60.0])
        self.assertEqual([f for f, _ in _events.read_keys("jump")], [10.0])

    def test_no_shift_outside_range(self):
        _events.write_key("footstep", frame=100)
        shifted = _events.shift_keys_in_range(0, 50, 10)
        self.assertEqual(shifted, [])
        self.assertEqual([f for f, _ in _events.read_keys("footstep")], [100.0])

    def test_shift_preserves_keys_when_new_positions_collide_with_old(self):
        """Bug: Phase 2 cut was destroying newly-placed keys when the new
        frame positions coincided with old frames still to be cut.

        Example: keys at [10, 20, 30], delta=+10 → new keys should be at
        [20, 30, 40].  Phase 2 must not cut positions 20 and 30 because
        they now hold the freshly-written keys.
        """
        _events.write_key("footstep", frame=10)
        _events.write_key("footstep", frame=20)
        _events.write_key("footstep", frame=30)
        shifted = _events.shift_keys_in_range(old_start=0, old_end=40, delta=10)
        self.assertIn("footstep", shifted)
        frames = sorted(f for f, _ in _events.read_keys("footstep"))
        self.assertEqual(frames, [20.0, 30.0, 40.0])

    def test_shift_preserves_keys_negative_delta_collision(self):
        """Mirror case: keys at [20, 30, 40], delta=-10 → [10, 20, 30]."""
        _events.write_key("footstep", frame=20)
        _events.write_key("footstep", frame=30)
        _events.write_key("footstep", frame=40)
        shifted = _events.shift_keys_in_range(old_start=0, old_end=50, delta=-10)
        self.assertIn("footstep", shifted)
        frames = sorted(f for f, _ in _events.read_keys("footstep"))
        self.assertEqual(frames, [10.0, 20.0, 30.0])


class TestDeleteTrack(MayaTkTestCase):
    def test_delete_removes_attr(self):
        _events.write_key("footstep", frame=10)
        self.assertTrue(_events.delete_track("footstep"))
        self.assertFalse(_events.has_track("footstep"))

    def test_delete_missing_returns_false(self):
        self.assertFalse(_events.delete_track("ghost"))


class TestVisibilityEscapeHatch(MayaTkTestCase):
    def test_show_and_hide_single(self):
        _events.ensure_track_attr("footstep")
        affected = _events.show_track_attrs("footstep")
        self.assertEqual(affected, ["footstep"])

        attr = f"{_schema.CARRIER_NODE}.audio_clip_footstep"
        self.assertTrue(cmds.getAttr(attr, channelBox=True))

        _events.hide_track_attrs("footstep")
        # hidden channel-box returns False; keyable also False when hidden.
        self.assertFalse(cmds.getAttr(attr, channelBox=True))

    def test_show_all_when_track_id_none(self):
        _events.ensure_track_attr("foot")
        _events.ensure_track_attr("jump")
        affected = _events.show_track_attrs()
        self.assertEqual(sorted(affected), ["foot", "jump"])


if __name__ == "__main__":
    unittest.main()
