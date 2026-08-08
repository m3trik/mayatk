# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Blender-bridge parameters exposed to the panel.

Each entry maps a placeholder token (e.g. ``__APPLY_UNIT_SCALE__``) to a widget spec. The slot
scans the selected template for these tokens, shows only the matching widgets, and substitutes the
user values into the template before launching Blender (via :func:`StrUtils.replace_delimited`).

Export-affecting knobs (``INCLUDE_MATERIALS`` / ``EMBED_TEXTURES`` / ``TRIANGULATE`` /
``INCLUDE_ANIMATION``) are read by :class:`BlenderBridge` to configure the Maya-side FBX export;
import-affecting knobs (``APPLY_UNIT_SCALE`` / ``INCLUDE_ANIMATION`` / ``CLEAR_SCENE`` /
``FRAME_VIEW``) are substituted into the Blender import template. Each template references the
subset it exposes.

To expose a new knob: add an entry below, then reference ``__YOUR_KEY__`` in any ``templates/*.py``.
Mirrors :mod:`mayatk.mat_utils.marmoset_bridge.parameters` so the slots class stays identical in
shape (and the blendertk ``maya_bridge`` counterpart mirrors this file).
"""

from __future__ import annotations

from typing import Any

from uitk.bridge import AttributeSpec, Formatters, Parameters as _BridgeParams

# Default VALUES live with the Qt-free engine so ``params_defaults()`` still answers
# where this module cannot be imported (a DCC running headless has no Qt); the specs
# below read them, so the two can never drift.
from mayatk.env_utils.blender_bridge._blender_bridge import DEFAULTS


# Templates are executable Blender Python -- substitute user values as Python source literals.
_FORMATTER = Formatters.python_literal


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    # Shared across every hand-off bridge (uitk owns the one spec);
    # resolved by the DCC bridge-slots base.
    "SCOPE": _BridgeParams.scope_spec(default=DEFAULTS["SCOPE"]),
    "INCLUDE_MATERIALS": AttributeSpec(
        key="INCLUDE_MATERIALS",
        label="Include Materials",
        kind="bool",
        default=DEFAULTS["INCLUDE_MATERIALS"],
        tooltip=(
            "Carry materials/shading across. When off, the selection is exported with only\n"
            "the default shader (materials stripped Maya-side); geometry only."
        ),
    ),
    "EMBED_TEXTURES": AttributeSpec(
        key="EMBED_TEXTURES",
        label="Embed Textures",
        kind="bool",
        default=DEFAULTS["EMBED_TEXTURES"],
        tooltip="Embed the texture files inside the FBX so Blender resolves the maps.",
    ),
    "APPLY_UNIT_SCALE": AttributeSpec(
        key="APPLY_UNIT_SCALE",
        label="Apply Unit Scale",
        kind="bool",
        default=DEFAULTS["APPLY_UNIT_SCALE"],
        tooltip=(
            "Convert Maya units (cm) to Blender units (m) on import so objects arrive at the\n"
            "correct real-world size. Off preserves the raw numeric values."
        ),
    ),
    "INCLUDE_ANIMATION": AttributeSpec(
        key="INCLUDE_ANIMATION",
        label="Include Animation",
        kind="bool",
        default=DEFAULTS["INCLUDE_ANIMATION"],
        tooltip="Bake & export keyframes and import them in Blender (off = static mesh hand-off).",
    ),
    "TRIANGULATE": AttributeSpec(
        key="TRIANGULATE",
        label="Triangulate",
        kind="bool",
        default=DEFAULTS["TRIANGULATE"],
        tooltip="Triangulate meshes on export.",
    ),
    "CLEAR_SCENE": AttributeSpec(
        key="CLEAR_SCENE",
        label="Clear Scene First",
        kind="bool",
        default=DEFAULTS["CLEAR_SCENE"],
        tooltip=(
            "Delete the existing scene objects before importing (clean-slate / replace-scene\n"
            "hand-off). Off imports additively into the current scene."
        ),
    ),
    "FRAME_VIEW": AttributeSpec(
        key="FRAME_VIEW",
        label="Frame in View",
        kind="bool",
        # Off by default so the unified template's default behavior matches the old plain
        # "import" template (no selection change / no viewport shading switch); opt in for the
        # old "import_and_frame" behavior.
        default=DEFAULTS["FRAME_VIEW"],
        tooltip=(
            "After import, select the new objects, frame them in the 3D viewport, and switch to\n"
            "material-preview shading."
        ),
    ),
    # ---------------------------------------------------------------- lightmap bake
    # Referenced only by templates/bake_lightmaps.py, so the panel shows these widgets
    # only when that recipe is selected.
    "LIGHTMAP_MODE": AttributeSpec(
        key="LIGHTMAP_MODE",
        label="Lightmap Mode",
        kind="choice",
        default=DEFAULTS["LIGHTMAP_MODE"],
        choices=[
            (
                "Lighting Only (keep PBR)",
                "separated",
                "Bake lighting onto a second UV channel and keep the full PBR material.\n"
                "The engine multiplies albedo x lightmap, so normal/roughness still work.",
            ),
            (
                "Fused Unlit (single map)",
                "fused",
                "Bake albedo x lighting into one map with an unlit material.\n"
                "Renders correctly in ANY glTF viewer, but drops normals and re-lighting.",
            ),
        ],
        tooltip="How the bake is carried into the GLB.",
    ),
    "LIGHTMAP_RESOLUTION": AttributeSpec(
        key="LIGHTMAP_RESOLUTION",
        label="Lightmap Resolution",
        kind="choice",
        default=DEFAULTS["LIGHTMAP_RESOLUTION"],
        choices=[("256", 256), ("512", 512), ("1024", 1024), ("2048", 2048), ("4096", 4096)],
        tooltip="Square lightmap size, per material atlas.",
    ),
    "LIGHTMAP_SAMPLES": AttributeSpec(
        key="LIGHTMAP_SAMPLES",
        label="Samples",
        kind="int",
        default=DEFAULTS["LIGHTMAP_SAMPLES"],
        minimum=1,
        maximum=8192,
        tooltip=(
            "Cycles samples per lightmap. Higher = cleaner indirect light, slower bake.\n"
            "With denoising on, ~256-512 is usually enough for an interior."
        ),
    ),
    "LIGHTMAP_DENOISE": AttributeSpec(
        key="LIGHTMAP_DENOISE",
        label="Denoise",
        kind="bool",
        default=DEFAULTS["LIGHTMAP_DENOISE"],
        tooltip=(
            "Run OpenImageDenoise over each baked map. Cycles does NOT denoise bakes on its\n"
            "own, and baked grain is permanent — leave this on unless comparing raw output."
        ),
    ),
    "LIGHTMAP_DEVICE": AttributeSpec(
        key="LIGHTMAP_DEVICE",
        label="Bake Device",
        kind="choice",
        default=DEFAULTS["LIGHTMAP_DEVICE"],
        choices=[("GPU", "GPU"), ("CPU", "CPU")],
        tooltip="GPU falls back to the CPU automatically when no compute device is found.",
    ),
    "ENVIRONMENT_HDR": AttributeSpec(
        key="ENVIRONMENT_HDR",
        label="Environment HDRI",
        kind="path",
        default=DEFAULTS["ENVIRONMENT_HDR"],
        tooltip=(
            "Equirect .hdr/.exr used as the world light.\n"
            "A Maya scene lit by StingrayPBS IBL exports NO lights — its cubemaps are not\n"
            "FBX-portable — so without this (or Fixture Lights) the bake is black."
        ),
    ),
    "WORLD_STRENGTH": AttributeSpec(
        key="WORLD_STRENGTH",
        label="World Strength",
        kind="float",
        default=DEFAULTS["WORLD_STRENGTH"],
        minimum=0.0,
        maximum=100.0,
        decimals=2,
        tooltip="Environment multiplier. Keep low when fixture lights should dominate.",
    ),
    "FIXTURE_LIGHTS": AttributeSpec(
        key="FIXTURE_LIGHTS",
        label="Lights From Fixtures",
        kind="bool",
        default=DEFAULTS["FIXTURE_LIGHTS"],
        tooltip=(
            "Build real Cycles area lights from the light-fixture meshes, matched to each\n"
            "fixture's size, position and facing.\n"
            "An emissive MAP is only an appearance — this is what actually lights the bake."
        ),
    ),
    "FIXTURE_PATTERN": AttributeSpec(
        key="FIXTURE_PATTERN",
        label="Fixture Name Contains",
        kind="str",
        default=DEFAULTS["FIXTURE_PATTERN"],
        tooltip="Case-insensitive substring picking the fixture meshes (blank = every mesh).",
    ),
    "FIXTURE_WATTS": AttributeSpec(
        key="FIXTURE_WATTS",
        label="Fixture Power (W)",
        kind="float",
        default=DEFAULTS["FIXTURE_WATTS"],
        minimum=0.0,
        maximum=100000.0,
        decimals=1,
        tooltip="Radiant power of each generated fixture light.",
    ),
    "EMISSION_STRENGTH": AttributeSpec(
        key="EMISSION_STRENGTH",
        label="Emission Strength",
        kind="float",
        default=DEFAULTS["EMISSION_STRENGTH"],
        minimum=0.0,
        maximum=1000.0,
        decimals=2,
        tooltip=(
            "Emissive-map strength — an APPEARANCE knob so fixtures read as switched-on.\n"
            "Not the room's light source; use Fixture Lights / Environment HDRI for that."
        ),
    ),
    "TEXTURE_MAX_SIZE": AttributeSpec(
        key="TEXTURE_MAX_SIZE",
        label="Max Texture Size",
        kind="choice",
        default=DEFAULTS["TEXTURE_MAX_SIZE"],
        choices=[("512", 512), ("1024", 1024), ("2048", 2048), ("4096", 4096), ("No limit", 0)],
        tooltip=(
            "Downsize source textures for the web deliverable.\n"
            "Texture bytes ARE the file size: an unlimited export of a 4K PBR set measured\n"
            "96 MB against 1.7 MB at 2048 + WebP."
        ),
    ),
    "IMAGE_FORMAT": AttributeSpec(
        key="IMAGE_FORMAT",
        label="Image Format",
        kind="choice",
        default=DEFAULTS["IMAGE_FORMAT"],
        choices=[
            ("WebP", "WEBP", "Smallest; supported by every WebXR-capable browser."),
            ("JPEG", "JPEG", "Wider legacy support, no alpha."),
            ("Keep source", "AUTO", "Preserve each image's own format (largest)."),
        ],
        tooltip="Texture codec inside the GLB.",
    ),
    "LIGHTMAP_DIR": AttributeSpec(
        key="LIGHTMAP_DIR",
        label="Lightmap Folder",
        kind="path",
        default=DEFAULTS["LIGHTMAP_DIR"],
        tooltip=(
            "Where the HDR (.exr) lightmaps are written.\n"
            "These are wired back into THIS Maya scene, so they belong in the project's\n"
            "textures — not beside the .glb, which may be a delivery folder.\n"
            "Empty uses the project's sourceimages."
        ),
    ),
}


class Parameters:
    """Parameters — module namespace."""

    #: The parameter registry, exposed on the class so a bridge slot can hand
    #: this class to the shared base as its ``params_module`` (the base reads
    #: ``params_module.PARAMS`` and ``.referenced_keys``) — no module-level shim.
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
    def render_context(values: "dict[str, Any]") -> "dict[str, str]":
        """Format *values* for ``StrUtils.replace_delimited`` using Python literals."""
        return _BridgeParams.render_context(values, PARAMS, formatter=_FORMATTER)
