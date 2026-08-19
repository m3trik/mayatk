# !/usr/bin/python
# coding=utf-8
"""Regression and behavioral tests for mayatk.mat_utils.texture_path_editor.

Covers:
- ``_to_absolute`` (regression: 2026-08-04 sourceimages-doubling fix).
- ``_strategies_for_modes`` cascade/dedup logic.
- ``_resolve_missing_textures`` input validation.
- ``_normalize_to_relative`` semantics across path categories.
- ``_make_paths_absolute`` semantics (inverse of Normalize Paths).
"""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import maya.cmds as cmds
import pythontk as ptk

from base_test import MayaTkTestCase
from mayatk.mat_utils.texture_path_editor import TexturePathEditorSlots
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils


class TestToAbsolute(unittest.TestCase):
    """Pure-string tests for ``_to_absolute`` (no scene needed).

    Replaces the tests for the caller-less ``_resolve_absolute_texture_path``,
    whose relative branch was never exercised and joined against *sourceimages*
    instead of the workspace root — reproduced 2026-08-04 as
    ``C:/proj/sourceimages/sourceimages/foo.png``.
    """

    def setUp(self):
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)

    def test_absolute_input_passes_through(self):
        abs_path = os.path.abspath(__file__)
        result = self.slot._to_absolute(abs_path, "C:/proj")
        self.assertEqual(os.path.normcase(result), os.path.normcase(abs_path))

    def test_empty_returns_empty(self):
        self.assertEqual(self.slot._to_absolute("", "C:/proj"), "")

    def test_relative_resolves_against_workspace_not_sourceimages(self):
        """Regression: sourceimages must not be doubled."""
        result = self.slot._to_absolute("sourceimages/foo.png", "C:/proj")
        self.assertEqual(result, "C:/proj/sourceimages/foo.png")
        self.assertNotIn("sourceimages/sourceimages", result)

    def test_relative_without_workspace_is_left_alone(self):
        # No project set — don't fabricate a root; the caller's exists() check
        # then simply fails, which is the honest answer.
        self.assertEqual(self.slot._to_absolute("sourceimages/foo.png", ""), "sourceimages/foo.png")

    def test_result_is_forward_slashed(self):
        result = self.slot._to_absolute("sub\\tex.png", "C:\\proj")
        self.assertNotIn("\\", result)
        self.assertEqual(result, "C:/proj/sub/tex.png")

    def test_udim_token_survives_the_join(self):
        result = self.slot._to_absolute("sourceimages/tile_<UDIM>.png", "C:/proj")
        self.assertIn("<UDIM>", result)


