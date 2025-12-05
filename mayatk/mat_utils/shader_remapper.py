# !/usr/bin/python
# coding=utf-8
from typing import List, Dict, Any, Tuple, Type
import pymel.core as pm
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
        shaders: List["pm.nt.ShadingDependNode"],
        target_type: str,
    ) -> Dict["pm.nt.ShadingDependNode", "pm.nt.ShadingDependNode"]:
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
            src_type = type(old_shader).__name__
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

    def _create_shader(self, old_shader: Any, target_type: str) -> Any:
        """Create a new shader node of the target_type with the original name + suffix."""
        new_name = f"{old_shader}_{self.name_suffix}".replace("__", "_")
        if pm.objExists(new_name):
            try:
                pm.delete(new_name)
            except Exception:
                pm.warning(f"Could not delete existing shader node: {new_name}")

        new_shader = pm.shadingNode(target_type, asShader=True, name=new_name)
        return new_shader

    def _connect_file_nodes(
        self,
        old_shader: pm.nt.ShadingDependNode,
        new_shader: pm.nt.ShadingDependNode,
        mapping: tuple,
    ) -> None:
        for src_attr, src_plug, dst_attr in mapping:
            if dst_attr == "normalCamera":
                if old_shader.hasAttr(src_attr):
                    for file_node in old_shader.attr(src_attr).inputs(type="file"):
                        # Ensure color-managed attributes exist
                        if not file_node.hasAttr("isColorManaged"):
                            file_node.addAttr("isColorManaged", at="bool")
                        if not file_node.hasAttr("ignoreColorSpaceFileRules"):
                            file_node.addAttr("ignoreColorSpaceFileRules", at="bool")

                        file_node.isColorManaged.set(True)
                        file_node.ignoreColorSpaceFileRules.set(True)
                        file_node.colorSpace.set("Raw", type="string")

                        bump = pm.shadingNode(
                            "bump2d", asUtility=True, name=f"{file_node}_bump2d"
                        )
                        bump.bumpInterp.set(1)  # Tangent Space Normals

                        pm.connectAttr(
                            file_node.outColor, bump.normalCamera, force=True
                        )
                        pm.connectAttr(
                            bump.outNormal, new_shader.normalCamera, force=True
                        )

                        if self.verbose:
                            self.logger.info(
                                f"  {file_node}: added CMS attrs, set Raw, wired via bump2d"
                            )
                continue

            # Regular attribute mappings
            if old_shader.hasAttr(src_attr) and new_shader.hasAttr(dst_attr):
                for tex in old_shader.attr(src_attr).inputs(type="file"):
                    try:
                        out_attr = getattr(tex, src_plug)
                    except AttributeError:
                        self.logger.warning(f"  Missing plug '{src_plug}' on {tex}")
                        continue
                    try:
                        pm.connectAttr(
                            out_attr, getattr(new_shader, dst_attr), force=True
                        )
                        if self.verbose:
                            self.logger.info(
                                f"  Connected {out_attr.name()} → {new_shader}.{dst_attr}"
                            )
                    except Exception as e:
                        self.logger.warning(
                            f"  Failed to connect {out_attr.name()} → {new_shader}.{dst_attr}: {e}"
                        )

    def _reassign_to_geo(self, old_shader: Any, new_shader: Any) -> None:
        """Reassign the new shader to all geometry previously assigned to the old shader."""
        engines = set(pm.listConnections(old_shader, type="shadingEngine"))
        if not engines:
            if self.verbose:
                self.logger.info(
                    f"[ShaderRemapper] No shadingEngine assigned to {old_shader}"
                )
            return

        for se in engines:
            geo = pm.sets(se, query=True) or []
            if not geo:
                continue
            try:
                pm.connectAttr(new_shader.outColor, se.surfaceShader, force=True)
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
        print(f" - {mat.name()}")

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
