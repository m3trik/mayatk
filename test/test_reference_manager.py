# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils.reference_manager module
"""
import unittest
import os
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

    def message_box(self, msg):
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
        (e.g. 'CXAL, Speedrun') were hidden when filtering 'Speedrun'.
        Fixed: 2026-03-03
        """
        t = self.controller.ui.tbl000

        # Set an active filter that does NOT match filenames
        self.controller._active_filter_text = "Speedrun"
        self.controller._active_ignore_case = True

        files = ["C5M_FCR_ACTION.ma", "C5M_FCR_OTHER.ma"]
        paths = ["/ws/C5M_FCR_ACTION.ma", "/ws/C5M_FCR_OTHER.ma"]

        self.controller.update_table(files, paths)

        # Neither filename matches 'Speedrun', so both hidden initially
        self.assertTrue(t.isRowHidden(0))
        self.assertTrue(t.isRowHidden(1))

        # Now simulate notes on row 0 matching the filter
        notes_item = t.item(0, 4)  # Notes is column 4 (col 3 is the display-mode action column)
        self.assertIsNotNone(notes_item)
        notes_item.setText("CXAL, Speedrun")

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

        files = ["C5M_FCR_ACTION.ma", "C5M_FCR_OTHER.ma"]
        paths = ["/ws/C5M_FCR_ACTION.ma", "/ws/C5M_FCR_OTHER.ma"]

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

        files = ["C5M_FCR_ACTION.ma"]
        paths = ["/ws/C5M_FCR_ACTION.ma"]

        self.controller.update_table(files, paths)

        # Set notes that would match
        notes_item = t.item(0, 4)  # Notes is column 4 (col 3 is the display-mode action column)
        notes_item.setText("CXAL, Speedrun")
        self.controller.update_table(files, paths)

        # Even though notes match, include_notes is False so row should be hidden
        self.assertTrue(
            t.isRowHidden(0),
            "Row should be hidden when include_notes is disabled",
        )

    def test_is_foreign_classifies_blend_only(self):
        """_is_foreign flags .blend (the cross-DCC row) and nothing else — .fbx is NATIVE
        on this side (Maya references it directly), so it must not classify as foreign."""
        C = ref_mgr.ReferenceManagerController
        self.assertTrue(C._is_foreign("C:/proj/mesh.blend"))
        self.assertFalse(C._is_foreign("C:/proj/scene.ma"))
        self.assertFalse(C._is_foreign("C:/proj/scene.mb"))
        self.assertFalse(C._is_foreign("C:/proj/prop.fbx"))
        self.assertFalse(C._is_foreign(""))

    def test_include_type_classification_is_the_inverse_of_blendertk(self):
        """The panel's file-type split is the mirror of the Blender panel's: this side's
        natives are .ma/.mb/.fbx and its only foreign type is .blend, and every include
        toggle names one of the four shared types."""
        S = ref_mgr.ReferenceManagerSlots
        self.assertEqual(S._INCLUDE_TYPES, ("ma", "mb", "fbx", "blend"))
        self.assertEqual(S.NATIVE_EXTENSIONS, (".ma", ".mb", ".fbx"))
        self.assertEqual(S.FOREIGN_EXTENSIONS, (".blend",))
        self.assertEqual(S._INCLUDE_DEFAULTS, (".ma", ".mb"))
        self.assertEqual(
            set(f".{t}" for t in S._INCLUDE_TYPES),
            set(S.NATIVE_EXTENSIONS) | set(S.FOREIGN_EXTENSIONS),
            "every include toggle must classify as native or foreign",
        )

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
            t.actions.get(0, 2), "default", "open col clickable (bakes + opens) for a .blend"
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
        with patch.object(
            type(self.controller),
            "current_references",
            new_callable=PropertyMock,
            return_value=[ref],
        ), patch.object(
            ref_mgr.ReferenceManagerController,
            "_bake_source_key",
            staticmethod(
                lambda p: os.path.normcase(os.path.normpath("C:/proj/mesh.blend"))
            ),
        ), patch.object(
            ref_mgr.ReferenceManagerController,
            "get_reference_display_mode",
            lambda self, r: "off",
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
        with patch.object(
            type(self.controller),
            "current_references",
            new_callable=PropertyMock,
            return_value=[ref],
        ), patch.object(
            ref_mgr.ReferenceManagerController,
            "_bake_source_key",
            staticmethod(
                lambda p: os.path.normcase(os.path.normpath("C:/proj/mesh.blend"))
            ),
        ), patch.object(
            ref_mgr.ReferenceManagerController,
            "remove_references",
            lambda self, ns: removed.append(ns),
        ), patch.object(
            ref_mgr.ReferenceManagerController, "_sync_reference_icons", lambda self: None
        ):
            self.controller.handle_item_selection()

        self.assertEqual(removed, [], "a bake-backed reference must not be auto-removed")

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
    Files with matching notes (e.g. "CXAL, Speedrun") were excluded when
    searching for "*Speedrun*" unless the filename also contained the term.
    Fixed: 2026-03-03
    """

    def test_wildcard_matches_note_segment(self):
        """'*Speedrun*' should match 'CXAL, Speedrun' (comma-delimited notes)."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "CXAL, Speedrun", "*Speedrun*"
            )
        )

    def test_wildcard_matches_full_notes_string(self):
        """'*CXAL*' should match 'CXAL, Speedrun' via the full string."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter("CXAL, Speedrun", "*CXAL*")
        )

    def test_exact_segment_match(self):
        """Exact note segment 'Speedrun' should match without wildcards."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter("CXAL, Speedrun", "Speedrun")
        )

    def test_case_insensitive_by_default(self):
        """Matching should be case-insensitive by default."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "CXAL, Speedrun", "*speedrun*"
            )
        )

    def test_case_sensitive_when_specified(self):
        """Case-sensitive mode should not match mismatched case."""
        self.assertFalse(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "CXAL, Speedrun", "*speedrun*", ignore_case=False
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
        """Multi-pattern filter 'CXAL,Hero' should match notes containing either."""
        self.assertTrue(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "Hero, Speedrun", "CXAL,Hero"
            )
        )

    def test_no_match_returns_false(self):
        """Filter that doesn't match any note segment should return False."""
        self.assertFalse(
            ref_mgr.ReferenceManager._matches_notes_filter(
                "CXAL, Speedrun", "*LookDev*"
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
            ref_mgr.ReferenceManager._matches_notes_filter("CXAL, Speedrun", "")
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
        with patch.object(
            ref_mgr.ReferenceManagerController,
            "current_working_dir",
            new_callable=PropertyMock,
        ) as cwd, patch("os.path.isdir", return_value=True):
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
        with patch.object(ref_mgr.os.path, "exists", return_value=True), patch.object(
            ref_mgr.cmds, "file", create=True
        ) as mock_file:
            result = controller.open_scene("/proj/scenes/shot.ma", set_workspace=False)

        self.assertTrue(result)
        # The open call, then an explicit clear of the load-time dirty flag.
        mock_file.assert_any_call("/proj/scenes/shot.ma", open=True, force=True)
        mock_file.assert_any_call(modified=False)


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
        slot.ui.header = MagicMock()
        slot.ui.header.menu = menu
        return controller

    def test_wiring_and_preview_live_on_controller_not_slots(self):
        # header_init (on Slots) reaches these via self.controller; guard that split.
        self.assertTrue(hasattr(ref_mgr.ReferenceManagerController, "_folder_structure_preview"))
        self.assertTrue(hasattr(ref_mgr.ReferenceManagerController, "_wire_structure_tooltip"))
        self.assertFalse(hasattr(ref_mgr.ReferenceManagerSlots, "_wire_structure_tooltip"))

    def test_preview_resolves_tokens_against_live_context(self):
        controller = self._make_controller(pattern="{scenes}/{name}")
        with patch.object(
            ref_mgr.ReferenceManagerController,
            "current_working_dir",
            new_callable=PropertyMock,
            return_value="C:/proj/MyGame",
        ), patch.object(
            ref_mgr.cmds, "workspace", create=True, return_value="scenes"
        ), patch.object(
            ref_mgr.cmds, "file", create=True, return_value=""
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
        with patch.object(
            ref_mgr.ReferenceManagerController,
            "current_working_dir",
            new_callable=PropertyMock,
            return_value="C:/proj/MyGame",
        ), patch.object(
            ref_mgr.cmds, "workspace", create=True, return_value="scenes"
        ), patch.object(
            ref_mgr.cmds, "file", create=True, return_value=""
        ):
            html = controller._folder_structure_preview()

        # {scene} is corrected locally for resolution AND surfaced as a typo note.
        self.assertIn("did you mean", html)
        self.assertIn("{scenes}", html)


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
        with patch.object(
            ref_mgr.cmds,
            "file",
            create=True,
            side_effect=self._fake_cmds_file(calls, fail_save),
        ), patch.object(
            ref_mgr.os.path, "exists", return_value=False
        ), patch.object(
            ref_mgr.os,
            "rename",
            side_effect=lambda a, b: calls.append(("rename", a, b)),
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

        with patch.object(
            ref_mgr.cmds, "file", create=True, side_effect=self._fake_cmds_file(calls)
        ), patch.object(
            ref_mgr.os.path, "exists", side_effect=lambda p: p == taken
        ), patch.object(
            ref_mgr.os,
            "rename",
            side_effect=lambda a, b: calls.append(("rename", a, b)),
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
    """The header-menu Namespace choice must reach ``import_references`` from BOTH
    unlink entry points, and be named in the confirm prompt so it is never a hidden
    setting that silently changes what an unlink does to the scene."""

    @staticmethod
    def _make_controller(combo_text=None, answer="Yes"):
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
        if combo_text is None:
            slot.ui.header = None  # menu not built yet
        else:
            combo = type("C", (), {"currentText": lambda self: combo_text})()
            menu = type("M", (), {"cmb_unlink_namespace": combo})()
            slot.ui.header = type("H", (), {"menu": menu})()
        return controller

    @staticmethod
    def _unwrapped(name):
        """The undecorated method — ``block_table_selection_method`` touches tbl000."""
        return getattr(ref_mgr.ReferenceManagerController, name).__wrapped__

    def test_every_combo_item_maps_to_a_supported_mode(self):
        C = ref_mgr.ReferenceManagerController
        self.assertEqual(
            set(C._UNLINK_NAMESPACE_MODES.values()),
            set(ref_mgr.ReferenceManager.NAMESPACE_MODES),
            "the combo must cover every mode the core supports, and invent none",
        )
        # Every mode is describable in the confirm prompt.
        self.assertEqual(
            set(C._UNLINK_MODE_LABELS), set(ref_mgr.ReferenceManager.NAMESPACE_MODES)
        )

    def test_combo_items_in_the_ui_match_the_mode_map(self):
        """The strings added to the combo are the map's KEYS — a typo on either side
        would silently fall back to 'remove' instead of failing."""
        import ast
        import re

        with open(ref_mgr.__file__, encoding="utf-8") as fh:
            src = fh.read()
        block = re.search(
            r"addItems=(\[[^\]]*\]),\s*setCurrentIndex=(\d+),[^\n]*\s*"
            r'setObjectName="cmb_unlink_namespace"',
            src,
        )
        self.assertIsNotNone(block, "namespace combo: addItems -> index -> objectName")
        items = ast.literal_eval(block.group(1))
        self.assertEqual(
            set(items), set(ref_mgr.ReferenceManagerController._UNLINK_NAMESPACE_MODES)
        )
        # uitk persists a combo by INDEX, so the default moves via setCurrentIndex
        # rather than by reordering items: pin that the default IS 'remove'.
        self.assertEqual(
            ref_mgr.ReferenceManagerController._UNLINK_NAMESPACE_MODES[
                items[int(block.group(2))]
            ],
            "remove",
        )

    def test_mode_defaults_to_remove_without_a_menu(self):
        controller = self._make_controller(combo_text=None)
        self.assertEqual(controller._unlink_namespace_mode(), "remove")

    def test_unknown_combo_text_falls_back_to_remove(self):
        controller = self._make_controller(combo_text="Namespace: Something Else")
        self.assertEqual(controller._unlink_namespace_mode(), "remove")

    def test_each_entry_point_forwards_the_selected_mode(self):
        for text, expected in ref_mgr.ReferenceManagerController._UNLINK_NAMESPACE_MODES.items():
            controller = self._make_controller(combo_text=text)
            self._unwrapped("unlink_all")(controller)
            self._unwrapped("unlink_references")(controller, ["ns_a", "ns_b"])
            self.assertEqual(
                [c.get("namespace_mode") for c in controller.calls],
                [expected, expected],
                f"{text!r} must reach import_references from both entry points",
            )
            # The row-scoped call stays scoped to the namespaces it was handed.
            self.assertEqual(controller.calls[1].get("namespaces"), ["ns_a", "ns_b"])

    def test_prompt_names_the_mode_that_will_be_applied(self):
        for text, mode in ref_mgr.ReferenceManagerController._UNLINK_NAMESPACE_MODES.items():
            controller = self._make_controller(combo_text=text)
            self._unwrapped("unlink_all")(controller)
            self._unwrapped("unlink_references")(controller, ["ns_a"])
            label = ref_mgr.ReferenceManagerController._UNLINK_MODE_LABELS[mode]
            for prompt in controller.prompts:
                self.assertIn(label, prompt)

    def test_declining_the_prompt_imports_nothing(self):
        controller = self._make_controller(combo_text="Namespace: Keep", answer="No")
        self._unwrapped("unlink_all")(controller)
        self._unwrapped("unlink_references")(controller, ["ns_a"])
        self.assertEqual(controller.calls, [])


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
                for n in ref_mgr.cmds.namespaceInfo("ASSET", listOnlyDependencyNodes=True)
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
        self.assertEqual([r.namespace for r in self.manager.current_references], ["ASSET"])

    def test_deprecated_bool_form_still_maps_to_the_old_behaviour(self):
        """``remove_namespace`` predates the modes; a pinned caller must not break."""
        self._reference()
        self.manager.import_references(remove_namespace=False)
        self.assertEqual(self._transforms(), ["ASSET:asset_child", "ASSET:asset_root"])

        ref_mgr.cmds.file(new=True, force=True)
        self._reference()
        self.manager.import_references(remove_namespace=True)
        self.assertEqual(self._transforms(), ["asset_child", "asset_root"])


if __name__ == "__main__":
    unittest.main()
