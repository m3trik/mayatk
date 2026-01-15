# !/usr/bin/python
# coding=utf-8
"""
Tests for Script Output widget.
"""
import sys
import unittest
from unittest.mock import MagicMock


class TestScriptOutput(unittest.TestCase):
    """Validate that Ctrl+C copies selected text in ScriptOutput."""

    @classmethod
    def setUpClass(cls):
        try:
            from qtpy import QtWidgets

            cls._qt_available = True
            cls._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        except Exception:
            cls._qt_available = False
            cls._app = None

    def _import_script_output(self):
        """Import script_output with Maya/PyMel modules mocked."""
        if not self._qt_available:
            self.skipTest("qtpy not available")

        # Mock Maya + PyMel modules so we can import outside Maya
        mock_maya = MagicMock()
        mock_maya.OpenMayaUI = MagicMock()

        mock_maya_app = MagicMock()
        mock_maya_app.general = MagicMock()
        mock_maya_app.general.mayaMixin = MagicMock()

        from qtpy import QtWidgets

        class DummyMixin(QtWidgets.QWidget):
            pass

        mock_maya_app.general.mayaMixin.MayaQWidgetDockableMixin = DummyMixin

        modules = {
            "maya": mock_maya,
            "maya.OpenMayaUI": mock_maya.OpenMayaUI,
            "maya.app": mock_maya_app,
            "maya.app.general": mock_maya_app.general,
            "maya.app.general.mayaMixin": mock_maya_app.general.mayaMixin,
            "pymel": MagicMock(),
            "pymel.core": MagicMock(),
            "shiboken6": MagicMock(),
        }

        with unittest.mock.patch.dict(sys.modules, modules):
            import importlib

            module = importlib.import_module("mayatk.env_utils.script_output")
            return importlib.reload(module)

    def test_ctrl_c_copies_selected_text(self):
        """Ctrl+C should copy selected text from ScriptOutput."""
        if not self._qt_available:
            self.skipTest("qtpy not available")

        from qtpy import QtGui, QtCore, QtTest, QtWidgets

        module = self._import_script_output()
        ScriptOutput = module.ScriptOutput

        widget = ScriptOutput()
        widget.setPlainText("Hello\nWorld")

        cursor = widget.textCursor()
        cursor.setPosition(0)
        cursor.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor, 5)
        widget.setTextCursor(cursor)

        widget.show()
        widget.setFocus()

        QtTest.QTest.keyClick(widget, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)

        clipboard = QtWidgets.QApplication.clipboard()
        self.assertEqual(clipboard.text(), "Hello")


if __name__ == "__main__":
    unittest.main()
