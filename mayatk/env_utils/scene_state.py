# !/usr/bin/python
# coding=utf-8
"""Read named sections of live-scene state for transport.

The Maya *reader column* of the scene-data grid: every section of scene state
that FBX translation drops -- base colour and emissive today; lights,
environment tomorrow -- is read here, once, and handed to whichever carrier
the caller is filling (the WebXR preview's in-process envelope, the Scene
Exporter's GLB conversion, embedded in the GLB's ``extras``). The matching
*applier column* is :attr:`pythontk.MeshConvert.SIDECAR_APPLIERS`, and the
envelope wire format is :meth:`pythontk.MeshConvert.build_scene_sidecar` --
adding a new kind of extended setup is one reader here (mirrored in
blendertk) plus one applier row there. Nothing else in the codebase changes.

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

try:
    import maya.cmds as cmds
except ModuleNotFoundError:
    pass

# Eager: SceneState names the shader map's emission-weight table at class
# definition. The module is a pure table until a reader runs (no maya import).
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap


class SceneState:
    """Section-registry reader of scene state the FBX cannot express.

    >>> sections = SceneState.read(cmds.ls(selection=True))
    >>> envelope = ptk.MeshConvert.build_scene_sidecar(
    ...     sections, source=SceneState.source()
    ... )
    """

    #: Section -> the classmethod that reads it. The extension point: a new
    #: section is one row here plus its reader, with the same
    #: ``(materials, textures)`` signature.
    READERS: Dict[str, str] = {
        "base_color": "_read_base_color",
        "emissive": "_read_emissive",
        "metallic_roughness": "_read_metallic_roughness",
        "alpha_mode": "_read_alpha_mode",
        "unlit": "_read_unlit",
    }

    # Maya's own legacy shading models — the only ones its FBX exporter maps.
    # Their colour reaches the GLB already scaled by Maya's ``diffuse`` weight,
    # so the sidecar leaves them alone rather than re-asserting a raw value that
    # would preview brighter than the FBX intends.
    FBX_NATIVE_SHADERS = frozenset({"lambert", "blinn", "phong", "phongE"})

    # Maya's unlit shading model. The FBX makes it lit PBR like any other
    # shader; the ``unlit`` section says otherwise (:meth:`_read_unlit`), and
    # its ``outTransparency`` is a transparency channel like the legacy
    # models' (:meth:`_read_alpha_mode`).
    UNLIT_SHADERS = frozenset({"surfaceShader"})

    # Shaders whose transparency is a plain ``opacity`` colour rather than a
    # graph: a driven or non-opaque channel IS alpha blend, and the FBX
    # cannot say so (measured 2026-09-02: a standardSurface whose opacity
    # came from a texture's alpha reached the GLB OPAQUE -- its silhouette
    # embedded by the base-colour section and rendered solid). openPBR's is
    # the scalar ``geometryOpacity`` (``ShaderAttributeMap`` names each one).
    SURFACE_OPACITY_SHADERS = frozenset(
        {"standardSurface", "aiStandardSurface", "openPBRSurface"}
    )

    # Shaders that gate emission behind a SEPARATE scalar --
    # ``{node_type: (attribute, mode)}``. The shader map's table
    # (:attr:`ShaderAttributeMap.EMISSION_WEIGHT_ATTRS`, where its rationale
    # lives): a material's constant emission needs it below this layer too.
    EMISSION_WEIGHT_ATTRS = ShaderAttributeMap.EMISSION_WEIGHT_ATTRS

    @staticmethod
    def source() -> Dict[str, str]:
        """This host's identity for the envelope's ``source`` key."""
        return {"application": "maya", "version": cmds.about(version=True)}

    @classmethod
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
        for section, reader in cls.READERS.items():
            if sections is not None and section not in sections:
                continue
            data = getattr(cls, reader)(materials, textures)
            if data:
                result[section] = data
        return result

    @staticmethod
    def _expand(objects: List[str]) -> List[str]:
        """*objects* plus their descendant transforms, de-duplicated.

        ``MatUtils.get_mats`` reads direct shapes only, so a selected hierarchy
        root would otherwise resolve to no materials at all — the same
        expansion ``MatManifest.build`` does internally.
        """
        expanded: List[str] = []
        for obj in objects:
            obj = str(obj)
            expanded.append(obj)
            try:
                expanded.extend(
                    cmds.listRelatives(
                        obj, allDescendents=True, type="transform", fullPath=True
                    )
                    or []
                )
            except RuntimeError:  # components / shapes pass through
                pass
        return list(dict.fromkeys(expanded))

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
        """``{material: {}}`` for the *materials* the scene shades unlit.

        Two kinds: Maya's own unlit model (:attr:`UNLIT_SHADERS`), and the
        material of every projected shadow plane (:class:`ShadowRig`),
        whatever shader it wears -- a shadow is darkness laid over what is
        under it, never a surface the environment lights. The FBX cannot say
        either: FBX2glTF makes each one lit PBR, and a shadow plane in WebXR
        took the environment's specular and read BRIGHTER than the floor it
        lay on (2026-10-15; its viewer script, which shades it unlit, runs
        only when the scene carries a shadow record). Applied as
        ``KHR_materials_unlit`` (``MeshConvert.set_glb_unlit``).
        """
        result: Dict[str, Dict[str, Any]] = {}
        for mat in materials:
            if cmds.objExists(mat) and cmds.nodeType(mat) in cls.UNLIT_SHADERS:
                result[mat] = {}
        try:
            from mayatk.rig_utils.shadow_rig._shadow_rig import ShadowRig

            wanted = set(materials)
            for shader in ShadowRig.plane_materials():
                if shader in wanted:
                    result[shader] = {}
        except Exception as error:  # noqa: BLE001 -- a section never fails a push
            import logging

            logging.getLogger(__name__).debug("Shadow planes unread: %s", error)
        return result

    @classmethod
    def _surface_opacity_is_driven(cls, mat: str, node_type: str) -> bool:
        """Is *mat*'s opacity connected or below 1.0 on any channel?"""
        slot = ShaderAttributeMap.get_attr(node_type, "opacity")
        return bool(slot) and cls._channel_is_driven(mat, slot[0], clear=1.0)

    @staticmethod
    def _channel_is_driven(mat: str, channel: str, clear: float) -> bool:
        """Is *mat*'s *channel* connected (on the compound or a child) or away
        from *clear* -- the value that means fully opaque -- on any component?
        Maya allows a compound and its children to be wired independently, so
        both are checked. A scalar channel (openPBR's ``geometryOpacity``) is
        its own single component; read as a colour it raised, and the reader
        took the failure for "opaque"."""
        plug = f"{mat}.{channel}"
        if not cmds.objExists(plug):
            return False
        for candidate in [plug] + [f"{plug}{c}" for c in "RGB"]:
            if cmds.objExists(candidate) and cmds.listConnections(
                candidate, source=True, destination=False
            ):
                return True
        try:
            value = cmds.getAttr(plug)
            components = list(value[0])[:3] if isinstance(value, list) else [value]
        except (RuntimeError, ValueError, TypeError, IndexError):
            return False
        return max(abs(float(c) - clear) for c in components) > 1e-4

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
        """The scalar part of a :attr:`SURFACE_OPACITY_SHADERS` opacity --
        what glTF states as ``baseColorFactor[3]`` -- or None at 1.0.

        Two shapes carry one: a bare constant (a tint), and a texture's alpha
        times a scalar through a ``multiplyDivide`` -- the shadow rig's
        silhouette x its plane's ``opacity`` fade, read at the current frame
        (a keyed fade also ships as a fade track, which drives the same
        factor). A texture alone states nothing: its alpha rides the base
        colour texture. The FBX carries neither: a shadow plane held at 0.836
        reached WebXR at alpha 1.0, solid black where VP2 drew it
        half-transparent (2026-10-15).
        """
        if node_type not in cls.SURFACE_OPACITY_SHADERS:
            return None
        slot = ShaderAttributeMap.get_attr(node_type, "opacity")
        plug = f"{mat}.{slot[0]}" if slot else None
        if not plug or not cmds.objExists(plug):
            return None
        sources = (
            cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
        )
        try:
            if not sources:
                value = ShaderAttributeMap.read_constant(mat, "opacity", node_type)
                if not value:
                    return None
                factor = sum(value) / len(value)
            else:
                node = sources[0].split(".")[0]
                if cmds.nodeType(node) != "multiplyDivide" or (
                    cmds.getAttr(f"{node}.operation") != 1
                ):
                    return None
                factor = None
                for texture_in, scalar_in in (
                    ("input1X", "input2X"),
                    ("input2X", "input1X"),
                ):
                    upstream = (
                        cmds.listConnections(
                            f"{node}.{texture_in}", source=True, destination=False
                        )
                        or []
                    )
                    if (
                        upstream
                        and "texture"
                        in (cmds.getClassification(cmds.nodeType(upstream[0])) or [""])[
                            0
                        ]
                    ):
                        factor = float(cmds.getAttr(f"{node}.{scalar_in}"))
                        break
                if factor is None:
                    return None
        except (RuntimeError, ValueError, TypeError, IndexError):
            return None
        factor = min(1.0, max(0.0, factor))
        return factor if factor < 1.0 - 1e-4 else None

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