class TestProjectRelativeConverter(unittest.TestCase):
    """``_project_relative_converter`` must only emit round-trippable paths."""

    def setUp(self):
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        self._original_get_env_info = EnvUtils.get_env_info

    def tearDown(self):
        EnvUtils.get_env_info = staticmethod(self._original_get_env_info)

    def _patch_env(self, workspace, sourceimages):
        EnvUtils.get_env_info = staticmethod(
            lambda k: {"workspace": workspace, "sourceimages": sourceimages}.get(k, "")
        )

    def test_sourceimages_under_root_relativizes(self):
        self._patch_env("C:/proj", "C:/proj/sourceimages")
        result = self.slot._project_relative_converter()("C:/proj/sourceimages/foo.png")
        self.assertEqual(result, "sourceimages/foo.png")

    def test_out_of_project_sourceimages_stays_absolute(self):
        """Regression: an absolute ``sourceImages`` rule pointing outside the
        project produced ``shared/foo.png`` — i.e. ``<proj>/shared/foo.png``,
        which resolves to nothing. Reproduced 2026-08-04 via Workspace.load.
        """
        self._patch_env("C:/proj", "D:/shared")
        result = self.slot._project_relative_converter()("D:/shared/foo.png")
        self.assertTrue(
            os.path.isabs(result), f"Expected an absolute path, got {result!r}"
        )
        self.assertEqual(result, "D:/shared/foo.png")

    def test_relative_form_round_trips_through_to_absolute(self):
        self._patch_env("C:/proj", "C:/proj/sourceimages")
        rel = self.slot._project_relative_converter()("C:/proj/sourceimages/a/b.png")
        self.assertEqual(
            self.slot._to_absolute(rel, "C:/proj"), "C:/proj/sourceimages/a/b.png"
        )

    def test_path_outside_sourceimages_stays_absolute(self):
        self._patch_env("C:/proj", "C:/proj/sourceimages")
        result = self.slot._project_relative_converter()("C:/elsewhere/foo.png")
        self.assertEqual(result, "C:/elsewhere/foo.png")


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

    def _repoint_sourceimages(self, si_dir):
        """Point the fake env's sourceimages rule at *si_dir* (workspace unchanged)."""
        os.makedirs(si_dir, exist_ok=True)
        self.si_dir = si_dir

    def test_nested_sourceimages_rule_still_becomes_relative(self):
        """Regression: a rule below the root left every path absolute.

        The relative form used to be built against sourceimages and re-prefixed
        with only its *basename*, so ``<proj>/assets/sourceimages/x.png`` became
        ``sourceimages/x.png`` — resolving to nothing. The round-trip guard then
        refused it and handed back the absolute path: Normalize did nothing.
        """
        self._repoint_sourceimages(os.path.join(self.tmp_root, "assets", "sourceimages"))
        src_file = os.path.join(self.si_dir, "nested.png")
        with open(src_file, "w"):
            pass
        node = self._make_file_node("tex_nested", src_file.replace("\\", "/"))

        self.slot._normalize_to_relative([node], external_mode="rewrite")

        result = cmds.getAttr(f"{node}.fileTextureName")
        self.assertEqual(result, "assets/sourceimages/nested.png")
        # The relative form has to resolve back to the file Maya was given.
        self.assertTrue(os.path.exists(os.path.join(self.tmp_root, result)))

    def test_in_project_outside_sourceimages_becomes_relative(self):
        """Under the project root is the set of paths that HAVE a relative form."""
        other = os.path.join(self.tmp_root, "renders")
        os.makedirs(other, exist_ok=True)
        src_file = os.path.join(other, "plate.png")
        with open(src_file, "w"):
            pass
        node = self._make_file_node("tex_in_proj", src_file.replace("\\", "/"))

        self.slot._normalize_to_relative([node], external_mode="rewrite")

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "renders/plate.png")

    def test_in_project_file_is_never_relocated(self):
        """copy/move act on *external* textures; an in-project one just repaths."""
        other = os.path.join(self.tmp_root, "renders")
        os.makedirs(other, exist_ok=True)
        src_file = os.path.join(other, "keep.png")
        with open(src_file, "w"):
            pass
        node = self._make_file_node("tex_keep", src_file.replace("\\", "/"))

        self.slot._normalize_to_relative([node], external_mode="move")

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "renders/keep.png")
        self.assertTrue(os.path.exists(src_file))

    def test_sibling_root_prefix_is_not_inside_the_project(self):
        """``<root>2/x.png`` shares the root's prefix but is not under it."""
        sibling = self.tmp_root + "2"
        os.makedirs(sibling, exist_ok=True)
        try:
            src_file = os.path.join(sibling, "outside.png")
            with open(src_file, "w"):
                pass
            abs_path = src_file.replace("\\", "/")
            node = self._make_file_node("tex_sibling", abs_path)

            self.slot._normalize_to_relative([node], external_mode="rewrite")

            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), abs_path)
        finally:
            shutil.rmtree(sibling, ignore_errors=True)

    def test_out_of_project_sourceimages_never_moves_a_file_onto_itself(self):
        """An absolute rule outside the root makes dst == src; move must not delete."""
        outside_si = tempfile.mkdtemp(prefix="external_sourceimages_")
        try:
            self._repoint_sourceimages(outside_si)
            src_file = os.path.join(outside_si, "self.png")
            with open(src_file, "w") as fh:
                fh.write("DATA")
            abs_path = src_file.replace("\\", "/")
            node = self._make_file_node("tex_self", abs_path)

            self.slot._normalize_to_relative([node], external_mode="move")

            self.assertTrue(os.path.exists(src_file), "move deleted the source file")
            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), abs_path)
        finally:
            shutil.rmtree(outside_si, ignore_errors=True)


