# !/usr/bin/python
# coding=utf-8
import os, sys

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
from pythontk import Iter, Str

# from this package:
from mayatk import node_utils


class Misc:
    """ """

    def undo(fn):
        """A decorator to place a function into Maya's undo chunk.
        Prevents the undo queue from breaking entirely if an exception is raised within the given function.

        Parameters:
            fn (obj): The decorated python function that will be placed into the undo que as a single entry.
        """

        def wrapper(*args, **kwargs):
            with pm.UndoChunk():
                rtn = fn(*args, **kwargs)
                return rtn

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
    def append_maya_paths(maya_version=2023):
        """Adds the required library paths for Maya to the system's Python path.

        Parameters:
            maya_version (int, str, optional): The version of Maya to add the paths for. Defaults to 2023.

        Returns:
            (None)
        """
        maya_install_path = os.environ.get(
            "MAYA_LOCATION"
        )  # Get the Maya installation path.
        if not maya_install_path:
            return print(
                f"# Error: {__file__} in append_maya_paths\n#\tCould not find the Maya installation path.\n#\tPlease make sure that the MAYA_LOCATION environment variable is set."
            )

        # Add the Maya libraries to the Python path.
        os.environ["PYTHONHOME"] = os.path.join(maya_install_path, "Python")
        os.environ["PATH"] = (
            os.path.join(maya_install_path, "bin") + ";" + os.environ["PATH"]
        )

        sys.path.append(os.path.join(maya_install_path, "bin"))
        sys.path.append(os.path.join(maya_install_path, "Python"))
        sys.path.append(os.path.join(maya_install_path, "Python", maya_version, "DLLs"))
        sys.path.append(os.path.join(maya_install_path, "Python", maya_version, "lib"))
        sys.path.append(
            os.path.join(maya_install_path, "Python", maya_version, "lib", "lib-tk")
        )
        sys.path.append(
            os.path.join(maya_install_path, "Python", maya_version, "lib", "plat-win")
        )
        sys.path.append(
            os.path.join(
                maya_install_path, "Python", maya_version, "lib", "site-packages"
            )
        )
        sys.path.append(
            os.path.join(
                maya_install_path, "devkit", "other", "pymel", "extras", "modules"
            )
        )
        sys.path.append(
            os.path.join(
                maya_install_path, "devkit", "other", "pymel", "extras", "completion"
            )
        )

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
        layoutName = Str.set_case(
            container.objectName() + "Layout", "camel"
        )  # results in '<objectName>Layout' or 'layout' if container objectName is ''
        layout.setObjectName(layoutName)
        pm.setParent(layoutName)

        from uitk.switchboard import Switchboard

        derivedClass = Switchboard.get_derived_type(container)

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

        selectionList = om.MSelectionList()
        for mesh in node_utils.Node.get_shape_node(pm.ls(objects)):
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
        try:
            o = Iter.make_iterable(array)[0]
        except IndexError:
            # print (f'# Error: {__file__} in get_array_type:\n#\tOperation requires at least one object.\n#\t{error}')
            return ""

        return (
            "str"
            if isinstance(o, str)
            else "int"
            if isinstance(o, int)
            else node_utils.Node.get_type(o)
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
                flattened = Iter.flatten(result.values())
                result = Iter.remove_duplicates(flattened)

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


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------
