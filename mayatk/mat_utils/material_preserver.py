# !/usr/bin/python
# coding=utf-8
from typing import Dict, List, Optional, Set
import pymel.core as pm
import pythontk as ptk

from mayatk.mat_utils import MatUtils
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap


class MaterialPreserver(ptk.LoggingMixin):
    """Handles material preservation and assignment during object swapping operations."""

    def __init__(self):
        """Initialize the MaterialPreserver."""
        super().__init__()

    @staticmethod
    def _clean_namespace_name(namespaced_name: str) -> str:
        """Extract clean name without namespace prefix."""
        return namespaced_name.split(":")[-1]

    def get_material_node_types(self) -> Set[str]:
        """Get supported material node types."""
        material_types = {
            "shadingEngine",
            "materialInfo",
            "file",
            "place2dTexture",
            "bump2d",
            "aiNormalMap",
            "samplerInfo",
        }
        # Add shader types from ShaderAttributeMap
        material_types.update(ShaderAttributeMap.SHADER_TYPES)
        return material_types

    def is_material_related_node(self, node) -> bool:
        """Check if a node is material-related."""
        if not hasattr(node, "nodeType"):
            return False

        return node.nodeType() in self.get_material_node_types()

    def get_material_assignments(self, obj) -> Dict[str, Dict]:
        """Get material assignments using MatUtils for comprehensive material detection."""
        material_assignments = {}

        if not (hasattr(obj, "getShapes") and obj.getShapes()):
            return material_assignments

        for shape in obj.getShapes():
            shape_name = self._clean_namespace_name(shape.nodeName())

            # Use MatUtils to get comprehensive material information
            try:
                # Get shading engines connected to this shape
                connected_sgs = shape.listConnections(type="shadingEngine")
                if connected_sgs:
                    material_info = {
                        "shading_engines": connected_sgs,
                        "materials": [],
                        "textures": [],
                    }

                    # Get the actual materials (shaders) from the shading engines
                    for sg in connected_sgs:
                        # Get connected surface shader
                        surface_shaders = sg.surfaceShader.inputs()
                        if surface_shaders:
                            material_info["materials"].extend(surface_shaders)

                            # For each material, collect its texture network
                            for material in surface_shaders:
                                material_textures = MatUtils.collect_material_paths(
                                    [material.name()]
                                )
                                if material_textures:
                                    material_info["textures"].extend(material_textures)

                    material_assignments[shape_name] = material_info

            except Exception as e:
                self.logger.debug(
                    f"Advanced material detection failed for {shape_name}: {e}"
                )
                # Fallback to simple method
                connected_sgs = shape.listConnections(type="shadingEngine")
                if connected_sgs:
                    material_assignments[shape_name] = {
                        "shading_engines": connected_sgs
                    }

        return material_assignments

    def collect_material_assignments(self, obj):
        """Collect material assignments including shaders and textures."""
        return self.get_material_assignments(obj)

    def handle_object_materials(
        self, duplicated_obj, original_material_assignments: Dict
    ) -> None:
        """Handle material assignment with shader utilities integration."""
        if not (original_material_assignments and hasattr(duplicated_obj, "getShapes")):
            return

        for shape in duplicated_obj.getShapes():
            shape_base_name = self._clean_namespace_name(shape.nodeName())
            clean_shape_name = self._clean_shape_name_for_matching(shape_base_name)

            # Find original material assignment for this shape
            original_material_info = self._find_matching_material_assignment(
                shape_base_name, clean_shape_name, original_material_assignments
            )

            if original_material_info:
                self._assign_or_import_material(
                    shape, original_material_info, shape_base_name
                )

    def _find_matching_material_assignment(
        self, shape_base_name: str, clean_shape_name: str, material_assignments: Dict
    ) -> Optional[Dict]:
        """Find matching material assignment for a shape."""
        for orig_shape_name, material_info in material_assignments.items():
            orig_clean = self._clean_namespace_name(orig_shape_name)
            if orig_clean == shape_base_name or orig_clean.startswith(clean_shape_name):
                return material_info
        return None

    def _assign_or_import_material(
        self, shape, material_info: Dict, shape_name: str
    ) -> None:
        """Material assignment using shader utilities."""
        # Get shading engines from material info
        shading_engines = material_info.get("shading_engines", [])

        for sg in shading_engines:
            sg_name = self._clean_namespace_name(sg.nodeName())

            if pm.objExists(sg_name):
                # Use existing shading engine
                existing_sg = pm.PyNode(sg_name)
                pm.sets(existing_sg, edit=True, forceElement=shape)
                self.logger.debug(
                    f"Assigned existing material {sg_name} to {shape.nodeName()}"
                )
            else:
                # Import the material network
                self._import_material_network(sg, sg_name, shape, material_info)

    def _import_material_network(
        self, sg, sg_name: str, shape, material_info: Optional[Dict] = None
    ) -> None:
        """Import material network with shader utilities integration."""
        try:
            # Get materials from the material info if provided
            materials = material_info.get("materials", []) if material_info else []

            if materials:
                # For each material, check if we can use ShaderAttributeMap for better handling
                for material in materials:
                    material_type = material.nodeType()

                    # Check if this is a known shader type in our attribute map
                    if material_type in ShaderAttributeMap.SHADER_TYPES:
                        self.logger.debug(
                            f"Found {material_type} shader - using enhanced import"
                        )
                        # Use shader-aware import
                        self._import_shader_with_attributes(
                            material, sg, sg_name, shape
                        )
                    else:
                        # Fall back to standard material network duplication
                        self._import_standard_material_network(sg, sg_name, shape)
            else:
                # No materials found, use standard method
                self._import_standard_material_network(sg, sg_name, shape)

        except Exception as mat_error:
            self.logger.warning(f"Material import failed for {sg_name}: {mat_error}")
            # Final fallback
            self._import_standard_material_network(sg, sg_name, shape)

    def _import_shader_with_attributes(self, material, sg, sg_name: str, shape) -> None:
        """Import shader using ShaderAttributeMap knowledge for better attribute handling."""
        try:
            material_type = material.nodeType()

            # Get the full material network including the material and shading engine
            material_network = pm.listHistory([material, sg], allConnections=True)
            material_nodes = [
                node for node in material_network if self.is_material_related_node(node)
            ]

            if material_nodes:
                # Duplicate the entire network
                duplicated_materials = pm.duplicate(
                    material_nodes, upstreamNodes=True, inputConnections=True
                )

                # Find and clean up the duplicated nodes
                new_sg = self._find_and_rename_shading_engine(
                    duplicated_materials, sg_name
                )
                self._clean_duplicated_material_names(
                    duplicated_materials, material_nodes, new_sg
                )

                if new_sg:
                    pm.sets(new_sg, edit=True, forceElement=shape)
                    self.logger.debug(
                        f"Imported {material_type} shader network and assigned to {shape.nodeName()}"
                    )
                else:
                    self.logger.warning(
                        f"Failed to find duplicated shading engine for {sg_name}"
                    )

        except Exception as e:
            self.logger.warning(f"Shader-aware import failed: {e}")
            # Fallback to basic material assignment
            self._fallback_material_assignment(sg, shape)

    def _import_standard_material_network(self, sg, sg_name: str, shape) -> None:
        """Import material network using standard duplication."""
        try:
            # Get all upstream nodes from the shading engine
            material_network = pm.listHistory(sg, allConnections=True)
            material_nodes = [
                node for node in material_network if self.is_material_related_node(node)
            ]

            if material_nodes:
                duplicated_materials = pm.duplicate(
                    material_nodes, upstreamNodes=True, inputConnections=True
                )

                # Find and rename the new shading engine
                new_sg = self._find_and_rename_shading_engine(
                    duplicated_materials, sg_name
                )

                # Clean names of all duplicated material nodes
                self._clean_duplicated_material_names(
                    duplicated_materials, material_nodes, new_sg
                )

                if new_sg:
                    pm.sets(new_sg, edit=True, forceElement=shape)
                    self.logger.debug(
                        f"Imported and assigned material {sg_name} to {shape.nodeName()}"
                    )
                else:
                    self.logger.warning(
                        f"Failed to find duplicated shading engine for {sg_name}"
                    )

        except Exception as mat_error:
            self.logger.warning(f"Failed to import material {sg_name}: {mat_error}")
            self._fallback_material_assignment(sg, shape)

    def _clean_shape_name_for_matching(self, shape_name: str) -> str:
        """Clean shape name for material matching."""
        if shape_name.endswith("Shape"):
            return shape_name[:-5]  # Remove "Shape"
        elif shape_name[-1].isdigit():
            import re

            return re.sub(r"\d+$", "", shape_name)
        return shape_name

    def _find_and_rename_shading_engine(self, duplicated_materials: List, sg_name: str):
        """Find the shading engine in duplicated materials and rename it."""
        for dup_node in duplicated_materials:
            if isinstance(dup_node, pm.nt.ShadingEngine):
                if dup_node.nodeName() != sg_name:
                    dup_node.rename(sg_name)
                return dup_node
        return None

    def _clean_duplicated_material_names(
        self, duplicated_materials: List, material_nodes: List, new_sg
    ) -> None:
        """Clean names of all duplicated material nodes."""
        for dup_node, orig_node in zip(duplicated_materials, material_nodes):
            if dup_node != new_sg:  # Skip SG as we already renamed it
                orig_clean_name = self._clean_namespace_name(orig_node.nodeName())
                if dup_node.nodeName() != orig_clean_name:
                    try:
                        dup_node.rename(orig_clean_name)
                    except:
                        pass  # Name might already exist

    def _fallback_material_assignment(self, sg, shape) -> None:
        """Fallback material assignment using original namespaced material."""
        try:
            pm.sets(sg, edit=True, forceElement=shape)
            self.logger.debug(
                f"Assigned original namespaced material {sg.nodeName()} to {shape.nodeName()}"
            )
        except:
            pass

    def apply_materials_to_object(
        self, duplicated_obj, original_material_assignments: Dict
    ) -> None:
        """Apply material handling to a duplicated object."""
        self.handle_object_materials(duplicated_obj, original_material_assignments)


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
