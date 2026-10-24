# !/usr/bin/python
# coding=utf-8
"""Logical texture channel -> per-shader (attribute, output plug), and the one
connector that honors that declaration against a real Maya attribute.

``import maya.cmds`` is deferred into the connector's call bodies so the map
itself stays importable (and testable) without a running Maya.
"""

import math
from typing import Optional, Tuple, Dict, Any, Callable
from collections import namedtuple

import pythontk as ptk

# Each slot is: (attribute_name, output_plug)
# Example: ("TEX_roughness_map", "outColorR")
ShaderAttrSlot = Optional[Tuple[str, str]]

_CORE_CHANNELS = (
    "baseColor",  # Albedo/Diffuse/Base Color
    "emission",  # Emissive/Glow
    "specular",  # Specular/F0 -- the specular colour (tint) on the PBR surfaces
    "roughness",  # Roughness/Gloss/Eccentricity
    "metallic",  # Metalness/Metallic
    "opacity",  # Opacity/Transparency
    "normal",  # Normal/Bump
    "ambientOcclusion",  # Ambient Occlusion
)

#: The OpenPBR lobe channels: pythontk's ``MapRegistry.LOBE_TYPE_CHANNELS`` (the
#: shared vocabulary blendertk wires Principled from), less the core channel the
#: specular tint shares. Derived, not listed, so a lobe pythontk adds is a field
#: here with no edit. Only the physically-based surfaces declare them, so they
#: default to None and the classic shaders need not spell them out.
LOBE_CHANNELS = tuple(
    channel
    for channel in dict.fromkeys(ptk.MapRegistry.LOBE_TYPE_CHANNELS.values())
    if channel not in _CORE_CHANNELS
)

