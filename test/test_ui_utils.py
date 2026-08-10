# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.ui_utils module

Tests for UIUtils class functionality including:
- Panel queries
- UI widget operations
- Dialog utilities
"""
import unittest
import maya.cmds as cmds
import mayatk as mtk

from base_test import MayaTkTestCase, skipIfBatch


class TestUIUtils(MayaTkTestCase):
    """Tests for UIUtils class."""

    def test_get_panel(self):
        """Test getting Maya panels."""
        try:
            panels = mtk.get_panel(all=True)
            if panels:
                self.assertIsInstance(panels, list)
        except RuntimeError:
            self.skipTest("Panel queries not available in batch mode")

    def test_refresh_outliners_never_raises(self):
        """Must be safe to call anywhere — batch/mayapy included (returns 0)."""
        count = mtk.UiUtils.refresh_outliners()
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 0)

    @skipIfBatch("Outliner panels only exist in a GUI session")
    def test_refresh_outliners_finds_panels_in_gui(self):
        """The hiddenInOutliner writers rely on this actually redrawing a panel."""
        self.assertGreaterEqual(mtk.UiUtils.refresh_outliners(), 1)

    @skipIfBatch("Outliner panels only exist in a GUI session")
    def test_hiding_a_node_refreshes_the_outliner(self):
        """End of the chain DisplayUtils.set_hidden_in_outliner drives in a real UI."""
        cube = cmds.polyCube(name="refresh_probe")[0]
        self.assertTrue(mtk.DisplayUtils.set_hidden_in_outliner(cube))
        self.assertTrue(cmds.getAttr(f"{cube}.hiddenInOutliner"))

    def test_reveal_in_outliner_always_selects(self):
        """The select is the primary action; the panel scroll is best-effort.

        Batch has no Outliner panel at all — the call must still select rather
        than raise part-way through (it used to assume ``outlinerPanel1``).
        """
        cube = cmds.polyCube(name="reveal_probe")[0]
        mtk.UiUtils.reveal_in_outliner([cube])
        self.assertEqual(cmds.ls(selection=True), [cube])

    def test_reveal_in_outliner_empty_input_is_a_noop(self):
        cube = cmds.polyCube(name="reveal_noop_probe")[0]
        cmds.select(cube, replace=True)
        mtk.UiUtils.reveal_in_outliner([])
        self.assertEqual(cmds.ls(selection=True), [cube])


if __name__ == "__main__":
    unittest.main(verbosity=2)
