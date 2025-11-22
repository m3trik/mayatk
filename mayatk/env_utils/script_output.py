# !/usr/bin/python
# coding=utf-8
from typing import List, Optional
from qtpy import QtWidgets, QtGui, QtCore
from maya.OpenMayaUI import MQtUtil
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
from shiboken6 import wrapInstance
import pymel.core as pm


class ScriptHighlightRule:
    def __init__(
        self,
        color: tuple[int, int, int],
        pattern: str,
        bg_color: Optional[tuple[int, int, int]] = None,
        bold: bool = False,
        italic: bool = False,
    ):
        self.pattern = QtCore.QRegularExpression(pattern)
        self.format = QtGui.QTextCharFormat()
        self.format.setForeground(QtGui.QColor(*color))
        if bg_color:
            self.format.setBackground(QtGui.QColor(*bg_color))
        font = QtGui.QFont("Courier New", 9)
        font.setBold(bold)
        font.setItalic(italic)
        self.format.setFont(font)


class ScriptHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, doc: QtGui.QTextDocument, rules: List[ScriptHighlightRule]):
        super().__init__(doc)
        self.rules = rules

    def highlightBlock(self, text: str) -> None:
        for rule in self.rules:
            match_iter = rule.pattern.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                self.setFormat(
                    match.capturedStart(), match.capturedLength(), rule.format
                )


class ScriptOutput(QtWidgets.QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFontFamily("Courier New")
        # Ensure the widget reliably gets/keeps focus and supports selection
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard
        )
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

        # Set up syntax highlighting
        rules = [
            ScriptHighlightRule((90, 90, 90), r"(//|#).+"),  # comment
            ScriptHighlightRule((205, 200, 120), r".*\bWarning\b.*"),  # warning
            ScriptHighlightRule((165, 75, 75), r".*\bError\b.*"),  # error
            ScriptHighlightRule((115, 215, 150), r".*\bResult\b.*"),  # result
            ScriptHighlightRule((130, 220, 210), r".*\bInfo\b.*"),  # info (pastel teal)
        ]
        self.highlighter = ScriptHighlighter(self.document(), rules)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        """Let Qt handle all key events including copy"""
        # Let parent handle all key events - Qt's built-in copy should work
        super().keyPressEvent(event)

    def _clear_script_editor(self):
        """Clear both the widget and the actual Maya Script Editor output"""
        # Clear this widget
        self.clear()

        # Clear the actual Maya Script Editor
        try:
            pm.mel.eval("cmdScrollFieldReporter -edit -clear cmdScrollFieldReporter1;")
        except Exception as e:
            print(f"Failed to clear Maya Script Editor: {e}")

    def _context_menu(self, pos: QtCore.QPoint):
        # Create a simple context menu
        menu = QtWidgets.QMenu(self)

        menu.addAction("Clear", self._clear_script_editor)
        menu.addAction("Copy", self.copy)  # Qt's built-in copy

        menu.addSeparator()  # Always ensure Script Editor is present
        ws_name = "scriptEditorPanel1Window"
        if not pm.workspaceControl(ws_name, exists=True):
            pm.mel.eval("ScriptEditor;")
            pm.workspaceControl(ws_name, edit=True, visible=True)

        # Get current echo state using MEL for more reliability
        try:
            # Use the correct MEL command for echo all commands
            echo_all = bool(pm.mel.eval("commandEcho -query -state"))
        except Exception:
            echo_all = False

        echo_action = menu.addAction("Echo All Commands")
        echo_action.setCheckable(True)
        echo_action.setChecked(echo_all)

        def on_toggled(checked):
            try:
                # Use MEL commands for more reliable operation
                if checked:
                    pm.mel.eval("commandEcho -state on")
                else:
                    pm.mel.eval("commandEcho -state off")

                print(f"Echo All Commands: {'ON' if checked else 'OFF'}")

                # Verify the change took effect
                actual_state = bool(pm.mel.eval("commandEcho -query -state"))

                if actual_state != checked:
                    print(
                        f"Warning: Echo All Commands may not have updated correctly. Current state: {'ON' if actual_state else 'OFF'}"
                    )
                    # Update checkbox to reflect actual state
                    echo_action.blockSignals(True)
                    echo_action.setChecked(actual_state)
                    echo_action.blockSignals(False)

            except Exception as e:
                print(f"Failed to toggle Echo All Commands: {e}")
                # Revert checkbox state on failure
                echo_action.blockSignals(True)
                echo_action.setChecked(not checked)
                echo_action.blockSignals(False)

        echo_action.toggled.connect(on_toggled)
        menu.exec(self.mapToGlobal(pos))


