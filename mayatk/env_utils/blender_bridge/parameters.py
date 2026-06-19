# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Blender-bridge parameters exposed to the panel.

Each entry maps a placeholder token (e.g. ``__APPLY_UNIT_SCALE__``) to a widget spec. The slot
scans the selected template for these tokens, shows only the matching widgets, and substitutes the
user values into the template before launching Blender (via :func:`StrUtils.replace_delimited`).

Export-affecting knobs (``INCLUDE_MATERIALS`` / ``EMBED_TEXTURES`` / ``TRIANGULATE`` /
``INCLUDE_ANIMATION``) are read by :class:`BlenderBridge` to configure the Maya-side FBX export;
import-affecting knobs (``APPLY_UNIT_SCALE`` / ``INCLUDE_ANIMATION`` / ``FRAME_VIEW``) are
substituted into the Blender import template. Each template references the subset it exposes.

To expose a new knob: add an entry below, then reference ``__YOUR_KEY__`` in any ``templates/*.py``.
Mirrors :mod:`mayatk.mat_utils.marmoset_bridge.parameters` so the slots class stays identical in
shape (and the blendertk ``maya_bridge`` counterpart mirrors this file).
"""
from __future__ import annotations

from typing import Any

from uitk.bridge import (
    AttributeSpec,
    python_literal,
    referenced_keys as _refkeys,
    defaults as _defaults,
    render_context as _render_context,
)


# Templates are executable Blender Python -- substitute user values as Python source literals.
_FORMATTER = python_literal


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    "INCLUDE_MATERIALS": AttributeSpec(
        key="INCLUDE_MATERIALS",
        label="Include Materials",
        kind="bool",
        default=True,
        tooltip=(
            "Carry materials/shading across. When off, the selection is exported with only\n"
            "the default shader (materials stripped Maya-side); geometry only."
        ),
    ),
    "EMBED_TEXTURES": AttributeSpec(
        key="EMBED_TEXTURES",
        label="Embed Textures",
        kind="bool",
        default=True,
        tooltip="Embed the texture files inside the FBX so Blender resolves the maps.",
    ),
    "APPLY_UNIT_SCALE": AttributeSpec(
        key="APPLY_UNIT_SCALE",
        label="Apply Unit Scale",
        kind="bool",
        default=True,
        tooltip=(
            "Convert Maya units (cm) to Blender units (m) on import so objects arrive at the\n"
            "correct real-world size. Off preserves the raw numeric values."
        ),
    ),
    "INCLUDE_ANIMATION": AttributeSpec(
        key="INCLUDE_ANIMATION",
        label="Include Animation",
        kind="bool",
        default=False,
        tooltip="Bake & export keyframes and import them in Blender (off = static mesh hand-off).",
    ),
    "TRIANGULATE": AttributeSpec(
        key="TRIANGULATE",
        label="Triangulate",
        kind="bool",
        default=False,
        tooltip="Triangulate meshes on export.",
    ),
    "FRAME_VIEW": AttributeSpec(
        key="FRAME_VIEW",
        label="Frame in View",
        kind="bool",
        default=True,
        tooltip="After import, frame the imported objects in the 3D viewport.",
    ),
}


def referenced_keys(script_text: str) -> "set[str]":
    """Registered keys present in *script_text* (delegates to uitk.bridge)."""
    return _refkeys(script_text, PARAMS)


def defaults() -> "dict[str, Any]":
    """Return ``{key: default}`` for every registered parameter."""
    return _defaults(PARAMS)


def render_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``StrUtils.replace_delimited`` using Python literals."""
    return _render_context(values, PARAMS, formatter=_FORMATTER)
