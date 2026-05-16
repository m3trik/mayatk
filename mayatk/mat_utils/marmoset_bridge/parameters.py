# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.

Each entry maps a placeholder token (e.g. ``__BAKE_WIDTH__``) to a widget
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

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class MarmosetParam:
    """Describes one tunable Toolbag parameter and how to render its widget."""

    key: str
    label: str
    widget_type: str  # "int" | "float" | "choice" | "bool" | "path"
    default: Any

    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    decimals: int = 0

    choices: Optional[List[Tuple[str, Any]]] = None
    tooltip: str = ""

    def format_value(self, value: Any) -> str:
        """Render *value* for inlining into a Python template."""
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, str):
            return repr(value)
        if isinstance(value, float):
            if self.decimals:
                return f"{value:.{self.decimals}f}".rstrip("0").rstrip(".") or "0"
            return repr(value)
        return str(value)


# Display order is iteration order over this dict.
PARAMS: "dict[str, MarmosetParam]" = {
    # ------------------------------------------------------------------
    # Bake output
    # ------------------------------------------------------------------
    "BAKE_WIDTH": MarmosetParam(
        key="BAKE_WIDTH",
        label="Width",
        widget_type="int",
        default=2048,
        minimum=64,
        maximum=8192,
        step=64,
        tooltip="Bake output width in pixels.",
    ),
    "BAKE_HEIGHT": MarmosetParam(
        key="BAKE_HEIGHT",
        label="Height",
        widget_type="int",
        default=2048,
        minimum=64,
        maximum=8192,
        step=64,
        tooltip="Bake output height in pixels.",
    ),
    "BAKE_SAMPLES": MarmosetParam(
        key="BAKE_SAMPLES",
        label="Samples",
        widget_type="choice",
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
    "BAKE_PADDING": MarmosetParam(
        key="BAKE_PADDING",
        label="Edge Padding",
        widget_type="int",
        default=8,
        minimum=0,
        maximum=64,
        step=1,
        tooltip="Pixels of edge padding (UV bleed) around each shell.",
    ),
    "BAKE_BITS": MarmosetParam(
        key="BAKE_BITS",
        label="Bit Depth",
        widget_type="choice",
        default=8,
        choices=[
            ("8-bit (TGA)", 8),
            ("16-bit (TIF)", 16),
        ],
        tooltip=(
            "Output bit depth. Toolbag picks the file format automatically:\n"
            "8-bit -> .tga, 16-bit -> .tif. Use 16-bit for normal maps that\n"
            "need precision (avoids banding on near-axis-aligned faces)."
        ),
    ),
    "BAKE_OUTPUT_DIR": MarmosetParam(
        key="BAKE_OUTPUT_DIR",
        label="Output Dir",
        widget_type="path",
        default="",
        tooltip=(
            "Directory to write baked maps to.\n"
            "Leave empty to use the FBX export folder."
        ),
    ),
    # ------------------------------------------------------------------
    # Bake maps to enable (each maps to a Toolbag BakerMap.enabled flag)
    # ------------------------------------------------------------------
    "MAP_NORMAL": MarmosetParam(
        key="MAP_NORMAL",
        label="Normal Map",
        widget_type="bool",
        default=True,
        tooltip="Bake tangent-space normal map.",
    ),
    "MAP_AO": MarmosetParam(
        key="MAP_AO",
        label="Ambient Occlusion",
        widget_type="bool",
        default=True,
        tooltip="Bake ambient occlusion map.",
    ),
    "MAP_CURVATURE": MarmosetParam(
        key="MAP_CURVATURE",
        label="Curvature",
        widget_type="bool",
        default=True,
        tooltip="Bake curvature map (cavity/convex highlights).",
    ),
    "MAP_THICKNESS": MarmosetParam(
        key="MAP_THICKNESS",
        label="Thickness",
        widget_type="bool",
        default=False,
        tooltip="Bake thickness map for SSS / translucency lookups.",
    ),
    "MAP_POSITION": MarmosetParam(
        key="MAP_POSITION",
        label="Position",
        widget_type="bool",
        default=False,
        tooltip="Bake object-space position map.",
    ),
    "MAP_MATID": MarmosetParam(
        key="MAP_MATID",
        label="Material ID",
        widget_type="bool",
        default=True,
        tooltip="Bake material-ID map from source material colors.",
    ),
    # ------------------------------------------------------------------
    # High/Low pairing (suffix convention)
    # ------------------------------------------------------------------
    "HIGH_SUFFIX": MarmosetParam(
        key="HIGH_SUFFIX",
        label="High Suffix",
        widget_type="choice",
        default="_high",
        choices=[
            ("_high", "_high"),
            ("_hi", "_hi"),
            ("_HP", "_HP"),
            ("(none)", ""),
        ],
        tooltip="Suffix on transform names that identifies high-poly source meshes.",
    ),
    "LOW_SUFFIX": MarmosetParam(
        key="LOW_SUFFIX",
        label="Low Suffix",
        widget_type="choice",
        default="_low",
        choices=[
            ("_low", "_low"),
            ("_lo", "_lo"),
            ("_LP", "_LP"),
            ("(none)", ""),
        ],
        tooltip="Suffix on transform names that identifies low-poly target meshes.",
    ),
    "CAGE_OFFSET": MarmosetParam(
        key="CAGE_OFFSET",
        label="Cage Offset",
        widget_type="float",
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
    "IGNORE_BACKFACES": MarmosetParam(
        key="IGNORE_BACKFACES",
        label="Ignore Backfaces",
        widget_type="bool",
        default=True,
        tooltip="Discard ray hits on backfaces during bake (recommended).",
    ),
    # ------------------------------------------------------------------
    # Look-dev (lookdev.py template)
    # ------------------------------------------------------------------
    "SKY_PRESET": MarmosetParam(
        key="SKY_PRESET",
        label="Sky",
        widget_type="choice",
        default="Marmoset Skies/Hangar.tbsky",
        choices=[
            ("Hangar", "Marmoset Skies/Hangar.tbsky"),
            ("Studio Light", "Marmoset Skies/Studio Light.tbsky"),
            ("Sunset", "Marmoset Skies/Sunset.tbsky"),
            ("Overcast", "Marmoset Skies/Overcast.tbsky"),
        ],
        tooltip="Built-in Toolbag sky preset to apply during look-dev.",
    ),
    "FRAME_SELECTION": MarmosetParam(
        key="FRAME_SELECTION",
        label="Frame on Open",
        widget_type="bool",
        default=True,
        tooltip="Auto-frame the imported model in the viewport.",
    ),
}


_PLACEHOLDER_RE = None


def referenced_keys(script_text: str) -> "set[str]":
    """Return registered placeholder keys present in *script_text*.

    Any ``__KEY__`` token in the script that doesn't match a registry entry
    is silently ignored -- substitution leaves it intact, and Toolbag will
    surface the error if it actually mattered.
    """
    import re

    global _PLACEHOLDER_RE
    if _PLACEHOLDER_RE is None:
        _PLACEHOLDER_RE = re.compile(r"__([A-Z][A-Z0-9_]*)__")

    found = set(_PLACEHOLDER_RE.findall(script_text))
    return found & PARAMS.keys()


def defaults() -> "dict[str, Any]":
    """Return ``{key: default}`` for every registered parameter."""
    return {key: spec.default for key, spec in PARAMS.items()}


def render_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``StrUtils.replace_delimited`` (string-valued context)."""
    out = {}
    for key, val in values.items():
        spec = PARAMS.get(key)
        out[key] = spec.format_value(val) if spec else str(val)
    return out
