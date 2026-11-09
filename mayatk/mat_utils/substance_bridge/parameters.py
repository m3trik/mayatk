# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Substance Painter parameters exposed to the bridge UI.

Mirrors the Marmoset bridge's ``parameters`` so the bridge slots class
stays identical in shape.

Each entry maps a ``__KEY__`` token to a widget spec. The panel scans the
selected script for these tokens and shows only the matching rows; at a send
every value reaches the script, run in Painter, as ``args["KEY"]`` -- nothing
is substituted into its text. A script's own settings are its ``Row:`` lines
(``ptk.ScriptTemplate.declared_rows``), not entries here.

To expose a new shared knob:
  1. Add an entry to :data:`PARAMS` below.
  2. Reference ``__YOUR_KEY__`` in a script's ``# Rows:`` line and read
     ``args["YOUR_KEY"]``.

**Vendored copy -- keep byte-identical.** This file ships in each host
library's Substance bridge and in the standalone Substance Scripts app; the
Maya library's copy is the source (edit it there, then mirror it). Drift
fails the vendor guard (``test_vendor_sync.py``).
"""

from __future__ import annotations

from uitk.bridge import (
    AttributeSpec,
    ParamRegistry,
    Parameters as _BridgeParams,
)




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
            "Default resolution every texture set is created at (New\n"
            "Project\'s Document Resolution).\n\n"
            "\'Project default\' leaves Painter\'s own setting."
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
                "(a set saved with the scene, shared with the\n"
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
            "The scene's bake source (a set saved with the scene, shared\n"
            "with the Marmoset bridge). Define it once from a selection; it lives\n"
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
            "One texture set per UV tile (New Project's UDIM workflow,\n"
            "``TextureSetPerUVTile``). Only useful if the mesh has UVs laid\n"
            "out across multiple tiles."
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
    # ------------------------------------------------------------------
    # Where a script runs
    # ------------------------------------------------------------------
    "PAINTER_PROJECT": AttributeSpec(
        key="PAINTER_PROJECT",
        label="Project",
        kind="file",
        default="",
        placeholder="The project open in Painter",
        tooltip=(
            "A Painter project (.spp): a script that works on a project runs\n"
            "in the Painter holding it, else opens it; New Project saves\n"
            "as it (with Save When Done).\n\n"
            "Empty: the project open in Painter."
        ),
    ),
    "PAINTER_SAVE": AttributeSpec(
        key="PAINTER_SAVE",
        label="Save When Done",
        kind="bool",
        default=False,
        tooltip="Save the project once the script finished (New Project: as Project).",
    ),
    "PAINTER": AttributeSpec(
        key="PAINTER",
        label="Painter",
        kind="choice",
        default="auto",
        choices=[("Auto", "auto"), ("New Painter", "new")],
        # A live switch: the Painters running now, never a preset's.
        preset=False,
        tooltip=(
            "The Painter the script runs in.\n\n"
            "<b>Auto</b>: the one holding the project (by its record), a\n"
            "Painter with none open for a new project, else a new one.\n"
            "<b>New Painter</b>: launch one.\n\n"
            "A Painter you opened serves scripts once you installed the\n"
            "plugin (Install in Painter) and ticked substance_rpc in its\n"
            "Python menu; it is listed here then."
        ),
    ),
}


class Parameters(ParamRegistry):
    """Parameters — module namespace.

    Declared as data: :class:`uitk.bridge.ParamRegistry` supplies
    ``referenced_keys`` / ``defaults`` / ``affix_parts`` over :data:`PARAMS`,
    and a bridge slot hands this class to the shared base as its
    ``params_module``.
    """

    PARAMS = PARAMS
