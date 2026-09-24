# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils.reference_manager module

GUI-only (registered in ``run_tests.GUI_REQUIRED``):
``TestToggleReferenceOnCurrentSceneIsOneClick`` builds a real uitk
``TableWidget``, which hard-crashes mayapy in batch.
"""

import unittest
import os
import tempfile
from html import escape as html_escape
from unittest.mock import patch, MagicMock, PropertyMock

import pythontk as ptk
import mayatk.env_utils.reference_manager as ref_mgr


# Mock classes for UI components to allow testing logic without a GUI
class MockQt:
    UserRole = 32
    ItemIsEditable = 2
    ItemIsEnabled = 1
    ItemIsSelectable = 4


class QtCore:
    Qt = MockQt()


class QtWidgets:
    class QApplication:
        @staticmethod
        def instance():
            return True

        def __init__(self, args):
            pass

    class QLabel:
        def __init__(self, text="", parent=None):
            self._text = text
            self._properties = {}

        def setText(self, t):
            self._text = t

        def text(self):
            return self._text

        def setProperty(self, key, val):
            self._properties[key] = val

        def property(self, key):
            return self._properties.get(key)

    class QTableWidgetItem:
        def __init__(self, text=""):
            self._text = text
            self._flags = 0
            self._data = {}
            self._tooltip = ""

        def text(self):
            return self._text

        def setText(self, t):
            self._text = t

        def flags(self):
            return self._flags

        def setFlags(self, f):
            self._flags = f

        def data(self, role):
            return self._data.get(role)

        def setData(self, role, val):
            self._data[role] = val

        def setToolTip(self, t):
            self._tooltip = t

        def toolTip(self):
            return self._tooltip

    class QTableWidget:
        def __init__(self):
            self._rows = []
            self._sorting = False
            self._cell_widgets = {}
            self._hidden_rows = set()
            self._hidden_cols = set()
            self.actions = type("Actions", (), {"set": lambda *a, **kw: None})()

        def setColumnHidden(self, col, hidden):
            if hidden:
                self._hidden_cols.add(col)
            else:
                self._hidden_cols.discard(col)

        def isColumnHidden(self, col):
            return col in self._hidden_cols

        def setRowCount(self, count):
            current = len(self._rows)
            if count < current:
                self._rows = self._rows[:count]
            else:
                for _ in range(count - current):
                    self._rows.append([None, None, None])  # 3 columns now

        def rowCount(self):
            return len(self._rows)

        def item(self, row, col):
            if 0 <= row < len(self._rows) and col < len(self._rows[row]):
                return self._rows[row][col]
            return None

        def setItem(self, row, col, item):
            if 0 <= row < len(self._rows):
                while len(self._rows[row]) <= col:
                    self._rows[row].append(None)
                self._rows[row][col] = item

        def setCellWidget(self, row, col, widget):
            self._cell_widgets[(row, col)] = widget

        def cellWidget(self, row, col):
            return self._cell_widgets.get((row, col))

        def isSortingEnabled(self):
            return self._sorting

        def setSortingEnabled(self, val):
            self._sorting = val

        def apply_formatting(self):
            pass

        def insertRow(self, row):
            self._rows.insert(row, [None, None, None])

        def removeRow(self, row):
            self._rows.pop(row)

        def clearContents(self):
            self._rows = []
            self._cell_widgets = {}

        def blockSignals(self, block):
            return False

        def setUpdatesEnabled(self, val):
            pass

        def setRowHidden(self, row, hidden):
            if hidden:
                self._hidden_rows.add(row)
            else:
                self._hidden_rows.discard(row)

        def isRowHidden(self, row):
            return row in self._hidden_rows

        def selectedIndexes(self):
            return []  # nothing selectable is selected — the foreign-row scenario


class MockSettings:
    """Mock for uitk SettingsManager — stores values in a plain dict."""

    def __init__(self):
        self._store = {}

    def value(self, key, default=None):
        return self._store.get(key, default)

    def setValue(self, key, value):
        self._store[key] = value


class MockLineEdit:
    """Mock for txt000 QLineEdit."""

    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t

    def strip(self):
        return self._text.strip()

    def setToolTip(self, t):
        pass

    def set_action_color(self, c):
        pass


class MockComboBox:
    """Mock for cmb000 QComboBox."""

    def __init__(self):
        self._items = []  # list of (text, data)
        self._current_index = -1
        self._signals_blocked = False
        self.option_box = MagicMock()

    def addItem(self, text, data=None):
        self._items.append((text, data))
        if self._current_index == -1:
            self._current_index = 0

    def add(self, items):
        """uitk-style add: list of (text, data) tuples."""
        for text, data in items:
            self._items.append((text, data))
        if self._items and self._current_index == -1:
            self._current_index = 0

    def clear(self):
        self._items = []
        self._current_index = -1

    def count(self):
        return len(self._items)

    def currentIndex(self):
        return self._current_index

    def setCurrentIndex(self, i):
        if 0 <= i < len(self._items):
            self._current_index = i

    def itemText(self, i):
        if 0 <= i < len(self._items):
            return self._items[i][0]
        return ""

    def itemData(self, i):
        if 0 <= i < len(self._items):
            return self._items[i][1]
        return None

    def blockSignals(self, block):
        self._signals_blocked = block
        return not block


class MockSlot:
    def __init__(self):
        self.sb = MockSB()
        self.ui = MockUI()
        self.ui.tbl000 = QtWidgets.QTableWidget()


class MockSB:
    def __init__(self):
        self.QtWidgets = QtWidgets
        self.QtCore = QtCore
        self._tooltip = None

    @property
    def tooltip(self):
        """The REAL Switchboard tooltip namespace, built on first access.

        Production reaches the rich-text DSL through ``self.sb.tooltip`` (it
        used to import ``TooltipFormat`` directly), and the preview tests assert
        on actually-rendered HTML -- a stub would leave those assertions passing
        while proving nothing.

        Resolved lazily rather than in ``__init__`` because the real namespace
        needs a Qt binding while this suite otherwise runs against the fake
        ``QtWidgets`` above: building it eagerly would make every MockSB-backed
        test in the file depend on Qt, when only the two preview cases touch it.
        """
        if self._tooltip is None:
            from uitk.widgets.mixins.tooltip_mixin import TooltipNamespace

            self._tooltip = TooltipNamespace(self)
        return self._tooltip

    def message_box(self, msg, *buttons):
        pass


class MockUI:
    pass


class MockLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def info(self, msg):
        pass

    def error(self, msg):
        pass

    def setLevel(self, level):
        pass


class TestForeignConversionProgress(unittest.TestCase):
    """A foreign-scene conversion reports into the footer, and an Esc-hold stops it
    rather than failing it (mirror of blendertk's Reference Manager check).

    A conversion blocks for as long as the scene takes -- a headless Blender converts,
    then a headless Maya bakes -- so the panel hands the engine a progress callback from
    its footer instead of freezing behind a wait cursor.
    """

    class _Footer:
        def __init__(self):
            self.texts = []

        def setText(self, text, level=None):
            self.texts.append((text, level))

    class _SB(MockSB):
        def __init__(self):
            super().__init__()
            self.progress_calls, self.bar, self.busy, self.messages = [], [], 0, []

        def message_box(self, msg, *buttons):
            self.messages.append(msg)

        def busy_cursor(self):
            import contextlib

            @contextlib.contextmanager
            def _busy():
                self.busy += 1
                yield

            return _busy()

        def progress(self, ui=None, total=None, text="", busy=None):
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                self.progress_calls.append((ui, total, text))
                yield (
                    lambda value=None, message=None: (
                        self.bar.append((value, message)) or True
                    )
                )

            return _ctx()

        @staticmethod
        def progress_adapter(update):
            return lambda current, total, message: update(current, message)

    def _slots(self):
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slots.sb = self._SB()
        slots.ui = MockUI()
        slots.ui.footer = self._Footer()
        slots.logger = MockLogger()
        return slots

    def test_the_engine_reports_into_the_footer_and_a_stop_is_not_a_failure(self):
        import pythontk as ptk
        from mayatk.env_utils.blender_bridge import _scene_import as si

        slots = self._slots()
        seen = {}

        def stub(self, path, via=None, progress=None, rig_mode=None):
            seen["progress"] = progress
            if progress is not None:
                progress(40, 100, "Blender: Writing the FBX")
            raise ptk.OperationCancelled("stub stop")

        with patch.object(si.BlenderSceneImport, "bake_scene", stub):
            result = slots._bake_foreign_path("C:/proj/mesh.blend")
        self.assertIsNone(result)
        self.assertTrue(callable(seen.get("progress")))
        self.assertIn((40, "Blender: Writing the FBX"), slots.sb.bar)
        self.assertEqual(slots.sb.busy, 1)
        self.assertEqual(slots.sb.progress_calls[0][1], 100)
        self.assertIn("mesh.blend", slots.sb.progress_calls[0][2])
        self.assertFalse(slots.sb.messages, "a stop is not an error box")
        self.assertTrue(
            any("Stopped converting mesh.blend" in t for t, _ in slots.ui.footer.texts),
            slots.ui.footer.texts,
        )

    def test_a_finished_conversion_returns_its_bake(self):
        from mayatk.env_utils.blender_bridge import _scene_import as si

        slots = self._slots()

        def stub(self, path, via=None, progress=None, rig_mode=None):
            if progress is not None:
                progress(100, 100, "Maya: baked")
            return "C:/cache/mesh.ma"

        with patch.object(si.BlenderSceneImport, "bake_scene", stub):
            self.assertEqual(
                slots._bake_foreign_path("C:/proj/mesh.blend"), "C:/cache/mesh.ma"
            )
        self.assertFalse(slots.sb.messages)


class TestReferenceManager(unittest.TestCase):
    """Tests for ReferenceManagerController logic."""

    def setUp(self):
        # Create controller with mocks
        self.slot = MockSlot()

        # Patch the controller class to avoid super().__init__ calls that might need Maya
        # We specificially want to test the update_table logic which is pure Python/Qt
        self.controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        self.controller.slot = self.slot
        self.controller.sb = self.slot.sb
        self.controller.ui = self.slot.ui
        self.controller.logger = MockLogger()
        self.controller._format_table_item = lambda *args: None  # specific mock
        self.controller._active_filter_text = ""  # No filter by default
        self.controller._active_ignore_case = True
        self.controller._active_include_notes = True

        # Mock current_references (returns empty list — no Maya needed)
        self.controller.__class__.current_references = property(lambda self: [])

        # Patch cmds.file so update_table doesn't need Maya
        self._cmds_file_patcher = patch.object(
            ref_mgr.cmds, "file", create=True, return_value=""
        )
        self._mock_cmds_file = self._cmds_file_patcher.start()

    def tearDown(self):
        self._cmds_file_patcher.stop()

    def test_update_table_replaces_rows_correctly(self):
        """Test that update_table correctly sets row count and updates items, removing old ones."""
        t = self.controller.ui.tbl000

        # Setup initial state with 3 rows (simulating old workspace)
        initial_files = ["file1.mb", "file2.mb", "file3.mb"]
        initial_paths = ["/path/A/file1.mb", "/path/A/file2.mb", "/path/A/file3.mb"]

        self.controller.update_table(initial_files, initial_paths)

        self.assertEqual(t.rowCount(), 3)
        self.assertEqual(t.item(0, 0).text(), "file1.mb")

        # Now switch workspace - new list with 2 different files
        new_files = ["file4.mb", "file5.mb"]
        new_paths = ["/path/B/file4.mb", "/path/B/file5.mb"]

        self.controller.update_table(new_files, new_paths)

        # Assertions
        self.assertEqual(
            t.rowCount(), 2, "Row count should match new list size exactly"
        )
        self.assertEqual(
            t.item(0, 0).text(), "file4.mb", "First item should be from new list"
        )
        self.assertEqual(
            t.item(1, 0).text(), "file5.mb", "Second item should be from new list"
        )

    def test_update_table_handles_duplicates_in_previous_state(self):
        """Test that it clears GHOST rows (duplicates that shouldn't be there)."""
        t = self.controller.ui.tbl000

        # Manually inject ghost rows (simulating the bug state)
        t.setRowCount(4)
        item1 = QtWidgets.QTableWidgetItem("file1.mb")
        item2 = QtWidgets.QTableWidgetItem("file1.mb")  # Ghost duplicate
        item3 = QtWidgets.QTableWidgetItem("file2.mb")
        item4 = QtWidgets.QTableWidgetItem("file3.mb")

        t.setItem(0, 0, item1)
        t.setItem(1, 0, item2)
        t.setItem(2, 0, item3)
        t.setItem(3, 0, item4)

        self.assertEqual(t.rowCount(), 4)

        # Update with new clear list
        new_files = ["new_file.mb"]
        new_paths = ["/path/new_file.mb"]

        self.controller.update_table(new_files, new_paths)

        self.assertEqual(t.rowCount(), 1, "Should have exactly 1 row")
        self.assertEqual(t.item(0, 0).text(), "new_file.mb")

    def test_update_table_filter_shows_notes_match(self):
        """Rows whose notes match the filter should remain visible even
        when the filename doesn't match.

        Bug: filter only matched filenames; files with matching notes
        (e.g. 'Layout, Speedrun') were hidden when filtering 'Speedrun'.
        Fixed: 2026-03-03
        """
        t = self.controller.ui.tbl000

        # Set an active filter that does NOT match filenames
        self.controller._active_filter_text = "Speedrun"
        self.controller._active_ignore_case = True

        files = ["PROP_SET_ACTION.ma", "PROP_SET_OTHER.ma"]
        paths = ["/ws/PROP_SET_ACTION.ma", "/ws/PROP_SET_OTHER.ma"]

        self.controller.update_table(files, paths)

        # Neither filename matches 'Speedrun', so both hidden initially
        self.assertTrue(t.isRowHidden(0))
        self.assertTrue(t.isRowHidden(1))

        # Now simulate notes on row 0 matching the filter
        notes_item = t.item(
            0, 4
        )  # Notes is column 4 (col 3 is the display-mode action column)
        self.assertIsNotNone(notes_item)
        notes_item.setText("Layout, Speedrun")

        # Re-run update_table so the post-filter picks up the notes
        self.controller.update_table(files, paths)

        # Row 0 has notes matching 'Speedrun' — should be visible
        self.assertFalse(t.isRowHidden(0), "Row with matching notes should be visible")
        # Row 1 has no matching notes — should be hidden
        self.assertTrue(
            t.isRowHidden(1), "Row without matching filename or notes should be hidden"
        )

    def test_update_table_filter_shows_filename_match(self):
        """Rows whose filename matches the filter should remain visible."""
        t = self.controller.ui.tbl000

        self.controller._active_filter_text = "*ACTION*"
        self.controller._active_ignore_case = True

        files = ["PROP_SET_ACTION.ma", "PROP_SET_OTHER.ma"]
        paths = ["/ws/PROP_SET_ACTION.ma", "/ws/PROP_SET_OTHER.ma"]

        self.controller.update_table(files, paths)

        self.assertFalse(
            t.isRowHidden(0), "Row with matching filename should be visible"
        )
        self.assertTrue(
            t.isRowHidden(1), "Row without matching filename should be hidden"
        )

    def test_update_table_no_filter_all_visible(self):
        """When no filter is active, all rows should be visible."""
        t = self.controller.ui.tbl000

        self.controller._active_filter_text = ""
        self.controller._active_ignore_case = True

        files = ["file1.ma", "file2.ma"]
        paths = ["/ws/file1.ma", "/ws/file2.ma"]

        self.controller.update_table(files, paths)

        self.assertFalse(t.isRowHidden(0))
        self.assertFalse(t.isRowHidden(1))

    def test_update_table_filter_notes_disabled(self):
        """When 'Include Notes' is unchecked, notes should not contribute to matching."""
        t = self.controller.ui.tbl000

        self.controller._active_filter_text = "Speedrun"
        self.controller._active_ignore_case = True
        self.controller._active_include_notes = False  # Notes matching disabled

        files = ["PROP_SET_ACTION.ma"]
        paths = ["/ws/PROP_SET_ACTION.ma"]

        self.controller.update_table(files, paths)

        # Set notes that would match
        notes_item = t.item(
            0, 4
        )  # Notes is column 4 (col 3 is the display-mode action column)
        notes_item.setText("Layout, Speedrun")
        self.controller.update_table(files, paths)

        # Even though notes match, include_notes is False so row should be hidden
        self.assertTrue(
            t.isRowHidden(0),
            "Row should be hidden when include_notes is disabled",
        )

    def test_is_foreign_classifies_blend_only(self):
        """_is_foreign flags .blend (the cross-DCC row) and nothing else — .fbx and USD are
        NATIVE on this side (Maya references both directly, through their translators), so
        neither may classify as foreign."""
        C = ref_mgr.ReferenceManagerController
        self.assertTrue(C._is_foreign("C:/proj/mesh.blend"))
        self.assertFalse(C._is_foreign("C:/proj/scene.ma"))
        self.assertFalse(C._is_foreign("C:/proj/scene.mb"))
        self.assertFalse(C._is_foreign("C:/proj/prop.fbx"))
        for ext in ptk.USD_EXTENSIONS:
            self.assertFalse(C._is_foreign(f"C:/proj/set{ext}"), ext)
        self.assertFalse(C._is_foreign(""))

    def test_include_type_classification_is_the_inverse_of_blendertk(self):
        """The panel's file-type split is the mirror of the Blender panel's: this side's
        natives are .ma/.mb/.fbx plus every USD spelling and its only foreign type is
        .blend, and every include toggle lists one of the five shared types."""
        S = ref_mgr.ReferenceManagerSlots
        self.assertEqual(S._INCLUDE_TYPES, ("ma", "mb", "fbx", "usd", "blend"))
        self.assertEqual(
            S.NATIVE_EXTENSIONS, (".ma", ".mb", ".fbx", *ptk.USD_EXTENSIONS)
        )
        self.assertEqual(S.FOREIGN_EXTENSIONS, (".blend",))
        self.assertEqual(S._INCLUDE_DEFAULTS, (".ma", ".mb"))
        self.assertEqual(
            {ext for t in S._INCLUDE_TYPES for ext in S._type_extensions(t)},
            set(S.NATIVE_EXTENSIONS) | set(S.FOREIGN_EXTENSIONS),
            "every include toggle must classify as native or foreign",
        )

    def test_the_usd_toggle_lists_every_usd_spelling(self):
        """One 'usd' toggle, four extensions: a layer or package is any of
        .usd/.usda/.usdc/.usdz, and the checkbox is off by default like 'fbx'
        (deliverables, not this panel's own scenes)."""
        S = ref_mgr.ReferenceManagerSlots
        self.assertEqual(S._type_extensions("usd"), ptk.USD_EXTENSIONS)
        self.assertEqual(S._type_extensions("fbx"), (".fbx",))

        class _Check:
            def __init__(self, on):
                self._on = on

            def isChecked(self):
                return self._on

        menu = type(
            "M", (), {f"chk_include_{t}": _Check(t == "usd") for t in S._INCLUDE_TYPES}
        )()
        slot = S.__new__(S)
        slot.ui = type("U", (), {"header": type("H", (), {"menu": menu})()})()
        self.assertEqual(slot._included_extensions(), set(ptk.USD_EXTENSIONS))
        self.assertFalse(set(ptk.USD_EXTENSIONS) & set(S._INCLUDE_DEFAULTS))

    def test_unlink_and_import_brings_in_an_unreferenced_usd_row(self):
        """A USD row with no reference behind it imports natively (the way in for a
        stage a live read refuses); a native Maya row still says there is nothing to
        unlink."""
        S = ref_mgr.ReferenceManagerSlots
        for path, imports in (("C:/proj/set.usdc", True), ("C:/proj/shot.ma", False)):
            with self.subTest(path=path):
                slot = S.__new__(S)
                slot.ui = MockUI()
                slot.ui.tbl000 = QtWidgets.QTableWidget()
                slot.ui.tbl000.setRowCount(1)
                item = QtWidgets.QTableWidgetItem("row")
                item.setData(MockQt.UserRole, path)
                slot.ui.tbl000.setItem(0, 0, item)
                slot.sb = MockSB()
                slot.sb.message_box = MagicMock()
                slot.controller = MagicMock(current_references=[])
                slot.controller._is_foreign = (
                    ref_mgr.ReferenceManagerController._is_foreign
                )
                slot.controller._bake_source_key = lambda _p: None
                slot._context_row = lambda: 0
                slot._get_row_reference_namespaces = lambda _row: []
                slot._import_paths = MagicMock()

                slot.btn_unlink_import()

                if imports:
                    slot._import_paths.assert_called_once_with([path])
                    slot.sb.message_box.assert_not_called()
                else:
                    slot._import_paths.assert_not_called()
                    slot.sb.message_box.assert_called_once()

    def test_an_import_error_reaches_the_message_box_as_text_not_markup(self):
        """The box renders rich text, and pxr quotes prim paths as ``</...>``:
        unescaped, the part of the error that named the problem vanished (a
        file name's ``&`` too)."""
        import contextlib

        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        S = ref_mgr.ReferenceManagerSlots
        slot = S.__new__(S)
        shown = []
        slot.sb = type(
            "SB", (), {"message_box": lambda _s, text, *b: shown.append(text)}
        )()
        slot.controller = MagicMock()
        slot.controller._is_usd = lambda _p: True
        slot._is_importable = lambda _p: True
        slot._conversion_progress = lambda _text: contextlib.nullcontext()
        with patch.object(
            BlenderSceneImport,
            "import_scene",
            side_effect=RuntimeError(
                "layer </World/Crate> could not be read & skipped"
            ),
        ):
            slot._import_paths(["C:/proj/R&D set.usda"])

        self.assertEqual(len(shown), 1, shown)
        self.assertIn("&lt;/World/Crate&gt;", shown[0])
        self.assertIn("read &amp; skipped", shown[0])
        self.assertIn("<hl>R&amp;D set.usda</hl>", shown[0])

    def test_included_extensions_falls_back_to_defaults_without_a_menu(self):
        """An early refresh (header menu not built yet) must still list this panel's own
        native scenes rather than an empty set."""
        # Use the REAL slots class (bypassing its Qt-heavy __init__) so the fallback
        # under test is the production method, not a mock.
        slot = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slot.ui = type("U", (), {})()  # no header attr -> menu is None -> defaults
        self.assertEqual(
            slot._included_extensions(),
            set(ref_mgr.ReferenceManagerSlots._INCLUDE_DEFAULTS),
        )

    def _prompting_slot(self, has_rig, answer):
        """A real slots object whose probe and message box are stubbed."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        slot = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slot.ui = type("U", (), {})()  # no header attr -> menu is None -> defaults
        slot.prompts = []
        slot.sb = type(
            "SB",
            (),
            {"message_box": lambda _s, text, *b: slot.prompts.append(text) or answer},
        )()
        patcher = patch.object(
            BlenderSceneImport,
            "scene_has_complex_animation",
            classmethod(lambda cls, path: has_rig),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return slot

    def test_resolve_conversion_hands_the_engine_via_and_rig_mode(self):
        """The mirror of blendertk's: ONE dict the engine takes verbatim; a scene
        with no rig logic asks nothing and converts ``auto``."""
        slot = self._prompting_slot(has_rig=False, answer="Yes")
        self.assertEqual(
            slot._resolve_conversion("x.blend"), {"via": "fbx", "rig_mode": "auto"}
        )
        self.assertEqual(slot.prompts, [])
        self.assertFalse(hasattr(slot, "_rig_mode"), "no header Rig combo to consult")

    def test_rig_logic_prompts_transfer_or_bake(self):
        """Yes = transfer the rig, No = bake, Cancel = no conversion. No Raw: both
        Blender exporters sample the evaluated scene, so raw IS bake here."""
        for answer, mode in (("Yes", "rig"), ("No", "bake"), ("Cancel", None)):
            slot = self._prompting_slot(has_rig=True, answer=answer)
            self.assertEqual(slot._resolve_rig_mode("x.blend"), mode, answer)
            self.assertEqual(len(slot.prompts), 1)
            self.assertIn("x.blend", slot.prompts[0])
        slot = self._prompting_slot(has_rig=True, answer="Cancel")
        self.assertIsNone(slot._resolve_conversion("x.blend"))

    def test_foreign_route_defaults_to_fbx_without_a_menu(self):
        """No header menu (early refresh, or a headless caller) must fall back to
        the SAME route the engine defaults to, not the opposite one."""
        slot = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slot.ui = type("U", (), {})()  # no header attr -> menu is None
        self.assertEqual(slot._foreign_route(), "fbx")

    def test_foreign_route_returns_usd_only_when_explicitly_selected(self):
        """USD is opt-in: anything that isn't an explicit USD selection is FBX."""
        slot = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        for text, expected in (
            ("Convert via FBX", "fbx"),
            ("Convert via USD", "usd"),
            ("", "fbx"),  # combo not yet populated
        ):
            combo = type("C", (), {"currentText": lambda self, t=text: t})()
            menu = type("M", (), {"cmb_conversion_route": combo})()
            slot.ui = type("U", (), {"header": type("H", (), {"menu": menu})()})()
            self.assertEqual(slot._foreign_route(), expected, text)

    def test_conversion_route_combo_default_index_is_the_fbx_item(self):
        """The combo's default selection must actually BE the FBX entry.

        uitk persists a combo by INDEX, so the item list is append-only and the
        default moves via ``setCurrentIndex`` rather than by reordering. This
        ties the two together: reordering the items without moving the index
        fails here instead of silently re-defaulting every profile to USD.

        Anchored on the objectName, not just the first ``addItems`` in the file —
        this panel builds several combos, and an unanchored match would silently
        pin whichever one happened to come first. The kwarg ORDER is load-bearing
        too: ``set_attributes`` applies kwargs in order, so ``setCurrentIndex``
        must follow ``addItems`` or it selects into an empty model.
        """
        import ast
        import re

        with open(ref_mgr.__file__, encoding="utf-8") as fh:
            src = fh.read()
        block = re.search(
            # [^\n]* tolerates a trailing comment after setCurrentIndex.
            r"addItems=(\[[^\]]*\]),\s*setCurrentIndex=(\d+),[^\n]*\s*"
            r'setObjectName="cmb_conversion_route"',
            src,
        )
        self.assertIsNotNone(
            block, "route combo: addItems -> setCurrentIndex -> objectName block"
        )
        items = ast.literal_eval(block.group(1))
        self.assertIn("FBX", items[int(block.group(2))])

    def test_workspace_scan_covers_every_native_type(self):
        """The workspace-file cache scans the natively referenceable superset, so toggling
        an Include Type only re-filters — it never has to re-scan disk."""
        self.assertEqual(
            set(ref_mgr.ReferenceManager.SCENE_FILE_TYPES),
            {f"*{e}" for e in ref_mgr.ReferenceManagerSlots.NATIVE_EXTENSIONS},
        )

    @staticmethod
    def _action_recorder():
        """A stand-in for TableActions that records what update_table set per cell."""

        class _ActionRec:
            def __init__(self):
                self.states = {}

            def set(self, row, col, state):
                self.states[(row, col)] = state

            def get(self, row, col):
                return self.states.get((row, col))

        return _ActionRec()

    def test_update_table_foreign_row_toggles_like_a_native_row(self):
        """A Blender (.blend) row carries the SAME reference-toggle AND Open states as a native
        row (its icon bakes + references; Open bakes + opens as a new scene). It stays
        non-selectable so the selection->reference sync can't reference the .blend itself."""
        t = self.controller.ui.tbl000
        t.actions = self._action_recorder()

        self.controller.update_table(["mesh"], ["C:/proj/mesh.blend"])

        self.assertEqual(
            t.actions.get(0, 1),
            "unreferenced",
            "reference col mirrors a native row (no import-only state)",
        )
        self.assertEqual(
            t.actions.get(0, 2),
            "default",
            "open col clickable (bakes + opens) for a .blend",
        )
        self.assertEqual(
            t.actions.get(0, 3), "unavailable", "display col disabled until referenced"
        )
        item = t.item(0, 0)
        self.assertFalse(
            item.flags() & MockQt.ItemIsSelectable,
            "a foreign row must be non-selectable (kept out of the reference sync)",
        )

    def test_update_table_foreign_row_reads_referenced_through_its_bake(self):
        """A foreign row references a cached .ma, so update_table must resolve that
        reference back through the bake sidecar or the row reads as unreferenced."""
        t = self.controller.ui.tbl000
        t.actions = self._action_recorder()

        ref = MagicMock()
        ref.path = "C:/temp/maya_bake_cache_abc.ma"
        with (
            patch.object(
                type(self.controller),
                "current_references",
                new_callable=PropertyMock,
                return_value=[ref],
            ),
            patch.object(
                ref_mgr.ReferenceManagerController,
                "_bake_source_key",
                staticmethod(
                    lambda p: os.path.normcase(os.path.normpath("C:/proj/mesh.blend"))
                ),
            ),
            patch.object(
                ref_mgr.ReferenceManagerController,
                "get_reference_display_mode",
                lambda self, r: "off",
            ),
        ):
            self.controller.update_table(["mesh  (Blender)"], ["C:/proj/mesh.blend"])

        self.assertEqual(
            t.actions.get(0, 1),
            "referenced",
            "the source row must reflect its bake's reference",
        )

    def test_bake_backed_reference_survives_a_selection_change(self):
        """A foreign row is non-selectable, so its reference can never appear in the
        selection — handle_item_selection must not treat that as 'deselected' and remove
        it (that silently un-referenced every foreign row)."""
        ref = MagicMock()
        ref.path = "C:/temp/maya_bake_cache_abc.ma"
        ref.namespace = "mesh"
        removed = []
        with (
            patch.object(
                type(self.controller),
                "current_references",
                new_callable=PropertyMock,
                return_value=[ref],
            ),
            patch.object(
                ref_mgr.ReferenceManagerController,
                "_bake_source_key",
                staticmethod(
                    lambda p: os.path.normcase(os.path.normpath("C:/proj/mesh.blend"))
                ),
            ),
            patch.object(
                ref_mgr.ReferenceManagerController,
                "remove_references",
                lambda self, ns: removed.append(ns),
            ),
            patch.object(
                ref_mgr.ReferenceManagerController,
                "_sync_reference_icons",
                lambda self: None,
            ),
        ):
            self.controller.handle_item_selection()

        self.assertEqual(
            removed, [], "a bake-backed reference must not be auto-removed"
        )

    def test_update_table_native_row_editable_after_reusing_foreign_item(self):
        """update_table reuses items across refreshes: a row that held a non-editable
        foreign (.blend) file, reused for a native scene once the toggle is turned off,
        must be renameable again (not stuck non-editable from its foreign state)."""
        t = self.controller.ui.tbl000

        self.controller.update_table(["mesh  (Blender)"], ["C:/proj/mesh.blend"])
        self.assertFalse(
            t.item(0, 0).flags() & MockQt.ItemIsEditable,
            "foreign row starts non-editable",
        )

        # Same row index now holds a native .ma — the QTableWidgetItem is reused.
        self.controller.update_table(["shot.ma"], ["C:/proj/shot.ma"])
        self.assertTrue(
            t.item(0, 0).flags() & MockQt.ItemIsEditable,
            "a reused item must regain editability for a native scene",
        )

    def test_update_table_tooltip_names_the_file_in_full(self):
        """The FILES cell tooltip carries the untruncated file name — the displayed
        label can hide the suffix/extension (or elide), so hovering must still tell
        the user which file the row is."""
        t = self.controller.ui.tbl000

        # Display label: suffix + extension hidden, external tag appended.
        self.controller.update_table(
            ["hero (OtherProject)"], ["C:/proj/scenes/hero_lod0.ma"]
        )
        self.assertEqual(t.item(0, 0).toolTip(), "hero_lod0.ma")

    def test_update_table_tooltip_refreshes_on_a_reused_item(self):
        """Items are reused across refreshes — a stale tooltip must not survive a
        row now holding a different file."""
        t = self.controller.ui.tbl000

        self.controller.update_table(["a"], ["C:/proj/a.ma"])
        self.controller.update_table(["b"], ["C:/proj/b.mb"])
        self.assertEqual(t.item(0, 0).toolTip(), "b.mb")


class TestDisplayName(unittest.TestCase):
    """_display_name builds the table label from the hide-extension/hide-suffix settings.

    Bug: "Hide Suffix" ran `name.replace(suffix, "")`, so the token came off
    wherever it appeared — a scene named 'ITA_LOCKHANDLE.ma' listed as
    'ITAKHANDLE' with the suffix set to '_LOC'.
    Fixed: 2026-08-25
    """

    def name(self, path, hide_extension=False, hide_suffix=""):
        return ref_mgr._ReferenceManagerInternal._display_name(
            path, hide_extension, hide_suffix
        )

    def test_suffix_is_hidden_only_at_the_end_of_the_stem(self):
        self.assertEqual(
            self.name("C:/proj/ITA_LOCKHANDLE.ma", hide_suffix="_LOC"),
            "ITA_LOCKHANDLE.ma",
        )
        self.assertEqual(
            self.name("C:/proj/ITA_LOCKHANDLE_LOC.ma", hide_suffix="_LOC"),
            "ITA_LOCKHANDLE.ma",
        )

    def test_suffix_hides_with_the_extension_shown(self):
        """The extension is split off first, or the token is no longer the tail."""
        self.assertEqual(
            self.name("C:/proj/hero_LOC.ma", hide_suffix="_LOC"), "hero.ma"
        )
        self.assertEqual(
            self.name("C:/proj/hero_LOC.ma", hide_extension=True, hide_suffix="_LOC"),
            "hero",
        )

    def test_defaults_pass_the_basename_through(self):
        self.assertEqual(self.name("C:/proj/hero_LOC.ma"), "hero_LOC.ma")
        self.assertEqual(self.name("C:/proj/hero.ma", hide_extension=True), "hero")


class TestDeletePrompt(unittest.TestCase):
    """ReferenceManagerController._delete_prompt names the file(s) being deleted.

    A bare count ("Delete 1 file(s)?") gave no way to confirm WHICH file was about
    to be permanently removed, especially with the suffix/extension hidden.
    """

    def prompt(self, paths):
        return ref_mgr.ReferenceManagerController._delete_prompt(paths)

    def test_single_file_is_named_in_full(self):
        msg = self.prompt(["C:/proj/scenes/hero_lod0.ma"])
        self.assertIn("hero_lod0.ma", msg)
        self.assertNotIn("C:/proj", msg, "the prompt names the file, not the path")

    def test_multiple_files_are_listed(self):
        msg = self.prompt(["C:/proj/a.ma", "C:/proj/b.mb"])
        self.assertIn("Delete 2 file(s)?", msg)
        self.assertIn("a.ma", msg)
        self.assertIn("b.mb", msg)

    def test_long_selection_is_capped(self):
        cap = ref_mgr.ReferenceManagerController.DELETE_PROMPT_MAX_NAMES
        paths = [f"C:/proj/file{i}.ma" for i in range(cap + 3)]
        msg = self.prompt(paths)
        self.assertIn(f"Delete {cap + 3} file(s)?", msg)
        self.assertIn("file0.ma", msg)
        self.assertNotIn(f"file{cap}.ma", msg, "names past the cap are folded away")
        self.assertIn("and 3 more", msg)


class TestMatchesNotesFilter(unittest.TestCase):
    """Tests for ReferenceManager._matches_notes_filter.

    Bug: Filter only matched filenames, not notes/comments metadata.
    Files with matching notes (e.g. "Layout, Speedrun") were excluded when
    searching for "*Speedrun*" unless the filename also contained the term.
    Fixed: 2026-03-03
    """

    def test_wildcard_matches_note_segment(self):
        """'*Speedrun*' should match 'Layout, Speedrun' (comma-delimited notes)."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Layout, Speedrun", "*Speedrun*"
            )
        )

    def test_wildcard_matches_full_notes_string(self):
        """'*Layout*' should match 'Layout, Speedrun' via the full string."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Layout, Speedrun", "*Layout*"
            )
        )

    def test_exact_segment_match(self):
        """Exact note segment 'Speedrun' should match without wildcards."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Layout, Speedrun", "Speedrun"
            )
        )

    def test_case_insensitive_by_default(self):
        """Matching should be case-insensitive by default."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Layout, Speedrun", "*speedrun*"
            )
        )

    def test_case_sensitive_when_specified(self):
        """Case-sensitive mode should not match mismatched case."""
        self.assertFalse(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Layout, Speedrun", "*speedrun*", ignore_case=False
            )
        )

    def test_semicolon_delimited_notes(self):
        """Semicolon-delimited notes should also be matched per segment."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Hero; Speedrun", "*Speedrun*"
            )
        )

    def test_multi_pattern_filter(self):
        """Multi-pattern filter 'Layout,Hero' should match notes containing either."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Hero, Speedrun", "Layout,Hero"
            )
        )

    def test_no_match_returns_false(self):
        """Filter that doesn't match any note segment should return False."""
        self.assertFalse(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Layout, Speedrun", "*LookDev*"
            )
        )

    def test_empty_notes_returns_false(self):
        """Empty notes string should return False."""
        self.assertFalse(
            ref_mgr.ReferenceManager._matches_notes_filter("", "*Speedrun*")
        )

    def test_empty_filter_returns_false(self):
        """Empty filter string should return False."""
        self.assertFalse(
            ref_mgr.ReferenceManager._matches_notes_filter("Layout, Speedrun", "")
        )


