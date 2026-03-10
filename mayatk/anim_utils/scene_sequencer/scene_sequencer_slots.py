# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Scene Sequencer UI.

Provides ``SceneSequencerSlots`` — bridges the generic
:class:`~uitk.widgets.sequencer._sequencer.SequencerWidget` to the
Maya-specific :class:`~mayatk.anim_utils.scene_sequencer._scene_sequencer.SceneSequencer`.
"""
from collections import defaultdict
from typing import Optional, List

try:
    import pymel.core as pm
except ImportError:
    pass

import pythontk as ptk

from uitk.widgets.sequencer._sequencer import SequencerWidget
from mayatk.anim_utils.scene_sequencer._scene_sequencer import (
    SceneSequencer,
    SceneBlock,
    load_template,
)
from mayatk.anim_utils.scene_sequencer._audio_tracks import (
    AudioTrackManager,
)


class SceneSequencerController(ptk.LoggingMixin):
    """Business logic controller bridging SequencerWidget ↔ SceneSequencer."""

    def __init__(self, slots_instance):
        super().__init__()
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self.sequencer: Optional[SceneSequencer] = None
        self._audio_mgr = AudioTrackManager()
        self.logger.debug("SceneSequencerController initialized.")

    # ---- persistence ----------------------------------------------------

    def save(self) -> None:
        """Save the current sequencer state to the Maya scene."""
        if self.sequencer is None:
            pm.warning("No scene data to save.")
            return
        self.sequencer.save()
        pm.displayInfo("Scene Sequencer data saved.")

    def load(self) -> None:
        """Load scene sequencer state from the Maya scene."""
        seq = SceneSequencer.load()
        if seq is None:
            pm.warning("No saved Scene Sequencer data found.")
            return
        self.sequencer = seq
        self._audio_mgr.invalidate()
        self._sync_to_widget()
        self._sync_combobox()
        pm.displayInfo(f"Loaded {len(seq.scenes)} scene(s).")

    # ---- scene selection -------------------------------------------------

    def select_scene(self, scene_id: int) -> None:
        """Set Maya's playback range to the scene and select its objects."""
        if self.sequencer is None:
            return
        scene = self.sequencer.scene_by_id(scene_id)
        if scene is None:
            return
        pm.playbackOptions(min=scene.start, max=scene.end)
        existing = [o for o in scene.objects if pm.objExists(o)]
        if existing:
            pm.select(existing)
        else:
            pm.select(clear=True)

    def _sync_combobox(self) -> None:
        """Populate the scene combobox from the current sequencer."""
        cmb = getattr(self.ui, "cmb_scene", None)
        if cmb is None or self.sequencer is None:
            return
        cmb.blockSignals(True)
        cmb.clear()
        for scene in self.sequencer.sorted_scenes():
            cmb.addItem(
                f"{scene.name}  [{scene.start:.0f}-{scene.end:.0f}]", scene.scene_id
            )
        cmb.blockSignals(False)

    # ---- scene detection -------------------------------------------------

    def detect_from_selection(self, gap_threshold: float = 10.0) -> None:
        """Detect scenes from the current Maya selection and populate the widget."""
        sel = pm.selected(type="transform")
        if not sel:
            pm.warning("Select transform nodes to detect scenes.")
            return

        self.sequencer = SceneSequencer.detect_scenes(sel, gap_threshold=gap_threshold)
        self._audio_mgr.invalidate()
        self._sync_to_widget()
        self._sync_combobox()

    def detect_from_all(self, gap_threshold: float = 10.0) -> None:
        """Detect scenes from all animated transforms in the scene."""
        all_transforms = pm.ls(type="transform")
        animated = [
            t
            for t in all_transforms
            if pm.listConnections(t, type="animCurve", s=True, d=False)
        ]
        if not animated:
            pm.warning("No animated transforms found.")
            return

        self.sequencer = SceneSequencer.detect_scenes(
            animated, gap_threshold=gap_threshold
        )
        self._audio_mgr.invalidate()
        self._sync_to_widget()
        self._sync_combobox()

    # ---- define from range -----------------------------------------------

    def define_from_range(self, name: str = "") -> None:
        """Define a new scene from Maya's current playback range.

        All transforms with keyframes in that range are included automatically.
        """
        start = pm.playbackOptions(q=True, min=True)
        end = pm.playbackOptions(q=True, max=True)

        if self.sequencer is None:
            self.sequencer = SceneSequencer()

        if not name:
            idx = len(self.sequencer.scenes)
            name = f"Scene_{idx}"

        self.sequencer.define_scene(name, start, end)
        self._sync_combobox()
        self._sync_to_widget()
        pm.displayInfo(f"Defined '{name}' [{start:.0f}-{end:.0f}]")

    # ---- widget ↔ engine sync -------------------------------------------

    @property
    def active_scene_id(self) -> Optional[int]:
        """Return the scene_id currently selected in the combobox, or the
        first scene's id if nothing is selected."""
        cmb = getattr(self.ui, "cmb_scene", None)
        if cmb is not None and cmb.currentIndex() >= 0:
            sid = cmb.itemData(cmb.currentIndex())
            if sid is not None:
                return sid
        if self.sequencer and self.sequencer.scenes:
            return self.sequencer.sorted_scenes()[0].scene_id
        return None

    def _sync_to_widget(self, scene_id: Optional[int] = None) -> None:
        """Push per-object animation data for the active scene into the widget.

        Parameters:
            scene_id: Scene to display.  Falls back to :attr:`active_scene_id`.
        """
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return

        if scene_id is None:
            scene_id = self.active_scene_id
        if scene_id is None:
            return

        scene = self.sequencer.scene_by_id(scene_id)
        if scene is None:
            return

        widget.clear()

        segments = self.sequencer.collect_object_segments(scene_id)

        by_obj: dict = defaultdict(list)
        for seg in segments:
            by_obj[seg["obj"]].append(seg)

        # Ensure every object in the scene has a track, even without segments.
        # Merge all segments per object into a single clip (one color per row).
        for obj_name in sorted(set(scene.objects) | set(by_obj)):
            if self.sequencer.is_object_hidden(obj_name):
                continue
            track_id = widget.add_track(obj_name.split("|")[-1])
            obj_segs = by_obj.get(obj_name, [])
            if obj_segs:
                s = min(seg["start"] for seg in obj_segs)
                e = max(seg["end"] for seg in obj_segs)
                widget.add_clip(
                    track_id=track_id,
                    start=s,
                    duration=e - s,
                    label="",
                    scene_id=scene_id,
                    obj=obj_name,
                    orig_start=s,
                    orig_end=e,
                )

        # --- audio tracks (grouped by locator / node source) ---
        audio_segs = self._audio_mgr.collect_all_audio_segments(
            scene_start=scene.start, scene_end=scene.end
        )
        # Group segments by their source node so each locator gets one track
        audio_by_source: dict = defaultdict(list)
        for seg in audio_segs:
            audio_by_source[seg["node"]].append(seg)

        for source_node, segs in audio_by_source.items():
            short_name = source_node.rsplit("|", 1)[-1]
            track_id = widget.add_track(f"\u266B {short_name}")
            for seg in segs:
                # Clamp audio clip to the current scene range
                vis_start = max(seg["start"], scene.start)
                vis_end = min(seg["end"], scene.end)
                if vis_end <= vis_start:
                    continue
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
                    waveform=seg.get("waveform", []),
                    orig_start=seg["start"],
                    event_key_frame=seg.get("event_key_frame"),
                )

        widget.set_playhead(scene.start)

        # Inform the widget which tracks are hidden (for "show hidden" menu)
        widget.set_hidden_tracks(sorted(self.sequencer.hidden_objects))

    def hide_track(self, track_name: str) -> None:
        """Hide a track by object name, persist, and rebuild the widget."""
        if self.sequencer is None:
            return
        # Resolve short name back to the full object name stored in scenes
        full_name = self._resolve_full_name(track_name)
        self.sequencer.set_object_hidden(full_name, True)
        self.sequencer.save()
        self._sync_to_widget()

    def show_track(self, track_name: str) -> None:
        """Un-hide a track by object name, persist, and rebuild the widget."""
        if self.sequencer is None:
            return
        self.sequencer.set_object_hidden(track_name, False)
        self.sequencer.save()
        self._sync_to_widget()

    def _resolve_full_name(self, short_name: str) -> str:
        """Map a short display name back to the full DAG path in scene objects."""
        if self.sequencer is None:
            return short_name
        for scene in self.sequencer.scenes:
            for obj in scene.objects:
                if obj.split("|")[-1] == short_name:
                    return obj
        return short_name

    def _get_sequencer_widget(self):
        """Return the SequencerWidget from the UI."""
        return getattr(self.ui, "sequencer", None)

    # ---- signal handlers ------------------------------------------------

    def on_clip_resized(
        self, clip_id: int, new_start: float, new_duration: float
    ) -> None:
        """Handle clip resize — scale only this object's keys and ripple downstream.

        Audio clips are not resizable.  Only the specific animation
        object's keyframes are scaled; other objects in the same scene
        are untouched.  Downstream scenes shift to preserve the gap.
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

        scene_id = clip.data.get("scene_id")
        obj_name = clip.data.get("obj")
        if scene_id is None or obj_name is None:
            return

        orig_start = clip.data.get("orig_start")
        orig_end = clip.data.get("orig_end")
        if orig_start is None or orig_end is None:
            return

        new_end = new_start + new_duration
        self.sequencer.resize_object(
            scene_id, obj_name, orig_start, orig_end, new_start, new_end
        )
        self._sync_to_widget()

    def on_clip_moved(self, clip_id: int, new_start: float) -> None:
        """Handle clip move — routes to audio or scene-level logic."""
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return

        # Audio clip move
        if clip.data.get("is_audio"):
            source = clip.data.get("audio_source", "dg")
            if source == "event":
                # Event-based: shift the keyframe on the trigger attr
                locator = clip.data.get("audio_node")
                old_frame = clip.data.get("event_key_frame")
                if locator and old_frame is not None:
                    AudioTrackManager.move_event_key(
                        locator, old_frame, new_start
                    )
                    clip.data["event_key_frame"] = new_start
            else:
                # DG audio node: update offset attribute
                audio_node = clip.data.get("audio_node")
                if audio_node:
                    AudioTrackManager.set_audio_offset(audio_node, new_start)
            clip.data["orig_start"] = new_start
            self._audio_mgr.invalidate()
            return

        # Animation clip move — scene-level ripple
        if self.sequencer is None:
            return

        scene_id = clip.data.get("scene_id")
        orig_start = clip.data.get("orig_start")
        if scene_id is None or orig_start is None:
            return

        scene = self.sequencer.scene_by_id(scene_id)
        if scene is None:
            return

        delta = new_start - orig_start
        if abs(delta) < 1e-6:
            return

        new_scene_start = scene.start + delta
        self.sequencer.set_scene_start(scene_id, new_scene_start, ripple=True)
        self._sync_to_widget()

    def on_playhead_moved(self, frame: float) -> None:
        """Sync the Maya playhead to the widget playhead."""
        pm.currentTime(frame)

    # ---- template -------------------------------------------------------

    def apply_template(self, template_name: str, scene_id: int) -> None:
        """Apply a YAML template to all objects in a scene."""
        if self.sequencer is None:
            return
        self.sequencer.apply_template_to_scene(scene_id, template_name)


class SceneSequencerSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    def __init__(self, switchboard):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.scene_sequencer

        # Create controller
        self.controller = SceneSequencerController(self)

        # Replace the QWidget placeholder with the real SequencerWidget
        from qtpy import QtWidgets

        placeholder = self.ui.centralWidget().findChild(
            QtWidgets.QWidget, "sequencer_placeholder"
        )
        if placeholder is not None:
            layout = placeholder.parentWidget().layout()
            idx = layout.indexOf(placeholder)
            layout.removeWidget(placeholder)
            placeholder.deleteLater()

            sequencer = SequencerWidget()
            sequencer.setObjectName("sequencer")
            sequencer.setMinimumHeight(200)
            layout.insertWidget(idx, sequencer)

            # Connect signals
            sequencer.clip_resized.connect(self.controller.on_clip_resized)
            sequencer.clip_moved.connect(self.controller.on_clip_moved)
            sequencer.playhead_moved.connect(self.controller.on_playhead_moved)
            sequencer.track_hidden.connect(self.controller.hide_track)
            sequencer.track_shown.connect(self.controller.show_track)

        # Connect scene combobox
        cmb = getattr(self.ui, "cmb_scene", None)
        if cmb is not None:
            cmb.currentIndexChanged.connect(self._on_scene_selected)

        # Auto-load saved data on open
        self.controller.load()

    def _on_scene_selected(self, index: int) -> None:
        """Handle scene combobox selection — switch Maya range and widget view."""
        cmb = self.ui.cmb_scene
        if index < 0:
            return
        scene_id = cmb.itemData(index)
        if scene_id is not None:
            self.controller.select_scene(scene_id)
            self.controller._sync_to_widget()

    def header_init(self, widget):
        """Configure header menu."""
        widget.config_buttons("menu", "pin")
        widget.menu.setTitle("Scene Sequencer:")
        widget.menu.add(
            "QPushButton",
            setText="Detect from Selection",
            setObjectName="btn_detect_sel",
        )
        widget.menu.add(
            "QPushButton",
            setText="Detect from All",
            setObjectName="btn_detect_all",
        )
        widget.menu.add(
            "QPushButton",
            setText="Define from Range",
            setObjectName="btn_define_range",
        )
        widget.menu.add(
            "QPushButton",
            setText="Save Scenes",
            setObjectName="btn_save",
        )
        widget.menu.add(
            "QPushButton",
            setText="Load Scenes",
            setObjectName="btn_load",
        )

    # ---- buttons ---------------------------------------------------------

    def btn_detect_sel(self):
        self.controller.detect_from_selection()

    def btn_detect_all(self):
        self.controller.detect_from_all()

    def btn_define_range(self):
        self.controller.define_from_range()

    def btn_save(self):
        self.controller.save()

    def btn_load(self):
        self.controller.load()