class ScriptConsole(MayaQWidgetDockableMixin, QtWidgets.QDialog):
    """Dockable window that live-mirrors Maya's Script Editor output,
    with syntax highlighting, minimal padding, and an Echo All Commands toggle.
    Usage: ScriptConsole.show_console()
    """

    WORKSPACE_CONTROL_NAME = "ScriptConsoleWorkspaceControl"
    _instance: Optional["ScriptConsole"] = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ScriptConsole")
        self.setWindowTitle("Script Output")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.output = ScriptOutput()
        layout.addWidget(self.output)
        self.resize(800, 300)
        self._mirror_script_editor_output()

    @staticmethod
    def _get_script_editor_output_widget() -> Optional[QtWidgets.QPlainTextEdit]:
        ptr = MQtUtil.findControl("cmdScrollFieldReporter1")
        if ptr:
            return wrapInstance(int(ptr), QtWidgets.QPlainTextEdit)
        panel_ptr = MQtUtil.findControl("scriptEditorPanel1Window")
        if not panel_ptr:
            return None
        panel = wrapInstance(int(panel_ptr), QtWidgets.QWidget)
        for widget in panel.findChildren(QtWidgets.QPlainTextEdit):
            if widget.objectName() == "cmdScrollFieldReporter1":
                return widget
        return None

    @staticmethod
    def _ensure_script_editor_initialized() -> bool:
        ws_name = "scriptEditorPanel1Window"
        was_open = pm.workspaceControl(ws_name, exists=True)
        if not was_open or not pm.workspaceControl(ws_name, query=True, visible=True):
            pm.mel.eval("ScriptEditor;")
            pm.workspaceControl(ws_name, edit=True, visible=True)
        return was_open

    @staticmethod
    def _hide_script_editor() -> None:
        ws_name = "scriptEditorPanel1Window"
        try:
            pm.workspaceControl(ws_name, edit=True, visible=False)
        except Exception:
            pass

    @classmethod
    def _delete_existing_workspace_control(cls):
        ws_name = "ScriptConsoleWorkspaceControl"
        if pm.workspaceControl(ws_name, exists=True):
            try:
                pm.deleteUI(ws_name)
            except Exception:
                pass

    def _mirror_script_editor_output(self):
        # Ensure Script Editor is initialized
        was_open = self._ensure_script_editor_initialized()
        script_output = self._get_script_editor_output_widget()
        if not script_output:
            self.output.setPlainText("Could not find Script Editor output widget.")
            return

        # Disconnect all previous connections to avoid duplicates/crashes
        try:
            script_output.textChanged.disconnect()
        except Exception:
            pass

        def sync():
            self.output.setPlainText(script_output.toPlainText())
            self.output.moveCursor(QtGui.QTextCursor.End)

        sync()
        script_output.textChanged.connect(sync)
        # Optionally close the Script Editor if we opened it
        if not was_open:
            self._hide_script_editor()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.setFocus()

    @classmethod
    def show_console(
        cls, dock=None, width: int = None, height: int = None, tab_position: str = None
    ):
        ws_name = cls.WORKSPACE_CONTROL_NAME

        if cls._instance:
            cls._instance.close()
            cls._instance.deleteLater()

        cls._instance = cls()
        cls._instance.show(dockable=True, workspaceControlName=ws_name)

        def force_dock_and_resize():
            if pm.workspaceControl(ws_name, exists=True):
                try:
                    # Set size
                    if width is not None:
                        pm.workspaceControl(ws_name, edit=True, resizeWidth=width)
                    if height is not None:
                        pm.workspaceControl(ws_name, edit=True, resizeHeight=height)

                    # Dock logic
                    if dock:
                        if isinstance(dock, str):
                            pm.workspaceControl(
                                ws_name,
                                edit=True,
                                dockToMainWindow=(dock.lower(), False),
                            )
                        elif isinstance(dock, (tuple, list)) and len(dock) == 2:
                            pm.workspaceControl(
                                ws_name,
                                edit=True,
                                dockToControl=(dock[0], dock[1].lower()),
                            )
                        else:
                            print(f"Invalid dock parameter: {dock}")

                    # Set tab direction
                    if tab_position:
                        tab_dir = {
                            "top": "north",
                            "left": "west",
                            "right": "east",
                        }.get(tab_position.lower())
                        if tab_dir:
                            pm.workspaceControl(
                                ws_name, edit=True, tabPosition=(tab_dir, -1)
                            )
                        else:
                            print(
                                f"Invalid tab_position: {tab_position}. Must be 'top', 'left', or 'right'."
                            )

                except Exception as e:
                    print(f"Docking, resizing, or tab direction failed: {e}")

        QtCore.QTimer.singleShot(200, force_dock_and_resize)


# -----------------------------------------------------------------------------


def show(*args, **kwargs):
    ScriptConsole.show_console(*args, **kwargs)


# Usage: Call this in Maya's script editor or shelf button to show the window
if __name__ == "__main__":
    show()


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
