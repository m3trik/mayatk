# !/usr/bin/python
# coding=utf-8
"""Slots for the Marmoset Toolbag bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots_base.MayaBridgeSlotsBase`
(which itself subclasses uitk's :class:`BridgeSlotsBase`) -- the panel
machinery (widget construction, presets, log routing, Output Dir row
with scene-dir fallback, startup info, template description) lives
upstream. This file owns only Marmoset-specific bits:

* The bridge factory (:meth:`make_bridge` returns a :class:`MarmosetBridge`).
* The ``(template, mode)`` listing and preferred initial selection.
* The ``b000`` send action.
"""

import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase

# From this package:
from mayatk.mat_utils.marmoset_bridge._marmoset_bridge import (
    MarmosetEngine,
    MarmosetBridge,
    SEND_TO,
    ROUND_TRIP,
    _TEMPLATE_DIR,
)
from mayatk.mat_utils.bake_sets import BakeSourceSet
from mayatk.mat_utils.marmoset_bridge import parameters as _params


_PRESETS_ROOT = Path("mayatk/marmoset_bridge")


class MarmosetBridgeSlots(MayaBridgeSlotsBase):
    """Slots wired to ``marmoset_bridge.ui`` via :class:`MayaBridgeSlotsBase`.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler` so
    ``self.sb.handlers.marking_menu.show("marmoset_bridge")`` works from
    anywhere with no explicit registration.
    """

    UI_NAME = "marmoset_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "marmoset_bridge"
    # Fall back to a self-cleaning temp folder when no scene/workspace dir resolves
    # (unsaved scene) — the FBX is a transient hand-off artifact Toolbag reads once,
    # so the user shouldn't be forced to pick a path. A bake roundtrip's MAPS are
    # not transient, and do not land here — see the tooltip below.
    TEMP_OUTPUT_FALLBACK = True
    # A roundtrip runs Toolbag blocking and puts its only durable output (the
    # maps) in the project's texture folder, so its hand-off artifacts are
    # intermediates of the run itself. Left to the scene/workspace default
    # they silted up the project beside the scene file; with the field blank
    # the bridge stages them in a temp dir and removes it when the run
    # succeeds. A folder named here still wins -- and is still kept.
    TRANSIENT_OUTPUT_MODES = (ROUND_TRIP,)

    # The shared base tooltip promises the baked maps land in this folder too.
    # For a bake roundtrip they deliberately do not: they are production
    # textures and go to the project's own texture folder
    # (:meth:`MarmosetBridge.baked_texture_dir`), which is what stops a later
    # export from having to copy them in and rename each one around a
    # collision with the source maps that fed the bake.
    OUTPUT_DIR_TOOLTIP = (
        "Directory where the hand-off artifacts (FBX, material manifest,\n"
        "rendered script, saved .tbscene) land.\n\n"
        "Leave blank and a Send To defaults to the current scene's directory\n"
        "(or the active workspace if the scene hasn't been saved), while a\n"
        "bake ROUNDTRIP stages them in a temp folder and removes it when the\n"
        "bake succeeds -- they are intermediates it consumes itself. Name a\n"
        "folder here and a roundtrip keeps its artifacts in it instead.\n\n"
        "A bake roundtrip's TEXTURE MAPS do NOT land here — they go to the\n"
        "project's sourceimages/baked, where the scene references its\n"
        "textures from, so no later pass has to copy or rename them."
    )

    # Header = the base panel-level utilities only (Clear Log). Template
    # management lives on the template combo's own menu; the Bake Source set
    # actions are the bake template's BAKE_SOURCE_SET param row (parameters.py)
    # -- the base auto-wires its buttons to the same-named methods below.

    HELP_SPEC = {
        "title": "Marmoset Bridge",
        "body": "Send selected meshes to Marmoset Toolbag. Maya exports "
        "the selection as FBX with a <i>MatManifest</i> JSON sidecar; "
        "Toolbag runs the rendered template with your parameter values "
        "substituted in.",
        "steps": [
            "Set the <b>Output Dir</b> (or leave blank to use the scene "
            "directory; an unsaved scene falls back to a temp folder).",
            "Select one or more polygon transforms.",
            "Pick a <b>Template + Mode</b> from the dropdown.",
            "Tweak the template's exposed parameters.",
            "Click <b>Send to Marmoset</b>.",
        ],
        "sections": [
            (
                "Modes",
                [
                    "<b>send_to</b> — opens Toolbag for interactive work.",
                    "<b>roundtrip</b> — runs Toolbag headless, then "
                    "re-surfaces generated maps as clickable links in the "
                    "log panel below. Maya scene is left untouched.",
                ],
            ),
        ],
        "notes": [
            "For the <b>bake</b> template: select the bake <i>source</i> "
            "geometry once and use the <b>Bake Source</b> row's <b>Set From "
            "Selection</b> (the set saves with the scene and is shared "
            "with the Substance bridge). Then select the bake <i>target</i> "
            "meshes and Send -- the source rides along automatically and "
            "pairs explicitly, no name suffixes required. The Suffix rows "
            "grey out while the set exists; clear it to fall back to naming "
            "(with <b>Include Children</b> on, one suffixed group root "
            "tags every mesh under it).",
            "A <b>bake (roundtrip)</b> with <b>Assign Material</b> on wires "
            "the baked maps into one material per texture set — of the same "
            "shader type the meshes already wore, restoring the source's "
            "packed-map layout — and assigns it to the bake-target meshes. "
            "Re-baking <i>replaces</i> that material and its maps rather than "
            "stacking a new one beside it.",
            "Edge padding and bit depth are managed automatically: padding "
            "derives from the map size, bit depth from the per-map output "
            "templates.",
            "Add custom templates by dropping new files into the "
            "templates folder (use <code>__KEY__</code> tokens from "
            "<i>parameters.py</i> for tunable values), then use <b>Refresh "
            "Templates</b> on the template dropdown's menu.",
        ],
    }

    # ------------------------------------------------------------------
    # Bake Source set (bake param-row actions; shared with the substance bridge)
    # ------------------------------------------------------------------

    #: Rows that only apply when the scene has NO Bake Source set -- the
    #: name-suffix pairing fallback. An explicit set classifies both sides
    #: outright, so these are greyed while one exists rather than sitting
    #: there implying they still steer the bake.
    SUFFIX_FALLBACK_KEYS = ("HIGH_SUFFIX", "LOW_SUFFIX", "SUFFIX_INCLUDE_CHILDREN")

    _SUFFIX_DISABLED_REASON = (
        "Inactive: this scene has a Bake Source set, which pairs the two "
        "sides explicitly.\nThe name-suffix fallback applies only without "
        "one -- use the Bake Source row's Clear to fall back to it."
    )

    def _refresh_param_enablement(self) -> None:
        """Grey the suffix-fallback rows while a Bake Source set exists.

        The registry's own supersessions (Auto Maps / Auto cage) are applied by
        the base; this adds the one that keys off LIVE scene state.
        """
        super()._refresh_param_enablement()
        has_set = cmds is not None and BakeSourceSet.exists()
        for key in self.SUFFIX_FALLBACK_KEYS:
            self.set_param_enabled(
                key, not has_set, self._SUFFIX_DISABLED_REASON if has_set else ""
            )

    def set_bake_source_from_selection(self) -> None:
        """Store the current selection as the scene's bake source."""
        if cmds is None:
            return
        members = BakeSourceSet.define()
        self._refresh_param_enablement()
        if not members:
            self.bridge.logger.warning(
                "Nothing selected; the bake-source set was cleared."
            )
            return
        self.bridge.logger.info(
            f"Bake Source set: {len(members)} object(s) -> {BakeSourceSet.SET_NAME}. "
            f"Bake sends now export it as the bake source "
            f"(the Source/Target Suffix fallback is inactive while it exists)."
        )

    def select_bake_source(self) -> None:
        """Select the bake-source set's members (hidden ones included)."""
        if cmds is None:
            return
        members = BakeSourceSet.members()
        # Also resyncs the suffix rows if the set was deleted outside the panel.
        self._refresh_param_enablement()
        if not members:
            self.bridge.logger.warning("This scene has no bake-source set.")
            return
        cmds.select(members, replace=True)
        self.bridge.logger.info(f"Selected {len(members)} bake-source object(s).")

    def clear_bake_source(self) -> None:
        """Delete the bake-source set node; its members are left alone."""
        if cmds is None:
            return
        if not BakeSourceSet.exists():
            self.bridge.logger.warning("This scene has no bake-source set.")
            return
        BakeSourceSet.clear()
        self._refresh_param_enablement()
        self.bridge.logger.info(
            "Bake-source set cleared. Pairing falls back to the Source/Target "
            "Suffix convention."
        )

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _params.Parameters

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> MarmosetBridge:
        return MarmosetBridge()

    def list_template_modes(self):
        return MarmosetEngine.list_template_modes()

    def select_initial_template_index(self, pairs):
        """Prefer 'bake (roundtrip)' then 'bake (send_to)', else first entry."""
        for pref in (("bake", ROUND_TRIP), ("bake", SEND_TO)):
            if pref in pairs:
                return pairs.index(pref)
        return 0

    # ------------------------------------------------------------------
    # b000 -- the per-bridge send action
    # ------------------------------------------------------------------

    def b000(self):
        """Process selected transforms with the chosen template + mode."""
        # All operational diagnostics route through the in-window log
        # panel -- the user asked for the text-edit widget to carry every
        # warning + error so the run history stays in one place.
        if cmds is None:
            self.bridge.logger.error(
                "Maya is not available; cannot run the Marmoset bridge."
            )
            return

        # Scope (Selected / Entire Scene / Visible Only) resolves via the shared
        # bridge-slots base; it logs the scope-aware reason when empty.
        params = self.collect_param_values()
        selection = self.scoped_objects(params)
        if not selection:
            return

        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning(
                "No template chosen. Pick one from the dropdown above."
            )
            return
        template, mode = pair

        if not self.bridge.toolbag_path:
            self.bridge.logger.error(
                "Marmoset Toolbag not found. Install Toolbag and ensure it "
                "is on PATH, or set MarmosetBridge.toolbag_path manually."
            )
            return

        output_dir = self.require_output_dir(mode)
        if output_dir is None:
            return

        # Log accumulates across runs by design -- the user can use the
        # header menu's 'Clear Log' button to reset. The header line below
        # is the visual separator between operations.
        self.bridge.logger.info(
            f"--- {template} ({mode}) on {len(selection)} object(s) ---"
        )

        try:
            with self.sb.progress(text=f"Working: Marmoset {template} ({mode})"):
                result = self.bridge.send(
                    objects=selection,
                    template=template,
                    mode=mode,
                    output_dir=output_dir,
                    params=params,
                )
        except Exception:
            # Surface the whole traceback in the log panel so the user
            # doesn't have to flip to the Script Editor to diagnose.
            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())
            return

        if result is None:
            return  # logger already explained why


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("marmoset_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
