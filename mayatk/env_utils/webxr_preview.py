# !/usr/bin/python
# coding=utf-8
"""Push the Maya selection to a live browser / WebXR preview.

The lightest of the hand-off bridges: there is no target application to
discover or launch, because the target is a browser tab the user already has
open. :class:`pythontk.PreviewDeliverer` converts the exported FBX to GLB and
publishes it to a loopback :class:`pythontk.PreviewServer`; a page already open
-- including one open inside a PC-tethered headset -- picks the new version up
on its next poll.

Nothing here is Maya-specific except the binding itself, which is the point:
:class:`pythontk.PreviewBridge` owns the export defaults and the public
``push`` / ``url`` / ``stop`` surface, and :class:`MayaExportMixin` supplies
the selection read and FBX export every Maya-originating bridge shares. Writing
either half here would duplicate it against blendertk's twin, which cannot
import this package. Counterpart of blendertk's ``WebXrPreview``.

Example:
    >>> preview = mtk.WebXrPreview()
    >>> preview.push()              # opens a tab on the first call
    >>> preview.push()              # the open tab swaps to the new version
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

try:
    import maya.cmds as cmds
except ModuleNotFoundError as error:
    print(__file__, error)

import pythontk as ptk

from mayatk.env_utils.handoff_export import MayaExportMixin


class WebXrPreview(MayaExportMixin, ptk.PreviewBridge):
    """Live browser / WebXR preview of the Maya selection.

    One :class:`pythontk.PreviewDeliverer` is shared by every instance, so the
    server -- and therefore the port and the tab pointed at it -- survives
    across pushes and across panel reopens for the life of the Maya session.
    """

    payload_prefix = "maya_webxr_preview"
    deliverer = ptk.PreviewDeliverer(title="Maya")

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

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the FBX, then attach the scene sidecar the FBX can't carry.

        Same shape as the Marmoset bridge's producer: the skeleton's FBX
        payload plus a sidecar riding on ``Payload.extras``, written to a real
        file alongside the payload as well, because the point of the panel's
        toggle is being able to look at what travelled.
        """
        payload = super()._produce(objects, request)
        if payload is None or not request.params.get("SCENE_SIDECAR", True):
            return payload

        sidecar = self._scene_sidecar(
            objects, include_textures=request.params.get("EMBED_TEXTURES", True)
        )
        if sidecar:
            payload.extras["scene_sidecar"] = sidecar
            path = self._make_payload_path(extension=".scene.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sidecar, f, indent=2)
            payload.extras["scene_sidecar_path"] = path
            self.logger.info(
                "Scene sidecar (%s) -> %s", ", ".join(sorted(sidecar)), path
            )
        return payload

    def _scene_sidecar(
        self, objects: List[str], include_textures: bool = True
    ) -> Dict[str, Any]:
        """Scene state the FBX cannot express, one key per section.

        Most material data does survive the FBX round trip, so this is not a
        material channel -- it is where anything that *doesn't* travel goes.
        A new kind of extended setup (lights, environment, custom attributes)
        is added as one more section here plus one more applier in
        :meth:`pythontk.PreviewDeliverer._apply_sidecar`; sections are
        independent, and one that finds nothing is simply omitted.

        *include_textures* mirrors the export's ``EMBED_TEXTURES``: unchecked
        promises a fast, flat-material push, and the sidecar must not smuggle
        maps back in as data URIs after the FBX skipped them -- the readers
        then carry constants only. Skipping the manifest walk is also the
        cheap path the toggle advertises.
        """
        # Resolved ONCE and shared: both readers need the same material list
        # and the same texture manifest, and ``MatManifest.build`` walks every
        # assigned shader graph -- paying for that twice per push is pure waste
        # on the one operation that is meant to feel immediate.
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.mat_manifest import MatManifest

        materials = MatUtils.get_mats(self._expand(objects), as_strings=True) or []
        textures = (
            (MatManifest.build(objects).get("materials", {}) or {})
            if include_textures
            else {}
        )

        sidecar: Dict[str, Any] = {}
        base_color = self._read_base_color(materials, textures)
        if base_color:
            sidecar["base_color"] = base_color
        emissive = self._read_emissive(materials, textures)
        if emissive:
            sidecar["emissive"] = emissive
        return sidecar

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

        Materials with no emission at all are omitted, so an unlit scene adds
        no sidecar and takes the untouched-GLB path.
        """
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        result: Dict[str, Dict[str, Any]] = {}

        for mat in materials:
            if not cmds.objExists(mat):
                continue
            entry: Dict[str, Any] = {}

            texture = (textures.get(mat) or {}).get("emission")
            weight = cls._emission_weight(mat)

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
    def _emission_weight(cls, mat: str) -> float:
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
