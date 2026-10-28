# !/usr/bin/python
# coding=utf-8
"""Structural load test for the Lightmap Baker panel (lightmap_baker.ui + slots).

Mocked ``maya.cmds`` (this dir's conftest) + a real ``Switchboard`` /
``MayaUiHandler`` load through real (offscreen) Qt -- this directory's
convention: real Maya and real Qt widgets in one process crash natively.

What it is here for: the panel's four booleans are option-box switches
(``LightmapBakerSlots._TOGGLES``), and hanging one WRAPS its field -- the
widget is reparented into an ``OptionBoxContainer``. Under the runtime loader
a reparent can invalidate a ``QUiLoader``-built widget's shiboken wrapper, so
the stub-UI tests in ``test_lightmap_baker.py`` (which never build a real
widget) cannot answer whether ``self.ui.cmb_scope`` is still alive afterwards.
This builds the real thing and reads every wrapped field back.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

mock_cmds = sys.modules.get("maya.cmds")
_CMDS_IS_MOCKED = isinstance(mock_cmds, MagicMock)

try:
    from qtpy import QtWidgets
except Exception:  # pragma: no cover - Qt not installed
    QtWidgets = None


@unittest.skipUnless(
    _CMDS_IS_MOCKED and QtWidgets is not None,
    "Mock + Qt test — run via pytest or run_tests.py --mocks",
)
class TestLightmapBakerPanelLoads(unittest.TestCase):
    """The panel loads, wraps its four fields, and still reads them."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        cls.sb = Switchboard()
        cls.handler = MayaUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("lightmap_baker")
        # Flush the QTimer.singleShot(0, self._initialize_ui) deferred in
        # __init__ — Maya's own event loop does this immediately in real use.
        for _ in range(5):
            cls.app.processEvents()
        cls.slots = cls.ui.slots

    def _toggle(self, key):
        toggle = self.slots._toggle(key)
        self.assertIsNotNone(toggle, f"{key} has no toggle on its field")
        return toggle

    def test_ui_loads_as_the_slots_class(self):
        from mayatk.light_utils.lightmap_baker.lightmap_baker_slots import (
            LightmapBakerSlots,
        )

        self.assertIsNotNone(self.ui, "lightmap_baker UI failed to load")
        self.assertIsInstance(self.slots, LightmapBakerSlots)

    def test_the_help_card_links_the_tools_reference(self):
        """The ``?`` card is read at a glance and ends in a link to the full
        reference, a doc that exists (mayatk's covers both twins). Added:
        2026-10-22"""
        url = self.slots.HELP_URL
        self.assertEqual(self.ui.header.help_url(), url)
        doc = url.split("/blob/main/", 1)[1]
        repo = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.assertTrue(os.path.isfile(os.path.join(repo, doc)), doc)

    def test_every_switch_is_wired_to_its_field(self):
        from uitk.widgets.optionBox.options.toggle import ToggleOption
        from mayatk.light_utils.lightmap_baker.lightmap_baker_slots import (
            LightmapBakerSlots,
        )

        for key, (field, _default) in LightmapBakerSlots._TOGGLES.items():
            with self.subTest(key):
                # A field carrying several switches (Scope's three) holds them
                # in _TOGGLES order: each is the toggle at its place.
                toggles = [
                    option
                    for option in getattr(self.ui, field).option_box.get_options()
                    if isinstance(option, ToggleOption)
                ]
                place = [
                    k
                    for k, (f, _d) in LightmapBakerSlots._TOGGLES.items()
                    if f == field
                ].index(key)
                self.assertIs(self._toggle(key), toggles[place])
                # The STATE is persisted per user, so only its type is pinned
                # here; the shipped defaults are the .ui's and are covered by
                # the stub tests.
                self.assertIsInstance(self.slots._toggle_state(key), bool)

    def test_a_wrapped_field_is_still_readable(self):
        """The wrap reparents the field; a dead wrapper raises RuntimeError
        here rather than at bake time. Types and ranges, not values -- the
        panel restores each field's last session."""
        self.assertIn(
            self.slots._scope(), [s.lower() for s in self.slots._SCOPE_LABELS]
        )
        self.assertIn(self.slots._resolution(), self.slots._RESOLUTIONS)
        self.assertGreaterEqual(self.ui.spn_samples.value(), 1)
        self.assertIsInstance(self.ui.txt_output_dir.text(), str)
        self.assertIn(self.slots._packing(), ("atlas", "per_object"))
        self.assertIn(self.slots._device(), ("AUTO", "GPU", "CPU"))

    def test_a_wrapped_field_is_still_state_managed(self):
        """The wrap must not drop the field out of the window's state -- that
        is what restores Resolution and Samples next session, and what Reset to
        Defaults puts them back to. Read through ``default_for``, not
        ``has_default``: a wrap can hand back a new wrapper, which is why
        ``StateManager`` mirrors the default onto the C++ object."""
        from uitk.managers.state_manager import StateManager

        state = StateManager.for_widget(self.ui)
        for field in ("cmb_scope", "spn_samples", "cmb_resolution", "txt_output_dir"):
            with self.subTest(field):
                self.assertIsNotNone(state.default_for(getattr(self.ui, field)))

    def test_a_wrapped_field_keeps_its_place_in_the_panel(self):
        """Its container stands where the field stood, under the same group --
        so the sections the .ui lays out survive the wrap."""
        for field, group in (
            ("cmb_scope", "page_bake"),
            ("spn_samples", "quality_group"),
            ("cmb_resolution", "quality_group"),
            ("txt_output_dir", "output_group"),
        ):
            with self.subTest(field):
                widget = getattr(self.ui, field)
                container = widget.option_box.container
                self.assertIs(container.parent(), getattr(self.ui, group))

    def test_a_panel_reset_reaches_the_switches(self):
        """Reset to Defaults resets FIELDS, and a switch is part of its field --
        this is the per-field pass ``StateManager.reset_all`` makes. Not the
        whole panel: that would write over this machine's saved panel state."""
        toggle = self._toggle("denoise")
        was, default = toggle.is_on, toggle._default_on()
        try:
            toggle.set_on(not default)
            self.ui.cmb_resolution.option_box.restore_option_defaults()
            self.assertEqual(toggle.is_on, default)
        finally:
            toggle.set_on(was)

    def test_the_adaptive_switch_greys_out_on_a_cpu_bake(self):
        """A CPU bake spends its samples the other way, so the switch says so.
        The rule targets the toggle's BUTTON: the Samples field stays live."""
        button = self._toggle("adaptive").widget
        self.ui.cmb_device.setCurrentIndex(0)  # Auto
        self.app.processEvents()
        self.assertTrue(button.isEnabled())

        self.ui.cmb_device.setCurrentIndex(2)  # CPU
        self.app.processEvents()
        self.assertEqual(self.slots._device(), "CPU")
        self.assertFalse(button.isEnabled(), "adaptive does not apply on the CPU")
        self.assertTrue(self.ui.spn_samples.isEnabled(), "Samples still do")

        self.ui.cmb_device.setCurrentIndex(0)  # Auto
        self.app.processEvents()
        self.assertTrue(button.isEnabled())


    def test_the_bake_page_bakes_fresh_and_the_manage_page_redoes(self):
        """Creation and management are two pages: Scope bakes fresh (no
        Changed row), the header menu holds what reads the whole scene -- the
        Bake Report -- and the run button is the Bake page's. Changed:
        2026-10-23 (Select Changed moved to the footer's Select)."""
        self.assertEqual(
            [self.ui.cmb_scope.itemText(i) for i in range(self.ui.cmb_scope.count())],
            ["Selected", "Visible", "Scene"],
        )
        QPushButton = self.slots.sb.QtWidgets.QPushButton
        self.assertEqual(
            [b.objectName() for b in self.ui.header.menu.findChildren(QPushButton)
             if b.objectName() and not b.objectName().startswith("hdr_")],
            ["show_bake_state"],
        )
        for retired in ("lbl_bake_set", "select_bake_set"):
            self.assertIsNone(getattr(self.ui, retired, None), retired)
        self.assertEqual(
            [self.ui.cmb_page.itemText(i) for i in range(self.ui.cmb_page.count())],
            ["Bake", "Manage"],
        )
        self._show_page(0)
        self.assertEqual(self.ui.stk_pages.currentWidget(), self.ui.page_bake)
        self.assertEqual(self.ui.b000.text(), "Bake Lightmaps")
        self.assertTrue(self.ui.b000.isEnabled())

    def test_the_output_field_opens_its_folder(self):
        """The folder opens from the field that names it (the header menu's
        Open Sourceimages Folder retired). Added: 2026-10-22"""
        opens = [
            option
            for option in self.ui.txt_output_dir.option_box.get_options()
            if getattr(option, "_action_handler", None) == self.slots.open_output
        ]
        self.assertEqual(len(opens), 1)

    def _show_page(self, index):
        self.ui.cmb_page.setCurrentIndex(index)
        self.slots.cmb_page(index)
        self.app.processEvents()

    def _manage(self, rows, report):
        from unittest import mock
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        with mock.patch.object(
            LightmapBaker, "stale_report", return_value=report
        ), mock.patch.object(LightmapBaker, "bake_maps", return_value=rows):
            self._show_page(1)

    def test_preset_and_reset_are_the_bake_pages(self):
        """The Manage page shows no Preset, Reset to Defaults or run button:
        they act on settings only the Bake page shows, and the Manage page's
        verbs are its table menu's. Changed: 2026-10-22"""
        import pythontk as ptk

        ui = self.ui
        try:
            self._manage([], ptk.StaleReport())
            self.assertFalse(ui.cmb000.isVisibleTo(ui))
            self.assertFalse(ui.btn_reset_defaults.isVisibleTo(ui))
            self.assertFalse(ui.b000.isVisibleTo(ui))
            self._show_page(0)
            for widget in (ui.cmb000, ui.btn_reset_defaults, ui.b000):
                self.assertTrue(widget.isVisibleTo(ui), widget.objectName())
        finally:
            self._show_page(0)

    def test_the_table_menu_re_bakes_and_reverts_the_selection(self):
        """Re-Bake and Revert are the table menu's, bound to the selection's
        verbs; the Manage page carries no buttons of its own. Added:
        2026-10-22"""
        menu = self.ui.tbl_maps.menu
        labels = {
            name: self._menu_item(menu, name).text()
            for name in (
                "rebake_selected", "combine_selected", "track_selected",
                "revert_selected", "open_map_folder",
            )
        }
        self.assertEqual(
            labels,
            {"rebake_selected": "Re-Bake", "combine_selected": "Combine…",
             "track_selected": "Start Tracking", "revert_selected": "Revert…",
             "open_map_folder": "Open Folder"},
        )
        QPushButton = self.slots.sb.QtWidgets.QPushButton
        buttons = [  # the menu's own items are buttons parented under the table
            b.objectName()
            for b in self.ui.page_manage.findChildren(QPushButton)
            if not menu.isAncestorOf(b)
        ]
        self.assertEqual(buttons, [])

    def test_select_changed_selects_the_targets_and_highlights_their_maps(self):
        """The footer's Select: Changed selects exactly what a re-bake would
        redo and highlights the maps they read -- quietly: the highlight would
        select every reader of those maps. Then Re-Bake redoes exactly them.
        Changed: 2026-10-23 (was the header menu's Select Changed)."""
        from unittest import mock
        import pythontk as ptk
        from mayatk.light_utils.lightmap_baker import lightmap_baker_slots as mod
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        report = ptk.StaleReport(
            targets=("|room|A",), stale={"|room|A": (ptk.BakeBaseline.Reason.MOVED,)}
        )
        rows = report.map_rows(
            [
                {"map": "Room_LM.exr", "path": "C:/p/Room_LM.exr", "folder": "lm",
                 "objects": ["|room|A", "|room|B"], "writer": None, "size": (512, 512)},
                {"map": "Desk_LM.exr", "path": "C:/p/Desk_LM.exr", "folder": "lm",
                 "objects": ["|room|D"], "writer": None, "size": (512, 512)},
            ]
        )
        table = self.ui.tbl_maps
        try:
            self._manage(rows, report)
            mod.cmds.select.reset_mock()
            mod.cmds.objExists.return_value = True
            with mock.patch.object(
                LightmapBaker, "stale_report", return_value=report
            ), mock.patch.object(LightmapBaker, "bake_maps", return_value=rows):
                combo = self.ui.footer._lightmap_manage[0]
                combo.activated.emit(combo.findText("Changed"))
            # Cleared first: a pick that finds nothing leaves nothing selected.
            self.assertEqual(
                mod.cmds.select.call_args_list,
                [mock.call(clear=True), mock.call(["|room|A"], replace=True)],
            )
            self.assertEqual([r["map"] for r in self.slots._selected_maps()], ["Room_LM.exr"])
            self.assertEqual(self.ui.footer.text(), "Selected 1 object on 1 map.")
            self.assertEqual(combo.currentIndex(), 0, "the combo shows Select again")
        finally:
            blocked = table.blockSignals(True)
            table.clearSelection()
            table.blockSignals(blocked)
            self._show_page(0)

    def test_the_manage_page_lists_each_map_with_its_state(self):
        """One row per map: its readers' count and state, a tooltip saying
        where it is, how to fix its state, and why each reader re-bakes.
        Added: 2026-10-22"""
        import pythontk as ptk

        report = ptk.StaleReport(
            targets=("|room|A",), stale={"|room|A": (ptk.BakeBaseline.Reason.MOVED,)}
        )
        rows = report.map_rows(
            [
                {"map": "Room_LM.exr", "path": "C:/p/Room_LM.exr", "folder": "lm",
                 "objects": ["|room|A", "|room|B"], "writer": None, "size": (512, 512)},
                {"map": "Lost_LM.exr", "path": None, "folder": "lm",
                 "objects": ["|room|C"], "writer": None, "size": None},
            ]
        )
        try:
            self._manage(rows, report)
            table = self.ui.tbl_maps
            self.assertEqual(self.ui.stk_pages.currentWidget(), self.ui.page_manage)
            self.assertEqual(table.rowCount(), 2)
            cells = {
                table.item(r, 0).text(): (table.item(r, 1).text(), table.item(r, 2).text())
                for r in range(table.rowCount())
            }
            self.assertEqual(cells["Room_LM.exr"], ("2", "1 changed"))
            self.assertEqual(cells["Lost_LM.exr"], ("1", "missing"))
            room = next(r for r in range(2) if table.item(r, 0).text() == "Room_LM.exr")
            tip = table.item(room, 2).toolTip()
            self.assertIn("A: moved", tip)
            # The tooltip says what the state means and which button fixes it.
            self.assertIn("Re-Bake", tip)
        finally:
            self._show_page(0)

    def test_highlighting_a_row_selects_its_maps_readers(self):
        """The rows are the selection the table menu's Re-Bake and Revert act
        on. Added: 2026-10-22"""
        import pythontk as ptk
        from mayatk.light_utils.lightmap_baker import lightmap_baker_slots as mod

        report = ptk.StaleReport()
        rows = report.map_rows(
            [{"map": "Room_LM.exr", "path": "C:/p/Room_LM.exr", "folder": "lm",
              "objects": ["|room|A", "|room|B"], "writer": None, "size": (512, 512)}]
        )
        try:
            self._manage(rows, report)
            mod.cmds.select.reset_mock()
            mod.cmds.objExists.return_value = True
            self.ui.tbl_maps.selectRow(0)
            self.app.processEvents()
            mod.cmds.select.assert_called_with(["|room|A", "|room|B"], replace=True)
        finally:
            self.ui.tbl_maps.clearSelection()
            self._show_page(0)


    def test_a_refresh_keeps_the_highlighted_rows_and_the_scene_selection(self):
        """A re-bake or revert refills the table: the rows highlighted before
        stay highlighted, and the refill selects nothing in the scene (it
        cleared the user's selection). Added: 2026-10-22"""
        import pythontk as ptk
        from mayatk.light_utils.lightmap_baker import lightmap_baker_slots as mod

        report = ptk.StaleReport()
        rows = report.map_rows(
            [{"map": name, "path": f"C:/p/{name}", "folder": "lm",
              "objects": [f"|{name}|A"], "writer": None, "size": (512, 512)}
             for name in ("A_LM.exr", "B_LM.exr", "C_LM.exr")]
        )
        try:
            self._manage(rows, report)
            table = self.ui.tbl_maps
            names = [table.item(r, 0).text() for r in range(table.rowCount())]
            flags = self.slots.sb.QtCore.QItemSelectionModel
            for name in ("A_LM.exr", "C_LM.exr"):
                table.selectionModel().select(
                    table.model().index(names.index(name), 0), flags.Select | flags.Rows
                )
            self.app.processEvents()
            mod.cmds.select.reset_mock()
            self._manage(rows, report)
            self.assertEqual(
                sorted(row["map"] for row in self.slots._selected_maps()),
                ["A_LM.exr", "C_LM.exr"],
            )
            mod.cmds.select.assert_not_called()
        finally:
            self.ui.tbl_maps.clearSelection()
            self._show_page(0)


    def _menu_item(self, menu, name):
        return menu.findChild(self.slots.sb.QtWidgets.QPushButton, name)

    def test_start_tracking_is_offered_on_an_untracked_row(self):
        """Tracking records a map as matching the scene; offered where a row
        is untracked, and nowhere else. Added: 2026-10-22"""
        import pythontk as ptk

        report = ptk.StaleReport(untracked=("|room|U",))
        rows = report.map_rows(
            [{"map": name, "path": f"C:/p/{name}", "folder": "lm",
              "objects": [obj], "writer": None, "size": (512, 512)}
             for name, obj in (("T_LM.exr", "|room|T"), ("U_LM.exr", "|room|U"))]
        )
        table, item = self.ui.tbl_maps, None
        try:
            self._manage(rows, report)
            item = self._menu_item(table.menu, "track_selected")
            names = [table.item(r, 0).text() for r in range(table.rowCount())]
            for name, enabled in (("T_LM.exr", False), ("U_LM.exr", True)):
                table.selectRow(names.index(name))
                self.slots._fit_map_menu()
                self.assertEqual(item.isEnabled(), enabled, name)
        finally:
            table.clearSelection()
            self._show_page(0)

    def test_select_and_fix_all_are_the_manage_pages_footer(self):
        """Select (maps by state) and Fix All sit in the footer, shown with
        the Manage page only. Added: 2026-10-23"""
        import pythontk as ptk

        combo, fix = self.ui.footer._lightmap_manage
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count())],
            ["Select…", "Changed", "Split", "Untracked", "Missing", "All"],
        )
        self.assertEqual(fix.text(), "Fix All")
        # A button with a background (an action button is transparent on the
        # footer), and a list as wide as its items (a plain one clipped them).
        # Changed: 2026-10-23
        self.assertFalse(fix.property("footerWidget"))
        self.assertGreaterEqual(combo.width(), combo.sizeHint().width())
        combo.showPopup()
        try:
            view = combo.view()
            self.assertGreaterEqual(view.viewport().width(), view.sizeHintForColumn(0))
        finally:
            combo.hidePopup()
            self.app.processEvents()  # the popup closed before teardown
        # A reloaded panel re-wires its own handler, once.
        self.slots._manage_footer()
        self.assertEqual(fix.receivers("2clicked(bool)"), 1)
        try:
            self._manage([], ptk.StaleReport())
            self.assertTrue(combo.isVisibleTo(self.ui) and fix.isVisibleTo(self.ui))
            self._show_page(0)
            self.assertFalse(combo.isVisibleTo(self.ui) or fix.isVisibleTo(self.ui))
        finally:
            self._show_page(0)

    def test_combine_is_offered_on_a_split_row(self):
        """Combine packs a split; offered where a row is split, nowhere else.
        Added: 2026-10-23"""
        import pythontk as ptk

        report = ptk.StaleReport(
            split={"S_LM.exr": ("S_LM.exr", "S_LM_1.exr")},
            split_materials={"S_LM.exr": ("Crate",)},
        )
        rows = report.map_rows(
            [{"map": name, "path": f"C:/p/{name}", "folder": "lm",
              "objects": [f"|room|{name[0]}"], "writer": None, "size": (512, 512)}
             for name in ("S_LM.exr", "T_LM.exr")]
        )
        table = self.ui.tbl_maps
        item = self._menu_item(table.menu, "combine_selected")
        try:
            self._manage(rows, report)
            names = [table.item(r, 0).text() for r in range(table.rowCount())]
            for name, enabled in (("T_LM.exr", False), ("S_LM.exr", True)):
                table.selectRow(names.index(name))
                self.slots._fit_map_menu()
                self.assertEqual(item.isEnabled(), enabled, name)
        finally:
            table.clearSelection()
            self._show_page(0)

    def test_the_table_menu_edits_no_material(self):
        """A material split over two shading groups is one atlas group now (the
        baker groups by what a group renders), so the table offers no merge:
        the baker never reassigns what the exporter reads. Changed:
        2026-10-22"""
        menu = self.ui.tbl_maps.menu
        self.assertIsNone(self._menu_item(menu, "merge_duplicate_materials"))
        self.assertIsNotNone(self._menu_item(menu, "open_map_folder"))
        self.assertFalse(hasattr(self.slots, "merge_duplicate_materials"))

    def test_the_bake_report_is_the_header_menus_not_a_rows(self):
        """The Bake Report covers the whole scene, so it is offered in the
        header menu, not on a row's menu. Added: 2026-10-22"""
        self.assertIsNotNone(self._menu_item(self.ui.header.menu, "show_bake_state"))
        self.assertIsNone(self._menu_item(self.ui.tbl_maps.menu, "show_bake_state"))

    def test_a_footer_message_is_colored_by_its_level(self):
        """Something to act on is gold, a failure red, a plain report the
        theme's color. Added: 2026-10-22"""
        import pythontk as ptk

        report = ptk.StaleReport(
            targets=("|room|A",), stale={"|room|A": (ptk.BakeBaseline.Reason.MOVED,)}
        )
        rows = report.map_rows(
            [{"map": "Room_LM.exr", "path": "C:/p/Room_LM.exr", "folder": "lm",
              "objects": ["|room|A"], "writer": None, "size": (512, 512)}]
        )
        footer = self.ui.footer
        try:
            self._manage(rows, report)
            self.assertEqual(footer._status_level, "warning", "one to re-bake")
            missing = ptk.StaleReport().map_rows(
                [{"map": "Gone_LM.exr", "path": None, "folder": "lm",
                  "objects": ["|room|A"], "writer": None, "size": None}]
            )
            self._manage(missing, ptk.StaleReport())
            self.assertEqual(footer._status_level, "error", "a map is missing")
            self.ui.tbl_maps.clearSelection()
            self.slots.open_map_folder()
            self.assertEqual(footer._status_level, "warning", "nothing highlighted")
            self._manage(rows[:0], ptk.StaleReport())
            self.assertIsNone(footer._status_level, "nothing baked is no fault")
        finally:
            self._show_page(0)


