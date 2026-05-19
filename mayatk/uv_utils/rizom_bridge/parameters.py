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

from typing import Any

from uitk.bridge import (
    AttributeSpec,
    lua_literal,
    referenced_keys as _refkeys,
    defaults as _defaults,
    render_context as _render_context,
)


# Targets Lua scripts -- ``lua_literal`` produces lowercase ``true`` /
# ``false`` and bare numeric / string literals suitable for inlining
# into ``scripts/*.lua`` preset bodies.
_FORMATTER = lua_literal


# Display order is iteration order over this dict.
#
# NOTE: ``ZomPack.Margin`` and ``ZomPack.Quality`` are intentionally absent --
# RizomUV 2020.1 crashes (access violation) the moment either parameter is
# set, even to its documented default. SideFX Labs and the C4D bridge omit
# them too. Re-add as registry entries once we move to a release where this
# is fixed.
PARAMS: "dict[str, AttributeSpec]" = {
    # ------------------------------------------------------------------
    # Pack-time parameters (ZomPack)
    # ------------------------------------------------------------------
    "RECURSION_DEPTH": AttributeSpec(
        key="RECURSION_DEPTH",
        label="Recursion Depth",
        kind="int",
        default=2,
        minimum=1,
        maximum=5,
        step=1,
        tooltip=(
            "How many recursion levels the packer explores.\n"
            "Higher = tighter packing, much slower."
        ),
    ),
    "SCALING_MODE": AttributeSpec(
        key="SCALING_MODE",
        label="Pre-scale",
        kind="choice",
        default=2,
        choices=[
            ("0  None", 0),
            ("1  Uniform", 1),
            ("2  Non-uniform", 2),
        ],
        tooltip="How shells are pre-scaled before packing.",
    ),
    "LAYOUT_SCALING_MODE": AttributeSpec(
        key="LAYOUT_SCALING_MODE",
        label="Layout Scale",
        kind="choice",
        default=2,
        choices=[
            ("0  None", 0),
            ("1  Uniform", 1),
            ("2  Non-uniform", 2),
        ],
        tooltip="How the final packed layout is scaled to fit 0-1.",
    ),
    "ROTATE_STEP": AttributeSpec(
        key="ROTATE_STEP",
        label="Orientation",
        kind="int",
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
    "ITERATIONS": AttributeSpec(
        key="ITERATIONS",
        label="Accuracy",
        kind="int",
        default=10,
        minimum=1,
        maximum=100,
        step=1,
        tooltip=(
            "Solver iterations for unfold and optimize.\n"
            "Higher = more accurate, slower convergence."
        ),
    ),
    "PRE_ITERATIONS": AttributeSpec(
        key="PRE_ITERATIONS",
        label="Pre-iterations",
        kind="int",
        default=10,
        minimum=0,
        maximum=50,
        step=1,
        tooltip="Pre-pass iterations before the main unfold.",
    ),
    "MIX": AttributeSpec(
        key="MIX",
        label="Mutations",
        kind="float",
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
    "ROOM_SPACE": AttributeSpec(
        key="ROOM_SPACE",
        label="Spacing",
        kind="float",
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
    "MIN_ANGLE": AttributeSpec(
        key="MIN_ANGLE",
        label="Min Angle",
        kind="float",
        default=1e-5,
        minimum=1e-7,
        maximum=1.0,
        step=1e-5,
        decimals=7,
        tooltip="Solver minimum angle threshold for triangle stability.",
    ),
}


def referenced_keys(script_text: str) -> "set[str]":
    """Registered keys present in *script_text* (delegates to uitk.bridge)."""
    return _refkeys(script_text, PARAMS)


def defaults() -> "dict[str, Any]":
    """Return ``{key: default}`` for every registered parameter."""
    return _defaults(PARAMS)


def render_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``StrUtils.replace_delimited`` using Lua literals."""
    return _render_context(values, PARAMS, formatter=_FORMATTER)
