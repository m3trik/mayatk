# !/usr/bin/python
# coding=utf-8
"""Slots for the Unity bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots.MayaBridgeSlotsBase` (which subclasses
uitk's :class:`BridgeSlotsBase`). The panel machinery (parameter widgets, user presets, log routing)
lives upstream; this file owns the Unity-specific bits: the bridge factory, the delivery-mode
listing, the relabeled 'Unity Project' row, the header menu, and the ``b000`` send action.

The required 'Output Dir' row is repurposed as the **Unity Project** path (the folder containing
``Assets/``); there's no scene/workspace fallback (a Maya scene dir isn't a Unity project), so
:meth:`default_output_dir` returns "". The delivery is template-free, so every parameter is always
visible (:meth:`_relevant_param_keys` returns ``None``).
"""
import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.ui_utils.maya_bridge_slots import MayaBridgeSlotsBase

from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge, list_delivery_modes
from mayatk.env_utils.unity_bridge import parameters as _params


_PKG_DIR = Path(__file__).resolve().parent
_PRESETS_ROOT = Path("mayatk/unity_bridge")


class UnityBridgeSlots(MayaBridgeSlotsBase):
    """Slots wired to ``unity_bridge.ui`` via :class:`MayaBridgeSlotsBase`.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler` so
    ``marking_menu.show("unity_bridge")`` works from anywhere with no explicit registration.
    """

    UI_NAME = "unity_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "unity_bridge"

    # The required path row IS the Unity project (folder with Assets/).
    REQUIRE_OUTPUT_DIR = True
    OUTPUT_DIR_LABEL = "Unity Project:"
    OUTPUT_DIR_PLACEHOLDER = "(folder containing Assets/)"
    OUTPUT_DIR_TOOLTIP = (
        "Path to the target Unity project -- the folder that contains the\n"
        "'Assets/' directory. The exported FBX is copied into\n"
        "Assets/<subfolder>; Unity imports it on its next window focus."
    )

    # Copy-to-assets has no templates folder; the header menu is the Unity
    # project folder + Clear Log (the base builds it from this data).
    HEADER_MENU_ITEMS = (
        (
            "Open Unity Project", "btn_open_project",
            "Reveal the configured Unity project folder in Explorer.",
            "_open_project_folder",
        ),
        ("Clear Log", "btn_clear_log", "Clear the log panel below.", "clear_log"),
    )
    HELP_SPEC = {
        "title": "Unity Bridge",
        "body": "Export the selected objects and copy the FBX into a Unity project's "
        "<b>Assets/</b> folder. Unity imports the asset automatically on its next "
        "window focus -- no script, no fresh-instance launch, your open editor is "
        "never disturbed.",
        "steps": [
            "Set the <b>Unity Project</b> folder (the one containing Assets/).",
            "Select one or more objects.",
            "Tweak the export / Unity parameters.",
            "Click <b>Send to Unity</b>.",
        ],
        "sections": [
            ("Parameters", [
                "<b>Assets Subfolder</b> — where under Assets/ the FBX lands.",
                "<b>Asset Name</b> — optional; blank uses the object's name.",
                "<b>Launch Editor</b> — open Unity on the project after copying.",
            ]),
        ],
        "notes": [
            "Embedded textures (default) ride inside the FBX so Unity extracts the maps.",
            "Copying into Assets/ is non-destructive to a running Unity session.",
        ],
    }

    # ------------------------------------------------------------------ base-class hooks
    @property
    def params_module(self):
        return _params

    @property
    def template_dir(self) -> Path:
        # No script templates (copy-to-assets renders nothing); the package dir is
        # a harmless stand-in for the (no-op) per-template description lookup.
        return _PKG_DIR

    def make_bridge(self) -> UnityBridge:
        return UnityBridge()

    def list_template_modes(self):
        return list_delivery_modes()

    def default_output_dir(self) -> str:
        # No scene/workspace fallback -- a Maya scene dir isn't a Unity project.
        return ""

    def _relevant_param_keys(self):
        # Template-free delivery -> every parameter stays visible.
        return None

    def _open_project_folder(self) -> None:
        """Reveal the configured Unity project folder."""
        self.reveal_folder(self.resolved_output_dir())

    # ------------------------------------------------------------------ b000 -- send
    def b000(self):
        """Export the selected objects and copy them into the Unity project."""
        if cmds is None:
            self.bridge.logger.error("Maya is not available; cannot run the Unity bridge.")
            return

        selection = cmds.ls(selection=True) or []
        if not selection:
            self.bridge.logger.warning(
                "Nothing selected. Select one or more objects before clicking 'Send to Unity'."
            )
            return

        project = self.resolved_output_dir()
        if not project:
            self.bridge.logger.error(
                "Set the Unity Project folder (the one containing 'Assets/') in the field above."
            )
            if self._output_dir_edit is not None:
                self._output_dir_edit.setFocus()
            return

        pair = self._selected_template_mode()
        template, mode = pair if pair else ("copy_to_assets", "")

        self.bridge.project_path = project
        self.bridge.logger.info(
            f"--- {template} on {len(selection)} object(s) -> {project} ---"
        )
        try:
            with self.sb.progress(text=f"Working: Send to Unity ({template})"):
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

    ui = MayaUiHandler.instance().get("unity_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
