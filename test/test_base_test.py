# !/usr/bin/python
# coding=utf-8
"""Tests for ``base_test.TestAssets``: where machine-local fixtures resolve.

The fixtures carry neutral names. A folder still staged under the old ones
resolves through its own ``legacy_aliases.json``, which sits beside the private
fixtures because the old names are client identifiers. The real fixtures are
absent in CI and on most machines, so the fallback is pinned here against a
root under ``temp_tests`` with stand-in names.
"""

import json
import os
import shutil
import unittest
from unittest import mock

import base_test
from base_test import TestAssets, asset_path


class TestLegacyAliases(unittest.TestCase):
    """A renamed fixture resolves by its neutral name first, its old name second."""

    NEUTRAL = ("scenes", "fixture_scene.ma")
    LEGACY = ("scenes", "old_scene_name.ma")

    def setUp(self):
        here = os.path.dirname(os.path.abspath(__file__))
        self.root = os.path.join(here, "temp_tests", "asset_alias_root")
        os.makedirs(os.path.join(self.root, "scenes"), exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        patch = mock.patch.object(base_test, "TEST_ASSETS", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.aliases = os.path.join(self.root, TestAssets.ALIASES_FILE)
        self._write_aliases(json.dumps({"/".join(self.NEUTRAL): "/".join(self.LEGACY)}))

    def _write_aliases(self, text):
        with open(self.aliases, "w", encoding="utf-8") as handle:
            handle.write(text)

    def _touch(self, parts):
        path = os.path.join(self.root, *parts)
        with open(path, "w", encoding="utf-8"):
            pass
        return path

    def test_the_neutral_name_wins_when_both_are_staged(self):
        neutral = self._touch(self.NEUTRAL)
        self._touch(self.LEGACY)
        self.assertEqual(asset_path(*self.NEUTRAL), neutral)

    def test_a_staged_legacy_name_resolves_while_the_folder_is_renamed(self):
        legacy = self._touch(self.LEGACY)
        self.assertEqual(asset_path(*self.NEUTRAL), legacy)

    def test_nothing_staged_yields_the_neutral_path_so_guards_skip(self):
        path = asset_path(*self.NEUTRAL)
        self.assertEqual(path, os.path.join(self.root, *self.NEUTRAL))
        self.assertFalse(os.path.exists(path))

    def test_a_root_without_a_rename_map_resolves_neutral_names_only(self):
        os.remove(self.aliases)
        self._touch(self.LEGACY)
        self.assertEqual(
            asset_path(*self.NEUTRAL), os.path.join(self.root, *self.NEUTRAL)
        )

    def test_a_rename_map_saved_with_a_byte_order_mark_still_resolves(self):
        """Windows PowerShell 5.1 writes UTF-8 with a BOM, which ``json`` rejects."""
        text = json.dumps({"/".join(self.NEUTRAL): "/".join(self.LEGACY)})
        with open(self.aliases, "w", encoding="utf-8-sig") as handle:
            handle.write(text)
        legacy = self._touch(self.LEGACY)
        self.assertEqual(asset_path(*self.NEUTRAL), legacy)

    def test_an_unreadable_rename_map_is_no_map(self):
        self._write_aliases("{not json")
        self._touch(self.LEGACY)
        self.assertEqual(
            asset_path(*self.NEUTRAL), os.path.join(self.root, *self.NEUTRAL)
        )


if __name__ == "__main__":
    unittest.main()
