# !/usr/bin/python
# coding=utf-8
import os
import logging
from typing import List, Optional, Tuple, Callable, Union, Dict, Any
from qtpy import QtCore, QtGui

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.env_utils._env_utils import EnvUtils


class GameShader(ptk.LoggingMixin):
    """A class to manage the creation of a shader network using StingrayPBS or Standard Surface shaders.
    This class facilitates the automatic setup of textures into a shader and, if requested,
    an Arnold shader network, linking necessary nodes and setting up the shader graph based on the provided textures.
    """

    @CoreUtils.undoable
    def create_network(
        self,
        textures: List[str],
        name: str = "",
        prefix: str = "",
        config: Union[str, Dict[str, Any]] = None,
        **kwargs,
    ) -> Union[Optional[object], List[Optional[object]]]:
        """Create a PBR shader network with textures.

        Parameters:
            textures: List of texture file paths
            name: Shader name (auto-generated from texture if empty)
            config: Configuration preset name (str) or dictionary.
            **kwargs: Configuration overrides (e.g. shader_type, normal_type, etc.)

        Returns:
            The created shader node(s) (Stingray PBS or Standard Surface)
        """
        if not textures:
            self.logger.error("No textures given to create_network.")
            return None

        # Resolve Config
        cfg = ptk.MapRegistry().resolve_config(config, **kwargs)

        # Set defaults for missing keys
        defaults = {
            "shader_type": "stingray",
            "normal_type": "OpenGL",
            "create_arnold": False,
            "albedo_transparency": False,
            "metallic_smoothness": False,
            "mask_map": False,
            "orm_map": False,
            "opacity": False,
            "emissive": False,
            "ambient_occlusion": False,
            "convert_specgloss_to_pbr": False,
            "cleanup_base_color": False,
            "output_extension": "png",
        }

        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v

        # Log Header
        self.logger.info("Creating Shader Network...", preset="header")
        self.logger.log_divider()

        # Log Configuration
        config_info = [
            ["Shader Type", cfg["shader_type"]],
            ["Normal Type", cfg["normal_type"]],
            ["Create Arnold", str(cfg["create_arnold"])],
            ["Albedo Transparency", str(cfg["albedo_transparency"])],
            ["Metallic Smoothness", str(cfg["metallic_smoothness"])],
            ["Mask Map", str(cfg["mask_map"])],
            ["ORM Map", str(cfg["orm_map"])],
            ["Opacity", str(cfg["opacity"])],
            ["Emissive", str(cfg["emissive"])],
            ["Ambient Occlusion", str(cfg["ambient_occlusion"])],
        ]
        self.log_table(config_info, headers=["Option", "Value"], title="Configuration")

        prepared_data = ptk.MapFactory.prepare_maps(
            textures,
            logger=self.logger,
            group_by_set=(not bool(name)),
            **cfg,
        )

        if isinstance(prepared_data, dict):
            # Batch mode
            self.logger.info(f"Batch processing {len(prepared_data)} texture sets...")
            results = []
            created_shaders = []

            for set_name, set_textures in prepared_data.items():
                self.logger.log_divider()
                self.logger.info(f"Set: {set_name}", preset="header")
                node = self._create_single_network(
                    set_textures,
                    set_name,  # Use set name for shader name
                    cfg["shader_type"],
                    cfg["create_arnold"],
                    prefix=prefix,
                )
                results.append(node)

                status = "Success" if node else "Failed"
                node_name = node.name() if hasattr(node, "name") else str(node)
                created_shaders.append([set_name, node_name, status])

            # Log Summary
            self.logger.log_box("Batch Creation Summary")
            self.log_table(
                created_shaders,
                headers=["Set Name", "Node Name", "Status"],
            )
            return results
        else:
            # Single mode
            self.logger.log_divider()
            node = self._create_single_network(
                prepared_data,
                name,
                cfg["shader_type"],
                cfg["create_arnold"],
                prefix=prefix,
            )

            if node:
                node_name = node.name() if hasattr(node, "name") else str(node)
                self.logger.success(f"Successfully created shader: {node_name}")
            else:
                self.logger.error("Failed to create shader.")

            return node

    def _create_single_network(
        self,
        textures: List[str],
        name: str,
        shader_type: str,
        create_arnold: bool,
        prefix: str = "",
    ) -> Optional[object]:
        """Internal method to create a single shader network from prepared textures."""
        if not textures:
            self.logger.error("No valid textures after preparation.")
            return None

        opacity_map = ptk.MapFactory.filter_images_by_type(
            textures, ["Opacity", "Albedo_Transparency"]
        )

        name = name if name else ptk.MapFactory.get_base_texture_name(textures[0])

        if prefix:
            name = f"{prefix}{name}"

        # Log creation start with fancy formatting
        self.logger.info(f"Creating Shader: {name}", "INFO", "header")

        # Prioritize packed maps to avoid conflicts
        # If ORM exists, remove MSAO and Metallic_Smoothness
        # If MSAO exists, remove Metallic_Smoothness
        orm_maps = ptk.MapFactory.filter_images_by_type(textures, "ORM")
        msao_maps = ptk.MapFactory.filter_images_by_type(textures, "MSAO")

        if orm_maps:
            # Remove MSAO and Metallic_Smoothness from the list we process
            textures = [
                t
                for t in textures
                if ptk.MapFactory.resolve_map_type(t)
                not in ["MSAO", "Metallic_Smoothness"]
            ]
        elif msao_maps:
            # Remove Metallic_Smoothness
            textures = [
                t
                for t in textures
                if ptk.MapFactory.resolve_map_type(t) not in ["Metallic_Smoothness"]
            ]

        # Create the base shader based on shader_type
        if shader_type == "standard_surface":
            shader_node = self.setup_standard_surface_node(name, opacity_map)
        else:  # Default to stingray
            shader_node = self.setup_stringray_node(name, opacity_map)

        # Validation: Check for Opacity without Base Color
        if opacity_map and not ptk.MapFactory.filter_images_by_type(
            textures, ["Base_Color", "Diffuse", "Albedo_Transparency"]
        ):
            self.logger.warning(
                f"Shader '{name}' has Opacity but no Base Color. Object may appear invisible or black."
            )

        # Optional: Arnold shader creation
        if create_arnold:
            ai_node, aiMult_node, bump_node = self.setup_arnold_nodes(name, shader_node)

        # Process each texture
        length = len(textures)
        progress = 0
        base_dir = EnvUtils.get_env_info("sourceimages")

        connection_log = []

        for texture in ptk.convert_to_relative_path(textures, base_dir):
            progress += 1
            texture_name = ptk.format_path(texture, "file")
            texture_type = ptk.MapFactory.resolve_map_type(
                texture,
            )

            if texture_type is None:
                self.logger.warning(f"Unknown map type: {texture_name}.")
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
                connection_log.append(f"  • {texture_type}: {texture_name}")
            else:
                self.logger.warning(f"  • {texture_type}: Failed to connect")

            # Conditional Arnold nodes connection
            if create_arnold and success:
                self.connect_arnold_nodes(
                    texture, texture_type, ai_node, aiMult_node, bump_node
                )

        # Log connections
        if connection_log:
            self.logger.info("\n".join(connection_log))

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
            pm.cmds.shaderfx(sfxnode=sr_node.name(), loadGraph=graph)
        else:
            # Ensure standard graph is loaded (crucial for batch mode)
            maya_install_path = EnvUtils.get_env_info("install_path")
            graph = os.path.join(
                maya_install_path,
                "presets",
                "ShaderFX",
                "Scenes",
                "StingrayPBS",
                "Standard.sfx",
            )
            if os.path.exists(graph):
                pm.cmds.shaderfx(sfxnode=sr_node.name(), loadGraph=graph)

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
            # Note: We do NOT set transmission to 1.0 (glass).
            # We ONLY enable thinWalled for correct cutout/foliage behavior.
            # Opacity is driven by the 'opacity' (alpha) input connection later.
            std_node.thinWalled.set(True)

        return std_node

    def _connect_channel(self, source_plug, node, attr_name):
        """Helper to connect a source plug to a target attribute, handling compound attributes.

        Args:
            source_plug (pm.Attribute): The source attribute to connect from.
            node (pm.PyNode): The target node.
            attr_name (str): The name of the target attribute.
        """
        # Check if attribute exists
        if not pm.attributeQuery(attr_name, node=node, exists=True):
            print(f"Warning: Attribute {attr_name} not found on {node}")
            return False

        # Try to find children (R, G, B or X, Y, Z)
        children = []
        for suffix in ["R", "G", "B"]:
            if pm.attributeQuery(attr_name + suffix, node=node, exists=True):
                children.append(attr_name + suffix)

        if not children:
            for suffix in ["X", "Y", "Z"]:
                if pm.attributeQuery(attr_name + suffix, node=node, exists=True):
                    children.append(attr_name + suffix)

        if children and len(children) >= 3:
            # Explicitly break connection to parent attribute if it exists
            # This prevents "ghost" connections where parent remains connected to old node
            try:
                if pm.attributeQuery(attr_name, node=node, exists=True):
                    inputs = pm.listConnections(
                        f"{node}.{attr_name}",
                        plugs=True,
                        source=True,
                        destination=False,
                    )
                    if inputs:
                        pm.disconnectAttr(inputs[0], f"{node}.{attr_name}")
            except Exception as e:
                print(f"Warning: Failed to disconnect parent {attr_name}: {e}")

            # Connect to all 3 children
            for child in children[:3]:
                pm.connectAttr(source_plug, f"{node}.{child}", force=True)
            return True
        else:
            # Fallback: try connecting to parent directly
            try:
                pm.connectAttr(source_plug, f"{node}.{attr_name}", force=True)
                return True
            except Exception as e:
                print(f"Failed to connect {source_plug} to {node}.{attr_name}: {e}")
                return False

    def _ensure_fbx_safe_connection(self, texture_node, shader_node, attr_name):
        """Creates a dummy connection to a custom attribute to ensure FBX export preserves the texture reference.

        This addresses issues where FBX exporters drop textures connected via:
        1. Individual channels (e.g. outColorR -> metalness)
        2. Secondary nodes (e.g. outAlpha -> Reverse -> roughness)

        Args:
            texture_node: The file texture node.
            shader_node: The shader node.
            attr_name: The name of the custom attribute to create (e.g. 'MSAO_Map').
        """
        if not pm.attributeQuery(attr_name, node=shader_node, exists=True):
            pm.addAttr(
                shader_node,
                longName=attr_name,
                attributeType="float3",
                usedAsColor=True,
            )
            pm.addAttr(
                shader_node,
                longName=f"{attr_name}R",
                attributeType="float",
                parent=attr_name,
            )
            pm.addAttr(
                shader_node,
                longName=f"{attr_name}G",
                attributeType="float",
                parent=attr_name,
            )
            pm.addAttr(
                shader_node,
                longName=f"{attr_name}B",
                attributeType="float",
                parent=attr_name,
            )

        target_plug = f"{shader_node}.{attr_name}"
        if not pm.isConnected(texture_node.outColor, target_plug):
            pm.connectAttr(texture_node.outColor, target_plug, force=True)

    @CoreUtils.undoable
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
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_color_map, force=True)
            sr_node.use_color_map.set(1)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_color_map, force=True)
            if sr_node.hasAttr("opacity"):
                pm.connectAttr(texture_node.outAlpha, sr_node.opacity, force=True)
                sr_node.use_opacity_map.set(1)
            sr_node.use_color_map.set(1)
            return True

        elif texture_type in ["Roughness", "Metallic"]:
            target_attr_name = (
                "TEX_roughness_map"
                if texture_type == "Roughness"
                else "TEX_metallic_map"
            )
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            # Connect RGB directly to ensure FBX export (Single channel maps are usually grayscale so RGB matches)
            pm.connectAttr(
                texture_node.outColor, f"{sr_node}.{target_attr_name}", force=True
            )
            sr_node.setAttr(f"use_{texture_type.lower()}_map", 1)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic (RGB) -> Metallic Map
            # Connect RGB directly to ensure FBX export (Metallic is usually grayscale so RGB matches)
            pm.connectAttr(texture_node.outColor, sr_node.TEX_metallic_map, force=True)
            sr_node.use_roughness_map.set(1)

        elif texture_type == "ORM":
            # Unreal/glTF ORM Map: R=AO, G=Roughness, B=Metallic
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            # Connect RGB directly to AO map (R channel matches AO) to ensure FBX export
            pm.connectAttr(texture_node.outColor, sr_node.TEX_ao_map, force=True)

            # Connect other channels individually
            self._connect_channel(texture_node.outColorG, sr_node, "TEX_roughness_map")
            self._connect_channel(texture_node.outColorB, sr_node, "TEX_metallic_map")

            sr_node.use_ao_map.set(1)
            sr_node.use_roughness_map.set(1)
            sr_node.use_metallic_map.set(1)

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )

            # Connect metallic channel (R) -> TEX_metallic_map
            # Connect RGB directly to ensure FBX export (R=Metallic matches)
            pm.connectAttr(texture_node.outColor, sr_node.TEX_metallic_map, force=True)

            # Connect AO channel (G) -> TEX_ao_map
            self._connect_channel(texture_node.outColorG, sr_node, "TEX_ao_map")

            # Connect smoothness channel (A) -> Invert -> TEX_roughness_map
            # Unity Smoothness is inverse of Roughness
            rev_node = NodeUtils.create_render_node("reverse")
            pm.connectAttr(texture_node.outAlpha, rev_node.inputX, force=True)
            pm.connectAttr(texture_node.outAlpha, rev_node.inputY, force=True)
            pm.connectAttr(texture_node.outAlpha, rev_node.inputZ, force=True)

            # Use reverse output X (float) for roughness
            self._connect_channel(rev_node.outputX, sr_node, "TEX_roughness_map")

            sr_node.use_metallic_map.set(1)
            sr_node.use_ao_map.set(1)
            sr_node.use_roughness_map.set(1)

        elif "Normal" in texture_type:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_normal_map, force=True)
            sr_node.use_normal_map.set(1)

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, sr_node.TEX_emissive_map, force=True)
            sr_node.use_emissive_map.set(1)

        elif texture_type == "Ambient_Occlusion":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            # Connect RGB directly to ensure FBX export (AO is usually grayscale so RGB matches)
            pm.connectAttr(texture_node.outColor, sr_node.TEX_ao_map, force=True)
            sr_node.use_ao_map.set(1)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outAlpha, sr_node.opacity, force=True)
            sr_node.use_opacity_map.set(1)

        elif texture_type == "Specular":
            if sr_node.hasAttr("TEX_specular_map"):
                texture_node = NodeUtils.create_render_node(
                    "file",
                    fileTextureName=texture,
                    name=ptk.format_path(texture, section="name"),
                )
                pm.connectAttr(
                    texture_node.outColor, sr_node.TEX_specular_map, force=True
                )
                if sr_node.hasAttr("use_specular_map"):
                    sr_node.use_specular_map.set(1)
                return True
            return False

        elif texture_type == "Glossiness":
            if sr_node.hasAttr("TEX_glossiness_map"):
                texture_node = NodeUtils.create_render_node(
                    "file",
                    fileTextureName=texture,
                    name=ptk.format_path(texture, section="name"),
                )
                # Connect RGB directly to ensure FBX export
                pm.connectAttr(
                    texture_node.outColor, sr_node.TEX_glossiness_map, force=True
                )
                if sr_node.hasAttr("use_glossiness_map"):
                    sr_node.use_glossiness_map.set(1)
                return True
            return False

        else:  # Unsupported texture type
            return False

        return True

    def connect_arnold_nodes(
        self,
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
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, aiMult_node.input1, force=True)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            # Connect base color
            pm.connectAttr(texture_node.outColor, aiMult_node.input1, force=True)
            # Handle transparency by connecting alpha to Arnold's standard surface opacity
            pm.connectAttr(texture_node.outAlpha, ai_node.opacityR, force=True)
            pm.connectAttr(texture_node.outAlpha, ai_node.opacityG, force=True)
            pm.connectAttr(texture_node.outAlpha, ai_node.opacityB, force=True)
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outAlpha, ai_node.specularRoughness, force=True)
            # Opacity: same roughness map used in Specular Roughness to provide additional blurriness of refraction.
            pm.connectAttr(
                texture_node.outAlpha, ai_node.transmissionExtraRoughness, force=True
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outAlpha, ai_node.metalness, force=True)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            # Create a reverse node to invert the alpha channel
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            pm.connectAttr(texture_node.outAlpha, reverse_node.inputX, force=True)
            pm.connectAttr(reverse_node.outputX, ai_node.specularRoughness, force=True)
            pm.connectAttr(
                reverse_node.outputX, ai_node.transmissionExtraRoughness, force=True
            )
            pm.connectAttr(texture_node.outColorR, ai_node.metalness, force=True)

        elif texture_type == "ORM":
            # Unreal/glTF ORM Map: R=AO, G=Roughness, B=Metallic
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic (B)
            pm.connectAttr(texture_node.outColorB, ai_node.metalness, force=True)
            # Roughness (G)
            pm.connectAttr(
                texture_node.outColorG, ai_node.specularRoughness, force=True
            )
            pm.connectAttr(
                texture_node.outColorG, ai_node.transmissionExtraRoughness, force=True
            )
            # AO (R) -> Multiply with Base Color (using aiMultiply)
            # Connect R channel to all RGB inputs of input2 to multiply uniformly
            pm.connectAttr(texture_node.outColorR, aiMult_node.input2R, force=True)
            pm.connectAttr(texture_node.outColorR, aiMult_node.input2G, force=True)
            pm.connectAttr(texture_node.outColorR, aiMult_node.input2B, force=True)

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic from red channel
            pm.connectAttr(texture_node.outColorR, ai_node.metalness, force=True)
            # Smoothness in alpha needs to be inverted to roughness
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
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
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outAlpha, ai_node.emission, force=True)
            pm.connectAttr(texture_node.outColor, ai_node.emissionColor, force=True)

        elif "Normal" in texture_type:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outAlpha, bump_node.bumpValue, force=True)

        elif texture_type == "Ambient_Occlusion":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, aiMult_node.input2, force=True)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
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
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, std_node.baseColor, force=True)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, std_node.baseColor, force=True)
            # Opacity is RGB, connect alpha to all channels
            pm.connectAttr(texture_node.outAlpha, std_node.opacityR, force=True)
            pm.connectAttr(texture_node.outAlpha, std_node.opacityG, force=True)
            pm.connectAttr(texture_node.outAlpha, std_node.opacityB, force=True)
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(
                texture_node.outAlpha, std_node.specularRoughness, force=True
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outAlpha, std_node.metalness, force=True)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic in RGB, smoothness in alpha (need to invert for roughness)
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            pm.connectAttr(texture_node.outAlpha, reverse_node.inputX, force=True)
            pm.connectAttr(reverse_node.outputX, std_node.specularRoughness, force=True)
            pm.connectAttr(texture_node.outColorR, std_node.metalness, force=True)

            # Ensure FBX export preserves the texture
            self._ensure_fbx_safe_connection(
                texture_node, std_node, "Metallic_Smoothness_Map"
            )

        elif texture_type == "ORM":
            # Unreal/glTF ORM Map: R=AO, G=Roughness, B=Metallic
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic (B)
            pm.connectAttr(texture_node.outColorB, std_node.metalness, force=True)
            # Roughness (G)
            pm.connectAttr(
                texture_node.outColorG, std_node.specularRoughness, force=True
            )
            # AO (R) -> Multiply with Base Color
            existing_conn = pm.listConnections(
                std_node.baseColor, source=True, destination=False
            )
            if existing_conn:
                mult_node = pm.shadingNode("multiplyDivide", asUtility=True)
                pm.connectAttr(existing_conn[0].outColor, mult_node.input1, force=True)
                pm.connectAttr(texture_node.outColorR, mult_node.input2X, force=True)
                pm.connectAttr(texture_node.outColorR, mult_node.input2Y, force=True)
                pm.connectAttr(texture_node.outColorR, mult_node.input2Z, force=True)
                pm.connectAttr(mult_node.output, std_node.baseColor, force=True)

            self._ensure_fbx_safe_connection(texture_node, std_node, "ORM_Map")

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            # Connect red channel (metallic) to metalness
            pm.connectAttr(texture_node.outColorR, std_node.metalness, force=True)
            # Smoothness in alpha needs to be inverted to roughness
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
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

            # Ensure FBX export preserves the texture
            self._ensure_fbx_safe_connection(texture_node, std_node, "MSAO_Map")

        elif "Normal" in texture_type:
            # Standard Surface uses bump2d for normal maps
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
            )
            bump_node = pm.shadingNode("bump2d", asUtility=True)
            bump_node.bumpInterp.set(1)  # Tangent space normals
            # Use outAlpha (grayscale) instead of outColor for bump2d compatibility
            pm.connectAttr(texture_node.outAlpha, bump_node.bumpValue, force=True)
            pm.connectAttr(bump_node.outNormal, std_node.normalCamera, force=True)

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outColor, std_node.emissionColor, force=True)
            std_node.emission.set(1.0)

        elif texture_type == "Ambient_Occlusion":
            # Standard Surface doesn't have direct AO input, multiply with base color
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
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
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            pm.connectAttr(texture_node.outAlpha, std_node.opacity, force=True)

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
        other_textures = [
            tex for tex in textures if not ptk.MapFactory.is_normal_map(tex)
        ]

        # Filter normal maps by type
        opengl_maps = ptk.MapFactory.filter_images_by_type(textures, ["Normal_OpenGL"])
        directx_maps = ptk.MapFactory.filter_images_by_type(
            textures, ["Normal_DirectX"]
        )
        generic_normal_maps = ptk.MapFactory.filter_images_by_type(textures, ["Normal"])

        if desired_normal_type == "OpenGL":
            if opengl_maps:
                return other_textures + opengl_maps
            elif directx_maps:
                for nm in directx_maps:
                    converted_map = ptk.MapFactory.convert_normal_map_format(
                        nm, target_format="opengl"
                    )
                    if converted_map:
                        return other_textures + [converted_map]
        elif desired_normal_type == "DirectX":
            if directx_maps:
                return other_textures + directx_maps
            elif opengl_maps:
                for nm in opengl_maps:
                    converted_map = ptk.MapFactory.convert_normal_map_format(
                        nm, target_format="directx"
                    )
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
        metallic_smoothness_map = ptk.MapFactory.filter_images_by_type(
            textures, "Metallic_Smoothness"
        )
        metallic_map = ptk.MapFactory.filter_images_by_type(textures, "Metallic")
        roughness_map = ptk.MapFactory.filter_images_by_type(textures, "Roughness")
        smoothness_map = ptk.MapFactory.filter_images_by_type(textures, "Smoothness")
        specular_map = ptk.MapFactory.filter_images_by_type(textures, "Specular")

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
                base_name = ptk.MapFactory.get_base_texture_name(specular_map[0])
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
                combined_map_name = f"{base_name}_MetallicSmoothness.{output_extension}"
                combined_map_path = os.path.join(out_dir, combined_map_name)

                combined_map = ptk.pack_smoothness_into_metallic(
                    metal_path,
                    rough_path,
                    invert_alpha=True,
                    output_path=combined_map_path,
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

                base_name = ptk.MapFactory.get_base_texture_name(metallic_map[0])
                out_dir = os.path.dirname(metallic_map[0])
                combined_map_name = f"{base_name}_MetallicSmoothness.{output_extension}"
                combined_map_path = os.path.join(out_dir, combined_map_name)

                combined_map = ptk.pack_smoothness_into_metallic(
                    metallic_map[0],
                    alpha_map,
                    invert_alpha=invert_alpha,
                    output_path=combined_map_path,
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
    ) -> List[str]:
        """Creates Unity HDRP Mask Map (MSAO) by packing Metallic, AO, Detail, and Smoothness.

        Unity HDRP Mask Map format:
        - R: Metallic
        - G: Ambient Occlusion
        - B: Detail Mask
        - A: Smoothness

        Parameters:
            textures (List[str]): List of texture file paths.
            output_extension (str): File extension for generated mask map.

        Returns:
            List[str]: Modified list with mask map replacing individual maps.
        """
        # Filter for required maps
        metallic_map = ptk.MapFactory.filter_images_by_type(textures, "Metallic")
        ao_map = ptk.MapFactory.filter_images_by_type(
            textures, ["Ambient_Occlusion", "AO"]
        )
        detail_map = ptk.MapFactory.filter_images_by_type(textures, "Detail_Mask")
        roughness_map = ptk.MapFactory.filter_images_by_type(textures, "Roughness")
        smoothness_map = ptk.MapFactory.filter_images_by_type(textures, "Smoothness")

        # Need at least metallic map to create mask map
        if not metallic_map:
            self.logger.warning(
                "No metallic map found for Mask Map creation. Skipping MSAO packing."
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
            self.logger.warning(
                "No roughness or smoothness map found for Mask Map alpha channel."
            )
            alpha_map = None

        # Use AO if available, otherwise create a white map
        if not ao_map:
            self.logger.warning(
                "No AO map found. Using white (255) for AO channel in Mask Map."
            )
            # Will be handled by pack_msao_texture with fill_values

        try:
            # Create the MSAO mask map
            base_name = ptk.MapFactory.get_base_texture_name(metallic_map[0])
            out_dir = os.path.dirname(metallic_map[0])

            # Construct output path with extension
            mask_map_name = f"{base_name}_MaskMap.{output_extension}"
            mask_map_full_path = os.path.join(out_dir, mask_map_name)

            # Use pythontk's pack_msao_texture function
            mask_map_path = ptk.pack_msao_texture(
                metallic_map_path=metallic_map[0],
                ao_map_path=(
                    ao_map[0] if ao_map else None
                ),  # Use None if no AO (will be filled with white)
                alpha_map_path=(
                    alpha_map if alpha_map else None
                ),  # Use None if no alpha (will be filled with default)
                detail_map_path=(
                    detail_map[0] if detail_map else None
                ),  # Use None if no detail (will be filled with black)
                output_dir=out_dir,
                suffix="_MaskMap",
                invert_alpha=invert_alpha,
                output_path=mask_map_full_path,
            )

            self.logger.info(f"Created Mask Map: {os.path.basename(mask_map_path)}")

            # Remove individual maps and add mask map
            filtered_textures = [
                tex
                for tex in textures
                if tex
                not in metallic_map
                + ao_map
                + roughness_map
                + smoothness_map
                + detail_map
            ] + [mask_map_path]

            return filtered_textures

        except Exception as e:
            self.logger.error(f"Error creating Mask Map: {str(e)}")
            return textures

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
        albedo_transparency_map = ptk.MapFactory.filter_images_by_type(
            textures, "Albedo_Transparency"
        )
        base_color_map = ptk.MapFactory.filter_images_by_type(
            textures, ["Base_Color", "Diffuse"]
        )
        transparency_map = ptk.MapFactory.filter_images_by_type(textures, "Opacity")

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
                combined_map = ptk.pack_transparency_into_albedo(
                    base_color_map[0], transparency_map[0]
                )
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map
                ] + [combined_map]

        # If no base color or diffuse map is found, return the list unchanged
        return textures


class CallbackLogHandler(logging.Handler):
    """Log handler that calls a callback function with the formatted message."""

    def __init__(self, callback: Callable):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        msg = self.format(record)
        self.callback(msg)


class GameShaderSlots(GameShader):
    msg_intro = """<u>To setup the material:</u>
        <br>• Click the <b>Create Network</b> button to select texture maps and create the shader connections. This will bridge Stingray PBS and (optionally) Arnold aiStandardSurface shaders, create a shading network from provided textures, and manage OpenGL and DirectX normal map conversions.

        <p><b>Note:</b> To correctly render opacity and transmission in Maya, the Opaque setting needs to be disabled on the Shape node.
        If Opaque is enabled, opacity will not work at all. Transmission will work, however any shadows cast by
        the object will always be solid and not pick up the Transparent Color or density of the shader.</p>
    """
    msg_completed = '<br><hl style="color:rgb(0, 255, 255);"><b>COMPLETED.</b></hl>'

    def __init__(self, switchboard):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.game_shader

        self.workspace_dir = EnvUtils.get_env_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")
        self.image_files = None
        self.last_created_shader = None

        self.ui.txt001.setText(self.msg_intro)

        # Set monospace font for log output
        font = QtGui.QFont("Consolas")
        font.setStyleHint(QtGui.QFont.Monospace)
        self.ui.txt001.setFont(font)

        # Redirect logs to UI callback
        self.log_handler = CallbackLogHandler(self.callback)
        self.logger.addHandler(self.log_handler)

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.menu.setTitle("Global Options")
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
    def mat_prefix(self) -> str:
        """Get the material prefix from the UI."""
        if hasattr(self.ui, "txt002"):
            return self.ui.txt002.text()
        return ""

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

    def cmb002_init(self, widget):
        """Initialize Presets"""
        if not widget.is_initialized:
            # Populate template combo box from presets with tooltips
            presets = ptk.MapRegistry().get_workflow_presets()
            widget.clear()
            for name, settings in presets.items():
                widget.addItem(name)
                description = settings.get("description")
                if description:
                    widget.setItemData(
                        widget.count() - 1, description, QtCore.Qt.ToolTipRole
                    )

    def cmb003_init(self, widget):
        """Initialize Output Extension"""
        if not widget.is_initialized:
            # Populate with common image file extensions
            file_types = ptk.ImgUtils.texture_file_types
            widget.add(file_types)

    def b000(self):
        """Create network."""
        image_files = self.sb.file_dialog(
            file_types=[f"*.{ext}" for ext in ptk.ImgUtils.texture_file_types],
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
        else:
            return

        if self.image_files:
            # pm.mel.HypershadeWindow() #open the hypershade window.

            self.ui.txt001.clear()
            self.callback("Creating network ..<br>")

            create_arnold = self.ui.chk000.isChecked()

            # Get template configuration using combo box text
            template_name = self.ui.cmb002.currentText()

            self.last_created_shader = self.create_network(
                self.image_files,
                self.mat_name,
                prefix=self.mat_prefix,
                config=template_name,
                shader_type=self.shader_type,
                normal_type=self.normal_map_type,
                create_arnold=create_arnold,
                cleanup_base_color=False,  # Can be exposed in UI later if needed
                output_extension=self.output_extension,
            )

            self.callback(self.msg_completed)
            # pm.mel.hyperShadePanelGraphCommand('hyperShadePanel1', 'rearrangeGraph')

    def callback(self, string, progress=None, clear=False):
        """Callback function to output messages to the UI textEdit and update progress bar.

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
            # self.ui.progressBar.setValue(progress)
            self.sb.QtWidgets.QApplication.instance().processEvents()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.mayatk_ui_manager import UiManager

    ui = UiManager.instance().get("game_shader", reload=True)
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
