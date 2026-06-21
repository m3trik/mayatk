# !/usr/bin/python
# coding=utf-8
"""Slots for the Unity bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots.MayaBridgeSlotsBase` (which subclasses
uitk's :class:`BridgeSlotsBase`). The panel machinery (parameter widgets, user presets, log routing)
lives upstream; this file owns the Unity-specific bits: the bridge factory, the single copy-to-Assets
delivery, the relabeled 'Unity Project' row, the header menu, and the ``b000`` send action.

The required 'Output Dir' row is repurposed as the **Unity Project** path (the folder containing
``Assets/``); there's no scene/workspace fallback (a Maya scene dir isn't a Unity project), so
:meth:`default_output_dir` returns "". Delivery is a single target: export the selection and copy the
FBX into the project's ``Assets/`` (optionally launching the chosen Editor). The project create /
launch engine is shared (``unitytk.UnityLauncher`` / ``UnityFinder``); this file only wires the Qt
glue.

Note: *Unity Studio* is a separate paid, browser-based product (assets enter it via Unity Cloud's
Asset Manager), not this desktop FBX hand-off -- this bridge does not target it.
"""
import os
import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.ui_utils.maya_bridge_slots import MayaBridgeSlotsBase

from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge
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
        "Assets/<subfolder>; Unity imports it on its next window focus.\n"
        "No project yet? Create one via 'New Unity Project...' in the field's menu."
    )

    # Single delivery target: export the selection and copy the FBX into the Unity
    # project's Assets/ (optionally launching the chosen Editor). Matches the shared
    # CopyToAssetsDeliverer's one mode; the combo shows it under a friendly label.
    MODE_COPY = "copy_to_assets"
    MODE_LABELS = {MODE_COPY: "Copy to Project"}

    # The project actions (Set / Open / New) live on the Unity Project field's
    # option menu now (see _configure_output_dir_options), co-located with the
    # field they act on; the header keeps just Clear Log.
    HEADER_MENU_ITEMS = (
        ("Clear Log", "btn_clear_log", "Clear the log panel below.", "clear_log"),
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
            ("Parameters", [
                "<b>Scope</b> — Selected / Entire Scene / Visible Only.",
                "<b>Assets Subfolder</b> — where under Assets/ the FBX lands.",
                "<b>Asset Name</b> — optional; blank uses the object's name.",
                "<b>Launch Unity</b> — after copying: <i>Don't launch</i> (Unity "
                "imports on focus), <i>Open Editor</i> (windowed), or "
                "<i>Headless</i> (batch import).",
            ]),
        ],
        "notes": [
            "Embedded textures (default) ride inside the FBX so Unity extracts the maps.",
            "Copying into Assets/ is non-destructive to a running Unity session.",
        ],
    }

    # ------------------------------------------------------------------ init
    def __init__(self, switchboard):
        super().__init__(switchboard)
        self._populate_unity_versions()

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
        return [(self.MODE_COPY, "")]

    def _format_combo_label(self, template, mode):
        # Friendly label over the internal stem.
        return self.MODE_LABELS.get(template, template)

    def default_output_dir(self) -> str:
        # No scene/workspace fallback -- a Maya scene dir isn't a Unity project.
        return ""

    def _relevant_param_keys(self):
        # Copy-to-assets is template-free, so every parameter stays visible (no
        # per-template gating). Explicit so visibility never silently depends on
        # the absence of a 'copy_to_assets.py' file in the package dir.
        return None

    def _configure_output_dir_options(self, edit) -> None:
        """Unity Project field: recent-history button + an option menu of project
        actions (Set Project / Open / New), co-located with the field they act on
        (moved off the header menu). Overrides the base browse-button default.
        """
        self._add_recent_output_dir_option(edit)
        menu = edit.option_box.menu  # the option-menu (▾) button + its Menu
        for label, name, tooltip, handler in (
            (
                "Set Project…", "btn_set_project",
                "Browse for the Unity project folder (the one containing 'Assets/').",
                self._pick_output_dir,
            ),
            (
                "Open Unity Project", "btn_open_project",
                "Reveal the configured Unity project folder in Explorer.",
                self._open_project_folder,
            ),
            (
                "New Unity Project…", "btn_new_project",
                "Create a new Unity project (pick a version + location) and load it\n"
                "into the field above. Uses the selected Unity Version.",
                self._new_unity_project,
            ),
        ):
            menu.add(
                "QPushButton", setText=label, setObjectName=name, setToolTip=tooltip
            )
            getattr(menu, name).clicked.connect(handler)

    # ------------------------------------------------------------------ Unity helpers
    def _populate_unity_versions(self) -> None:
        """Fill the UNITY_VERSION combo from the installed Editors (newest first).

        The 'Auto (newest)' entry (data "") is the registry default; discovered
        versions are appended so the user can pin which Editor creates/launches.
        """
        widget = self._param_widgets.get("UNITY_VERSION")
        if widget is None:
            return
        try:
            from unitytk import UnityFinder

            editors = UnityFinder.find_editors()
        except Exception:  # noqa: BLE001
            editors = {}
        for ver in sorted(editors, reverse=True):
            widget.addItem(ver, ver)

    def _open_project_folder(self) -> None:
        """Reveal the configured Unity project folder."""
        self.reveal_folder(self.resolved_output_dir())

    def _new_unity_project(self) -> None:
        """Create a new Unity project (version + location) and load it into the field."""
        from qtpy import QtCore, QtWidgets
        from unitytk import UnityLauncher

        version = (
            self._read_param("UNITY_VERSION")
            if "UNITY_VERSION" in self._param_widgets
            else ""
        ) or None
        launcher = UnityLauncher(executable_path=version)
        if not launcher.executable_path:
            self.bridge.logger.error(
                "No Unity Editor found to create a project. Install one via Unity Hub."
            )
            return

        parent = QtWidgets.QFileDialog.getExistingDirectory(
            self.ui, "Choose where to create the new project", str(Path.home())
        )
        if not parent:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self.ui, "New Unity Project", "Project name:"
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        target = os.path.join(parent, name)
        if os.path.exists(target):
            self.bridge.logger.error(f"Target already exists: {target}")
            return

        self.bridge.logger.info(
            f"Creating Unity project at {target} (batch mode; this can take a minute)…"
        )
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            created = launcher.create_project(target)
        except Exception as e:  # noqa: BLE001
            self.bridge.logger.error(f"Project creation failed: {e}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        if created and self._output_dir_edit is not None:
            self._output_dir_edit.setText(target)
            self._record_output_dir(target)
            self.bridge.logger.info(f"Created project at {target}")
        elif not created:
            self.bridge.logger.error(f"Project creation did not complete at {target}.")

    # ------------------------------------------------------------------ scope
    def _resolve_scope_objects(self, scope: str):
        """Objects to export for the chosen ``SCOPE`` param.

        Mesh shapes/transforms are returned as-is; the engine's
        :class:`MayaExportMixin` coerces them to transform nodes. ``"selected"``
        is the default (and the fallback for any unknown value).
        """
        if scope == "all":
            return cmds.ls(type="mesh", noIntermediate=True, long=True) or []
        if scope == "visible":
            from mayatk.display_utils._display_utils import DisplayUtils

            # inherit_parent_visibility=True is what actually walks the
            # transform chain and drops hidden geometry (without it the
            # helper returns every renderable shape regardless of visibility).
            return (
                DisplayUtils.get_visible_geometry(
                    shapes=True, inherit_parent_visibility=True
                )
                or []
            )
        return cmds.ls(selection=True, long=True) or []

    # ------------------------------------------------------------------ b000 -- send
    def b000(self):
        """Export per the chosen Scope and copy the FBX into the Unity project."""
        if cmds is None:
            self.bridge.logger.error("Maya is not available; cannot run the Unity bridge.")
            return

        params = self.collect_param_values()
        scope = params.get("SCOPE", "selected")
        objects = self._resolve_scope_objects(scope)
        if not objects:
            self.bridge.logger.warning(
                {
                    "all": "The scene contains no mesh geometry to export.",
                    "visible": "No visible mesh geometry to export.",
                }.get(
                    scope,
                    "Nothing selected. Select one or more objects, or change "
                    "Scope to 'Entire Scene' / 'Visible Only'.",
                )
            )
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
