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

from uitk.bridge import (
    AttributeSpec,
    cli_raw,
    js_literal,
    referenced_keys as _refkeys,
    defaults as _defaults,
    render_context as _render_context,
)


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
# every one of them with a help-popup that prevents launch. Until Painter
# brings them back (or a Painter-side plugin re-exposes them via JS), the
# project-setup knobs live inside Painter's New Project dialog.
PARAMS: "dict[str, AttributeSpec]" = {
    # ------------------------------------------------------------------
    # Project setup (Painter CLI flags applied at --mesh launch time)
    # ------------------------------------------------------------------
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


def referenced_keys(script_text: str) -> "set[str]":
    """Registered keys present in *script_text* (delegates to uitk.bridge)."""
    return _refkeys(script_text, PARAMS)


def defaults() -> "dict[str, Any]":
    """Return ``{key: default}`` for every registered parameter."""
    return _defaults(PARAMS)


def render_cli_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``LAUNCH_ARGS`` -- raw, no quoting."""
    return _render_context(values, PARAMS, formatter=cli_raw)


def render_js_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``RPC_SCRIPT`` -- JS-literal quoting/escaping."""
    return _render_context(values, PARAMS, formatter=js_literal)
