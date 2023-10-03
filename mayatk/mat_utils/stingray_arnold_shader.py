# !/usr/bin/python
# coding=utf-8
import os
from PySide2 import QtWidgets

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.node_utils import NodeUtils
from mayatk.mat_utils import hdr_manager


class StingrayArnoldShader:
    @CoreUtils.undo
    def create_network(
        self,
        textures,
        name="",
        normalMapType="OpenGL",
        callback=print,
    ):
        """ """
        normal_map_created_from_other_type = False
        # Assure normalMapType is formatted as 'Normal_OpenGL' whether given as 'OpenGL' or 'Normal_OpenGL'
        normalMapType = "Normal_" + normalMapType.strip("Normal_")

        if not textures:
            callback(
                '<br><hl style="color:rgb(255, 100, 100);"><b>Error:</b> No textures given to create_network.</hl>'
            )
            return None
        try:
            pm.loadPlugin("mtoa", quiet=True)  # Assure arnold plugin is loaded.
            # Assure stringray plugin is loaded.
            pm.loadPlugin("shaderFXPlugin", quiet=True)

            sr_node = NodeUtils.create_render_node("StingrayPBS", name=name)
            ai_node = NodeUtils.create_render_node(
                "aiStandardSurface", name=name + "_ai" if name else ""
            )

            opacityMap = ptk.filter_images_by_type(textures, "Opacity")
            if opacityMap:
                maya_install_path = CoreUtils.get_maya_info("install_path")

                graph = os.path.join(
                    maya_install_path,
                    "presets",
                    "ShaderFX",
                    "Scenes",
                    "StingrayPBS",
                    "Standard_Transparent.sfx",
                )
                pm.shaderfx(sfxnode="StingrayPBS1", loadGraph=graph)

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

            srSG_node = NodeUtils.get_connected_nodes(
                sr_node,
                node_type="shadingEngine",
                direction="outgoing",
                first_match=True,
            )

            aiMult_node = pm.shadingNode("aiMultiply", asShader=True)

            bump_node = pm.shadingNode("bump2d", asShader=True)
            bump_node.bumpInterp.set(1)  # Set bump node to 'tangent space normals'

            NodeUtils.connect_multi_attr(  # Set node connections.
                (ai_node.outColor, srSG_node.aiSurfaceShader),
                (aiMult_node.outColor, ai_node.baseColor),
                (bump_node.outNormal, ai_node.normalCamera),
            )

            length = len(textures)
            progress = 0
            for f in textures:
                typ = ptk.get_image_type_from_filename(f)

                progress += 1

                # Filter normal maps for the correct type.
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
                    n1 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                    )
                    pm.connectAttr(n1.outColor, sr_node.TEX_color_map, force=True)
                    sr_node.use_color_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                        texture_node=True,
                        colorSpace="Raw",
                        ignoreColorSpaceFileRules=1,
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
                    # Opacity: same roughness map used in Specular Roughness to provide additional bluriness of refraction.
                    pm.connectAttr(
                        n2.outAlpha, ai_node.transmissionExtraRoughness, force=True
                    )

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
                    n1 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                    )
                    pm.connectAttr(n1.outColor, sr_node.TEX_emissive_map, force=True)
                    sr_node.use_emissive_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                        colorSpace="Raw",
                        texture_node=True,
                        ignoreColorSpaceFileRules=1,
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
                    n1 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                    )
                    pm.connectAttr(n1.outColor, sr_node.TEX_ao_map, force=True)
                    sr_node.use_ao_map.set(1)

                    n2 = NodeUtils.create_render_node(
                        "file",
                        "as2DTexture",
                        tex=f,
                        texture_node=True,
                        colorSpace="Raw",
                        ignoreColorSpaceFileRules=1,
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
                        # Do not show a warning for unconnected normal maps if it resulted from being converted to a different output type.
                        continue
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


class StingrayArnoldShaderSlots(StingrayArnoldShader):
    msg_intro = """<u>To setup the material:</u>
        <br>• Use the <b>Get Texture Maps</b> button to load the images you intend to use.
        <br>• Click the <b>Create Network</b> button to create the shader connections.

        <p><b>Note:</b> To correctly render opacity and transmission, the Opaque setting needs to be disabled on the Shape node.
        If Opaque is enabled, opacity will not work at all. Transmission will work, however any shadows cast by
        the object will always be solid and not pick up the Transparent Color or density of the shader.</p>
    """
    msg_completed = '<br><hl style="color:rgb(0, 255, 255);"><b>COMPLETED.</b></hl>'

    def __init__(self, **kwargs):
        super().__init__()

        self.sb = self.switchboard()
        self.ui = self.sb.stingray_arnold_shader
        self.workspace_dir = CoreUtils.get_maya_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")
        self.image_files = None

        self.ui.txt001.setText(self.msg_intro)
        self.init_header_menu()

    def init_header_menu(self):
        """Configure header menu"""
        self.ui.header.menu.setTitle("OPTIONS")
        self.ui.header.menu.add(
            self.sb.PushButton,
            "HDR Manager",
            setText="Open HDR Manager",
            setObjectName="b002",
        )

        module = hdr_manager
        slot_class = module.HdrManagerSlots

        # Register and configure HDR Manager UI
        self.sb.register("hdr_manager.ui", slot_class, base_dir=module)
        ui = self.sb.hdr_manager
        ui.set_attributes(WA_TranslucentBackground=True)
        ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
        ui.set_style(theme="dark", style_class="translucentBgWithBorder")
        ui.header.configureButtons(hide_button=True)

        # Connect button click to show HDR Manager
        self.ui.header.menu.b002.clicked.connect(lambda: ui.show(pos="cursor"))

    @property
    def mat_name(self) -> str:
        """Get the mat name from the user input text field.

        Returns:
            (str)
        """
        text = self.ui.txt000.text()
        return text

    @property
    def normal_map_type(self) -> str:
        """Get the normal map type from the comboBoxes current text.

        Returns:
            (str)
        """
        text = self.ui.cmb001.currentText()
        return text

    def b000(self):
        """Create network."""
        if self.image_files:
            # pm.mel.HypershadeWindow() #open the hypershade window.

            self.ui.txt001.clear()
            self.callback("Creating network ..<br>")

            self.create_network(
                self.image_files,
                self.mat_name,
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
            directory=self.source_images_dir,
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
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "stingray_arnold_shader.ui")
    sb = Switchboard(
        parent, ui_location=ui_file, slot_location=StingrayArnoldShaderSlots
    )

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(
        menu_button=True, minimize_button=True, hide_button=True
    )
    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
