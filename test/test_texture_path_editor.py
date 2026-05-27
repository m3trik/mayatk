# !/usr/bin/python
# coding=utf-8
"""Regression and behavioral tests for mayatk.mat_utils.texture_path_editor.

Covers:
- ``_resolve_absolute_texture_path`` (regression: 2026-05-07 PyMEL-idiom fix).
- ``_strategies_for_modes`` cascade/dedup logic.
- ``_resolve_missing_textures`` input validation.
- ``_normalize_to_relative`` semantics across path categories.
"""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.mat_utils.texture_path_editor import TexturePathEditorSlots
from mayatk.env_utils._env_utils import EnvUtils


class TestResolveAbsoluteTexturePath(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.file_node = cmds.shadingNode("file", asTexture=True, name="tpe_file")
        # Bypass __init__ — the method only needs `self`.
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)

    def test_returns_absolute_path_for_absolute_input(self):
        abs_path = os.path.abspath(__file__)
        cmds.setAttr(f"{self.file_node}.fileTextureName", abs_path, type="string")

        result = self.slot._resolve_absolute_texture_path(self.file_node)

        self.assertEqual(os.path.normcase(result), os.path.normcase(abs_path))

    def test_returns_empty_when_unset(self):
        # fileTextureName starts empty
        result = self.slot._resolve_absolute_texture_path(self.file_node)
        self.assertEqual(result, "")

    def test_does_not_crash_on_string_node(self):
        """Regression: must not raise AttributeError on a string file_node."""
        cmds.setAttr(
            f"{self.file_node}.fileTextureName", "C:/tmp/x.png", type="string"
        )
        # Bug pre-fix: AttributeError: 'str' object has no attribute 'fileTextureName'
        self.slot._resolve_absolute_texture_path(self.file_node)


class TestStrategiesForModes(unittest.TestCase):
    """Pure-logic tests for the cascade strategy pipeline."""

    def setUp(self):
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)

    def test_single_mode_stem(self):
        result = self.slot._strategies_for_modes(["stem"], index_stems=[])
        self.assertEqual(result, ["exact"])

    def test_single_mode_fuzzy(self):
        result = self.slot._strategies_for_modes(["fuzzy"], index_stems=[])
        self.assertEqual(result, ["exact", "substring", "ratio"])

    def test_texture_strategy_includes_callable(self):
        result = self.slot._strategies_for_modes(["texture"], index_stems=[])
        self.assertEqual(result[0], "exact")
        self.assertTrue(callable(result[1]))
        self.assertEqual(result[2:], ["substring", "ratio"])

    def test_cascade_dedups_exact_first_tier(self):
        # All three modes start with "exact"; pipeline must contain it once.
        result = self.slot._strategies_for_modes(
            ["stem", "texture", "fuzzy"], index_stems=[]
        )
        self.assertEqual(result.count("exact"), 1)

    def test_cascade_preserves_safest_first_order(self):
        # stem → fuzzy: stem contributes "exact"; fuzzy adds substring+ratio.
        result = self.slot._strategies_for_modes(
            ["stem", "fuzzy"], index_stems=[]
        )
        self.assertEqual(result, ["exact", "substring", "ratio"])


class TestResolveMissingValidation(MayaTkTestCase):
    """Input validation contract of _resolve_missing_textures."""

    def setUp(self):
        super().setUp()
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)

    def test_empty_modes_raises(self):
        with self.assertRaises(ValueError):
            self.slot._resolve_missing_textures(modes=[])

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            self.slot._resolve_missing_textures(modes=["bogus"])


