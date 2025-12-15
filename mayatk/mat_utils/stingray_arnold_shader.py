# !/usr/bin/python
# coding=utf-8
import os
from typing import List, Optional, Tuple, Callable, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk
from pythontk.img_utils.texture_map_factory import TextureMapFactory

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.env_utils._env_utils import EnvUtils


class PBRWorkflowTemplate:
    """Configuration class for PBR workflow templates.

    Defines standard PBR export configurations for different game engines and pipelines.
    Each template specifies how textures should be packed/combined for the target platform.
    Templates are indexed by their position in the UI combo box (0-6).
    """

    # Template configurations indexed by combo box position
    # Format: (albedo_transparency, metallic_smoothness, mask_map, orm_map, convert_specgloss)
    TEMPLATE_CONFIGS = [
        (
            False,
            False,
            False,
            False,
            False,
        ),  # 0: PBR Metallic/Roughness (Separate Maps)
        (
            True,
            True,
            False,
            False,
            False,
        ),  # 1: Unity URP Lit (Packed: Albedo+Alpha, Metallic+Smoothness)
        (
            False,
            False,
            True,
            False,
            False,
        ),  # 2: Unity HDRP Lit (Mask Map: Metallic+AO+Detail+Smoothness)
        (
            True,
            False,
            False,
            True,
            False,
        ),  # 3: Unreal Engine (Packed: BaseColor+Alpha, ORM)
        (False, False, False, True, False),  # 4: glTF 2.0 (ORM: AO+Roughness+Metallic)
        (
            False,
            False,
            False,
            False,
            False,
        ),  # 5: Godot (Separate: Albedo, Metallic, Roughness)
        (False, True, False, False, True),  # 6: PBR Specular/Glossiness Workflow
    ]

    @classmethod
    def get_template_config(cls, index: int) -> Tuple[bool, bool, bool, bool, bool]:
        """Get configuration for a template by its combo box index.

        Parameters:
            index: The combo box index (0-6)

        Returns:
            Tuple of (albedo_transparency, metallic_smoothness, mask_map, orm_map, convert_specgloss_to_pbr)
        """
        if 0 <= index < len(cls.TEMPLATE_CONFIGS):
            return cls.TEMPLATE_CONFIGS[index]
        # Default to standard PBR if index out of range
        return (False, False, False, False, False)


