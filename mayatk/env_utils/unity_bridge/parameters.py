# !/usr/bin/python
# coding=utf-8
"""User-tunable parameters for the Maya->Unity bridge panel.

Two groups: *Export* knobs drive the Maya-side FBX export (read by
:class:`mayatk.env_utils.handoff_export.MayaExportMixin`); *Unity* knobs drive the
copy-to-Assets delivery (read by :class:`unitytk.CopyToAssetsDeliverer`). Unlike the
script-launch bridges these are never substituted into a template -- the Unity
deliverer copies the FBX into the project rather than rendering a live-session
script -- so the panel shows every param (no per-template visibility gating).

Mirrors :mod:`mayatk.env_utils.blender_bridge.parameters` in shape; the blendertk
``unity_bridge`` counterpart mirrors this file.
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


_FORMATTER = python_literal


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    "INCLUDE_MATERIALS": AttributeSpec(
        key="INCLUDE_MATERIALS",
        label="Include Materials",
        kind="bool",
        default=True,
        section="Export",
        tooltip=(
            "Carry materials across. When off, the selection is exported with only\n"
            "the default shader (materials stripped Maya-side); geometry only."
        ),
    ),
    "EMBED_TEXTURES": AttributeSpec(
        key="EMBED_TEXTURES",
        label="Embed Textures",
        kind="bool",
        default=True,
        section="Export",
        tooltip=(
            "Embed the texture files inside the FBX so Unity extracts the maps on\n"
            "import. Off relies on the textures already living in the project."
        ),
    ),
    "TRIANGULATE": AttributeSpec(
        key="TRIANGULATE",
        label="Triangulate",
        kind="bool",
        default=False,
        section="Export",
        tooltip="Triangulate meshes on export.",
    ),
    "ASSETS_SUBDIR": AttributeSpec(
        key="ASSETS_SUBDIR",
        label="Assets Subfolder",
        kind="str",
        default="Imported",
        section="Unity",
        tooltip=(
            "Subfolder under the project's <b>Assets/</b> the FBX is copied into\n"
            "(created if absent). Blank = drop directly in Assets/."
        ),
    ),
    "ASSET_NAME": AttributeSpec(
        key="ASSET_NAME",
        label="Asset Name",
        kind="str",
        default="",
        section="Unity",
        tooltip=(
            "Optional name for the copied FBX (no extension). Blank = use the\n"
            "selected object's name. Invalid filename characters are sanitized."
        ),
    ),
    "LAUNCH_EDITOR": AttributeSpec(
        key="LAUNCH_EDITOR",
        label="Launch Editor",
        kind="bool",
        default=False,
        section="Unity",
        tooltip=(
            "After copying, launch a Unity Editor on the project (auto-detected via\n"
            "Unity Hub). Off = just copy; Unity imports on its next window focus."
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
    """Format *values* for substitution (kept for API parity; Unity renders no script)."""
    return _render_context(values, PARAMS, formatter=_FORMATTER)