class TestScopeWords(unittest.TestCase):
    """Which scene read each Scope word takes, before ``resolve_meshes``.

    Stubbed reads (``cmds.ls`` + ``DisplayUtils.is_visible``) and a stub panel:
    the reads themselves run against a real scene in ``test_lightmap_baker.py``
    (instanced paths, hidden and templated meshes). What is pinned here is the
    word -> read mapping, and that the Scene/Visible words gather EVERY DAG path
    of every mesh (an instanced shape is one node under several transforms).
    """

    def _pool(self, label):
        from unittest import mock

        from mayatk.display_utils._display_utils import DisplayUtils
        from mayatk.light_utils.lightmap_baker import lightmap_baker_slots as mod

        calls = []

        def ls(*args, **kwargs):
            calls.append(kwargs)
            if kwargs.get("selection"):
                return ["|sel"]
            return ["|seen", "|hidden"]

        stub = mod.LightmapBakerSlots.__new__(mod.LightmapBakerSlots)
        stub._scope = lambda: label
        with (
            mock.patch.object(mod, "cmds", mock.Mock(ls=ls)),
            mock.patch.object(
                mod.TextureBaker,
                "resolve_meshes",
                staticmethod(lambda pool, **_kw: list(pool)),
            ),
            mock.patch.object(
                DisplayUtils, "is_visible", staticmethod(lambda path: path == "|seen")
            ),
        ):
            return stub._scope_objects(), calls

    def test_selected_reads_the_selection(self):
        pool, calls = self._pool("selected")
        self.assertEqual(pool, ["|sel"])
        self.assertEqual(calls, [{"selection": True, "long": True}])

    def test_scene_reads_every_mesh_path(self):
        pool, calls = self._pool("scene")
        self.assertEqual(pool, ["|seen", "|hidden"])
        self.assertTrue(calls[0].get("allPaths") and calls[0].get("type") == "mesh")

    def test_visible_filters_the_scene_read(self):
        pool, _calls = self._pool("visible")
        self.assertEqual(pool, ["|seen"])

    def test_an_unknown_word_never_widens(self):
        pool, _calls = self._pool("bogus")
        self.assertEqual(pool, ["|sel"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
