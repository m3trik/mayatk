# !/usr/bin/python
# coding=utf-8
"""Read named sections of live-scene state for transport.

Retired 2026-10-20 (removed in mayatk 0.29.0): the FBX states every material
in its ``materials`` record (:class:`~mayatk.env_utils.material_records.MaterialRecords`,
the one reader of the scene's materials), and a GLB's handoff comes from the
FBX's own record -- so no export reads a section. Its readers still answer,
through MaterialRecords' helpers, until the removal.

It was the Maya *reader column* of the scene-data grid: each section of
scene state that FBX translation drops, read once and handed to the carrier
the caller filled (the WebXR preview's in-process envelope, the Scene
Exporter's GLB conversion). The GLB build now applies the FBX's own
``materials`` record (``ptk.MeshConvert.apply_glb_materials``) instead, so no
section is read.

Boundary with :class:`~mayatk.node_utils.data_nodes.DataNodes`: a section
belongs here only when it *repairs FBX translation loss*, derived read-only
from the live scene. Tool-authored semantic metadata (shots, audio, lightmap
manifests, ...) ships **inside** the FBX via ``data_export`` and must never
be duplicated into a sidecar section -- see the boundary section in
``docs/data_nodes.md``.

Mirror of ``blendertk.env_utils.scene_state.SceneState`` (name + behavior;
the readers differ by host idiom).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pythontk as ptk

try:
    import maya.cmds as cmds
except ModuleNotFoundError:
    pass

# Eager: SceneState names the shader map's emission-weight table, and
# MaterialRecords' shader sets, at class definition. Both modules are pure
# tables until a reader runs (no maya import).
from mayatk.env_utils.material_records import MaterialRecords
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap


class SceneState:
    """Section-registry reader of scene state the FBX cannot express --
    retired with the sections it read (see the module's docstring)."""

    #: Section -> the classmethod that reads it. The extension point: a new
    #: section is one row here plus its reader, with the same
    #: ``(materials, textures)`` signature.
    _READERS: Dict[str, str] = {
        "base_color": "_read_base_color",
        "emissive": "_read_emissive",
        "metallic_roughness": "_read_metallic_roughness",
        "alpha_mode": "_read_alpha_mode",
        "unlit": "_read_unlit",
    }
    READERS = ptk.Deprecation.class_attribute(
        _READERS,
        "mtk.MaterialRecords -- the materials record states every material",
        remove_in="0.29.0",
        since="2026-10-24",
    )

    # Maya's own legacy shading models — the only ones its FBX exporter maps.
    # Their colour reaches the GLB already scaled by Maya's ``diffuse`` weight,
    # so the sidecar leaves them alone rather than re-asserting a raw value that
    # would preview brighter than the FBX intends.
    FBX_NATIVE_SHADERS = frozenset({"lambert", "blinn", "phong", "phongE"})

    # MaterialRecords' own: the reader of the scene's materials.
    UNLIT_SHADERS = MaterialRecords.UNLIT_SHADERS
    SURFACE_OPACITY_SHADERS = MaterialRecords.SURFACE_OPACITY_SHADERS

    # Shaders that gate emission behind a SEPARATE scalar --
    # ``{node_type: (attribute, mode)}``. The shader map's table
    # (:attr:`ShaderAttributeMap.EMISSION_WEIGHT_ATTRS`, where its rationale
    # lives): a material's constant emission needs it below this layer too.
    EMISSION_WEIGHT_ATTRS = ShaderAttributeMap.EMISSION_WEIGHT_ATTRS

    @staticmethod
    @ptk.Deprecation.symbol(
        "mtk.FbxUtils.export_context().source", remove_in="0.29.0", since="2026-10-24"
    )
    def source() -> Dict[str, str]:
        """This host's identity for the envelope's ``source`` key."""
        return {"application": "maya", "version": cmds.about(version=True)}

    @classmethod
    @ptk.Deprecation.symbol(
        "mtk.MaterialRecords.entries -- the materials record states every material",
        remove_in="0.29.0",
        since="2026-10-24",
    )
    def read(
        cls,
        objects: List[str],
        include_textures: bool = True,
        sections: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Scene state the FBX cannot express, one key per requested section.

        The material list and the texture manifest are resolved **once** and
        shared by every reader -- ``MatManifest.build`` walks every assigned
        shader graph, and paying for that per section is pure waste on
        operations that are meant to feel immediate.

        Parameters:
            objects: Transforms whose subtrees define the material set (the
                closed export set, typically).
            include_textures: Mirrors an export's ``EMBED_TEXTURES``: off
                promises a fast, flat-material result, and the sidecar must
                not smuggle maps back in as data URIs after the FBX skipped
                them -- the readers then carry constants only. Skipping the
                manifest walk is also the cheap path the toggle advertises.
            sections: Subset of :attr:`READERS` to read; ``None`` reads all.

        Returns:
            ``{section: data}`` -- a section that finds nothing is omitted.
        """
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.mat_manifest import MatManifest

        materials = MatUtils.get_mats(cls._expand(objects)) or []
        textures = (
            (MatManifest.build(objects).get("materials", {}) or {})
            if include_textures
            else {}
        )

        result: Dict[str, Any] = {}
        for section, reader in cls._READERS.items():
            if sections is not None and section not in sections:
                continue
            data = getattr(cls, reader)(materials, textures)
            if data:
                result[section] = data
        return result

    @staticmethod
    def _expand(objects: List[str]) -> List[str]:
        """:meth:`MaterialRecords._expand`."""
        return MaterialRecords._expand(objects)

    @classmethod
    def _read_alpha_mode(
        cls, materials: List[str], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"mode": "MASK"|"BLEND", "cutoff": float}}`` for *materials*.

        Which StingrayPBS opacity graph a material wears IS its glTF
        ``alphaMode``, and the FBX cannot say so: FBX2glTF decides from the
        base colour's alpha channel alone -- an RGBA base colour becomes
        ``BLEND``, never ``MASK`` -- so a masked material (alpha cutout, the
        opaque queue, the one single-material layout a solid body survives)
        reached WebXR as alpha blend, back faces sorting through the body.
        ``Standard_Masked.sfx`` carries ``mask_threshold`` as the cutoff;
        ``Standard_Transparent.sfx`` is ``BLEND``; the opaque graph is left to
        the converter, whose ``OPAQUE`` is already right. Maya's own legacy
        models (:attr:`FBX_NATIVE_SHADERS`) are ``BLEND`` wherever their
        ``transparency`` is driven or above zero: the FBX carries it, and
        FBX2glTF writes it into ``baseColorFactor``'s alpha -- but judges the
        mode from a texture's alpha alone, so a Phong lens at 89% transparency
        reached the GLB ``OPAQUE``, its alpha of 0.105 ignored by every viewer,
        and rendered a solid grey disc (a production magnifier, 2026-10-03).
        Other shader types are not read: their opacity reaches the GLB through
        their own alpha and the converter's judgement stands.
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        result: Dict[str, Dict[str, Any]] = {}
        for mat in materials:
            if not cmds.objExists(mat):
                continue
            node_type = cmds.nodeType(mat)
            if node_type in cls.SURFACE_OPACITY_SHADERS:
                # Blend, never mask: the channel is continuous.
                if cls._surface_opacity_is_driven(mat, node_type):
                    result[mat] = {"mode": "BLEND"}
                continue
            if node_type in cls.FBX_NATIVE_SHADERS:
                if cls._channel_is_driven(mat, "transparency", clear=0.0):
                    result[mat] = {"mode": "BLEND"}
                continue
            if node_type in cls.UNLIT_SHADERS:
                if cls._channel_is_driven(mat, "outTransparency", clear=0.0):
                    result[mat] = {"mode": "BLEND"}
                continue
            if node_type != "StingrayPBS":
                continue
            mode = MatUtils.get_stingray_opacity_mode(mat)
            if mode == "masked":
                entry: Dict[str, Any] = {"mode": "MASK"}
                try:
                    entry["cutoff"] = float(cmds.getAttr(f"{mat}.mask_threshold"))
                except (RuntimeError, ValueError, TypeError):
                    pass  # glTF's default cutoff (0.5) then applies
                result[mat] = entry
            elif mode == "transparent":
                result[mat] = {"mode": "BLEND"}
        return result

    @classmethod
    def _read_unlit(
        cls, materials: List[str], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {}}`` for the *materials* the scene shades unlit
        (:meth:`MaterialRecords._unlit_materials`)."""
        return {mat: {} for mat in MaterialRecords._unlit_materials(materials)}

    @classmethod
    def _surface_opacity_is_driven(cls, mat: str, node_type: str) -> bool:
        """:meth:`MaterialRecords._surface_opacity_is_driven`."""
        return MaterialRecords._surface_opacity_is_driven(mat, node_type)

    @staticmethod
    def _channel_is_driven(mat: str, channel: str, clear: float) -> bool:
        """:meth:`MaterialRecords._channel_is_driven`."""
        return MaterialRecords._channel_is_driven(mat, channel, clear)

    @classmethod
    def _read_base_color(
        cls, materials: List[str], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"color": (r, g, b), "texture": path}}`` for *materials*.

        Measured: ``aiStandardSurface`` and ``standardSurface`` reach the GLB
        with ``baseColorFactor`` at a flat **[1,1,1,1]** -- Maya's FBX exporter
        does not map them -- so every modern shader previews as white plastic,
        which also leaves emissive nothing to read against. The legacy models
        (``lambert`` / ``blinn`` / ``phong``) *do* carry colour, so they are
        left alone rather than re-asserted at a subtly different value: the
        exporter folds Maya's ``diffuse`` weight in, and overwriting with the
        raw colour would make the preview brighter than the FBX intends.

        For the same reason a modern shader's own base WEIGHT is folded in
        (:meth:`ShaderAttributeMap.base_weight`: ``base`` x ``baseColor``, the
        albedo it renders), and rides as the factor over a texture -- as
        unitytk's importer folds it, so the two deliverables agree.

        Its opacity's scalar part rides as ``alpha`` (:meth:`_opacity_factor`),
        the factor glTF multiplies into the texture's alpha.
        """
        result: Dict[str, Dict[str, Any]] = {}

        for mat in materials:
            if not cmds.objExists(mat):
                continue
            node_type = cmds.nodeType(mat)
            if node_type in cls.FBX_NATIVE_SHADERS:
                continue

            entry: Dict[str, Any] = {}
            weight = ShaderAttributeMap.base_weight(mat, node_type)
            texture = (textures.get(mat) or {}).get("baseColor")
            if texture:
                entry["texture"] = texture
                if abs(weight - 1.0) > 1e-6:
                    entry["color"] = [weight] * 3

            # The VALUE's attribute, never the slot: StingrayPBS keeps its
            # colour in ``base_color``, and its ``TEX_color_map`` sampler reads
            # (0, 0, 0) -- a grey metal lock reached WebXR black (2026-10-15).
            attr = ShaderAttributeMap.constant_attr(node_type, "baseColor")
            if attr and not texture:
                try:
                    entry["color"] = [
                        c * weight for c in list(cmds.getAttr(f"{mat}.{attr}")[0])[:3]
                    ]
                except (RuntimeError, ValueError, TypeError, IndexError):
                    pass

            alpha = cls._opacity_factor(mat, node_type)
            if alpha is not None:
                entry["alpha"] = alpha

            if entry:
                result[mat] = entry
        return result

    @classmethod
    def _opacity_factor(cls, mat: str, node_type: str) -> Optional[float]:
        """:meth:`MaterialRecords._opacity_factor`."""
        return MaterialRecords._opacity_factor(mat, node_type)

    @classmethod
    def _read_emissive(
        cls, materials: List[str], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"color": (r, g, b), "texture": path}}`` for *materials*.

        Reuses the registry that already owns this knowledge rather than
        re-deriving it: :class:`ShaderAttributeMap` knows which attribute *is*
        emission for a given shader type -- ``emissionColor`` on
        aiStandardSurface / standardSurface / openPBR, ``TEX_emissive_map`` on
        StingrayPBS, ``incandescence`` on lambert and phong. A hand-written
        per-shader table here would be a fourth copy of that mapping.

        *materials* is enumerated by the caller rather than derived from
        *textures*: :class:`MatManifest` drops a material whose slot dict comes
        back empty, so it cannot see a colour-only emissive -- the common case.

        Materials with no emission at all are omitted, so an unlit scene
        contributes no section and its GLB goes untouched (the envelope still
        ships, with empty sections -- that is the "requested, nothing to
        carry" signal the panel summary reads).
        """
        result: Dict[str, Dict[str, Any]] = {}

        for mat in materials:
            if not cmds.objExists(mat):
                continue
            entry: Dict[str, Any] = {}

            texture = (textures.get(mat) or {}).get("emission")
            weight = cls.emission_weight(mat)

            # Weight 0: the shader emits nothing. With a map connected that is
            # still something to carry -- the FBX hands the map on, and the
            # converter glows it at 1.0 unless the factor says 0 (a production
            # room's ceiling panels, emissive_intensity 0, lit WebXR at full,
            # 2026-10-17). A colour-only one is left out: there is no map to
            # switch off, and an unlit scene keeps an empty section.
            if weight == 0.0:
                if texture:
                    result[mat] = {"texture": texture, "color": [0.0, 0.0, 0.0]}
                continue

            if texture:
                entry["texture"] = texture
                # The map supplies the colour, so the weight is all that is
                # left to carry -- and it must still travel, or a map authored
                # at intensity 5 previews at 1. Mirror of the Blender twin's
                # linked-socket branch.
                if weight != 1.0:
                    entry["color"] = [weight, weight, weight]
            else:
                # The value's attribute (StingrayPBS: ``emissive``), never its
                # ``TEX_emissive_map`` sampler, whose literal reads black.
                attr = ShaderAttributeMap.constant_attr(cmds.nodeType(mat), "emission")
                color = None
                if attr:
                    try:
                        color = list(cmds.getAttr(f"{mat}.{attr}")[0])[:3]
                    except (RuntimeError, ValueError, TypeError, IndexError):
                        color = None
                if color and any(c > 0.0 for c in color):
                    entry["color"] = [c * weight for c in color]

            if entry:
                result[mat] = entry
        return result

    @classmethod
    def _read_metallic_roughness(
        cls, materials: List[str], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"metallic": path, "roughness": path, "occlusion": path}}``.

        The most destructive form of the translation gap the other readers
        repair. Measured on a production room (StingrayPBS -> FBX2glTF): the
        converter packs a **solid-white** ORM when it cannot resolve the real
        maps -- and glTF reads metallic from the blue channel, so the whole
        material renders metallic=1. Diffuse response is zero on a pure metal,
        a baked lightmap contributes only to diffuse, and a lightmapped viewer
        turns its own lights off: three correct behaviours that compound into a
        black room, with nothing anywhere naming the lost roughness map.

        Texture paths only -- there is no colour fallback here because the
        scalar case (``metalness``/``specularRoughness`` as plain values) DOES
        survive FBX for the native shaders, and StingrayPBS without maps ships
        its factors through the Maya|* properties FBX2glTF already reads. Only
        the *maps* are lost in translation, so only the maps are carried.
        """
        result: Dict[str, Dict[str, Any]] = {}
        for mat in materials:
            if not cmds.objExists(mat):
                continue
            slots = textures.get(mat) or {}
            entry = {
                key: slots[slot]
                for key, slot in (
                    ("metallic", "metallic"),
                    ("roughness", "roughness"),
                    ("occlusion", "ambientOcclusion"),
                )
                if slots.get(slot)
            }
            # Occlusion alone is not a repair: the FBX carries AO fine, and a
            # metallic/roughness-free entry would replace a correct ORM with a
            # roughness-black one (mirror-smooth everything).
            if "metallic" in entry or "roughness" in entry:
                result[mat] = entry
        return result

    @classmethod
    def emission_weight(cls, mat: str) -> float:
        """The shader's separate emission scalar, or 1.0 when it has none.

        :meth:`ShaderAttributeMap.emission_weight`. On aiStandardSurface the
        weight defaults to **0**, so reading ``emissionColor`` alone reports a
        bright emissive on a material that renders black. glTF has a single
        emissive term, so a ``multiply`` weight is folded into the colour;
        above 1 the writer preserves the magnitude via
        ``KHR_materials_emissive_strength`` instead of clipping.
        """
        return ShaderAttributeMap.emission_weight(mat)
