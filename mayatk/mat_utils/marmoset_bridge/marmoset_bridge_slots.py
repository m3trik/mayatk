# !/usr/bin/python
# coding=utf-8
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
from mayatk.mat_utils.marmoset_bridge._marmoset_bridge import (
    MarmosetBridge,
    SEND_TO,
    ROUNDTRIP,
    _TEMPLATE_DIR,
    list_templates,
    list_template_modes,
)
from mayatk.mat_utils.marmoset_bridge import parameters as _params


_PRESETS_ROOT = Path("~/.mayatk/presets/marmoset_bridge").expanduser()


class MarmosetBridgeSlots:
    """UI slots for the Marmoset Toolbag bridge.

    Architectural twin of :class:`mayatk.uv_utils.rizom_bridge.RizomBridgeSlots`:
    one parameter row per registered :class:`MarmosetParam`, with rows
    auto-shown/hidden based on which placeholders the selected template
    references.
    """

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.marmoset_bridge
        self._bridge = None
        self._param_widgets: "dict[str, QtWidgets.QWidget]" = {}
        self._param_rows: "dict[str, QtWidgets.QWidget]" = {}
        self._preset_mgr: "PresetManager | None" = None
        self._preset_combo: "WidgetComboBox | None" = None
        self._param_visibility_settled = False
        self._build_param_widgets()
        self._build_preset_controls()

        # Pipe MarmosetBridge's logger into the in-window log panel and
        # treat ``action://`` URIs in the log as clickable open-folder links.
        # Failures here must NEVER break the param widgets we just built,
        # so we swallow anything the logger setup throws.
        try:
            self._redirect_log_to_panel()
            if hasattr(self.ui.txt000, "anchorClicked"):
                self.ui.txt000.anchorClicked.connect(self._on_log_link_clicked)
        except Exception as e:  # noqa: BLE001
            print(f"[marmoset_bridge] log panel wiring failed (ignored): {e}")

    @property
    def bridge(self) -> MarmosetBridge:
        """Lazy-instantiated MarmosetBridge (defers Toolbag path lookup)."""
        if self._bridge is None:
            self._bridge = MarmosetBridge()
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
            # Don't pin the height for composite widgets (path = container +
            # internal hbox); clipping the container hides the line edit.
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

        # Insert above the Send button (b000 is at the bottom of grp_process).
        parent_layout = self.ui.grp_process.layout()
        insert_at = parent_layout.indexOf(self.ui.b000)
        parent_layout.insertWidget(insert_at, grp)
        self._param_group = grp

    def _make_widget_for(self, spec: _params.MarmosetParam) -> QtWidgets.QWidget:
        """Construct a Qt widget for *spec*. Supports int/float/choice/bool/path.

        Widgets are returned parentless; the caller calls ``setParent(row)``
        so the construction order matches the rizom_bridge pattern that's
        known to render correctly inside Switchboard.
        """
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
            # uitk's CheckBox is a QCheckBox subclass: PresetManager picks
            # it up via its QCheckBox isinstance check, and its overridden
            # hitButton makes the entire widget bounds clickable. The
            # native indicator is collapsed to 0x0 by the global theme QSS
            # (intentional -- uitk shows state via rich-text color), so we
            # use the text itself as the affordance: "On"/"Off" toggles on
            # state change, against the theme's BUTTON_CHECKED background.
            w = CheckBox()
            w.setChecked(bool(spec.default))
            w.setText("On" if w.isChecked() else "Off")
            # uitk's constructor ran set_checkbox_rich_text_style before any
            # text existed (no-op via has_rich_text guard), so apply the
            # initial style explicitly now. Future state changes are handled
            # by uitk's own stateChanged slot (connected first) -- it
            # updates the style before our lambda below replaces the text.
            w.set_checkbox_rich_text_style(w.isChecked())
            w.stateChanged.connect(
                lambda state, btn=w: btn.setText("On" if state else "Off")
            )
            return w

        if spec.widget_type == "path":
            # Composite widget: line edit + browse button. The OUTER container
            # is intentionally height-free so it can grow to fit its children;
            # _build_param_widgets skips the 19px clamp for path widgets.
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
            browse.clicked.connect(lambda _=None, e=edit: self._browse_dir(e))
            hl.addWidget(edit, 1)
            hl.addWidget(browse)
            container._line_edit = edit  # exposed for read/write helpers
            return container

        raise ValueError(f"Unknown widget_type {spec.widget_type!r} for {spec.key}")

    @staticmethod
    def _browse_dir(line_edit: QtWidgets.QLineEdit):
        start = line_edit.text() or str(Path.home())
        path = QtWidgets.QFileDialog.getExistingDirectory(
            line_edit, "Select output directory", start
        )
        if path:
            line_edit.setText(path)

    def _read_param(self, key: str):
        w = self._param_widgets[key]
        if isinstance(w, QtWidgets.QComboBox):
            return w.currentData()
        if isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            return w.value()
        if isinstance(w, QtWidgets.QAbstractButton):
            return w.isChecked()
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
        """Insert a user-preset combobox + 'Reset to Defaults' above b000.

        ``PresetManager`` works on the raw param widgets. The composite
        'path' widget is a QWidget container, so we hand the inner line
        edit to the manager instead.
        """
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

        # Hand the PresetManager the underlying serialisable widgets.
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
        templates = self._list_templates()
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

    def _list_templates(self):
        """Return template stem names from the bundled templates/ directory."""
        return [p.stem for p in list_templates()]

    def header_init(self, widget):
        """Configure header menu with utilities (no per-call options).

        Headless mode is no longer surfaced -- it's derived from the chosen
        template's mode (``roundtrip`` always headless, ``send_to`` never).
        """
        widget.menu.add("Separator", setTitle="Utilities")
        widget.menu.add(
            "QPushButton",
            setText="Open Templates Folder",
            setObjectName="btn_open_templates",
            setToolTip="Reveal the bundled Toolbag template folder in Explorer.",
        )
        widget.menu.btn_open_templates.clicked.connect(self._open_templates_folder)

        widget.menu.add(
            "QPushButton",
            setText="Refresh Templates",
            setObjectName="btn_refresh_templates",
            setToolTip="Re-scan the templates folder and rebuild the template combo.",
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
                "Marmoset Bridge -- Send selected meshes to Toolbag.\n\n"
                "1. Select one or more polygon transforms.\n"
                "2. Pick a template + mode from the dropdown:\n"
                "     * 'send_to' opens Toolbag for you to drive interactively.\n"
                "     * 'roundtrip' runs Toolbag headless, then re-surfaces\n"
                "        the generated maps in the log panel below as\n"
                "        clickable links (Maya scene untouched).\n"
                "3. Adjust the parameters the template exposes.\n"
                "4. Click 'Send to Marmoset'.\n\n"
                "Maya exports the selection as FBX with a MatManifest JSON\n"
                "sidecar; Toolbag runs the rendered template with your\n"
                "parameter values substituted in.\n\n"
                "A template's supported modes are declared in its BRIDGE_MODES\n"
                "tuple at the top of the .py file. Drop new templates into\n"
                "the templates folder (use __KEY__ tokens from parameters.py\n"
                "for tunable values) and use 'Refresh Templates' to pick\n"
                "them up."
            ),
        )

    # --- Combo: (template, mode) pairs ---------------------------------

    @staticmethod
    def _format_combo_label(template: str, mode: str) -> str:
        """Display string for one combo entry: e.g. 'bake (roundtrip)'."""
        return f"{template} ({mode})"

    def cmb000_init(self, widget):
        """Populate the template combobox with one entry per (template, mode)."""
        self._populate_template_combo(widget)
        widget.currentIndexChanged.connect(lambda _: self._on_template_changed())
        self._on_template_changed()

    def _populate_template_combo(self, widget):
        """Fill cmb000 with ``"<template> (<mode>)"`` entries.

        ``itemData`` carries the ``(template, mode)`` tuple so the click
        handler doesn't have to re-parse the display label.
        """
        pairs = list_template_modes()
        widget.blockSignals(True)
        try:
            widget.clear()
            for template, mode in pairs:
                widget.addItem(self._format_combo_label(template, mode), (template, mode))
            if pairs:
                # Prefer 'bake (roundtrip)' if present, then 'bake (send_to)',
                # then the first entry.
                preferred = [
                    ("bake", ROUNDTRIP),
                    ("bake", SEND_TO),
                ]
                for pref in preferred:
                    if pref in pairs:
                        widget.setCurrentIndex(pairs.index(pref))
                        break
                else:
                    widget.setCurrentIndex(0)
        finally:
            widget.blockSignals(False)

    def _refresh_templates(self):
        """Repopulate cmb000 from disk and resync the parameter UI."""
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
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.sb.message_box(f"<b>Could not open folder:</b><br>{e}")

    def b000(self):
        """Process selected transforms with the chosen template + mode."""
        import traceback

        if cmds is None:
            self.sb.message_box("<b>Maya is not available.</b>")
            return

        selection = cmds.ls(selection=True) or []
        if not selection:
            self.sb.message_box(
                "<b>Nothing selected.</b><br>"
                "Select one or more polygon transforms before sending."
            )
            return

        pair = self._selected_template_mode()
        if not pair:
            self.sb.message_box(
                "<b>No template chosen.</b><br>Pick a template from the dropdown."
            )
            return
        template, mode = pair

        if not self.bridge.toolbag_path:
            self.sb.message_box(
                "<b>Marmoset Toolbag not found.</b><br>"
                "Install Toolbag and ensure it is on PATH, or set "
                "<code>MarmosetBridge.toolbag_path</code> manually."
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
            print("Marmoset bridge failed:")
            traceback.print_exc()
            print("=" * 60)
            self.bridge.logger.error("Bridge raised -- see Script Editor for traceback.")
            return

        if result is None:
            return  # logger already explained why

    # ------------------------------------------------------------------
    # Log panel: redirect bridge logger to txt000 with clickable links
    # ------------------------------------------------------------------

    def _redirect_log_to_panel(self):
        """Pipe MarmosetBridge's logger into the in-window QTextBrowser.

        Best-effort: any missing piece (widget registry entry, LoggingMixin
        helper method, etc.) just falls through to console logging instead
        of breaking the slot wiring.
        """
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

    ui = MayaUiHandler.instance().get("marmoset_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
