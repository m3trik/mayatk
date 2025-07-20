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
        """Get the main Maya window as a QMainWindow instance."""
        from qtpy import QtWidgets
        from shiboken6 import wrapInstance
        import maya.OpenMayaUI as omui

        main_window_ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(main_window_ptr), QtWidgets.QMainWindow)

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


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
