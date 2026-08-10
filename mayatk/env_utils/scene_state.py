# !/usr/bin/python
# coding=utf-8
"""Read named sections of live-scene state for transport.

The Maya *reader column* of the scene-data grid: every section of scene state
that FBX translation drops -- base colour and emissive today; lights,
environment tomorrow -- is read here, once, and handed to whichever carrier
the caller is filling (the WebXR preview's in-process envelope, the Scene
Exporter's GLB conversion, a ``.scene.json`` handoff file). The matching
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
except ModuleNotFoundError as error:
    print(__file__, error)


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
    }

    # Maya's own legacy shading models — the only ones its FBX exporter maps.
    # Their colour reaches the GLB already scaled by Maya's ``diffuse`` weight,
    # so the sidecar leaves them alone rather than re-asserting a raw value that
    # would preview brighter than the FBX intends.
    FBX_NATIVE_SHADERS = frozenset({"lambert", "blinn", "phong", "phongE"})

    # Shaders that gate emission behind a SEPARATE scalar, measured on Maya
    # 2025 + MtoA -- ``{node_type: (attribute, mode)}``.
    #
    # This is an explicit table rather than a list of candidate attribute names
    # tried against every shader, which is what it replaced. Of the three names
    # guessed there, ``emissionWeight`` and ``emissive_intensity`` matched
    # nothing at all, and guessing is actively unsafe: a graph-built shader
    # (StingrayPBS attributes are graph-dependent) can expose a same-named
    # attribute with different semantics and a 0 default, which would silently
    # drop a material that had been previewing correctly.
    #
    # ``multiply``: a 0-1 weight folded into the colour.
    # ``gate``: not a 0-1 scale (OpenPBR carries luminance in nits), so it
    # decides whether the material emits at all but must never scale it.
    EMISSION_WEIGHT_ATTRS = {
        "aiStandardSurface": ("emission", "multiply"),
        "standardSurface": ("emission", "multiply"),
        "openPBRSurface": ("emissionLuminance", "gate"),
    }

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

        materials = MatUtils.get_mats(cls._expand(objects), as_strings=True) or []
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
        """
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        result: Dict[str, Dict[str, Any]] = {}

        for mat in materials:
            if not cmds.objExists(mat):
                continue
            if cmds.nodeType(mat) in cls.FBX_NATIVE_SHADERS:
                continue

            entry: Dict[str, Any] = {}
            texture = (textures.get(mat) or {}).get("baseColor")
            if texture:
                entry["texture"] = texture

            mapping = ShaderAttributeMap.get_attr(cmds.nodeType(mat), "baseColor")
            if mapping and not texture:
                try:
                    entry["color"] = list(cmds.getAttr(f"{mat}.{mapping[0]}")[0])[:3]
                except (RuntimeError, ValueError, TypeError, IndexError):
                    pass

            if entry:
                result[mat] = entry
        return result

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
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        result: Dict[str, Dict[str, Any]] = {}

        for mat in materials:
            if not cmds.objExists(mat):
                continue
            entry: Dict[str, Any] = {}

            texture = (textures.get(mat) or {}).get("emission")
            weight = cls.emission_weight(mat)

            # Weight 0 means the shader emits nothing, so there is nothing to
            # carry -- and claiming otherwise is worse than silence. With a map
            # connected, a 0 factor multiplies it to black in glTF while the
            # panel reports a successful transfer; the colour-only branch below
            # already skipped this case, so the two disagreed. Omitting the
            # material makes the panel say "nothing to carry", which points at
            # the emission weight instead of at the preview.
            if weight == 0.0:
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
                mapping = ShaderAttributeMap.get_attr(cmds.nodeType(mat), "emission")
                color = None
                if mapping:
                    # StingrayPBS names a *map* slot here, which is a texture
                    # plug rather than a colour -- a failed read is normal.
                    try:
                        color = list(cmds.getAttr(f"{mat}.{mapping[0]}")[0])[:3]
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

        On aiStandardSurface the weight defaults to **0**, so reading
        ``emissionColor`` alone reports a bright emissive on a material that
        renders black. glTF has a single emissive term, so a ``multiply``
        weight is folded into the colour; above 1 the writer preserves the
        magnitude via ``KHR_materials_emissive_strength`` instead of clipping.

        A shader absent from :attr:`EMISSION_WEIGHT_ATTRS` is **ungated** --
        returning 1.0 rather than hunting for a plausibly-named attribute,
        because a wrong guess here silently removes a working emissive.
        """
        entry = cls.EMISSION_WEIGHT_ATTRS.get(cmds.nodeType(mat))
        if entry is None:
            return 1.0
        attr, mode = entry
        plug = f"{mat}.{attr}"
        if not cmds.objExists(plug):
            return 1.0
        try:
            value = float(cmds.getAttr(plug))
        except (RuntimeError, ValueError, TypeError):
            return 1.0
        if mode == "gate":
            return 1.0 if value > 0.0 else 0.0
        return value
