# !/usr/bin/python
# coding=utf-8
"""Regression and behavioral tests for mayatk.mat_utils.texture_path_editor.

Covers:
- ``MatUtils.to_absolute`` (regression: 2026-08-04 sourceimages-doubling fix;
  promoted from this panel 2026-08-20 when Normalize Paths and the Scene
  Exporter's relative-path task were unified on one engine).
- ``MatUtils.to_project_relative`` round-trip guarantees (same promotion).
- ``_strategies_for_modes`` cascade/dedup logic.
- ``_resolve_missing_textures`` input validation.
- ``_normalize_to_relative`` semantics across path categories (a driver over
  ``MatUtils.stage_textures_relative(scope="project")`` since 2026-08-20).
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
    """Pure-string tests for ``MatUtils.to_absolute`` (no scene needed).

    Replaces the tests for the caller-less ``_resolve_absolute_texture_path``,
    whose relative branch was never exercised and joined against *sourceimages*
    instead of the workspace root — reproduced 2026-08-04 as
    ``C:/proj/sourceimages/sourceimages/foo.png``. (The helper lived on this
    panel as ``_to_absolute`` until 2026-08-20.)
    """

    def test_absolute_input_passes_through(self):
        abs_path = os.path.abspath(__file__)
        result = MatUtils.to_absolute(abs_path, "C:/proj")
        self.assertEqual(os.path.normcase(result), os.path.normcase(abs_path))

    def test_empty_returns_empty(self):
        self.assertEqual(MatUtils.to_absolute("", "C:/proj"), "")

    def test_relative_resolves_against_workspace_not_sourceimages(self):
        """Regression: sourceimages must not be doubled."""
        result = MatUtils.to_absolute("sourceimages/foo.png", "C:/proj")
        self.assertEqual(result, "C:/proj/sourceimages/foo.png")
        self.assertNotIn("sourceimages/sourceimages", result)

    def test_relative_without_workspace_is_left_alone(self):
        # Explicit "" workspace — don't fabricate a root (and don't fall back
        # to the live project either; "" is an answer, None means "look it
        # up"). The caller's exists() check then simply fails, which is the
        # honest answer.
        self.assertEqual(
            MatUtils.to_absolute("sourceimages/foo.png", ""), "sourceimages/foo.png"
        )

    def test_result_is_forward_slashed(self):
        result = MatUtils.to_absolute("sub\\tex.png", "C:\\proj")
        self.assertNotIn("\\", result)
        self.assertEqual(result, "C:/proj/sub/tex.png")

    def test_environment_variable_is_expanded(self):
        """Maya resolves ``$VAR`` in a ``.ftn``; so must the one primitive that
        turns a stored value into the path on disk.

        Unexpanded, the value fails ``os.path.isabs`` and gets the workspace
        pasted in front (``<proj>/$TEXDIR/foo.png``) -- a path that exists
        nowhere. The panel then painted the row red, Select Broken Paths
        collected it, and Make Paths Absolute WROTE that value back. The
        engine has always expanded (``stage_textures_relative``); this is the
        display half agreeing with it.

        Added: 2026-08-25
        """
        os.environ["MTK_TEST_TEXDIR"] = "C:/lib/textures"
        self.addCleanup(os.environ.pop, "MTK_TEST_TEXDIR", None)

        result = MatUtils.to_absolute("$MTK_TEST_TEXDIR/foo.png", "C:/proj")

        self.assertEqual(result, "C:/lib/textures/foo.png")
        self.assertNotIn("$", result)

    def test_undefined_environment_variable_is_left_intact(self):
        """``expandvars`` leaves an unknown name alone, and so must this -- the
        stored value is still the honest thing to show the user."""
        os.environ.pop("MTK_NO_SUCH_VAR", None)
        self.assertIn(
            "$MTK_NO_SUCH_VAR",
            MatUtils.to_absolute("$MTK_NO_SUCH_VAR/foo.png", "C:/proj"),
        )

    def test_udim_token_survives_the_join(self):
        result = MatUtils.to_absolute("sourceimages/tile_<UDIM>.png", "C:/proj")
        self.assertIn("<UDIM>", result)


class TestProjectRelativeConverter(unittest.TestCase):
    """``MatUtils.to_project_relative`` emits the ROOT-relative form.

    ``sourceimages/foo.png`` — how Maya spells a relative texture path, the
    first thing its loader tries, and the only relative form the FBX
    plug-in locates at write time. The round trip through ``to_absolute`` is
    the guard, and it passes for a texture not yet ON DISK (``to_absolute``
    falls back to the root form): Set Directory plans the relative form in
    phase 1 and only copies in phase 2, so a strict existence gate would
    store an absolute path for every texture about to land in sourceimages.
    (Find & Copy relativizes AFTER its copy, so it always has the file.)

    The RULE-relative fallback (a bare ``foo.png``) is emitted only for an
    out-of-root ``sourceImages`` rule, where no root-relative form finds the
    file — and there it keeps its shadow guard, since Maya answers the root
    first.

    (Promoted from this panel's ``_project_relative_converter`` closure
    2026-08-20; the roots now resolve per call unless passed in.)
    """

    def setUp(self):
        self._original_get_env_info = EnvUtils.get_env_info

    def tearDown(self):
        EnvUtils.get_env_info = staticmethod(self._original_get_env_info)

    def _patch_env(self, workspace, sourceimages):
        EnvUtils.get_env_info = staticmethod(
            lambda k: {"workspace": workspace, "sourceimages": sourceimages}.get(k, "")
        )

    def test_sourceimages_under_root_relativizes(self):
        self._patch_env("C:/proj", "C:/proj/sourceimages")
        result = MatUtils.to_project_relative("C:/proj/sourceimages/foo.png")
        self.assertEqual(result, "sourceimages/foo.png")

    def test_out_of_project_sourceimages_is_relative_to_the_rule(self):
        """An out-of-project rule USED to have no relative form; now it does.

        The 2026-08-04 regression was the root-relative spelling
        ``shared/foo.png`` — i.e. ``<proj>/shared/foo.png``, resolving to
        nothing. The rule-relative form carries no such prefix, and Maya
        resolves it through the rule wherever the rule points: measured
        loading the right image and surviving two save/open generations with
        an absolute out-of-project rule (``probe_ftn_reopen.py``).
        """
        self._patch_env("C:/proj", "D:/shared")
        self.assertEqual(MatUtils.to_project_relative("D:/shared/foo.png"), "foo.png")

    def test_relative_form_round_trips_through_to_absolute(self):
        """Resolution reads the DISK now, so the round trip needs real files."""
        root = tempfile.mkdtemp(prefix="ftn_roundtrip_")
        try:
            si_dir = os.path.join(root, "sourceimages")
            os.makedirs(os.path.join(si_dir, "a"))
            texture = os.path.join(si_dir, "a", "b.png").replace("\\", "/")
            with open(texture, "w"):
                pass
            self._patch_env(root, si_dir)

            rel = MatUtils.to_project_relative(texture)

            self.assertEqual(rel, "sourceimages/a/b.png")
            self.assertEqual(
                MatUtils.to_absolute(rel, root, si_dir).lower(),
                os.path.normpath(texture).replace("\\", "/").lower(),
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_explicit_arguments_win_over_the_env(self):
        """The loop-caller form: no per-call env lookup, no drift."""
        self._patch_env("C:/other", "C:/other/sourceimages")
        result = MatUtils.to_project_relative(
            "C:/proj/sourceimages/foo.png", "C:/proj", "C:/proj/sourceimages"
        )
        self.assertEqual(result, "sourceimages/foo.png")

    def test_path_outside_sourceimages_stays_absolute(self):
        self._patch_env("C:/proj", "C:/proj/sourceimages")
        result = MatUtils.to_project_relative("C:/elsewhere/foo.png")
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
        result = self.slot._strategies_for_modes(["stem", "fuzzy"], index_stems=[])
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

    def test_udim_path_relativizes_with_its_token_intact(self):
        """The engine relativizes a token path in place (the old local pass
        skipped UDIM paths entirely — they were the one category Normalize
        could not make portable).

        The tile on disk is the premise, not decoration: since 2026-08-25 a
        rewrite whose result names no file is refused, and a token path is
        checked through its tiles rather than by literal name.
        """
        for tile in ("1001", "1002"):
            with open(os.path.join(self.si_dir, f"tile_{tile}.png"), "w"):
                pass
        path = os.path.join(self.si_dir, "tile_<UDIM>.png").replace("\\", "/")
        node = self._make_file_node("tex_udim", path)
        self.slot._normalize_to_relative([node], external_mode="rewrite")
        result = cmds.getAttr(f"{node}.fileTextureName")
        self.assertEqual(result, "sourceimages/tile_<UDIM>.png")

    def test_in_project_path_with_no_file_behind_it_is_left_alone(self):
        """Normalize must not hand back a relative path that names nothing.

        The external branch has always refused this case
        (``skipped:missing-source`` — Resolve Missing Textures is the command
        for it); the in-project branch rewrote it anyway and counted it as
        ``rewritten``, so the panel reported a batch of successes over rows
        that stayed red. Reported 2026-08-25.
        """
        abs_path = os.path.join(self.si_dir, "ghost.png").replace("\\", "/")
        node = self._make_file_node("tex_ghost", abs_path)

        self.slot._normalize_to_relative([node], external_mode="rewrite")

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), abs_path)

    def test_udim_set_not_starting_at_1001_still_relativizes(self):
        """The missing-source guard asks the TILES, never the 1001 probe.

        ``_texture_exists`` deliberately probes tile 1001 (the exporter's
        representative must name the same file), so a set running 1002-1005 --
        routine -- reads as missing through it. Guarding on that would refuse
        to normalize a perfectly good set.
        """
        for tile in ("1002", "1003"):
            with open(os.path.join(self.si_dir, f"late_{tile}.png"), "w"):
                pass
        path = os.path.join(self.si_dir, "late_<UDIM>.png").replace("\\", "/")
        node = self._make_file_node("tex_late_udim", path)

        self.slot._normalize_to_relative([node], external_mode="rewrite")

        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"),
            "sourceimages/late_<UDIM>.png",
        )

    def test_udim_set_with_no_tiles_on_disk_is_left_alone(self):
        """Same rule, checked through the tiles — not the literal token name."""
        abs_path = os.path.join(self.si_dir, "ghost_<UDIM>.png").replace("\\", "/")
        node = self._make_file_node("tex_ghost_udim", abs_path)

        self.slot._normalize_to_relative([node], external_mode="rewrite")

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), abs_path)

    def test_already_relative_is_noop(self):
        node = self._make_file_node("tex_rel", "foo.png")
        self.slot._normalize_to_relative([node], external_mode="rewrite")
        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "foo.png")

    def test_the_legacy_rule_relative_form_is_upgraded_in_place(self):
        """A bare ``foo.png`` resolves in Maya but names no folder.

        It is the form this panel emitted between 2026-08-18 and 2026-08-25,
        so it is what production scenes normalized in that window carry. The
        FBX plug-in cannot locate it at write time (it resolves against the
        process CWD, which the exporter aligns with the project ROOT), and
        the exporter's own gate read it as a MISSING texture and rebound the
        node by basename. Normalize upgrades it in place, which is how such a
        scene repairs itself.
        """
        with open(os.path.join(self.si_dir, "legacy.png"), "w"):
            pass
        node = self._make_file_node("tex_legacy", "legacy.png")

        self.slot._normalize_to_relative([node], external_mode="rewrite")

        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/legacy.png"
        )

    def test_a_path_already_in_the_stored_form_is_left_alone(self):
        """``sourceimages/foo.png`` is the emitted form — nothing to upgrade."""
        with open(os.path.join(self.si_dir, "settled_rel.png"), "w"):
            pass
        node = self._make_file_node("tex_settled_rel", "sourceimages/settled_rel.png")

        self.slot._normalize_to_relative([node], external_mode="rewrite")

        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/settled_rel.png"
        )

    def test_a_relative_path_naming_nothing_is_left_alone(self):
        """Resolve Missing Textures' job, not Normalize's."""
        node = self._make_file_node("tex_ghost_rel", "sourceimages/ghost.png")
        self.slot._normalize_to_relative([node], external_mode="rewrite")
        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/ghost.png"
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
            self.assertFalse(
                os.path.isabs(result), f"Expected relative, got {result!r}"
            )
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
            self.assertFalse(
                os.path.isabs(result), f"Expected relative, got {result!r}"
            )
            self.assertIn("external3.png", result)
            # File is in sourceimages.
            self.assertTrue(os.path.exists(os.path.join(self.si_dir, "external3.png")))
            # Original is gone (moved).
            self.assertFalse(os.path.exists(ext_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_move_rebinds_every_node_sharing_the_external_path(self):
        """Two file nodes storing the SAME external path (duplicated file
        nodes are routine): the first node's move stages the file and removes
        the original, so the second finds no source on disk. It must rebind
        to the same staged twin — before the 2026-08-20 fix it read "shared"
        as "missing" and stayed absolute on a deleted file (the old editor
        pass failed the same case through its collision skip)."""
        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "shared_move.png")
            with open(ext_file, "w") as fh:
                fh.write("SHARED")
            abs_path = ext_file.replace("\\", "/")
            node_a = self._make_file_node("tex_share_a", abs_path)
            node_b = self._make_file_node("tex_share_b", abs_path)

            self.slot._normalize_to_relative([node_a, node_b], external_mode="move")

            for node in (node_a, node_b):
                self.assertEqual(
                    cmds.getAttr(f"{node}.fileTextureName"),
                    "sourceimages/shared_move.png",
                    f"{node} must rebind to the staged twin",
                )
            self.assertFalse(os.path.exists(ext_file), "moved, not copied")
            with open(os.path.join(self.si_dir, "shared_move.png")) as fh:
                self.assertEqual(fh.read(), "SHARED")
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_move_collision_with_different_content_stages_a_variant(self):
        """Move + same-name-same-SIZE collision: the engine hashes content, so
        the different file lands as an ``_1`` variant and the external is
        removed (move semantics) — the resident is never overwritten and the
        node is never rebound to it.

        (The old size-proxy called these two files "the same" and rebound the
        node to the resident — a wrong-file rebind this fixture literally
        constructed. Engine policy since 2026-08-20.)
        """
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

            # Rebound to the staged variant, not the same-named resident.
            result = cmds.getAttr(f"{node}.fileTextureName")
            self.assertEqual(result, "sourceimages/match_move_1.png")
            staged = os.path.join(self.si_dir, "match_move_1.png")
            with open(staged) as fh:
                self.assertEqual(fh.read(), "BBBBB")
            # Pre-existing file kept (no overwrite).
            with open(existing) as fh:
                self.assertEqual(fh.read(), "AAAAA")
            # External removed (move semantics — its content is staged).
            self.assertFalse(os.path.exists(ext_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_copy_collision_with_different_content_stages_a_variant(self):
        """Same basename in sourceimages with different content: staged as an
        ``_1`` variant and rebound to it — never silently rebound to the
        resident, never abandoned on the absolute path (the old policy's
        skip leaked the external path into every export)."""
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

            self.assertEqual(
                cmds.getAttr(f"{node}.fileTextureName"),
                "sourceimages/collide_1.png",
            )
            with open(os.path.join(self.si_dir, "collide_1.png")) as fh:
                self.assertEqual(fh.read(), "DIFFERENT CONTENT")
            # Pre-existing sourceimages file untouched.
            with open(existing) as fh:
                self.assertEqual(fh.read(), "X")
            # Source still on disk (copy semantics).
            self.assertTrue(os.path.exists(ext_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_copy_collision_with_identical_content_reuses_without_copying(self):
        """A resident that provably IS the same texture (size + hash) is
        reused: no disk write, node rebound to it — repeat runs converge
        instead of stacking variants."""
        existing = os.path.join(self.si_dir, "match.png")
        with open(existing, "w") as fh:
            fh.write("ABCDE")
        existing_mtime = os.path.getmtime(existing)

        ext_dir = tempfile.mkdtemp(prefix="external_textures_")
        try:
            ext_file = os.path.join(ext_dir, "match.png")
            with open(ext_file, "w") as fh:
                fh.write("ABCDE")  # identical content
            abs_path = ext_file.replace("\\", "/")
            node = self._make_file_node("tex_match", abs_path)

            self.slot._normalize_to_relative([node], external_mode="copy")

            self.assertEqual(
                cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/match.png"
            )
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
        self._repoint_sourceimages(
            os.path.join(self.tmp_root, "assets", "sourceimages")
        )
        src_file = os.path.join(self.si_dir, "nested.png")
        with open(src_file, "w"):
            pass
        node = self._make_file_node("tex_nested", src_file.replace("\\", "/"))

        self.slot._normalize_to_relative([node], external_mode="rewrite")

        result = cmds.getAttr(f"{node}.fileTextureName")
        self.assertEqual(result, "assets/sourceimages/nested.png")
        # The relative form has to resolve back to the file Maya was given —
        # against the ROOT, which is where Maya looks first and what the
        # emitted form spells in full (a nested rule is why the prefix cannot
        # be a hardcoded 'sourceimages/').
        self.assertTrue(os.path.exists(os.path.join(self.tmp_root, result)))
        self.assertEqual(
            MatUtils.to_absolute(result, self.tmp_root, self.si_dir).lower(),
            src_file.replace("\\", "/").lower(),
        )

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
            # Rebound to the rule-relative form: Maya resolves it through the
            # rule wherever the rule points, so it still names this file.
            stored = cmds.getAttr(f"{node}.fileTextureName")
            self.assertEqual(stored, "self.png")
            self.assertEqual(
                MatUtils.to_absolute(stored, self.tmp_root, outside_si).lower(),
                abs_path.lower(),
            )
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

    def test_env_var_path_is_not_rewritten(self):
        """It already resolves absolutely, so there is nothing to absolutize.

        The command tested ``os.path.isabs`` on the RAW value, so a
        ``$VAR/foo.png`` read as relative and was rewritten to
        ``<proj>/$VAR/foo.png`` -- the variable pasted under the project, a
        path that resolves nowhere and cannot be undone by re-running.
        """
        os.environ["MTK_TEST_TEXDIR"] = self.tmp_root.replace("\\", "/")
        self.addCleanup(os.environ.pop, "MTK_TEST_TEXDIR", None)
        stored = "$MTK_TEST_TEXDIR/sourceimages/env.png"
        node = self._make_file_node("tex_mabs_env", stored)

        self.slot._make_paths_absolute([node])

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), stored)

    def test_previous_path_recorded(self):
        node = self._make_file_node("tex_mabs_prev", "sourceimages/baz.png")
        self.slot._make_paths_absolute([node])
        self.assertEqual(self.slot._previous_paths.get(node), "sourceimages/baz.png")

    def test_round_trip_with_normalize(self):
        """The two commands are inverses -- over a texture that is THERE.

        The file on disk is the premise: since 2026-08-25 Normalize refuses to
        relativize a path naming no file, while ``_make_paths_absolute`` still
        rewrites one (``test_missing_file_still_rewritten``). The asymmetry is
        deliberate -- absolutizing a missing texture spells out where Maya
        looked for it, which is the diagnostic; relativizing one only claims a
        portability it cannot deliver.
        """
        with open(os.path.join(self.si_dir, "round.png"), "w"):
            pass
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
            # The file the node is being pointed AT. Rewrite moves nothing, so
            # the target has to already hold it -- see
            # test_rewrite_mode_will_not_point_a_node_at_a_file_that_is_not_there.
            with open(os.path.join(target, "tex.png"), "w") as fh:
                fh.write("payload")

            self.slot._set_texture_dir_flat([node], target, relocate_mode="rewrite")

            # Path points at the new dir; SOURCE file untouched (nothing moved).
            self.assertIn(
                "newdir/tex.png",
                cmds.getAttr(f"{node}.fileTextureName").replace("\\", "/"),
            )
            self.assertTrue(os.path.exists(src_file))
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_rewrite_mode_will_not_point_a_node_at_a_file_that_is_not_there(self):
        """The default must not manufacture a broken path.

        Reported 2026-08-25: picking sourceimages in rewrite mode repointed
        every row at ``sourceimages/<basename>`` whether or not that file was
        there, so the table filled with red rows naming files that never
        existed at the destination.
        """
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            src_file = os.path.join(ext_dir, "lonely.png")
            with open(src_file, "w") as fh:
                fh.write("payload")
            stored = src_file.replace("\\", "/")
            node = self._make_file_node("tex_rw_missing", stored)

            target = os.path.join(self.tmp_root, "emptydir")
            os.makedirs(target, exist_ok=True)
            count = self.slot._set_texture_dir_flat(
                [node], target, relocate_mode="rewrite"
            )

            self.assertEqual(count, 0)
            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), stored)
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_allow_missing_restores_the_blind_path_only_rewrite(self):
        """The deliberate case: point a batch at a folder about to be filled."""
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            src_file = os.path.join(ext_dir, "lonely.png")
            with open(src_file, "w") as fh:
                fh.write("payload")
            node = self._make_file_node("tex_rw_allow", src_file.replace("\\", "/"))

            target = os.path.join(self.tmp_root, "emptydir2")
            os.makedirs(target, exist_ok=True)
            count = self.slot._set_texture_dir_flat(
                [node], target, relocate_mode="rewrite", allow_missing=True
            )

            self.assertEqual(count, 1)
            self.assertIn(
                "emptydir2/lonely.png",
                cmds.getAttr(f"{node}.fileTextureName").replace("\\", "/"),
            )
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

    def _make_tiles(self, directory, stem, tiles=("1001", "1002")):
        for tile in tiles:
            with open(os.path.join(directory, f"{stem}.{tile}.png"), "w") as fh:
                fh.write(f"payload {tile}")

    def test_copy_mode_relocates_every_tile_of_a_udim_set(self):
        """Regression (2026-08-25): a token path satisfied no ``os.path.exists``,
        so NOTHING was copied -- and the node was repathed to the destination
        anyway, landing on a folder holding no tile of that name."""
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            self._make_tiles(ext_dir, "rock")
            stored = os.path.join(ext_dir, "rock.<UDIM>.png").replace("\\", "/")
            node = self._make_file_node("tex_udim_copy", stored)

            target = os.path.join(self.tmp_root, "udimdir")
            os.makedirs(target, exist_ok=True)
            self.slot._set_texture_dir_flat([node], target, relocate_mode="copy")

            for tile in ("1001", "1002"):
                self.assertTrue(
                    os.path.exists(os.path.join(target, f"rock.{tile}.png")),
                    f"tile {tile} was not copied",
                )
            new_path = cmds.getAttr(f"{node}.fileTextureName")
            self.assertIn("<UDIM>", new_path, "the token must survive the repath")
            self.assertTrue(
                MatUtils.texture_tiles(MatUtils.to_absolute(new_path, self.tmp_root)),
                f"{new_path!r} names no file on disk",
            )
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_copy_mode_repaths_a_set_that_does_not_start_at_1001(self):
        """The tiles travel, so the node must follow them.

        The repath gate has to ask the same question the relocation did. Asked
        through the 1001 probe instead, a 1002-1005 set copied cleanly and was
        then left on its old path -- files moved, node did not.
        """
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            self._make_tiles(ext_dir, "late", tiles=("1002", "1003"))
            stored = os.path.join(ext_dir, "late.<UDIM>.png").replace("\\", "/")
            node = self._make_file_node("tex_late_copy", stored)

            target = os.path.join(self.tmp_root, "latedir")
            os.makedirs(target, exist_ok=True)
            count = self.slot._set_texture_dir_flat(
                [node], target, relocate_mode="copy"
            )

            self.assertEqual(count, 1, "the node was not repathed onto its own tiles")
            self.assertIn(
                "latedir/late.<UDIM>.png",
                cmds.getAttr(f"{node}.fileTextureName").replace("\\", "/"),
            )
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_move_mode_relocates_every_tile_of_a_udim_set(self):
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            self._make_tiles(ext_dir, "moss")
            stored = os.path.join(ext_dir, "moss.<UDIM>.png").replace("\\", "/")
            node = self._make_file_node("tex_udim_move", stored)

            target = os.path.join(self.tmp_root, "udimmove")
            os.makedirs(target, exist_ok=True)
            self.slot._set_texture_dir_flat([node], target, relocate_mode="move")

            for tile in ("1001", "1002"):
                self.assertTrue(
                    os.path.exists(os.path.join(target, f"moss.{tile}.png"))
                )
                self.assertFalse(
                    os.path.exists(os.path.join(ext_dir, f"moss.{tile}.png"))
                )
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_a_node_whose_texture_never_landed_is_not_repathed(self):
        """Copy mode with a source that is not on disk: nothing to copy, so
        nothing to point at -- the node keeps the path it had."""
        ext_dir = tempfile.mkdtemp(prefix="src_")
        try:
            stored = os.path.join(ext_dir, "never_existed.png").replace("\\", "/")
            node = self._make_file_node("tex_absent", stored)

            target = os.path.join(self.tmp_root, "destdir2")
            os.makedirs(target, exist_ok=True)
            count = self.slot._set_texture_dir_flat(
                [node], target, relocate_mode="copy"
            )

            self.assertEqual(count, 0)
            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), stored)
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_a_texture_already_at_the_target_is_still_repathed(self):
        """The skip is "no file there", never "no copy happened" -- a texture
        already sitting in the target needs no file op and must still be
        rebound to the shorter (relative) form."""
        target = os.path.join(self.tmp_root, "sourceimages")
        src_file = os.path.join(target, "resident.png")
        with open(src_file, "w") as fh:
            fh.write("payload")
        node = self._make_file_node("tex_resident", src_file.replace("\\", "/"))

        count = self.slot._set_texture_dir_flat([node], target, relocate_mode="copy")

        self.assertEqual(count, 1)
        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/resident.png"
        )

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
        self.assertEqual(
            slot._read_relocate_mode(self._relocate_button(0), items), "rewrite"
        )
        self.assertEqual(
            slot._read_relocate_mode(self._relocate_button(1), items), "copy"
        )
        self.assertEqual(
            slot._read_relocate_mode(self._relocate_button(2), items), "move"
        )

    def test_relocate_find_indices(self):
        slot = self._slot()
        items = slot._FIND_MODE_ITEMS
        self.assertEqual(
            slot._read_relocate_mode(self._relocate_button(0), items), "copy"
        )
        self.assertEqual(
            slot._read_relocate_mode(self._relocate_button(1), items), "move"
        )

    def test_relocate_out_of_range_returns_safe_default(self):
        slot = self._slot()
        items = slot._RELOCATE_MODE_ITEMS
        # currentIndex == -1 → first item (rewrite).
        self.assertEqual(
            slot._read_relocate_mode(self._relocate_button(-1), items), "rewrite"
        )


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
        """ ".." would read as a parent-directory segment in a path column."""
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


class TestFindAndCopyPanel(MayaTkTestCase):
    """Find & Copy asks for both folders on ONE panel, at the same time.

    Reported 2026-08-25: users pick their texture folder as the DESTINATION,
    believing they are answering "where do I find these". It used to be two
    native directory pickers back to back — the same widget twice, the
    direction carried only by the window caption — and the two option-box
    toggles that could skip either one made the ORDER variable, so with every
    path valid the FIRST and only dialog was the destination.

    Side by side and labelled there is nothing to tell apart and no order to
    remember, the accept button names the operation and the count, and
    source == destination is refused inline before any file is touched. Both
    toggles are gone with the sequence that needed them.

    The form is now a uitk ``FormPanel`` that stays open and reports into its
    own pane, so these tests drive the two seams it separates: composing the
    rows (``_find_and_copy_fields``, pure) and doing the work
    (``_execute_find_and_copy``, a plain dict in). Neither needs a window.
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

        self.panel_calls = []  # every form_panel construction, in order
        self.panel_reseeds = []  # every set_fields on an already-built panel
        self.presented = 0
        self.form_answers = None  # scripted answer (None == dismissed)

        test = self

        self.reported = []  # (level, message) the panel's pane would show

        class _StubLogger:
            """Records what the pane would show, with the log_group shape."""

            def __getattr__(self, level):
                def emit(message, *_args, **_kwargs):
                    test.reported.append((level, str(message)))

                return emit

            def log_group(self, title, items, level="info"):
                test.reported.append((level, "\n".join([str(title), *map(str, items)])))

        class _StubPanel:
            """The window, minus Qt: what the slot actually touches."""

            def __init__(self):
                self.logger = _StubLogger()
                self.footer = SimpleNamespace(setDefaultStatusText=lambda *a: None)

            def set_fields(self, fields):
                test.panel_reseeds.append([dict(f) for f in fields])

            def present(self):
                test.presented += 1

        def fake_form_panel(fields, **kwargs):
            self.panel_calls.append({"fields": [dict(f) for f in fields], **kwargs})
            return _StubPanel()

        self.sb = SimpleNamespace(
            form_panel=fake_form_panel,
            tooltip=SimpleNamespace(fmt=lambda **kw: str(kw)),
            progress=lambda *a, **kw: _NullProgress(),
            progress_adapter=lambda update: None,
        )
        # The REAL constructor over the stub switchboard, so every attribute
        # ``__init__`` seeds is present — a hand-set list of them goes stale
        # the moment ``__init__`` grows one more (it did: a concurrent lightmap
        # change added ``_find_copy_lightmaps`` and two direct calls broke).
        self.sb.loaded_ui = SimpleNamespace(
            texture_path_editor=SimpleNamespace(
                tbl000=SimpleNamespace(init_slot=lambda: None)
            )
        )
        self.slot = TexturePathEditorSlots(self.sb)

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

    # -- the one dialog -------------------------------------------------------

    def _answer(self, source_dir="", dest_dir=None, mode="Copy"):
        """Script what the user fills in and accepts."""
        self.form_answers = {
            "source_dir": source_dir,
            "dest_dir": self.dest_dir if dest_dir is None else dest_dir,
            "mode": mode,
        }

    def _fields(self, index=0):
        """The panel's field specs, keyed by name."""
        return {f["name"]: f for f in self.panel_calls[index]["fields"]}

    def _run(self, nodes, relocate_mode="copy"):
        """Open the panel over *nodes*, then press its Run with the answers.

        Drives the production path — ``_find_and_copy_workflow`` composes the
        form, ``_run_find_and_copy_over`` is what the panel's accept button
        calls — with the window itself stubbed out, because the panel already
        separates the two halves that need no window: composing the rows is
        pure and doing the work takes a plain dict. ``form_answers = None``
        stands in for "the panel was opened and dismissed without running".

        Returns:
            The commit call a dry run hands back for Apply, else None.
        """
        nodes = [str(n) for n in nodes]
        self.slot._find_and_copy_workflow(nodes, relocate_mode=relocate_mode)
        if self.form_answers is None:
            return None
        return self.slot._run_find_and_copy_over(nodes, self.form_answers)

    def test_one_dialog_carries_both_folders(self):
        """The fix: nothing to tell apart, and no order to remember."""
        node = self._make_file_node("tex_v", self._write(self.ext_dir, "valid.png"))
        self._answer()

        self._run([node], relocate_mode="copy")

        self.assertEqual(len(self.panel_calls), 1, "one panel, not a sequence")
        fields = self._fields()
        self.assertIn("source_dir", fields)
        self.assertIn("dest_dir", fields)
        self.assertIn("Search in", fields["source_dir"]["label"])
        self.assertIn("Copy into", fields["dest_dir"]["label"])
        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "valid.png")))
        self.assertTrue(self._path_of(node).endswith("dest/valid.png"))

    def test_each_row_says_what_will_happen_to_it(self):
        """The hint is what makes two path rows tell themselves apart."""
        node = self._make_file_node("tex_h", self._write(self.ext_dir, "hint.png"))
        self._answer()

        self._run([node], relocate_mode="copy")

        fields = self._fields()
        self.assertIn("land HERE", fields["dest_dir"]["hint"])
        self.assertIn("nothing needs finding", fields["source_dir"]["hint"])

    def test_the_labels_carry_the_direction_marks(self):
        """Colour is redundancy; the words carry the meaning either way."""
        node = self._make_file_node("tex_m", self._write(self.ext_dir, "mark.png"))
        self._answer()

        self._run([node], relocate_mode="copy")

        fields = self._fields()
        self.assertIn(
            TexturePathEditorSlots._DIALOG_MARK_SOURCE, fields["source_dir"]["label"]
        )
        self.assertIn(
            TexturePathEditorSlots._DIALOG_MARK_DEST, fields["dest_dir"]["label"]
        )

    def test_the_destination_prefills_to_sourceimages(self):
        """What the retired 'Always Relocate To sourceimages' toggle bought,
        minus the hidden state: the common answer is already typed in."""
        node = self._make_file_node("tex_si", self._write(self.ext_dir, "auto.png"))
        self._answer(dest_dir=self.si_dir)

        self._run([node], relocate_mode="copy")

        self.assertEqual(self._fields()["dest_dir"]["value"], self.si_dir)
        self.assertTrue(os.path.exists(os.path.join(self.si_dir, "auto.png")))
        # Inside the project → repathed relative.
        self.assertEqual(self._path_of(node), "sourceimages/auto.png")

    def test_the_accept_button_names_the_operation_and_the_count(self):
        """The last thing read before committing must say what happens.

        ``ok_text`` is a CALLABLE so the verb tracks the mode row live -- a
        fixed string would contradict the combo the moment it is changed.
        """
        nodes = [
            self._make_file_node("tex_c1", self._write(self.ext_dir, "c1.png")),
            self._make_file_node("tex_c2", self._write(self.ext_dir, "c2.png")),
        ]
        self._answer(mode="Move")

        self._run(nodes, relocate_mode="move")

        ok_text = self.panel_calls[0]["ok_text"]
        self.assertEqual(ok_text({"mode": "Move"}), "Move 2 texture(s)")
        self.assertEqual(ok_text({"mode": "Copy"}), "Copy 2 texture(s)")

    def test_the_count_is_per_node_not_per_basename(self):
        """Two nodes reading the SAME file are two textures to relocate.

        The count came from the basename-keyed ``resolved`` dict, so a scene
        where two file nodes share a texture promised to copy fewer than it
        would repath.
        """
        shared = self._write(self.ext_dir, "shared_count.png")
        nodes = [
            self._make_file_node("tex_s1", shared),
            self._make_file_node("tex_s2", shared),
        ]
        self._answer()

        self._run(nodes, relocate_mode="copy")

        self.assertEqual(
            self.panel_calls[0]["ok_text"]({"mode": "Copy"}), "Copy 2 texture(s)"
        )

    def test_the_mode_is_read_from_the_dialog(self):
        """Copy/Move moved out of the option box and onto the form."""
        tex = self._write(self.ext_dir, "moved.png")
        node = self._make_file_node("tex_mv", tex)
        self._answer(mode="Move")

        self._run([node], relocate_mode="copy")

        self.assertFalse(os.path.exists(tex), "Move must remove the original")
        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "moved.png")))

    # -- the source row -------------------------------------------------------

    def test_the_source_row_is_disabled_when_every_path_resolves(self):
        """Disabled WITH its reason, not hidden: a row that vanishes leaves a
        gap the user has to explain to themselves."""
        node = self._make_file_node("tex_all", self._write(self.ext_dir, "all.png"))
        self._answer()

        self._run([node], relocate_mode="copy")

        source = self._fields()["source_dir"]
        self.assertFalse(source["enabled"])
        self.assertIn("nothing needs finding", source["hint"])
        # ...and the same answer is in the empty field itself, where a
        # greyed row is looked at rather than hovered — short, because a
        # line edit elides a placeholder that overruns it.
        self.assertEqual(source["placeholder"], "No path requires a search dir")

    def test_the_source_row_names_what_is_actually_missing(self):
        good = self._make_file_node("tex_g", self._write(self.ext_dir, "good2.png"))
        gone = self._make_file_node(
            "tex_x", os.path.join(self.ext_dir, "absent.png").replace("\\", "/")
        )
        self._answer(source_dir=self.ext_dir)

        self._run([good, gone], relocate_mode="copy")

        source = self._fields()["source_dir"]
        self.assertTrue(source["enabled"])
        self.assertIn("absent.png", source["hint"])
        self.assertIn("1 unresolved", source["hint"])
        # The field says why it would be filled, in one short line; what
        # leaving it empty costs is the hint's job, where there is room.
        self.assertEqual(source["placeholder"], "1 path(s) require a search dir")
        self.assertIn("skip them and relocate the 1", source["hint"])

    def test_an_empty_source_row_skips_the_unresolved_and_keeps_the_rest(self):
        """48-of-50 valid: no search folder skips 2, it doesn't abort 48."""
        good = self._make_file_node("tex_k", self._write(self.ext_dir, "keep.png"))
        gone = self._make_file_node(
            "tex_m2", os.path.join(self.ext_dir, "nope.png").replace("\\", "/")
        )
        self._answer(source_dir="")

        self._run([good, gone], relocate_mode="copy")

        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "keep.png")))
        self.assertTrue(self._path_of(good).endswith("dest/keep.png"))
        self.assertTrue(self._path_of(gone).endswith("external/nope.png"))

    def test_a_search_folder_never_re_sources_a_path_that_resolves(self):
        """One rule, no toggle: the file the scene is RENDERING is the one
        that relocates, whatever else the search folder holds under that
        name. The retired "re-source everything" checkbox was the only way
        to say otherwise, and it could silently swap a texture."""
        live = self._write(self.ext_dir, "dup2.png", payload="THE LIVE ONE")
        other_dir = os.path.join(self.tmp_root, "newer")
        os.makedirs(other_dir, exist_ok=True)
        self._write(other_dir, "dup2.png", payload="a same-named stranger")
        node = self._make_file_node("tex_re", live)
        self._answer(source_dir=other_dir)

        self._run([node], relocate_mode="copy")

        with open(os.path.join(self.dest_dir, "dup2.png")) as fh:
            self.assertEqual(fh.read(), "THE LIVE ONE")

    def test_cancelling_the_dialog_does_nothing(self):
        node = self._make_file_node("tex_a", self._write(self.ext_dir, "cancel.png"))
        before = self._path_of(node)
        self.form_answers = None  # Cancel

        self._run([node], relocate_mode="copy")

        self.assertEqual(self._path_of(node), before)
        self.assertFalse(os.path.exists(os.path.join(self.dest_dir, "cancel.png")))

    # -- the window it now is ------------------------------------------------

    def test_reopening_re_seeds_the_panel_instead_of_rebuilding_it(self):
        """It stays open while it works, so a second invocation must not throw
        away the report being read or the size the user set."""
        node = self._make_file_node("tex_r1", self._write(self.ext_dir, "r1.png"))
        self._answer()

        self._run([node])
        self._run([node])

        self.assertEqual(len(self.panel_calls), 1, "the panel was rebuilt")
        self.assertEqual(len(self.panel_reseeds), 1, "the reopen did not re-seed")
        self.assertEqual(self.presented, 2, "the reopen must raise the panel")

    def test_the_re_seeded_rows_describe_the_new_scope(self):
        """A panel still claiming the previous scope's counts is worse than
        no panel — the button names a number it would not act on."""
        one = self._make_file_node("tex_s1x", self._write(self.ext_dir, "s1.png"))
        two = self._make_file_node("tex_s2x", self._write(self.ext_dir, "s2.png"))
        self._answer()

        self._run([one])
        self._run([one, two])

        dest = {f["name"]: f for f in self.panel_reseeds[0]}["dest_dir"]
        self.assertIn("2 texture(s) land HERE", dest["hint"])

    def test_the_accept_verb_follows_the_re_seeded_scope(self):
        one = self._make_file_node("tex_v1", self._write(self.ext_dir, "v1.png"))
        two = self._make_file_node("tex_v2", self._write(self.ext_dir, "v2.png"))
        self._answer()

        self._run([one])
        ok_text = self.panel_calls[0]["ok_text"]
        self.assertEqual(ok_text({"mode": "Copy"}), "Copy 1 texture(s)")

        self._run([one, two])
        self.assertEqual(ok_text({"mode": "Copy"}), "Copy 2 texture(s)")

    def test_the_panel_is_anchored_to_the_editor(self):
        """Unparented it would outlive the panel that opened it."""
        node = self._make_file_node("tex_p", self._write(self.ext_dir, "p.png"))
        self._answer()

        self._run([node])

        self.assertIs(self.panel_calls[0]["parent"], self.slot.ui)

    def test_the_run_handler_is_wired(self):
        """A panel whose accept button ran nothing would relocate nothing."""
        node = self._make_file_node("tex_run", self._write(self.ext_dir, "run.png"))
        self._answer()

        self._run([node])

        self.assertEqual(
            self.panel_calls[0]["on_run"].__func__,
            TexturePathEditorSlots._run_find_and_copy,
        )

    def test_the_report_goes_to_the_panel_when_one_is_driving(self):
        """The pane IS the report — the point of it is not looking elsewhere."""
        node = self._make_file_node("tex_rep", self._write(self.ext_dir, "rep.png"))
        self._answer()

        self._run([node])

        self.assertTrue(
            any("Remapped 1 file nodes." in m for _lvl, m in self.reported),
            self.reported,
        )
        self.assertTrue(
            any(lvl == "success" for lvl, _m in self.reported),
            f"a completed remap should read as a success: {self.reported}",
        )

    def test_the_report_falls_back_to_mayas_channels_with_no_panel(self):
        """Headless, and from a test that never opened a window."""
        self.slot._active_logger = None
        node = self._make_file_node("tex_hd", self._write(self.ext_dir, "hd.png"))
        self._answer()

        self.slot._execute_find_and_copy([node], self.form_answers)

        self.assertEqual(self.reported, [], "the panel logger was used with no panel")
        self.assertTrue(self._path_of(node).endswith("dest/hd.png"))

    # -- the rows, in reading order ------------------------------------------

    def test_the_rows_read_in_the_order_of_the_decision(self):
        """What to do, where to look, where it lands, whether to commit —
        four rows, no opt-ins: every question the form asks is one the
        scope and the folders cannot already answer."""
        node = self._make_file_node("tex_ord", self._write(self.ext_dir, "ord.png"))
        self._answer()

        self._run([node])

        order = [f["name"] for f in self.panel_calls[0]["fields"]]
        self.assertEqual(order, ["mode", "source_dir", "dest_dir", "dry_run"])

    # -- dry run -------------------------------------------------------------

    def _dry(self, **kwargs):
        self._answer(**kwargs)
        self.form_answers["dry_run"] = True

    def test_the_accept_verb_says_preview_while_dry_run_is_ticked(self):
        node = self._make_file_node("tex_dv", self._write(self.ext_dir, "dv.png"))
        self._answer()

        self._run([node])

        ok_text = self.panel_calls[0]["ok_text"]
        self.assertEqual(
            ok_text({"mode": "Copy", "dry_run": True}), "Preview 1 texture(s)"
        )
        self.assertEqual(
            ok_text({"mode": "Copy", "dry_run": False}), "Copy 1 texture(s)"
        )

    def test_a_dry_run_writes_nothing(self):
        """No file relocated, no plug touched, and the destination folder is
        not even created — the whole point is that it can be run to look."""
        tex = self._write(self.ext_dir, "look.png")
        node = self._make_file_node("tex_look", tex)
        before = self._path_of(node)
        fresh_dest = os.path.join(self.tmp_root, "not_yet")
        self._dry(dest_dir=fresh_dest)

        self._run([node])

        self.assertTrue(os.path.exists(tex), "the source was moved by a preview")
        self.assertFalse(
            os.path.exists(fresh_dest), "a preview created the destination"
        )
        self.assertEqual(self._path_of(node), before)
        self.assertEqual(self.slot._previous_paths, {})

    def test_a_dry_run_reports_what_would_move_and_repath(self):
        self._make_file_node("tex_rep1", self._write(self.ext_dir, "rep1.png"))
        self._dry()

        self._run(["tex_rep1"])

        report = "\n".join(m for _lvl, m in self.reported)
        self.assertIn("Dry run", report)
        self.assertIn("rep1.png", report)
        self.assertIn("Would repath 1 file node(s)", report)
        self.assertIn("Apply", report)

    def test_a_dry_run_arms_apply_with_the_call_that_commits_it(self):
        tex = self._write(self.ext_dir, "arm.png")
        node = self._make_file_node("tex_arm", tex)
        self._dry()

        commit = self._run([node])

        self.assertTrue(callable(commit), "a preview must hand back its commit")
        self.assertFalse(os.path.exists(os.path.join(self.dest_dir, "arm.png")))

        commit()

        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "arm.png")))
        self.assertTrue(self._path_of(node).endswith("dest/arm.png"))

    def test_applying_a_preview_arms_nothing_further(self):
        """The plan is spent — a commit that re-armed would offer itself again."""
        node = self._make_file_node("tex_once", self._write(self.ext_dir, "once.png"))
        self._dry()

        commit = self._run([node])
        self.assertIsNone(commit())

    def test_a_live_run_arms_nothing(self):
        node = self._make_file_node("tex_live", self._write(self.ext_dir, "live.png"))
        self._answer()

        self.assertIsNone(self._run([node]))

    def test_a_preview_with_nothing_to_do_arms_nothing(self):
        """Applying a plan that changes nothing is a button offering a no-op."""
        self._write(self.si_dir, "settled2.png")
        node = self._make_file_node("tex_settled2", "sourceimages/settled2.png")
        self._dry(dest_dir=self.si_dir)

        self.assertIsNone(self._run([node]))
        self.assertIn("nothing would change", "\n".join(m for _lvl, m in self.reported))

    def test_the_preview_promises_the_path_the_commit_writes(self):
        """Both derive it through ``_plan_remap`` — a preview that could
        promise a different path is the one failure a preview must not have."""
        node = self._make_file_node("tex_same", self._write(self.ext_dir, "same.png"))
        self._dry(dest_dir=self.si_dir)

        commit = self._run([node])
        promised = "\n".join(m for _lvl, m in self.reported)
        commit()

        stored = self._path_of(node)
        self.assertEqual(stored, "sourceimages/same.png")
        self.assertIn(stored, promised)

    def test_a_long_plan_says_it_was_truncated(self):
        """A cut listing that does not SAY it was cut reads as the whole plan."""
        nodes = []
        for i in range(TexturePathEditorSlots._PLAN_PREVIEW_ROWS + 3):
            name = f"many{i}.png"
            nodes.append(
                self._make_file_node(f"tex_many{i}", self._write(self.ext_dir, name))
            )
        self._dry()

        self._run(nodes)

        report = "\n".join(m for _lvl, m in self.reported)
        self.assertIn("and 3 more", report)

    # -- the validator: the reported mistake, refused before any file op ------

    def test_the_same_folder_twice_is_refused(self):
        """Aiming the destination at the folder being searched relocates
        nothing, reports success, and leaves the user believing it worked."""
        error = TexturePathEditorSlots._validate_find_and_copy(
            {"source_dir": self.ext_dir, "dest_dir": self.ext_dir}
        )
        self.assertIn("nothing would move", error)

    def test_the_same_folder_spelled_differently_is_still_refused(self):
        """Typed by hand vs picked: different case, slashes and trailing sep."""
        error = TexturePathEditorSlots._validate_find_and_copy(
            {
                "source_dir": self.ext_dir.replace("/", "\\").upper(),
                "dest_dir": self.ext_dir + "/",
            }
        )
        self.assertIn("nothing would move", error)

    def test_an_empty_destination_is_refused(self):
        error = TexturePathEditorSlots._validate_find_and_copy(
            {"source_dir": self.ext_dir, "dest_dir": ""}
        )
        self.assertIn("destination", error.lower())

    def test_two_different_folders_are_accepted(self):
        self.assertEqual(
            TexturePathEditorSlots._validate_find_and_copy(
                {"source_dir": self.ext_dir, "dest_dir": self.dest_dir}
            ),
            "",
        )

    def test_an_empty_source_with_a_destination_is_accepted(self):
        """Skipping the search is a legitimate answer, not an incomplete form."""
        self.assertEqual(
            TexturePathEditorSlots._validate_find_and_copy(
                {"source_dir": "", "dest_dir": self.dest_dir}
            ),
            "",
        )

    def test_the_dialog_is_wired_to_the_validator(self):
        """A validator nothing calls would refuse nothing."""
        node = self._make_file_node("tex_w", self._write(self.ext_dir, "wired.png"))
        self._answer()

        self._run([node], relocate_mode="copy")

        self.assertIs(
            self.panel_calls[0]["validate"],
            TexturePathEditorSlots._validate_find_and_copy,
        )

    # -- destination handling -------------------------------------------------

    def test_a_destination_that_does_not_exist_yet_is_created(self):
        """Typed as often as browsed — a new folder is a normal answer."""
        node = self._make_file_node("tex_new", self._write(self.ext_dir, "new.png"))
        fresh = os.path.join(self.tmp_root, "brand_new")
        self._answer(dest_dir=fresh)

        self._run([node], relocate_mode="copy")

        self.assertTrue(os.path.exists(os.path.join(fresh, "new.png")))

    def test_no_sourceimages_setting_leaves_the_destination_empty(self):
        """No silent guess: the row is blank and the validator refuses it."""
        node = self._make_file_node("tex_no_si", self._write(self.ext_dir, "x.png"))
        EnvUtils.get_env_info = staticmethod(
            lambda key: "" if key == "sourceimages" else self.tmp_root
        )
        self.form_answers = None

        self._run([node], relocate_mode="copy")

        self.assertEqual(self._fields()["dest_dir"]["value"], "")
        self.assertEqual(
            self._path_of(node), os.path.join(self.ext_dir, "x.png").replace("\\", "/")
        )

    # -- repath bookkeeping ---------------------------------------------------

    def test_repath_records_the_previous_path_for_the_tooltip(self):
        """Every other path command feeds ``_previous_paths``; this one skipped it."""
        node = self._make_file_node("tex_prev", self._write(self.ext_dir, "prev.png"))
        before = self._path_of(node)
        self._answer()

        self._run([node], relocate_mode="copy")

        self.assertEqual(self.slot._previous_paths.get(node), before)

    def test_a_path_that_is_already_final_is_not_rewritten(self):
        """Re-running over the same destination must not dirty every plug.

        The second run finds every texture already there and already stored in
        its final relative form — rewriting it would reload every texture and
        report them all as remapped.
        """
        self._write(self.si_dir, "settled.png")
        node = self._make_file_node("tex_settled", "sourceimages/settled.png")
        self._answer(dest_dir=self.si_dir)

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
            self._run([node], relocate_mode="copy")
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
        self._answer(dest_dir=self.si_dir, mode="Move")

        self._run([node], relocate_mode="move")

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
        self._answer(source_dir=stale_dir)

        self._run([good, gone], relocate_mode="copy")

        with open(os.path.join(self.dest_dir, "dup.png")) as fh:
            self.assertEqual(fh.read(), "THE REAL ONE")


