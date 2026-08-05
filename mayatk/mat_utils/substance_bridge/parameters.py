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
# it can reach (``PAINTER_RESOLUTION``, ``PAINTER_HIGH_POLY``) are applied
# through the plugin -- immediately on an open project, otherwise held and
# replayed when one opens. The rest (normal-map format, project template,
# tangent mode) are still New Project dialog territory: they are only
# honoured at project *creation*, which the plugin does not drive.
PARAMS: "dict[str, AttributeSpec]" = {
    # Shared across every hand-off bridge (uitk owns the one spec);
    # resolved by the DCC bridge-slots base.
    "SCOPE": _BridgeParams.scope_spec(),
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
                "Marmoset bridge). Also ticks Export Bake Source below.",
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
            "Marmoset bridge). Define it once from a selection; it lives\n"
            "in the scene, independent of the Scope above."
        ),
    ),
    "PAINTER_HIGH_POLY": AttributeSpec(
        key="PAINTER_HIGH_POLY",
        label="Export Bake Source",
        kind="bool",
        default=False,
        tooltip=(
            "Also export the scene's bake-source set to a companion\n"
            "``<name>_source.fbx`` and wire it into Painter's baking\n"
            "options as the Hipoly Mesh (Painter's name for the slot).\n\n"
            "Define the set with the <b>Bake Source</b> row's <b>Set From\n"
            "Selection</b> -- it lives in the scene, so it survives\n"
            "saves and restarts and is independent of the Scope above.\n\n"
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
            "Off = ship only the FBX; the artist wires textures by hand."
        ),
    ),
    "PAINTER_UNPACK_MAPS": AttributeSpec(
        key="PAINTER_UNPACK_MAPS",
        label="Unpack Packed Maps",
        kind="bool",
        default=True,
        tooltip=(
            "Split channel-packed textures into the separate maps Painter\n"
            "can actually read, instead of staging the packed file.\n\n"
            "Painter identifies a map by its filename suffix and has no\n"
            "concept of a packed one, so an ORM / MRAO / MSAO /\n"
            "MetallicSmoothness / AlbedoTransparency file is unusable as\n"
            "shipped -- and only its AO channel is a mesh map at all; the\n"
            "rest are material channels. Each is unpacked into the output\n"
            "folder under the suffix Painter recognises (_AO, _Roughness,\n"
            "_Metallic, ...).\n\n"
            "Off = stage the packed file verbatim, for a pipeline that\n"
            "wants it kept.\n\n"
            "Disabled when Include Textures is off."
        ),
    ),
    "PAINTER_TEXTURE_PREFIX": AttributeSpec(
        key="PAINTER_TEXTURE_PREFIX",
        label="Texture Prefix",
        kind="str",
        default="",
        tooltip=(
            "Optional prefix prepended to every staged texture's filename.\n"
            "Useful for namespacing maps in Painter's shelf -- e.g. a\n"
            "prefix of 'character_' renames 'body_normal.png' to\n"
            "'character_body_normal.png' on the way out.\n\n"
            "Idempotent: if a filename already starts with the prefix it\n"
            "is stripped first, so re-running with the same prefix never\n"
            "doubles it.\n\n"
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
