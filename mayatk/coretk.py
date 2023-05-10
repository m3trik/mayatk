# !/usr/bin/python
# coding=utf-8
import os, sys

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
from pythontk import Iter

# from this package:
from mayatk import nodetk


class Core:
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
    def getMainWindow():
        """Get maya's main window object.

        Returns:
                (QWidget)
        """
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if not app:
            return print(
                f"# Warning: {__file__} in getMainWindow\n#\tCould not find QApplication instance."
            )

        main_window = next(
            iter(w for w in app.topLevelWidgets() if w.objectName() == "MayaWindow"),
            None,
        )
        if not main_window:
            return print(
                f"# Warning: {__file__} in getMainWindow\n#\tCould not find main window instance."
            )

        return main_window

    @staticmethod
    def appendMayaPaths(maya_version=2023):
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
                f"# Error: {__file__} in appendMayaPaths\n#\tCould not find the Maya installation path.\n#\tPlease make sure that the MAYA_LOCATION environment variable is set."
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
    def wrapControl(controlName, container):
        """Embed a Maya Native UI Object.

        Parameters:
                controlName (str): The name of an existing maya control. ie. 'cmdScrollFieldReporter1'
                container (obj): A widget instance in which to wrap the control.

        Example:
                modelPanelName = pm.modelPanel("embeddedModelPanel#", cam='persp')
                wrapControl(modelPanelName, QtWidgets.QtWidget())
        """
        from PySide2 import QtWidgets
        from shiboken2 import wrapInstance
        from maya.OpenMayaUI import MQtUtil

        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layoutName = Str.setCase(
            container.objectName() + "Layout", "camel"
        )  # results in '<objectName>Layout' or 'layout' if container objectName is ''
        layout.setObjectName(layoutName)
        pm.setParent(layoutName)

        from uitk.switchboard import Switchboard

        derivedClass = Switchboard.getDerivedType(container)

        ptr = MQtUtil.findControl(
            controlName
        )  # get a pointer to the maya api paneLayout.
        control = wrapInstance(int(ptr), derivedClass)
        layout.addWidget(control)

        return control

    @staticmethod
    def mfnMeshGenerator(objects):
        """Generate mfn mesh from the given list of objects.

        Parameters:
                objects (str)(obj(list): The objects to convert to mfn mesh.

        Returns:
                (generator)
        """
        import maya.OpenMaya as om

        selectionList = om.MSelectionList()
        for mesh in nodetk.Node.getShapeNode(pm.ls(objects)):
            selectionList.add(mesh)

        for i in range(selectionList.length()):
            dagPath = om.MDagPath()
            selectionList.getDagPath(i, dagPath)
            # print (dagPath.fullPathName()) #debug
            mfnMesh = om.MFnMesh(dagPath)
            yield mfnMesh

    @staticmethod
    def getArrayType(lst):
        """Determine if the given element(s) type.
        Samples only the first element.

        Parameters:
                obj (str/obj/list): The components(s) to query.

        Returns:
                (list) 'str', 'obj'(shape node), 'transform'(as string), 'int'(valid only at sub-object level)

        Example:
        getArrayType('cyl.vtx[0]') #returns: 'transform'
        getArrayType('cylShape.vtx[:]') #returns: 'str'
        """
        try:
            o = Iter.makeList(lst)[0]
        except IndexError as error:
            # print (f'# Error: {__file__} in getArrayType:\n#\tOperation requires at least one object.\n#\t{error}')
            return ""

        return "str" if isinstance(o, str) else "int" if isinstance(o, int) else "obj"

    @staticmethod
    def convertArrayType(lst, returnType="str", flatten=False):
        """Convert the given element(s) to <obj>, 'str', or int values.

        Parameters:
                lst (str/obj/list): The components(s) to convert.
                returnType (str): The desired returned array element type.
                        valid: 'str'(default), 'obj', 'int'(valid only at sub-object level).
                flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
                (list)(dict) return a dict only with a return type of 'int' and more that one object given.

        Example:
        convertArrayType('obj.vtx[:2]', 'str') #returns: ['objShape.vtx[0:2]']
        convertArrayType('obj.vtx[:2]', 'str', True) #returns: ['objShape.vtx[0]', 'objShape.vtx[1]', 'objShape.vtx[2]']
        convertArrayType('obj.vtx[:2]', 'obj') #returns: [MeshVertex('objShape.vtx[0:2]')]
        convertArrayType('obj.vtx[:2]', 'obj', True) #returns: [MeshVertex('objShape.vtx[0]'), MeshVertex('objShape.vtx[1]'), MeshVertex('objShape.vtx[2]')]
        convertArrayType('obj.vtx[:2]', 'int')) #returns: {nt.Mesh('objShape'): [(0, 2)]}
        convertArrayType('obj.vtx[:2]', 'int', True)) #returns: {nt.Mesh('objShape'): [0, 1, 2]}
        """
        lst = pm.ls(lst, flatten=flatten)
        if not lst or isinstance(lst[0], int):
            return []

        if returnType == "int":
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
                        f"# Error: {__file__} in convertArrayType\n#\tunable to convert {obj} {num} to int.\n#\t{error}"
                    )
                    break

            objects = set(pm.ls(lst, objectsOnly=True))
            if (
                len(objects) == 1
            ):  # flatten the dict values from 'result' and remove any duplicates.
                flattened = Iter.flatten(result.values())
                result = Iter.removeDuplicates(flattened)

        elif returnType == "str":
            result = list(map(str, lst))

        else:
            result = lst

        return result

    @staticmethod
    def getParameterValuesMEL(node, cmd, parameters):
        """Query a Maya command, and return a key:value pair for each of the given parameters.

        Parameters:
                node (str/obj/list): The object to query attributes of.
                parameters (list): The command parameters to query. ie. ['enableTranslationX','translationX']

        Returns:
                (dict) {'parameter name':<value>} ie. {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]}

        Example: getParameterValuesMEL(obj, 'transformLimits', ['enableTranslationX','translationX'])
        """
        cmd = getattr(pm, cmd)
        node = pm.ls(node)[0]

        result = {}
        for p in parameters:
            values = cmd(
                node, **{"q": True, p: True}
            )  # query the parameter to get it's value.

            result[p] = values

        return result

    @staticmethod
    def setParameterValuesMEL(node, cmd, parameters):
        """Set parameters using a maya command.

        Parameters:
                node (str/obj/list): The object to query attributes of.
                parameters (dict): The command's parameters and their desired values. ie. {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]}

        Example: setParameterValuesMEL(obj, 'transformLimits', {'enableTranslationX': [False, False], 'translationX': [-1.0, 1.0]})
        """
        cmd = getattr(pm, cmd)
        node = pm.ls(node)[0]

        for p, v in parameters.items():
            cmd(node, **{p: v})

    @staticmethod
    def getSelectedChannels():
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
    def getPanel(*args, **kwargs):
        """Returns panel and panel configuration information.
        A fix for the broken pymel command of the same name.

        Parameters:
                [allConfigs=boolean], [allPanels=boolean], [allScriptedTypes=boolean], [allTypes=boolean], [configWithLabel=string], [containing=string], [invisiblePanels=boolean], [scriptType=string], [type=string], [typeOf=string], [underPointer=boolean], [visiblePanels=boolean], [withFocus=boolean], [withLabel=string])

        Returns:
                (str) An array of panel names.
        """
        from maya.cmds import getPanel  # pymel getPanel is broken in ver: 2022.

        result = getPanel(*args, **kwargs)

        return result

    @staticmethod
    def mainProgressBar(size, name="progressBar#", stepAmount=1):
        """#add esc key pressed return False

        Parameters:
                size (int): total amount
                name (str): name of progress bar created
                stepAmount(int): increment amount

        example use-case:
        mainProgressBar (len(edges), progressCount)
                pm.progressBar ("progressBar_", edit=1, step=1)
                if pm.progressBar ("progressBar_", query=1, isCancelled=1):
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
            step=stepAmount,
        )

    @staticmethod
    def viewportMessage(
        message="", statusMessage="", assistMessage="", position="topCenter"
    ):
        """
        Parameters:
                message (str): The message to be displayed, (accepts html formatting). General message, inherited by -amg/assistMessage and -smg/statusMessage.
                statusMessage (str): The status info message to be displayed (accepts html formatting).
                assistMessage (str): The user assistance message to be displayed, (accepts html formatting).
                position (str): position on screen. possible values are: topCenter","topRight","midLeft","midCenter","midCenterTop","midCenterBot","midRight","botLeft","botCenter","botRight"

        ex. viewportMessage("shutting down:<hl>"+str(timer)+"</hl>")
        """
        fontSize = 10
        fade = 1
        fadeInTime = 0
        fadeStayTime = 1000
        fadeOutTime = 500
        alpha = 75

        if message:
            pm.inViewMessage(
                message=message,
                position=position,
                fontSize=fontSize,
                fade=fade,
                fadeInTime=fadeInTime,
                fadeStayTime=fadeStayTime,
                fadeOutTime=fadeOutTime,
                alpha=alpha,
            )  # 1000ms = 1 sec
        elif statusMessage:
            pm.inViewMessage(
                statusMessage=statusMessage,
                position=position,
                fontSize=fontSize,
                fade=fade,
                fadeInTime=fadeInTime,
                fadeStayTime=fadeStayTime,
                fadeOutTime=fadeOutTime,
                alpha=alpha,
            )  # 1000ms = 1 sec
        elif assistMessage:
            pm.inViewMessage(
                assistMessage=assistMessage,
                position=position,
                fontSize=fontSize,
                fade=fade,
                fadeInTime=fadeInTime,
                fadeStayTime=fadeStayTime,
                fadeOutTime=fadeOutTime,
                alpha=alpha,
            )  # 1000ms = 1 sec

    @staticmethod
    def outputText(text, window_title):
        """output text"""
        # window_title = pm.mel.eval(python("window_title"))
        window = str(
            pm.window(
                widthHeight=(300, 300),
                topLeftCorner=(65, 265),
                maximizeButton=False,
                resizeToFitChildren=True,
                toolbox=True,
                title=window_title,
            )
        )
        scrollLayout = str(
            pm.scrollLayout(
                verticalScrollBarThickness=16, horizontalScrollBarThickness=16
            )
        )
        pm.columnLayout(adjustableColumn=True)
        text_field = str(pm.text(label=text, align="left"))
        print(text_field)
        pm.setParent("..")
        pm.showWindow(window)
        return

    # #output textfield parsed by ';'
    # def outputTextField2(text):
    #   window = str(pm.window( widthHeight=(250, 650),
    #                           topLeftCorner=(50,275),
    #                           maximizeButton=False,
    #                           resizeToFitChildren=False,
    #                           toolbox=True,
    #                           title=""))
    #   scrollLayout = str(pm.scrollLayout(verticalScrollBarThickness=16,
    #                                   horizontalScrollBarThickness=16))
    #   pm.columnLayout(adjustableColumn=True)
    #   print(text)
    #   #for item in array:
    #   text_field = str(pm.textField(height=20,
    #                                       width=250,
    #                                       editable=False,
    #                                       insertText=str(text)))
    #   pm.setParent('..')
    #   pm.showWindow(window)
    #   return

    @staticmethod
    def outputscrollField(text, window_title, width, height):
        """Create an output scroll layout."""
        window_width = width * 300
        window_height = height * 600
        scroll_width = width * 294
        scroll_height = height * 590
        window = str(
            pm.window(
                widthHeight=(window_width, window_height),
                topLeftCorner=(45, 0),
                maximizeButton=False,
                sizeable=False,
                title=window_title,
            )
        )
        scrollLayout = str(
            pm.scrollLayout(
                verticalScrollBarThickness=16, horizontalScrollBarThickness=16
            )
        )
        pm.columnLayout(adjustableColumn=True)
        scroll_field = str(
            pm.scrollField(
                text=(text),
                width=scroll_width,
                height=scroll_height,
            )
        )
        print(window)
        pm.setParent("..")
        pm.showWindow(window)
        return scroll_field

    @staticmethod
    def outputTextField(array, window_title):
        """Create an output text field."""
        window = str(
            pm.window(
                widthHeight=(250, 650),
                topLeftCorner=(65, 275),
                maximizeButton=False,
                resizeToFitChildren=False,
                toolbox=True,
                title=window_title,
            )
        )
        scrollLayout = str(
            pm.scrollLayout(
                verticalScrollBarThickness=16, horizontalScrollBarThickness=16
            )
        )
        pm.columnLayout(adjustableColumn=True)
        for item in array:
            text_field = str(
                pm.textField(height=20, width=500, editable=False, insertText=str(item))
            )
        pm.setParent("..")
        pm.showWindow(window)
        return text_field


# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------
