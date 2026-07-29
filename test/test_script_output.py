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
        """Import script_output with Maya/PyMel modules mocked when needed."""
        if not self._qt_available:
            self.skipTest("qtpy not available")

        from qtpy import QtWidgets
        import importlib

        # When real Maya is available (run_tests.py path), import directly —
        # mocking sys.modules would corrupt downstream imports walked by
        # mayatk's package resolver and trip shibokensupport's import hook.
        real_maya_available = "maya.cmds" in sys.modules and not isinstance(
            sys.modules["maya.cmds"], MagicMock
        )
        if real_maya_available:
            module = importlib.import_module("mayatk.env_utils.script_output")
            return importlib.reload(module)

        # Mock Maya + PyMel modules so we can import outside Maya
        mock_maya = MagicMock()
        mock_maya.OpenMayaUI = MagicMock()

        mock_maya_app = MagicMock()
        mock_maya_app.general = MagicMock()
        mock_maya_app.general.mayaMixin = MagicMock()

        class DummyMixin(QtWidgets.QWidget):
            pass

        mock_maya_app.general.mayaMixin.MayaQWidgetDockableMixin = DummyMixin

        modules = {
            "maya": mock_maya,
            "maya.OpenMayaUI": mock_maya.OpenMayaUI,
            "maya.app": mock_maya_app,
            "maya.app.general": mock_maya_app.general,
            "maya.app.general.mayaMixin": mock_maya_app.general.mayaMixin,
            "shiboken6": MagicMock(),
        }

        with unittest.mock.patch.dict(sys.modules, modules):
            module = importlib.import_module("mayatk.env_utils.script_output")
            return importlib.reload(module)

    def test_ctrl_c_copies_selected_text(self):
        """Ctrl+C should copy selected text from ScriptOutput."""
        if not self._qt_available:
            self.skipTest("qtpy not available")

        from qtpy import QtGui, QtCore, QtTest, QtWidgets

        # The OS clipboard is a machine-global resource: any other process can
        # hold it open, and the set then fails silently so ``text()`` comes back
        # empty — a false failure that says nothing about the copy path under
        # test. (This suite runs in a GUI Maya, i.e. a real platform, so there
        # is no in-memory offscreen clipboard to fall back on.) Probe rather
        # than key off the platform, so a genuine Ctrl+C regression still fails.
        _cb = QtWidgets.QApplication.clipboard()
        _cb.setText("mtk-clipboard-probe")
        if _cb.text() != "mtk-clipboard-probe":
            self.skipTest(
                "OS clipboard is unavailable in this environment "
                "(another process holds it)."
            )

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

    def test_show_is_not_a_shadowing_classmethod(self):
        """Regression: ``ScriptConsole`` is a ``MayaQWidgetDockableMixin``
        QWidget, and the mixin's ``setVisible()`` calls ``self.show()``
        INTERNALLY (the virtual ``setVisible`` is invoked by C++ and dispatched
        back to Python by shiboken). A ``show`` *classmethod* on the class would
        shadow the mixin's instance ``show``, so ``self.show()`` resolves to a
        workspace-control facade that re-enters ``show_console`` instead of the
        real ``QWidget.setVisible`` the mixin means to call — a classmethod
        cannot stand in for the instance method Qt/Maya invoke. Guard that
        ``show`` resolves to an inherited instance method, never a classmethod
        facade. Needs no widget instance, so it runs under the mock path too.
        """
        if not self._qt_available:
            self.skipTest("qtpy not available")

        import inspect

        module = self._import_script_output()
        raw_show = inspect.getattr_static(module.ScriptConsole, "show")
        self.assertNotIsInstance(
            raw_show,
            classmethod,
            "ScriptConsole.show must not be a classmethod — it shadows the "
            "MayaQWidgetDockableMixin.show that the mixin's setVisible() calls "
            "internally, breaking the uiScript restore across sessions.",
        )

    def test_show_console_first_creation_binds_mixin_show(self):
        """The first-time creation path inside ``show_console`` must call the
        *mixin* ``show`` with the docking kwargs (``dockable=``,
        ``workspaceControlName=``, ``uiScript=``, ``retain=``) so the panel is
        created as a dockable ``workspaceControl`` (and registers its restore
        ``uiScript``). It calls ``MayaQWidgetDockableMixin.show`` explicitly — see
        the sibling ``test_show_is_not_a_shadowing_classmethod`` for why ``show``
        itself must never be overridden on this class.
        """
        if not self._qt_available:
            self.skipTest("qtpy not available")

        # ScriptConsole subclasses the REAL MayaQWidgetDockableMixin (a
        # non-QWidget mixin). Under the no-Maya mock path that mixin is a
        # QWidget, so ScriptConsole ends up with two QWidget bases and its
        # construction native-crashes (access violation). This module is
        # GUI_REQUIRED, so gate on a real Maya being present.
        real_maya = "maya.cmds" in sys.modules and not isinstance(
            sys.modules["maya.cmds"], MagicMock
        )
        if not real_maya:
            self.skipTest("requires real Maya (mock mixin => double-QWidget crash)")

        import unittest.mock as mock

        module = self._import_script_output()
        ScriptConsole = module.ScriptConsole
        mixin = module.MayaQWidgetDockableMixin

        ScriptConsole._instance = None
        captured = {}

        def spy_mixin_show(self, *args, **kwargs):
            captured["kwargs"] = kwargs

        try:
            with (
                mock.patch.object(mixin, "show", spy_mixin_show),
                mock.patch.object(
                    ScriptConsole, "_mirror_script_editor_output", lambda self: None
                ),
                mock.patch.object(module.cmds, "workspaceControl", return_value=False),
                mock.patch.object(
                    module.QtCore.QTimer, "singleShot", lambda *a, **k: None
                ),
            ):
                # Must NOT raise TypeError — the whole point of the fix.
                result = ScriptConsole.show_console()

            self.assertIn("kwargs", captured, "dockable-mixin show() was never invoked")
            self.assertTrue(
                captured["kwargs"].get("dockable"),
                "creation path did not pass dockable=True to the mixin show()",
            )
            self.assertEqual(
                captured["kwargs"].get("workspaceControlName"),
                ScriptConsole.WORKSPACE_CONTROL_NAME,
            )
            self.assertIs(result, ScriptConsole._instance)
        finally:
            inst = ScriptConsole._instance
            ScriptConsole._instance = None
            if inst is not None:
                try:
                    inst.deleteLater()
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
