# !/usr/bin/python
# coding=utf-8
import sys, os
from PySide2 import QtCore, QtWidgets

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

from uitk import Switchboard
from pythontk import File, Img, Str

# from this package:
from mayatk.coretk import Core
from mayatk.nodetk import Node


__version__ = "0.5.3"


class Stingray_arnold_shader:
    """
    To correctly render opacity and transmission, the Opaque setting needs to be disabled on the Shape node.
    If Opaque is enabled, opacity will not work at all. Transmission will work however any shadows cast by
    the object will always be solid and not pick up the Transparent Color or density of the shader.
    """

    hdr_env_name = "aiSkyDomeLight_"

    @property
    def hdr_env(self) -> object:
        """ """
        node = pm.ls(self.hdr_env_name, exactType="aiSkyDomeLight")
        try:
            return node[0]
        except IndexError as error:
            return None

    @hdr_env.setter
    def hdr_env(self, tex) -> None:
        """ """
        node = self.hdr_env  # Node.nodeExists('aiSkyDomeLight', search='exactType')
        if not node:
            node = Node.createRenderNode(
                "aiSkyDomeLight",
                "asLight",
                name=self.hdr_env_name,
                camera=0,
                skyRadius=0,
            )  # turn off skydome and viewport visibility.
            self.hdr_env_transform.hiddenInOutliner.set(1)
            pm.outlinerEditor("outlinerPanel1", edit=True, refresh=True)

        file_node = Node.getIncomingNodeByType(node, "file")
        if not file_node:
            file_node = Node.createRenderNode(
                "file", "as2DTexture", place2dTexture=True
            )
            pm.connectAttr(file_node.outColor, node.color, force=True)

        file_node.fileTextureName.set(tex)

    @property
    def hdr_env_transform(self) -> object:
        """ """
        node = Node.getTransformNode(self.hdr_env)
        if not node:
            return None
        return node

    def setHdrMapVisibility(self, state):
        """ """
        node = self.hdr_env
        if node:
            node.camera.set(state)

    @Core.undo
    def createNetwork(
        self,
        textures,
        name="",
        hdrMap="",
        hdrMapVisibility=False,
        normalMapType="OpenGL",
        callback=print,
    ):
        """ """
        normal_map_created_from_other_type = False
        normalMapType = "Normal_" + normalMapType.strip(
            "Normal_"
        )  # assure normalMapType is formatted as 'Normal_OpenGL' whether given as 'OpenGL' or 'Normal_OpenGL'

        if not textures:
            callback(
                '<br><hl style="color:rgb(255, 100, 100);"><b>Error:</b> No textures given to createNetwork.</hl>'
            )
            return None
        try:
            pm.loadPlugin("mtoa", quiet=True)  # assure arnold plugin is loaded.
            pm.loadPlugin(
                "shaderFXPlugin", quiet=True
            )  # assure stringray plugin is loaded.

            sr_node = Node.createRenderNode("StingrayPBS", name=name)
            ai_node = Node.createRenderNode(
                "aiStandardSurface", name=name + "_ai" if name else ""
            )

            opacityMap = Img.filterImagesByType(textures, "Opacity")
            if opacityMap:
                pm.shaderfx(
                    sfxnode="StingrayPBS1",
                    loadGraph=r"C:/_local/_test/shaderfx/Standard_Transparent.sfx",
                )

            openGLMap = Img.filterImagesByType(textures, "Normal_OpenGL")
            directXMap = Img.filterImagesByType(textures, "Normal_DirectX")
            if directXMap and not openGLMap and normalMapType == "Normal_OpenGL":
                mapPath = Img.createGLFromDX(directXMap[0])
                textures.append(mapPath)
                normal_map_created_from_other_type = True
                callback(
                    "OpenGL map created using {}.".format(
                        Str.truncate(directXMap[0], 20)
                    )
                )
            if openGLMap and not directXMap and normalMapType == "Normal_DirectX":
                mapPath = Img.createDXFromGL(openGLMap[0])
                textures.append(mapPath)
                normal_map_created_from_other_type = True
                callback(
                    "DirectX map created using {}.".format(
                        Str.truncate(openGLMap[0], 20)
                    )
                )

            srSG_node = Node.getOutgoingNodeByType(sr_node, "shadingEngine")

            aiMult_node = pm.shadingNode("aiMultiply", asShader=True)

            bump_node = pm.shadingNode("bump2d", asShader=True)
            bump_node.bumpInterp.set(1)  # set bump node to 'tangent space normals'

            Node.connectMultiAttr(  # set node connections.
                (ai_node.outColor, srSG_node.aiSurfaceShader),
                (aiMult_node.outColor, ai_node.baseColor),
                (bump_node.outNormal, ai_node.normalCamera),
            )

            length = len(textures)
            progress = 0
            for f in textures:
                typ = Img.getImageTypeFromFilename(f)

                progress += 1

                # filter normal maps for the correct type.
                if typ == "Normal" and (openGLMap or directXMap):
                    continue
                elif typ == "Normal_DirectX" and normalMapType == "Normal_OpenGL":
                    continue
                elif typ == "Normal_OpenGL" and normalMapType == "Normal_DirectX":
                    continue

                callback(
                    "creating nodes and connections for <b>{}</b> map ..".format(typ),
                    [progress, length],
                )

                if typ == "Base_Color":
                    n1 = Node.createRenderNode("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_color_map, force=True)
                    sr_node.use_color_map.set(1)

                    n2 = Node.createRenderNode(
                        "file", "as2DTexture", tex=f, place2dTexture=True
                    )
                    pm.connectAttr(n2.outColor, aiMult_node.input1, force=True)

                elif typ == "Roughness":
                    n1 = Node.createRenderNode("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_roughness_map, force=True)
                    sr_node.use_roughness_map.set(1)

                    n2 = Node.createRenderNode(
                        "file",
                        "as2DTexture",
                        tex=f,
                        place2dTexture=True,
                        colorSpace="Raw",
                        alphaIsLuminance=1,
                        ignoreColorSpaceFileRules=1,
                    )
                    pm.connectAttr(n2.outAlpha, ai_node.specularRoughness, force=True)
                    pm.connectAttr(
                        n2.outAlpha, ai_node.transmissionExtraRoughness, force=True
                    )  # opacity: same roughness map used in Specular Roughness to provide additional bluriness of refraction.

                elif typ == "Metallic":
                    n1 = Node.createRenderNode("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_metallic_map, force=True)
                    sr_node.use_metallic_map.set(1)

                    n2 = Node.createRenderNode(
                        "file",
                        "as2DTexture",
                        tex=f,
                        place2dTexture=True,
                        colorSpace="Raw",
                        alphaIsLuminance=1,
                        ignoreColorSpaceFileRules=1,
                    )
                    pm.connectAttr(n2.outAlpha, ai_node.metalness, force=True)

                elif typ == "Emissive":
                    n1 = Node.createRenderNode("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_emissive_map, force=True)
                    sr_node.use_emissive_map.set(1)

                    n2 = Node.createRenderNode(
                        "file", "as2DTexture", tex=f, place2dTexture=True
                    )
                    pm.connectAttr(n2.outAlpha, ai_node.emission, force=True)
                    pm.connectAttr(n2.outColor, ai_node.emissionColor, force=True)

                elif "Normal" in typ:
                    n1 = Node.createRenderNode("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_normal_map, force=True)
                    sr_node.use_normal_map.set(1)

                    n2 = Node.createRenderNode(
                        "file",
                        "as2DTexture",
                        tex=f,
                        place2dTexture=True,
                        colorSpace="Raw",
                        alphaIsLuminance=1,
                        ignoreColorSpaceFileRules=1,
                    )
                    pm.connectAttr(n2.outAlpha, bump_node.bumpValue, force=True)

                elif typ == "Ambient_Occlusion":
                    n1 = Node.createRenderNode("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_ao_map, force=True)
                    sr_node.use_ao_map.set(1)

                    n2 = Node.createRenderNode(
                        "file", "as2DTexture", tex=f, place2dTexture=True
                    )
                    pm.connectAttr(n2.outColor, aiMult_node.input2, force=True)

                elif typ == "Opacity":
                    n1 = Node.createRenderNode("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outAlpha, sr_node.opacity, force=True)
                    sr_node.use_opacity_map.set(1)

                    n2 = Node.createRenderNode(
                        "file",
                        "as2DTexture",
                        tex=f,
                        place2dTexture=True,
                        colorSpace="Raw",
                        alphaIsLuminance=1,
                        ignoreColorSpaceFileRules=1,
                    )
                    pm.connectAttr(n2.outAlpha, ai_node.transmission, force=True)
                    pm.connectAttr(n2.outColor, ai_node.opacity, force=True)

                else:
                    if normal_map_created_from_other_type:
                        continue  # do not show a warning for unconnected normal maps if it resulted from being converted to a different output type.
                    callback(
                        '<br><hl style="color:rgb(255, 100, 100);"><b>Map type: <b>{}</b> not connected:<br></hl>'.format(
                            typ, Str.truncate(f, 60)
                        ),
                        [progress, length],
                    )
                    continue

                callback(
                    '<font style="color: rgb(80,180,100)">{}..connected successfully.</font>'.format(
                        typ
                    )
                )
        except Exception as error:
            callback(
                '<br><hl style="color:rgb(255, 100, 100);"><b>Error:</b>{}.</hl>'.format(
                    error
                )
            )

        self.hdr_env = hdrMap
        self.setHdrMapVisibility(hdrMapVisibility)


class Stingray_arnold_shader_slots(Stingray_arnold_shader):
    msg_intro = """<u>To setup the material:</u>
        <br>• Use the <b>Get Texture Maps</b> button to load the images you intend to use.
        <br>• Click the <b>Create Network</b> button to create the shader connections.
        <br>• The HDR map, it's visiblity, and rotation can be changed after creation.
    """
    msg_completed = '<br><hl style="color:rgb(0, 255, 255);"><b>COMPLETED.</b></hl>'

    proj_root_dir = File.getFilepath(__file__)

    def __init__(self, **kwargs):
        super().__init__()
        """
        """
        self.sb = self.switchboard()
        self.imageFiles = None

        # set json file location.
        path = "{}/stingray_arnold_shader.json".format(self.sb.defaultDir)
        File.setJsonFile(path)  # set json file name

        # add filenames|filepaths to the comboBox.
        hdr_path = "{}/resources/hdr".format(self.proj_root_dir)
        hdr_filenames = File.getDirContents(hdr_path, "filenames", incFiles="*.exr")
        hdr_fullpaths = File.getDirContents(hdr_path, "filepaths", incFiles="*.exr")
        self.sb.ui.cmb000.addItems_(
            dict(zip(hdr_filenames, hdr_fullpaths)), ascending=False
        )

        # initialize widgets with any saved values.
        self.sb.ui.txt000.setText(File.getJson("mat_name"))
        self.sb.ui.txt001.setText(self.msg_intro)
        hdr_map_visibility = File.getJson("hdr_map_visibility")
        if hdr_map_visibility:
            self.sb.ui.chk000.setChecked(hdr_map_visibility)
        hdr_map = File.getJson("hdr_map")
        if hdr_map:
            self.sb.ui.cmb000.setCurrentItem(hdr_map)
        normal_map_type = File.getJson("normal_map_type")
        if normal_map_type:
            self.sb.ui.cmb001.setCurrentItem(normal_map_type)
        node = self.hdr_env_transform
        if node:
            rotation = node.rotateY.get()
            self.sb.ui.slider000.setSliderPosition(rotation)

    @property
    def mat_name(self) -> str:
        """Get the mat name from the user input text field.

        Returns:
                (str)
        """
        text = self.sb.ui.txt000.text()
        return text

    @property
    def hdr_map(self) -> str:
        """Get the hdr map filepath from the comboBoxes current text.

        Returns:
                (str) data as string.
        """
        data = self.sb.ui.cmb000.currentData()
        return data

    @property
    def hdr_map_visibility(self) -> bool:
        """Get the hdr map visibility state from the checkBoxes current state.

        Returns:
                (bool)
        """
        state = self.sb.ui.chk000.isChecked()
        return state

    @property
    def normal_map_type(self) -> str:
        """Get the normal map type from the comboBoxes current text.

        Returns:
                (str)
        """
        text = self.sb.ui.cmb001.currentText()
        return text

    def cmb000(self, index):
        """HDR map selection."""
        cmb = self.sb.ui.cmb000
        text = cmb.currentText()
        data = cmb.currentData()

        self.hdr_env = data  # set the HDR map.
        File.setJson("hdr_map", text)

    def chk000(self, state):
        """ """
        chk = self.sb.ui.chk000

        self.setHdrMapVisibility(state)  # set the HDR map visibility.
        File.setJson("hdr_map_visibility", state)

    def cmb001(self, index):
        """Normal map output selection."""
        cmb = self.sb.ui.cmb001
        text = cmb.currentText()
        File.setJson("normal_map_type", text)

    def txt000(self, text=None):
        """Material name."""
        txt = self.sb.ui.txt000
        text = txt.text()
        File.setJson("mat_name", text)

    def slider000(self, value):
        """Rotate the HDR map."""
        if self.hdr_env:
            transform = Node.getTransformNode(self.hdr_env)
            pm.rotate(
                transform,
                value,
                rotateY=True,
                forceOrderXYZ=True,
                objectSpace=True,
                absolute=True,
            )

    def b000(self):
        """Create network."""
        if self.imageFiles:
            # pm.mel.HypershadeWindow() #open the hypershade window.

            self.sb.ui.txt001.clear()
            self.callback("Creating network ..<br>")

            self.createNetwork(
                self.imageFiles,
                self.mat_name,
                hdrMap=self.hdr_map,
                hdrMapVisibility=self.hdr_map_visibility,
                normalMapType=self.normal_map_type,
                callback=self.callback,
            )

            self.callback(self.msg_completed)
            # pm.mel.hyperShadePanelGraphCommand('hyperShadePanel1', 'rearrangeGraph')

    def b001(self):
        """Get texture maps."""
        imageFiles = Img.getImageFiles()

        if imageFiles:
            self.imageFiles = imageFiles
            self.sb.ui.txt001.clear()

            msg_mat_selection = self.imageFiles
            for (
                i
            ) in msg_mat_selection:  # format msg_intro using the mapTypes in imtools.
                self.callback(Str.truncate(i, 60))

            self.sb.ui.b000.setDisabled(False)
        elif not self.imageFiles:
            self.sb.ui.b000.setDisabled(True)

    def callback(self, string, progress=None, clear=False):
        """
        Parameters:
                string (str): The text to output to a textEdit widget.
                progress (int/list): The progress amount to register with the progressBar.
                        Can be given as an int or a tuple as: (progress, total_len)
        """
        if clear:
            self.sb.ui.txt003.clear()

        if isinstance(progress, (list, tuple, set)):
            p, l = progress
            progress = (p / l) * 100

        self.sb.ui.txt001.append(string)

        if progress is not None:
            self.sb.ui.progressBar.setValue(progress)
            QtWidgets.QApplication.instance().processEvents()


class Stingray_arnold_shader_sb(Switchboard):
    """Constructs the main ui window for `Stingray_arnold_shader` class."""

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent)

        self.ui_location = "stingray_arnold_shader.ui"
        self.slots_location = Stingray_arnold_shader_slots

        self.ui.uitk.widgets.draggableHeader.DraggableHeader.hide()
        self.ui.txt001.hide()
        self.ui.toggle_expand.clicked.connect(self.toggleTextEdit)

        self.ui.resize(self.ui.sizeHint())

    def toggleTextEdit(self):
        txt = self.ui.txt001
        if txt.isVisible():
            self._height_open = self.ui.height()
            txt.hide()
            self.ui.resize(self.ui.width(), self._height_closed)
        else:
            self._height_closed = self.ui.height()
            txt.show()
            self.ui.resize(
                self.ui.width(),
                self._height_open
                if hasattr(self, "_height_open")
                else self.ui.sizeHint().height(),
            )


# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    parent = Core.getMainWindow()
    sb = Stingray_arnold_shader_sb(parent)
    sb.ui.show()

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------


# Deprecated ------------------------------------------------------------------