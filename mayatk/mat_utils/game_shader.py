# !/usr/bin/python
# coding=utf-8
import os
import logging
from typing import List, Optional, Callable, Union, Dict, Any, Tuple
from qtpy import QtCore

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap
from mayatk.env_utils._env_utils import EnvUtils


class _GameShaderInternal(object):
    """Internal helpers for GameShader."""

    @staticmethod
    def _has_attr(node, attr: str) -> bool:
        """True if `attr` exists on `node`.

        A StingrayPBS node's slots come from the ShaderFX graph loaded into it, so
        the set is not fixed: Autodesk's opacity presets omit slots `Standard.sfx`
        exposes (mayatk's `_AO` presets restore `TEX_ao_map` / `use_ao_map`, but a
        graph loaded from anywhere else may still lack any slot). Every plug
        write must be probed first.
        """
        try:
            return bool(cmds.attributeQuery(attr, node=str(node), exists=True))
        except RuntimeError:  # node gone / not queryable
            return False

    @classmethod
    def _set_flag(cls, node, attr: str, value=1) -> bool:
        """Set a shader toggle (e.g. `use_ao_map`) if the graph exposes it."""
        if not cls._has_attr(node, attr):
            return False
        cmds.setAttr(f"{node}.{attr}", value)
        return True

    @classmethod
    def _clear_slot(cls, node, attr: str) -> None:
        """Break existing inputs on `attr` and on its child plugs.

        This package only ever writes the parent plug now, but a material built
        by an older version (or by hand) can still carry per-child inputs, and
        clearing one level would leave two textures driving one slot.
        """
        for plug in [attr] + [f"{attr}{s}" for s in ("R", "G", "B", "X", "Y", "Z")]:
            if not cls._has_attr(node, plug):
                continue
            for src in (
                cmds.listConnections(
                    f"{node}.{plug}", source=True, destination=False, plugs=True
                )
                or []
            ):
                cmds.disconnectAttr(src, f"{node}.{plug}")

    @staticmethod
    def _graph_label(node) -> str:
        """Name the loaded StingrayPBS graph for a report line."""
        mode = MatUtils.get_stingray_opacity_mode(node)
        return f"'{mode}' ShaderFX graph" if mode else "shader graph"

    def _missing_slot(self, node, texture_type: str, attr: str) -> bool:
        """Report a slot the loaded shader graph doesn't expose. Always False.

        Names the graph: a miss is the GRAPH's doing (its slot set is fixed
        at load), so "no such slot" alone sends the reader hunting through
        the map, not the material.
        """
        self.logger.warning(
            f"{node}: the {self._graph_label(node)} has no '{attr}' slot — "
            f"{texture_type} not connected."
        )
        return False

    def _wire(self, node, texture_type: str, attr: str, source_plug: str) -> bool:
        """Connect `source_plug` → `node.attr` and enable its `use_*` toggle.

        Skips (and reports) cleanly when the loaded graph has no such slot, so a
        graph-specific gap is a skipped map rather than a hard failure.

        Always the COMPOUND plug: a `TEX_*` slot sampled per-child renders as no
        map in VP2 and is not carried by the FBX exporter, so there is no
        legitimate caller for a per-child connect here (the packed maps that
        used to want one now get an image per slot -- `_wire_packed_map`).

        Parameters:
            node: The shader node.
            texture_type (str): Map type, for reporting.
            attr (str): Target slot, e.g. "TEX_ao_map".
            source_plug (str): Source plug to connect from.

        Returns:
            bool: True if the connection was made.
        """
        if not self._has_attr(node, attr):
            return self._missing_slot(node, texture_type, attr)

        self._clear_slot(node, attr)
        cmds.connectAttr(source_plug, f"{node}.{attr}", force=True)

        # Toggle name AND value from the shared ShaderFX rule (ShaderAttributeMap
        # owns it, so the manifest-replay route derives the same pair). The
        # value matters: `TEX_mask_map`'s companion is the `use_opacity_map`
        # SELECTOR, read at 0 -- "enabling" it to 1 points the graph at the
        # colour map's alpha instead and the cutout is connected but inert.
        self._set_flag(node, *ShaderAttributeMap.map_toggle_state(attr))
        return True

    # Where a SEPARATE opacity texture can be sampled, per StingrayPBS graph --
    # probed live against Maya 2025 (VP2), and Unity's Autodesk Interactive
    # shaders document the same contract:
    #
    # * `Standard_Masked.sfx`: `TEX_mask_map`, read on its RED channel when the
    #   `use_opacity_map` selector is 0 (1 selects the colour map's alpha), cut
    #   at `mask_threshold`. Bound only through the COMPOUND plug: a per-child
    #   bind (`TEX_mask_mapX/Y/Z`) is an UNBOUND sampler, reads 0, and discards
    #   every fragment. Cutout runs in the opaque queue with depth writes, so a
    #   solid body sharing the material stays correct.
    # * `Standard_Transparent.sfx`: nothing. Its `opacity` is a scalar UNIFORM
    #   (a texture there is one flat value), and `use_opacity_map` selects
    #   between the colour map's alpha (1) and that uniform (0). The only
    #   per-pixel route is the colour map's alpha -- `_pack_opacity_into_color_map`
    #   moves a standalone map there before the build. And alpha BLENDING puts
    #   every mesh on the material through VP2's transparent queue (no depth
    #   write, per-object sorting), so a solid body wearing it shows its own
    #   back faces: blend only what is really translucent.
    # * `Standard.sfx`: neither slot.
    OPACITY_SLOTS = ("TEX_mask_map",)

    #: Packed maps this shader cannot sample directly -- see `_wire_packed_map`.
    #: A `TEX_*` slot binds only through its COMPOUND plug, so one image can drive
    #: exactly one slot; the rest of its channels have to become images of their
    #: own. Which channels each packing carries is the registry's business
    #: (`MapRegistry.get(<type>).channels`), so only the type list lives here.
    PACKED_CHANNEL_MAPS = ("ORM", "MSAO", "MRAO", "Metallic_Smoothness")

    #: Canonical map type -> the StingrayPBS slot that carries it. Smoothness is
    #: absent on purpose: the graph wants roughness, and the extraction inverts.
    PACKED_CHANNEL_SLOTS = {
        "Metallic": "TEX_metallic_map",
        "Roughness": "TEX_roughness_map",
        "Ambient_Occlusion": "TEX_ao_map",
    }

    def _wire_packed_map(self, sr_node, texture_type: str, texture: str) -> bool:
        """Bind a packed map to StingrayPBS as one image per slot.

        A `TEX_*` slot samples a texture ONLY through its compound plug (see
        :meth:`_wire`): a per-child bind (`TEX_roughness_mapX/Y/Z`) renders as
        NO map in VP2 and the FBX exporter does not carry it -- the property
        ships with its `use_*_map` flag raised and nothing linked. One image
        can therefore drive exactly ONE slot, and a packed map has three
        channels wanting three slots. The branches this replaced bound the
        channel-aligned slot compound and wired the other two per-child, so
        every Mask-Map/ORM material previewed and exported without roughness
        and without AO -- measured 2026-08-25 on two shipped hand-offs, whose
        FBXs carried `Maya|TEX_ao_map` alone.

        So each wanted channel is materialized as its own image
        (:meth:`pythontk.MapFactory.extract_channels`, which writes the loose
        maps beside the packed one and REUSES any already on disk -- the
        artist's own map always outranks derived data) and every slot binds
        compound. The packed file itself stays on disk for the engines that
        read it (Unity HDRP's Mask Map, a glTF ORM); it is simply not what
        drives the Maya material.

        Which channels a packing carries comes from the registry, so a layout
        change there needs no edit here. `Smoothness` is requested as
        `Roughness`: the conversion registry inverts it on the way out, which
        is what the graph wants.

        Parameters:
            sr_node: The StingrayPBS node.
            texture_type (str): The packed map's type (`PACKED_CHANNEL_MAPS`).
            texture (str): Path to the packed map.

        Returns:
            bool: True if at least one slot was bound.
        """
        map_def = ptk.MapRegistry().get(texture_type)
        carried = {
            str(t).rstrip("?")
            for t in ((map_def.channels if map_def else None) or {}).values()
        }
        if "Smoothness" in carried:  # the graph takes roughness; extraction inverts
            carried.add("Roughness")
        offered = {
            map_type: slot
            for map_type, slot in self.PACKED_CHANNEL_SLOTS.items()
            if map_type in carried
        }
        wanted = {
            map_type: slot
            for map_type, slot in offered.items()
            if self._has_attr(sr_node, slot)
        }
        if not wanted:
            return self._missing_slot(
                sr_node, texture_type, " / ".join(sorted(offered.values()))
            )
        # A channel this map carries that the loaded graph has no slot for is a
        # skipped map, not a silent one -- same report every other branch makes.
        for slot in sorted(set(offered.values()) - set(wanted.values())):
            self._missing_slot(sr_node, texture_type, slot)

        extracted = ptk.MapFactory.extract_channels(
            texture_type, texture, sorted(wanted)
        )
        if not extracted:
            # All-or-nothing by contract. Say what was lost rather than falling
            # back to a per-child bind that only looks connected.
            self.logger.warning(
                f"{sr_node}: {ptk.format_path(texture, 'file')} is a packed "
                f"{texture_type} map and StingrayPBS slots bind one image each; "
                f"its channels could not be extracted, so "
                f"{', '.join(sorted(wanted.values()))} are not connected"
            )
            return False

        connected = False
        for map_type, slot in wanted.items():
            path = extracted.get(map_type)
            if not path:
                continue
            node = NodeUtils.create_render_node(
                "file",
                fileTextureName=path,
                colorSpace="Raw",  # every one of these is linear data
                name=ptk.format_path(path, section="name"),
            )
            connected = (
                self._wire(sr_node, texture_type, slot, f"{node}.outColor") or connected
            )
        return connected

    def _wire_opacity(
        self, sr_node, texture_type: str, texture_node, quiet: bool = False
    ) -> bool:
        """Bind a separate opacity texture to the graph's sampler, if it has one.

        Parameters:
            sr_node: StingrayPBS node.
            texture_type (str): Map type, for reporting.
            texture_node: File node of the opacity map (grayscale: the value
                is in every channel, so the compound bind reads it on red).
            quiet (bool): Skip the "no such slot" report — for callers where
                opacity is a bonus channel rather than the whole request.

        Returns:
            bool: True if a sampler was bound (the selector is set with it).
        """
        for attr in self.OPACITY_SLOTS:
            if self._has_attr(sr_node, attr):
                return self._wire(
                    sr_node, texture_type, attr, f"{texture_node}.outColor"
                )
        if quiet:
            return False
        return self._missing_slot(sr_node, texture_type, "TEX_mask_map")

    def _select_color_map_alpha(self, sr_node, texture: str) -> bool:
        """Point the transparent graph's opacity at the colour map's alpha.

        `use_opacity_map` is a SOURCE SELECTOR on `Standard_Transparent.sfx`,
        not an enable — see :attr:`OPACITY_SLOTS`. Raising it when the wired
        colour map has no alpha band selects a branch that samples nothing, so
        the flag is only set when there is an alpha there to read; otherwise it
        is cleared so the graph falls back to its (opaque) scalar rather than
        rendering off an undefined channel.

        Parameters:
            sr_node: StingrayPBS node.
            texture (str): Path of the colour map wired into `TEX_color_map`.

        Returns:
            bool: True if the alpha branch was selected.
        """
        mode = MatUtils.get_stingray_opacity_mode(sr_node)
        if mode not in ("transparent", "masked"):
            return False  # opaque graph — no selector
        carries = self._carries_alpha(texture)
        if carries:
            self._set_flag(sr_node, "use_opacity_map", 1)
            return True
        if mode == "masked":
            # No alpha to select. A separate mask map sets the selector to 0
            # as it binds (`_wire`), whichever order the maps arrive in; with
            # none bound, 1 reads this alpha-less colour map as fully opaque,
            # where 0 would read an UNBOUND sampler and discard every fragment.
            if not cmds.listConnections(
                f"{sr_node}.TEX_mask_map", source=True, destination=False
            ):
                self._set_flag(sr_node, "use_opacity_map", 1)
            return False
        self._set_flag(sr_node, "use_opacity_map", 0)
        if not carries:
            self.logger.warning(
                f"{sr_node}: {ptk.format_path(texture, 'file')} carries no usable "
                f"alpha, so the {self._graph_label(sr_node)} has no per-pixel "
                "opacity to read — the material renders opaque, but its meshes "
                "still go through the viewport's transparent queue (see-through, "
                "wrongly sorted). Give the colour map an alpha channel, or build "
                "this set opaque."
            )
        return carries

    # Alpha extrema that carry no information: the padding every RGBA writer
    # adds for free, and an empty channel that would render the surface
    # invisible. Every alpha judgement here reads through this one pair.
    _FLAT_ALPHA = ((0, 0), (255, 255))

    @staticmethod
    def _open_image(texture: str):
        """*texture* as a PIL image, or None for an unreadable / exotic file.

        An unprobeable image is not evidence of anything, so it must never
        swap the shader graph out from under the rest of the set.
        """
        try:
            with ptk.ImgUtils.allow_large_images():
                return ptk.ImgUtils.ensure_image(texture)
        except Exception:
            return None

    @classmethod
    def _band_extrema(cls, texture: str, band: str) -> Optional[Tuple[int, int]]:
        """8-bit ``(lo, hi)`` of one band of *texture*, or None.

        Parameters:
            texture (str): Path to the image to probe.
            band (str): ``"A"`` for the alpha band, ``"L"`` for the luminance
                of the colour bands.

        Returns:
            tuple | None: None for an absent band or an unreadable file.
        """
        img = cls._open_image(texture)
        if img is None:
            return None
        if band == "A":
            return img.getchannel("A").getextrema() if "A" in img.getbands() else None
        return img.convert("L").getextrema()

    @classmethod
    def _describe_band(cls, texture: str) -> str:
        """``[mode WxH extrema]`` of *texture*'s alpha (else luminance), for a report line."""
        img = cls._open_image(texture)
        if img is None:
            return "[unreadable]"
        band = "A" if "A" in img.getbands() else "L"
        return f"[{img.mode} {img.size[0]}x{img.size[1]} {cls._band_extrema(texture, band)}]"

    @classmethod
    def _opacity_band(cls, texture: str):
        """The band of an opacity map that actually carries the data, or None.

        Most opacity exports are grayscale, but some are written white-RGB over
        a real alpha. Every reader of an opacity map has to agree on which band
        it is looking at or they contradict each other: judging inertness on
        luminance while packing from alpha (or the reverse) is how an authored
        map gets discarded as "uniformly opaque", and how one that survived
        gets packed as solid white.

        Parameters:
            texture (str): Path to the opacity map.

        Returns:
            PIL.Image.Image | None: The single ``L`` band holding the opacity;
            None for an unreadable file.
        """
        img = cls._open_image(texture)
        if img is None:
            return None
        if "A" in img.getbands():
            alpha = img.getchannel("A")
            if alpha.getextrema() not in cls._FLAT_ALPHA:
                return alpha
        return img.convert("L")

    @classmethod
    def _carries_alpha(cls, texture: str) -> bool:
        """True if *texture* has an alpha band holding real transparency.

        Uniformly opaque (255) and uniformly empty (0) alphas are both
        rejected: the first is the padding every RGBA writer adds for free
        (a PNG saved from an RGB source still reports an "A" band), the
        second an empty channel that would render the surface invisible.
        """
        return cls._band_extrema(texture, "A") not in (None, *cls._FLAT_ALPHA)

    @classmethod
    def _is_inert_opacity_map(cls, texture: str) -> bool:
        """True if a standalone opacity map is uniformly full white.

        Exporters write one whenever the PROJECT has an opacity channel --
        Painter's default templates ship a solid-white ``_Opacity`` beside
        every opaque set. It makes nothing transparent, so it must not be
        allowed to summon a transparency graph (and, before mayatk's `_AO`
        presets, to cost the AO slot). Solid black is honoured: it is what
        was authored.

        Judged on ``_opacity_band``, not luminance: a white-RGB / real-alpha
        export reads as solid white through `convert("L")` and was being
        retired as inert with its opacity still in the alpha channel.
        """
        band = cls._opacity_band(texture)
        return band is not None and band.getextrema() == (255, 255)

    def _retire_inert_opacity(
        self, textures: List[str], type_cache: Dict[str, Optional[str]]
    ) -> Tuple[List[str], List[str], List[tuple]]:
        """Keep only the opacity sources that can make something transparent.

        Parameters:
            textures (list): The set after conflict resolution.
            type_cache (dict): ``{path: map type}``, from the caller.

        Returns:
            tuple: ``(textures, sources, retired)`` -- the set with any inert
            standalone ``Opacity`` map removed; the opacity-bearing maps that
            survive (these are what may pick the shader graph); and the
            ``(texture, type, reason)`` rows for what was dropped, reported
            alongside the superseded maps so the drop is never silent. An
            ``Albedo_Transparency`` whose alpha is mere padding stays in the
            set for its colour but is not a source.
        """
        keep, sources, retired = [], [], []
        for texture in textures:
            map_type = type_cache.get(texture) or ptk.MapFactory.resolve_map_type(
                texture
            )
            if map_type == "Opacity" and self._is_inert_opacity_map(texture):
                retired.append(
                    (
                        texture,
                        map_type,
                        "uniformly opaque — nothing to make transparent",
                    )
                )
                continue
            keep.append(texture)
            if map_type == "Opacity" or (
                map_type == "Albedo_Transparency" and self._carries_alpha(texture)
            ):
                sources.append(texture)
        return keep, sources, retired

    @staticmethod
    def _opacity_ruled_out(config: Dict[str, Any]) -> bool:
        """Whether the caller explicitly named the opaque graph.

        Distinct from Auto (``opacity_mode`` unset), which lets the texture
        set decide: ``"none"`` is an assertion that this build is opaque no
        matter what the set carries. An unrecognized value is Auto, not an
        assertion -- see the comment below.

        Parameters:
            config (dict): The resolved config.

        Returns:
            bool: True when an explicit ``opacity_mode`` resolves to ``"none"``.
        """
        mode = (config or {}).get("opacity_mode")
        # Auto (unset) lets the texture set decide, and a typo is not a request
        # either: `resolve_opacity_mode` falls back to "none" for anything it
        # cannot place, and reading that fallback as "the caller ruled opacity
        # out" would let a misspelling silently drop a real opacity map -- the
        # mirror of `_wants_opacity` refusing to read an unknown mode as an
        # assertion the other way. So only a value the graph table NAMES counts;
        # no alias resolves to the opaque graph, so none is lost to the test.
        return (
            mode in MatUtils.STINGRAY_GRAPHS
            and MatUtils.resolve_opacity_mode(mode) == "none"
        )

    def _ignore_opacity_sources(
        self,
        textures: List[str],
        opacity_map: List[str],
        type_cache: Dict[str, Optional[str]],
        name: str = "",
    ) -> Tuple[List[str], List[str], List[tuple]]:
        """Retire every opacity source because the caller ruled opacity out.

        ``Opacity: None`` is an assertion, not an absence -- the set may well
        carry a usable alpha and the caller is saying to build the solid
        shader anyway (a cutout the target engine masks with its own
        material, a decal sheet reused as a body). A standalone ``Opacity``
        leaves the set entirely: the opaque graph has no slot for it, so
        keeping it would only spend a slot miss on a map already ruled out.
        An ``Albedo_Transparency`` stays -- the opaque graph reads its RGB and
        never looks at the alpha.

        Parameters:
            textures (list): The set after ``_retire_inert_opacity``.
            opacity_map (list): The surviving opacity sources.
            type_cache (dict): ``{path: map type}``, from the caller.
            name (str): Material name, for reporting.

        Returns:
            tuple: ``(textures, [], ignored)`` -- the set without its
            standalone opacity maps, no sources left to pick a graph, and the
            ``(texture, type, reason)`` rows so the drop is reported alongside
            the superseded maps rather than being silent.
        """
        standalone = [t for t in opacity_map if type_cache.get(t) == "Opacity"]
        carried = [t for t in opacity_map if t not in standalone]
        if carried:
            link = f"{name}: " if name else ""
            self.logger.info(
                f"{link}opacity set to None — "
                f"{ptk.format_path(carried[0], 'file')} keeps its colour; its "
                "alpha is not wired."
            )
        ignored = [
            (t, type_cache.get(t) or "Opacity", "opacity ruled out (Opacity: None)")
            for t in standalone
        ]
        kept = [t for t in textures if t not in standalone]
        return kept, [], ignored

    def _pack_opacity_into_color_map(
        self,
        textures: List[str],
        opacity_map: List[str],
        type_cache: Dict[str, Optional[str]],
    ) -> Tuple[List[str], List[str], List[tuple]]:
        """Move a standalone opacity map into the colour map's alpha channel.

        The colour map's alpha is where a StingrayPBS opacity is READ: it is
        the transparent graph's only per-pixel route, the masked graph selects
        it with `use_opacity_map` = 1, and every engine importer looks there
        and nowhere else (see :attr:`OPACITY_SLOTS`). A set that ships
        `_Base_Color` (RGB) beside `_Opacity` — the shape every Substance
        export produces — therefore has an authored opacity nothing downstream
        can reach, and the build used to wire it into a uniform (transparent)
        or a Maya-only sampler (masked) and call it done. Packing the two into
        one `Albedo_Transparency` is the same operation the artist would run in
        the exporter, and it is what makes the alpha reachable everywhere.

        A colour map that ALREADY carries an alpha is left alone (it is its own
        opacity source), as is a set whose colour map is missing — there is
        nothing to pack into, and the caller reports that separately.

        Parameters:
            textures (list): The set after `_retire_inert_opacity`.
            opacity_map (list): The surviving opacity sources.
            type_cache (dict): ``{path: map type}``; the packed file is added.

        Returns:
            tuple: ``(textures, opacity_map, superseded)`` — the set with the
            colour map and the standalone opacity replaced by the packed map,
            the opacity sources repointed at it, and the
            ``(texture, type, reason)`` rows so neither input vanishes silently.
        """
        standalone = [t for t in opacity_map if type_cache.get(t) == "Opacity"]
        # By the CACHE, not the filename: a padding-alpha Albedo_Transparency
        # is reclassified there as the plain colour map it is (see
        # `_create_single_network`), and the packer must see it the same way.
        color = [t for t in textures if type_cache.get(t) in ("Base_Color", "Diffuse")]
        if not standalone or not color:
            return textures, opacity_map, []
        color, alpha = color[0], standalone[0]
        if self._carries_alpha(color):  # already its own opacity source
            return textures, opacity_map, []

        # The band is resolved here (`_opacity_band`) because the generic
        # packer takes the LUMINANCE of whatever fills its alpha slot; the
        # packer's own resize to the colour map's size is left to it.
        alpha_band = self._opacity_band(alpha)
        colour_img = self._open_image(color)
        if alpha_band is None or colour_img is None:
            self.logger.warning(
                f"could not read {ptk.format_path(alpha, 'file')} or "
                f"{ptk.format_path(color, 'file')} — leaving the maps separate."
            )
            return textures, opacity_map, []
        if alpha_band.size != colour_img.size:
            self.logger.info(
                f"{ptk.format_path(alpha, 'file')} {self._describe_band(alpha)} is "
                f"resampled to the colour map's {colour_img.size[0]}x"
                f"{colour_img.size[1]} while packing."
            )
        try:
            with ptk.ImgUtils.allow_large_images():
                packed = ptk.MapFactory.pack_transparency_into_albedo(color, alpha_band)
        except (
            Exception
        ) as e:  # mismatched pair / unwritable target — keep the set as-is
            self.logger.warning(
                f"could not pack {ptk.format_path(alpha, 'file')} into "
                f"{ptk.format_path(color, 'file')}: {e}"
            )
            return textures, opacity_map, []

        # Verify the pack instead of trusting it. Everything downstream reads
        # this file's alpha and nothing else re-checks the opacity survived, so
        # a pack that quietly flattened it would wire a dud into the shader and
        # surface as "carries no usable alpha" with no way to tell WHY.
        if not self._carries_alpha(packed):
            self.logger.warning(
                f"packing {ptk.format_path(alpha, 'file')} "
                f"{self._describe_band(alpha)} into "
                f"{ptk.format_path(color, 'file')} {self._describe_band(color)} "
                f"produced {ptk.format_path(packed, 'file')} with a flat alpha "
                f"{self._describe_band(packed)} — the opacity did not survive. "
                "Leaving the maps separate; fix the opacity export (or match its "
                "resolution to the colour map) and rebuild."
            )
            return textures, opacity_map, []

        type_cache[packed] = "Albedo_Transparency"
        reason = f"packed into {ptk.format_path(packed, 'file')} (alpha)"
        superseded = [
            (color, type_cache.get(color) or "Base_Color", reason),
            (alpha, "Opacity", reason),
        ]
        kept = [t for t in textures if t not in (color, alpha)] + [packed]
        return kept, [packed], superseded

    def _wants_opacity(
        self, opacity_map: List[str], config: Dict[str, Any], name: str = ""
    ) -> bool:
        """Whether this build should stand up a transparency-capable shader.

        A usable opacity source (``_retire_inert_opacity``) settles it, and so
        does an explicit ``opacity_mode``: the caller NAMED the graph, which
        is an assertion. The one mode that cannot be seen here is ``"none"``
        -- it is an assertion too, but it has to retire the sources rather
        than outvote them, so ``_ignore_opacity_sources`` has already emptied
        *opacity_map* by the time this runs. That is how a manifest-declared opacity travels
        (``_rebuild_material``) -- the file it refers to classifies to
        nothing, and ``MapFactory.prepare_maps`` drops every unclassified
        file, so nothing in the set can vouch for it by the time the graph
        is chosen.

        The bare ``opacity`` config flag settles NOTHING: workflow presets set
        it to advertise that the workflow *supports* transparency
        (``MapRegistry.get_workflow_presets`` enables the flag for every map
        type the workflow registers), not to assert that THIS set carries an
        alpha. Honouring it cost real maps: Autodesk's transparent graph
        carried no ``TEX_ao_map``, so an opaque set built under a
        transparency-capable preset silently lost its AO to a slot that only
        existed to serve an opacity nothing was going to drive -- and it still
        puts a solid body through the transparent queue for nothing.

        Deliberately NOT evidence: an alpha packed into a plain ``Base_Color``.
        The factory rewrites a base colour to its registry mode (RGB) during
        ``prepare_maps``, so that alpha is already gone when this runs; a
        base colour meant to carry one is an ``Albedo_Transparency`` and
        classifies as such.

        Parameters:
            opacity_map (list): Opacity sources that survived
                ``_retire_inert_opacity``, if any.
            config (dict): The resolved config.
            name (str): Material name, for reporting.

        Returns:
            bool: True to build a transparency-capable shader.
        """
        config = config or {}
        if opacity_map:
            return True
        # Normalized through the graph loader's own resolver, so an alias
        # ("transparent_graph") counts and a typo does not.
        if MatUtils.resolve_opacity_mode(config.get("opacity_mode")) != "none":
            return True
        if config.get("opacity"):
            link = f"{name}: " if name else ""
            self.logger.info(
                f"{link}opacity requested by config, but nothing in this set can "
                "make anything transparent — building the opaque shader (which "
                "keeps the AO slot)."
            )
        return False


