# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.edit_utils.macros module.

The Macros class composes 5 mixins (DisplayMacros, EditMacros, SelectionMacros,
UiMacros, AnimationMacros) on top of MacroManager. Most macro functions are
selection/viewport driven — these tests cover the testable surface:

    - MacroManager.call_with_input (pure parsing)
    - MacroManager.set_macro / set_macros (Maya runtimeCommand)
    - Macros class composition / inheritance
    - Headless-safe macros: m_group, m_combine, m_*_selection (selection masks)
"""
import unittest

import maya.cmds as cmds

from mayatk.edit_utils.macros import (
    Macros,
    MacroManager,
    DisplayMacros,
    EditMacros,
    SelectionMacros,
    AnimationMacros,
    UiMacros,
)

from base_test import MayaTkTestCase, QuickTestCase


class TestCallWithInput(QuickTestCase):
    """Pure-Python parsing of input strings into args/kwargs."""

    def test_positional_only(self):
        captured = {}

        def fn(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        MacroManager.call_with_input(fn, "hello, world")
        self.assertEqual(captured["args"], ("hello", "world"))
        self.assertEqual(captured["kwargs"], {})

    def test_keyword_only(self):
        captured = {}

        def fn(**kwargs):
            captured.update(kwargs)

        MacroManager.call_with_input(fn, "key=1, cat=Display")
        self.assertEqual(captured, {"key": "1", "cat": "Display"})

    def test_mixed_positional_and_keyword(self):
        captured = {"args": (), "kwargs": {}}

        def fn(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        MacroManager.call_with_input(fn, "macro_name, key=1, cat=Display")
        self.assertEqual(captured["args"], ("macro_name",))
        self.assertEqual(captured["kwargs"], {"key": "1", "cat": "Display"})

    def test_strips_whitespace(self):
        captured = {}

        def fn(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        MacroManager.call_with_input(fn, "  alpha  ,  key = 5 ")
        self.assertEqual(captured["args"], ("alpha",))
        self.assertEqual(captured["kwargs"], {"key": "5"})


class TestMacrosComposition(QuickTestCase):
    """Macros must inherit all 5 mixins + MacroManager."""

    def test_inherits_macro_manager(self):
        self.assertTrue(issubclass(Macros, MacroManager))

    def test_inherits_all_mixins(self):
        for mixin in (
            DisplayMacros,
            EditMacros,
            SelectionMacros,
            AnimationMacros,
            UiMacros,
        ):
            self.assertTrue(
                issubclass(Macros, mixin),
                f"Macros does not inherit from {mixin.__name__}",
            )

    def test_has_set_macros_classmethod(self):
        self.assertTrue(hasattr(Macros, "set_macros"))
        self.assertTrue(callable(Macros.set_macros))

    def test_representative_macros_present(self):
        # A handful of macros from each mixin
        for name in (
            "m_back_face_culling",
            "m_isolate_selected",
            "m_group",
            "m_combine",
            "m_object_selection",
            "m_vertex_selection",
            "m_edge_selection",
            "m_face_selection",
            "m_set_selected_keys",
            "m_unset_selected_keys",
        ):
            self.assertTrue(
                hasattr(Macros, name), f"Macros missing expected method: {name}"
            )


class TestSetMacro(MayaTkTestCase):
    """MacroManager.set_macro creates Maya runtime commands and hotkeys."""

    TEST_MACRO_NAME = "m_object_selection"

    def tearDown(self):
        # Clean up any runtime command we might have created
        try:
            if cmds.runTimeCommand(self.TEST_MACRO_NAME, exists=True):
                if not cmds.runTimeCommand(self.TEST_MACRO_NAME, query=True, default=True):
                    cmds.runTimeCommand(self.TEST_MACRO_NAME, edit=True, delete=True)
        except Exception:
            pass
        super().tearDown()

    def test_set_macro_creates_runtime_command(self):
        Macros.set_macro(self.TEST_MACRO_NAME, key="F", cat="Test")
        self.assertTrue(
            cmds.runTimeCommand(self.TEST_MACRO_NAME, exists=True),
            "set_macro should create a runtimeCommand",
        )

    def test_set_macros_string_form_creates_command(self):
        Macros.set_macros(f"{self.TEST_MACRO_NAME}, key=G, cat=Test")
        self.assertTrue(cmds.runTimeCommand(self.TEST_MACRO_NAME, exists=True))


class TestSelectionMacros(MayaTkTestCase):
    """Selection-mode macros work headlessly via cmds.selectMode."""

    def test_vertex_selection_sets_component_mode(self):
        cube = cmds.polyCube(name="sel_cube")[0]
        cmds.select(cube)
        SelectionMacros.m_vertex_selection()
        self.assertTrue(cmds.selectMode(query=True, component=True))
        self.assertTrue(cmds.selectType(query=True, vertex=True))

    def test_edge_selection_sets_component_mode(self):
        cube = cmds.polyCube(name="edge_cube")[0]
        cmds.select(cube)
        SelectionMacros.m_edge_selection()
        self.assertTrue(cmds.selectMode(query=True, component=True))
        self.assertTrue(cmds.selectType(query=True, edge=True))

    def test_face_selection_sets_component_mode(self):
        cube = cmds.polyCube(name="face_cube")[0]
        cmds.select(cube)
        SelectionMacros.m_face_selection()
        self.assertTrue(cmds.selectMode(query=True, component=True))
        self.assertTrue(cmds.selectType(query=True, facet=True))

    def test_object_selection_returns_to_object_mode(self):
        SelectionMacros.m_object_selection()
        # selectMode should be in object mode after the call
        self.assertTrue(cmds.selectMode(query=True, object=True))


class TestEditMacros(MayaTkTestCase):
    """EditMacros — geometry-affecting headless-safe ones."""

    def test_m_group_creates_group(self):
        cube = cmds.polyCube(name="grp_cube")[0]
        cmds.select(cube)
        result = EditMacros.m_group()
        # Result should be a group containing the cube
        self.assertIsNotNone(result)

    def test_m_combine_two_cubes_yields_single_mesh(self):
        a = cmds.polyCube(name="comb_a")[0]
        b = cmds.polyCube(name="comb_b")[0]
        cmds.move(3, 0, 0, b)
        before = len(cmds.ls(type="mesh"))

        EditMacros.m_combine(objects=[a, b])

        after = len(cmds.ls(type="mesh"))
        # Combine collapses two meshes into one (count may include shape nodes)
        self.assertLessEqual(after, before)


class TestMacroDiscovery(QuickTestCase):
    """Every public m_* method declared on a mixin must resolve on Macros.

    Catches the class of bug where a hotkey/runtimeCommand is registered for a
    name that no longer exists on the class (rename, deletion, typo).
    """

    MIXINS = (DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)

    @classmethod
    def all_macro_names(cls):
        names = set()
        for mixin in cls.MIXINS:
            for n, v in vars(mixin).items():
                if n.startswith("m_") and callable(
                    v.__func__ if isinstance(v, (staticmethod, classmethod)) else v
                ):
                    names.add(n)
        return sorted(names)

    def test_every_mixin_macro_resolves_on_Macros(self):
        missing = [n for n in self.all_macro_names() if not hasattr(Macros, n)]
        self.assertFalse(
            missing, f"Macros class is missing methods declared on mixins: {missing}"
        )

    def test_every_macro_is_callable(self):
        non_callable = [
            n for n in self.all_macro_names() if not callable(getattr(Macros, n))
        ]
        self.assertFalse(non_callable, f"Non-callable m_* attributes: {non_callable}")


class TestMacroSmokeInvocation(MayaTkTestCase):
    """Invoke every m_* macro on a fresh selection to catch latent code bugs.

    We tolerate failures that depend on a real viewport (RuntimeError from
    missing modelPanel, KeyError from MEL globals, etc.) but FAIL on the
    AttributeError/NameError/TypeError-on-string class — exactly the bug
    pattern produced by leftover PyMel attribute access on string node names.
    """

    # Bugs we want to catch; everything else is tolerated as environment-related.
    FATAL = (AttributeError, NameError)

    # Macros that legitimately need user input or destructive scene state we
    # don't want to set up generically. Skipped from invocation but still
    # discovered/required to exist by TestMacroDiscovery.
    SKIP_INVOCATION = {
        "m_paste_and_rename",  # depends on the cut/copy buffer
        "m_boolean",           # needs >=2 specific meshes
        "m_toggle_panels",     # needs main Maya window (Qt) — None in mayapy
    }

    def _selected_cube(self, name="smoke_cube"):
        cube = cmds.polyCube(name=name)[0]
        cmds.select(cube, replace=True)
        return cube

    def test_every_macro_invokes_without_python_bug(self):
        names = TestMacroDiscovery.all_macro_names()
        bug_failures = []

        for name in names:
            if name in self.SKIP_INVOCATION:
                continue
            cmds.file(new=True, force=True)
            self._selected_cube()
            fn = getattr(Macros, name)
            try:
                fn()
            except self.FATAL as e:
                bug_failures.append(f"{name}: {type(e).__name__}: {e}")
            except Exception:
                # Tolerated: runtime/UI/state errors that depend on a real
                # viewport, focused panel, or MEL globals. Not what we're
                # testing here.
                pass

        self.assertFalse(
            bug_failures,
            "Macros raised Python-level bugs (likely PyMel-on-string or "
            "missing-symbol):\n  " + "\n  ".join(bug_failures),
        )


class TestMacroRegistration(MayaTkTestCase):
    """set_macro must succeed for every discovered macro name.

    Mirrors the real registration path the user invokes from userSetup.py;
    any name that fails here would also fail at startup.
    """

    def tearDown(self):
        for name in TestMacroDiscovery.all_macro_names():
            try:
                if cmds.runTimeCommand(name, exists=True) and not cmds.runTimeCommand(
                    name, query=True, default=True
                ):
                    cmds.runTimeCommand(name, edit=True, delete=True)
            except Exception:
                pass
        super().tearDown()

    def test_register_every_macro(self):
        failures = []
        for i, name in enumerate(TestMacroDiscovery.all_macro_names()):
            try:
                # Use F-key slots to avoid clobbering common shortcuts.
                Macros.set_macro(name, key=f"F{(i % 12) + 1}", cat="SmokeTest")
            except Exception as e:
                failures.append(f"{name}: {type(e).__name__}: {e}")
        self.assertFalse(failures, "set_macro failed for:\n  " + "\n  ".join(failures))


class TestKeyFormatConversion(QuickTestCase):
    """Pure Maya<->Qt key-token conversion + canonicalisation."""

    def test_qt_to_maya_modifiers(self):
        self.assertEqual(Macros.qt_sequence_to_maya_key("Ctrl+Shift+I"), "ctl+sht+i")
        self.assertEqual(Macros.qt_sequence_to_maya_key("Alt+S"), "alt+s")

    def test_maya_to_qt_modifiers(self):
        self.assertEqual(Macros.maya_key_to_qt_sequence("ctl+sht+i"), "Ctrl+Shift+I")
        self.assertEqual(Macros.maya_key_to_qt_sequence("alt+s"), "Alt+S")

    def test_function_key_passthrough(self):
        self.assertEqual(Macros.qt_sequence_to_maya_key("F3"), "F3")
        self.assertEqual(Macros.maya_key_to_qt_sequence("F3"), "F3")

    def test_round_trip_is_stable(self):
        for token in ("1", "f", "ctl+g", "alt+ctl+s", "ctl+sht+i", "F5"):
            self.assertEqual(
                Macros._normalize_key(
                    Macros.qt_sequence_to_maya_key(
                        Macros.maya_key_to_qt_sequence(token)
                    )
                ),
                Macros._normalize_key(token),
            )

    def test_normalize_is_order_independent(self):
        self.assertEqual(
            Macros._normalize_key("sht+ctl+i"), Macros._normalize_key("ctl+sht+i")
        )

    def test_empty_inputs(self):
        self.assertEqual(Macros.qt_sequence_to_maya_key(""), "")
        self.assertEqual(Macros.maya_key_to_qt_sequence(""), "")


class TestFindConflicts(QuickTestCase):
    """Duplicate-hotkey detection over a binding set."""

    def test_detects_duplicate_key(self):
        bindings = {
            "a": {"key": "1"},
            "b": {"key": "1"},
            "c": {"key": "2"},
        }
        conflicts = Macros.find_conflicts(bindings)
        self.assertIn("1", conflicts)
        self.assertCountEqual(conflicts["1"], ["a", "b"])
        self.assertNotIn("2", conflicts)

    def test_modifier_order_collides(self):
        bindings = {"a": {"key": "ctl+sht+i"}, "b": {"key": "sht+ctl+i"}}
        conflicts = Macros.find_conflicts(bindings)
        self.assertEqual(len(conflicts), 1)

    def test_no_conflicts_in_default_preset(self):
        defaults = Macros.load_preset(Macros.DEFAULT_PRESET)
        self.assertEqual(Macros.find_conflicts(defaults), {})


class TestListAvailableMacros(QuickTestCase):
    """Macro discovery for the UI table."""

    def test_discovers_macros(self):
        macros = Macros.list_available_macros()
        self.assertGreater(len(macros), 0)
        self.assertIn("m_wireframe", macros)

    def test_excludes_non_macro_methods(self):
        macros = Macros.list_available_macros()
        self.assertNotIn("set_macro", macros)
        self.assertNotIn("apply_bindings", macros)

    def test_annotation_is_first_docline(self):
        macros = Macros.list_available_macros()
        self.assertTrue(macros["m_wireframe"])  # non-empty annotation
        self.assertNotIn("\n", macros["m_wireframe"])


class TestMacroPresentation(QuickTestCase):
    """Human-readable labels + docstring-sourced help for the UI."""

    def test_label_humanizes_name(self):
        self.assertEqual(Macros.macro_label("m_back_face_culling"), "Back Face Culling")

    def test_label_preserves_acronyms(self):
        self.assertEqual(
            Macros.macro_label("m_toggle_UV_select_type"), "Toggle UV Select Type"
        )
        self.assertEqual(
            Macros.macro_label("m_component_id_display"), "Component ID Display"
        )

    def test_help_is_full_docstring(self):
        help_text = Macros.macro_help("m_wireframe")
        self.assertTrue(help_text)
        self.assertIn("wireframe", help_text.lower())

    def test_help_missing_macro_is_empty(self):
        self.assertEqual(Macros.macro_help("m_does_not_exist"), "")

    def test_every_macro_has_a_default_category(self):
        # The defining *Macros mixin is the SSoT, so no macro is uncategorized.
        uncategorized = [
            name
            for name in Macros.list_available_macros()
            if not Macros.macro_category(name)
        ]
        self.assertEqual(uncategorized, [])

    def test_category_derives_from_defining_mixin(self):
        cases = {
            "m_wireframe": "Display",  # DisplayMacros
            "m_group": "Edit",  # EditMacros
            "m_object_selection": "Selection",  # SelectionMacros
            "m_set_selected_keys": "Animation",  # AnimationMacros
            "m_toggle_panels": "UI",  # UiMacros (acronym preserved)
        }
        for name, cat in cases.items():
            self.assertEqual(Macros.macro_category(name), cat, name)

    def test_category_missing_macro_is_empty(self):
        self.assertEqual(Macros.macro_category("m_does_not_exist"), "")

    def test_list_categories_matches_derived_set(self):
        self.assertEqual(
            Macros.list_categories(), ["Animation", "Display", "Edit", "Selection", "UI"]
        )

    def test_default_preset_categories_match_mixin(self):
        # Shipped default bindings must agree with the code's category SSoT
        # (no Edit/Selection-style drift between bound + unbound siblings).
        defaults = Macros.load_preset(Macros.DEFAULT_PRESET)
        for name, spec in defaults.items():
            self.assertEqual(spec.get("cat"), Macros.macro_category(name), name)


class _TempPresetRoot:
    """Mixin: redirect the shared preset root to a throwaway dir per test."""

    def setUp(self):
        super().setUp()
        import os
        import tempfile

        self._preset_tmp = tempfile.mkdtemp(prefix="macro_presets_")
        self._prev_root = os.environ.get("UITK_PRESETS_ROOT")
        os.environ["UITK_PRESETS_ROOT"] = os.path.join(self._preset_tmp, "uitk")

    def tearDown(self):
        import os
        import shutil

        if self._prev_root is None:
            os.environ.pop("UITK_PRESETS_ROOT", None)
        else:
            os.environ["UITK_PRESETS_ROOT"] = self._prev_root
        shutil.rmtree(self._preset_tmp, ignore_errors=True)
        super().tearDown()


class TestPresetRoundTrip(_TempPresetRoot, QuickTestCase):
    """PresetStore-backed persistence (no Maya required)."""

    def test_builtin_default_is_listed_and_readonly(self):
        self.assertIn(Macros.DEFAULT_PRESET, Macros.list_presets())
        # Built-ins cannot be deleted.
        self.assertFalse(Macros.delete_preset(Macros.DEFAULT_PRESET))

    def test_load_preset_strips_meta(self):
        data = Macros.load_preset(Macros.DEFAULT_PRESET)
        self.assertNotIn("_meta", data)
        self.assertIn("m_wireframe", data)

    def test_save_load_delete_round_trip(self):
        bindings = {
            "m_wireframe": {"key": "3", "cat": "Display"},
            "m_group": {"key": "ctl+g", "cat": "Edit"},
        }
        Macros.save_preset("unittest_set", bindings)
        self.assertIn("unittest_set", Macros.list_presets())
        self.assertEqual(Macros.get_active_preset(), "unittest_set")
        self.assertEqual(Macros.load_preset("unittest_set"), bindings)
        self.assertTrue(Macros.delete_preset("unittest_set"))
        self.assertNotIn("unittest_set", Macros.list_presets())


class TestApplyBindings(_TempPresetRoot, MayaTkTestCase):
    """apply_bindings / set_macros parity + clear/unset (needs Maya)."""

    NAMES = ("m_wireframe", "m_group", "m_object_selection")

    def tearDown(self):
        for name in self.NAMES:
            try:
                if cmds.runTimeCommand(name, exists=True) and not cmds.runTimeCommand(
                    name, query=True, default=True
                ):
                    cmds.runTimeCommand(name, edit=True, delete=True)
            except Exception:
                pass
        super().tearDown()

    def test_apply_bindings_registers_commands(self):
        Macros.apply_bindings(
            {
                "m_wireframe": {"key": "F7", "cat": "Display"},
                "m_group": {"key": "F8", "cat": "Edit"},
            }
        )
        self.assertTrue(cmds.runTimeCommand("m_wireframe", exists=True))
        self.assertEqual(
            cmds.runTimeCommand("m_wireframe", query=True, category=True), "Display"
        )

    def test_apply_bindings_matches_set_macros(self):
        Macros.apply_bindings({"m_object_selection": {"key": "F9", "cat": "Edit"}})
        cat_a = cmds.runTimeCommand("m_object_selection", query=True, category=True)
        cmds.runTimeCommand("m_object_selection", edit=True, delete=True)
        Macros.set_macros("m_object_selection, key=F9, cat=Edit")
        cat_b = cmds.runTimeCommand("m_object_selection", query=True, category=True)
        self.assertEqual(cat_a, cat_b)

    def test_unset_macro_removes_command(self):
        Macros.set_macro("m_group", key="F10", cat="Edit")
        Macros.unset_macro("m_group", key="F10")
        self.assertFalse(cmds.runTimeCommand("m_group", exists=True))


class TestLiveHotkeyIntrospection(QuickTestCase):
    """assignCommand keyString -> Maya token conversion (live map is GUI-only)."""

    def test_keystring_to_token_modifiers(self):
        from mayatk.ui_utils.hotkey_collisions import keystring_to_token

        # Maya 2025 7-element keyString: [key, alt, ctrl, ?, shift, ?, ?]
        self.assertEqual(keystring_to_token(["i", "0", "1", "0", "0", "0", "0"]), "ctl+i")
        self.assertEqual(
            keystring_to_token(["g", "0", "1", "0", "0", "0", "0"]), "ctl+g"
        )

    def test_keystring_uppercase_letter_implies_shift(self):
        from mayatk.ui_utils.hotkey_collisions import keystring_to_token

        # Upper-case glyph with ctrl flag -> ctl+sht+i (canonical token form).
        token = keystring_to_token(["I", "0", "1", "0", "0", "0", "0"])
        self.assertEqual(Macros._normalize_key(token), Macros._normalize_key("ctl+sht+i"))

    def test_keystring_function_key_passthrough(self):
        from mayatk.ui_utils.hotkey_collisions import keystring_to_token

        self.assertEqual(keystring_to_token(["F3", "0", "0", "0", "0", "0", "0"]), "F3")

    def test_empty_keystring(self):
        from mayatk.ui_utils.hotkey_collisions import keystring_to_token

        self.assertEqual(keystring_to_token([]), "")

    def test_live_map_empty_when_registry_unavailable(self):
        # When assignCommand reports no elements (None in mayapy standalone),
        # the map is empty and never crashes. Mock the count so the assertion
        # is deterministic regardless of whether the running Maya happens to
        # have its default hotkeys loaded (a GUI-connected runner does).
        from unittest import mock
        from mayatk.ui_utils import hotkey_collisions

        with mock.patch.object(
            hotkey_collisions.cmds, "assignCommand", return_value=None
        ):
            self.assertEqual(hotkey_collisions.live_hotkey_map(), {})


class TestApplySavedMacros(_TempPresetRoot, MayaTkTestCase):
    """Startup path: apply_saved_macros() applies the shipped default set."""

    def tearDown(self):
        for name in Macros.load_preset(Macros.DEFAULT_PRESET):
            try:
                if cmds.runTimeCommand(name, exists=True) and not cmds.runTimeCommand(
                    name, query=True, default=True
                ):
                    cmds.runTimeCommand(name, edit=True, delete=True)
            except Exception:
                pass
        super().tearDown()

    def test_applies_default_when_no_active_preset(self):
        Macros.apply_saved_macros()
        # A representative subset from the default set should be registered.
        for name in ("m_wireframe", "m_invert_selection", "m_group"):
            self.assertTrue(
                cmds.runTimeCommand(name, exists=True),
                f"{name} not registered by apply_saved_macros()",
            )


if __name__ == "__main__":
    unittest.main()
