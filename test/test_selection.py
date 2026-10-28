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


class TestSelectionPanelOperations(MayaTkTestCase):
    """The selection operations tentacle's composed selection panel calls."""

    _ALL_OFF = dict(
        border=False, borderPropagation=False, shell=False, anglePropagation=False,
        loopPropagation=False, ringPropagation=False, uvEdgeLoopPropagation=False,
    )

    def setUp(self):
        super().setUp()
        cmds.polySelectConstraint(**self._ALL_OFF)
        self.addCleanup(cmds.polySelectConstraint, **self._ALL_OFF)
        self.plane = cmds.polyPlane(sx=3, sy=3, w=2, h=2, ch=False, name="sel_plane")[0]

    def test_objects_in_scope(self):
        hidden = cmds.polyCube(name="sel_hidden")[0]
        cmds.hide(hidden)
        cmds.select(self.plane)
        self.assertEqual(Selection.objects_in_scope("selected"), [self.plane])
        self.assertNotIn(hidden, Selection.objects_in_scope("visible"))
        self.assertIn(hidden, Selection.objects_in_scope("all"))
        with self.assertRaises(ValueError):
            Selection.objects_in_scope("nearby")

    def test_reorder_selection_reselects_in_order(self):
        b = cmds.polyCube(name="sel_b")[0]
        a = cmds.polyCube(name="sel_a")[0]
        cmds.select([b, a])
        self.assertEqual(Selection.reorder_selection(None, method="name"), [a, b])
        self.assertEqual(cmds.ls(selection=True), [a, b])
        cmds.select(clear=True)
        self.assertEqual(Selection.reorder_selection(None), [])

    def test_maya_lists_all_twenty_conversions(self):
        self.assertEqual(len(Selection.get_conversion_types()), 20)

    def test_convert_a_vertex_to_its_faces(self):
        cmds.select(f"{self.plane}.vtx[5]")  # interior: 4 faces around it
        result = Selection.convert_selection("faces")
        self.assertEqual(len(cmds.ls(result, flatten=True)), 4)

    def test_convert_to_border_edges(self):
        cmds.select(f"{self.plane}.f[0:8]")
        result = Selection.convert_selection("border_edges")
        self.assertEqual(len(cmds.ls(result, flatten=True)), 12)

    def test_convert_with_nothing_selected_is_an_empty_list(self):
        cmds.select(clear=True)
        self.assertEqual(Selection.convert_selection("faces"), [])
        with self.assertRaises(ValueError):
            Selection.convert_selection("everything")

    def test_two_constraints_stay_on_together(self):
        Selection.set_selection_constraint("angle", True)
        on = Selection.set_selection_constraint("edge_loop", True)
        self.assertEqual(on, ["angle", "edge_loop"])
        on = Selection.set_selection_constraint("angle", False)
        self.assertEqual(on, ["edge_loop"])
        self.assertEqual(Selection.get_selection_constraints(), ["edge_loop"])

    def test_border_constraint_owns_both_flags(self):
        Selection.set_selection_constraint("border", True)
        self.assertTrue(cmds.polySelectConstraint(q=True, borderPropagation=True))
        Selection.set_selection_constraint("border", False)
        self.assertFalse(cmds.polySelectConstraint(q=True, border=True))

    def test_select_edge_path_keeps_every_nth(self):
        cmds.select(f"{self.plane}.e[0]")
        loop = Selection.select_edge_path(None, path="edge_loop")
        cmds.select(f"{self.plane}.e[0]")
        halved = Selection.select_edge_path(None, path="edge_loop", step=2)
        self.assertTrue(loop)
        self.assertEqual(len(halved), (len(loop) + 1) // 2)
        cmds.select(clear=True)
        self.assertEqual(Selection.select_edge_path(None), [])

    def test_select_similar_objects(self):
        cube = cmds.polyCube(name="sim_src")[0]
        twin = cmds.duplicate(cube, name="sim_twin")[0]
        cmds.xform(twin, t=(25, 0, 0), ro=(0, 45, 0))
        cmds.polySphere(name="sim_other", r=9)
        cmds.select(cube)
        scope, matched = Selection.select_similar(None, vertex=True, face=True)
        self.assertEqual(scope, "objects")
        self.assertEqual([m.rsplit("|", 1)[-1] for m in matched], [twin])
        with self.assertRaises(TypeError):
            Selection.select_similar(None, colour=True)

    def test_select_similar_metric_flags_are_polyevaluate_flags(self):
        for metric, flag in Selection._SIMILAR_METRICS.items():
            with self.subTest(metric=metric):
                self.assertIsNotNone(cmds.polyEvaluate(self.plane, **{flag: True}))

    def test_select_island_grows_over_a_flat_plane(self):
        cmds.select(f"{self.plane}.f[4]")
        island = Selection.select_island(None)
        self.assertEqual(len(island), 9)
        cmds.select(self.plane)  # an object stands for all of its faces
        self.assertEqual(len(Selection.select_island(None)), 9)

    def test_toggle_selectability_is_uniform(self):
        cube = cmds.polyCube(name="sel_ref")[0]
        self.assertEqual(Selection.toggle_selectability([cube]), (False, [cube]))
        self.assertEqual(cmds.getAttr(f"{cube}.overrideDisplayType"), 2)
        self.assertEqual(Selection.toggle_selectability([cube]), (True, [cube]))
        self.assertEqual(cmds.getAttr(f"{cube}.overrideDisplayType"), 0)
        cmds.select(clear=True)
        self.assertEqual(Selection.toggle_selectability(None), [])

    def test_ignore_backfacing_round_trips(self):
        before = Selection.get_ignore_backfacing()
        self.addCleanup(Selection.set_ignore_backfacing, before)
        Selection.set_ignore_backfacing(not before)
        self.assertEqual(Selection.get_ignore_backfacing(), not before)


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
