# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Substance Painter parameters exposed to the bridge UI.

Mirrors :mod:`mayatk.mat_utils.marmoset_bridge.parameters` so the bridge
slots class stays identical in shape.

Each entry maps a placeholder token (e.g. ``__PAINTER_RESOLUTION__``) to
a widget spec. The slot scans the selected template for these tokens,
shows only the matching widgets, and substitutes user values into the
template before shipping it to Painter.

Two rendering contexts -- the bridge picks the right one per call site:

* **CLI** (``LAUNCH_ARGS``) -- raw values, no quoting. ``2048`` ->
  ``"2048"``; ``"C:/path"`` -> ``"C:/path"``. ``subprocess.Popen`` with
  ``shell=False`` will pass each entry as one argv slot.
* **JS** (``RPC_SCRIPT``) -- escaped JS literals for inlining inside
  Painter's JS RPC body. ``"C:/path"`` -> ``'"C:/path"'``; ``True`` ->
  ``"true"``.

To expose a new Painter knob:
  1. Add an entry to :data:`PARAMS` below.
  2. Reference ``__YOUR_KEY__`` in any ``templates/*.py`` LAUNCH_ARGS or
     RPC_SCRIPT body.

Known limitations
-----------------

* **Presence-only CLI flags** (e.g. Painter's ``--shader-balanced``,
  ``--mesh-map-bake``) don't fit the ``__KEY__`` substitution shape --
  the flag must either be present or absent, not given a value. Wire
  those into a template's ``LAUNCH_ARGS`` unconditionally, or add a
  conditional-flag mechanism if the need arises.
* **Empty path values** substitute as ``""``, producing an empty argv
  slot if the template puts ``__PATH__`` after a flag. Template authors
  should avoid that pattern; the bridge does not auto-skip empty pairs.
"""

from __future__ import annotations

from typing import Any

from uitk.bridge import AttributeSpec, Formatters, Parameters as _BridgeParams


# Painter has two substitution contexts:
#
# * ``LAUNCH_ARGS`` -- raw argv tokens (``subprocess.Popen(..., shell=False)``
#   passes each entry as a single token, so no quoting). Use :func:`cli_raw`.
# * ``RPC_SCRIPT`` -- JavaScript literals embedded in the RPC body. Use
#   :func:`js_literal` (double-quoted, escapes backslashes + quotes).
#
# The boolean ``PAINTER_INCLUDE_TEXTURES`` triggers an out-of-band
# texture stage: the bridge walks the selection's shading networks via
# :meth:`mayatk.mat_utils.MatUtils.get_texture_paths` and copies each
# resolved file into the FBX output folder. Nothing is substituted into
# argv -- Painter discovers the textures by scanning the folder.


# Display order is iteration order over this dict.
#
# NOTE on missing project-setup knobs: earlier Painter releases accepted
# ``--resolution``, ``--normal-map-format``, ``--uvtile-mode`` and
# ``--template`` on the CLI. Current Painter (verified 2026-05-22) rejects
# every one of them with a help-popup that prevents launch.
#
# The Painter-side ``substance_rpc`` plugin is that missing surface: knobs
# it can reach (``PAINTER_RESOLUTION``, and the bake source the
# ``BAKE_SOURCE_SET`` row defines) are applied through the plugin --
# immediately on an open project, otherwise held and replayed when one
# opens. The rest (normal-map format, project template, tangent mode) are
# still New Project dialog territory: they are only honoured at project
# *creation*, which the plugin does not drive.
PARAMS: "dict[str, AttributeSpec]" = {
    # Shared across every hand-off bridge (uitk owns the one spec);
    # resolved by the DCC bridge-slots base.
    "SCOPE": _BridgeParams.scope_spec(),
    "CARRIER": _BridgeParams.carrier_spec(),
    # ------------------------------------------------------------------
    # Project setup (applied Painter-side once the project is open)
    # ------------------------------------------------------------------
    "PAINTER_RESOLUTION": AttributeSpec(
        key="PAINTER_RESOLUTION",
        label="Map Resolution",
        kind="choice",
        default=4096,
        choices=[
            ("Project default", 0),
            ("512", 512),
            ("1024 (1K)", 1024),
            ("2048 (2K)", 2048),
            ("4096 (4K)", 4096),
            ("8192 (8K)", 8192),
        ],
        tooltip=(
            "Document resolution every texture set is created at.\n\n"
            "Painter dropped the ``--resolution`` CLI flag, so this is\n"
            "applied through the ``substance_rpc`` plugin instead: on a\n"
            "project that is already open it takes effect immediately;\n"
            "on a fresh launch the plugin holds it and applies it the\n"
            "moment the New Project wizard finishes.\n\n"
            "'Project default' leaves Painter's own setting alone and\n"
            "skips the RPC call entirely."
        ),
    ),
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
                "Marmoset bridge). A non-empty set ships automatically --\n"
                "there is no second checkbox to remember.",
            ),
            (
                "Select",
                "select_bake_source",
                "Select the scene's bake-source set members, hidden ones included.",
                "select",
            ),
            (
                "Clear",
                "clear_bake_source",
                "Delete the bake-source set, so sends stop shipping a bake\n"
                "source. The geometry itself is untouched.",
                "clear",
            ),
        ],
        tooltip=(
            "The scene's bake source (a scene objectSet shared with the\n"
            "Marmoset bridge). Define it once from a selection; it lives\n"
            "in the scene, independent of the Scope above.\n\n"
            "Whenever the set has members, a send also exports them to a\n"
            "companion ``<name>_source.fbx`` and wires it into Painter's\n"
            "baking options as the Hipoly Mesh (Painter's name for the\n"
            "slot). An empty/absent set ships nothing -- the set's contents\n"
            "ARE the switch.\n\n"
            "Hidden geometry needs no special handling: FBX carries it\n"
            "verbatim, so the scene is never modified by the export."
        ),
    ),
    "PAINTER_SPLIT_BY_UDIM": AttributeSpec(
        key="PAINTER_SPLIT_BY_UDIM",
        label="Split by UDIM",
        kind="bool",
        default=False,
        tooltip=(
            "Create one texture set per UDIM tile (Painter's\n"
            "``--split-by-udim`` presence flag). Only useful if the mesh\n"
            "has UVs laid out across multiple tiles -- on a single-UV mesh\n"
            "Painter ignores the flag."
        ),
    ),
    # ------------------------------------------------------------------
    # Iray render (render.py template -- BLOCKED on Painter plugin)
    # ------------------------------------------------------------------
    "PAINTER_RENDER_WIDTH": AttributeSpec(
        key="PAINTER_RENDER_WIDTH",
        label="Render Width",
        kind="int",
        default=1920,
        minimum=128,
        maximum=8192,
        step=64,
        tooltip="Iray output image width in pixels.",
    ),
    "PAINTER_RENDER_HEIGHT": AttributeSpec(
        key="PAINTER_RENDER_HEIGHT",
        label="Render Height",
        kind="int",
        default=1080,
        minimum=128,
        maximum=8192,
        step=64,
        tooltip="Iray output image height in pixels.",
    ),
    "PAINTER_RENDER_SAMPLES": AttributeSpec(
        key="PAINTER_RENDER_SAMPLES",
        label="Iray Samples",
        kind="choice",
        default=128,
        choices=[
            ("Draft (32)", 32),
            ("Preview (128)", 128),
            ("Final (512)", 512),
            ("Hero (1024)", 1024),
        ],
        tooltip=(
            "Iray samples per pixel. More = cleaner image, slower render.\n"
            "Draft for blocking; Hero for marketing-quality stills."
        ),
    ),
    "PAINTER_RENDER_OUTPUT_PATH": AttributeSpec(
        key="PAINTER_RENDER_OUTPUT_PATH",
        label="Render Output",
        kind="path",
        default="",
        tooltip=(
            "Where Painter saves the rendered image (.png / .exr).\n"
            "Leave empty to default to ``<scene_dir>/painter_render.png``."
        ),
    ),
    "PAINTER_INCLUDE_TEXTURES": AttributeSpec(
        key="PAINTER_INCLUDE_TEXTURES",
        label="Include Textures",
        kind="bool",
        default=True,
        tooltip=(
            "Auto-collect file textures from the selection's assigned\n"
            "materials and stage them alongside the FBX in the output\n"
            "folder. Painter's New Project dialog can then point at the\n"
            "same folder via 'Import Baked Maps' to wire them into\n"
            "texture sets -- Painter auto-detects channel by the filename\n"
            "suffix (e.g. '_normal', '_ao').\n\n"
            "Channel-packed sources (ORM / MRAO / MSAO /\n"
            "MetallicSmoothness / AlbedoTransparency) are always split\n"
            "into their component maps on the way out: Painter's\n"
            "filename-suffix detection has no concept of a packed file,\n"
            "so the packed one is dead weight while its channels are\n"
            "exactly what Painter wants.\n\n"
            "Off = ship only the FBX; the artist wires textures by hand."
        ),
    ),
    "PAINTER_TEXTURE_AFFIX": AttributeSpec(
        key="PAINTER_TEXTURE_AFFIX",
        label="Texture Affix",
        kind="affix",
        default={"text": "", "mode": "auto"},
        tooltip=(
            "Optional affix applied to every staged texture's name.\n"
            "Useful for namespacing maps in Painter's shelf -- 'character_'\n"
            "renames 'body_normal.png' to 'character_body_normal.png' on\n"
            "the way out.\n\n"
            "The icon button pins the side when the spelling does not say:\n"
            "<b>Auto</b> -> <b>Suffix</b> -> <b>Prefix</b>. Under Auto a\n"
            "leading '_' ('_hero') reads as a suffix and a trailing '_'\n"
            "('hero_') as a prefix.\n\n"
            "A suffix lands BEFORE the map-type token ('body_hero_Normal',\n"
            "never 'body_Normal_hero'): Painter classifies a map by the\n"
            "last token of its filename, so anything after it would make\n"
            "the map unrecognisable.\n\n"
            "Idempotent: a name that already carries the affix keeps\n"
            "exactly one, so re-running never doubles it.\n\n"
            "Disabled when Include Textures is off."
        ),
    ),
}


class Parameters:
    """Parameters — module namespace."""

    #: The parameter registry, exposed on the class so a bridge slot can hand
    #: this class to the shared base as its ``params_module`` (the base reads
    #: ``params_module.PARAMS`` and ``.referenced_keys``) — no module-level
    #: re-export shim required.
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
    def affix_parts(value: "Any", *, default: str = "prefix") -> "tuple[str, str]":
        """``(prefix, suffix)`` for an ``affix`` param value (delegates to uitk)."""
        return _BridgeParams.affix_parts(value, default=default)

    @staticmethod
    def render_cli_context(values: "dict[str, Any]") -> "dict[str, str]":
        """Format *values* for ``LAUNCH_ARGS`` -- raw, no quoting."""
        return _BridgeParams.render_context(
            values, PARAMS, formatter=Formatters.cli_raw
        )

    @staticmethod
    def render_js_context(values: "dict[str, Any]") -> "dict[str, str]":
        """Format *values* for ``RPC_SCRIPT`` -- JS-literal quoting/escaping."""
        return _BridgeParams.render_context(
            values, PARAMS, formatter=Formatters.js_literal
        )
