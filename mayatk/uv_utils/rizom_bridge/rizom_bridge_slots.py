# !/usr/bin/python
# coding=utf-8
import os
import sys
import subprocess
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from qtpy import QtCore, QtWidgets

from uitk.widgets.pushButton import PushButton
from uitk.widgets.widgetComboBox import WidgetComboBox
from uitk.widgets.mixins.preset_manager import PresetManager

# From this package:
from mayatk.uv_utils.rizom_bridge._rizom_bridge import (
    RizomUVBridge,
    _SCRIPT_DIR,
)
from mayatk.uv_utils.rizom_bridge import parameters as _params


_PRESETS_ROOT = Path("~/.mayatk/presets/rizom_bridge").expanduser()


class RizomBridgeSlots:
    """UI slots for the RizomUV bridge."""

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.rizom_bridge
        self._bridge = None
        self._param_widgets: "dict[str, QtWidgets.QWidget]" = {}
        # key -> (label, widget, row_index) so we can call setRowVisible.
        self._param_rows: "dict[str, tuple]" = {}
        self._preset_mgr: "PresetManager | None" = None
        self._preset_combo: "WidgetComboBox | None" = None
        # Tracks whether _refresh_param_visibility has run once. The first
        # run happens during showEvent (cmb000_init) -- we skip fit_height
        # then so the just-restored saved window geometry isn't trampled.
        self._param_visibility_settled = False
        self._build_param_widgets()
        self._build_preset_controls()

    @property
    def bridge(self) -> RizomUVBridge:
        """Lazy-instantiated RizomUVBridge (defers RizomUV path lookup)."""
        if self._bridge is None:
            self._bridge = RizomUVBridge()
        return self._bridge

    # ------------------------------------------------------------------
    # Parameter widget construction (parameters.PARAMS -> Qt widgets)
    # ------------------------------------------------------------------

    def _build_param_widgets(self):
        """Inject a 'Parameters' group between the preset combo and Process button.

        Each parameter is its own row widget inside a QVBoxLayout rather
        than a QFormLayout row. ``QFormLayout.setRowVisible`` only hides
        widgets in Qt 5.15 -- it doesn't aggressively invalidate the
        layout's sizeHint, so the parent group (and the window) never
        shrink when rows hide. A row widget hidden via ``setVisible(False)``
        is fully excluded from QVBoxLayout's sizeHint.
        """
        grp = QtWidgets.QGroupBox("Parameters", self.ui.grp_process)
        vbox = QtWidgets.QVBoxLayout(grp)
        vbox.setContentsMargins(2, 4, 2, 2)
        vbox.setSpacing(0)

        for key, spec in _params.PARAMS.items():
            row = QtWidgets.QWidget(grp)
            hbox = QtWidgets.QHBoxLayout(row)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(0)

            label = QtWidgets.QLabel(spec.label + ":", row)
            label.setMinimumWidth(80)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            label.setToolTip(spec.tooltip)

            widget = self._make_widget_for(spec)
            widget.setParent(row)
            widget.setObjectName(f"param_{key.lower()}")
            widget.setMinimumHeight(19)
            widget.setMaximumHeight(19)
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)

            hbox.addWidget(label)
            hbox.addWidget(widget, 1)
            vbox.addWidget(row)

            self._param_widgets[key] = widget
            self._param_rows[key] = row

        # Insert above the Process button (b000 is at index 1 inside grp_process).
        parent_layout = self.ui.grp_process.layout()
        parent_layout.insertWidget(1, grp)
        self._param_group = grp

    @staticmethod
    def _make_widget_for(spec: _params.RizomParam) -> QtWidgets.QWidget:
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
        raise ValueError(f"Unknown widget_type {spec.widget_type!r} for {spec.key}")

    def _read_param(self, key: str):
        w = self._param_widgets[key]
        if isinstance(w, QtWidgets.QComboBox):
            return w.currentData()
        if isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            return w.value()
        raise TypeError(f"Don't know how to read value from {type(w).__name__}")

    def _collect_param_values(self) -> dict:
        """Snapshot all widget values, regardless of visibility."""
        return {key: self._read_param(key) for key in self._param_widgets}

    def _refresh_param_visibility(self):
        """Show only the rows whose placeholder appears in the selected preset."""
        preset = (self.ui.cmb000.currentText() or "").strip()
        if not preset:
            return
        path = _SCRIPT_DIR / f"{preset}.lua"
        if not path.is_file():
            return
        used = _params.referenced_keys(path.read_text(encoding="utf-8"))

        for key, row in self._param_rows.items():
            row.setVisible(key in used)
        self._param_group.setVisible(bool(used))

        # Skip fit on the very first call (during cmb000_init, which runs
        # inside showEvent after restore_window_geometry) so we don't
        # trample the user's saved height. Subsequent user-driven script
        # changes do fit, deferred to the next event-loop tick so Qt has
        # processed the visibility changes before we read sizeHints --
        # otherwise the row hides queue up and the window reads its
        # pre-hide minimum.
        if self._param_visibility_settled:
            fit = getattr(self.ui, "fit_height_to_content", None)
            if callable(fit):
                QtCore.QTimer.singleShot(0, fit)
        self._param_visibility_settled = True

    # ------------------------------------------------------------------
    # User-saved presets (uitk PresetManager)
    # ------------------------------------------------------------------

    def _build_preset_controls(self):
        """Insert a user-preset combobox + 'Reset to Defaults' button above b000.

        Uses ``uitk.widgets.mixins.preset_manager.PresetManager`` in standalone
        mode, with ``preset_dir`` swapped per active script so each preset
        (pack / unwrap / optimize) gets its own namespace on disk.
        """
        layout = self.ui.grp_process.layout()

        combo = WidgetComboBox(self.ui.grp_process)
        combo.setObjectName("cmb_user_presets")
        combo.setMinimumHeight(19)
        combo.setMaximumHeight(19)
        combo.setToolTip(
            "Saved user presets for the active script.\n"
            "Open the side menu to Save / Rename / Delete the current values."
        )

        reset_btn = PushButton(self.ui.grp_process)
        reset_btn.setObjectName("btn_reset_defaults")
        reset_btn.setText("Reset to Defaults")
        reset_btn.setMinimumHeight(19)
        reset_btn.setMaximumHeight(19)
        reset_btn.setToolTip("Restore every parameter widget to its registry default.")
        reset_btn.clicked.connect(self._reset_to_defaults)

        # Insert immediately above the existing Process button.
        insert_at = layout.indexOf(self.ui.b000)
        layout.insertWidget(insert_at, combo)
        layout.insertWidget(insert_at + 1, reset_btn)

        # PresetManager scoped to the current script's subdirectory. The dir
        # is repointed in cmb000_init when the user switches preset scripts.
        self._preset_mgr = PresetManager.from_widgets(
            preset_dir=_PRESETS_ROOT / self._active_preset(),
            widgets=list(self._param_widgets.values()),
        )
        self._preset_mgr.wire_combo(combo)

        self._preset_combo = combo
        self._reset_btn = reset_btn

    def _active_preset(self) -> str:
        """The currently-selected script stem.

        Falls back to the first script discovered on disk (and finally to
        ``"default"``) so the PresetManager constructed in ``__init__``
        doesn't eagerly create a stray ``presets/.../default/`` folder
        before ``cmb000_init`` has populated the script combo.
        """
        try:
            text = (self.ui.cmb000.currentText() or "").strip()
        except (AttributeError, RuntimeError):
            text = ""
        if text:
            return text
        presets = self._list_presets()
        return presets[0] if presets else "default"

    def _on_script_changed(self):
        """React to the user swapping presets: re-show widgets, re-point preset dir."""
        self._refresh_param_visibility()
        if self._preset_mgr is not None:
            self._preset_mgr.preset_dir = _PRESETS_ROOT / self._active_preset()
            # wire_combo stashed its repopulation hook on the manager; the
            # preset combo is otherwise stale after a preset_dir swap.
            refresh = getattr(self._preset_mgr, "_refresh_combo", None)
            if callable(refresh):
                refresh()

    def _reset_to_defaults(self):
        """Restore every parameter widget to the registry default."""
        for key, spec in _params.PARAMS.items():
            widget = self._param_widgets.get(key)
            if widget is None:
                continue
            if isinstance(widget, QtWidgets.QSpinBox):
                widget.setValue(int(spec.default))
            elif isinstance(widget, QtWidgets.QDoubleSpinBox):
                widget.setValue(float(spec.default))
            elif isinstance(widget, QtWidgets.QComboBox):
                for i in range(widget.count()):
                    if widget.itemData(i) == spec.default:
                        widget.setCurrentIndex(i)
                        break
        # Clear preset-combo selection so the displayed name doesn't lie
        # about the values that are now active.
        if self._preset_combo is not None:
            self._preset_combo.blockSignals(True)
            try:
                self._preset_combo.setCurrentIndex(-1)
            finally:
                self._preset_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Header menu / utilities
    # ------------------------------------------------------------------

    def _list_presets(self):
        """Return preset stem names from the bundled scripts/ directory."""
        return sorted(p.stem for p in _SCRIPT_DIR.glob("*.lua"))

    def header_init(self, widget):
        """Configure header menu with tool instructions and utilities."""
        widget.menu.add("Separator", setTitle="Utilities")
        widget.menu.add(
            "QPushButton",
            setText="Open UV Editor",
            setObjectName="btn_open_uv_editor",
            setToolTip="Open Maya's UV Editor for inspecting the result.",
        )
        widget.menu.btn_open_uv_editor.clicked.connect(self._open_uv_editor)

        widget.menu.add(
            "QPushButton",
            setText="Open Scripts Folder",
            setObjectName="btn_open_scripts",
            setToolTip="Reveal the bundled Lua preset folder in Explorer.",
        )
        widget.menu.btn_open_scripts.clicked.connect(self._open_scripts_folder)

        widget.menu.add(
            "QPushButton",
            setText="Refresh Scripts",
            setObjectName="btn_refresh_scripts",
            setToolTip="Re-scan the scripts folder and rebuild the script combo.",
        )
        widget.menu.btn_refresh_scripts.clicked.connect(self._refresh_scripts)

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "RizomUV Bridge -- Round-trip selected meshes through RizomUV.\n\n"
                "1. Select one or more polygon transforms.\n"
                "2. Pick a Lua preset (pack, unwrap, optimize, ...).\n"
                "3. Adjust the parameters that the preset exposes.\n"
                "4. Click 'Process Selected'.\n\n"
                "Maya exports duplicates with a __RZTMP suffix as FBX,\n"
                "RizomUV runs the script headlessly with your parameter\n"
                "values substituted in, and the resulting UVs are\n"
                "transferred back onto the originals.\n\n"
                "Drop new presets as .lua files into the scripts folder\n"
                "(use __KEY__ tokens from parameters.py for tunable values)\n"
                "and use 'Refresh Scripts' to pick them up."
            ),
        )

    def cmb000_init(self, widget):
        """Populate the preset combobox from scripts/*.lua (one-time setup)."""
        self._populate_script_combo(widget)
        # Connect once -- _refresh_scripts repopulates without re-binding.
        widget.currentIndexChanged.connect(lambda _: self._on_script_changed())
        self._on_script_changed()

    def _populate_script_combo(self, widget):
        """Fill cmb000 with the current set of bundled scripts."""
        presets = self._list_presets()
        widget.blockSignals(True)
        try:
            widget.add(presets)
            if presets:
                widget.setCurrentIndex(0)
        finally:
            widget.blockSignals(False)

    def _refresh_scripts(self):
        """Repopulate cmb000 from disk and resync the parameter UI."""
        self._populate_script_combo(self.ui.cmb000)
        self._on_script_changed()

    def _open_uv_editor(self):
        """Open Maya's UV Editor (TextureViewWindow)."""
        if cmds is None:
            self.sb.message_box("<b>Maya is not available.</b>")
            return
        try:
            import maya.mel as mel

            mel.eval("TextureViewWindow;")
        except Exception as e:
            self.sb.message_box(f"<b>Could not open UV Editor:</b><br>{e}")

    def _open_scripts_folder(self):
        """Reveal the bundled scripts directory in the OS file explorer."""
        path = str(_SCRIPT_DIR)
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
        """Process selected transforms with the chosen preset."""
        import traceback

        selection = cmds.ls(selection=True) or []
        if not selection:
            self.sb.message_box(
                "<b>Nothing selected.</b><br>"
                "Select one or more polygon transforms before processing."
            )
            return

        preset = (self.ui.cmb000.currentText() or "").strip()
        if not preset:
            self.sb.message_box(
                "<b>No preset chosen.</b><br>Pick a Lua preset from the dropdown."
            )
            return

        if not self.bridge.rizom_path:
            self.sb.message_box(
                "<b>RizomUV not found.</b><br>"
                "Install RizomUV and ensure it is on PATH, or set "
                "<code>RizomUVBridge.rizom_path</code> manually."
            )
            return

        try:
            self.bridge.process_with_rizomuv(
                selection,
                preset=preset,
                params=self._collect_param_values(),
            )
        except Exception as e:
            # Console: full traceback so the user can read the chain
            # of bridge debug prints leading up to the failure.
            print("=" * 60)
            print("RizomUV bridge failed:")
            traceback.print_exc()
            print("=" * 60)

            self.sb.message_box(
                "<hl>RizomUV bridge failed.</hl><br>See Script Editor for details."
            )
            return

        self.sb.message_box(
            f"<hl>RizomUV '{preset}' applied to {len(selection)} object(s).</hl>"
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("rizom_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
