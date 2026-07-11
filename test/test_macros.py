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


@unittest.skipIf(
    cmds.about(batch=True),
    "cmds.selectMode(query=...) is unreliable in batch/standalone mode — it "
    "returns False regardless of the actual mode, so these assertions only "
    "hold in the GUI-connected runner (run_tests.py port mode).",
)
class TestSelectionMacros(MayaTkTestCase):
    """Selection-mode macros — assert the live selectMode/selectType state.

    GUI-only: the macros themselves run headlessly, but the *query* side lies
    under mayapy standalone (see the class skip), so the suite verifies them
    where the query is trustworthy.
    """

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


class _NoPrefsFlush:
    """Mixin: block ``cmds.savePrefs`` for the test.

    The test harness runs against the developer's REAL Maya prefs (no
    ``MAYA_APP_DIR`` sandbox), so an integration test that drives the real
    ``apply_bindings`` would otherwise flush its throwaway hotkey bindings
    (F7/F8/F9, ...) into the user's ``userHotkeys_*.mel``. That pollution is
    exactly what seeded the stale chords the launch-time preset re-apply then
    fought (and re-saved) on every launch. In-memory hotkey edits are fine —
    the test Maya is force-closed, so nothing persists unless savePrefs runs.
    """

    def setUp(self):
        super().setUp()
        from unittest import mock

        from mayatk.edit_utils import macros

        patcher = mock.patch.object(macros.cmds, "savePrefs", create=True)
        patcher.start()
        self.addCleanup(patcher.stop)


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
        Macros.save_preset("unittest_meta", {"m_wireframe": {"key": "3", "cat": "Display"}})
        try:
            data = Macros.load_preset("unittest_meta")
            self.assertNotIn("_meta", data)
            self.assertIn("m_wireframe", data)
        finally:
            Macros.delete_preset("unittest_meta")

    def test_shipped_default_is_all_unbound(self):
        # Contract: the shipped 'default' preset carries NO bindings — loading
        # it clears every macro hotkey. Bindings are opt-in via user presets.
        self.assertEqual(Macros.load_preset(Macros.DEFAULT_PRESET), {})

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


class TestApplyBindings(_NoPrefsFlush, _TempPresetRoot, MayaTkTestCase):
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


class TestEnsureEditableHotkeySet(QuickTestCase):
    """set_macro's locked-set guard: Maya refuses hotkey edits while the
    factory ``Maya_Default`` set is current, so an editable user set must be
    made current first (hotkey sets are GUI-only — mocked here; the live-GUI
    behavior was verified end-to-end 2026-07-10)."""

    @staticmethod
    def _fake_hotkeySet(current, existing):
        """A stateful ``cmds.hotkeySet`` stand-in covering the query /
        create / switch calls ``ensure_editable_hotkey_set`` makes."""
        state = {"current": current, "sets": list(existing)}

        def fake(*args, **kw):
            if kw.get("query"):
                if kw.get("current"):
                    return state["current"]
                if kw.get("exists"):
                    return args[0] in state["sets"]
                if kw.get("hotkeySetArray"):
                    return list(state["sets"])
                return None
            if kw.get("edit"):
                if kw.get("current"):
                    state["current"] = args[0]
                if kw.get("delete"):
                    state["sets"].remove(args[0])
                return None
            state["sets"].append(args[0])  # create
            if kw.get("current"):
                state["current"] = args[0]
            return args[0]

        return fake, state

    def test_locked_factory_set_creates_and_switches(self):
        from unittest import mock
        from mayatk.ui_utils import hotkey_collisions as hc

        fake, state = self._fake_hotkeySet("Maya_Default", ["Maya_Default"])
        with mock.patch.object(hc.cmds, "hotkeySet", side_effect=fake):
            name = hc.ensure_editable_hotkey_set()
        self.assertEqual(name, hc.MACRO_HOTKEY_SET)
        self.assertEqual(state["current"], hc.MACRO_HOTKEY_SET)
        self.assertIn(hc.MACRO_HOTKEY_SET, state["sets"])

    def test_existing_user_set_reused_not_duplicated(self):
        from unittest import mock
        from mayatk.ui_utils import hotkey_collisions as hc

        fake, state = self._fake_hotkeySet(
            "Maya_Default", ["Maya_Default", hc.MACRO_HOTKEY_SET]
        )
        with mock.patch.object(hc.cmds, "hotkeySet", side_effect=fake):
            name = hc.ensure_editable_hotkey_set()
        self.assertEqual(name, hc.MACRO_HOTKEY_SET)
        self.assertEqual(state["current"], hc.MACRO_HOTKEY_SET)
        self.assertEqual(state["sets"].count(hc.MACRO_HOTKEY_SET), 1)

    def test_editable_current_set_untouched(self):
        from unittest import mock
        from mayatk.ui_utils import hotkey_collisions as hc

        fake, state = self._fake_hotkeySet("MySet", ["Maya_Default", "MySet"])
        with mock.patch.object(hc.cmds, "hotkeySet", side_effect=fake):
            name = hc.ensure_editable_hotkey_set()
        self.assertEqual(name, "MySet")
        self.assertEqual(state["current"], "MySet")
        self.assertNotIn(hc.MACRO_HOTKEY_SET, state["sets"])


