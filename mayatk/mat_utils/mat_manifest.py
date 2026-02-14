# !/usr/bin/python
# coding=utf-8
import logging
from typing import Dict, List, Any, Optional

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap, ShaderAttrs
from mayatk.mat_utils._mat_utils import MatUtils

logger = logging.getLogger(__name__)


class MatManifest(ptk.HelpMixin):
    """Builds and restores a material-to-texture manifest for bridge workflows.

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

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    @classmethod
    def restore(
        cls,
        mat_name: str,
        manifest: Dict[str, Any],
        source_mat_name: Optional[str] = None,
    ) -> int:
        """Reconnect file textures to *mat_name* from a previously built manifest.

        After a ShaderFX ``loadGraph`` call every external connection is lost.
        This method looks up the texture paths stored in *manifest* and
        reconnects the corresponding file nodes.

        Parameters:
            mat_name: The material to restore textures onto.
            manifest: A manifest dict as returned by :meth:`build`.
            source_mat_name: Key to look up in the manifest. Defaults to
                *mat_name*. Useful when the material was duplicated/renamed
                after the manifest was captured.

        Returns:
            Number of texture slots successfully reconnected.
        """
        key = source_mat_name or mat_name
        mat_data = manifest.get("materials", {}).get(key, {})
        if not mat_data:
            logger.debug(f"No manifest entry for '{key}', nothing to restore.")
            return 0

        try:
            node_type = cmds.nodeType(mat_name)
        except RuntimeError:
            return 0

        if node_type not in ShaderAttributeMap.SHADER_ATTRS:
            return 0

        mapping: ShaderAttrs = ShaderAttributeMap.SHADER_ATTRS[node_type]
        restored = 0

        for field, tex_path in mat_data.items():
            slot_def = getattr(mapping, field, None)
            if not slot_def:
                continue

            attr_name, out_plug = slot_def
            full_attr = f"{mat_name}.{attr_name}"
            if not cmds.objExists(full_attr):
                continue

            # Find an existing file node that points to this path, or create one.
            file_node = cls._find_or_create_file_node(tex_path)
            if not file_node:
                continue

            # Determine the correct output plug.  ShaderAttributeMap may list a
            # scalar plug (e.g. "outColorR") for channels that are read as
            # scalars, but some shader types (StingrayPBS) expect compound
            # inputs on their TEX_* attributes.  Try the mapped plug first and
            # fall back to "outColor" on type-mismatch.
            src_plug = f"{file_node}.{out_plug}"
            try:
                cmds.connectAttr(src_plug, full_attr, force=True)
            except RuntimeError:
                # Retry with compound plug if the scalar one failed.
                if out_plug != "outColor":
                    src_plug = f"{file_node}.outColor"
                    try:
                        cmds.connectAttr(src_plug, full_attr, force=True)
                    except Exception as exc:
                        logger.debug(
                            f"Could not reconnect {src_plug} -> {full_attr}: {exc}"
                        )
                        continue
                else:
                    continue

            # Auto-enable the corresponding use_*_map toggle.
            if attr_name.startswith("TEX_"):
                toggle = attr_name.replace("TEX_", "use_", 1)
                if cmds.objExists(f"{mat_name}.{toggle}"):
                    try:
                        cmds.setAttr(f"{mat_name}.{toggle}", 1.0)
                    except Exception:
                        pass
            restored += 1

        if restored:
            logger.info(
                f"Restored {restored} texture slot(s) on '{mat_name}' from manifest."
            )
        return restored

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_or_create_file_node(tex_path: str) -> Optional[str]:
        """Return an existing file node for *tex_path*, or create a new one.

        Searches all ``file`` nodes in the scene first to avoid duplicates.
        """
        import os

        norm = os.path.normpath(tex_path)
        for fn in cmds.ls(type="file") or []:
            existing = cmds.getAttr(f"{fn}.fileTextureName") or ""
            if os.path.normpath(existing) == norm:
                return fn

        # Create a new file node + place2dTexture
        file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
        cmds.setAttr(f"{file_node}.fileTextureName", tex_path, type="string")

        p2d = cmds.shadingNode("place2dTexture", asUtility=True)
        _PAIRS = [
            ("outUV", "uvCoord"),
            ("outUvFilterSize", "uvFilterSize"),
            ("coverage", "coverage"),
            ("translateFrame", "translateFrame"),
            ("rotateFrame", "rotateFrame"),
            ("mirrorU", "mirrorU"),
            ("mirrorV", "mirrorV"),
            ("stagger", "stagger"),
            ("wrapU", "wrapU"),
            ("wrapV", "wrapV"),
            ("repeatUV", "repeatUV"),
            ("vertexUvOne", "vertexUvOne"),
            ("vertexUvTwo", "vertexUvTwo"),
            ("vertexUvThree", "vertexUvThree"),
            ("vertexCameraOne", "vertexCameraOne"),
            ("noiseUV", "noiseUV"),
            ("offset", "offset"),
            ("rotateUV", "rotateUV"),
        ]
        for src, dst in _PAIRS:
            try:
                cmds.connectAttr(f"{p2d}.{src}", f"{file_node}.{dst}", force=True)
            except Exception:
                pass

        return file_node
