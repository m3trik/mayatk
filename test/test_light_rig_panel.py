# !/usr/bin/python
# coding=utf-8
"""Panel tests for ``light_rig.ui`` + LightRigSlots, driven through the real widgets.

GUI-only (registered in ``run_tests.GUI_REQUIRED``): the panel is loaded through
MayaUiHandler. ``test_light_rig.py`` covers the engine; this covers what it can't
see: the dropdown is the library (the swap snapshot is not in it), Apply and Swap
Back act on the pick, the footer reports the scene's rig and an edit to it, and
the picker's verbs (save form, rename in place, lock, delete, compare).

Each test points the panel at a scratch library, never the user's.
"""

import os
import unittest

import maya.cmds as cmds

import pythontk as ptk
from mayatk.light_utils.light_rig import LightRig
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler
from base_test import MayaTkTestCase


class _Form:
    """A FormPanel stand-in: accepted, with the values a user typed."""

    def __init__(self, values):
        self._values = values

    def exec_panel(self):
        return True

    def values(self):
        return dict(self._values)

    def deleteLater(self):
        pass


class TestLightRigPanel(MayaTkTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ui = MayaUiHandler.instance().get("light_rig")
        cls.slots = cls.ui.slots
        cls.temp = ptk.TempArtifacts("light_rig_panel", policy="scoped")
        cls.root = str(cls.temp.dir_path())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup(force=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.store = ptk.PresetStore("light_rigs", user_dir=os.path.join(self.root, self._testMethodName))
        self.engine = LightRig(group="TEST_RIG", store=self.store)
        self._engine = self.slots.engine
        self.slots.engine = self.engine
        self.messages = []
        self._message_box = self.slots.sb.message_box
        self.slots.sb.message_box = lambda text, *a, **k: self.messages.append(text)
        self.slots._ask = lambda *a: True

    def tearDown(self):
        self.slots.engine = self._engine
        self.slots.sb.message_box = self._message_box
        del self.slots._ask
        super().tearDown()

    # ------------------------------------------------------------------ helpers
    def _rig(self, name, intensity):
        """Save a one-light rig *name*, then clear the scene of it."""
        tf = cmds.rename(cmds.shadingNode("pointLight", asLight=True), "L_KEY")
        cmds.setAttr(cmds.listRelatives(tf, shapes=True, fullPath=True)[0] + ".intensity", intensity)
        cmds.group(tf, name="TEST_RIG", world=True)
        self.engine.save(name)
        self.engine.remove()

    def _items(self):
        cmb = self.ui.cmb000
        return [cmb.itemData(i) for i in range(cmb.count())]

    def _pick(self, name):
        cmb = self.ui.cmb000
        cmb.setCurrentIndex(cmb.findData(name))

    def _intensity(self):
        return cmds.getAttr("|TEST_RIG|L_KEY|L_KEYShape.intensity")

    # ------------------------------------------------------------------ tests
    def test_the_dropdown_lists_the_library_not_the_swap_snapshot(self):
        self._rig("A", 1)
        self._rig("B", 2)
        self.engine.apply("A")
        self.engine.apply("B")  # leaves the swap snapshot in the library
        self.slots.refresh()
        self.assertEqual(self._items(), ["A", "B"])
        self.assertTrue(self.ui.b001.isEnabled())
        self.assertIn("B", self.ui.footer.text())

    def test_apply_puts_the_picked_rig_in_the_scene(self):
        self._rig("A", 1)
        self._rig("B", 2)
        self.slots.refresh()
        self._pick("B")
        self.slots.b000()
        self.assertEqual(self.engine.applied(), ("B", False))
        self.assertAlmostEqual(self._intensity(), 2)
        self.assertIn("Applied B", self.ui.footer.text())

    def test_swap_back_returns_to_the_replaced_rig(self):
        self._rig("A", 1)
        self._rig("B", 2)
        self.slots.refresh()
        self._pick("A")
        self.slots.b000()
        self._pick("B")
        self.slots.b000()
        self.slots.b001()
        self.assertEqual(self.engine.applied(), ("A", False))
        self.assertAlmostEqual(self._intensity(), 1)

    def test_the_footer_flags_an_edited_scene_rig(self):
        self._rig("A", 1)
        self.engine.apply("A")
        cmds.setAttr("|TEST_RIG|L_KEY|L_KEYShape.intensity", 5)
        self.slots.refresh()
        self.assertIn("(edited)", self.ui.footer.text())

    def test_renaming_in_the_dropdown_renames_the_rig(self):
        self._rig("A", 1)
        self.slots.refresh()
        self.ui.cmb000.on_cells_edited.emit(self.ui.cmb000.findData("A"), {"name": "A2"})
        self.assertEqual(self.engine.names(), ["A2"])
        self.assertEqual(self._items(), ["A2"])

    def test_a_locked_rig_survives_delete(self):
        self._rig("A", 1)
        self.slots._lock("A", True)
        self.slots._delete("A")
        self.assertTrue(self.store.exists("A"))
        self.slots._lock("A", False)
        self.slots._delete("A")
        self.assertFalse(self.store.exists("A"))

    def test_the_save_form_saves_the_scene_rig(self):
        tf = cmds.rename(cmds.shadingNode("pointLight", asLight=True), "L_KEY")
        cmds.group(tf, name="TEST_RIG", world=True)
        self.slots._form = lambda *a, **k: _Form({"name": "N", "description": "first", "group": "TEST_RIG"})
        try:
            self.slots.prompt_save()
        finally:
            del self.slots._form
        self.assertEqual(self.engine.names(), ["N"])
        self.assertEqual(self.store.info("N").get("description"), "first")
        self.assertEqual(self.ui.cmb000.currentData(), "N")

    def test_compare_reports_what_applying_would_change(self):
        self._rig("A", 1)
        self.engine.apply("A")
        cmds.setAttr("|TEST_RIG|L_KEY|L_KEYShape.intensity", 5)
        self.slots._compare("A")
        self.assertTrue(self.messages and "intensity" in self.messages[-1], self.messages)


if __name__ == "__main__":
    unittest.main(exit=False)
