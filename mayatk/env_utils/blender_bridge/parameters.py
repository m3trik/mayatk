# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Blender-bridge parameters exposed to the panel.

Each entry maps a placeholder token (e.g. ``__APPLY_UNIT_SCALE__``) to a widget spec. The slot
scans the selected template for these tokens, shows only the matching widgets, and substitutes the
user values into the template before launching Blender (via :func:`StrUtils.replace_delimited`).

Export-affecting knobs (``CARRIER`` / ``INCLUDE_MATERIALS`` / ``EMBED_TEXTURES`` /
``TRIANGULATE`` / ``INCLUDE_ANIMATION``) are read by :class:`BlenderBridge` to configure the
Maya-side export (FBX or USD per ``CARRIER``);
import-affecting knobs (``APPLY_UNIT_SCALE`` / ``INCLUDE_ANIMATION`` / ``CLEAR_SCENE`` /
``FRAME_VIEW``) are substituted into the Blender import template. Each template references the
subset it exposes.

To expose a new knob: add an entry below, then reference ``__YOUR_KEY__`` in any ``templates/*.py``.
Mirrors :mod:`mayatk.mat_utils.marmoset_bridge.parameters` so the slots class stays identical in
shape (and the blendertk ``maya_bridge`` counterpart mirrors this file).
"""

from __future__ import annotations

from typing import Any

from uitk.bridge import AttributeSpec, Formatters, Parameters as _BridgeParams

# Default VALUES live with the Qt-free engine so ``params_defaults()`` still answers
# where this module cannot be imported (a DCC running headless has no Qt); the specs
# below read them, so the two can never drift.
from mayatk.env_utils.blender_bridge._blender_bridge import DEFAULTS


# Templates are executable Blender Python -- substitute user values as Python source literals.
_FORMATTER = Formatters.python_literal


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    # Shared across every hand-off bridge (uitk owns the one spec);
    # resolved by the DCC bridge-slots base.
    "SCOPE": _BridgeParams.scope_spec(default=DEFAULTS["SCOPE"]),
    "CARRIER": _BridgeParams.carrier_spec(default=DEFAULTS["CARRIER"]),
    "INCLUDE_MATERIALS": AttributeSpec(
        key="INCLUDE_MATERIALS",
        label="Include Materials",
        kind="bool",
        default=DEFAULTS["INCLUDE_MATERIALS"],
        tooltip=(
            "Carry materials/shading across. When off, the selection is exported with only\n"
            "the default shader (materials stripped Maya-side); geometry only."
        ),
    ),
    "EMBED_TEXTURES": AttributeSpec(
        key="EMBED_TEXTURES",
        label="Embed Textures",
        kind="bool",
        default=DEFAULTS["EMBED_TEXTURES"],
        tooltip="Embed the texture files inside the FBX so Blender resolves the maps.",
    ),
    "APPLY_UNIT_SCALE": AttributeSpec(
        key="APPLY_UNIT_SCALE",
        label="Apply Unit Scale",
        kind="bool",
        default=DEFAULTS["APPLY_UNIT_SCALE"],
        tooltip=(
            "Convert Maya units (cm) to Blender units (m) on import so objects arrive at the\n"
            "correct real-world size. Off preserves the raw numeric values."
        ),
    ),
    "INCLUDE_ANIMATION": AttributeSpec(
        key="INCLUDE_ANIMATION",
        label="Include Animation",
        kind="bool",
        default=DEFAULTS["INCLUDE_ANIMATION"],
        tooltip="Bake & export keyframes and import them in Blender (off = static mesh hand-off).",
    ),
    "INCLUDE_LIGHTS": AttributeSpec(
        key="INCLUDE_LIGHTS",
        label="Include Lights",
        kind="bool",
        default=DEFAULTS["INCLUDE_LIGHTS"],
        tooltip=(
            "Send the Maya lights in the sent selection so Blender can rebuild them.\n\n"
            "On for the lightmap bake, which needs the scene's own illumination — with it\n"
            "off, a scene lit by ordinary point/spot/directional/area lights bakes BLACK.\n"
            "Only lights INSIDE the sent hierarchy travel: a light grouped elsewhere in\n"
            "the scene does not cross, so keep module lights parented with their module.\n"
            "Turn it off for a pure asset hand-off where Blender does its own lighting.\n\n"
            "The lights travel as DATA beside the FBX, not inside it: an FBX light crashes\n"
            "Blender 5.1's importer outright, and FBX cannot represent Arnold lights at\n"
            "any version. Point / spot / directional / area — including aiAreaLight — all\n"
            "come across. Ambient and volume lights have no Cycles equivalent and are\n"
            "skipped; a StingrayPBS IBL is not a light object at all, so use Environment\n"
            "HDRI for that."
        ),
    ),
    "TRIANGULATE": AttributeSpec(
        key="TRIANGULATE",
        label="Triangulate",
        kind="bool",
        default=DEFAULTS["TRIANGULATE"],
        tooltip="Triangulate meshes on export.",
    ),
    "CLEAR_SCENE": AttributeSpec(
        key="CLEAR_SCENE",
        label="Clear Scene First",
        kind="bool",
        default=DEFAULTS["CLEAR_SCENE"],
        tooltip=(
            "Delete the existing scene objects before importing (clean-slate / replace-scene\n"
            "hand-off). Off imports additively into the current scene."
        ),
    ),
    "FRAME_VIEW": AttributeSpec(
        key="FRAME_VIEW",
        label="Frame in View",
        kind="bool",
        # Off by default so the unified template's default behavior matches the old plain
        # "import" template (no selection change / no viewport shading switch); opt in for the
        # old "import_and_frame" behavior.
        default=DEFAULTS["FRAME_VIEW"],
        tooltip=(
            "After import, select the new objects, frame them in the 3D viewport, and switch to\n"
            "material-preview shading."
        ),
    ),
    # ---------------------------------------------------------------- lightmap bake
    # Referenced only by templates/bake_lightmaps.py, so the panel shows these widgets
    # only when that recipe is selected.
    #
    # This block IS blendertk ``LightmapBaker``'s panel surface, one row per dial and in
    # its order -- Quality, Resolution, Samples, Packing, output folder, name affix --
    # because the recipe drives that exact baker. The bridge previously showed Quality
    # alone, on the argument that a tier already names the good values; that hid
    # Packing (so every send was force-atlased) and the affix (so every map was named
    # ``_Lightmap``), and it left the artist reading a panel whose dials did not match
    # the tool underneath. The tier still FILLS resolution/samples -- 0 means "whatever
    # the tier says", the headless equivalent of the panel's preset -> dials fill -- so
    # the default experience is unchanged for anyone who only touches Quality.
    #
    # Blender-side lighting (Environment HDRI / World / Scene Light / Emission
    # strength) has no counterpart in the baker's panel and never will: Maya's baker
    # renders the scene's own Arnold lights in place, while this one has to TRANSPORT
    # them into another renderer's units. Those rows belong to the crossing, not to
    # the baker.
    "LIGHTMAP_QUALITY": AttributeSpec(
        key="LIGHTMAP_QUALITY",
        label="Quality",
        kind="choice",
        default=DEFAULTS["LIGHTMAP_QUALITY"],
        choices=[
            ("Preview", "preview", "256 px / 64 samples — fast iteration."),
            ("Quest / Mobile", "quest", "1024 px / 256 samples — the production default."),
            ("Desktop / High", "desktop", "2048 px / 512 samples — hero environments."),
            (
                "Hero / Production",
                "hero",
                "4096 px / 1024 samples — a whole environment sharing one material.\n"
                "The figure is the ATLAS size, and one atlas is split between every\n"
                "object in a material group: a 46-piece room on one material gets 1/46th\n"
                "of it each, so an environment needs a tier above per-object intuition.",
            ),
        ],
        tooltip=(
            "Bake quality tier (blendertk's lightmap preset store; denoised, GPU).\n"
            "Fills Resolution and Samples below — set either of those and the tier\n"
            "stops applying to it, exactly as in the Lightmap Baker panel."
        ),
    ),
    "LIGHTMAP_RESOLUTION": AttributeSpec(
        key="LIGHTMAP_RESOLUTION",
        label="Resolution",
        kind="choice",
        default=DEFAULTS["LIGHTMAP_RESOLUTION"],
        # The Lightmap Baker panel's own fixed sizes (``LightmapBakerSlots._RESOLUTIONS``),
        # plus a leading row for "leave it to the tier" -- the panel has no such row
        # because a preset there WRITES the dial, which a one-shot headless send cannot.
        choices=[
            ("From Quality preset", 0, "Use the tier's resolution (the default)."),
            ("256", 256, "Preview / fast iteration."),
            ("512", 512, "Between the preview and mobile tiers."),
            ("1024", 1024, "Quest / mobile."),
            ("2048", 2048, "Desktop / high."),
            ("4096", 4096, "Hero — a whole environment on one atlas."),
        ],
        tooltip=(
            "Lightmap size in pixels (square), overriding the Quality tier.\n\n"
            "With Packing set to Atlas by Material this is the ATLAS size, SHARED by\n"
            "every object in a material group — a 46-piece room on one material gets\n"
            "1/46th of it each, so an environment wants a tier above per-object\n"
            "intuition. Per-Object gives each mesh a map this size of its own."
        ),
    ),
    "LIGHTMAP_SAMPLES": AttributeSpec(
        key="LIGHTMAP_SAMPLES",
        label="Samples",
        kind="int",
        default=DEFAULTS["LIGHTMAP_SAMPLES"],
        minimum=0,
        maximum=8192,
        tooltip=(
            "Cycles path samples per texel, overriding the Quality tier. 0 uses the\n"
            "tier's value.\n\n"
            "These are CYCLES paths, not Arnold AA samples — the Lightmap Baker's\n"
            "Maya twin counts in the hundreds where this counts in the tens, so a\n"
            "number carried over from that panel bakes far faster and far noisier\n"
            "than it looks. Denoising below covers a lot of the difference."
        ),
    ),
    "LIGHTMAP_PACKING": AttributeSpec(
        key="LIGHTMAP_PACKING",
        label="Packing",
        kind="choice",
        default=DEFAULTS["LIGHTMAP_PACKING"],
        choices=[
            (
                "Atlas by Material (shared map)",
                "atlas",
                "One shared EXR per material, each object given an area-weighted\n"
                "rect. A material carries ONE lightmap in any engine, so this is\n"
                "what keeps a 46-piece room from forcing 46 material copies.\n"
                "Instances are first-class: every copy keeps its own rect and its\n"
                "own lighting, bound as Unity's native lightmapScaleOffset.",
            ),
            (
                "Per-Object (one map each)",
                "per_object",
                "Each object gets its own full-resolution map. Self-contained in\n"
                "any engine with no rect binding; more textures.",
            ),
        ],
        tooltip=(
            "How the baked maps are laid out — the Lightmap Baker panel's Packing\n"
            "combobox.\n\n"
            "Atlas is the default HERE (the panel defaults to Per-Object) because a\n"
            "bridge send is a whole module, not one selected mesh. The UVs are never\n"
            "edited either way; the choice is made BEFORE baking, since the atlas\n"
            "path plans the layout first and bakes each object at the footprint it\n"
            "will occupy rather than downscaling a full map per object."
        ),
    ),
    "ENVIRONMENT_HDR": AttributeSpec(
        key="ENVIRONMENT_HDR",
        label="Environment HDRI",
        kind="path",
        default=DEFAULTS["ENVIRONMENT_HDR"],
        tooltip=(
            "Equirect .hdr/.exr used as the world light.\n"
            "A Maya scene lit by StingrayPBS IBL exports NO lights — its cubemaps are not\n"
            "FBX-portable — so without this (or real scene lights) the bake is black."
        ),
    ),
    "WORLD_STRENGTH": AttributeSpec(
        key="WORLD_STRENGTH",
        label="World Strength",
        kind="float",
        default=DEFAULTS["WORLD_STRENGTH"],
        minimum=0.0,
        maximum=100.0,
        decimals=2,
        tooltip="Environment multiplier. Keep low when the scene's own lights should dominate.",
    ),
    "SCENE_LIGHT_STRENGTH": AttributeSpec(
        key="SCENE_LIGHT_STRENGTH",
        label="Scene Light Strength",
        kind="float",
        default=DEFAULTS["SCENE_LIGHT_STRENGTH"],
        minimum=0.0,
        maximum=1000.0,
        decimals=3,
        tooltip=(
            "Multiplier applied to the imported Maya lights' power before baking.\n\n"
            "Maya intensity and Cycles watts are different units, and the FBX crossing\n"
            "translates through both exporters' guesses — so the same rig can arrive far\n"
            "too dim or blown out. Your relative brightnesses survive; this is the one\n"
            "dial for the overall level. 1.0 leaves them exactly as imported.\n\n"
            "The bake log reports each light's final power, so tune from that rather\n"
            "than guessing."
        ),
    ),
    "EMISSION_STRENGTH": AttributeSpec(
        key="EMISSION_STRENGTH",
        label="Emission Strength",
        kind="float",
        default=DEFAULTS["EMISSION_STRENGTH"],
        minimum=0.0,
        maximum=1000.0,
        decimals=2,
        tooltip=(
            "Emissive-map strength — an APPEARANCE knob so fixtures read as switched-on.\n"
            "Not the room's light source; use real scene lights / Environment HDRI for\n"
            "that (Lights From Geometry in the Lighting panel builds them from fixture\n"
            "meshes). It is not free, though: emissive surfaces DO light a Cycles bake.\n"
            "Measured on a 61 m² office, the lens supplied ~15% of the room's light at\n"
            "2.0 and ~33% at 6.0 — illumination driven by a texture's exposure, which\n"
            "cannot be aimed or re-coloured. Raise the lights' power instead and leave\n"
            "this near 2."
        ),
    ),
    "LIGHTMAP_DIR": AttributeSpec(
        key="LIGHTMAP_DIR",
        label="Lightmap Folder",
        kind="path",
        default=DEFAULTS["LIGHTMAP_DIR"],
        tooltip=(
            "Where the HDR (.exr) lightmaps are written.\n"
            "They come back as textures THIS Maya scene references (and its exporters\n"
            "resolve), so they belong in the project's texture folder.\n"
            "Empty puts each map beside the textures it joins — the folder holding the\n"
            "selection's existing maps — falling back to the project's sourceimages."
        ),
    ),
    "LIGHTMAP_AFFIX": AttributeSpec(
        key="LIGHTMAP_AFFIX",
        label="Map Affix",
        kind="affix",
        default=DEFAULTS["LIGHTMAP_AFFIX"],
        tooltip=(
            "Affix on each baked map's filename — the Lightmap Baker panel's name\n"
            "field, so a bridge bake and a native one land on the same convention\n"
            "instead of the bridge hard-coding '_Lightmap'.\n\n"
            "The icon button pins the side when the spelling does not say:\n"
            "<b>Auto</b> -> <b>Suffix</b> -> <b>Prefix</b>. Under Auto a leading '_'\n"
            "('_Lightmap') reads as a suffix and a trailing one ('LM_') as a prefix.\n\n"
            "Under Atlas by Material the affix names the ATLAS, which is derived from\n"
            "the material rather than from any one object."
        ),
    ),
    "LIGHTMAP_DENOISE": AttributeSpec(
        key="LIGHTMAP_DENOISE",
        label="Denoise",
        kind="bool",
        default=DEFAULTS["LIGHTMAP_DENOISE"],
        tooltip=(
            "Run Cycles' denoiser over each bake. On unless you are chasing what the\n"
            "raw sample count actually gives you — it is what makes the low Samples\n"
            "of the fast tiers usable."
        ),
    ),
    "LIGHTMAP_DEVICE": AttributeSpec(
        key="LIGHTMAP_DEVICE",
        label="Device",
        kind="choice",
        default=DEFAULTS["LIGHTMAP_DEVICE"],
        choices=[
            (
                "Auto",
                "AUTO",
                "Per object: the GPU where it pays, the CPU for small tiles.\n"
                "Every bake op rebuilds its Cycles session, and on the GPU that\n"
                "setup+teardown (~0.35 s/object, measured) outweighs a small\n"
                "tile's render.",
            ),
            ("GPU", "GPU", "Force the GPU for every object."),
            ("CPU", "CPU", "Force the CPU — the fallback when the GPU session fails."),
        ],
        tooltip="Which device Cycles bakes on (blendertk TextureBaker's device policy).",
    ),
}


class Parameters:
    """Parameters — module namespace."""

    #: The parameter registry, exposed on the class so a bridge slot can hand
    #: this class to the shared base as its ``params_module`` (the base reads
    #: ``params_module.PARAMS`` and ``.referenced_keys``) — no module-level shim.
    PARAMS = PARAMS

    @staticmethod
    def referenced_keys(script_text: str) -> "set[str]":
        """Registered keys present in *script_text* (delegates to uitk.bridge)."""
        return _BridgeParams.referenced_keys(script_text, PARAMS)

    @staticmethod
    def defaults() -> "dict[str, Any]":
        """Return ``{key: default}`` for every registered parameter."""
        return _BridgeParams.defaults(PARAMS)

    @staticmethod
    def affix_parts(value: "Any", *, default: str = "suffix") -> "tuple[str, str]":
        """``(prefix, suffix)`` for an ``affix`` param value (delegates to uitk).

        ``suffix`` is the fallback side, not ``prefix``: a lightmap follows the
        texture-set convention (``<object>_Lightmap``), the same default the
        Lightmap Baker panel resolves its own field with.
        """
        return _BridgeParams.affix_parts(value, default=default)

    @staticmethod
    def render_context(values: "dict[str, Any]") -> "dict[str, str]":
        """Format *values* for ``StrUtils.replace_delimited`` using Python literals.

        The shared base formats REGISTERED keys and lets unknown ones fall through to
        ``str()`` -- correct for the bridge-injected raw tokens (``FBX_PATH``), but a
        typed value rendered by ``str()`` loses its quotes: ``"GPU"`` becomes the bare
        name ``GPU`` and the template dies on a NameError. Any DEFAULTS key that
        reaches here without a spec is re-rendered as a Python literal.

        It also DERIVES two tokens the registry has no widget for.
        ``LIGHTMAP_AFFIX`` is a composite (``{"text", "mode"}``) and the ``affix``
        kind substitutes the SPELLING ALONE -- by design, since most templates only
        want the string -- so a mode the artist pinned would not survive the crossing
        and a "Prefix" would silently bake as Auto. Resolving it here, through the
        same ``split_affix`` the Lightmap Baker panel's field uses, is what keeps the
        two spellings of that rule from drifting; the template then receives the
        answer rather than the question.
        """
        out = _BridgeParams.render_context(values, PARAMS, formatter=_FORMATTER)
        for key, val in values.items():
            if key not in PARAMS and key in DEFAULTS:
                out[key] = repr(val)
        if "LIGHTMAP_AFFIX" in values:
            prefix, suffix = Parameters.affix_parts(values["LIGHTMAP_AFFIX"])
            out["LIGHTMAP_PREFIX"] = repr(prefix)
            out["LIGHTMAP_SUFFIX"] = repr(suffix)
        return out
