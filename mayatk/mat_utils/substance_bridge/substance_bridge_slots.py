# !/usr/bin/python
# coding=utf-8
"""Slots for the Substance Painter bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots.MayaBridgeSlotsBase`
(itself a :class:`uitk.bridge.BridgeSlotsBase`). The panel machinery
lives upstream. Substance-specific extras:

* A custom widget kind, ``painter_template_file``, registered on module
  import -- a single-file picker filtered on ``.spt`` / ``.spp`` for
  Painter project templates.
* The ``b000`` send action (FBX export + Painter handoff with optional
  RPC dispatch).

The ``file_list`` kind used for ``PAINTER_BAKED_MAPS`` is no longer
substance-specific -- it's part of the shared ``uitk.bridge.spec``
registry now, so substance just references it by name.
"""
import traceback
from pathlib import Path

from qtpy import QtWidgets

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from uitk.bridge.spec import KindHandler, register_kind
from mayatk.ui_utils.maya_bridge_slots import MayaBridgeSlotsBase

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
# Custom kind: painter_template_file (single-file picker for .spt / .spp)
# ---------------------------------------------------------------------


def _build_painter_template_file(spec, parent):
    """Composite line edit + browse button with Painter-template filter."""
    container = QtWidgets.QWidget(parent)
    hl = QtWidgets.QHBoxLayout(container)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(2)
    edit = QtWidgets.QLineEdit("" if spec.default is None else str(spec.default))
    edit.setMinimumHeight(19)
    edit.setMaximumHeight(19)
    browse = QtWidgets.QPushButton("...")
    browse.setFixedWidth(22)
    browse.setMinimumHeight(19)
    browse.setMaximumHeight(19)
    hl.addWidget(edit, 1)
    hl.addWidget(browse)
    container._line_edit = edit

    def _on_browse():
        start = edit.text() or str(Path.home())
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            container,
            "Select Painter project template",
            start,
            "Painter template (*.spt);;Painter project (*.spp);;All files (*)",
        )
        if path:
            edit.setText(path)

    browse.clicked.connect(_on_browse)
    return container


def _read_painter_template_file(widget):
    return widget._line_edit.text()


def _write_painter_template_file(widget, value):
    widget._line_edit.setText("" if value is None else str(value))


def _connect_painter_template_file(widget, callback):
    widget._line_edit.textChanged.connect(
        lambda *_: callback(_read_painter_template_file(widget))
    )


# Registered exactly once -- the global registry is process-wide, so a
# subsequent import of this module is a no-op overwrite.
register_kind(
    "painter_template_file",
    KindHandler(
        _build_painter_template_file,
        _read_painter_template_file,
        _write_painter_template_file,
        connect=_connect_painter_template_file,
    ),
)


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

    # ``painter_template_file`` is a composite (line edit + browse), so it
    # belongs to the same "do not clamp the row to 19px" group as the
    # standard composite kinds.
    PATH_LIKE_KINDS = MayaBridgeSlotsBase.PATH_LIKE_KINDS + ("painter_template_file",)

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
        """Prefer 'with_textures (send_to)' then 'import (send_to)'."""
        for pref in (("with_textures", "send_to"), ("import", "send_to")):
            if pref in pairs:
                return pairs.index(pref)
        return 0

    # ------------------------------------------------------------------
    # Header menu
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure the header menu with template / log utilities."""
        widget.menu.add("Separator", setTitle="Utilities")
        widget.menu.add(
            "QPushButton",
            setText="Open Templates Folder",
            setObjectName="btn_open_templates",
            setToolTip="Reveal the bundled Painter template folder in Explorer.",
        )
        widget.menu.btn_open_templates.clicked.connect(self.open_templates_folder)

        widget.menu.add(
            "QPushButton",
            setText="Refresh Templates",
            setObjectName="btn_refresh_templates",
            setToolTip="Re-scan the templates folder and rebuild the combo.",
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
                "Substance Bridge -- Send selected meshes to Substance Painter.\n\n"
                "1. Set Output Dir (or leave blank for scene-dir default).\n"
                "2. Select one or more polygon transforms.\n"
                "3. Pick a template + mode from the dropdown:\n"
                "     * 'send_to' launches Painter for you to drive interactively.\n"
                "     * 'roundtrip' launches Painter with remote scripting,\n"
                "        sends the template's JS body via JSON-RPC, and waits\n"
                "        for completion.\n"
                "4. Adjust the parameters the template exposes.\n"
                "5. Click 'Send to Painter'.\n\n"
                "Maya exports the selection as FBX. The template's metadata\n"
                "constants (BRIDGE_MODES, LAUNCH_ARGS, RPC_SCRIPT,\n"
                "BUILD_MANIFEST, FBX_OPTIONS) drive the launch line and\n"
                "optional RPC step. Drop new templates into the templates\n"
                "folder (use __KEY__ tokens from parameters.py for tunable\n"
                "values) and use 'Refresh Templates' to pick them up."
            ),
        )

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
