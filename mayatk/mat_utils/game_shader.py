# !/usr/bin/python
# coding=utf-8
import os
import logging
from typing import List, Optional, Callable, Union, Dict, Any
from qtpy import QtCore

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    print(__file__, error)
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt


def _plug(node, attr: str) -> str:
    """Build a plug string from a node-or-string node."""
    return f"{node}.{attr}"

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

    # Texture types whose connection produces an internal conversion node
    # (e.g. invert smoothness → roughness, split a packed channel map).
    CONVERSION_NOTES = {
        "Metallic_Smoothness": "smoothness → roughness (inverted)",
        "ORM": "split R/G/B → AO / Roughness / Metallic",
        "MSAO": "smoothness → roughness; R/G channels split",
        "Albedo_Transparency": "alpha → opacity",
    }

    @CoreUtils.undoable
    def create_network(
        self,
        textures: List[str],
        name: str = "",
        prefix: str = "",
        suffix: str = "",
        config: Union[str, Dict[str, Any]] = None,
        progress_callback: Callable = None,
        **kwargs,
    ) -> Union[Optional[object], List[Optional[object]]]:
        """Create a PBR shader network with textures.

        Parameters:
            textures: List of texture file paths
            name: Shader name (auto-generated from texture if empty)
            prefix: Optional prefix prepended to the resolved shader name.
            suffix: Optional suffix appended to the resolved shader name.
            config: Configuration preset name (str) or dictionary.
            progress_callback: Optional callback(percent, message) for progress updates.
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

        # Compact configuration banner: one boxed header + a 2-column table.
        self.logger.log_box("Game Shader Network")
        config_info = [
            ["Shader Type", cfg["shader_type"]],
            ["Normal Type", cfg["normal_type"]],
            ["Create Arnold", str(cfg["create_arnold"])],
            ["Opacity", str(cfg["opacity"])],
            ["Emissive", str(cfg["emissive"])],
            ["Ambient Occlusion", str(cfg["ambient_occlusion"])],
            ["Albedo Transparency", str(cfg["albedo_transparency"])],
            ["Metallic Smoothness", str(cfg["metallic_smoothness"])],
            ["Mask Map", str(cfg["mask_map"])],
            ["ORM Map", str(cfg["orm_map"])],
        ]
        self.log_table(config_info, headers=["Option", "Value"])

        # Check for large input size
        try:
            total_size_bytes = sum(
                os.path.getsize(t) for t in textures if os.path.exists(t)
            )
            total_size_mb = total_size_bytes / (1024 * 1024)

            # Warn if over 300MB
            if total_size_mb > 300:
                warn_msg = f"Large input detected ({total_size_mb:.1f} MB). Processing may take some time..."
                self.logger.warning(warn_msg)
                if progress_callback:
                    progress_callback(0, warn_msg)
                    # Force a UI update immediately so the user sees the warning before the heavy lift starts
                    from qtpy import QtWidgets

                    QtWidgets.QApplication.instance().processEvents()
        except Exception as e:
            self.logger.debug(f"Could not calculate input size: {e}")

        def factory_progress(curr, total, msg):
            """Bridge callback to map Factory progress (0-50%) to UI."""
            if progress_callback:
                # Map 0-50 range
                try:
                    pct = int((curr / total) * 50)
                    progress_callback(pct, f"Preparing Maps: {msg}")
                except Exception:
                    pass

        prepared_data = ptk.MapFactory.prepare_maps(
            textures,
            logger=self.logger,
            group_by_set=(not bool(name)),
            max_workers=4,
            progress_callback=factory_progress,
            prefix=prefix,
            suffix=suffix,
            **cfg,
        )

        if isinstance(prepared_data, dict):
            # Batch mode
            total = len(prepared_data)
            self.logger.info(f"Batch processing {total} texture sets...")
            results = []
            created_shaders = []

            i = 0
            for set_name, set_textures in prepared_data.items():
                i += 1
                if progress_callback:
                    # Map 50-100 range
                    pct = 50 + int((i / total) * 50)
                    progress_callback(pct, f"Building Network: {set_name}")

                node = self._create_single_network(
                    set_textures,
                    set_name,  # Use set name for shader name
                    cfg["shader_type"],
                    cfg["create_arnold"],
                    prefix=prefix,
                    suffix=suffix,
                )
                results.append(node)

                status = "Success" if node else "Failed"
                node_name = str(node).split("|")[-1].split(":")[-1]
                created_shaders.append([set_name, node_name, status])

            # Log Summary
            self.logger.log_box("Batch Creation Summary")
            self.log_table(
                created_shaders,
                headers=["Set Name", "Node Name", "Status"],
            )

            if progress_callback:
                progress_callback(100, "Completed")

            return results
        else:
            if progress_callback:
                progress_callback(75, "Building Network...")

            node = self._create_single_network(
                prepared_data,
                name,
                cfg["shader_type"],
                cfg["create_arnold"],
                prefix=prefix,
                suffix=suffix,
            )

            if progress_callback:
                progress_callback(100, "Completed")

            return node

    def _create_single_network(
        self,
        textures: List[str],
        name: str,
        shader_type: str,
        create_arnold: bool,
        prefix: str = "",
        suffix: str = "",
    ) -> Optional[object]:
        """Internal method to create a single shader network from prepared textures."""
        if not textures:
            self.logger.error("No valid textures after preparation.")
            return None

        opacity_map = ptk.MapFactory.filter_images_by_type(
            textures, ["Opacity", "Albedo_Transparency"]
        )

        if not name:
            name = ptk.MapFactory.get_base_texture_name(
                textures[0], prefix=prefix, suffix=suffix
            )
        # Idempotent affix application: strips any pre-existing occurrence of the
        # configured prefix/suffix from `name` before re-applying, so a filename
        # like "Mat_brick_Albedo.png" with prefix="Mat_" yields "Mat_brick", not
        # "Mat_Mat_brick". Also collapses dangling underscores on either end.
        name = ptk.StrUtils.apply_affix(name, prefix=prefix, suffix=suffix)

        self.logger.info(f"Shader: {name}")

        # Pre-compute map type for each texture to avoid redundant lookups
        type_cache = {t: ptk.MapFactory.resolve_map_type(t) for t in textures}

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
                if type_cache.get(t) not in ["MSAO", "Metallic_Smoothness"]
            ]
        elif msao_maps:
            # Remove Metallic_Smoothness
            textures = [
                t for t in textures if type_cache.get(t) not in ["Metallic_Smoothness"]
            ]

        # Create the base shader based on shader_type
        if shader_type == "standard_surface":
            shader_node = self.setup_standard_surface_node(name, opacity_map)
        elif shader_type == "open_pbr":
            shader_node = self.setup_open_pbr_node(name, opacity_map)
        else:  # Default to stingray
            shader_node = self.setup_stringray_node(name, opacity_map)

        # Validation: Check for Opacity without Base Color
        if opacity_map and not ptk.MapFactory.filter_images_by_type(
            textures, ["Base_Color", "Diffuse", "Albedo_Transparency"]
        ):
            self.logger.warning(
                f"Shader '{name}' has Opacity but no Base Color. Object may appear invisible or black."
            )

        base_dir = EnvUtils.get_env_info("sourceimages")

        # Per-map outcome rows: [status, type, file, note]
        rows: List[List[str]] = []
        connected_count = 0
        failed_count = 0
        conversion_count = 0

        for texture in ptk.convert_to_relative_path(textures, base_dir):
            texture_name = ptk.format_path(texture, "file")
            # Use pre-computed type cache; fall back to resolve for converted paths
            texture_type = type_cache.get(texture) or ptk.MapFactory.resolve_map_type(
                texture,
            )

            if texture_type is None:
                rows.append(["✗", "Unknown", texture_name, "unrecognized map type"])
                failed_count += 1
                continue

            # Connect shader nodes based on type
            if shader_type == "standard_surface":
                success = self.connect_standard_surface_nodes(
                    texture, texture_type, shader_node
                )
            elif shader_type == "open_pbr":
                success = self.connect_open_pbr_nodes(
                    texture, texture_type, shader_node
                )
            else:
                success = self.connect_stingray_nodes(
                    texture, texture_type, shader_node
                )

            note = self.CONVERSION_NOTES.get(texture_type, "")
            if success:
                connected_count += 1
                if note:
                    conversion_count += 1
                rows.append(["✓", texture_type, texture_name, note])
            else:
                failed_count += 1
                rows.append(["✗", texture_type, texture_name, "shader has no matching slot"])

        # Per-map connection table
        self.log_table(rows, headers=["", "Map", "Source", "Conversion"])

        # Optional Arnold render bridge — delegated to ArnoldBridge, which
        # introspects the now-wired base shader's file nodes (no texture list
        # needed). Same module powers add/remove after creation.
        if create_arnold:
            from mayatk.mat_utils.arnold_bridge import ArnoldBridge

            ArnoldBridge().add(materials=shader_node)

        # Resolve created shading engine
        shading_groups = cmds.listConnections(shader_node, type="shadingEngine")
        result_node = shading_groups[0] if shading_groups else shader_node
        result_name = str(result_node).split("|")[-1].split(":")[-1]

        # Clickable link — points at the shader node (not the SG) so users
        # land on the editable material in the Hypershade.
        link = self.logger.log_link(result_name, "select", node=str(shader_node))

        # Final compact summary
        if failed_count == 0:
            self.logger.success(
                f"{link} — {connected_count} connected, {conversion_count} converted"
            )
        else:
            self.logger.warning(
                f"{link} — {connected_count} connected, "
                f"{failed_count} failed, {conversion_count} converted"
            )

        return result_node

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
            str: The created StingrayPBS shader node.
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
            cmds.shaderfx(sfxnode=str(sr_node), loadGraph=graph)
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
                cmds.shaderfx(sfxnode=str(sr_node), loadGraph=graph)

        return sr_node

    def setup_standard_surface_node(self, name: str, opacity: bool) -> object:
        """Creates and sets up a Maya Standard Surface shader node.

        Maya Standard Surface is the modern PBR shader for Maya 2020+ that replaces
        Stingray PBS. It supports glTF/FBX export for game engines like Unity and Unreal.

        Parameters:
            name (str): The desired name for the Standard Surface shader node.
            opacity (bool): Flag to indicate whether the shader should support transparency.
                          If True, sets up transparency attributes.

        Returns:
            str: The created Standard Surface shader node.
        """
        # Create Standard Surface node - must use shadingNode, not create_render_node
        std_node = cmds.shadingNode("standardSurface", asShader=True, name=name)

        # Create and assign shading group
        sg_node = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{std_node}.outColor", f"{sg_node}.surfaceShader", force=True)

        if opacity:
            # Enable transparency for standard surface
            # Note: We do NOT set transmission to 1.0 (glass).
            # We ONLY enable thinWalled for correct cutout/foliage behavior.
            # Opacity is driven by the 'opacity' (alpha) input connection later.
            cmds.setAttr(f"{std_node}.thinWalled", True)

        return std_node

    def setup_open_pbr_node(self, name: str, opacity: bool) -> object:
        """Creates and sets up a Maya OpenPBR Surface shader node.

        OpenPBR Surface is the open-standard PBR shader (Maya 2025+) that unifies
        Autodesk Standard Surface and Adobe Standard Material. Suitable for
        glTF/USD/MaterialX export targeting modern game engines and renderers.

        Parameters:
            name (str): The desired name for the OpenPBR Surface shader node.
            opacity (bool): Whether the shader should support cutout transparency.
                          If True, enables thin-walled mode for correct cutout/foliage behavior.

        Returns:
            str: The created OpenPBR Surface shader node.
        """
        try:
            op_node = cmds.shadingNode("openPBRSurface", asShader=True, name=name)
        except RuntimeError as err:
            raise RuntimeError(
                "Cannot create openPBRSurface — node type unavailable. "
                "OpenPBR Surface requires a recent Maya 2025 update or newer. "
                "Use 'Stingray PBS' or 'Standard Surface' on earlier versions."
            ) from err

        sg_node = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{op_node}.outColor", f"{sg_node}.surfaceShader", force=True)

        if opacity:
            if cmds.attributeQuery("geometryThinWalled", node=op_node, exists=True):
                cmds.setAttr(f"{op_node}.geometryThinWalled", True)

        return op_node

    def _connect_channel(self, source_plug, node, attr_name):
        """Helper to connect a source plug to a target attribute, handling compound attributes.

        Args:
            source_plug (pm.Attribute): The source attribute to connect from.
            node (str): The target node.
            attr_name (str): The name of the target attribute.
        """
        # Check if attribute exists
        if not cmds.attributeQuery(attr_name, node=node, exists=True):
            print(f"Warning: Attribute {attr_name} not found on {node}")
            return False

        # Try to find children (R, G, B or X, Y, Z)
        children = []
        for suffix in ["R", "G", "B"]:
            if cmds.attributeQuery(attr_name + suffix, node=node, exists=True):
                children.append(attr_name + suffix)

        if not children:
            for suffix in ["X", "Y", "Z"]:
                if cmds.attributeQuery(attr_name + suffix, node=node, exists=True):
                    children.append(attr_name + suffix)

        if children and len(children) >= 3:
            # Explicitly break connection to parent attribute if it exists
            # This prevents "ghost" connections where parent remains connected to old node
            try:
                if cmds.attributeQuery(attr_name, node=node, exists=True):
                    inputs = cmds.listConnections(
                        f"{node}.{attr_name}",
                        plugs=True,
                        source=True,
                        destination=False,
                    )
                    if inputs:
                        cmds.disconnectAttr(inputs[0], f"{node}.{attr_name}")
            except Exception as e:
                print(f"Warning: Failed to disconnect parent {attr_name}: {e}")

            # Connect to all 3 children
            for child in children[:3]:
                cmds.connectAttr(source_plug, f"{node}.{child}", force=True)
            return True
        else:
            # Fallback: try connecting to parent directly
            try:
                cmds.connectAttr(source_plug, f"{node}.{attr_name}", force=True)
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
        if not cmds.attributeQuery(attr_name, node=shader_node, exists=True):
            cmds.addAttr(
                shader_node,
                longName=attr_name,
                attributeType="float3",
                usedAsColor=True,
            )
            cmds.addAttr(
                shader_node,
                longName=f"{attr_name}R",
                attributeType="float",
                parent=attr_name,
            )
            cmds.addAttr(
                shader_node,
                longName=f"{attr_name}G",
                attributeType="float",
                parent=attr_name,
            )
            cmds.addAttr(
                shader_node,
                longName=f"{attr_name}B",
                attributeType="float",
                parent=attr_name,
            )

        target_plug = f"{shader_node}.{attr_name}"
        if not cmds.isConnected(f"{texture_node}.outColor", target_plug):
            cmds.connectAttr(f"{texture_node}.outColor", target_plug, force=True)

    @CoreUtils.undoable
    def connect_stingray_nodes(
        self, texture: str, texture_type: str, sr_node: object
    ) -> bool:
        """Connects texture files to the corresponding slots in the StingrayPBS shader node
        based on the texture type, including handling various specific texture types.

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of the texture (e.g., "Base_Color", "Roughness", "Metallic", "Emissive", etc.).
            sr_node (str): The StingrayPBS shader node to which the textures will be connected.

        Returns:
            bool: True if the connection is successful, False otherwise.
        """
        if texture_type in ["Base_Color", "Diffuse"]:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{sr_node}.TEX_color_map", force=True)
            cmds.setAttr(f"{sr_node}.use_color_map", 1)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{sr_node}.TEX_color_map", force=True)
            if cmds.attributeQuery("opacity", node=str(sr_node), exists=True):
                cmds.connectAttr(f"{texture_node}.outAlpha", f"{sr_node}.opacity", force=True)
                cmds.setAttr(f"{sr_node}.use_opacity_map", 1)
            cmds.setAttr(f"{sr_node}.use_color_map", 1)
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
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{sr_node}.{target_attr_name}", force=True
            )
            cmds.setAttr(f"{sr_node}.use_{texture_type.lower()}_map", 1)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic (RGB) -> Metallic Map
            # Connect RGB directly to ensure FBX export (Metallic is usually grayscale so RGB matches)
            cmds.connectAttr(f"{texture_node}.outColor", f"{sr_node}.TEX_metallic_map", force=True)

            # Smoothness (Alpha) -> Invert -> Roughness Map (Unity stores smoothness, Stingray expects roughness)
            rev_node = NodeUtils.create_render_node("reverse")
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{rev_node}.inputX", force=True)
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{rev_node}.inputY", force=True)
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{rev_node}.inputZ", force=True)
            self._connect_channel(f"{rev_node}.outputX", sr_node, "TEX_roughness_map")

            cmds.setAttr(f"{sr_node}.use_metallic_map", 1)
            cmds.setAttr(f"{sr_node}.use_roughness_map", 1)

        elif texture_type == "ORM":
            # Unreal/glTF ORM Map: R=AO, G=Roughness, B=Metallic
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            # Connect RGB directly to AO map (R channel matches AO) to ensure FBX export
            cmds.connectAttr(f"{texture_node}.outColor", f"{sr_node}.TEX_ao_map", force=True)

            # Connect other channels individually
            self._connect_channel(f"{texture_node}.outColorG", sr_node, "TEX_roughness_map")
            self._connect_channel(f"{texture_node}.outColorB", sr_node, "TEX_metallic_map")

            cmds.setAttr(f"{sr_node}.use_ao_map", 1)
            cmds.setAttr(f"{sr_node}.use_roughness_map", 1)
            cmds.setAttr(f"{sr_node}.use_metallic_map", 1)

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )

            # Connect metallic channel (R) -> TEX_metallic_map
            # Connect RGB directly to ensure FBX export (R=Metallic matches)
            cmds.connectAttr(f"{texture_node}.outColor", f"{sr_node}.TEX_metallic_map", force=True)

            # Connect AO channel (G) -> TEX_ao_map
            self._connect_channel(f"{texture_node}.outColorG", sr_node, "TEX_ao_map")

            # Connect smoothness channel (A) -> Invert -> TEX_roughness_map
            # Unity Smoothness is inverse of Roughness
            rev_node = NodeUtils.create_render_node("reverse")
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{rev_node}.inputX", force=True)
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{rev_node}.inputY", force=True)
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{rev_node}.inputZ", force=True)

            # Use reverse output X (float) for roughness
            self._connect_channel(f"{rev_node}.outputX", sr_node, "TEX_roughness_map")

            cmds.setAttr(f"{sr_node}.use_metallic_map", 1)
            cmds.setAttr(f"{sr_node}.use_ao_map", 1)
            cmds.setAttr(f"{sr_node}.use_roughness_map", 1)

        elif "Normal" in texture_type:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{sr_node}.TEX_normal_map", force=True)
            cmds.setAttr(f"{sr_node}.use_normal_map", 1)

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{sr_node}.TEX_emissive_map", force=True)
            cmds.setAttr(f"{sr_node}.use_emissive_map", 1)

        elif texture_type == "Ambient_Occlusion":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            # Connect RGB directly to ensure FBX export (AO is usually grayscale so RGB matches)
            cmds.connectAttr(f"{texture_node}.outColor", f"{sr_node}.TEX_ao_map", force=True)
            cmds.setAttr(f"{sr_node}.use_ao_map", 1)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{sr_node}.opacity", force=True)
            cmds.setAttr(f"{sr_node}.use_opacity_map", 1)

        elif texture_type == "Specular":
            if cmds.attributeQuery("TEX_specular_map", node=str(sr_node), exists=True):
                texture_node = NodeUtils.create_render_node(
                    "file",
                    fileTextureName=texture,
                    name=ptk.format_path(texture, section="name"),
                )
                cmds.connectAttr(
                    f"{texture_node}.outColor", f"{sr_node}.TEX_specular_map", force=True
                )
                if cmds.attributeQuery("use_specular_map", node=str(sr_node), exists=True):
                    cmds.setAttr(f"{sr_node}.use_specular_map", 1)
                return True
            return False

        elif texture_type == "Glossiness":
            if cmds.attributeQuery("TEX_glossiness_map", node=str(sr_node), exists=True):
                texture_node = NodeUtils.create_render_node(
                    "file",
                    fileTextureName=texture,
                    name=ptk.format_path(texture, section="name"),
                )
                # Connect RGB directly to ensure FBX export
                cmds.connectAttr(
                    f"{texture_node}.outColor", f"{sr_node}.TEX_glossiness_map", force=True
                )
                if cmds.attributeQuery("use_glossiness_map", node=str(sr_node), exists=True):
                    cmds.setAttr(f"{sr_node}.use_glossiness_map", 1)
                return True
            return False

        else:  # Unsupported texture type
            return False

        return True

    def connect_standard_surface_nodes(
        self, texture: str, texture_type: str, std_node: object
    ) -> bool:
        """Connects texture files to Maya Standard Surface shader slots.

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of texture (e.g., "Base_Color", "Roughness", "Metallic").
            std_node (str): The Standard Surface shader node.

        Returns:
            bool: True if connection successful, False otherwise.
        """
        if texture_type in ["Base_Color", "Diffuse"]:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{std_node}.baseColor", force=True)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{std_node}.baseColor", force=True)
            # Opacity is RGB, connect alpha to all channels
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{std_node}.opacityR", force=True)
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{std_node}.opacityG", force=True)
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{std_node}.opacityB", force=True)
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{std_node}.specularRoughness", force=True
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{std_node}.metalness", force=True)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,  # smoothness is the real alpha, not luminance
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic in RGB, smoothness in alpha (need to invert for roughness)
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True)
            cmds.connectAttr(f"{reverse_node}.outputX", f"{std_node}.specularRoughness", force=True)
            cmds.connectAttr(f"{texture_node}.outColorR", f"{std_node}.metalness", force=True)

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
            cmds.connectAttr(f"{texture_node}.outColorB", f"{std_node}.metalness", force=True)
            # Roughness (G)
            cmds.connectAttr(
                f"{texture_node}.outColorG", f"{std_node}.specularRoughness", force=True
            )
            # AO (R) -> Multiply with Base Color
            existing_conn = cmds.listConnections(
                f"{std_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
                cmds.connectAttr(f"{texture_node}.outColorR", f"{mult_node}.input2X", force=True)
                cmds.connectAttr(f"{texture_node}.outColorR", f"{mult_node}.input2Y", force=True)
                cmds.connectAttr(f"{texture_node}.outColorR", f"{mult_node}.input2Z", force=True)
                cmds.connectAttr(f"{mult_node}.output", f"{std_node}.baseColor", force=True)

            self._ensure_fbx_safe_connection(texture_node, std_node, "ORM_Map")

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,  # smoothness is the real alpha, not luminance
                name=ptk.format_path(texture, section="name"),
            )
            # Connect red channel (metallic) to metalness
            cmds.connectAttr(f"{texture_node}.outColorR", f"{std_node}.metalness", force=True)
            # Smoothness in alpha needs to be inverted to roughness
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True)
            cmds.connectAttr(f"{reverse_node}.outputX", f"{std_node}.specularRoughness", force=True)
            # AO in green channel - multiply with base color if already connected
            existing_conn = cmds.listConnections(
                f"{std_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
                cmds.connectAttr(f"{texture_node}.outColorG", f"{mult_node}.input2X", force=True)
                cmds.connectAttr(f"{texture_node}.outColorG", f"{mult_node}.input2Y", force=True)
                cmds.connectAttr(f"{texture_node}.outColorG", f"{mult_node}.input2Z", force=True)
                cmds.connectAttr(f"{mult_node}.output", f"{std_node}.baseColor", force=True)

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
            bump_node = cmds.shadingNode("bump2d", asUtility=True)
            cmds.setAttr(f"{bump_node}.bumpInterp", 1)  # Tangent space normals
            # Use outAlpha (grayscale) instead of outColor for bump2d compatibility
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{bump_node}.bumpValue", force=True)
            cmds.connectAttr(f"{bump_node}.outNormal", f"{std_node}.normalCamera", force=True)

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{std_node}.emissionColor", force=True)
            cmds.setAttr(f"{std_node}.emission", 1.0)

        elif texture_type == "Ambient_Occlusion":
            # Standard Surface doesn't have direct AO input, multiply with base color
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
            )
            # Create multiply node to combine AO with base color
            mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
            # If base color already connected, insert multiply
            existing_conn = cmds.listConnections(
                f"{std_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
            cmds.connectAttr(f"{texture_node}.outColor", f"{mult_node}.input2", force=True)
            cmds.connectAttr(f"{mult_node}.output", f"{std_node}.baseColor", force=True)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{std_node}.opacity", force=True)

        else:
            return False

        return True

    def connect_open_pbr_nodes(
        self, texture: str, texture_type: str, op_node: object
    ) -> bool:
        """Connects texture files to Maya OpenPBR Surface shader slots.

        OpenPBR attribute mapping:
            Base Color           -> baseColor (color3)
            Metallic             -> baseMetalness (float)
            Roughness            -> specularRoughness (float)
            Normal               -> bump2d.outNormal -> geometryNormal (vector)
            Emissive             -> emissionColor (color3) + emissionLuminance
            Opacity              -> geometryOpacity (color3, RGB driven by alpha)
            AO                   -> multiplied with baseColor (no native AO input)

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of texture (e.g., "Base_Color", "Roughness", "Metallic").
            op_node (str): The OpenPBR Surface shader node.

        Returns:
            bool: True if connection successful, False otherwise.
        """
        if texture_type in ["Base_Color", "Diffuse"]:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{op_node}.baseColor", force=True)

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{op_node}.baseColor", force=True)
            # geometryOpacity is color3 — drive all channels from alpha
            for chan in ("R", "G", "B"):
                cmds.connectAttr(
                    f"{texture_node}.outAlpha",
                    f"{op_node}.geometryOpacity{chan}",
                    force=True,
                )
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{op_node}.specularRoughness", force=True
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{op_node}.baseMetalness", force=True)

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,  # smoothness is the real alpha, not luminance
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic in RGB, smoothness in alpha (need to invert for roughness)
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True)
            cmds.connectAttr(f"{reverse_node}.outputX", f"{op_node}.specularRoughness", force=True)
            cmds.connectAttr(f"{texture_node}.outColorR", f"{op_node}.baseMetalness", force=True)

            self._ensure_fbx_safe_connection(
                texture_node, op_node, "Metallic_Smoothness_Map"
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
            cmds.connectAttr(f"{texture_node}.outColorB", f"{op_node}.baseMetalness", force=True)
            cmds.connectAttr(
                f"{texture_node}.outColorG", f"{op_node}.specularRoughness", force=True
            )
            # AO (R) -> Multiply with Base Color
            existing_conn = cmds.listConnections(
                f"{op_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
                cmds.connectAttr(f"{texture_node}.outColorR", f"{mult_node}.input2X", force=True)
                cmds.connectAttr(f"{texture_node}.outColorR", f"{mult_node}.input2Y", force=True)
                cmds.connectAttr(f"{texture_node}.outColorR", f"{mult_node}.input2Z", force=True)
                cmds.connectAttr(f"{mult_node}.output", f"{op_node}.baseColor", force=True)

            self._ensure_fbx_safe_connection(texture_node, op_node, "ORM_Map")

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,  # smoothness is the real alpha, not luminance
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColorR", f"{op_node}.baseMetalness", force=True)
            # Smoothness (alpha) -> invert -> roughness
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True)
            cmds.connectAttr(f"{reverse_node}.outputX", f"{op_node}.specularRoughness", force=True)
            # AO (G) -> multiply with base color if already connected
            existing_conn = cmds.listConnections(
                f"{op_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
                cmds.connectAttr(f"{texture_node}.outColorG", f"{mult_node}.input2X", force=True)
                cmds.connectAttr(f"{texture_node}.outColorG", f"{mult_node}.input2Y", force=True)
                cmds.connectAttr(f"{texture_node}.outColorG", f"{mult_node}.input2Z", force=True)
                cmds.connectAttr(f"{mult_node}.output", f"{op_node}.baseColor", force=True)

            self._ensure_fbx_safe_connection(texture_node, op_node, "MSAO_Map")

        elif "Normal" in texture_type:
            # OpenPBR uses geometryNormal — feed via bump2d in tangent-space mode
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
            )
            bump_node = cmds.shadingNode("bump2d", asUtility=True)
            cmds.setAttr(f"{bump_node}.bumpInterp", 1)  # Tangent space normals
            cmds.connectAttr(f"{texture_node}.outAlpha", f"{bump_node}.bumpValue", force=True)
            cmds.connectAttr(f"{bump_node}.outNormal", f"{op_node}.geometryNormal", force=True)

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(f"{texture_node}.outColor", f"{op_node}.emissionColor", force=True)
            # OpenPBR emissionLuminance is in nits (cd/m^2); default 0 means no
            # emission. 1000 nits is a reasonable starting point for a visibly
            # glowing surface (typical emissive panel/screen). Tweak per scene.
            if cmds.attributeQuery("emissionLuminance", node=op_node, exists=True):
                cmds.setAttr(f"{op_node}.emissionLuminance", 1000.0)

        elif texture_type == "Ambient_Occlusion":
            # OpenPBR has no native AO input — multiply with base color
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
            )
            mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
            existing_conn = cmds.listConnections(
                f"{op_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
            cmds.connectAttr(f"{texture_node}.outColor", f"{mult_node}.input2", force=True)
            cmds.connectAttr(f"{mult_node}.output", f"{op_node}.baseColor", force=True)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            # geometryOpacity is color3 — drive all channels from alpha
            for chan in ("R", "G", "B"):
                cmds.connectAttr(
                    f"{texture_node}.outAlpha",
                    f"{op_node}.geometryOpacity{chan}",
                    force=True,
                )

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
                created_roughness_map = ptk.MapFactory.create_roughness_from_spec(specular_map[0])
                created_metallic_map = ptk.MapFactory.create_metallic_from_spec(specular_map[0])

                # Save these images to disk and get their file paths
                base_name = ptk.MapFactory.get_base_texture_name(specular_map[0])
                out_dir = os.path.dirname(specular_map[0])

                rough_path = os.path.join(
                    out_dir, f"{base_name}_Roughness.{output_extension}"
                )
                metal_path = os.path.join(
                    out_dir, f"{base_name}_Metallic.{output_extension}"
                )

                ptk.ImgUtils.save_image(created_roughness_map, rough_path)
                ptk.ImgUtils.save_image(created_metallic_map, metal_path)

                # Now you can combine using file paths:
                combined_map_name = f"{base_name}_MetallicSmoothness.{output_extension}"
                combined_map_path = os.path.join(out_dir, combined_map_name)

                combined_map = ptk.MapFactory.pack_smoothness_into_metallic(
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

                combined_map = ptk.MapFactory.pack_smoothness_into_metallic(
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

            if (not metallic_map or not roughness_map) and specular_map:
                # create_*_from_spec return in-memory Image.Image objects; save them
                # to disk and append the resulting paths (mirrors the True-branch),
                # keeping filtered_textures a pure list of file-path strings.
                base_name = ptk.MapFactory.get_base_texture_name(specular_map[0])
                out_dir = os.path.dirname(specular_map[0])

                if not metallic_map:
                    created_metallic_map = ptk.MapFactory.create_metallic_from_spec(
                        specular_map[0]
                    )
                    metal_path = os.path.join(
                        out_dir, f"{base_name}_Metallic.{output_extension}"
                    )
                    ptk.ImgUtils.save_image(created_metallic_map, metal_path)
                    filtered_textures.append(metal_path)

                if not roughness_map:
                    created_roughness_map = ptk.MapFactory.create_roughness_from_spec(
                        specular_map[0]
                    )
                    rough_path = os.path.join(
                        out_dir, f"{base_name}_Roughness.{output_extension}"
                    )
                    ptk.ImgUtils.save_image(created_roughness_map, rough_path)
                    filtered_textures.append(rough_path)

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
            invert_alpha = False  # no alpha source; pack_msao_texture fills a default

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
            mask_map_path = ptk.MapFactory.pack_msao_texture(
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
                combined_map = ptk.MapFactory.pack_transparency_into_albedo(
                    base_color_map[0], transparency_map[0]
                )
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map
                ] + [combined_map]

        # If no base color or diffuse map is found, return the list unchanged
        return textures


class GameShaderSlots(GameShader):
    msg_intro = """<u>To setup the material:</u>
        <br>• Click the <b>Create Network</b> button to select texture maps and create the shader connections. This will bridge Stingray PBS and (optionally) Arnold aiStandardSurface shaders, create a shading network from provided textures, and manage OpenGL and DirectX normal map conversions.

        <p><b>Note:</b> To correctly render opacity and transmission in Maya, the Opaque setting needs to be disabled on the Shape node.
        If Opaque is enabled, opacity will not work at all. Transmission will work, however any shadows cast by
        the object will always be solid and not pick up the Transparent Color or density of the shader.</p>
    """

    def __init__(self, switchboard):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.game_shader

        # Don't keep this window glued above other tools — user can use the
        # pin button to toggle stay-on-top when needed.
        if hasattr(self.ui, "set_flags"):
            self.ui.set_flags(WindowStaysOnTopHint=False)

        self.workspace_dir = EnvUtils.get_env_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")
        self.image_files = None
        self.last_created_shader = None

        self.ui.txt001.setText(self.msg_intro)

        # Route the shared logger into the txt001 QTextBrowser with HTML
        # colorization. Using setup_logging_redirect (instead of the old
        # CallbackLogHandler) is what enables clickable <a href="action://…">
        # links inside log messages.
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt001)

        # Dispatch action:// links (e.g. select the created shader).
        if hasattr(self.ui.txt001, "anchorClicked"):
            self.ui.txt001.anchorClicked.connect(self._on_log_link_clicked)

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_graph_material",
            setText="Open in Editor",
            setToolTip="Graph the material in the Hypershade.",
        )
        widget.set_help_text(
            fmt(
                title="Game Shader",
                body="Build complete PBR shader networks from a folder of "
                "texture maps. Map types (Base Color, Normal, Roughness, "
                "Metallic, AO, etc.) are auto-detected from file names.",
                steps=[
                    "Set <b>Material Name</b> and the <b>Prefix / Suffix</b> "
                    "(affix-mode option box selects placement).",
                    "Pick a <b>Shader Type</b> — Stingray PBS / Standard "
                    "Surface / OpenPBR Surface.",
                    "Pick a <b>Preset</b> — the preset's tooltip describes "
                    "its target workflow (UE/Unity/film/etc.).",
                    "Enable <b>Arnold</b> to also create an aiStandardSurface "
                    "bridge for IPR rendering.",
                    "Press <b>Create</b> and select a folder; results stream "
                    "into the log panel.",
                ],
                notes=[
                    "Use <b>Open in Editor</b> from the header menu to graph "
                    "the resulting material in the Hypershade.",
                ],
            )
        )

    def lbl_graph_material(self):
        """Graph the material in the Hypershade."""
        if self.last_created_shader:
            MatUtils.graph_materials(self.last_created_shader)
        elif cmds.objExists(self.mat_name):
            MatUtils.graph_materials(self.mat_name)
        else:
            cmds.warning(f"Material '{self.mat_name}' not found.")

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
        """Return the affix text when it resolves as a prefix, else empty string."""
        if not hasattr(self.ui, "txt002"):
            return ""
        prefix, _ = self.ui.txt002.option_box.resolve_affix(default="prefix")
        return prefix

    @property
    def mat_suffix(self) -> str:
        """Return the affix text when it resolves as a suffix, else empty string."""
        if not hasattr(self.ui, "txt002"):
            return ""
        _, suffix = self.ui.txt002.option_box.resolve_affix(default="prefix")
        return suffix

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
        """Selected output extension, or '' when 'Profile default' is chosen.

        An empty string signals the caller to defer per-map format to the selected
        workflow profile's template rather than forcing one container for all maps.

        Returns:
            (str) The file extension in lowercase (e.g., 'png', 'jpg'), or ''.
        """
        text = self.ui.cmb003.currentText().lower()
        return "" if text.startswith("profile") else text

    @property
    def shader_type(self) -> str:
        """Get the shader type selection.

        Returns:
            (str) One of 'stingray', 'standard_surface', or 'open_pbr'.
        """
        if hasattr(self.ui, "cmb004"):
            text = self.ui.cmb004.currentText()
            if "Open PBR" in text or "OpenPBR" in text:
                return "open_pbr"
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
        """Initialize Output Format.

        Selecting 'Profile default' defers each map's container/bit-depth to the
        selected workflow profile's output template; a concrete format forces that
        container for all maps.
        """
        if not widget.is_initialized:
            # Append 'Profile default' LAST so the existing format indices are
            # preserved — combobox state is persisted by index, so inserting it at
            # the front would silently shift every saved selection by one.
            widget.add([*ptk.ImgUtils.writable, "Profile default"])

    def txt002_init(self, widget):
        """Add a prefix/suffix/auto-mode picker to the affix field."""
        widget.option_box.set_affix(
            default="prefix",
            on_change=lambda _mode, w=widget: self._apply_affix_placeholder(w),
        )
        self._apply_affix_placeholder(widget)

    @staticmethod
    def _apply_affix_placeholder(widget):
        mode = widget.option_box.affix_mode
        if mode == "prefix":
            widget.setPlaceholderText("Prefix")
            widget.setToolTip(
                'Prefix prepended to the base name.\n'
                'Example: "MAT_" + "brick" → "MAT_brick".'
            )
        elif mode == "suffix":
            widget.setPlaceholderText("Suffix")
            widget.setToolTip(
                'Suffix appended to the base name.\n'
                'Example: "brick" + "_MAT" → "brick_MAT".'
            )
        else:  # auto
            widget.setPlaceholderText("Affix")
            widget.setToolTip(
                "Affix — placement inferred from '_' position.\n"
                "  '_MAT' → suffix (appended)\n"
                "  'MAT_' → prefix (prepended)"
            )

    def b000(self):
        """Create network."""
        image_files = self.sb.file_dialog(
            file_types=[f"*.{ext}" for ext in ptk.ImgUtils.texture_file_types],
            title="Select one or more image files to open.",
            start_dir=self.source_images_dir,
        )

        if not image_files:
            return

        self.image_files = image_files
        self.ui.txt001.clear()

        create_arnold = self.ui.chk000.isChecked()

        # Get template configuration using combo box text
        template_name = self.ui.cmb002.currentText()

        # 'Profile default' (empty ext) → let the workflow profile drive per-map
        # format; a concrete ext overrides it for all maps.
        ext = self.output_extension
        output_profile = template_name if not ext else None

        def progress_adapter(p, m):
            # Surface progress in the footer (the .ui has no progressBar —
            # the old setValue branch was dead) and keep the UI responsive
            # during the long network build.
            self.ui.footer.setText(f"{m} ({int(p)}%)" if m else f"{int(p)}%")
            self.sb.QtWidgets.QApplication.instance().processEvents()

        self.last_created_shader = self.create_network(
            self.image_files,
            self.mat_name,
            prefix=self.mat_prefix,
            suffix=self.mat_suffix,
            config=template_name,
            shader_type=self.shader_type,
            normal_type=self.normal_map_type,
            create_arnold=create_arnold,
            cleanup_base_color=False,  # Can be exposed in UI later if needed
            output_extension=ext or None,
            output_profile=output_profile,
            progress_callback=progress_adapter,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("game_shader", reload=True)
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
