# !/usr/bin/python
# coding=utf-8
"""UI slots for the Substance Painter bridge.

Architectural twin of :class:`mayatk.mat_utils.marmoset_bridge.MarmosetBridgeSlots`:

- Template/mode combo (cmb000).
- Parameter widgets driven by :data:`parameters.PARAMS`. Rows show only
  when the selected template references the matching ``__KEY__`` token.
- User preset combo (uitk PresetManager) + Reset-to-Defaults button.
- Send-to-Painter button (b000).
- Log panel (txt000) with clickable ``action://`` URIs.
- Header utilities menu.
"""
import os
import subprocess
import sys
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from qtpy import QtCore, QtWidgets

from uitk.widgets.checkBox import CheckBox
from uitk.widgets.pushButton import PushButton
from uitk.widgets.widgetComboBox import WidgetComboBox
from uitk.widgets.mixins.preset_manager import PresetManager

# From this package:
from mayatk.mat_utils.substance_bridge._substance_bridge import (
    SubstanceBridge,
    _TEMPLATE_DIR,
    list_templates,
    list_template_modes,
    parse_template,
)
from mayatk.mat_utils.substance_bridge import parameters as _params


_PRESETS_ROOT = Path("mayatk/substance_bridge")


class SubstanceBridgeSlots:
    """Slots class wired to ``substance_bridge.ui``.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler`,
    so :code:`self.sb.handlers.marking_menu.show("substance_bridge")` works
    from anywhere with no explicit registration.
    """

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.substance_bridge
        self._bridge: "SubstanceBridge | None" = None
        self._param_widgets: "dict[str, QtWidgets.QWidget]" = {}
        self._param_rows: "dict[str, QtWidgets.QWidget]" = {}
        self._preset_mgr: "PresetManager | None" = None
        self._preset_combo: "WidgetComboBox | None" = None
        self._param_visibility_settled = False
        self._build_param_widgets()
        self._build_preset_controls()

        # Pipe SubstanceBridge's logger into the in-window log panel and
        # treat ``action://`` URIs in the log as clickable open-folder links.
        # Failures here must NEVER break the param widgets we just built,
        # so we swallow anything the logger setup throws.
        try:
            self._redirect_log_to_panel()
            if hasattr(self.ui.txt000, "anchorClicked"):
                self.ui.txt000.anchorClicked.connect(self._on_log_link_clicked)
        except Exception as e:  # noqa: BLE001
            print(f"[substance_bridge] log panel wiring failed (ignored): {e}")

    @property
    def bridge(self) -> SubstanceBridge:
        """Lazy-instantiated :class:`SubstanceBridge` (defers Painter lookup)."""
        if self._bridge is None:
            self._bridge = SubstanceBridge()
        return self._bridge

    # ------------------------------------------------------------------
    # Parameter widget construction (parameters.PARAMS -> Qt widgets)
    # ------------------------------------------------------------------

    def _build_param_widgets(self):
        """Inject a 'Parameters' group between the template combo and Send button.

        Each parameter gets its own row widget inside a QVBoxLayout (not a
        QFormLayout); a hidden row is fully excluded from the layout's
        sizeHint so the window can actually shrink when rows hide.
        """
        grp = QtWidgets.QGroupBox("Parameters", self.ui.grp_process)
        vbox = QtWidgets.QVBoxLayout(grp)
        vbox.setContentsMargins(2, 4, 2, 2)
        vbox.setSpacing(0)

        for key, spec in _params.PARAMS.items():
            row = QtWidgets.QWidget(grp)
            hbox = QtWidgets.QHBoxLayout(row)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(2)

            label = QtWidgets.QLabel(spec.label + ":", row)
            label.setMinimumWidth(90)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            label.setToolTip(spec.tooltip)

            widget = self._make_widget_for(spec)
            widget.setParent(row)
            widget.setObjectName(f"param_{key.lower()}")
            if spec.widget_type != "path":
                widget.setMinimumHeight(19)
                widget.setMaximumHeight(19)
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)

            hbox.addWidget(label)
            hbox.addWidget(widget, 1)
            vbox.addWidget(row)

            self._param_widgets[key] = widget
            self._param_rows[key] = row

        parent_layout = self.ui.grp_process.layout()
        insert_at = parent_layout.indexOf(self.ui.b000)
        parent_layout.insertWidget(insert_at, grp)
        self._param_group = grp

    def _make_widget_for(self, spec: _params.SubstanceParam) -> QtWidgets.QWidget:
        """Construct a Qt widget for *spec*. Supports int/float/choice/bool/path."""
        if spec.widget_type == "int":
            w = QtWidgets.QSpinBox()
            w.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            if spec.minimum is not None:
                w.setMinimum(int(spec.minimum))
            if spec.maximum is not None:
                w.setMaximum(int(spec.maximum))
            if spec.step is not None:
                w.setSingleStep(int(spec.step))
            w.setValue(int(spec.default))
            return w

        if spec.widget_type == "float":
            w = QtWidgets.QDoubleSpinBox()
            w.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            w.setDecimals(spec.decimals or 4)
            if spec.minimum is not None:
                w.setMinimum(float(spec.minimum))
            if spec.maximum is not None:
                w.setMaximum(float(spec.maximum))
            if spec.step is not None:
                w.setSingleStep(float(spec.step))
            w.setValue(float(spec.default))
            return w

        if spec.widget_type == "choice":
            w = QtWidgets.QComboBox()
            for label, value in spec.choices or []:
                w.addItem(label, value)
            for i in range(w.count()):
                if w.itemData(i) == spec.default:
                    w.setCurrentIndex(i)
                    break
            return w

        if spec.widget_type == "bool":
            w = CheckBox()
            w.setChecked(bool(spec.default))
            w.setText("On" if w.isChecked() else "Off")
            w.set_checkbox_rich_text_style(w.isChecked())
            w.stateChanged.connect(
                lambda state, btn=w: btn.setText("On" if state else "Off")
            )
            return w

        if spec.widget_type == "path":
            container = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(container)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(2)
            edit = QtWidgets.QLineEdit(str(spec.default))
            edit.setMinimumHeight(19)
            edit.setMaximumHeight(19)
            browse = QtWidgets.QPushButton("...")
            browse.setFixedWidth(22)
            browse.setMinimumHeight(19)
            browse.setMaximumHeight(19)
            browse.clicked.connect(lambda _=None, e=edit: self._browse_file(e))
            hl.addWidget(edit, 1)
            hl.addWidget(browse)
            container._line_edit = edit
            return container

        if spec.widget_type == "file_list":
            # Composite: scrollable list + Add / Remove buttons. The list
            # widget itself holds the value; ``_read_param`` walks its items.
            container = QtWidgets.QWidget()
            grid = QtWidgets.QGridLayout(container)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(2)
            grid.setVerticalSpacing(2)

            list_widget = QtWidgets.QListWidget()
            list_widget.setSelectionMode(
                QtWidgets.QAbstractItemView.ExtendedSelection
            )
            list_widget.setMinimumHeight(48)
            list_widget.setMaximumHeight(80)
            for item in (spec.default or []):
                list_widget.addItem(str(item))

            add_btn = QtWidgets.QPushButton("Add...")
            add_btn.setMinimumHeight(19)
            add_btn.setMaximumHeight(19)
            rm_btn = QtWidgets.QPushButton("Remove")
            rm_btn.setMinimumHeight(19)
            rm_btn.setMaximumHeight(19)

            add_btn.clicked.connect(
                lambda _=None, lw=list_widget: self._browse_files(lw)
            )
            rm_btn.clicked.connect(
                lambda _=None, lw=list_widget: self._remove_selected_files(lw)
            )

            grid.addWidget(list_widget, 0, 0, 2, 1)
            grid.addWidget(add_btn, 0, 1)
            grid.addWidget(rm_btn, 1, 1)
            grid.setColumnStretch(0, 1)
            container._list_widget = list_widget
            return container

        raise ValueError(f"Unknown widget_type {spec.widget_type!r} for {spec.key}")

    @staticmethod
    def _browse_file(line_edit: QtWidgets.QLineEdit):
        """File-picker for Painter project templates (.spt)."""
        start = line_edit.text() or str(Path.home())
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            line_edit,
            "Select Painter project template",
            start,
            "Painter template (*.spt);;Painter project (*.spp);;All files (*)",
        )
        if path:
            line_edit.setText(path)

    @staticmethod
    def _browse_files(list_widget: QtWidgets.QListWidget):
        """Multi-file picker for the ``file_list`` widget type (baked maps)."""
        # Anchor at the parent of the first item, if any.
        start = ""
        if list_widget.count():
            start = str(Path(list_widget.item(0).text()).parent)
        if not start:
            start = str(Path.home())
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            list_widget,
            "Select baked maps",
            start,
            "Images (*.png *.tif *.tiff *.exr *.tga *.jpg *.jpeg *.psd);;"
            "All files (*)",
        )
        existing = {
            list_widget.item(i).text() for i in range(list_widget.count())
        }
        for path in paths:
            if path and path not in existing:
                list_widget.addItem(path)

    @staticmethod
    def _remove_selected_files(list_widget: QtWidgets.QListWidget):
        """Drop selected entries from a ``file_list`` widget."""
        for item in list_widget.selectedItems():
            list_widget.takeItem(list_widget.row(item))

    def _read_param(self, key: str):
        w = self._param_widgets[key]
        if isinstance(w, QtWidgets.QComboBox):
            return w.currentData()
        if isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            return w.value()
        if isinstance(w, QtWidgets.QAbstractButton):
            return w.isChecked()
        list_widget = getattr(w, "_list_widget", None)
        if list_widget is not None:
            return [list_widget.item(i).text() for i in range(list_widget.count())]
        edit = getattr(w, "_line_edit", None)
        if edit is not None:
            return edit.text()
        raise TypeError(f"Don't know how to read value from {type(w).__name__}")

    def _collect_param_values(self) -> dict:
        """Snapshot all widget values, regardless of visibility."""
        return {key: self._read_param(key) for key in self._param_widgets}

    def _refresh_param_visibility(self):
        """Show only the rows whose placeholder appears in the selected template."""
        pair = self._selected_template_mode()
        if not pair:
            return
        template, _mode = pair
        path = _TEMPLATE_DIR / f"{template}.py"
        if not path.is_file():
            return
        used = _params.referenced_keys(path.read_text(encoding="utf-8"))

        for key, row in self._param_rows.items():
            row.setVisible(key in used)
        self._param_group.setVisible(bool(used))

        if self._param_visibility_settled:
            fit = getattr(self.ui, "fit_height_to_content", None)
            if callable(fit):
                QtCore.QTimer.singleShot(0, fit)
        self._param_visibility_settled = True

    # ------------------------------------------------------------------
    # User-saved presets (uitk PresetManager)
    # ------------------------------------------------------------------

    def _build_preset_controls(self):
        """Insert a user-preset combobox + 'Reset to Defaults' above b000."""
        layout = self.ui.grp_process.layout()

        combo = WidgetComboBox(self.ui.grp_process)
        combo.setObjectName("cmb_user_presets")
        combo.setMinimumHeight(19)
        combo.setMaximumHeight(19)
        combo.setToolTip(
            "Saved user presets for the active template.\n"
            "Open the side menu to Save / Rename / Delete the current values."
        )

        reset_btn = PushButton(self.ui.grp_process)
        reset_btn.setObjectName("btn_reset_defaults")
        reset_btn.setText("Reset to Defaults")
        reset_btn.setMinimumHeight(19)
        reset_btn.setMaximumHeight(19)
        reset_btn.setToolTip("Restore every parameter widget to its registry default.")
        reset_btn.clicked.connect(self._reset_to_defaults)

        insert_at = layout.indexOf(self.ui.b000)
        layout.insertWidget(insert_at, combo)
        layout.insertWidget(insert_at + 1, reset_btn)

        managed = [
            getattr(w, "_line_edit", w) for w in self._param_widgets.values()
        ]
        self._preset_mgr = PresetManager.from_widgets(
            preset_dir=_PRESETS_ROOT / self._active_template(),
            widgets=managed,
        )
        self._preset_mgr.wire_combo(combo)

        self._preset_combo = combo
        self._reset_btn = reset_btn

    def _active_template(self) -> str:
        """The currently-selected template stem (mode-agnostic preset key)."""
        pair = self._selected_template_mode()
        if pair:
            return pair[0]
        templates = [p.stem for p in list_templates()]
        return templates[0] if templates else "default"

    def _on_template_changed(self):
        """Re-show rows + re-point preset dir when the template combo changes."""
        self._refresh_param_visibility()
        if self._preset_mgr is not None:
            self._preset_mgr.preset_dir = _PRESETS_ROOT / self._active_template()
            refresh = getattr(self._preset_mgr, "_refresh_combo", None)
            if callable(refresh):
                refresh()

    def _reset_to_defaults(self):
        """Restore every parameter widget to the registry default."""
        for key, spec in _params.PARAMS.items():
            w = self._param_widgets.get(key)
            if w is None:
                continue
            if isinstance(w, QtWidgets.QSpinBox):
                w.setValue(int(spec.default))
            elif isinstance(w, QtWidgets.QDoubleSpinBox):
                w.setValue(float(spec.default))
            elif isinstance(w, QtWidgets.QComboBox):
                for i in range(w.count()):
                    if w.itemData(i) == spec.default:
                        w.setCurrentIndex(i)
                        break
            elif isinstance(w, QtWidgets.QAbstractButton):
                w.setChecked(bool(spec.default))
            else:
                list_widget = getattr(w, "_list_widget", None)
                if list_widget is not None:
                    list_widget.clear()
                    for item in (spec.default or []):
                        list_widget.addItem(str(item))
                else:
                    edit = getattr(w, "_line_edit", None)
                    if edit is not None:
                        edit.setText(str(spec.default))

        if self._preset_combo is not None:
            self._preset_combo.blockSignals(True)
            try:
                self._preset_combo.setCurrentIndex(-1)
            finally:
                self._preset_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Header menu / utilities
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
        widget.menu.btn_open_templates.clicked.connect(self._open_templates_folder)

        widget.menu.add(
            "QPushButton",
            setText="Refresh Templates",
            setObjectName="btn_refresh_templates",
            setToolTip="Re-scan the templates folder and rebuild the combo.",
        )
        widget.menu.btn_refresh_templates.clicked.connect(self._refresh_templates)

        widget.menu.add(
            "QPushButton",
            setText="Clear Log",
            setObjectName="btn_clear_log",
            setToolTip="Clear the log panel below.",
        )
        widget.menu.btn_clear_log.clicked.connect(lambda: self.ui.txt000.clear())

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Substance Bridge -- Send selected meshes to Substance Painter.\n\n"
                "1. Select one or more polygon transforms.\n"
                "2. Pick a template + mode from the dropdown:\n"
                "     * 'send_to' launches Painter for you to drive interactively.\n"
                "     * 'roundtrip' launches Painter with remote scripting,\n"
                "        sends the template's JS body via JSON-RPC, and waits\n"
                "        for completion.\n"
                "3. Adjust the parameters the template exposes.\n"
                "4. Click 'Send to Painter'.\n\n"
                "Maya exports the selection as FBX. The template's metadata\n"
                "constants (BRIDGE_MODES, LAUNCH_ARGS, RPC_SCRIPT,\n"
                "BUILD_MANIFEST, FBX_OPTIONS) drive the launch line and\n"
                "optional RPC step. Drop new templates into the templates\n"
                "folder (use __KEY__ tokens from parameters.py for tunable\n"
                "values) and use 'Refresh Templates' to pick them up."
            ),
        )

    # ------------------------------------------------------------------
    # Combo: (template, mode) pairs
    # ------------------------------------------------------------------

    @staticmethod
    def _format_combo_label(template: str, mode: str) -> str:
        """Display string for one combo entry: e.g. 'import (send_to)'."""
        return f"{template} ({mode})"

    def cmb000_init(self, widget):
        """Populate the template combobox with one entry per (template, mode)."""
        self._populate_template_combo(widget)
        widget.currentIndexChanged.connect(lambda _: self._on_template_changed())
        self._on_template_changed()

    def _populate_template_combo(self, widget):
        """Fill cmb000 with ``"<template> (<mode>)"`` entries.

        ``itemData`` carries the ``(template, mode)`` tuple so the click
        handler doesn't need to re-parse the display label.
        """
        pairs = list_template_modes()
        widget.blockSignals(True)
        try:
            widget.clear()
            for template, mode in pairs:
                widget.addItem(
                    self._format_combo_label(template, mode), (template, mode)
                )
            if pairs:
                # Prefer 'with_textures (send_to)' if present -- it's the
                # richest default (mesh + materials baked into the FBX).
                preferred = [("with_textures", "send_to"), ("import", "send_to")]
                for pref in preferred:
                    if pref in pairs:
                        widget.setCurrentIndex(pairs.index(pref))
                        break
                else:
                    widget.setCurrentIndex(0)
        finally:
            widget.blockSignals(False)

    def _refresh_templates(self):
        """Re-scan disk and repopulate cmb000."""
        self._populate_template_combo(self.ui.cmb000)
        self._on_template_changed()

    def _selected_template_mode(self) -> "tuple[str, str] | None":
        """Return the ``(template, mode)`` tuple for the active combo entry."""
        idx = self.ui.cmb000.currentIndex()
        if idx < 0:
            return None
        data = self.ui.cmb000.itemData(idx)
        if isinstance(data, tuple) and len(data) == 2:
            return data
        return None

    def _open_templates_folder(self):
        """Reveal the bundled templates directory in the OS file explorer."""
        path = str(_TEMPLATE_DIR)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606  (Windows-only API)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.sb.message_box(f"<b>Could not open folder:</b><br>{e}")

    # ------------------------------------------------------------------
    # Send button
    # ------------------------------------------------------------------

    def b000(self):
        """Process the selected transforms with the chosen template + mode."""
        import traceback

        if cmds is None:
            self.sb.message_box("<b>Maya is not available.</b>")
            return

        pair = self._selected_template_mode()
        if not pair:
            self.sb.message_box(
                "<b>No template chosen.</b><br>Pick a template from the dropdown."
            )
            return
        template, mode = pair

        # Templates that don't export FBX (e.g. ``render``) operate on the
        # project already loaded in Painter and don't need a Maya selection.
        meta = parse_template(_TEMPLATE_DIR / f"{template}.py")
        needs_selection = meta.get("EXPORT_FBX", True)

        selection = cmds.ls(selection=True) or []
        if needs_selection and not selection:
            self.sb.message_box(
                "<b>Nothing selected.</b><br>"
                "Select one or more polygon transforms before sending."
            )
            return

        if not self.bridge.painter_path:
            self.sb.message_box(
                "<b>Substance Painter not found.</b><br>"
                "Install Painter, or pass <code>painter_exe</code> when "
                "instantiating <code>SubstanceBridge</code>."
            )
            return

        self.ui.txt000.clear()
        self.bridge.logger.info(
            f"--- {template} ({mode}) on {len(selection)} object(s) ---"
        )

        try:
            result = self.bridge.send(
                objects=selection,
                template=template,
                mode=mode,
                params=self._collect_param_values(),
            )
        except Exception:
            print("=" * 60)
            print("Substance bridge failed:")
            traceback.print_exc()
            print("=" * 60)
            self.bridge.logger.error(
                "Bridge raised -- see Script Editor for traceback."
            )
            return

        if result is None:
            return  # logger already explained why

    # ------------------------------------------------------------------
    # Log panel: redirect bridge logger to txt000 with clickable links
    # ------------------------------------------------------------------

    def _redirect_log_to_panel(self):
        """Pipe SubstanceBridge's logger into the in-window QTextBrowser."""
        try:
            handler_cls = self.sb.registered_widgets.TextEditLogHandler
        except AttributeError:
            return
        try:
            logger = self.bridge.logger
            logger.hide_logger_name(True)
            logger.set_text_handler(handler_cls)
            logger.setup_logging_redirect(self.ui.txt000)
        except AttributeError:
            pass

    def _on_log_link_clicked(self, url):
        """Handle ``action://`` URIs emitted by bridge log entries."""
        try:
            from mayatk.ui_utils._ui_utils import UiUtils
        except Exception:
            return
        try:
            UiUtils.dispatch_log_link(url, self.bridge.logger)
        except Exception as e:
            self.bridge.logger.error(f"Could not open link: {e}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("substance_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
