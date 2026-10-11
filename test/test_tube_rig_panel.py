# !/usr/bin/python
# coding=utf-8
"""Tube Rig panel (``tube_rig_slots.py``), driven through the loaded UI.

The engine's own coverage is ``test_tube_rig.py``; this module pins what only
the panel decides -- which options a page shows, and that a click hands them
to the engine.
"""

import unittest

import maya.cmds as cmds
import maya.api.OpenMaya as om

from base_test import MayaTkTestCase
from mayatk.rig_utils.tube_rig import TubeRig
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler


def _make_cable(name="panelCable"):
    """An 8-unit capped cable standing on the origin, root-agnostic."""
    tube = cmds.polyCylinder(r=0.2, h=8.0, sy=6, sx=6, name=name)[0]
    cmds.move(0, 4.0, 0, tube)
    cmds.makeIdentity(tube, apply=True, t=1, r=1, s=1, n=0, pn=1)
    return tube


class TestTubeRigPanelReverse(MayaTkTestCase):
    """Reverse Direction belongs to EVERY build, One-Click included.

    Regression: the toggle lived on the Step 1 page while the panel opens
    on One-Click, so a user building in one click never saw it and could
    not root a plug at the end it unplugs from.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ui = MayaUiHandler.instance().get("tube_rig")
        cls.slots = cls.ui.slots
        # uitk persists these; hand the developer's panel back as found.
        cls._saved = (
            cls.ui.cmb_preset.currentIndex(),
            cls.ui.chk000.isChecked(),
            cls.ui.toolbox_steps.currentIndex(),
            cls.ui.s001.value(),
        )

    @classmethod
    def tearDownClass(cls):
        preset, reverse, page, controls = cls._saved
        cls.ui.cmb_preset.setCurrentIndex(preset)
        cls.ui.chk000.setChecked(reverse)
        cls.ui.toolbox_steps.setCurrentIndex(page)
        cls.ui.s001.setValue(controls)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.messages = []
        self._orig_message_box = self.slots.sb.message_box
        self.slots.sb.message_box = lambda *a, **k: self.messages.append(a)

    def tearDown(self):
        self.slots.sb.message_box = self._orig_message_box
        super().tearDown()

    def test_reverse_is_shown_on_the_one_click_page(self):
        macro = self.ui.toolbox_steps.indexOf(self.ui.page_macro)
        self.ui.toolbox_steps.setCurrentIndex(macro)
        self.assertTrue(self.ui.chk000.isVisibleTo(self.ui))
        self.assertTrue(self.ui.b000.isVisibleTo(self.ui))

    def test_one_click_roots_the_chain_at_the_reversed_end(self):
        fk = next(
            i
            for i in range(self.ui.cmb_preset.count())
            if self.ui.cmb_preset.itemData(i).strategy == "fk"
        )
        self.ui.cmb_preset.setCurrentIndex(fk)
        self.ui.s001.setValue(3)
        roots = []
        for reverse in (False, True):
            cmds.file(new=True, force=True)
            tube = _make_cable()
            cmds.select(tube)
            self.ui.chk000.setChecked(reverse)
            self.slots.b000()
            rig = TubeRig.for_node(tube)
            self.assertIsNotNone(rig, self.messages)
            roots.append(
                om.MVector(*cmds.xform(rig.joints[0], q=True, ws=True, t=True))
            )
        self.assertGreater((roots[0] - roots[1]).length(), 7.0)


if __name__ == "__main__":
    unittest.main()
