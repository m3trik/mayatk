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
    def get_selected_channels():
        """Get any attributes (channels) that are selected in the channel box.

        Returns:
            (str) list of any selected attributes as strings. (ie. ['tx', ry', 'sz'])
        """
        channelBox = pm.mel.eval(
            "global string $gChannelBoxName; $temp=$gChannelBoxName;"
        )  # fetch maya's main channelbox
        attrs = pm.channelBox(channelBox, q=True, sma=True)

        if attrs is None:
            attrs = []
        return attrs

    @staticmethod
    def get_channel_box_attributes(
        objects,
        *args,
        include_locked=False,
        include_nonkeyable=False,
        include_object_name=False,
        as_group=False,
    ):
        """Retrieves the current values of specified attributes from the channel box for given objects.

        Parameters:
            objects (str/obj/list): Objects to query the attributes of.
            *args (str, optional): Specific attribute(s) to query. If omitted, 'selected' attributes will be queried.
            include_locked (bool, optional): Includes locked attributes in the results.
            include_nonkeyable (bool, optional): Includes non-keyable attributes in the results.
            include_object_name (bool, optional): Returns full attribute names including the object name if True.
            as_group (bool, optional): If True, returns a flat dict where later objects overwrite earlier ones.
                                      If False (default), returns nested dict preserving each object's unique values.

        Returns:
            dict: If as_group=False (default): {obj_name: {attr: value, ...}, ...}
                  If as_group=True: {attr: value, ...} (flat dict, later objects overwrite earlier ones)

        Example:
            # Per-object dict (default - preserves each object's unique values)
            attrs = get_channel_box_attributes(objects)
            # Returns: {'pCube1': {'translateX': 5.0}, 'pCube2': {'translateX': 10.0}}

            # Flat dict (last object's values win)
            attrs = get_channel_box_attributes(objects, as_group=True)
            # Returns: {'translateX': 10.0}

            # With specific attributes
            attrs = get_channel_box_attributes(objects, 'translateX', 'rotateY')
        """
        channel_box = pm.melGlobals["gChannelBoxName"]
        attributes_dict = {}

        for obj in pm.ls(objects):
            # Determine the attributes to query
            if args:
                attrs = list(args)
            else:
                # Default to selected attributes if none are specified
                attrs = pm.channelBox(channel_box, query=True, sma=True) or []

            # Append locked and nonkeyable attributes if requested
            if include_locked:
                attrs += pm.listAttr(obj, locked=True)
            if include_nonkeyable:
                attrs += pm.listAttr(obj, keyable=False)

            # Fetch attribute values
            if as_group:
                # Flat mode: later objects overwrite earlier ones
                for attr in attrs:
                    attr_name = f"{obj}.{attr}" if include_object_name else attr
                    value = pm.getAttr(f"{obj}.{attr}")
                    attributes_dict[attr_name] = value
            else:
                # Per-object mode (default): preserve each object's unique values
                obj_attrs = {}
                for attr in attrs:
                    value = pm.getAttr(f"{obj}.{attr}")
                    obj_attrs[attr] = value
                if obj_attrs:
                    attributes_dict[str(obj)] = obj_attrs

        return attributes_dict

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
