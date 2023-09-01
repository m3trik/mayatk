# !/usr/bin/python
# coding=utf-8
from PySide2 import QtWidgets

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.node_utils import NodeUtils


__version__ = "0.5.3"


class StingrayArnoldShader:
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
        except IndexError:
            return None

    @hdr_env.setter
    def hdr_env(self, tex) -> None:
        """ """
        node = (
            self.hdr_env
        )  # NodeUtils.node_exists('aiSkyDomeLight', search='exactType')
        if not node:
            node = NodeUtils.create_render_node(
                "aiSkyDomeLight",
                "asLight",
                name=self.hdr_env_name,
                camera=0,
                skyRadius=0,
            )  # turn off skydome and viewport visibility.
            self.hdr_env_transform.hiddenInOutliner.set(1)
            pm.outlinerEditor("outlinerPanel1", edit=True, refresh=True)

        file_node = NodeUtils.get_incoming_node_by_type(node, "file")
        if not file_node:
            file_node = NodeUtils.create_render_node(
                "file", "as2DTexture", texture_node=True
            )
            pm.connectAttr(file_node.outColor, node.color, force=True)

        file_node.fileTextureName.set(str(tex))

    @property
    def hdr_env_transform(self) -> object:
        """ """
        node = NodeUtils.get_transform_node(self.hdr_env)
        if not node:
            return None
        return node

    def set_hdr_map_visibility(self, state):
        """ """
        node = self.hdr_env
        if node:
            node.camera.set(state)

    @CoreUtils.undo
    def create_network(
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
                '<br><hl style="color:rgb(255, 100, 100);"><b>Error:</b> No textures given to create_network.</hl>'
            )
            return None
        try:
            pm.loadPlugin("mtoa", quiet=True)  # assure arnold plugin is loaded.
            pm.loadPlugin(
                "shaderFXPlugin", quiet=True
            )  # assure stringray plugin is loaded.

            sr_node = NodeUtils.create_render_node("StingrayPBS", name=name)
            ai_node = NodeUtils.create_render_node(
                "aiStandardSurface", name=name + "_ai" if name else ""
            )

            opacityMap = ptk.filter_images_by_type(textures, "Opacity")
            if opacityMap:
                pm.shaderfx(
                    sfxnode="StingrayPBS1",
                    loadGraph=r"C:/_local/_test/shaderfx/Standard_Transparent.sfx",
                )

            openGLMap = ptk.filter_images_by_type(textures, "Normal_OpenGL")
            directXMap = ptk.filter_images_by_type(textures, "Normal_DirectX")
            if directXMap and not openGLMap and normalMapType == "Normal_OpenGL":
                mapPath = ptk.create_gl_from_dx(directXMap[0])
                textures.append(mapPath)
                normal_map_created_from_other_type = True
                callback(f"OpenGL map created using {ptk.truncate(directXMap[0], 20)}.")
            if openGLMap and not directXMap and normalMapType == "Normal_DirectX":
                mapPath = ptk.create_dx_from_gl(openGLMap[0])
                textures.append(mapPath)
                normal_map_created_from_other_type = True
                callback(f"DirectX map created using {ptk.truncate(openGLMap[0], 20)}.")

            srSG_node = NodeUtils.get_outgoing_node_by_type(sr_node, "shadingEngine")

            aiMult_node = pm.shadingNode("aiMultiply", asShader=True)

            bump_node = pm.shadingNode("bump2d", asShader=True)
            bump_node.bumpInterp.set(1)  # set bump node to 'tangent space normals'

            NodeUtils.connect_multi_attr(  # set node connections.
                (ai_node.outColor, srSG_node.aiSurfaceShader),
                (aiMult_node.outColor, ai_node.baseColor),
                (bump_node.outNormal, ai_node.normalCamera),
            )

            length = len(textures)
            progress = 0
            for f in textures:
                typ = ptk.get_image_type_from_filename(f)

                progress += 1

                # filter normal maps for the correct type.
                if typ == "Normal" and (openGLMap or directXMap):
                    continue
                elif typ == "Normal_DirectX" and normalMapType == "Normal_OpenGL":
                    continue
                elif typ == "Normal_OpenGL" and normalMapType == "Normal_DirectX":
                    continue

                callback(
                    f"creating nodes and connections for <b>{typ}</b> map ..",
                    [progress, length],
                )

                if typ == "Base_Color":
                    n1 = NodeUtils.create_render_node("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_color_map, force=True)
                    sr_node.use_color_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file", "as2DTexture", tex=f, texture_node=True
                    )
                    pm.connectAttr(n2.outColor, aiMult_node.input1, force=True)

                elif typ == "Roughness":
                    n1 = NodeUtils.create_render_node("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_roughness_map, force=True)
                    sr_node.use_roughness_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                        texture_node=True,
                        colorSpace="Raw",
                        alphaIsLuminance=1,
                        ignoreColorSpaceFileRules=1,
                    )
                    pm.connectAttr(n2.outAlpha, ai_node.specularRoughness, force=True)
                    pm.connectAttr(
                        n2.outAlpha, ai_node.transmissionExtraRoughness, force=True
                    )  # opacity: same roughness map used in Specular Roughness to provide additional bluriness of refraction.

                elif typ == "Metallic":
                    n1 = NodeUtils.create_render_node("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_metallic_map, force=True)
                    sr_node.use_metallic_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                        texture_node=True,
                        colorSpace="Raw",
                        alphaIsLuminance=1,
                        ignoreColorSpaceFileRules=1,
                    )
                    pm.connectAttr(n2.outAlpha, ai_node.metalness, force=True)

                elif typ == "Emissive":
                    n1 = NodeUtils.create_render_node("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_emissive_map, force=True)
                    sr_node.use_emissive_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file", "as2DTexture", tex=f, texture_node=True
                    )
                    pm.connectAttr(n2.outAlpha, ai_node.emission, force=True)
                    pm.connectAttr(n2.outColor, ai_node.emissionColor, force=True)

                elif "Normal" in typ:
                    n1 = NodeUtils.create_render_node("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_normal_map, force=True)
                    sr_node.use_normal_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                        texture_node=True,
                        colorSpace="Raw",
                        alphaIsLuminance=1,
                        ignoreColorSpaceFileRules=1,
                    )
                    pm.connectAttr(n2.outAlpha, bump_node.bumpValue, force=True)

                elif typ == "Ambient_Occlusion":
                    n1 = NodeUtils.create_render_node("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outColor, sr_node.TEX_ao_map, force=True)
                    sr_node.use_ao_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file", "as2DTexture", tex=f, texture_node=True
                    )
                    pm.connectAttr(n2.outColor, aiMult_node.input2, force=True)

                elif typ == "Opacity":
                    n1 = NodeUtils.create_render_node("file", "as2DTexture", tex=f)
                    pm.connectAttr(n1.outAlpha, sr_node.opacity, force=True)
                    sr_node.use_opacity_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                        texture_node=True,
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
                        f'<br><hl style="color:rgb(255, 100, 100);"><b>Map type: <b>{typ, ptk.truncate(f, 60)}</b> not connected:<br></hl>',
                        [progress, length],
                    )
                    continue

                callback(
                    f'<font style="color: rgb(80,180,100)">{typ}..connected successfully.</font>'
                )
        except Exception as e:
            callback(
                f'<br><hl style="color:rgb(255, 100, 100);"><b>Error:</b>{e}.</hl>'
            )

        self.hdr_env = hdrMap
        self.set_hdr_map_visibility(hdrMapVisibility)


