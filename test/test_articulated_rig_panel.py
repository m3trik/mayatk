# !/usr/bin/python
# coding=utf-8
"""Panel-wiring tests for ``articulated_rig.ui`` + ArticulatedRigSlots.

GUI-only (registered in ``run_tests.GUI_REQUIRED``): the panel's joint table
is a uitk ``TableWidget``, which hard-crashes mayapy in batch.

Where ``test_articulated_rig.py`` covers the engine, this covers what it
can't see, by acting on the widgets as a user does: that the ``.ui`` parses
and every widget the slots address exists; Analyze filling the table; a type
changed in a row's combo -- before the build (the plan) and after it (a
rebuild, which replaces the very combo whose signal ran it, so the refresh
must be deferred); limits and give typed into a row, the give scrolled; a row
clicked selecting its control; the End Control checkbox on a built rig; Switch
IK / FK and Rest Pose; Split Off Selected, Set Limits From Pose, Adjust Pivots
and Remove Rig.
"""

import math
import unittest
from unittest import mock

import maya.cmds as cmds
from qtpy import QtCore, QtWidgets

from base_test import MayaTkTestCase
from mayatk.rig_utils.articulated_rig import ArticulatedRig
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler
from test_articulated_rig import build_arm

WIDGETS = (
    "header",
    "cmb_source",
    "txt_name",
    "btn_analyze",
    "tbl_joints",
    "chk_end_control",
    "btn_build",
    "btn_grab",
    "btn_ik_fk",
    "btn_rest",
    "cmb_insert_type",
    "btn_insert",
    "btn_fold",
    "btn_limits_from_pose",
    "btn_clear_limits",
    "chk_adjust",
    "btn_rebuild",
    "btn_remove",
    "txt003",
)


