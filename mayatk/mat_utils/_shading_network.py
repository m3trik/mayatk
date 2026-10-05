# !/usr/bin/python
# coding=utf-8
"""Shading networks behind :class:`mayatk.MatUtils`.

Building and reading shading graphs: the preferred standard shader, new
materials, ``file`` + ``place2dTexture`` pairs, shading groups, driving a
compound slot per channel, finding the file node behind a material slot
(through utility nodes and packed-channel child plugs), reclaiming a rebuilt
network's name, bump-to-normal conversion and normal-map checks, and graphing
materials in the Hypershade.
Reached through :class:`mayatk.MatUtils`; nothing here is called directly.
"""

import os

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins


class _ShadingNetworkInternal:
    """Private helpers and ``MatUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _is_clash_variant(candidate: str, want: str) -> bool:
        """True when *candidate* is *want* modulo Maya's clash-rename digit suffix."""
        if candidate == want:
            return True
        return candidate.startswith(want) and candidate[len(want) :].isdigit()

    @staticmethod
    def get_texture_file_node(material, attr_name, _depth=0):
        """Locate the file texture node feeding a material attribute."""
        if _depth > 10 or not material or not attr_name:
            return None

        full_attr = f"{material}.{attr_name}"
        if not cmds.objExists(full_attr):
            return None

        files = cmds.listConnections(
            full_attr, source=True, destination=False, type="file"
        )
        if files:
            return files[0]

        sources = cmds.listConnections(full_attr, source=True, destination=False)
        if sources:
            node = sources[0]
            ntype = cmds.nodeType(node)

            _FOLLOW = {
                "bump2d": ["bumpValue"],
                "aiNormalMap": ["input"],
                "projection": ["image"],
                "stencil": ["image"],
                "gammaCorrect": ["value"],
                "luminance": ["value"],
                "reverse": ["input"],
                "clamp": ["input"],
                "colorCorrect": ["color", "inColor", "input"],
                "aiColorCorrect": ["input"],
                "remapHsv": ["color", "inColor"],
                "remapColor": ["color", "inColor"],
                "remapValue": ["inputValue", "color"],
            }

            candidates = _FOLLOW.get(ntype, ["input", "color", "inColor"])
            for inp in candidates:
                if cmds.objExists(f"{node}.{inp}"):
                    result = _ShadingNetworkInternal.get_texture_file_node(
                        node, inp, _depth + 1
                    )
                    if result:
                        return result

        # LAST resort: a PACKED map is wired per channel, into the compound's
        # CHILD plugs -- `outColorG -> TEX_roughness_mapX/Y/Z` -- and
        # `listConnections` on the parent reports none of them. Measured on a
        # production room whose one ORM feeds three StingrayPBS slots: only the
        # AO slot took the whole `outColor`, so only AO resolved,
        # `_read_metallic_roughness` refused an occlusion-only entry
        # (correctly), and the GLB shipped FBX2glTF's scalar fallback --
        # roughness flat 0.329 and metallic flat 0.0 against a source carrying
        # 223 and 256 distinct values.
        #
        # Deliberately AFTER the parent's own follow-chain rather than before
        # it. Maya does NOT forbid a compound and its children being connected
        # at once -- probe-measured on 2025: connecting a child after the parent
        # is ALLOWED and both survive -- so ordering is a real decision, not a
        # moot one. The parent binding is the primary one and keeps precedence;
        # descending only once it has yielded nothing makes this purely additive
        # to every path that already worked. Recursing per child rather than
        # querying inline reuses the whole resolution path, so a packed map
        # behind a colorCorrect resolves the same way a plain one does.
        try:
            children = (
                cmds.attributeQuery(attr_name, node=material, listChildren=True) or []
            )
        except (RuntimeError, TypeError):
            children = []
        for child in children:
            result = _ShadingNetworkInternal.get_texture_file_node(
                material, child, _depth + 1
            )
            if result:
                return result

        return None

    @staticmethod
    def _create_standard_shader(name=None, color=None, return_type="type"):
        """Create or get the preferred shader type, with optional node creation."""
        try:
            if Plugins.is_loaded("mtoa") or cmds.nodeType(
                "standardSurface", isTypeName=True
            ):
                shader_type = "standardSurface"
            else:
                try:
                    test = cmds.shadingNode("standardSurface", asShader=True)
                    cmds.delete(test)
                    shader_type = "standardSurface"
                except Exception:
                    shader_type = "lambert"
        except Exception:
            shader_type = "lambert"

        if return_type == "type":
            return shader_type

        shader_name = name or f"material_{shader_type}"
        shader = cmds.shadingNode(shader_type, asShader=True, name=shader_name)

        if color:
            color_attr = "baseColor" if shader_type == "standardSurface" else "color"
            cmds.setAttr(
                f"{shader}.{color_attr}", color[0], color[1], color[2], type="double3"
            )

        if return_type == "shader":
            return shader

        sg_name = f"{shader_name}_SG" if name else f"{shader}_SG"
        sg = cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name=sg_name,
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)

        if return_type == "shading_group":
            return sg
        elif return_type == "both":
            return shader, sg
        else:
            raise ValueError(
                f"Invalid return_type: {return_type}. Must be 'type', 'shader', 'shading_group', or 'both'."
            )

    @staticmethod
    def _connect_to_channels(source_plug, node, attr):
        """Body of :meth:`MatUtils.connect_to_channels`."""
        if not cmds.attributeQuery(attr, node=node, exists=True):
            return False

        children = [
            f"{attr}{s}"
            for s in ("R", "G", "B")
            if cmds.attributeQuery(f"{attr}{s}", node=node, exists=True)
        ] or [
            f"{attr}{s}"
            for s in ("X", "Y", "Z")
            if cmds.attributeQuery(f"{attr}{s}", node=node, exists=True)
        ]

        if len(children) >= 3:
            # Break the parent so it can't stay bound to a previous texture
            # while the children are driven by this one.
            parent_sources = (
                cmds.listConnections(
                    f"{node}.{attr}", plugs=True, source=True, destination=False
                )
                or []
            )
            for src in parent_sources:
                cmds.disconnectAttr(src, f"{node}.{attr}")
            # All-or-nothing: a child that refuses (locked, wrong type) must not
            # leave a HALF-driven compound behind, nor raise out of a method whose
            # contract is a bool -- both callers treat False as "report and move
            # on". Undo the children made, then restore the parent input broken
            # above, so a failure is a genuine no-op.
            connected = []
            try:
                for child in children[:3]:
                    cmds.connectAttr(source_plug, f"{node}.{child}", force=True)
                    connected.append(child)
            except RuntimeError:
                for child in connected:
                    try:
                        cmds.disconnectAttr(source_plug, f"{node}.{child}")
                    except RuntimeError:
                        pass
                for src in parent_sources:
                    try:
                        cmds.connectAttr(src, f"{node}.{attr}", force=True)
                    except RuntimeError:
                        pass
                return False
            return True

        try:  # scalar slot
            cmds.connectAttr(source_plug, f"{node}.{attr}", force=True)
            return True
        except RuntimeError:
            return False

    @classmethod
    def _create_mat(cls, mat_type, prefix, name):
        """Body of :meth:`MatUtils.create_mat`."""
        import random

        if mat_type == "random":
            preferred_type = cls._create_standard_shader(return_type="type")
            rgb = [random.randint(0, 255) for _ in range(3)]
            name = "{}{}_{}_{}_{}".format(
                prefix, name, str(rgb[0]), str(rgb[1]), str(rgb[2])
            )
            mat = cmds.shadingNode(preferred_type, asShader=True, name=name)
            convertedRGB = [round(float(v) / 255, 3) for v in rgb]
            color_attr = (
                f"{mat}.baseColor"
                if preferred_type == "standardSurface"
                else f"{mat}.color"
            )
            cmds.setAttr(
                color_attr,
                convertedRGB[0],
                convertedRGB[1],
                convertedRGB[2],
                type="double3",
            )
        else:
            name = prefix + name if name else mat_type
            mat = cmds.shadingNode(mat_type, asShader=True, name=name)

        return mat

    @classmethod
    def _claim_material_name(cls, shading_group, desired):
        """Body of :meth:`MatUtils.claim_material_name`."""
        if not desired:
            return shading_group
        shaders = (
            cmds.listConnections(
                f"{shading_group}.surfaceShader", source=True, destination=False
            )
            or []
        )
        if not shaders:
            return shading_group
        shader = shaders[0]
        old = CoreUtils.short_name(shader)
        if old == desired or cmds.objExists(desired):
            return shading_group
        try:
            cmds.rename(shader, desired)
        except RuntimeError:
            return shading_group
        # Carry the shading group along so the pair stays legible ("M_xSG" for
        # "M_x"). Two conventions reach here: named after the CREATED shader
        # ("M_x1SG") or after the REQUESTED name plus Maya's own clash digits
        # ("M_xSG1"). Both resolve to "M_xSG"; any other spelling is left alone
        # rather than renamed on a guess.
        short_sg = CoreUtils.short_name(shading_group)
        wanted = ""
        if short_sg.startswith(old):
            wanted = desired + short_sg[len(old) :]
        elif cls._is_clash_variant(short_sg, f"{desired}SG"):
            wanted = f"{desired}SG"
        if wanted and wanted != short_sg and not cmds.objExists(wanted):
            try:
                return cmds.rename(shading_group, wanted)
            except RuntimeError:
                pass
        return shading_group

    @staticmethod
    def _create_file_node(image_path, name, color_space):
        """Body of :meth:`MatUtils.create_file_node`."""
        from pathlib import Path

        if name is None:
            name = Path(image_path).stem

        file_node = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(f"{file_node}.fileTextureName", image_path, type="string")

        if color_space:
            cmds.setAttr(f"{file_node}.colorSpace", color_space, type="string")

        place2d = cmds.shadingNode(
            "place2dTexture", asUtility=True, name=f"{name}_place2d"
        )

        connections = [
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
        for src, dst in connections:
            cmds.connectAttr(f"{place2d}.{src}", f"{file_node}.{dst}", force=True)

        return file_node, place2d

    @staticmethod
    def _create_shading_group(shader, name, assign_to):
        """Body of :meth:`MatUtils.create_shading_group`."""
        shader_name = str(shader)
        sg_name = name or f"{shader_name}_SG"

        sg = cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name=sg_name,
        )
        cmds.connectAttr(f"{shader_name}.outColor", f"{sg}.surfaceShader", force=True)

        if assign_to is not None:
            items = (
                assign_to if isinstance(assign_to, (list, tuple, set)) else [assign_to]
            )
            items = [str(i) for i in items]
            cmds.sets(items, edit=True, forceElement=sg)

        return sg

    @classmethod
    def _convert_bump_to_normal(
        cls,
        bump_file_node,
        output_path,
        intensity,
        format_type,
        create_file_node,
        node_name,
    ):
        """Body of :meth:`MatUtils.convert_bump_to_normal`."""
        bump_node = str(bump_file_node)
        if not cmds.objExists(bump_node):
            raise ValueError(f"Bump file node {bump_file_node} does not exist")
        if cmds.nodeType(bump_node) != "file":
            raise ValueError(f"Node {bump_file_node} is not a file node")
        if format_type not in ("opengl", "directx"):
            raise ValueError("format_type must be 'opengl' or 'directx'")

        source = cmds.getAttr(f"{bump_node}.fileTextureName") or ""
        if not source or not os.path.exists(source):
            raise ValueError(
                f"Bump file node {bump_node} has no existing texture file "
                f"(fileTextureName={source!r})"
            )

        try:
            written = ptk.MapFactory.convert_bump_to_normal(
                source,
                output_path=output_path,
                intensity=intensity,
                output_format=format_type,
                save=True,
            )
        except Exception as e:
            cmds.warning(f"Bump-to-normal conversion failed for {source}: {e}")
            return None

        if not create_file_node:
            return written

        base_name = node_name or f"{CoreUtils.short_name(bump_node)}_normal"
        normal_file_node, _place2d = cls.create_file_node(
            written, name=base_name, color_space="Raw"
        )
        # Normal data is non-color; never treat its alpha as luminance.
        cmds.setAttr(f"{normal_file_node}.alphaIsLuminance", False)
        return normal_file_node

    @staticmethod
    def _validate_normal_map_setup(normal_file_node, material):
        """Body of :meth:`MatUtils.validate_normal_map_setup`."""
        normal_node = str(normal_file_node)
        if not cmds.objExists(normal_node):
            return {
                "valid": False,
                "error": f"Normal file node {normal_file_node} does not exist",
            }
        if cmds.nodeType(normal_node) != "file":
            return {
                "valid": False,
                "error": f"Node {normal_file_node} is not a file node",
            }

        results = {
            "valid": True,
            "warnings": [],
            "recommendations": [],
            "color_space": None,
            "connected_to_normal": False,
            "file_exists": False,
        }

        color_space = cmds.getAttr(f"{normal_node}.colorSpace") or ""
        results["color_space"] = color_space
        if color_space.lower() not in ["raw", "linear", "utility - raw"]:
            results["warnings"].append(
                f"Color space is '{color_space}'. Normal maps should use 'Raw' or 'Linear' "
                "to avoid gamma correction that corrupts normal data."
            )
            results["recommendations"].append("Set colorSpace to 'Raw'")

        file_path = cmds.getAttr(f"{normal_node}.fileTextureName") or ""
        if file_path and os.path.exists(file_path):
            results["file_exists"] = True
        elif file_path:
            results["warnings"].append(f"Normal map file does not exist: {file_path}")

        if material:
            material = str(material)
            if not cmds.objExists(material):
                results["warnings"].append(f"Material {material} does not exist")
            else:
                connections = (
                    cmds.listConnections(
                        f"{normal_node}.outColor",
                        plugs=True,
                        source=False,
                        destination=True,
                    )
                    or []
                )
                normal_connections = [
                    c
                    for c in connections
                    if "normal" in c.lower() or "bump" in c.lower()
                ]

                if normal_connections:
                    results["connected_to_normal"] = True
                else:
                    results["warnings"].append(
                        "Normal map not connected to material normal/bump input"
                    )
                    results["recommendations"].append(
                        "Connect to material normalCamera or bump input"
                    )

        return results

    @classmethod
    def _graph_materials(cls, materials, mode):
        """Body of :meth:`MatUtils.graph_materials`."""
        if not materials:
            return

        materials_list = cls._to_strs(materials)
        cmds.select(materials_list)

        mel.eval("HypershadeWindow")

        cmds.evalDeferred(
            f'maya.mel.eval(\'hyperShadePanelGraphCommand "hyperShadePanel1" "{mode}"\')'
        )
