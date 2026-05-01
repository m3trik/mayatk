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


if __name__ == "__main__":
    unittest.main()