class StingrayArnoldShader:
    """A class to manage the creation of a shader network using StingrayPBS and optionally Arnold shaders.
    This class facilitates the automatic setup of textures into a StingrayPBS shader and, if requested,
    an Arnold shader network, linking necessary nodes and setting up the shader graph based on the provided textures.
    """

    color_info = "rgb(100, 100, 160)"
    color_success = "rgb(100, 160, 100)"
    color_warning = "rgb(200, 200, 100)"
    color_error = "rgb(255, 100, 100)"

    @CoreUtils.undoable
    def create_network(
        self,
        textures: List[str],
        name: str = "",
        shader_type: str = "stingray",  # "stingray" or "standard_surface"
        normal_type: str = "OpenGL",
        create_arnold: bool = False,
        albedo_transparency: bool = False,
        metallic_smoothness: bool = False,
        mask_map: bool = False,
        orm_map: bool = False,
        convert_specgloss_to_pbr: bool = False,
        cleanup_base_color: bool = False,
        output_extension: str = "png",
        callback: Callable = print,
    ) -> Union[Optional[object], List[Optional[object]]]:
        """Create a PBR shader network with textures.

        Parameters:
            textures: List of texture file paths
            name: Shader name (auto-generated from texture if empty)
            shader_type: "stingray" for Stingray PBS (legacy) or "standard_surface" for Maya Standard Surface (modern)
            normal_type: "OpenGL" or "DirectX" normal map format
            create_arnold: Also create Arnold aiStandardSurface for rendering
            albedo_transparency: Combine albedo and opacity into single map
            metallic_smoothness: Combine metallic and smoothness into single map
            mask_map: Create Unity HDRP Mask Map (MSAO: Metallic+AO+Detail+Smoothness)
            orm_map: Create Unreal/glTF ORM Map (Occlusion+Roughness+Metallic)
            convert_specgloss_to_pbr: Auto-convert Specular/Glossiness workflow to PBR Metal/Rough
            cleanup_base_color: Remove baked reflections from base color (requires metallic map)
            output_extension: File extension for generated combined maps
            callback: Progress callback function

        Returns:
            The created shader node(s) (Stingray PBS or Standard Surface)
        """
        if not textures:
            callback(
                f'<br><hl style="color:{self.color_error};"><b>Error:</b> No textures given to create_network.</hl>'
            )
            return None

        # Use TextureMapFactory for DRY texture preparation
        workflow_config = {
            "albedo_transparency": albedo_transparency,
            "metallic_smoothness": metallic_smoothness,
            "mask_map": mask_map,
            "orm_map": orm_map,
            "convert_specgloss_to_pbr": convert_specgloss_to_pbr,
            "cleanup_base_color": cleanup_base_color,
            "normal_type": normal_type,
            "output_extension": output_extension,
        }

        prepared_data = TextureMapFactory.prepare_maps(
            textures,
            workflow_config,
            callback=callback,
            group_by_set=(not bool(name)),
        )

        if isinstance(prepared_data, dict):
            # Batch mode
            results = []
            for set_name, set_textures in prepared_data.items():
                callback(f"Creating network for set: {set_name}")
                node = self._create_single_network(
                    set_textures,
                    set_name,  # Use set name for shader name
                    shader_type,
                    create_arnold,
                    callback,
                )
                results.append(node)
            return results
        else:
            # Single mode
            return self._create_single_network(
                prepared_data, name, shader_type, create_arnold, callback
            )

    def _create_single_network(
        self,
        textures: List[str],
        name: str,
        shader_type: str,
        create_arnold: bool,
        callback: Callable,
    ) -> Optional[object]:
        """Internal method to create a single shader network from prepared textures."""
        if not textures:
            callback(
                f'<br><hl style="color:{self.color_error};"><b>Error:</b> No valid textures after preparation.</hl>'
            )
            return None

        opacity_map = ptk.filter_images_by_type(
            textures, ["Opacity", "Albedo_Transparency"]
        )

        name = name if name else ptk.get_base_texture_name(textures[0])

        # Create the base shader based on shader_type
        if shader_type == "standard_surface":
            shader_node = self.setup_standard_surface_node(name, opacity_map)
        else:  # Default to stingray
            shader_node = self.setup_stringray_node(name, opacity_map)

        # Optional: Arnold shader creation
        if create_arnold:
            ai_node, aiMult_node, bump_node = self.setup_arnold_nodes(name, shader_node)

        # Process each texture
        length = len(textures)
        progress = 0
        base_dir = EnvUtils.get_env_info("sourceimages")
        for texture in ptk.convert_to_relative_path(textures, base_dir):
            progress += 1
            texture_name = ptk.format_path(texture, "file")
            texture_type = ptk.resolve_map_type(
                texture,
            )

            if texture_type is None:
                callback(
                    f'<br><hl style="color:{self.color_error};"><b>Unknown map type: </b>{texture_name}.</hl>',
                    [progress, length],
                )
                continue

            # Connect shader nodes based on type
            if shader_type == "standard_surface":
                success = self.connect_standard_surface_nodes(
                    texture, texture_type, shader_node
                )
            else:
                success = self.connect_stingray_nodes(
                    texture, texture_type, shader_node
                )

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

        # Return the shading engine (not the shader node itself)
        # Find the connected shading engine
        shading_groups = pm.listConnections(shader_node, type="shadingEngine")
        if shading_groups:
            return shading_groups[0]
        return shader_node

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
        EnvUtils.load_plugin("shaderFXPlugin")  # Load Stingray plugin

        # Create StingrayPBS node
        sr_node = NodeUtils.create_render_node("StingrayPBS", name=name)

        if opacity:
            maya_install_path = EnvUtils.get_env_info("install_path")

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
        self, name: str, shader_node: object
    ) -> Tuple[object, object, object]:
        """Sets up a basic Arnold shader network for use with a Stingray PBS or Standard Surface shader.

        This method loads the MtoA plugin if not already loaded, creates an aiStandardSurface
        shader, an aiMultiply utility node, and a bump2d node for normal mapping. It connects
        these nodes together and to the shader node's shading engine to integrate Arnold
        rendering with the base material.

        Parameters:
            name (str): Base name for the created Arnold nodes. The names will have suffixes
                        '_ai', '_multiply', and '_bump' respectively.
            shader_node (object): The Stingray PBS or Standard Surface shader node that the
                                 Arnold shader network is being set up for. This is used to
                                 find the connected shading engine.

        Returns:
            Tuple[pm.nt.AiStandardSurface, pm.nt.AiMultiply, pm.nt.Bump2d]: A tuple containing
            the created aiStandardSurface node, aiMultiply node, and bump2d node, in that order.
        """
        EnvUtils.load_plugin("mtoa")  # Load Arnold plugin

        ai_node = NodeUtils.create_render_node(
            "aiStandardSurface", name=name + "_ai" if name else ""
        )
        aiMult_node = pm.shadingNode("aiMultiply", asShader=True)
        bump_node = pm.shadingNode("bump2d", asShader=True)
        bump_node.bumpInterp.set(1)  # Set to tangent space normals

        # Get shading engine from either Stingray PBS or Standard Surface
        shading_engine = NodeUtils.get_connected_nodes(
            shader_node,
            node_type="shadingEngine",
            direction="outgoing",
            first_match=True,
        )

        # Connect Arnold nodes to the shading engine
        NodeUtils.connect_multi_attr(
            (ai_node.outColor, shading_engine.aiSurfaceShader),
            (aiMult_node.outColor, ai_node.baseColor),
            (bump_node.outNormal, ai_node.normalCamera),
        )
        return ai_node, aiMult_node, bump_node

    def setup_standard_surface_node(self, name: str, opacity: bool) -> object:
        """Creates and sets up a Maya Standard Surface shader node.

        Maya Standard Surface is the modern PBR shader for Maya 2020+ that replaces
        Stingray PBS. It supports glTF/FBX export for game engines like Unity and Unreal.

        Parameters:
            name (str): The desired name for the Standard Surface shader node.
            opacity (bool): Flag to indicate whether the shader should support transparency.
                          If True, sets up transparency attributes.

        Returns:
            pm.nt.StandardSurface: The created Standard Surface shader node.
        """
        # Create Standard Surface node - must use shadingNode, not create_render_node
        std_node = pm.shadingNode("standardSurface", asShader=True, name=name)

        # Create and assign shading group
        sg_node = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        pm.connectAttr(std_node.outColor, sg_node.surfaceShader, force=True)

        if opacity:
            # Enable transparency for standard surface
            std_node.transmission.set(1.0)
            std_node.thinWalled.set(True)

        return std_node

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
        if texture_type in ["Base_Color", "Diffuse"]:
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

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            # StingrayPBS expects RGB color connections, not individual channels
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            # Connect full color output - StingrayPBS will use red channel for metallic
            pm.connectAttr(texture_node.outColor, sr_node.TEX_metallic_map, force=True)
            # Connect full color output - StingrayPBS will use all channels for AO
            pm.connectAttr(texture_node.outColor, sr_node.TEX_ao_map, force=True)
            # Connect alpha channel (smoothness) - this connection works for alpha
            pm.connectAttr(
                texture_node.outAlpha, sr_node.TEX_roughness_mapX, force=True
            )
            sr_node.use_metallic_map.set(1)
            sr_node.use_ao_map.set(1)
            sr_node.use_roughness_map.set(1)

        elif "Normal" in texture_type:
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
        if texture_type in ["Base_Color", "Diffuse"]:
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

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
            )
            # Metallic from red channel
            pm.connectAttr(texture_node.outColorR, ai_node.metalness, force=True)
            # Smoothness in alpha needs to be inverted to roughness
            reverse_node = pm.shadingNode(
                "reverse", asUtility=True, name="invertSmoothness"
            )
            pm.connectAttr(texture_node.outAlpha, reverse_node.inputX, force=True)
            pm.connectAttr(reverse_node.outputX, ai_node.specularRoughness, force=True)
            pm.connectAttr(
                reverse_node.outputX, ai_node.transmissionExtraRoughness, force=True
            )
            # AO from green channel - multiply with base color using aiMultiply
            # Connect green channel as grayscale to all RGB channels of input2
            pm.connectAttr(texture_node.outColor, aiMult_node.input2, force=True)

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

    def connect_standard_surface_nodes(
        self, texture: str, texture_type: str, std_node: object
    ) -> bool:
        """Connects texture files to Maya Standard Surface shader slots.

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of texture (e.g., "Base_Color", "Roughness", "Metallic").
            std_node (pm.nt.StandardSurface): The Standard Surface shader node.

        Returns:
            bool: True if connection successful, False otherwise.
        """
        if texture_type in ["Base_Color", "Diffuse"]:
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, std_node.baseColor, force=True)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, std_node.baseColor, force=True)
            pm.connectAttr(texture_node.outAlpha, std_node.opacity, force=True)
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
            )
            pm.connectAttr(
                texture_node.outAlpha, std_node.specularRoughness, force=True
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
            )
            pm.connectAttr(texture_node.outAlpha, std_node.metalness, force=True)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
            )
            # Metallic in RGB, smoothness in alpha (need to invert for roughness)
            reverse_node = pm.shadingNode(
                "reverse", asUtility=True, name="invertSmoothness"
            )
            pm.connectAttr(texture_node.outAlpha, reverse_node.inputX, force=True)
            pm.connectAttr(reverse_node.outputX, std_node.specularRoughness, force=True)
            pm.connectAttr(texture_node.outColorR, std_node.metalness, force=True)

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
            )
            # Connect red channel (metallic) to metalness
            pm.connectAttr(texture_node.outColorR, std_node.metalness, force=True)
            # Smoothness in alpha needs to be inverted to roughness
            reverse_node = pm.shadingNode(
                "reverse", asUtility=True, name="invertSmoothness"
            )
            pm.connectAttr(texture_node.outAlpha, reverse_node.inputX, force=True)
            pm.connectAttr(reverse_node.outputX, std_node.specularRoughness, force=True)
            # AO in green channel - multiply with base color if already connected
            existing_conn = pm.listConnections(
                std_node.baseColor, source=True, destination=False
            )
            if existing_conn:
                mult_node = pm.shadingNode("multiplyDivide", asUtility=True)
                pm.connectAttr(existing_conn[0].outColor, mult_node.input1, force=True)
                pm.connectAttr(texture_node.outColorG, mult_node.input2X, force=True)
                pm.connectAttr(texture_node.outColorG, mult_node.input2Y, force=True)
                pm.connectAttr(texture_node.outColorG, mult_node.input2Z, force=True)
                pm.connectAttr(mult_node.output, std_node.baseColor, force=True)

        elif "Normal" in texture_type:
            # Standard Surface uses bump2d for normal maps
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
            )
            bump_node = pm.shadingNode("bump2d", asUtility=True)
            bump_node.bumpInterp.set(1)  # Tangent space normals
            # Use outAlpha (grayscale) instead of outColor for bump2d compatibility
            pm.connectAttr(texture_node.outAlpha, bump_node.bumpValue, force=True)
            pm.connectAttr(bump_node.outNormal, std_node.normalCamera, force=True)

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file", "as2DTexture", fileTextureName=texture
            )
            pm.connectAttr(texture_node.outColor, std_node.emissionColor, force=True)
            std_node.emission.set(1.0)

        elif texture_type == "Ambient_Occlusion":
            # Standard Surface doesn't have direct AO input, multiply with base color
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
            )
            # Create multiply node to combine AO with base color
            mult_node = pm.shadingNode("multiplyDivide", asUtility=True)
            # If base color already connected, insert multiply
            existing_conn = pm.listConnections(
                std_node.baseColor, source=True, destination=False
            )
            if existing_conn:
                pm.connectAttr(existing_conn[0].outColor, mult_node.input1, force=True)
            pm.connectAttr(texture_node.outColor, mult_node.input2, force=True)
            pm.connectAttr(mult_node.output, std_node.baseColor, force=True)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                "as2DTexture",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
            )
            pm.connectAttr(texture_node.outAlpha, std_node.opacity, force=True)
            std_node.transmission.set(1.0)
            std_node.thinWalled.set(True)

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
        other_textures = [tex for tex in textures if not ptk.is_normal_map(tex)]

        # Filter normal maps by type
        opengl_maps = ptk.filter_images_by_type(textures, ["Normal_OpenGL"])
        directx_maps = ptk.filter_images_by_type(textures, ["Normal_DirectX"])
        generic_normal_maps = ptk.filter_images_by_type(textures, ["Normal"])

        if desired_normal_type == "OpenGL":
            if opengl_maps:
                return other_textures + opengl_maps
            elif directx_maps:
                for nm in directx_maps:
                    converted_map = ptk.create_gl_from_dx(nm)
                    if converted_map:
                        return other_textures + [converted_map]
        elif desired_normal_type == "DirectX":
            if directx_maps:
                return other_textures + directx_maps
            elif opengl_maps:
                for nm in opengl_maps:
                    converted_map = ptk.create_dx_from_gl(nm)
                    if converted_map:
                        return other_textures + [converted_map]

        # If no normal map conversion was possible, use generic normal maps if available
        if generic_normal_maps:
            return other_textures + generic_normal_maps

        # If no normal maps are found, return the list unchanged
        return other_textures

    def filter_for_correct_metallic_map(
        self,
        textures: List[str],
        use_metallic_smoothness: bool,
        output_extension: str = "png",
    ) -> List[str]:
        """Filters textures to ensure the correct handling of metallic maps based on the use_metallic_smoothness parameter.
        Prioritizes a metallic smoothness map over separate metallic and roughness maps when use_metallic_smoothness is True.
        If use_metallic_smoothness is False, filters out any metallic smoothness or smoothness maps from the textures.
        If neither a roughness nor a metallic map is provided, converts the specular map to the necessary maps.

        Parameters:
            textures (List[str]): List of texture file paths.
            use_metallic_smoothness (bool): Flag indicating whether to use a combined metallic smoothness map.

        Returns:
            List[str]: Modified list of texture file paths with the correct metallic map handling.
        """
        # Filter for existing maps
        metallic_smoothness_map = ptk.filter_images_by_type(
            textures, "Metallic_Smoothness"
        )
        metallic_map = ptk.filter_images_by_type(textures, "Metallic")
        roughness_map = ptk.filter_images_by_type(textures, "Roughness")
        smoothness_map = ptk.filter_images_by_type(textures, "Smoothness")
        specular_map = ptk.filter_images_by_type(textures, "Specular")

        filtered_textures = textures.copy()

        if use_metallic_smoothness:
            if metallic_smoothness_map:
                # If a metallic smoothness map exists, remove other maps and return
                filtered_textures = [
                    tex
                    for tex in textures
                    if tex not in metallic_map + roughness_map + smoothness_map
                ]
                return filtered_textures

            elif specular_map:
                # Convert specular map to roughness and metallic maps
                created_roughness_map = ptk.create_roughness_from_spec(specular_map[0])
                created_metallic_map = ptk.create_metallic_from_spec(specular_map[0])

                # Save these images to disk and get their file paths
                base_name = ptk.get_base_texture_name(specular_map[0])
                out_dir = os.path.dirname(specular_map[0])

                rough_path = os.path.join(
                    out_dir, f"{base_name}_Roughness.{output_extension}"
                )
                metal_path = os.path.join(
                    out_dir, f"{base_name}_Metallic.{output_extension}"
                )

                created_roughness_map.save(rough_path)
                created_metallic_map.save(metal_path)

                # Now you can combine using file paths:
                combined_map = ptk.pack_smoothness_into_metallic(
                    metal_path, rough_path, invert_alpha=True
                )

                # Remove individual metallic, roughness, smoothness maps and the newly created maps
                filtered_textures = [
                    tex
                    for tex in filtered_textures
                    if tex not in metallic_map + roughness_map + smoothness_map
                ] + [combined_map]
                return filtered_textures

            elif metallic_map and (roughness_map or smoothness_map):
                # If metallic and roughness/smoothness maps exist, combine them into a metallic smoothness map
                alpha_map = roughness_map[0] if roughness_map else smoothness_map[0]
                invert_alpha = bool(roughness_map)
                combined_map = ptk.pack_smoothness_into_metallic(
                    metallic_map[0], alpha_map, invert_alpha=invert_alpha
                )
                filtered_textures = [
                    tex
                    for tex in filtered_textures
                    if tex not in metallic_map + roughness_map + smoothness_map
                ] + [combined_map]
                return filtered_textures

        else:  # If use_metallic_smoothness is False
            # Remove any metallic smoothness or smoothness maps from the list
            filtered_textures = [
                tex
                for tex in textures
                if tex not in metallic_smoothness_map + smoothness_map
            ]

            if not metallic_map and specular_map:
                # Create a metallic map from the specular map
                created_metallic_map = ptk.create_metallic_from_spec(specular_map[0])
                filtered_textures.append(created_metallic_map)

            if not roughness_map and specular_map:
                # Create a roughness map from the specular map
                created_roughness_map = ptk.create_roughness_from_spec(specular_map[0])
                filtered_textures.append(created_roughness_map)

            return filtered_textures

        # Return the textures list unchanged if no conditions are met
        return filtered_textures

    def filter_for_mask_map(
        self,
        textures: List[str],
        output_extension: str = "png",
        callback: Callable = print,
    ) -> List[str]:
        """Creates Unity HDRP Mask Map (MSAO) by packing Metallic, AO, Detail, and Smoothness.

        Unity HDRP Mask Map format:
        - R: Metallic
        - G: Ambient Occlusion
        - B: Detail Mask (unused, set to 0)
        - A: Smoothness

        Parameters:
            textures (List[str]): List of texture file paths.
            output_extension (str): File extension for generated mask map.
            callback (Callable): Progress callback function.

        Returns:
            List[str]: Modified list with mask map replacing individual maps.
        """
        # Filter for required maps
        metallic_map = ptk.filter_images_by_type(textures, "Metallic")
        ao_map = ptk.filter_images_by_type(textures, ["Ambient_Occlusion", "AO"])
        roughness_map = ptk.filter_images_by_type(textures, "Roughness")
        smoothness_map = ptk.filter_images_by_type(textures, "Smoothness")

        # Need at least metallic map to create mask map
        if not metallic_map:
            callback(
                f'<br><hl style="color:{self.color_warning};"><b>Warning:</b> No metallic map found for Mask Map creation. Skipping MSAO packing.</hl>'
            )
            return textures

        # Determine smoothness/roughness source
        if smoothness_map:
            alpha_map = smoothness_map[0]
            invert_alpha = False
        elif roughness_map:
            alpha_map = roughness_map[0]
            invert_alpha = True  # Invert roughness to get smoothness
        else:
            callback(
                f'<br><hl style="color:{self.color_warning};"><b>Warning:</b> No roughness or smoothness map found for Mask Map alpha channel.</hl>'
            )
            alpha_map = None

        # Use AO if available, otherwise create a white map
        if not ao_map:
            callback(
                f'<br><hl style="color:{self.color_warning};"><b>Warning:</b> No AO map found. Using white (255) for AO channel in Mask Map.</hl>'
            )
            # Will be handled by pack_msao_texture with fill_values

        try:
            # Create the MSAO mask map
            base_name = ptk.get_base_texture_name(metallic_map[0])
            out_dir = os.path.dirname(metallic_map[0])

            # Use pythontk's pack_msao_texture function
            mask_map_path = ptk.pack_msao_texture(
                metallic_map_path=metallic_map[0],
                ao_map_path=(
                    ao_map[0] if ao_map else metallic_map[0]
                ),  # Use metallic as placeholder if no AO
                alpha_map_path=(
                    alpha_map if alpha_map else metallic_map[0]
                ),  # Use metallic as placeholder
                output_dir=out_dir,
                suffix="_MaskMap",
                invert_alpha=invert_alpha,
            )

            callback(
                f'<br><hl style="color:{self.color_success};"><b>Created Mask Map:</b> {os.path.basename(mask_map_path)}</hl>'
            )

            # Remove individual maps and add mask map
            filtered_textures = [
                tex
                for tex in textures
                if tex not in metallic_map + ao_map + roughness_map + smoothness_map
            ] + [mask_map_path]

            return filtered_textures

        except Exception as e:
            callback(
                f'<br><hl style="color:{self.color_error};"><b>Error creating Mask Map:</b> {str(e)}</hl>'
            )
            return textures

    def filter_for_correct_base_color_map(
        self, textures: List[str], use_albedo_transparency: bool
    ) -> List[str]:
        """Filters textures to ensure the correct handling of albedo maps based on the use_albedo_transparency parameter.
        Prioritizes an albedo transparency map over separate albedo and transparency maps when use_albedo_transparency is True.
        If use_albedo_transparency is False, filters out any albedo transparency maps from the textures.
        Falls back to diffuse map if no base color map is found.

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
        diffuse_map = ptk.filter_images_by_type(textures, "Diffuse")
        transparency_map = ptk.filter_images_by_type(textures, "Opacity")

        if use_albedo_transparency:
            if albedo_transparency_map:
                # Remove separate albedo and transparency maps if an albedo transparency map exists
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map + diffuse_map
                ]
            elif base_color_map and transparency_map:
                # Create an albedo transparency map from albedo and transparency maps, then update the list
                combined_map = ptk.pack_transparency_into_albedo(
                    base_color_map[0], transparency_map[0]
                )
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map
                ] + [combined_map]
            elif diffuse_map and transparency_map:
                # Create an albedo transparency map from diffuse and transparency maps, then update the list
                combined_map = ptk.pack_transparency_into_albedo(
                    diffuse_map[0], transparency_map[0]
                )
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map + diffuse_map
                ] + [combined_map]
        else:
            if base_color_map:
                return textures
            elif diffuse_map:
                return [
                    tex for tex in textures if tex not in base_color_map
                ] + diffuse_map

        # If no base color or diffuse map is found, return the list unchanged
        return textures


class StingrayArnoldShaderSlots(StingrayArnoldShader):
    msg_intro = """<u>To setup the material:</u>
        <br>• Use the <b>Get Texture Maps</b> button to load the images you intend to use.
        <br>• Click the <b>Create Network</b> button to create the shader connections. This will bridge Stingray PBS and (optionally) Arnold aiStandardSurface shaders, create a shading network from provided textures, and manage OpenGL and DirectX normal map conversions.

        <p><b>Note:</b> To correctly render opacity and transmission in Maya, the Opaque setting needs to be disabled on the Shape node.
        If Opaque is enabled, opacity will not work at all. Transmission will work, however any shadows cast by
        the object will always be solid and not pick up the Transparent Color or density of the shader.</p>
    """
    msg_completed = '<br><hl style="color:rgb(0, 255, 255);"><b>COMPLETED.</b></hl>'

    def __init__(self, switchboard):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.stingray_arnold_shader

        self.workspace_dir = EnvUtils.get_env_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")
        self.image_files = None
        self.last_created_shader = None

        self.ui.txt001.setText(self.msg_intro)
        self.ui.progressBar.setValue(0)
        # self.init_header_menu()

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.setTitle("Stingray Arnold Shader")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_graph_material",
            setText="Open in Editor",
            setToolTip="Graph the material in the Hypershade.",
        )

    def lbl_graph_material(self):
        """Graph the material in the Hypershade."""
        if self.last_created_shader:
            MatUtils.graph_materials(self.last_created_shader)
        elif pm.objExists(self.mat_name):
            MatUtils.graph_materials(self.mat_name)
        else:
            pm.warning(f"Material '{self.mat_name}' not found.")

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

    @property
    def output_extension(self) -> str:
        """Get the output map extension from the comboBox current text.

        Returns:
            (str) The file extension in lowercase (e.g., 'png', 'jpg')
        """
        text = self.ui.cmb003.currentText()
        return text.lower()

    @property
    def shader_type(self) -> str:
        """Get the shader type selection.

        Returns:
            (str) Either 'stingray' or 'standard_surface'
        """
        # This will be cmb004 or whichever combo box is added for shader type
        # For now, default to stingray for backwards compatibility
        if hasattr(self.ui, "cmb004"):
            text = self.ui.cmb004.currentText()
            if "Standard Surface" in text:
                return "standard_surface"
        return "stingray"

    def b000(self):
        """Create network."""
        if self.image_files:
            # pm.mel.HypershadeWindow() #open the hypershade window.

            self.ui.txt001.clear()
            self.callback("Creating network ..<br>")

            create_arnold = self.ui.chk000.isChecked()

            # Get template configuration using combo box index
            template_index = self.ui.cmb002.currentIndex()
            (
                albedo_transparency,
                metallic_smoothness,
                mask_map,
                orm_map,
                convert_specgloss_to_pbr,
            ) = PBRWorkflowTemplate.get_template_config(template_index)

            self.last_created_shader = self.create_network(
                self.image_files,
                self.mat_name,
                shader_type=self.shader_type,
                normal_type=self.normal_map_type,
                create_arnold=create_arnold,
                albedo_transparency=albedo_transparency,
                metallic_smoothness=metallic_smoothness,
                mask_map=mask_map,
                orm_map=orm_map,
                convert_specgloss_to_pbr=convert_specgloss_to_pbr,
                cleanup_base_color=False,  # Can be exposed in UI later if needed
                output_extension=self.output_extension,
                callback=self.callback,
            )

            self.callback(self.msg_completed)
            # pm.mel.hyperShadePanelGraphCommand('hyperShadePanel1', 'rearrangeGraph')

    def b001(self):
        """Get texture maps."""
        image_files = self.sb.file_dialog(
            file_types=["*.png", "*.jpg", "*.bmp", "*.tga", "*.tiff", "*.gif"],
            title="Select one or more image files to open.",
            start_dir=self.source_images_dir,
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
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("stingray_arnold_shader", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------


# deprecated:

# def filter_for_correct_normal_map(
#     self, textures: List[str], desired_normal_type: str
# ) -> List[str]:
#     """Filters and ensures only the desired type of normal map is in the textures list.
#     If the desired normal map doesn't exist, attempts to create it by converting from the other type.

