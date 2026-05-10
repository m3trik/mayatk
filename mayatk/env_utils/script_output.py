# !/usr/bin/python
# coding=utf-8
import maya.cmds as cmds
import maya.mel as mel
from typing import List, Optional
from qtpy import QtWidgets, QtGui, QtCore
from maya.OpenMayaUI import MQtUtil
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
from shiboken6 import wrapInstance
from mayatk.env_utils.maya_connection import MayaConnection


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
        # Ensure Ctrl+C works reliably even when Maya intercepts shortcuts
        self._copy_shortcut = QtGui.QShortcut(QtGui.QKeySequence.Copy, self)
        self._copy_shortcut.setContext(QtCore.Qt.ApplicationShortcut)
        self._copy_shortcut.activated.connect(self._handle_copy_shortcut)
        # Install application-level event filter to capture Ctrl+C before Maya
        app = QtWidgets.QApplication.instance()
        if app:
            app.installEventFilter(self)
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
        """Ensure copy shortcut works reliably in the output widget."""
        if event.matches(QtGui.QKeySequence.Copy):
            self._handle_copy_shortcut()
            event.accept()
            return
        super().keyPressEvent(event)

    def event(self, event: QtCore.QEvent):
        """Intercept shortcut override so Maya doesn't steal Ctrl+C."""
        if event.type() == QtCore.QEvent.ShortcutOverride:
            if isinstance(event, QtGui.QKeyEvent) and event.matches(
                QtGui.QKeySequence.Copy
            ):
                if self.textCursor().hasSelection():
                    event.accept()
                    return True
        return super().event(event)

    def eventFilter(self, obj, event: QtCore.QEvent):
        if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride):
            if isinstance(event, QtGui.QKeyEvent) and event.matches(
                QtGui.QKeySequence.Copy
            ):
                if self.textCursor().hasSelection():
                    self._handle_copy_shortcut()
                    event.accept()
                    return True
        return super().eventFilter(obj, event)

    def _handle_copy_shortcut(self):
        if self.textCursor().hasSelection():
            cursor = self.textCursor()
            text = cursor.selectedText().replace("\u2029", "\n")
            QtWidgets.QApplication.clipboard().setText(text)

    def _clear_script_editor(self):
        """Clear both the widget and the actual Maya Script Editor output"""
        # Clear this widget
        self.clear()

        # Clear the actual Maya Script Editor
        conn = MayaConnection.get_instance()
        if not conn.is_connected:
            conn.connect(mode="auto")

        if not conn.clear_script_editor():
            print("Failed to clear Maya Script Editor")

    def _context_menu(self, pos: QtCore.QPoint):
        # Create a simple context menu
        menu = QtWidgets.QMenu(self)

        menu.addAction("Clear", self._clear_script_editor)
        menu.addAction("Copy", self.copy)  # Qt's built-in copy

        menu.addSeparator()  # Always ensure Script Editor is present
        ws_name = "scriptEditorPanel1Window"
        if not cmds.workspaceControl(ws_name, exists=True):
            mel.eval("ScriptEditor;")
            cmds.workspaceControl(ws_name, edit=True, visible=True)

        # Get current echo state using MEL for more reliability
        try:
            # Use the correct MEL command for echo all commands
            echo_all = bool(mel.eval("commandEcho -query -state"))
        except Exception:
            echo_all = False

        echo_action = menu.addAction("Echo All Commands")
        echo_action.setCheckable(True)
        echo_action.setChecked(echo_all)

        def on_toggled(checked):
            try:
                # Use MEL commands for more reliable operation
                if checked:
                    mel.eval("commandEcho -state on")
                else:
                    mel.eval("commandEcho -state off")

                print(f"Echo All Commands: {'ON' if checked else 'OFF'}")

                # Verify the change took effect
                actual_state = bool(mel.eval("commandEcho -query -state"))

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
        was_open = cmds.workspaceControl(ws_name, exists=True)
        if not was_open or not cmds.workspaceControl(ws_name, query=True, visible=True):
            mel.eval("ScriptEditor;")
            cmds.workspaceControl(ws_name, edit=True, visible=True)
        return was_open

    @staticmethod
    def _hide_script_editor() -> None:
        ws_name = "scriptEditorPanel1Window"
        try:
            cmds.workspaceControl(ws_name, edit=True, visible=False)
        except Exception:
            pass

    @classmethod
    def _delete_existing_workspace_control(cls):
        ws_name = "ScriptConsoleWorkspaceControl"
        if cmds.workspaceControl(ws_name, exists=True):
            try:
                cmds.deleteUI(ws_name)
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
    def _build_ui_script(cls) -> str:
        """Self-contained string Maya stores in the workspace prefs file
        and re-runs on next session to restore this panel."""
        return (
            "from mayatk.env_utils.script_output import ScriptConsole\n"
            "ScriptConsole.show_console(restore=True)"
        )

    @classmethod
    def show_console(
        cls,
        dock=None,
        width: int = None,
        height: int = None,
        tab_position: str = None,
        restore: bool = False,
    ):
        """Show the Script Output console.

        Persistence model:
            On first creation we register a ``uiScript`` with the workspace
            control. Maya stores this in its workspace prefs file and re-runs
            it on next launch to restore the panel — including its docked
            location and user-adjusted size — without ``userSetup.py`` having
            to recreate it. ``dock``/``width``/``height``/``tab_position``
            therefore apply only on the **first** creation; later sessions
            inherit the user's saved layout.

        Parameters:
            dock: Initial dock target (only used on first creation).
            width: Initial width in pixels (first creation only).
            height: Initial height in pixels (first creation only).
            tab_position: One of ``'top' | 'left' | 'right'``.
            restore: Internal — set True when invoked by Maya's uiScript
                during workspace restoration.
        """
        ws_name = cls.WORKSPACE_CONTROL_NAME

        # ---- restoration path (called by Maya via uiScript) -------------
        if restore:
            restored_control = MQtUtil.findControl(ws_name)
            if not restored_control:
                print(f"[ScriptConsole] No workspace control found for restore: {ws_name}")
                return
            cls._instance = cls()
            mixin_ptr = MQtUtil.findControl(cls._instance.objectName())
            if mixin_ptr:
                MQtUtil.addWidgetToMayaLayout(int(mixin_ptr), int(restored_control))
            return cls._instance

        # ---- already exists (Maya kept it from prior session) ----------
        if cmds.workspaceControl(ws_name, exists=True):
            cmds.workspaceControl(ws_name, edit=True, restore=True, visible=True)
            return cls._instance

        # ---- first-time creation ---------------------------------------
        if cls._instance:
            try:
                cls._instance.close()
                cls._instance.deleteLater()
            except Exception:
                pass

        cls._instance = cls()
        cls._instance.show(
            dockable=True,
            workspaceControlName=ws_name,
            uiScript=cls._build_ui_script(),
            retain=False,
        )

        # MayaQWidgetDockableMixin.show() does not always propagate uiScript
        # to the underlying workspaceControl. Set it explicitly so Maya can
        # persist this panel across sessions.
        if cmds.workspaceControl(ws_name, exists=True):
            try:
                cmds.workspaceControl(
                    ws_name, edit=True, uiScript=cls._build_ui_script()
                )
            except Exception as e:
                print(f"[ScriptConsole] Could not set uiScript: {e}")

        def force_dock_and_resize():
            if not cmds.workspaceControl(ws_name, exists=True):
                return
            try:
                if width is not None:
                    cmds.workspaceControl(ws_name, edit=True, resizeWidth=width)
                if height is not None:
                    cmds.workspaceControl(ws_name, edit=True, resizeHeight=height)

                if dock:
                    if isinstance(dock, str):
                        cmds.workspaceControl(
                            ws_name,
                            edit=True,
                            dockToMainWindow=(dock.lower(), False),
                        )
                    elif isinstance(dock, (tuple, list)) and len(dock) == 2:
                        cmds.workspaceControl(
                            ws_name,
                            edit=True,
                            dockToControl=(dock[0], dock[1].lower()),
                        )
                    else:
                        print(f"Invalid dock parameter: {dock}")

                if tab_position:
                    tab_dir = {"top": "north", "left": "west", "right": "east"}.get(
                        tab_position.lower()
                    )
                    if tab_dir:
                        cmds.workspaceControl(
                            ws_name, edit=True, tabPosition=(tab_dir, -1)
                        )
                    else:
                        print(
                            f"Invalid tab_position: {tab_position}. Must be 'top', 'left', or 'right'."
                        )

                if height is not None:
                    cmds.workspaceControl(ws_name, edit=True, resizeHeight=height)

            except Exception as e:
                print(f"Docking, resizing, or tab direction failed: {e}")

        QtCore.QTimer.singleShot(200, force_dock_and_resize)
        return cls._instance


# -----------------------------------------------------------------------------


def show(*args, **kwargs):
    ScriptConsole.show_console(*args, **kwargs)


def toggle(*args, **kwargs):
    """Toggle the Script Output panel.

    - If the workspace control is already visible, hide it.
    - If it exists but is hidden, show it.
    - Otherwise create it (forwarding any *args / **kwargs to ``show``).
    """
    ws = ScriptConsole.WORKSPACE_CONTROL_NAME
    if cmds.workspaceControl(ws, exists=True):
        visible = cmds.workspaceControl(ws, query=True, visible=True)
        cmds.workspaceControl(ws, edit=True, visible=not visible)
        return
    show(*args, **kwargs)


# Usage: Call this in Maya's script editor or shelf button to show the window
if __name__ == "__main__":
    show()


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
