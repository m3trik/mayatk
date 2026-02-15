# !/usr/bin/python
# coding=utf-8
from typing import Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)


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
            pass

        # Fallback to PySide2 logic if shiboken import fails but PySide2 is present
        try:
            import PySide2.QtWidgets
            import PySide2.QtGui

            return wrapInstance(long(ptr), PySide2.QtWidgets.QWidget)
        except:
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
        A fix for the broken pymel command `getPanel`.

        Parameters:
            [allConfigs=boolean], [allPanels=boolean], [allScriptedTypes=boolean], [allTypes=boolean], [configWithLabel=string], [containing=string], [invisiblePanels=boolean], [scriptType=string], [type=string], [typeOf=string], [underPointer=boolean], [visiblePanels=boolean], [withFocus=boolean], [withLabel=string])

        Returns:
            (str) An array of panel names.
        """
        from maya.cmds import getPanel  # pymel getPanel is broken in ver: 2022,23

        result = getPanel(*args, **kwargs)

        return result

    @staticmethod
    def main_progress_bar(size, name="progressBar#", step_amount=1):
        """# add esc key pressed return False

        Parameters:
            size (int): total amount
            name (str): name of progress bar created
            step_amount(int): increment amount

        Example:
            main_progress_bar (len(edges), progressCount)
            pm.progressBar ("progressBar_", edit=1, step=1)
            if pm.progressBar ("progressBar_", q=True, isCancelled=1):
                break
            pm.progressBar ("progressBar_", edit=1, endProgress=1)

            to use main progressBar: name=string $gMainProgressBar
        """
        status = "processing: {} items ..".format(size)

        edit = False
        if pm.progressBar(name, exists=1):
            edit = True

        pm.progressBar(
            name,
            edit=edit,
            beginProgress=1,
            isInterruptable=True,
            status=status,
            maxValue=size,
            step=step_amount,
        )

    @staticmethod
    def list_ui_objects():
        """List all UI objects."""
        ui_objects = {
            "windows": pm.lsUI(windows=True),
            "panels": pm.lsUI(panels=True),
            "editors": pm.lsUI(editors=True),
            "menus": pm.lsUI(menus=True),
            "menuItems": pm.lsUI(menuItems=True),
            "controls": pm.lsUI(controls=True),
            "controlLayouts": pm.lsUI(controlLayouts=True),
            "contexts": pm.lsUI(contexts=True),
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
        # Get a list of all UI objects of type "cmdScrollFieldReporter"
        reporters = pm.lsUI(type="cmdScrollFieldReporter")

        # If any reporters are found, clear them
        for reporter in reporters:
            pm.cmdScrollFieldReporter(reporter, edit=True, clear=True)

    @staticmethod
    def reveal_in_outliner(objects):
        """Reveal objects in the Outliner panel."""
        # Get the outliner editor associated with 'outlinerPanel1'
        outliner_editor = pm.outlinerPanel(
            "outlinerPanel1", query=True, outlinerEditor=True
        )
        # Reveal the objects in the outliner
        pm.outlinerEditor(outliner_editor, edit=True, revealObjects=objects)


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