class TestNotesColumnVisibility(unittest.TestCase):
    """Tests for the Notes (metadata) column show/hide toggle.

    Feature: the Notes column (index 4) is hidden by default and shown only
    when the ``chk_show_notes_column`` header checkbox is checked.
    Added: 2026-06-16
    """

    def _make_slots(self, checked):
        """Build a ReferenceManagerSlots with mocked ui wired for the toggle.

        ``checked`` is the checkbox state, or ``None`` to omit the checkbox
        entirely (exercising the safe-default-hidden path).
        """
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slots.logger = MockLogger()
        ui = MockUI()
        ui.tbl000 = QtWidgets.QTableWidget()
        ui.header = type("Hdr", (), {})()
        if checked is None:
            ui.header.menu = type("Menu", (), {})()  # no chk_show_notes_column
        else:
            chk = type("Chk", (), {"isChecked": lambda self: checked})()
            ui.header.menu = type("Menu", (), {"chk_show_notes_column": chk})()
        slots.ui = ui
        return slots

    def test_column_hidden_when_unchecked(self):
        """Unchecked toggle (the default) hides the Notes column."""
        slots = self._make_slots(checked=False)
        slots._apply_notes_column_visibility()
        self.assertTrue(slots.ui.tbl000.isColumnHidden(4))

    def test_column_shown_when_checked(self):
        """Checked toggle shows the Notes column."""
        slots = self._make_slots(checked=True)
        slots._apply_notes_column_visibility()
        self.assertFalse(slots.ui.tbl000.isColumnHidden(4))

    def test_missing_checkbox_defaults_to_hidden(self):
        """If the checkbox is absent, the column stays hidden (safe default)."""
        slots = self._make_slots(checked=None)
        slots._apply_notes_column_visibility()
        self.assertTrue(slots.ui.tbl000.isColumnHidden(4))


