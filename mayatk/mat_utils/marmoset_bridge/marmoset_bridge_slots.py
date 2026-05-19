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
    # Header menu
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu with utilities (no per-call options).

        Headless mode is no longer surfaced -- it's derived from the
        chosen template's mode (``roundtrip`` always headless,
        ``send_to`` never).
        """
        widget.menu.add("Separator", setTitle="Utilities")
        widget.menu.add(
            "QPushButton",
            setText="Open Templates Folder",
            setObjectName="btn_open_templates",
            setToolTip="Reveal the bundled Toolbag template folder in Explorer.",
        )
        widget.menu.btn_open_templates.clicked.connect(self.open_templates_folder)

        widget.menu.add(
            "QPushButton",
            setText="Refresh Templates",
            setObjectName="btn_refresh_templates",
            setToolTip="Re-scan the templates folder and rebuild the template combo.",
        )
        widget.menu.btn_refresh_templates.clicked.connect(self.refresh_templates)

        widget.menu.add(
            "QPushButton",
            setText="Clear Log",
            setObjectName="btn_clear_log",
            setToolTip="Clear the log panel below.",
        )
        widget.menu.btn_clear_log.clicked.connect(self.clear_log)

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Marmoset Bridge -- Send selected meshes to Toolbag.\n\n"
                "1. Set Output Dir (required).\n"
                "2. Select one or more polygon transforms.\n"
                "3. Pick a template + mode from the dropdown:\n"
                "     * 'send_to' opens Toolbag for you to drive interactively.\n"
                "     * 'roundtrip' runs Toolbag headless, then re-surfaces\n"
                "        the generated maps in the log panel below as\n"
                "        clickable links (Maya scene untouched).\n"
                "4. Adjust the parameters the template exposes.\n"
                "5. Click 'Send to Marmoset'.\n\n"
                "Maya exports the selection as FBX with a MatManifest JSON\n"
                "sidecar; Toolbag runs the rendered template with your\n"
                "parameter values substituted in.\n\n"
                "Drop new templates into the templates folder (use __KEY__\n"
                "tokens from parameters.py for tunable values) and use\n"
                "'Refresh Templates' to pick them up."
            ),
        )

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
