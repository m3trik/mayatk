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
    "PACK_ROTATE_ENABLE": AttributeSpec(
        key="PACK_ROTATE_ENABLE",
        label="Rotate",
        kind="bool",
        default=True,
        tooltip=(
            "Allow the packer to rotate islands. When off, every island\n"
            "keeps its incoming UV-space angle (the rotation step still\n"
            "applies during the initial pre-orientation pass)."
        ),
    ),
    "PACK_TRANSLATE": AttributeSpec(
        key="PACK_TRANSLATE",
        label="Translate",
        kind="bool",
        default=True,
        tooltip=(
            "Allow the packer to translate islands. When off, islands\n"
            "stay in place (useful when repacking against a pinned layout)."
        ),
    ),
    "PACK_RESOLUTION": AttributeSpec(
        key="PACK_RESOLUTION",
        label="Resolution",
        kind="choice",
        default=1024,
        choices=[
            ("256", 256),
            ("512", 512),
            ("1024", 1024),
            ("2048", 2048),
            ("4096", 4096),
            ("8192", 8192),
        ],
        tooltip=(
            "Pack-time resolution baseline. Anchors texel density for\n"
            "spacing and margin calculations; doesn't resample the layout."
        ),
    ),
    "PACK_MAX_MUTATIONS": AttributeSpec(
        key="PACK_MAX_MUTATIONS",
        label="Mutations",
        kind="int",
        default=1000,
        minimum=1,
        maximum=10000,
        step=1,
        tooltip=(
            "Packer solver iterations.\n"
            "Higher = tighter packing, slower convergence."
        ),
    ),
    "SCALING_MIX": AttributeSpec(
        key="SCALING_MIX",
        label="Mix Scale",
        kind="bool",
        default=False,
        tooltip=(
            "Mix incoming UV scale with the packer's computed scale.\n"
            "Useful when repacking an existing layout you want to mostly\n"
            "preserve; off = fully recompute scale from scratch."
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
    # ------------------------------------------------------------------
    # Auto-seam detection (ZomSelect Auto={SharpEdges={AngleMin=...}})
    # ------------------------------------------------------------------
    "SHARP_ANGLE": AttributeSpec(
        key="SHARP_ANGLE",
        label="Sharp Angle",
        kind="float",
        default=39.0,
        minimum=1.0,
        maximum=180.0,
        step=1.0,
        decimals=1,
        tooltip=(
            "Dihedral angle (degrees) above which an edge is treated as a seam.\n"
            "Lower = more cuts (hard-surface, ~39).\n"
            "Higher = fewer cuts on smooth surfaces (organic, ~80)."
        ),
    ),
    # ------------------------------------------------------------------
    # One-way send load options (ZomLoad File={...} fields)
    # ------------------------------------------------------------------
    # These only show in the panel when the ``send`` preset is active --
    # the ``send.lua`` body references each placeholder so the existing
    # ``_refresh_param_visibility`` scanner picks them up. The bridge's
    # ``send_to_rizomuv`` flow substitutes the boolean values into
    # ``templates/send_wrapper.lua``; the round-trip flow ignores them.
    "LOAD_UVS": AttributeSpec(
        key="LOAD_UVS",
        label="Load UVs",
        kind="bool",
        default=True,
        tooltip=(
            "Load existing UVs along with positions (XYZUVW=true).\n"
            "Off = load positions only; Rizom starts from a clean slate."
        ),
    ),
    "LOAD_UVW_PROPS": AttributeSpec(
        key="LOAD_UVW_PROPS",
        label="Load UVW Props",
        kind="bool",
        default=True,
        tooltip=(
            "Preserve UV-side metadata: seam/cut edges, pinned vertices,\n"
            "groups, and selection state. Off = mesh only, no metadata."
        ),
    ),
    "IMPORT_GROUPS": AttributeSpec(
        key="IMPORT_GROUPS",
        label="Import Groups",
        kind="bool",
        default=True,
        tooltip=(
            "Map source groups (Maya transforms / FBX hierarchies) into\n"
            "Rizom island groups. Off = every mesh imports as a flat list."
        ),
    ),
    "LOAD_TEXTURES": AttributeSpec(
        key="LOAD_TEXTURES",
        label="Load Textures",
        kind="bool",
        default=True,
        tooltip=(
            "Auto-collect file textures from the selection's shading networks\n"
            "and bind them in Rizom (ZomLoadTexture) so they show on the\n"
            "model in the 3D view. Off = open with no textures."
        ),
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


# ---------------------------------------------------------------------------
# Version gating
# ---------------------------------------------------------------------------
# Minimum RizomUV version required for each placeholder. Params absent from
# this map are considered universally compatible. Versions are (major, minor)
# tuples for natural comparison.
#
# Why this exists: Rizom 2020.1 crashes (access violation) the moment it
# encounters certain ZomPack fields that were added in later releases --
# the same family of crashes that already keeps ``Margin`` / ``Quality`` out
# of the registry entirely. Rather than dropping the newer knobs for users
# on a current Rizom, the bridge parses the version from the install dir
# and strips lines referencing unsupported placeholders before sending the
# script to Rizom. The panel does the same strip before scanning for
# placeholders, so the rows auto-hide for users on older Rizom.
#
# The gate is 2022.0 -- a conservative midpoint between Titus's 2020.1-era
# reference (no MaxMutations / Resolution / Rotate.Enable) and the adevra
# 2024 Maya bridge (uses all three). Adjust downward if a 2021.x release is
# confirmed to support these.
#
# IMPORTANT (for future contributors): each gated placeholder must live on
# its OWN line in the source .lua -- ``strip_unsupported`` drops whole
# lines, so a sibling 2020.1-compatible key on the same line would be
# dropped too. See the ``Rotate={Step=..., Enable=...}`` multi-line layout
# in scripts/*.lua for the pattern.
MIN_VERSIONS: "dict[str, tuple[int, ...]]" = {
    "PACK_MAX_MUTATIONS": (2022, 0),
    "PACK_RESOLUTION": (2022, 0),
    "PACK_ROTATE_ENABLE": (2022, 0),
}

# Minimum Rizom version that accepts the nested ``FBX={UseUVSetNames=true}``
# load/save flag. Below this, the bridge emits an empty FBX block (Rizom
# auto-detects format from the file extension). Kept here (rather than
# inline in ``_construct_full_script``) so the gate threshold lives next
# to its peers and stays in sync if ``MIN_VERSIONS`` shifts.
FBX_USE_UV_SET_NAMES_MIN_VERSION: "tuple[int, ...]" = (2022, 0)


def strip_unsupported(script_text: str, version: "tuple[int, ...]") -> str:
    """Drop every line that references a placeholder requiring a newer Rizom.

    The substitution is line-level: each ``__KEY__`` token in :data:`MIN_VERSIONS`
    whose required version exceeds *version* causes the entire containing
    line to disappear. Lua's trailing-comma tolerance in tables means
    removing the last entry before ``})`` stays parse-valid.

    Pre-existing constraint: every gated placeholder must live on its own
    line in the source ``.lua`` -- otherwise dropping the line also drops
    sibling 2020.1-compatible keys on the same line.
    """
    if not version:
        return script_text
    out_lines = []
    for line in script_text.splitlines(keepends=True):
        keep = True
        for key, min_ver in MIN_VERSIONS.items():
            if f"__{key}__" in line and version < min_ver:
                keep = False
                break
        if keep:
            out_lines.append(line)
    return "".join(out_lines)