#     Parameters:
#         textures (List[str]): The list of texture file paths.
#         desired_normal_type (str): The desired normal map type, either 'OpenGL' or 'DirectX'.

#     Returns:
#         List[str]: The modified list of texture file paths with the correct normal map type.
#     """

#     # Normalize desired_normal_type to match naming convention in textures
#     desired_normal_type = "Normal_" + desired_normal_type

#     # Separate normal maps from other textures
#     normal_maps = [tex for tex in textures if "Normal_" in tex]
#     other_textures = [tex for tex in textures if "Normal_" not in tex]

#     # Filter normal maps for the desired type
#     desired_normal_maps = [nm for nm in normal_maps if desired_normal_type in nm]

#     # If the desired normal map is already present, return it with the other textures
#     if desired_normal_maps:
#         return other_textures + desired_normal_maps

#     # Attempt to create the desired normal map by converting from the available one
#     for nm in normal_maps:
#         if "OpenGL" in desired_normal_type and "DirectX" in nm:
#             # Convert DirectX to OpenGL
#             converted_map = ptk.create_gl_from_dx(nm)
#             if converted_map:
#                 return other_textures + [converted_map]
#         elif "DirectX" in desired_normal_type and "OpenGL" in nm:
#             # Convert OpenGL to DirectX
#             converted_map = ptk.create_dx_from_gl(nm)
#             if converted_map:
#                 return other_textures + [converted_map]

#     # If no normal map conversion was possible, return the list without any normal maps
#     return other_textures
