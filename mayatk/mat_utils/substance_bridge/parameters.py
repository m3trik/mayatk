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

import os
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class SubstanceParam:
    """Describes one tunable Painter parameter and how to render its widget.

    widget_type values:
      ``"int"`` / ``"float"`` -- spin boxes.
      ``"choice"`` -- combo box; ``choices`` carries ``(label, value)`` pairs.
      ``"bool"`` -- check box.
      ``"path"`` -- single-file picker (default filter is .spt project templates).
      ``"file_list"`` -- multi-file picker that produces a ``List[str]``.
          Skipped during CLI/JS substitution; the bridge stages the listed
          files alongside the FBX export and records them in the manifest.
    """

    key: str
    label: str
    widget_type: str  # "int" | "float" | "choice" | "bool" | "path" | "file_list"
    default: Any

    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    decimals: int = 0

    choices: Optional[List[Tuple[str, Any]]] = None
    tooltip: str = ""

    def format_cli(self, value: Any) -> str:
        """Render *value* as a raw CLI argument value.

        Strings pass through verbatim; ``subprocess.Popen(..., shell=False)``
        treats each argv entry as a single token, so no quoting is needed.
        Lists (``file_list``) are joined with the OS path separator -- but
        templates should not generally substitute file_list params into
        LAUNCH_ARGS; the bridge handles file_list staging out-of-band.
        """
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (list, tuple)):
            return os.pathsep.join(str(v) for v in value)
        if isinstance(value, float):
            if self.decimals:
                return f"{value:.{self.decimals}f}".rstrip("0").rstrip(".") or "0"
            return repr(value)
        return str(value)

    def format_js(self, value: Any) -> str:
        """Render *value* as a JS literal for inlining into RPC_SCRIPT."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            # JS string literal -- double-quoted; escape backslashes and quotes.
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        if isinstance(value, float):
            if self.decimals:
                return f"{value:.{self.decimals}f}".rstrip("0").rstrip(".") or "0"
            return repr(value)
        return str(value)


# Display order is iteration order over this dict.
PARAMS: "dict[str, SubstanceParam]" = {
    # ------------------------------------------------------------------
    # Project setup (Painter CLI flags applied at --mesh launch time)
    # ------------------------------------------------------------------
    "PAINTER_RESOLUTION": SubstanceParam(
        key="PAINTER_RESOLUTION",
        label="Resolution",
        widget_type="choice",
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
    "PAINTER_NORMAL_FORMAT": SubstanceParam(
        key="PAINTER_NORMAL_FORMAT",
        label="Normal Format",
        widget_type="choice",
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
    "PAINTER_UV_TILE_MODE": SubstanceParam(
        key="PAINTER_UV_TILE_MODE",
        label="UV Mode",
        widget_type="choice",
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
    "PAINTER_PROJECT_TEMPLATE": SubstanceParam(
        key="PAINTER_PROJECT_TEMPLATE",
        label="Project Template",
        widget_type="path",
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
    "PAINTER_RENDER_WIDTH": SubstanceParam(
        key="PAINTER_RENDER_WIDTH",
        label="Render Width",
        widget_type="int",
        default=1920,
        minimum=128,
        maximum=8192,
        step=64,
        tooltip="Iray output image width in pixels.",
    ),
    "PAINTER_RENDER_HEIGHT": SubstanceParam(
        key="PAINTER_RENDER_HEIGHT",
        label="Render Height",
        widget_type="int",
        default=1080,
        minimum=128,
        maximum=8192,
        step=64,
        tooltip="Iray output image height in pixels.",
    ),
    "PAINTER_RENDER_SAMPLES": SubstanceParam(
        key="PAINTER_RENDER_SAMPLES",
        label="Iray Samples",
        widget_type="choice",
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
    "PAINTER_RENDER_OUTPUT_PATH": SubstanceParam(
        key="PAINTER_RENDER_OUTPUT_PATH",
        label="Render Output",
        widget_type="path",
        default="",
        tooltip=(
            "Where Painter saves the rendered image (.png / .exr).\n"
            "Leave empty to default to ``<scene_dir>/painter_render.png``."
        ),
    ),
    "PAINTER_BAKED_MAPS": SubstanceParam(
        key="PAINTER_BAKED_MAPS",
        label="Import Baked Maps",
        widget_type="file_list",
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


_PLACEHOLDER_RE = re.compile(r"__([A-Z][A-Z0-9_]*)__")


def referenced_keys(script_text: str) -> "set[str]":
    """Return registered placeholder keys present in *script_text*.

    Any ``__KEY__`` token that doesn't match a registry entry is silently
    ignored -- substitution leaves it intact, and Painter will surface the
    error if it actually mattered.
    """
    found = set(_PLACEHOLDER_RE.findall(script_text))
    return found & PARAMS.keys()


def defaults() -> "dict[str, Any]":
    """Return ``{key: default}`` for every registered parameter."""
    return {key: spec.default for key, spec in PARAMS.items()}


def render_cli_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``LAUNCH_ARGS`` -- raw, no quoting."""
    out: "dict[str, str]" = {}
    for key, val in values.items():
        spec = PARAMS.get(key)
        out[key] = spec.format_cli(val) if spec else str(val)
    return out


def render_js_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``RPC_SCRIPT`` -- JS-literal quoting/escaping."""
    out: "dict[str, str]" = {}
    for key, val in values.items():
        spec = PARAMS.get(key)
        out[key] = spec.format_js(val) if spec else str(val)
    return out
