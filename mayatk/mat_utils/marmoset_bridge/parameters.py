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

from uitk.bridge import (
    AttributeSpec,
    python_literal,
    referenced_keys as _refkeys,
    defaults as _defaults,
    render_context as _render_context,
)


# Targets Python templates -- ``python_literal`` is the formatter the
# ``render_context`` wrapper below uses to turn user values into Python
# source literals when the bridge substitutes them into ``templates/*.py``.
_FORMATTER = python_literal


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
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
    "BAKE_PADDING": AttributeSpec(
        key="BAKE_PADDING",
        label="Edge Padding",
        kind="int",
        default=16,
        minimum=0,
        maximum=64,
        step=1,
        tooltip="Pixels of edge padding (UV bleed) around each shell.",
    ),
    "BAKE_BITS": AttributeSpec(
        key="BAKE_BITS",
        label="Bit Depth",
        kind="choice",
        default=8,
        choices=[
            ("8-bit", 8),
            ("16-bit", 16),
        ],
        tooltip=(
            "Per-map output bit depth. Maps are written as PSDs in the\n"
            "output directory (one PSD per enabled map). Use 16-bit for\n"
            "normal maps that need precision -- avoids banding on near-\n"
            "axis-aligned faces."
        ),
    ),
    # ------------------------------------------------------------------
    # Bake maps to enable (each maps to a Toolbag BakerMap.enabled flag)
    # ------------------------------------------------------------------
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
        default=True,
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
        default=True,
        tooltip="Bake material-ID map from source material colors.",
    ),
    # ------------------------------------------------------------------
    # High/Low pairing (suffix convention)
    # ------------------------------------------------------------------
    "HIGH_SUFFIX": AttributeSpec(
        key="HIGH_SUFFIX",
        label="High Suffix",
        kind="choice",
        default="_high",
        choices=[
            ("_high", "_high"),
            ("_hi", "_hi"),
            ("_HP", "_HP"),
            ("(none)", ""),
        ],
        tooltip=(
            "Suffix that marks high-poly source meshes.\n"
            "Applied to a mesh's OWN name, or any ancestor group's name --\n"
            "tag a parent group ('engine_high') once instead of every mesh.\n"
            "Own suffix wins if both a mesh and its ancestor are tagged.\n"
            "If Low Suffix is '(none)', every unsuffixed mesh is treated as low.\n"
            "If both are '(none)', no auto-pairing is attempted."
        ),
    ),
    "LOW_SUFFIX": AttributeSpec(
        key="LOW_SUFFIX",
        label="Low Suffix",
        kind="choice",
        default="",
        choices=[
            ("(none)", ""),
            ("_low", "_low"),
            ("_lo", "_lo"),
            ("_LP", "_LP"),
        ],
        tooltip=(
            "Suffix that marks low-poly target meshes.\n"
            "Default '(none)': every unsuffixed mesh is treated as low.\n"
            "Otherwise applied to a mesh's OWN name, or any ancestor group's\n"
            "name -- tag a parent group ('engine_low') once instead of every\n"
            "mesh."
        ),
    ),
    "CAGE_OFFSET": AttributeSpec(
        key="CAGE_OFFSET",
        label="Cage Offset",
        kind="float",
        default=0.02,
        minimum=0.0,
        maximum=1.0,
        step=0.005,
        decimals=4,
        tooltip=(
            "Ray-cast offset distance for cage-less baking.\n"
            "Bump up if you see normal artefacts on convex edges."
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


def referenced_keys(script_text: str) -> "set[str]":
    """Registered keys present in *script_text* (delegates to uitk.bridge)."""
    return _refkeys(script_text, PARAMS)


def defaults() -> "dict[str, Any]":
    """Return ``{key: default}`` for every registered parameter."""
    return _defaults(PARAMS)


def render_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``StrUtils.replace_delimited`` using Python literals."""
    return _render_context(values, PARAMS, formatter=_FORMATTER)