class TestWorkspaceHistory(unittest.TestCase):
    """Tests for per-directory workspace selection persistence.

    Feature: Remember which workspace (cmb000) was last selected for each
    root directory (txt000) and restore it across sessions.
    Added: 2026-03-06
    """

    def setUp(self):
        self.slot = MockSlot()
        self.slot.ui.settings = MockSettings()
        self.slot.ui.txt000 = MockLineEdit("D:\\Projects")
        self.slot.ui.cmb000 = MockComboBox()
        self.slot.ui.tbl000 = QtWidgets.QTableWidget()

        self.controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        self.controller.slot = self.slot
        self.controller.sb = self.slot.sb
        self.controller.ui = self.slot.ui
        self.controller.logger = MockLogger()
        self.controller._workspace_history_max = 50
        self.controller._last_dir_valid = None
        self.controller._updating_directory = False
        self.controller._editing_item = None
        self.controller.last_unlink_time = 0
        self.controller._warned_scene_placeholder_typo = False

        self._cmds_file_patcher = patch.object(
            ref_mgr.cmds, "file", create=True, return_value=""
        )
        self._mock_cmds_file = self._cmds_file_patcher.start()

    def tearDown(self):
        self._cmds_file_patcher.stop()

    # -- _save_workspace_selection / _get_workspace_history -----------------

    def test_save_and_load_workspace_selection(self):
        """Basic round-trip: save a selection, load it back."""
        self.controller._save_workspace_selection("D:\\Projects", "MyProject")
        history = self.controller._get_workspace_history()

        key = os.path.normcase(os.path.normpath("D:\\Projects"))
        self.assertIn(key, history)
        self.assertEqual(history[key], "MyProject")

    def test_save_overwrites_previous_for_same_dir(self):
        """Saving a new workspace for the same root dir replaces the old one."""
        self.controller._save_workspace_selection("D:\\Projects", "OldProject")
        self.controller._save_workspace_selection("D:\\Projects", "NewProject")
        history = self.controller._get_workspace_history()

        key = os.path.normcase(os.path.normpath("D:\\Projects"))
        self.assertEqual(history[key], "NewProject")

    def test_save_different_directories_independent(self):
        """Different root dirs store independent workspace selections."""
        self.controller._save_workspace_selection("D:\\Projects", "ProjectA")
        self.controller._save_workspace_selection("E:\\Work", "ProjectB")
        history = self.controller._get_workspace_history()

        key_d = os.path.normcase(os.path.normpath("D:\\Projects"))
        key_e = os.path.normcase(os.path.normpath("E:\\Work"))
        self.assertEqual(history[key_d], "ProjectA")
        self.assertEqual(history[key_e], "ProjectB")

    def test_save_caps_at_max_entries(self):
        """History is trimmed to _workspace_history_max, evicting oldest."""
        self.controller._workspace_history_max = 5
        for i in range(10):
            self.controller._save_workspace_selection(f"D:\\Dir{i}", f"WS{i}")

        history = self.controller._get_workspace_history()
        self.assertEqual(len(history), 5)

        # Oldest entries (Dir0-Dir4) should be evicted
        key_old = os.path.normcase(os.path.normpath("D:\\Dir0"))
        key_new = os.path.normcase(os.path.normpath("D:\\Dir9"))
        self.assertNotIn(key_old, history)
        self.assertIn(key_new, history)

    def test_empty_history_returns_empty_dict(self):
        """No saved history returns empty dict, not None."""
        history = self.controller._get_workspace_history()
        self.assertIsInstance(history, dict)
        self.assertEqual(len(history), 0)

    # -- _restore_workspace_index -------------------------------------------

    def test_restore_selects_saved_workspace(self):
        """Restore should set the combo box to the saved workspace name."""
        # Save a selection for the current root dir
        self.controller._save_workspace_selection("D:\\Projects", "ProjectB")

        # Populate combo box
        cmb = self.controller.ui.cmb000
        cmb.add(
            [
                ("ProjectA", "D:\\Projects\\ProjectA"),
                ("ProjectB", "D:\\Projects\\ProjectB"),
                ("ProjectC", "D:\\Projects\\ProjectC"),
            ]
        )

        self.controller.ui.txt000.setText("D:\\Projects")
        result = self.controller._restore_workspace_index(cmb)

        self.assertTrue(result)
        self.assertEqual(cmb.currentIndex(), 1)
        self.assertEqual(cmb.itemText(cmb.currentIndex()), "ProjectB")

    def test_restore_returns_false_when_no_history(self):
        """Restore returns False when no history exists for this directory."""
        cmb = self.controller.ui.cmb000
        cmb.add([("ProjectA", "D:\\Projects\\ProjectA")])

        self.controller.ui.txt000.setText("D:\\Projects")
        result = self.controller._restore_workspace_index(cmb)

        self.assertFalse(result)

    def test_restore_returns_false_when_saved_name_gone(self):
        """Restore returns False when the saved workspace no longer exists in combo."""
        self.controller._save_workspace_selection("D:\\Projects", "DeletedProject")

        cmb = self.controller.ui.cmb000
        cmb.add([("ProjectA", "D:\\Projects\\ProjectA")])

        self.controller.ui.txt000.setText("D:\\Projects")
        result = self.controller._restore_workspace_index(cmb)

        self.assertFalse(result)

    def test_restore_returns_false_when_txt000_empty(self):
        """Restore returns False when txt000 is empty."""
        self.controller._save_workspace_selection("D:\\Projects", "ProjectA")
        cmb = self.controller.ui.cmb000
        cmb.add([("ProjectA", "D:\\Projects\\ProjectA")])

        self.controller.ui.txt000.setText("")
        result = self.controller._restore_workspace_index(cmb)

        self.assertFalse(result)

    # -- _update_workspace_combo --------------------------------------------

    def _setup_update_combo(self, workspaces, root_dir="D:\\Projects"):
        """Helper: configure mocks for _update_workspace_combo tests."""
        self.controller.ui.txt000.setText(root_dir)
        # Mock current_working_dir as a plain attribute
        self.controller.__class__.current_working_dir = property(
            lambda s: getattr(s, "_cwd", root_dir),
            lambda s, v: setattr(s, "_cwd", v),
        )
        self.controller._cwd = root_dir
        self.controller.find_available_workspaces = MagicMock(return_value=workspaces)
        self.controller.refresh_file_list = MagicMock()

    def test_update_combo_restores_from_history(self):
        """When no in-memory selection, history should be used."""
        workspaces = [
            ("ProjectA", "D:\\Projects\\ProjectA"),
            ("ProjectB", "D:\\Projects\\ProjectB"),
        ]
        self._setup_update_combo(workspaces)
        self.controller._save_workspace_selection("D:\\Projects", "ProjectB")

        with patch("os.path.isdir", return_value=True):
            self.controller._update_workspace_combo()

        cmb = self.controller.ui.cmb000
        self.assertEqual(cmb.itemText(cmb.currentIndex()), "ProjectB")

    def test_update_combo_falls_back_to_first(self):
        """When no history and no in-memory match, selects first item."""
        workspaces = [
            ("ProjectA", "D:\\Projects\\ProjectA"),
            ("ProjectB", "D:\\Projects\\ProjectB"),
        ]
        self._setup_update_combo(workspaces)

        with patch("os.path.isdir", return_value=True):
            self.controller._update_workspace_combo()

        cmb = self.controller.ui.cmb000
        self.assertEqual(cmb.currentIndex(), 0)
        self.assertEqual(cmb.itemText(0), "ProjectA")

    def test_update_combo_prefers_in_memory_over_history(self):
        """In-memory selection (same path from before clear) wins over history."""
        workspaces = [
            ("ProjectA", "D:\\Projects\\ProjectA"),
            ("ProjectB", "D:\\Projects\\ProjectB"),
            ("ProjectC", "D:\\Projects\\ProjectC"),
        ]
        self._setup_update_combo(workspaces)

        # History says ProjectC
        self.controller._save_workspace_selection("D:\\Projects", "ProjectC")

        # Pre-populate combo with ProjectB selected (simulates in-memory state)
        cmb = self.controller.ui.cmb000
        cmb.add(workspaces)
        cmb.setCurrentIndex(1)  # ProjectB

        with patch("os.path.isdir", return_value=True):
            self.controller._update_workspace_combo()

        # In-memory (ProjectB) should win over history (ProjectC)
        self.assertEqual(cmb.itemText(cmb.currentIndex()), "ProjectB")

    def test_update_combo_empty_workspaces_clears(self):
        """When no workspaces found, combo and table are cleared."""
        self._setup_update_combo([])

        with patch("os.path.isdir", return_value=True):
            self.controller._update_workspace_combo()

        self.assertEqual(self.controller.ui.cmb000.count(), 0)
        self.assertEqual(self.controller.ui.tbl000.rowCount(), 0)

    # -- set_workspace saves history ----------------------------------------

    def test_set_workspace_saves_to_history(self):
        """set_workspace should persist the selection in workspace history."""
        self.controller.ui.txt000.setText("D:\\Projects")
        self.controller.__class__.current_working_dir = property(
            lambda s: getattr(s, "_cwd", ""),
            lambda s, v: setattr(s, "_cwd", v),
        )
        self.controller._cwd = ""  # Different from workspace_path
        self.controller.refresh_file_list = MagicMock()

        with patch("os.path.isdir", return_value=True):
            result = self.controller.set_workspace("D:\\Projects\\MyProject")

        self.assertTrue(result)
        history = self.controller._get_workspace_history()
        key = os.path.normcase(os.path.normpath("D:\\Projects"))
        self.assertEqual(history[key], "MyProject")

    def test_set_workspace_skips_save_for_same_workspace(self):
        """set_workspace should not write history when workspace is unchanged."""
        ws = "D:\\Projects\\MyProject"
        self.controller.ui.txt000.setText("D:\\Projects")
        self.controller.__class__.current_working_dir = property(
            lambda s: getattr(s, "_cwd", ""),
            lambda s, v: setattr(s, "_cwd", v),
        )
        self.controller._cwd = ws  # Already set to this workspace
        self.controller.refresh_file_list = MagicMock()

        with patch("os.path.isdir", return_value=True):
            self.controller.set_workspace(ws)

        history = self.controller._get_workspace_history()
        self.assertEqual(len(history), 0, "Should not save when workspace unchanged")

    # -- End-to-end: save then restore across fresh controller --------------

    def test_end_to_end_persistence(self):
        """Simulate full cycle: select workspace, 'restart', restore selection."""
        # SESSION 1: User selects ProjectC
        self.controller.ui.txt000.setText("D:\\Projects")
        self.controller._save_workspace_selection("D:\\Projects", "ProjectC")

        # Grab the persisted settings store
        settings_store = self.controller.ui.settings

        # SESSION 2: Fresh controller, same settings
        controller2 = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        controller2.slot = self.slot
        controller2.sb = self.slot.sb
        controller2.ui = MagicMock()
        controller2.ui.settings = settings_store  # Same persistence
        controller2.ui.txt000 = MockLineEdit("D:\\Projects")
        controller2.ui.cmb000 = MockComboBox()
        controller2.ui.tbl000 = QtWidgets.QTableWidget()
        controller2.logger = MockLogger()
        controller2._workspace_history_max = 50

        # Populate combo with available workspaces
        controller2.ui.cmb000.add(
            [
                ("ProjectA", "D:\\Projects\\ProjectA"),
                ("ProjectB", "D:\\Projects\\ProjectB"),
                ("ProjectC", "D:\\Projects\\ProjectC"),
            ]
        )

        # Restore should find ProjectC
        result = controller2._restore_workspace_index(controller2.ui.cmb000)
        self.assertTrue(result)
        self.assertEqual(
            controller2.ui.cmb000.itemText(controller2.ui.cmb000.currentIndex()),
            "ProjectC",
        )


class TestUpdateCurrentDirNormalization(unittest.TestCase):
    """``update_current_dir`` must treat a normalization-only difference as
    *unchanged*.

    Regression: ``current_working_dir`` comes from Maya's
    ``cmds.workspace(q=True, rd=True)`` (forward slashes + trailing separator).
    Comparing it raw against the ``os.path.normpath``-ed text input read as
    "changed" on every startup, firing a redundant ``_update_workspace_combo()``
    on top of the one from ``cmb000_init`` — which double-logged the
    "No workspaces in ..." warning when the folder had none.
    Added: 2026-07-10
    """

    def setUp(self):
        self.slot = MockSlot()
        self.slot.ui.settings = MockSettings()
        self.slot.ui.txt000 = MockLineEdit("")
        self.slot.ui.cmb000 = MockComboBox()
        self.slot.ui.tbl000 = QtWidgets.QTableWidget()

        self.controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        self.controller.slot = self.slot
        self.controller.sb = self.slot.sb
        self.controller.ui = self.slot.ui
        self.controller.logger = MockLogger()
        self.controller._last_dir_valid = None
        self.controller._updating_directory = False
        self.controller._recursive_search = True  # read by the debug log line
        # Spy on the populate + footer so we can assert exact call counts.
        self.controller._update_workspace_combo = MagicMock()
        self.controller._update_workspace_footer = MagicMock()

    def _run(self, txt_text, current_working_dir):
        """Drive update_current_dir with txt000=txt_text and a fixed cwd."""
        self.controller.ui.txt000.setText(txt_text)
        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
            ) as cwd,
            patch("os.path.isdir", return_value=True),
        ):
            cwd.return_value = current_working_dir
            self.controller.update_current_dir()

    def test_trailing_separator_is_not_a_change(self):
        """A trailing separator alone (Maya's rd path) must not repopulate.

        Cross-platform: ``os.path.normpath`` strips the trailing separator on
        every OS, so this holds under both nt and posix path semantics.
        """
        self._run("O:/Projects/shot_010/", "O:/Projects/shot_010")
        self.controller._update_workspace_combo.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows path semantics (case + \\)")
    def test_maya_forwardslash_vs_windows_backslash_not_changed(self):
        """The real regression: Maya's forward-slash/trailing-sep ``rd`` path
        vs the ``normpath``-ed backslash text must read as *unchanged*.

        Windows-only by nature — the bug exists because Maya returns forward
        slashes while the OS is case-insensitive and backslash-separated, which
        ``os.path.normcase(os.path.normpath(...))`` reconciles. On POSIX these
        would be genuinely different paths.
        """
        # cwd from cmds.workspace(rd=True); txt000 as the user/os would spell it
        self._run("O:\\Projects\\shot_010", "O:/Projects/Shot_010/")
        self.controller._update_workspace_combo.assert_not_called()

    def test_genuinely_different_dir_triggers_repopulate(self):
        """A real directory change must still repopulate the combo (guard).

        Cross-platform: different basenames differ under any path semantics.
        """
        self._run("O:/Projects/shot_020", "O:/Projects/shot_010")
        self.controller._update_workspace_combo.assert_called_once()


class TestOpenSceneClearsModifiedFlag(unittest.TestCase):
    """open_scene must not leave Maya's load-time 'modified' flag set.

    Regression: opening a reference-bearing scene via the Open icon leaves
    ``cmds.file(q=True, modified=True)`` True (reference edits are applied during
    load), so an immediate close/reference toggle falsely prompted "unsaved
    changes — close anyway?" even though the user made no edits.
    """

    def _make_controller(self):
        slot = MockSlot()
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        controller.slot = slot
        controller.sb = slot.sb
        controller.ui = slot.ui
        controller.logger = MockLogger()
        return controller

    def test_open_scene_resets_modified_flag(self):
        controller = self._make_controller()
        with (
            patch.object(ref_mgr.os.path, "exists", return_value=True),
            patch.object(ref_mgr.cmds, "file", create=True) as mock_file,
        ):
            result = controller.open_scene("/proj/scenes/shot.ma", set_workspace=False)

        self.assertTrue(result)
        # The open call, then an explicit clear of the load-time dirty flag.
        mock_file.assert_any_call("/proj/scenes/shot.ma", open=True, force=True)
        mock_file.assert_any_call(modified=False)


class TestForeignScratch(unittest.TestCase):
    """Foreign rows open from ``<temp>/mtk_opened_<hash>/<stem>_<ext>.ma`` and the
    untouched scratch is discarded on close (mirror of blendertk's tests)."""

    def test_scratch_name_carries_the_source_type_and_is_per_source(self):
        a = ref_mgr.ReferenceManagerSlots._foreign_scratch_path(
            "/projA/scenes/shot.blend"
        )
        b = ref_mgr.ReferenceManagerSlots._foreign_scratch_path(
            "/projB/scenes/shot.blend"
        )
        self.assertEqual(os.path.basename(a), "shot_blend.ma")
        self.assertTrue(os.path.basename(os.path.dirname(a)).startswith("mtk_opened_"))
        import tempfile

        self.assertEqual(
            os.path.normcase(os.path.dirname(os.path.dirname(a))),
            os.path.normcase(tempfile.gettempdir()),
        )
        self.assertNotEqual(os.path.dirname(a), os.path.dirname(b))
        self.assertEqual(
            ref_mgr.ReferenceManagerSlots._foreign_scratch_path(
                "/projA/scenes/shot.blend"
            ),
            a,
        )

    def test_store_is_one_process_wide_scratch_twins(self):
        # The slot delegates naming + untouched-vs-saved discard to ptk.ScratchTwins
        # (pinned in pythontk's own tests); here only the wiring is checked.
        twins = ref_mgr._scratch_twins()
        self.assertIs(twins, ref_mgr._scratch_twins())
        self.assertIsInstance(twins, ref_mgr.ptk.ScratchTwins)
        self.assertEqual(twins.extension, ".ma")


class _FakeRef:
    """Minimal stand-in for a scene reference (``.path`` / ``.namespace``)."""

    def __init__(self, path, namespace):
        self.path = path
        self.namespace = namespace


try:
    from qtpy import QtWidgets as _RealQtWidgets, QtCore as _RealQtCore

    _HAVE_QT = True
except Exception:  # pragma: no cover - Qt not installed
    _HAVE_QT = False


