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
# The kind ``"file_list"`` is registered in :mod:`uitk.bridge.spec`; it
# produces a ``list[str]`` widget value that the bridge stages
# out-of-band alongside the FBX export rather than substituting into
# argv directly.


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    # ------------------------------------------------------------------
    # Project setup (Painter CLI flags applied at --mesh launch time)
    # ------------------------------------------------------------------
    "PAINTER_RESOLUTION": AttributeSpec(
        key="PAINTER_RESOLUTION",
        label="Resolution",
        kind="choice",
        default=2048,
        choices=[
            ("512", 512),
            ("1024", 1024),
            ("2048", 2048),
            ("4096", 4096),
            ("8192", 8192),
        ],
        tooltip=(
            "Default texture set resolution Painter will create new\n"
            "documents at. Passed to Painter as ``--resolution <N>``."
        ),
    ),
    "PAINTER_NORMAL_FORMAT": AttributeSpec(
        key="PAINTER_NORMAL_FORMAT",
        label="Normal Format",
        kind="choice",
        default="OpenGL",
        choices=[
            ("OpenGL (Maya, Unity)", "OpenGL"),
            ("DirectX (Unreal, 3ds Max)", "DirectX"),
        ],
        tooltip=(
            "Tangent-space normal convention for new documents.\n"
            "Maya viewports default to OpenGL; Unreal Engine to DirectX.\n"
            "Mismatched normals look 'inverted' on lit surfaces."
        ),
    ),
    "PAINTER_UV_TILE_MODE": AttributeSpec(
        key="PAINTER_UV_TILE_MODE",
        label="UV Mode",
        kind="choice",
        default="UV",
        choices=[
            ("Single UV (one texture set)", "UV"),
            ("UDIM (per-tile texture sets)", "UDIM"),
        ],
        tooltip=(
            "How Painter slices the mesh into texture sets.\n"
            "UDIM creates one set per UV tile -- only useful if the\n"
            "mesh actually has UVs laid out across multiple tiles."
        ),
    ),
    "PAINTER_PROJECT_TEMPLATE": AttributeSpec(
        key="PAINTER_PROJECT_TEMPLATE",
        label="Project Template",
        # ``painter_template_file`` is a substance-specific kind registered
        # at import time by :mod:`mayatk.mat_utils.substance_bridge.substance_bridge_slots`
        # -- a single-file picker filtered on ``.spt`` / ``.spp``. The
        # standard ``path`` kind is a directory picker, which would
        # produce a folder path Painter's ``--template`` flag rejects.
        kind="painter_template_file",
        default="",
        tooltip=(
            "Optional Painter project template (.spt) to seed the new\n"
            "project with (channel layout, default smart materials, etc.).\n"
            "Leave empty to use Painter's default."
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
    "PAINTER_BAKED_MAPS": AttributeSpec(
        key="PAINTER_BAKED_MAPS",
        label="Import Baked Maps",
        kind="file_list",
        default=[],
        tooltip=(
            "Pre-baked mesh maps to ship to Painter alongside the FBX\n"
            "(AO, normals, curvature, etc.).\n\n"
            "The bridge copies each selected file into the FBX output\n"
            "folder and records the list in the material manifest. In\n"
            "Painter's New Project dialog, click 'Import Baked Maps' and\n"
            "point at the same folder to wire them into texture sets --\n"
            "Painter auto-detects channel by the filename suffix\n"
            "(e.g. '_normal', '_ao')."
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