class TestMakePathsAbsolute(MayaTkTestCase):
    """Behavioral tests for _make_paths_absolute (inverse of Normalize Paths)."""

    def setUp(self):
        super().setUp()
        self.tmp_root = tempfile.mkdtemp(prefix="make_abs_test_")
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

    def test_relative_becomes_absolute_under_workspace(self):
        node = self._make_file_node("tex_mabs_rel", "sourceimages/foo.png")
        self.slot._make_paths_absolute([node])
        result = cmds.getAttr(f"{node}.fileTextureName")
        self.assertTrue(os.path.isabs(result), f"Expected absolute, got {result!r}")
        expected = os.path.normpath(os.path.join(self.tmp_root, "sourceimages/foo.png"))
        self.assertEqual(os.path.normpath(result), expected)

    def test_absolute_path_untouched(self):
        abs_path = os.path.join(self.si_dir, "bar.png").replace("\\", "/")
        node = self._make_file_node("tex_mabs_abs", abs_path)
        self.slot._make_paths_absolute([node])
        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), abs_path)

    def test_empty_path_skipped(self):
        node = cmds.shadingNode("file", asTexture=True, name="tex_mabs_empty")
        self.slot._make_paths_absolute([node])
        self.assertFalse(cmds.getAttr(f"{node}.fileTextureName"))

    def test_udim_token_preserved(self):
        node = self._make_file_node("tex_mabs_udim", "sourceimages/tile_<UDIM>.png")
        self.slot._make_paths_absolute([node])
        result = cmds.getAttr(f"{node}.fileTextureName")
        self.assertTrue(os.path.isabs(result))
        self.assertIn("<udim>", result.lower())

    def test_missing_file_still_rewritten(self):
        # A relative path whose file doesn't exist is still absolutized —
        # the absolute form points where Maya would have looked.
        node = self._make_file_node("tex_mabs_missing", "sourceimages/gone.png")
        self.slot._make_paths_absolute([node])
        self.assertTrue(os.path.isabs(cmds.getAttr(f"{node}.fileTextureName")))

    def test_previous_path_recorded(self):
        node = self._make_file_node("tex_mabs_prev", "sourceimages/baz.png")
        self.slot._make_paths_absolute([node])
        self.assertEqual(
            self.slot._previous_paths.get(node), "sourceimages/baz.png"
        )

    def test_round_trip_with_normalize(self):
        node = self._make_file_node("tex_mabs_round", "sourceimages/round.png")
        self.slot._make_paths_absolute([node])
        self.assertTrue(os.path.isabs(cmds.getAttr(f"{node}.fileTextureName")))
        self.slot._normalize_to_relative([node], external_mode="rewrite")
        result = cmds.getAttr(f"{node}.fileTextureName")
        self.assertFalse(os.path.isabs(result), f"Expected relative, got {result!r}")
        self.assertIn("round.png", result)


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

    def test_move_does_not_delete_a_texture_already_in_the_target_dir(self):
        """Regression: absolute backslash path already at the destination.

        ``_set_texture_dir_flat`` used to return an absolute ``old_path``
        verbatim while ``new_abs`` was forward-slashed, so the "already
        there?" guard never matched. The node became a relocation source,
        the destination "collision" was itself, the sizes matched — and move
        mode ran ``os.remove`` on the very file it was repathing to.
        Verified 2026-08-04: ``os.path.samefile(src, dst)`` was True.
        """
        target = os.path.join(self.tmp_root, "already")
        os.makedirs(target, exist_ok=True)
        tex = os.path.join(target, "there.png")
        with open(tex, "w") as fh:
            fh.write("payload")

        # Stored the way Maya hands back a Windows path: absolute, backslashes.
        node = self._make_file_node("tex_already", tex.replace("/", "\\"))
        self.slot._set_texture_dir_flat([node], target, relocate_mode="move")

        self.assertTrue(
            os.path.exists(tex), "move deleted the texture already at the destination"
        )
        with open(tex) as fh:
            self.assertEqual(fh.read(), "payload")

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

    def _slot_with_header(self, menu):
        """Build a slot whose ui.header.menu is *menu* (None = menu not built yet)."""
        slot = self._slot()
        slot.ui = SimpleNamespace(header=SimpleNamespace(menu=menu))
        return slot

    def test_exclude_arnold_off_returns_no_pattern(self):
        slot = self._slot_with_header(
            SimpleNamespace(chk_exclude_arnold=self._FakeCheck(False))
        )
        self.assertIsNone(slot._exclude_arnold_pattern())

    def test_exclude_arnold_on_returns_arnold_classification(self):
        slot = self._slot_with_header(
            SimpleNamespace(chk_exclude_arnold=self._FakeCheck(True))
        )
        self.assertEqual(slot._exclude_arnold_pattern(), "rendernode/arnold*")

    def test_exclude_arnold_before_menu_is_built_returns_no_pattern(self):
        """A refresh that beats header_init must not raise."""
        self.assertIsNone(self._slot_with_header(None)._exclude_arnold_pattern())
        self.assertIsNone(
            self._slot_with_header(SimpleNamespace())._exclude_arnold_pattern()
        )

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