@unittest.skipUnless(_HAVE_QT, "needs a real Qt binding")
class TestToggleReferenceOnCurrentSceneIsOneClick(unittest.TestCase):
    """Referencing the currently-open scene must take ONE click, not two.

    Regression (real Qt, real ``_toggle_reference_at_row`` + real
    ``handle_item_selection``; only the Maya scene ops are stubbed):
    ``_toggle_reference_at_row`` closed the current scene then added the
    reference, but the *unblocked* ``item.setSelected(True)`` fired
    ``itemSelectionChanged`` -> ``handle_item_selection`` (the selection->reference
    sync). The just-closed row's name item is still flagged non-selectable, and
    ``setSelected`` on a non-selectable item fires the signal yet leaves the item
    UN-selected — so the handler saw the freshly-added reference as a stale
    selection diff and removed it. Net effect: the first click only closed the
    scene; a second click was needed to reference it. Blocking the table's signals
    around the programmatic ``setSelected`` fixes it.
    """

    @classmethod
    def setUpClass(cls):
        # Run headless even under the mayapy runner / CI (no-op if a QApplication
        # already exists, e.g. a GUI Maya session or an offscreen already set).
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = _RealQtWidgets.QApplication.instance() or _RealQtWidgets.QApplication(
            []
        )

    def _build(self, file_path, refs):
        from uitk.widgets.tableWidget import TableWidget

        class _StubController(ref_mgr.ReferenceManagerController):
            # Real: handle_item_selection, _sync_reference_icons, _bake_source_key,
            # _is_foreign. Stubbed: the Maya scene ops (mutate an in-memory list).
            def __init__(self, ui, sb, ref_list):
                self.ui = ui
                self.sb = sb
                self.logger = MockLogger()
                self._refs = ref_list

            @property
            def current_references(self):
                return list(self._refs)

            def add_reference(self, namespace, fp):
                self._refs.append(_FakeRef(fp, namespace))
                return True

            def remove_references(self, namespaces=None):
                if namespaces is None:
                    self._refs.clear()
                    return
                ns = (
                    namespaces
                    if isinstance(namespaces, (list, tuple, set))
                    else [namespaces]
                )
                self._refs[:] = [r for r in self._refs if r.namespace not in ns]

            def new_scene(self):
                return True

            def refresh_file_list(self, invalidate=False):
                pass

            def get_reference_display_mode(self, ref):
                return "off"

        sb = type(
            "SB",
            (),
            {
                "QtWidgets": _RealQtWidgets,
                "QtCore": _RealQtCore,
                "message_box": lambda self, *a, **k: None,
            },
        )()
        table = TableWidget()
        table.setColumnCount(5)
        table.setRowCount(1)
        table.actions.add(
            1,
            states={
                "referenced": {"icon": "link"},
                "unreferenced": {"icon": "link"},
            },
        )
        table.actions.add(
            3,
            states={
                "off": {"icon": "grid"},
                "unavailable": {"icon": "grid"},
                "reference": {"icon": "lock"},
                "template": {"icon": "grid"},
            },
        )
        ui = type("UI", (), {})()
        ui.tbl000 = table

        controller = _StubController(ui, sb, refs)

        slot = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slot.ui = ui
        slot.sb = sb
        slot.controller = controller
        slot.logger = MockLogger()

        # The clicked row is the OPEN scene: enabled but non-selectable (current-scene styling).
        item = _RealQtWidgets.QTableWidgetItem("asset_a")
        item.setData(_RealQtCore.Qt.UserRole, file_path)
        item.setFlags(
            (item.flags() | _RealQtCore.Qt.ItemIsEnabled)
            & ~_RealQtCore.Qt.ItemIsSelectable
        )
        table.setItem(0, 0, item)

        # The selection->reference sync that clobbered the reference.
        table.itemSelectionChanged.connect(controller.handle_item_selection)
        return slot, controller, table

    def test_referencing_open_scene_sticks_after_one_click(self):
        file_path = os.path.normpath("/proj/scenes/asset_a.ma")
        refs = []
        slot, controller, table = self._build(file_path, refs)

        def fake_file(*args, **kwargs):
            if kwargs.get("sceneName"):
                return file_path  # the clicked file IS the current scene
            if kwargs.get("modified"):
                return False  # no unsaved changes -> no discard prompt
            return ""

        try:
            with patch.object(ref_mgr.cmds, "file", create=True, side_effect=fake_file):
                slot._toggle_reference_at_row(0, 1)

            # One click: the scene was closed AND the reference persists.
            self.assertEqual(
                [r.path for r in controller._refs],
                [file_path],
                "reference was clobbered by handle_item_selection -> needs a 2nd click",
            )
            self.assertEqual(table.actions.get(0, 1), "referenced")
        finally:
            table.deleteLater()


