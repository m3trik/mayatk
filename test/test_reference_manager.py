# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils.reference_manager module
"""
import unittest
import os
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

        def setRowCount(self, count):
            current = len(self._rows)
            if count < current:
                self._rows = self._rows[:count]
            else:
                for _ in range(count - current):
                    self._rows.append([None, None])  # 2 columns

        def rowCount(self):
            return len(self._rows)

        def item(self, row, col):
            if 0 <= row < len(self._rows):
                return self._rows[row][col]
            return None

        def setItem(self, row, col, item):
            if 0 <= row < len(self._rows):
                self._rows[row][col] = item

        def isSortingEnabled(self):
            return self._sorting

        def setSortingEnabled(self, val):
            self._sorting = val

        def apply_formatting(self):
            pass

        def insertRow(self, row):
            self._rows.insert(row, [None, None])

        def removeRow(self, row):
            self._rows.pop(row)

        def clearContents(self):
            self._rows = []

        def blockSignals(self, block):
            return False


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


if __name__ == "__main__":
    unittest.main()
