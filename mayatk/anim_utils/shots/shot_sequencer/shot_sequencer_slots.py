# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shot Sequencer UI.

Provides ``ShotSequencerSlots`` — bridges the generic
:class:`~uitk.widgets.sequencer._sequencer.SequencerWidget` to the
Maya-specific :class:`~mayatk.anim_utils.shots.shot_sequencer._shot_sequencer.ShotSequencer`.
"""
from collections import defaultdict
from typing import Optional, List

from qtpy import QtWidgets, QtCore, QtGui

try:
    import pymel.core as pm
    import maya.api.OpenMaya as om2
    import maya.api.OpenMayaAnim as oma
except ImportError:
    pm = None
    om2 = None
    oma = None

import pythontk as ptk

from uitk.widgets.sequencer._sequencer import (
    SequencerWidget,
    AttributeColorDialog,
    _COMMON_ATTRIBUTES,
    _DEFAULT_ATTRIBUTE_COLORS,
)
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
    ShotSequencer,
    ShotBlock,
)
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.audio_utils.segments import collect_all_segments
from mayatk.anim_utils.shots.shot_sequencer.gap_manager import GapManagerMixin
from mayatk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin
from mayatk.anim_utils.shots.shot_sequencer.segment_collector import (
    collect_segments,
    active_object_set,
    extract_attributes,
    build_curve_preview,
)
from mayatk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin
from mayatk.anim_utils.shots.shot_sequencer.marker_manager import MarkerManagerMixin
from mayatk.anim_utils.shots._shots import StoreEvent
from mayatk.node_utils.attributes._attributes import Attributes


class ShotSequencerController(
    GapManagerMixin,
    ClipMotionMixin,
    ShotNavMixin,
    MarkerManagerMixin,
    ptk.LoggingMixin,
):
    """Business logic controller bridging SequencerWidget ↔ ShotSequencer."""

    def __init__(self, slots_instance, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self._sequencer: Optional[ShotSequencer] = None
        self._undo_callback_ids: List[int] = []
        self._time_change_cb: Optional[int] = None
        self._keyframe_cb: Optional[int] = None
        self._keyframe_debounce: Optional[QtCore.QTimer] = None
        self._syncing = False
        self._syncing_playhead = False
        self._store_listener_bound = False
        self._shot_display_mode: str = "current"  # "current" | "adjacent" | "all"
        self._segment_cache: dict = {}  # shot_id → segments list
        self._sub_row_cache: dict = {}  # (shot_id, track_name) → sub-row data
        self._color_map_cache: Optional[dict] = None  # persisted attribute color map
        self._audio_segments_cache: Optional[tuple] = None  # (range_key, segments)
        self._last_visible_key: Optional[tuple] = None  # fast-path gating key
        self._reconcile_needed: bool = True  # gated by DAG/store events
        self._shot_undo_stack: list = []  # shot-state snapshots for undo
        self._shifted_out_keys: dict = {}  # obj_name → {time, …} shift-moved out
        self._prev_action = None  # OptionBox action for prev shot
        self._next_action = None  # OptionBox action for next shot
        self._view_mode_action = None  # OptionBox action for view mode cycle
        self._cmb_mode_widget = None  # mode selector combobox (Shots/Markers)
        self._holds_action = None  # OptionBox action for internal holds toggle
        self._playback_range_mode: str = (
            "follows_view"  # "off" | "follows_view" | "locked"
        )
        self._track_order_scope: str = "visible"  # "visible" | "global"
        self._show_internal_holds: bool = (
            False  # show flat-key spans in attribute sub-rows
        )
        self._cmb_mode: str = "shots"  # "shots" or "markers"

        self._register_maya_undo_callbacks()
        self._register_time_change_callback()
        self._register_keyframe_callback()
        self._bind_store_listener()
        self.logger.debug("ShotSequencerController initialized.")

    # ---- footer helpers --------------------------------------------------

    def _set_footer(self, text: str, *, color: str = "") -> None:
        """Set the window footer text with an optional foreground color."""
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return
        label = footer._status_label
        if color:
            label.setStyleSheet(
                f"background: transparent; border: none; color: {color};"
            )
        else:
            label.setStyleSheet("background: transparent; border: none;")
        footer.setText(text)

    def _update_footer_shot_summary(self) -> None:
        """Update the footer with a summary of the active shot."""
        if self.sequencer is None:
            self._set_footer("No shots defined.")
            return
        shot_id = self.active_shot_id
        if shot_id is None:
            self._set_footer("No shot selected.")
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            self._set_footer("No shot selected.")
            return
        dur = int(shot.end - shot.start)
        n_obj = len(shot.objects)
        n_shots = len(self.sequencer.shots)
        idx = next(
            (
                i
                for i, s in enumerate(self.sequencer.sorted_shots())
                if s.shot_id == shot_id
            ),
            0,
        )
        sep = " \u00b7 "
        parts = [
            f"[{idx + 1}/{n_shots}]",
            f"{dur}f",
            f"{n_obj} object{'s' if n_obj != 1 else ''}",
        ]
        self._set_footer(sep.join(parts))

    # ---- sequencer property (lazy init from ShotStore) -------------------

    @property
    def sequencer(self) -> Optional[ShotSequencer]:
        """Return the ShotSequencer, lazily creating one from the active store."""
        if self._sequencer is None:
            from mayatk.anim_utils.shots._shots import ShotStore

            store = ShotStore.active()
            self._sequencer = ShotSequencer(store=store)
            self.logger.debug("Lazy-initialized ShotSequencer from ShotStore.active().")
        return self._sequencer

    @sequencer.setter
    def sequencer(self, value: Optional[ShotSequencer]) -> None:
        self._sequencer = value

    # ---- ShotStore observer ----------------------------------------------

    def _bind_store_listener(self) -> None:
        """Register as a listener on the active ShotStore."""
        if self._store_listener_bound:
            return
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            store = ShotStore.active()
            store.add_listener(self._on_store_event)
            self._bound_store = store
            self._store_listener_bound = True
        except Exception:
            pass

    def _unbind_store_listener(self) -> None:
        """Remove the ShotStore listener."""
        if not self._store_listener_bound:
            return
        try:
            store = getattr(self, "_bound_store", None)
            if store is not None:
                store.remove_listener(self._on_store_event)
                self._bound_store = None
        except Exception:
            pass
        self._store_listener_bound = False

    def _on_store_event(self, event: StoreEvent) -> None:
        """React to ShotStore mutations from any source (e.g. manifest build)."""
        if self._syncing or self.sequencer is None:
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        # Refresh combobox and widget when shots change externally
        self._sync_combobox()
        self._sync_to_widget()
        # Emit widget-level signals for any external consumers
        widget = self._get_sequencer_widget()
        if widget is not None and hasattr(widget, "shots_changed"):
            widget.shots_changed.emit()
            widget.app_event.emit(event.name, event)

    # ---- Maya undo/redo event callbacks ----------------------------------

    def _register_maya_undo_callbacks(self) -> None:
        """Listen for Maya Undo/Redo events to refresh the widget.

        Registered through ``ScriptJobManager`` so all callbacks tear down
        through a single ``unsubscribe_all(owner=self)`` path.
        """
        if om2 is None or self._undo_callback_ids:
            return
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        for event_name in ("Undo", "Redo"):
            token = mgr.add_om_callback(
                om2.MEventMessage.addEventCallback,
                event_name,
                self._on_maya_undo,
                owner=self,
            )
            if token is not None:
                self._undo_callback_ids.append(token)

    def remove_callbacks(self) -> None:
        """Remove Maya event callbacks and ShotStore listener (call on teardown).

        All OpenMaya callbacks registered by this controller live under the
        SJM owner ``self``, so a single ``unsubscribe_all`` removes them.
        """
        self._unbind_store_listener()
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        ScriptJobManager.instance().unsubscribe_all(self)
        self._undo_callback_ids.clear()
        self._time_change_cb = None
        self._keyframe_cb = None
        if self._keyframe_debounce is not None:
            self._keyframe_debounce.stop()
            self._keyframe_debounce = None

    def _on_maya_undo(self, *_args) -> None:
        """Refresh the widget when Maya's undo/redo fires."""
        if self._syncing:
            return
        self._restore_shot_state()
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    # ---- Maya keyframe-edited callback ------------------------------------

    def _register_keyframe_callback(self) -> None:
        """Listen for keyframe edits so new keys appear in the sequencer.

        Uses ``MAnimMessage.addAnimKeyframeEditedCallback`` which fires
        once per anim-curve change.  A debounce timer coalesces rapid
        bursts (e.g. keying 10 attributes at once) into a single refresh.
        """
        if oma is None or self._keyframe_cb is not None:
            return
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        self._keyframe_cb = ScriptJobManager.instance().add_om_callback(
            oma.MAnimMessage.addAnimKeyframeEditedCallback,
            self._on_keyframe_edited,
            owner=self,
        )

    def _on_keyframe_edited(self, *_args) -> None:
        """Schedule a debounced refresh when keyframes change.

        Skipped during playback (keys aren't meaningfully added during
        playback) and when the controller is already syncing.
        """
        if self._syncing:
            return
        # Skip during playback — avoid stalling the viewport
        try:
            import maya.cmds as _cmds

            if _cmds.play(q=True, state=True):
                return
        except Exception:
            pass
        if self._keyframe_debounce is None:
            self._keyframe_debounce = QtCore.QTimer()
            self._keyframe_debounce.setSingleShot(True)
            self._keyframe_debounce.setInterval(200)
            self._keyframe_debounce.timeout.connect(self._on_keyframe_debounce_fire)
        self._keyframe_debounce.start()

    def _on_keyframe_debounce_fire(self) -> None:
        """Perform the actual refresh after the debounce window.

        Only evicts the active shot from the segment cache so that
        non-active shots (adjacent/all view modes) keep their cached
        segments.  The active shot is always re-queried by
        ``collect_segments`` anyway.

        If the keyframe was set on an object not yet in the active shot,
        the object is auto-added to the shot's object list.
        """
        if self._syncing:
            return
        # Only invalidate the active shot — collect_segments always
        # re-queries it, and non-active shots don't need re-collection.
        active_id = self.active_shot_id
        # Audio keys can be edited (e.g. dragging an audio clip) — the
        # cached segments must drop so the next rebuild re-discovers.
        # A keyframe edit can also reveal newly-keyed objects, requiring
        # DAG-path reconciliation on the next rebuild.
        self._audio_segments_cache = None
        self._reconcile_needed = True
        if active_id is not None:
            self._segment_cache.pop(active_id, None)
            self._sub_row_cache = {
                k: v for k, v in self._sub_row_cache.items() if k[0] != active_id
            }
            added = self._auto_add_keyed_objects(active_id)
        else:
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            added = False
        if not added:
            self._sync_to_widget()

    def _auto_add_keyed_objects(self, shot_id: int) -> bool:
        """Add newly-keyed transforms to the active shot's object list.

        Checks the currently selected transforms for animation in the
        shot's time range and merges any missing ones into
        ``shot.objects``.  Returns ``True`` if objects were added
        (triggering a store event and widget sync), ``False`` otherwise.
        """
        import maya.cmds as cmds

        if self.sequencer is None:
            return False
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return False
        selected = cmds.ls(sl=True, long=True, type="transform") or []
        if not selected:
            return False
        existing = set(shot.objects)
        candidates = [s for s in selected if s not in existing]
        if not candidates:
            return False
        # Check each candidate for non-flat animation in the shot range.
        # Query curves connected to the candidate directly rather than
        # scanning all scene curves.
        from mayatk.anim_utils._anim_utils import STANDARD_TRANSFORM_ATTRS

        new_objects = []
        for obj in candidates:
            curves = cmds.listConnections(obj, type="animCurve", s=True, d=False) or []
            for crv in curves:
                plugs = cmds.listConnections(crv, d=True, s=False, plugs=True) or []
                attr = ""
                for p in plugs:
                    attr = p.rsplit(".", 1)[-1] if "." in p else ""
                    break
                if attr not in STANDARD_TRANSFORM_ATTRS:
                    continue
                vals = cmds.keyframe(
                    crv, q=True, time=(shot.start, shot.end), valueChange=True
                )
                if vals and (max(vals) - min(vals)) > 1e-4:
                    new_objects.append(obj)
                    break
        if not new_objects:
            return False
        merged = sorted(existing | set(new_objects))
        self.sequencer.store.update_shot(shot_id, objects=merged)
        return True

    # ---- Maya time-change callback ----------------------------------------

    def _register_time_change_callback(self) -> None:
        """Register an om2 DG time-change callback for reliable playhead sync.

        Unlike scriptJob(event='timeChanged'), MDGMessage.addTimeChangeCallback
        fires on every DG time change including during playback in all
        evaluation modes (DG, Serial, Parallel).
        """
        if om2 is None or self._time_change_cb is not None:
            return
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        self._time_change_cb = ScriptJobManager.instance().add_om_callback(
            om2.MDGMessage.addTimeChangeCallback,
            self._on_time_changed,
            owner=self,
        )

    def _on_time_changed(self, time_msg, _client_data=None) -> None:
        """Update the sequencer playhead when Maya's time changes.

        Parameters
        ----------
        time_msg : om2.MTime
            The new DG time supplied by the callback.
        """
        if self._syncing_playhead:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        widget.set_playhead(time_msg.value)

    # -- zone context menus ------------------------------------------------

    def on_zone_context_menu(self, zone: str, time: float, global_pos) -> None:
        """Build a context menu specific to the clicked zone."""
        if zone == "shot_lane":
            self._show_shot_lane_context_menu(time, global_pos)
            return
        # ruler and tracks share the widget's built-in menu
        widget = self._get_sequencer_widget()
        if widget is not None:
            widget._timeline._show_default_context_menu(widget, time, global_pos)

    def _show_shot_lane_context_menu(self, time: float, global_pos) -> None:
        """Context menu for the shots track: selection, editing, creation."""
        from qtpy import QtWidgets

        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return

        clicked_shot = self._find_shot_at_time(time)

        menu = QtWidgets.QMenu(widget)
        menu.setStyleSheet(
            "QMenu { background:#333; color:#CCC; }"
            "QMenu::item:selected { background:#555; }"
        )

        if clicked_shot is not None:
            act_select = menu.addAction(f'Select "{clicked_shot.name}"')
            act_edit = menu.addAction(f'Edit "{clicked_shot.name}"\u2026')
            menu.addSeparator()
            act_trim = menu.addAction("Trim Empty Space")
            menu.addSeparator()
        else:
            act_select = None
            act_edit = None
            act_trim = None

        act_new = menu.addAction("New Shot")
        menu.addSeparator()
        act_refresh = menu.addAction("Refresh")

        chosen = menu.exec_(global_pos)
        if chosen is None:
            return
        if chosen == act_select and clicked_shot is not None:
            self.on_shot_block_clicked(clicked_shot.name)
        elif chosen == act_edit and clicked_shot is not None:
            self._edit_shot_dialog(clicked_shot)
        elif chosen == act_trim and clicked_shot is not None:
            self._trim_shot(clicked_shot.shot_id)
        elif chosen == act_new:
            self._create_shot_one_click()
        elif chosen == act_refresh:
            self.refresh()

    def _trim_shot(self, shot_id: int) -> None:
        """Trim empty space from *shot_id*, undoable, then refresh the widget."""
        if self.sequencer is None or pm is None:
            return
        self._save_shot_state()
        with pm.UndoChunk():
            self.sequencer.trim_shot_to_content(shot_id)
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def _create_shot_one_click(self) -> None:
        """Append a new shot using the configured gap and default duration."""
        if self.sequencer is None:
            return
        store = self.sequencer.store
        gap = store.gap or 0
        existing = self.sequencer.sorted_shots()
        existing_names = {s.name for s in existing}
        idx = len(existing) + 1
        while f"Shot {idx}" in existing_names:
            idx += 1
        name = f"Shot {idx}"
        from mayatk.anim_utils.shots.shot_manifest.behaviors import compute_duration

        duration = compute_duration([], fallback=100.0)
        shot = store.append_shot(name=name, duration=duration, gap=gap)
        self._sync_combobox()
        # Select the new shot in the combobox
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is not None:
            for i in range(cmb.count()):
                if cmb.itemData(i) == shot.shot_id:
                    cmb.setCurrentIndex(i)
                    break
        self.select_shot(shot.shot_id)
        self._sync_to_widget()
        self._set_footer(
            f"Created {shot.name} \u00b7 {shot.start:.0f}\u2013{shot.end:.0f}"
        )

    def _find_shot_at_time(self, time: float):
        """Return the shot whose range contains *time*, or ``None``."""
        if self.sequencer is None:
            return None
        for s in self.sequencer.sorted_shots():
            if s.start <= time <= s.end:
                return s
        return None

    def _on_shot_lane_double_clicked(self, time: float) -> None:
        """Double-click on the shot lane opens the edit dialog for the shot at *time*."""
        shot = self._find_shot_at_time(time)
        if shot is not None:
            self._edit_shot_dialog(shot)

    def _on_shot_switch_requested(self, time: float) -> None:
        """Ctrl+Shift+Click on timeline — switch to the shot at *time*."""
        shot = self._find_shot_at_time(time)
        if shot is not None:
            self.on_shot_block_clicked(shot.name)

    def _edit_shot_dialog(self, shot) -> None:
        """Open Shot Settings with the given shot pre-selected for editing."""
        self.sequencer.store.set_active_shot(shot.shot_id)
        self.sb.handlers.marking_menu.show("shots")

    def _set_view_mode(self, mode: str) -> None:
        """Set the shot display mode and rebuild the widget."""
        self._shot_display_mode = mode
        if self._playback_range_mode != "off":
            self._apply_view_playback_range()
        self._sync_to_widget()

    def _set_playback_range_mode(self, mode: str) -> None:
        """Set the playback-range tracking mode.

        *mode* must be one of ``"off"``, ``"follows_view"``, or
        ``"locked"``.
        """
        self._playback_range_mode = mode
        if mode != "off":
            self._apply_view_playback_range()

    def _set_cmb_mode(self, mode: str) -> None:
        """Switch the combobox between shots and scene markers."""
        self._cmb_mode = mode
        # Keep the mode selector in sync (guard against re-entry)
        cmb_mode = self._cmb_mode_widget
        if cmb_mode is not None:
            idx = 1 if mode == "markers" else 0
            if cmb_mode.currentIndex() != idx:
                cmb_mode.blockSignals(True)
                cmb_mode.setCurrentIndex(idx)
                cmb_mode.blockSignals(False)
        self._sync_combobox()

    # ---- widget ↔ engine sync -------------------------------------------

    @property
    def active_shot_id(self) -> Optional[int]:
        """Return the shot_id currently selected, or the first shot's id."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is not None and cmb.currentIndex() >= 0:
            sid = cmb.itemData(cmb.currentIndex())
            if sid is not None:
                return sid
        if self.sequencer and self.sequencer.shots:
            return self.sequencer.sorted_shots()[0].shot_id
        return None

    def _save_shot_state(self) -> None:
        """Push a snapshot of all shot boundaries onto the undo stack."""
        if self.sequencer is None:
            return
        state = [
            (s.shot_id, s.start, s.end, list(s.objects)) for s in self.sequencer.shots
        ]
        self._shot_undo_stack.append(state)
        if len(self._shot_undo_stack) > 50:
            self._shot_undo_stack.pop(0)

    def _restore_shot_state(self) -> None:
        """Pop the last shot-boundary snapshot and restore it."""
        if not self._shot_undo_stack or self.sequencer is None:
            return
        state = self._shot_undo_stack.pop()
        store = self.sequencer.store
        with store.batch_update():
            for shot_id, start, end, objects in state:
                store.update_shot(shot_id, start=start, end=end, objects=objects)

    def on_undo(self) -> None:
        """Handle undo_requested from the widget — delegate to Maya undo."""
        if pm is None:
            return
        self._syncing = True
        try:
            try:
                self._restore_shot_state()
            except Exception:
                self.logger.debug("on_undo: _restore_shot_state failed", exc_info=True)
            pm.undo()
        except RuntimeError:
            pass
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def on_redo(self) -> None:
        """Handle redo_requested from the widget — delegate to Maya redo."""
        if pm is None:
            return
        self._syncing = True
        try:
            pm.redo()
        except RuntimeError:
            pass
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    # -- item menu extensibility hooks -------------------------------------

    def on_clip_menu(self, menu, clip_id: int) -> None:
        """Add domain-specific actions to a clip's context menu.

        Called before ``menu.exec_`` so consumers can append actions.
        Override or extend in subclasses for custom clip menu items.

        When multiple clips are selected the actions operate on all of
        them.  The *clip_id* parameter identifies the right-clicked clip
        for actions that need a single target (e.g. "Lock Others").
        """
        if pm is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        clip = widget.get_clip(clip_id)
        if clip is None:
            return

        obj_name = clip.data.get("obj")

        # Gather all selected clip IDs for batch operations.
        selected_ids = widget.selected_clips() or [clip_id]
        if clip_id not in selected_ids:
            selected_ids = [clip_id]
        multi = len(selected_ids) > 1

        menu.addSeparator()
        label = f"Delete Keys ({len(selected_ids)})" if multi else "Delete Key"
        act_delete = menu.addAction(label)
        act_delete.triggered.connect(lambda: self._delete_clip_keys(selected_ids))

        # Per-object lock helpers
        if obj_name and self.sequencer:
            menu.addSeparator()
            act_lock_others = menu.addAction("Lock Others")
            act_unlock_all = menu.addAction("Unlock All")
            act_lock_others.triggered.connect(
                lambda: self._lock_others(widget, obj_name)
            )
            act_unlock_all.triggered.connect(lambda: self._unlock_all(widget))

        # "Move to Shot" submenu — anim/audio clips moved as sequences.
        if self.sequencer:
            seqs = self._clips_to_sequences(widget, selected_ids)
            shots = self.sequencer.sorted_shots()
            if seqs and len(shots) > 1:
                menu.addSeparator()
                move_label = (
                    f"Move to Shot ({len(seqs)})" if multi else "Move to Shot"
                )
                move_menu = menu.addMenu(move_label)
                # Exclude shots whose id matches every sequence's source.
                source_ids = {self.sequencer._source_shot_id_for(s) for s in seqs}
                for sh in shots:
                    if len(source_ids) == 1 and sh.shot_id in source_ids:
                        continue  # all sequences already live here
                    act = move_menu.addAction(
                        f'{sh.name}  [{sh.start:.0f}\u2013{sh.end:.0f}]'
                    )
                    act.triggered.connect(
                        lambda _checked=False, sid=sh.shot_id: (
                            self._move_clips_to_shot(seqs, sid)
                        )
                    )

    def _clips_to_sequences(self, widget, clip_ids):
        """Convert widget clip ids to unified sequence dicts.

        Stepped (zero-duration) anim clips are skipped — they are individual
        keys, not sequences, and can't meaningfully move between shots.
        Read-only clips (non-active visible shots) are skipped too.
        """
        seqs = []
        seen: set = set()
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None or clip.data.get("read_only"):
                continue
            if clip.data.get("is_stepped"):
                continue
            start = clip.data.get("orig_start")
            end = clip.data.get("orig_end")
            if start is None or end is None or end <= start:
                continue
            if clip.data.get("is_audio"):
                obj = clip.data.get("audio_track_id")
                kind = "audio"
            else:
                obj = clip.data.get("obj")
                kind = "anim"
            if not obj:
                continue
            # Dedupe: the same underlying segment can produce multiple
            # clips when it spans multiple visible shots (esp. audio).
            key = (kind, obj, round(start, 6), round(end, 6))
            if key in seen:
                continue
            seen.add(key)
            seqs.append({"kind": kind, "obj": obj, "start": start, "end": end})
        return seqs

    def _move_clips_to_shot(self, sequences, dest_shot_id):
        """Run move_sequences_to_shot, undoable, then refresh."""
        if self.sequencer is None or pm is None or not sequences:
            return
        self._save_shot_state()
        with pm.UndoChunk():
            self.sequencer.move_sequences_to_shot(sequences, dest_shot_id)
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    # -- lock helpers -------------------------------------------------------

    def _lock_others(self, widget, keep_obj: str) -> None:
        """Lock every main-row object clip except *keep_obj*."""
        store = self.sequencer.store if self.sequencer else None
        if store is None:
            return
        # Collect all unique main-row object names in the active shot
        obj_names: set = set()
        for cd in widget._clips.values():
            o = cd.data.get("obj")
            if o and not cd.sub_row and not cd.data.get("read_only"):
                obj_names.add(o)
        for o in obj_names:
            if o == keep_obj:
                store.locked_objects.discard(o)
            else:
                store.locked_objects.add(o)
        # Apply to all clips
        for cid, cd in list(widget._clips.items()):
            o = cd.data.get("obj")
            if o and not cd.data.get("read_only"):
                widget.set_clip_locked(cid, o != keep_obj)
        self._sub_row_cache.clear()

    def _unlock_all(self, widget) -> None:
        """Unlock every clip in the current view."""
        store = self.sequencer.store if self.sequencer else None
        if store is not None:
            store.locked_objects.clear()
        for cid, cd in list(widget._clips.items()):
            if cd.locked and not cd.data.get("read_only"):
                widget.set_clip_locked(cid, False)
        self._sub_row_cache.clear()

    def on_gap_menu(self, menu, gap_start: float, gap_end: float) -> None:
        """Add domain-specific actions to a gap overlay's context menu.

        Called before ``menu.exec_`` so consumers can append actions.
        Override or extend in subclasses for custom gap menu items.
        """

    _node_icons_cls_cache = ...  # sentinel — not yet resolved

    @classmethod
    def _try_load_maya_icons(cls):
        """Return the :class:`NodeIcons` class if Maya is available, else ``None``.

        Resolved once per process; the result (including the ``None``
        no-Maya case) is memoised on the class so every rebuild pays a
        single attribute read instead of an import + try/except.
        """
        if cls._node_icons_cls_cache is not ...:
            return cls._node_icons_cls_cache
        try:
            from mayatk.ui_utils.node_icons import NodeIcons
            import maya.cmds  # noqa: F401 — availability check
            cls._node_icons_cls_cache = NodeIcons
        except ImportError:
            cls._node_icons_cls_cache = None
        return cls._node_icons_cls_cache

    def _visible_shots(self, active_shot):
        """Return the shots to render based on ``_shot_display_mode``."""
        if self._shot_display_mode == "current":
            return [active_shot]
        sorted_shots = self.sequencer.sorted_shots()
        if self._shot_display_mode == "all":
            return sorted_shots
        # "adjacent" — previous + current + next
        idx = next(
            (i for i, s in enumerate(sorted_shots) if s.shot_id == active_shot.shot_id),
            None,
        )
        if idx is None:
            return [active_shot]
        result = []
        if idx > 0:
            result.append(sorted_shots[idx - 1])
        result.append(active_shot)
        if idx < len(sorted_shots) - 1:
            result.append(sorted_shots[idx + 1])
        return result

    def _sync_to_widget(
        self, shot_id: Optional[int] = None, *, frame: bool = False
    ) -> None:
        """Full rebuild: content + decoration + viewport.

        When the display mode is ``"adjacent"`` or ``"all"``, clips from
        non-active shots are also rendered (greyed-out, locked) and their
        ranges are shown as non-interactive overlays.

        Parameters:
            shot_id: Shot to display.  Falls back to :attr:`active_shot_id`.
            frame: If True, reframe the viewport on the active shot.
        """
        widget, shot = self._resolve_sync_target(shot_id)
        if widget is None or shot is None:
            # No shots — try scene-wide display
            widget = self._get_sequencer_widget()
            if (
                widget is not None
                and self.sequencer is not None
                and not self.sequencer.shots
            ):
                self._sync_shotless(widget, frame=frame)
            return

        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        visible_shots = self._visible_shots(shot)

        self._rebuild_content(widget, shot, visible_shots)
        self._rebuild_decoration(widget, shot, visible_shots)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        self._update_footer_shot_summary()

    def _sync_shotless(self, widget, *, frame: bool = False) -> None:
        """Populate the widget with scene-wide animation when no shots exist.

        Discovers animated transforms across the full playback range and
        displays them as tracks/clips so the user can inspect animation
        before defining any shots.
        """
        if pm is None:
            return
        import maya.cmds as cmds

        start = cmds.playbackOptions(q=True, min=True)
        end = cmds.playbackOptions(q=True, max=True)

        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        widget.clear()
        self._sync_header_settings(widget)

        if end <= start:
            self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
            self._set_footer("No valid playback range.")
            return

        discovered = self.sequencer._find_keyed_transforms(start, end)
        if not discovered:
            self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
            self._set_footer("No animated objects in scene.")
            return

        scene_shot = ShotBlock(
            shot_id=-1,
            name="Scene",
            start=start,
            end=end,
            objects=sorted(set(discovered)),
        )

        from mayatk.anim_utils.segment_keys import SegmentKeys

        valid = cmds.ls(scene_shot.objects, long=True) or []
        segments = SegmentKeys.collect_segments(
            valid,
            split_static=True,
            time_range=(start, end),
            ignore_holds=True,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=1e-3,
        )
        for seg in segments:
            seg["obj"] = str(seg["obj"])

        segments_by_shot = {scene_shot.shot_id: segments}
        all_objects = set(scene_shot.objects) | {seg["obj"] for seg in segments}

        track_ids = self._build_tracks(
            widget, all_objects, all_objects, active_shot=scene_shot
        )
        self._build_clips(widget, scene_shot, [scene_shot], segments_by_shot, track_ids)
        self._ensure_scene_attr_colors(widget)
        self._build_audio_tracks(widget, scene_shot, [scene_shot])

        current_time = cmds.currentTime(q=True)
        widget.set_playhead(current_time)
        widget.set_active_range(start, end)

        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        n = len(scene_shot.objects)
        self._set_footer(
            f"Scene  {start:.0f}\u2013{end:.0f}  \u00b7  "
            f"{n} object{'s' if n != 1 else ''}"
        )

    def _sync_decoration(self, *, frame: bool = False) -> None:
        """Lightweight refresh: rebuild overlays/metadata without re-querying
        Maya for animation data.  Tracks and clips are preserved."""
        widget, shot = self._resolve_sync_target()
        if widget is None or shot is None:
            return

        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        visible_shots = self._visible_shots(shot)

        widget.clear_decorations(keep_range_highlight=True)
        self._rebuild_decoration(widget, shot, visible_shots)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)

    def refresh(self) -> None:
        """Clear cached segments and rebuild the sequencer widget."""
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        self._sync_to_widget()

    # ---- _sync_to_widget helpers -----------------------------------------

    def _resolve_sync_target(self, shot_id=None):
        """Return ``(widget, shot)`` or ``(None, None)`` if unavailable."""
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return None, None

        if shot_id is None:
            shot_id = self.active_shot_id
        if shot_id is None:
            return None, None

        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return None, None
        return widget, shot

    def _save_viewport_state(self, widget):
        """Capture scroll, zoom, and expanded tracks for later restoration."""
        h_scroll = widget._timeline.horizontalScrollBar().value()
        zoom = widget._timeline.pixels_per_unit
        expanded_names = set()
        for tid in list(widget._expanded_tracks):
            td = widget.get_track(tid)
            if td is not None:
                expanded_names.add(td.name)
        return h_scroll, zoom, expanded_names

    def _rebuild_content(self, widget, shot, visible_shots) -> None:
        """Clear widget and rebuild tracks + clips from segments (expensive)."""
        # Suppress store-event → _sync_to_widget re-entrancy for the
        # entire rebuild.  Both reconciliation and auto-discovery may
        # call store.update_shot(); without this guard each call would
        # trigger a nested _sync_to_widget mid-build → duplicate tracks.
        self._syncing = True
        try:
            widget.clear(keep_range_highlight=True)
            self._sub_row_cache.clear()
            self._sync_header_settings(widget)

            # Re-resolve any stale DAG paths (e.g. parent renamed) across
            # ALL shots before collecting segments so that global track sets
            # and segment caches never mix old and new paths.  Gated by a
            # dirty flag so pure shot-switches (which can't rename nodes)
            # don't pay the cmds.ls cost on every rebuild.
            if self._reconcile_needed:
                if self.sequencer.reconcile_all_shots():
                    self._segment_cache.clear()
                self._reconcile_needed = False

            segments_by_shot, all_objects = collect_segments(
                self.sequencer,
                shot,
                visible_shots,
                self._segment_cache,
                self._shifted_out_keys,
                self.logger,
            )

            # When "global" scope is active, expand the object set to include
            # every object across all shots so track positions never shift.
            if self._track_order_scope == "global":
                for s in self.sequencer.sorted_shots():
                    all_objects.update(s.objects)

            active_objects = active_object_set(shot, segments_by_shot)
            track_ids = self._build_tracks(
                widget, all_objects, active_objects, active_shot=shot
            )
            self._build_clips(widget, shot, visible_shots, segments_by_shot, track_ids)
            self._ensure_scene_attr_colors(widget)
            self._build_audio_tracks(widget, shot, visible_shots)
        finally:
            self._syncing = False

    def _rebuild_decoration(self, widget, shot, visible_shots) -> None:
        """Recreate overlays, markers, gap indicators, and active-shot tint."""
        try:
            import maya.cmds as _cmds

            current_time = _cmds.currentTime(q=True)
        except ImportError:
            current_time = shot.start
        widget.set_playhead(current_time)
        widget.set_hidden_tracks(sorted(self.sequencer.hidden_objects))
        widget.set_active_range(shot.start, shot.end)
        widget.set_range_highlight(shot.start, shot.end)

        # Populate the shot lane with all shots so the user always sees
        # the full shot structure (including gaps) regardless of display mode.
        all_sorted = self.sequencer.sorted_shots()
        store = self.sequencer.store
        shot_blocks = [
            {
                "name": s.name,
                "start": s.start,
                "end": s.end,
                "active": s.shot_id == shot.shot_id,
            }
            for s in all_sorted
        ]
        widget.set_shot_blocks(shot_blocks)

        for m in self.sequencer.markers:
            widget.add_marker(
                time=m["time"],
                note=m.get("note", ""),
                color=m.get("color"),
                draggable=m.get("draggable", True),
                style=m.get("style", "triangle"),
                line_style=m.get("line_style", "dashed"),
                opacity=m.get("opacity", 1.0),
            )

        # Gap overlays between ALL consecutive shots — they serve as
        # interactive handles the user can drag even when gap is zero.
        gap_count = 0
        for i in range(len(all_sorted) - 1):
            left = all_sorted[i]
            right = all_sorted[i + 1]
            gap_start = left.end
            gap_end = right.start
            gap_size = gap_end - gap_start
            if gap_size > -0.5:
                locked = store.is_gap_locked(left.shot_id, right.shot_id)
                widget.add_gap_overlay(gap_start, gap_end, locked=locked)
                gap_count += 1
        self.logger.debug(
            "Gap overlays: %d created across %d shots", gap_count, len(all_sorted)
        )

        # Gray tint over inactive shot regions so the active shot
        # stands out visually against the rest of the timeline.
        for s in all_sorted:
            if s.shot_id != shot.shot_id:
                widget.add_range_overlay(s.start, s.end, color="#000000", alpha=40)

    def _restore_viewport(self, widget, frame, h_scroll, zoom, expanded_names) -> None:
        """Restore scroll/zoom/expansion and trigger geometry recalculation."""
        if frame:
            widget._timeline._refresh_all()
            widget.frame_shot()
        else:
            widget._timeline._pixels_per_unit = zoom
            widget._timeline._refresh_all()
            widget._timeline.horizontalScrollBar().setValue(h_scroll)

        widget.sub_row_provider = self._provide_sub_rows

        if expanded_names:
            for td in widget.tracks():
                if td.name in expanded_names:
                    widget.expand_track(td.track_id)

    def _sync_header_settings(self, widget) -> None:
        """Push header spinbox values and attribute colors to the widget."""
        spn_snap = getattr(self.ui, "spn_snap", None)
        if spn_snap is not None:
            widget.snap_interval = float(spn_snap.value())
        spn_gap = getattr(self.ui, "spn_gap", None)
        if spn_gap is not None:
            stored_gap = self.sequencer.store.gap if self.sequencer else 0
            spn_gap.blockSignals(True)
            spn_gap.setValue(int(stored_gap))
            spn_gap.blockSignals(False)

        # QSettings.allKeys() is a disk-backed scan (~4ms each) — cache
        # the resolved color map and only rebuild when the color dialog
        # publishes a new one via btn_colors.
        if self._color_map_cache is None:
            from uitk.widgets.mixins.settings_manager import SettingsManager

            color_settings = SettingsManager(namespace=AttributeColorDialog._SETTINGS_NS)
            color_map = dict(_DEFAULT_ATTRIBUTE_COLORS)
            for key in color_settings.keys():
                val = color_settings.value(key)
                if val:
                    color_map[key] = val
            self._color_map_cache = color_map
        widget.attribute_colors = self._color_map_cache

    # Palette for auto-assigning colors to scene-specific attributes
    # not present in the user's color map (e.g. custom/plugin attrs).
    _AUTO_PALETTE = [
        "#5B8BD4",
        "#6EBF6E",
        "#D4A65B",
        "#C45C5C",
        "#8E6FBF",
        "#5BBFB4",
        "#BF6E8E",
        "#8EB05B",
    ]

    def _ensure_scene_attr_colors(self, widget) -> None:
        """Auto-assign colors to scene attributes missing from the color map.

        Scans all clips for attribute names not yet in
        ``widget.attribute_colors`` and assigns each a deterministic
        color from ``_AUTO_PALETTE`` (hash-based so the same attribute
        always gets the same color).  The widget's live color map is
        updated in-place so that both ``ClipItem._resolve_color`` and
        ``_provide_sub_rows`` see the assignments.
        """
        if widget is None:
            return
        color_map = widget.attribute_colors
        changed = False
        from hashlib import md5

        for clip in widget._clips.values():
            for attr in clip.data.get("attributes", []):
                if attr not in color_map:
                    # Deterministic hash — same attribute always maps to
                    # the same palette slot (built-in hash() is randomized).
                    idx = int(md5(attr.encode()).hexdigest(), 16) % len(
                        self._AUTO_PALETTE
                    )
                    color_map[attr] = self._AUTO_PALETTE[idx]
                    changed = True
        if changed:
            widget.attribute_colors = color_map

    def _build_tracks(
        self, widget, all_objects, active_objects, active_shot=None
    ) -> dict:
        """Create one track per unique object and return ``{obj_name: track_id}``.

        Non-pinned objects that no longer exist in the scene are silently
        skipped.  Pinned objects (e.g. from a manifest) are kept with a
        'missing' icon so users can see them and re-import.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.shots._shots import SHOT_PALETTE

        node_icons_cls = self._try_load_maya_icons()
        obj_classes = active_shot.classify_objects() if active_shot else {}
        track_ids: dict = {}
        _NOT_FOUND_COLOR = "#E0A0A0"
        if self._track_order_scope == "global":
            ordered = sorted(all_objects)
        else:
            sorted_active = sorted(o for o in all_objects if o in active_objects)
            sorted_inactive = sorted(o for o in all_objects if o not in active_objects)
            ordered = sorted_active + sorted_inactive

        # Batch existence check: one `cmds.ls` round-trip instead of N
        # `cmds.objExists` calls.  Scenes with many tracks hit this on
        # every rebuild.
        existing_set = set(cmds.ls(ordered, long=True) or []) if ordered else set()

        for obj_name in ordered:
            if self.sequencer.is_object_hidden(obj_name):
                continue
            exists = obj_name in existing_set
            # Skip missing objects unless they are pinned
            if not exists and not self.sequencer.store.is_object_pinned(obj_name):
                continue
            in_active = obj_name in active_objects
            icon = node_icons_cls.get_icon(obj_name) if node_icons_cls else None
            if not exists and icon is None:
                from uitk.widgets.mixins.icon_manager import IconManager

                icon = IconManager.get("close", size=(16, 16), color=_NOT_FOUND_COLOR)
            color_kw: dict = {}
            status = obj_classes.get(obj_name, "valid")
            if status != "valid":
                pair = SHOT_PALETTE.get(status)
                if pair is not None:
                    fg, bg = pair[0], pair[1]
                    if bg:
                        color_kw["color"] = bg
                    if fg:
                        color_kw["text_color"] = fg
            tid = widget.add_track(
                obj_name.split("|")[-1],
                icon=icon,
                dimmed=not in_active or not exists,
                italic=not in_active and exists,
                **color_kw,
            )
            track_ids[obj_name] = tid
        return track_ids

    def _build_clips(self, widget, shot, visible_shots, segments_by_shot, track_ids):
        """Add animation and stepped clips for each visible shot."""
        from mayatk.anim_utils.shots._shots import SHOT_PALETTE

        for vs in visible_shots:
            is_active = vs.shot_id == shot.shot_id
            segs = segments_by_shot[vs.shot_id]
            obj_classes = vs.classify_objects()

            by_obj: dict = defaultdict(list)
            for seg in segs:
                by_obj[seg["obj"]].append(seg)

            store = self.sequencer.store if self.sequencer else None

            for obj_name in sorted(set(vs.objects) | set(by_obj)):
                if self.sequencer.is_object_hidden(obj_name):
                    continue
                tid = track_ids.get(obj_name)
                if tid is None:
                    continue
                obj_segs = by_obj.get(obj_name, [])
                if not obj_segs:
                    continue

                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}
                elif store and obj_name in store.locked_objects:
                    extra = {"locked": True}
                status = obj_classes.get(obj_name, "valid")
                if status != "valid":
                    pair = SHOT_PALETTE.get(status)
                    if pair is not None:
                        fg = pair[0]
                        if fg:
                            extra["status_color"] = fg

                # Merge adjacent segments separated only by flat-key
                # gaps so the main track shows fewer, larger clips.
                # Stepped (zero-duration) segments are kept separate — they
                # are point events and must not be absorbed into spans.
                gap = store.detection_threshold if store else 10.0
                span_segs = [sg for sg in obj_segs if not sg.get("is_stepped")]
                stepped_segs = [sg for sg in obj_segs if sg.get("is_stepped")]

                span_segs.sort(key=lambda sg: sg["start"])
                merged: list = []
                if span_segs:
                    merged.append(
                        {
                            "start": span_segs[0]["start"],
                            "end": span_segs[0]["end"],
                            "segs": [span_segs[0]],
                        }
                    )
                    for seg in span_segs[1:]:
                        if seg["start"] <= merged[-1]["end"] + gap:
                            merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
                            merged[-1]["segs"].append(seg)
                        else:
                            merged.append(
                                {
                                    "start": seg["start"],
                                    "end": seg["end"],
                                    "segs": [seg],
                                }
                            )

                for m in merged:
                    s = m["start"]
                    e = m["end"]
                    attrs = extract_attributes(m["segs"])
                    clip_extra = dict(extra)
                    if is_active and attrs:
                        clip_extra["label_center"] = Attributes.abbreviate_attrs(attrs)
                    widget.add_clip(
                        track_id=tid,
                        start=s,
                        duration=e - s,
                        label="",
                        shot_id=vs.shot_id,
                        obj=obj_name,
                        orig_start=s,
                        orig_end=e,
                        attributes=attrs,
                        **clip_extra,
                    )

                # Add stepped (zero-duration) clips individually
                for seg in stepped_segs:
                    t = seg["start"]
                    # Skip stepped keys that fall inside a merged span —
                    # the span clip already covers that time.
                    if any(m["start"] <= t <= m["end"] for m in merged):
                        self.logger.debug(
                            "[SYNC]   stepped key at %s inside span — skipped",
                            t,
                        )
                        continue
                    clip_extra = dict(extra)
                    widget.add_clip(
                        track_id=tid,
                        start=t,
                        duration=0.0,
                        label="",
                        shot_id=vs.shot_id,
                        obj=obj_name,
                        orig_start=t,
                        orig_end=t,
                        is_stepped=True,
                        stepped_key_time=t,
                        **clip_extra,
                    )

    def _build_audio_tracks(self, widget, shot, visible_shots) -> None:
        """Add audio tracks and clips for visible shots.

        Iterates segments produced by the unified audio system
        (``mayatk.audio_utils.segments``).  Each canonical
        ``track_id`` becomes one widget track; segments are keyed into
        the sequencer with ``audio_track_id`` for downstream consumers.
        """
        scene_start = min(vs.start for vs in visible_shots)
        scene_end = max(vs.end for vs in visible_shots)
        # Audio discovery hammers maya.cmds.keyframe / attributeQuery
        # (~28ms per rebuild on a busy carrier).  Segments only change
        # on audio edits, not on shot-switches — cache by range.
        cache_key = (scene_start, scene_end)
        cached = self._audio_segments_cache
        if cached is not None and cached[0] == cache_key:
            segs = cached[1]
        else:
            segs = collect_all_segments(
                scene_start=scene_start,
                scene_end=scene_end,
                include_waveform=True,
            )
            self._audio_segments_cache = (cache_key, segs)

        # Group by canonical track_id.
        by_track: dict = defaultdict(list)
        for seg in segs:
            by_track[seg.track_id].append(seg)

        node_icons_cls = self._try_load_maya_icons()

        for track_id, track_segs in by_track.items():
            if self.sequencer.is_object_hidden(track_id):
                continue

            # Pre-compute visible clip descriptors; skip the track
            # entirely if no segment strictly overlaps any visible shot.
            clip_descs: list = []
            for seg in track_segs:
                for vs in visible_shots:
                    vis_start = max(seg.start, vs.start)
                    vis_end = min(seg.end, vs.end)
                    if vis_end <= vis_start:
                        continue
                    clip_descs.append((seg, vs, vis_start, vis_end))

            if not clip_descs:
                continue

            # Track icon: look up DG node if one exists (rendered view).
            dg_node = audio_utils.find_dg_node_for_track(track_id)
            icon = (
                node_icons_cls.get_icon(dg_node)
                if (node_icons_cls and dg_node)
                else None
            )
            widget_track_id = widget.add_track(track_id, icon=icon)

            for seg, vs, vis_start, vis_end in clip_descs:
                is_active = vs.shot_id == shot.shot_id

                full_waveform = seg.waveform or []
                full_dur = seg.end - seg.start
                if full_waveform and full_dur > 0:
                    n = len(full_waveform)
                    frac_lo = (vis_start - seg.start) / full_dur
                    frac_hi = (vis_end - seg.start) / full_dur
                    i_lo = int(frac_lo * n)
                    i_hi = max(i_lo + 1, int(frac_hi * n))
                    vis_waveform = full_waveform[i_lo:i_hi]
                else:
                    vis_waveform = full_waveform

                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}

                widget.add_clip(
                    track_id=widget_track_id,
                    start=vis_start,
                    duration=vis_end - vis_start,
                    label=seg.label or track_id,
                    color="#3A7D44",
                    is_audio=True,
                    audio_track_id=seg.track_id,
                    file_path=seg.file_path,
                    waveform=vis_waveform,
                    orig_start=seg.start,
                    orig_end=seg.end,
                    shot_id=vs.shot_id,
                    **extra,
                )

    def hide_track(self, track_names) -> None:
        """Hide one or more tracks by name, persist, and rebuild the widget."""
        if self.sequencer is None:
            return
        if isinstance(track_names, str):
            track_names = [track_names]
        for name in track_names:
            full_name = self._resolve_full_name(name)
            self.sequencer.set_object_hidden(full_name, True)
        self._sync_to_widget()

    def show_track(self, track_name: str) -> None:
        """Un-hide a track by object name, persist, and rebuild the widget."""
        if self.sequencer is None:
            return
        self.sequencer.set_object_hidden(track_name, False)
        self._sync_to_widget()

    def delete_track(self, track_names) -> None:
        """Permanently remove objects from all shots and rebuild the widget."""
        if self.sequencer is None:
            return
        if isinstance(track_names, str):
            track_names = [track_names]
        for name in track_names:
            full_name = self._resolve_full_name(name)
            self.sequencer.store.remove_object_from_shots(full_name)
        self._sync_to_widget()

    def on_selection_changed(self, clip_ids: list) -> None:
        """Select the corresponding Maya objects when clips are clicked.

        Also opens the Graph Editor so the selected object's animation
        curves are immediately visible.
        """
        if not clip_ids or pm is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return

        resolved = []
        clip_labels = []
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                continue
            obj = clip.data.get("obj")
            if obj:
                full = self._resolve_full_name(obj)
                if pm.objExists(full):
                    resolved.append(full)
                attrs = clip.data.get("attributes", [])
                if not attrs:
                    attr_name = clip.data.get("attr_name")
                    if attr_name:
                        attrs = [attr_name]
                start = clip.data.get("orig_start")
                end = clip.data.get("orig_end")
                parts = [obj]
                if attrs:
                    parts.append(", ".join(attrs[:3]))
                    if len(attrs) > 3:
                        parts[-1] += f" +{len(attrs) - 3}"
                if start is not None and end is not None:
                    dur = int(end - start)
                    parts.append(f"{start:.0f}\u2013{end:.0f} ({dur}f)")
                clip_labels.append(" \u00b7 ".join(parts))
        self._select_and_show(resolved)
        if clip_labels:
            self._set_footer("  |  ".join(clip_labels[:3]))
            if len(clip_labels) > 3:
                self._set_footer(
                    "  |  ".join(clip_labels[:3]) + f"  (+{len(clip_labels) - 3} more)"
                )

    def on_track_selected(self, track_names: list) -> None:
        """Select Maya objects when track labels are clicked in the header."""
        if not track_names or pm is None:
            return
        resolved = []
        for name in track_names:
            full = self._resolve_full_name(name)
            if pm.objExists(full):
                resolved.append(full)
        self._select_and_show(resolved)

    def on_clip_locked(self, clip_id: int, locked: bool) -> None:
        """Persist per-object clip lock and propagate to sibling clips."""
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        clip = widget._clips.get(clip_id)
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        if not obj_name:
            return

        # Persist on the store
        store = self.sequencer.store
        if locked:
            store.locked_objects.add(obj_name)
        else:
            store.locked_objects.discard(obj_name)

        # Propagate to every clip (main + sub-row) for the same object.
        # The originating clip is included — contextMenuEvent routes
        # through set_clip_locked, and a redundant call is harmless.
        for cid, cd in widget._clips.items():
            if cd.data.get("obj") == obj_name:
                widget.set_clip_locked(cid, locked)
        self._sub_row_cache.clear()

    def on_track_menu(self, menu, track_names) -> None:
        """Add Maya-specific actions to the track header context menu."""
        if not track_names:
            return

        if pm is None:
            return

        menu.addSeparator()
        resolved = []
        for name in track_names:
            full = self._resolve_full_name(name)
            if pm.objExists(full):
                resolved.append(full)
        if resolved:
            menu.addAction(
                "Reveal in Outliner",
                lambda objs=list(resolved): self._reveal_in_outliner(objs),
            )
        menu.addAction(
            "Attribute Spreadsheet",
            lambda names=list(track_names): self._open_spreadsheet(names),
        )

    def on_header_menu(self, menu) -> None:
        """Add settings actions to the header background context menu."""

    def _on_frame_on_shot_change_toggled(self, checked: bool) -> None:
        if self.sequencer is None:
            return
        self.sequencer.store.frame_on_shot_change = checked
        self.sequencer.store.mark_dirty()

    def _on_select_on_load_toggled(self, checked: bool) -> None:
        if self.sequencer is None:
            return
        self.sequencer.store.select_on_load = checked
        self.sequencer.store.mark_dirty()

    def _set_show_internal_holds(self, enabled: bool) -> None:
        """Toggle flat-key span visibility in attribute sub-rows."""
        self._show_internal_holds = enabled
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def _open_spreadsheet(self, track_names) -> None:
        """Select the objects and open Maya's Attribute Spread Sheet."""
        resolved = []
        for name in track_names:
            full = self._resolve_full_name(name)
            if pm.objExists(full):
                resolved.append(full)
        if resolved:
            pm.select(resolved, replace=True)
        try:
            pm.mel.eval("SpreadSheetEditor")
        except Exception:
            pass

    def _select_and_show(self, objects: list) -> None:
        """Select the given Maya objects and open the Graph Editor."""
        if not objects:
            return
        # Resolve to long DAG paths to avoid ambiguous short-name errors
        long_names = pm.ls(objects, long=True)
        if not long_names:
            return
        pm.select(long_names, replace=True)
        try:
            pm.mel.eval("GraphEditor")
        except Exception:
            pass

    def on_key_selection_changed(self, key_groups: list) -> None:
        """Sync the Maya Graph Editor selection to match the sequencer.

        Parameters
        ----------
        key_groups : list[dict]
            ``[{clip_id, times}, ...]`` — one entry per clip with
            selected :class:`KeyframeItem` children.
        """
        if pm is None:
            return

        import maya.cmds as cmds

        widget = self._get_sequencer_widget()
        if widget is None:
            return

        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import (
            curves_for_attr,
        )

        # Deselect all keys first.
        cmds.selectKey(clear=True)

        for group in key_groups:
            clip = widget.get_clip(group["clip_id"])
            if clip is None:
                continue
            obj_name = clip.data.get("obj")
            attr_name = clip.data.get("attr_name")
            if not obj_name or not attr_name:
                continue

            curves = curves_for_attr(obj_name, attr_name)
            for crv in curves:
                for t in group["times"]:
                    cmds.selectKey(
                        str(crv),
                        add=True,
                        time=(t, t),
                    )

    def _reveal_in_outliner(self, objects) -> None:
        """Select and reveal object(s) in Maya's Outliner."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.reveal_in_outliner(objects)

    def _delete_clip_keys(self, clip_ids: list) -> None:
        """Delete Maya keyframes for the given clip IDs and refresh."""
        if pm is None:
            self.logger.debug("_delete_clip_keys: pm is None, skipping.")
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            self.logger.debug("_delete_clip_keys: no widget, skipping.")
            return

        import maya.cmds as cmds

        # Collect all operations first so we can wrap in a single UndoChunk.
        ops: list = []
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                self.logger.debug("_delete_clip_keys: clip %s not found.", cid)
                continue
            if clip.data.get("read_only"):
                self.logger.debug("_delete_clip_keys: clip %s is read-only.", cid)
                continue
            obj = clip.data.get("obj")
            if not obj:
                self.logger.debug("_delete_clip_keys: clip %s has no obj.", cid)
                continue
            full = self._resolve_full_name(obj)
            if not pm.objExists(full):
                self.logger.debug("_delete_clip_keys: '%s' does not exist.", full)
                continue

            attrs = clip.data.get("attributes", [])
            # Sub-row clips store a single attribute name, not a list.
            if not attrs:
                attr_name = clip.data.get("attr_name")
                if attr_name:
                    attrs = [attr_name]
            start = clip.data.get("orig_start")
            end = clip.data.get("orig_end")
            if start is None or end is None:
                self.logger.debug(
                    "_delete_clip_keys: clip %s missing orig_start/end.", cid
                )
                continue

            if not attrs:
                self.logger.debug("_delete_clip_keys: clip %s has no attributes.", cid)
                continue

            for attr in attrs:
                ops.append((f"{full}.{attr}", start, end))

        if not ops:
            return

        deleted = False
        with pm.UndoChunk():
            for plug, start, end in ops:
                try:
                    cmds.cutKey(plug, time=(start, end), clear=True)
                    deleted = True
                except Exception:
                    self.logger.debug(
                        "_delete_clip_keys: cutKey failed for '%s'.",
                        plug,
                        exc_info=True,
                    )

        if deleted:
            self._save_shot_state()
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            self._sync_to_widget()
            n = len(clip_ids)
            self._set_footer(f"Deleted {n} clip{'s' if n != 1 else ''}")

    def _delete_selected_clip_keys(self) -> None:
        """Delete selected keyframes or, if none, all keys on selected clips.

        Individual keyframes are batched into a single Maya UndoChunk so
        that Ctrl+Z restores all deleted keys in one step.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            self.logger.debug("_delete_selected_clip_keys: no widget.")
            return

        from uitk.widgets.sequencer._keyframe import KeyframeItem

        try:
            items = widget._timeline._scene.selectedItems()
        except RuntimeError:
            items = []

        # Group selected keyframe items by clip_id.
        by_clip: dict = {}
        for item in items:
            if isinstance(item, KeyframeItem):
                cid = item._parent_clip._data.clip_id
                by_clip.setdefault(cid, []).append(item._time)

        if by_clip:
            # Batch-delete all selected keyframes in a single undo chunk.
            import maya.cmds as cmds

            from mayatk.anim_utils.shots.shot_sequencer.clip_motion import (
                curves_for_attr,
            )

            deleted = 0
            with pm.UndoChunk():
                for clip_id, times in by_clip.items():
                    clip = widget.get_clip(clip_id)
                    if clip is None:
                        continue
                    obj_name = clip.data.get("obj")
                    attr_name = clip.data.get("attr_name")
                    if not obj_name or not attr_name:
                        continue
                    curves = curves_for_attr(obj_name, attr_name)
                    if not curves:
                        continue
                    for t in times:
                        for crv in curves:
                            cmds.cutKey(str(crv), time=(t, t), clear=True)
                    deleted += len(times)

            if deleted:
                self._save_shot_state()
                shot_id = self.active_shot_id
                self._segment_cache.clear()
                self._sub_row_cache.clear()
                self._sync_to_widget(shot_id=shot_id)
                self._set_footer(f"Deleted {deleted} key{'s' if deleted != 1 else ''}")
            return

        # Fallback: delete entire clips when no individual keys are selected.
        clip_ids = widget.selected_clips()
        self.logger.debug("_delete_selected_clip_keys: selected_clips=%s", clip_ids)
        if clip_ids:
            self._delete_clip_keys(clip_ids)

    def _resolve_full_name(self, short_name: str) -> str:
        """Map a short display name back to the full DAG path.

        Handles both regular object tracks and audio tracks (prefixed
        with ``♫ ``).
        """
        # Strip audio track prefix
        if short_name.startswith("\u266b "):
            short_name = short_name[2:]
        if self.sequencer is None:
            return short_name
        # Check shot objects
        for shot in self.sequencer.shots:
            for obj in shot.objects:
                if obj.split("|")[-1] == short_name:
                    return obj
        # Check audio source nodes
        if pm is not None:
            try:
                matches = pm.ls(short_name, long=True)
                if matches:
                    return str(matches[0])
            except Exception:
                pass
        return short_name

    def _get_sequencer_widget(self):
        """Return the SequencerWidget from the UI."""
        return getattr(self.ui, "sequencer_widget", None)

    def _provide_sub_rows(self, track_id, track_name):
        """Return per-attribute sub-row data for a track.

        Called by the widget's ``sub_row_provider`` protocol when a user
        double-clicks a header label to expand a track.

        Uses the same ``SegmentKeys.collect_segments`` pipeline as the
        object row so that hold absorption, hold-only synthesis, and
        motion detection are consistent between both views.

        Returns
        -------
        list
            ``[(attr_name, [(start, dur, label, color, extra), ...]), ...]``
            where *extra* is a dict of kwargs passed through to ``add_clip``.
        """
        if self.sequencer is None or pm is None:
            return []

        shot_id = self.active_shot_id
        if shot_id is None:
            return []
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return []

        obj_name = self._resolve_full_name(track_name)

        # Return cached result if available
        cache_key = (shot_id, track_name)
        cached = self._sub_row_cache.get(cache_key)
        if cached is not None:
            return cached
        import maya.cmds as cmds

        # Resolve to long DAG path to avoid ambiguous short-name errors
        long_names = cmds.ls(obj_name, long=True)
        if not long_names:
            return []
        obj_name = long_names[0]

        from mayatk.anim_utils.segment_keys import SegmentKeys

        all_curves = (
            cmds.listConnections(obj_name, type="animCurve", s=True, d=False) or []
        )
        if not all_curves:
            return []

        widget = self._get_sequencer_widget()
        color_map = widget.attribute_colors if widget else {}
        show_holds = self._show_internal_holds

        # Discover which attributes this object has animated curves for.
        # We need the attribute names to iterate; the actual per-attribute
        # curve filtering is handled by collect_segments via channel_box_attrs.
        attr_names: set = set()
        for curve in all_curves:
            try:
                conns = (
                    cmds.listConnections(
                        str(curve), plugs=True, destination=True, source=False
                    )
                    or []
                )
                for conn in conns:
                    if "." in conn:
                        attr_names.add(conn.rsplit(".", 1)[-1])
            except Exception:
                continue

        store = self.sequencer.store if self.sequencer else None
        is_obj_locked = bool(store and obj_name in store.locked_objects)

        # Build a map from attribute name to its animCurve node so we can
        # produce full-range background curve previews after the per-segment
        # clips are assembled.
        attr_to_curve: dict = {}
        for curve in all_curves:
            try:
                conns = (
                    cmds.listConnections(
                        str(curve), plugs=True, destination=True, source=False
                    )
                    or []
                )
                for conn in conns:
                    if "." in conn:
                        a = conn.rsplit(".", 1)[-1]
                        attr_to_curve.setdefault(a, curve)
            except Exception:
                continue

        # Determine the visible time range for the full-range curve based
        # on the current display mode.
        visible = self._visible_shots(shot)
        curve_range_start = min(s.start for s in visible)
        curve_range_end = max(s.end for s in visible)

        result = []
        for attr_name in sorted(attr_names):
            # Reuse the same collect_segments pipeline as the object row.
            # channel_box_attrs filters to just this attribute's curves.
            segs = SegmentKeys.collect_segments(
                [obj_name],
                split_static=True,
                channel_box_attrs=[attr_name],
                ignore_holds=not show_holds,
                ignore_visibility_holds=True,
                motion_only=True,
                motion_rate=1e-3,
                time_range=(shot.start, shot.end),
            )

            if not segs:
                continue

            # Determine which segments are pure holds by comparing
            # against active-only results.  A segment is a pure hold
            # only when it has zero overlap with any active span.
            # Motion-extended segments (motion + trailing hold) keep
            # normal styling since they contain real motion.
            hold_ranges: set = set()
            if show_holds:
                active_segs = SegmentKeys.collect_segments(
                    [obj_name],
                    split_static=True,
                    channel_box_attrs=[attr_name],
                    ignore_holds=True,
                    ignore_visibility_holds=True,
                    motion_only=True,
                    motion_rate=1e-3,
                    time_range=(shot.start, shot.end),
                )
                active_spans = [(s["start"], s["end"]) for s in active_segs]
                for seg in segs:
                    ss, se = seg["start"], seg["end"]
                    # Pure hold: no overlap with any active span
                    if not any(a_s < se and a_e > ss for a_s, a_e in active_spans):
                        hold_ranges.add((ss, se))

            color = color_map.get(attr_name)
            segments = []
            for seg in segs:
                s, e = seg["start"], seg["end"]
                dur = e - s
                is_hold = (s, e) in hold_ranges

                # Build curve preview from the segment's own curves
                preview = None
                for crv in seg.get("curves", []):
                    preview = build_curve_preview(crv, s, e)
                    if preview:
                        break
                extra = {
                    "obj": obj_name,
                    "attr_name": attr_name,
                    "shot_id": shot_id,
                    "orig_start": s,
                    "orig_end": e,
                }
                if preview:
                    extra["curve_preview"] = preview
                if is_hold:
                    extra["is_hold"] = True
                if is_obj_locked:
                    extra["locked"] = True
                segments.append((s, dur, attr_name, color, extra))
            result.append((attr_name, segments))

        # Push full-range background curve previews to the widget for each
        # attribute sub-row.  These are static reference lines painted in
        # drawBackground — no interaction, no updates during drag.
        if widget is not None:
            for attr_name, _ in result:
                crv = attr_to_curve.get(attr_name)
                if crv is None:
                    continue
                bg_preview = build_curve_preview(
                    crv, curve_range_start, curve_range_end
                )
                hex_color = color_map.get(attr_name, "#CCCCCC")
                widget.set_bg_curve_preview(
                    track_id, attr_name, bg_preview, color=hex_color or "#CCCCCC"
                )

        self._sub_row_cache[cache_key] = result
        return result

    # ---- signal handlers (clip motion in _clip_motion.py) ----------------

    def on_clip_renamed(self, clip_id: int, new_label: str) -> None:
        """Handle inline rename — currently a no-op (shot clips removed)."""
        pass

    def on_playhead_moved(self, frame: float) -> None:
        """Sync the Maya playhead to the widget playhead.

        Audio scrub is handled by the widget's own :class:`ScrubPlayer`
        (bound via :meth:`_ensure_sound_on_timeline`); this method only
        needs to mirror the Maya time value.
        """
        self._syncing_playhead = True
        try:
            self._ensure_sound_on_timeline()
            pm.currentTime(frame, update=True)
        finally:
            self._syncing_playhead = False

    def _ensure_sound_on_timeline(self) -> None:
        """Bind the composite audio to both Maya's time slider and the
        sequencer widget's :class:`ScrubPlayer`.

        Maya's Time Slider handles playback/loop audio; the widget's
        scrub player handles drag-scrub (since ``pm.currentTime(
        update=True)`` does not emit audio).  Both are refreshed together
        so they stay in lockstep.
        """
        cached = getattr(self, "_active_sound", None)
        if cached and pm.objExists(cached):
            node = cached
        else:
            node = self._resolve_preferred_audio_node()
            if not node:
                self._active_sound = ""
                return
            try:
                slider = pm.mel.eval("$tmp = $gPlayBackSlider")
                pm.timeControl(slider, e=True, sound=node, displaySound=True)
            except Exception:
                pass
            self._active_sound = node

        # Push the bound node's WAV into the widget's ScrubPlayer.  Works
        # for both the composite node *and* a per-track DG node —
        # whichever the Time Slider ended up bound to.
        if getattr(self, "_bound_audio_node", None) == node:
            return  # path already in sync with current node
        wav_path = self._get_bound_audio_wav(node)
        if not wav_path:
            return
        widget = self._get_sequencer_widget()
        set_audio = getattr(widget, "set_audio_source", None)
        if set_audio is None:
            return
        if set_audio(wav_path, audio_utils.get_fps()):
            self._bound_audio_node = node
            self._bound_audio_path = wav_path

    @staticmethod
    def _resolve_preferred_audio_node() -> str:
        """Return the composite DG audio node name, else the first per-track
        DG node, else empty string."""
        try:
            from mayatk.audio_utils.audio_clips._audio_clips import AudioClips
            comp = AudioClips._find_composite_node()
            if comp and pm.objExists(comp):
                return comp
        except Exception:
            pass
        for track_id in audio_utils.list_tracks():
            dg = audio_utils.find_dg_node_for_track(track_id)
            if dg and pm.objExists(dg):
                return dg
        return ""

    @staticmethod
    def _get_composite_wav_path() -> str:
        """Return the composite WAV file path, or empty string."""
        try:
            import maya.cmds as cmds
            from mayatk.audio_utils.audio_clips._audio_clips import AudioClips
            node = AudioClips._find_composite_node()
            if not node:
                return ""
            path = cmds.getAttr(f"{node}.filename") or ""
            return path.replace("\\", "/")
        except Exception:
            return ""

    # ---- Transport controls (footer) -------------------------------------

    def _setup_transport_controls(self) -> None:
        """Install the reusable :class:`TransportControls` row on the
        right side of the footer, wired to a Maya :class:`PlayController`.

        Frame/key/go-to actions interrupt playback by default (see
        :attr:`TransportControls.interrupt_mode`).  Playhead navigation
        goes through the :class:`SequencerWidget` so scrub audio fires
        via ``playhead_moved``.
        """
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return
        if getattr(self, "_transport_controls", None) is not None:
            return  # already built

        widget = self._get_sequencer_widget()
        if widget is None:
            return

        from uitk.widgets.sequencer import TransportControls

        pc = _MayaPlayController(self)
        h = max(footer.height(), 20)
        transport = TransportControls(
            sequencer=widget,
            play_controller=pc,
            parent=footer,
            button_height=h,
            interrupt_mode=TransportControls.INTERRUPT_STOP,
            range_fn=self._playback_range,
            button_names=(
                "go_to_start", "prev_key",
                "play_back", "play_forward",
                "next_key", "go_to_end",
            ),
        )
        transport.attach_to_footer(footer, side="right")
        self._transport_controls = transport

        # Prime the audio binding now so the first scrub produces
        # sound — the widget's built-in audio slot runs before the
        # controller's ``on_playhead_moved``, so without this the first
        # drag fires into an unsourced player.
        try:
            self._ensure_sound_on_timeline()
        except Exception:
            pass

    @staticmethod
    def _playback_range() -> tuple:
        try:
            lo = float(pm.playbackOptions(q=True, min=True))
            hi = float(pm.playbackOptions(q=True, max=True))
        except Exception:
            lo, hi = 1.0, 120.0
        return lo, hi

    @staticmethod
    def _get_bound_audio_wav(node: str) -> str:
        """Return the WAV path stored on *node* (composite or per-track).

        Both the composite DG audio node and Maya's per-track audio nodes
        expose a ``.filename`` attr pointing at an on-disk WAV, so the
        same accessor works for either — letting the widget's scrub
        player fall back to a per-track preview when no composite yet
        exists.
        """
        if not node:
            return ""
        try:
            import maya.cmds as cmds
            path = cmds.getAttr(f"{node}.filename") or ""
            return path.replace("\\", "/")
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Play controller (Maya)
# ---------------------------------------------------------------------------


