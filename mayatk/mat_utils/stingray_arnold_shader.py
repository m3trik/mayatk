# !/usr/bin/python
# coding=utf-8
import os
from typing import List, Optional, Tuple, Callable

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import _core_utils
from mayatk.node_utils import NodeUtils


class StingrayArnoldShader:
    """A class to manage the creation of a shader network using StingrayPBS and optionally Arnold shaders.
    This class facilitates the automatic setup of textures into a StingrayPBS shader and, if requested,
    an Arnold shader network, linking necessary nodes and setting up the shader graph based on the provided textures.
    """

    color_info = "rgb(100, 100, 160)"
    color_success = "rgb(100, 160, 100)"
    color_warning = "rgb(200, 200, 100)"
    color_error = "rgb(255, 100, 100)"

    @_core_utils.CoreUtils.undo
    def create_network(
        self,
        textures: List[str],
        name: str = "",
        normal_type: str = "OpenGL",
        create_arnold: bool = False,
        albedo_transparency: bool = False,
        metallic_smoothness: bool = False,
        callback: Callable = print,
    ) -> Optional[object]:
        """ """
        if not textures:
            callback(
                f'<br><hl style="color:{self.color_error};"><b>Error:</b> No textures given to create_network.</hl>'
            )
            return None

        textures = self.filter_for_correct_base_color_map(textures, albedo_transparency)
        textures = self.filter_for_correct_metallic_map(textures, metallic_smoothness)
        textures = self.filter_for_correct_normal_map(textures, normal_type)

        opacity_map = ptk.filter_images_by_type(
            textures, ["Opacity", "Albedo_Transparency"]
        )

        name = name if name else ptk.get_base_texture_name(textures[0])

        sr_node = self.setup_stringray_node(name, opacity_map)

        # Optional: Arnold shader creation
        if create_arnold:
            ai_node, aiMult_node, bump_node = self.setup_arnold_nodes(name, sr_node)

        # Process each texture
        length = len(textures)
        progress = 0
        for texture in textures:
            progress += 1
            texture_name = ptk.format_path(texture, "file")
            texture_type = ptk.get_image_type_from_filename(texture)

            if texture_type is None:
                callback(
                    f'<br><hl style="color:{self.color_error};"><b>Unknown map type: </b>{texture_name}.</hl>',
                    [progress, length],
                )
                continue

            # Connect Stingray nodes
            success = self.connect_stingray_nodes(texture, texture_type, sr_node)
            if success:
                callback(
                    f'<br><hl style="color:{self.color_success};">Map type: <b>{texture_type}</b> connected.</hl>',
                    [progress, length],
                )
            else:
                callback(
                    f'<br><hl style="color:{self.color_warning};">Map type: <b>{texture_type}</b> not connected.</hl>',
                    [progress, length],
                )

            # Conditional Arnold nodes connection
            if create_arnold and success:
                self.connect_arnold_nodes(
                    texture, texture_type, ai_node, aiMult_node, bump_node
                )

    def setup_stringray_node(self, name: str, opacity: bool) -> object:
        """Initializes and sets up a StingrayPBS shader node in Maya.

        Loads the ShaderFX plugin if not already loaded, creates a new StingrayPBS node
        with the given name, and optionally sets it up for transparency using a preset graph.

        Parameters:
            name (str): The desired name for the StingrayPBS shader node.
            opacity (bool): Flag to indicate whether the shader should support opacity
                            (transparent materials). If True, a transparency-enabled graph
                            is loaded into the shader node.

        Returns:
            pm.nt.StingrayPBS: The created StingrayPBS shader node.
        """
        _core_utils.CoreUtils.load_plugin("shaderFXPlugin")  # Load Stingray plugin

        # Create StingrayPBS node
        sr_node = NodeUtils.create_render_node("StingrayPBS", name=name)

        if opacity:
            maya_install_path = _core_utils.CoreUtils.get_maya_info("install_path")

            graph = os.path.join(
                maya_install_path,
                "presets",
                "ShaderFX",
                "Scenes",
                "StingrayPBS",
                "Standard_Transparent.sfx",
            )
            pm.shaderfx(sfxnode="StingrayPBS1", loadGraph=graph)

        return sr_node

    def setup_arnold_nodes(
        self, name: str, sr_node: object
    ) -> Tuple[object, object, object]:
        """Sets up a basic Arnold shader network for use with a StingrayPBS node.

        This method loads the MtoA plugin if not already loaded, creates an aiStandardSurface
        shader, an aiMultiply utility node, and a bump2d node for normal mapping. It connects
        these nodes together and to the StingrayPBS node's shading engine to integrate Arnold
        rendering with Stingray materials.

        Parameters:
            name (str): Base name for the created Arnold nodes. The names will have suffixes
                        '_ai', '_multiply', and '_bump' respectively.
            sr_node (object): The StingrayPBS node that the Arnold shader network is being
                              set up for. This is used to find the connected shading engine.

        Returns:
            Tuple[pm.nt.AiStandardSurface, pm.nt.AiMultiply, pm.nt.Bump2d]: A tuple containing
            the created aiStandardSurface node, aiMultiply node, and bump2d node, in that order.
        """
        _core_utils.CoreUtils.load_plugin("mtoa")  # Load Arnold plugin

        ai_node = NodeUtils.create_render_node(
            "aiStandardSurface", name=name + "_ai" if name else ""
        )
        aiMult_node = pm.shadingNode("aiMultiply", asShader=True)
        bump_node = pm.shadingNode("bump2d", asShader=True)
        bump_node.bumpInterp.set(1)  # Set to tangent space normals

        srSG_node = NodeUtils.get_connected_nodes(
            sr_node,
            node_type="shadingEngine",
            direction="outgoing",
            first_match=True,
        )

        # Connect Arnold nodes
        NodeUtils.connect_multi_attr(
            (ai_node.outColor, srSG_node.aiSurfaceShader),
            (aiMult_node.outColor, ai_node.baseColor),
            (bump_node.outNormal, ai_node.normalCamera),
        )
        return ai_node, aiMult_node, bump_node

    def connect_stingray_nodes(
        self, texture: str, texture_type: str, sr_node: object
    ) -> bool:
        """Connects texture files to the corresponding slots in the StingrayPBS shader node
        based on the texture type, including handling various specific texture types.

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of the texture (e.g., "Base_Color", "Roughness", "Metallic", "Emissive", etc.).
            sr_node (pm.nt.StingrayPBS): The StingrayPBS shader node to which the textures will be connected.

        Returns:
            bool: True if the connection is successful, False otherwise.
        """
        if texture_type == "Base_Color":
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_color_map, force=True)
            sr_node.use_color_map.set(1)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_color_map, force=True)
            pm.connectAttr(texture_node.outAlpha, sr_node.opacity, force=True)
            sr_node.use_color_map.set(1)
            sr_node.use_opacity_map.set(1)
            return True

        elif texture_type in ["Roughness", "Metallic"]:
            target_attr = (
                sr_node.TEX_roughness_map
                if texture_type == "Roughness"
                else sr_node.TEX_metallic_map
            )
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, target_attr, force=True)
            sr_node.setAttr(f"use_{texture_type.lower()}_map", 1)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_metallic_map, force=True)
            pm.connectAttr(
                texture_node.outAlpha, sr_node.TEX_roughness_mapX, force=True
            )
            sr_node.use_metallic_map.set(1)
            sr_node.use_roughness_map.set(1)

        elif texture_type in ["Normal_OpenGL", "Normal_DirectX"]:
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_normal_map, force=True)
            sr_node.use_normal_map.set(1)

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_emissive_map, force=True)
            sr_node.use_emissive_map.set(1)

        elif texture_type == "Ambient_Occlusion":
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_ao_map, force=True)
            sr_node.use_ao_map.set(1)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outAlpha, sr_node.opacity, force=True)
            sr_node.use_opacity_map.set(1)

        else:  # Unsupported texture type
            return False

        return True

    @staticmethod
    def connect_arnold_nodes(
        texture: str,
        texture_type: str,
        ai_node: object,
        aiMult_node: object,
        bump_node: object,
    ) -> bool:
        """Connects texture files to the corresponding slots in the Arnold shader nodes based on the texture type.

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of the texture (e.g., "Base_Color", "Roughness", "Metallic").
            ai_node (pm.nt.Anisotropic): The Arnold shader node to which the base color and metallic textures will be connected.
            aiMult_node (pm.nt.LayeredTexture): The Arnold multiply node used for blending textures.
            bump_node (pm.nt.Bump2d): The Arnold bump node to which normal maps will be connected.

        Returns:
            bool: True if the connection is successful, False otherwise.
        """
        if texture_type == "Base_Color":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
            )
            pm.connectAttr(texture_node.outColor, aiMult_node.input1, force=True)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
            )
            # Connect base color
            pm.connectAttr(texture_node.outColor, aiMult_node.input1, force=True)
            # Handle transparency by connecting alpha to Arnold's standard surface opacity
            pm.connectAttr(texture_node.outAlpha, ai_node.opacity, force=True)
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
            )
            pm.connectAttr(texture_node.outAlpha, ai_node.specularRoughness, force=True)
            # Opacity: same roughness map used in Specular Roughness to provide additional blurriness of refraction.
            pm.connectAttr(
                texture_node.outAlpha, ai_node.transmissionExtraRoughness, force=True
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
            )
            pm.connectAttr(texture_node.outAlpha, ai_node.metalness, force=True)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
            )
            # Create a reverse node to invert the alpha channel
            reverse_node = pm.shadingNode(
                "reverse", asUtility=True, name="invertSmoothness"
            )
            pm.connectAttr(texture_node.outAlpha, reverse_node.inputX, force=True)
            pm.connectAttr(reverse_node.outputX, ai_node.specularRoughness, force=True)
            pm.connectAttr(
                reverse_node.outputX, ai_node.transmissionExtraRoughness, force=True
            )
            pm.connectAttr(texture_node.outColorR, ai_node.metalness, force=True)

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
            )
            pm.connectAttr(texture_node.outAlpha, ai_node.emission, force=True)
            pm.connectAttr(texture_node.outColor, ai_node.emissionColor, force=True)

        elif "Normal" in texture_type:
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
            )
            pm.connectAttr(texture_node.outAlpha, bump_node.bumpValue, force=True)

        elif texture_type == "Ambient_Occlusion":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
            )
            pm.connectAttr(texture_node.outColor, aiMult_node.input2, force=True)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
            )
            pm.connectAttr(texture_node.outAlpha, ai_node.transmission, force=True)
            pm.connectAttr(texture_node.outColor, ai_node.opacity, force=True)
        else:
            return False
        return True

    def filter_for_correct_normal_map(
        self, textures: List[str], desired_normal_type: str
    ) -> List[str]:
        """Filters and ensures only the desired type of normal map is in the textures list.
        If the desired normal map doesn't exist, attempts to create it by converting from the other type.

        Parameters:
            textures (List[str]): The list of texture file paths.
            desired_normal_type (str): The desired normal map type, either 'OpenGL' or 'DirectX'.

        Returns:
            List[str]: The modified list of texture file paths with the correct normal map type.
        """

        # Normalize desired_normal_type to match naming convention in textures
        desired_normal_type = "Normal_" + desired_normal_type

        # Separate normal maps from other textures
        normal_maps = [tex for tex in textures if "Normal_" in tex]
        other_textures = [tex for tex in textures if "Normal_" not in tex]

        # Filter normal maps for the desired type
        desired_normal_maps = [nm for nm in normal_maps if desired_normal_type in nm]

        # If the desired normal map is already present, return it with the other textures
        if desired_normal_maps:
            return other_textures + desired_normal_maps

        # Attempt to create the desired normal map by converting from the available one
        for nm in normal_maps:
            if "OpenGL" in desired_normal_type and "DirectX" in nm:
                # Convert DirectX to OpenGL
                converted_map = ptk.create_gl_from_dx(nm)
                if converted_map:
                    return other_textures + [converted_map]
            elif "DirectX" in desired_normal_type and "OpenGL" in nm:
                # Convert OpenGL to DirectX
                converted_map = ptk.create_dx_from_gl(nm)
                if converted_map:
                    return other_textures + [converted_map]

        # If no normal map conversion was possible, return the list without any normal maps
        return other_textures

    def filter_for_correct_metallic_map(
        self, textures: List[str], use_metallic_smoothness: bool
    ) -> List[str]:
        """Filters textures to ensure the correct handling of metallic maps based on the use_metallic_smoothness parameter.
        Prioritizes a metallic smoothness map over separate metallic and roughness maps when use_metallic_smoothness is True.
        If use_metallic_smoothness is False, filters out any metallic smoothness or smoothness maps from the textures.

        Parameters:
            textures (List[str]): List of texture file paths.
            use_metallic_smoothness (bool): Flag indicating whether to use a combined metallic smoothness map.

        Returns:
            List[str]: Modified list of texture file paths with the correct metallic map handling.
        """
        # Filter for metallic smoothness, metallic, roughness, and smoothness maps
        metallic_smoothness_map = ptk.filter_images_by_type(
            textures, "Metallic_Smoothness"
        )
        metallic_map = ptk.filter_images_by_type(textures, "Metallic")
        roughness_map = ptk.filter_images_by_type(textures, "Roughness")
        smoothness_map = ptk.filter_images_by_type(textures, "Smoothness")

        # If use_metallic_smoothness is True, prioritize the metallic smoothness map
        if use_metallic_smoothness:
            if metallic_smoothness_map:
                # Remove separate metallic, roughness, and smoothness maps if a metallic smoothness map exists
                return [
                    tex
                    for tex in textures
                    if tex not in metallic_map + roughness_map + smoothness_map
                ]
            elif metallic_map and (roughness_map or smoothness_map):
                # Create a metallic smoothness map from metallic and roughness or smoothness maps, then update the list
                alpha_map = roughness_map[0] if roughness_map else smoothness_map[0]
                invert_alpha = bool(
                    roughness_map
                )  # Invert alpha if the source is roughness
                combined_map = self.pack_smoothness_into_metallic(
                    metallic_map[0], alpha_map, invert_alpha=invert_alpha
                )
                return [
                    tex
                    for tex in textures
                    if tex not in metallic_map + roughness_map + smoothness_map
                ] + [combined_map]
        else:  # If use_metallic_smoothness is False, filter out any metallic smoothness or smoothness maps
            return [
                tex
                for tex in textures
                if tex not in metallic_smoothness_map + smoothness_map
            ]

        # Return the textures list unchanged if no conditions are met
        return textures

    def pack_smoothness_into_metallic(
        self, metallic_map_path: str, alpha_map_path: str, invert_alpha: bool = False
    ) -> str:
        """Packs the alpha channel (smoothness or inverted roughness) into the metallic map.

        Parameters:
            metallic_map_path (str): File path of the metallic texture.
            alpha_map_path (str): File path of the smoothness or roughness texture to be packed into the alpha channel.
            invert_alpha (bool): If True, inverts the alpha channel. Useful for converting roughness to smoothness.

        Returns:
            str: File path of the resulting metallic smoothness map.
        """
        # Determine the base name for the output path without the "_Metallic" suffix
        base_name = os.path.splitext(metallic_map_path)[0].replace("_Metallic", "")
        output_path = f"{base_name}_MetallicSmoothness.png"

        # Pack the alpha channel into the metallic map
        success = ptk.pack_channel_into_alpha(
            metallic_map_path, alpha_map_path, output_path, invert_alpha=invert_alpha
        )

        if success:
            return output_path
        else:
            raise Exception("Failed to pack smoothness into metallic map.")

    def filter_for_correct_base_color_map(
        self, textures: List[str], use_albedo_transparency: bool
    ) -> List[str]:
        """Filters textures to ensure the correct handling of albedo maps based on the use_albedo_transparency parameter.
        Prioritizes an albedo transparency map over separate albedo and transparency maps when use_albedo_transparency is True.
        If use_albedo_transparency is False, filters out any albedo transparency maps from the textures.

        Parameters:
            textures (List[str]): List of texture file paths.
            use_albedo_transparency (bool): Flag indicating whether to use a combined albedo transparency map.

        Returns:
            List[str]: Modified list of texture file paths with the correct albedo map handling.
        """
        albedo_transparency_map = ptk.filter_images_by_type(
            textures, "Albedo_Transparency"
        )
        base_color_map = ptk.filter_images_by_type(textures, "Base_Color")
        transparency_map = ptk.filter_images_by_type(textures, "Opacity")

        if use_albedo_transparency:
            if albedo_transparency_map:
                # Remove separate albedo and transparency maps if an albedo transparency map exists
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map
                ]
            elif base_color_map and transparency_map:
                # Create an albedo transparency map from albedo and transparency maps, then update the list
                combined_map = self.pack_transparency_into_albedo(
                    base_color_map[0], transparency_map[0]
                )
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map
                ] + [combined_map]
        else:
            # If use_albedo_transparency is False, filter out any albedo transparency maps
            return [tex for tex in textures if tex not in albedo_transparency_map]

        # Return the textures list unchanged if no conditions are met
        return textures

    @staticmethod
    def pack_transparency_into_albedo(
        albedo_map_path: str, alpha_map_path: str, invert_alpha: bool = False
    ) -> str:
        """Packs the transparency channel into the albedo map.

        Parameters:
            albedo_map_path (str): File path of the albedo texture.
            alpha_map_path (str): File path of the transparency texture to be packed into the alpha channel.
            invert_alpha (bool): If True, inverts the alpha channel before packing.

        Returns:
            str: File path of the resulting AlbedoTransparency map.
        """
        # Determine the output path without the "_BaseColor" or "_Albedo" suffix
        base_name = (
            os.path.splitext(albedo_map_path)[0]
            .replace("_BaseColor", "")
            .replace("_Albedo", "")
        )
        output_path = f"{base_name}_AlbedoTransparency.png"

        # Pack the transparency channel into the albedo map
        success = ptk.pack_channel_into_alpha(
            albedo_map_path, alpha_map_path, output_path, invert_alpha=invert_alpha
        )

        if success:
            return output_path
        else:
            raise Exception("Failed to pack transparency into albedo map.")


