# !/usr/bin/python
# coding=utf-8
import maya.cmds as cmds
from typing import Optional



class UiUtils:
    @staticmethod
    def get_main_window():
        """Get the main Maya window as a QMainWindow instance.

        Robust implementation supporting PySide2 (Maya < 2024) and PySide6 (Maya >= 2024).
        """
        from qtpy import QtWidgets
        import maya.OpenMayaUI as omui

        ptr = omui.MQtUtil.mainWindow()
        if not ptr:
            return None

        # Try shiboken6 first (newer Maya)
        try:
            from shiboken6 import wrapInstance

            return wrapInstance(int(ptr), QtWidgets.QMainWindow)
        except ImportError:
            pass

        # Try shiboken2 next (older Maya)
        try:
            from shiboken2 import wrapInstance

            return wrapInstance(int(ptr), QtWidgets.QMainWindow)
        except ImportError:
            return None

    @staticmethod
    def get_menu_name(qt_object_name: str) -> Optional[str]:
        """Retrieve the internal Maya name of a menu given its Qt object name."""
        import maya.OpenMayaUI as omui

        # Find the control associated with the given Qt object name
        ptr = omui.MQtUtil.findControl(qt_object_name)
        if ptr is not None:
            # Convert the pointer to an integer and get the full Maya menu name
            maya_menu_name = omui.MQtUtil.fullName(int(ptr))
            if maya_menu_name:
                print(f"Derived Maya menu name: {maya_menu_name}")
                return maya_menu_name
            else:
                print(
                    f"Failed to derive the Maya menu name from Qt object '{qt_object_name}'."
                )
                return None
        else:
            print(f"Failed to find the pointer for the Qt object '{qt_object_name}'.")
            return None

    @staticmethod
    def get_panel(*args, **kwargs):
        """Returns panel and panel configuration information.
        Returns Maya panel info via the cmds.getPanel command.

        Parameters:
            [allConfigs=boolean], [allPanels=boolean], [allScriptedTypes=boolean], [allTypes=boolean], [configWithLabel=string], [containing=string], [invisiblePanels=boolean], [scriptType=string], [type=string], [typeOf=string], [underPointer=boolean], [visiblePanels=boolean], [withFocus=boolean], [withLabel=string])

        Returns:
            (str) An array of panel names.
        """
        from maya.cmds import getPanel

        result = getPanel(*args, **kwargs)

        return result

    @staticmethod
    def get_model_panel(with_focus: bool = True) -> Optional[str]:
        """Return a 3D model panel (viewport), suitable for commands like isolateSelect.

        Resolves to an actual `modelPanel`, so it never returns the outliner,
        Hypershade, Attribute Editor, etc. — which would otherwise raise
        `model panel '<name>' does not exist`.

        Parameters:
            with_focus (bool): Prefer the focused panel, then the one under the
                pointer, before falling back to the active/any visible model panel.

        Returns:
            (str/None) A model panel name, or None if no model panel is visible.
        """
        model_panels = cmds.getPanel(type="modelPanel") or []
        if not model_panels:
            return None

        if with_focus:
            for panel in (
                cmds.getPanel(withFocus=True),
                cmds.getPanel(underPointer=True),
            ):
                if panel in model_panels:
                    return panel
            # The active model editor maps to its panel for standard viewports.
            try:
                active = cmds.playblast(activeEditor=True)
            except RuntimeError:
                active = None
            if active in model_panels:
                return active

        # Fall back to the first visible model panel, then any model panel.
        visible = [
            p for p in (cmds.getPanel(visiblePanels=True) or []) if p in model_panels
        ]
        return visible[0] if visible else model_panels[0]

    @staticmethod
    def main_progress_bar(size, name="progressBar#", step_amount=1):
        """# add esc key pressed return False

        Parameters:
            size (int): total amount
            name (str): name of progress bar created
            step_amount(int): increment amount

        Example:
            main_progress_bar (len(edges), progressCount)
            cmds.progressBar ("progressBar_", edit=1, step=1)
            if cmds.progressBar ("progressBar_", q=True, isCancelled=1):
                break
            cmds.progressBar ("progressBar_", edit=1, endProgress=1)

            to use main progressBar: name=string $gMainProgressBar
        """
        import maya.mel as mel

        status = "processing: {} items ..".format(size)

        # beginProgress/step are edit-only flags, so operate on an existing bar:
        # the named control if it exists, else Maya's always-present main bar.
        if not cmds.progressBar(name, exists=True):
            name = mel.eval("$tmp = $gMainProgressBar")

        cmds.progressBar(
            name,
            edit=True,
            beginProgress=True,
            isInterruptable=True,
            status=status,
            maxValue=size,
            step=step_amount,
        )
        return name

    @staticmethod
    def list_ui_objects():
        """List all UI objects."""
        ui_objects = {
            "windows": cmds.lsUI(windows=True),
            "panels": cmds.lsUI(panels=True),
            "editors": cmds.lsUI(editors=True),
            "menus": cmds.lsUI(menus=True),
            "menuItems": cmds.lsUI(menuItems=True),
            "controls": cmds.lsUI(controls=True),
            "controlLayouts": cmds.lsUI(controlLayouts=True),
            "contexts": cmds.lsUI(contexts=True),
        }
        for category, objects in ui_objects.items():
            print(f"{category}:\n{objects}\n")

    @staticmethod
    def clear_scrollfield_reporters():
        """Clears the contents of all cmdScrollFieldReporter UI objects in the current Maya session.

        This function is useful for cleaning up the script output display in Maya's UI,
        particularly before executing scripts or operations that generate a lot of output.
        It iterates over all cmdScrollFieldReporter objects and clears them, ensuring a clean
        slate for viewing new script or command output.
        """
        # ``lsUI`` is a GUI-only command — absent in mayapy standalone. No UI
        # means no reporters to clear, so no-op cleanly (lets headless test
        # runners import/exercise modules that call this).
        if not hasattr(cmds, "lsUI"):
            return

        # Get a list of all UI objects of type "cmdScrollFieldReporter"
        reporters = cmds.lsUI(type="cmdScrollFieldReporter") or []

        # If any reporters are found, clear them
        for reporter in reporters:
            cmds.cmdScrollFieldReporter(reporter, edit=True, clear=True)

    @staticmethod
    def _outliner_editors() -> list:
        """The Outliner editor of every Outliner panel; ``[]`` with no UI.

        Panels are enumerated rather than assumed — ``outlinerPanel1`` can be
        deleted in a custom workspace / stripped UI, and every caller here is a
        best-effort convenience that must not raise on that account.
        """
        try:
            panels = cmds.getPanel(type="outlinerPanel") or []
        except Exception:  # no UI at all (mayapy / batch)
            return []

        editors = []
        for panel in panels:
            try:
                editor = cmds.outlinerPanel(panel, query=True, outlinerEditor=True)
            except Exception:  # panel gone between query and use
                continue
            if editor:  # a panel can answer None -- never pass that on
                editors.append(editor)
        return editors

    @staticmethod
    def reveal_in_outliner(objects):
        """Select *objects* and scroll the Outliner to them.

        The selection is the primary action and always happens; scrolling a
        panel to it is the best-effort half (there may be no Outliner panel to
        scroll), so a stripped UI degrades to a plain select instead of
        raising half-way through.
        """
        if not objects:
            return
        cmds.select(objects, replace=True)
        for editor in UiUtils._outliner_editors()[:1]:
            try:
                cmds.outlinerEditor(editor, edit=True, showSelected=True)
            except Exception:  # editor died between query and use
                pass

    @staticmethod
    def refresh_outliners() -> int:
        """Redraw every Outliner panel. Returns the number of panels refreshed.

        Some node changes the Outliner doesn't watch — notably a
        ``hiddenInOutliner`` write (``DisplayUtils.set_hidden_in_outliner``) —
        leave stale rows on screen until the panel redraws. No-ops cleanly with
        no UI at all (mayapy / batch).
        """
        count = 0
        for editor in UiUtils._outliner_editors():
            try:
                cmds.outlinerEditor(editor, edit=True, refresh=True)
            except Exception:  # panel gone between query and use
                continue
            count += 1
        return count

    @staticmethod
    def dispatch_log_link(url, logger=None) -> bool:
        """Handle ``action://`` links emitted by ``log_link()`` in a QTextBrowser.

        Supported actions:
            ``open``   — open *path* (file or directory) in the OS shell.
                         Accepts ``?path=`` (canonical) or ``?filepath=``
                         (legacy) -- the marmoset/substance/rizom bridges
                         all emit ``?path=``.
            ``select`` — select *node* in the Maya viewport.
            ``reveal`` — select *node* and reveal it in the Outliner.

        Parameters:
            url:    A ``QUrl`` from ``QTextBrowser.anchorClicked``.
            logger: Optional logger for debug/warning messages.

        Returns:
            True if the link was handled, False otherwise.
        """
        from urllib.parse import parse_qs

        if url.scheme() != "action":
            return False

        action = url.host()
        params = parse_qs(url.query())

        # Non-node actions -------------------------------------------------
        if action == "open":
            # Accept ``path`` (canonical -- what every bridge emits) and
            # ``filepath`` as a back-compat fallback for any external
            # caller still using the older key.
            filepath = (
                params.get("path", [""])[0]
                or params.get("filepath", [""])[0]
            )
            if not filepath:
                return False
            import pythontk as ptk

            # Cross-platform open (win/mac/linux); returns False on failure and
            # avoids the Windows-only os.startfile AttributeError off-Windows.
            return ptk.FileUtils.open_explorer(filepath, logger=logger)

        # Node-based actions need cmds; defer the import so the ``open``
        # branch above stays usable in non-Maya contexts.
        from maya import cmds

        # Node-based actions -----------------------------------------------
        node = params.get("node", [""])[0]

        if not node:
            return False

        if not cmds.objExists(node):
            if logger:
                logger.warning(f"Object not found: {node}")
            return False

        if action == "select":
            cmds.select(node, replace=True)
        elif action == "reveal":
            UiUtils.reveal_in_outliner([node])
        else:
            if logger:
                logger.debug(f"Unknown log link action: {action}")
            return False

        return True


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
