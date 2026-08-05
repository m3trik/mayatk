# !/usr/bin/python
# coding=utf-8
"""Slots for the Substance Painter bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots_base.MayaBridgeSlotsBase`
(itself a :class:`uitk.bridge.BridgeSlotsBase`). The panel machinery
lives upstream. Substance-specific extras live below: the ``b000`` send
action (FBX export + Painter handoff with optional RPC dispatch).

Assigned-mesh textures (formerly a ``file_list`` browser called
``PAINTER_BAKED_MAPS``) are now driven by the boolean
``PAINTER_INCLUDE_TEXTURES`` -- when True, the bridge walks the
selection's shading networks and stages the resolved textures into the
FBX output folder, then passes each one via ``--mesh-map`` on launch.
The companion ``PAINTER_TEXTURE_PREFIX`` widget is greyed out while
INCLUDE_TEXTURES is off so the user can't dial in a prefix that won't
be applied.
"""

import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from uitk.bridge.spec import KindFactory
from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase

# From this package:
from mayatk.mat_utils.substance_bridge._substance_bridge import (
    BakeSourceSet,
    SubstanceBridge,
    _TEMPLATE_DIR,
)
from mayatk.mat_utils.substance_bridge import parameters as _params


_PRESETS_ROOT = Path("mayatk/substance_bridge")


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
    # Fall back to a self-cleaning temp folder when no scene/workspace dir resolves
    # (unsaved scene) — the FBX + staged maps are transient hand-off artifacts Painter
    # reads once, so the user shouldn't be forced to pick a path.
    TEMP_OUTPUT_FALLBACK = True

    # Header = the base panel-level utilities only (Clear Log). Template
    # management lives on the template combo's own menu; the Bake Source set
    # actions are the BAKE_SOURCE_SET param row (parameters.py) -- the base
    # auto-wires its buttons to the same-named methods below. The
    # ``PAINTER_HIGH_POLY`` checkbox only decides whether to ship the set.

    HELP_SPEC = {
        "title": "Substance Bridge",
        "body": "Send selected meshes to Substance Painter. Maya exports "
        "the selection as FBX; the template's metadata constants "
        "(<i>BRIDGE_MODES</i>, <i>LAUNCH_ARGS</i>, <i>RPC_SCRIPT</i>, "
        "<i>BUILD_MANIFEST</i>, <i>FBX_OPTIONS</i>) drive the launch "
        "line and optional RPC step.",
        "steps": [
            "Set the <b>Output Dir</b> (or leave blank to use the "
            "scene directory; an unsaved scene falls back to a temp folder).",
            "Select one or more polygon transforms.",
            "Pick a <b>Template + Mode</b> from the dropdown.",
            "Tweak the template's exposed parameters.",
            "Click <b>Send to Painter</b>.",
        ],
        "sections": [
            (
                "Modes",
                [
                    "<b>send_to</b> — launches Painter for interactive work.",
                    "<b>roundtrip</b> — launches Painter with remote "
                    "scripting, sends the template's JS body via "
                    "JSON-RPC, and waits for completion.",
                ],
            ),
        ],
        "notes": [
            "<b>reimport</b> overwrites the FBX from the last send and "
            "reloads it in the already-running Painter (never launches a "
            "new one). Needs the <i>substance_rpc</i> Painter plugin, "
            "installed automatically on send. <b>First-run:</b> activate "
            "it once in Painter — <i>Python > Reload Plugins Folder</i> "
            "(or relaunch Painter), then tick <i>substance_rpc</i> in the "
            "<i>Python</i> menu (Painter remembers it). Without a reachable "
            "Painter the log shows the manual reload steps.",
            "<b>Export Bake Source</b> ships a companion "
            "<i>&lt;name&gt;_source.fbx</i> and sets it as Painter's "
            "<i>Hipoly Mesh</i> in the baking options. Define the set once "
            "with the <b>Bake Source</b> row's <b>Set From Selection</b> — "
            "it lives in the scene (an objectSet), so it survives saves and "
            "restarts and is independent of the <b>Scope</b>. Hidden "
            "geometry needs no preparation: FBX carries it verbatim, so the "
            "export never touches your scene.",
            "<b>Map Resolution</b> and <b>Export Bake Source</b> have no "
            "Painter command line any more, so they travel over the "
            "<i>substance_rpc</i> plugin. On a project that is already open "
            "they apply at once; on a fresh launch the plugin holds them "
            "and applies them the moment the New Project wizard finishes — "
            "so the first send after Painter starts waits briefly for the "
            "plugin's endpoint.",
            "Add custom templates by dropping new files into the "
            "templates folder (use <code>__KEY__</code> tokens from "
            "<i>parameters.py</i> for tunable values), then use <b>Refresh "
            "Templates</b> on the template dropdown's menu.",
        ],
    }

    def __init__(self, switchboard):
        super().__init__(switchboard)
        self._wire_texture_prefix_dependency()

    def _wire_texture_prefix_dependency(self) -> None:
        """Grey out the texture sub-options while ``Include Textures`` is off.

        ``Texture Prefix`` and ``Unpack Packed Maps`` both only act on files
        the staging step copies, so neither means anything with staging off.
        Each widget only exists when the active template references it (e.g.
        ``import.py``); missing ones are skipped so the panel stays usable on
        templates that omit them.
        """
        include_widget = self._param_widgets.get("PAINTER_INCLUDE_TEXTURES")
        if include_widget is None:
            return
        dependents = [
            widget
            for key in ("PAINTER_TEXTURE_PREFIX", "PAINTER_UNPACK_MAPS")
            for widget in [self._param_widgets.get(key)]
            if widget is not None
        ]
        if not dependents:
            return

        def _sync(_value=None):
            enabled = bool(KindFactory.read_value(include_widget))
            for widget in dependents:
                widget.setEnabled(enabled)

        KindFactory.connect_changed(include_widget, _sync)
        _sync()

    # ------------------------------------------------------------------
    # Bake Source set (param-row actions; shared with the marmoset bridge)
    # ------------------------------------------------------------------

    def set_bake_source_from_selection(self) -> None:
        """Store the current selection as the scene's bake source.

        Ticks ``Export Bake Source`` on success -- defining the set is only
        ever done in order to ship it, so making the user find the checkbox
        afterwards would be a pure extra step.
        """
        if cmds is None:
            return
        members = BakeSourceSet.define()
        if not members:
            self.bridge.logger.warning(
                "Nothing selected; the bake-source set was cleared."
            )
            return
        self.bridge.logger.info(
            f"Bake Source set: {len(members)} object(s) "
            f"-> {BakeSourceSet.SET_NAME}"
        )
        widget = self._param_widgets.get("PAINTER_HIGH_POLY")
        if widget is not None:
            KindFactory.set_value(widget, True)

    def select_bake_source(self) -> None:
        """Select the bake-source set's members (hidden ones included)."""
        if cmds is None:
            return
        members = BakeSourceSet.members()
        if not members:
            self.bridge.logger.warning("This scene has no bake-source set.")
            return
        cmds.select(members, replace=True)
        self.bridge.logger.info(f"Selected {len(members)} bake-source object(s).")

    def clear_bake_source(self) -> None:
        """Delete the bake-source set node; its members are left alone."""
        if cmds is None:
            return
        if not BakeSourceSet.exists():
            self.bridge.logger.warning("This scene has no bake-source set.")
            return
        BakeSourceSet.clear()
        self.bridge.logger.info("Bake-source set cleared.")

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _params.Parameters

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> SubstanceBridge:
        return SubstanceBridge()

    def list_template_modes(self):
        return SubstanceBridge.list_template_modes()

    def select_initial_template_index(self, pairs):
        """Default the panel to ``import (send_to)`` when it's available."""
        pref = ("import", "send_to")
        return pairs.index(pref) if pref in pairs else 0

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
        meta = SubstanceBridge.parse_template(_TEMPLATE_DIR / f"{template}.py")
        needs_selection = meta.get("EXPORT_FBX", True)

        # Scope resolves via the shared bridge-slots base. Warn only when this
        # template actually needs geometry -- ``render`` operates on the project
        # already open in Painter.
        params = self.collect_param_values()
        selection = self.scoped_objects(params, warn=needs_selection)
        if needs_selection and not selection:
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
            with self.sb.progress(text=f"Working: Substance {template} ({mode})"):
                result = self.bridge.send(
                    objects=selection,
                    template=template,
                    mode=mode,
                    output_dir=output_dir,
                    params=params,
                )
        except Exception:
            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())
            return

        if result is None:
            return  # logger already explained why


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("substance_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
