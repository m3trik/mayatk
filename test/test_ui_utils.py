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


class TestToolRegistry(MayaTkTestCase):
    """UiUtils.get_tool_types / set_tool / current_tool -- the mirror of
    blendertk's trio, what a panel shared by both hosts calls to pick a tool.

    ``mel.eval`` is stubbed where it would activate a real tool (GUI-only); what
    is pinned here is the lookup and the raise-on-refusal contract.
    """

    @skipIfBatch("tool runtime commands and contexts are registered by the GUI")
    def test_every_tool_maps_to_a_context_or_a_command(self):
        for name, command in mtk.UiUtils.get_tool_types().items():
            if command.startswith("setToolTo "):
                ok = cmds.contextInfo(command.split(" ", 1)[1], exists=True)
            else:
                ok = cmds.runTimeCommand(command, exists=True) or mel.eval(
                    f'exists "{command}"'
                )
            self.assertTrue(ok, f"{name}: {command} is no context or command")

    def test_a_copy_is_returned(self):
        mtk.UiUtils.get_tool_types()["Multi-Cut"] = "nope"
        self.assertEqual(mtk.UiUtils.get_tool_types()["Multi-Cut"], "dR_multiCutTool")

    def test_set_runs_the_mapped_mel_a_context_or_a_raw_command(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(_ui_utils.mel, "eval") as ev, mock.patch.object(
            _ui_utils.cmds,
            "contextInfo",
            side_effect=lambda name, **_: name == "lassoSelectContext",
        ):
            self.assertEqual(mtk.UiUtils.set_tool("Multi-Cut"), "dR_multiCutTool")
            self.assertEqual(
                mtk.UiUtils.set_tool("Marquee Select"), "setToolTo selectSuperContext"
            )
            self.assertEqual(
                mtk.UiUtils.set_tool("lassoSelectContext"),
                "setToolTo lassoSelectContext",
            )
            self.assertEqual(mtk.UiUtils.set_tool("MergeVertexTool"), "MergeVertexTool")
        self.assertEqual(
            [c.args[0] for c in ev.call_args_list],
            [
                "dR_multiCutTool",
                "setToolTo selectSuperContext",
                "setToolTo lassoSelectContext",
                "MergeVertexTool",
            ],
        )

    def test_a_refused_tool_raises(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(
            _ui_utils.mel, "eval", side_effect=RuntimeError("Cannot find procedure")
        ):
            with self.assertRaises(RuntimeError):
                mtk.UiUtils.set_tool("Quad Draw")

    def test_current_tool_names_a_registered_context(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(
            _ui_utils.cmds, "currentCtx", return_value="lassoSelectContext"
        ):
            self.assertEqual(mtk.UiUtils.current_tool(), "Lasso Select")
        with mock.patch.object(
            _ui_utils.cmds, "currentCtx", return_value="moveSuperContext"
        ):
            self.assertEqual(mtk.UiUtils.current_tool(), "moveSuperContext")

    @skipIfBatch("tool contexts need a GUI session")
    def test_a_select_style_round_trips(self):
        prior = cmds.currentCtx()
        try:
            mtk.UiUtils.set_tool("Lasso Select")
            self.assertEqual(mtk.UiUtils.current_tool(), "Lasso Select")
        finally:
            cmds.setToolTo(prior)


class TestAnnotateAndPencil(MayaTkTestCase):
    """UiUtils.annotate / open_pencil: Maya's own annotation and pencil, what
    tentacle's composed utilities panel calls. ``mel.eval`` is stubbed: the real
    commands open a modal prompt (annotate) and a toolbar (Blue Pencil)."""

    def test_annotate_runs_create_annotation_and_arms_no_tool(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(_ui_utils.mel, "eval") as ev:
            self.assertIsNone(mtk.UiUtils.annotate())
        ev.assert_called_once_with("CreateAnnotateNode")

    def test_open_pencil_runs_blue_pencil(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(_ui_utils.mel, "eval") as ev:
            self.assertEqual(mtk.UiUtils.open_pencil(), "OpenBluePencil")
        ev.assert_called_once_with("OpenBluePencil")

    def test_a_refusal_raises(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(
            _ui_utils.mel, "eval", side_effect=RuntimeError("Cannot find procedure")
        ):
            with self.assertRaises(RuntimeError):
                mtk.UiUtils.open_pencil()

    @skipIfBatch("the runtime commands are registered by the GUI")
    def test_both_runtime_commands_exist(self):
        for command in ("CreateAnnotateNode", "OpenBluePencil"):
            self.assertTrue(cmds.runTimeCommand(command, exists=True), command)


class TestEditorCategoriesAndToggles(MayaTkTestCase):
    """``get_editor_categories`` / ``toggle_editor`` and the editor names
    tentacle's shared Editors panel opens -- the mirror of blendertk's.

    ``mel.eval`` is stubbed where it would open or toggle real UI (GUI-only);
    what is pinned is the registry and the toggle rule.
    """

    def test_every_listed_editor_opens(self):
        categories = mtk.UiUtils.get_editor_categories()
        self.assertEqual(
            list(categories),
            [
                "General Editors",
                "Modeling Editors",
                "Animation Editors",
                "Rendering Editors",
                "Relationship Editors",
            ],
        )
        listed = [name for names in categories.values() for name in names]
        self.assertEqual(len(listed), 59)
        self.assertEqual(set(listed) - set(mtk.UiUtils.get_editor_types()), set())

    def test_the_panels_editor_names_open_their_windows(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(_ui_utils.mel, "eval"):
            for name, command in (
                ("Attribute Editor", "AttributeEditor"),
                ("Outliner", "OutlinerWindow"),
                ("Tool Settings", "ToolSettingsWindow"),
                ("Layer Editor", "OpenLayerEditor"),
                ("Channel Box", "OpenChannelBox"),
                ("Node Editor", "NodeEditorWindow"),
                ("Hypergraph: Hierarchy", "HypergraphHierarchyWindow"),
            ):
                self.assertEqual(mtk.UiUtils.open_editor(name), command)

    @staticmethod
    def _toggle(editor, visible):
        """(shown afterwards, the toggle commands run) with *visible* showing."""
        from mayatk.ui_utils import _ui_utils

        ran = []

        def fake(command):
            if command.startswith("isUIComponentVisible"):
                return command.split('"')[1] in visible
            ran.append(command)
            return None

        with mock.patch.object(_ui_utils.mel, "eval", side_effect=fake):
            shown = mtk.UiUtils.toggle_editor(editor)
        return shown, ran

    def test_the_timeline_hides_what_shows_and_shows_both_when_hidden(self):
        both = ["ToggleTimeSlider", "ToggleRangeSlider"]
        self.assertEqual(
            self._toggle("Timeline", {"Time Slider", "Range Slider"}), (False, both)
        )
        self.assertEqual(
            self._toggle("Timeline", {"Time Slider"}), (False, ["ToggleTimeSlider"])
        )
        self.assertEqual(
            self._toggle("Timeline", {"Range Slider"}), (False, ["ToggleRangeSlider"])
        )
        self.assertEqual(self._toggle("Timeline", set()), (True, both))

    def test_a_single_component_toggles(self):
        self.assertEqual(
            self._toggle("Status Line", {"Status Line"}), (False, ["ToggleStatusLine"])
        )
        self.assertEqual(self._toggle("Tool Box", set()), (True, ["ToggleToolbox"]))

    def test_an_unknown_component_raises(self):
        with self.assertRaises(ValueError):
            mtk.UiUtils.toggle_editor("Attribute Editor")

    @skipIfBatch("the UI-component runtime commands are registered by the GUI")
    def test_every_component_toggle_is_a_runtime_command(self):
        for name, command in mtk.UiUtils._UI_COMPONENTS.items():
            self.assertTrue(
                cmds.runTimeCommand(command, exists=True), f"{name}: {command}"
            )


class TestBrowseDirectory(MayaTkTestCase):
    """``browse_directory`` is Maya's own folder browser: the twin of blendertk's
    platform dialog, what the shared Workspace tab's Set Workspace asks."""

    def test_a_pick_is_returned_and_a_dismissal_is_empty(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(
            _ui_utils.cmds, "fileDialog2", return_value=["C:/proj"]
        ) as dialog:
            picked = mtk.UiUtils.browse_directory(
                "C:/", caption="Set Workspace", ok_caption="Set"
            )
        self.assertEqual(picked, "C:/proj")
        kwargs = dialog.call_args.kwargs
        self.assertEqual(kwargs["fileMode"], 3)
        self.assertEqual(kwargs["caption"], "Set Workspace")
        self.assertEqual(kwargs["okCaption"], "Set")
        self.assertEqual(kwargs["startingDirectory"], "C:/")
        with mock.patch.object(
            _ui_utils.cmds, "fileDialog2", return_value=None
        ) as dialog:
            self.assertEqual(mtk.UiUtils.browse_directory(), "")
        self.assertNotIn("startingDirectory", dialog.call_args.kwargs)

    def test_the_project_window_is_an_editor(self):
        self.assertEqual(
            mtk.UiUtils.get_editor_types().get("Project Window"), "ProjectWindow"
        )


class TestOpenPreferences(MayaTkTestCase):
    """``open_preferences``: Maya's own preference windows by section, the mirror
    of blendertk's Preferences tabs (what the shared Preferences panel opens)."""

    def test_each_section_runs_its_window(self):
        from mayatk.ui_utils import _ui_utils

        with mock.patch.object(_ui_utils.mel, "eval") as ev:
            self.assertEqual(mtk.UiUtils.open_preferences(), "PreferencesWindow")
            for section, command in (
                ("colors", "colorPrefWnd"),
                ("hotkeys", "HotkeyPreferencesWindow"),
                ("plugins", "PluginManager"),
            ):
                self.assertEqual(mtk.UiUtils.open_preferences(section), command)
        self.assertEqual(ev.call_count, 4)

    def test_an_unknown_section_raises(self):
        with self.assertRaises(ValueError):
            mtk.UiUtils.open_preferences("nowhere")


if __name__ == "__main__":
    unittest.main(verbosity=2)
