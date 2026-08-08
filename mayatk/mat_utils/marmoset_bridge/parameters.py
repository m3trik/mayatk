# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.

Each entry maps a placeholder token (e.g. ``__BAKE_SIZE__``) to a widget
spec. The slot scans the selected template for these tokens, shows only the
matching widgets, and substitutes the user values into the template before
shipping it to Toolbag via :func:`StrUtils.replace_delimited`.

To expose a new Toolbag knob:
  1. Add an entry below.
  2. Reference ``__YOUR_KEY__`` in any ``templates/*.py`` file.

Mirrors :mod:`mayatk.uv_utils.rizom_bridge.parameters` so the slots class
stays identical in shape.
"""

from __future__ import annotations

from typing import Any

from uitk.bridge import AttributeSpec, Formatters, Parameters as _BridgeParams


# Targets Python templates -- ``python_literal`` is the formatter the
# ``render_context`` wrapper below uses to turn user values into Python
# source literals when the bridge substitutes them into ``templates/*.py``.
_FORMATTER = Formatters.python_literal


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    # Shared across every hand-off bridge (uitk owns the one spec);
    # resolved by the DCC bridge-slots base.
    "SCOPE": _BridgeParams.scope_spec(),
    # ------------------------------------------------------------------
    # Bake output
    # ------------------------------------------------------------------
    "BAKE_SIZE": AttributeSpec(
        key="BAKE_SIZE",
        label="Size",
        kind="choice",
        default=4096,
        choices=[
            ("512", 512),
            ("1024", 1024),
            ("2048", 2048),
            ("4096", 4096),
            ("8192 (8K)", 8192),
            ("16384 (16K)", 16384),
        ],
        tooltip=(
            "Bake output resolution. One value sets both width and height\n"
            "(square map). 16K bakes are RAM-heavy and slow."
        ),
    ),
    "BAKE_SAMPLES": AttributeSpec(
        key="BAKE_SAMPLES",
        label="Samples",
        kind="choice",
        default=16,
        choices=[
            ("1x", 1),
            ("4x", 4),
            ("16x", 16),
            ("64x", 64),
        ],
        tooltip=(
            "Anti-aliasing samples per pixel for the bake.\n"
            "Higher = cleaner edges and AO, slower."
        ),
    ),
    # BAKE_PADDING and BAKE_BITS are deliberately NOT registered: they are
    # managed values (TemplateParams.derive_bake_values) -- padding derives
    # from the map size via pythontk's UV-padding primitive, bit depth from
    # the per-map-type output templates. No widget, no drift.
    "OUTPUT_FORMAT": AttributeSpec(
        key="OUTPUT_FORMAT",
        label="Format",
        kind="choice",
        default="png",
        choices=[
            ("PNG", "png"),
            ("TGA", "tga"),
            ("PSD", "psd"),
        ],
        tooltip=(
            "Image format for the baked maps (one file per enabled map,\n"
            "per texture set). PNG/TGA are production-ready; PSD keeps\n"
            "Toolbag's layered format."
        ),
    ),
    # ------------------------------------------------------------------
    # Bake maps to enable (each maps to a Toolbag BakerMap.enabled flag)
    # ------------------------------------------------------------------
    "AUTO_MAPS": AttributeSpec(
        key="AUTO_MAPS",
        label="Auto Maps",
        kind="bool",
        # On by default: a fixed roster bakes maps the source has no texture
        # for (flat, useless files, and a deeper bit depth for all of them)
        # while missing ones it does carry. What the materials actually hold
        # is knowable per send, so it should not be a checklist to maintain.
        default=True,
        tooltip=(
            "Bake the maps the SOURCE materials actually carry, instead of a\n"
            "fixed roster: each transfer map is enabled only when a source\n"
            "material has a texture wired into that channel (read from the\n"
            "material manifest the send exports).\n"
            "The geometry maps -- Normal and Ambient Occlusion -- are always\n"
            "on: Toolbag derives those from the source MESH, so they need no\n"
            "source texture.\n"
            "While this is on the individual map toggles below are inactive."
        ),
    ),
    "MAP_NORMAL": AttributeSpec(
        key="MAP_NORMAL",
        label="Normal Map",
        kind="bool",
        default=True,
        tooltip="Bake tangent-space normal map.",
    ),
    "MAP_AO": AttributeSpec(
        key="MAP_AO",
        label="Ambient Occlusion",
        kind="bool",
        default=True,
        tooltip="Bake ambient occlusion map.",
    ),
    "MAP_CURVATURE": AttributeSpec(
        key="MAP_CURVATURE",
        label="Curvature",
        kind="bool",
        default=False,
        tooltip="Bake curvature map (cavity/convex highlights).",
    ),
    "MAP_THICKNESS": AttributeSpec(
        key="MAP_THICKNESS",
        label="Thickness",
        kind="bool",
        default=False,
        tooltip="Bake thickness map for SSS / translucency lookups.",
    ),
    "MAP_POSITION": AttributeSpec(
        key="MAP_POSITION",
        label="Position",
        kind="bool",
        default=False,
        tooltip="Bake object-space position map.",
    ),
    "MAP_MATID": AttributeSpec(
        key="MAP_MATID",
        label="Material ID",
        kind="bool",
        default=False,
        tooltip="Bake material-ID map from source material colors.",
    ),
    "MAP_ALBEDO": AttributeSpec(
        key="MAP_ALBEDO",
        label="Albedo",
        kind="bool",
        default=True,
        tooltip=(
            "Transfer the source meshes' base-color textures onto the\n"
            "target UVs (samples the wired source materials)."
        ),
    ),
    "MAP_ROUGHNESS": AttributeSpec(
        key="MAP_ROUGHNESS",
        label="Roughness",
        kind="bool",
        default=True,
        tooltip=(
            "Transfer the source meshes' roughness onto the target UVs\n"
            "(packed MSAO/MetallicSmoothness sources are unpacked first)."
        ),
    ),
    "MAP_METALNESS": AttributeSpec(
        key="MAP_METALNESS",
        label="Metalness",
        kind="bool",
        default=True,
        tooltip="Transfer the source meshes' metalness onto the target UVs.",
    ),
    "MAP_EMISSIVE": AttributeSpec(
        key="MAP_EMISSIVE",
        label="Emissive",
        kind="bool",
        default=False,
        tooltip="Transfer the source meshes' emissive onto the target UVs.",
    ),
    # ------------------------------------------------------------------
    # Post-bake (host-side; resolved by the bridge, not Toolbag)
    # ------------------------------------------------------------------
    "ASSIGN_MATERIAL": AttributeSpec(
        key="ASSIGN_MATERIAL",
        label="Assign Material",
        kind="bool",
        default=True,
        tooltip=(
            "After a roundtrip bake, build a StingrayPBS network from each\n"
            "baked texture set and assign it to the bake-target meshes."
        ),
    ),
    # ------------------------------------------------------------------
    # Source/target pairing (the Bake Source set, with a suffix fallback)
    # ------------------------------------------------------------------
    "BAKE_SOURCE_SET": AttributeSpec(
        key="BAKE_SOURCE_SET",
        label="Bake Source",
        kind="action",
        choices=[
            (
                "Set From Selection",
                "set_bake_source_from_selection",
                "Store the current selection as this scene's bake source\n"
                "(an objectSet; saves with the scene, shared with the\n"
                "Substance bridge). The bake exports it as a companion FBX\n"
                "and pairs it as the bake-from side -- no name suffixes\n"
                "needed.",
            ),
            (
                "Select",
                "select_bake_source",
                "Select the scene's bake-source set members, hidden ones included.",
            ),
            (
                "Clear",
                "clear_bake_source",
                "Delete the bake-source set. The geometry itself is untouched.",
            ),
        ],
        tooltip=(
            "The scene's bake source (a scene objectSet shared with the\n"
            "Substance bridge) -- the geometry whose detail and textures\n"
            "bake onto the target. Define it once from a selection; bake\n"
            "sends then export it automatically as the bake-from side."
        ),
    ),
    "HIGH_SUFFIX": AttributeSpec(
        key="HIGH_SUFFIX",
        label="Source Suffix",
        kind="choice",
        default="_source",
        choices=[
            ("_source", "_source"),
            ("_high", "_high"),
            ("_hi", "_hi"),
            ("_HP", "_HP"),
            ("(none)", ""),
        ],
        tooltip=(
            "Fallback pairing when the scene has NO Bake Source set:\n"
            "suffix that marks bake-SOURCE meshes.\n"
            "Applied to a mesh's OWN name, and (with Include Children on)\n"
            "to any ancestor group's name -- tag a parent group\n"
            "('engine_source') once instead of every mesh.\n"
            "Own suffix wins if both a mesh and its ancestor are tagged.\n"
            "If Target Suffix is '(none)', every unsuffixed mesh is a target.\n"
            "If both are '(none)', no auto-pairing is attempted."
        ),
    ),
    "LOW_SUFFIX": AttributeSpec(
        key="LOW_SUFFIX",
        label="Target Suffix",
        kind="choice",
        default="",
        choices=[
            ("(none)", ""),
            ("_target", "_target"),
            ("_low", "_low"),
            ("_lo", "_lo"),
            ("_LP", "_LP"),
        ],
        tooltip=(
            "Suffix that marks bake-TARGET meshes.\n"
            "Default '(none)': every unsuffixed mesh is treated as a target.\n"
            "Otherwise applied to a mesh's OWN name, and (with Include\n"
            "Children on) to any ancestor group's name -- tag a parent group\n"
            "('engine_target') once instead of every mesh."
        ),
    ),
    "SUFFIX_INCLUDE_CHILDREN": AttributeSpec(
        key="SUFFIX_INCLUDE_CHILDREN",
        label="Include Children",
        kind="bool",
        default=True,
        tooltip=(
            "A suffix on a GROUP tags every mesh under it, so you only have\n"
            "to name the group root ('engine_source') instead of renaming\n"
            "each mesh inside. A mesh's own suffix still wins over its\n"
            "ancestors'.\n"
            "Off: only a mesh's own name is matched -- use this when a\n"
            "suffixed group holds a mix of source and target geometry."
        ),
    ),
    "CAGE_OFFSET": AttributeSpec(
        key="CAGE_OFFSET",
        label="Cage Offset",
        kind="float",
        default=0.02,
        minimum=0.0,
        # In SCENE UNITS, so the ceiling has to clear a centimetre scene (Maya's
        # default): a 1.0 cap is a metres-scale assumption, and under it a bake
        # source sitting 5 cm off its target could not be reached at ANY
        # setting -- the geometry silently missing from the bake, with the one
        # control that looks responsible pinned at its useless maximum.
        maximum=1000.0,
        step=0.05,
        decimals=4,
        tooltip=(
            "Ray-cast offset distance for cage-less baking, in SCENE UNITS.\n"
            "Toolbag spends this over the ray's whole traversal, so it reaches\n"
            "only HALF as far as the number suggests: to pick up source detail\n"
            "standing D from the target, this has to exceed 2 x D. Anything\n"
            "further out is simply not baked, with nothing to say so.\n"
            "Bump up if you see normal artefacts on convex edges, or if source\n"
            "geometry that stands off the target (a light fixture under a\n"
            "ceiling, a door in its opening) is missing from the maps.\n"
            "One fixed distance cannot suit both a centimetre and a metre\n"
            "scene -- leave Auto on beside it unless you need an exact value."
        ),
    ),
    "AUTO_CAGE": AttributeSpec(
        key="AUTO_CAGE",
        label="Auto",
        kind="bool",
        # On by default: the alternative is a fixed distance in scene units,
        # which is wrong by two orders of magnitude the moment the scene is
        # centimetres instead of metres.
        default=True,
        inline=True,
        tooltip=(
            "Size the cage offset per bake group from the actual geometry\n"
            "instead of using one typed distance for the whole scene.\n"
            "Measures how far each source mesh's FURTHEST point stands off the\n"
            "target surface -- a real closest-point query, so it sees a fixture\n"
            "hanging under a ceiling or a door inset in its opening, which sit\n"
            "inside the target's bounding box and read as zero to any estimate\n"
            "made from bounds alone -- then doubles it, because Toolbag's\n"
            "offset reaches only half its value.\n"
            "Scene-scale independent: the same setting works for a 2 cm bolt\n"
            "and a 20 m building, which one fixed number cannot.\n"
            "Falls back to a bounds estimate for any group it cannot measure."
        ),
    ),
    "IGNORE_BACKFACES": AttributeSpec(
        key="IGNORE_BACKFACES",
        label="Ignore Backfaces",
        kind="bool",
        default=True,
        tooltip="Discard ray hits on backfaces during bake (recommended).",
    ),
    # ------------------------------------------------------------------
    # Look-dev (lookdev.py template)
    # ------------------------------------------------------------------
    "SKY_PRESET": AttributeSpec(
        key="SKY_PRESET",
        label="Sky",
        kind="choice",
        default="Marmoset Skies/Hangar.tbsky",
        choices=[
            ("Hangar", "Marmoset Skies/Hangar.tbsky"),
            ("Studio Light", "Marmoset Skies/Studio Light.tbsky"),
            ("Sunset", "Marmoset Skies/Sunset.tbsky"),
            ("Overcast", "Marmoset Skies/Overcast.tbsky"),
        ],
        tooltip="Built-in Toolbag sky preset to apply during look-dev.",
    ),
    "FRAME_SELECTION": AttributeSpec(
        key="FRAME_SELECTION",
        label="Frame on Open",
        kind="bool",
        default=True,
        tooltip="Auto-frame the imported model in the viewport.",
    ),
}


#: ``(trigger, governed, reason)`` rows the bridge-slots base greys out while
#: the trigger is on. Declared with the registry rather than on a panel class
#: because "Auto takes over these controls" is a property of the parameter set
#: — so the Maya and Blender panels, which share this registry, behave the same
#: without either restating it.
SUPERSESSIONS = (
    (
        "AUTO_MAPS",
        tuple(k for k in PARAMS if k.startswith("MAP_")),
        "Inactive: Auto Maps is on, so the roster comes from the channels the "
        "source materials\nhave textures in (plus Normal and Ambient "
        "Occlusion, which bake from the mesh).\nTurn Auto Maps off to pick the "
        "maps by hand.",
    ),
    (
        "AUTO_CAGE",
        ("CAGE_OFFSET",),
        "Inactive: Auto is on, so each bake group sizes its own cage offset "
        "from the imported\ngeometry. Turn Auto off to bake with this typed "
        "distance instead.",
    ),
)


class Parameters:
    """Parameters — module namespace."""

    #: The parameter registry, exposed on the class so a bridge slot can hand
    #: this class to the shared base as its ``params_module`` (the base reads
    #: ``params_module.PARAMS`` and ``.referenced_keys``) — no module-level
    #: re-export shim required.
    PARAMS = PARAMS

    #: Rows one parameter's value takes over; read by the bridge-slots base.
    SUPERSESSIONS = SUPERSESSIONS

    @staticmethod
    def referenced_keys(script_text: str) -> "set[str]":
        """Registered keys present in *script_text* (delegates to uitk.bridge)."""
        return _BridgeParams.referenced_keys(script_text, PARAMS)

    @staticmethod
    def defaults() -> "dict[str, Any]":
        """Return ``{key: default}`` for every registered parameter."""
        return _BridgeParams.defaults(PARAMS)

    @staticmethod
    def render_context(values: "dict[str, Any]") -> "dict[str, str]":
        """Format *values* for ``StrUtils.replace_delimited`` using Python literals."""
        return _BridgeParams.render_context(values, PARAMS, formatter=_FORMATTER)
