# !/usr/bin/python
# coding=utf-8
import maya.cmds as cmds
import maya.mel as mel
from typing import Optional
from qtpy import QtWidgets, QtGui, QtCore
from maya.OpenMayaUI import MQtUtil
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
from shiboken6 import wrapInstance
from mayatk.env_utils.maya_connection import MayaConnection
from uitk import ScriptOutput


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
        # The read-only, syntax-highlighted view now lives in uitk (shared with
        # blendertk / standalone). Maya-specific behavior is injected: Clear also
        # empties Maya's reporter, and the context menu gains the Echo toggle.
        # Hover-to-focus (so Ctrl+C / Ctrl+A reach the console without clicking in
        # first) is the widget's own `focus_on_hover`, default on — it used to be a
        # hand-rolled `enterEvent` on this dialog; the widget owning it means the
        # Blender console gets the same behavior from the same code.
        self.output = ScriptOutput(
            clear_callback=self._clear_reporter,
            context_menu_hook=self._echo_menu_hook,
        )
        layout.addWidget(self.output)
        self.resize(800, 300)
        self._mirror_script_editor_output()

    def _clear_reporter(self):
        """Context-menu **Clear**: empty the mirror widget AND Maya's reporter."""
        self.output.clear()
        conn = MayaConnection.get_instance()
        if not conn.is_connected:
            conn.connect(mode="auto")
        if not conn.clear_script_editor():
            print("Failed to clear Maya Script Editor")

    def _echo_menu_hook(self, menu: QtWidgets.QMenu):
        """Append Maya's 'Echo All Commands' toggle to the shared context menu."""
        menu.addSeparator()
        # Ensure the Script Editor exists so commandEcho has a target.
        ws_name = "scriptEditorPanel1Window"
        if not cmds.workspaceControl(ws_name, exists=True):
            mel.eval("ScriptEditor;")
            cmds.workspaceControl(ws_name, edit=True, visible=True)

        try:
            echo_all = bool(mel.eval("commandEcho -query -state"))
        except Exception:
            echo_all = False

        echo_action = menu.addAction("Echo All Commands")
        echo_action.setCheckable(True)
        echo_action.setChecked(echo_all)

        def on_toggled(checked):
            try:
                mel.eval("commandEcho -state on" if checked else "commandEcho -state off")
                print(f"Echo All Commands: {'ON' if checked else 'OFF'}")
                actual_state = bool(mel.eval("commandEcho -query -state"))
                if actual_state != checked:
                    print(
                        f"Warning: Echo All Commands may not have updated correctly. "
                        f"Current state: {'ON' if actual_state else 'OFF'}"
                    )
                    echo_action.blockSignals(True)
                    echo_action.setChecked(actual_state)
                    echo_action.blockSignals(False)
            except Exception as e:
                print(f"Failed to toggle Echo All Commands: {e}")
                echo_action.blockSignals(True)
                echo_action.setChecked(not checked)
                echo_action.blockSignals(False)

        echo_action.toggled.connect(on_toggled)

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