class StingrayArnoldShaderSlots(StingrayArnoldShader):
    msg_intro = """<u>To setup the material:</u>
        <br>• Use the <b>Get Texture Maps</b> button to load the images you intend to use.
        <br>• Click the <b>Create Network</b> button to create the shader connections.
        <br>• The HDR map, it's visiblity, and rotation can be changed after creation.
    """
    msg_completed = '<br><hl style="color:rgb(0, 255, 255);"><b>COMPLETED.</b></hl>'

    proj_root_dir = ptk.get_object_path(__file__)

    def __init__(self, **kwargs):
        super().__init__()
        """ """
        self.sb = self.switchboard()
        self.ui = self.sb.stingray_arnold_shader
        self.image_files = None

        # Add filenames|filepaths to the comboBox.
        hdr_path = f"{self.proj_root_dir}/resources/hdr"
        hdr_filenames = ptk.get_dir_contents(hdr_path, "filename", inc_files="*.exr")
        hdr_fullpaths = ptk.get_dir_contents(hdr_path, "filepath", inc_files="*.exr")
        self.ui.cmb000.add(zip(hdr_filenames, hdr_fullpaths), ascending=False)

        self.ui.txt001.setText(self.msg_intro)

        node = self.hdr_env_transform
        if node:
            rotation = node.rotateY.get()
            self.ui.slider000.setSliderPosition(rotation)

    @property
    def mat_name(self) -> str:
        """Get the mat name from the user input text field.

        Returns:
            (str)
        """
        text = self.ui.txt000.text()
        return text

    @property
    def hdr_map(self) -> str:
        """Get the hdr map filepath from the comboBoxes current text.

        Returns:
            (str) data as string.
        """
        data = self.ui.cmb000.currentData()
        return data

    @property
    def hdr_map_visibility(self) -> bool:
        """Get the hdr map visibility state from the checkBoxes current state.

        Returns:
            (bool)
        """
        state = self.ui.chk000.isChecked()
        return state

    @property
    def normal_map_type(self) -> str:
        """Get the normal map type from the comboBoxes current text.

        Returns:
            (str)
        """
        text = self.ui.cmb001.currentText()
        return text

    def cmb000(self, index, widget):
        """HDR map selection."""
        data = widget.currentData()

        self.hdr_env = data  # set the HDR map.

    def chk000(self, state, widget):
        """ """
        self.set_hdr_map_visibility(state)  # set the HDR map visibility.

    def slider000(self, value, widget):
        """Rotate the HDR map."""
        if self.hdr_env:
            transform = NodeUtils.get_transform_node(self.hdr_env)
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
        if self.image_files:
            # pm.mel.HypershadeWindow() #open the hypershade window.

            self.ui.txt001.clear()
            self.callback("Creating network ..<br>")

            self.create_network(
                self.image_files,
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
        image_files = self.sb.file_dialog(
            file_types=["*.png", "*.jpg", "*.bmp", "*.tga", "*.tiff", "*.gif"],
            title="Select one or more image files to open.",
        )

        if image_files:
            self.image_files = image_files
            self.ui.txt001.clear()

            msg_mat_selection = self.image_files
            for (
                i
            ) in msg_mat_selection:  # format msg_intro using the map_types in imtools.
                self.callback(ptk.truncate(i, 60))

            self.ui.b000.setDisabled(False)
        elif not self.image_files:
            self.ui.b000.setDisabled(True)

    def callback(self, string, progress=None, clear=False):
        """
        Parameters:
            string (str): The text to output to a textEdit widget.
            progress (int/list): The progress amount to register with the progressBar.
                    Can be given as an int or a tuple as: (progress, total_len)
        """
        if clear:
            self.ui.txt003.clear()

        if isinstance(progress, (list, tuple, set)):
            p, length = progress
            progress = (p / length) * 100

        self.ui.txt001.append(string)

        if progress is not None:
            self.ui.progressBar.setValue(progress)
            QtWidgets.QApplication.instance().processEvents()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "stingray_arnold_shader.ui")
    sb = Switchboard(
        parent, ui_location=ui_file, slot_location=StingrayArnoldShaderSlots
    )

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(minimize_button=True, hide_button=True)
    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
