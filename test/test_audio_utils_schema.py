# !/usr/bin/python
# coding=utf-8
"""Schema tests — pure-Python, no Maya required.

Covers track_id validation + derivation round-trips + reserved words.
"""
import os
import sys
import unittest

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from mayatk.audio_utils._audio_utils import AudioUtils as _schema


class TestValidateTrackId(unittest.TestCase):
    def test_accepts_lowercase_alpha(self):
        _schema.validate_track_id("footstep")

    def test_accepts_with_digits(self):
        _schema.validate_track_id("footstep_01")

    def test_accepts_underscores(self):
        _schema.validate_track_id("my_track_id")

    def test_rejects_uppercase(self):
        with self.assertRaises(ValueError):
            _schema.validate_track_id("Footstep")

    def test_rejects_leading_digit(self):
        with self.assertRaises(ValueError):
            _schema.validate_track_id("1track")

    def test_rejects_leading_underscore(self):
        with self.assertRaises(ValueError):
            _schema.validate_track_id("_track")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            _schema.validate_track_id("")

    def test_rejects_reserved_off(self):
        with self.assertRaises(ValueError):
            _schema.validate_track_id("off")

    def test_rejects_reserved_none(self):
        with self.assertRaises(ValueError):
            _schema.validate_track_id("none")

    def test_rejects_special_chars(self):
        for bad in ("foot step", "foot-step", "foot.step", "foot/step"):
            with self.assertRaises(ValueError, msg=bad):
                _schema.validate_track_id(bad)

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            _schema.validate_track_id(123)


class TestNormalizeTrackId(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(_schema.normalize_track_id("FootStep"), "footstep")

    def test_replaces_spaces(self):
        self.assertEqual(_schema.normalize_track_id("Foot Step"), "foot_step")

    def test_replaces_punctuation(self):
        self.assertEqual(_schema.normalize_track_id("foot-step.wav"), "foot_step_wav")

    def test_collapses_underscores(self):
        self.assertEqual(_schema.normalize_track_id("foot   step"), "foot_step")

    def test_strips_leading_trailing(self):
        self.assertEqual(_schema.normalize_track_id("_foot_"), "foot")

    def test_prefixes_leading_digit(self):
        self.assertEqual(_schema.normalize_track_id("1shot"), "t_1shot")

    def test_rejects_empty_result(self):
        with self.assertRaises(ValueError):
            _schema.normalize_track_id("___")

    def test_rejects_reserved_result(self):
        with self.assertRaises(ValueError):
            _schema.normalize_track_id("OFF")
        with self.assertRaises(ValueError):
            _schema.normalize_track_id("none")


class TestAttrDerivations(unittest.TestCase):
    def test_attr_for_prepends_prefix(self):
        self.assertEqual(_schema.attr_for("footstep"), "audio_clip_footstep")

    def test_attr_for_validates(self):
        with self.assertRaises(ValueError):
            _schema.attr_for("BadId")

    def test_track_id_from_attr_strips_prefix(self):
        self.assertEqual(_schema.track_id_from_attr("audio_clip_footstep"), "footstep")

    def test_track_id_from_attr_rejects_wrong_prefix(self):
        with self.assertRaises(ValueError):
            _schema.track_id_from_attr("foo_bar")

    def test_round_trip(self):
        for tid in ("footstep", "ambient_01", "voice_npc_guard"):
            self.assertEqual(_schema.track_id_from_attr(_schema.attr_for(tid)), tid)


class TestConstants(unittest.TestCase):
    def test_carrier_node_is_data_internal(self):
        self.assertEqual(_schema.CARRIER_NODE, "data_internal")

    def test_attr_prefix(self):
        self.assertEqual(_schema.ATTR_PREFIX, "audio_clip_")

    def test_marker_attr(self):
        self.assertEqual(_schema.MARKER_ATTR, "audio_node_source")

    def test_file_map_attr(self):
        self.assertEqual(_schema.FILE_MAP_ATTR, "audio_file_map")

    def test_reserved_includes_off_none_empty(self):
        self.assertIn("off", _schema.RESERVED_TRACK_IDS)
        self.assertIn("none", _schema.RESERVED_TRACK_IDS)
        self.assertIn("", _schema.RESERVED_TRACK_IDS)


if __name__ == "__main__":
    unittest.main()