class GameShader(ptk.LoggingMixin, _GameShaderInternal):
    """A class to manage the creation of a shader network using StingrayPBS or Standard Surface shaders.
    Classifies a set of texture maps by type and wires them into the shader graph, creating
    whatever conversion nodes each map needs along the way. Renderer-agnostic: an Arnold
    preview network is ``ArnoldBridge``'s concern, applied after the material exists.
    """

    # Texture types whose connection produces an internal conversion node
    # (e.g. invert smoothness → roughness, split a packed channel map).
    CONVERSION_NOTES = {
        "Metallic_Smoothness": "smoothness → roughness (inverted)",
        "ORM": "split R/G/B → AO / Roughness / Metallic",
        "MSAO": "smoothness → roughness; R/G channels split",
        "Albedo_Transparency": "alpha → opacity",
    }

    @CoreUtils.undoable
    def create_network(
        self,
        textures: List[str],
        name: str = "",
        prefix: str = "",
        suffix: str = "",
        config: Union[str, Dict[str, Any]] = None,
        progress_callback: Callable = None,
        **kwargs,
    ) -> Union[Optional[object], List[Optional[object]]]:
        """Create a PBR shader network with textures.

        Parameters:
            textures: List of texture file paths
            name: Shader name (auto-generated from texture if empty)
            prefix: Optional prefix prepended to the resolved shader name.
            suffix: Optional suffix appended to the resolved shader name.
            config: Configuration preset name (str) or dictionary.
            progress_callback: Optional callback(percent, message) for progress updates.
            **kwargs: Configuration overrides (e.g. shader_type, normal_type, etc.)

        Returns:
            The created shader node(s) (Stingray PBS or Standard Surface)
        """
        if not textures:
            self.logger.error("No textures given to create_network.")
            return None

        # Resolve Config
        cfg = ptk.MapRegistry().resolve_config(config, **kwargs)

        # Set defaults for missing keys
        defaults = {
            "shader_type": "stingray",
            "normal_type": "OpenGL",
            "albedo_transparency": False,
            "metallic_smoothness": False,
            "mask_map": False,
            "orm_map": False,
            "opacity": False,
            "emissive": False,
            "ambient_occlusion": False,
            "convert_specgloss_to_pbr": False,
            "cleanup_base_color": False,
            "output_extension": "png",
            # StingrayPBS only: "transparent" (alpha blend) vs "masked" (alpha
            # cutout). None lets the shader pick per `wants_opacity`.
            "opacity_mode": None,
        }

        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v

        # Compact configuration banner: one boxed header + a 2-column table.
        # Gated: ``log_box`` / ``log_table`` write through ``log_raw``, which
        # bypasses level filtering BY DESIGN, so a caller that quieted this
        # logger (a batch driver, another tool running this as a step) would
        # otherwise still get the banner and the whole settings table.
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.log_box("Game Shader Network")
            config_info = [
                ["Shader Type", cfg["shader_type"]],
                ["Normal Type", cfg["normal_type"]],
                ["Opacity", str(cfg["opacity"])],
                ["Emissive", str(cfg["emissive"])],
                ["Ambient Occlusion", str(cfg["ambient_occlusion"])],
                ["Albedo Transparency", str(cfg["albedo_transparency"])],
                ["Metallic Smoothness", str(cfg["metallic_smoothness"])],
                ["Mask Map", str(cfg["mask_map"])],
                ["ORM Map", str(cfg["orm_map"])],
            ]
            self.log_table(config_info, headers=["Option", "Value"])

        # Check for large input size
        try:
            total_size_bytes = sum(
                os.path.getsize(t) for t in textures if os.path.exists(t)
            )
            total_size_mb = total_size_bytes / (1024 * 1024)

            # Warn if over 300MB
            if total_size_mb > 300:
                warn_msg = f"Large input detected ({total_size_mb:.1f} MB). Processing may take some time..."
                self.logger.warning(warn_msg)
                if progress_callback:
                    progress_callback(0, warn_msg)
                    # Force a UI update immediately so the user sees the warning before the heavy lift starts
                    from qtpy import QtWidgets

                    QtWidgets.QApplication.instance().processEvents()
        except Exception as e:
            self.logger.debug(f"Could not calculate input size: {e}")

        def factory_progress(curr, total, msg):
            """Bridge callback to map Factory progress (0-50%) to UI."""
            if progress_callback:
                # Map 0-50 range
                try:
                    pct = int((curr / total) * 50)
                    progress_callback(pct, f"Preparing Maps: {msg}")
                except Exception:
                    pass

        prepared_data = ptk.MapFactory.prepare_maps(
            textures,
            logger=self.logger,
            group_by_set=(not bool(name)),
            max_workers=4,
            progress_callback=factory_progress,
            prefix=prefix,
            suffix=suffix,
            **cfg,
        )

        if isinstance(prepared_data, dict):
            # Batch mode
            total = len(prepared_data)
            self.logger.info(f"Batch processing {total} texture sets...")
            results = []
            created_shaders = []

            i = 0
            for set_name, set_textures in prepared_data.items():
                i += 1
                if progress_callback:
                    # Map 50-100 range
                    pct = 50 + int((i / total) * 50)
                    progress_callback(pct, f"Building Network: {set_name}")

                # Isolate each set: one bad set must not abort the remaining ones.
                try:
                    node = self._create_single_network(
                        set_textures,
                        set_name,  # Use set name for shader name
                        cfg["shader_type"],
                        prefix=prefix,
                        suffix=suffix,
                        config=cfg,
                    )
                except Exception as e:
                    self.logger.error(f"Set '{set_name}' failed: {e}")
                    node = None
                results.append(node)

                status = "Success" if node else "Failed"
                node_name = CoreUtils.short_name(node) if node else "-"
                created_shaders.append([set_name, node_name, status])

            # Log Summary (gated for the same reason as the config banner).
            if self.logger.isEnabledFor(logging.INFO):
                succeeded = sum(1 for r in results if r)
                self.logger.log_box(
                    "Batch Creation Summary",
                    [f"{succeeded}/{total} set(s) built"],
                    level="SUCCESS" if succeeded == total else "WARNING",
                )
                self.log_table(
                    created_shaders,
                    headers=["Set Name", "Node Name", "Status"],
                )

            if progress_callback:
                progress_callback(100, "Completed")

            return results
        else:
            if progress_callback:
                progress_callback(75, "Building Network...")

            node = self._create_single_network(
                prepared_data,
                name,
                cfg["shader_type"],
                prefix=prefix,
                suffix=suffix,
                config=cfg,
            )

            if progress_callback:
                progress_callback(100, "Completed")

            return node

    def _resolve_map_conflicts(
        self,
        textures: List[str],
        type_cache: Dict[str, str],
        config: Dict[str, Any] = None,
    ) -> tuple:
        """Reduce `textures` to one source per shader slot.

        A packed map (ORM / MSAO / MRAO / Metallic_Smoothness /
        Albedo_Transparency) drives the same slots as the separate maps it
        contains, so a set carrying both wires each slot twice — the second
        connection wins and the first map is left as a stray file node, with
        parent and child plugs driven by different textures on split channels.
        Two packings can collide the same way (an ORM beside an HDRP mask map),
        and that one is invisible to `replaces` — the filter ranks them by the
        target workflow instead.

        Which side wins is `ptk.MapFactory.filter_redundant_maps`' call: the
        registry's `replaces` / `config_key` / `channels` /
        `packed_precedence` rules are the single source of truth, so this and
        Mat Updater can't drift apart. When
        dropping a packed map would lose a channel no loose map covers, the
        filter extracts that channel to a real file first — those recovered
        maps join the kept list and get wired like any other texture. Same-type
        duplicates (a set with both `_Mixed_AO` and `_AO`) collapse to the
        first occurrence.

        Parameters:
            textures: Prepared texture paths.
            type_cache: Path → resolved map type (extracted paths are added).
            config: Resolved workflow config; decides packed-vs-separate.

        Returns:
            tuple: (kept textures, [(texture, type, reason), …] dropped,
            {extracted path: table note}).
        """
        inventory: Dict[str, List[str]] = {}
        for t in textures:
            map_type = type_cache.get(t)
            if map_type:
                inventory.setdefault(map_type, []).append(t)

        surviving = {k: list(v) for k, v in inventory.items()}
        report = ptk.MapFactory.filter_redundant_maps(surviving, config=config)

        # The redundancy filter cannot see this one: the three normal types have
        # no `replaces` relationship yet drive one input. Maya's tangent space is
        # OpenGL (Y+) and neither bump2d nor StingrayPBS can flip green in the
        # network (probed on 2025 / MtoA 7.3.4), so the FILE has to be right --
        # hence a target convention here, where blendertk's node graph passes
        # None and flips downstream.
        normal_report = ptk.MapFactory.resolve_normal_maps(
            surviving, target_format="OpenGL"
        )
        report["dropped"].update(normal_report["dropped"])

        kept: List[str] = []
        dropped: List[tuple] = []
        seen: set = set()
        for t in textures:
            map_type = type_cache.get(t)
            if map_type in report["dropped"]:
                dropped.append((t, map_type, report["dropped"][map_type]))
            elif map_type is not None and map_type in seen:
                dropped.append((t, map_type, f"duplicate {map_type} map"))
            else:
                seen.add(map_type)
                kept.append(t)

        # Channels recovered from a dropped packed map are real files now —
        # wire them like any other texture, noting their provenance.
        packed_sources = [
            t
            for t, reason in report["dropped"].items()
            if t in inventory and reason == "superseded by separate maps"
        ]
        source_note = (
            f"extracted from {', '.join(packed_sources)}"
            if packed_sources
            else "extracted"
        )
        # Keyed by FILENAME: the table loop may relativize paths before lookup.
        extracted_notes: Dict[str, str] = {}
        for map_type, path in report["extracted"].items():
            type_cache[path] = map_type
            kept.append(path)
            extracted_notes[ptk.format_path(path, "file")] = source_note

        # Same treatment for a green-flipped normal: a real file now, wired in
        # place of the DirectX source the dropped-walk above already retired.
        for map_type, path in normal_report["converted"].items():
            type_cache[path] = map_type
            kept.append(path)
            extracted_notes[ptk.format_path(path, "file")] = (
                "green channel flipped (DirectX -> OpenGL)"
            )

        return kept, dropped, extracted_notes

    def _create_single_network(
        self,
        textures: List[str],
        name: str,
        shader_type: str,
        prefix: str = "",
        suffix: str = "",
        config: Dict[str, Any] = None,
    ) -> Optional[object]:
        """Internal method to create a single shader network from prepared textures."""
        if not textures:
            self.logger.error("No valid textures after preparation.")
            return None

        if not name:
            name = ptk.MapFactory.get_base_texture_name(
                textures[0], prefix=prefix, suffix=suffix
            )
        # Idempotent affix application: strips any pre-existing occurrence of the
        # configured prefix/suffix from `name` before re-applying, so a filename
        # like "Mat_brick_Albedo.png" with prefix="Mat_" yields "Mat_brick", not
        # "Mat_Mat_brick". Also collapses dangling underscores on either end.
        name = ptk.StrUtils.apply_affix(name, prefix=prefix, suffix=suffix)

        # Pre-compute map type for each texture to avoid redundant lookups
        type_cache = {t: ptk.MapFactory.resolve_map_type(t) for t in textures}
        # An `Albedo_Transparency` whose alpha is mere padding IS a base colour:
        # left classified as the alpha-bearing type it would supersede a real
        # standalone `Opacity` in the conflict pass (the registry rule cannot
        # see image content) and then carry no opacity itself -- the set's
        # transparency silently gone. Reclassify it before anything ranks it.
        for texture, map_type in type_cache.items():
            if map_type == "Albedo_Transparency" and not self._carries_alpha(texture):
                type_cache[texture] = "Base_Color"

        # One source per slot: drop the maps another map already supplies.
        # Channels only a dropped packed map carried come back as extracted
        # loose files, appended to `textures` with a provenance note.
        textures, superseded, extracted_notes = self._resolve_map_conflicts(
            textures, type_cache, config
        )
        # An opacity source has to be able to make something transparent
        # before it may pick the graph: a solid-white Opacity export or a
        # padding alpha would otherwise summon the transparent graph -- and
        # cost the AO slot -- for nothing.
        textures, opacity_map, retired = self._retire_inert_opacity(
            textures, type_cache
        )
        superseded = superseded + retired

        # `Opacity: None` is an assertion -- the caller ruled opacity out, so a
        # usable source may not summon the transparent graph the way it does on
        # Auto. Retire the sources HERE, before the packer reaches for a colour
        # map to fold an alpha into and before `_wants_opacity` weighs them, so
        # the build is the plain opaque one end to end.
        if self._opacity_ruled_out(config):
            textures, opacity_map, ignored = self._ignore_opacity_sources(
                textures, opacity_map, type_cache, name
            )
            superseded = superseded + ignored

        # StingrayPBS: the opacity rides the COLOUR MAP'S ALPHA, on both
        # graphs. It is the transparent graph's only per-pixel opacity, and it
        # is where every engine reads a Stingray cutout from too -- Unity's
        # Built-in importer tests `_MainTex` alpha against `_Cutoff` and has no
        # mask-map slot at all (a separate opacity texture is simply dropped),
        # URP/HDRP select the ColorMap alpha, and glTF has nothing else. The
        # masked graph's own `TEX_mask_map` sampler is a Maya/URP-only route,
        # kept as the fallback when there is no colour map to pack into. Pack
        # before the graph is chosen so `_wants_opacity` still sees a source
        # and the packed map is wired like any other texture.
        if shader_type not in ("standard_surface", "open_pbr"):
            textures, opacity_map, packed_rows = self._pack_opacity_into_color_map(
                textures, opacity_map, type_cache
            )
            superseded = superseded + packed_rows

        # A shader decides its SLOTS at creation -- StingrayPBS loads an
        # opacity ShaderFX graph only when asked for it here. So the choice
        # is made once, against evidence: a usable opacity source,
        # or an explicit `opacity_mode` (how the bridge manifest declares a
        # cutout the filename taxonomy cannot see). A preset's bare `opacity`
        # flag is a workflow CAPABILITY, not an assertion about this set --
        # see `_wants_opacity`.
        wants_opacity = self._wants_opacity(opacity_map, config, name)
        # StingrayPBS offers two ways to spend that opacity — alpha-blend
        # (`transparent`) or alpha-cutout (`masked`). Only the former has a
        # scalar `opacity` slot, so the choice has to travel with the request.
        opacity_mode = (config or {}).get("opacity_mode")
        if shader_type == "standard_surface":
            shader_node = self.setup_standard_surface_node(name, wants_opacity)
        elif shader_type == "open_pbr":
            shader_node = self.setup_open_pbr_node(name, wants_opacity)
        else:  # Default to stingray
            shader_node = self.setup_stringray_node(
                name, wants_opacity, opacity_mode=opacity_mode
            )

        # Which ShaderFX graph the node actually got (None off StingrayPBS).
        # The report names it: a slot miss is the GRAPH's doing.
        graph_mode = MatUtils.get_stingray_opacity_mode(shader_node)
        slot_note = (
            f"no slot on the '{graph_mode}' graph"
            if graph_mode
            else "shader has no matching slot"
        )

        # Validation: Check for Opacity without Base Color
        if opacity_map and not ptk.MapFactory.filter_images_by_type(
            textures, ["Base_Color", "Diffuse", "Albedo_Transparency"]
        ):
            self.logger.warning(
                f"Shader '{name}' has Opacity but no Base Color. Object may appear invisible or black."
            )

        base_dir = EnvUtils.get_env_info("sourceimages")

        # Per-map outcome rows: [status, type, file, note]
        rows: List[List[str]] = []
        connected_count = 0
        failed_count = 0
        slot_misses = 0
        conversion_count = 0

        # Report what a packed map (or an earlier duplicate) took over, so a
        # missing map in the shading network is never a silent drop.
        for texture, texture_type, reason in superseded:
            rows.append(["–", texture_type, ptk.format_path(texture, "file"), reason])

        # Workspace-relative ONLY for a map that lives in the workspace's
        # sourceimages; any other map keeps its absolute path. The blanket
        # relativize kept just the BASENAME for a file on another drive
        # (``sourceimages/x.png`` for ``O:/.../uv_transfer/x.png``), which
        # resolves nowhere -- every map of a scene pulled through a fresh
        # mayapy (default workspace on C:) rendered black (2026-08-22).
        for texture in textures:
            if base_dir and ptk.FileUtils.is_under(texture, base_dir):
                texture = ptk.convert_to_relative_path(texture, base_dir)
            texture_name = ptk.format_path(texture, "file")
            # Use pre-computed type cache; fall back to resolve for converted paths
            texture_type = type_cache.get(texture) or ptk.MapFactory.resolve_map_type(
                texture,
            )

            if texture_type is None:
                rows.append(["✗", "Unknown", texture_name, "unrecognized map type"])
                failed_count += 1
                continue

            # Connect shader nodes based on type
            if shader_type == "standard_surface":
                success = self.connect_standard_surface_nodes(
                    texture, texture_type, shader_node
                )
            elif shader_type == "open_pbr":
                success = self.connect_open_pbr_nodes(
                    texture, texture_type, shader_node
                )
            else:
                success = self.connect_stingray_nodes(
                    texture, texture_type, shader_node
                )

            note = extracted_notes.get(texture_name) or self.CONVERSION_NOTES.get(
                texture_type, ""
            )
            if success:
                connected_count += 1
                if note:
                    conversion_count += 1
                rows.append(["✓", texture_type, texture_name, note])
            else:
                failed_count += 1
                slot_misses += 1
                rows.append(["✗", texture_type, texture_name, slot_note])

        # Per-map connection table (gated — log_table bypasses level filtering).
        # The shader name is the table's TITLE rather than a preceding
        # ``info`` record: in batch mode this runs once per set, and every
        # record is its own paragraph, so the name-then-table pair cost an
        # extra blank-line-separated section per shader.
        if self.logger.isEnabledFor(logging.INFO):
            self.log_table(
                rows,
                headers=["", "Map", "Source", "Conversion"],
                title=f"Shader: {name}",
            )

        # Resolve created shading engine
        shading_groups = cmds.listConnections(shader_node, type="shadingEngine")
        result_node = shading_groups[0] if shading_groups else shader_node
        result_name = CoreUtils.short_name(result_node)

        # Clickable link — points at the shader node (not the SG) so users
        # land on the editable material in the Hypershade.
        link = self.logger.log_link(result_name, "select", node=str(shader_node))

        # Final compact summary
        tail = f"{conversion_count} converted"
        if superseded:
            tail = f"{len(superseded)} superseded, {tail}"
        if failed_count == 0:
            self.logger.success(f"{link} — {connected_count} connected, {tail}")
        else:
            # Per-row notes above name the graph for any slot miss (a graph
            # loaded from elsewhere may lack a slot); the summary stays plain.
            self.logger.warning(
                f"{link} — {connected_count} connected, {failed_count} failed, {tail}"
            )

        return result_node

    def setup_stringray_node(
        self, name: str, opacity: bool, opacity_mode: str = None
    ) -> object:
        """Create a StingrayPBS shader node with the right ShaderFX graph loaded.

        Graph selection and loading live on ``MatUtils`` (the SSoT every route
        into a StingrayPBS shares); this adds the shading group the network
        build expects, which ``create_stingray_shader`` deliberately omits.

        Parameters:
            name (str): The desired name for the StingrayPBS shader node.
            opacity (bool): Legacy flag — True selects the transparent graph
                when *opacity_mode* is not given.
            opacity_mode (str, optional): ``"none"`` / ``"masked"`` /
                ``"transparent"``. ``"masked"`` gives alpha-cutout with hard
                edges and a clean VP2.0 preview — usually what a decal wants.

        Returns:
            str: The created StingrayPBS shader node.
        """
        sr_node = MatUtils.create_stingray_shader(
            name, opacity=opacity, opacity_mode=opacity_mode
        )
        # Named from the node Maya actually created, not the name asked for:
        # Maya uniquifies a taken name (`x` -> `x1`), and `x1` paired with an
        # `xSG1` reads as two unrelated networks.
        MatUtils.create_shading_group(sr_node, name=f"{sr_node}SG")
        return sr_node

    def setup_standard_surface_node(self, name: str, opacity: bool) -> object:
        """Creates and sets up a Maya Standard Surface shader node.

        Maya Standard Surface is the modern PBR shader for Maya 2020+ that replaces
        Stingray PBS. It supports glTF/FBX export for game engines like Unity and Unreal.

        Parameters:
            name (str): The desired name for the Standard Surface shader node.
            opacity (bool): Flag to indicate whether the shader should support transparency.
                          If True, sets up transparency attributes.

        Returns:
            str: The created Standard Surface shader node.
        """
        # Create Standard Surface node - must use shadingNode, not create_render_node
        std_node = cmds.shadingNode("standardSurface", asShader=True, name=name)

        # Create and assign shading group
        sg_node = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{std_node}SG"
        )
        cmds.connectAttr(f"{std_node}.outColor", f"{sg_node}.surfaceShader", force=True)

        if opacity:
            # Enable transparency for standard surface
            # Note: We do NOT set transmission to 1.0 (glass).
            # We ONLY enable thinWalled for correct cutout/foliage behavior.
            # Opacity is driven by the 'opacity' (alpha) input connection later.
            cmds.setAttr(f"{std_node}.thinWalled", True)

        return std_node

    def setup_open_pbr_node(self, name: str, opacity: bool) -> object:
        """Creates and sets up a Maya OpenPBR Surface shader node.

        OpenPBR Surface is the open-standard PBR shader (Maya 2025+) that unifies
        Autodesk Standard Surface and Adobe Standard Material. Suitable for
        glTF/USD/MaterialX export targeting modern game engines and renderers.

        Parameters:
            name (str): The desired name for the OpenPBR Surface shader node.
            opacity (bool): Whether the shader should support cutout transparency.
                          If True, enables thin-walled mode for correct cutout/foliage behavior.

        Returns:
            str: The created OpenPBR Surface shader node.
        """
        try:
            op_node = cmds.shadingNode("openPBRSurface", asShader=True, name=name)
        except RuntimeError as err:
            raise RuntimeError(
                "Cannot create openPBRSurface — node type unavailable. "
                "OpenPBR Surface requires a recent Maya 2025 update or newer. "
                "Use 'Stingray PBS' or 'Standard Surface' on earlier versions."
            ) from err

        sg_node = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{op_node}SG"
        )
        cmds.connectAttr(f"{op_node}.outColor", f"{sg_node}.surfaceShader", force=True)

        if opacity:
            if cmds.attributeQuery("geometryThinWalled", node=op_node, exists=True):
                cmds.setAttr(f"{op_node}.geometryThinWalled", True)

        return op_node

    def _ensure_fbx_safe_connection(self, texture_node, shader_node, attr_name):
        """Creates a dummy connection to a custom attribute to ensure FBX export preserves the texture reference.

        This addresses issues where FBX exporters drop textures connected via:
        1. Individual channels (e.g. outColorR -> metalness)
        2. Secondary nodes (e.g. outAlpha -> Reverse -> roughness)

        Args:
            texture_node: The file texture node.
            shader_node: The shader node.
            attr_name: The name of the custom attribute to create (e.g. 'MSAO_Map').
        """
        if not cmds.attributeQuery(attr_name, node=shader_node, exists=True):
            cmds.addAttr(
                shader_node,
                longName=attr_name,
                attributeType="float3",
                usedAsColor=True,
            )
            cmds.addAttr(
                shader_node,
                longName=f"{attr_name}R",
                attributeType="float",
                parent=attr_name,
            )
            cmds.addAttr(
                shader_node,
                longName=f"{attr_name}G",
                attributeType="float",
                parent=attr_name,
            )
            cmds.addAttr(
                shader_node,
                longName=f"{attr_name}B",
                attributeType="float",
                parent=attr_name,
            )

        target_plug = f"{shader_node}.{attr_name}"
        if not cmds.isConnected(f"{texture_node}.outColor", target_plug):
            cmds.connectAttr(f"{texture_node}.outColor", target_plug, force=True)

    @CoreUtils.undoable
    def connect_stingray_nodes(
        self, texture: str, texture_type: str, sr_node: object
    ) -> bool:
        """Connects texture files to the corresponding slots in the StingrayPBS shader node
        based on the texture type, including handling various specific texture types.

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of the texture (e.g., "Base_Color", "Roughness", "Metallic", "Emissive", etc.).
            sr_node (str): The StingrayPBS shader node to which the textures will be connected.

        Returns:
            bool: True if the connection is successful, False otherwise.
        """

        # Slots are graph-dependent (see _has_attr): probe before creating any node
        # so a slot the graph lacks costs a skipped map, not an orphan file node.
        def _file_node(color_space="Raw", alpha_is_luminance=False):
            """A file node for this texture -- DATA (linear) unless told otherwise.

            Every Stingray slot but the color/emissive pair carries linear data
            (roughness, metallic, normal, AO, the packed maps), and Maya's
            per-extension default reads a PNG as sRGB -- so an unmarked data map
            arrives gamma-DECODED: no error, just wrong values everywhere. The
            standardSurface path has always tagged these ``Raw``; this one did
            not tag anything, which is why a Stingray rebuild produced sRGB
            roughness (caught by the live send harness).
            """
            kwargs = {"colorSpace": color_space} if color_space else {}
            if alpha_is_luminance:
                kwargs["alphaIsLuminance"] = 1
            return NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
                **kwargs,
            )

        if texture_type in ["Base_Color", "Diffuse"]:
            if not self._has_attr(sr_node, "TEX_color_map"):
                return self._missing_slot(sr_node, texture_type, "TEX_color_map")
            texture_node = _file_node(color_space=None)  # color: Maya's sRGB default
            wired = self._wire(
                sr_node, texture_type, "TEX_color_map", f"{texture_node}.outColor"
            )
            # No-op off the transparent graph. On it, this is the plug the
            # opacity is read from, so the selector has to match what the map
            # actually carries — see `_select_color_map_alpha`.
            self._select_color_map_alpha(sr_node, texture)
            return wired

        elif texture_type == "Albedo_Transparency":
            if not self._has_attr(sr_node, "TEX_color_map"):
                return self._missing_slot(sr_node, texture_type, "TEX_color_map")
            texture_node = _file_node(color_space=None)  # color: Maya's sRGB default
            self._wire(
                sr_node, texture_type, "TEX_color_map", f"{texture_node}.outColor"
            )
            # Both ShaderFX graphs read this map's alpha as the opacity once
            # the selector says so. Never ALSO bind it as the masked graph's
            # mask map: that sampler reads RED, not alpha.
            self._select_color_map_alpha(sr_node, texture)
            return True

        elif texture_type in ["Roughness", "Metallic"]:
            target_attr_name = (
                "TEX_roughness_map"
                if texture_type == "Roughness"
                else "TEX_metallic_map"
            )
            if not self._has_attr(sr_node, target_attr_name):
                return self._missing_slot(sr_node, texture_type, target_attr_name)
            texture_node = _file_node()
            # Connect RGB directly to ensure FBX export (Single channel maps are usually grayscale so RGB matches)
            return self._wire(
                sr_node, texture_type, target_attr_name, f"{texture_node}.outColor"
            )

        elif texture_type in self.PACKED_CHANNEL_MAPS:
            return self._wire_packed_map(sr_node, texture_type, texture)

        elif "Normal" in texture_type:
            if not self._has_attr(sr_node, "TEX_normal_map"):
                return self._missing_slot(sr_node, texture_type, "TEX_normal_map")
            texture_node = _file_node()
            return self._wire(
                sr_node, texture_type, "TEX_normal_map", f"{texture_node}.outColor"
            )

        elif texture_type == "Emissive":
            if not self._has_attr(sr_node, "TEX_emissive_map"):
                return self._missing_slot(sr_node, texture_type, "TEX_emissive_map")
            texture_node = _file_node(color_space=None)  # color: Maya's sRGB default
            return self._wire(
                sr_node, texture_type, "TEX_emissive_map", f"{texture_node}.outColor"
            )

        elif texture_type == "Ambient_Occlusion":
            if not self._has_attr(sr_node, "TEX_ao_map"):
                return self._missing_slot(sr_node, texture_type, "TEX_ao_map")
            texture_node = _file_node()
            # Connect RGB directly to ensure FBX export (AO is usually grayscale so RGB matches)
            return self._wire(
                sr_node, texture_type, "TEX_ao_map", f"{texture_node}.outColor"
            )

        elif texture_type == "Opacity":
            # Bound through the compound plug and read on red by the masked
            # graph's sampler (`OPACITY_SLOTS`). Reaching this branch on the
            # transparent graph means `_pack_opacity_into_color_map` declined
            # (it said why); that graph has no sampler for a separate map.
            texture_node = _file_node()
            return self._wire_opacity(sr_node, texture_type, texture_node)

        elif texture_type in ["Specular", "Glossiness"]:
            target_attr_name = (
                "TEX_specular_map"
                if texture_type == "Specular"
                else "TEX_glossiness_map"
            )
            if not self._has_attr(sr_node, target_attr_name):
                return self._missing_slot(sr_node, texture_type, target_attr_name)
            texture_node = _file_node()
            # Connect RGB directly to ensure FBX export
            return self._wire(
                sr_node, texture_type, target_attr_name, f"{texture_node}.outColor"
            )

        else:  # Unsupported texture type
            return False

    def connect_standard_surface_nodes(
        self, texture: str, texture_type: str, std_node: object
    ) -> bool:
        """Connects texture files to Maya Standard Surface shader slots.

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of texture (e.g., "Base_Color", "Roughness", "Metallic").
            std_node (str): The Standard Surface shader node.

        Returns:
            bool: True if connection successful, False otherwise.
        """
        if texture_type in ["Base_Color", "Diffuse"]:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{std_node}.baseColor", force=True
            )

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{std_node}.baseColor", force=True
            )
            # Opacity is RGB; the shared connector broadcasts the alpha across it.
            ShaderAttributeMap.connect_channel(
                texture_node, "opacity", std_node, shader_type="standardSurface"
            )
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{std_node}.specularRoughness", force=True
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{std_node}.metalness", force=True
            )

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,  # smoothness is the real alpha, not luminance
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic in RGB, smoothness in alpha (need to invert for roughness)
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True
            )
            cmds.connectAttr(
                f"{reverse_node}.outputX", f"{std_node}.specularRoughness", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{std_node}.metalness", force=True
            )

            # Ensure FBX export preserves the texture
            self._ensure_fbx_safe_connection(
                texture_node, std_node, "Metallic_Smoothness_Map"
            )

        elif texture_type == "ORM":
            # Unreal/glTF ORM Map: R=AO, G=Roughness, B=Metallic
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic (B)
            cmds.connectAttr(
                f"{texture_node}.outColorB", f"{std_node}.metalness", force=True
            )
            # Roughness (G)
            cmds.connectAttr(
                f"{texture_node}.outColorG", f"{std_node}.specularRoughness", force=True
            )
            # AO (R) -> Multiply with Base Color
            existing_conn = cmds.listConnections(
                f"{std_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
                cmds.connectAttr(
                    f"{texture_node}.outColorR", f"{mult_node}.input2X", force=True
                )
                cmds.connectAttr(
                    f"{texture_node}.outColorR", f"{mult_node}.input2Y", force=True
                )
                cmds.connectAttr(
                    f"{texture_node}.outColorR", f"{mult_node}.input2Z", force=True
                )
                cmds.connectAttr(
                    f"{mult_node}.output", f"{std_node}.baseColor", force=True
                )

            self._ensure_fbx_safe_connection(texture_node, std_node, "ORM_Map")

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,  # smoothness is the real alpha, not luminance
                name=ptk.format_path(texture, section="name"),
            )
            # Connect red channel (metallic) to metalness
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{std_node}.metalness", force=True
            )
            # Smoothness in alpha needs to be inverted to roughness
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True
            )
            cmds.connectAttr(
                f"{reverse_node}.outputX", f"{std_node}.specularRoughness", force=True
            )
            # AO in green channel - multiply with base color if already connected
            existing_conn = cmds.listConnections(
                f"{std_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
                cmds.connectAttr(
                    f"{texture_node}.outColorG", f"{mult_node}.input2X", force=True
                )
                cmds.connectAttr(
                    f"{texture_node}.outColorG", f"{mult_node}.input2Y", force=True
                )
                cmds.connectAttr(
                    f"{texture_node}.outColorG", f"{mult_node}.input2Z", force=True
                )
                cmds.connectAttr(
                    f"{mult_node}.output", f"{std_node}.baseColor", force=True
                )

            # Ensure FBX export preserves the texture
            self._ensure_fbx_safe_connection(texture_node, std_node, "MSAO_Map")

        elif "Normal" in texture_type:
            # Standard Surface uses bump2d for normal maps
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
            )
            bump_node = cmds.shadingNode("bump2d", asUtility=True)
            cmds.setAttr(f"{bump_node}.bumpInterp", 1)  # Tangent space normals
            # Use outAlpha (grayscale) instead of outColor for bump2d compatibility
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{bump_node}.bumpValue", force=True
            )
            cmds.connectAttr(
                f"{bump_node}.outNormal", f"{std_node}.normalCamera", force=True
            )

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{std_node}.emissionColor", force=True
            )
            cmds.setAttr(f"{std_node}.emission", 1.0)

        elif texture_type == "Ambient_Occlusion":
            # Standard Surface doesn't have direct AO input, multiply with base color
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
            )
            # Create multiply node to combine AO with base color
            mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
            # If base color already connected, insert multiply
            existing_conn = cmds.listConnections(
                f"{std_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{mult_node}.input2", force=True
            )
            cmds.connectAttr(f"{mult_node}.output", f"{std_node}.baseColor", force=True)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            # standardSurface.opacity is a float3, so a bare
            # outAlpha -> opacity raised "Data types ... are not compatible"
            # and the map arrived unconnected. Routed through the shared
            # connector, which broadcasts the alpha across the compound's
            # children (the Albedo_Transparency branch above did this by hand).
            ShaderAttributeMap.connect_channel(
                texture_node, "opacity", std_node, shader_type="standardSurface"
            )

        else:
            return False

        return True

    def connect_open_pbr_nodes(
        self, texture: str, texture_type: str, op_node: object
    ) -> bool:
        """Connects texture files to Maya OpenPBR Surface shader slots.

        OpenPBR attribute mapping:
            Base Color           -> baseColor (color3)
            Metallic             -> baseMetalness (float)
            Roughness            -> specularRoughness (float)
            Normal               -> bump2d.outNormal -> geometryNormal (vector)
            Emissive             -> emissionColor (color3) + emissionLuminance
            Opacity              -> geometryOpacity (color3, RGB driven by alpha)
            AO                   -> multiplied with baseColor (no native AO input)

        Parameters:
            texture (str): The file path of the texture image to be connected.
            texture_type (str): The type of texture (e.g., "Base_Color", "Roughness", "Metallic").
            op_node (str): The OpenPBR Surface shader node.

        Returns:
            bool: True if connection successful, False otherwise.
        """
        if texture_type in ["Base_Color", "Diffuse"]:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{op_node}.baseColor", force=True
            )

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{op_node}.baseColor", force=True
            )
            # geometryOpacity is color3 — the shared connector broadcasts the alpha.
            ShaderAttributeMap.connect_channel(
                texture_node, "opacity", op_node, shader_type="openPBRSurface"
            )
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{op_node}.specularRoughness", force=True
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{op_node}.baseMetalness", force=True
            )

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,  # smoothness is the real alpha, not luminance
                name=ptk.format_path(texture, section="name"),
            )
            # Metallic in RGB, smoothness in alpha (need to invert for roughness)
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True
            )
            cmds.connectAttr(
                f"{reverse_node}.outputX", f"{op_node}.specularRoughness", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{op_node}.baseMetalness", force=True
            )

            self._ensure_fbx_safe_connection(
                texture_node, op_node, "Metallic_Smoothness_Map"
            )

        elif texture_type == "ORM":
            # Unreal/glTF ORM Map: R=AO, G=Roughness, B=Metallic
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColorB", f"{op_node}.baseMetalness", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outColorG", f"{op_node}.specularRoughness", force=True
            )
            # AO (R) -> Multiply with Base Color
            existing_conn = cmds.listConnections(
                f"{op_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
                cmds.connectAttr(
                    f"{texture_node}.outColorR", f"{mult_node}.input2X", force=True
                )
                cmds.connectAttr(
                    f"{texture_node}.outColorR", f"{mult_node}.input2Y", force=True
                )
                cmds.connectAttr(
                    f"{texture_node}.outColorR", f"{mult_node}.input2Z", force=True
                )
                cmds.connectAttr(
                    f"{mult_node}.output", f"{op_node}.baseColor", force=True
                )

            self._ensure_fbx_safe_connection(texture_node, op_node, "ORM_Map")

        elif texture_type == "MSAO":
            # Unity HDRP Mask Map: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,  # smoothness is the real alpha, not luminance
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{op_node}.baseMetalness", force=True
            )
            # Smoothness (alpha) -> invert -> roughness
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True
            )
            cmds.connectAttr(
                f"{reverse_node}.outputX", f"{op_node}.specularRoughness", force=True
            )
            # AO (G) -> multiply with base color if already connected
            existing_conn = cmds.listConnections(
                f"{op_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
                cmds.connectAttr(
                    f"{texture_node}.outColorG", f"{mult_node}.input2X", force=True
                )
                cmds.connectAttr(
                    f"{texture_node}.outColorG", f"{mult_node}.input2Y", force=True
                )
                cmds.connectAttr(
                    f"{texture_node}.outColorG", f"{mult_node}.input2Z", force=True
                )
                cmds.connectAttr(
                    f"{mult_node}.output", f"{op_node}.baseColor", force=True
                )

            self._ensure_fbx_safe_connection(texture_node, op_node, "MSAO_Map")

        elif "Normal" in texture_type:
            # Feed via bump2d in tangent-space mode. The vector input's name is
            # version-dependent: Maya 2025's openPBRSurface exposes the classic
            # `normalCamera` and has NO `geometryNormal` (probed on 2025 —
            # connecting to it raised "destination attribute cannot be found"
            # and lost the normal map entirely). Prefer the OpenPBR-spec name
            # where a newer Maya provides it, then fall back.
            normal_plug = next(
                (
                    a
                    for a in ("geometryNormal", "normalCamera")
                    if self._has_attr(op_node, a)
                ),
                None,
            )
            if not normal_plug:
                return self._missing_slot(op_node, texture_type, "geometryNormal")
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
            )
            bump_node = cmds.shadingNode("bump2d", asUtility=True)
            cmds.setAttr(f"{bump_node}.bumpInterp", 1)  # Tangent space normals
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{bump_node}.bumpValue", force=True
            )
            cmds.connectAttr(
                f"{bump_node}.outNormal", f"{op_node}.{normal_plug}", force=True
            )

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{op_node}.emissionColor", force=True
            )
            # OpenPBR emissionLuminance is in nits (cd/m^2); default 0 means no
            # emission. 1000 nits is a reasonable starting point for a visibly
            # glowing surface (typical emissive panel/screen). Tweak per scene.
            if cmds.attributeQuery("emissionLuminance", node=op_node, exists=True):
                cmds.setAttr(f"{op_node}.emissionLuminance", 1000.0)

        elif texture_type == "Ambient_Occlusion":
            # OpenPBR has no native AO input — multiply with base color
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                name=ptk.format_path(texture, section="name"),
            )
            mult_node = cmds.shadingNode("multiplyDivide", asUtility=True)
            existing_conn = cmds.listConnections(
                f"{op_node}.baseColor", source=True, destination=False, plugs=True
            )
            if existing_conn:
                cmds.connectAttr(existing_conn[0], f"{mult_node}.input1", force=True)
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{mult_node}.input2", force=True
            )
            cmds.connectAttr(f"{mult_node}.output", f"{op_node}.baseColor", force=True)

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                name=ptk.format_path(texture, section="name"),
            )
            # geometryOpacity is color3 — the shared connector broadcasts the alpha.
            ShaderAttributeMap.connect_channel(
                texture_node, "opacity", op_node, shader_type="openPBRSurface"
            )

        else:
            return False

        return True

    def filter_for_correct_metallic_map(
        self,
        textures: List[str],
        use_metallic_smoothness: bool,
        output_extension: str = "png",
    ) -> List[str]:
        """Filters textures to ensure the correct handling of metallic maps based on the use_metallic_smoothness parameter.
        Prioritizes a metallic smoothness map over separate metallic and roughness maps when use_metallic_smoothness is True.
        If use_metallic_smoothness is False, filters out any metallic smoothness or smoothness maps from the textures.
        If neither a roughness nor a metallic map is provided, converts the specular map to the necessary maps.

        Parameters:
            textures (List[str]): List of texture file paths.
            use_metallic_smoothness (bool): Flag indicating whether to use a combined metallic smoothness map.

        Returns:
            List[str]: Modified list of texture file paths with the correct metallic map handling.
        """
        # Filter for existing maps
        metallic_smoothness_map = ptk.MapFactory.filter_images_by_type(
            textures, "Metallic_Smoothness"
        )
        metallic_map = ptk.MapFactory.filter_images_by_type(textures, "Metallic")
        roughness_map = ptk.MapFactory.filter_images_by_type(textures, "Roughness")
        smoothness_map = ptk.MapFactory.filter_images_by_type(textures, "Smoothness")
        specular_map = ptk.MapFactory.filter_images_by_type(textures, "Specular")

        filtered_textures = textures.copy()

        if use_metallic_smoothness:
            if metallic_smoothness_map:
                # If a metallic smoothness map exists, remove other maps and return
                filtered_textures = [
                    tex
                    for tex in textures
                    if tex not in metallic_map + roughness_map + smoothness_map
                ]
                return filtered_textures

            elif specular_map:
                # Convert specular map to roughness and metallic maps
                created_roughness_map = ptk.MapFactory.create_roughness_from_spec(
                    specular_map[0]
                )
                created_metallic_map = ptk.MapFactory.create_metallic_from_spec(
                    specular_map[0]
                )

                # Save these images to disk and get their file paths
                base_name = ptk.MapFactory.get_base_texture_name(specular_map[0])
                out_dir = os.path.dirname(specular_map[0])

                rough_path = os.path.join(
                    out_dir, f"{base_name}_Roughness.{output_extension}"
                )
                metal_path = os.path.join(
                    out_dir, f"{base_name}_Metallic.{output_extension}"
                )

                ptk.ImgUtils.save_image(created_roughness_map, rough_path)
                ptk.ImgUtils.save_image(created_metallic_map, metal_path)

                # Now you can combine using file paths:
                combined_map_name = f"{base_name}_MetallicSmoothness.{output_extension}"
                combined_map_path = os.path.join(out_dir, combined_map_name)

                combined_map = ptk.MapFactory.pack_smoothness_into_metallic(
                    metal_path,
                    rough_path,
                    invert_alpha=True,
                    output_path=combined_map_path,
                )

                # Remove individual metallic, roughness, smoothness maps and the newly created maps
                filtered_textures = [
                    tex
                    for tex in filtered_textures
                    if tex not in metallic_map + roughness_map + smoothness_map
                ] + [combined_map]
                return filtered_textures

            elif metallic_map and (roughness_map or smoothness_map):
                # If metallic and roughness/smoothness maps exist, combine them into a metallic smoothness map
                alpha_map = roughness_map[0] if roughness_map else smoothness_map[0]
                invert_alpha = bool(roughness_map)

                base_name = ptk.MapFactory.get_base_texture_name(metallic_map[0])
                out_dir = os.path.dirname(metallic_map[0])
                combined_map_name = f"{base_name}_MetallicSmoothness.{output_extension}"
                combined_map_path = os.path.join(out_dir, combined_map_name)

                combined_map = ptk.MapFactory.pack_smoothness_into_metallic(
                    metallic_map[0],
                    alpha_map,
                    invert_alpha=invert_alpha,
                    output_path=combined_map_path,
                )
                filtered_textures = [
                    tex
                    for tex in filtered_textures
                    if tex not in metallic_map + roughness_map + smoothness_map
                ] + [combined_map]
                return filtered_textures

        else:  # If use_metallic_smoothness is False
            # Remove any metallic smoothness or smoothness maps from the list
            filtered_textures = [
                tex
                for tex in textures
                if tex not in metallic_smoothness_map + smoothness_map
            ]

            if (not metallic_map or not roughness_map) and specular_map:
                # create_*_from_spec return in-memory Image.Image objects; save them
                # to disk and append the resulting paths (mirrors the True-branch),
                # keeping filtered_textures a pure list of file-path strings.
                base_name = ptk.MapFactory.get_base_texture_name(specular_map[0])
                out_dir = os.path.dirname(specular_map[0])

                if not metallic_map:
                    created_metallic_map = ptk.MapFactory.create_metallic_from_spec(
                        specular_map[0]
                    )
                    metal_path = os.path.join(
                        out_dir, f"{base_name}_Metallic.{output_extension}"
                    )
                    ptk.ImgUtils.save_image(created_metallic_map, metal_path)
                    filtered_textures.append(metal_path)

                if not roughness_map:
                    created_roughness_map = ptk.MapFactory.create_roughness_from_spec(
                        specular_map[0]
                    )
                    rough_path = os.path.join(
                        out_dir, f"{base_name}_Roughness.{output_extension}"
                    )
                    ptk.ImgUtils.save_image(created_roughness_map, rough_path)
                    filtered_textures.append(rough_path)

            return filtered_textures

        # Return the textures list unchanged if no conditions are met
        return filtered_textures

    def filter_for_mask_map(
        self,
        textures: List[str],
        output_extension: str = "png",
    ) -> List[str]:
        """Creates Unity HDRP Mask Map (MSAO) by packing Metallic, AO, Detail, and Smoothness.

        Unity HDRP Mask Map format:
        - R: Metallic
        - G: Ambient Occlusion
        - B: Detail Mask
        - A: Smoothness

        Parameters:
            textures (List[str]): List of texture file paths.
            output_extension (str): File extension for generated mask map.

        Returns:
            List[str]: Modified list with mask map replacing individual maps.
        """
        # Filter for required maps
        metallic_map = ptk.MapFactory.filter_images_by_type(textures, "Metallic")
        ao_map = ptk.MapFactory.filter_images_by_type(
            textures, ["Ambient_Occlusion", "AO"]
        )
        detail_map = ptk.MapFactory.filter_images_by_type(textures, "Detail_Mask")
        roughness_map = ptk.MapFactory.filter_images_by_type(textures, "Roughness")
        smoothness_map = ptk.MapFactory.filter_images_by_type(textures, "Smoothness")

        # Need at least metallic map to create mask map
        if not metallic_map:
            self.logger.warning(
                "No metallic map found for Mask Map creation. Skipping MSAO packing."
            )
            return textures

        # Determine smoothness/roughness source
        if smoothness_map:
            alpha_map = smoothness_map[0]
            invert_alpha = False
        elif roughness_map:
            alpha_map = roughness_map[0]
            invert_alpha = True  # Invert roughness to get smoothness
        else:
            self.logger.warning(
                "No roughness or smoothness map found for Mask Map alpha channel."
            )
            alpha_map = None
            invert_alpha = False  # no alpha source; pack_msao_texture fills a default

        # Use AO if available, otherwise create a white map
        if not ao_map:
            self.logger.warning(
                "No AO map found. Using white (255) for AO channel in Mask Map."
            )
            # Will be handled by pack_msao_texture with fill_values

        try:
            # Create the MSAO mask map
            base_name = ptk.MapFactory.get_base_texture_name(metallic_map[0])
            out_dir = os.path.dirname(metallic_map[0])

            # Construct output path with extension
            mask_map_name = f"{base_name}_MaskMap.{output_extension}"
            mask_map_full_path = os.path.join(out_dir, mask_map_name)

            # Use pythontk's pack_msao_texture function
            mask_map_path = ptk.MapFactory.pack_msao_texture(
                metallic_map_path=metallic_map[0],
                ao_map_path=(
                    ao_map[0] if ao_map else None
                ),  # Use None if no AO (will be filled with white)
                alpha_map_path=(
                    alpha_map if alpha_map else None
                ),  # Use None if no alpha (will be filled with default)
                detail_map_path=(
                    detail_map[0] if detail_map else None
                ),  # Use None if no detail (will be filled with black)
                output_dir=out_dir,
                suffix="_MaskMap",
                invert_alpha=invert_alpha,
                output_path=mask_map_full_path,
            )

            self.logger.info(f"Created Mask Map: {os.path.basename(mask_map_path)}")

            # Remove individual maps and add mask map
            filtered_textures = [
                tex
                for tex in textures
                if tex
                not in metallic_map
                + ao_map
                + roughness_map
                + smoothness_map
                + detail_map
            ] + [mask_map_path]

            return filtered_textures

        except Exception as e:
            self.logger.error(f"Error creating Mask Map: {str(e)}")
            return textures

    def filter_for_correct_base_color_map(
        self, textures: List[str], use_albedo_transparency: bool
    ) -> List[str]:
        """Filters textures to ensure the correct handling of albedo maps based on the use_albedo_transparency parameter.
        Prioritizes an albedo transparency map over separate albedo and transparency maps when use_albedo_transparency is True.
        If use_albedo_transparency is False, filters out any albedo transparency maps from the textures.

        Parameters:
            textures (List[str]): List of texture file paths.
            use_albedo_transparency (bool): Flag indicating whether to use a combined albedo transparency map.

        Returns:
            List[str]: Modified list of texture file paths with the correct albedo map handling.
        """
        albedo_transparency_map = ptk.MapFactory.filter_images_by_type(
            textures, "Albedo_Transparency"
        )
        base_color_map = ptk.MapFactory.filter_images_by_type(
            textures, ["Base_Color", "Diffuse"]
        )
        transparency_map = ptk.MapFactory.filter_images_by_type(textures, "Opacity")

        if use_albedo_transparency:
            if albedo_transparency_map:
                # Remove separate albedo and transparency maps if an albedo transparency map exists
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map
                ]
            elif base_color_map and transparency_map:
                # Create an albedo transparency map from albedo and transparency maps, then update the list
                combined_map = ptk.MapFactory.pack_transparency_into_albedo(
                    base_color_map[0], transparency_map[0]
                )
                return [
                    tex
                    for tex in textures
                    if tex not in base_color_map + transparency_map
                ] + [combined_map]

        # If no base color or diffuse map is found, return the list unchanged
        return textures


