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
from unittest import mock

import maya.cmds as cmds
import maya.mel as mel
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


class TestNodeIconsAudio(MayaTkTestCase):
    """``NodeIcons.audio_icon``: an audio row's icon, read from the ``audio``
    node type alone (the clip's node may not exist yet). The shared Shot
    Manifest panel asks either host for it by this one name."""

    def test_it_is_the_audio_node_types_resource_icon(self):
        from qtpy import QtGui

        from mayatk.ui_utils.node_icons import NodeIcons

        with mock.patch.object(QtGui, "QIcon") as icon:
            icon.return_value.isNull.return_value = False
            self.assertIs(NodeIcons.audio_icon(), icon.return_value)
        icon.assert_called_once_with(":/out_audio.png")

    def test_a_missing_resource_reads_none(self):
        from qtpy import QtGui

        from mayatk.ui_utils.node_icons import NodeIcons

        with mock.patch.object(QtGui, "QIcon") as icon:
            icon.return_value.isNull.return_value = True
            self.assertIsNone(NodeIcons.audio_icon())


class TestEditorRegistry(MayaTkTestCase):
    """UiUtils.get_editor_types / open_editor -- the mirror of blendertk's pair.

    ``mel.eval`` is stubbed: opening a real editor window is GUI-only, and what
    is pinned here is the lookup and the None-on-failure contract.
    """

    @skipIfBatch("runtime commands are registered by the GUI's startup")
    def test_every_editor_maps_to_a_runtime_command(self):
        editors = mtk.UiUtils.get_editor_types()
        self.assertEqual(editors["UV Editor"], "TextureViewWindow")
        for name, command in editors.items():
            self.assertTrue(
                cmds.runTimeCommand(command, exists=True)
                or mel.eval(f'exists "{command}"'),
                f"{name}: {command} is not a runtime command or MEL procedure",
            )

    def test_a_copy_is_returned(self):
        mtk.UiUtils.get_editor_types()["UV Editor"] = "nope"
        self.assertEqual(
            mtk.UiUtils.get_editor_types()["UV Editor"], "TextureViewWindow"
        )

    def test_open_runs_the_mapped_command_or_a_raw_one(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(_ui_utils.mel, "eval") as ev:
            self.assertEqual(mtk.UiUtils.open_editor("Graph Editor"), "GraphEditor")
            self.assertEqual(
                mtk.UiUtils.open_editor("OutlinerWindow"), "OutlinerWindow"
            )
        self.assertEqual(
            [c.args[0] for c in ev.call_args_list], ["GraphEditor", "OutlinerWindow"]
        )

    def test_a_failed_open_returns_none(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(
            _ui_utils.mel, "eval", side_effect=RuntimeError("Cannot find procedure")
        ):
            self.assertIsNone(mtk.UiUtils.open_editor("XGen Editor"))


class TestLogLinks(MayaTkTestCase):
    """``UiUtils.dispatch_log_link`` -- the ``action://`` links a report
    viewer hands back."""

    def test_a_select_link_selects_every_node_it_names(self):
        """The Bake State viewer selects every reader of a map in one link
        (``ptk.ReportDoc.action(..., node=[...])``); a node gone since the
        report was built is warned of, the rest still selected."""
        import pythontk as ptk
        from qtpy import QtCore

        a = cmds.ls(cmds.polyCube(name="linkA")[0], long=True)[0]
        b = cmds.ls(cmds.polyCube(name="linkB")[0], long=True)[0]
        link = ptk.ReportDoc.action("3", "select", node=[a, b, "|gone"])
        href = link.html.split('href="', 1)[1].split('"', 1)[0].replace("&amp;", "&")
        logger = mock.Mock()

        handled = mtk.UiUtils.dispatch_log_link(QtCore.QUrl(href), logger=logger)

        self.assertTrue(handled)
        self.assertEqual(sorted(cmds.ls(selection=True, long=True)), sorted([a, b]))
        self.assertIn("|gone", logger.warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