@unittest.skipUnless(_HAVE_QT, "needs a real Qt binding")
class TestFooterActions(unittest.TestCase):
    """The footer's action row, built by the REAL ``_setup_footer_actions`` on a
    real uitk ``Footer`` (only the controller is stubbed).

    Left to right: Un-Reference All, then Save To Workspace -- the primary
    action, outermost, carrying the naming option box. A second slots instance
    on the same persisted footer (a panel reload) must not build the row again.
    Added: 2026-09-23.
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = _RealQtWidgets.QApplication.instance() or _RealQtWidgets.QApplication(
            []
        )

    class _Controller:
        def _save_scene_preview(self):
            return "Save To Workspace"

        def _wire_structure_tooltip(self, menu):
            pass

    def _slots(self, footer):
        from qtpy import QtGui
        from uitk.widgets.mixins.tooltip_mixin import TooltipNamespace

        sb = type(
            "SB",
            (),
            {"QtWidgets": _RealQtWidgets, "QtCore": _RealQtCore, "QtGui": QtGui},
        )()
        sb.tooltip = TooltipNamespace(sb)
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slots.sb = sb
        slots.ui = type("UI", (), {"footer": footer})()
        slots.logger = MockLogger()
        slots.controller = self._Controller()
        return slots

    def _footer(self):
        from uitk.widgets.footer import Footer

        footer = Footer()
        self.addCleanup(footer.deleteLater)
        return footer

    def test_unreference_all_sits_left_of_save(self):
        footer = self._footer()
        self._slots(footer)._setup_footer_actions()

        row = footer.main_layout
        unref = row.indexOf(footer._rm_unref_btn)
        save = row.indexOf(footer._rm_save_btn)
        self.assertGreaterEqual(unref, 0)
        self.assertLess(unref, save)

    def test_the_naming_fields_are_not_in_the_footer(self):
        """They are panel-wide (Rename, Delete and the filters read them too), so
        they live in the header menu, not on a Save option box."""
        footer = self._footer()
        self._slots(footer)._setup_footer_actions()

        # Seated directly in the row: no option-box container wraps it. (Not
        # ``option_box is None``: uitk patches a lazy option_box onto QPushButton.)
        self.assertGreaterEqual(footer.main_layout.indexOf(footer._rm_save_btn), 0)
        for name in ("cmb_case_style", "txt_suffix", "txt_subfolder_structure"):
            self.assertIsNone(footer.findChild(_RealQtWidgets.QWidget, name), name)

    def test_a_reload_does_not_rebuild_the_row(self):
        footer = self._footer()
        first = self._slots(footer)
        first._setup_footer_actions()
        save_btn = footer._rm_save_btn
        second = self._slots(footer)  # a reload: new instance, same footer
        second._setup_footer_actions()

        labels = sorted(
            b.text()
            for b in footer.findChildren(_RealQtWidgets.QPushButton)
            if b.text() in ("Un-Reference All", "Save To Workspace")
        )
        self.assertEqual(labels, ["Save To Workspace", "Un-Reference All"])
        self.assertIs(footer._rm_save_btn, save_btn)


class TestReferenceManagerHeaderInit(unittest.TestCase):
    """header_init opts this gesture-scoped panel into tap-to-pin.

    Explicit per-tool assignment (uitk.widgets.header.Header.pin_on_tap),
    not a dependency on the process-wide UiHandler.pin_on_tap preference: a
    user who leaves that preference off must still get tap-to-pin on THIS
    panel, since the panel opted in deliberately (mirrors the "pin" button
    itself, which is likewise forced via config_buttons regardless of the
    generic mayatk-tool-panels-are-sticky default).
    Added: 2026-08-28
    """

    def test_header_init_enables_pin_on_tap(self):
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slots.controller = MagicMock()
        widget = MagicMock()
        widget.is_initialized = True  # skip the one-time menu build
        slots.header_init(widget)
        self.assertTrue(widget.pin_on_tap)
        widget.config_buttons.assert_called_with("refresh", "menu", "collapse", "pin")


class TestNamingLivesInTheHeaderMenu(unittest.TestCase):
    """The naming fields (case / suffix / folder structure) are panel-wide: Save
    and Rename apply them, Delete and the list filters read them. So header_init
    builds them as a Naming section above the filters that match against them,
    not on a Save option box, and editing one re-filters the list.
    Added: 2026-09-23
    """

    NAMING = ("cmb_case_style", "txt_suffix", "txt_subfolder_structure")

    def test_naming_section_precedes_the_filters_that_read_it(self):
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slots.controller = MagicMock()
        slots.sb = MagicMock()
        widget = MagicMock()
        widget.is_initialized = False
        slots.header_init(widget)

        names = [c.kwargs.get("setObjectName") for c in widget.menu.add.call_args_list]
        order = [
            names.index(n)
            for n in (*self.NAMING, "chk_filter_suffix", "chk_filter_folder_structure")
        ]
        self.assertEqual(order, sorted(order))
        slots.controller._wire_structure_tooltip.assert_called_once_with(widget.menu)

    def test_a_reload_rebinds_the_structure_preview_to_the_new_controller(self):
        """The header outlives a slots reload; a preview left bound to the dead
        controller would resolve against ITS stale workspace state."""
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slots.controller = MagicMock()
        widget = MagicMock()
        widget.is_initialized = True  # a reload: the menu is already built
        slots.header_init(widget)
        slots.controller._wire_structure_tooltip.assert_called_once_with(widget.menu)
        widget.menu.add.assert_not_called()

    def test_the_controller_reads_them_from_the_header_menu(self):
        slot = MockSlot()
        menu = MagicMock()
        menu.cmb_case_style.currentText.return_value = "pascal"
        menu.txt_suffix = MockLineEdit(" _v01 ")
        menu.txt_subfolder_structure = MockLineEdit("{scenes}/{name}")
        slot.ui.header = type("H", (), {"menu": menu})()
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        controller.slot = slot
        self.assertEqual(
            controller._naming_options(), ("pascal", "_v01", "{scenes}/{name}")
        )

    def _slots_with_filters(self, **checked):
        def chk(on):
            box = MagicMock()
            box.isChecked.return_value = on
            return box

        menu = type(
            "M",
            (),
            {
                name: chk(checked.get(name, False))
                for name in (
                    "chk_hide_suffix",
                    "chk_filter_suffix",
                    "chk_filter_folder_structure",
                )
            },
        )()
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slots.ui = MockUI()
        slots.ui.header = type("H", (), {"menu": menu})()
        slots.controller = MagicMock()
        return slots

    def test_editing_a_field_refilters_when_a_dependent_option_is_on(self):
        slots = self._slots_with_filters(chk_filter_folder_structure=True)
        slots.txt_subfolder_structure("{scenes}/{name}")
        slots.txt_suffix("_v02")
        self.assertEqual(slots.controller.refresh_file_list.call_count, 2)

    def test_editing_a_field_leaves_the_list_alone_otherwise(self):
        slots = self._slots_with_filters()
        slots.txt_subfolder_structure("{scenes}/{name}")
        slots.txt_suffix("_v02")
        slots.controller.refresh_file_list.assert_not_called()


class TestFolderStructurePreview(unittest.TestCase):
    """The Folder Structure field's live tooltip (``_folder_structure_preview``).

    Regression guard: the preview + its ``_wire_structure_tooltip`` binder live on
    ``ReferenceManagerController`` (which owns the UI-state reads via ``self.slot``),
    NOT on ``ReferenceManagerSlots`` — so ``header_init`` must route through
    ``self.controller``. A ``self._wire_structure_tooltip`` call from the slots would
    ``AttributeError`` on every panel open. These tests also cover the HTML-escaping
    of the ``<scene name>`` sentinel (else Qt's rich-text parser eats it as a tag).
    """

    def _make_controller(self, pattern="{scenes}/{name}", suffix="_v01", case="None"):
        slot = MockSlot()
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        controller.slot = slot
        controller.sb = slot.sb
        controller.logger = MockLogger()

        menu = MagicMock()
        menu.txt_subfolder_structure = MockLineEdit(pattern)
        menu.txt_suffix = MockLineEdit(suffix)
        menu.cmb_case_style = MagicMock()
        menu.cmb_case_style.currentText.return_value = case
        # The naming fields live in the header menu (controller._naming_menu).
        slot.ui.header = type("H", (), {"menu": menu})()
        return controller

    def test_wiring_and_preview_live_on_controller_not_slots(self):
        # header_init (on Slots) reaches these via self.controller; guard that split.
        self.assertTrue(
            hasattr(ref_mgr.ReferenceManagerController, "_folder_structure_preview")
        )
        self.assertTrue(
            hasattr(ref_mgr.ReferenceManagerController, "_wire_structure_tooltip")
        )
        self.assertFalse(
            hasattr(ref_mgr.ReferenceManagerSlots, "_wire_structure_tooltip")
        )

    def test_preview_resolves_tokens_against_live_context(self):
        controller = self._make_controller(pattern="{scenes}/{name}")
        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value="C:/proj/MyGame",
            ),
            patch.object(ref_mgr.cmds, "workspace", create=True, return_value="scenes"),
            patch.object(ref_mgr.cmds, "file", create=True, return_value=""),
        ):
            html = controller._folder_structure_preview()

        # Tokens present, {scenes} resolved, {workspace} basename shown.
        self.assertIn("{scenes}", html)
        self.assertIn("{name}", html)
        self.assertIn("scenes", html)
        # No open scene -> the "<scene name>" sentinel, HTML-escaped (not eaten as a tag).
        self.assertIn("&lt;scene name&gt;", html)
        self.assertNotIn("<scene name>", html)
        # The resolved absolute save dir is shown.
        self.assertIn("MyGame", html)
        # Instruction is NOT lost: the field's purpose + every key's meaning render,
        # and all supported keys appear even though the pattern uses only two.
        self.assertIn("Save To Workspace", html)  # purpose (body)
        self.assertIn("workspace scenes folder", html)  # {scenes} meaning
        self.assertIn("excludes the suffix", html)  # {name} meaning
        self.assertIn("{workspace}", html)  # available key, unused in pattern
        self.assertIn("{suffix}", html)  # available key, unused in pattern

    def test_preview_warns_on_scene_typo(self):
        controller = self._make_controller(pattern="{scene}/x")
        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value="C:/proj/MyGame",
            ),
            patch.object(ref_mgr.cmds, "workspace", create=True, return_value="scenes"),
            patch.object(ref_mgr.cmds, "file", create=True, return_value=""),
        ):
            html = controller._folder_structure_preview()

        # {scene} is corrected locally for resolution AND surfaced as a typo note.
        self.assertIn("did you mean", html)
        self.assertIn("{scenes}", html)


class TestSaveTargetAndFooterActions(unittest.TestCase):
    """The footer Save button's shared path computation + the new footer/combo actions.

    ``_resolve_save_target`` is the single computation behind ``save_scene`` AND the
    Save button's live tooltip (``_save_scene_preview``), so the preview can never
    show a path the save wouldn't write. ``set_maya_project`` commits the browsed
    workspace to Maya's project (the explicit counterpart of open_scene's automatic
    set); ``btn_copy_path`` puts the right-clicked row's path on the clipboard.
    Added: 2026-09-23 (Save To Workspace moved to the footer).
    """

    def _make_controller(self, pattern="{scenes}/{name}", suffix="_v01", case="None"):
        slot = MockSlot()
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        controller.slot = slot
        controller.sb = slot.sb
        controller.ui = slot.ui
        controller.logger = MockLogger()

        menu = MagicMock()
        menu.txt_subfolder_structure = MockLineEdit(pattern)
        menu.txt_suffix = MockLineEdit(suffix)
        menu.cmb_case_style = MagicMock()
        menu.cmb_case_style.currentText.return_value = case
        slot.ui.header = type("H", (), {"menu": menu})()
        return controller

    def test_resolve_save_target_joins_workspace_pattern_and_suffixed_name(self):
        controller = self._make_controller()
        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value=os.path.normpath(tempfile.gettempdir()),
            ),
            patch.object(ref_mgr.cmds, "workspace", create=True, return_value="scenes"),
        ):
            path = controller._resolve_save_target(
                "Env", "None", "_v01", "{scenes}/{name}"
            )
        ws = os.path.normpath(tempfile.gettempdir())
        self.assertEqual(path, os.path.join(ws, "scenes", "Env", "Env_v01.ma"))

    def test_resolve_save_target_refuses_an_invalid_workspace(self):
        controller = self._make_controller()
        with patch.object(
            ref_mgr.ReferenceManagerController,
            "current_working_dir",
            new_callable=PropertyMock,
            return_value="Z:/no/such/dir",
        ):
            with self.assertRaises(ValueError):
                controller._resolve_save_target("Env", "None", "", "{scenes}")

    def test_save_preview_shows_the_exact_target_path(self):
        controller = self._make_controller()
        ws = os.path.normpath(tempfile.gettempdir())
        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value=ws,
            ),
            patch.object(ref_mgr.cmds, "workspace", create=True, return_value="scenes"),
            patch.object(ref_mgr.cmds, "file", create=True, return_value=""),
        ):
            preview = controller._save_scene_preview()
            expected = controller._resolve_save_target(
                "<scene name>", "None", "_v01", "{scenes}/{name}"
            )
        # The preview names the button's purpose and carries the resolved path,
        # HTML-escaped (the "<scene name>" sentinel must not be eaten as a tag).
        self.assertIn("Save To Workspace", preview)
        self.assertIn(html_escape(expected), preview)

    def test_save_preview_surfaces_the_scene_typo_note(self):
        controller = self._make_controller(pattern="{scene}/x")
        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value=os.path.normpath(tempfile.gettempdir()),
            ),
            patch.object(ref_mgr.cmds, "workspace", create=True, return_value="scenes"),
            patch.object(ref_mgr.cmds, "file", create=True, return_value=""),
        ):
            preview = controller._save_scene_preview()
        self.assertIn("did you mean", preview)

    def test_default_save_name_keeps_a_dotted_scene_name(self):
        # Regression: the old ``split(".")[0]`` prepopulated "hero" for
        # "hero.rig.ma" — only the extension may come off (blendertk parity).
        controller = self._make_controller(suffix="")
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            return_value="C:/proj/scenes/hero.rig.ma",
        ):
            name = controller._default_save_name("None", "")
        self.assertEqual(name, "hero.rig")

    def test_a_doubled_suffix_scene_previews_the_path_its_save_writes(self):
        """Save strips the suffix from whatever its prompt returns, so the prefill
        must already be a fixed point of that strip. Regression: a scene the old
        Rename doubled (``villain_v01_v01.ma``) prefilled ``villain_v01``, which
        the tooltip previewed as ``scenes/villain_v01/villain_v01_v01.ma`` while
        accepting the prompt saved ``scenes/villain/villain_v01.ma``."""
        controller = self._make_controller()  # {scenes}/{name}, suffix _v01
        controller.refresh_file_list = lambda invalidate=False: None
        controller.sb.input_dialog = lambda title, label, default: default
        ws = os.path.normpath(tempfile.gettempdir())
        saved = []

        def _file(*args, **kwargs):
            if kwargs.get("q") or kwargs.get("query"):
                return "C:/proj/scenes/villain/villain_v01_v01.ma"
            if kwargs.get("rename"):
                saved.append(kwargs["rename"])

        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value=ws,
            ),
            patch.object(ref_mgr.cmds, "workspace", create=True, return_value="scenes"),
            patch.object(ref_mgr.cmds, "file", create=True, side_effect=_file),
            patch.object(ref_mgr.os.path, "exists", side_effect=lambda p: p == ws),
            patch.object(ref_mgr.os, "makedirs"),  # nothing touches disk
        ):
            preview = controller._save_scene_preview()
            controller.save_scene()
        self.assertEqual(
            [os.path.relpath(p, ws) for p in saved],
            [os.path.join("scenes", "villain", "villain_v01.ma")],
        )
        self.assertIn(html_escape(saved[0]), preview)

    def test_set_maya_project_commits_the_browsed_workspace(self):
        controller = self._make_controller()
        controller.ui.footer = None
        ws = os.path.normpath(tempfile.gettempdir())
        calls = []

        def _workspace(*args, **kwargs):
            if kwargs.get("q"):
                return "C:/somewhere/else"
            calls.append((args, kwargs))
            return None

        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value=ws,
            ),
            patch.object(
                ref_mgr.cmds, "workspace", create=True, side_effect=_workspace
            ),
        ):
            result = controller.set_maya_project()
        self.assertTrue(result)
        self.assertEqual(calls, [((ws,), {"openWorkspace": True})])

    def test_set_maya_project_refuses_an_invalid_workspace(self):
        controller = self._make_controller()
        boxes = []
        controller.sb.message_box = lambda msg, *b: boxes.append(msg)
        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value="Z:/no/such/dir",
            ),
            patch.object(ref_mgr.cmds, "workspace", create=True) as ws_cmd,
        ):
            result = controller.set_maya_project()
        self.assertFalse(result)
        self.assertEqual(ws_cmd.call_count, 0)
        self.assertTrue(boxes)

    def test_btn_copy_path_puts_the_row_path_on_the_clipboard(self):
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slot = MockSlot()
        slots.sb = slot.sb
        slots.ui = slot.ui
        slots.logger = MockLogger()
        slots.controller = MagicMock()
        slots.controller._context_menu_row = 0

        table = slots.ui.tbl000
        table.setRowCount(1)
        item = QtWidgets.QTableWidgetItem("EnvA")
        item.setData(QtCore.Qt.UserRole, "C:/proj/scenes/EnvA_v01.ma")
        table.setItem(0, 0, item)

        copied = []
        clipboard = type(
            "Clipboard", (), {"setText": staticmethod(lambda t: copied.append(t))}
        )
        with patch.object(
            QtWidgets.QApplication,
            "clipboard",
            create=True,
            new=staticmethod(lambda: clipboard),
        ):
            slots.btn_copy_path()
        self.assertEqual(copied, [os.path.normpath("C:/proj/scenes/EnvA_v01.ma")])

    def test_btn_copy_path_without_a_row_reports_instead_of_raising(self):
        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slot = MockSlot()
        slots.sb = slot.sb
        slots.ui = slot.ui
        slots.logger = MockLogger()
        slots.controller = MagicMock()
        slots.controller._context_menu_row = None
        boxes = []
        slots.sb.message_box = lambda msg, *b: boxes.append(msg)
        slots.btn_copy_path()
        self.assertTrue(boxes)


class TestNamingConventionsNeverDoubleTheSuffix(unittest.TestCase):
    """Rename and Save append the configured suffix, so the name they are handed
    must not carry it already.

    Regression: Rename prefilled the dialog with the file's full stem -- suffix
    included -- and ``_format_name`` then appended the suffix again, so keeping
    the prefilled suffix while editing (``hero_v01`` -> ``villain_v01``) wrote
    ``villain_v01_v01.ma``, and a ``{name}`` per-scene folder took the suffix
    too. Save's prefill already stripped it, but a suffix TYPED into Save's
    dialog doubled the same way. Added: 2026-09-23.
    """

    SUFFIX = "_v01"

    def _make_controller(self, structure="{scenes}"):
        slot = MockSlot()
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        controller.slot = slot
        controller.sb = slot.sb
        controller.ui = slot.ui
        controller.logger = MockLogger()
        controller.refresh_file_list = lambda invalidate=False: None
        menu = MagicMock()
        menu.txt_subfolder_structure = MockLineEdit(structure)
        menu.txt_suffix = MockLineEdit(self.SUFFIX)
        menu.cmb_case_style = MagicMock()
        menu.cmb_case_style.currentText.return_value = "None"
        slot.ui.header = type("H", (), {"menu": menu})()
        self.boxes = []
        controller.sb.message_box = lambda msg, *b: self.boxes.append(msg)
        return controller

    def _rename(self, answer, structure="{scenes}", filename="hero_v01.ma"):
        """Rename *filename* through the dialog answering *answer*; returns
        (prefill, the ``_rename_scene_file`` call or None)."""
        controller = self._make_controller(structure)
        old = os.path.normpath(f"C:/proj/scenes/hero/{filename}")
        table = controller.ui.tbl000
        table.setRowCount(1)
        item = QtWidgets.QTableWidgetItem("hero")
        item.setData(QtCore.Qt.UserRole, old)
        table.setItem(0, 0, item)
        controller._context_menu_row = 0

        seen = {}

        def _dialog(title, label, default):
            seen["prefill"] = default
            return answer

        controller.sb.input_dialog = _dialog
        calls = []
        controller._rename_scene_file = lambda o, n, folder=None: (
            calls.append((o, n, folder)) or n
        )
        with patch.object(ref_mgr.os.path, "exists", side_effect=lambda p: p == old):
            controller.rename_scene()
        return seen.get("prefill"), (calls[0] if calls else None)

    def test_the_rename_prefill_leaves_out_the_suffix(self):
        prefill, _call = self._rename(answer=None)
        self.assertEqual(prefill, "hero")

    def test_typing_the_suffix_onto_an_unsuffixed_file_adds_it(self):
        # The answer differs from the prefill even though it strips back to it:
        # "unchanged" is judged on what the user typed, not on the stripped name.
        _prefill, call = self._rename(answer="hero_v01", filename="hero.ma")
        self.assertIsNotNone(call, "a typed suffix was read as an unchanged name")
        self.assertEqual(os.path.basename(call[1]), "hero_v01.ma")

    def test_a_rename_that_keeps_the_suffix_writes_it_once(self):
        _prefill, call = self._rename(answer="villain_v01")
        self.assertEqual(os.path.basename(call[1]), "villain_v01.ma")

    def test_a_rename_without_the_suffix_gains_it(self):
        _prefill, call = self._rename(answer="villain")
        self.assertEqual(os.path.basename(call[1]), "villain_v01.ma")

    def test_the_per_scene_folder_takes_the_name_without_the_suffix(self):
        _prefill, call = self._rename(answer="villain_v01", structure="{scenes}/{name}")
        self.assertEqual(call[2], "villain")

    def test_an_unchanged_answer_renames_nothing(self):
        _prefill, call = self._rename(answer="hero")
        self.assertIsNone(call)
        self.assertEqual(self.boxes, [])  # no "target exists" for a no-op

    def test_a_suffix_typed_into_save_is_written_once(self):
        # Both the file and the {name} folder: the folder resolves from the
        # typed name, so it took the suffix too (scenes/villain_v01/).
        controller = self._make_controller(structure="{scenes}/{name}")
        controller.sb.input_dialog = lambda title, label, default: "villain_v01"
        ws = os.path.normpath(tempfile.gettempdir())
        renamed = []

        def _file(*args, **kwargs):
            if kwargs.get("q") or kwargs.get("query"):
                return ""
            if kwargs.get("rename"):
                renamed.append(kwargs["rename"])

        with (
            patch.object(
                ref_mgr.ReferenceManagerController,
                "current_working_dir",
                new_callable=PropertyMock,
                return_value=ws,
            ),
            patch.object(ref_mgr.cmds, "workspace", create=True, return_value="scenes"),
            patch.object(ref_mgr.cmds, "file", create=True, side_effect=_file),
            patch.object(ref_mgr.os.path, "exists", side_effect=lambda p: p == ws),
            patch.object(ref_mgr.os, "makedirs"),  # nothing touches disk
        ):
            controller.save_scene()
        self.assertEqual(
            [os.path.relpath(p, ws) for p in renamed],
            [os.path.join("scenes", "villain", "villain_v01.ma")],
        )


class _OnDiskRename:
    """Fixture for renames against REAL files: a scoped temp workspace ``proj``
    holding its ``scenes`` folder, and a controller browsing it, wired for Rename
    under ``{scenes}/{name}`` with suffix ``_v01`` (only Qt/ui is stubbed)."""

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_rename_disk_test", policy="scoped")
        self.workspace = os.path.join(self._store.dir_path(), "proj")
        self.scenes = os.path.join(self.workspace, "scenes")
        os.makedirs(self.scenes)
        self.boxes = []

    def tearDown(self):
        self._store.cleanup()

    def _touch(self, *parts):
        path = os.path.join(self.scenes, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def _listing(self, *parts):
        return sorted(os.listdir(os.path.join(self.scenes, *parts)))

    def _controller(self, answer):
        slot = MockSlot()
        slot._is_current = lambda path, current=None: False
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        controller.slot = slot
        controller.sb = slot.sb
        controller.ui = slot.ui
        controller.logger = MockLogger()
        controller.refresh_file_list = lambda invalidate=False: None
        # The browsed workspace and its {scenes} rule, pinned: the guards that
        # keep Rename / Delete off the project's own structure read both.
        controller._current_working_dir = self.workspace
        controller._scenes_folder = lambda: "scenes"
        menu = MagicMock()
        menu.txt_subfolder_structure = MockLineEdit("{scenes}/{name}")
        menu.txt_suffix = MockLineEdit("_v01")
        menu.cmb_case_style = MagicMock()
        menu.cmb_case_style.currentText.return_value = "None"
        slot.ui.header = type("H", (), {"menu": menu})()
        controller.sb.message_box = lambda msg, *b: self.boxes.append(msg)
        controller.sb.input_dialog = lambda title, label, default: answer
        table = controller.ui.tbl000
        table.setRowCount(1)
        item = QtWidgets.QTableWidgetItem("row")
        item.setData(QtCore.Qt.UserRole, self.old)
        table.setItem(0, 0, item)
        controller._context_menu_row = 0
        return controller

    def _inline_rename(self, typed):
        """Commit an inline (double-click) edit of the row's name to *typed*,
        through the REAL controller rename."""

        class _Item:
            def __init__(self, text, path):
                self._text, self._path = text, path

            def column(self):
                return 0

            def text(self):
                return self._text

            def data(self, role):
                return self._path

        slots = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slots.sb = MockSB()
        slots.sb.message_box = lambda msg, *b: self.boxes.append(msg)
        slots.ui = MockUI()
        slots.logger = MockLogger()
        slots.controller = self._controller(answer=None)
        item = _Item(typed, self.old)
        slots.controller._editing_item = item
        slots.tbl000_item_changed(item)


class TestCaseOnlyRename(_OnDiskRename, unittest.TestCase):
    """A rename that changes only the name's case goes through -- real files.

    Regression: on a case-insensitive file system (Windows) ``os.path.exists``
    reports ``Hero_v01.ma`` present while ``hero_v01.ma`` -- the very file being
    renamed -- is, so both rename routes refused it ("Target file exists") and
    the per-scene folder refused ``hero`` -> ``Hero`` the same way; the folder's
    ownership test was also case-sensitive, unlike Delete's. ``os.rename`` does
    case-only renames fine; the guards now let a target through when it IS the
    source (``ptk.FileUtils.is_same_file``). Real files, so the tests hold on
    either kind of file system. Added: 2026-09-23.
    """

    def setUp(self):
        super().setUp()
        self.old = self._touch("hero", "hero_v01.ma")

    def test_the_context_rename_changes_only_the_case(self):
        self._controller("Hero").rename_scene()
        self.assertEqual(self.boxes, [])
        self.assertEqual(self._listing(), ["Hero"])  # the folder too
        self.assertEqual(self._listing("Hero"), ["Hero_v01.ma"])

    def test_a_folder_differing_only_in_case_is_still_the_scenes_own(self):
        os.rename(os.path.join(self.scenes, "hero"), os.path.join(self.scenes, "Hero"))
        self.old = os.path.join(self.scenes, "Hero", "hero_v01.ma")
        self._controller("villain").rename_scene()
        self.assertEqual(self._listing(), ["villain"])
        self.assertEqual(self._listing("villain"), ["villain_v01.ma"])

    def test_the_inline_rename_changes_only_the_case(self):
        self._inline_rename("Hero_v01.ma")
        self.assertEqual(self.boxes, [])
        self.assertEqual(self._listing(), ["Hero"])
        self.assertEqual(self._listing("Hero"), ["Hero_v01.ma"])

    def test_the_inline_rename_carries_the_per_scene_folder(self):
        # Regression: the inline rename called the same disk-side rename as the
        # context menu's, minus the folder -- "villain_v01.ma" was left in "hero".
        self._inline_rename("villain_v01.ma")
        self.assertEqual(self._listing(), ["villain"])
        self.assertEqual(self._listing("villain"), ["villain_v01.ma"])


class TestPerSceneFolderMovesOnlyWithItsOwnScene(_OnDiskRename, unittest.TestCase):
    """A rename takes the scene's folder along only when the folder is its alone.

    Regression: ownership was a bare name-prefix test, so a scene loose in the
    scenes root whose name starts with the root's -- ``scenes/scenes_final.ma``
    -- "owned" ``scenes/``, and renaming it renamed the whole scenes folder with
    every other scene in it (Delete guards the same coincidence). A folder
    shared with other scenes (``hero_v01`` beside ``hero_v02``) moved with one of
    them the same way, filing the rest under a name no longer theirs. A folder
    now moves only when it holds no other Maya scene at any depth -- the scene's
    own incremental saves excepted. Added: 2026-09-23.
    """

    def test_a_loose_scene_never_moves_the_scenes_root(self):
        self.old = self._touch("scenes_final.ma")
        self._touch("hero", "hero_v01.ma")
        self._controller("final").rename_scene()
        self.assertTrue(os.path.isdir(self.scenes), "the scenes root was renamed")
        self.assertEqual(self._listing(), ["final_v01.ma", "hero"])

    def test_a_folder_shared_with_other_scenes_stays(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "hero_v02.ma")
        self._controller("villain").rename_scene()
        self.assertEqual(self._listing(), ["hero"])
        self.assertEqual(self._listing("hero"), ["hero_v02.ma", "villain_v01.ma"])

    def test_the_scenes_own_incremental_saves_do_not_hold_it_back(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "incrementalSave", "hero_v01.ma", "hero_v01.0001.ma")
        self._controller("villain").rename_scene()
        self.assertEqual(self._listing(), ["villain"])
        self.assertEqual(
            self._listing("villain", "incrementalSave", "villain_v01.ma"),
            ["hero_v01.0001.ma"],
        )

    def test_a_folder_that_cannot_be_read_is_left_alone(self):
        def _walk(top, onerror=None):
            onerror(PermissionError(13, "Access is denied", top))
            return iter(())

        holds = ref_mgr._ReferenceManagerInternal._holds_other_scenes
        with patch.object(ref_mgr.os, "walk", side_effect=_walk):
            self.assertTrue(holds(self.scenes, os.path.join(self.scenes, "a.ma")))

    def test_a_missing_folder_holds_nothing(self):
        holds = ref_mgr._ReferenceManagerInternal._holds_other_scenes
        gone = os.path.join(self.scenes, "gone")
        self.assertFalse(holds(gone, os.path.join(gone, "a.ma")))

    def test_the_inline_rename_never_moves_the_scenes_root_either(self):
        self.old = self._touch("scenes_final.ma")
        self._touch("hero", "hero_v01.ma")
        self._inline_rename("final.ma")
        self.assertTrue(os.path.isdir(self.scenes), "the scenes root was renamed")
        self.assertEqual(self._listing(), ["final.ma", "hero"])

    def test_a_scene_alone_in_the_scenes_root_never_moves_it(self):
        # No OTHER Maya scene anywhere below (a fresh project; its other rows are
        # FBX), so "holds other scenes" cannot protect the root: the structure
        # guard has to. Regression: the scenes root was renamed to "final".
        self.old = self._touch("scenes_final.ma")
        self._touch("props", "chair.fbx")
        self._controller("final").rename_scene()
        self.assertTrue(os.path.isdir(self.scenes), "the scenes root was renamed")
        self.assertEqual(self._listing(), ["final_v01.ma", "props"])

    def test_a_scene_at_the_workspace_root_never_moves_the_workspace(self):
        # "proj/proj_v01.ma" is named for the project folder itself; with no other
        # Maya scene in the project, Rename renamed the whole project to "final".
        self.old = os.path.join(self.workspace, "proj_v01.ma")
        open(self.old, "w").close()
        self._controller("final").rename_scene()
        self.assertTrue(os.path.isdir(self.workspace), "the workspace was renamed")
        self.assertEqual(
            sorted(os.listdir(self.workspace)), ["final_v01.ma", "scenes"]
        )


class TestDeleteRemovesOnlyWhatItNames(_OnDiskRename, unittest.TestCase):
    """Delete removes the scene (and its sidecar), and its per-scene folder only
    once nothing else is left in it -- real files.

    Regression: with a ``{name}`` structure, deleting a folder's last Maya scene
    ``rmtree``'d the folder, and with it every file that was not a Maya scene --
    an FBX or USD listed as a row of the same panel, a playblast, another file's
    notes -- none of them named in the "Delete hero_v01.ma?" prompt, none
    recoverable. And a scene alone in the scenes root took the root with it.
    Added: 2026-09-23.
    """

    def _delete(self):
        controller = self._controller(answer=None)
        controller.sb.message_box = lambda msg, *b: "Yes"  # confirm the delete
        controller.delete_scene()

    def test_the_last_scene_takes_its_emptied_folder_along(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "hero_v01.ma.metadata.json")
        self._delete()
        self.assertEqual(self._listing(), [])

    def test_a_file_that_is_not_a_scene_keeps_the_folder_and_survives(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "hero_v01.fbx")
        self._delete()
        self.assertEqual(self._listing("hero"), ["hero_v01.fbx"])

    def test_a_scene_alone_in_the_scenes_root_never_removes_it(self):
        self.old = self._touch("scenes_final.ma")
        self._delete()
        self.assertTrue(os.path.isdir(self.scenes), "the scenes root was removed")


class TestRenameOpenSceneSavesAndReopens(unittest.TestCase):
    """Renaming the scene that is currently open must save it first, then re-open the new file.

    Regression: rename only touched disk, so the Maya session kept pointing at the pre-rename
    filename — the user's unsaved edits went nowhere and the next save silently re-created the
    old file beside the renamed one (two scenes where the user renamed one).
    """

    def _make_controller(self, is_current=True):
        slot = MockSlot()
        slot._is_current = lambda path, current=None: is_current
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        controller.slot = slot
        controller.sb = slot.sb
        controller.ui = slot.ui
        controller.logger = MockLogger()
        # A browsed workspace unrelated to the C:proj paths below, so the
        # per-scene folders they rename are never the project's own structure.
        controller._current_working_dir = tempfile.gettempdir()
        controller._scenes_folder = lambda: "scenes"
        return controller

    @staticmethod
    def _fake_cmds_file(calls, fail_save=False):
        """Stand-in for ``cmds.file``: reports the scene modified, records saves."""

        def _file(*args, **kwargs):
            if kwargs.get("q") or kwargs.get("query"):
                return True  # scene has unsaved edits
            if kwargs.get("save"):
                if fail_save:
                    raise RuntimeError("disk full")
                calls.append(("save", kwargs.get("type")))

        return _file

    def _run_rename(self, controller, old, new, folder=None, fail_save=False):
        """Run the rename with disk + scene ops stubbed; returns (final_path, ordered calls)."""
        calls = []
        controller.open_scene = lambda p, **kw: calls.append(("open", p))
        with (
            patch.object(
                ref_mgr.cmds,
                "file",
                create=True,
                side_effect=self._fake_cmds_file(calls, fail_save),
            ),
            patch.object(ref_mgr.os.path, "exists", return_value=False),
            patch.object(
                ref_mgr.os,
                "rename",
                side_effect=lambda a, b: calls.append(("rename", a, b)),
            ),
        ):
            final = controller._rename_scene_file(old, new, folder=folder)
        return final, calls

    def test_open_scene_is_saved_before_the_rename_and_reopened_after(self):
        controller = self._make_controller(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "shot.ma")
        new = os.path.join("C:", "proj", "scenes", "hero.ma")

        final, calls = self._run_rename(controller, old, new)

        self.assertEqual(final, new)
        # Save must precede the rename (a save after it would re-create the old name),
        # and the re-open must follow it.
        self.assertEqual([c[0] for c in calls], ["save", "rename", "open"])
        self.assertEqual(calls[0][1], "mayaAscii")  # .ma keeps its type
        self.assertEqual(calls[-1][1], new)  # session lands on the new path

    def test_binary_scene_saves_as_mayabinary(self):
        controller = self._make_controller(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "shot.mb")
        new = os.path.join("C:", "proj", "scenes", "hero.mb")

        _, calls = self._run_rename(controller, old, new)

        self.assertEqual(calls[0], ("save", "mayaBinary"))

    def test_open_fbx_row_is_renamed_on_disk_only(self):
        """Maya opens an .fbx as a scene, but it is not one to save over — no save, no re-open."""
        controller = self._make_controller(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "kit.fbx")
        new = os.path.join("C:", "proj", "scenes", "kit_v2.fbx")

        final, calls = self._run_rename(controller, old, new)

        self.assertEqual(final, new)
        self.assertEqual([c[0] for c in calls], ["rename"])

    def test_a_folder_that_cant_be_renamed_is_reported_not_just_logged(self):
        """The scene itself WAS renamed, so a silently un-renamed folder leaves the file under
        the old scene's folder with nothing to explain it."""
        controller = self._make_controller(is_current=False)
        messages = []
        controller.sb = type(
            "_SB", (), {"message_box": lambda _s, msg, *a: messages.append(msg)}
        )()
        old = os.path.join("C:", "proj", "scenes", "Hero", "Hero_v01.ma")
        new = os.path.join("C:", "proj", "scenes", "Hero", "Villain_v01.ma")
        taken = os.path.join("C:", "proj", "scenes", "Villain")
        calls = []

        with (
            patch.object(
                ref_mgr.cmds,
                "file",
                create=True,
                side_effect=self._fake_cmds_file(calls),
            ),
            patch.object(ref_mgr.os.path, "exists", side_effect=lambda p: p == taken),
            patch.object(
                ref_mgr.os,
                "rename",
                side_effect=lambda a, b: calls.append(("rename", a, b)),
            ),
        ):
            final = controller._rename_scene_file(old, new, folder="Villain")

        self.assertEqual(final, new)  # the file rename stands
        self.assertEqual(len(calls), 1)  # only the file moved
        self.assertTrue(messages, "the skipped folder rename must reach the user")

    def test_renaming_a_closed_scene_leaves_the_session_alone(self):
        controller = self._make_controller(is_current=False)
        old = os.path.join("C:", "proj", "scenes", "other.ma")
        new = os.path.join("C:", "proj", "scenes", "renamed.ma")

        final, calls = self._run_rename(controller, old, new)

        self.assertEqual(final, new)
        self.assertEqual([c[0] for c in calls], ["rename"])  # no save, no re-open

    def test_failed_save_aborts_the_rename(self):
        """A scene that could not be saved must stay put — renaming it would strand the edits."""
        controller = self._make_controller(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "shot.ma")
        new = os.path.join("C:", "proj", "scenes", "hero.ma")

        final, calls = self._run_rename(controller, old, new, fail_save=True)

        self.assertIsNone(final)
        self.assertEqual(calls, [])  # nothing renamed, nothing re-opened

    def test_reopens_the_path_the_folder_move_landed_on(self):
        """With a {name} per-scene folder, the re-open must use the post-move path."""
        controller = self._make_controller(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "Hero", "Hero_v01.ma")
        new = os.path.join("C:", "proj", "scenes", "Hero", "Villain_v01.ma")

        final, calls = self._run_rename(controller, old, new, folder="Villain")

        moved = os.path.join("C:", "proj", "scenes", "Villain", "Villain_v01.ma")
        self.assertEqual(final, moved)
        self.assertEqual([c[0] for c in calls], ["save", "rename", "rename", "open"])
        self.assertEqual(calls[-1][1], moved)  # not the pre-move path


