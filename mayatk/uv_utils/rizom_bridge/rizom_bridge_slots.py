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


class _VersionedParamsProxy:
    """Wraps the ``parameters`` module so ``referenced_keys`` is Rizom-version-aware.

    The base :meth:`BridgeSlotsBase._refresh_param_visibility` shows rows
    whose placeholder appears in the active template. For Rizom we need
    the panel to ALSO hide widgets gated above the installed Rizom version
    -- otherwise the user can dial knobs that get silently stripped from
    the script before send. Strips the lua first, then delegates.

    Everything except ``referenced_keys`` falls through to the underlying
    module via ``__getattr__`` (``PARAMS``, ``defaults``, ``render_context``,
    ``strip_unsupported``).
    """

    def __init__(self, slot: "RizomBridgeSlots", module):
        self._slot = slot
        self._mod = module

    def referenced_keys(self, script_text: str):
        version = self._slot.bridge.rizom_version
        return self._mod.referenced_keys(
            self._mod.strip_unsupported(script_text, version)
        )

    def __getattr__(self, name):
        return getattr(self._mod, name)


class RizomBridgeSlots(MayaBridgeSlotsBase):
    """Slots wired to ``rizom_bridge.ui`` via :class:`MayaBridgeSlotsBase`.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler` so
    ``self.sb.handlers.marking_menu.show("rizom_bridge")`` works from
    anywhere with no explicit registration.
    """

    UI_NAME = "rizom_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "rizom_bridge"

    # Rizom's bundled scripts (one per recipe: pack, unwrap_hard, ...) live under
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

    # Rizom's header menu adds a UV-Editor shortcut and renames "Templates"
    # to "Scripts"; the base builds it from this data (handlers resolve to
    # open_uv_editor + the base's open_templates_folder / refresh_templates).
    HEADER_MENU_ITEMS = (
        (
            "Open UV Editor", "btn_open_uv_editor",
            "Open Maya's UV Editor for inspecting the result.", "open_uv_editor",
        ),
        (
            "Open Scripts Folder", "btn_open_scripts",
            "Reveal the bundled Lua preset folder in Explorer.",
            "open_templates_folder",
        ),
        (
            "Refresh Scripts", "btn_refresh_scripts",
            "Re-scan the scripts folder and rebuild the script combo.",
            "refresh_templates",
        ),
        ("Clear Log", "btn_clear_log", "Clear the log panel below.", "clear_log"),
    )
    HELP_SPEC = {
        "title": "RizomUV Bridge",
        "body": "Round-trip selected meshes through RizomUV using a "
        "Lua preset, or one-way send them and continue working in "
        "RizomUV directly.",
        "steps": [
            "Select one or more polygon transforms.",
            "Pick a <b>Lua preset</b> from the dropdown.",
            "Tweak the parameters that the preset exposes.",
            "Click <b>Process Selected</b>.",
        ],
        "sections": [
            ("Presets", [
                "<b>pack / unwrap_hard / unwrap_organic / optimize</b> "
                "— round-trip: Maya exports duplicates with "
                "<code>__RZTMP</code> suffix, RizomUV runs the script "
                "headlessly, UVs are transferred back onto originals.",
                "<b>send</b> — one-way: exports the selection directly "
                "(no rename), optionally collects diffuse textures "
                "from the shading networks, then launches RizomUV "
                "detached. Save manually inside RizomUV when done.",
            ]),
            ("Header menu", [
                "<b>Open UV Editor</b> — open Maya's UV Editor to "
                "inspect the result.",
                "<b>Open Scripts Folder</b> — reveal the bundled "
                "Lua preset folder in Explorer.",
                "<b>Refresh Scripts</b> — re-scan the scripts folder "
                "and rebuild the script combo.",
                "<b>Clear Log</b> — clear the log panel below.",
            ]),
        ],
        "notes": [
            "Add custom presets by dropping new <code>.lua</code> "
            "files into the scripts folder (use <code>__KEY__</code> "
            "tokens from <i>parameters.py</i> for tunable values), "
            "then click <b>Refresh Scripts</b>.",
        ],
    }

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _VersionedParamsProxy(self, _params)

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
    # b000 -- the per-bridge send action
    # ------------------------------------------------------------------

    # Name of the pseudo-preset that triggers the one-way send flow
    # instead of the headless round-trip. Picking it in the combo causes
    # the panel to reveal the load-option widgets (LOAD_UVS, IMPORT_GROUPS,
    # ...) via the existing placeholder-discovery scan over send.lua.
    SEND_PRESET = "send"

    def b000(self):
        """Run the chosen preset: round-trip, or one-way send when ``send`` is picked."""
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
            with self.sb.progress(text=f"Working: RizomUV {preset}"):
                if preset == self.SEND_PRESET:
                    # One-way: open in RizomUV, no UV transfer back. Maya
                    # returns control immediately after Rizom is launched.
                    self.bridge.send_to_rizomuv(
                        selection,
                        params=self.collect_param_values(),
                    )
                else:
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