class TestNormalizeToRelative(MayaTkTestCase):
    """Behavioral tests for _normalize_to_relative across path categories."""

    def setUp(self):
        super().setUp()
        # Sandbox sourceimages under a temp dir. Patch EnvUtils.get_env_info
        # directly rather than fight Maya's workspace state in tests.
        self.tmp_root = tempfile.mkdtemp(prefix="texture_path_editor_test_")
        self.si_dir = os.path.join(self.tmp_root, "sourceimages")
        os.makedirs(self.si_dir, exist_ok=True)

        self._original_get_env_info = EnvUtils.get_env_info

        def fake_get_env_info(key):
            if key == "sourceimages":
                return self.si_dir
            if key == "workspace":
                return self.tmp_root
            return self._original_get_env_info(key)

        EnvUtils.get_env_info = staticmethod(fake_get_env_info)

        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        self.slot._previous_paths = {}

    def tearDown(self):
        # Restore staticmethod wrapping so the class attribute descriptor type
        # matches what was there before the patch.
        EnvUtils.get_env_info = staticmethod(self._original_get_env_info)
        super().tearDown()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _make_file_node(self, name, path):
        node = cmds.shadingNode("file", asTexture=True, name=name)
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        return node

    def test_udim_path_preserved(self):
        path = os.path.join(self.si_dir, "tile_<UDIM>.png").replace("\\", "/")
        node = self._make_file_node("tex_udim", path)
        self.slot._normalize_to_relative([node], external_mode="rewrite")
        self.assertIn("<udim>", cmds.getAttr(f"{node}.fileTextureName").lower())

    def test_already_relative_is_noop(self):
        node = self._make_file_node("tex_rel", "sourceimages/foo.png")
        self.slot._normalize_to_relative([node], external_mode="rewrite")
        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/foo.png"
        )

    def test_absolute_under_sourceimages_becomes_relative(self):
        src_file = os.path.join(self.si_dir, "bar.png")
        with open(src_file, "w"):
            pass
        abs_path = src_file.replace("\\", "/")
        node = self._make_file_node("tex_abs_in", abs_path)
        self.slot._normalize_to_relative([node], external_mode="rewrite")
        result = cmds.getAttr(f"{node}.fileTextureName")
        self.assertFalse(
            os.path.isabs(result), f"Expected relative path, got {result!r}"
        )
        self.assertIn("bar.png", result)

    def test_external_absolute_left_alone_in_rewrite_mode(self):
        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "external.png")
            with open(ext_file, "w"):
                pass
            abs_path = ext_file.replace("\\", "/")
            node = self._make_file_node("tex_ext_off", abs_path)
            self.slot._normalize_to_relative([node], external_mode="rewrite")
            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), abs_path)
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_external_absolute_copied_in_copy_mode(self):
        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "external2.png")
            with open(ext_file, "w") as fh:
                fh.write("payload")
            abs_path = ext_file.replace("\\", "/")
            node = self._make_file_node("tex_ext_on", abs_path)
            self.slot._normalize_to_relative([node], external_mode="copy")
            result = cmds.getAttr(f"{node}.fileTextureName")
            self.assertFalse(os.path.isabs(result), f"Expected relative, got {result!r}")
            self.assertIn("external2.png", result)
            # Copied into sourceimages.
            self.assertTrue(os.path.exists(os.path.join(self.si_dir, "external2.png")))
            # Original still exists at external source.
            self.assertTrue(os.path.exists(ext_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_external_absolute_moved_in_move_mode(self):
        """external_mode='move' relocates the file and removes the original."""
        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "external3.png")
            with open(ext_file, "w") as fh:
                fh.write("moveme")
            abs_path = ext_file.replace("\\", "/")
            node = self._make_file_node("tex_ext_move", abs_path)
            self.slot._normalize_to_relative([node], external_mode="move")
            result = cmds.getAttr(f"{node}.fileTextureName")
            self.assertFalse(os.path.isabs(result), f"Expected relative, got {result!r}")
            self.assertIn("external3.png", result)
            # File is in sourceimages.
            self.assertTrue(os.path.exists(os.path.join(self.si_dir, "external3.png")))
            # Original is gone (moved).
            self.assertFalse(os.path.exists(ext_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_move_collision_with_same_size_removes_external(self):
        """Move + same-size collision: existing dst kept, external removed, rebind."""
        existing = os.path.join(self.si_dir, "match_move.png")
        with open(existing, "w") as fh:
            fh.write("AAAAA")

        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "match_move.png")
            with open(ext_file, "w") as fh:
                fh.write("BBBBB")  # different content, same length
            abs_path = ext_file.replace("\\", "/")
            node = self._make_file_node("tex_match_move", abs_path)

            self.slot._normalize_to_relative([node], external_mode="move")

            # Rebound to relative.
            result = cmds.getAttr(f"{node}.fileTextureName")
            self.assertFalse(os.path.isabs(result))
            self.assertIn("match_move.png", result)
            # Pre-existing file kept (no overwrite).
            with open(existing) as fh:
                self.assertEqual(fh.read(), "AAAAA")
            # External removed (move semantics, redundant since dst already exists).
            self.assertFalse(os.path.exists(ext_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_copy_collision_with_different_size_skips_rebind(self):
        """Same basename in sourceimages with different content → skip; preserve src."""
        existing = os.path.join(self.si_dir, "collide.png")
        with open(existing, "w") as fh:
            fh.write("X")

        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "collide.png")
            with open(ext_file, "w") as fh:
                fh.write("DIFFERENT CONTENT")
            abs_path = ext_file.replace("\\", "/")
            node = self._make_file_node("tex_collide", abs_path)

            self.slot._normalize_to_relative([node], external_mode="copy")

            # No silent rebind to wrong file.
            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), abs_path)
            # Pre-existing sourceimages file untouched.
            with open(existing) as fh:
                self.assertEqual(fh.read(), "X")
            # Source still on disk (copy didn't happen, nothing was moved).
            self.assertTrue(os.path.exists(ext_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_move_collision_with_different_size_preserves_external(self):
        """Move + different-size collision: skip + warn; do NOT delete external."""
        existing = os.path.join(self.si_dir, "collide_move.png")
        with open(existing, "w") as fh:
            fh.write("Y")

        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "collide_move.png")
            with open(ext_file, "w") as fh:
                fh.write("ANOTHER LONGER PAYLOAD")
            abs_path = ext_file.replace("\\", "/")
            node = self._make_file_node("tex_collide_move", abs_path)

            self.slot._normalize_to_relative([node], external_mode="move")

            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), abs_path)
            # External preserved — never delete on collision.
            self.assertTrue(os.path.exists(ext_file))
            # sourceimages file untouched.
            with open(existing) as fh:
                self.assertEqual(fh.read(), "Y")
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_copy_collision_with_same_size_rebinds_without_copying(self):
        existing = os.path.join(self.si_dir, "match.png")
        with open(existing, "w") as fh:
            fh.write("ABCDE")
        existing_mtime = os.path.getmtime(existing)

        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "match.png")
            with open(ext_file, "w") as fh:
                fh.write("FGHIJ")
            abs_path = ext_file.replace("\\", "/")
            node = self._make_file_node("tex_match", abs_path)

            self.slot._normalize_to_relative([node], external_mode="copy")

            result = cmds.getAttr(f"{node}.fileTextureName")
            self.assertFalse(os.path.isabs(result))
            self.assertIn("match.png", result)
            # Pre-existing file content preserved.
            with open(existing) as fh:
                self.assertEqual(fh.read(), "ABCDE")
            self.assertEqual(os.path.getmtime(existing), existing_mtime)
            # External preserved (copy semantics).
            self.assertTrue(os.path.exists(ext_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_invalid_external_mode_raises(self):
        with self.assertRaises(ValueError):
            self.slot._normalize_to_relative([], external_mode="bogus")


class TestSetTextureDirRelocate(MayaTkTestCase):
    """Behavioral tests for ``_set_texture_dir_flat`` relocate modes."""

    def setUp(self):
        super().setUp()
        self.tmp_root = tempfile.mkdtemp(prefix="set_dir_test_")
        self.si_dir = os.path.join(self.tmp_root, "sourceimages")
        os.makedirs(self.si_dir, exist_ok=True)

        self._original_get_env_info = EnvUtils.get_env_info

        def fake_get_env_info(key):
            if key == "sourceimages":
                return self.si_dir
            if key == "workspace":
                return self.tmp_root
            return self._original_get_env_info(key)

        EnvUtils.get_env_info = staticmethod(fake_get_env_info)
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        self.slot._previous_paths = {}

    def tearDown(self):
        EnvUtils.get_env_info = staticmethod(self._original_get_env_info)
        super().tearDown()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _make_file_node(self, name, path):
        node = cmds.shadingNode("file", asTexture=True, name=name)
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        return node

    def test_rewrite_mode_is_path_only(self):
        """rewrite: no file movement, only path updates."""
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            src_file = os.path.join(ext_dir, "tex.png")
            with open(src_file, "w") as fh:
                fh.write("payload")
            node = self._make_file_node("tex_rw", src_file.replace("\\", "/"))

            target = os.path.join(self.tmp_root, "newdir")
            os.makedirs(target, exist_ok=True)
            self.slot._set_texture_dir_flat([node], target, relocate_mode="rewrite")

            # Path points at the new dir; source file untouched.
            self.assertIn("newdir/tex.png", cmds.getAttr(f"{node}.fileTextureName").replace("\\", "/"))
            self.assertTrue(os.path.exists(src_file))
            self.assertFalse(os.path.exists(os.path.join(target, "tex.png")))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_copy_mode_duplicates_file(self):
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            src_file = os.path.join(ext_dir, "tex_copy.png")
            with open(src_file, "w") as fh:
                fh.write("payload")
            node = self._make_file_node("tex_copy", src_file.replace("\\", "/"))

            target = os.path.join(self.tmp_root, "copydir")
            os.makedirs(target, exist_ok=True)
            self.slot._set_texture_dir_flat([node], target, relocate_mode="copy")

            self.assertTrue(os.path.exists(os.path.join(target, "tex_copy.png")))
            self.assertTrue(os.path.exists(src_file))  # original preserved
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_move_mode_relocates_file(self):
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            src_file = os.path.join(ext_dir, "tex_move.png")
            with open(src_file, "w") as fh:
                fh.write("payload")
            node = self._make_file_node("tex_move", src_file.replace("\\", "/"))

            target = os.path.join(self.tmp_root, "movedir")
            os.makedirs(target, exist_ok=True)
            self.slot._set_texture_dir_flat([node], target, relocate_mode="move")

            self.assertTrue(os.path.exists(os.path.join(target, "tex_move.png")))
            self.assertFalse(os.path.exists(src_file))  # original gone
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_copy_collision_with_different_size_skips_rebind(self):
        target = os.path.join(self.tmp_root, "destdir")
        os.makedirs(target, exist_ok=True)
        # Pre-existing same-name file with different content/size at destination.
        existing = os.path.join(target, "collide.png")
        with open(existing, "w") as fh:
            fh.write("X")

        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            src_file = os.path.join(ext_dir, "collide.png")
            with open(src_file, "w") as fh:
                fh.write("DIFFERENT CONTENT")
            node = self._make_file_node("tex_collide", src_file.replace("\\", "/"))

            self.slot._set_texture_dir_flat([node], target, relocate_mode="copy")

            # File node should NOT have been rebound (collision skipped).
            self.assertEqual(
                cmds.getAttr(f"{node}.fileTextureName"),
                src_file.replace("\\", "/"),
            )
            # Pre-existing file untouched; src preserved.
            with open(existing) as fh:
                self.assertEqual(fh.read(), "X")
            self.assertTrue(os.path.exists(src_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_invalid_relocate_mode_raises(self):
        with self.assertRaises(ValueError):
            self.slot._set_texture_dir_flat([], "/anywhere", relocate_mode="bogus")


class TestMenuStateReaders(unittest.TestCase):
    """Pin the button.option_box.menu → mode contract so it can't drift silently."""

    class _FakeCombo:
        def __init__(self, idx):
            self._idx = idx

        def currentIndex(self):
            return self._idx

    class _FakeCheck:
        def __init__(self, checked):
            self._checked = checked

        def isChecked(self):
            return self._checked

    def _normalize_button(self, combo_idx):
        """Build a fake tb_normalize_paths button whose option_box.menu has cmb_external_mode."""
        menu = SimpleNamespace(cmb_external_mode=self._FakeCombo(combo_idx))
        return SimpleNamespace(option_box=SimpleNamespace(menu=menu))

    def _resolve_button(self, checks):
        """Build a fake tb_resolve_missing_textures button with three strategy checkboxes."""
        menu = SimpleNamespace(
            chk_stem=self._FakeCheck(checks[0]),
            chk_texture=self._FakeCheck(checks[1]),
            chk_fuzzy=self._FakeCheck(checks[2]),
        )
        return SimpleNamespace(option_box=SimpleNamespace(menu=menu))

    def _slot(self):
        return TexturePathEditorSlots.__new__(TexturePathEditorSlots)

    def test_normalize_mode_index_zero_is_rewrite(self):
        slot = self._slot()
        self.assertEqual(
            slot._read_normalize_external_mode(self._normalize_button(0)),
            "rewrite",
        )

    def test_normalize_mode_index_one_is_copy(self):
        slot = self._slot()
        self.assertEqual(
            slot._read_normalize_external_mode(self._normalize_button(1)),
            "copy",
        )

    def test_normalize_mode_index_two_is_move(self):
        slot = self._slot()
        self.assertEqual(
            slot._read_normalize_external_mode(self._normalize_button(2)),
            "move",
        )

    def test_normalize_mode_out_of_range_returns_safe_default(self):
        # currentIndex() returns -1 if no selection. Should fall back to the
        # first item (rewrite) rather than IndexError.
        slot = self._slot()
        self.assertEqual(
            slot._read_normalize_external_mode(self._normalize_button(-1)),
            "rewrite",
        )

    def test_resolve_all_checked_returns_full_pipeline_in_order(self):
        slot = self._slot()
        self.assertEqual(
            slot._read_resolve_modes(self._resolve_button((True, True, True))),
            ["stem", "texture", "fuzzy"],
        )

    def test_resolve_subset_preserves_safest_first_order(self):
        slot = self._slot()
        self.assertEqual(
            slot._read_resolve_modes(self._resolve_button((True, False, True))),
            ["stem", "fuzzy"],
        )

    def test_resolve_none_checked_returns_empty(self):
        slot = self._slot()
        self.assertEqual(
            slot._read_resolve_modes(self._resolve_button((False, False, False))),
            [],
        )

    def _relocate_button(self, combo_idx):
        """Button whose option_box.menu has a cmb_relocate_mode combo."""
        menu = SimpleNamespace(cmb_relocate_mode=self._FakeCombo(combo_idx))
        return SimpleNamespace(option_box=SimpleNamespace(menu=menu))

    def test_relocate_set_directory_indices(self):
        slot = self._slot()
        items = slot._RELOCATE_MODE_ITEMS
        self.assertEqual(slot._read_relocate_mode(self._relocate_button(0), items), "rewrite")
        self.assertEqual(slot._read_relocate_mode(self._relocate_button(1), items), "copy")
        self.assertEqual(slot._read_relocate_mode(self._relocate_button(2), items), "move")

    def test_relocate_find_indices(self):
        slot = self._slot()
        items = slot._FIND_MODE_ITEMS
        self.assertEqual(slot._read_relocate_mode(self._relocate_button(0), items), "copy")
        self.assertEqual(slot._read_relocate_mode(self._relocate_button(1), items), "move")

    def test_relocate_out_of_range_returns_safe_default(self):
        slot = self._slot()
        items = slot._RELOCATE_MODE_ITEMS
        # currentIndex == -1 → first item (rewrite).
        self.assertEqual(slot._read_relocate_mode(self._relocate_button(-1), items), "rewrite")


if __name__ == "__main__":
    unittest.main(verbosity=2)