class GameShaderSlots(GameShader):
    msg_intro = """<u>To setup the material:</u>
        <br>• Click the <b>Create Network</b> button to select texture maps and create the shader connections. This will build a shading network from the provided textures and manage OpenGL and DirectX normal map conversions.

        <p><b>Note:</b> To correctly render opacity and transmission in Maya, the Opaque setting needs to be disabled on the Shape node.
        If Opaque is enabled, opacity will not work at all. Transmission will work, however any shadows cast by
        the object will always be solid and not pick up the Transparent Color or density of the shader.</p>
    """

    def __init__(self, switchboard):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.game_shader

        # Don't keep this window glued above other tools — user can use the
        # pin button to toggle stay-on-top when needed.
        if hasattr(self.ui, "set_flags"):
            self.ui.set_flags(WindowStaysOnTopHint=False)

        self.workspace_dir = EnvUtils.get_env_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")
        self.image_files = None
        self.last_created_shader = None

        self.ui.txt001.setText(self.msg_intro)

        # Route the shared logger into the txt001 QTextBrowser with HTML
        # colorization. Using setup_logging_redirect (instead of the old
        # CallbackLogHandler) is what enables clickable <a href="action://…">
        # links inside log messages.
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt001)

        # Dispatch action:// links (e.g. select the created shader).
        if hasattr(self.ui.txt001, "anchorClicked"):
            self.ui.txt001.anchorClicked.connect(self._on_log_link_clicked)

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_graph_material",
            setText="Open in Editor",
            setToolTip="Graph the material in the Hypershade.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Game Shader",
                body="Build complete PBR shader networks from a folder of "
                "texture maps. Map types (Base Color, Normal, Roughness, "
                "Metallic, AO, etc.) are auto-detected from file names.",
                steps=[
                    "Set <b>Material Name</b> and the <b>Prefix / Suffix</b> "
                    "(affix-mode option box selects placement).",
                    "Pick a <b>Shader Type</b> — Stingray PBS / Standard "
                    "Surface / OpenPBR Surface.",
                    "Pick a <b>Preset</b> — the preset's tooltip names its "
                    "target workflow and the platforms it ships to "
                    "(UE / Unity / Godot / film, or glTF 2.0 for WebXR).",
                    "Press <b>Create</b> and select a folder; results stream "
                    "into the log panel.",
                ],
                notes=[
                    "Use <b>Open in Editor</b> from the header menu to graph "
                    "the resulting material in the Hypershade.",
                ],
            )
        )

    def lbl_graph_material(self):
        """Graph the material in the Hypershade."""
        if self.last_created_shader:
            MatUtils.graph_materials(self.last_created_shader)
        elif cmds.objExists(self.mat_name):
            MatUtils.graph_materials(self.mat_name)
        else:
            cmds.warning(f"Material '{self.mat_name}' not found.")

    @property
    def mat_name(self) -> str:
        """Get the mat name from the user input text field.

        Returns:
            (str)
        """
        text = self.ui.txt000.text()
        return text

    @property
    def mat_prefix(self) -> str:
        """Return the affix text when it resolves as a prefix, else empty string."""
        if not hasattr(self.ui, "txt002"):
            return ""
        prefix, _ = self.ui.txt002.option_box.resolve_affix(default="prefix")
        return prefix

    @property
    def mat_suffix(self) -> str:
        """Return the affix text when it resolves as a suffix, else empty string."""
        if not hasattr(self.ui, "txt002"):
            return ""
        _, suffix = self.ui.txt002.option_box.resolve_affix(default="prefix")
        return suffix

    @property
    def normal_map_type(self) -> str:
        """Get the normal map type from the comboBoxes current text.

        Returns:
            (str)
        """
        text = self.ui.cmb001.currentText()
        return text

    @property
    def output_extension(self) -> str:
        """Selected output extension, or '' when 'Profile default' is chosen.

        An empty string signals the caller to defer per-map format to the selected
        workflow profile's template rather than forcing one container for all maps.

        Returns:
            (str) The file extension in lowercase (e.g., 'png', 'jpg'), or ''.
        """
        text = self.ui.cmb003.currentText().lower()
        return "" if text.startswith("profile") else text

    @property
    def shader_type(self) -> str:
        """Get the shader type selection.

        Returns:
            (str) One of 'stingray', 'standard_surface', or 'open_pbr'.
        """
        if hasattr(self.ui, "cmb004"):
            text = self.ui.cmb004.currentText()
            if "Open PBR" in text or "OpenPBR" in text:
                return "open_pbr"
            if "Standard Surface" in text:
                return "standard_surface"
        return "stingray"

    @property
    def opacity_mode(self) -> Optional[str]:
        """The opacity graph the panel asks for.

        Returns:
            str | None: ``"transparent"`` (alpha blend), ``"masked"`` (alpha
            cutout), or ``"none"`` -- opacity ruled out, which retires the
            set's opacity sources instead of letting them pick the graph.
            None for Auto: a usable opacity source then builds as transparent,
            as it always has.
        """
        if hasattr(self.ui, "cmb005"):
            text = self.ui.cmb005.currentText().lower()
            if "masked" in text:
                return "masked"
            if "transparent" in text:
                return "transparent"
            if "none" in text:
                return "none"
        return None

    def cmb002_init(self, widget):
        """Initialize Presets"""
        if not widget.is_initialized:
            # Names + tooltips come from the OutputTemplates SSoT, shared with the
            # converter / compositor / mat_updater / scene exporter, so none of
            # them depends on the preset dict's internal shape.
            widget.clear()
            for name, description in ptk.OutputTemplates.profile_choices():
                widget.addItem(name)
                if description:
                    widget.setItemData(
                        widget.count() - 1, description, QtCore.Qt.ToolTipRole
                    )

    def cmb003_init(self, widget):
        """Initialize Output Format.

        Selecting 'Profile default' defers each map's container/bit-depth to the
        selected workflow profile's output template; a concrete format forces that
        container for all maps.
        """
        if not widget.is_initialized:
            # format_choices appends the sentinel LAST, preserving the existing
            # format indices — combobox state is persisted by index, so moving it
            # to the front would silently shift every saved selection by one.
            widget.add(
                ptk.OutputTemplates.format_choices(
                    sentinel=ptk.OutputTemplates.PROFILE_DEFAULT_LABEL
                )
            )

    def txt000_init(self, widget):
        """Material-name field — clearable back to the auto-derived name."""
        widget.option_box.clear_option = True

    def txt002_init(self, widget):
        """Add a prefix/suffix/auto-mode picker to the affix field."""
        widget.option_box.set_affix(
            default="prefix",
            on_change=lambda _mode, w=widget: self._apply_affix_placeholder(w),
            settings_key="game_shader_affix",  # ``txt002`` alone is too generic
            convention_key="material",  # fourth state: the shared convention
        )
        self._apply_affix_placeholder(widget)

    @staticmethod
    def _apply_affix_placeholder(widget):
        mode = widget.option_box.affix_mode
        if mode == "prefix":
            widget.setPlaceholderText("Prefix")
            widget.setToolTip(
                "Prefix prepended to the base name.\n"
                'Example: "MAT_" + "brick" → "MAT_brick".'
            )
        elif mode == "suffix":
            widget.setPlaceholderText("Suffix")
            widget.setToolTip(
                "Suffix appended to the base name.\n"
                'Example: "brick" + "_MAT" → "brick_MAT".'
            )
        elif mode == "convention":
            # The field is showing (and locked to) the shared convention, so
            # the placeholder would never be seen — name the source instead, so
            # a user wondering why they cannot type has the answer in the tip.
            widget.setPlaceholderText("Scene Convention")
            widget.setToolTip(
                "Following the shared naming convention for materials.\n"
                "Edit it in the Naming panel (Suffix By Type); every tool set "
                "to this mode follows.\n"
                "Click the button beside the field to type your own instead."
            )
        else:  # auto
            widget.setPlaceholderText("Affix")
            widget.setToolTip(
                "Affix — placement inferred from '_' position.\n"
                "  '_MAT' → suffix (appended)\n"
                "  'MAT_' → prefix (prepended)"
            )

    def b000(self):
        """Create network."""
        image_files = self.sb.file_dialog(
            file_types=[f"*.{ext}" for ext in ptk.ImgUtils.texture_file_types],
            title="Select one or more image files to open.",
            start_dir=self.source_images_dir,
        )

        if not image_files:
            return

        self.image_files = image_files
        self.ui.txt001.clear()

        # Get template configuration using combo box text
        template_name = self.ui.cmb002.currentText()

        # 'Profile default' (empty ext) → let the workflow profile drive per-map
        # format; a concrete ext overrides it for all maps.
        ext = self.output_extension
        output_profile = template_name if not ext else None

        def progress_adapter(p, m):
            # Surface progress in the footer (the .ui has no progressBar —
            # the old setValue branch was dead) and keep the UI responsive
            # during the long network build.
            self.ui.footer.setText(f"{m} ({int(p)}%)" if m else f"{int(p)}%")
            self.sb.QtWidgets.QApplication.instance().processEvents()

        self.last_created_shader = self.create_network(
            self.image_files,
            self.mat_name,
            prefix=self.mat_prefix,
            suffix=self.mat_suffix,
            config=template_name,
            shader_type=self.shader_type,
            normal_type=self.normal_map_type,
            opacity_mode=self.opacity_mode,
            cleanup_base_color=False,  # Can be exposed in UI later if needed
            output_extension=ext or None,
            output_profile=output_profile,
            progress_callback=progress_adapter,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("game_shader", reload=True)
    ui.show(pos="screen", app_exec=True)
