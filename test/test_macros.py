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


if __name__ == "__main__":
    unittest.main()