ShaderAttrs = namedtuple(
    "ShaderAttrs",
    [*_CORE_CHANNELS, *LOBE_CHANNELS],
    defaults=(None,) * len(LOBE_CHANNELS),
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
        ``lambert.transparency`` (``openPBRSurface.geometryOpacity`` is a scalar
        and connects directly).

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
    def _apply_gain(file_node: str, gain: float) -> None:
        """Scale what *file_node* outputs by *gain* -- colour and alpha alike.

        Set, never multiplied, so re-wiring the same file (a converter or
        manifest replay of a network this built) cannot compound it.
        Best-effort: a locked or connected gain keeps the connection.
        """
        import maya.cmds as cmds

        try:
            cmds.setAttr(f"{file_node}.colorGain", gain, gain, gain, type="double3")
            cmds.setAttr(f"{file_node}.alphaGain", gain)
        except (RuntimeError, ValueError):
            pass

    @staticmethod
    def _prepare_alpha_source(file_node: str) -> None:
        """Make the alpha-derived plugs meaningful for an image with no alpha.

        A file node's ``outAlpha`` is a constant 1.0 when the image has no alpha
        channel (and ``outTransparency``, being ``1 - outAlpha``, a constant 0),
        so a grayscale Opacity map wired from it does NOTHING -- fully opaque
        geometry, with every connection present and correct-looking.
        ``alphaIsLuminance`` derives alpha from luminance instead; this is the
        same convention ``GameShader`` applies when it creates those file nodes
        itself (``alphaIsLuminance=1`` for Opacity, and deliberately ``0`` for
        ``Metallic_Smoothness``, whose alpha is real). Opacity is the only
        channel declared on an alpha plug -- the scalar PBR maps read
        ``outColorR``. Best-effort: an unloadable texture must not cost the
        caller the connection.
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
        "phongE",
        "aiStandardSurface",
        "standardSurface",
        "StingrayPBS",
        "openPBRSurface",
        "surfaceShader",
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
        #
        # ``incandescence`` and ``normalCamera`` are lambert's own attributes,
        # inherited by blinn, phong and phongE: each declares both (a ``None``
        # here dropped a lambert's normal map on a retype).
        "lambert": ShaderAttrs(
            baseColor=("color", "outColor"),
            emission=("incandescence", "outColor"),
            specular=None,
            roughness=None,
            metallic=None,
            opacity=("transparency", "outTransparency"),
            normal=("normalCamera", "outColor"),
            ambientOcclusion=None,
        ),
        "blinn": ShaderAttrs(
            baseColor=("color", "outColor"),
            emission=("incandescence", "outColor"),
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
        # Phong E keeps its highlight in its own ``roughness`` (0..1, default
        # 0.5) -- a width, as blinn's ``eccentricity`` is, so it reads as a
        # roughness with no transform (``highlightSize`` and ``whiteness`` shape
        # the highlight; no channel holds them). Undeclared, a textured phongE
        # named no map: the materials record states the type, and a map it
        # leaves out is CLEARED from the GLB and Unity's Deliverable Lit.
        "phongE": ShaderAttrs(
            baseColor=("color", "outColor"),
            emission=("incandescence", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("roughness", "outColorR"),
            metallic=None,
            opacity=("transparency", "outTransparency"),
            normal=("normalCamera", "outColor"),
            ambientOcclusion=None,
        ),
        # The PBR trio reads its scalar maps off ``outColorR``, never the alpha:
        # a grey map carries its value in every channel, and a PACKED one keeps
        # the scalar in RGB and something else in A -- Unity's
        # MetallicSmoothness puts smoothness there, so ``outAlpha`` turned a
        # non-metal ~78% metal (a transferred table rendered at a third of its
        # source's brightness). R is also the channel StingrayPBS samples.
        #
        # The lobes: Standard Surface (and Arnold's, which shares its names --
        # probed on Maya 2025.3 + MtoA) carries every OpenPBR lobe but thin
        # film's weight. ``subsurfaceRadius`` is OpenPBR's SCALAR radius, which
        # is Standard Surface's ``subsurfaceScale``; its float3
        # ``subsurfaceRadius`` is OpenPBR's ``subsurfaceRadiusScale``. Thin film
        # stays undeclared: Standard Surface measures it in nanometres with no
        # weight (thickness 0 is off), so OpenPBR's default 0.5 um film -- off
        # only by its weight -- would arrive switched on.
        "aiStandardSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outColorR"),
            metallic=("metalness", "outColorR"),
            opacity=("opacity", "outAlpha"),
            normal=(
                "normalCamera",
                "outColor",
            ),  # connect to aiNormalMap.outValue, not file.outColor
            ambientOcclusion=None,
            diffuseRoughness=("diffuseRoughness", "outColorR"),
            specularWeight=("specular", "outColorR"),
            anisotropy=("specularAnisotropy", "outColorR"),
            transmission=("transmission", "outColorR"),
            transmissionColor=("transmissionColor", "outColor"),
            subsurface=("subsurface", "outColorR"),
            subsurfaceColor=("subsurfaceColor", "outColor"),
            subsurfaceRadius=("subsurfaceScale", "outColorR"),
            coat=("coat", "outColorR"),
            coatColor=("coatColor", "outColor"),
            coatRoughness=("coatRoughness", "outColorR"),
            coatNormal=("coatNormal", "outColor"),
            sheen=("sheen", "outColorR"),
            sheenColor=("sheenColor", "outColor"),
            sheenRoughness=("sheenRoughness", "outColorR"),
        ),
        "standardSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outColorR"),
            metallic=("metalness", "outColorR"),
            opacity=("opacity", "outAlpha"),
            normal=("normalCamera", "outColor"),
            ambientOcclusion=None,
            diffuseRoughness=("diffuseRoughness", "outColorR"),
            specularWeight=("specular", "outColorR"),
            anisotropy=("specularAnisotropy", "outColorR"),
            transmission=("transmission", "outColorR"),
            transmissionColor=("transmissionColor", "outColor"),
            subsurface=("subsurface", "outColorR"),
            subsurfaceColor=("subsurfaceColor", "outColor"),
            subsurfaceRadius=("subsurfaceScale", "outColorR"),
            coat=("coat", "outColorR"),
            coatColor=("coatColor", "outColor"),
            coatRoughness=("coatRoughness", "outColorR"),
            coatNormal=("coatNormal", "outColor"),
            sheen=("sheen", "outColorR"),
            sheenColor=("sheenColor", "outColor"),
            sheenRoughness=("sheenRoughness", "outColorR"),
        ),
        "StingrayPBS": ShaderAttrs(
            baseColor=("TEX_color_map", "outColor"),
            emission=("TEX_emissive_map", "outColor"),
            specular=None,
            # ``outColor`` (compound), NOT ``outColorR``: a ShaderFX ``TEX_*``
            # slot only binds a texture sampler when its COMPOUND plug is
            # driven by the file node. A scalar source can only be wired per
            # child (``TEX_metallic_mapX/Y/Z``), and VP2 then samples NOTHING
            # -- probed with ``ogsRender`` on Maya 2025: a per-child wired
            # colour map renders identically to no map. Grayscale roughness /
            # metallic maps carry the value in every channel, so the compound
            # connection reads the same data (and is what GameShader and
            # Painter-exported scenes wire). A per-child restore is the bug
            # that made transferred Stingray materials lose their metallic.
            roughness=("TEX_roughness_map", "outColor"),
            metallic=("TEX_metallic_map", "outColor"),
            # NOT "TEX_opacity_map" -- verified live against Maya 2025: neither
            # ShaderFX graph exposes that attribute. Standard_Transparent.sfx
            # carries a SCALAR "opacity" (plus the "use_opacity_map" toggle);
            # Standard.sfx has no opacity slot at all, which get_attr callers
            # handle as a missing attribute.
            opacity=("opacity", "outAlpha"),
            normal=("TEX_normal_map", "outColor"),
            ambientOcclusion=("TEX_ao_map", "outColor"),
        ),
        # Every OpenPBR lobe, named as the spec names it (probed on Maya
        # 2025.3). ``geometryOpacity`` is a scalar there, not a colour.
        "openPBRSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outColorR"),
            metallic=("baseMetalness", "outColorR"),
            opacity=("geometryOpacity", "outAlpha"),
            normal=("geometryNormal", "outColor"),
            ambientOcclusion=None,
            diffuseRoughness=("baseDiffuseRoughness", "outColorR"),
            specularWeight=("specularWeight", "outColorR"),
            anisotropy=("specularRoughnessAnisotropy", "outColorR"),
            transmission=("transmissionWeight", "outColorR"),
            transmissionColor=("transmissionColor", "outColor"),
            subsurface=("subsurfaceWeight", "outColorR"),
            subsurfaceColor=("subsurfaceColor", "outColor"),
            subsurfaceRadius=("subsurfaceRadius", "outColorR"),
            coat=("coatWeight", "outColorR"),
            coatColor=("coatColor", "outColor"),
            coatRoughness=("coatRoughness", "outColorR"),
            coatNormal=("geometryCoatNormal", "outColor"),
            sheen=("fuzzWeight", "outColorR"),
            sheenColor=("fuzzColor", "outColor"),
            sheenRoughness=("fuzzRoughness", "outColorR"),
            thinFilm=("thinFilmWeight", "outColorR"),
            thinFilmThickness=("thinFilmThickness", "outColorR"),
        ),
        # Maya's unlit model: the colour IS what shows, and transparency is
        # the inverted channel, as on the classic shaders. Declared so a
        # texture on it is a base colour to the manifest and the materials
        # record (which states it unlit -- ``MaterialRecords``).
        "surfaceShader": ShaderAttrs(
            baseColor=("outColor", "outColor"),
            emission=None,
            specular=None,
            roughness=None,
            metallic=None,
            opacity=("outTransparency", "outTransparency"),
            normal=None,
            ambientOcclusion=None,
        ),
    }

    #: Channels whose input is a VECTOR (a tangent-space normal), never a
    #: value: an undriven one means the surface's own normal, and its literal
    #: (``normalCamera`` reads (1, 1, 1), ``geometryCoatNormal`` (0, 0, 0)) is
    #: no tangent-space value. See :meth:`constant_attr`.
    VECTOR_CHANNELS = ("normal", "coatNormal")

    #: ``(shader type, logical channel) -> (map type, gain)``: a map TYPE whose
    #: texels are authored on a different scale from the slot. A
    #: ``Specular_Level`` map holds the authored level, 0.5 the neutral 4% F0
    #: (Substance, Blender, Unreal -- see ``ptk.MapRegistry``), while these
    #: shaders' specular WEIGHT is neutral at 1.0: wired raw, every level map
    #: halved the reflections. Keyed by the map type, not the slot alone, so a
    #: file already in weight units -- a hand-wired or legacy map, which does
    #: not classify as a level -- keeps its own gain: a retype, a manifest
    #: replay or a transfer re-wires it unchanged instead of doubling it.
    #: Applied as the file node's own colour/alpha gain, so the slot stays
    #: driven by the file directly -- the converter's tracer and the FBX writer
    #: both read a texture only one hop upstream.
    TEXTURE_GAINS: Dict[Tuple[str, str], Tuple[str, float]] = {
        ("standardSurface", "specularWeight"): ("Specular_Level", 2.0),
        ("aiStandardSurface", "specularWeight"): ("Specular_Level", 2.0),
        ("openPBRSurface", "specularWeight"): ("Specular_Level", 2.0),
    }

    @classmethod
    def _texture_gain(
        cls, file_node: str, shader_type: str, logical: str
    ) -> Optional[float]:
        """The :attr:`TEXTURE_GAINS` gain *file_node*'s texture takes into
        *logical* on *shader_type*: only when its filename classifies as the
        declared map type, else None (its own gain stands)."""
        import maya.cmds as cmds

        entry = cls.TEXTURE_GAINS.get((shader_type, logical))
        if entry is None:
            return None
        map_type, gain = entry
        try:
            path = cmds.getAttr(f"{file_node}.fileTextureName") or ""
        except (RuntimeError, ValueError):
            return None
        return gain if ptk.MapFactory.resolve_map_type(path) == map_type else None

    @classmethod
    def texture_scale(
        cls, shader: str, logical: str, shader_type: Optional[str] = None
    ) -> Optional[float]:
        """Factor that brings the texels driving *logical* into the units of
        its declared :attr:`TEXTURE_GAINS` map type, or None when they already
        are (or nothing declares one).

        The file node's own gain is what the slot reads, so texels times that
        gain are the slot's value; a map written as the declared type is read
        back through the declared gain. A ``Specular_Level`` source (gain 2)
        is already in level units; a weight-unit map (gain 1) is 1/2 -- carried
        unscaled into a level map, its re-wire doubled it. For whoever re-bakes
        the texels into a new map: the UV transfer.

        Parameters:
            shader (str): The live shader node.
            logical (str): Logical channel name.
            shader_type (str, optional): Skips the ``nodeType`` lookup.

        Returns:
            float | None: ``own gain / declared gain``.
        """
        import maya.cmds as cmds

        from mayatk.mat_utils._mat_utils import MatUtils

        if not shader_type:
            try:
                shader_type = cmds.nodeType(shader)
            except RuntimeError:
                return None
        entry = cls.TEXTURE_GAINS.get((shader_type, logical))
        slot = cls.resolve_live_slot(shader, logical, shader_type) if entry else None
        file_node = MatUtils.get_texture_file_node(shader, slot[0]) if slot else None
        if not file_node:
            return None
        try:
            own = float(cmds.getAttr(f"{file_node}.colorGain")[0][0])
        except (RuntimeError, ValueError, TypeError, IndexError):
            return None
        scale = own / entry[1]
        return None if abs(scale - 1.0) < 1e-6 else scale

    #: The boolean that makes a surface thin-walled (a cutout leaf, not a
    #: solid): every PBR builder sets it for an opacity map. OpenPBR spells it
    #: ``geometryThinWalled``, so a builder probing only ``thinWalled`` left
    #: every retyped openPBR solid.
    THIN_WALLED_ATTRS: Dict[str, str] = {
        "standardSurface": "thinWalled",
        "aiStandardSurface": "thinWalled",
        "openPBRSurface": "geometryThinWalled",
    }

    @classmethod
    def set_thin_walled(cls, shader: str, shader_type: Optional[str] = None) -> bool:
        """Make *shader* thin-walled, if its type has the switch.

        Parameters:
            shader (str): The live shader node.
            shader_type (str, optional): Skips the ``nodeType`` lookup.

        Returns:
            bool: True when the attribute was set.
        """
        import maya.cmds as cmds

        if not shader_type:
            try:
                shader_type = cmds.nodeType(shader)
            except RuntimeError:
                return False
        attr = cls.THIN_WALLED_ATTRS.get(shader_type)
        if not attr or not cmds.attributeQuery(attr, node=str(shader), exists=True):
            return False
        cmds.setAttr(f"{shader}.{attr}", True)
        return True

    @classmethod
    def open_lobe_gate(
        cls, shader: str, logical: str, shader_type: Optional[str] = None
    ) -> bool:
        """Switch on the lobe a just-wired *logical* map belongs to.

        Every lobe weight defaults to 0, so a coat colour, roughness or normal
        on an untouched coat renders nothing (see ``ptk.MapRegistry.LOBE_GATES``).
        Opens the weight at 1.0 -- unless a map already drives it, or it was
        already above 0 (an authored weight is the user's, not a default).

        Parameters:
            shader (str): The live shader node.
            logical (str): The channel that was just wired.
            shader_type (str, optional): Skips the ``nodeType`` lookup.

        Returns:
            bool: True when a weight was opened.
        """
        gate = ptk.MapRegistry.LOBE_GATES.get(logical)
        if not gate:
            return False
        slot = cls.resolve_live_slot(shader, gate, shader_type)
        if not slot:
            return False
        return cls._open_weight(f"{shader}.{slot[0]}")

    @staticmethod
    def _open_weight(plug: str) -> bool:
        """Set an undriven weight *plug* sitting at 0 to 1.0; True when it did.

        The one rule both gate openers share: a map on the weight or a value
        above 0 is the user's, never a default to overwrite.
        """
        import maya.cmds as cmds

        try:
            if not cmds.objExists(plug):
                return False
            if cmds.listConnections(plug, source=True, destination=False):
                return False
            if cmds.getAttr(plug):
                return False
            cmds.setAttr(plug, 1.0)
            return True
        except (RuntimeError, ValueError):
            return False

    @classmethod
    def slot_attrs(cls, shader_type: str) -> Tuple[str, ...]:
        """Every attribute a texture may drive on *shader_type*.

        The declared slots plus their :attr:`SLOT_ALTERNATES`, de-duplicated
        in declaration order -- the teardown list for a rewire, so a channel
        cannot be wired by the map and left connected by the teardown.

        Parameters:
            shader_type (str): Maya node type.

        Returns:
            tuple: Attribute names; empty for an undeclared type.
        """
        attrs = cls.SHADER_ATTRS.get(shader_type)
        if attrs is None:
            return ()
        names = []
        for logical in cls.logical_channels():
            slot = getattr(attrs, logical)
            if slot:
                names.append(slot[0])
            for alternate, _ in cls.SLOT_ALTERNATES.get((shader_type, logical), ()):
                names.append(alternate)
        return tuple(dict.fromkeys(names))

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

    # Per shader type, the attribute holding a channel's value when no map is
    # wired, where that is NOT the slot itself. StingrayPBS keeps its uniforms
    # apart from its ``TEX_*`` samplers, and a sampler's own literal is no value
    # at all: an undriven ``TEX_color_map`` reads (0, 0, 0).
    CONSTANT_ATTRS: Dict[str, Dict[str, str]] = {
        "StingrayPBS": {
            "baseColor": "base_color",
            "emission": "emissive",
            "roughness": "roughness",
            "metallic": "metallic",
            "opacity": "opacity",
        },
    }

    @classmethod
    def constant_attr(cls, shader_type: str, logical: str) -> Optional[str]:
        """The attribute holding *logical*'s value on an unmapped *shader_type*.

        :attr:`CONSTANT_ATTRS` where the type keeps it apart, else the declared
        slot. None where no attribute holds a VALUE: a ShaderFX ``TEX_*``
        sampler with no uniform (``TEX_ao_map``, ``TEX_normal_map``), and the
        :attr:`VECTOR_CHANNELS` everywhere -- an undriven normal input means the
        surface's own normal, and its literal (standardSurface's
        ``normalCamera`` is (1, 1, 1)) is not a tangent-space value.

        Parameters:
            shader_type (str): Maya node type.
            logical (str): Logical channel name.

        Returns:
            str | None: The attribute name.
        """
        if logical in cls.VECTOR_CHANNELS:
            return None
        attr = cls.CONSTANT_ATTRS.get(shader_type, {}).get(logical)
        if attr:
            return attr
        slot = cls.get_attr(shader_type, logical)
        if not slot or slot[0].startswith("TEX_"):
            return None
        return slot[0]

    #: ``(shader type, logical channel) -> f(component)`` for a literal that is
    #: not in the channel's own units. phong's roughness slot is
    #: ``cosinePower``, a Blinn-Phong EXPONENT (default 20): read raw, a phong
    #: source wrote 20 into a 0..1 roughness -- fully rough. It reads as the
    #: Beckmann width the exponent stands for, ``sqrt(2 / (n + 2))`` (0.30 at
    #: the default) -- the inverse of the Maya bridge's
    #: ``_roughness_to_cosine_power``, so a roughness survives a phong round trip.
    CONSTANT_TRANSFORMS: Dict[Tuple[str, str], Callable[[float], float]] = {
        ("phong", "roughness"): lambda power: math.sqrt(2.0 / (max(power, 0.0) + 2.0)),
    }

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
    EMISSION_WEIGHT_ATTRS: Dict[str, Tuple[str, str]] = {
        "aiStandardSurface": ("emission", "multiply"),
        "standardSurface": ("emission", "multiply"),
        "openPBRSurface": ("emissionLuminance", "gate"),
        # Autodesk's Stingray PBS graph multiplies its emissive by this uniform
        # (default 0 -- see `open_emission_gate`). Unread, a dimmed fixture
        # shipped at full glow: moody-room ceiling panels lit WebXR at 1.0
        # where the scene asked for less (2026-10-16). A graph without the
        # uniform reads ungated, as before.
        "StingrayPBS": ("emissive_intensity", "multiply"),
    }

    @classmethod
    def emission_weight(cls, shader: str, shader_type: Optional[str] = None) -> float:
        """The shader's separate emission scalar, or 1.0 when it has none.

        On standardSurface / aiStandardSurface the weight defaults to **0**
        while ``emissionColor`` defaults to white, so reading the colour alone
        reports a bright emissive on a material that renders black. A
        ``multiply`` weight is returned as is (above 1 it is a strength, not a
        clip); a ``gate`` returns 1.0 or 0.0.

        A shader absent from :attr:`EMISSION_WEIGHT_ATTRS` is **ungated** --
        returning 1.0 rather than hunting for a plausibly-named attribute,
        because a wrong guess here silently removes a working emissive.

        Parameters:
            shader (str): The live shader node.
            shader_type (str, optional): Skips the ``nodeType`` lookup.

        Returns:
            float: The weight.
        """
        import maya.cmds as cmds

        if not shader_type:
            try:
                shader_type = cmds.nodeType(shader)
            except RuntimeError:
                return 1.0
        entry = cls.EMISSION_WEIGHT_ATTRS.get(shader_type)
        if entry is None:
            return 1.0
        attr, mode = entry
        plug = f"{shader}.{attr}"
        if not cmds.objExists(plug):
            return 1.0
        try:
            value = float(cmds.getAttr(plug))
        except (RuntimeError, ValueError, TypeError):
            return 1.0
        if mode == "gate":
            return 1.0 if value > 0.0 else 0.0
        return value

    @classmethod
    def open_emission_gate(cls, shader: str, shader_type: Optional[str] = None) -> bool:
        """Switch on the emission weight of a shader that was just given a map.

        The emission twin of :meth:`open_lobe_gate`: a ``multiply`` weight in
        :attr:`EMISSION_WEIGHT_ATTRS` defaults to 0 (Stingray PBS's
        ``emissive_intensity`` included), so a wired emissive map renders --
        and exports -- as no emission. Opens it at 1.0 unless a map drives it
        or it is already above 0 (an authored weight is the user's). A
        ``gate`` weight is in physical units (OpenPBR nits) with no neutral
        1.0, so it is the caller's to choose.

        Parameters:
            shader (str): The live shader node.
            shader_type (str, optional): Skips the ``nodeType`` lookup.

        Returns:
            bool: True when a weight was opened.
        """
        import maya.cmds as cmds

        try:
            shader_type = shader_type or cmds.nodeType(shader)
        except RuntimeError:
            return False
        attr, mode = cls.EMISSION_WEIGHT_ATTRS.get(shader_type, (None, None))
        if mode != "multiply":
            return False
        return cls._open_weight(f"{shader}.{attr}")

    #: Shaders whose base colour is scaled by a SEPARATE weight --
    #: ``{node_type: attribute}``: the diffuse albedo is weight x colour.
    #: Maya's FBX exporter folds the legacy models' ``diffuse`` weight into
    #: their colour and maps these not at all, so a reader of the colour
    #: alone ran ``1 / weight`` too bright wherever an artist lowered it (the
    #: three default to 1.0, over a 0.8 colour).
    BASE_WEIGHT_ATTRS: Dict[str, str] = {
        "aiStandardSurface": "base",
        "standardSurface": "base",
        "openPBRSurface": "baseWeight",
    }

    @classmethod
    def base_weight(cls, shader: str, shader_type: Optional[str] = None) -> float:
        """The shader's base-colour weight, or 1.0 when it has none.

        A DRIVEN weight (a map on it) is 1.0 too: the FBX writes a map-driven
        scalar as 1, and unitytk's importer reads that, so the GLB carries the
        same rather than one frame's sample of the map.

        Parameters:
            shader (str): The live shader node.
            shader_type (str, optional): Skips the ``nodeType`` lookup.

        Returns:
            float: The weight.
        """
        import maya.cmds as cmds

        if not shader_type:
            try:
                shader_type = cmds.nodeType(shader)
            except RuntimeError:
                return 1.0
        attr = cls.BASE_WEIGHT_ATTRS.get(shader_type)
        plug = f"{shader}.{attr}" if attr else None
        if plug is None or not cmds.objExists(plug):
            return 1.0
        if cmds.listConnections(plug, source=True, destination=False):
            return 1.0
        try:
            return float(cmds.getAttr(plug))
        except (RuntimeError, ValueError, TypeError):
            return 1.0

    @classmethod
    def read_constant(
        cls,
        shader: str,
        logical: str,
        shader_type: Optional[str] = None,
        texture_units: bool = False,
    ) -> Optional[Tuple[float, ...]]:
        """*logical*'s literal on *shader*, in the channel's own terms.

        Read from :meth:`constant_attr`, so a value that is no value (a
        sampler's literal, an undriven normal) is None. Opacity reads as
        OPACITY (1 = opaque): the classic shaders store it inverted, as
        ``transparency`` (declared on ``outTransparency``), so their literal
        is flipped -- read raw, an untouched lambert was fully transparent.
        A literal in other units converts through
        :attr:`CONSTANT_TRANSFORMS` (phong's exponent to a roughness), and
        emission is scaled by the shader's :meth:`emission_weight` -- read
        alone, an untouched standardSurface's white ``emissionColor`` was a
        white emitter.

        Parameters:
            shader (str): The live shader node.
            logical (str): Logical channel name.
            shader_type (str, optional): Skips the ``nodeType`` lookup.
            texture_units (bool): The value as the channel's declared map
                type would hold it -- divided by its :attr:`TEXTURE_GAINS`
                gain, so a specular weight of 1.0 reads as the 0.5 a
                ``Specular_Level`` map (what the UV transfer writes) stores.
                For filling a texture beside real maps (the UV transfer's
                constant sources); a shader-to-shader copy wants the slot's
                own units, the default.

        Returns:
            tuple | None: The value as floats (a scalar is a 1-tuple).
        """
        import maya.cmds as cmds

        if not shader_type:
            try:
                shader_type = cmds.nodeType(shader)
            except RuntimeError:
                return None
        attr = cls.constant_attr(shader_type, logical)
        if not attr or not cmds.attributeQuery(attr, node=shader, exists=True):
            return None
        try:
            value = cmds.getAttr(f"{shader}.{attr}")
        except (RuntimeError, ValueError):
            return None
        # getAttr returns [(r, g, b)] for a float3; unwrap the outer list.
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        values = tuple(
            float(v) for v in (value if isinstance(value, (tuple, list)) else (value,))
        )
        if logical == "opacity" and cls.get_attr(shader_type, logical) == (
            attr,
            "outTransparency",
        ):
            values = tuple(1.0 - v for v in values)
        transform = cls.CONSTANT_TRANSFORMS.get((shader_type, logical))
        if transform is not None:
            values = tuple(transform(v) for v in values)
        if logical == "emission":
            weight = cls.emission_weight(shader, shader_type)
            values = tuple(v * weight for v in values)
        if texture_units:
            entry = cls.TEXTURE_GAINS.get((shader_type, logical))
            if entry:
                values = tuple(v / entry[1] for v in values)
        return values

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
        if logical == "opacity" and cls.select_color_alpha(shader, file_node):
            return True
        slot = cls.resolve_live_slot(shader, logical, shader_type)
        if not slot:
            return False
        attr, plug = slot
        if (shader_type, attr) in cls.UNIFORM_SLOTS:
            return False  # a texture on a uniform is a flat constant, not a map
        if plug in cls.ALPHA_DERIVED_PLUGS:
            cls._prepare_alpha_source(file_node)
        gain = cls._texture_gain(file_node, shader_type, logical)
        if gain is not None:
            cls._apply_gain(file_node, gain)
        if not cls._connect_plug(f"{file_node}.{plug}", shader, attr):
            return False
        cls._enable_map_toggle(shader, attr)
        return True

    # Fallback slots for a logical channel when the DECLARED one is absent from
    # the live node. StingrayPBS: its attributes come from the loaded ShaderFX
    # graph, so one node type has three different opacity answers --
    # `Standard_Transparent.sfx` the scalar `opacity` declared above,
    # `Standard_Masked.sfx` a float3 `TEX_mask_map` (alpha cutout), and
    # `Standard.sfx` neither. Declaring only the first silently dropped the
    # channel on every masked material.
    # ``outColor``, the COMPOUND plug: a ShaderFX sampler binds only through
    # it. The scalar ``outAlpha`` this used to declare can only land per child
    # (``TEX_mask_mapX/Y/Z``), and VP2 then reads an UNBOUND sampler -- 0 --
    # and discards every fragment. The masked graph reads the RED channel of
    # the bound texture (a grayscale opacity map carries its value there).
    # openPBRSurface: the spec's ``geometryNormal`` is what a newer Maya may
    # expose; Maya 2025's node has the classic ``normalCamera`` instead (probed
    # -- GameShader tries both), so the declaration alone dropped every normal.
    SLOT_ALTERNATES: Dict[Tuple[str, str], Tuple[Tuple[str, str], ...]] = {
        ("StingrayPBS", "opacity"): (("TEX_mask_map", "outColor"),),
        ("openPBRSurface", "normal"): (("normalCamera", "outColor"),),
    }

    # Slots that are shader UNIFORMS, never samplers. A texture connected to
    # ``Standard_Transparent.sfx``'s ``opacity`` evaluates to ONE value (the DG
    # samples the file node once), never per pixel -- and the graph's
    # ``use_opacity_map`` selector routes past it anyway. Verified live on
    # Maya 2025: renders with the map connected and disconnected are
    # byte-identical. Still READABLE for conversion (a legacy scene may carry
    # that connection, and the texture IS the opacity it meant), but never a
    # write target: the only per-pixel opacity on that graph is the colour
    # map's alpha, which is a packing job (``GameShader``), not a plug.
    UNIFORM_SLOTS = {("StingrayPBS", "opacity")}

    # Toggles the naming rule below gets WRONG -- ``(name, value)``, because on
    # the ShaderFX graphs ``use_opacity_map`` is not an enable but a SOURCE
    # SELECTOR: 1 reads the colour map's alpha channel, 0 the ``TEX_mask_map``
    # texture. Probed live against Maya 2025 (and Unity's Autodesk Interactive
    # Masked shader documents the same pair). There is no ``use_mask_map``, so
    # the derived name silently no-oped; naming the attribute but "enabling" it
    # to 1 pointed the graph at the colour map's (absent) alpha instead --
    # cutout connected, and every fragment kept.
    _TOGGLE_OVERRIDES = {"TEX_mask_map": ("use_opacity_map", 0)}

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
        return cls.map_toggle_state(attr)[0]

    @classmethod
    def map_toggle_state(cls, attr: str) -> Tuple[str, int]:
        """The ``use_*`` companion of slot *attr* AND the value that reads it.

        For every ``TEX_*`` slot the companion is an enable (1 = sample the
        map). ``TEX_mask_map`` is the exception: its companion is the shared
        ``use_opacity_map`` SELECTOR, and the mask map is read when it is 0 --
        see :attr:`_TOGGLE_OVERRIDES`. Callers that "switch on" a
        just-connected slot must set this value, not 1.

        Parameters:
            attr (str): The slot, e.g. ``"TEX_mask_map"``.

        Returns:
            tuple: ``(toggle attribute, value)``.
        """
        if attr in cls._TOGGLE_OVERRIDES:
            return cls._TOGGLE_OVERRIDES[attr]
        if attr.startswith("TEX_"):
            return attr.replace("TEX_", "use_", 1), 1
        return f"use_{attr}_map", 1

    @classmethod
    def select_color_alpha(cls, shader: str, file_node: str) -> bool:
        """Read the opacity from the colour map's alpha, if *file_node* IS it.

        On both ShaderFX opacity graphs the colour map's alpha channel is an
        opacity source in its own right, selected by ``use_opacity_map`` = 1 --
        and on ``Standard_Transparent.sfx`` it is the ONLY per-pixel one. So
        when the texture asked to drive opacity is the very file already bound
        to ``TEX_color_map`` (a material whose transparency came from its
        colour texture's alpha -- ``lambert.transparency <- file.outTransparency``
        -- converts this way), the answer is the selector, not a second bind:
        the masked graph's own sampler reads RED, not alpha, so binding the
        colour file there would mask on the wrong channel.

        Parameters:
            shader (str): StingrayPBS node (any other type has no selector).
            file_node (str): The file node offered as the opacity source.

        Returns:
            bool: True if the selector was set; False when *shader* has no
            selector or *file_node* is not its colour map -- the caller binds.
        """
        import maya.cmds as cmds

        try:
            if not cmds.attributeQuery(
                "use_opacity_map", node=str(shader), exists=True
            ):
                return False
            colour = (
                cmds.listConnections(
                    f"{shader}.TEX_color_map", source=True, destination=False
                )
                or []
            )
        except RuntimeError:
            return False
        if str(file_node) not in colour:
            return False
        cmds.setAttr(f"{shader}.use_opacity_map", 1)
        return True

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

        toggle, value = cls.map_toggle_state(attr)
        try:
            if not cmds.attributeQuery(toggle, node=str(shader), exists=True):
                return False
            cmds.setAttr(f"{shader}.{toggle}", value)
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
