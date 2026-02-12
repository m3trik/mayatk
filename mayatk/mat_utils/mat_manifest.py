# !/usr/bin/python
# coding=utf-8
import logging
from typing import Dict, List, Any

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap, ShaderAttrs
from mayatk.mat_utils._mat_utils import MatUtils

logger = logging.getLogger(__name__)


class MatManifest(ptk.HelpMixin):
    """Builds a material-to-texture manifest for bridge workflows.

    The output is a plain dict suitable for ``json.dump`` and consumption
    by external tools (Marmoset Toolbag, Unreal, Unity, etc.).

    Manifest structure::

        {
            "materials": {
                "MAT_Body": {
                    "baseColor": "C:/tex/body_BaseColor.png",
                    "normal":    "C:/tex/body_Normal.png",
                    ...
                }
            }
        }

    Slot keys match the field names defined in
    :class:`~mayatk.mat_utils.shader_attribute_map.ShaderAttrs`.
    """

    @classmethod
    def build(cls, objects: List) -> Dict[str, Any]:
        """Build a manifest from the materials assigned to *objects*.

        Parameters:
            objects: Maya transform/shape nodes (strings or PyNodes).

        Returns:
            Manifest dict ready for serialisation.
        """
        manifest: Dict[str, Any] = {"materials": {}}

        obj_strings = [str(o) for o in objects]
        materials = MatUtils.get_mats(obj_strings, as_strings=True)

        for mat_name in materials:
            mat_data = cls._process_material(mat_name)
            if mat_data:
                manifest["materials"][mat_name] = mat_data

        return manifest

    @classmethod
    def _process_material(cls, mat_name: str) -> Dict[str, str]:
        """Resolve texture paths for every mapped slot of a single material.

        Parameters:
            mat_name: The Maya material node name.

        Returns:
            Dict mapping slot names (e.g. ``"baseColor"``) to absolute file paths.
            Empty dict if the shader type is unsupported or has no textures.
        """
        try:
            node_type = cmds.nodeType(mat_name)
        except RuntimeError:
            return {}

        if node_type not in ShaderAttributeMap.SHADER_ATTRS:
            logger.debug(
                f"Unmapped shader type '{node_type}' on '{mat_name}', skipping."
            )
            return {}

        mapping: ShaderAttrs = ShaderAttributeMap.SHADER_ATTRS[node_type]
        data: Dict[str, str] = {}

        for field in mapping._fields:
            slot_def = getattr(mapping, field)
            if not slot_def:
                continue

            attr_name, _ = slot_def
            file_node = MatUtils.get_texture_file_node(mat_name, attr_name)
            if not file_node:
                continue

            paths = MatUtils._paths_from_file_nodes([file_node], absolute=True)
            if paths:
                data[field] = paths[0]

        return data
