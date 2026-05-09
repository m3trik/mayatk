# !/usr/bin/python
# coding=utf-8
import os
import sys
import subprocess

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from qtpy import QtWidgets

# From this package:
from mayatk.uv_utils.rizom_bridge._rizom_bridge import (
    RizomUVBridge,
    _SCRIPT_DIR,
)
from mayatk.uv_utils.rizom_bridge import parameters as _params


class RizomBridgeSlots:
    """UI slots for the RizomUV bridge."""

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.rizom_bridge
        self._bridge = None
        self._param_widgets: "dict[str, QtWidgets.QWidget]" = {}
        self._param_rows: "dict[str, QtWidgets.QWidget]" = {}
        self._build_param_widgets()

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
        """Inject a 'Parameters' group between the preset combo and Process button."""
        grp = QtWidgets.QGroupBox("Parameters", self.ui.grp_process)
        form = QtWidgets.QFormLayout(grp)
        form.setContentsMargins(2, 4, 2, 2)
        form.setSpacing(1)
        form.setLabelAlignment(_qt_align_right())
        form.setFormAlignment(_qt_align_top())

        for key, spec in _params.PARAMS.items():
            widget = self._make_widget_for(spec)
            widget.setObjectName(f"param_{key.lower()}")
            widget.setMinimumHeight(20)
            widget.setMaximumHeight(20)
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)
            label = QtWidgets.QLabel(spec.label + ":")
            label.setToolTip(spec.tooltip)
            form.addRow(label, widget)

            self._param_widgets[key] = widget
            # Store the row so we can hide both label and widget together.
            self._param_rows[key] = (label, widget)

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
        for key, (label, widget) in self._param_rows.items():
            visible = key in used
            label.setVisible(visible)
            widget.setVisible(visible)
        self._param_group.setVisible(bool(used))

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
            setText="Open Scripts Folder",
            setObjectName="btn_open_scripts",
            setToolTip="Reveal the bundled Lua preset folder in Explorer.",
        )
        widget.menu.btn_open_scripts.clicked.connect(self._open_scripts_folder)

        widget.menu.add(
            "QPushButton",
            setText="Refresh Presets",
            setObjectName="btn_refresh_presets",
            setToolTip="Re-scan the scripts folder and rebuild the preset list.",
        )
        widget.menu.btn_refresh_presets.clicked.connect(self._refresh_presets)

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
                "and use 'Refresh Presets' to pick them up."
            ),
        )

    def cmb000_init(self, widget):
        """Populate the preset combobox from scripts/*.lua."""
        presets = self._list_presets()
        widget.add(presets)
        if presets:
            widget.setCurrentIndex(0)
        # Currently-selected preset drives which param widgets are visible.
        widget.currentIndexChanged.connect(lambda _: self._refresh_param_visibility())
        self._refresh_param_visibility()

    def _refresh_presets(self):
        """Repopulate cmb000 from disk."""
        self.cmb000_init(self.ui.cmb000)

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
            self.sb.message_box(f"<b>RizomUV bridge failed:</b><br>{e}")
            return

        self.sb.message_box(
            f"<hl>RizomUV '{preset}' applied to {len(selection)} object(s).</hl>"
        )


def _qt_align_right():
    from qtpy import QtCore
    return QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter


def _qt_align_top():
    from qtpy import QtCore
    return QtCore.Qt.AlignTop


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("rizom_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
