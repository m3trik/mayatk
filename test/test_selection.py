# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.edit_utils.selection (Selection).

Run with mayapy:
    & $MAYAPY mayatk\\test\\run_tests.py selection
"""

import maya.cmds as cmds

from base_test import MayaTkTestCase, skipIfBatch

from mayatk.edit_utils.selection import Selection


class TestRigSelectionMode(MayaTkTestCase):
    """A rig-locked mesh (display type DRIVEN by a rig control, the tube
    rig's ``meshDisplay``) toggles; a hand-locked mesh never does."""

    def setUp(self):
        super().setUp()
        self.addCleanup(cmds.selectType, nurbsCurve=True, joint=True)
        self.rigged = cmds.listRelatives(
            cmds.polyCube(name="riggedGeo")[0], shapes=True, fullPath=True
        )[0]
        control = cmds.circle(name="settings_CTRL")[0]
        cmds.addAttr(
            control,
            longName="meshDisplay",
            attributeType="enum",
            enumName="normal:template:reference",
            defaultValue=2,
            keyable=True,
        )
        cmds.connectAttr(f"{control}.meshDisplay", f"{self.rigged}.overrideDisplayType")
        cmds.setAttr(f"{self.rigged}.overrideEnabled", True)
        self.manual = cmds.listRelatives(
            cmds.polySphere(name="handLocked")[0], shapes=True, fullPath=True
        )[0]
        cmds.setAttr(f"{self.manual}.overrideEnabled", True)
        cmds.setAttr(f"{self.manual}.overrideDisplayType", 2)

    def test_finds_only_rig_driven_meshes(self):
        self.assertEqual(Selection.rig_locked_meshes(), [self.rigged])

    def test_normal_unlocks_and_rig_relocks(self):
        self.assertEqual(Selection.get_rig_selection_mode(), "rig")
        self.assertEqual(Selection.set_rig_selection_mode("normal"), [self.rigged])
        self.assertFalse(cmds.getAttr(f"{self.rigged}.overrideEnabled"))
        self.assertTrue(cmds.getAttr(f"{self.manual}.overrideEnabled"))
        self.assertEqual(Selection.get_rig_selection_mode(), "normal")
        Selection.set_rig_selection_mode("rig")
        self.assertTrue(cmds.getAttr(f"{self.rigged}.overrideEnabled"))
        self.assertEqual(Selection.get_rig_selection_mode(), "rig")

    @skipIfBatch("selectType is a GUI-session preference")
    def test_meshes_only_masks_controls_until_left(self):
        Selection.set_rig_selection_mode("meshes_only")
        self.assertFalse(cmds.getAttr(f"{self.rigged}.overrideEnabled"))
        self.assertFalse(cmds.selectType(query=True, nurbsCurve=True))
        self.assertFalse(cmds.selectType(query=True, joint=True))
        self.assertEqual(Selection.get_rig_selection_mode(), "meshes_only")
        Selection.set_rig_selection_mode("normal")
        self.assertTrue(cmds.selectType(query=True, nurbsCurve=True))
        self.assertTrue(cmds.selectType(query=True, joint=True))

    def test_a_mode_change_is_one_undo_step(self):
        """Every rig-locked mesh the mode touches comes back with ONE Ctrl+Z."""
        second = cmds.listRelatives(
            cmds.polyCube(name="riggedGeo2")[0], shapes=True, fullPath=True
        )[0]
        cmds.connectAttr("settings_CTRL.meshDisplay", f"{second}.overrideDisplayType")
        cmds.setAttr(f"{second}.overrideEnabled", True)
        self.addCleanup(cmds.undoInfo, state=cmds.undoInfo(query=True, state=True))
        cmds.undoInfo(state=True)
        cmds.flushUndo()
        self.assertEqual(len(Selection.set_rig_selection_mode("normal")), 2)
        cmds.undo()
        self.assertTrue(cmds.getAttr(f"{self.rigged}.overrideEnabled"))
        self.assertTrue(cmds.getAttr(f"{second}.overrideEnabled"))

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            Selection.set_rig_selection_mode("controls")


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