class TestRenameOpenSceneAgainstRealMaya(unittest.TestCase):
    """The same rename, driven against a REAL Maya scene on disk.

    The mocked cases above prove the ORDER of save / rename / re-open; only this one proves the
    thing the feature rests on — that Maya tolerates its open scene file being renamed out from
    under it, and that the edits flushed by the save survive the re-open (blendertk's suite has
    covered its side live from the start; this is the Maya twin).
    """

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_rename_test", policy="scoped")
        self.root = self._store.dir_path()
        self.controller = self._make_controller()
        # The panel browses the project these scenes live in (root/scenes/...).
        self.controller._current_working_dir = self.root
        self.controller._scenes_folder = lambda: "scenes"

    def tearDown(self):
        ref_mgr.cmds.file(new=True, force=True)  # leave no scene open for the next test
        self._store.cleanup()

    @staticmethod
    def _make_controller():
        """The real controller wired to the real slot ``_is_current`` — only Qt/ui is stubbed."""
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        slot = MockSlot()
        slot.controller = controller
        slot.logger = MockLogger()
        for name in ("_is_current", "_current_scene_path"):
            setattr(
                slot,
                name,
                getattr(ref_mgr.ReferenceManagerSlots, name).__get__(slot, type(slot)),
            )
        slot._foreign_scratch_path = ref_mgr.ReferenceManagerSlots._foreign_scratch_path
        controller.slot = slot
        controller.sb = slot.sb
        controller.ui = slot.ui
        controller.logger = MockLogger()
        return controller

    def _save_scene_as(self, path, *objects):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ref_mgr.cmds.file(new=True, force=True)
        for name in objects:
            ref_mgr.cmds.polySphere(name=name)
        ref_mgr.cmds.file(rename=path)
        ref_mgr.cmds.file(save=True, type="mayaAscii")

    @staticmethod
    def _open_scene_path():
        scene = ref_mgr.cmds.file(q=True, sceneName=True) or ""
        # normpath("") is "." — guard it, as the production _current_scene_path does.
        return os.path.normcase(os.path.normpath(scene)) if scene else ""

    def _assert_session_on(self, path):
        self.assertEqual(
            self._open_scene_path(), os.path.normcase(os.path.normpath(path))
        )

    def test_open_scene_rename_carries_unsaved_edits_and_moves_the_session(self):
        old = os.path.join(self.root, "scenes", "shot_v01.ma")
        new = os.path.join(self.root, "scenes", "hero_v01.ma")
        self._save_scene_as(old, "keeper")
        ref_mgr.cmds.polyCube(name="unsaved_edit")  # authored AFTER the save

        final = self.controller._rename_scene_file(old, new)

        self.assertEqual(final, new)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(new))
        self._assert_session_on(new)
        self.assertTrue(ref_mgr.cmds.objExists("keeper"))
        # The edit made since the last save was flushed into the file that got renamed.
        self.assertTrue(ref_mgr.cmds.objExists("unsaved_edit"))
        # And a save now writes the new name — it does not resurrect the old one.
        ref_mgr.cmds.file(save=True, type="mayaAscii")
        self.assertFalse(os.path.exists(old))

    def test_open_scene_rename_reopens_the_path_the_folder_move_landed_on(self):
        old = os.path.join(self.root, "scenes", "Hero", "Hero_v01.ma")
        new = os.path.join(self.root, "scenes", "Hero", "Villain_v01.ma")
        self._save_scene_as(old, "folder_probe")
        ref_mgr.cmds.polyCube(name="folder_edit")
        # Increments live INSIDE the per-scene folder, so the two moves compose: they are
        # re-keyed to the new filename first, then ride along with the folder rename.
        increments = os.path.join(
            self.root, "scenes", "Hero", "incrementalSave", "Hero_v01.ma"
        )
        os.makedirs(increments)
        with open(os.path.join(increments, "Hero_v01.0001.ma"), "w") as f:
            f.write("an older increment")

        final = self.controller._rename_scene_file(old, new, folder="Villain")

        moved = os.path.join(self.root, "scenes", "Villain", "Villain_v01.ma")
        self.assertEqual(final, moved)
        self.assertTrue(os.path.isfile(moved))
        self.assertFalse(os.path.isdir(os.path.dirname(old)))
        self._assert_session_on(moved)
        self.assertTrue(ref_mgr.cmds.objExists("folder_edit"))
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    self.root,
                    "scenes",
                    "Villain",
                    "incrementalSave",
                    "Villain_v01.ma",
                    "Hero_v01.0001.ma",
                )
            )
        )

    def test_sidecar_metadata_follows_the_rename(self):
        old = os.path.join(self.root, "scenes", "with_notes.ma")
        new = os.path.join(self.root, "scenes", "with_notes_renamed.ma")
        self._save_scene_as(old, "note_probe")
        with open(old + ".metadata.json", "w", encoding="utf-8") as f:
            f.write('{"Comments": "hello"}')

        self.controller._rename_scene_file(old, new)

        self.assertTrue(os.path.isfile(new + ".metadata.json"))
        self.assertFalse(os.path.exists(old + ".metadata.json"))

    def test_incremental_save_folder_follows_the_rename(self):
        """Maya keys its Incremental Save folder to the scene FILENAME. Left behind, the history
        detaches from its scene — and a later scene reusing the old name inherits it."""
        old = os.path.join(self.root, "scenes", "shot_v01.ma")
        new = os.path.join(self.root, "scenes", "hero_v01.ma")
        self._save_scene_as(old, "inc_probe")
        # The layout Maya's own incrementalSaveProcessPath.mel builds.
        increments = os.path.join(self.root, "scenes", "incrementalSave", "shot_v01.ma")
        os.makedirs(increments)
        with open(os.path.join(increments, "shot_v01.0001.ma"), "w") as f:
            f.write("an older increment")

        self.controller._rename_scene_file(old, new)

        moved = os.path.join(self.root, "scenes", "incrementalSave", "hero_v01.ma")
        self.assertTrue(os.path.isfile(os.path.join(moved, "shot_v01.0001.ma")))
        self.assertFalse(os.path.exists(increments))

    def test_renaming_a_closed_scene_opens_nothing(self):
        path = os.path.join(self.root, "scenes", "untouched.ma")
        self._save_scene_as(path, "other_probe")
        ref_mgr.cmds.file(new=True, force=True)  # nothing open now

        renamed = os.path.join(self.root, "scenes", "untouched_renamed.ma")
        self.controller._rename_scene_file(path, renamed)

        self.assertTrue(os.path.isfile(renamed))
        self.assertEqual(self._open_scene_path(), "")


class TestUnlinkNamespaceModeSelection(unittest.TestCase):
    """The namespace choice cycled beside Unlink and Import All must reach
    ``import_references`` from BOTH unlink entry points, and be named in the confirm
    prompt so it is never a hidden setting that silently changes what an unlink does
    to the scene."""

    STATES = ref_mgr.ReferenceManagerController._UNLINK_NAMESPACE_STATES

    @staticmethod
    def _make_controller(state=None, answer="Yes"):
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        slot = MockSlot()
        controller.slot = slot
        controller.sb = slot.sb
        controller.ui = slot.ui
        controller.logger = MockLogger()
        controller.prompts = []
        controller.sb.message_box = lambda text, *b, **kw: (
            controller.prompts.append(text) or answer
        )
        controller.refresh_file_list = lambda *a, **kw: None
        controller.calls = []
        controller.import_references = lambda **kw: controller.calls.append(kw)
        if state is None:
            slot.ui.header = None  # menu not built yet
        else:
            action = type("A", (), {"current_state": state})()
            box = type("X", (), {"find_option": lambda self, _t: action})()
            button = type("B", (), {"option_box": box})()
            menu = type("M", (), {"btn_unlink_import_all": button})()
            slot.ui.header = type("H", (), {"menu": menu})()
        return controller

    @staticmethod
    def _unwrapped(name):
        """The undecorated method — ``block_table_selection_method`` touches tbl000."""
        return getattr(ref_mgr.ReferenceManagerController, name).__wrapped__

    def test_every_state_maps_to_a_supported_mode(self):
        modes = [mode for mode, _icon, _tip in self.STATES]
        self.assertEqual(
            sorted(modes),
            sorted(ref_mgr.ReferenceManager.NAMESPACE_MODES),
            "the button must cycle every mode the core supports, once, and invent none",
        )
        # uitk persists the state by INDEX: pin that the default (0) IS 'remove'.
        self.assertEqual(modes[0], "remove")
        # Every mode is describable in the confirm prompt.
        self.assertEqual(
            set(ref_mgr.ReferenceManagerController._UNLINK_MODE_LABELS), set(modes)
        )

    def test_every_state_icon_exists(self):
        """A bad icon name is a silent empty QIcon — a blank, unreadable button."""
        import uitk

        icons = os.path.join(os.path.dirname(uitk.__file__), "icons")
        for _mode, icon, _tip in self.STATES:
            self.assertTrue(
                os.path.isfile(os.path.join(icons, f"{icon}.svg")), f"{icon!r}"
            )

    def test_the_built_action_cycles_through_every_state(self):
        """``_add_unlink_namespace_action`` hands uitk one state per mode, in order."""
        captured = {}
        box = type("X", (), {"set_action": lambda self, **kw: captured.update(kw)})()
        button = type("B", (), {"option_box": box})()
        ref_mgr.ReferenceManagerController._add_unlink_namespace_action(
            self._make_controller(), button
        )
        self.assertEqual(
            [s["icon"] for s in captured["states"]],
            [icon for _mode, icon, _tip in self.STATES],
        )

    def test_mode_defaults_to_remove_without_a_menu(self):
        controller = self._make_controller(state=None)
        self.assertEqual(controller._unlink_namespace_mode(), "remove")

    def test_each_entry_point_forwards_the_selected_mode(self):
        for index, (expected, _icon, _tip) in enumerate(self.STATES):
            controller = self._make_controller(state=index)
            self._unwrapped("unlink_all")(controller)
            self._unwrapped("unlink_references")(controller, ["ns_a", "ns_b"])
            self.assertEqual(
                [c.get("namespace_mode") for c in controller.calls],
                [expected, expected],
                f"state {index} must reach import_references from both entry points",
            )
            # The row-scoped call stays scoped to the namespaces it was handed.
            self.assertEqual(controller.calls[1].get("namespaces"), ["ns_a", "ns_b"])

    def test_prompt_names_the_mode_that_will_be_applied(self):
        for index, (mode, _icon, _tip) in enumerate(self.STATES):
            controller = self._make_controller(state=index)
            self._unwrapped("unlink_all")(controller)
            self._unwrapped("unlink_references")(controller, ["ns_a"])
            label = ref_mgr.ReferenceManagerController._UNLINK_MODE_LABELS[mode]
            for prompt in controller.prompts:
                self.assertIn(label, prompt)

    def test_declining_the_prompt_imports_nothing(self):
        controller = self._make_controller(state=1, answer="No")
        self._unwrapped("unlink_all")(controller)
        self._unwrapped("unlink_references")(controller, ["ns_a"])
        self.assertEqual(controller.calls, [])

    def test_both_entry_points_ask_about_the_scene_data(self):
        controller = self._make_controller(state=0)
        self._unwrapped("unlink_all")(controller)
        self._unwrapped("unlink_references")(controller, ["ns_a"])
        self.assertEqual(
            [c.get("scene_data") for c in controller.calls],
            [controller._ask_scene_data] * 2,
        )

    def test_the_scene_data_question_maps_each_answer(self):
        """Yes merges, No drops, Cancel leaves the reference linked -- and the
        question names what the reference brings."""
        for answer, expected in (("Yes", "merge"), ("No", "discard"), ("Cancel", None)):
            controller = self._make_controller(answer=answer)
            self.assertEqual(
                controller._ask_scene_data(["Audio Clips: 1 entry"], "MOD"), expected
            )
            self.assertIn("Audio Clips: 1 entry", controller.prompts[-1])


