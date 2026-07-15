# !/usr/bin/python
# coding=utf-8
"""Slots for the Marmoset Toolbag bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots.MayaBridgeSlotsBase`
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

from mayatk.ui_utils.maya_bridge_slots import MayaBridgeSlotsBase

# From this package:
from mayatk.mat_utils.marmoset_bridge._marmoset_bridge import (
    MarmosetBridge,
    SEND_TO,
    ROUNDTRIP,
    _TEMPLATE_DIR,
    list_template_modes,
)
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
    # (unsaved scene) — the FBX + baked maps are transient hand-off artifacts Toolbag
    # reads once, so the user shouldn't be forced to pick a path.
    TEMP_OUTPUT_FALLBACK = True

    # Uses the base's default header menu (Open Templates / Refresh / Clear
    # Log); only the help differs, so it's declared as data. Headless mode is
    # not surfaced -- it's derived from the chosen template's mode.
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
            ("Modes", [
                "<b>send_to</b> — opens Toolbag for interactive work.",
                "<b>roundtrip</b> — runs Toolbag headless, then "
                "re-surfaces generated maps as clickable links in the "
                "log panel below. Maya scene is left untouched.",
            ]),
        ],
        "notes": [
            "Add custom templates by dropping new files into the "
            "templates folder (use <code>__KEY__</code> tokens from "
            "<i>parameters.py</i> for tunable values), then click "
            "<b>Refresh Templates</b> in the header menu.",
        ],
    }

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _params

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> MarmosetBridge:
        return MarmosetBridge()

    def list_template_modes(self):
        return list_template_modes()

    def select_initial_template_index(self, pairs):
        """Prefer 'bake (roundtrip)' then 'bake (send_to)', else first entry."""
        for pref in (("bake", ROUNDTRIP), ("bake", SEND_TO)):
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

        selection = cmds.ls(selection=True) or []
        if not selection:
            self.bridge.logger.warning(
                "Nothing selected. Select one or more polygon transforms "
                "before clicking 'Send to Marmoset'."
            )
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

        output_dir = self.require_output_dir()
        if output_dir is None:
            return

        # Log accumulates across runs by design -- the user can use the
        # header menu's 'Clear Log' button to reset. The header line below
        # is the visual separator between operations.
        self.bridge.logger.info(
            f"--- {template} ({mode}) on {len(selection)} object(s) ---"
        )

        try:
            with self.sb.progress(
                text=f"Working: Marmoset {template} ({mode})"
            ):
                result = self.bridge.send(
                    objects=selection,
                    template=template,
                    mode=mode,
                    output_dir=output_dir,
                    params=self.collect_param_values(),
                )
        except Exception:
            # Surface the whole traceback in the log panel so the user
            # doesn't have to flip to the Script Editor to diagnose.
            self.bridge.logger.error(
                "Bridge raised:\n" + traceback.format_exc()
            )
            return

        if result is None:
            return  # logger already explained why


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("marmoset_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
