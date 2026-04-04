# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shots settings UI.

Provides a single source of truth for shot-level settings
(detection threshold, use-selected-keys) that both the
Shot Manifest and Shot Sequencer consume via :class:`ShotStore`.
"""
import pythontk as ptk

from mayatk.anim_utils.shots._shots import (
    ShotStore,
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

        # Shot editor widgets get their values from the store, not QSettings.
        for name in (
            "cmb_shot_select",
            "txt_shot_name",
            "spn_shot_start",
            "spn_shot_end",
            "txt_shot_desc",
            "spn_move_to",
        ):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.restore_state = False

        # Debounce value-change signals so rapid spinner clicks / text
        # edits coalesce into a single store update.
        for name in ("spn_shot_start", "spn_shot_end", "txt_shot_name", "txt_shot_desc"):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.debounce = 400

        # Disable keyboard tracking on frame spinners so valueChanged only
        # fires on commit (Enter / focus-loss / arrow-click), not on every
        # keystroke.  Without this, clearing the text to retype a value
        # emits valueChanged(0) mid-edit, which triggers ripple_shift with
        # a bogus delta and corrupts all downstream shot ranges.
        for name in ("spn_shot_start", "spn_shot_end"):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.setKeyboardTracking(False)

        self._sync_from_store()
        self._bind_store_listener()
        self._setup_delete_menu()
        self._setup_move_menu()

        # Register persistent scene-change scriptJobs so the UI refreshes
        # when the user opens or creates a scene while this panel is visible.
        self._scene_opened_job: int | None = None
        self._new_scene_job: int | None = None
        self._install_scene_jobs()

        # Enable hide-on-mouse-leave so the window behaves like a quick-access panel.
        # WindowStaysOnTopHint prevents the panel from falling behind the
        # sequencer when focus shifts back to it.
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

    def _unbind_store_listener(self) -> None:
        """Detach from the current store so we can rebind after scene change."""
        if not self._store_listener_bound:
            return
        store = ShotStore._active
        if store is not None:
            store.remove_listener(self._on_store_event)
        self._store_listener_bound = False

    def remove_callbacks(self) -> None:
        """Remove scene-change scriptJobs and store listener (call on teardown)."""
        self._unbind_store_listener()
        try:
            import maya.cmds as cmds

            if self._scene_opened_job is not None:
                try:
                    cmds.scriptJob(kill=self._scene_opened_job, force=True)
                except Exception:
                    pass
                self._scene_opened_job = None
            if self._new_scene_job is not None:
                try:
                    cmds.scriptJob(kill=self._new_scene_job, force=True)
                except Exception:
                    pass
                self._new_scene_job = None
        except ImportError:
            pass

    def _on_scene_opened(self) -> None:
        """Re-sync the UI after a scene switch.

        Explicitly invalidates ``ShotStore._active`` so that
        ``_active_store()`` loads the new scene's data, regardless of
        whether the persistence-layer scriptJob has already fired.
        """
        self._unbind_store_listener()
        ShotStore._active = None
        self._sync_from_store()
        self._bind_store_listener()

    def _install_scene_jobs(self) -> None:
        """Register persistent scriptJobs for scene lifecycle events."""
        try:
            import maya.cmds as cmds
        except ImportError:
            return

        try:
            if self._scene_opened_job is None or not cmds.scriptJob(
                exists=self._scene_opened_job
            ):
                self._scene_opened_job = cmds.scriptJob(
                    event=["SceneOpened", self._on_scene_opened],
                )
        except Exception:
            pass

        try:
            if self._new_scene_job is None or not cmds.scriptJob(
                exists=self._new_scene_job
            ):
                self._new_scene_job = cmds.scriptJob(
                    event=["NewSceneOpened", self._on_scene_opened],
                )
        except Exception:
            pass

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

    _DETECTION_MODES = ShotStore.DETECTION_MODES

    def _mode_to_index(self, mode: str) -> int:
        try:
            return self._DETECTION_MODES.index(mode)
        except ValueError:
            return 0

    def _index_to_mode(self, index: int) -> str:
        if 0 <= index < len(self._DETECTION_MODES):
            return self._DETECTION_MODES[index]
        return "auto"

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

        cmb_mode = getattr(self.ui, "cmb_detection_mode", None)
        if cmb_mode is not None:
            cmb_mode.blockSignals(True)
            cmb_mode.setCurrentIndex(self._mode_to_index(store.detection_mode))
            cmb_mode.blockSignals(False)

        # Disable Min Gap spinner in zero_as_end mode (unused there).
        mode = store.detection_mode
        if spn_det is not None:
            spn_det.setEnabled(mode != "zero_as_end")

        # ---- Editing group ----
        has_shots = bool(store.shots)

        spn_gap = getattr(self.ui, "spn_gap", None)
        if spn_gap is not None:
            spn_gap.blockSignals(True)
            spn_gap.setValue(int(store.gap))
            spn_gap.blockSignals(False)
            spn_gap.setEnabled(has_shots)

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
            for shot in store.sorted_shots():
                cmb.addItem(
                    f"{shot.name}  [{shot.start:.0f}\u2013{shot.end:.0f}]",
                    shot.shot_id,
                )
            # Select the active shot, or auto-select the first one
            active_id = store.active_shot_id
            matched = False
            if active_id is not None:
                for i in range(cmb.count()):
                    if cmb.itemData(i) == active_id:
                        cmb.setCurrentIndex(i)
                        matched = True
                        break
            if not matched and cmb.count() > 0:
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
            shot = None
            if store is not None and store.active_shot_id is not None:
                shot = store.shot_by_id(store.active_shot_id)

            spn_move = getattr(self.ui, "spn_move_to", None)

            has_shot = shot is not None
            has_any_shots = cmb is not None and cmb.count() > 0
            if cmb is not None:
                cmb.setEnabled(has_any_shots)
            for w in (txt_name, spn_start, spn_end, txt_desc):
                if w is not None:
                    w.setEnabled(has_shot)
            if btn_del is not None:
                btn_del.setEnabled(has_shot)
            if spn_move is not None:
                spn_move.setEnabled(has_shot and has_any_shots)

            if shot is None:
                if txt_name is not None:
                    txt_name.blockSignals(True)
                    txt_name.setText("")
                    txt_name.blockSignals(False)
                if spn_start is not None:
                    spn_start.blockSignals(True)
                    spn_start.setValue(0)
                    spn_start.blockSignals(False)
                if spn_end is not None:
                    spn_end.blockSignals(True)
                    spn_end.setValue(0)
                    spn_end.blockSignals(False)
                if txt_desc is not None:
                    txt_desc.blockSignals(True)
                    txt_desc.setText("")
                    txt_desc.blockSignals(False)
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

            # Sync move-to spinbox range and current position
            if spn_move is not None and store is not None:
                sorted_ = store.sorted_shots()
                n = len(sorted_)
                spn_move.blockSignals(True)
                spn_move.setMaximum(max(n, 1))
                if shot is not None:
                    pos = next(
                        (
                            i + 1
                            for i, s in enumerate(sorted_)
                            if s.shot_id == shot.shot_id
                        ),
                        1,
                    )
                    spn_move.setValue(pos)
                spn_move.blockSignals(False)
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
            store.mark_dirty()
            store.notify_settings_changed()

    def on_detection_mode_changed(self, index: int) -> None:
        store = self._active_store()
        mode = self._index_to_mode(index)
        if store is not None:
            store.detection_mode = mode
            store.mark_dirty()
            store.notify_settings_changed()
        # Disable Min Gap spinner in zero_as_end mode (unused there).
        spn = getattr(self.ui, "spn_detection", None)
        if spn is not None:
            spn.setEnabled(mode != "zero_as_end")

    def on_gap_changed(self, value: int) -> None:
        store = self._active_store()
        if store is not None:
            store.gap = float(value)
            store.mark_dirty()

            from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
                ShotSequencer,
            )
            import pymel.core as pm

            seq = ShotSequencer(store=store)
            with pm.UndoChunk():
                seq.respace(gap=float(value))
            store.notify_settings_changed()

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
        self._push_shot_field(start=value)

    def on_shot_end_changed(self, value: float) -> None:
        if self._refreshing_editor:
            return
        store = self._active_store()
        if store is None or store.active_shot_id is None:
            return
        shot = store.shot_by_id(store.active_shot_id)
        if shot is None:
            return
        old_end = shot.end
        delta = value - old_end
        with store.batch_update():
            store.update_shot(store.active_shot_id, end=value)
            store.ripple_shift(old_end, delta, exclude_id=shot.shot_id)

    def on_shot_desc_changed(self, text: str) -> None:
        self._push_shot_field(description=text)

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

    def _setup_move_menu(self) -> None:
        """Attach an option box action to the move-to spinbox."""
        spn = getattr(self.ui, "spn_move_to", None)
        if spn is None:
            return
        menu = spn.option_box.menu
        menu.add(
            "QPushButton",
            setText="Move Shot",
            setObjectName="btn_move_shot",
            setToolTip="Move the selected shot to the specified position.",
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

    def on_move_shot(self) -> None:
        """Move the active shot to the position specified by spn_move_to."""
        store = self._active_store()
        if store is None or store.active_shot_id is None:
            return

        spn = getattr(self.ui, "spn_move_to", None)
        if spn is None:
            return

        target_pos = int(spn.value())

        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )
        import pymel.core as pm

        seq = ShotSequencer(store=store)
        with pm.UndoChunk():
            seq.move_shot_to_position(store.active_shot_id, target_pos)

        self._populate_shot_combobox(store)
        store.notify_settings_changed()


class ShotsSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shots

        self.controller = ShotsController(self)

    # ---- header ----------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu."""
        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Shots \u2014 Detection settings, shot properties, and gap\n"
                "control for the Shot Manifest and Shot Sequencer.\n\n"
                "Quick Start:\n"
                "  1. Choose a Detection Mode and set Min Gap.\n"
                "  2. Open the Shot Manifest or Shot Sequencer to trigger\n"
                "     detection (settings here are shared by both tools).\n"
                "  3. Edit individual shot properties in the Shot Editor\n"
                "     section below, or adjust the Gap spinner to respace\n"
                "     all shots at once.\n\n"
                "Detection:\n"
                "  \u2022 Auto-Detect \u2014 Scans all scene animation and groups\n"
                "    contiguous segments separated by gaps larger than\n"
                "    the Min Gap threshold.\n"
                "  \u2022 All Keys \u2014 Each selected keyframe becomes a shot\n"
                "    boundary. Select keys in the Graph Editor first.\n"
                "  \u2022 Skip Zero-Value \u2014 Like All Keys but ignores any\n"
                "    keys with a value of 0.\n"
                "  \u2022 Zero = Shot End \u2014 Non-zero keys start shots,\n"
                "    zero-value keys end them (Min Gap is disabled).\n\n"
                "  Min Gap: Minimum frame gap that separates segments into\n"
                "  distinct shots, or merges nearby keys into one boundary.\n\n"
                "Gap (Editing):\n"
                "  Sets the frame gap between consecutive shots. Adjusting\n"
                "  this spinner immediately respaces all shots and moves\n"
                "  their keyframes. The operation is undoable (Ctrl+Z).\n\n"
                "Shot Editor:\n"
                "  Select a shot from the dropdown to view or edit:\n"
                "  \u2022 Name \u2014 Human-readable shot label.\n"
                "  \u2022 Start / End \u2014 Frame range (syncs with Sequencer).\n"
                "  \u2022 Description \u2014 Free-text notes.\n"
                "  \u2022 Move To \u2014 Set position and click option box \u25b8 to reorder.\n"
                "  \u2022 Delete \u2014 Remove shot (option box \u25b8 for Delete All).\n\n"
                "Footer: Shot count, total duration, object count, and\n"
                "overall frame range at a glance."
            ),
        )

    # ---- widget slots (objectName \u2192 method) ------------------------------

    def spn_detection(self, value):
        """Detection threshold changed."""
        self.controller.on_detection_changed(value)

    def cmb_detection_mode(self, index):
        """Detection mode combobox changed."""
        self.controller.on_detection_mode_changed(index)

    def spn_gap(self, value):
        """Global gap spinner changed."""
        self.controller.on_gap_changed(value)

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

    def btn_move_shot(self):
        """Move shot to the position in spn_move_to."""
        self.controller.on_move_shot()
