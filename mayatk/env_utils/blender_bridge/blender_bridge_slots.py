# !/usr/bin/python
# coding=utf-8
"""Slots for the Blender bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots.MayaBridgeSlotsBase` (which subclasses
uitk's :class:`BridgeSlotsBase`) -- the panel machinery (template combo, dynamic parameter widgets,
user presets, log routing, per-template description) lives upstream. This file owns only the
Blender-specific bits: the bridge factory, the ``(template, mode)`` listing, the header menu, and
the ``b000`` send action. Mirrors ``marmoset_bridge_slots`` / the blendertk ``maya_bridge`` slots.

``REQUIRE_OUTPUT_DIR = False`` -- the bridge round-trips through a temp FBX it manages internally;
there's no user-visible artifact to point at, so the base skips the Output Dir row.
"""
import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.ui_utils.maya_bridge_slots import MayaBridgeSlotsBase

# From this package:
from mayatk.env_utils.blender_bridge._blender_bridge import (
    BlenderBridge,
    _TEMPLATE_DIR,
    list_template_modes,
)
from mayatk.env_utils.blender_bridge import parameters as _params


_PRESETS_ROOT = Path("mayatk/blender_bridge")


class BlenderBridgeSlots(MayaBridgeSlotsBase):
    """Slots wired to ``blender_bridge.ui`` via :class:`MayaBridgeSlotsBase`.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler` so
    ``marking_menu.show("blender_bridge")`` works from anywhere with no explicit registration.
    """

    UI_NAME = "blender_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "blender_bridge"
    REQUIRE_OUTPUT_DIR = False

    # Uses the base's default header menu (Open Templates / Refresh / Clear
    # Log); only the help differs, so it's declared as data.
    HELP_SPEC = {
        "title": "Blender Bridge",
        "body": "Send the selected objects to a fresh Blender. Maya exports the selection as "
        "FBX; Blender runs the chosen import template with your parameter values "
        "substituted in.",
        "steps": [
            "Select one or more objects.",
            "Pick an <b>import template</b> from the dropdown.",
            "Tweak the template's exposed parameters.",
            "Click <b>Send to Blender</b>.",
        ],
        "sections": [
            ("Templates", [
                "<b>import</b> — import the FBX into the current scene.",
                "<b>import_and_frame</b> — import, frame the objects, material-preview shading.",
                "<b>replace_scene</b> — clear the scene's objects, then import (clean slate).",
            ]),
        ],
        "notes": [
            "Add custom templates by dropping new <code>.py</code> files into the templates "
            "folder (use <code>__KEY__</code> tokens from <i>parameters.py</i>), then click "
            "<b>Refresh Templates</b>.",
            "A fresh Blender is launched every time; your running Blender is never touched.",
        ],
    }

    # ------------------------------------------------------------------ base-class hooks
    @property
    def params_module(self):
        return _params

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> BlenderBridge:
        return BlenderBridge()

    def list_template_modes(self):
        return list_template_modes()

    # ------------------------------------------------------------------ b000 -- send
    def b000(self):
        """Send the selected objects to Blender with the chosen template."""
        if cmds is None:
            self.bridge.logger.error("Maya is not available; cannot run the Blender bridge.")
            return

        selection = cmds.ls(selection=True) or []
        if not selection:
            self.bridge.logger.warning(
                "Nothing selected. Select one or more objects before clicking 'Send to Blender'."
            )
            return

        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning("No template chosen. Pick one from the dropdown above.")
            return
        template, mode = pair

        if not self.bridge.blender_path:
            self.bridge.logger.error(
                "Blender not found. Install Blender or set $BLENDER_EXE / "
                "BlenderBridge.blender_path."
            )
            return

        self.bridge.logger.info(f"--- {template} ({mode}) on {len(selection)} object(s) ---")
        try:
            with self.sb.progress(text=f"Working: Send to Blender ({template})"):
                self.bridge.send(
                    objects=selection,
                    template=template,
                    mode=mode,
                    params=self.collect_param_values(),
                )
        except Exception:
            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("blender_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
