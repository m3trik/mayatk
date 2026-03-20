# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shot Sequencer UI.

Provides ``ShotSequencerSlots`` — bridges the generic
:class:`~uitk.widgets.sequencer._sequencer.SequencerWidget` to the
Maya-specific :class:`~mayatk.anim_utils.shots.shot_sequencer._shot_sequencer.ShotSequencer`.
"""
from collections import defaultdict
from typing import Optional, List

try:
    import pymel.core as pm
    import maya.api.OpenMaya as om2
except ImportError:
    pm = None
    om2 = None

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


class ShotSequencerController(ptk.LoggingMixin):
    """Business logic controller bridging SequencerWidget ↔ ShotSequencer."""

    def __init__(self, slots_instance, log_level="DEBUG"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self.sequencer: Optional[ShotSequencer] = None
        self._audio_mgr = AudioTrackManager()
        self._undo_callback_ids: List[int] = []
        self._time_change_job: Optional[int] = None
        self._syncing = False
        self._syncing_playhead = False
        self._store_listener_bound = False
        self._shot_display_mode: str = "current"  # "current" | "adjacent" | "all"
        self._segment_cache: dict = {}  # shot_id → segments list
        self._shot_undo_stack: list = []  # shot-state snapshots for undo
        self._shifted_out_keys: dict = {}  # obj_name → {time, …} shift-moved out
        self._prev_action = None  # OptionBox action for prev shot
        self._next_action = None  # OptionBox action for next shot
        self._view_mode_action = None  # OptionBox action for view mode cycle
        self._markers_mode_action = None  # OptionBox action shots↔markers
        self._cmb_mode: str = "shots"  # "shots" or "markers"
        self._cmb_label = None  # QLabel next to cmb_shot for mode text
        self._register_maya_undo_callbacks()
        self._register_time_change_job()
        self._bind_store_listener()
        self.logger.debug("ShotSequencerController initialized.")

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

    def _on_store_event(self, event: str, payload=None) -> None:
        """React to ShotStore mutations from any source (e.g. manifest build)."""
        if self._syncing or self.sequencer is None:
            return
        self._segment_cache.clear()
        # Refresh combobox and widget when shots change externally
        self._sync_combobox()
        self._sync_to_widget()
        # Emit widget-level signals for any external consumers
        widget = self._get_sequencer_widget()
        if widget is not None and hasattr(widget, "shots_changed"):
            widget.shots_changed.emit()
            widget.app_event.emit(event, payload)

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
        if self._time_change_job is not None:
            try:
                if pm.scriptJob(exists=self._time_change_job):
                    pm.scriptJob(kill=self._time_change_job, force=True)
            except Exception:
                pass
            self._time_change_job = None

    def _on_maya_undo(self, *_args) -> None:
        """Refresh the widget when Maya's undo/redo fires."""
        if self._syncing:
            return
        self._restore_shot_state()
        self._segment_cache.clear()
        self._sync_to_widget()

    # ---- Maya time-change scriptJob --------------------------------------

    def _register_time_change_job(self) -> None:
        """Create a scriptJob that syncs Maya's current time to the widget playhead."""
        if pm is None:
            return
        # Find a Maya-parented UI control so the job dies with the window.
        ui_parent = None
        try:
            widget = self._get_sequencer_widget()
            if widget:
                from uitk.widgets.mainWindow import MainWindow

                win = widget.window()
                if isinstance(win, MainWindow):
                    ui_parent = win.objectName()
        except Exception:
            pass

        kwargs = {"event": ["timeChanged", self._on_time_changed]}
        if ui_parent:
            kwargs["parent"] = ui_parent
        self._time_change_job = pm.scriptJob(**kwargs)

    def _on_time_changed(self) -> None:
        """Update the sequencer playhead when Maya's time changes externally."""
        if self._syncing_playhead:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        widget.set_playhead(pm.currentTime(q=True))

    # ---- shot selection -------------------------------------------------

    def select_shot(self, shot_id: int) -> None:
        """Set Maya's playback range to the shot and select its objects."""
        if self.sequencer is None:
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return
        pm.playbackOptions(min=shot.start, max=shot.end)
        import maya.cmds as cmds

        long_names = []
        for o in shot.objects:
            resolved = cmds.ls(o, long=True)
            if resolved:
                long_names.extend(resolved)
        if long_names:
            pm.select(long_names)
        else:
            pm.select(clear=True)

    def _sync_combobox(self) -> None:
        """Populate the shot combobox and update prev/next action state."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return

        old_sid = self.active_shot_id

        cmb.blockSignals(True)
        cmb.clear()

        if self._cmb_mode == "markers":
            # Populate with scene markers from the sequencer widget
            widget = self._get_sequencer_widget()
            if widget:
                for md in sorted(widget.markers(), key=lambda m: m.time):
                    label = f"@ {md.time:.0f}"
                    if md.note:
                        label += f"  {md.note}"
                    cmb.addItem(label, md.time)
            cmb.blockSignals(False)
            self._update_shot_nav_state()
            return

        if self.sequencer is None:
            cmb.blockSignals(False)
            return
        for shot in self.sequencer.sorted_shots():
            label = f"{shot.name}  [{shot.start:.0f}-{shot.end:.0f}]"
            if shot.description:
                label += f"  {shot.description}"
            cmb.addItem(label, shot.shot_id)
        # Restore previous selection
        if old_sid is not None:
            for i in range(cmb.count()):
                if cmb.itemData(i) == old_sid:
                    cmb.setCurrentIndex(i)
                    break
        cmb.blockSignals(False)
        self._update_shot_nav_state()

    def _update_shot_nav_state(self) -> None:
        """Enable/disable prev/next option box actions based on combobox index."""
        cmb = getattr(self.ui, "cmb_shot", None)
        idx = cmb.currentIndex() if cmb is not None else 0
        count = cmb.count() if cmb is not None else 0
        if self._prev_action is not None:
            self._prev_action.widget.setEnabled(idx > 0)
        if self._next_action is not None:
            self._next_action.widget.setEnabled(idx < count - 1)

    def _navigate_shot(self, delta: int) -> None:
        """Move to the previous (-1) or next (+1) shot."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return
        new_idx = cmb.currentIndex() + delta
        if new_idx < 0 or new_idx >= cmb.count():
            return
        cmb.setCurrentIndex(new_idx)
        shot_id = cmb.itemData(new_idx)
        self._shifted_out_keys.clear()
        self.select_shot(shot_id)
        self._sync_to_widget(frame=self._shot_display_mode == "current")
        self._update_shot_nav_state()

    def _set_view_mode(self, mode: str) -> None:
        """Set the shot display mode and rebuild the widget."""
        self._shot_display_mode = mode
        self._sync_to_widget()

    def _set_cmb_mode(self, mode: str) -> None:
        """Toggle the combobox between shots and scene markers."""
        if mode == "markers":
            widget = self._get_sequencer_widget()
            if widget and not widget.markers():
                return  # No markers available — stay in shots mode
        self._cmb_mode = mode
        if self._cmb_label is not None:
            self._cmb_label.setText("Markers:" if mode == "markers" else "Shots:")
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
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sync_to_widget()

    def on_redo(self) -> None:
        """Handle redo_requested from the widget — delegate to Maya redo."""
        if pm is None:
            return
        self._syncing = True
        try:
            pm.redo()
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sync_to_widget()

    def on_range_highlight_changed(self, start: float, end: float) -> None:
        """Update the active shot boundaries when the range highlight is dragged.

        If both edges shifted by the same delta it's a *move* — all keys
        in the shot are shifted and downstream shots are rippled.
        Otherwise it's a boundary resize — only the shot start/end is
        updated in the store.

        Holding **Shift** decouples keys from the range: a move updates
        boundaries only, leaving keyframes in place.
        """
        if self.sequencer is None or self.active_shot_id is None:
            return

        shot = self.sequencer.shot_by_id(self.active_shot_id)
        if shot is None:
            return

        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "_shift_at_press", False)

        ds = start - shot.start
        de = end - shot.end

        self._save_shot_state()

        # Both edges moved by the same amount → translate entire shot
        if abs(ds - de) < 1e-3 and abs(ds) > 1e-3:
            self._syncing = True
            try:
                with pm.UndoChunk():
                    if shift_held:
                        # Shift: move boundaries only, keys stay in place
                        duration = shot.end - shot.start
                        self.sequencer.store.update_shot(
                            self.active_shot_id, start=start, end=start + duration
                        )
                    else:
                        self.sequencer.move_shot(self.active_shot_id, start)
            finally:
                self._syncing = False
            self._sync_to_widget()
            self._sync_combobox()
            return

        # Edge resize
        self._syncing = True
        try:
            with pm.UndoChunk():
                if shift_held:
                    # Shift: move boundaries only, keys stay in place
                    self.sequencer.store.update_shot(
                        self.active_shot_id, start=start, end=end
                    )
                else:
                    # Scale keys to match the new range
                    self.sequencer.resize_shot(self.active_shot_id, start, end)
        finally:
            self._syncing = False
        self._sync_to_widget()
        self._sync_combobox()

    def on_gap_resized(self, original_next_start: float, new_next_start: float) -> None:
        """Handle a gap overlay being dragged to resize.

        Shifts the shot starting at *original_next_start* and all
        downstream shots by the same delta so that every shot keeps
        its original duration.  Only the gap size changes.
        """
        if self.sequencer is None:
            return

        delta = new_next_start - original_next_start
        if abs(delta) < 1e-3:
            return

        sorted_shots = self.sequencer.sorted_shots()

        # Find the index of the shot whose start matches the original gap end
        target_idx = None
        for i, shot in enumerate(sorted_shots):
            if abs(shot.start - original_next_start) < 1.0:
                target_idx = i
                break

        if target_idx is None:
            return

        self._save_shot_state()

        self._syncing = True
        try:
            with pm.UndoChunk():
                # Shift the target and all downstream shots by delta,
                # preserving each shot's duration.
                for shot in sorted_shots[target_idx:]:
                    duration = shot.end - shot.start
                    self.sequencer.store.update_shot(
                        shot.shot_id,
                        start=shot.start + delta,
                        end=shot.start + delta + duration,
                    )
        finally:
            self._syncing = False
        self._sync_to_widget()
        self._sync_combobox()

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
        """Push per-object animation data for the active shot into the widget.

        When the display mode is ``"adjacent"`` or ``"all"``, clips from
        non-active shots are also rendered (greyed-out, locked) and their
        ranges are shown as non-interactive overlays.

        Parameters:
            shot_id: Shot to display.  Falls back to :attr:`active_shot_id`.
        """
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return

        if shot_id is None:
            shot_id = self.active_shot_id
        if shot_id is None:
            return

        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return

        # Preserve viewport state across the rebuild
        h_scroll = widget._timeline.horizontalScrollBar().value()
        zoom = widget._timeline.pixels_per_unit

        # Remember which tracks were expanded (by name) so we can restore
        expanded_names = set()
        for tid in list(widget._expanded_tracks):
            td = widget.get_track(tid)
            if td is not None:
                expanded_names.add(td.name)

        widget.clear()
        self._sync_header_settings(widget)

        visible_shots = self._visible_shots(shot)
        segments_by_shot, all_objects = self._collect_segments(shot, visible_shots)
        active_objects = self._active_object_set(shot, segments_by_shot)
        track_ids = self._build_tracks(widget, all_objects, active_objects)
        self._build_clips(widget, shot, visible_shots, segments_by_shot, track_ids)
        self._build_audio_tracks(widget, shot)
        self._restore_widget_state(
            widget,
            shot,
            visible_shots,
            expanded_names,
            h_scroll,
            zoom,
            frame,
        )

    # ---- _sync_to_widget helpers -----------------------------------------

    def _sync_header_settings(self, widget) -> None:
        """Push header spinbox values and attribute colors to the widget."""
        spn_snap = getattr(self.ui, "spn_snap", None)
        if spn_snap is not None:
            widget.snap_interval = float(spn_snap.value())
        spn_gap = getattr(self.ui, "spn_gap", None)
        if spn_gap is not None:
            stored_gap = self.sequencer.store.gap if self.sequencer else 0
            if stored_gap:
                # Store was loaded from serialised data with an explicit gap;
                # push it into the spinbox so the UI matches.
                spn_gap.blockSignals(True)
                spn_gap.setValue(int(stored_gap))
                spn_gap.blockSignals(False)
            # When store.gap is 0, leave it alone — only explicit user
            # interaction (spn_gap slot) should write to store.gap.
            widget.gap_threshold = float(spn_gap.value())

        from uitk.widgets.mixins.settings_manager import SettingsManager

        color_settings = SettingsManager(namespace=AttributeColorDialog._SETTINGS_NS)
        color_map = dict(_DEFAULT_ATTRIBUTE_COLORS)
        for key in color_settings.keys():
            val = color_settings.value(key)
            if val:
                color_map[key] = val
        widget.attribute_colors = color_map

    def _collect_segments(self, shot, visible_shots):
        """Collect animation segments for visible shots and return
        ``(segments_by_shot, all_objects)``."""
        segments_by_shot: dict = {}
        all_objects: set = set()
        for vs in visible_shots:
            is_active_shot = vs.shot_id == shot.shot_id
            if is_active_shot or vs.shot_id not in self._segment_cache:
                segs = self.sequencer.collect_object_segments(
                    vs.shot_id, ignore_flat_keys=True
                )
                self._segment_cache[vs.shot_id] = segs
            else:
                segs = self._segment_cache[vs.shot_id]
            segments_by_shot[vs.shot_id] = segs
            all_objects.update(vs.objects)
            all_objects.update(seg["obj"] for seg in segs)

        active_segs = segments_by_shot.get(shot.shot_id, [])

        # Filter out segments for keys that were shift-moved out of this
        # shot.  Without this, a later non-shift expansion that covers
        # the shifted-out time would re-capture those keys.
        if self._shifted_out_keys:
            _EPS = 0.5
            filtered = []
            for seg in active_segs:
                obj = seg.get("obj")
                t = seg.get("start")
                if (
                    obj in self._shifted_out_keys
                    and t is not None
                    and any(abs(t - ex) < _EPS for ex in self._shifted_out_keys[obj])
                ):
                    self.logger.debug(
                        "[SYNC] excluding shift-moved-out segment: obj=%s time=%s",
                        obj,
                        t,
                    )
                    continue
                filtered.append(seg)
            active_segs = filtered
            segments_by_shot[shot.shot_id] = active_segs

        self.logger.debug(
            "[SYNC] shot=%s range=(%s,%s) total_segments=%s objects=%s",
            shot.shot_id,
            shot.start,
            shot.end,
            len(active_segs),
            sorted(all_objects),
        )
        for seg in active_segs:
            self.logger.debug(
                "[SYNC]   obj=%s start=%s end=%s dur=%s stepped=%s attr=%s",
                seg.get("obj"),
                seg.get("start"),
                seg.get("end"),
                seg.get("duration"),
                seg.get("is_stepped"),
                seg.get("attr"),
            )
        return segments_by_shot, all_objects

    @staticmethod
    def _active_object_set(shot, segments_by_shot) -> set:
        """Return the set of objects that belong to the active shot."""
        active_objects = set(shot.objects)
        active_objects.update(
            seg["obj"] for seg in segments_by_shot.get(shot.shot_id, [])
        )
        return active_objects

    def _build_tracks(self, widget, all_objects, active_objects) -> dict:
        """Create one track per unique object and return ``{obj_name: track_id}``."""
        import maya.cmds as cmds

        node_icons_cls = self._try_load_maya_icons()
        track_ids: dict = {}
        sorted_active = sorted(o for o in all_objects if o in active_objects)
        sorted_inactive = sorted(o for o in all_objects if o not in active_objects)
        _NOT_FOUND_COLOR = "#E0A0A0"
        for obj_name in sorted_active + sorted_inactive:
            if self.sequencer.is_object_hidden(obj_name):
                continue
            exists = cmds.objExists(obj_name)
            in_active = obj_name in active_objects
            icon = node_icons_cls.get_icon(obj_name) if node_icons_cls else None
            if not exists and icon is None:
                from uitk.widgets.mixins.icon_manager import IconManager

                icon = IconManager.get("close", size=(16, 16), color=_NOT_FOUND_COLOR)
            tid = widget.add_track(
                obj_name.split("|")[-1],
                icon=icon,
                dimmed=not in_active or not exists,
                italic=not in_active and exists,
            )
            track_ids[obj_name] = tid
        return track_ids

    def _build_clips(self, widget, shot, visible_shots, segments_by_shot, track_ids):
        """Add animation and stepped clips for each visible shot."""
        _SCENE_DISCOVERED_COLOR = "#8BAACC"
        for vs in visible_shots:
            is_active = vs.shot_id == shot.shot_id
            segs = segments_by_shot[vs.shot_id]
            csv_objs = set(vs.metadata.get("csv_objects", []))

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
                if csv_objs and obj_name not in csv_objs:
                    extra.setdefault("color", _SCENE_DISCOVERED_COLOR)

                if span_segs:
                    for seg in span_segs:
                        s = seg["start"]
                        e = seg["end"]
                        attrs = self._extract_attributes([seg])
                        widget.add_clip(
                            track_id=tid,
                            start=s,
                            duration=e - s,
                            label="" if is_active else vs.name,
                            shot_id=vs.shot_id,
                            obj=obj_name,
                            orig_start=s,
                            orig_end=e,
                            attributes=attrs,
                            **extra,
                        )

                for seg in stepped_segs:
                    attrs = self._extract_attributes([seg])
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
            track_id = widget.add_track(f"\u266b {short_name}", icon=icon)
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

    def _restore_widget_state(
        self,
        widget,
        shot,
        visible_shots,
        expanded_names,
        h_scroll,
        zoom,
        frame,
    ) -> None:
        """Restore playhead, markers, overlays, zoom, and track expansion."""
        current_time = pm.currentTime(q=True) if pm else shot.start
        widget.set_playhead(current_time)
        widget.set_hidden_tracks(sorted(self.sequencer.hidden_objects))

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

        for vs in visible_shots:
            if vs.shot_id != shot.shot_id:
                widget.add_range_overlay(vs.start, vs.end)

        # Gap overlays only for gaps that border a visible shot.
        visible_ids = {vs.shot_id for vs in visible_shots}
        all_sorted = sorted(self.sequencer.sorted_shots(), key=lambda s: s.start)
        for i in range(len(all_sorted) - 1):
            left = all_sorted[i]
            right = all_sorted[i + 1]
            gap_start = left.end
            gap_end = right.start
            if gap_end - gap_start > 0.5:
                if left.shot_id in visible_ids or right.shot_id in visible_ids:
                    widget.add_gap_overlay(gap_start, gap_end)

        widget.set_range_highlight(shot.start, shot.end)

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
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                continue
            obj = clip.data.get("obj")
            if obj:
                full = self._resolve_full_name(obj)
                if pm.objExists(full):
                    resolved.append(full)
        self._select_and_show(resolved)

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
        if not track_names or pm is None:
            return

        menu.addSeparator()
        menu.addAction(
            "Attribute Spreadsheet",
            lambda names=list(track_names): self._open_spreadsheet(names),
        )

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
        pm.select(objects, replace=True)
        try:
            pm.mel.eval("GraphEditor")
        except Exception:
            pass

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

    @staticmethod
    def _extract_attributes(segments) -> list:
        """Extract attribute names from animation curves in the given segments."""
        import maya.cmds as cmds

        attrs = set()
        for seg in segments:
            for curve in seg.get("curves", []):
                try:
                    crv_str = str(curve)
                    conns = (
                        cmds.listConnections(
                            crv_str, plugs=True, destination=True, source=False
                        )
                        or []
                    )
                    for conn in conns:
                        # conn is "node.attr" — extract the attr portion
                        if "." in conn:
                            attrs.add(conn.rsplit(".", 1)[-1])
                except Exception:
                    pass
        return sorted(attrs)

    def _provide_sub_rows(self, track_id, track_name):
        """Return per-attribute sub-row data for a track.

        Called by the widget's ``sub_row_provider`` protocol when a user
        double-clicks a header label to expand a track.

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
        import maya.cmds as cmds

        if not cmds.objExists(obj_name):
            return []

        from mayatk.anim_utils.segment_keys import SegmentKeys

        all_curves = (
            cmds.listConnections(obj_name, type="animCurve", s=True, d=False) or []
        )
        if not all_curves:
            return []

        widget = self._get_sequencer_widget()
        color_map = widget.attribute_colors if widget else {}
        gap = widget.gap_threshold if widget else 10.0

        # Group curves by attribute
        attr_curves: dict = defaultdict(list)
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
                        attr_curves[conn.rsplit(".", 1)[-1]].append(curve)
            except Exception:
                continue

        result = []
        for attr_name, curves in sorted(attr_curves.items()):
            # Use active-segment analysis to trim flat holds
            spans, stepped, _kf = SegmentKeys._get_active_animation_segments(
                curves, ignore_visibility_holds=True
            )

            # Filter to shot range and merge with gap threshold
            all_ranges = []
            for s, e in spans:
                cs = max(s, shot.start)
                ce = min(e, shot.end)
                if cs < ce:
                    all_ranges.append((cs, ce))
            for s, _ in stepped:
                if shot.start - 0.001 <= s <= shot.end + 0.001:
                    all_ranges.append((s, s))

            if not all_ranges:
                continue

            all_ranges.sort()

            # Merge ranges respecting gap_threshold
            merged = [list(all_ranges[0])]
            for s, e in all_ranges[1:]:
                if s <= merged[-1][1] + gap:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])

            color = color_map.get(attr_name)
            segments = []
            for s, e in merged:
                dur = e - s
                extra = {
                    "obj": obj_name,
                    "attr_name": attr_name,
                    "shot_id": shot_id,
                    "orig_start": s,
                    "orig_end": e,
                }
                # Zero-duration: lock it
                if dur < 1e-6:
                    extra["is_stepped"] = True
                    extra["stepped_key_time"] = s
                segments.append((s, dur, attr_name, color, extra))
            result.append((attr_name, segments))

        return result

    # ---- signal handlers ------------------------------------------------

    def on_marker_added(self, marker_id: int, time: float) -> None:
        """Persist a newly added marker."""
        if self.sequencer is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        md = widget.get_marker(marker_id)
        if md is None:
            return
        self.sequencer.markers.append(
            {
                "time": md.time,
                "note": md.note,
                "color": md.color,
                "draggable": md.draggable,
                "style": md.style,
                "line_style": md.line_style,
                "opacity": md.opacity,
            }
        )

    def on_marker_moved(self, marker_id: int, new_time: float) -> None:
        """Update persisted marker time."""
        self._rebuild_markers_store()

    def on_marker_changed(self, marker_id: int) -> None:
        """Update persisted marker note/color."""
        self._rebuild_markers_store()

    def on_marker_removed(self, marker_id: int) -> None:
        """Remove marker from persistent store."""
        self._rebuild_markers_store()

    def _rebuild_markers_store(self) -> None:
        """Rebuild the sequencer's markers list from the widget's markers."""
        if self.sequencer is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        self.sequencer.markers = [
            {
                "time": md.time,
                "note": md.note,
                "color": md.color,
                "draggable": md.draggable,
                "style": md.style,
                "line_style": md.line_style,
                "opacity": md.opacity,
            }
            for md in widget.markers()
        ]

    def on_clip_resized(
        self, clip_id: int, new_start: float, new_duration: float
    ) -> None:
        """Handle clip resize — scale only this object's keys and ripple downstream.

        Audio clips are not resizable.  Only the specific animation
        object's keyframes are scaled; other objects in the same shot
        are untouched.  Downstream shots shift to preserve the gap.
        """
        if self.sequencer is None:
            return
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return

        # Audio clips don't support resize — early return (no rebuild)
        if clip.data.get("is_audio"):
            return

        shot_id = clip.data.get("shot_id")
        obj_name = clip.data.get("obj")
        if shot_id is None or obj_name is None:
            return

        orig_start = clip.data.get("orig_start")
        orig_end = clip.data.get("orig_end")
        if orig_start is None or orig_end is None:
            return

        self._save_shot_state()
        new_end = new_start + new_duration
        with pm.UndoChunk():
            self.sequencer.resize_object(
                shot_id, obj_name, orig_start, orig_end, new_start, new_end
            )
        self._sync_to_widget()

    def _apply_clip_move(self, clip_id: int, new_start: float) -> bool:
        """Move a single clip's keys without rebuilding the widget.

        Returns True if a widget sync is needed afterward.
        """
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return False

        # Audio clip move
        if clip.data.get("is_audio"):
            source = clip.data.get("audio_source", "dg")
            if source == "event":
                locator = clip.data.get("audio_node")
                old_frame = clip.data.get("event_key_frame")
                if locator and old_frame is not None:
                    AudioTrackManager.move_event_key(locator, old_frame, new_start)
                    clip.data["event_key_frame"] = new_start
            else:
                audio_node = clip.data.get("audio_node")
                if audio_node:
                    AudioTrackManager.set_audio_offset(audio_node, new_start)
            clip.data["orig_start"] = new_start
            self._audio_mgr.invalidate()
            return True

        # Stepped key clip
        if clip.data.get("is_stepped"):
            obj_name = clip.data.get("obj")
            old_time = clip.data.get("stepped_key_time")
            attr_name = clip.data.get("attr_name")
            self.logger.debug(
                "[STEPPED MOVE] obj=%s old_time=%s new_start=%s attr=%s",
                obj_name,
                old_time,
                new_start,
                attr_name,
            )
            if obj_name and old_time is not None and pm.objExists(obj_name):
                import maya.cmds as cmds

                if attr_name:
                    curves = self._curves_for_attr(obj_name, attr_name)
                    self.logger.debug("[STEPPED MOVE] attr-specific curves: %s", curves)
                else:
                    # Only move curves that actually have a stepped key at
                    # old_time.  Without this filter a visibility-key drag
                    # would also shift translate/rotate keys at the same
                    # frame, corrupting smooth animation.
                    all_curves = (
                        cmds.listConnections(
                            obj_name, type="animCurve", s=True, d=False
                        )
                        or []
                    )
                    all_curves = list(set(all_curves))
                    self.logger.debug(
                        "[STEPPED MOVE] all_curves on %s: %s",
                        obj_name,
                        all_curves,
                    )
                    curves = []
                    _eps = 1e-3
                    for crv in all_curves:
                        kt = cmds.keyframe(
                            crv, q=True, time=(old_time - _eps, old_time + _eps)
                        )
                        if not kt:
                            self.logger.debug(
                                "[STEPPED MOVE]   %s: no key at %s", crv, old_time
                            )
                            continue
                        ot = cmds.keyTangent(
                            crv,
                            q=True,
                            time=(old_time - _eps, old_time + _eps),
                            outTangentType=True,
                        )
                        conns = (
                            cmds.listConnections(crv, plugs=True, d=True, s=False) or []
                        )
                        self.logger.debug(
                            "[STEPPED MOVE]   %s -> %s: key_times=%s tangent=%s",
                            crv,
                            conns,
                            kt,
                            ot,
                        )
                        if ot and ot[0] in ("step", "stepnext"):
                            curves.append(crv)
                            self.logger.debug("[STEPPED MOVE]   -> INCLUDED (stepped)")
                        else:
                            self.logger.debug(
                                "[STEPPED MOVE]   -> SKIPPED (not stepped)"
                            )
                delta = new_start - old_time
                self.logger.debug(
                    "[STEPPED MOVE] delta=%s curves_to_move=%s",
                    delta,
                    curves,
                )
                if abs(delta) > 1e-6 and curves:
                    # Move each key via delete-and-recreate to avoid
                    # shift_curves two-pass failures where Maya silently
                    # misplaces keys at large temp offsets.
                    for crv in curves:
                        vals = cmds.keyframe(
                            crv,
                            q=True,
                            time=(old_time - _eps, old_time + _eps),
                            valueChange=True,
                        )
                        in_tan = cmds.keyTangent(
                            crv,
                            q=True,
                            time=(old_time - _eps, old_time + _eps),
                            inTangentType=True,
                        )
                        out_tan = cmds.keyTangent(
                            crv,
                            q=True,
                            time=(old_time - _eps, old_time + _eps),
                            outTangentType=True,
                        )
                        if not vals:
                            self.logger.debug(
                                "[STEPPED MOVE] %s: no value at %s — skip",
                                crv,
                                old_time,
                            )
                            continue
                        val = vals[0]
                        itt = in_tan[0] if in_tan else "step"
                        ott = out_tan[0] if out_tan else "step"
                        self.logger.debug(
                            "[STEPPED MOVE] %s: delete key at %s "
                            "(val=%s itt=%s ott=%s) → recreate at %s",
                            crv,
                            old_time,
                            val,
                            itt,
                            ott,
                            new_start,
                        )
                        cmds.cutKey(
                            crv,
                            time=(old_time - _eps, old_time + _eps),
                            clear=True,
                        )
                        cmds.setKeyframe(
                            crv,
                            time=new_start,
                            value=val,
                        )
                        cmds.keyTangent(
                            crv,
                            time=(new_start, new_start),
                            inTangentType=itt,
                            outTangentType=ott,
                        )
                clip.data["stepped_key_time"] = new_start
                clip.data["orig_start"] = new_start
                self._expand_shot_for_clip(clip, new_start, new_start)
                # Track keys shift-moved outside the shot so later
                # expansions from other objects don't recapture them.
                self._track_shifted_out_key(clip, obj_name, new_start)
            else:
                self.logger.debug(
                    "[STEPPED MOVE] skipped: exists=%s old_time=%s",
                    pm.objExists(obj_name) if obj_name else False,
                    old_time,
                )
            return True

        # Sub-row attribute clip move
        attr_name = clip.data.get("attr_name")
        if attr_name:
            obj_name = clip.data.get("obj")
            orig_start = clip.data.get("orig_start")
            orig_end = clip.data.get("orig_end")
            if not obj_name or orig_start is None or orig_end is None:
                return False
            if not pm.objExists(obj_name):
                return False
            delta = new_start - orig_start
            if abs(delta) < 1e-6:
                return False
            curves = self._curves_for_attr(obj_name, attr_name)
            if curves:
                from mayatk.anim_utils.segment_keys import SegmentKeys

                SegmentKeys.shift_curves(
                    curves,
                    delta,
                    time_range=(orig_start, orig_end),
                    remove_flat_at_dest=False,
                )
            new_end = new_start + (orig_end - orig_start)
            self._expand_shot_for_clip(clip, new_start, new_end)
            return True

        # Animation clip move — per-object within a shot
        if self.sequencer is None:
            return False

        shot_id = clip.data.get("shot_id")
        obj_name = clip.data.get("obj")
        orig_start = clip.data.get("orig_start")
        orig_end = clip.data.get("orig_end")
        if shot_id is None or obj_name is None:
            return False
        if orig_start is None or orig_end is None:
            return False

        delta = new_start - orig_start
        if abs(delta) < 1e-6:
            return False

        shot = self.sequencer.shot_by_id(shot_id)
        self.logger.debug(
            "[ANIM MOVE] obj=%s orig=(%s,%s) new_start=%s delta=%s "
            "shot=%s range=(%s,%s) shift=%s",
            obj_name,
            orig_start,
            orig_end,
            new_start,
            delta,
            shot_id,
            shot.start if shot else "?",
            shot.end if shot else "?",
            getattr(widget, "_shift_at_press", False),
        )

        shift_held = getattr(widget, "_shift_at_press", False)

        if shift_held:
            # Shift held — move keys freely without changing shot boundaries.
            self.sequencer.move_object_keys(obj_name, orig_start, orig_end, new_start)
        else:
            self.sequencer.move_object_in_shot(
                shot_id, obj_name, orig_start, orig_end, new_start
            )

        # Log post-move shot range (may have expanded)
        shot_after = self.sequencer.shot_by_id(shot_id)
        if shot_after:
            self.logger.debug(
                "[ANIM MOVE] post-move shot range=(%s,%s)",
                shot_after.start,
                shot_after.end,
            )
        return True

    @staticmethod
    def _curves_for_attr(obj_name, attr_name):
        """Return anim curves connected to a specific attribute on an object."""
        try:
            plug = pm.PyNode(f"{obj_name}.{attr_name}")
            return pm.listConnections(plug, type="animCurve", s=True, d=False) or []
        except Exception:
            return []

    def _track_shifted_out_key(self, clip, obj_name: str, new_time: float) -> None:
        """Record or clear a shift-moved-out key for segment filtering.

        When shift is held and the key lands outside the shot range,
        the (obj, time) pair is recorded so ``_sync_to_widget`` can
        exclude it even if the shot later expands to cover that time.
        When shift is NOT held (normal move), any prior exclusion for
        this object is cleared because the user explicitly placed the
        key inside the shot.
        """
        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "_shift_at_press", False) if widget else False
        shot_id = clip.data.get("shot_id")
        shot = (
            self.sequencer.shot_by_id(shot_id)
            if self.sequencer and shot_id is not None
            else None
        )
        if not shift_held:
            # Normal move — clear any prior exclusion for this object
            self._shifted_out_keys.pop(obj_name, None)
            return
        if shot is None:
            return
        if new_time < shot.start or new_time > shot.end:
            self._shifted_out_keys.setdefault(obj_name, set()).add(new_time)
            self.logger.debug(
                "[SHIFT-OUT] recorded exclusion obj=%s time=%s", obj_name, new_time
            )

    def _expand_shot_for_clip(self, clip, new_start: float, new_end: float) -> None:
        """Grow the shot if the clip's new range exceeds shot boundaries.

        Skipped when shift is held — shift means "move freely across shot
        boundaries without changing them".
        """
        widget = self._get_sequencer_widget()
        if getattr(widget, "_shift_at_press", False):
            self.logger.debug("[EXPAND] skipped — shift held")
            return
        if self.sequencer is None:
            self.logger.debug("[EXPAND] skipped — no sequencer")
            return
        shot_id = clip.data.get("shot_id")
        if shot_id is None:
            self.logger.debug("[EXPAND] skipped — no shot_id in clip data")
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            self.logger.debug(
                "[EXPAND] skipped — shot_by_id(%s) returned None", shot_id
            )
            return
        prior_start = shot.start
        prior_end = shot.end
        expanded_start = min(shot.start, new_start)
        expanded_end = max(shot.end, new_end)
        if expanded_start != prior_start or expanded_end != prior_end:
            self.sequencer.store.update_shot(
                shot_id, start=expanded_start, end=expanded_end
            )
        self.logger.debug(
            "[EXPAND] shot=%s prior=(%s,%s) new_clip=(%s,%s) result=(%s,%s)",
            shot_id,
            prior_start,
            prior_end,
            new_start,
            new_end,
            shot.start,
            shot.end,
        )

    def on_clip_moved(self, clip_id: int, new_start: float) -> None:
        """Handle clip move — routes to audio or shot-level logic."""
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        self.logger.debug(
            "[CLIP MOVED] clip_id=%s new_start=%s clip_data=%s",
            clip_id,
            new_start,
            dict(clip.data) if clip else None,
        )
        # Capture the shot_id from the clip BEFORE the move so that the
        # subsequent sync always targets the correct shot, regardless of
        # any combobox/store-event interference.
        shot_id = clip.data.get("shot_id") if clip else None
        self._save_shot_state()
        with pm.UndoChunk():
            if self._apply_clip_move(clip_id, new_start):
                self.logger.debug(
                    "[CLIP MOVED] sync triggered — cache_keys=%s shifted_out=%s",
                    list(self._segment_cache.keys()),
                    {k: sorted(v) for k, v in self._shifted_out_keys.items()},
                )
                self._sync_to_widget(shot_id=shot_id)
                self._sync_combobox()

    def on_clips_batch_moved(self, moves) -> None:
        """Handle a batch of clip moves (group drag), syncing once at the end."""
        # Capture the shot_id from the first clip so the sync targets
        # the correct shot after combobox-resetting store events.
        shot_id = None
        if moves:
            widget = self._get_sequencer_widget()
            if widget:
                clip = widget.get_clip(moves[0][0])
                if clip:
                    shot_id = clip.data.get("shot_id")
        self._save_shot_state()
        with pm.UndoChunk():
            needs_sync = False
            for clip_id, new_start in moves:
                if self._apply_clip_move(clip_id, new_start):
                    needs_sync = True
            if needs_sync:
                self._sync_to_widget(shot_id=shot_id)
                self._sync_combobox()

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
            sequencer.clip_resized.connect(self.controller.on_clip_resized)
            sequencer.clip_moved.connect(self.controller.on_clip_moved)
            sequencer.clips_batch_moved.connect(self.controller.on_clips_batch_moved)
            sequencer.playhead_moved.connect(self.controller.on_playhead_moved)
            sequencer.track_hidden.connect(self.controller.hide_track)
            sequencer.track_shown.connect(self.controller.show_track)
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
            sequencer.range_highlight_changed.connect(
                self.controller.on_range_highlight_changed
            )
            sequencer.gap_resized.connect(self.controller.on_gap_resized)

        # Setup shot navigation on the combobox
        self._setup_shot_nav()

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
        )
        next_opt = ActionOption(
            wrapped_widget=cmb,
            callback=lambda: self.controller._navigate_shot(1),
            icon="chevron_right",
            tooltip="Next Shot",
        )

        # View mode cycle: Current → Adjacent → All
        _VIEW_STATES = [
            {
                "icon": "target",
                "tooltip": "Show: Current Only",
                "callback": lambda: self.controller._set_view_mode("current"),
            },
            {
                "icon": "expand_plus",
                "tooltip": "Show: Adjacent",
                "callback": lambda: self.controller._set_view_mode("adjacent"),
            },
            {
                "icon": "grid",
                "tooltip": "Show: All Shots",
                "callback": lambda: self.controller._set_view_mode("all"),
            },
        ]
        view_opt = ActionOption(
            wrapped_widget=cmb,
            states=_VIEW_STATES,
        )

        # Shots ↔ Markers toggle
        _MARKER_STATES = [
            {
                "icon": "camera",
                "tooltip": "Showing Shots (click for Markers)",
                "callback": lambda: self.controller._set_cmb_mode("markers"),
            },
            {
                "icon": "locator",
                "tooltip": "Showing Markers (click for Shots)",
                "callback": lambda: self.controller._set_cmb_mode("shots"),
            },
        ]
        markers_opt = ActionOption(
            wrapped_widget=cmb,
            states=_MARKER_STATES,
        )

        cmb.option_box.set_order(["action"])
        cmb.option_box.add_option(prev_opt)
        cmb.option_box.add_option(next_opt)
        cmb.option_box.add_option(view_opt)
        cmb.option_box.add_option(markers_opt)

        # "+" button — create a new shot via dialog
        add_opt = ActionOption(
            wrapped_widget=cmb,
            callback=self._new_shot_dialog,
            icon="add",
            tooltip="New Shot",
        )
        cmb.option_box.add_option(add_opt)

        self.controller._prev_action = prev_opt
        self.controller._next_action = next_opt
        self.controller._view_mode_action = view_opt
        self.controller._markers_mode_action = markers_opt

        # Install right-click context menu on the combobox
        from qtpy import QtCore

        cmb.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        cmb.customContextMenuRequested.connect(self._cmb_context_menu)

        # Reference the label from the .ui file
        lbl = getattr(self.ui, "lbl_cmb_mode", None)
        if lbl is not None:
            from qtpy import QtCore

            lbl.setFixedWidth(lbl.fontMetrics().horizontalAdvance("Markers:") + 4)
            self.controller._cmb_label = lbl

    # ---- shot CRUD helpers -----------------------------------------------

    def _new_shot_dialog(self, start: float = 1.0, end: float = 100.0) -> None:
        """Open the New Shot dialog and create a shot from user input."""
        if self.controller.sequencer is None:
            return
        existing = self.controller.sequencer.sorted_shots()
        idx = len(existing) + 1
        result = ShotEditDialog.show(
            parent=self.ui,
            name=f"Shot {idx}",
            start=start,
            end=end,
            title="New Shot",
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
            description=desc,
        )
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _edit_shot_dialog(self) -> None:
        """Open the Edit Shot dialog for the currently selected shot."""
        if self.controller.sequencer is None:
            return
        sid = self.controller.active_shot_id
        if sid is None:
            return
        shot = self.controller.sequencer.shot_by_id(sid)
        if shot is None:
            return
        result = ShotEditDialog.show(
            parent=self.ui,
            name=shot.name,
            start=shot.start,
            end=shot.end,
            description=shot.description,
            title="Edit Shot",
        )
        if result is None:
            return
        name, s, e, desc = result
        if e <= s:
            return
        self.controller.sequencer.store.update_shot(
            sid,
            name=name,
            start=s,
            end=e,
            description=desc,
        )
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

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
        self.controller.sequencer.store.remove_shot(sid)
        self.controller.active_shot_id = None
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _detect_next_shot(self) -> None:
        """Detect and create the next unregistered animation cluster."""
        if self.controller.sequencer is None or pm is None:
            return
        cand = self.controller.sequencer.detect_next_shot()
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
        menu.addAction("New Shot\u2026", self._new_shot_dialog)
        menu.addAction("Detect Next Shot\u2026", self._detect_next_shot)
        menu.addSeparator()

        has_shot = self.controller.active_shot_id is not None
        edit_action = menu.addAction("Edit Shot\u2026", self._edit_shot_dialog)
        edit_action.setEnabled(has_shot)
        delete_action = menu.addAction("Delete Shot", self._delete_shot)
        delete_action.setEnabled(has_shot)

        menu.exec_(cmb.mapToGlobal(pos))

    def header_init(self, widget):
        """Configure header menu."""
        widget.menu.setTitle("Shot Sequencer:")
        widget.menu.add(
            "QSpinBox",
            setMinimum=0,
            setMaximum=1000,
            setValue=1,
            setObjectName="spn_snap",
            setPrefix="Snap: ",
            setToolTip="Snap interval for clip edges when dragging or resizing (0 = free movement).",
        )
        widget.menu.add(
            "QSpinBox",
            setMinimum=0,
            setMaximum=1000,
            setValue=10,
            setObjectName="spn_gap",
            setPrefix="Gap: ",
            setToolTip="Frame gap between shots. Changing this respaces all shots on the timeline.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Attribute Colors",
            setObjectName="btn_colors",
            setToolTip="Customize the colors used to display each animated attribute in the sequencer.",
        )
        widget.menu.add(
            "QPushButton",
            setText="New Shot\u2026",
            setObjectName="btn_new_shot",
            setToolTip="Create a new shot with a custom name, range, and description.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Edit Current Shot\u2026",
            setObjectName="btn_edit_shot",
            setToolTip="Edit the name, range, or description of the currently selected shot.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Delete Current Shot",
            setObjectName="btn_delete_shot",
            setToolTip="Remove the currently selected shot from the store.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Detect Next Shot\u2026",
            setObjectName="btn_detect_next",
            setToolTip="Find the next unregistered animation cluster after existing shots.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Import from Scene",
            setObjectName="btn_import_scene",
            setToolTip="Discover animated objects in the Maya scene and create shots from their keyframe ranges.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Re-apply Behaviors",
            setObjectName="btn_reapply_behaviors",
            setToolTip="Re-apply shot behaviors (hold, loop, etc.) to all shots without changing shot data.",
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
        self.controller._sync_to_widget(
            frame=self.controller._shot_display_mode == "current"
        )
        self.controller._update_shot_nav_state()

    def spn_snap(self, value):
        """Set the snap interval on the sequencer widget."""
        widget = self.controller._get_sequencer_widget()
        if widget is None:
            return
        widget.snap_interval = float(value)

    def spn_gap(self, value):
        """Respace shots with the chosen gap and rebuild the widget."""
        widget = self.controller._get_sequencer_widget()
        if widget is None:
            return

        widget.gap_threshold = float(value)

        if self.controller.sequencer is None:
            return

        self.controller.sequencer.store.gap = float(value)
        self.controller.sequencer.respace(gap=value)
        self.controller._segment_cache.clear()
        self.controller._shifted_out_keys.clear()
        self.controller._sync_to_widget()

    def btn_new_shot(self):
        """Open the New Shot dialog (header menu entry)."""
        self._new_shot_dialog()

    def btn_edit_shot(self):
        """Open the Edit Shot dialog (header menu entry)."""
        self._edit_shot_dialog()

    def btn_delete_shot(self):
        """Delete the current shot (header menu entry)."""
        self._delete_shot()

    def btn_detect_next(self):
        """Detect and create the next unregistered shot (header menu entry)."""
        self._detect_next_shot()

    def btn_reapply_behaviors(self):
        """Re-apply behavior templates to the active shot.

        Objects with existing keyframes in the shot range are skipped
        to avoid overwriting user animation.
        """
        if self.controller.sequencer is None or pm is None:
            return
        sid = self.controller.active_shot_id
        if sid is None:
            return
        shot = self.controller.sequencer.shot_by_id(sid)
        if shot is None or shot.locked:
            return
        from mayatk.anim_utils.shots.behaviors import apply_behavior, apply_to_shots

        result = apply_to_shots(
            [shot],
            apply_fn=apply_behavior,
        )
        applied = len(result.get("applied", []))
        skipped = len(result.get("skipped", []))
        if applied or skipped:
            self.controller._sync_to_widget()

    def btn_import_scene(self):
        """Detect animation boundaries and create shots from them.

        Uses :meth:`ShotSequencer.detect_shots` to find clusters of
        animation, then defines a :class:`ShotBlock` for each cluster.
        The user can adjust boundary markers first (via *Anim Boundaries*)
        or import directly.
        """
        from qtpy import QtWidgets

        if self.controller.sequencer is None or pm is None:
            return

        widget = self.controller._get_sequencer_widget()

        # Check for existing boundary markers to use as overrides
        boundary_markers = [
            md
            for md in (widget.markers() if widget else [])
            if md.note.startswith("[boundary]")
        ]

        if boundary_markers:
            # Group boundary markers into pairs (start/end per shot name)
            pairs: dict = {}
            for md in boundary_markers:
                # note format: "[boundary] Shot N start/end"
                parts = md.note.replace("[boundary] ", "").rsplit(" ", 1)
                name = parts[0] if len(parts) > 1 else "Shot"
                role = parts[-1] if len(parts) > 1 else "start"
                pairs.setdefault(name, {})
                pairs[name][role] = md.time

            candidates = []
            for name, times in sorted(pairs.items()):
                start = times.get("start", 0)
                end = times.get("end", start + 1)
                if end <= start:
                    continue
                # Find objects with animation in this range
                objs = self.controller.sequencer._find_keyed_transforms(start, end)
                candidates.append(
                    {"name": name, "start": start, "end": end, "objects": objs}
                )
        else:
            candidates = self.controller.sequencer.detect_shots()

        if not candidates:
            pm.displayInfo("No animation boundaries detected.")
            return

        summary = "\n".join(
            f"  {c['name']}: {c['start']:.0f}–{c['end']:.0f}  ({len(c['objects'])} objects)"
            for c in candidates
        )
        reply = QtWidgets.QMessageBox.question(
            widget or self.ui,
            "Import from Scene",
            f"Create {len(candidates)} shot(s)?\n\n{summary}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        for cand in candidates:
            self.controller.sequencer.define_shot(
                name=cand["name"],
                start=cand["start"],
                end=cand["end"],
                objects=cand["objects"],
            )

        # Clean up boundary markers
        if widget and boundary_markers:
            for md in boundary_markers:
                widget.remove_marker(md.marker_id)
            self.controller._rebuild_markers_store()

        self.controller._sync_combobox()
        self.controller._sync_to_widget()
        pm.displayInfo(f"Created {len(candidates)} shot(s) from scene animation.")