class TestPathTruncationWiring(unittest.TestCase):
    """The header's Truncate Texture Paths toggle drives the path column only.

    Display-only by construction: the slot never rewrites cell text, it hands
    uitk a per-column *display* length, so every reader of the cell (edit
    write-back, Select Absolute Paths, the tooltip) keeps the full path.
    """

    class _FakeTable:
        """Stand-in for uitk's TableWidget truncation surface."""

        def __init__(self):
            self.calls = []

        def set_column_truncation(
            self, col, length=None, mode="start", insert="..", head=None
        ):
            self.calls.append((col, length, mode, insert, head))

    def _slot(self, checked=None):
        """Slot whose header menu carries the toggle (None = menu not built yet)."""
        slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        chk = None if checked is None else SimpleNamespace(isChecked=lambda: checked)
        menu = SimpleNamespace(chk_truncate_paths=chk)
        slot.ui = SimpleNamespace(header=SimpleNamespace(menu=menu))
        return slot

    def test_enabled_truncates_the_path_column_from_the_start(self):
        slot, table = self._slot(checked=True), self._FakeTable()
        slot._apply_path_truncation(table)
        self.assertEqual(
            table.calls,
            [
                (
                    1,
                    TexturePathEditorSlots._PATH_TRUNCATE_LENGTH,
                    "path",
                    "…",
                    TexturePathEditorSlots._PATH_TRUNCATE_HEAD,
                )
            ],
        )

    def test_head_is_capped_so_the_filename_end_gets_the_budget(self):
        """The path's tail identifies the texture; the drive alone opens it."""
        self.assertEqual(TexturePathEditorSlots._PATH_TRUNCATE_HEAD, 1)
        shown = ptk.truncate(
            "O:/Cloud/Projects/jets/c130j/sourceimages/textures/c130j_body_DIFF.png",
            TexturePathEditorSlots._PATH_TRUNCATE_LENGTH,
            "path",
            "…",
            head=TexturePathEditorSlots._PATH_TRUNCATE_HEAD,
        )
        self.assertTrue(shown.startswith("O:/…/"))
        self.assertTrue(shown.endswith("/sourceimages/textures/c130j_body_DIFF.png"))

    def test_disabled_clears_the_truncation(self):
        slot, table = self._slot(checked=False), self._FakeTable()
        slot._apply_path_truncation(table)
        self.assertEqual(table.calls, [(1, None, "path", "…", 1)])

    def test_header_menu_not_built_yet_reads_as_disabled(self):
        slot, table = self._slot(checked=None), self._FakeTable()
        slot._apply_path_truncation(table)  # must not raise
        self.assertEqual(table.calls, [(1, None, "path", "…", 1)])
        self.assertFalse(slot._truncate_paths_enabled())

    def test_ellipsis_marker_not_a_parent_dir_lookalike(self):
        """".." would read as a parent-directory segment in a path column."""
        slot, table = self._slot(checked=True), self._FakeTable()
        slot._apply_path_truncation(table)
        self.assertNotEqual(table.calls[0][3], "..")

    def test_no_table_yet_is_a_no_op(self):
        """A restored checkbox state can toggle before tbl000 is loaded."""
        slot = self._slot(checked=True)
        slot.ui = SimpleNamespace(header=slot.ui.header)  # no tbl000
        slot._apply_path_truncation()  # must not raise


class TestOverLongPathWarning(unittest.TestCase):
    """The header's Warn On Over-Long Paths toggle, and the limit it reads."""

    def _slot(self, checked=None):
        slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        chk = None if checked is None else SimpleNamespace(isChecked=lambda: checked)
        menu = SimpleNamespace(chk_warn_path_length=chk)
        slot.ui = SimpleNamespace(header=SimpleNamespace(menu=menu))
        return slot

    def test_toggle_state_is_read(self):
        self.assertTrue(self._slot(checked=True)._warn_path_length_enabled())
        self.assertFalse(self._slot(checked=False)._warn_path_length_enabled())

    def test_header_menu_not_built_yet_warns_by_default(self):
        """Opposite default to truncation: an early refresh must not skip it."""
        self.assertTrue(self._slot(checked=None)._warn_path_length_enabled())

    def test_limit_comes_from_the_shared_primitive(self):
        """One helper backs this and the Scene Exporter's check — no local copy."""
        limit = ptk.FileUtils.path_length_limit()
        self.assertIsInstance(limit, int)
        self.assertGreater(limit, 0)
        over = "C:/" + ("dir/" * limit) + "t.png"
        self.assertTrue(ptk.FileUtils.exceeds_path_length(over))


class _NullProgress:
    """``sb.progress`` stand-in yielding a no-op tick callable."""

    def __enter__(self):
        return lambda *a, **kw: True

    def __exit__(self, *exc):
        return False