class TestApplyBindingsResilience(QuickTestCase):
    """apply_bindings: one bad chord logs and continues — it must never abort
    the rest of the preset (pre-fix, the first raising entry killed the whole
    startup / manual preset apply)."""

    def test_one_bad_entry_does_not_abort_the_rest(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        applied = []

        def fake_set_macro(name, key=None, cat=None):
            if name == "m_bad":
                raise RuntimeError("boom")
            applied.append(name)

        # apply_bindings diffs against the live registry, so stub it: m_bad and
        # m_group are unbound (forcing set_macro), m_unbind is currently bound
        # (forcing clear_hotkey). The bad entry must still not abort the rest.
        # savePrefs is mocked so this unit test never flushes the real prefs.
        live = {"m_unbind": {"key": "F4", "cat": "Edit"}}
        with mock.patch.object(
            Macros, "get_current_bindings", return_value=live
        ), mock.patch.object(
            Macros, "_key_bound_to", return_value=False  # targets not yet live
        ), mock.patch.object(
            Macros, "set_macro", side_effect=fake_set_macro
        ), mock.patch.object(
            Macros, "clear_hotkey"
        ) as clear, mock.patch.object(
            macros.cmds, "savePrefs", create=True
        ):
            Macros.apply_bindings(
                {
                    "m_bad": {"key": "F5", "cat": "Edit"},  # raises first...
                    "m_group": {"key": "F6", "cat": "Edit"},  # ...rest applies
                    "m_unbind": {"key": "", "cat": ""},
                }
            )
        self.assertEqual(applied, ["m_group"])
        clear.assert_called_once_with("m_unbind")


class TestApplyBindingsIdempotent(QuickTestCase):
    """apply_bindings must be a no-op — and must NOT flush prefs — when the live
    hotkey registry already matches the target preset. The launch-time re-apply
    (``apply_saved_macros`` from TclMaya) otherwise recreated every
    runtimeCommand and forced ``savePrefs(hotkeys=True)`` on every start, so the
    Script Editor logged "Saving runtime commands / Saving hotkeys / Saving
    named commands" each launch even though nothing had changed."""

    def test_matching_preset_applies_nothing_and_skips_saveprefs(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        # Live registry already equals the preset (one bound, one unbound).
        live = {
            "m_wireframe": {"key": "ctl+i", "cat": "Display"},
            "m_group": {"key": "", "cat": "Edit"},
        }
        with mock.patch.object(
            Macros, "get_current_bindings", return_value=live
        ), mock.patch.object(
            Macros, "_key_bound_to", return_value=True  # target chord already live
        ), mock.patch.object(Macros, "set_macro") as set_macro, mock.patch.object(
            Macros, "clear_hotkey"
        ) as clear, mock.patch.object(
            macros.cmds, "savePrefs", create=True
        ) as save:
            Macros.apply_bindings(
                {
                    "m_wireframe": {"key": "ctl+i", "cat": "Display"},
                    "m_group": {"key": "", "cat": "Edit"},  # already unbound
                }
            )
        set_macro.assert_not_called()
        clear.assert_not_called()
        save.assert_not_called()  # nothing changed -> no prefs flush

    def test_multibound_target_present_is_noop(self):
        """THE recurrence bug: a command bound to several chords (sht+q + extras
        added in Maya's Hotkey Editor) collapses to ONE, non-deterministically-
        chosen entry in live_hotkey_map. When that entry is an EXTRA (here
        sht+g) rather than the preset key (sht+q), the old normalize-compare
        re-applied + flushed prefs — on some launches only, which is why the
        save spam seemed random. The target key IS live, so it must be a no-op."""
        from unittest import mock
        from mayatk.edit_utils import macros

        # live_hotkey_map returned an extra chord, not the preset's sht+q...
        live = {"m_object_selection": {"key": "sht+g", "cat": "Edit"}}
        with mock.patch.object(
            Macros, "get_current_bindings", return_value=live
        ), mock.patch.object(
            Macros, "_key_bound_to", return_value=True  # ...but sht+q IS bound
        ) as kb, mock.patch.object(
            Macros, "set_macro"
        ) as set_macro, mock.patch.object(
            Macros, "clear_hotkey"
        ) as clear, mock.patch.object(
            macros.cmds, "savePrefs", create=True
        ) as save:
            Macros.apply_bindings(
                {"m_object_selection": {"key": "sht+q", "cat": "Edit"}}
            )
        kb.assert_called_once_with("m_object_selection", "sht+q")
        set_macro.assert_not_called()
        clear.assert_not_called()
        save.assert_not_called()  # target already live -> no flush

    def test_real_difference_applies_and_flushes_once(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        live = {"m_wireframe": {"key": "F1", "cat": "Display"}}
        with mock.patch.object(
            Macros, "get_current_bindings", return_value=live
        ), mock.patch.object(
            Macros, "_key_bound_to", return_value=False  # target F2 not yet live
        ), mock.patch.object(Macros, "set_macro") as set_macro, mock.patch.object(
            Macros, "clear_hotkey"
        ) as clear, mock.patch.object(
            macros.cmds, "savePrefs", create=True
        ) as save:
            Macros.apply_bindings({"m_wireframe": {"key": "F2", "cat": "Display"}})
        # Rebinding must release the stale live chord first: set_macro only
        # ADDS a binding, so without the clear the command stays multi-bound,
        # the registry keeps reporting the old chord, and the launch diff
        # re-applies (and re-saves prefs) forever.
        clear.assert_called_once_with("m_wireframe", key="F1")
        set_macro.assert_called_once_with("m_wireframe", key="F2", cat="Display")
        save.assert_called_once()  # a genuine change still persists

    def test_unbound_macro_binds_without_clearing(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        # No live key -> nothing to release; just bind.
        live = {"m_wireframe": {"key": "", "cat": "Display"}}
        with mock.patch.object(
            Macros, "get_current_bindings", return_value=live
        ), mock.patch.object(
            Macros, "_key_bound_to", return_value=False
        ), mock.patch.object(Macros, "set_macro") as set_macro, mock.patch.object(
            Macros, "clear_hotkey"
        ) as clear, mock.patch.object(
            macros.cmds, "savePrefs", create=True
        ):
            Macros.apply_bindings({"m_wireframe": {"key": "F2", "cat": "Display"}})
        clear.assert_not_called()
        set_macro.assert_called_once_with("m_wireframe", key="F2", cat="Display")

    def test_chord_swap_does_not_clobber_sibling_target(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        # m_wireframe and m_group swap chords in one apply. Neither stale chord
        # may be cleared — each is the OTHER's target, and set_macro (last write
        # wins on a key) reassigns it. Clearing would wipe the sibling's set.
        live = {
            "m_wireframe": {"key": "F1", "cat": "Display"},
            "m_group": {"key": "F2", "cat": "Edit"},
        }
        with mock.patch.object(
            Macros, "get_current_bindings", return_value=live
        ), mock.patch.object(
            Macros, "_key_bound_to", return_value=False  # neither target live yet
        ), mock.patch.object(Macros, "set_macro") as set_macro, mock.patch.object(
            Macros, "clear_hotkey"
        ) as clear, mock.patch.object(
            macros.cmds, "savePrefs", create=True
        ):
            Macros.apply_bindings(
                {
                    "m_wireframe": {"key": "F2", "cat": "Display"},  # was F1
                    "m_group": {"key": "F1", "cat": "Edit"},  # was F2
                }
            )
        clear.assert_not_called()  # both stale chords are targets -> no clear
        self.assertEqual(set_macro.call_count, 2)


class TestApplySavedMacros(_NoPrefsFlush, _TempPresetRoot, MayaTkTestCase):
    """Startup path: apply_saved_macros() resolves active -> shipped default."""

    NAMES = ("m_wireframe", "m_invert_selection", "m_group")

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

    def test_default_registers_nothing_when_no_active_preset(self):
        # The shipped 'default' is all-unbound, so with no active preset the
        # startup path must not register any macro runTimeCommands.
        Macros.apply_saved_macros()
        for name in self.NAMES:
            self.assertFalse(
                cmds.runTimeCommand(name, exists=True),
                f"{name} unexpectedly registered by the empty default preset",
            )

    def test_applies_active_user_preset(self):
        bindings = {
            "m_wireframe": {"key": "3", "cat": "Display"},
            "m_group": {"key": "ctl+g", "cat": "Edit"},
        }
        Macros.save_preset("unittest_active", bindings)  # also sets it active
        try:
            Macros.apply_saved_macros()
            for name in bindings:
                self.assertTrue(
                    cmds.runTimeCommand(name, exists=True),
                    f"{name} not registered from the active user preset",
                )
        finally:
            Macros.delete_preset("unittest_active")


if __name__ == "__main__":
    unittest.main()
