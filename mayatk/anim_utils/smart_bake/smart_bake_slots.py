# !/usr/bin/python
# coding=utf-8
"""Slots for the Smart Bake tool panel (smart_bake.ui)."""
from typing import List, Optional

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt
from uitk.switchboard.slots import Cancelable

from mayatk.anim_utils.smart_bake._smart_bake import SmartBake


class SmartBakeSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Controller wiring smart_bake.ui to the SmartBake engine.

    Composition, not inheritance (mirrors HdrManagerSlots/LightmapBakerSlots):
    SmartBake is instantiated fresh per bake from the panel's current option
    state, not held as a persistent collaborator.
    """

    # Combo index -> SmartBake(backup_file=...) value. Index is the source of
    # truth so reordering the combo labels can't silently remap the value.
    _BACKUP_MODES = (("Auto", None), ("Always", True), ("Never", False))

    def __init__(self, switchboard, log_level: str = "INFO"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.smart_bake

        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        # setup_logging_redirect() defaults its *own* level to INFO, which
        # would silently override whatever log_level the caller just set —
        # pass the resolved level through explicitly so log_level actually
        # governs verbosity.
        self.logger.setup_logging_redirect(self.ui.txt000, level=self.logger.level)
        self.logger.hide_logger_name(True)
        self.logger.log_timestamp = "%H:%M:%S"

        # Deferred: child widgets (footer, checkboxes, combos) aren't wired
        # onto self.ui until register_children runs after __init__ returns.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        """Wire cross-widget behavior and sync the Unbake button to scene state.

        Deferred from __init__ (see there) so the full UI is registered
        before any ``self.ui.<widget>`` access.
        """
        self.sb.add_reset_buttons(self.ui)
        self.ui.chk_override_layer.toggled.connect(self._on_override_layer_toggled)
        self._on_override_layer_toggled(self.ui.chk_override_layer.isChecked())
        self._log_getting_started()
        self._refresh_session_state()

    def _log_getting_started(self) -> None:
        """Print a one-time orientation block to the output panel."""
        self.logger.log_box(
            "Smart Bake",
            [
                "1. Pick Scope — Auto (whole scene, default) or Selected.",
                "2. Adjust the options above, then click Bake.",
                "3. Click Unbake anytime — even after saving and",
                "   reopening the scene — to reverse the last bake.",
            ],
        )

    def _log_run_header(self, title: str) -> None:
        """Blank line + colored section title + divider, opening a new
        bake/unbake report so consecutive runs stay visually distinct."""
        self.logger.log_raw("")
        self.logger.notice(title)
        self.logger.log_divider()

    def _warn(self, msg: str) -> None:
        """Write *msg* to the footer (transient) and the output panel
        (persistent) — every warning in this panel needs both."""
        self.ui.footer.setText(msg, level="warning")
        self.logger.warning(msg)

    def _succeed(
        self,
        msg: str,
        details: Optional[List[str]] = None,
        item_color: Optional[str] = None,
    ) -> None:
        """Write *msg* to the footer (transient) and the output panel
        (persistent). With *details*, renders a colored group; otherwise a
        plain success line."""
        self.ui.footer.setText(msg, level="success")
        if details:
            kwargs = {"item_color": item_color} if item_color else {}
            self.logger.log_group(msg, details, level="SUCCESS", **kwargs)
        else:
            self.logger.success(msg)

    def cmb_scope_init(self, widget) -> None:
        # Auto is index 0 (the default): analyze() + bake() already restrict
        # a whole-scene scope to driven objects, so Auto costs nothing extra
        # over "All Transforms & Joints" — it just names what already happens.
        widget.add(["Auto (Whole Scene)", "Selected"])

    def cmb_backup_init(self, widget) -> None:
        widget.add([label for label, _ in self._BACKUP_MODES])

    def header_init(self, widget) -> None:
        """Configure header menu, refresh button, and help text."""
        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self._refresh_session_state)
        widget.menu.add(
            "QPushButton",
            setText="Reset to Defaults",
            setObjectName="reset_defaults",
            setToolTip="Reset every field in this panel to its default value.",
        )
        widget.set_help_text(
            fmt(
                title="Smart Bake",
                body="Analyzes the scene for constraints, driven keys, expressions, "
                "IK, motion paths, and blend shapes, then bakes only the channels "
                "that are actually driven — already-keyed channels are untouched.",
                steps=[
                    "Pick <b>Scope</b> — <b>Auto</b> (default) scans the whole "
                    "scene and bakes only what's actually driven; "
                    "<b>Selected</b> restricts the scan to your selection.",
                    "Adjust the options below and click <b>Bake</b>.",
                    "Click <b>Unbake</b> at any time — even after saving and "
                    "reopening the scene — to reverse the most recent bake.",
                ],
                sections=[
                    (
                        "Safety",
                        [
                            "<b>Use Override Layer</b> (default on) bakes onto a "
                            "separate animation layer; the original rig stays "
                            "connected underneath — fully reversible.",
                            "<b>Delete Inputs</b> permanently removes driver "
                            "nodes. Not reversible by Unbake; a scene backup is "
                            "saved automatically unless Backup is set to Never.",
                        ],
                    )
                ],
            )
        )

    def _on_override_layer_toggled(self, checked: bool) -> None:
        # delete_inputs is a base-layer-only behavior — ignored (and visibly
        # disabled) whenever the nondestructive override layer is active.
        self.ui.chk_delete_inputs.setDisabled(checked)

    def reset_defaults(self) -> None:
        """Header menu: reset every field in this panel to its registry default."""
        self.ui.state.reset_all()

    def _scope_objects(self) -> Optional[List[str]]:
        """Selected scope -> the selection (possibly empty); Auto -> None
        (SmartBake then scans every transform + joint and bakes only the
        ones analyze() finds actually driven)."""
        if self.ui.cmb_scope.currentIndex() == 1:  # Selected
            return cmds.ls(selection=True, long=True)
        return None

    def _backup_value(self):
        return self._BACKUP_MODES[self.ui.cmb_backup.currentIndex()][1]

    @Cancelable(180)
    def b000(self, widget) -> None:
        """Bake."""
        objects = self._scope_objects()
        if objects == []:
            # Selected scope with nothing selected must NOT silently
            # escalate to a whole-scene bake (objects=None would).
            self._warn(
                "Nothing selected — select objects, or set Scope to "
                "Auto (Whole Scene)."
            )
            return

        self._log_run_header("Bake")

        baker = SmartBake(
            objects=objects,
            sample_by=self.ui.spn_sample_by.value(),
            preserve_outside_keys=self.ui.chk_preserve_outside.isChecked(),
            delete_inputs=self.ui.chk_delete_inputs.isChecked(),
            optimize_keys=self.ui.chk_optimize.isChecked(),
            bake_blend_shapes=self.ui.chk_bake_blendshapes.isChecked(),
            bake_inherited_visibility=self.ui.chk_inherited_vis.isChecked(),
            use_override_layer=self.ui.chk_override_layer.isChecked(),
            mute_drivers=self.ui.chk_mute_drivers.isChecked(),
            backup_file=self._backup_value(),
        )

        # All footer messaging happens AFTER the progress context: its
        # __exit__ synchronously writes "Complete" to the status label, so
        # text set inside the block would be clobbered on exit.
        result = None
        with self.ui.footer.progress(text="Analyzing scene…") as update:
            self.logger.info("Analyzing scene…")
            analysis = baker.analyze()
            if any(a.requires_bake for a in analysis.values()):
                update(None, "Baking…")
                self.logger.info("Baking…")
                result = baker.bake(analysis)

        if result is None:
            self._warn(
                "Nothing to bake — no constraints, driven keys, expressions, "
                "IK, or motion paths detected."
            )
        else:
            self._report_bake_result(result)
        self._refresh_session_state()

    def _report_bake_result(self, result) -> None:
        if not result.success:
            self._warn("Bake produced no output.")
            return

        summary = (
            f"Baked {result.baked_count} object(s), "
            f"range {result.time_range[0]}-{result.time_range[1]}."
        )

        details = [f"{obj}: {', '.join(chans)}" for obj, chans in result.baked.items()]
        if result.skipped:
            details.append(f"Skipped {len(result.skipped)} object(s).")
        if result.override_layer:
            details.append(f"Override layer: {result.override_layer}")
        if result.muted_drivers:
            details.append(f"Muted {len(result.muted_drivers)} driver(s).")
        if result.deleted:
            details.append(f"Deleted {len(result.deleted)} driver node(s).")
        if result.backup_path:
            details.append(f"Backup saved: {result.backup_path}")
        if result.session_id:
            details.append(
                f"Restorable — session '{result.session_id}'. Click Unbake to reverse."
            )
        else:
            details.append("Not restorable (delete_inputs without an override layer).")

        self._succeed(summary, details)

    @Cancelable(60)
    def b001(self, widget) -> None:
        """Unbake."""
        self._log_run_header("Unbake")
        restore = SmartBake.restore()
        if not restore.success:
            self._warn(restore.warnings[0] if restore.warnings else "Nothing to restore.")
        else:
            summary = f"Restored session '{restore.session_id}'."
            self._succeed(
                summary, restore.warnings, item_color=self.LOG_COLORS["WARNING"]
            )
        self._refresh_session_state()

    def _refresh_session_state(self) -> None:
        # Non-restorable (delete_inputs) sessions stay clickable on purpose:
        # the click reports the backup path and pops the dead entry so any
        # older restorable session becomes reachable on the next click.
        pending = SmartBake.list_sessions()
        self.ui.b001.setEnabled(bool(pending))
        self.ui.b001.setToolTip(
            f"Restore the most recent of {len(pending)} pending bake(s)."
            if pending
            else "No bakes pending restore."
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("smart_bake", reload=True)
    ui.show(pos="screen", app_exec=True)
