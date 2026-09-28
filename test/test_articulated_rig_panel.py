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
must be deferred); Split Off Selected, Set Limits From Pose and Remove Rig.
"""

import unittest

import maya.cmds as cmds
from qtpy import QtWidgets

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
    "btn_build",
    "btn_grab",
    "cmb_insert_type",
    "btn_insert",
    "btn_fold",
    "btn_limits_from_pose",
    "btn_clear_limits",
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
        self.ui.cmb_source.setCurrentIndex(0)
        self.ui.txt_name.setText("")
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

    @staticmethod
    def settle():
        QtWidgets.QApplication.processEvents()


class TestPanelSurface(_PanelCase):
    def test_every_widget_the_slots_address_exists(self):
        for name in WIDGETS:
            self.assertTrue(hasattr(self.ui, name), name)


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
        cmds.select(self.arm, replace=True)
        self.ui.txt_name.setText("desk")
        self.ui.btn_build.click()  # analyzes first when nothing is planned
        self.assertIsNotNone(self.slots.rig)
        self.assertEqual(self.slots.rig.name, "desk")
        ctrl = self.slots.rig.control("LEG_2")
        cmds.setKeyframe(ctrl, attribute="rz", time=1, value=10)
        cmds.setKeyframe(ctrl, attribute="rz", time=24, value=40)
        # The combo's own signal rebuilds the rig: its refresh must wait.
        self.ui.tbl_joints.cellWidget(1, 1).setCurrentText("ball")
        self.settle()
        self.assertEqual(self.rows()[1], ("LEG_2", "ball"))
        ctrl = self.slots.rig.control("LEG_2")
        self.assertEqual(cmds.keyframe(ctrl, attribute="rz", query=True), [1.0, 24.0])


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

    def test_limits_from_pose_and_remove(self):
        cmds.select(self.arm, replace=True)
        self.ui.btn_build.click()
        rig = self.slots.rig
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