class TestOptionFlagReader(unittest.TestCase):
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
        btn = self._button(chk_allow_missing=True, chk_truncate_paths=False)
        self.assertTrue(read(btn, "chk_allow_missing", False))
        self.assertFalse(read(btn, "chk_truncate_paths", True))

    def test_absent_checkbox_falls_back_to_the_default(self):
        read = TexturePathEditorSlots._read_option_flag
        self.assertFalse(read(self._button(), "chk_allow_missing", False))
        self.assertTrue(read(self._button(), "chk_warn_path_length", True))

    def test_no_button_at_all_falls_back_to_the_default(self):
        """The workflow stays callable without a built option box."""
        read = TexturePathEditorSlots._read_option_flag
        self.assertFalse(read(None, "chk_allow_missing", False))
        self.assertTrue(read(None, "chk_warn_path_length", True))


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

        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/flat.png"
        )

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
        # Real constructor, stub switchboard: robust to whatever ``__init__``
        # seeds (see TestFindAndCopyPanel.setUp).
        sb = SimpleNamespace(
            progress=lambda *a, **kw: _NullProgress(),
            progress_adapter=lambda update: None,
            loaded_ui=SimpleNamespace(
                texture_path_editor=SimpleNamespace(
                    tbl000=SimpleNamespace(init_slot=lambda: None)
                )
            ),
        )
        self.slot = TexturePathEditorSlots(sb)

        self.slot._execute_find_and_copy(
            [node],
            {"source_dir": "", "dest_dir": self.si_dir, "mode": "Copy"},
        )

        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/found.png"
        )


