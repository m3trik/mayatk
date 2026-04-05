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
from mayatk.anim_utils.shots.shot_sequencer._audio_tracks import (
    AudioTrackManager,
)
from mayatk.anim_utils.shots.shot_sequencer._gap_manager import GapManagerMixin
from mayatk.anim_utils.shots.shot_sequencer._clip_motion import ClipMotionMixin
from mayatk.anim_utils.shots.shot_sequencer._segment_collector import (
    collect_segments,
    active_object_set,
    extract_attributes,
    build_curve_preview,
)
from mayatk.anim_utils.shots.shot_sequencer._shot_nav import ShotNavMixin
from mayatk.anim_utils.shots.shot_sequencer._marker_manager import MarkerManagerMixin
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

    def __init__(self, slots_instance, log_level="DEBUG"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self._sequencer: Optional[ShotSequencer] = None
        self._audio_mgr = AudioTrackManager()
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
        """Listen for Maya Undo/Redo events to refresh the widget."""
        if om2 is None:
            return
        for event_name in ("Undo", "Redo"):
            cb_id = om2.MEventMessage.addEventCallback(event_name, self._on_maya_undo)
            self._undo_callback_ids.append(cb_id)

    def remove_callbacks(self) -> None:
        """Remove Maya event callbacks and ShotStore listener (call on teardown)."""
        self._unbind_store_listener()
        if om2 is None:
            return
        for cb_id in self._undo_callback_ids:
            om2.MMessage.removeCallback(cb_id)
        self._undo_callback_ids.clear()
        if self._time_change_cb is not None:
            try:
                om2.MMessage.removeCallback(self._time_change_cb)
            except Exception:
                pass
            self._time_change_cb = None
        if self._keyframe_cb is not None:
            try:
                om2.MMessage.removeCallback(self._keyframe_cb)
            except Exception:
                pass
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
        if oma is None:
            return
        try:
            self._keyframe_cb = oma.MAnimMessage.addAnimKeyframeEditedCallback(
                self._on_keyframe_edited
            )
        except Exception:
            pass

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
        from mayatk.anim_utils.shots._shots import STANDARD_TRANSFORM_ATTRS

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
        if om2 is None:
            return
        self._time_change_cb = om2.MDGMessage.addTimeChangeCallback(
            self._on_time_changed
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
        else:
            act_select = None
            act_edit = None

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
        elif chosen == act_new:
            self._create_shot_one_click()
        elif chosen == act_refresh:
            self.refresh()

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
        if mode == "markers":
            widget = self._get_sequencer_widget()
            if widget and not widget.markers():
                return  # No markers available — stay in shots mode
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
            self._restore_shot_state()
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
        """
        if pm is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        clip = widget.get_clip(clip_id)
        if clip is None:
            return

        menu.addSeparator()
        act_delete = menu.addAction("Delete Key")
        act_delete.triggered.connect(lambda: self._delete_clip_keys([clip_id]))

    def on_gap_menu(self, menu, gap_start: float, gap_end: float) -> None:
        """Add domain-specific actions to a gap overlay's context menu.

        Called before ``menu.exec_`` so consumers can append actions.
        Override or extend in subclasses for custom gap menu items.
        """

    @staticmethod
    def _try_load_maya_icons():
        """Return the :class:`NodeIcons` class if Maya is available, else ``None``."""
        try:
            from mayatk.ui_utils.node_icons import NodeIcons
            import maya.cmds as cmds  # noqa: F401 — availability check
        except ImportError:
            return None
        return NodeIcons

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
        self._build_audio_tracks(widget, scene_shot)

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

        widget.clear_decorations()
        self._rebuild_decoration(widget, shot, visible_shots)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)

    def refresh(self) -> None:
        """Clear cached segments and rebuild the sequencer widget."""
        self._segment_cache.clear()
        self._sub_row_cache.clear()
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
            widget.clear()
            self._sub_row_cache.clear()
            self._sync_header_settings(widget)

            # Re-resolve any stale DAG paths (e.g. parent renamed) across
            # ALL shots before collecting segments so that global track sets
            # and segment caches never mix old and new paths.
            if self.sequencer.reconcile_all_shots():
                self._segment_cache.clear()

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
            self._build_audio_tracks(widget, shot)
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

        from uitk.widgets.mixins.settings_manager import SettingsManager

        color_settings = SettingsManager(namespace=AttributeColorDialog._SETTINGS_NS)
        color_map = dict(_DEFAULT_ATTRIBUTE_COLORS)
        for key in color_settings.keys():
            val = color_settings.value(key)
            if val:
                color_map[key] = val
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
        for obj_name in ordered:
            if self.sequencer.is_object_hidden(obj_name):
                continue
            exists = cmds.objExists(obj_name)
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

            for obj_name in sorted(set(vs.objects) | set(by_obj)):
                if self.sequencer.is_object_hidden(obj_name):
                    continue
                tid = track_ids.get(obj_name)
                if tid is None:
                    continue
                obj_segs = by_obj.get(obj_name, [])
                if not obj_segs:
                    continue

                span_segs = [seg for seg in obj_segs if not seg.get("is_stepped")]
                stepped_segs = [seg for seg in obj_segs if seg.get("is_stepped")]

                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}
                status = obj_classes.get(obj_name, "valid")
                if status != "valid":
                    pair = SHOT_PALETTE.get(status)
                    if pair is not None:
                        fg = pair[0]
                        if fg:
                            extra["status_color"] = fg

                if span_segs:
                    # Merge adjacent segments separated only by flat-key
                    # gaps so the main track shows fewer, larger clips.
                    store = self.sequencer.store if self.sequencer else None
                    gap = store.detection_threshold if store else 10.0
                    span_segs.sort(key=lambda sg: sg["start"])
                    merged: list = [
                        {
                            "start": span_segs[0]["start"],
                            "end": span_segs[0]["end"],
                            "segs": [span_segs[0]],
                        }
                    ]
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

                    # Absorb stepped segments that fall within a merged
                    # span so the main track shows one consolidated clip
                    # rather than layered stepped + span clips.
                    uncovered_stepped = []
                    for seg in stepped_segs:
                        t = seg["start"]
                        absorbed = False
                        for m in merged:
                            if m["start"] <= t <= m["end"]:
                                m["segs"].append(seg)
                                absorbed = True
                                break
                        if not absorbed:
                            uncovered_stepped.append(seg)
                    stepped_segs = uncovered_stepped

                    for m in merged:
                        s = m["start"]
                        e = m["end"]
                        attrs = extract_attributes(m["segs"])
                        clip_extra = dict(extra)
                        if is_active and attrs:
                            clip_extra["label_center"] = Attributes.abbreviate_attrs(
                                attrs
                            )
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

                for seg in stepped_segs:
                    attrs = extract_attributes([seg])
                    widget.add_clip(
                        track_id=tid,
                        start=seg["start"],
                        duration=0.0,
                        label="",
                        shot_id=vs.shot_id,
                        obj=obj_name,
                        orig_start=seg["start"],
                        orig_end=seg["start"],
                        is_stepped=True,
                        resizable_left=False,
                        resizable_right=False,
                        stepped_key_time=seg["start"],
                        attributes=attrs,
                        **extra,
                    )

    def _build_audio_tracks(self, widget, shot) -> None:
        """Add audio tracks and clips for the active shot."""
        audio_segs = self._audio_mgr.collect_all_audio_segments(
            scene_start=shot.start, scene_end=shot.end
        )
        audio_by_source: dict = defaultdict(list)
        for seg in audio_segs:
            audio_by_source[seg["node"]].append(seg)

        for source_node, segs in audio_by_source.items():
            if self.sequencer.is_object_hidden(source_node):
                continue
            short_name = source_node.rsplit("|", 1)[-1]
            node_icons_cls = self._try_load_maya_icons()
            icon = node_icons_cls.get_icon(source_node) if node_icons_cls else None
            track_id = widget.add_track(short_name, icon=icon)
            for seg in segs:
                vis_start = max(seg["start"], shot.start)
                vis_end = min(seg["end"], shot.end)
                if vis_end <= vis_start:
                    continue

                full_waveform = seg.get("waveform", [])
                full_dur = seg["end"] - seg["start"]
                if full_waveform and full_dur > 0:
                    n = len(full_waveform)
                    frac_lo = (vis_start - seg["start"]) / full_dur
                    frac_hi = (vis_end - seg["start"]) / full_dur
                    i_lo = int(frac_lo * n)
                    i_hi = max(i_lo + 1, int(frac_hi * n))
                    vis_waveform = full_waveform[i_lo:i_hi]
                else:
                    vis_waveform = full_waveform

                widget.add_clip(
                    track_id=track_id,
                    start=vis_start,
                    duration=vis_end - vis_start,
                    label=seg["label"],
                    color="#3A7D44",
                    is_audio=True,
                    audio_source=seg.get("audio_source", "dg"),
                    audio_node=seg["node"],
                    file_path=seg["file_path"],
                    waveform=vis_waveform,
                    orig_start=seg["start"],
                    event_key_frame=seg.get("event_key_frame"),
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
        """Persist clip lock toggle to the ShotBlock and propagate to siblings."""
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        clip = widget._clips.get(clip_id)
        if clip is None:
            return
        shot_id = clip.data.get("shot_id")
        if shot_id is None:
            return
        self.sequencer.store.update_shot(shot_id, locked=locked)
        # Propagate to all sibling clips belonging to the same shot
        for cid, cd in widget._clips.items():
            if cd.data.get("shot_id") == shot_id and cid != clip_id:
                widget.set_clip_locked(cid, locked)

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
        if self.sequencer is None:
            return
        store = self.sequencer.store
        act = menu.addAction("Select Members on Load")
        act.setCheckable(True)
        act.setChecked(store.select_on_load)
        act.setToolTip(
            "Select all objects belonging to the shot\n"
            "when navigating to it in the sequencer."
        )
        act.toggled.connect(self._on_select_on_load_toggled)

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

        deleted = False
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
                plug = f"{full}.{attr}"
                try:
                    pm.cutKey(plug, time=(start, end), clear=True)
                    deleted = True
                except Exception:
                    self.logger.debug(
                        "_delete_clip_keys: cutKey failed for '%s'.",
                        plug,
                        exc_info=True,
                    )

        if deleted:
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            self._sync_to_widget()

    def _delete_selected_clip_keys(self) -> None:
        """Delete keys for all marquee-selected clips."""
        widget = self._get_sequencer_widget()
        if widget is None:
            self.logger.debug("_delete_selected_clip_keys: no widget.")
            return
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
                is_stepped = seg.get("is_stepped", False)

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
                if is_stepped or dur < 1e-6:
                    extra["is_stepped"] = True
                    extra["stepped_key_time"] = s
                elif is_hold:
                    extra["is_hold"] = True
                segments.append((s, dur, attr_name, color, extra))
            result.append((attr_name, segments))

        self._sub_row_cache[cache_key] = result
        return result

    # ---- signal handlers (clip motion in _clip_motion.py) ----------------

    def on_clip_renamed(self, clip_id: int, new_label: str) -> None:
        """Handle inline rename — currently a no-op (shot clips removed)."""
        pass

    def on_playhead_moved(self, frame: float) -> None:
        """Sync the Maya playhead to the widget playhead with audio scrub."""
        self._syncing_playhead = True
        try:
            self._ensure_sound_on_timeline()
            pm.currentTime(frame, update=True)
        finally:
            self._syncing_playhead = False

    def _ensure_sound_on_timeline(self) -> None:
        """Bind the first available audio node to Maya's time slider.

        This only runs once (per session) — subsequent calls are no-ops
        unless the cached node no longer exists.
        """
        if hasattr(self, "_active_sound") and pm.objExists(self._active_sound):
            return
        clips = self._audio_mgr.find_audio_nodes()
        if not clips:
            self._active_sound = ""
            return
        node = clips[0].node_name
        try:
            slider = pm.mel.eval("$tmp = $gPlayBackSlider")
            pm.timeControl(slider, e=True, sound=node)
        except Exception:
            pass
        self._active_sound = node


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
            sequencer.zone_context_menu_requested.connect(
                self.controller.on_zone_context_menu
            )
            sequencer.shot_block_clicked.connect(self.controller.on_shot_block_clicked)
            sequencer.shot_switch_requested.connect(
                self.controller._on_shot_switch_requested
            )
            sequencer.header_menu_requested.connect(self.controller.on_header_menu)
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

        # Setup shot navigation on the combobox
        self._setup_shot_nav()

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
        """Detect and create the next unregistered animation cluster."""
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
            title="Detected Shot",
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
        menu.addAction("Detect Next Shot\u2026", self._detect_next_shot)
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
            setToolTip="Open shared shot detection, gap, and editing settings.",
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
                "  \u2022 Right-click dropdown: New Shot, Detect Next Shot\n"
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
        self.controller._sync_to_widget(frame=True)
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