class TestImportReferencesNamespaceModes(unittest.TestCase):
    """The three namespace modes, driven against real referenced scenes.

    'root' is the reason this runs live: it has no mock-able surface — it rests on
    Maya's own namespace-merge clash handling plus the fact that an MObject survives
    the renames that merge performs.
    """

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_namespace_test", policy="scoped")
        self.root = self._store.dir_path()
        self.asset = os.path.join(self.root, "asset.ma")
        self._author_asset(self.asset)
        self.manager = self._make_manager()

    def tearDown(self):
        ref_mgr.cmds.file(new=True, force=True)
        self._store.cleanup()

    @staticmethod
    def _make_manager():
        manager = ref_mgr.ReferenceManager.__new__(ref_mgr.ReferenceManager)
        manager.logger = MockLogger()
        return manager

    @staticmethod
    def _author_asset(path):
        """A two-level asset: ``asset_root`` > ``asset_child`` (+ their shapes)."""
        ref_mgr.cmds.file(new=True, force=True)
        root = ref_mgr.cmds.polyCube(name="asset_root")[0]
        child = ref_mgr.cmds.polyCube(name="asset_child")[0]
        ref_mgr.cmds.parent(child, root)
        ref_mgr.cmds.file(rename=path)
        ref_mgr.cmds.file(save=True, type="mayaAscii")
        ref_mgr.cmds.file(new=True, force=True)

    def _reference(self, namespace="ASSET"):
        ref_mgr.cmds.file(self.asset, reference=True, namespace=namespace)

    @staticmethod
    def _transforms():
        """Short names of the scene's non-default transforms."""
        default = {"persp", "top", "front", "side"}
        return sorted(
            n.split("|")[-1]
            for n in ref_mgr.cmds.ls(type="transform", long=True) or []
            if n.split("|")[-1].split(":")[-1] not in default
        )

    def test_remove_mode_strips_the_namespace_from_every_node(self):
        self._reference()
        self.manager.import_references(namespace_mode="remove")
        self.assertEqual(self._transforms(), ["asset_child", "asset_root"])
        self.assertFalse(ref_mgr.cmds.namespace(exists="ASSET"))

    def test_keep_mode_leaves_every_node_namespaced(self):
        self._reference()
        self.manager.import_references(namespace_mode="keep")
        self.assertEqual(self._transforms(), ["ASSET:asset_child", "ASSET:asset_root"])
        self.assertTrue(ref_mgr.cmds.namespace(exists="ASSET"))
        # The reference link itself is gone — this is an import, not a load.
        self.assertEqual(self.manager.current_references, [])

    def test_root_mode_namespaces_the_top_transform_only(self):
        self._reference()
        self.manager.import_references(namespace_mode="root")
        self.assertEqual(self._transforms(), ["ASSET:asset_root", "asset_child"])
        # The namespace survives holding the root and nothing below it. The root's OWN
        # shape rides along: Maya keeps a shape's name in step with its transform, so
        # re-namespacing the root re-namespaces its shape too (documented, not a leak).
        self.assertEqual(
            sorted(
                n.split("|")[-1]
                for n in ref_mgr.cmds.namespaceInfo(
                    "ASSET", listOnlyDependencyNodes=True
                )
                or []
            ),
            ["ASSET:asset_root", "ASSET:asset_rootShape"],
        )
        # The child hierarchy and its shape are merged into the scene, unprefixed.
        self.assertTrue(ref_mgr.cmds.objExists("|ASSET:asset_root|asset_child"))
        self.assertEqual(ref_mgr.cmds.ls("ASSET:asset_child"), [])
        self.assertEqual(self.manager.current_references, [])

    def _point_current_namespace_elsewhere(self):
        """Leave the session's CURRENT namespace on something other than the root.

        Routine in a real session — the Namespace Editor sets it, and so does any tool
        that imports into a sandbox namespace (see ``namespace_sandbox``).
        """
        ref_mgr.cmds.namespace(add=":BYSTANDER")
        ref_mgr.cmds.namespace(set=":BYSTANDER")
        self.addCleanup(lambda: ref_mgr.cmds.namespace(set=":"))

    def test_remove_mode_strips_under_a_non_root_current_namespace(self):
        """``cmds.namespace`` resolves a BARE name against the CURRENT namespace, so a
        session pointing anywhere but root made the existence guard report False and the
        strip silently no-op — 'remove' quietly behaved as 'keep'."""
        self._reference()
        self._point_current_namespace_elsewhere()

        self.manager.import_references(namespace_mode="remove")

        ref_mgr.cmds.namespace(set=":")
        self.assertEqual(self._transforms(), ["asset_child", "asset_root"])
        self.assertFalse(ref_mgr.cmds.namespace(exists=":ASSET"))

    def test_root_mode_works_under_a_non_root_current_namespace(self):
        self._reference()
        self._point_current_namespace_elsewhere()

        self.manager.import_references(namespace_mode="root")

        ref_mgr.cmds.namespace(set=":")
        self.assertEqual(self._transforms(), ["ASSET:asset_root", "asset_child"])
        # Re-created at the ROOT, not nested under whatever was current.
        self.assertTrue(ref_mgr.cmds.namespace(exists=":ASSET"))
        self.assertFalse(ref_mgr.cmds.namespace(exists=":BYSTANDER:ASSET"))

    def test_root_mode_survives_the_asset_being_grouped(self):
        """Top-level is relative to the REFERENCE, not the world. Parenting a referenced
        asset under a scene group is routine; if that made the reference look rootless,
        'root' would silently degrade into 'remove'."""
        self._reference()
        group = ref_mgr.cmds.group(empty=True, name="SET_DRESSING")
        ref_mgr.cmds.parent("ASSET:asset_root", group)

        self.manager.import_references(namespace_mode="root")

        self.assertTrue(ref_mgr.cmds.objExists("|SET_DRESSING|ASSET:asset_root"))
        self.assertTrue(
            ref_mgr.cmds.objExists("|SET_DRESSING|ASSET:asset_root|asset_child")
        )

    def test_top_transforms_are_reference_relative_not_world_relative(self):
        """The same rule read directly off the query the modes rest on."""
        self._reference()
        group = ref_mgr.cmds.group(empty=True, name="SET_DRESSING")
        ref_mgr.cmds.parent("ASSET:asset_root", group)

        (ref,) = self.manager.current_references
        self.assertEqual(
            [t.split("|")[-1] for t in self.manager.get_reference_top_transforms(ref)],
            ["ASSET:asset_root"],
        )

    def test_root_mode_keeps_each_reference_under_its_own_namespace(self):
        """Two references of the SAME file: Maya uniquifies the merged child names, and
        each root must still land back under its own namespace rather than collide."""
        self._reference("ASSET_A")
        self._reference("ASSET_B")
        self.manager.import_references(namespace_mode="root")

        roots = [t for t in self._transforms() if "asset_root" in t]
        self.assertEqual(roots, ["ASSET_A:asset_root", "ASSET_B:asset_root"])
        for ns in ("ASSET_A", "ASSET_B"):
            self.assertEqual(
                sorted(ref_mgr.cmds.namespaceInfo(ns, listOnlyDependencyNodes=True)),
                [f"{ns}:asset_root", f"{ns}:asset_rootShape"],
            )

    def test_root_mode_scoped_to_one_namespace_leaves_the_other_referenced(self):
        self._reference("ASSET_A")
        self._reference("ASSET_B")
        self.manager.import_references(namespaces="ASSET_A", namespace_mode="root")

        self.assertEqual(
            [r.namespace for r in self.manager.current_references], ["ASSET_B"]
        )
        self.assertTrue(ref_mgr.cmds.objExists("ASSET_A:asset_root"))
        self.assertTrue(ref_mgr.cmds.objExists("asset_child"))

    def test_invalid_mode_raises_rather_than_silently_removing(self):
        self._reference()
        with self.assertRaises(ValueError):
            self.manager.import_references(namespace_mode="strip")
        # Nothing was imported — the reference is untouched.
        self.assertEqual(
            [r.namespace for r in self.manager.current_references], ["ASSET"]
        )

    def test_the_retired_bool_form_is_gone(self):
        """``remove_namespace`` (the bool the modes replaced 2026-08-11) had no
        caller left and was retired 2026-09-21: ``namespace_mode="keep"`` is
        the old ``False``, ``"remove"`` the old ``True``."""
        self._reference()
        with self.assertRaises(TypeError):
            self.manager.import_references(remove_namespace=False)
        self.manager.import_references(namespace_mode="keep")
        self.assertEqual(self._transforms(), ["ASSET:asset_child", "ASSET:asset_root"])


class TestImportReferencesClearsWhatItPromoted(unittest.TestCase):
    """An import leaves no broken reference behind (BACKLOG 2026-08-29).

    A reference Maya could not form -- a scene referencing the file that is
    ALREADY open -- survives inside its parent as a node with no file.
    Importing the parent promoted it to a top-level file-less node that the
    scene then saved and Maya's Reference Editor listed as broken. It is
    debris the import itself made, so the import removes it inside its own
    undo chunk (the maintainer's call of 2026-09-10); nothing else is touched.
    """

    def setUp(self):
        cmds = ref_mgr.cmds
        self._store = ptk.TempArtifacts("mtk_rm_file_less_test", policy="scoped")
        root = self._store.dir_path()
        self.child = os.path.join(root, "fl_child.ma")
        self.parent = os.path.join(root, "fl_parent.ma")
        self.healthy = os.path.join(root, "fl_healthy.ma")
        for path, build in (
            (self.child, lambda: cmds.polyCube(name="child_cube")),
            (self.healthy, lambda: cmds.polyCube(name="healthy_cube")),
            (
                self.parent,
                lambda: cmds.file(self.child, reference=True, namespace="CHILD"),
            ),
        ):
            cmds.file(new=True, force=True)
            build()
            cmds.file(rename=path)
            cmds.file(save=True, type="mayaAscii", force=True)
        # The child IS the open scene, so the parent's reference to it cannot form.
        cmds.file(self.child, open=True, force=True)
        self.manager = TestImportReferencesNamespaceModes._make_manager()

    def tearDown(self):
        ref_mgr.cmds.file(new=True, force=True)
        self._store.cleanup()

    @staticmethod
    def _file_less():
        return ref_mgr.EnvUtils.list_reference_nodes(file_less=True)

    def test_the_node_the_import_promoted_is_removed(self):
        cmds = ref_mgr.cmds
        cmds.file(self.parent, reference=True, namespace="PARENT")
        cmds.file(self.healthy, reference=True, namespace="KEEP")

        self.manager.import_references(namespaces="PARENT")

        self.assertEqual(self._file_less(), [], "no broken reference is left")
        self.assertEqual(
            [ref.namespace for ref in self.manager.current_references],
            ["KEEP"],
            "a healthy reference is never caught by the probe",
        )

    def test_debris_an_earlier_import_left_is_not_this_one_s(self):
        cmds = ref_mgr.cmds
        cmds.file(self.parent, reference=True, namespace="EARLIER")
        cmds.file(referenceNode="EARLIERRN", importReference=True)  # a raw import
        earlier = self._file_less()
        self.assertEqual(len(earlier), 1, "the fixture must hold older debris")
        cmds.file(self.parent, reference=True, namespace="PARENT")

        self.manager.import_references(namespaces="PARENT")

        self.assertEqual(self._file_less(), earlier)

    def test_one_undo_brings_the_removed_node_back(self):
        cmds = ref_mgr.cmds
        cmds.file(self.parent, reference=True, namespace="PARENT")
        self.manager.import_references(namespaces="PARENT")
        self.assertEqual(self._file_less(), [])
        cmds.undo()
        self.assertEqual(len(self._file_less()), 1)