class TestRelativePathsAcrossTheReopen(MayaTkTestCase):
    """What a normalized path does across save / open / save.

    ``TestRelativePathsSurviveTheWrite`` pins the write; this pins the round
    trip through disk. The stored form is ROOT-relative
    (``sourceimages/foo.png``) — Maya's own spelling, the first thing its
    loader tries, and the only relative form the FBX plug-in can locate at
    write time. Two consequences pull in opposite directions and both are
    pinned here:

    - the SAVED scene carries the relative path, which is the portability
      that matters when a ``.ma`` is handed to another machine; and
    - Maya EXPANDS that path back to absolute as it LOADS the scene, so the
      next save writes the absolute form back (measured across three
      generations, ``test/temp_tests/probe_root_relative_reopen.py``).

    The recovery is what makes that trade safe, so it is pinned too:
    Normalize is idempotent and restores the relative form in one click, and
    the exporter runs the same conversion on every export, so what SHIPS is
    never affected.

    The RULE-relative form (``foo.png``) emitted between 2026-08-18 and
    2026-08-25 is the one that survives a reopen verbatim, but it names no
    folder, the FBX writer cannot locate it, and the exporter's own gate read
    it as a missing texture — see
    ``test_the_legacy_rule_relative_form_is_upgraded_in_place``.

    A real ``workspace.mel`` is mandatory here: without the file rule the
    roots resolve differently and every assertion below would pass or fail
    for the wrong reason.
    """

    def setUp(self):
        super().setUp()
        self.tmp_root = tempfile.mkdtemp(prefix="ftn_reopen_test_")
        self.si_dir = os.path.join(self.tmp_root, "sourceimages")
        self.scenes_dir = os.path.join(self.tmp_root, "scenes")
        os.makedirs(self.si_dir, exist_ok=True)
        os.makedirs(self.scenes_dir, exist_ok=True)
        with open(os.path.join(self.tmp_root, "workspace.mel"), "w") as fh:
            fh.write(
                'workspace -fr "sourceImages" "sourceimages";\n'
                'workspace -fr "scene" "scenes";\n'
                'workspace -fr "mayaAscii" "scenes";\n'
            )

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
        cmds.file(new=True, force=True)
        if self._original_workspace:
            cmds.workspace(self._original_workspace, openWorkspace=True)
        super().tearDown()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    @staticmethod
    def _png(path, size):
        """A REAL PNG — ``outSizeX`` is how we prove WHICH file resolved."""
        import struct
        import zlib

        raw = b"".join(b"\x00" + b"\xff\x00\x00" * size for _ in range(size))

        def chunk(tag, data):
            body = tag + data
            return (
                struct.pack(">I", len(data))
                + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            )

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b"")
            )
        return path.replace("\\", "/")

    def _texture(self, relative_name, size=4):
        return self._png(os.path.join(self.si_dir, relative_name), size)

    def _node(self, name, path):
        node = cmds.shadingNode("file", asTexture=True, name=name)
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        return node

    def _save(self, name):
        scene = os.path.join(self.scenes_dir, name).replace("\\", "/")
        cmds.file(rename=scene)
        cmds.file(save=True, type="mayaAscii", force=True)
        return scene

    def _reopen(self, scene):
        cmds.file(new=True, force=True)
        cmds.workspace(self.tmp_root, openWorkspace=True)
        cmds.file(scene, open=True, force=True)

    @staticmethod
    def _stored_on_disk(scene):
        with open(scene, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if 'setAttr ".ftn"' in line:
                    return line.strip().split('"string" ')[-1].strip(';"')
        return None

    def test_normalize_stores_the_root_relative_form(self):
        """The folder is part of the path — the user's own spelling."""
        self._node("tex_form", self._texture("form.png"))

        self.slot._normalize_to_relative(["tex_form"])

        self.assertEqual(
            cmds.getAttr("tex_form.fileTextureName"), "sourceimages/form.png"
        )

    def test_the_saved_scene_carries_the_relative_path(self):
        """The portability that matters: the ``.ma`` on disk is machine-independent.

        This is what a colleague opening the scene from a different drive
        letter or Dropbox mount actually reads.
        """
        self._node("tex_saved", self._texture("saved.png"))
        self.slot._normalize_to_relative(["tex_saved"])

        stored = self._stored_on_disk(self._save("saved.ma"))

        self.assertEqual(stored, "sourceimages/saved.png")

    def test_maya_expands_the_relative_path_on_reopen(self):
        """The known cost of the root-relative form, pinned so it cannot surprise.

        Maya resolves the path against the project root as it loads and keeps
        the ABSOLUTE result in memory, so the next save writes that back and
        the panel shows absolute paths in the next session. The test below is
        the recovery. Should Maya ever stop doing this, THIS test fails first
        and the trade documented on ``to_project_relative`` is stale.
        """
        self._node("tex_expand", self._texture("expand.png"))
        self.slot._normalize_to_relative(["tex_expand"])

        self._reopen(self._save("expand.ma"))

        self.assertTrue(
            os.path.isabs(cmds.getAttr("tex_expand.fileTextureName")),
            "Maya no longer expands a root-relative .ftn on load",
        )

    def test_normalize_restores_the_relative_form_after_a_reopen(self):
        """One click puts it back — which is what makes the expansion survivable."""
        self._node("tex_restore", self._texture("restore.png"))
        self.slot._normalize_to_relative(["tex_restore"])
        self._reopen(self._save("restore.ma"))

        self.slot._normalize_to_relative(["tex_restore"])

        self.assertEqual(
            cmds.getAttr("tex_restore.fileTextureName"), "sourceimages/restore.png"
        )

    def test_normalize_is_idempotent_across_generations(self):
        """Three save/open generations, normalized each time: no drift."""
        self._node("tex_gen", self._texture("gen.png"))

        scene = self._save("gen0.ma")
        for generation in range(3):
            self._reopen(scene)
            self.slot._normalize_to_relative(["tex_gen"])
            self.assertEqual(
                cmds.getAttr("tex_gen.fileTextureName"),
                "sourceimages/gen.png",
                f"drifted at generation {generation}",
            )
            scene = self._save(f"gen{generation + 1}.ma")

    def test_the_relative_form_still_loads_the_right_image(self):
        """A stable string that resolves to nothing is not a fix.

        ``outSizeX`` is read from the decoded image, so it names the file Maya
        actually found.
        """
        self._node("tex_loads", self._texture("loads.png", size=8))
        self.slot._normalize_to_relative(["tex_loads"])

        self._reopen(self._save("loads.ma"))

        self.assertEqual(cmds.getAttr("tex_loads.outSizeX"), 8.0)

    def test_a_subfolder_under_sourceimages_keeps_its_subfolder(self):
        """Relativizing must not flatten ``sourceimages/sub/…`` to a basename."""
        self._node("tex_sub", self._texture("sub/deep.png", size=16))

        self.slot._normalize_to_relative(["tex_sub"])

        self.assertEqual(
            cmds.getAttr("tex_sub.fileTextureName"), "sourceimages/sub/deep.png"
        )

        self._reopen(self._save("sub.ma"))

        self.assertEqual(cmds.getAttr("tex_sub.outSizeX"), 16.0)

    def test_a_namesake_at_the_project_root_no_longer_shadows_the_form(self):
        """The hazard the RULE-relative form carried, and why this one drops it.

        A bare ``dup.png`` resolves against the project ROOT first, so with
        ``<proj>/dup.png`` present it silently bound to the WRONG image — and
        the converter had to refuse the relative form entirely, keeping the
        texture on an absolute path. ``sourceimages/dup.png`` names the
        folder, so there is nothing to shadow and it relativizes like any
        other texture.
        """
        self._png(os.path.join(self.tmp_root, "dup.png"), 32)  # the former shadow
        self._node("tex_dup", self._texture("dup.png", size=4))

        self.slot._normalize_to_relative(["tex_dup"])

        self.assertEqual(
            cmds.getAttr("tex_dup.fileTextureName"), "sourceimages/dup.png"
        )

        self._reopen(self._save("dup.ma"))

        self.assertEqual(
            cmds.getAttr("tex_dup.outSizeX"),
            4.0,
            "the stored path resolved to the root namesake, not the texture",
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


class TestFindAndCopyLightmaps(MayaTkTestCase):
    """Find & Copy takes the lightmap dependencies along, and names what it
    could not find.

    Reported 2026-08-26 on a migrated room: the panel copied every texture and
    the WebXR preview came back unlit -- the baked EXRs are referenced by bake
    markers, not file nodes, so no path command saw them -- and the export
    then failed on two textures the search had silently missed. Same harness
    shape as TestFindAndCopyPanel: the window is stubbed, the two seams
    (composing the rows, doing the work) are driven directly.
    """

    def setUp(self):
        super().setUp()
        self.tmp_root = tempfile.mkdtemp(prefix="find_copy_lm_")
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)
        self.si_dir = os.path.join(self.tmp_root, "sourceimages")
        self.ext_dir = os.path.join(self.tmp_root, "external")
        self.dest_dir = os.path.join(self.tmp_root, "dest")
        for d in (self.si_dir, self.ext_dir, self.dest_dir):
            os.makedirs(d, exist_ok=True)

        original = EnvUtils.get_env_info

        def fake_get_env_info(key):
            if key == "sourceimages":
                return self.si_dir
            if key == "workspace":
                return self.tmp_root
            return original(key)

        EnvUtils.get_env_info = staticmethod(fake_get_env_info)
        self.addCleanup(
            lambda: setattr(EnvUtils, "get_env_info", staticmethod(original))
        )

        self.reported = []  # (level, message) the panel's pane would show
        test = self

        class _StubLogger:
            def __getattr__(self, level):
                def emit(message, *_args, **_kwargs):
                    test.reported.append((level, str(message)))

                return emit

            def log_group(self, title, items, level="info"):
                test.reported.append((level, "\n".join([str(title), *map(str, items)])))

        class _StubPanel:
            def __init__(self):
                self.logger = _StubLogger()
                self.footer = SimpleNamespace(setDefaultStatusText=lambda *a: None)

            def set_fields(self, fields):
                test.panel_calls.append({"fields": [dict(f) for f in fields]})

            def present(self):
                pass

        self.panel_calls = []

        def fake_form_panel(fields, **kwargs):
            self.panel_calls.append({"fields": [dict(f) for f in fields], **kwargs})
            return _StubPanel()

        self.sb = SimpleNamespace(
            form_panel=fake_form_panel,
            tooltip=SimpleNamespace(fmt=lambda **kw: str(kw)),
            progress=lambda *a, **kw: _NullProgress(),
            progress_adapter=lambda update: None,
        )
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        self.slot.sb = self.sb
        self.slot.ui = SimpleNamespace(tbl000=SimpleNamespace(init_slot=lambda: None))
        self.slot._previous_paths = {}
        self.slot._find_copy_panel = None
        self.slot._find_copy_nodes = []
        self.slot._find_copy_mode = "copy"
        self.slot._find_copy_scope_label = ""
        self.slot._lightmap_rows = {}
        self.slot._find_copy_lightmaps = []
        self.slot._active_logger = None

    # -- helpers --------------------------------------------------------------

    def _write(self, directory, name, payload="payload"):
        os.makedirs(directory, exist_ok=True)
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

    @staticmethod
    def _baker():
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker()

    def _lit_cube(self, name, map_path):
        cube = cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]
        self._baker().commit_lightmap({cube: map_path})
        return cube

    @staticmethod
    def _marker_dir(cube):
        import json

        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        info = json.loads(cmds.getAttr(f"{cube}.{LightmapBaker.LIGHTMAP_INFO_ATTR}"))
        # The marker stores the portable spelling; compare what it resolves to.
        return os.path.normcase(
            os.path.abspath(LightmapBaker._resolved_dir(info["dir"], info["map"]))
        )

    def _norm(self, path):
        return os.path.normcase(os.path.abspath(path))

    def _run(self, nodes, lightmaps=None, **answers):
        """Open the panel over *nodes* (+ *lightmaps*), then press Run."""
        answers.setdefault("source_dir", "")
        answers.setdefault("dest_dir", self.dest_dir)
        answers.setdefault("mode", "Copy")
        self.slot._find_and_copy_workflow([str(n) for n in nodes], lightmaps=lightmaps)
        return self.slot._run_find_and_copy_over(
            list(self.slot._find_copy_nodes), answers
        )

    def _messages(self, level=None):
        return [m for lvl, m in self.reported if level is None or lvl == level]

    # -- what the search did not find -----------------------------------------

    def test_names_the_unresolved_textures_the_search_did_not_find(self):
        good = self._make_file_node("tex_ok", self._write(self.ext_dir, "ok.png"))
        gone = self._make_file_node(
            "tex_gone",
            os.path.join(self.ext_dir, "never_there.png").replace("\\", "/"),
        )
        search = os.path.join(self.tmp_root, "search")
        self._write(search, "unrelated.png")

        self._run([good, gone], source_dir=search)

        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "ok.png")))
        not_found = [m for m in self._messages("warning") if "not found under" in m]
        self.assertEqual(len(not_found), 1, self.reported)
        self.assertIn("never_there.png", not_found[0])
        self.assertIn(gone, not_found[0])
        still = [m for m in self._messages("warning") if "still unresolved" in m]
        self.assertEqual(len(still), 1, self.reported)
        self.assertIn(gone, still[0])

    def test_a_clean_run_reports_nothing_unresolved(self):
        node = self._make_file_node("tex_clean", self._write(self.ext_dir, "clean.png"))

        self._run([node])

        self.assertFalse([m for m in self._messages("warning") if "unresolved" in m])

    def test_a_tiled_node_is_repathed_once_its_tiles_land(self):
        """Regression: the remap matched stored basenames literally, so a
        <UDIM> node whose tiles had just been copied kept its old path."""
        for tile in ("rock.1001.png", "rock.1002.png"):
            self._write(self.ext_dir, tile)
        node = self._make_file_node(
            "tex_udim", os.path.join(self.ext_dir, "rock.<UDIM>.png").replace("\\", "/")
        )

        self._run([node], source_dir=self.ext_dir)

        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "rock.1002.png")))
        self.assertTrue(
            self._path_of(node).endswith("dest/rock.<UDIM>.png"), self._path_of(node)
        )

    # -- lightmaps ride along -------------------------------------------------

    def test_lightmaps_are_copied_and_their_markers_repointed(self):
        node = self._make_file_node("tex_lm", self._write(self.ext_dir, "wall.png"))
        cube = self._lit_cube("lit", self._write(self.ext_dir, "lit_LightMap.exr"))
        deps = self._baker().lightmap_dependencies()

        self._run([node], lightmaps=deps)

        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "lit_LightMap.exr")))
        self.assertEqual(self._marker_dir(cube), self._norm(self.dest_dir))
        self.assertTrue(
            any("Lightmaps —" in m for m in self._messages("success")), self.reported
        )
        # The button counted them, so the scope was visible before Run.
        self.assertEqual(
            self.slot._find_and_copy_ok_text({"mode": "Copy"}),
            "Copy 1 texture(s) + 1 lightmap(s)",
        )

    def test_the_lightmaps_have_no_opt_out_row(self):
        """The scope already answered this. A row asking again could only
        contradict the selection that opened the panel — and an answer that
        contradicts the scope is the bug, not the feature."""
        node = self._make_file_node("tex_opt", self._write(self.ext_dir, "opt.png"))
        cube = self._lit_cube(
            "optout", self._write(self.ext_dir, "optout_LightMap.exr")
        )

        self._run([node], lightmaps=self._baker().lightmap_dependencies())

        names = [f["name"] for f in self.panel_calls[0]["fields"]]
        self.assertNotIn("include_lightmaps", names)
        # ...and the scoped lightmap rode along without being asked about.
        self.assertTrue(
            os.path.exists(os.path.join(self.dest_dir, "optout_LightMap.exr"))
        )
        self.assertEqual(self._marker_dir(cube), self._norm(self.dest_dir))

    def test_a_missing_lightmap_is_searched_for_in_the_source_folder(self):
        cube = self._lit_cube(
            "lost", os.path.join(self.tmp_root, "gone", "lost_LightMap.exr")
        )
        self._write(os.path.join(self.ext_dir, "deep"), "lost_LightMap.exr")
        deps = self._baker().lightmap_dependencies()
        self.assertIsNone(deps[0]["path"])

        self._run([], lightmaps=deps, source_dir=self.ext_dir)

        self.assertTrue(
            os.path.exists(os.path.join(self.dest_dir, "lost_LightMap.exr"))
        )
        self.assertEqual(self._marker_dir(cube), self._norm(self.dest_dir))

    def test_a_lightmap_only_scope_runs_and_a_missing_one_is_named(self):
        self._lit_cube("nowhere", os.path.join(self.tmp_root, "gone", "nowhere.exr"))

        self._run(
            [], lightmaps=self._baker().lightmap_dependencies(), source_dir=self.ext_dir
        )

        missing = [m for m in self._messages("warning") if "found nowhere" in m]
        self.assertEqual(len(missing), 1, self.reported)
        self.assertIn("nowhere.exr", missing[0])
        self.assertFalse(any("No textures found" in m for m in self._messages()))

    def test_dry_run_plans_the_lightmaps_and_touches_nothing(self):
        cube = self._lit_cube("dry", self._write(self.ext_dir, "dry_LightMap.exr"))
        before = self._marker_dir(cube)

        apply_call = self._run(
            [], lightmaps=self._baker().lightmap_dependencies(), dry_run=True
        )

        self.assertFalse(
            os.path.exists(os.path.join(self.dest_dir, "dry_LightMap.exr"))
        )
        self.assertEqual(self._marker_dir(cube), before)
        self.assertTrue(
            any("lightmap(s) into" in m for m in self._messages()), self.reported
        )
        self.assertIsNotNone(apply_call, "a plan with work in it arms Apply")
        apply_call()
        self.assertTrue(os.path.exists(os.path.join(self.dest_dir, "dry_LightMap.exr")))
        self.assertEqual(self._marker_dir(cube), self._norm(self.dest_dir))

    def test_the_search_hint_counts_missing_lightmaps(self):
        self._lit_cube(
            "hinted", os.path.join(self.tmp_root, "gone", "hinted_LightMap.exr")
        )

        fields = {
            f["name"]: f
            for f in self.slot._find_and_copy_fields(
                [], [], self.si_dir, lightmaps=self._baker().lightmap_dependencies()
            )
        }

        self.assertTrue(fields["source_dir"]["enabled"])
        self.assertIn("hinted_LightMap.exr", fields["source_dir"]["hint"])
        self.assertIn("1 unresolved", fields["source_dir"]["hint"])
        # The counts come off what is WANTED, not off the file nodes: a
        # lightmap-only scope has none, and a placeholder reading "all 0
        # path(s) are missing" makes the rest of the form untrustworthy.
        placeholder = fields["source_dir"]["placeholder"]
        self.assertEqual(placeholder, "1 path(s) require a search dir")
        # ...and with no file node in scope, the hint drops the resolving
        # clause rather than offering to "relocate the 0 that already resolve".
        self.assertNotIn("relocate the 0", fields["source_dir"]["hint"])

    def test_a_lightmap_only_scope_that_resolves_says_so_without_counting_nodes(self):
        """Same trap on the settled side: "All 0 path(s) resolve" is nonsense."""
        self._lit_cube("found", self._write(self.ext_dir, "found_LightMap.exr"))

        fields = {
            f["name"]: f
            for f in self.slot._find_and_copy_fields(
                [], [], self.si_dir, lightmaps=self._baker().lightmap_dependencies()
            )
        }

        source = fields["source_dir"]
        self.assertFalse(source["enabled"])
        self.assertIn("Nothing in scope needs finding", source["hint"])
        self.assertNotIn("All 0", source["hint"])

    # -- lightmap rows in the table -------------------------------------------

    def test_a_selected_lightmap_row_is_a_scope_of_its_own(self):
        dep = {
            "map": "x.exr",
            "dir": self.ext_dir,
            "objects": [],
            "path": None,
            "found_by": None,
            "note": "",
        }
        path = self.slot._lightmap_row_path(dep)
        self.slot._lightmap_rows = {path: dep}
        entry = SimpleNamespace(
            values={"shader": "<lightmap>", "path": path, "file_node": ""}
        )
        self.slot.ui.tbl000 = SimpleNamespace(
            init_slot=lambda: None, get_selection=lambda **kw: [entry]
        )

        self.assertEqual(self.slot._get_scope_lightmaps(), [dep])
        nodes, label = self.slot._get_scope_nodes()
        self.assertEqual(nodes, [])
        self.assertIn("lightmap", label)
        # File-node-only commands see nothing here.
        self.assertEqual(self.slot._file_nodes_from_selection(None), [])

    def test_no_selection_scopes_to_every_lightmap_row_shown(self):
        dep = {
            "map": "y.exr",
            "dir": "",
            "objects": [],
            "path": None,
            "found_by": None,
            "note": "",
        }
        self.slot._lightmap_rows = {"y.exr": dep}
        self.slot.ui.tbl000 = SimpleNamespace(
            init_slot=lambda: None, get_selection=lambda **kw: []
        )

        self.assertEqual(self.slot._get_scope_lightmaps(), [dep])


if __name__ == "__main__":
    unittest.main(verbosity=2)