class TestFindAndCopySourceAndDestinationOptions(MayaTkTestCase):
    """Find & Copy's two dialog-skipping option-box toggles.

    ``Use Valid Paths As Source`` (default on) sources every node whose path
    already resolves from that file, so the SOURCE dialog only opens for what
    is unresolved. ``Always Relocate To sourceimages`` (default off) pins the
    destination, so the DESTINATION dialog never opens. With both engaged and
    every path valid, the operation runs without a single browser.
    """

    def setUp(self):
        super().setUp()
        self.tmp_root = tempfile.mkdtemp(prefix="find_copy_test_")
        self.si_dir = os.path.join(self.tmp_root, "sourceimages")
        self.ext_dir = os.path.join(self.tmp_root, "external")
        self.dest_dir = os.path.join(self.tmp_root, "dest")
        for d in (self.si_dir, self.ext_dir, self.dest_dir):
            os.makedirs(d, exist_ok=True)

        self._original_get_env_info = EnvUtils.get_env_info

        def fake_get_env_info(key):
            if key == "sourceimages":
                return self.si_dir
            if key == "workspace":
                return self.tmp_root
            return self._original_get_env_info(key)

        EnvUtils.get_env_info = staticmethod(fake_get_env_info)

        self.dialog_titles = []  # every dir_dialog title, in order
        self.dialog_returns = []  # scripted answers ("" == Cancel)

        def fake_dir_dialog(title="", start_dir=""):
            self.dialog_titles.append(title)
            return self.dialog_returns.pop(0) if self.dialog_returns else ""

        self.sb = SimpleNamespace(
            dir_dialog=fake_dir_dialog,
            progress=lambda *a, **kw: _NullProgress(),
            progress_adapter=lambda update: None,
        )
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        self.slot.sb = self.sb
        self.slot.ui = SimpleNamespace(tbl000=SimpleNamespace(init_slot=lambda: None))
        self.slot._previous_paths = {}
        self.slot._find_copy_in_progress = False

    def tearDown(self):
        EnvUtils.get_env_info = staticmethod(self._original_get_env_info)
        super().tearDown()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _write(self, directory, name, payload="payload"):
        path = os.path.join(directory, name).replace("\\", "/")
        with open(path, "w") as fh:
            fh.write(payload)
        return path

    def _make_file_node(self, name, path):
        node = cmds.shadingNode("file", asTexture=True, name=name)
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        return node

    def _path_of(self, node):
        return (cmds.getAttr(f"{node}.fileTextureName") or "").replace("\\", "/")

    # -- _partition_resolved_sources -----------------------------------------

    def test_partition_splits_resolving_paths_from_the_rest(self):
        good = self._make_file_node("tex_good", self._write(self.ext_dir, "good.png"))
        gone = self._make_file_node(
            "tex_gone", os.path.join(self.ext_dir, "gone.png").replace("\\", "/")
        )
        resolved, unresolved = self.slot._partition_resolved_sources([good, gone])

        self.assertEqual(list(resolved), ["good.png"])
        self.assertTrue(os.path.isfile(resolved["good.png"]))
        self.assertEqual(unresolved, [gone])

    def test_partition_resolves_a_relative_path_against_the_workspace(self):
        self._write(self.si_dir, "rel.png")
        node = self._make_file_node("tex_rel", "sourceimages/rel.png")
        resolved, unresolved = self.slot._partition_resolved_sources([node])

        self.assertEqual(unresolved, [])
        self.assertEqual(
            os.path.normcase(resolved["rel.png"]),
            os.path.normcase(os.path.join(self.si_dir, "rel.png").replace("\\", "/")),
        )

    def test_partition_sends_udim_nodes_to_the_search(self):
        """A UDIM path is never a literal file; only the walk expands it."""
        self._write(self.ext_dir, "t.1001.png")
        node = self._make_file_node(
            "tex_udim",
            os.path.join(self.ext_dir, "t.<UDIM>.png").replace("\\", "/"),
        )
        resolved, unresolved = self.slot._partition_resolved_sources([node])

        self.assertEqual(resolved, {})
        self.assertEqual(unresolved, [node])

    # -- dialog skipping ------------------------------------------------------

    def test_all_paths_valid_opens_only_the_destination_dialog(self):
        node = self._make_file_node("tex_v", self._write(self.ext_dir, "valid.png"))
        self.dialog_returns = [self.dest_dir]

        self.slot._do_find_and_copy_workflow([node], relocate_mode="copy")

        self.assertEqual(len(self.dialog_titles), 1, self.dialog_titles)
        self.assertIn("DESTINATION", self.dialog_titles[0])
        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "valid.png")))
        self.assertTrue(self._path_of(node).endswith("dest/valid.png"))

    def test_sourceimages_destination_opens_no_dialog_at_all(self):
        node = self._make_file_node("tex_si", self._write(self.ext_dir, "auto.png"))

        self.slot._do_find_and_copy_workflow(
            [node], relocate_mode="copy", dest_sourceimages=True
        )

        self.assertEqual(self.dialog_titles, [])
        self.assertTrue(os.path.exists(os.path.join(self.si_dir, "auto.png")))
        # Inside the project → repathed relative.
        self.assertEqual(self._path_of(node), "sourceimages/auto.png")

    def test_toggle_off_searches_even_when_every_path_is_valid(self):
        node = self._make_file_node("tex_off", self._write(self.ext_dir, "srch.png"))
        self.dialog_returns = [self.ext_dir, self.dest_dir]

        self.slot._do_find_and_copy_workflow(
            [node], relocate_mode="copy", use_valid_paths=False
        )

        self.assertEqual(len(self.dialog_titles), 2, self.dialog_titles)
        self.assertIn("SOURCE", self.dialog_titles[0])
        self.assertIn("DESTINATION", self.dialog_titles[1])
        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "srch.png")))

    def test_dialog_titles_name_the_direction_of_the_folder(self):
        """The two pickers are the same widget — only the title tells them apart."""
        node = self._make_file_node(
            "tex_t", os.path.join(self.ext_dir, "missing.png").replace("\\", "/")
        )
        self._write(self.ext_dir, "missing.png")  # findable, but path set before write
        self.dialog_returns = [self.ext_dir, self.dest_dir]

        self.slot._do_find_and_copy_workflow(
            [node], relocate_mode="move", use_valid_paths=False
        )

        search, dest = self.dialog_titles
        self.assertIn("SEARCH", search)
        self.assertNotIn("DESTINATION", search)
        self.assertIn("MOVE", dest)
        self.assertIn("INTO", dest)
        self.assertNotIn("SEARCH", dest)

    def test_cancelling_the_search_keeps_the_valid_ones(self):
        """48-of-50 valid: cancelling the search skips 2, it doesn't abort 48."""
        good = self._make_file_node("tex_k", self._write(self.ext_dir, "keep.png"))
        gone = self._make_file_node(
            "tex_m", os.path.join(self.ext_dir, "nope.png").replace("\\", "/")
        )
        self.dialog_returns = ["", self.dest_dir]  # Cancel search, pick dest

        self.slot._do_find_and_copy_workflow([good, gone], relocate_mode="copy")

        self.assertEqual(len(self.dialog_titles), 2)
        self.assertIn("Cancel = skip them", self.dialog_titles[0])
        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "keep.png")))
        self.assertTrue(self._path_of(good).endswith("dest/keep.png"))
        self.assertTrue(self._path_of(gone).endswith("external/nope.png"))

    def test_cancelling_the_search_with_nothing_valid_aborts(self):
        gone = self._make_file_node(
            "tex_a", os.path.join(self.ext_dir, "absent.png").replace("\\", "/")
        )
        self.dialog_returns = [""]

        self.slot._do_find_and_copy_workflow([gone], relocate_mode="copy")

        self.assertEqual(len(self.dialog_titles), 1)  # never reached destination
        self.assertNotIn("Cancel = skip them", self.dialog_titles[0])

    # -- repath bookkeeping ---------------------------------------------------

    def test_repath_records_the_previous_path_for_the_tooltip(self):
        """Every other path command feeds ``_previous_paths``; this one skipped it."""
        node = self._make_file_node("tex_prev", self._write(self.ext_dir, "prev.png"))
        before = self._path_of(node)
        self.dialog_returns = [self.dest_dir]

        self.slot._do_find_and_copy_workflow([node], relocate_mode="copy")

        self.assertEqual(self.slot._previous_paths.get(node), before)

    def test_a_path_that_is_already_final_is_not_rewritten(self):
        """Re-running over a pinned destination must not dirty every plug.

        With the destination pinned to sourceimages, the second run finds every
        texture already there and already stored in its final relative form —
        rewriting it would reload every texture and report them all as remapped.
        """
        self._write(self.si_dir, "settled.png")
        node = self._make_file_node("tex_settled", "sourceimages/settled.png")

        # Count the writes rather than inferring from the result: the stored
        # path is identical either way, so only the plug write itself tells a
        # skipped rewrite from one that happened to land on the same string.
        writes = []
        original_set_attr = cmds.setAttr

        def counting_set_attr(plug, *args, **kwargs):
            if str(plug).endswith(".fileTextureName"):
                writes.append(str(plug))
            return original_set_attr(plug, *args, **kwargs)

        cmds.setAttr = counting_set_attr
        try:
            self.slot._do_find_and_copy_workflow(
                [node], relocate_mode="copy", dest_sourceimages=True
            )
        finally:
            cmds.setAttr = original_set_attr

        self.assertEqual(writes, [])
        self.assertEqual(self._path_of(node), "sourceimages/settled.png")
        self.assertEqual(self.slot._previous_paths, {})

    # -- source already at the destination ------------------------------------

    def test_move_does_not_delete_a_texture_already_at_the_destination(self):
        """A valid path inside the destination is a self-copy for Move.

        shutil rejects that as SameFileError, which would drop the file from
        the copied set and leave the node unrepathed. It is carried to the
        repath directly instead — and the file must survive.
        """
        tex = self._write(self.si_dir, "there.png")
        # Stored the way Maya hands back a Windows path: absolute, backslashes.
        node = self._make_file_node("tex_here", tex.replace("/", "\\"))

        self.slot._do_find_and_copy_workflow(
            [node], relocate_mode="move", dest_sourceimages=True
        )

        self.assertTrue(os.path.exists(tex), "move deleted the destination's own file")
        with open(tex) as fh:
            self.assertEqual(fh.read(), "payload")
        self.assertEqual(self._path_of(node), "sourceimages/there.png")

    def test_a_valid_path_outranks_a_search_hit_of_the_same_name(self):
        """The file the scene renders with wins over whatever the walk finds."""
        valid = self._write(self.ext_dir, "dup.png", payload="THE REAL ONE")
        stale_dir = os.path.join(self.tmp_root, "archive")
        os.makedirs(stale_dir, exist_ok=True)
        self._write(stale_dir, "dup.png", payload="stale")

        good = self._make_file_node("tex_dup", valid)
        gone = self._make_file_node(
            "tex_gone2",
            os.path.join(self.tmp_root, "vanished", "dup.png").replace("\\", "/"),
        )
        self.dialog_returns = [stale_dir, self.dest_dir]

        self.slot._do_find_and_copy_workflow([good, gone], relocate_mode="copy")

        with open(os.path.join(self.dest_dir, "dup.png")) as fh:
            self.assertEqual(fh.read(), "THE REAL ONE")

    def test_missing_sourceimages_setting_is_reported_not_guessed(self):
        node = self._make_file_node("tex_no_si", self._write(self.ext_dir, "x.png"))
        EnvUtils.get_env_info = staticmethod(
            lambda key: "" if key == "sourceimages" else self.tmp_root
        )

        self.slot._do_find_and_copy_workflow(
            [node], relocate_mode="copy", dest_sourceimages=True
        )

        self.assertEqual(self.dialog_titles, [])  # no silent fallback prompt
        self.assertEqual(
            self._path_of(node), os.path.join(self.ext_dir, "x.png").replace("\\", "/")
        )