class TestUsdRowsAgainstRealMaya(unittest.TestCase):
    """USD rows reference and open NATIVELY, through mayaUsd's translator -- the way an
    .fbx row goes through the FBX plugin -- driven against a real Maya.

    Measured first (Maya 2025 / mayaUsd 0.30): the translator is a real reference
    reader -- reference node, namespace, unload/reload and importReference all work,
    and the saved scene records ``-typ`` / ``-op`` so a reopen reads the stage the same
    way. Left to pick the translator by extension, though, Maya reads with
    ``readAnimData`` at its OFF default (the keyed stage arrived static), and a
    dual-quaternion skin crashes the reader outright (an access violation) -- so a live
    read of one is refused rather than attempted.
    """

    def setUp(self):
        from mayatk.env_utils.usd import UsdUtils

        UsdUtils.load_plugin()
        self._store = ptk.TempArtifacts("mtk_rm_usd_test", policy="scoped")
        self.root = self._store.dir_path()
        self.usd = os.path.join(self.root, "crate.usda")
        cmds = ref_mgr.cmds
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="crate")[0]
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=10, v=5)
        cmds.select(cube)
        cmds.mayaUSDExport(file=self.usd, selection=True, frameRange=(1, 10))
        cmds.file(new=True, force=True)

    def tearDown(self):
        ref_mgr.cmds.file(new=True, force=True)
        self._store.cleanup()

    def _dual_quaternion_stage(self):
        """A stage authoring the one skinning method mayaUsd 0.30's reader crashes on."""
        from pxr import Usd, UsdGeom, UsdSkel

        path = os.path.join(self.root, "limb_dq.usda")
        stage = Usd.Stage.CreateNew(path)
        prim = UsdGeom.Mesh.Define(stage, "/rig/limb").GetPrim()
        UsdSkel.BindingAPI.Apply(prim)
        UsdSkel.BindingAPI(prim).CreateSkinningMethodAttr().Set("dualQuaternion")
        stage.GetRootLayer().Save()
        return path

    @staticmethod
    def _norm(path):
        return os.path.normcase(os.path.normpath(path))

    def _metre_z_up_stage(self):
        """Blender's default USD export shape: metres, Z up -- a 0.2 x 0.2 x 1 m
        box standing on Z. mayaUsd 0.30 converts neither on read."""
        from pxr import Gf, Usd, UsdGeom

        path = os.path.join(self.root, "tall_box_m_zup.usda")
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        mesh = UsdGeom.Mesh.Define(stage, "/tall_box")
        corners = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
        points = [Gf.Vec3f(x, y, z) for z in (0.0, 1.0) for x, y in corners]
        mesh.CreatePointsAttr(points)
        mesh.CreateFaceVertexCountsAttr([4] * 6)
        mesh.CreateFaceVertexIndicesAttr(
            [0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 1, 2, 6, 5, 2, 3, 7, 6, 3, 0, 4, 7]
        )
        stage.SetDefaultPrim(mesh.GetPrim())
        stage.GetRootLayer().Save()
        return path

    def _world_box(self, nodes):
        box = ref_mgr.cmds.exactWorldBoundingBox(nodes)
        return [round(v, 3) for v in box]

    def test_a_metre_z_up_reference_lands_at_size_and_upright(self):
        """BACKLOG 2026-09-21 (decided 2026-09-23: auto-conform): measured, this
        layer referenced at 0.2 x 0.2 x 1.0 cm standing along Z. Its top nodes
        now sit under a host-side group that scales and turns it into the
        scene's cm / Y-up -- a parent edit, so an unload/reload keeps it."""
        cmds = ref_mgr.cmds
        manager = TestImportReferencesNamespaceModes._make_manager()
        self.assertTrue(manager.add_reference("tall", self._metre_z_up_stage()))
        ref = manager.current_references[0]
        geo = cmds.ls(f"{ref.namespace}:tall_box", long=True)
        want = [-10.0, 0.0, -10.0, 10.0, 100.0, 10.0]
        self.assertEqual(self._world_box(geo), want)
        self.assertTrue(cmds.objExists("tall_conform"))

        rn = cmds.referenceQuery(geo[0], referenceNode=True)
        cmds.file(unloadReference=rn)
        cmds.file(loadReference=rn)
        geo = cmds.ls(f"{ref.namespace}:tall_box", long=True)
        self.assertEqual(self._world_box(geo), want, "the conform survives a reload")

        self.assertEqual(manager.remove_references(["tall"]), [])
        self.assertFalse(cmds.objExists("tall_conform"), "an empty group goes too")

    def test_a_metre_z_up_scene_opens_at_size_and_upright(self):
        controller = TestRenameOpenSceneAgainstRealMaya._make_controller()
        self.assertTrue(
            controller.open_scene(self._metre_z_up_stage(), set_workspace=False)
        )
        self.assertEqual(
            self._world_box(ref_mgr.cmds.ls("tall_box", long=True)),
            [-10.0, 0.0, -10.0, 10.0, 100.0, 10.0],
        )

    def test_a_scene_unit_layer_gets_no_conform_group(self):
        """The bridge writes Maya-bound layers in cm / Y-up; so does mayaUsd."""
        manager = TestImportReferencesNamespaceModes._make_manager()
        self.assertTrue(manager.add_reference("crate", self.usd))
        self.assertFalse(ref_mgr.cmds.ls("*_conform"))

    def test_a_metre_z_up_import_lands_at_size_and_upright(self):
        """The native import path (the Reference Manager's Import of a USD row,
        through ``BlenderSceneImport.import_scene``'s USD fast path) conforms
        the same way; the bridge's own payload does not ask to."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        cmds = ref_mgr.cmds
        BlenderSceneImport().import_scene(self._metre_z_up_stage())
        self.assertEqual(
            self._world_box(cmds.ls("tall_box", long=True)),
            [-10.0, 0.0, -10.0, 10.0, 100.0, 10.0],
        )

    def test_add_reference_reads_a_usd_through_its_translator(self):
        manager = TestImportReferencesNamespaceModes._make_manager()
        self.assertTrue(manager.add_reference("crate", self.usd))

        refs = manager.current_references
        self.assertEqual([self._norm(r.path) for r in refs], [self._norm(self.usd)])
        self.assertTrue(
            ref_mgr.cmds.keyframe(
                f"{refs[0].namespace}:crate", q=True, timeChange=True
            ),
            "the referenced stage arrived static",
        )
        # Stored on the reference itself, so a reopen reads the stage the same way.
        host = os.path.join(self.root, "host.ma")
        ref_mgr.cmds.file(rename=host)
        ref_mgr.cmds.file(save=True, type="mayaAscii")
        with open(host, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        self.assertIn('-typ "USD Import"', text)
        self.assertIn("readAnimData=1", text)

    def test_add_reference_refuses_a_usd_the_reader_crashes_on(self):
        manager = TestImportReferencesNamespaceModes._make_manager()
        with patch.object(ref_mgr.cmds, "warning") as warned:
            self.assertFalse(
                manager.add_reference("limb", self._dual_quaternion_stage())
            )
        self.assertEqual(manager.current_references, [])
        self.assertIn("/rig/limb", warned.call_args[0][0])

    def test_open_scene_opens_a_usd_through_its_translator(self):
        controller = TestRenameOpenSceneAgainstRealMaya._make_controller()
        self.assertTrue(controller.open_scene(self.usd, set_workspace=False))
        self.assertEqual(
            self._norm(ref_mgr.cmds.file(q=True, sceneName=True)), self._norm(self.usd)
        )
        self.assertTrue(
            ref_mgr.cmds.keyframe("crate", q=True, timeChange=True),
            "the opened stage arrived static",
        )

    def test_open_scene_refuses_a_usd_the_reader_crashes_on(self):
        controller = TestRenameOpenSceneAgainstRealMaya._make_controller()
        shown = []
        controller.sb.message_box = lambda msg, *buttons: shown.append(msg)
        dq = self._dual_quaternion_stage()
        with patch.object(ref_mgr.cmds, "warning"):
            self.assertFalse(controller.open_scene(dq, set_workspace=False))
        self.assertNotEqual(
            self._norm(ref_mgr.cmds.file(q=True, sceneName=True) or "x"),
            self._norm(dq),
        )
        self.assertTrue(shown and "/rig/limb" in shown[0], shown)
        self.assertIn("Unlink and Import", shown[0])

    def _garbage_layer(self):
        """A layer pxr cannot parse -- its error quotes the prim path ``</>``."""
        path = os.path.join(self.root, "damaged.usda")
        with open(path, "wb") as fh:
            fh.write(b"#usda 1.0\n(\n this is not usd {{{\n")
        return path

    def test_add_reference_refuses_an_unreadable_usd(self):
        """Measured: the translator does not fail on a layer pxr cannot read -- it
        leaves an EMPTY reference node behind, which reads as referenced in the table
        while holding nothing."""
        manager = TestImportReferencesNamespaceModes._make_manager()
        with patch.object(ref_mgr.cmds, "warning") as warned:
            self.assertFalse(manager.add_reference("damaged", self._garbage_layer()))
        self.assertEqual(manager.current_references, [])
        self.assertEqual(
            [r for r in ref_mgr.cmds.ls(type="reference") or [] if "shared" not in r],
            [],
        )
        self.assertIn("not a readable USD layer", warned.call_args[0][0])

    def test_open_scene_refuses_an_unreadable_usd_without_offering_the_import(self):
        """A damaged layer reads for neither path, so the box must not send the user
        to Unlink and Import; and pxr's ``</>`` must reach the box escaped, not as
        markup that swallows the rest of the message."""
        controller = TestRenameOpenSceneAgainstRealMaya._make_controller()
        shown = []
        controller.sb.message_box = lambda msg, *buttons: shown.append(msg)
        with patch.object(ref_mgr.cmds, "warning"):
            self.assertFalse(
                controller.open_scene(self._garbage_layer(), set_workspace=False)
            )
        self.assertTrue(shown and "not a readable USD layer" in shown[0], shown)
        self.assertNotIn("Unlink and Import", shown[0])
        self.assertNotIn("</>", shown[0])


class TestImportReferencesSceneData(unittest.TestCase):
    """An imported reference's own data nodes never stay behind, read by nothing:
    its records merge into this scene's -- respelled to where the import put its
    nodes -- or go with them, and a decider is asked only when a merge would keep
    something. Live: the renames are Maya's own namespace-merge clash handling."""

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_scene_data_test", policy="scoped")
        self.module = os.path.join(self._store.dir_path(), "module.ma")
        self.manager = TestImportReferencesNamespaceModes._make_manager()

    def tearDown(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ref_mgr.cmds.file(new=True, force=True)
        ShotStore.clear_active()
        self._store.cleanup()

    def _author_module(self, **records):
        """A module with a cube named like the host's (``door``) and *records*
        saved on its own data nodes."""
        from mayatk.node_utils.data_nodes import DataNodes

        cmds = ref_mgr.cmds
        cmds.file(new=True, force=True)
        cmds.polyCube(name="door")
        for spec, payload in records.items():
            getattr(ptk.SceneRecords, spec).save(DataNodes, payload)
        cmds.file(rename=self.module)
        cmds.file(save=True, type="mayaAscii")
        cmds.file(new=True, force=True)

    def _host_referencing_it(self):
        """A host with its own ``door`` -- the module's arrives as ``door1`` -- and
        its own audio clips."""
        from mayatk.node_utils.data_nodes import DataNodes

        ref_mgr.cmds.polyCube(name="door")
        ptk.SceneRecords.AUDIO_FILE_MAP.save(DataNodes, {"1": "a.wav"})
        ref_mgr.cmds.file(self.module, reference=True, namespace="MOD")

    def _module_with_a_shot(self):
        self._author_module(
            SHOT_STORE={
                "shots": [
                    {
                        "shot_id": 1,
                        "name": "Intro",
                        "start": 0,
                        "end": 5,
                        "objects": ["|door"],
                    }
                ]
            },
            AUDIO_FILE_MAP={"2": "b.wav"},
        )
        self._host_referencing_it()

    @staticmethod
    def _load(spec):
        from mayatk.node_utils.data_nodes import DataNodes

        return getattr(ptk.SceneRecords, spec).load(DataNodes)

    @staticmethod
    def _carriers():
        return sorted(
            ref_mgr.cmds.ls("*data_internal*", "*:*data_internal*", type="network")
            or []
        )

    def test_a_merge_respells_the_records_and_the_carriers_go(self):
        self._module_with_a_shot()
        self.manager.import_references(namespace_mode="remove")
        audio = self._load("AUDIO_FILE_MAP")
        self.assertEqual(audio["1"], "a.wav")
        # The module's path is spelled from ITS project (the folder it was saved
        # in) and lands spelled from this scene's -- absolute while this one is
        # unsaved (the path rule, 2026-09-23).
        self.assertEqual(
            os.path.normcase(audio["2"]),
            os.path.normcase(os.path.join(os.path.dirname(self.module), "b.wav")),
        )
        (shot,) = self._load("SHOT_STORE")["shots"]
        self.assertEqual(shot["objects"], ["|door1"], "where the clash put it")
        self.assertEqual(self._carriers(), ["data_internal"])

    def test_a_merged_path_is_spelled_from_the_host_project(self):
        """The path rule (2026-09-23): the module's audio path is spelled from
        ITS project; merged into a host saved in another project, it arrives
        re-spelled from the host's (``merge_carriers(source_path_base=)``) --
        a relative spelling naming the same file."""
        self._module_with_a_shot()
        host_root = self._store.dir_path(name="host_project")
        os.makedirs(os.path.join(host_root, "scenes"), exist_ok=True)
        with open(os.path.join(host_root, "workspace.mel"), "w") as fh:
            fh.write("//Maya 2025 Project Definition\n")
        ref_mgr.cmds.file(rename=os.path.join(host_root, "scenes", "host.ma"))
        ref_mgr.cmds.file(save=True, type="mayaAscii")
        self.manager.import_references(namespace_mode="remove")
        landed = self._load("AUDIO_FILE_MAP")["2"]
        module_wav = os.path.join(os.path.dirname(self.module), "b.wav")
        self.assertFalse(os.path.isabs(landed), landed)
        self.assertEqual(landed, ptk.FileUtils.portable_path(module_wav, host_root))
        self.assertEqual(
            os.path.normcase(os.path.normpath(os.path.join(host_root, landed))),
            os.path.normcase(os.path.normpath(module_wav)),
        )

    def test_a_kept_namespace_is_respelled_too(self):
        self._module_with_a_shot()
        self.manager.import_references(namespace_mode="keep")
        (shot,) = self._load("SHOT_STORE")["shots"]
        self.assertEqual(shot["objects"], ["|MOD:door"])
        self.assertEqual(self._carriers(), ["data_internal"])

    def test_discard_drops_the_records_with_the_carriers(self):
        self._module_with_a_shot()
        self.manager.import_references(scene_data="discard")
        self.assertEqual(self._load("AUDIO_FILE_MAP"), {"1": "a.wav"})
        self.assertIsNone(self._load("SHOT_STORE"))
        self.assertEqual(self._carriers(), ["data_internal"])

    def test_a_shot_the_host_has_not_written_yet_survives_either_answer(self):
        """A GUI session writes the shot store on idle, so a script that adds
        a shot and imports in one go still holds it unwritten -- in a scene
        with no carrier yet, whose import therefore adopts the module's.  The
        importer stores it first: merged, both scenes' shots are kept;
        discarded, the host's stays and the module's goes."""
        from mayatk.anim_utils.shots._shots import ShotStore

        intro = {"shot_id": 1, "name": "Intro", "start": 0, "end": 5, "objects": []}
        for scene_data, expected in (
            ("merge", ["HostShot", "Intro"]),
            ("discard", ["HostShot"]),
        ):
            with self.subTest(scene_data=scene_data):
                self._author_module(SHOT_STORE={"shots": [intro]})
                ShotStore.clear_active()
                with patch.object(ShotStore, "_schedule_flush", lambda self: None):
                    ShotStore.active().define_shot("HostShot", 10.0, 20.0)
                    ref_mgr.cmds.file(self.module, reference=True, namespace="MOD")
                    self.manager.import_references(
                        namespace_mode="remove", scene_data=scene_data
                    )
                ShotStore.flush_pending()  # the idle write the session held
                ShotStore.clear_active()
                self.assertEqual(
                    sorted(s.name for s in ShotStore.active().shots), expected
                )

    def test_the_decider_is_asked_with_what_arrives(self):
        self._module_with_a_shot()
        asked = []
        self.manager.import_references(
            scene_data=lambda summary, ns: asked.append((summary, ns)) or "merge"
        )
        ((summary, namespace),) = asked
        self.assertEqual(namespace, "MOD")
        self.assertIn("Audio Clips: 1 entry", summary)

    def test_declining_leaves_the_reference_linked(self):
        self._module_with_a_shot()
        self.manager.import_references(scene_data=lambda summary, ns: None)
        self.assertEqual(
            [r.namespace for r in self.manager.current_references], ["MOD"]
        )
        self.assertEqual(self._load("AUDIO_FILE_MAP"), {"1": "a.wav"})

    def test_a_module_holding_nothing_a_merge_keeps_is_not_asked_about(self):
        """Its own baseline describes itself: no question, and no carrier left."""
        self._author_module(HIERARCHY_BASELINE={"format": 1, "paths": ["door"]})
        self._host_referencing_it()
        self.manager.import_references(
            scene_data=lambda *_: self.fail("nothing to ask about")
        )
        self.assertEqual(self._carriers(), ["data_internal"])
        self.assertIsNone(self._load("HIERARCHY_BASELINE"))

    def test_an_invalid_choice_raises_before_anything_is_imported(self):
        self._module_with_a_shot()
        with self.assertRaises(ValueError):
            self.manager.import_references(scene_data="keep")
        self.assertEqual(
            [r.namespace for r in self.manager.current_references], ["MOD"]
        )


class TestUnsavedChangesPrompt(unittest.TestCase):
    """The unsaved-changes guard OFFERS TO SAVE (Save / Discard / Cancel) instead of the old
    "close anyway?" yes/no, and the row context menu says 'Reopen' on the open scene.

    The two go together: 'Reopen' is the only click in the panel that throws away the current
    session's edits while staying on the same file, so it must be both labelled and guarded.
    """

    @staticmethod
    def _fake_file(modified, scene_name=""):
        """Stand-in for ``cmds.file``: answers the modified-flag query and the scene-name query
        (everything else — the save itself, file-new — is a no-op returning "")."""

        def f(*args, **kwargs):
            if kwargs.get("q") and "modified" in kwargs:
                return modified()
            if kwargs.get("sceneName"):
                return scene_name
            return ""

        return f

    def _make_slot(self, answer="Cancel"):
        slot = ref_mgr.ReferenceManagerSlots.__new__(ref_mgr.ReferenceManagerSlots)
        slot.ui = MockUI()
        slot.ui.tbl000 = QtWidgets.QTableWidget()
        slot.sb = MockSB()
        slot.logger = MockLogger()
        slot.prompts = []

        def message_box(msg, *buttons):
            slot.prompts.append((msg, buttons))
            return answer

        slot.sb.message_box = message_box
        slot.controller = MagicMock()
        slot.controller._is_foreign.return_value = False
        return slot

    def _row(self, slot, path):
        """One row holding *path*, plus a stub context menu carrying the Open button."""
        slot.ui.tbl000.setRowCount(1)
        item = QtWidgets.QTableWidgetItem(os.path.basename(path))
        item.setData(QtCore.Qt.UserRole, path)
        slot.ui.tbl000.setItem(0, 0, item)
        menu = type("MockMenu", (), {})()
        menu.btn_open_scene = QtWidgets.QLabel("Open")
        slot.ui.tbl000.menu = menu
        slot.ui.tbl000.has_menu = True
        slot.controller._context_menu_row = 0
        return menu

    # ---------------------------------------------------------------- the prompt
    def test_clean_scene_never_prompts(self):
        slot = self._make_slot()
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: False),
        ):
            self.assertTrue(slot._confirm_discard_unsaved())
        self.assertEqual(slot.prompts, [])

    def test_prompt_offers_to_save(self):
        slot = self._make_slot("Cancel")
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: True),
        ):
            self.assertFalse(slot._confirm_discard_unsaved())
        msg, buttons = slot.prompts[0]
        self.assertEqual(msg, "The current scene has changes, do you want to save?")
        self.assertEqual(buttons, ("Save", "Discard", "Cancel"))

    def test_discard_proceeds_without_saving(self):
        slot = self._make_slot("Discard")
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: True),
        ):
            self.assertTrue(slot._confirm_discard_unsaved())
        slot.controller._save_open_scene.assert_not_called()
        slot.controller.save_scene.assert_not_called()

    def test_save_flushes_the_named_scene_in_place(self):
        slot = self._make_slot("Save")
        slot.controller._save_open_scene.return_value = True
        scene = os.path.normpath("/proj/scenes/shot.ma")
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: True, scene),
        ):
            self.assertTrue(slot._confirm_discard_unsaved())
        slot.controller._save_open_scene.assert_called_once_with(scene)
        slot.controller.save_scene.assert_not_called()

    def test_a_failed_save_aborts_the_caller(self):
        """The work is still unsaved — proceeding would lose exactly what Save was meant to keep."""
        slot = self._make_slot("Save")
        slot.controller._save_open_scene.return_value = False
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: True, os.path.normpath("/proj/s.ma")),
        ):
            self.assertFalse(slot._confirm_discard_unsaved())

    def test_never_saved_scene_routes_to_the_save_to_workspace_prompt(self):
        slot = self._make_slot("Save")
        state = {"modified": True}

        def named_and_saved():
            state["modified"] = False

        slot.controller.save_scene.side_effect = named_and_saved
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: state["modified"], ""),
        ):
            self.assertTrue(slot._confirm_discard_unsaved())
        slot.controller.save_scene.assert_called_once_with()
        slot.controller._save_open_scene.assert_not_called()

    def test_backing_out_of_the_name_prompt_aborts(self):
        """save_scene returns nothing whether it saved or bailed — the modified flag decides."""
        slot = self._make_slot("Save")
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: True, ""),
        ):
            self.assertFalse(slot._confirm_discard_unsaved())

    # ---------------------------------------------------------------- the label
    def test_open_action_reads_reopen_on_the_open_scene(self):
        slot = self._make_slot()
        scene = os.path.normpath("/proj/scenes/shot.ma")
        menu = self._row(slot, scene)
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: False, scene),
        ):
            slot._label_open_action()
        self.assertEqual(menu.btn_open_scene.text(), "Reopen")

    def test_open_action_reads_open_on_any_other_row(self):
        slot = self._make_slot()
        menu = self._row(slot, os.path.normpath("/proj/scenes/shot.ma"))
        menu.btn_open_scene.setText("Reopen")  # left over from a prior right-click
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(
                lambda: False, os.path.normpath("/proj/scenes/other.ma")
            ),
        ):
            slot._label_open_action()
            self.assertEqual(menu.btn_open_scene.text(), "Open")
            menu.btn_open_scene.setText("Reopen")
            slot.controller._context_menu_row = None  # right-clicked empty space
            slot._label_open_action()
        self.assertEqual(menu.btn_open_scene.text(), "Open")

    # ---------------------------------------------------------------- the guard on Open
    def test_reopen_aborts_when_the_prompt_is_cancelled(self):
        slot = self._make_slot("Cancel")
        scene = os.path.normpath("/proj/scenes/shot.ma")
        self._row(slot, scene)
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: True, scene),
        ):
            slot.btn_open_scene()
        slot.controller.open_scene.assert_not_called()

    def test_reopen_proceeds_once_the_prompt_is_answered(self):
        slot = self._make_slot("Discard")
        scene = os.path.normpath("/proj/scenes/shot.ma")
        self._row(slot, scene)
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(lambda: True, scene),
        ):
            slot.btn_open_scene()
        slot.controller.open_scene.assert_called_once_with(scene)

    def test_open_icon_on_another_row_is_guarded_too(self):
        """The Open column opens a DIFFERENT scene over the current one — same loss, same guard."""
        slot = self._make_slot("Cancel")
        other = os.path.normpath("/proj/scenes/other.ma")
        self._row(slot, other)
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(
                lambda: True, os.path.normpath("/proj/scenes/shot.ma")
            ),
        ):
            slot._open_scene_at_row(0, 2)
        slot.controller.open_scene.assert_not_called()

    def test_a_menuless_table_is_not_given_a_menu(self):
        """uitk builds a table's menu lazily on first ``.menu`` access — the labeller must
        check ``has_menu`` rather than reach for ``.menu`` and construct one."""
        slot = self._make_slot()
        created = []

        class _LazyTable(QtWidgets.QTableWidget):
            has_menu = False

            @property
            def menu(self):
                created.append(True)
                raise AssertionError("labelling must not build the menu")

        slot.ui.tbl000 = _LazyTable()
        slot._label_open_action()
        self.assertEqual(created, [])

    def test_a_non_maya_format_scene_saves_through_the_workspace_prompt(self):
        """An .fbx row opens through the translator and keeps the .fbx as its scene name;
        SCENE_SAVE_TYPES holds only .ma/.mb, so an in-place flush would raise a bare KeyError."""
        slot = self._make_slot("Save")
        state = {"modified": True}
        slot.controller.save_scene.side_effect = lambda: state.__setitem__(
            "modified", False
        )
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_file(
                lambda: state["modified"], os.path.normpath("/proj/scenes/prop.fbx")
            ),
        ):
            self.assertTrue(slot._confirm_discard_unsaved())
        slot.controller.save_scene.assert_called_once_with()
        slot.controller._save_open_scene.assert_not_called()


class _StubManager:
    """Minimal stand-in so remove_references can be exercised without a live scene."""

    remove_references = ref_mgr.ReferenceManager.remove_references

    def __init__(self, refs):
        self.current_references = refs
        self.logger = MagicMock()


class TestReferenceRemoval(unittest.TestCase):
    """The panel side of the file-less reference node found in the PROPS assembly.

    Opening ROOM_ENV and then referencing PROPS_ASSEMBLY — which references
    ROOM_ENV itself — leaves Maya a reference node with no file behind it, which
    threw out of every consumer of .path and killed Unreference All on its first
    removal. The screen itself lives in EnvUtils.list_reference_nodes (covered live
    in test_env_utils.py); what is checked here is that the panel goes through it and
    that a removal Maya refuses is reported rather than silently swallowed.
    """

    def _controller(self, failed):
        controller = ref_mgr.ReferenceManagerController.__new__(
            ref_mgr.ReferenceManagerController
        )
        slot = MockSlot()
        controller.slot = slot
        controller.sb = slot.sb
        controller.ui = slot.ui
        controller.logger = MockLogger()
        controller.refresh_file_list = MagicMock()
        controller.remove_references = MagicMock(return_value=failed)
        controller.sb.message_box = MagicMock()
        return controller

    def test_list_file_refs_wraps_the_shared_screen(self):
        """It must not re-implement the screen — a second copy is what let the two
        diverge in the first place."""
        with patch.object(
            ref_mgr.EnvUtils,
            "list_reference_nodes",
            return_value=["ARN", "BRN"],
        ) as screen:
            refs = ref_mgr._ReferenceManagerInternal._list_file_refs()

        screen.assert_called_once_with()  # default = top level only
        self.assertEqual([r._ref_node for r in refs], ["ARN", "BRN"])
        self.assertTrue(all(isinstance(r, ref_mgr._FileRef) for r in refs))

    def test_one_unremovable_reference_does_not_strand_the_rest(self):
        """Unreference All must keep going past a reference Maya refuses to remove,
        and hand the failures back rather than report a silent partial success."""
        bad, good = MagicMock(), MagicMock()
        bad._ref_node, good._ref_node = "BAD_RN", "GOOD_RN"
        bad.remove.side_effect = RuntimeError('File not found: ""')
        stub = _StubManager([bad, good])

        failed = stub.remove_references()

        good.remove.assert_called_once_with()
        self.assertEqual(failed, [bad])
        self.assertTrue(stub.logger.warning.called)

    def test_removal_returns_empty_when_every_reference_went(self):
        stub = _StubManager([MagicMock(), MagicMock()])
        self.assertEqual(stub.remove_references(), [])

    def test_namespace_filter_still_scopes_the_removal(self):
        keep, drop = MagicMock(), MagicMock()
        keep.namespace, drop.namespace = "KEEP", "DROP"
        stub = _StubManager([keep, drop])

        stub.remove_references("DROP")

        drop.remove.assert_called_once_with()
        keep.remove.assert_not_called()

    def test_unreference_all_names_what_it_could_not_remove(self):
        """The table is refreshed first, so those rows read as still referenced —
        saying nothing would look exactly like Unreference All doing nothing."""
        stuck = MagicMock()
        stuck.label = "ROOM_ENV"
        controller = self._controller([stuck])

        controller.unreference_all()

        controller.refresh_file_list.assert_called_once_with()
        controller.sb.message_box.assert_called_once()
        self.assertIn("ROOM_ENV", controller.sb.message_box.call_args[0][0])

    def test_a_broken_reference_still_has_a_name_to_report(self):
        """.namespace raises on a file-less reference — the one kind most likely to be
        in a failure message — so the label must fall back to the node name."""
        ref = ref_mgr._FileRef("PROPS_ASSEMBLY:ROOM_ENVRN")
        with patch.object(
            ref_mgr.cmds,
            "referenceQuery",
            create=True,
            side_effect=RuntimeError("is not associated with a reference file"),
        ):
            self.assertEqual(ref.label, "PROPS_ASSEMBLY:ROOM_ENVRN")

    def test_unreference_all_stays_quiet_when_everything_went(self):
        controller = self._controller([])
        controller.unreference_all()
        controller.sb.message_box.assert_not_called()


if __name__ == "__main__":
    unittest.main()
