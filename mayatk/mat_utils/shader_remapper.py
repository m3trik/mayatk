# !/usr/bin/python
# coding=utf-8
from typing import List, Dict, Any, Tuple, Type

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap


class ShaderRemapper(ptk.LoggingMixin):
    def __init__(
        self,
        attr_map: Type,
        name_suffix: str = "_remapped",
        assign: bool = True,
        verbose: bool = False,
        log_level: str = "DEBUG",
    ):
        super().__init__()
        self.attr_map = attr_map
        self.name_suffix = name_suffix
        self.assign = assign
        self.verbose = verbose
        self.logger.setLevel(log_level)

    def remap_shaders(
        self,
        shaders: List[str],
        target_type: str,
    ) -> Dict[str, str]:
        """
        For each shader:
        - Create a new shader node of target_type.
        - Connect all texture nodes according to logical attribute mapping.
        - Rename the new shader (old name + suffix).
        - Optionally assign new shader to all geo assigned to the old shader.
        Returns {old_shader: new_shader}
        """
        result = {}
        for old_shader in shaders:
            src_type = cmds.nodeType(old_shader)
            mapping = self.attr_map.get_mapping(src_type, target_type)
            if not mapping:
                if self.verbose:
                    self.logger.info(
                        f"[ShaderRemapper] No mapping from {src_type} to {target_type}"
                    )
                continue

            new_shader = self._create_shader(old_shader, target_type)
            if self.verbose:
                self.logger.info(
                    f"[ShaderRemapper] {old_shader}: {src_type} -> {target_type} ({new_shader})"
                )

            self._connect_file_nodes(old_shader, new_shader, mapping)
            if self.assign:
                self._reassign_to_geo(old_shader, new_shader)

            result[old_shader] = new_shader
        return result

    def _create_shader(self, old_shader: str, target_type: str) -> str:
        """Create a new shader node of the target_type with the original name + suffix."""
        new_name = f"{old_shader}_{self.name_suffix}".replace("__", "_")
        if cmds.objExists(new_name):
            try:
                cmds.delete(new_name)
            except Exception:
                cmds.warning(f"Could not delete existing shader node: {new_name}")

        new_shader = cmds.shadingNode(target_type, asShader=True, name=new_name)
        return new_shader

    def _connect_file_nodes(
        self,
        old_shader: str,
        new_shader: str,
        mapping: tuple,
    ) -> None:
        for src_attr, src_plug, dst_attr in mapping:
            if dst_attr == "normalCamera":
                if cmds.attributeQuery(src_attr, node=old_shader, exists=True):
                    for file_node in cmds.listConnections(
                        f"{old_shader}.{src_attr}", source=True, destination=False, type="file"
                    ) or []:
                        if not cmds.attributeQuery("isColorManaged", node=file_node, exists=True):
                            cmds.addAttr(file_node, longName="isColorManaged", attributeType="bool")
                        if not cmds.attributeQuery("ignoreColorSpaceFileRules", node=file_node, exists=True):
                            cmds.addAttr(file_node, longName="ignoreColorSpaceFileRules", attributeType="bool")

                        cmds.setAttr(f"{file_node}.isColorManaged", True)
                        cmds.setAttr(f"{file_node}.ignoreColorSpaceFileRules", True)
                        cmds.setAttr(f"{file_node}.colorSpace", "Raw", type="string")

                        bump = cmds.shadingNode(
                            "bump2d", asUtility=True, name=f"{file_node}_bump2d"
                        )
                        cmds.setAttr(f"{bump}.bumpInterp", 1)  # Tangent Space Normals

                        cmds.connectAttr(
                            f"{file_node}.outColor", f"{bump}.normalCamera", force=True
                        )
                        cmds.connectAttr(
                            f"{bump}.outNormal", f"{new_shader}.normalCamera", force=True
                        )

                        if self.verbose:
                            self.logger.info(
                                f"  {file_node}: added CMS attrs, set Raw, wired via bump2d"
                            )
                continue

            # Regular attribute mappings
            if (
                cmds.attributeQuery(src_attr, node=old_shader, exists=True)
                and cmds.attributeQuery(dst_attr, node=new_shader, exists=True)
            ):
                for tex in cmds.listConnections(
                    f"{old_shader}.{src_attr}", source=True, destination=False, type="file"
                ) or []:
                    out_attr = f"{tex}.{src_plug}"
                    if not cmds.attributeQuery(src_plug, node=tex, exists=True):
                        self.logger.warning(f"  Missing plug '{src_plug}' on {tex}")
                        continue
                    try:
                        cmds.connectAttr(
                            out_attr, f"{new_shader}.{dst_attr}", force=True
                        )
                        if self.verbose:
                            self.logger.info(
                                f"  Connected {out_attr} → {new_shader}.{dst_attr}"
                            )
                    except Exception as e:
                        self.logger.warning(
                            f"  Failed to connect {out_attr} → {new_shader}.{dst_attr}: {e}"
                        )

    def _reassign_to_geo(self, old_shader: str, new_shader: str) -> None:
        """Reassign the new shader to all geometry previously assigned to the old shader."""
        engines = set(cmds.listConnections(old_shader, type="shadingEngine") or [])
        if not engines:
            if self.verbose:
                self.logger.info(
                    f"[ShaderRemapper] No shadingEngine assigned to {old_shader}"
                )
            return

        for se in engines:
            geo = cmds.sets(se, query=True) or []
            if not geo:
                continue
            try:
                cmds.connectAttr(f"{new_shader}.outColor", f"{se}.surfaceShader", force=True)
                if self.verbose:
                    self.logger.info(
                        f"  Assigned {new_shader} to {len(geo)} geo via {se}"
                    )
            except Exception as e:
                self.logger.warning(
                    f"  Failed assigning {new_shader} to shadingEngine {se}: {e}"
                )


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    # Get StingrayPBS materials whose names start with 'mat_wing'
    mats = MatUtils.get_scene_mats(inc="mat_wing*", node_type="StingrayPBS", sort=True)
    print(f"Found {len(mats)} materials to remap.")
    for mat in mats:
        print(f" - {mat}")

    # Remap to blinn
    remapper = ShaderRemapper(
        attr_map=ShaderAttributeMap,
        name_suffix="_blinn",
        assign=True,
        verbose=True,
    )
    remapper.remap_shaders(mats, "blinn")


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