class TestFindAndCopyOptionFlagReader(unittest.TestCase):
    """``_read_option_flag`` — checkbox state, with defaults when unbuilt."""

    def _button(self, **checkboxes):
        menu = SimpleNamespace(
            **{
                name: SimpleNamespace(isChecked=lambda v=value: v)
                for name, value in checkboxes.items()
            }
        )
        return SimpleNamespace(option_box=SimpleNamespace(menu=menu))

    def test_reads_the_checkbox(self):
        read = TexturePathEditorSlots._read_option_flag
        btn = self._button(chk_use_valid_paths=False, chk_dest_sourceimages=True)
        self.assertFalse(read(btn, "chk_use_valid_paths", True))
        self.assertTrue(read(btn, "chk_dest_sourceimages", False))

    def test_absent_checkbox_falls_back_to_the_default(self):
        read = TexturePathEditorSlots._read_option_flag
        self.assertTrue(read(self._button(), "chk_use_valid_paths", True))
        self.assertFalse(read(self._button(), "chk_dest_sourceimages", False))

    def test_no_button_at_all_falls_back_to_the_default(self):
        """The workflow stays callable without a built option box."""
        read = TexturePathEditorSlots._read_option_flag
        self.assertTrue(read(None, "chk_use_valid_paths", True))
        self.assertFalse(read(None, "chk_dest_sourceimages", False))


