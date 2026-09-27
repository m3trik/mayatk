# !/usr/bin/python
# coding=utf-8
"""Slots for the Unity bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots_base.MayaBridgeSlotsBase` (which subclasses
uitk's :class:`BridgeSlotsBase`). The panel machinery (parameter widgets, user presets, log routing)
lives upstream; the Unity panel behavior shared with blendertk's and extapps' Unity panels (the
relabeled 'Unity Project' row and its project actions, the mode combo, the Editor combo, script
management) is :class:`UnityPanelMixin`, vendored beside this file (``_unity_panel.py``). This
file owns the Maya-specific bits: the bridge factory, the help text and the ``b000`` send action.

The required 'Output Dir' row is repurposed as the **Unity Project** path (the folder containing
``Assets/``); there's no scene/workspace fallback (a Maya scene dir isn't a Unity project), so
:meth:`default_output_dir` returns "". Delivery is a single target: export the selection and copy the
FBX into the project's ``Assets/`` (optionally launching the chosen Editor). The project create /
launch engine is shared (``unitytk.UnityLauncher`` / ``UnityFinder``); this file only wires the Qt
glue.

Note: *Unity Studio* is a separate paid, browser-based product (assets enter it via Unity Cloud's
Asset Manager), not this desktop FBX hand-off -- this bridge does not target it.
"""

import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase

from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge
from mayatk.env_utils.unity_bridge._unity_panel import UnityPanelMixin
from mayatk.env_utils.unity_bridge import parameters as _params


_PRESETS_ROOT = Path("mayatk/unity_bridge")


class UnityBridgeSlots(UnityPanelMixin, MayaBridgeSlotsBase):
    """Slots wired to ``unity_bridge.ui`` via :class:`MayaBridgeSlotsBase`.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler` so
    ``marking_menu.show("unity_bridge")`` works from anywhere with no explicit registration.
    """

    UI_NAME = "unity_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "unity_bridge"

    OUTPUT_DIR_TOOLTIP = (
        "Path to the target Unity project -- the folder that contains the\n"
        "'Assets/' directory. The exported FBX is copied into\n"
        "Assets/<subfolder>; Unity imports it on its next window focus.\n"
        "No project yet? Create one via 'New Unity Project...' in the field's menu."
    )

    HELP_SPEC = {
        "title": "Unity Bridge",
        "body": "Export the selected objects and copy the FBX into a Unity project's "
        "<b>Assets/</b> folder. Unity imports the asset automatically on its next "
        "window focus -- no script, no fresh-instance launch, your open editor is "
        "never disturbed.",
        "steps": [
            "Set the <b>Unity Project</b> folder (or create one via the menu).",
            "Choose the <b>Scope</b> and tweak the export parameters.",
            "Click <b>Send to Unity</b>.",
        ],
        "sections": [
            (
                "Parameters",
                [
                    "<b>Scope</b> — Selected / Entire Scene / Visible Only.",
                    "<b>Assets Subfolder</b> — where under Assets/ the FBX lands.",
                    "<b>Asset Name</b> — optional; blank uses the object's name.",
                    "<b>Launch Unity</b> — after copying: <i>Don't launch</i> (Unity "
                    "imports on focus), <i>Open Editor</i> (windowed), or "
                    "<i>Headless</i> (batch import).",
                ],
            ),
        ],
        "notes": [
            "Embedded textures (default) ride inside the FBX so Unity extracts the maps.",
            "Copying into Assets/ is non-destructive to a running Unity session.",
            "The <b>Manage Unity Scripts</b> template installs, updates, "
            "inspects or removes unitytk's C# import automation in the project "
            "(the embedded <i>com.m3trik.unitytk</i> package). Check the "
            "scripts to act on — one row per import channel, all on by "
            "default; the shared core files ride along with any install. "
            "Per-channel runtime toggles live in Unity under Project "
            "Settings ▸ unitytk.",
        ],
    }

    # ------------------------------------------------------------------ init
    def __init__(self, switchboard):
        super().__init__(switchboard)
        self._populate_unity_rows()  # UnityPanelMixin: Editor combo + SCRIPTS list

    # ------------------------------------------------------------------ base-class hooks
    @property
    def params_module(self):
        return _params.Parameters

    def make_bridge(self):
        """Build the engine, or ``None`` when the optional unitytk is absent.

        SILENT on purpose -- this runs from ``__init__`` (via the log wiring),
        and a modal raised from a constructor is parented to a window that does
        not exist yet, stranding an undismissable dialog if construction then
        fails. The panel opens either way; installing unitytk is an explicit
        action on the Unity Scripts template.
        """
        if not self.optional_package_available("unitytk"):
            return None
        return UnityBridge()

    # ------------------------------------------------------------------ b000 -- send
    def b000(self):
        """Run the selected template: export-and-copy, or script management."""
        if self._run_manage_mode():
            return

        if cmds is None:
            self.bridge.logger.error(
                "Maya is not available; cannot run the Unity bridge."
            )
            return

        params = self.collect_param_values()
        # Same default `scoped_objects` resolves with, so the header line always
        # names the scope the objects were actually collected under.
        scope = params.get("SCOPE", "selected")
        objects = self.scoped_objects(params)
        if not objects:
            return

        project = self.resolved_output_dir()
        if not project:
            self.bridge.logger.error(
                "Set the Unity Project folder in the field above (the one "
                "containing 'Assets/'), or create one via 'New Unity Project…'."
            )
            if self._output_dir_edit is not None:
                self._output_dir_edit.setFocus()
            return

        self.bridge.project_path = project
        self.bridge.logger.info(
            f"--- Send to Unity ({scope}) on {len(objects)} object(s) -> {project} ---"
        )
        try:
            with self.sb.progress(text="Working: Send to Unity"):
                self.bridge.send(
                    objects=objects,
                    template=self.MODE_COPY,
                    mode="",
                    params=params,
                )
        except Exception:
            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("unity_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
