# !/usr/bin/python
# coding=utf-8
"""Logical texture channel -> per-shader (attribute, output plug), and the one
connector that honors that declaration against a real Maya attribute.

``import maya.cmds`` is deferred into the connector's call bodies so the map
itself stays importable (and testable) without a running Maya.
"""
from typing import Optional, Tuple, Dict, Any
from collections import namedtuple

# Each slot is: (attribute_name, output_plug)
# Example: ("TEX_roughness_map", "outColorR")
ShaderAttrSlot = Optional[Tuple[str, str]]

ShaderAttrs = namedtuple(
    "ShaderAttrs",
    [
        "baseColor",  # Albedo/Diffuse/Base Color
        "emission",  # Emissive/Glow
        "specular",  # Specular/F0
        "roughness",  # Roughness/Gloss/Eccentricity
        "metallic",  # Metalness/Metallic
        "opacity",  # Opacity/Transparency
        "normal",  # Normal/Bump
        "ambientOcclusion",  # Ambient Occlusion
    ],
)


class _ShaderAttributeMapInternal(object):
    """Internal helpers for ShaderAttributeMap (Maya-side plug mechanics)."""

    @staticmethod
    def _connect_plug(src_plug: str, node: str, attr: str) -> bool:
        """Connect *src_plug* -> ``node.attr``, reconciling a mismatched arity.

        The declared output plug and the destination attribute do not always
        share an arity, and Maya rejects the connection outright when they
        differ ("Data types of source and destination are not compatible").
        The channel that exposes this is **opacity**: every PBR shader here
        declares it as ``outAlpha`` (opacity IS the image's alpha), while the
        attribute itself is a ``float3`` -- ``standardSurface.opacity``,
        ``openPBRSurface.geometryOpacity``, ``lambert.transparency``.

        Substituting a different SOURCE plug to make the types line up (the old
        blanket ``outColor`` fallback) silently changes WHICH DATA flows: it
        wired a texture's RGB into opacity, so color drove transparency. The
        arity is the thing to fix, not the channel.

        Direct first, so a compatible declaration (``outColor`` -> a color slot)
        connects as one plug; only the mismatch falls through to
        :meth:`MatUtils.connect_to_channels`, the established single-channel
        -> compound primitive (it also breaks any existing parent input, so the
        parent and its children can never end up driven by two textures).
        """
        import maya.cmds as cmds

        from mayatk.mat_utils._mat_utils import MatUtils

        try:
            cmds.connectAttr(src_plug, f"{node}.{attr}", force=True)
            return True
        except RuntimeError:
            # A single-channel source into a compound slot -- drive each child.
            return MatUtils.connect_to_channels(src_plug, node, attr)

    @staticmethod
    def _prepare_alpha_source(file_node: str) -> None:
        """Make the alpha-derived plugs meaningful for an image with no alpha.

        A file node's ``outAlpha`` is a constant 1.0 when the image has no alpha
        channel (and ``outTransparency``, being ``1 - outAlpha``, a constant 0),
        so a grayscale Opacity (or Roughness / Metallic) map wired from
        it does NOTHING -- fully opaque geometry, with every connection present
        and correct-looking. ``alphaIsLuminance`` derives alpha from luminance
        instead; this is the same convention ``GameShader`` applies when it
        creates those file nodes itself (``alphaIsLuminance=1`` for Roughness /
        Metallic / Opacity, and deliberately ``0`` for ``Metallic_Smoothness``,
        whose alpha is real). Best-effort: an unloadable texture must not cost
        the caller the connection.
        """
        import maya.cmds as cmds

        try:
            if cmds.getAttr(f"{file_node}.fileHasAlpha"):
                return  # a real alpha channel -- never override it
            cmds.setAttr(f"{file_node}.alphaIsLuminance", True)
        except (RuntimeError, ValueError):
            pass