class TestRelativePathsSurviveTheWrite(MayaTkTestCase):
    """A relative path this panel writes must still be relative afterwards.

    ``cmds.setAttr`` is not literal on ``fileTextureName``: it expands a
    *resolvable* relative path straight back to absolute, so the bug only
    appears for textures that actually exist AND that Maya's own project can
    resolve. Every other test in this file misses both halves — they patch
    ``EnvUtils.get_env_info`` at the Python level while Maya's project stays
    elsewhere, so nothing resolves and every relative string survives by
    accident. These set Maya's REAL workspace and write REAL files, which is
    the only configuration that reproduces it (probe:
    ``test/temp_tests/probe_ftn_expansion.py``).
    """

    def setUp(self):
        super().setUp()
        self.tmp_root = tempfile.mkdtemp(prefix="ftn_literal_test_")
        self.si_dir = os.path.join(self.tmp_root, "sourceimages")
        os.makedirs(self.si_dir, exist_ok=True)

        # Maya must resolve against this root, not just our fake env reader —
        # the expansion is done by the DG, which never sees the patch.
        self._original_workspace = cmds.workspace(q=True, rootDirectory=True)
        cmds.workspace(self.tmp_root, openWorkspace=True)

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
        if self._original_workspace:
            cmds.workspace(self._original_workspace, openWorkspace=True)
        super().tearDown()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _real_texture(self, name):
        """A texture that EXISTS — an absent file is never expanded."""
        path = os.path.join(self.si_dir, name).replace("\\", "/")
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        return path

    def _make_file_node(self, name, path):
        node = cmds.shadingNode("file", asTexture=True, name=name)
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        return node

    def test_setattr_expands_a_resolvable_relative_path(self):
        """The Maya behavior the fix exists for — pinned so it can't silently change."""
        real = self._real_texture("premise.png")
        node = self._make_file_node("tex_premise", real)
        cmds.setAttr(
            f"{node}.fileTextureName", "sourceimages/premise.png", type="string"
        )
        self.assertTrue(
            os.path.isabs(cmds.getAttr(f"{node}.fileTextureName")),
            "cmds.setAttr no longer expands — the literal write may be redundant",
        )

    def test_normalize_leaves_an_existing_texture_relative(self):
        """The reported regression: every path displayed absolute."""
        real = self._real_texture("normalized.png")
        node = self._make_file_node("tex_norm", real)

        self.slot._normalize_to_relative([node])

        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/normalized.png"
        )

    def test_browse_and_set_directory_also_store_relative(self):
        """Same trap, the other two writers that relativize."""
        real = self._real_texture("flat.png")
        node = self._make_file_node("tex_flat", real)

        self.slot._set_texture_dir_flat([node], self.si_dir, relocate_mode="rewrite")

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/flat.png")

    def test_reload_textures_does_not_flatten_relative_paths(self):
        """Reload writes the path back to force a re-read — verbatim, now.

        The panel's own Find & Copy tooltip tells the user to reload after a
        relocation, so this turned every freshly relativized path absolute.
        """
        real = self._real_texture("reloaded.png")
        node = self._make_file_node("tex_reload", real)
        shader = cmds.shadingNode("lambert", asShader=True)
        cmds.connectAttr(f"{node}.outColor", f"{shader}.color", force=True)
        self.slot._normalize_to_relative([node])
        before = cmds.getAttr(f"{node}.fileTextureName")
        self.assertEqual(before, "sourceimages/reloaded.png")  # precondition

        MatUtils.reload_textures(refresh_viewport=False)

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), before)

    def test_find_and_copy_stores_a_relative_path(self):
        """The remap loop is a writer too — same trap, same fix."""
        ext_dir = os.path.join(self.tmp_root, "external")
        os.makedirs(ext_dir, exist_ok=True)
        src = os.path.join(ext_dir, "found.png").replace("\\", "/")
        with open(src, "wb") as fh:
            fh.write(b"payload")
        node = self._make_file_node("tex_found", src)
        self.slot.sb = SimpleNamespace(
            dir_dialog=lambda **kw: "",
            progress=lambda *a, **kw: _NullProgress(),
            progress_adapter=lambda update: None,
        )
        self.slot.ui = SimpleNamespace(tbl000=SimpleNamespace(init_slot=lambda: None))

        self.slot._do_find_and_copy_workflow(
            [node], relocate_mode="copy", dest_sourceimages=True
        )

        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/found.png"
        )


class TestFooterLabel(unittest.TestCase):
    """The footer names the resolved folder, whatever the project calls it."""

    def _slot(self, path):
        slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        slot._resolve_source_images_path = lambda: path
        return slot

    def test_label_is_the_folder_name(self):
        self.assertEqual(
            self._slot("C:/proj/sourceimages")._footer_status_text(),
            "SOURCEIMAGES: C:/proj/sourceimages",
        )

    def test_a_renamed_rule_renames_the_label(self):
        """A blendertk-promoted project maps sourceImages to ``textures``."""
        self.assertEqual(
            self._slot("C:/proj/textures")._footer_status_text(),
            "TEXTURES: C:/proj/textures",
        )

    def test_a_nested_rule_uses_its_last_component(self):
        self.assertEqual(
            self._slot("C:/proj/assets/sourceimages")._footer_status_text(),
            "SOURCEIMAGES: C:/proj/assets/sourceimages",
        )

    def test_no_project_is_an_empty_footer(self):
        self.assertEqual(self._slot("")._footer_status_text(), "")

    def test_a_nameless_path_still_shows_the_path(self):
        """A drive root has no folder name — show the path rather than "": path."""
        self.assertEqual(self._slot("C:/")._footer_status_text(), "C:/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