class StingrayArnoldShaderSlots(StingrayArnoldShader):
    msg_intro = """<u>To setup the material:</u>
        <br>• Use the <b>Get Texture Maps</b> button to load the images you intend to use.
        <br>• Click the <b>Create Network</b> button to create the shader connections. This will bridge Stingray PBS and (optionally) Arnold aiStandardSurface shaders, create a shading network from provided textures, and manage OpenGL and DirectX normal map conversions.

        <p><b>Note:</b> To correctly render opacity and transmission in Maya, the Opaque setting needs to be disabled on the Shape node.
        If Opaque is enabled, opacity will not work at all. Transmission will work, however any shadows cast by
        the object will always be solid and not pick up the Transparent Color or density of the shader.</p>
    """
    msg_completed = '<br><hl style="color:rgb(0, 255, 255);"><b>COMPLETED.</b></hl>'

    def __init__(self, **kwargs):
        super().__init__()

        self.sb = self.switchboard()
        self.ui = self.sb.stingray_arnold_shader
        self.workspace_dir = _core_utils.CoreUtils.get_maya_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")
        self.image_files = None

        self.ui.txt001.setText(self.msg_intro)
        self.ui.progressBar.setValue(0)
        # self.init_header_menu()

    # def init_header_menu(self):
    #     """Configure header menu"""
    #     self.ui.header.menu.setTitle("OPTIONS")
    #     self.ui.header.menu.add(
    #         self.sb.PushButton,
    #         setText="HDR Manager",
    #         setObjectName="b002",
    #     )

    #     module = hdr_manager
    #     slot_class = module.HdrManagerSlots

    #     # Register and configure HDR Manager UI
    #     self.sb.register("hdr_manager.ui", slot_class, base_dir=module)
    #     ui = self.sb.hdr_manager
    #     ui.set_attributes(WA_TranslucentBackground=True)
    #     ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    #     ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    #     ui.header.configureButtons(hide_button=True)

    #     # Connect button click to show HDR Manager
    #     self.ui.header.menu.b002.clicked.connect(lambda: ui.show(pos="cursor"))

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

            create_arnold = self.ui.chk000.isChecked()

            output_template = self.ui.cmb002.currentText()
            if output_template == "PBR Metal Roughness":
                albedo_transparency = False
                metallic_smoothness = False
            elif (
                output_template == "Unity Univeral Render Pipeline (Metallic Standard)"
            ):
                albedo_transparency = True
                metallic_smoothness = True

            self.create_network(
                self.image_files,
                self.mat_name,
                normal_type=self.normal_map_type,
                create_arnold=create_arnold,
                albedo_transparency=albedo_transparency,
                metallic_smoothness=metallic_smoothness,
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
            self.sb.QtWidgets.QApplication.instance().processEvents()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from uitk import Switchboard

    parent = _core_utils.CoreUtils.get_main_window()
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