class ShaderAttributeMap(_ShaderAttributeMapInternal):
    """
    Central mapping of logical texture/material channels to per-shader attribute/plug pairs.
    Extend by adding new shader types or logical channels as needed.
    """

    SHADER_TYPES = [
        "lambert",
        "blinn",
        "phong",
        "aiStandardSurface",
        "standardSurface",
        "StingrayPBS",
        "openPBRSurface",
    ]

    # Output plugs a file node derives from its ALPHA. Both need
    # :meth:`_prepare_alpha_source` when the image carries no alpha channel --
    # ``outTransparency`` is just ``1 - outAlpha``, so it inherits the same
    # constant-1.0 (fully transparent, in its sense) failure.
    ALPHA_DERIVED_PLUGS = ("outAlpha", "outTransparency")

    SHADER_ATTRS: Dict[str, ShaderAttrs] = {
        # The classic shaders express the channel INVERTED, as ``transparency``
        # (0 = opaque), so they read ``outTransparency`` -- the file node's own
        # ``1 - alpha``, already a float3 to match the attribute. Declaring
        # ``outColor`` here (as this did) wired the image's RGB into
        # transparency: the alpha was ignored entirely and a white opacity map
        # made the surface fully SEE-THROUGH rather than fully opaque.
        "lambert": ShaderAttrs(
            baseColor=("color", "outColor"),
            emission=("incandescence", "outColor"),
            specular=None,
            roughness=None,
            metallic=None,
            opacity=("transparency", "outTransparency"),
            normal=None,
            ambientOcclusion=None,
        ),
        "blinn": ShaderAttrs(
            baseColor=("color", "outColor"),
            emission=None,
            specular=("specularColor", "outColor"),
            roughness=("eccentricity", "outColorR"),  # expects .outColorR from file
            metallic=None,
            opacity=("transparency", "outTransparency"),
            normal=("normalCamera", "outColor"),
            ambientOcclusion=None,
        ),
        "phong": ShaderAttrs(
            baseColor=("color", "outColor"),
            emission=("incandescence", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("cosinePower", "outColorR"),
            metallic=None,
            opacity=("transparency", "outTransparency"),
            normal=("normalCamera", "outColor"),
            ambientOcclusion=None,
        ),
        "aiStandardSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outAlpha"),
            metallic=("metalness", "outAlpha"),
            opacity=("opacity", "outAlpha"),
            normal=(
                "normalCamera",
                "outColor",
            ),  # connect to aiNormalMap.outValue, not file.outColor
            ambientOcclusion=None,
        ),
        "standardSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outAlpha"),
            metallic=("metalness", "outAlpha"),
            opacity=("opacity", "outAlpha"),
            normal=("normalCamera", "outColor"),
            ambientOcclusion=None,
        ),
        "StingrayPBS": ShaderAttrs(
            baseColor=("TEX_color_map", "outColor"),
            emission=("TEX_emissive_map", "outColor"),
            specular=None,
            roughness=("TEX_roughness_map", "outColorR"),
            metallic=("TEX_metallic_map", "outColorR"),
            # NOT "TEX_opacity_map" -- verified live against Maya 2025: neither
            # ShaderFX graph exposes that attribute. Standard_Transparent.sfx
            # carries a SCALAR "opacity" (plus the "use_opacity_map" toggle);
            # Standard.sfx has no opacity slot at all, which get_attr callers
            # handle as a missing attribute.
            opacity=("opacity", "outAlpha"),
            normal=("TEX_normal_map", "outColor"),
            ambientOcclusion=("TEX_ao_map", "outColor"),
        ),
        "openPBRSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outAlpha"),
            metallic=("baseMetalness", "outAlpha"),
            opacity=("geometryOpacity", "outAlpha"),
            normal=("geometryNormal", "outColor"),
            ambientOcclusion=None,
        ),
    }

    @classmethod
    def logical_channels(cls) -> Tuple[str, ...]:
        """Returns the logical channel names as a tuple."""
        return ShaderAttrs._fields

    @classmethod
    def get_attr(cls, shader_type: str, logical: str) -> Optional[Tuple[str, str]]:
        """Return (attribute, plug) tuple for shader type and logical channel, or None."""
        attrs = cls.SHADER_ATTRS.get(shader_type)
        if attrs is None or logical not in cls.logical_channels():
            return None
        return getattr(attrs, logical)

    @classmethod
    def get_mapping(
        cls, src_type: str, dst_type: str
    ) -> Tuple[Tuple[str, str, str], ...]:
        """
        Returns a tuple of (src_attr, src_plug, dst_attr) for each logical channel present in both shader types.
        """
        src_attrs = cls.SHADER_ATTRS.get(src_type)
        dst_attrs = cls.SHADER_ATTRS.get(dst_type)
        if not src_attrs or not dst_attrs:
            return tuple()
        pairs = []
        for logical in cls.logical_channels():
            src_info = getattr(src_attrs, logical)
            dst_info = getattr(dst_attrs, logical)
            if src_info and dst_info:
                src_attr, src_plug = src_info
                dst_attr, _ = dst_info  # (dest plug is for future-proofing if needed)
                pairs.append((src_attr, src_plug, dst_attr))
        return tuple(pairs)

    @classmethod
    def connect_channel(
        cls,
        file_node: str,
        logical: str,
        shader: str,
        shader_type: Optional[str] = None,
    ) -> bool:
        """Wire *file_node* into *shader*'s *logical* channel as this map declares.

        The one place that turns a ``(attribute, output plug)`` declaration into
        real connections, so every caller -- the ``GameShader`` network build and
        the bridge's manifest replay (:meth:`MatManifest.restore`) -- binds a
        channel identically instead of each re-deriving the plug mechanics.

        Handles the two ways a declaration and a live attribute disagree:
        arity (see :meth:`_connect_plug`) and an alpha source on an image with no
        alpha channel (see :meth:`_prepare_alpha_source`). Returns True only when
        the channel is actually driven; an unmapped channel, a missing attribute
        (a StingrayPBS graph need not expose every slot) or a failed connection
        all return False rather than raising.
        """
        import maya.cmds as cmds

        if not shader_type:
            try:
                shader_type = cmds.nodeType(shader)
            except RuntimeError:
                return False
        slot = cls.resolve_live_slot(shader, logical, shader_type)
        if not slot:
            return False
        attr, plug = slot
        if plug in cls.ALPHA_DERIVED_PLUGS:
            cls._prepare_alpha_source(file_node)
        if not cls._connect_plug(f"{file_node}.{plug}", shader, attr):
            return False
        cls._enable_map_toggle(shader, attr)
        return True

    # Fallback slots for a logical channel when the DECLARED one is absent from
    # the live node. Only StingrayPBS needs this: its attributes come from the
    # loaded ShaderFX graph, so one node type has three different opacity
    # answers -- `Standard_Transparent.sfx` the scalar `opacity` declared above,
    # `Standard_Masked.sfx` a float3 `TEX_mask_map` (alpha cutout), and
    # `Standard.sfx` neither. Declaring only the first silently dropped the
    # channel on every masked material.
    SLOT_ALTERNATES: Dict[Tuple[str, str], Tuple[Tuple[str, str], ...]] = {
        ("StingrayPBS", "opacity"): (("TEX_mask_map", "outAlpha"),),
    }

    # Slots whose toggle the naming rule below gets WRONG. Probed live against
    # Maya 2025: ``Standard_Masked.sfx`` exposes ``TEX_mask_map`` but gates it
    # behind ``use_opacity_map`` -- there is no ``use_mask_map``, so the derived
    # name silently no-ops and leaves the cutout connected but inert.
    _TOGGLE_OVERRIDES = {"TEX_mask_map": "use_opacity_map"}

    @classmethod
    def resolve_live_slot(
        cls, shader: str, logical: str, shader_type: Optional[str] = None
    ) -> ShaderAttrSlot:
        """The ``(attribute, plug)`` for *logical* that this NODE actually has.

        :meth:`get_attr` answers per shader TYPE, which is enough for every
        shader whose attributes are fixed. A StingrayPBS's are not — they come
        from its loaded ShaderFX graph — so the declared slot may simply not
        exist on the node in front of you. Falls back through
        :attr:`SLOT_ALTERNATES` before giving up.

        Parameters:
            shader (str): The live shader node.
            logical (str): Logical channel name.
            shader_type (str, optional): Skips the ``nodeType`` lookup.

        Returns:
            tuple | None: ``(attribute, plug)``, or None when the node exposes
            no slot for this channel.
        """
        import maya.cmds as cmds

        if not shader_type:
            try:
                shader_type = cmds.nodeType(shader)
            except RuntimeError:
                return None

        candidates = []
        declared = cls.get_attr(shader_type, logical)
        if declared:
            candidates.append(declared)
        candidates.extend(cls.SLOT_ALTERNATES.get((shader_type, logical), ()))

        for attr, plug in candidates:
            if cmds.objExists(f"{shader}.{attr}"):
                return (attr, plug)
        return None

    @classmethod
    def map_toggle_attr(cls, attr: str) -> str:
        """The ``use_*`` companion ShaderFX pairs with slot *attr*.

        ShaderFX's own naming rule, in one place because both routes into a
        StingrayPBS need it: the network build (``GameShader._wire``) and the
        bridge's manifest replay (:meth:`connect_channel`). Two shapes exist --
        the texture slots (``TEX_color_map`` -> ``use_color_map``) and the
        scalar ``opacity`` slot, whose toggle is ``use_opacity_map`` and which a
        naive ``TEX_`` substitution silently leaves as ``opacity``.

        Answers for any attribute; whether the toggle EXISTS is the caller's
        probe (a graph exposes only its own slots, and non-ShaderFX shaders have
        none of this).
        """
        if attr in cls._TOGGLE_OVERRIDES:
            return cls._TOGGLE_OVERRIDES[attr]
        if attr.startswith("TEX_"):
            return attr.replace("TEX_", "use_", 1)
        return f"use_{attr}_map"

    @classmethod
    def _enable_map_toggle(cls, shader: str, attr: str) -> bool:
        """Switch on the ``use_*`` companion of a just-connected slot, if any.

        A ShaderFX slot is INERT until its toggle is set: a StingrayPBS with a
        file wired into ``TEX_color_map`` and ``use_color_map`` still 0 renders
        the flat base color, so the texture is connected AND invisible -- the
        worst failure mode to debug, because the graph looks right.

        Shaders outside that family expose no such attribute and are left
        untouched. Best-effort: the connection has already succeeded either way.
        """
        import maya.cmds as cmds

        toggle = cls.map_toggle_attr(attr)
        try:
            if not cmds.attributeQuery(toggle, node=str(shader), exists=True):
                return False
            cmds.setAttr(f"{shader}.{toggle}", 1)
            return True
        except RuntimeError:
            return False

    @classmethod
    def add_shader_type(cls, shader_type: str, attrs: ShaderAttrs) -> None:
        """Add a new shader type mapping."""
        cls.SHADER_ATTRS[shader_type] = attrs

    @classmethod
    def update_attr(
        cls, shader_type: str, logical: str, value: Optional[Tuple[str, str]]
    ) -> None:
        """Update a logical channel mapping for a shader type."""
        attrs = cls.SHADER_ATTRS.get(shader_type)
        if not attrs or logical not in cls.logical_channels():
            return
        cls.SHADER_ATTRS[shader_type] = attrs._replace(**{logical: value})

    @classmethod
    def as_dict(cls) -> Dict[str, Dict[str, Any]]:
        """Returns a dict of dicts for all shader mappings."""
        return {
            stype: dict(attrs._asdict()) for stype, attrs in cls.SHADER_ATTRS.items()
        }


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
