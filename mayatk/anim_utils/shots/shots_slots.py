# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shots settings UI.

Provides a single source of truth for shot-level settings
(detection threshold, use-selected-keys) that both the
Shot Manifest and Shot Sequencer consume via :class:`ShotStore`.
"""
import pythontk as ptk

from mayatk.anim_utils.shots._shots import (
    StoreEvent,
    BatchComplete,
    ActiveShotChanged,
    ShotUpdated,
    ShotRemoved,
)


class ShotsController(ptk.LoggingMixin):
    """Business logic for the Shots settings panel."""

    def __init__(self, slots_instance, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self._store_listener_bound = False
        self._refreshing_editor = False

        # Allow the checkbox signal to fire during uitk state restore
        # so that dependent enable/disable states are synced correctly.
        chk = getattr(self.ui, "chk_selected_keys", None)
        if chk is not None:
            chk.block_signals_on_restore = False

        # Shot editor widgets get their values from the store, not QSettings.
        for name in (
            "cmb_shot_select",
            "txt_shot_name",
            "spn_shot_start",
            "spn_shot_end",
            "txt_shot_desc",
        ):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.restore_state = False

        self._sync_from_store()
        self._bind_store_listener()
        self._setup_delete_menu()

        spn_dur = getattr(self.ui, "spn_default_duration", None)
        if spn_dur is not None:
            spn_dur.setCustomDisplayValues({0: "Auto"})

        # Remove the collapse button from the header and enable
        # hide-on-mouse-leave so the window behaves like a quick-access panel.
        # WindowStaysOnTopHint prevents the panel from falling behind the
        # sequencer when focus shifts back to it.
        header = getattr(self.ui, "header", None)
        if header is not None and hasattr(header, "config_buttons"):
            header.config_buttons("menu", "minimize", "pin")

        self._setup_hide_on_leave()

    # ---- hide on mouse leave ---------------------------------------------

    def _setup_hide_on_leave(self) -> None:
        """Install a polling timer that hides the window when the cursor leaves."""
        from qtpy import QtCore, QtGui, QtWidgets

        self._leave_timer = QtCore.QTimer(self.ui)
        self._leave_timer.setInterval(100)
        self._leave_timer.timeout.connect(self._check_cursor_outside)
        self._mouse_entered = False

        # Patch the window's showEvent to start tracking
        orig_show = self.ui.showEvent

        def _on_show(event, _orig=orig_show):
            _orig(event)
            self._mouse_entered = False
            self._leave_timer.start()

        self.ui.showEvent = _on_show

    def _check_cursor_outside(self) -> None:
        """Hide the window if the cursor has left and it isn't pinned."""
        from qtpy import QtGui, QtWidgets

        if not self.ui.isVisible():
            self._leave_timer.stop()
            return

        if getattr(self.ui, "is_pinned", False):
            return

        cursor_pos = self.ui.mapFromGlobal(QtGui.QCursor.pos())
        inside = self.ui.rect().contains(cursor_pos)

        if not inside:
            widget_at = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
            if widget_at and self.ui.isAncestorOf(widget_at):
                inside = True

        if inside:
            self._mouse_entered = True
        elif self._mouse_entered:
            self.ui._auto_hiding = True
            self.ui.hide()
            self.ui._auto_hiding = False
            self._leave_timer.stop()

    # ---- store access ----------------------------------------------------

    def _active_store(self):
        """Return the active ShotStore, or ``None``."""
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            return ShotStore.active()
        except Exception:
            return None

    def _bind_store_listener(self) -> None:
        """Listen for external store mutations to keep widgets in sync."""
        if self._store_listener_bound:
            return
        store = self._active_store()
        if store is not None:
            store.add_listener(self._on_store_event)
            self._store_listener_bound = True

    def _on_store_event(self, event: StoreEvent) -> None:
        """Re-sync widgets when the store changes externally."""
        if isinstance(event, BatchComplete):
            self._sync_from_store()
        elif isinstance(event, (ActiveShotChanged, ShotUpdated)):
            self._sync_shot_editor()
            self._sync_footer()
        elif isinstance(event, ShotRemoved):
            self._populate_shot_combobox()
            self._sync_footer()

    # ---- index ↔ mode mapping ---------------------------------------------

    _FILTER_MODES = ("all", "skip_zero", "zero_as_end")

    def _mode_to_index(self, mode: str) -> int:
        try:
            return self._FILTER_MODES.index(mode)
        except ValueError:
            return 0

    def _index_to_mode(self, index: int) -> str:
        if 0 <= index < len(self._FILTER_MODES):
            return self._FILTER_MODES[index]
        return "all"

    # ---- footer ----------------------------------------------------------

    def _set_footer(self, text: str) -> None:
        """Write *text* into the window footer."""
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return
        label = footer._status_label
        label.setStyleSheet("background: transparent; border: none;")
        footer.setText(text)

    def _sync_footer(self, store=None) -> None:
        """Update footer with aggregate shot statistics."""
        if store is None:
            store = self._active_store()
        if store is None or not store.shots:
            self._set_footer("")
            return

        shots = store.sorted_shots()
        n = len(shots)
        objs = {o for s in shots for o in s.objects}
        total_dur = sum(s.duration for s in shots)
        first = shots[0].start
        last = shots[-1].end

        sep = " \u00b7 "
        parts = [
            f"{n} shot{'s' if n != 1 else ''}",
            f"{total_dur:.0f}f",
            f"{len(objs)} object{'s' if len(objs) != 1 else ''}",
            f"[{first:.0f}\u2013{last:.0f}]",
        ]
        self._set_footer(sep.join(parts))

    # ---- sync ------------------------------------------------------------

    def _sync_from_store(self) -> None:
        """Pull current values from ShotStore into UI widgets."""
        store = self._active_store()
        if store is None:
            return

        spn_det = getattr(self.ui, "spn_detection", None)
        if spn_det is not None:
            spn_det.blockSignals(True)
            spn_det.setValue(store.detection_threshold)
            spn_det.blockSignals(False)

        chk = getattr(self.ui, "chk_selected_keys", None)
        if chk is not None:
            chk.blockSignals(True)
            chk.setChecked(store.use_selected_keys)
            chk.blockSignals(False)

        cmb = getattr(self.ui, "cmb_key_filter", None)
        if cmb is not None:
            cmb.blockSignals(True)
            cmb.setCurrentIndex(self._mode_to_index(store.key_filter_mode))
            cmb.blockSignals(False)

        # Disable detection spinner when selected-keys mode is active
        # (auto-detection threshold is irrelevant in that mode).
        # Enable key-filter combobox only in selected-keys mode.
        sel_active = chk.isChecked() if chk is not None else False
        if spn_det is not None:
            spn_det.setEnabled(not sel_active)
        if cmb is not None:
            cmb.setEnabled(sel_active)

        # ---- Editing group ----
        spn_gap = getattr(self.ui, "spn_gap", None)
        if spn_gap is not None:
            spn_gap.blockSignals(True)
            spn_gap.setValue(int(store.gap))
            spn_gap.blockSignals(False)

        spn_dur = getattr(self.ui, "spn_default_duration", None)
        if spn_dur is not None:
            spn_dur.blockSignals(True)
            spn_dur.setValue(store.default_duration)
            spn_dur.blockSignals(False)
            # Disabled when shots already have explicit ranges
            has_shots = bool(store.shots)
            spn_dur.setEnabled(not has_shots)

        self._populate_shot_combobox(store)
        self._sync_footer(store)

    # ---- shot editor sync ------------------------------------------------

    def _populate_shot_combobox(self, store=None) -> None:
        """Rebuild the shot selector combobox from the store."""
        if store is None:
            store = self._active_store()

        cmb = getattr(self.ui, "cmb_shot_select", None)
        if cmb is None:
            return

        cmb.blockSignals(True)
        cmb.clear()
        if store is not None:
            for shot in store.shots:
                cmb.addItem(
                    f"{shot.name}  [{shot.start:.0f}\u2013{shot.end:.0f}]",
                    shot.shot_id,
                )
            # Select the active shot, or auto-select the first one
            active_id = store.active_shot_id
            if active_id is not None:
                for i in range(cmb.count()):
                    if cmb.itemData(i) == active_id:
                        cmb.setCurrentIndex(i)
                        break
            elif cmb.count() > 0:
                cmb.setCurrentIndex(0)
                first_id = cmb.itemData(0)
                if first_id is not None:
                    store.set_active_shot(first_id)
        cmb.blockSignals(False)
        self._sync_shot_editor(store)

    def _sync_shot_editor(self, store=None) -> None:
        """Load the active shot's fields into the editor widgets."""
        if store is None:
            store = self._active_store()

        self._refreshing_editor = True
        try:
            txt_name = getattr(self.ui, "txt_shot_name", None)
            spn_start = getattr(self.ui, "spn_shot_start", None)
            spn_end = getattr(self.ui, "spn_shot_end", None)
            txt_desc = getattr(self.ui, "txt_shot_desc", None)
            btn_del = getattr(self.ui, "b000", None)
            cmb = getattr(self.ui, "cmb_shot_select", None)
            chk_scale = getattr(self.ui, "chk_scale_keys", None)

            shot = None
            if store is not None and store.active_shot_id is not None:
                shot = store.shot_by_id(store.active_shot_id)

            has_shot = shot is not None
            has_any_shots = cmb is not None and cmb.count() > 0
            if cmb is not None:
                cmb.setEnabled(has_any_shots)
            for w in (txt_name, spn_start, spn_end, txt_desc, chk_scale):
                if w is not None:
                    w.setEnabled(has_shot)
            if btn_del is not None:
                btn_del.setEnabled(has_shot)

            if shot is None:
                if txt_name is not None:
                    txt_name.setText("")
                if spn_start is not None:
                    spn_start.setValue(0)
                if spn_end is not None:
                    spn_end.setValue(0)
                if txt_desc is not None:
                    txt_desc.setText("")
                return

            if txt_name is not None and txt_name.text() != shot.name:
                txt_name.blockSignals(True)
                txt_name.setText(shot.name)
                txt_name.blockSignals(False)
            if spn_start is not None:
                spn_start.blockSignals(True)
                spn_start.setValue(shot.start)
                spn_start.blockSignals(False)
            if spn_end is not None:
                spn_end.blockSignals(True)
                spn_end.setValue(shot.end)
                spn_end.blockSignals(False)
            if txt_desc is not None and txt_desc.text() != shot.description:
                txt_desc.blockSignals(True)
                txt_desc.setText(shot.description)
                txt_desc.blockSignals(False)

            # Keep combobox display text in sync after edits
            if cmb is not None:
                idx = cmb.currentIndex()
                if idx >= 0:
                    cmb.blockSignals(True)
                    cmb.setItemText(
                        idx,
                        f"{shot.name}  [{shot.start:.0f}\u2013{shot.end:.0f}]",
                    )
                    cmb.blockSignals(False)
        finally:
            self._refreshing_editor = False

    # ---- widget → store pushes -------------------------------------------

    @staticmethod
    def _has_selected_keys() -> bool:
        """True if any keyframes are selected in the Graph Editor."""
        try:
            import maya.cmds as cmds

            return bool(cmds.keyframe(query=True, selected=True, name=True))
        except Exception:
            return False

    def _save(self) -> None:
        """Persist settings to the scene."""
        store = self._active_store()
        if store is not None:
            store.save()

    def on_detection_changed(self, value: float) -> None:
        store = self._active_store()
        if store is not None:
            store.detection_threshold = float(value)
            store.save()
            store.notify_settings_changed()

    def on_selected_keys_changed(self, checked: bool) -> None:
        store = self._active_store()
        if store is not None:
            store.use_selected_keys = checked
            store.save()
            store.notify_settings_changed()
        # Disable detection spinner in selected-keys mode.
        # Enable key-filter combobox only in selected-keys mode.
        spn = getattr(self.ui, "spn_detection", None)
        if spn is not None:
            spn.setEnabled(not checked)
        cmb = getattr(self.ui, "cmb_key_filter", None)
        if cmb is not None:
            cmb.setEnabled(checked)

    def on_key_filter_changed(self, index: int) -> None:
        store = self._active_store()
        if store is not None:
            store.key_filter_mode = self._index_to_mode(index)
            store.save()
            store.notify_settings_changed()

    def on_gap_changed(self, value: int) -> None:
        store = self._active_store()
        if store is not None:
            store.gap = float(value)
            store.save()

            from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
                ShotSequencer,
            )
            import pymel.core as pm

            seq = ShotSequencer(store=store)
            with pm.UndoChunk():
                seq.respace(gap=float(value))
            store.notify_settings_changed()

    def on_default_duration_changed(self, value: float) -> None:
        store = self._active_store()
        if store is not None:
            store.default_duration = float(value)
            store.save()

    # ---- shot editor actions ---------------------------------------------

    def on_shot_selected(self, index: int) -> None:
        """User picked a different shot from the combobox."""
        cmb = getattr(self.ui, "cmb_shot_select", None)
        if cmb is None:
            return
        shot_id = cmb.itemData(index)
        store = self._active_store()
        if store is not None and shot_id is not None:
            store.set_active_shot(shot_id)

    def _push_shot_field(self, **kwargs) -> None:
        """Push one or more field changes to the active shot."""
        if self._refreshing_editor:
            return
        store = self._active_store()
        if store is None or store.active_shot_id is None:
            return
        store.update_shot(store.active_shot_id, **kwargs)

    def on_shot_name_changed(self, text: str) -> None:
        self._push_shot_field(name=text)

    def on_shot_start_changed(self, value: float) -> None:
        if self._should_scale_keys():
            self._resize_shot_keys(start=value)
        else:
            self._push_shot_field(start=value)

    def on_shot_end_changed(self, value: float) -> None:
        if self._should_scale_keys():
            self._resize_shot_keys(end=value)
        else:
            self._push_shot_field(end=value)

    def on_shot_desc_changed(self, text: str) -> None:
        self._push_shot_field(description=text)

    def _should_scale_keys(self) -> bool:
        """Return True if the Scale Keys checkbox is checked."""
        chk = getattr(self.ui, "chk_scale_keys", None)
        return chk is not None and chk.isChecked()

    def _resize_shot_keys(self, start: float = None, end: float = None) -> None:
        """Scale keys to fit modified shot range, then update boundaries."""
        if self._refreshing_editor:
            return
        store = self._active_store()
        if store is None or store.active_shot_id is None:
            return
        shot = store.shot_by_id(store.active_shot_id)
        if shot is None:
            return

        old_start, old_end = shot.start, shot.end
        new_start = start if start is not None else old_start
        new_end = end if end is not None else old_end

        if abs(new_start - old_start) < 1e-6 and abs(new_end - old_end) < 1e-6:
            return

        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )
        import pymel.core as pm

        seq = ShotSequencer(store=store)
        with pm.UndoChunk():
            for obj in shot.objects:
                seq.scale_object_keys(obj, old_start, old_end, new_start, new_end)
        store.update_shot(shot.shot_id, start=new_start, end=new_end)

    def _setup_delete_menu(self) -> None:
        """Attach an option box menu to the delete button."""
        btn = getattr(self.ui, "b000", None)
        if btn is None:
            return
        menu = btn.option_box.menu
        menu.add(
            "QPushButton",
            setText="Delete All Shots",
            setObjectName="btn_delete_all_shots",
            setToolTip="Remove every shot from the store.",
        )

    def on_delete_shot(self) -> None:
        """Delete the active shot after confirmation."""
        from qtpy import QtWidgets

        store = self._active_store()
        if store is None or store.active_shot_id is None:
            return
        shot = store.shot_by_id(store.active_shot_id)
        if shot is None:
            return

        reply = QtWidgets.QMessageBox.question(
            self.ui,
            "Delete Shot",
            f'Delete "{shot.name}" [{shot.start:.0f}\u2013{shot.end:.0f}]?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        store.remove_shot(shot.shot_id)
        store.set_active_shot(None)

    def on_delete_all_shots(self) -> None:
        """Delete every shot after confirmation."""
        from qtpy import QtWidgets

        store = self._active_store()
        if store is None or not store.shots:
            return

        count = len(store.shots)
        reply = QtWidgets.QMessageBox.question(
            self.ui,
            "Delete All Shots",
            f"Delete all {count} shot(s)?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        for shot in list(store.shots):
            store.remove_shot(shot.shot_id)
        store.set_active_shot(None)


class ShotsSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shots

        self.controller = ShotsController(self)

    # ---- widget slots (objectName → method) ------------------------------

    def spn_detection(self, value):
        """Detection threshold changed."""
        self.controller.on_detection_changed(value)

    def chk_selected_keys(self, state):
        """Use-selected-keys checkbox toggled."""
        self.controller.on_selected_keys_changed(bool(state))

    def cmb_key_filter(self, index):
        """Key-filter mode combobox changed."""
        self.controller.on_key_filter_changed(index)

    def spn_gap(self, value):
        """Global gap spinner changed."""
        self.controller.on_gap_changed(value)

    def spn_default_duration(self, value):
        """Default duration spinner changed."""
        self.controller.on_default_duration_changed(value)

    # ---- shot editor slots -----------------------------------------------

    def cmb_shot_select(self, index):
        """Shot selector combobox changed."""
        self.controller.on_shot_selected(index)

    def txt_shot_name(self, text=None):
        """Shot name edited."""
        widget = getattr(self.ui, "txt_shot_name", None)
        if widget is not None:
            self.controller.on_shot_name_changed(widget.text())

    def spn_shot_start(self, value):
        """Shot start frame changed."""
        self.controller.on_shot_start_changed(value)

    def spn_shot_end(self, value):
        """Shot end frame changed."""
        self.controller.on_shot_end_changed(value)

    def txt_shot_desc(self, text=None):
        """Shot description edited."""
        widget = getattr(self.ui, "txt_shot_desc", None)
        if widget is not None:
            self.controller.on_shot_desc_changed(widget.text())

    def b000(self):
        """Delete the selected shot."""
        self.controller.on_delete_shot()

    def btn_delete_all_shots(self):
        """Delete all shots."""
        self.controller.on_delete_all_shots()
