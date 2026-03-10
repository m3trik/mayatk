# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils.reference_manager module
"""
import unittest
import os
from unittest.mock import patch, MagicMock
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
            pass

    class QTableWidget:
        def __init__(self):
            self._rows = []
            self._sorting = False
            self._cell_widgets = {}
            self._hidden_rows = set()
            self.actions = type("Actions", (), {"set": lambda *a, **kw: None})()

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

        # Patch pm.sceneName so update_table doesn't need Maya
        # pm may not exist as a module attr (try/except import), so use create=True
        self._pm_patcher = patch.object(ref_mgr, "pm", create=True)
        self._mock_pm = self._pm_patcher.start()
        self._mock_pm.sceneName.return_value = ""

    def tearDown(self):
        self._pm_patcher.stop()

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
        notes_item = t.item(0, 3)
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
        notes_item = t.item(0, 3)
        notes_item.setText("CXAL, Speedrun")
        self.controller.update_table(files, paths)

        # Even though notes match, include_notes is False so row should be hidden
        self.assertTrue(
            t.isRowHidden(0),
            "Row should be hidden when include_notes is disabled",
        )


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

        self._pm_patcher = patch.object(ref_mgr, "pm", create=True)
        self._mock_pm = self._pm_patcher.start()
        self._mock_pm.sceneName.return_value = ""

    def tearDown(self):
        self._pm_patcher.stop()

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


if __name__ == "__main__":
    unittest.main()