class _MayaPlayController:
    """:class:`PlayController` adapter driving Maya's timeline via ``pm.play``.

    Ensures audio is bound to the Time Slider before starting playback.
    Tracks direction so ``TransportControls`` can resume the right way.
    """

    def __init__(self, controller: "ShotSequencerController"):
        self._ctl = controller
        self._forward = True

    def is_playing(self) -> bool:
        try:
            return bool(pm.play(q=True, state=True))
        except Exception:
            return False

    def play(self, forward: bool) -> None:
        self._forward = bool(forward)
        try:
            self._ctl._ensure_sound_on_timeline()
        except Exception:
            pass
        try:
            if self.is_playing():
                pm.play(state=False)
            pm.play(forward=bool(forward))
        except Exception:
            pass

    def stop(self) -> None:
        try:
            if self.is_playing():
                pm.play(state=False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Shot Edit Dialog
# ---------------------------------------------------------------------------


class ShotEditDialog:
    """Lightweight dialog for creating or editing a shot.

    Uses plain Qt widgets — no dependency on uitk beyond the parent.
    Returns ``(name, start, end, description)`` on accept, ``None`` on cancel.
    """

    @staticmethod
    def show(
        parent=None,
        name: str = "",
        start: float = 1.0,
        end: float = 100.0,
        description: str = "",
        title: str = "Shot",
    ):
        """Show a modal dialog and return the result tuple or ``None``."""
        from qtpy import QtWidgets, QtCore

        dlg = QtWidgets.QDialog(parent)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(280)

        layout = QtWidgets.QFormLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)

        name_edit = QtWidgets.QLineEdit(name)
        name_edit.setPlaceholderText("Shot name")
        layout.addRow("Name:", name_edit)

        start_spin = QtWidgets.QDoubleSpinBox()
        start_spin.setDecimals(1)
        start_spin.setRange(-1e6, 1e6)
        start_spin.setValue(start)
        layout.addRow("Start:", start_spin)

        end_spin = QtWidgets.QDoubleSpinBox()
        end_spin.setDecimals(1)
        end_spin.setRange(-1e6, 1e6)
        end_spin.setValue(end)
        layout.addRow("End:", end_spin)

        desc_edit = QtWidgets.QLineEdit(description)
        desc_edit.setPlaceholderText("Optional description")
        layout.addRow("Description:", desc_edit)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return None

        return (
            name_edit.text().strip() or "Shot",
            start_spin.value(),
            end_spin.value(),
            desc_edit.text().strip(),
        )


class ShotSequencerSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shot_sequencer

        # Create controller
        self.controller = ShotSequencerController(self)

        # SequencerWidget is promoted directly in the .ui file.
        # When loaded outside tentacle (deferred promotion), the widget
        # may still be the placeholder QSplitter — skip signal wiring.
        sequencer = self.controller._get_sequencer_widget()
        if sequencer is not None and hasattr(sequencer, "clip_resized"):
            sequencer.window_shortcuts = True
            sequencer.clip_resized.connect(self.controller.on_clip_resized)
            sequencer.clip_moved.connect(self.controller.on_clip_moved)
            sequencer.clips_batch_moved.connect(self.controller.on_clips_batch_moved)
            sequencer.clip_renamed.connect(self.controller.on_clip_renamed)
            sequencer.playhead_moved.connect(self.controller.on_playhead_moved)
            sequencer.track_hidden.connect(self.controller.hide_track)
            sequencer.track_shown.connect(self.controller.show_track)
            sequencer.track_deleted.connect(self.controller.delete_track)
            sequencer.selection_changed.connect(self.controller.on_selection_changed)
            sequencer.track_selected.connect(self.controller.on_track_selected)
            sequencer.track_menu_requested.connect(self.controller.on_track_menu)
            sequencer.clip_locked.connect(self.controller.on_clip_locked)
            sequencer.undo_requested.connect(self.controller.on_undo)
            sequencer.redo_requested.connect(self.controller.on_redo)
            sequencer.marker_added.connect(self.controller.on_marker_added)
            sequencer.marker_moved.connect(self.controller.on_marker_moved)
            sequencer.marker_changed.connect(self.controller.on_marker_changed)
            sequencer.marker_removed.connect(self.controller.on_marker_removed)
            sequencer.gap_resized.connect(self.controller.on_gap_resized)
            sequencer.gap_left_resized.connect(self.controller.on_gap_left_resized)
            sequencer.gap_moved.connect(self.controller.on_gap_moved)
            sequencer.gap_lock_changed.connect(self.controller.on_gap_lock_changed)
            sequencer.gap_lock_all_requested.connect(self.controller.on_gap_lock_all)
            sequencer.gap_unlock_all_requested.connect(
                self.controller.on_gap_unlock_all
            )
            sequencer.clip_menu_requested.connect(self.controller.on_clip_menu)
            sequencer.gap_menu_requested.connect(self.controller.on_gap_menu)
            sequencer.range_highlight_changed.connect(
                self.controller.on_range_highlight_changed
            )
            sequencer.zone_context_menu_requested.connect(
                self.controller.on_zone_context_menu
            )
            sequencer.shot_block_clicked.connect(self.controller.on_shot_block_clicked)
            sequencer.shot_switch_requested.connect(
                self.controller._on_shot_switch_requested
            )
            sequencer.header_menu_requested.connect(self.controller.on_header_menu)
            sequencer.keys_moved.connect(self.controller.on_keys_moved)
            sequencer.keys_deleted.connect(self.controller.on_keys_deleted)
            sequencer.key_selection_changed.connect(
                self.controller.on_key_selection_changed
            )
            sequencer._zone_menu_connected = True

            # Register Delete key shortcut for selected clips.
            # Always update the action callback to the current controller
            # in case the slots were re-initialised with a new controller.
            # Use WindowShortcut context when window_shortcuts is active so
            # Qt claims the key at the window level and Maya never sees it.
            from qtpy import QtCore as _QtCore, QtGui as _QtGui

            _del_ctx = (
                _QtCore.Qt.WindowShortcut
                if sequencer.window_shortcuts
                else _QtCore.Qt.WidgetWithChildrenShortcut
            )
            _del_key = _QtGui.QKeySequence("Delete").toString()
            if _del_key in sequencer._shortcut_mgr.shortcuts:
                _entry = sequencer._shortcut_mgr.shortcuts[_del_key]
                _entry["action"] = self.controller._delete_selected_clip_keys
                if _entry["shortcut"] is not None:
                    _entry["shortcut"].setContext(_del_ctx)
                    _entry["shortcut"].activated.disconnect()
                    _entry["shortcut"].activated.connect(
                        self.controller._delete_selected_clip_keys
                    )
            else:
                sequencer._shortcut_mgr.add_shortcut(
                    "Delete",
                    self.controller._delete_selected_clip_keys,
                    "Delete keys for selected clips",
                    _del_ctx,
                )
        self._setup_shot_nav()
        self.controller._setup_transport_controls()

        # Initial population so gaps and clips are visible immediately.
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _setup_shot_nav(self) -> None:
        """Configure prev/next option box actions on cmb_shot."""
        if self.controller._prev_action is not None:
            return  # Already configured — avoid duplicating actions

        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None or not hasattr(cmb, "option_box"):
            return

        from uitk.widgets.optionBox.options.action import ActionOption

        prev_opt = ActionOption(
            wrapped_widget=cmb,
            callback=lambda: self.controller._navigate_shot(-1),
            icon="chevron_left",
            tooltip="Previous Shot",
            order=0,
        )
        next_opt = ActionOption(
            wrapped_widget=cmb,
            callback=lambda: self.controller._navigate_shot(1),
            icon="chevron_right",
            tooltip="Next Shot",
            order=1,
        )

        # View mode cycle: Current → Adjacent → All
        _VIEW_STATES = [
            {
                "icon": "target",
                "tooltip": "View: Current Shot (click for adjacent)",
                "callback": lambda: self.controller._set_view_mode("adjacent"),
            },
            {
                "icon": "columns",
                "tooltip": "View: Adjacent Shots (click for all)",
                "callback": lambda: self.controller._set_view_mode("all"),
            },
            {
                "icon": "grid",
                "tooltip": "View: All Shots (click for current)",
                "callback": lambda: self.controller._set_view_mode("current"),
            },
        ]
        view_opt = ActionOption(
            wrapped_widget=cmb,
            states=_VIEW_STATES,
            order=4,
        )

        cmb.option_box.set_order(["action"])
        cmb.option_box.add_option(prev_opt)
        cmb.option_box.add_option(next_opt)
        cmb.option_box.add_option(view_opt)

        # "+" button — one-click shot creation
        add_opt = ActionOption(
            wrapped_widget=cmb,
            callback=self.controller._create_shot_one_click,
            icon="add",
            tooltip="New Shot",
            order=2,
        )
        cmb.option_box.add_option(add_opt)

        # Refresh button — re-collect animation data and rebuild widget
        refresh_opt = ActionOption(
            wrapped_widget=cmb,
            callback=self.controller.refresh,
            icon="refresh",
            tooltip="Refresh Sequencer",
            order=6,
        )
        cmb.option_box.add_option(refresh_opt)

        # Show Internal Holds toggle (two-state: off / on)
        _HOLD_STATES = [
            {
                "icon": "eye_off",
                "tooltip": "Show Internal Holds (off)\nClick to reveal flat-key spans in sub-rows",
                "callback": lambda: self.controller._set_show_internal_holds(True),
            },
            {
                "icon": "eye",
                "tooltip": "Show Internal Holds (on)\nClick to hide flat-key spans in sub-rows",
                "callback": lambda: self.controller._set_show_internal_holds(False),
            },
        ]
        holds_opt = ActionOption(
            wrapped_widget=cmb,
            states=_HOLD_STATES,
            order=5,
            settings_key="shot_sequencer_show_holds",
        )
        cmb.option_box.add_option(holds_opt)
        # Sync controller state from persisted option state
        self.controller._show_internal_holds = holds_opt.current_state == 1
        self.controller._holds_action = holds_opt

        self.controller._prev_action = prev_opt
        self.controller._next_action = next_opt
        self.controller._view_mode_action = view_opt
        # Sync controller view mode from persisted button state
        _VIEW_MODE_MAP = {0: "current", 1: "adjacent", 2: "all"}
        self.controller._shot_display_mode = _VIEW_MODE_MAP.get(
            view_opt.current_state, "current"
        )

        # Install right-click context menu on the combobox
        from qtpy import QtCore

        cmb.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        cmb.customContextMenuRequested.connect(self._cmb_context_menu)

        # Wire the mode selector combobox (Shots / Markers)
        cmb_mode = getattr(self.ui, "cmb_mode", None)
        if cmb_mode is not None:
            cmb_mode.blockSignals(True)
            cmb_mode.clear()
            cmb_mode.addItem("Shots:", "shots")
            cmb_mode.addItem("Markers:", "markers")
            cmb_mode.setCurrentIndex(0)
            cmb_mode.blockSignals(False)
            cmb_mode.currentIndexChanged.connect(self._on_cmb_mode_changed)
            self.controller._cmb_mode_widget = cmb_mode

    def _on_playback_range_changed(self, index: int) -> None:
        """Handle playback-range combobox selection."""
        cmb_pb = getattr(self.ui, "cmb_playback_range", None)
        if cmb_pb is None:
            return
        mode = cmb_pb.itemData(index)
        if mode:
            self.controller._set_playback_range_mode(mode)

    def _on_cmb_mode_changed(self, index: int) -> None:
        """Handle the Shots/Markers mode selector combobox."""
        cmb_mode = getattr(self.ui, "cmb_mode", None)
        if cmb_mode is None:
            return
        mode = cmb_mode.itemData(index)
        if mode:
            self.controller._set_cmb_mode(mode)

    def _on_track_order_changed(self, index: int) -> None:
        """Handle track-order scope combobox selection."""
        cmb = getattr(self.ui, "cmb_track_order", None)
        if cmb is None:
            return
        scope = cmb.itemData(index)
        if scope and scope != self.controller._track_order_scope:
            self.controller._track_order_scope = scope
            self.controller._sync_to_widget()

    # ---- shot CRUD helpers -----------------------------------------------

    def _edit_shot_in_settings(self) -> None:
        """Open Shot Settings with the active shot pre-selected."""
        if self.controller.sequencer is not None:
            sid = self.controller.active_shot_id
            if sid is not None:
                self.controller.sequencer.store.set_active_shot(sid)
        self.sb.handlers.marking_menu.show("shots")

    def _delete_shot(self) -> None:
        """Delete the currently selected shot after confirmation."""
        from qtpy import QtWidgets

        if self.controller.sequencer is None:
            return
        sid = self.controller.active_shot_id
        if sid is None:
            return
        shot = self.controller.sequencer.shot_by_id(sid)
        if shot is None:
            return
        reply = QtWidgets.QMessageBox.question(
            self.ui,
            "Delete Shot",
            f'Delete "{shot.name}" [{shot.start:.0f}–{shot.end:.0f}]?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        store = self.controller.sequencer.store
        store.remove_shot(sid)
        store.set_active_shot(None)
        self.controller._set_footer(f"Deleted {shot.name}")

    def _detect_next_shot(self) -> None:
        """Generate a shot from the next unregistered animation cluster."""
        if self.controller.sequencer is None or pm is None:
            return
        widget = self.controller._get_sequencer_widget()
        store = self.controller.sequencer.store if self.controller.sequencer else None
        cand = self.controller.sequencer.detect_next_shot(
            gap_threshold=(store.detection_threshold if store else 5.0),
        )
        if cand is None:
            pm.displayInfo("No additional animation clusters found.")
            return
        result = ShotEditDialog.show(
            parent=self.ui,
            name=cand["name"],
            start=cand["start"],
            end=cand["end"],
            title="Generated Shot",
        )
        if result is None:
            return
        name, s, e, desc = result
        if e <= s:
            return
        self.controller.sequencer.define_shot(
            name=name,
            start=s,
            end=e,
            objects=cand["objects"],
            description=desc,
        )
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _cmb_context_menu(self, pos) -> None:
        """Right-click context menu on the shot combobox."""
        from qtpy import QtWidgets

        if self.controller._cmb_mode != "shots":
            return

        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return

        menu = QtWidgets.QMenu(cmb)
        menu.addAction("New Shot", self.controller._create_shot_one_click)
        menu.addAction("Generate Next Shot\u2026", self._detect_next_shot)
        menu.addSeparator()

        has_shot = self.controller.active_shot_id is not None
        edit_action = menu.addAction("Edit Shot\u2026", self._edit_shot_in_settings)
        edit_action.setEnabled(has_shot)
        delete_action = menu.addAction("Delete Shot", self._delete_shot)
        delete_action.setEnabled(has_shot)

        menu.exec_(cmb.mapToGlobal(pos))

    def header_init(self, widget):
        """Configure header menu."""
        widget.menu.add(
            "QSpinBox",
            setMinimum=0,
            setMaximum=1000,
            setValue=1,
            setObjectName="spn_snap",
            setPrefix="Snap: ",
            setToolTip="Snap interval for clip edges when dragging or resizing (0 = free movement).",
        )
        from uitk.widgets.widgetComboBox import WidgetComboBox

        cmb_pb = widget.menu.add(
            WidgetComboBox,
            setObjectName="cmb_playback_range",
            setToolTip="Control how Maya's playback range tracks the visible shots.",
        )
        cmb_pb.addItem("Playback Range: Off", "off")
        cmb_pb.addItem("Playback Range: Follows View", "follows_view")
        cmb_pb.addItem("Playback Range: Locked to Shot", "locked")
        cmb_pb.setCurrentIndex(1)
        cmb_pb.currentIndexChanged.connect(self._on_playback_range_changed)

        from uitk.widgets.widgetComboBox import WidgetComboBox as _WCB2

        cmb_scope = widget.menu.add(
            _WCB2,
            setObjectName="cmb_track_order",
            setToolTip=(
                "Control how object tracks are ordered across shots.\n\n"
                "Visible: show objects from visible shots only.\n"
                "Global: show all objects from every shot so tracks\n"
                "never reorder when switching shots."
            ),
        )
        cmb_scope.addItem("Track Order: Visible", "visible")
        cmb_scope.addItem("Track Order: Global", "global")
        cmb_scope.setCurrentIndex(
            0 if self.controller._track_order_scope == "visible" else 1
        )
        cmb_scope.currentIndexChanged.connect(self._on_track_order_changed)

        chk_select = widget.menu.add(
            "QCheckBox",
            setText="Select Members on Load",
            setObjectName="chk_select_on_load",
            setToolTip=(
                "Select all objects belonging to the shot\n"
                "when navigating to it in the sequencer."
            ),
        )
        chk_select.restore_state = False  # ShotStore owns this setting
        seq = getattr(self.controller, "sequencer", None)
        if seq is not None and hasattr(seq, "store"):
            chk_select.setChecked(seq.store.select_on_load)
        chk_select.toggled.connect(self.controller._on_select_on_load_toggled)

        chk_frame = widget.menu.add(
            "QCheckBox",
            setText="Frame on Shot Change",
            setObjectName="chk_frame_on_shot_change",
            setToolTip=(
                "Automatically frame the camera on the shot's objects\n"
                "when navigating to a different shot."
            ),
        )
        chk_frame.restore_state = False  # ShotStore owns this setting
        if seq is not None and hasattr(seq, "store"):
            chk_frame.setChecked(seq.store.frame_on_shot_change)
        chk_frame.toggled.connect(self.controller._on_frame_on_shot_change_toggled)

        widget.menu.add("Separator", setTitle="Actions")
        widget.menu.add(
            "QPushButton",
            setText="Attribute Colors",
            setObjectName="btn_colors",
            setToolTip="Customize the colors used to display each animated attribute in the sequencer.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Shortcuts\u2026",
            setObjectName="btn_shortcuts",
            setToolTip="View and customise sequencer keyboard shortcuts.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Shots\u2026",
            setObjectName="btn_shot_settings",
            setToolTip="Open shared shot generation, gap, and editing settings.",
        )
        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Shot Sequencer \u2014 Visual timeline editor for per-shot\n"
                "animation with ripple editing, gap management, markers,\n"
                "and audio tracks.\n\n"
                "Quick Start:\n"
                "  1. Click + to create a shot (or use the Manifest).\n"
                "  2. Select a shot from the dropdown to load its clips.\n"
                "  3. Drag clips to adjust timing; edges to resize.\n"
                "  4. Use View Mode to see adjacent or all shots.\n\n"
                "Shot Navigation:\n"
                "  \u2022 Dropdown \u2014 Select shot (sets playback range,\n"
                "    selects objects, reframes the timeline).\n"
                "  \u2022 \u25c4 / \u25ba \u2014 Previous / next shot.\n"
                "  \u2022 + \u2014 Append a new shot to the end.\n"
                "  \u2022 View Mode (cycles): Current \u2192 Adjacent \u2192 All.\n"
                "  \u2022 Shots / Markers selector \u2014 Switch dropdown content.\n"
                "  \u2022 Refresh \u2014 Rebuild from Maya.\n"
                "  \u2022 Right-click dropdown: New Shot, Generate Next Shot\n"
                "    (finds the next unregistered animation cluster),\n"
                "    Edit Shot, Delete Shot.\n\n"
                "Ruler: Click/drag to move playhead, double-click to\n"
                "  add a marker, mouse wheel to zoom, middle-drag to pan.\n\n"
                "Shot Lane: Click a block to select that shot,\n"
                "  double-click to open Shot Settings.\n\n"
                "Clips:\n"
                "  \u2022 Drag body \u2014 Move in time (ripple editing).\n"
                "  \u2022 Drag edge \u2014 Resize (scales keyframes).\n"
                "  \u2022 Shift+drag \u2014 Move boundaries only; keyframes\n"
                "    stay in place. Useful for re-framing.\n"
                "  \u2022 Ctrl while dragging \u2014 Per-frame snap override.\n"
                "  \u2022 Right-click \u2014 Lock/Unlock, Rename, Delete Key.\n"
                "  All clip edits are undoable (Ctrl+Z).\n\n"
                "Tracks: Double-click a header to expand per-attribute\n"
                "  sub-rows (diamond = spline, square = stepped).\n"
                "  Right-click \u2014 Hide, Delete, Reveal in Outliner.\n\n"
                "Gaps: Drag gap body to slide adjacent shots, drag an\n"
                "  edge to resize one shot. Right-click to Lock/Unlock\n"
                "  (locked gaps survive respace operations).\n\n"
                "Range Highlight: Drag edges or body of the active-shot\n"
                "  overlay to resize or move it (ripple downstream).\n\n"
                "Markers: M or double-click ruler to add. Drag to move.\n"
                "  Right-click to edit note, color, or style.\n\n"
                "Audio: Auto-discovered from Maya audio nodes. Displays\n"
                "  green clips with waveform. Read-only.\n\n"
                "Keyboard:\n"
                "  \u2190/\u2192 prev/next key \u2022 Shift+\u2190/\u2192 step \u00b11 frame\n"
                "  Home/End start/end \u2022 F frame shot \u2022 M add marker\n"
                "  Ctrl+Z undo \u2022 Ctrl+Shift+Z redo \u2022 Del delete keys"
            ),
        )

    def btn_colors(self):
        """Open the attribute color configuration dialog."""
        from qtpy import QtWidgets
        from uitk.widgets.mixins.settings_manager import SettingsManager

        widget = self.controller._get_sequencer_widget()

        # Collect active attributes from all clips in the current widget
        active_attrs = set()
        if widget:
            for clip in widget._clips.values():
                for attr in clip.data.get("attributes", []):
                    active_attrs.add(attr)

        color_settings = SettingsManager(namespace=AttributeColorDialog._SETTINGS_NS)
        dlg = AttributeColorDialog(
            defaults=dict(_DEFAULT_ATTRIBUTE_COLORS),
            common_attrs=list(_COMMON_ATTRIBUTES),
            active_attrs=sorted(active_attrs),
            settings=color_settings,
            parent=widget or self.ui,
        )

        def _apply(cmap):
            if widget:
                widget.attribute_colors = cmap
            # Invalidate cached map so the next rebuild reloads it.
            self.controller._color_map_cache = None

        dlg.colors_changed.connect(_apply)
        dlg.exec_()

    def cmb_shot(self, index):
        """Handle direct combobox selection of a shot or marker."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None or index < 0:
            return
        if self.controller._cmb_mode == "markers":
            # In markers mode, navigate playhead to the marker time
            marker_time = cmb.itemData(index)
            if marker_time is not None:
                widget = self.controller._get_sequencer_widget()
                if widget:
                    widget.set_playhead(marker_time)
                    widget.playhead_moved.emit(marker_time)
            return
        shot_id = cmb.itemData(index)
        if shot_id is None:
            return
        self.controller._shifted_out_keys.clear()
        self.controller.select_shot(shot_id)
        store = self.controller.sequencer.store if self.controller.sequencer else None
        do_frame = store.frame_on_shot_change if store else False
        self.controller._sync_to_widget(frame=do_frame)
        self.controller._update_shot_nav_state()

    def spn_snap(self, value):
        """Set the snap interval on the sequencer widget."""
        widget = self.controller._get_sequencer_widget()
        if widget is None:
            return
        widget.snap_interval = float(value)

    def btn_shortcuts(self):
        """Open the sequencer shortcut editor."""
        widget = self.controller._get_sequencer_widget()
        if widget is not None:
            widget._shortcut_mgr.show_editor(parent=widget, title="Sequencer Shortcuts")

    def btn_shot_settings(self):
        """Open the shared shots settings panel."""
        self.sb.handlers.marking_menu.show("shots")
