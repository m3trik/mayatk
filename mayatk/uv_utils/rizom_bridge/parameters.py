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

import re
from pathlib import Path
from typing import Any

from uitk.bridge import AttributeSpec, Formatters, Parameters as _BridgeParams


# Targets Lua scripts -- ``lua_literal`` produces lowercase ``true`` /
# ``false`` and bare numeric / string literals suitable for inlining
# into ``scripts/*.lua`` preset bodies.
_FORMATTER = Formatters.lua_literal


# Display order is iteration order over this dict.
#
# NOTE: ``ZomPack.Margin`` and ``ZomPack.Quality`` are intentionally absent --
# RizomUV 2020.1 crashes (access violation) the moment either parameter is
# set, even to its documented default. SideFX Labs and the C4D bridge omit
# them too. Re-add as registry entries once we move to a release where this
# is fixed.
PARAMS: "dict[str, AttributeSpec]" = {
    # Shared across every hand-off bridge (uitk owns the one spec);
    # resolved by the DCC bridge-slots base.
    "SCOPE": _BridgeParams.scope_spec(),
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
            ("0  Keep current scale", 0),
            ("1  Uniform (3D area)", 1),
            ("2  Avg texel density", 2),
        ],
        tooltip=(
            "ZomPack Scaling.Mode -- how shells are scaled before packing.\n"
            "Keep current scale (0): leave incoming UV scale untouched.\n"
            "Uniform 3D area (1): set each shell's UV area to its 3D area.\n"
            "Avg texel density (2, default): equalize texel density.\n"
            "\n"
            "EXPECT NO VISIBLE CHANGE in two common cases (measured on\n"
            "2020.1): (a) 1 vs 2 differ only by a GLOBAL scale factor, which\n"
            "every Layout Scale except 'Keep positions' renormalizes away --\n"
            "so at the default they are equivalent; (b) all three agree when\n"
            "the selection's incoming UVs already have consistent texel\n"
            "density, which is the normal state after any unwrap. This knob\n"
            "only bites on a selection whose objects disagree on texel\n"
            "density, and only when comparing 0 against 1 or 2.\n"
            "\n"
            "To MAINTAIN the existing scale between objects, set this to\n"
            "'Keep current scale' AND Layout Scale to 'Keep positions'."
        ),
    ),
    "LAYOUT_SCALING_MODE": AttributeSpec(
        key="LAYOUT_SCALING_MODE",
        label="Layout Scale",
        kind="choice",
        default=2,
        choices=[
            ("0  Keep positions", 0),
            ("1  Translate only", 1),
            ("2  Best fit (uniform)", 2),
            ("3  Force fit (non-uniform)", 3),
        ],
        tooltip=(
            "ZomPack LayoutScalingMode -- how the packed layout is fit to the tile.\n"
            "Keep positions (0): don't rescale/reposition (required for the\n"
            "'maintain scale between objects' workflow -- any other value\n"
            "rescales even locked islands).\n"
            "Translate only (1) / Best fit uniform (2, default) / Force fit (3)."
        ),
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
            "Rotation step in degrees.\n90 = axis-aligned, 1 = free rotation (slowest)."
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
            "Packer solver iterations.\nHigher = tighter packing, slower convergence."
        ),
    ),
    "SCALING_MIX": AttributeSpec(
        key="SCALING_MIX",
        label="Mix Scale",
        kind="bool",
        default=False,
        tooltip=(
            "Mix incoming UV scale with the packer's computed scale.\n"
            "Intended for repacking a layout you want to mostly preserve;\n"
            "off = fully recompute scale from scratch.\n"
            "MEASURED NO-OP on RizomUV 2020.1 -- on and off save a\n"
            "byte-identical result. Effect on >= 2022 is unverified."
        ),
    ),
    # NOTE: island spacing + tile margin are NOT registry entries -- they're
    # derived, see DERIVED_KEYS / Parameters.derived_values below.
    # Post-pack placement (ZomDeform). Both probe-verified on 2020.1:
    # ZomDeform accepts a row-major 3x3 UV-space Transform, so target-UDIM
    # translation and fractional-tile compression need no version gate.
    "TARGET_UDIM": AttributeSpec(
        key="TARGET_UDIM",
        label="Target UDIM",
        kind="int",
        default=1001,
        minimum=1001,
        maximum=1100,
        step=1,
        tooltip=(
            "UDIM tile the packed layout lands in (1001-1100).\n"
            "1001 = the 0-1 tile, 1002 = one tile right (u 1-2),\n"
            "1011 = one tile up (v 1-2). Applied as a whole-layout\n"
            "translate after packing."
        ),
    ),
    "UV_AREA": AttributeSpec(
        key="UV_AREA",
        label="Tile Coverage",
        kind="choice",
        default=0,
        choices=[
            ("Full tile", 0),
            ("Half (U 0-0.5)", 1),
            ("Half (V 0-0.5)", 2),
            ("Quarter (bottom-left)", 3),
        ],
        tooltip=(
            "Fraction of the target tile the packed layout occupies.\n"
            "The layout is packed for the full tile, then compressed into\n"
            "the chosen region (anchored bottom-left); island spacing\n"
            "compresses proportionally."
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
        label="Mix",
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
    # Auto-seam detection (ZomSelect Auto={...})
    # ------------------------------------------------------------------
    "WELD_SEAMS": AttributeSpec(
        key="WELD_SEAMS",
        label="Weld First",
        kind="bool",
        default=True,
        tooltip=(
            "Weld ALL existing seams before auto-seaming so the result is\n"
            "a clean re-unwrap of the surface. Off = keep the incoming\n"
            "seams and only add the newly detected cuts on top."
        ),
    ),
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
            "Lower = more cuts; ~39 suits most hard-surface meshes."
        ),
    ),
    "DEVELOPABILITY": AttributeSpec(
        key="DEVELOPABILITY",
        label="Developability",
        kind="float",
        default=0.5,
        minimum=0.0,
        maximum=1.0,
        step=0.05,
        decimals=2,
        tooltip=(
            "Mosaic segmentation threshold (organic unwrap).\n"
            "Lower = fewer, larger islands that flatten with more distortion;\n"
            "higher = more, flatter islands with more seams."
        ),
    ),
    "FIT_CONES": AttributeSpec(
        key="FIT_CONES",
        label="Fit Cones",
        kind="bool",
        default=False,
        tooltip=(
            "Mosaic QuasiDevelopable.FitCones (organic unwrap).\n"
            "On = fit cones / cylinders (fewer, cleaner seams on tubular\n"
            "shapes) at a solve-time cost; off (default) = planes only."
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


# Preset-level version gate: a ``-- @min_rizom: X.Y`` marker in a preset's
# leading comment hides the WHOLE preset (combo entry + execution) below
# that Rizom version. Token-level ``MIN_VERSIONS`` gating strips single
# lines, which can't express a preset whose core mechanism (e.g. the
# ZomPack ``WorkingSet`` field in pack_into_existing) doesn't exist on
# older Rizom -- stripping the line would silently change semantics
# instead of failing loudly.
_PRESET_MIN_VERSION_RE = re.compile(
    r"^--\s*@min_rizom:\s*(\d+(?:\.\d+)*)\s*$", re.MULTILINE
)

# Inline per-LINE version range: ``... , -- @min_rizom_line: 2022.0`` keeps
# the line only on Rizom >= that version; ``-- @max_rizom_line: 2021.9999``
# keeps it only <= that version. Unlike ``MIN_VERSIONS`` (keyed on the param
# token) this is keyed on the line itself, so two lines can render the SAME
# param token under different field names for different Rizom versions -- e.g.
# ``SpacingSize=__PACK_SPACING__`` (<= 2021) vs ``PaddingSize=__PACK_SPACING__``
# (>= 2022), the probed rename (2020.1: SpacingSize/MarginSize safe,
# PaddingSize access-violates). Re-probe with test/rizom_headless_probe.py.
_INLINE_MIN_LINE_RE = re.compile(r"--\s*@min_rizom_line:\s*(\d+(?:\.\d+)*)")
_INLINE_MAX_LINE_RE = re.compile(r"--\s*@max_rizom_line:\s*(\d+(?:\.\d+)*)")

# Include directives expanded before version-stripping + substitution. Keeps
# the shared group/pack/placement recipe in ONE file (templates/pack_block.lua)
# instead of duplicated across pack.lua + the unwrap_*.lua presets.
_INCLUDE_TOKENS = {"PACK_BLOCK": "pack_block.lua"}
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# Pack gutter tokens that are COMPUTED, not exposed. They render into
# ``ZomPack.SpacingSize`` / ``PaddingSize`` (island-to-island) and
# ``MarginSize`` (tile border) -- the same two gutters Maya's own pack
# operation feeds to ``u3dLayout`` as ``shellSpacing`` / ``tileMargin``.
#
# Why derived: a Rizom round-trip and an in-Maya repack must land on the
# same gutter, or re-packing a Rizom result silently reflows the layout.
# Two hand-dialed spinboxes made that agreement the user's problem; one
# ecosystem rule (:meth:`UvUtils.calculate_uv_padding`) makes it structural.
# The ratio mirrors the pack op exactly: tile margin is HALF the island
# spacing (see ``UvUtils.unwrap_cylinder`` / the ``tb000`` pack slot).
#
# Resolution-invariant by construction: normalized padding is
# ``(map_size / 256) / map_size`` == 1/256 for every map size, so the value
# is stable whatever PACK_RESOLUTION is set to -- 4 px at 1024, 16 px at
# 4096, always the same fraction of the tile.
DERIVED_KEYS = ("PACK_SPACING", "PACK_MARGIN")


def _parse_version_literal(text: str) -> "tuple[int, ...]":
    """``"2022.0"`` -> ``(2022, 0)``; padded to length 2 for tuple compare."""
    parsed = tuple(int(p) for p in text.split("."))
    return parsed if len(parsed) >= 2 else parsed + (0,) * (2 - len(parsed))


class Parameters:
    """Parameters — module namespace."""

    @staticmethod
    def expand_includes(script_text: str) -> str:
        """Expand ``__PACK_BLOCK__``-style include tokens to their partial's text.

        Runs before :meth:`strip_unsupported` and param substitution so the
        included lines participate in both (version-gating + ``__KEY__``
        replacement). Idempotent -- once expanded the token is gone.

        Only a line whose sole non-whitespace content is the token is
        expanded -- an in-comment mention of the token is left untouched.
        (The bridge's ``StrUtils.replace_delimited`` param substitution is a
        blind replace that WOULD clobber comments; the include expander must
        not share that footgun, since the whole point is a multi-line block.)
        """
        markers = {f"__{token}__": filename for token, filename in _INCLUDE_TOKENS.items()}
        out = []
        for line in script_text.splitlines(keepends=True):
            filename = markers.get(line.strip())
            if filename:
                out.append((_TEMPLATE_DIR / filename).read_text(encoding="utf-8"))
            else:
                out.append(line)
        return "".join(out)

    @staticmethod
    def preset_min_version(script_text: str) -> "tuple[int, ...] | None":
        """Minimum Rizom version a preset declares, or ``None`` if ungated.

        Parses the ``@min_rizom`` marker (see :data:`_PRESET_MIN_VERSION_RE`).
        The result is padded to length 2 so single-segment versions compare
        correctly against ``(year, minor)`` tuples (same convention as
        ``RizomUVBridge.rizom_version``).
        """
        match = _PRESET_MIN_VERSION_RE.search(script_text or "")
        return _parse_version_literal(match.group(1)) if match else None

    @staticmethod
    def referenced_keys(script_text: str) -> "set[str]":
        """Registered keys present in *script_text* (delegates to uitk.bridge).

        Includes are expanded first so tokens living only inside a shared
        partial (``templates/pack_block.lua``) are still discovered for panel
        visibility.
        """
        return _BridgeParams.referenced_keys(
            Parameters.expand_includes(script_text), PARAMS
        )

    @staticmethod
    def defaults() -> "dict[str, Any]":
        """Return ``{key: default}`` for every registered parameter."""
        return _BridgeParams.defaults(PARAMS)

    @staticmethod
    def derived_values(values: "dict[str, Any]") -> "dict[str, float]":
        """Return the computed pack-gutter tokens (see :data:`DERIVED_KEYS`).

        Both come off the single ecosystem padding rule,
        :meth:`mayatk.uv_utils.UvUtils.calculate_uv_padding`, so a Rizom
        round-trip packs to the same gutter an in-Maya ``u3dLayout`` pack
        would -- island spacing = the normalized padding, tile margin =
        half of it, matching the ``shellSpacing`` / ``tileMargin`` pair the
        pack slot passes.

        Imported lazily: this module is otherwise Maya-free (the panel
        imports it to build widgets), and ``_uv_utils`` pulls ``maya.cmds``.
        """
        from mayatk.uv_utils._uv_utils import UvUtils

        try:
            map_size = int(values.get("PACK_RESOLUTION") or 0)
        except (TypeError, ValueError):
            map_size = 0
        if map_size <= 0:  # 0 would divide by zero in the normalize step
            map_size = PARAMS["PACK_RESOLUTION"].default
        spacing = UvUtils.calculate_uv_padding(map_size, normalize=True)
        spacing_key, margin_key = DERIVED_KEYS
        return {spacing_key: spacing, margin_key: spacing / 2}

    @staticmethod
    def render_context(values: "dict[str, Any]") -> "dict[str, str]":
        """Format *values* for ``StrUtils.replace_delimited`` using Lua literals.

        The derived gutter tokens are folded in LAST so they win over any
        stale ``PACK_SPACING`` / ``PACK_MARGIN`` left in a saved JSON preset
        from when the two were spinboxes.
        """
        merged = dict(values)
        merged.update(Parameters.derived_values(merged))
        return _BridgeParams.render_context(merged, PARAMS, formatter=_FORMATTER)

    @staticmethod
    def strip_unsupported(script_text: str, version: "tuple[int, ...]") -> str:
        """Drop every line that references a placeholder requiring a newer Rizom.

        The substitution is line-level: each ``__KEY__`` token in :data:`MIN_VERSIONS`
        whose required version exceeds *version* causes the entire containing
        line to disappear. Lua's trailing-comma tolerance in tables means
        removing the last entry before ``})`` stays parse-valid.

        Pre-existing constraint: every gated placeholder must live on its own
        line in the source ``.lua`` -- otherwise dropping the line also drops
        sibling 2020.1-compatible keys on the same line.

        Also honors inline per-line markers ``-- @min_rizom_line: X.Y`` /
        ``-- @max_rizom_line: X.Y`` (see :data:`_INLINE_MIN_LINE_RE`) for
        lines gated independently of any param token -- e.g. the
        SpacingSize/PaddingSize field-name split.
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
                m = _INLINE_MIN_LINE_RE.search(line)
                if m and version < _parse_version_literal(m.group(1)):
                    keep = False
            if keep:
                m = _INLINE_MAX_LINE_RE.search(line)
                if m and version > _parse_version_literal(m.group(1)):
                    keep = False
            if keep:
                out_lines.append(line)
        return "".join(out_lines)


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
# The gate is 2022.0 -- originally a conservative midpoint between Titus's
# 2020.1-era reference (no MaxMutations / Resolution / Rotate.Enable) and the
# adevra 2024 Maya bridge (uses all three). It was a GUESS, not a probed
# crash, and ``Resolution`` has since been measured out of it (below).
#
# ``PACK_RESOLUTION`` is deliberately NOT listed: probed safe on a real 2020.1
# (rc 0, file saved) and it is what makes a single send converge. Stripping it
# was the cause of the reported "pack has to be sent twice before it fills the
# UV space" -- verified in mayapy through the real bridge, two sends per
# config: 4 DAG instances packed to 0.5766 of the tile on send 1 and needed a
# second send to reach 0.6655, while sending Resolution lands send 1 at 0.6777
# (better than the old two-send result) and send 2 changes nothing. A mixed
# instances+unique scene gains +11.3%, likewise converged. The trade, recorded
# so it isn't rediscovered: an all-unique-mesh scene is slightly worse at 1024
# (send-1 coverage 0.2124 -> 0.2088). ``ZomPack`` is a heuristic solver, so
# re-sending is a fresh dice roll rather than a refinement -- which is why the
# old behaviour looked like "the second one works".
#
# The other two stay gated because neither earns its cost, not merely out of
# caution: ``MaxMutations`` changed the stacked/instances result by NOTHING at
# any value 25-250 (it only helps a single-mesh grid, 0.8084 -> 0.8394, and
# needs 250 to be stable, at 8x the runtime -- 78s vs 10s), and
# ``Rotate.Enable`` measurably changed nothing at all.
#
# IMPORTANT (for future contributors): each gated placeholder must live on
# its OWN line in the source .lua -- ``strip_unsupported`` drops whole
# lines, so a sibling 2020.1-compatible key on the same line would be
# dropped too. See the ``Rotate={Step=..., Enable=...}`` multi-line layout
# in scripts/*.lua for the pattern.
MIN_VERSIONS: "dict[str, tuple[int, ...]]" = {
    "PACK_MAX_MUTATIONS": (2022, 0),
    "PACK_ROTATE_ENABLE": (2022, 0),
}

# Minimum Rizom version that accepts the nested ``FBX={UseUVSetNames=true}``
# load/save flag. Below this, the bridge emits an empty FBX block (Rizom
# auto-detects format from the file extension). Kept here (rather than
# inline in ``_construct_full_script``) so the gate threshold lives next
# to its peers and stays in sync if ``MIN_VERSIONS`` shifts.
FBX_USE_UV_SET_NAMES_MIN_VERSION: "tuple[int, ...]" = (2022, 0)
