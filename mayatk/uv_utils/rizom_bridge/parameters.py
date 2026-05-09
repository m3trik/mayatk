# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable RizomUV parameters exposed to the bridge UI.

Each entry maps a Lua placeholder token (e.g. ``__MARGIN__``) to a widget
spec. The slot scans the selected preset for these tokens, shows only the
matching widgets, and substitutes the user values into the script before
sending it to RizomUV via :func:`StrUtils.replace_delimited`.

To expose a new RizomUV knob:
  1. Add an entry below.
  2. Reference ``__YOUR_KEY__`` in any preset Lua file.

The registry is intentionally non-exhaustive -- it covers the params real
RizomUV bridge implementations actually expose (SideFX Labs, the C4D
bridge, the 3ds Max bridge), not every flag in the Lua API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class RizomParam:
    """Describes one tunable RizomUV parameter and how to render its widget."""

    key: str
    """Placeholder token (without the ``__`` delimiters)."""

    label: str
    """UI label shown next to the widget."""

    widget_type: str
    """One of ``"int"``, ``"float"``, ``"choice"``."""

    default: Any
    """Default value (numeric, or one of the choice values)."""

    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    decimals: int = 0

    choices: Optional[List[Tuple[str, int]]] = None
    """For ``"choice"`` widgets only: ``[(display_label, value), ...]``."""

    tooltip: str = ""

    def format_value(self, value: Any) -> str:
        """Render *value* for inlining into Lua source."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            if self.decimals:
                return f"{value:.{self.decimals}f}".rstrip("0").rstrip(".") or "0"
            return repr(value)
        return str(value)


# Display order is iteration order over this dict.
#
# NOTE: ``ZomPack.Margin`` and ``ZomPack.Quality`` are intentionally absent --
# RizomUV 2020.1 crashes (access violation) the moment either parameter is
# set, even to its documented default. SideFX Labs and the C4D bridge omit
# them too. Re-add as registry entries once we move to a release where this
# is fixed.
PARAMS: "dict[str, RizomParam]" = {
    # ------------------------------------------------------------------
    # Pack-time parameters (ZomPack)
    # ------------------------------------------------------------------
    "RECURSION_DEPTH": RizomParam(
        key="RECURSION_DEPTH",
        label="Recursion Depth",
        widget_type="int",
        default=2,
        minimum=1,
        maximum=5,
        step=1,
        tooltip=(
            "How many recursion levels the packer explores.\n"
            "Higher = tighter packing, much slower."
        ),
    ),
    "SCALING_MODE": RizomParam(
        key="SCALING_MODE",
        label="Pre-scale",
        widget_type="choice",
        default=2,
        choices=[
            ("0  None", 0),
            ("1  Uniform", 1),
            ("2  Non-uniform", 2),
        ],
        tooltip="How shells are pre-scaled before packing.",
    ),
    "LAYOUT_SCALING_MODE": RizomParam(
        key="LAYOUT_SCALING_MODE",
        label="Layout Scale",
        widget_type="choice",
        default=2,
        choices=[
            ("0  None", 0),
            ("1  Uniform", 1),
            ("2  Non-uniform", 2),
        ],
        tooltip="How the final packed layout is scaled to fit 0-1.",
    ),
    "ROTATE_STEP": RizomParam(
        key="ROTATE_STEP",
        label="Orientation",
        widget_type="int",
        default=90,
        minimum=1,
        maximum=360,
        step=1,
        tooltip=(
            "Rotation step in degrees.\n"
            "90 = axis-aligned, 1 = free rotation (slowest)."
        ),
    ),
    # ------------------------------------------------------------------
    # Unfold / Optimize solver parameters (ZomUnfold, ZomOptimize)
    # ------------------------------------------------------------------
    "ITERATIONS": RizomParam(
        key="ITERATIONS",
        label="Accuracy",
        widget_type="int",
        default=10,
        minimum=1,
        maximum=100,
        step=1,
        tooltip=(
            "Solver iterations for unfold and optimize.\n"
            "Higher = more accurate, slower convergence."
        ),
    ),
    "PRE_ITERATIONS": RizomParam(
        key="PRE_ITERATIONS",
        label="Pre-iterations",
        widget_type="int",
        default=10,
        minimum=0,
        maximum=50,
        step=1,
        tooltip="Pre-pass iterations before the main unfold.",
    ),
    "MIX": RizomParam(
        key="MIX",
        label="Mutations",
        widget_type="float",
        default=1.0,
        minimum=0.0,
        maximum=1.0,
        step=0.05,
        decimals=2,
        tooltip=(
            "How aggressively the solver re-arranges UVs.\n"
            "0 = preserve incoming layout; 1 = full re-solve."
        ),
    ),
    "ROOM_SPACE": RizomParam(
        key="ROOM_SPACE",
        label="Spacing",
        widget_type="float",
        default=0.001,
        minimum=0.0,
        maximum=0.1,
        step=0.001,
        decimals=4,
        tooltip=(
            "Per-shell margin used during unfold/optimize.\n"
            "Distinct from pack Margin -- this controls the solver, not the packer."
        ),
    ),
    "MIN_ANGLE": RizomParam(
        key="MIN_ANGLE",
        label="Min Angle",
        widget_type="float",
        default=1e-5,
        minimum=1e-7,
        maximum=1.0,
        step=1e-5,
        decimals=7,
        tooltip="Solver minimum angle threshold for triangle stability.",
    ),
}


_PLACEHOLDER_RE = None


def referenced_keys(script_text: str) -> "set[str]":
    """Return the set of registered placeholder keys present in *script_text*.

    Any ``__KEY__`` token in the script that doesn't match a registry entry
    is silently ignored -- the bridge's substitution leaves it intact and
    RizomUV will surface the error if it actually mattered.
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
    """Format *values* for ``StrUtils.replace_delimited`` (string-valued context).

    Unknown keys are passed through ``str()``; registered keys go through
    :meth:`RizomParam.format_value` so floats keep their precision and
    booleans become ``true``/``false``.
    """
    out = {}
    for key, val in values.items():
        spec = PARAMS.get(key)
        out[key] = spec.format_value(val) if spec else str(val)
    return out
