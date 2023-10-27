# !/usr/bin/python
# coding=utf-8
import os
import sys
from functools import wraps

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk


class CoreUtils:
    """ """

    def undo(fn):
        """A decorator to place a function into Maya's undo chunk.
        Prevents the undo queue from breaking entirely if an exception is raised within the given function.

        Parameters:
            fn (obj): The decorated python function that will be placed into the undo que as a single entry.
        """

        @wraps(fn)
        def wrapper(*args, **kwargs):
            with pm.UndoChunk():
                if args and hasattr(args[0], "__class__"):
                    self = args[0]
                    return fn(self, *args[1:], **kwargs)
                else:
                    return fn(*args, **kwargs)

        return wrapper

    @staticmethod
    def get_main_window():
        """Get maya's main window object.

        Returns:
            (QWidget)
        """
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if not app:
            return print(
                f"# Warning: {__file__} in get_main_window\n#\tCould not find QApplication instance."
            )

        main_window = next(
            iter(w for w in app.topLevelWidgets() if w.objectName() == "MayaWindow"),
            None,
        )
        if not main_window:
            return print(
                f"# Warning: {__file__} in get_main_window\n#\tCould not find main window instance."
            )

        return main_window

    @staticmethod
    def get_maya_info(key):
        """Fetch specific information about the current Maya environment based on the provided key.

        Parameters:
            key (str): The key corresponding to the specific Maya information to fetch.

        Returns:
            The corresponding information based on the key, or an error message if the key is invalid.
        """
        available_keys = {
            "install_path": lambda: os.environ.get("MAYA_LOCATION"),
            "version": lambda: pm.about(version=True),
            "renderer": lambda: pm.getAttr("defaultRenderGlobals.currentRenderer"),
            "workspace_dir": lambda: pm.workspace(q=True, rd=True),
            "scene_name": lambda: pm.sceneName(),
            "user_name": lambda: pm.optionVar(q="PTglobalUserName"),
            "ui_language": lambda: pm.about(uiLanguage=True),
            "os_type": lambda: pm.about(os=True),
            "linear_units": lambda: pm.currentUnit(q=True, fullName=True),
            "time_units": lambda: pm.currentUnit(q=True, t=True),
            "loaded_plugins": lambda: pm.pluginInfo(q=True, listPlugins=True),
            "api_version": lambda: pm.about(api=True),
            "host_name": lambda: pm.about(hostName=True),
            "current_frame": lambda: pm.currentTime(q=True),
            "frame_range": lambda: (
                pm.playbackOptions(q=True, min=True),
                pm.playbackOptions(q=True, max=True),
            ),
            "viewport_renderer": lambda: pm.modelEditor(
                "modelPanel4", q=True, rendererName=True
            ),
            "current_camera": lambda: pm.modelEditor(
                "modelPanel4", q=True, camera=True
            ),
            "available_cameras": lambda: pm.listCameras(),
            "active_layers": lambda: [
                layer.name()
                for layer in pm.ls(type="displayLayer")
                if not layer.attr("visibility").isLocked()
            ],
            "current_tool": lambda: pm.currentCtx(),
            "up_axis": lambda: pm.upAxis(q=True, axis=True),
            "maya_uptime": lambda: pm.timerX(),
            "total_polys": lambda: pm.polyEvaluate(scene=True, triangle=True),
            "total_nodes": lambda: len(pm.ls(dag=True)),
            "current_selection": lambda: pm.selected(),
        }

        if key not in available_keys:
            raise KeyError(
                "Invalid key. Available keys are: {}".format(
                    ", ".join(available_keys.keys())
                )
            )

        value = available_keys[key]()
        if value is None:
            raise ValueError(f"The value for {key} could not be found.")

        return value

    @staticmethod
    def load_plugin(plugin):
        """Loads a specified plugin.
        This method checks if the plugin is already loaded before attempting to load it.

        Parameters:
            plugin (str): The name of the plugin to load.

        Examples:
            >>> load_plugin('nearestPointOnMesh')

        Raises:
            ValueError: If the plugin is not found or fails to load.
        """
        if not pm.pluginInfo(plugin, query=True, loaded=True):
            try:
                pm.loadPlugin(plugin)
            except RuntimeError as e:
                raise ValueError(f"Failed to load plugin {plugin}: {e}")

    @staticmethod
    def append_maya_paths(maya_version=None):
        """Appends various Maya-related paths to the system's Python environment and sys.path.
        This function sets environment variables and extends sys.path to include paths
        for Maya's Python API, libraries, and related functionalities. It aims to
        facilitate the integration of Maya with external Python scripts.

        Parameters:
        maya_version (int, str, optional): The version of Maya to add the paths for.
                                          If None, the function will query the version
                                          using PyMel. Defaults to None.
        Raises:
        EnvironmentError: If the MAYA_LOCATION environment variable is not set.

        Example:
        >>> append_maya_paths()
        This will set paths for the current Maya version in use.

        >>> append_maya_paths(2023)
        This will set paths explicitly for Maya version 2023.

        Returns:
        None
        """
        # Query Maya version if not provided
        if maya_version is None:
            maya_version = pm.about(version=True)

        maya_install_path = os.environ.get("MAYA_LOCATION")
        if not maya_install_path:
            raise EnvironmentError("MAYA_LOCATION environment variable not set.")

        # Setting Environment Variables
        os.environ["PYTHONHOME"] = os.path.join(maya_install_path, "Python")
        os.environ["PATH"] = (
            os.path.join(maya_install_path, "bin") + ";" + os.environ["PATH"]
        )

        # List of paths to append
        paths_to_add = [
            os.path.join(maya_install_path, "bin"),
            os.path.join(maya_install_path, "Python"),
            os.path.join(maya_install_path, "Python", str(maya_version), "DLLs"),
            os.path.join(maya_install_path, "Python", str(maya_version), "lib"),
            os.path.join(
                maya_install_path, "Python", str(maya_version), "lib", "lib-tk"
            ),
            os.path.join(
                maya_install_path, "Python", str(maya_version), "lib", "plat-win"
            ),
            os.path.join(
                maya_install_path, "Python", str(maya_version), "lib", "site-packages"
            ),
            os.path.join(
                maya_install_path, "devkit", "other", "pymel", "extras", "modules"
            ),
            os.path.join(
                maya_install_path, "devkit", "other", "pymel", "extras", "completion"
            ),
        ]

        # Append paths only if they are not already in sys.path
        for path in paths_to_add:
            if path not in sys.path:
                sys.path.append(path)

    @staticmethod
    def wrap_control(control_name, container):
        """Embed a Maya Native UI Object.

        Parameters:
            control_name (str): The name of an existing maya control. ie. 'cmdScrollFieldReporter1'
            container (obj): A widget instance in which to wrap the control.

        Example:
            modelPanelName = pm.modelPanel("embeddedModelPanel#", cam='persp')
            wrap_control(modelPanelName, QtWidgets.QtWidget())
        """
        from PySide2 import QtWidgets
        from shiboken2 import wrapInstance
        from maya.OpenMayaUI import MQtUtil

        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layoutName = ptk.set_case(
            container.objectName() + "Layout", "camel"
        )  # results in '<objectName>Layout' or 'layout' if container objectName is ''
        layout.setObjectName(layoutName)
        pm.setParent(layoutName)

        derivedClass = ptk.get_derived_type(container)

        ptr = MQtUtil.findControl(
            control_name
        )  # get a pointer to the maya api paneLayout.
        control = wrapInstance(int(ptr), derivedClass)
        layout.addWidget(control)

        return control

    @staticmethod
    def mfn_mesh_generator(objects):
        """Generate mfn mesh from the given list of objects.

        Parameters:
            objects (str)(obj(list): The objects to convert to mfn mesh.

        Returns:
            (generator)
        """
        import maya.OpenMaya as om
        from mayatk import node_utils

        selectionList = om.MSelectionList()
        for mesh in node_utils.NodeUtils.get_shape_node(pm.ls(objects)):
            selectionList.add(mesh)

        for i in range(selectionList.length()):
            dagPath = om.MDagPath()
            selectionList.getDagPath(i, dagPath)
            # print (dagPath.fullPathName()) #debug
            mfnMesh = om.MFnMesh(dagPath)
            yield mfnMesh

    @staticmethod
    def get_array_type(array):
        """Determine the given element(s) type.
        Samples only the first element.

        Parameters:
            array (str/obj/list): The components(s) to query.

        Returns:
            (list) 'str', 'int'(valid only at sub-object level), or maya object type as string.
        """
        from mayatk import node_utils

        try:
            o = ptk.make_iterable(array)[0]
        except IndexError:
            # print (f'# Error: {__file__} in get_array_type:\n#\tOperation requires at least one object.\n#\t{error}')
            return ""

        return (
            "str"
            if isinstance(o, str)
            else "int"
            if isinstance(o, int)
            else node_utils.NodeUtils.get_type(o)
        )

    @staticmethod
    def convert_array_type(lst, returned_type="str", flatten=False):
        """Convert the given element(s) to <obj>, 'str', or int values.

        Parameters:
            lst (str/obj/list): The components(s) to convert.
            returned_type (str): The desired returned array element type.
                    valid: 'str'(default), 'obj', 'int'(valid only at sub-object level).
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list)(dict) return a dict only with a return type of 'int' and more that one object given.

        Example:
        convert_array_type('obj.vtx[:2]', 'str') #returns: ['objShape.vtx[0:2]']
        convert_array_type('obj.vtx[:2]', 'str', True) #returns: ['objShape.vtx[0]', 'objShape.vtx[1]', 'objShape.vtx[2]']
        convert_array_type('obj.vtx[:2]', 'obj') #returns: [MeshVertex('objShape.vtx[0:2]')]
        convert_array_type('obj.vtx[:2]', 'obj', True) #returns: [MeshVertex('objShape.vtx[0]'), MeshVertex('objShape.vtx[1]'), MeshVertex('objShape.vtx[2]')]
        convert_array_type('obj.vtx[:2]', 'int')) #returns: {nt.Mesh('objShape'): [(0, 2)]}
        convert_array_type('obj.vtx[:2]', 'int', True)) #returns: {nt.Mesh('objShape'): [0, 1, 2]}
        """
        lst = pm.ls(lst, flatten=flatten)
        if not lst or isinstance(lst[0], int):
            return []

        if returned_type == "int":
            result = {}
            for c in lst:
                obj = pm.ls(c, objectsOnly=1)[0]
                num = c.split("[")[-1].rstrip("]")

                try:
                    if flatten:
                        componentNum = int(num)
                    else:
                        n = [int(n) for n in num.split(":")]
                        componentNum = tuple(n) if len(n) > 1 else n[0]

                    if obj in result:  # append to existing object key.
                        result[obj].append(componentNum)
                    else:
                        result[obj] = [componentNum]
                except ValueError as error:  # incompatible object type.
                    print(
                        f"# Error: {__file__} in convert_array_type\n#\tunable to convert {obj} {num} to int.\n#\t{error}"
                    )
                    break

            objects = set(pm.ls(lst, objectsOnly=True))
            if (
                len(objects) == 1
            ):  # flatten the dict values from 'result' and remove any duplicates.
                flattened = ptk.flatten(result.values())
                result = ptk.remove_duplicates(flattened)

        elif returned_type == "str":
            result = list(map(str, lst))

        else:
            result = lst

        return result

    @staticmethod
    def get_parameter_mapping(node, cmd, parameters):
        """Queries a specified Maya command and returns a dictionary mapping the provided parameters to their values.

        This function helps to retrieve the values of different parameters or attributes associated with a given Maya node (like transformLimits). The node can be a string name, an object or a list of nodes.

        Parameters:
            node (str/obj): The node for which the attributes need to be queried.
            cmd (str): The name of the Maya command that is to be executed. For example, 'transformLimits'.
            parameters (list): A list of strings representing the parameters of the command to query. For example, ['enableTranslationX','translationX'].

        Returns:
            dict: A dictionary where each key is a queried parameter name and the corresponding value is the returned attribute value from the query. For example, {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]}.

        Example:
            >>> get_parameter_mapping(obj, 'transformLimits', ['enableTranslationX','translationX'])
            {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]}
        """
        cmd = getattr(pm, cmd)
        node = pm.ls(node)[0]

        return {p: cmd(node, **{"q": True, p: True}) for p in parameters}

    @staticmethod
    def set_parameter_mapping(node, cmd, parameters):
        """Applies a set of parameter values to a specified Maya node using a given Maya command.

        Parameters:
            node (str/obj/list): The object to query attributes of.
            parameters (dict): The command's parameters and their desired values. ie. {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]}

        Example:
            >>> apply_parameter_mapping(obj, 'transformLimits', {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]})
        """
        cmd = getattr(pm, cmd)
        node = pm.ls(node)[0]

        for p, v in parameters.items():
            cmd(node, **{p: v})

    @staticmethod
    def generate_unique_name(base_name):
        """Generate a unique name based on the base_name."""
        # Base case: If the base_name doesn't exist, just return it.
        if not pm.objExists(base_name):
            return base_name

        # Otherwise, append numbers until we get a unique name.
        counter = 1
        new_name = f"{base_name}_{counter}"
        while pm.objExists(new_name):
            counter += 1
            new_name = f"{base_name}_{counter}"
        return new_name

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
    def get_panel(*args, **kwargs):
        """Returns panel and panel configuration information.
        A fix for the broken pymel command `getPanel`.

        Parameters:
            [allConfigs=boolean], [allPanels=boolean], [allScriptedTypes=boolean], [allTypes=boolean], [configWithLabel=string], [containing=string], [invisiblePanels=boolean], [scriptType=string], [type=string], [typeOf=string], [underPointer=boolean], [visiblePanels=boolean], [withFocus=boolean], [withLabel=string])

        Returns:
            (str) An array of panel names.
        """
        from maya.cmds import getPanel  # pymel getPanel is broken in ver: 2022.

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
    def viewport_message(
        message="", status_message="", assist_message="", position="topCenter"
    ):
        """
        Parameters:
            message (str): The message to be displayed, (accepts html formatting).
            status_message (str): The status info message to be displayed (accepts html formatting).
            assist_message (str): The user assistance message to be displayed, (accepts html formatting).
            position (str): position on screen. possible values are: topCenter","topRight","midLeft","midCenter","midCenterTop","midCenterBot","midRight","botLeft","botCenter","botRight"

        Example:
            viewport_message("shutting down:<hl>"+str(timer)+"</hl>")
        """
        fontSize = 10
        fade = 1
        fadeInTime = 0
        fadeStayTime = 1000
        fadeOutTime = 500
        alpha = 75

        pm.inViewMessage(
            message=message,
            statusMessage=status_message,
            assistMessage=assist_message,
            position=position,
            fontSize=fontSize,
            fade=fade,
            fadeInTime=fadeInTime,
            fadeStayTime=fadeStayTime,
            fadeOutTime=fadeOutTime,
            alpha=alpha,
        )  # 1000ms = 1 sec

    @staticmethod
    def get_mel_globals(keyword=None, ignore_case=True):
        """Get global MEL variables."""
        variables = [
            v
            for v in sorted(pm.mel.eval("env"))
            if not keyword
            or (
                v.count(keyword)
                if not ignore_case
                else v.lower().count(keyword.lower())
            )
        ]
        return variables

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

    def clear_scroll_field_reporter():
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


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------
