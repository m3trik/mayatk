# !/usr/bin/python
# coding=utf-8
"""Slots for the Substance Painter bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots_base.MayaBridgeSlotsBase`
(itself a :class:`uitk.bridge.BridgeSlotsBase`). The panel machinery
lives upstream. Substance-specific extras live below: the ``b000`` send
action (FBX export + Painter handoff with optional RPC dispatch).

Assigned-mesh textures (formerly a ``file_list`` browser called
``PAINTER_BAKED_MAPS``) are now driven by the boolean
``PAINTER_INCLUDE_TEXTURES`` -- when True, the bridge walks the
selection's shading networks and stages the resolved textures into the
FBX output folder, then passes each one via ``--mesh-map`` on launch.
The companion ``PAINTER_TEXTURE_PREFIX`` widget is greyed out while
INCLUDE_TEXTURES is off so the user can't dial in a prefix that won't
be applied.
"""
import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from uitk.bridge.spec import connect_changed, read_value
from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase

# From this package:
from mayatk.mat_utils.substance_bridge._substance_bridge import (
    SubstanceBridge,
    _TEMPLATE_DIR,
    list_template_modes,
    parse_template,
)
from mayatk.mat_utils.substance_bridge import parameters as _params


_PRESETS_ROOT = Path("mayatk/substance_bridge")


# ---------------------------------------------------------------------
# Slot class
# ---------------------------------------------------------------------


class SubstanceBridgeSlots(MayaBridgeSlotsBase):
    """Slots wired to ``substance_bridge.ui`` via :class:`MayaBridgeSlotsBase`.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler` so
    ``self.sb.handlers.marking_menu.show("substance_bridge")`` works
    from anywhere with no explicit registration.
    """

    UI_NAME = "substance_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "substance_bridge"
    # Fall back to a self-cleaning temp folder when no scene/workspace dir resolves
    # (unsaved scene) — the FBX + staged maps are transient hand-off artifacts Painter
    # reads once, so the user shouldn't be forced to pick a path.
    TEMP_OUTPUT_FALLBACK = True

    # Uses the base's default header menu (Open Templates / Refresh / Clear
    # Log); only the help differs, so it's declared as data.
    HELP_SPEC = {
        "title": "Substance Bridge",
        "body": "Send selected meshes to Substance Painter. Maya exports "
        "the selection as FBX; the template's metadata constants "
        "(<i>BRIDGE_MODES</i>, <i>LAUNCH_ARGS</i>, <i>RPC_SCRIPT</i>, "
        "<i>BUILD_MANIFEST</i>, <i>FBX_OPTIONS</i>) drive the launch "
        "line and optional RPC step.",
        "steps": [
            "Set the <b>Output Dir</b> (or leave blank to use the "
            "scene directory; an unsaved scene falls back to a temp folder).",
            "Select one or more polygon transforms.",
            "Pick a <b>Template + Mode</b> from the dropdown.",
            "Tweak the template's exposed parameters.",
            "Click <b>Send to Painter</b>.",
        ],
        "sections": [
            ("Modes", [
                "<b>send_to</b> — launches Painter for interactive work.",
                "<b>roundtrip</b> — launches Painter with remote "
                "scripting, sends the template's JS body via "
                "JSON-RPC, and waits for completion.",
            ]),
        ],
        "notes": [
            "Add custom templates by dropping new files into the "
            "templates folder (use <code>__KEY__</code> tokens from "
            "<i>parameters.py</i> for tunable values), then click "
            "<b>Refresh Templates</b> in the header menu.",
        ],
    }

    def __init__(self, switchboard):
        super().__init__(switchboard)
        self._wire_texture_prefix_dependency()

    def _wire_texture_prefix_dependency(self) -> None:
        """Grey out the ``Texture Prefix`` field while ``Include Textures`` is off.

        Both widgets only exist when the active template references them
        (e.g. ``import.py``); the lookup gracefully no-ops otherwise so
        the panel stays usable on templates that omit either knob.
        """
        include_widget = self._param_widgets.get("PAINTER_INCLUDE_TEXTURES")
        prefix_widget = self._param_widgets.get("PAINTER_TEXTURE_PREFIX")
        if include_widget is None or prefix_widget is None:
            return

        def _sync(_value=None):
            prefix_widget.setEnabled(bool(read_value(include_widget)))

        connect_changed(include_widget, _sync)
        _sync()

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _params

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> SubstanceBridge:
        return SubstanceBridge()

    def list_template_modes(self):
        return list_template_modes()

    def select_initial_template_index(self, pairs):
        """Default the panel to ``import (send_to)`` when it's available."""
        pref = ("import", "send_to")
        return pairs.index(pref) if pref in pairs else 0

    # ------------------------------------------------------------------
    # b000 -- the per-bridge send action
    # ------------------------------------------------------------------

    def b000(self):
        """Process the selected transforms with the chosen template + mode."""
        if cmds is None:
            self.bridge.logger.error(
                "Maya is not available; cannot run the Substance bridge."
            )
            return

        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning(
                "No template chosen. Pick one from the dropdown above."
            )
            return
        template, mode = pair

        # Templates that don't export FBX (e.g. ``render``) operate on
        # the project already loaded in Painter and don't need a Maya
        # selection.
        meta = parse_template(_TEMPLATE_DIR / f"{template}.py")
        needs_selection = meta.get("EXPORT_FBX", True)

        selection = cmds.ls(selection=True) or []
        if needs_selection and not selection:
            self.bridge.logger.warning(
                "Nothing selected. Select one or more polygon transforms "
                "before clicking 'Send to Painter'."
            )
            return

        if not self.bridge.painter_path:
            self.bridge.logger.error(
                "Substance Painter not found. Install Painter, or pass "
                "painter_exe= when instantiating SubstanceBridge."
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
                text=f"Working: Substance {template} ({mode})"
            ):
                result = self.bridge.send(
                    objects=selection,
                    template=template,
                    mode=mode,
                    output_dir=output_dir,
                    params=self.collect_param_values(),
                )
        except Exception:
            self.bridge.logger.error(
                "Bridge raised:\n" + traceback.format_exc()
            )
            return

        if result is None:
            return  # logger already explained why


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("substance_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