class _PanelCase(MayaTkTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ui = MayaUiHandler.instance().get("articulated_rig")
        cls.slots = cls.ui.slots

    def setUp(self):
        super().setUp()
        self.slots.plan = None
        self.slots.rig = None
        self.slots._sync_adjust()  # clear a box a failed test left checked
        self.ui.cmb_source.setCurrentIndex(0)
        self.ui.txt_name.setText("")
        self.ui.chk_end_control.setChecked(True)
        self.messages = []
        self._orig_message_box = self.slots.sb.message_box
        self.slots.sb.message_box = lambda message, *a, **k: self.messages.append(
            str(message)
        )
        self.arm = build_arm()

    def tearDown(self):
        self.slots.sb.message_box = self._orig_message_box
        super().tearDown()

    def rows(self):
        table = self.ui.tbl_joints
        return [
            (table.item(r, 0).text(), table.cellWidget(r, 1).currentText())
            for r in range(table.rowCount())
        ]

    def cell(self, row, col):
        return self.ui.tbl_joints.item(row, col)

    def build(self, end_control=True):
        self.ui.chk_end_control.setChecked(end_control)
        cmds.select(self.arm, replace=True)
        self.ui.btn_build.click()  # analyzes first when nothing is planned
        self.settle()
        self.assertIsNotNone(self.slots.rig)
        return self.slots.rig

    @staticmethod
    def settle():
        QtWidgets.QApplication.processEvents()


class TestPanelSurface(_PanelCase):
    def test_every_widget_the_slots_address_exists(self):
        for name in WIDGETS:
            self.assertTrue(hasattr(self.ui, name), name)
        headers = [
            self.ui.tbl_joints.horizontalHeaderItem(c).text()
            for c in range(self.ui.tbl_joints.columnCount())
        ]
        self.assertEqual(headers, self.slots.COLUMNS)

    def test_limits_typed_as_text_parse(self):
        parse = self.slots.parse_limits
        self.assertEqual(parse("rz -30..95", ("rz",)), {"rz": (-30.0, 95.0)})
        self.assertEqual(parse("-..12.5", ("tx",)), {"tx": (None, 12.5)})
        self.assertEqual(
            parse("rx -10..10, rz ..45", ("rx", "rz")),
            {"rx": (-10.0, 10.0), "rz": (None, 45.0)},
        )
        self.assertEqual(
            parse("free", ("rx", "rz")), {"rx": (None, None), "rz": (None, None)}
        )
        for bad in ("rz 5", "ry -1..1", "-1..1", "rz 9..3"):
            with self.assertRaises(ValueError, msg=bad):
                parse(bad, ("rx", "rz"))


class TestAnalyzeAndBuild(_PanelCase):
    def test_analyze_fills_the_table_and_a_row_retypes_the_plan(self):
        cmds.select(self.arm, replace=True)
        self.ui.btn_analyze.click()
        self.assertEqual(
            self.rows(),
            [
                ("LEG_1", "universal"),
                ("LEG_2", "hinge"),
                ("LEG_3", "hinge"),
                ("LEG_4", "slide"),
                ("HEAD", "ball"),
            ],
        )
        self.ui.tbl_joints.cellWidget(1, 1).setCurrentText("ball")
        self.settle()
        self.assertEqual(self.slots.plan["joints"][1]["type"], "ball")
        self.assertEqual(self.rows()[1], ("LEG_2", "ball"))

    def test_build_then_retype_a_built_joint_in_its_row(self):
        self.ui.txt_name.setText("desk")
        rig = self.build()
        self.assertEqual(rig.name, "desk")
        self.assertIsNotNone(rig.end_control)
        ctrl = rig.control("LEG_2")
        cmds.setKeyframe(ctrl, attribute="rz", time=1, value=10)
        cmds.setKeyframe(ctrl, attribute="rz", time=24, value=40)
        # The combo's own signal rebuilds the rig: its refresh must wait.
        self.ui.tbl_joints.cellWidget(1, 1).setCurrentText("ball")
        self.settle()
        self.assertEqual(self.rows()[1], ("LEG_2", "ball"))
        ctrl = self.slots.rig.control("LEG_2")
        self.assertEqual(cmds.keyframe(ctrl, attribute="rz", query=True), [1.0, 24.0])

    def test_a_build_without_the_end_control(self):
        rig = self.build(end_control=False)
        self.assertIsNone(rig.end_control)


class TestTableEdits(_PanelCase):
    def test_limits_typed_into_a_row_bound_the_built_joint(self):
        rig = self.build(end_control=False)
        self.cell(1, self.slots.LIMITS_COLUMN).setText("rz -20..45")
        self.settle()
        ctrl = rig.control("LEG_2")
        self.assertEqual(
            cmds.transformLimits(ctrl, query=True, rotationZ=True), [-20.0, 45.0]
        )
        self.assertEqual(self.cell(1, self.slots.LIMITS_COLUMN).text(), "rz -20..45")

    def test_a_bad_limits_entry_is_reported_and_changes_nothing(self):
        rig = self.build(end_control=False)
        self.cell(1, self.slots.LIMITS_COLUMN).setText("tight please")
        self.settle()
        joint = next(j for j in rig.spec["joints"] if j["id"] == "LEG_2")
        self.assertEqual(joint.get("limits") or {}, {})
        self.assertIn("failed", self.ui.txt003.toPlainText())
        self.assertEqual(self.cell(1, self.slots.LIMITS_COLUMN).text(), "free")

    def test_give_typed_and_scrolled_sets_the_weight(self):
        rig = self.build()
        give = self.slots.GIVE_COLUMN
        self.cell(0, give).setText("0.5")
        self.settle()
        weights = lambda: next(  # noqa: E731
            j for j in rig.spec["joints"] if j["id"] == "LEG_1"
        )["weights"]
        self.assertEqual(weights(), {"rx": 0.5, "rz": 0.5})
        self.ui.tbl_joints.cellWheelScrolled.emit(0, give, 2, QtCore.Qt.NoModifier)
        self.settle()
        self.assertAlmostEqual(weights()["rx"], 0.7, places=6)
        self.assertEqual(self.cell(0, give).text(), "0.7")

    def test_a_give_in_the_plan_reaches_the_build(self):
        cmds.select(self.arm, replace=True)
        self.ui.btn_analyze.click()
        self.cell(3, self.slots.GIVE_COLUMN).setText("0.25")
        self.settle()
        self.ui.btn_build.click()
        joint = next(j for j in self.slots.rig.spec["joints"] if j["id"] == "LEG_4")
        self.assertEqual(joint["weights"], {"tx": 0.25})

    def test_clicking_a_row_selects_its_control(self):
        rig = self.build()
        cmds.select(clear=True)
        self.ui.tbl_joints.selectRow(1)
        self.settle()
        self.assertEqual(
            cmds.ls(selection=True, long=True), cmds.ls(rig.control("LEG_2"), long=True)
        )


class TestPose(_PanelCase):
    def test_the_end_control_checkbox_drops_and_restores_it(self):
        rig = self.build()
        self.ui.chk_end_control.setChecked(False)
        self.settle()
        self.assertIsNone(rig.end_control)
        self.ui.chk_end_control.setChecked(True)
        self.settle()
        self.assertIsNotNone(rig.end_control)

    def test_switch_ik_fk_and_rest_pose(self):
        rig = self.build()
        end = rig.end_control
        cmds.move(-6.0, 7.0, 5.0, end, relative=True, worldSpace=True)
        cmds.select(end, replace=True)
        posed = cmds.getAttr("HEAD.worldMatrix[0]")
        self.ui.btn_ik_fk.click()
        self.assertEqual(cmds.getAttr(f"{end}.ikBlend"), 0.0)
        moved = cmds.getAttr("HEAD.worldMatrix[0]")  # the FK controls took the pose
        self.assertLess(max(abs(a - b) for a, b in zip(posed, moved)), 1e-4)
        self.ui.btn_ik_fk.click()
        self.assertEqual(cmds.getAttr(f"{end}.ikBlend"), 1.0)
        self.ui.btn_rest.click()
        self.assertEqual(rig.fk_state(), [0.0] * len(rig.fk_state()))
        self.assertEqual(cmds.getAttr(f"{end}.translate")[0], (0.0, 0.0, 0.0))


class TestGrabTool(_PanelCase):
    def test_the_grab_button_makes_the_dragger_the_current_tool(self):
        from mayatk.rig_utils.articulated_rig import ArticulatedRigGrab

        self.ui.btn_grab.click()
        try:
            self.assertEqual(cmds.currentCtx(), ArticulatedRigGrab.CONTEXT)
            origin, direction = ArticulatedRigGrab.ray((0.0, 0.0, 0.0))
            self.assertAlmostEqual(sum(v * v for v in direction), 1.0, places=6)
            self.assertEqual(len(origin), 3)
        finally:
            cmds.setToolTo("selectSuperContext")


class TestPostRig(_PanelCase):
    def test_split_off_selected_adds_the_telescope(self):
        links = [[f"{self.arm}|{p}"] for p in ("BASE", "LEG_1", "LEG_2")]
        links += [[f"{self.arm}|LEG_3", f"{self.arm}|LEG_4"], [f"{self.arm}|HEAD"]]
        self.slots.rig = ArticulatedRig.create(links)
        self.slots.refresh_table()
        self.assertEqual(len(self.rows()), 4)
        cmds.select("LEG_4", replace=True)
        self.ui.cmb_insert_type.setCurrentIndex(1)  # Joint: Slide
        self.ui.btn_insert.click()
        self.assertIn(("LEG_4", "slide"), self.rows())

    def test_an_analysis_left_in_the_table_gives_way_to_the_rig_acted_on(self):
        rig = self.build()
        other = build_arm()
        cmds.select(other, replace=True)
        self.ui.btn_analyze.click()  # a plan for another prop fills the table
        cmds.select(rig.control("LEG_2"), replace=True)
        self.ui.btn_rebuild.click()
        self.assertIsNone(self.slots.plan)
        self.assertEqual(self.slots.rig.name, rig.name)
        self.assertEqual(len(self.rows()), 5)

    def test_adjust_pivots_puts_handles_out_and_rebuilds_when_cleared(self):
        rig = self.build()
        cmds.select(rig.control("LEG_2"), replace=True)
        self.ui.chk_adjust.setChecked(True)
        self.settle()
        handles = rig.adjust_handles()
        self.assertEqual(set(handles), set(rig.joint_ids()) | {ArticulatedRig.END_KEY})
        self.assertEqual(
            sorted(cmds.ls(selection=True, long=True)),
            sorted(cmds.ls(list(handles.values()), long=True)),
        )
        self.assertTrue(self.ui.chk_adjust.isChecked())
        before = cmds.xform(rig.joint("LEG_2"), query=True, worldSpace=True, t=True)
        cmds.move(2.0, 0.0, 0.0, handles["LEG_2"], relative=True, objectSpace=True)
        self.ui.chk_adjust.setChecked(False)
        self.settle()
        self.assertFalse(self.slots.rig.adjusting)
        after = cmds.xform(
            self.slots.rig.joint("LEG_2"), query=True, worldSpace=True, t=True
        )
        self.assertGreater(math.dist(before, after), 1.0)
        self.assertFalse(self.ui.chk_adjust.isChecked())

    def test_a_panel_open_never_restores_adjust_pivots(self):
        """The box shows a live state -- whether the rig in the table is
        adjusting -- not a setting. Persisted, an adjust that ended any way but
        by clearing the box (Rebuild, Remove, undo, closing Maya) left True
        stored, and the next panel open's restore checked it: handles built on
        whatever rig the selection touched. The store is stood in for, never
        written: the restore is driven as a panel open drives it."""
        rig = self.build()
        cmds.select(rig.control("LEG_2"), replace=True)
        box = self.ui.chk_adjust
        self.ui.restored_widgets.discard(box)  # an open restores a widget once
        stored_on = mock.patch.object(
            self.ui.state, "load", side_effect=lambda w, *a, **k: w.setChecked(True)
        )
        with stored_on:
            self.ui.perform_restore_state(box)
        self.settle()
        self.assertFalse(rig.adjusting, "the restore began an adjust")
        self.assertFalse(box.isChecked())
        self.assertFalse(box.restore_state)

    def test_limits_from_pose_and_remove(self):
        rig = self.build(end_control=False)
        ctrl = rig.control("LEG_2")
        cmds.setAttr(f"{ctrl}.rz", 55.0)
        cmds.select(ctrl, replace=True)
        self.ui.btn_limits_from_pose.click()
        self.assertEqual(
            cmds.transformLimits(ctrl, query=True, enableRotationZ=True), [False, True]
        )
        self.assertEqual(
            cmds.transformLimits(ctrl, query=True, rotationZ=True)[1], 55.0
        )
        self.ui.btn_remove.click()
        self.assertFalse(ArticulatedRig.scene_rigs())
        self.assertEqual(self.ui.tbl_joints.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
