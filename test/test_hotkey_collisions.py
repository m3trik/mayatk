# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.ui_utils.hotkey_collisions.

Two layers:
- Pure parser tests (``parse_qt_sequence``) — no live Maya state needed.
- Integration tests — bind a real Maya runtimeCommand, query it through
  the checker, assert a CollisionConflict is reported.
"""
import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.ui_utils.hotkey_collisions import (
    parse_qt_sequence,
    keystring_to_token,
    maya_collision_checker,
)


class TestParseQtSequence(unittest.TestCase):
    """Pure parser — no Maya state required."""

    def test_simple_letter(self):
        self.assertEqual(parse_qt_sequence("S"), {"keyShortcut": "s"})

    def test_ctrl_letter(self):
        self.assertEqual(
            parse_qt_sequence("Ctrl+S"),
            {"keyShortcut": "s", "ctrlModifier": True},
        )

    def test_ctrl_alt_letter(self):
        self.assertEqual(
            parse_qt_sequence("Ctrl+Alt+S"),
            {"keyShortcut": "s", "ctrlModifier": True, "altModifier": True},
        )

    def test_shift_letter_uppercases(self):
        # Maya wants the uppercase glyph for shift+letter, no shift flag.
        self.assertEqual(
            parse_qt_sequence("Shift+S"),
            {"keyShortcut": "S"},
        )

    def test_function_key_passthrough(self):
        self.assertEqual(parse_qt_sequence("F5"), {"keyShortcut": "F5"})

    def test_named_key_fixup(self):
        self.assertEqual(parse_qt_sequence("Esc"), {"keyShortcut": "Escape"})
        self.assertEqual(parse_qt_sequence("Return"), {"keyShortcut": "Enter"})

    def test_meta_returns_none(self):
        self.assertIsNone(parse_qt_sequence("Meta+S"))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_qt_sequence(""))
        self.assertIsNone(parse_qt_sequence(None))

    def test_multistep_returns_none(self):
        # "Ctrl+K, Ctrl+S" is a multi-step sequence Maya can't represent
        # as a single hotkey query.
        self.assertIsNone(parse_qt_sequence("Ctrl+K, Ctrl+S"))


class TestKeystringToToken(unittest.TestCase):
    """Pure keyString -> token conversion — no Maya state required."""

    # 7-element Maya 2025 layout: [key, alt, ctrl, ?, shift, release, ?]
    def _ks(self, key, alt="0", ctrl="0", shift="0"):
        return [key, alt, ctrl, "0", shift, "0", "0"]

    def test_ctrl_letter(self):
        # Maya stores a non-shifted letter as its lower-case glyph.
        self.assertEqual(keystring_to_token(self._ks("i", ctrl="1")), "ctl+i")

    def test_uppercase_letter_becomes_shift(self):
        # Maya stores shift+letter as the upper-case glyph, no shift flag.
        self.assertEqual(keystring_to_token(self._ks("K")), "sht+k")

    def test_empty_array(self):
        self.assertEqual(keystring_to_token([]), "")
        self.assertEqual(keystring_to_token(None), "")

    def test_empty_key(self):
        self.assertEqual(keystring_to_token(self._ks("")), "")

    def test_none_sentinel_key_is_empty(self):
        """Maya reports a keyless (cleared) command's key as the string 'NONE'.

        Regression: that literal leaked through to the Macro Manager and
        rendered as the hotkey 'NONE' (and two such entries falsely collided).
        Case varies across Maya versions, so the guard is case-insensitive.
        """
        for sentinel in ("NONE", "None", "none"):
            self.assertEqual(keystring_to_token(self._ks(sentinel)), "", sentinel)
        # …even if a stray modifier flag is set on the orphan entry.
        self.assertEqual(keystring_to_token(self._ks("NONE", ctrl="1")), "")


class TestMayaCollisionChecker(MayaTkTestCase):
    """Integration tests — exercise the real cmds.hotkey API."""

    # The hotkeys we touch are restored to their previous state via a
    # fresh user hotkey set we create per-test.
    TEST_SET = "mayatk_test_hotkey_collisions"
    RUNTIME_CMD = "mayatk_test_hotkey_collisions_cmd"

    def setUp(self):
        super().setUp()
        # Ensure runtime command exists and is bindable. Recreate idempotently.
        if cmds.runTimeCommand(self.RUNTIME_CMD, exists=True):
            cmds.runTimeCommand(self.RUNTIME_CMD, edit=True, delete=True)
        cmds.runTimeCommand(
            self.RUNTIME_CMD,
            annotation="mayatk hotkey-collision test",
            command="print('mayatk test')",
            category="Custom Scripts",
        )

        # Create or switch to a disposable hotkey set so we don't mutate
        # the user's active set.
        if cmds.hotkeySet(self.TEST_SET, exists=True):
            cmds.hotkeySet(self.TEST_SET, edit=True, delete=True)
        cmds.hotkeySet(self.TEST_SET, source="Maya_Default")
        cmds.hotkeySet(self.TEST_SET, edit=True, current=True)

        # Bind the runtime command to Ctrl+Alt+J for the press event.
        cmds.nameCommand(
            "mayatk_test_press_nc",
            command=self.RUNTIME_CMD,
            annotation="mayatk test press name command",
        )
        cmds.hotkey(
            keyShortcut="j",
            ctrlModifier=True,
            altModifier=True,
            name="mayatk_test_press_nc",
        )

    def tearDown(self):
        # Unbind first so deleting the runtime command is safe.
        try:
            cmds.hotkey(
                keyShortcut="j",
                ctrlModifier=True,
                altModifier=True,
                name="",
                releaseName="",
            )
        except Exception:
            pass
        try:
            if cmds.hotkeySet(self.TEST_SET, exists=True):
                # Leave the default set active before deleting our set.
                cmds.hotkeySet("Maya_Default", edit=True, current=True)
                cmds.hotkeySet(self.TEST_SET, edit=True, delete=True)
        except Exception:
            pass
        try:
            if cmds.runTimeCommand(self.RUNTIME_CMD, exists=True):
                cmds.runTimeCommand(self.RUNTIME_CMD, edit=True, delete=True)
        except Exception:
            pass
        super().tearDown()

    def test_detects_bound_press_command(self):
        """A sequence that matches a bound hotkey should report a conflict."""
        conflicts = maya_collision_checker(
            "Ctrl+Alt+J", "application", "ui_x", "method_x"
        )
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c.source, "maya")
        self.assertFalse(c.breaks_binding)
        self.assertIn(self.RUNTIME_CMD, c.description)
        self.assertIn(self.TEST_SET, c.description)

    def test_unbound_sequence_returns_no_conflicts(self):
        """A sequence that isn't bound in Maya should report nothing."""
        # F19 is virtually never bound by default
        conflicts = maya_collision_checker(
            "Ctrl+Alt+F19", "application", "ui_x", "method_x"
        )
        self.assertEqual(conflicts, [])

    def test_unparseable_sequence_returns_no_conflicts(self):
        """Sequences the parser rejects should silently return []."""
        conflicts = maya_collision_checker("", "window", "ui_x", "method_x")
        self.assertEqual(conflicts, [])

    def test_conflict_in_editable_set_carries_clear_action(self):
        """In an editable (user) hotkey set, a Maya conflict is clearable: it
        carries a callable clear_action so the editor can free the binding.
        (setUp makes TEST_SET — a user set — current, so unbinding is allowed.)"""
        conflicts = maya_collision_checker(
            "Ctrl+Alt+J", "application", "ui_x", "method_x"
        )
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(callable(conflicts[0].clear_action))

    def test_no_false_match_with_extra_modifiers(self):
        """Querying with Shift added shouldn't match a Ctrl+Alt-only binding."""
        # Setup binds Ctrl+Alt+J. Ctrl+Alt+Shift+J is a different shortcut
        # and must not be reported as colliding.
        conflicts = maya_collision_checker(
            "Ctrl+Alt+Shift+J", "application", "ui_x", "method_x"
        )
        self.assertEqual(conflicts, [])

    def test_detects_shift_letter_binding(self):
        """Shift+letter bindings should match via uppercase keyShortcut."""
        # Bind a shift+letter shortcut. Maya's convention is the upper-case
        # glyph as keyShortcut, no shiftModifier flag — same shape the
        # parser produces for "Shift+K".
        cmds.nameCommand(
            "mayatk_test_shift_nc",
            command=self.RUNTIME_CMD,
            annotation="mayatk shift letter test",
        )
        cmds.hotkey(keyShortcut="K", name="mayatk_test_shift_nc")

        conflicts = maya_collision_checker(
            "Shift+K", "application", "ui_x", "method_x"
        )
        self.assertEqual(len(conflicts), 1, msg=f"got: {conflicts}")
        self.assertEqual(conflicts[0].source, "maya")
        self.assertIn(self.RUNTIME_CMD, conflicts[0].description)


if __name__ == "__main__":
    unittest.main(verbosity=2)
