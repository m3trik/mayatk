# !/usr/bin/python
# coding=utf-8
"""Slots for the RizomUV bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots.MayaBridgeSlotsBase`
(which itself subclasses uitk's :class:`BridgeSlotsBase`) -- the panel
machinery (widget construction, presets, log routing, startup info,
template/script description) lives upstream. This file owns only
Rizom-specific bits:

* The bridge factory (:meth:`make_bridge` returns a :class:`RizomUVBridge`).
* The script listing (:meth:`list_template_modes` returns
  ``[(stem, ""), ...]`` -- single-mode entries).
* The ``b000`` send action.
* The Rizom-specific header menu (Open UV Editor, Open Scripts Folder,
  Refresh Scripts, Clear Log, Instructions).
* ``REQUIRE_OUTPUT_DIR = False`` -- Rizom roundtrips through a temp FBX
  it manages internally; there's no user-visible artifact to point at,
  so the base class skips building the Output Dir row.

The base's :func:`template_description` extractor dispatches on the
file extension (``.py`` -> AST docstring, ``.lua`` -> leading ``--``
comment block), so the leading comment of each ``scripts/*.lua`` is
automatically logged when the user switches scripts -- no override
needed here.
"""
import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    cmds = None
    mel = None

from mayatk.ui_utils.maya_bridge_slots import MayaBridgeSlotsBase

# From this package:
from mayatk.uv_utils.rizom_bridge._rizom_bridge import (
    RizomUVBridge,
    _SCRIPT_DIR,
)
from mayatk.uv_utils.rizom_bridge import parameters as _params


_PRESETS_ROOT = Path("mayatk/rizom_bridge")


class RizomBridgeSlots(MayaBridgeSlotsBase):
    """Slots wired to ``rizom_bridge.ui`` via :class:`MayaBridgeSlotsBase`.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler` so
    ``self.sb.handlers.marking_menu.show("rizom_bridge")`` works from
    anywhere with no explicit registration.
    """

    UI_NAME = "rizom_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "rizom_bridge"

    # Rizom's bundled scripts (one per recipe: pack, unwrap, ...) live under
    # ``scripts/*.lua``. The base class defaults the extension to ``.py``
    # for marmoset/substance templates; this override is what lets
    # :meth:`_refresh_param_visibility` find the ``__KEY__`` placeholders
    # inside each ``.lua`` body. Distinct from the user-saved parameter
    # presets managed by :class:`PresetManager` -- those are JSON files
    # under :attr:`PRESETS_ROOT` and aren't affected by this setting.
    TEMPLATE_EXTENSION = ".lua"

    # Rizom roundtrips through a temp FBX it manages internally -- there's
    # no user-visible artifact to point at, so skip the Output Dir row.
    REQUIRE_OUTPUT_DIR = False

    # Narrower label column than the marmoset/substance default (90px) --
    # Rizom's param labels are short ("Spacing", "Mutations") so the
    # tighter column hugs the values more cleanly on a 220px-wide panel.
    LABEL_MIN_WIDTH = 80

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _params

    @property
    def template_dir(self) -> Path:
        return _SCRIPT_DIR

    def make_bridge(self) -> RizomUVBridge:
        return RizomUVBridge()

    def list_template_modes(self):
        """Return ``[(stem, ""), ...]`` for every bundled ``.lua`` script.

        Rizom has no per-template mode dimension (no analogue of
        marmoset's ``send_to`` / ``roundtrip``) -- every script is just
        a recipe the bridge runs end-to-end. We emit ``mode=""`` so the
        base class's :meth:`_format_combo_label` elides the parens.
        """
        return [
            (p.stem, "")
            for p in sorted(_SCRIPT_DIR.glob("*.lua"))
        ]

    # ------------------------------------------------------------------
    # Header menu
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu with Rizom-specific utilities."""
        widget.menu.add("Separator", setTitle="Utilities")
        widget.menu.add(
            "QPushButton",
            setText="Open UV Editor",
            setObjectName="btnopen_uv_editor",
            setToolTip="Open Maya's UV Editor for inspecting the result.",
        )
        widget.menu.btnopen_uv_editor.clicked.connect(self.open_uv_editor)

        widget.menu.add(
            "QPushButton",
            setText="Open Scripts Folder",
            setObjectName="btn_open_scripts",
            setToolTip="Reveal the bundled Lua preset folder in Explorer.",
        )
        widget.menu.btn_open_scripts.clicked.connect(self.open_templates_folder)

        widget.menu.add(
            "QPushButton",
            setText="Refresh Scripts",
            setObjectName="btn_refresh_scripts",
            setToolTip="Re-scan the scripts folder and rebuild the script combo.",
        )
        widget.menu.btn_refresh_scripts.clicked.connect(self.refresh_templates)

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

    # ------------------------------------------------------------------
    # b000 -- the per-bridge send action
    # ------------------------------------------------------------------

    def b000(self):
        """Process selected transforms with the chosen preset."""
        # All operational diagnostics route through the in-window log
        # panel, matching the marmoset/substance bridges' convention.
        if cmds is None:
            self.bridge.logger.error(
                "Maya is not available; cannot run the RizomUV bridge."
            )
            return

        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning(
                "No preset chosen. Pick a Lua preset from the dropdown above."
            )
            return
        preset, _mode = pair

        selection = cmds.ls(selection=True) or []
        if not selection:
            self.bridge.logger.warning(
                "Nothing selected. Select one or more polygon transforms "
                "before clicking 'Process Selected'."
            )
            return

        if not self.bridge.rizom_path:
            self.bridge.logger.error(
                "RizomUV not found. Install RizomUV and ensure it is on PATH, "
                "or set RizomUVBridge.rizom_path manually."
            )
            return

        self.bridge.logger.info(
            f"--- {preset} on {len(selection)} object(s) ---"
        )

        try:
            self.bridge.process_with_rizomuv(
                selection,
                preset=preset,
                params=self.collect_param_values(),
            )
        except Exception:
            self.bridge.logger.error(
                "Bridge raised:\n" + traceback.format_exc()
            )
            return

    # ------------------------------------------------------------------
    # Rizom-specific helpers (only the bits not covered by the base)
    # ------------------------------------------------------------------

    def open_uv_editor(self):
        """Open Maya's UV Editor (TextureViewWindow)."""
        if cmds is None or mel is None:
            self.bridge.logger.error("Maya is not available.")
            return
        try:
            mel.eval("TextureViewWindow;")
        except Exception as e:  # noqa: BLE001
            self.bridge.logger.error(f"Could not open UV Editor: {e}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("rizom_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
