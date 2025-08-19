# !/usr/bin/python
# coding=utf-8
from typing import Dict, List
import pymel.core as pm
import pythontk as ptk

# From this package
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap


class MaterialTransfer(ptk.LoggingMixin):
    """ """

    def __init__(self, log_level="WARNING"):
        """ """
        self.logger.setLevel(log_level)

    @staticmethod
    def _clean_namespace_name(namespaced_name: str) -> str:
        """Extract clean name without namespace prefix."""
        return namespaced_name.split(":")[-1]

    def is_material_related_node(self, node) -> bool:
        """Check if a node is material-related."""
        if not hasattr(node, "nodeType"):
            return False

        return node.nodeType() in set(ShaderAttributeMap.SHADER_TYPES)

    def get_material_assignments(self, obj) -> Dict[str, List]:
        """Get material assignments for an object."""
        assignments = {}

        if not hasattr(obj, "getShapes"):
            return assignments

        for shape in obj.getShapes():
            shape_name = shape.nodeName().split(":")[-1]
            sgs = shape.listConnections(type="shadingEngine")
            if sgs:
                assignments[shape_name] = sgs

        return assignments

    def collect_material_assignments(self, obj):
        """Collect material assignments including shaders and textures."""
        return self.get_material_assignments(obj)

    def handle_object_materials(self, target_obj, material_assignments: Dict) -> None:
        """Simple material handling - let Maya do the heavy lifting."""
        if not (material_assignments and hasattr(target_obj, "getShapes")):
            return

        for shape in target_obj.getShapes():
            shape_name = shape.nodeName().split(":")[-1]

            # Find matching materials
            sgs = self._find_matching_materials(shape_name, material_assignments)

            for sg in sgs:
                self._assign_material(shape, sg)

    def _find_matching_materials(self, shape_name: str, assignments: Dict) -> List:
        """Find materials for a shape with simple matching."""
        # Direct match first
        if shape_name in assignments:
            return assignments[shape_name]

        # Simple fuzzy match - remove Shape suffix and numbers
        clean_name = shape_name.rstrip("Shape0123456789")

        for orig_name, sgs in assignments.items():
            if orig_name.startswith(clean_name) or clean_name in orig_name:
                return sgs

        return []

    def _assign_material(self, shape, sg) -> None:
        """Assign material using Maya's robust duplication."""
        try:
            sg_name = sg.nodeName().split(":")[-1]

            if pm.objExists(sg_name):
                # Material already exists locally
                existing_sg = pm.PyNode(sg_name)
                pm.sets(existing_sg, edit=True, forceElement=shape)
                self.logger.debug(f"Assigned existing material: {sg_name}")
            else:
                # Use Maya's built-in material import
                self._import_material_simple(sg, shape)

        except Exception as e:
            self.logger.warning(f"Material assignment failed: {e}")

    def _import_material_simple(self, sg, shape) -> None:
        """Import material using Maya's duplicate with upstream nodes."""
        try:
            # Maya handles the entire network automatically
            duplicated = pm.duplicate(sg, upstreamNodes=True, inputConnections=True)

            # Find the new shading engine
            new_sg = None
            for node in duplicated:
                if isinstance(node, pm.nt.ShadingEngine):
                    new_sg = node
                    break

            if new_sg:
                # Clean up the name
                clean_name = sg.nodeName().split(":")[-1]
                if new_sg.nodeName() != clean_name:
                    try:
                        new_sg.rename(clean_name)
                    except:
                        pass  # Name might exist

                # Assign to shape
                pm.sets(new_sg, edit=True, forceElement=shape)
                self.logger.debug(f"Imported and assigned material: {clean_name}")
            else:
                # Fallback - assign original (might be namespaced)
                pm.sets(sg, edit=True, forceElement=shape)

        except Exception as e:
            self.logger.warning(f"Material import failed: {e}")
            # Final fallback
            try:
                pm.sets(sg, edit=True, forceElement=shape)
            except:
                pass


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
