# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Audio Events UI.

Provides ``AudioEventsSlots`` — a standalone window for importing,
syncing, and managing Maya audio nodes from keyed event triggers.
"""
import json
import logging
import os

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError:
    pass

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils.attributes.audio_events._audio_events import AudioEvents


class AudioEventsSlots:
    """Switchboard slots for the Audio Events UI.

    Layout
    ------
    - **Header**: Title bar.
    - **Audio Tracks**: Browse for files, combo shows loaded stems.
    - **Sync**: One-click import of audio nodes to the timeline.
    - **Manage**: Single Remove action cleans trigger/audio data.
    - **Footer**: Status messages.
    """

    AUDIO_FILTER = (
        "Audio Files (*.wav *.aif *.aiff *.mp3 *.ogg *.m4a *.flac);;" "All Files (*)"
    )
    CATEGORY = "audio"
    FILE_MAP_ATTR = "audio_file_map"

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.audio_events

        # Internal state: {lowercase_stem: absolute_path, ...}
        self._audio_files = {}
        self._current_target = None  # PyNode of the active trigger object
        self._selection_sync_job_id = None
        self._time_changed_job_id = None
        self._scene_opened_job_id = None  # Persistent (survives scene changes)
        self._new_scene_job_id = None  # Persistent (survives scene changes)
        self._attr_callback_ids = []  # OpenMaya attribute-changed callbacks
        self._syncing_combo = False  # Echo guard for combo ↔ attr sync
        self._last_enum_idx = None  # Cached enum index for timeChanged throttle
        # b002/b003/b004 are auto-connected by switchboard (name-matching),
        # same as b000.  No manual .clicked.connect() needed.

        # Deferred so Maya's event loop is ready before scriptJob creation
        try:
            cmds.evalDeferred(self._ensure_sync_job)
        except Exception:
            pass  # Maya not ready; cmb000_init will retry

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu with tool description and workflow instructions."""
        widget.config_buttons("menu", "pin")
        widget.menu.setTitle("Audio Events:")

        widget.menu.add("Separator", setTitle="Create")
        btn_new = widget.menu.add(
            "QPushButton",
            setText="New Audio Object",
            setObjectName="btn_new_audio_object",
            setToolTip="Create a new empty audio-trigger locator and select it.",
        )
        btn_new.clicked.connect(self._create_new_audio_object)

        widget.menu.add("Separator", setTitle="Export")
        btn_export = widget.menu.add(
            "QPushButton",
            setText="Export Composite WAV",
            setObjectName="btn_export_composite",
            setToolTip=(
                "Save the current composite WAV to a chosen location.\n"
                "The composite is built by 'Sync Audio to Timeline'\n"
                "and contains all keyed clips mixed into one file."
            ),
        )
        btn_export.clicked.connect(self._export_composite)
        widget.menu.add(
            "QCheckBox",
            setText="Trim Silence",
            setObjectName="chk_trim_silence",
            setToolTip=(
                "When enabled, leading and trailing silence is removed\n"
                "from the exported composite WAV."
            ),
            setChecked=True,
        )

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Audio Events — Keys audio clip triggers on a scene object\n"
                "and imports matching audio nodes onto the Maya timeline.\n\n"
                "Workflow:\n"
                "  1. Press 'Add' to browse and select audio tracks.\n"
                "     • File stems become event trigger names.\n"
                "     • Re-adding a file with the same name replaces\n"
                "       it at the same enum index (keyframes kept).\n"
                "  2. Select a loaded track in the combo.\n"
                "  3. Move the timeline cursor to the desired start frame.\n"
                "  4. Press 'Key Audio Event' to key the trigger.\n"
                "     • Enable 'Auto End None' (option box ▸) to auto-key\n"
                "       a None marker at the clip's end frame.\n"
                "     • Enable 'Next Event' to auto-advance through tracks.\n"
                "  5. Repeat steps 2–4 for each audio cue.\n"
                "  6. Click the refresh (↻) button on the Key Audio\n"
                "     Event option box to sync audio to the timeline\n"
                "     and rebuild the composite WAV for scrub playback.\n\n"
                "Note: Adding or replacing tracks when events are already\n"
                "keyed triggers an automatic sync so the composite WAV\n"
                "reflects the updated audio immediately."
            ),
        )

    def _export_composite(self):
        """Export the current composite WAV to a user-chosen file path.

        Locates the ``{cat}_composite`` audio node, reads its filename
        attribute, and copies the WAV to the destination selected via a
        Save-File dialog.
        """
        import shutil

        # Find the composite audio node
        comp_path = None
        for node_name in AudioEvents.list_nodes(category=self.CATEGORY):
            if node_name.endswith("_composite"):
                try:
                    comp_path = cmds.getAttr(f"{node_name}.filename") or ""
                except Exception:
                    comp_path = ""
                break

        if not comp_path or not os.path.isfile(comp_path):
            self.ui.footer.setText(
                "No composite WAV found — sync audio to timeline first."
            )
            return

        # Navigate up from the cache directory so the save dialog opens
        # in the project's audio folder, not the internal cache.
        export_dir = os.path.dirname(comp_path)
        _CACHE_DIRS = {"_audio_cache", "_maya_audio_cache"}
        while os.path.basename(export_dir) in _CACHE_DIRS:
            export_dir = os.path.dirname(export_dir)

        dest = self.sb.save_file_dialog(
            file_types=["*.wav"],
            title="Export Composite WAV",
            start_dir=os.path.join(
                export_dir,
                f"{self.CATEGORY}_composite.wav",
            ),
            filter_description="WAV Files",
        )
        if not dest:
            return

        try:
            shutil.copy2(comp_path, dest)

            # Optionally trim leading/trailing silence
            trim = getattr(self.ui.header.menu, "chk_trim_silence", None)
            if trim and trim.isChecked():
                try:
                    ptk.AudioUtils.trim_silence(dest)
                except Exception as exc:
                    logging.warning("AudioEvents: trim failed: %s", exc)

            self.ui.footer.setText(f"Exported composite → {os.path.basename(dest)}")
            logging.info("AudioEvents: exported composite to '%s'", dest)
        except OSError as exc:
            self.ui.footer.setText(f"Export failed: {exc}")
            logging.warning("AudioEvents: export failed: %s", exc)

    def _sync_from_menu(self):
        """Sync audio to timeline — header-menu entry point.

        Wraps the same logic as the former tb000 toolbar button.
        """
        self.tb000()

    def _create_new_audio_object(self):
        """Prompt for a name, then create an empty audio-trigger locator.

        The locator's shape is hidden and all transform channels are
        locked + hidden so it serves purely as a data carrier.  The new
        object is selected after creation.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        name = self.sb.input_dialog(
            title="New Audio Object",
            label="Object name:",
            text="audio_events",
            parent=self.ui,
            placeholder="e.g. dialogue_triggers",
            validate=lambda t: bool(t.strip()),
            error_text="Name cannot be empty.",
        )
        if not name:
            return

        # Sanitise for Maya naming (replace spaces with underscores)
        name = name.replace(" ", "_")

        loc_name = cmds.spaceLocator(name=name)[0]

        # Lock and hide all transform attributes
        for attr in (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
            "scaleX",
            "scaleY",
            "scaleZ",
            "visibility",
        ):
            cmds.setAttr(
                f"{loc_name}.{attr}", lock=True, keyable=False, channelBox=False
            )

        # Hide the locator shape in the viewport.
        shape = (cmds.listRelatives(loc_name, shapes=True, fullPath=True) or [None])[0]
        if shape:
            cmds.setAttr(f"{shape}.visibility", 0)
            cmds.setAttr(f"{shape}.overrideEnabled", 1)
            cmds.setAttr(f"{shape}.overrideDisplayType", 1)  # template
            cmds.setAttr(f"{shape}.localScaleX", 0)
            cmds.setAttr(f"{shape}.localScaleY", 0)
            cmds.setAttr(f"{shape}.localScaleZ", 0)
            # Stamp so _is_tool_created_carrier() can identify this locator.
            attr = EventTriggers._LOCATOR_ATTR
            if not cmds.attributeQuery(attr, node=shape, exists=True):
                cmds.addAttr(shape, ln=attr, at="bool", dv=True)
                cmds.setAttr(f"{shape}.{attr}", True)

        grp = pm.PyNode(loc_name)
        pm.select(grp, replace=True)
        self._audio_files.clear()
        self._current_target = grp
        self.ui.footer.setText(f"Created '{grp.name()}'.")
        logging.info("AudioEvents: created empty audio object '%s'", grp.name())

    # ------------------------------------------------------------------
    # Audio Tracks
    # ------------------------------------------------------------------

    def cmb000_init(self, widget):
        """Init track combo and selection-sync job."""
        self._ensure_sync_job()

    def b000(self):
        """Add — browse for audio files and load/update tracks.

        Opens a file dialog.  Selected files are merged into the
        existing track map (keyed by lowercase stem).  Re-adding a
        file with the same stem overwrites the path at the same
        enum index — existing keyframes are preserved.

        If keyed events already exist on the target, a full sync
        is triggered automatically to rebuild synced nodes and the
        composite WAV.
        """
        self._browse_audio_files()

    def _browse_audio_files(self):
        """Browse for audio files and load/update tracks.

        Merge-upsert workflow:

        1. Open a file dialog — cancel leaves the scene untouched.
        2. Selected paths are merged into ``_audio_files`` keyed by
           lowercase stem.  Re-selecting a file with the same stem
           **overwrites** the path (same dict key = same enum index).
        3. ``EventTriggers.ensure()`` adds any new event names to the
           enum without disturbing existing indices or keyframes.
        4. ``AudioEvents.load_tracks()`` creates new preview audio
           nodes or updates existing ones whose file path changed.
        5. If keyed events already exist, ``_sync_and_refresh_target()``
           rebuilds synced audio nodes and the composite WAV so the
           timeline reflects the updated tracks immediately.
        """
        from qtpy.QtWidgets import QFileDialog
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        # Dialog first — all cancellation gates must fire before any scene
        # mutation so that cancelling (dialog or format-conversion prompt)
        # leaves the scene untouched.
        paths, _ = QFileDialog.getOpenFileNames(
            self.ui, "Select Audio Tracks", "", self.AUDIO_FILTER
        )
        if not paths:
            return

        paths = self._prepare_selected_paths(paths)
        if not paths:
            self.ui.footer.setText("No audio files selected for import.")
            return

        target = self._require_target()
        if not target:
            return

        # Merge new files into existing map (upsert, no clear).
        for p in paths:
            stem = os.path.splitext(os.path.basename(p))[0].lower()
            self._audio_files[stem] = p.replace("\\", "/")

        # Repair old capitalize()-corrupted enum labels if the original
        # file casing differs (e.g.  "A01_welcometothe" → "A01_WelcomeToThe").
        self._repair_enum_casing(target)

        # Create/update the keyable enum trigger on the target object
        events = self._event_names_from_files()
        EventTriggers.ensure(objects=[target], events=events, category=self.CATEGORY)

        # Create/update audio nodes at offset 0 for immediate preview
        nodes = AudioEvents.load_tracks(
            list(self._audio_files.values()), category=self.CATEGORY
        )

        # Persist file map on the target object
        self._save_file_map(target)

        # If keyed events already exist, rebuild synced nodes and composite
        # so edited tracks are reflected immediately.
        keyed = EventTriggers.iter_keyed_events(target, category=self.CATEGORY)
        if keyed:
            total = self._sync_and_refresh_target(target)
            self.ui.footer.setText(
                f"Loaded {len(nodes)} track(s), synced {total} clip(s) "
                f"→ {target.name()}.{self.CATEGORY}_trigger"
            )
        else:
            self._refresh_combo_from_target()
            self.ui.footer.setText(
                f"Loaded {len(nodes)} track(s) → {target.name()}.{self.CATEGORY}_trigger"
            )

    def _prepare_selected_paths(self, paths):
        """Filter selected paths and prompt for conversion when needed."""
        from qtpy.QtWidgets import QMessageBox

        playable = []
        convertible = []
        unsupported = []

        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in ptk.AudioUtils.PLAYABLE_EXTENSIONS:
                playable.append(path)
            elif ext in ptk.AudioUtils.SOURCE_EXTENSIONS:
                convertible.append(path)
            else:
                unsupported.append(path)

        if unsupported:
            QMessageBox.warning(
                self.ui,
                "Unsupported Audio",
                "Some files have unsupported formats and will be skipped:\n\n"
                + "\n".join(os.path.basename(p) for p in unsupported[:8]),
            )

        if convertible:
            choice = QMessageBox.question(
                self.ui,
                "Convert Audio",
                "Selected files include non-Maya-playable formats "
                "(e.g. MP3/OGG/M4A/FLAC).\n\n"
                "Convert them to WAV on import?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )

            if choice == QMessageBox.Cancel:
                return []

            if choice == QMessageBox.No:
                return playable

            if not ptk.AudioUtils.resolve_ffmpeg(required=False):
                QMessageBox.warning(
                    self.ui,
                    "FFmpeg Not Found",
                    "FFmpeg is required to convert selected files to WAV.\n"
                    "Install FFmpeg or choose only WAV/AIF/AIFF files.",
                )
                return playable

            return playable + convertible

        return playable

    def _cleanup_unused_events(self):
        """Remove enum entries that have no keyframes and their preview nodes."""
        from mayatk.node_utils.attributes.event_triggers import EventTriggers
        from mayatk.node_utils.attributes._attributes import Attributes

        target = self._current_target
        if not target or not pm.objExists(target):
            self.ui.footer.setText("Select an object with audio_trigger first.")
            return

        trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)
        if not cmds.attributeQuery(trigger_attr, node=str(target), exists=True):
            self.ui.footer.setText(f"{target.name()} has no audio_trigger.")
            return

        events = EventTriggers.get_events(target, category=self.CATEGORY) or []
        if not events:
            return

        # Determine which events are actually keyed
        keyed_labels = {
            label.lower()
            for _, label in EventTriggers.iter_keyed_events(
                target, category=self.CATEGORY
            )
        }

        # Find unused: non-None events with no keyframes
        unused = [e for e in events if e != "None" and e.lower() not in keyed_labels]
        if not unused:
            self.ui.footer.setText("All events are keyed — nothing to clean up.")
            return

        # Remove enum fields for unused events
        for label in unused:
            Attributes.delete_enum_field(str(target), trigger_attr, label)

        # Remove corresponding preview audio nodes from the set
        audio_set = AudioEvents._find_audio_set(self.CATEGORY)
        if audio_set:
            unused_lower = {u.lower() for u in unused}
            for member in list(audio_set.members()):
                node_label = self._node_to_event_label(str(member))
                if node_label and node_label in unused_lower:
                    pm.delete(member)

        # Clean up _audio_files dict
        for label in unused:
            self._audio_files.pop(label.lower(), None)

        # Re-bake manifest after enum changes
        EventTriggers.bake_manifest([target], category=self.CATEGORY)

        # Persist updated file map
        self._save_file_map(target)

        self._refresh_combo_from_target()
        self.ui.footer.setText(
            f"Cleaned up {len(unused)} unused event(s) on {target.name()}."
        )

    def cmb000(self, index, widget):
        """Track selection — set active clip on time slider and sync attr.

        Mirrors ``AttributeManager._on_enum_combo_activated``: pushes
        the combo index to the Maya ``audio_trigger`` attribute so the
        channel box stays in sync.
        """
        label = widget.currentText()
        if not label or label == "None":
            return

        # Push combo index → Maya attribute (combo→attr direction).
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        target = self._current_target
        if target and pm.objExists(target):
            trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)
            if cmds.attributeQuery(trigger_attr, node=str(target), exists=True):
                self._syncing_combo = True
                try:
                    cmds.setAttr(f"{str(target)}.{trigger_attr}", index)
                except Exception:
                    pass
                finally:
                    self._syncing_combo = False

        # Set active audio clip on time slider.
        node_name = self._resolve_audio_node_for_event(label)
        if node_name:
            try:
                AudioEvents.set_active(node_name)
                self.ui.footer.setText(f"Active: {node_name}")
            except Exception:
                pass
        else:
            self.ui.footer.setText(f"No audio node for '{label}' (sync first).")

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def tb000(self, widget=None):
        """Sync Audio to Timeline."""
        if not self._audio_files:
            self.ui.footer.setText("Browse for audio files first.")
            return

        target = self._require_target()
        if not target:
            return

        original_selection = [obj for obj in pm.selected() if pm.objExists(obj)]

        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        try:
            # Ensure trigger attribute with event enum entries from file stems
            events = self._event_names_from_files()
            EventTriggers.ensure(
                objects=[target],
                events=events,
                category=self.CATEGORY,
            )
            total = self._sync_and_refresh_target(target)

            if total:
                self.ui.footer.setText(f"Synced {total} clip(s) on {target.name()}.")
            else:
                self.ui.footer.setText("No clips imported. Key events first.")
        finally:
            if original_selection:
                pm.select(original_selection, r=True)
            else:
                pm.select(clear=True)

    # ------------------------------------------------------------------
    # Key Audio Event
    # ------------------------------------------------------------------

    def tb001_init(self, widget):
        """Init Key Audio Event option-box menu."""
        widget.option_box.set_action(
            self._sync_from_menu,
            icon="refresh",
            tooltip="Sync audio to timeline.\nRebuilds synced nodes and the composite WAV\nfrom the current track map and keyframes.",
        )
        widget.option_box.menu.setTitle("Key Audio Event")
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Auto End None",
            setObjectName="chk_auto_end_none",
            setToolTip=(
                "When enabled, keying an audio trigger will "
                "auto-key 'None' at the clip end based on clip length."
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Next Event",
            setObjectName="chk_next_event",
            setToolTip=(
                "Automatically key the next event in the track list.\n"
                "The next event is determined by the last keyed audio event:\n"
                "  • If events have been keyed, the next index after the\n"
                "    most recent key (by time) is used, wrapping around.\n"
                "  • If no events have been keyed yet, the first track is used.\n\n"
                "The combo selection is updated to reflect the chosen event."
            ),
        )
        chk_key_all = widget.option_box.menu.add(
            "QCheckBox",
            setText="Key All",
            setObjectName="chk_key_all",
            setToolTip=(
                "Key every loaded track sequentially starting from the\n"
                "current frame. Each clip is placed end-to-end using\n"
                "the clip length plus the stagger amount as spacing.\n"
                "When Auto End None is disabled, the track length is\n"
                "still used to calculate the stagger interval."
            ),
        )
        spn_stagger = widget.option_box.menu.add(
            "QSpinBox",
            setText="Stagger",
            setObjectName="spn_stagger",
            setToolTip=(
                "Extra frames added between each clip when Key All\n"
                "is enabled.  The interval is: clip_length + stagger."
            ),
            setMinimum=0,
            setMaximum=9999,
            setValue=0,
            setPrefix="Stagger: ",
            setSuffix=" fr",
        )

        # Wire enable/disable: Key All toggles stagger on and next-event off.
        def _on_key_all_toggled(checked):
            spn_stagger.setEnabled(checked)
            if checked:
                widget.option_box.menu.chk_next_event.setChecked(False)
            widget.option_box.menu.chk_next_event.setEnabled(not checked)

        chk_key_all.toggled.connect(_on_key_all_toggled)

        # Sync initial state — the checkbox may already be checked from
        # a saved/restored UI state, so match the stagger widget to it.
        spn_stagger.setEnabled(chk_key_all.isChecked())

    @CoreUtils.undoable
    def tb001(self, widget=None):
        """Key Audio Event — key the selected event, auto-end-none, and sync.

        Deterministic single-button workflow:
        1. Read the currently selected event from cmb000.
        2. Get/create the target via ``_require_target()``.
        3. Key the event at the current frame.
        4. If Auto End None is enabled, key "None" at clip end.
        5. Sync audio nodes to the timeline.

        Key All mode keys every loaded track sequentially from the
        current frame, spacing each by ``clip_length + stagger``.
        """
        if not self._audio_files:
            self.ui.footer.setText("Browse for audio files first.")
            return

        target = self._require_target()
        if not target:
            return

        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        # Ensure trigger attr exists with all events
        events = self._event_names_from_files()
        EventTriggers.ensure(
            objects=[target],
            events=events,
            category=self.CATEGORY,
        )

        key_all = widget.option_box.menu.chk_key_all.isChecked()
        auto_end = self.ui.tb001.option_box.menu.chk_auto_end_none.isChecked()

        if key_all:
            self._key_all_events(widget, target, events, auto_end)
            return

        # --- Single-event path ---
        # Resolve event label — auto-advance when "Next Event" is enabled.
        next_event = widget.option_box.menu.chk_next_event.isChecked()
        if next_event:
            event_label = self._resolve_next_event(target)
            if not event_label:
                self.ui.footer.setText("No audio tracks available.")
                return
            # Sync combo to the resolved event so the UI reflects it.
            idx = self.ui.cmb000.findText(event_label)
            if idx >= 0:
                self.ui.cmb000.setCurrentIndex(idx)
        else:
            event_label = self.ui.cmb000.currentText()
            if not event_label or event_label == "None":
                self.ui.footer.setText("Select an audio track first.")
                return

        current_frame = pm.currentTime(query=True)

        # Clean stale None keys from previous overlapping clips BEFORE
        # keying the new event.  This avoids the auto_clear "None at
        # time-1" problem and ensures no leftover None keys interrupt
        # playback.
        if auto_end:
            self._prune_overlap_none_keys(target, current_frame)

        # Key the selected event at the current frame.
        # auto_clear=False — we handle None keys ourselves via
        # _prune_overlap_none_keys + explicit end-None keying.
        keyed = EventTriggers.set_key(
            target,
            event=event_label,
            time=current_frame,
            auto_clear=False,
            category=self.CATEGORY,
        )
        if not keyed:
            self.ui.footer.setText(
                f"Event '{event_label}' not found on {target.name()}."
            )
            return

        # Auto-end-none: key "None" at clip end if enabled
        if auto_end:
            source = self._audio_files.get(event_label.lower())
            if source:
                import math

                length_frames = self._get_clip_length_frames(source)
                if length_frames > 0:
                    end_frame = math.ceil(current_frame + length_frames)

                    EventTriggers.set_key(
                        target,
                        event="None",
                        time=end_frame,
                        auto_clear=False,
                        category=self.CATEGORY,
                    )

        # Sync audio nodes to keyed frames
        original_selection = [obj for obj in pm.selected() if pm.objExists(obj)]
        try:
            total = self._sync_and_refresh_target(target)
            end_info = ""
            if auto_end:
                end_info = " + end-None"
            self.ui.footer.setText(
                f"Keyed '{event_label}' @ {int(current_frame)}{end_info}"
                f" — {total} clip(s) synced."
            )
        finally:
            if original_selection:
                pm.select(original_selection, r=True)
            else:
                pm.select(clear=True)

    def _key_all_events(self, widget, target, events, auto_end):
        """Key every loaded track sequentially from the current frame.

        Spacing between clips is ``clip_length + stagger``.
        When *auto_end* is False the track length is still used to
        calculate the stagger interval — only the explicit end-None
        key is skipped.

        Parameters:
            widget: The tb001 widget (provides option-box access).
            target: PyNode of the trigger object.
            events: Ordered list of event labels (non-None).
            auto_end: Whether to key "None" at each clip's end.
        """
        import math
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        stagger = widget.option_box.menu.spn_stagger.value()
        current_frame = pm.currentTime(query=True)
        cursor = float(current_frame)
        keyed_count = 0

        tracks = [e for e in events if e and e != "None"]
        if not tracks:
            self.ui.footer.setText("No audio tracks available.")
            return

        for event_label in tracks:
            if auto_end:
                self._prune_overlap_none_keys(target, cursor)

            keyed = EventTriggers.set_key(
                target,
                event=event_label,
                time=cursor,
                auto_clear=False,
                category=self.CATEGORY,
            )
            if not keyed:
                continue
            keyed_count += 1

            # Determine clip length (used for both auto-end-none and spacing).
            length_frames = 0.0
            source = self._audio_files.get(event_label.lower())
            if source:
                length_frames = self._get_clip_length_frames(source)

            # Key end-None when auto_end is enabled.
            if auto_end and length_frames > 0:
                end_frame = math.ceil(cursor + length_frames)
                EventTriggers.set_key(
                    target,
                    event="None",
                    time=end_frame,
                    auto_clear=False,
                    category=self.CATEGORY,
                )

            # Advance cursor: clip length + stagger (length used even
            # when auto_end is off to keep clips from overlapping).
            if length_frames > 0:
                cursor = math.ceil(cursor + length_frames) + stagger
            else:
                # No clip length available — fall back to stagger only.
                cursor += max(stagger, 1)

        # Sync once after all keys are placed.
        original_selection = [obj for obj in pm.selected() if pm.objExists(obj)]
        try:
            total = self._sync_and_refresh_target(target)
            end_info = " + end-None" if auto_end else ""
            stagger_info = f" stagger={stagger}" if stagger else ""
            self.ui.footer.setText(
                f"Key All: {keyed_count} track(s){end_info}{stagger_info}"
                f" — {total} clip(s) synced."
            )
        finally:
            if original_selection:
                pm.select(original_selection, r=True)
            else:
                pm.select(clear=True)

    def _resolve_next_event(self, target):
        """Return the next event label to key based on the last keyed event.

        Looks at all keyed events on *target* sorted by time, finds the
        most recent one, then advances to the next index in the track
        list (wrapping around to the first track after the last).

        If no events have been keyed yet, the first non-None track is
        returned.

        Returns:
            str or None — event label, or None if no tracks exist.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        events = EventTriggers.get_events(target, category=self.CATEGORY) or []
        # Filter to non-None tracks only
        tracks = [e for e in events if e and e != "None"]
        if not tracks:
            return None

        # Find the most recent keyed event (by frame)
        keyed = EventTriggers.iter_keyed_events(target, category=self.CATEGORY)
        if not keyed:
            return tracks[0]

        # Last keyed event is the final item (iter_keyed_events is sorted by time)
        _, last_label = keyed[-1]

        # Find its position in the track list and advance
        try:
            last_idx = next(
                i for i, t in enumerate(tracks) if t.lower() == last_label.lower()
            )
            next_idx = (last_idx + 1) % len(tracks)
        except StopIteration:
            # Last keyed label not found in current tracks — start from first
            next_idx = 0

        return tracks[next_idx]

    def _sync_and_refresh_target(self, target):
        """Sync keyed events to audio nodes and persist UI/cache state.

        Single source of truth for the shared post-key/post-sync flow used
        by both ``tb000`` and ``tb001``.
        """
        results = AudioEvents.sync(
            objects=[target],
            audio_file_map=dict(self._audio_files),
            category=self.CATEGORY,
        )
        total = sum(len(v) for v in results.values())
        self._refresh_combo_from_target()
        self._save_file_map(target)
        return total

    def _prune_overlap_none_keys(self, target, start_frame):
        """Remove stale intermediate ``None`` keys after a new clip starts.

        Finds the next non-None key after ``start_frame``, and removes all
        ``None`` keys between ``start_frame`` and that next key. This ensures
        that when a new clip cuts off an older clip, the older clip's
        now-redundant end-None key is removed, even if it falls after the
        new clip's end frame.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)
        none_index = EventTriggers.event_index(
            target,
            "None",
            category=self.CATEGORY,
        )
        if none_index < 0:
            return

        attr_path = f"{target.name()}.{trigger_attr}"
        key_times = cmds.keyframe(attr_path, query=True) or []

        # Find the next non-None key after start_frame
        next_non_none_time = None
        for key_time in sorted(key_times):
            key_time = float(key_time)
            if key_time > start_frame:
                vals = cmds.keyframe(
                    attr_path,
                    query=True,
                    time=(key_time, key_time),
                    valueChange=True,
                )
                if vals and not any(int(round(v)) == none_index for v in vals):
                    next_non_none_time = key_time
                    break

        # Remove all None keys from start_frame up to next_non_none_time
        for key_time in sorted(key_times):
            key_time = float(key_time)
            if key_time >= start_frame and (
                next_non_none_time is None or key_time < next_non_none_time
            ):
                vals = cmds.keyframe(
                    attr_path,
                    query=True,
                    time=(key_time, key_time),
                    valueChange=True,
                )
                if vals and any(int(round(v)) == none_index for v in vals):
                    EventTriggers.clear_key(
                        target,
                        time=key_time,
                        category=self.CATEGORY,
                    )

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    def b002(self):
        """Remove Audio — delete imported audio nodes and composite WAV."""
        cat = self.CATEGORY
        count = AudioEvents.remove(category=cat)
        self.ui.footer.setText(
            f"Removed {count} audio node(s)." if count else "No audio nodes to remove."
        )

    def b004(self):
        """Cleanup Unused — remove unkeyed enum entries and their preview nodes."""
        self._cleanup_unused_events()

    def b005(self):
        """Replace Selected Track — swap the selected event's audio file.

        Renames the enum label to the new file's stem (preserving the
        index and all existing keyframes), updates ``_audio_files``,
        reconfigures the preview audio node, persists the file map, and
        re-syncs if keyed events exist.
        """
        from qtpy.QtWidgets import QFileDialog
        from mayatk.node_utils.attributes._attributes import Attributes

        # 1. Determine which event is selected in the combo
        current_label = self.ui.cmb000.currentText()
        if not current_label or current_label == "None":
            self.ui.footer.setText("Select a track in the combo first.")
            return

        target = self._require_target()
        if not target:
            return

        # 2. File dialog — single file only
        paths, _ = QFileDialog.getOpenFileNames(
            self.ui, "Replace Track Audio", "", self.AUDIO_FILTER
        )
        if not paths:
            return

        paths = self._prepare_selected_paths(paths)
        if not paths or len(paths) < 1:
            return

        new_path = paths[0].replace("\\", "/")
        new_stem = os.path.splitext(os.path.basename(new_path))[0]
        old_stem = current_label  # enum labels preserve original casing
        old_key = old_stem.lower()
        new_key = new_stem.lower()

        # 3. If the stem actually changed, rename the enum label
        if old_key != new_key:
            from mayatk.node_utils.attributes.event_triggers import EventTriggers

            trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)
            Attributes.rename_enum_field(str(target), trigger_attr, old_stem, new_stem)

            # Update _audio_files dict: remove old, set new
            self._audio_files.pop(old_key, None)
            self._audio_files[new_key] = new_path

            # Rename / re-stamp the preview audio node if it exists
            for node_name in AudioEvents.list_nodes(category=self.CATEGORY):
                if cmds.attributeQuery(
                    AudioEvents.NODE_STEM_ATTR, node=node_name, exists=True
                ):
                    stem_val = (
                        cmds.getAttr(f"{node_name}.{AudioEvents.NODE_STEM_ATTR}") or ""
                    )
                    if stem_val.lower() == old_key:
                        ntype = ""
                        if cmds.attributeQuery(
                            AudioEvents.NODE_TYPE_ATTR, node=node_name, exists=True
                        ):
                            ntype = (
                                cmds.getAttr(
                                    f"{node_name}.{AudioEvents.NODE_TYPE_ATTR}"
                                )
                                or ""
                            )
                        if ntype == "preview":
                            # Reconfigure with new file
                            playable = AudioEvents._resolve_playable_path(new_path)
                            if playable:
                                AudioEvents._configure_audio_node(
                                    node_name, playable, 0
                                )
                            AudioEvents._stamp_event_attrs(
                                node_name, new_key, "preview"
                            )
                            # Rename the Maya node itself
                            try:
                                cmds.rename(node_name, new_stem)
                            except Exception:
                                pass
                        break
        else:
            # Same stem, just updating the file path
            playable = AudioEvents._resolve_playable_path(new_path)
            self._audio_files[new_key] = new_path

            # Update existing preview node
            for node_name in AudioEvents.list_nodes(category=self.CATEGORY):
                if cmds.attributeQuery(
                    AudioEvents.NODE_STEM_ATTR, node=node_name, exists=True
                ):
                    stem_val = (
                        cmds.getAttr(f"{node_name}.{AudioEvents.NODE_STEM_ATTR}") or ""
                    )
                    if stem_val.lower() == new_key:
                        ntype = ""
                        if cmds.attributeQuery(
                            AudioEvents.NODE_TYPE_ATTR, node=node_name, exists=True
                        ):
                            ntype = (
                                cmds.getAttr(
                                    f"{node_name}.{AudioEvents.NODE_TYPE_ATTR}"
                                )
                                or ""
                            )
                        if ntype == "preview" and playable:
                            AudioEvents._configure_audio_node(node_name, playable, 0)
                        break

        # 4. Persist and re-sync
        self._save_file_map(target)

        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        keyed = EventTriggers.iter_keyed_events(target, category=self.CATEGORY)
        if keyed:
            total = self._sync_and_refresh_target(target)
            self.ui.footer.setText(
                f"Replaced '{old_stem}' → '{new_stem}', synced {total} clip(s)."
            )
        else:
            self._refresh_combo_from_target()
            self.ui.footer.setText(f"Replaced '{old_stem}' → '{new_stem}'.")

    def b003(self):
        """Remove — clean up trigger attributes and audio nodes.

        If the target is a tool-created data-carrier (its only shapes are
        the hidden locator added by ``EventTriggers._protect_empty_transforms``),
        it is deleted entirely.  Otherwise the object is left in the scene
        and only the audio trigger attributes and audio nodes are removed.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        target = self._current_target
        name = target.name() if target and pm.objExists(target) else None
        tool_created = (
            self._is_tool_created_carrier(target)
            if target and pm.objExists(target)
            else False
        )

        # EventTriggers.remove is the single source of truth for teardown:
        # trigger/manifest attrs, persisted file_map, anim curves, and
        # category audio nodes.
        if target and pm.objExists(target):
            EventTriggers.remove(objects=[target], category=self.CATEGORY)

        # Delete the object only if it was a tool-created data carrier.
        if target and pm.objExists(target) and tool_created:
            pm.delete(target)

        self._audio_files.clear()
        self._current_target = None
        self._trigger_attr_path = None
        self._last_enum_idx = None
        self._refresh_combo_from_target()
        self.ui.footer.setText(
            f"Removed audio data from '{name}'." if name else "Removed audio data."
        )

    def _is_tool_created_carrier(self, target):
        """Return True if *target* is a pure tool-created data-carrier object.

        A target is considered tool-created when all of its shape children
        are hidden locator shapes stamped with ``event_trigger_locator`` by
        ``EventTriggers._protect_empty_transforms``.  User scene objects
        (meshes, joints, nurbs curves, …) always have at least one
        non-stamped shape and return False.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        shapes = cmds.listRelatives(str(target), shapes=True, fullPath=True) or []
        if not shapes:
            return False  # already shapeless after EventTriggers.remove — treat as user object
        return all(
            cmds.attributeQuery(EventTriggers._LOCATOR_ATTR, node=s, exists=True)
            for s in shapes
        )

    def _get_clip_length_frames(self, audio_path):
        """Return clip duration in timeline frames for an audio file path.

        Checks existing preview nodes first to avoid creating a
        temporary audio node (which pollutes the undo stack).
        """
        # Try existing preview node (avoids temp node creation)
        stem_lower = os.path.splitext(os.path.basename(audio_path))[0].lower()
        for node_name in AudioEvents.list_nodes(category=self.CATEGORY):
            try:
                ntype = ""
                if cmds.attributeQuery(
                    AudioEvents.NODE_TYPE_ATTR, node=node_name, exists=True
                ):
                    ntype = (
                        cmds.getAttr(f"{node_name}.{AudioEvents.NODE_TYPE_ATTR}") or ""
                    )
                is_preview = ntype == "preview" or (
                    not ntype and not node_name.endswith("_composite")
                )
                if is_preview and node_name.lower() == stem_lower:
                    return float(cmds.sound(node_name, query=True, length=True))
            except Exception:
                continue

        # Fallback: create temp node
        path = AudioEvents._resolve_playable_path(audio_path)
        if not path:
            return 0.0

        temp_node = None
        try:
            temp_node = cmds.sound(file=path, offset=0)
            length = cmds.sound(temp_node, query=True, length=True)
            return float(length)
        except Exception:
            return 0.0
        finally:
            if temp_node and pm.objExists(temp_node):
                try:
                    pm.delete(temp_node)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _event_names_from_files(self):
        """Return event names preserving original file casing.

        The enum field labels on the current target are the authoritative
        source for casing.  Using them avoids deriving stems from
        converted/cached audio paths (e.g. ``Footstep_3d7a1f8b.wav``)
        that ``_sync_from_selection`` may have hydrated into
        ``_audio_files``.  For events not yet in the enum (new browse),
        the stem is extracted from the stored path, which at that point
        is still the original user-selected file.
        """
        if not self._audio_files:
            return []

        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        # Build lowercase → original-cased label map from the current enum.
        existing_labels: dict = {}
        target = self._current_target
        if target and pm.objExists(target):
            for e in EventTriggers.get_events(target, category=self.CATEGORY) or []:
                if e and e != "None":
                    existing_labels[e.lower()] = e

        result = []
        for k in sorted(self._audio_files):
            if k in existing_labels:
                # Known event — use enum's casing directly.
                result.append(existing_labels[k])
            else:
                # New event not yet in enum — extract from path.
                # At browse-time this is always the original file path,
                # so the stem is clean (no cache-path hash suffix).
                result.append(
                    os.path.splitext(os.path.basename(self._audio_files[k]))[0]
                )
        return result

    def _repair_enum_casing(self, target):
        """Rename capitalize()-corrupted enum labels to match original filenames.

        Early versions derived enum labels via ``stem.capitalize()`` which
        mangled PascalCase/mixed-case filenames (``A01_WelcomeToThe`` →
        ``A01_welcometothe``).  This method compares the current enum
        labels against the original-cased file stems and renames any
        that differ only in casing, preserving keyframe indices.

        Only called from ``_browse_audio_files`` where ``_audio_files``
        values are guaranteed to be **original** (non-cache) file paths.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers
        from mayatk.node_utils.attributes._attributes import Attributes

        if not target or not pm.objExists(target):
            return

        trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)
        if not cmds.attributeQuery(trigger_attr, node=str(target), exists=True):
            return

        events = EventTriggers.get_events(target, category=self.CATEGORY) or []
        # Build {lowercase: original_cased_stem} from file paths
        original_stems = {}
        for key, path in self._audio_files.items():
            original_stems[key] = os.path.splitext(os.path.basename(path))[0]

        renamed = 0
        for label in events:
            if label == "None":
                continue
            lower = label.lower()
            correct = original_stems.get(lower)
            if correct and correct != label:
                Attributes.rename_enum_field(str(target), trigger_attr, label, correct)
                renamed += 1

        if renamed:
            EventTriggers.bake_manifest([target], category=self.CATEGORY)

    def _require_target(self):
        """Return the current target object, auto-creating one if needed.

        Checks (in order):
        1. Current selection with audio_trigger attr.
        2. Any selected transform (will get trigger added on browse/sync).
        3. Cached ``_current_target`` if still valid and nothing selected.
        4. Auto-create an ``audio_events`` locator (hidden) as a dedicated data carrier.

        Returns:
            PyNode — always succeeds.
        """
        # 1. Check selection for object with trigger attr
        obj = self._get_selected_trigger_object()
        if obj:
            self._current_target = obj
            return obj

        # 2. Check selection for ANY transform (will get trigger added)
        sel = pm.selected()
        if sel:
            self._current_target = sel[0]
            return sel[0]

        # 3. Use cached target if still alive and nothing is selected
        if self._current_target and pm.objExists(self._current_target):
            return self._current_target

        # 4. Auto-create a dedicated data-carrier locator (hidden in viewport)
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        loc_name = cmds.spaceLocator(name="audio_events")[0]
        shape = (cmds.listRelatives(loc_name, shapes=True, fullPath=True) or [None])[0]
        if shape:
            cmds.setAttr(f"{shape}.visibility", 0)
            cmds.setAttr(f"{shape}.overrideEnabled", 1)
            cmds.setAttr(f"{shape}.overrideDisplayType", 1)  # template
            cmds.setAttr(f"{shape}.localScaleX", 0)
            cmds.setAttr(f"{shape}.localScaleY", 0)
            cmds.setAttr(f"{shape}.localScaleZ", 0)
            # Stamp so _is_tool_created_carrier() can identify this locator.
            attr = EventTriggers._LOCATOR_ATTR
            if not cmds.attributeQuery(attr, node=shape, exists=True):
                cmds.addAttr(shape, ln=attr, at="bool", dv=True)
                cmds.setAttr(f"{shape}.{attr}", True)
        grp = pm.PyNode(loc_name)
        pm.select(grp, replace=True)
        self._audio_files.clear()
        self._current_target = grp
        self.ui.footer.setText(f"Created '{grp.name()}' for audio triggers.")
        return grp

    def _resolve_audio_node_for_event(self, event_label):
        """Find the best audio node name for an event label.

        Priority: plain stem preview node > first synced node > composite.
        """
        label_lower = event_label.lower()
        nodes = AudioEvents.list_nodes(category=self.CATEGORY)
        # Pass 1: exact stem match (preview node)
        for n in nodes:
            if n.lower() == label_lower:
                return n
        # Pass 2: synced node "Label_frame"
        for n in nodes:
            parts = n.rsplit("_", 1)
            if (
                len(parts) == 2
                and parts[1].isdigit()
                and parts[0].lower() == label_lower
            ):
                return n
        # Pass 3: composite
        for n in nodes:
            if n.endswith("_composite"):
                return n
        return None

    def _refresh_combo_from_target(self):
        """Populate cmb000 with enum event names from the current target."""
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        cmb = self.ui.cmb000
        cmb.blockSignals(True)
        cmb.clear()

        target = self._current_target
        if target and pm.objExists(target):
            events = EventTriggers.get_events(target, category=self.CATEGORY) or []
            if events:
                cmb.addItems(events)

        # Sync selection to the current enum value at this frame
        self._sync_combo_to_enum()

        cmb.blockSignals(False)

    def _sync_combo_to_enum(self):
        """Set cmb000 selection to match the current enum attr value.

        Reads the ``audio_trigger`` integer index at the current frame
        and sets the combo index directly (matching the attribute_manager
        ``_on_attr_value_set`` pattern).  Combo items are added in
        enum-field order so indices correspond 1:1.

        Skipped when ``_syncing_combo`` is True (the change originated
        from the combo itself).
        """
        if self._syncing_combo:
            return

        # Guard: skip if UI has been destroyed
        try:
            if not self.ui or not self.ui.isVisible():
                return
        except RuntimeError:
            return  # Qt C++ object already deleted

        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        target = self._current_target
        if not target or not pm.objExists(target):
            return

        trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)
        if not cmds.attributeQuery(trigger_attr, node=str(target), exists=True):
            return

        try:
            idx = cmds.getAttr(f"{str(target)}.{trigger_attr}")
        except Exception:
            return

        self._last_enum_idx = idx

        cmb = self.ui.cmb000
        cmb.blockSignals(True)
        if 0 <= idx < cmb.count():
            cmb.setCurrentIndex(idx)
        else:
            cmb.setCurrentIndex(-1)
        cmb.blockSignals(False)
        cmb.repaint()

    def _on_time_changed(self):
        """Lightweight per-frame callback for timeChanged scriptJob.

        Only performs a single ``cmds.getAttr`` and compares against the
        cached index.  The full ``_sync_combo_to_enum`` (with objExists,
        attributeQuery, Qt repaint) only runs when the value changes.
        """
        if self._syncing_combo:
            return

        target = self._current_target
        if not target:
            return

        # Guard: skip if UI has been destroyed
        try:
            if not self.ui or not self.ui.isVisible():
                return
        except RuntimeError:
            return  # Qt C++ object already deleted

        # Build attr path once per target switch (cheap string compare)
        attr_path = getattr(self, "_trigger_attr_path", None)
        if not attr_path:
            return

        try:
            idx = cmds.getAttr(attr_path)
        except Exception:
            return

        if idx == self._last_enum_idx:
            return  # No change — skip Qt update

        self._last_enum_idx = idx
        cmb = self.ui.cmb000
        cmb.blockSignals(True)
        if 0 <= idx < cmb.count():
            cmb.setCurrentIndex(idx)
        else:
            cmb.setCurrentIndex(-1)
        cmb.blockSignals(False)
        cmb.repaint()

    def _ensure_sync_job(self):
        """Create the SelectionChanged + timeChanged scriptJobs and connect the Channel Box signal.

        Also creates persistent ``SceneOpened`` / ``NewSceneOpened`` jobs
        (NOT ``killWithScene``) that re-create the volatile jobs after
        File > Open or File > New, preventing the recurring regression
        where the UI goes dead after reopening a scene.
        """
        # -- Persistent scene-lifecycle jobs (survive scene changes) ------
        # These re-create the volatile jobs below whenever the scene is
        # replaced, forming a self-healing lifecycle.
        try:
            if self._scene_opened_job_id is None or not cmds.scriptJob(
                exists=self._scene_opened_job_id
            ):
                self._scene_opened_job_id = cmds.scriptJob(
                    event=["SceneOpened", self._on_scene_opened],
                )
        except Exception:
            pass

        try:
            if self._new_scene_job_id is None or not cmds.scriptJob(
                exists=self._new_scene_job_id
            ):
                self._new_scene_job_id = cmds.scriptJob(
                    event=["NewSceneOpened", self._on_scene_opened],
                )
        except Exception:
            pass

        # -- Volatile jobs (killed with scene, recreated by the above) ---
        try:
            if self._selection_sync_job_id is not None and cmds.scriptJob(
                exists=self._selection_sync_job_id
            ):
                pass  # Already alive
            else:
                self._selection_sync_job_id = cmds.scriptJob(
                    event=["SelectionChanged", self._deferred_sync_from_selection],
                    killWithScene=True,
                )
        except Exception:
            pass

        # timeChanged fires on play/scrub — keeps combo in sync with keyed enum.
        try:
            if self._time_changed_job_id is not None and cmds.scriptJob(
                exists=self._time_changed_job_id
            ):
                pass  # Already alive
            else:
                self._time_changed_job_id = cmds.scriptJob(
                    event=["timeChanged", self._on_time_changed],
                    killWithScene=True,
                )
        except Exception:
            pass

        # Connect Channel Box Qt signal — fires when user edits a value in
        # the channel box (same mechanism as attribute_manager).
        self._connect_cb_signal()

        # Run initial sync now
        cmds.evalDeferred(self._sync_from_selection)

    def _on_scene_opened(self):
        """Reset stale state and re-create volatile scriptJobs after scene change.

        Called by the persistent ``SceneOpened`` / ``NewSceneOpened``
        scriptJobs.  The old ``_current_target`` PyNode is invalid in the
        new scene, so all cached state is cleared before re-creating the
        ``SelectionChanged`` and ``timeChanged`` jobs.
        """
        log = logging.getLogger(__name__)
        log.debug("_on_scene_opened: resetting state for new scene")

        # Invalidate all scene-specific state
        self._current_target = None
        self._trigger_attr_path = None
        self._last_enum_idx = None
        self._audio_files.clear()

        # Volatile job IDs are invalid after killWithScene
        self._selection_sync_job_id = None
        self._time_changed_job_id = None

        # OpenMaya callbacks are also invalidated by scene change
        self._attr_callback_ids = []

        # Re-create volatile jobs and run initial sync
        self._ensure_sync_job()

    def _connect_cb_signal(self):
        """Connect to the Channel Box’s QItemSelectionModel signal.

        Mirrors ``AttributeManager._connect_cb_signal``.  Safe to call
        repeatedly — disconnects any prior connection first (the C++ pointer
        can go stale after scene changes).
        """
        self._disconnect_cb_signal()

        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            ChannelBox.connect_selection_changed(self._on_cb_changed)
        except Exception:
            pass

    def _disconnect_cb_signal(self):
        """Disconnect Audio Events from Channel Box selection signal."""
        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            ChannelBox.disconnect_selection_changed(self._on_cb_changed)
        except Exception:
            pass

    def _on_cb_changed(self, selected, deselected):
        """Slot for Channel Box ``selectionModel().selectionChanged``.

        Fires whenever the user interacts with the channel box (including
        after setting an enum value).  Re-reads the trigger attr and syncs
        the combo — same pattern as AttributeManager._on_cb_selection_changed.
        """
        try:
            if self._syncing_combo:
                return
            if not self.ui or not self.ui.isVisible():
                return
        except RuntimeError:
            return  # Qt C++ object already deleted
        self._sync_combo_to_enum()

    def _deferred_sync_from_selection(self):
        """Deferred wrapper — ensures Maya selection state is committed."""
        try:
            if not self.ui or not self.ui.isVisible():
                return
        except RuntimeError:
            return  # Qt C++ object already deleted
        cmds.evalDeferred(self._sync_from_selection)

    def _sync_from_selection(self):
        """Sync combo/footer from currently selected object with audio trigger.

        Detection priority:
        1. ``_get_selected_trigger_object()`` — walks selection + DAG parents.
        2. Safety-net ``hasAttr`` on ``sel[0]`` (handles edge cases).
        3. Non-trigger selection → switch target, show "(no audio_trigger)".
        4. Nothing selected → cached target, then scene-wide scan.
        """
        # Guard: skip if UI has been destroyed
        try:
            if not self.ui or not self.ui.isVisible():
                return
        except RuntimeError:
            return  # Qt C++ object already deleted

        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        log = logging.getLogger(__name__)

        try:
            old_target = self._current_target
            trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)

            # -- 1. Primary detection ------------------------------------
            obj = self._get_selected_trigger_object()

            if obj:
                self._hydrate_from_target(obj, old_target)
                return

            # -- Primary failed — check selection manually ---------------
            sel = pm.selected()

            if sel:
                first = sel[0]

                # -- 2. Safety-net hasAttr (catches edge cases the
                #       primary walk misses, e.g. component sub-objects).
                try:
                    if first.hasAttr(trigger_attr):
                        log.debug(
                            "_sync_from_selection: safety-net found " "trigger on %s",
                            first,
                        )
                        self._hydrate_from_target(first, old_target)
                        return
                except Exception:
                    pass

                # Also check via cmds (long name) as a third-chance
                # fallback in case PyMEL wrapping is stale.
                try:
                    if cmds.attributeQuery(trigger_attr, node=str(first), exists=True):
                        log.debug(
                            "_sync_from_selection: cmds fallback found "
                            "trigger on %s",
                            first,
                        )
                        self._hydrate_from_target(pm.PyNode(str(first)), old_target)
                        return
                except Exception:
                    pass

                # -- 3. Non-trigger object selected ----------------------
                short = first.name()
                # If we have a valid cached target with trigger, keep it
                if old_target and old_target != first:
                    try:
                        if pm.objExists(old_target) and cmds.attributeQuery(
                            trigger_attr, node=str(old_target), exists=True
                        ):
                            self.ui.footer.setText(
                                f"Selected '{short}' — keeping '{old_target.name()}'"
                            )
                            return
                    except Exception:
                        pass
                if first != old_target:
                    self._audio_files.clear()
                self._current_target = first
                self._trigger_attr_path = None
                self._last_enum_idx = None
                self._refresh_combo_from_target()
                self.ui.footer.setText(f"{short} (no audio_trigger)")
                return

            # -- 4. Nothing selected — use cached target or scan scene ---
            if old_target:
                try:
                    if pm.objExists(old_target):
                        obj = old_target
                except Exception:
                    obj = None

            if not obj:
                obj = self._find_trigger_in_scene(trigger_attr)

            if not obj:
                self._current_target = None
                self._trigger_attr_path = None
                self._last_enum_idx = None
                self._audio_files.clear()
                self._refresh_combo_from_target()
                self.ui.footer.setText("No audio trigger object in scene.")
                return

            self._hydrate_from_target(obj, old_target)

        except Exception:
            log.warning("_sync_from_selection error", exc_info=True)
            try:
                self.ui.footer.setText("Error syncing selection — see Script Editor.")
            except Exception:
                pass

    def _hydrate_from_target(self, obj, old_target=None):
        """Populate the combo and audio-file map from *obj*'s trigger attribute.

        Shared by ``_sync_from_selection`` so that both selection-based and
        scene-scan-based code paths use identical hydration logic.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        # Clear stale audio-file entries when switching targets
        if old_target is None:
            old_target = self._current_target
        if obj != old_target:
            self._audio_files.clear()

        self._current_target = obj

        # Cache the full attr path for the lightweight _on_time_changed callback.
        trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)
        self._trigger_attr_path = f"{str(obj)}.{trigger_attr}"
        self._last_enum_idx = None

        events = EventTriggers.get_events(obj, category=self.CATEGORY) or []
        track_names = [e for e in events if e and e != "None"]

        if not track_names:
            self.ui.footer.setText(f"{obj.name()} (no events)")
            self._refresh_combo_from_target()
            return

        # Show feedback immediately so the user always sees target status,
        # even if subsequent steps (combo refresh, hydration) error.
        self.ui.footer.setText(f"{obj.name()} ({len(track_names)} track(s))")

        # Populate combo with enum event names
        self._refresh_combo_from_target()

        # Hydrate _audio_files: persisted attr is primary source,
        # then fill gaps from existing audio-node .filename attrs.
        # The node-filename fallback reads from the GLOBAL audio_set
        # which contains nodes from ALL targets, so it is only used
        # when no persisted file-map exists.  Cache paths (converted
        # WAVs inside ``_maya_audio_cache/`` dirs) are always rejected
        # because they are ephemeral build artefacts, not user sources.
        _CACHE_SEGMENTS = {"_maya_audio_cache", "_audio_cache"}
        try:
            persisted = self._load_file_map(obj)
            if persisted:
                self._audio_files.update(persisted)
            else:
                # Fallback: fill gaps from node .filename attrs.
                # Only runs when no persisted map exists (first hydration).
                event_labels = {e.lower() for e in track_names}
                for node_name in AudioEvents.list_nodes(category=self.CATEGORY):
                    node_event = self._node_to_event_label(node_name)
                    if node_event and node_event in event_labels:
                        if node_event not in self._audio_files:
                            try:
                                filename = cmds.getAttr(f"{node_name}.filename")
                                if filename:
                                    norm = filename.replace("\\", "/")
                                    # Reject cache paths — they belong to
                                    # whichever target triggered the conversion
                                    # and are not reliable cross-target sources.
                                    parts = set(norm.split("/"))
                                    if parts & _CACHE_SEGMENTS:
                                        continue
                                    self._audio_files[node_event] = norm
                            except Exception:
                                pass
        except Exception:
            logging.getLogger(__name__).debug(
                "_hydrate_from_target: file-map hydration error", exc_info=True
            )

        # Register OpenMaya attribute-changed callback and reconnect Channel Box
        try:
            self._register_attr_change_callback(obj)
            self._connect_cb_signal()
        except Exception:
            logging.getLogger(__name__).debug(
                "_hydrate_from_target: callback registration error", exc_info=True
            )

    # -- OpenMaya attribute-changed callback ------------------------------

    def _register_attr_change_callback(self, target):
        """Register an ``MNodeMessage.addAttributeChangedCallback`` on *target*.

        Fires ``_on_attr_value_set`` whenever *any* attribute changes on
        the node — we filter for the trigger attr inside the callback.
        Re-called after every selection change to track the new target.
        """
        self._cleanup_attr_callbacks()

        if not target or not pm.objExists(target):
            return

        try:
            import maya.api.OpenMaya as om2

            sel = om2.MSelectionList()
            sel.add(str(target))
            mobj = sel.getDependNode(0)

            def _on_attr_changed(msg, plug, other_plug, *args):
                try:
                    if self._syncing_combo:
                        return
                    if not (msg & om2.MNodeMessage.kAttributeSet):
                        return
                    # Guard: skip if UI has been destroyed
                    if not self.ui or not self.ui.isVisible():
                        return
                    # Only react to the trigger attr
                    from mayatk.node_utils.attributes.event_triggers import (
                        EventTriggers,
                    )

                    trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)
                    if plug.partialName(useLongNames=True) == trigger_attr:
                        cmds.evalDeferred(self._sync_combo_to_enum)
                except RuntimeError:
                    pass  # Deleted C++ object — swallow to prevent Maya crash
                except Exception:
                    logging.getLogger(__name__).debug(
                        "_on_attr_changed callback error", exc_info=True
                    )

            cb_id = om2.MNodeMessage.addAttributeChangedCallback(mobj, _on_attr_changed)
            self._attr_callback_ids.append(cb_id)
        except Exception:
            logging.getLogger(__name__).debug(
                "_register_attr_change_callback failed", exc_info=True
            )

    def _cleanup_attr_callbacks(self):
        """Remove OpenMaya attribute-changed callbacks."""

        ids = self._attr_callback_ids
        if not ids:
            return
        try:
            import maya.api.OpenMaya as om2

            for cb_id in ids:
                try:
                    om2.MMessage.removeCallback(cb_id)
                except Exception:
                    pass
        except ImportError:
            pass
        self._attr_callback_ids = []

    def _get_selected_trigger_object(self):
        """Return first selected object containing this category trigger attr.

        Uses ``pm.hasAttr`` as the primary check, with a ``cmds.attributeQuery``
        fallback in case the PyMEL wrapper returns stale results.
        Also walks DAG ancestors so clicking a locator shape in the viewport
        picks up its parent transform.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        log = logging.getLogger(__name__)
        trigger_attr, _ = EventTriggers.attr_names(self.CATEGORY)

        for obj in pm.selected():
            # -- Check the selected object itself --------------------
            try:
                if obj.hasAttr(trigger_attr):
                    return obj
            except Exception:
                pass

            # cmds fallback — catches cases where a stale PyNode
            # wrapper doesn't reflect the attribute correctly.
            try:
                node_str = str(obj)
                if cmds.attributeQuery(trigger_attr, node=node_str, exists=True):
                    log.debug(
                        "_get_selected_trigger_object: cmds fallback "
                        "found trigger on %s",
                        node_str,
                    )
                    return obj
            except Exception:
                pass

            # -- Walk up the DAG (shape → transform, nested groups) --
            try:
                for parent in obj.getAllParents():
                    try:
                        if parent.hasAttr(trigger_attr):
                            return parent
                    except Exception:
                        pass
                    try:
                        parent_str = str(parent)
                        if cmds.attributeQuery(
                            trigger_attr, node=parent_str, exists=True
                        ):
                            log.debug(
                                "_get_selected_trigger_object: cmds "
                                "parent fallback found trigger on %s",
                                parent_str,
                            )
                            return parent
                    except Exception:
                        pass
            except Exception:
                continue

        return None

    def _find_trigger_in_scene(self, trigger_attr):
        """Scan the scene for a node with *trigger_attr* using multiple strategies.

        Called as a last-resort fallback when nothing is selected and
        ``_current_target`` is stale.  Uses three strategies in order:

        1. ``cmds.ls("*.attr")`` — fast but unreliable for user-defined
           enum attrs in some Maya builds.
        2. ``audio_file_map`` marker — any transform carrying the
           persisted file-map attr is almost certainly a trigger object.
        3. Brute-force ``attributeQuery`` over scene transforms.

        Returns:
            PyNode or None.
        """
        log = logging.getLogger(__name__)

        # Strategy 1: attribute wildcard (fastest)
        try:
            candidates = cmds.ls(f"*.{trigger_attr}", objectsOnly=True, long=True) or []
            if candidates:
                log.debug("_find_trigger_in_scene: ls wildcard found %s", candidates[0])
                return pm.PyNode(candidates[0])
        except Exception:
            pass

        # Strategy 2: look for the persisted file_map attr marker
        try:
            candidates = (
                cmds.ls(f"*.{self.FILE_MAP_ATTR}", objectsOnly=True, long=True) or []
            )
            if candidates:
                log.debug(
                    "_find_trigger_in_scene: file_map marker found %s", candidates[0]
                )
                return pm.PyNode(candidates[0])
        except Exception:
            pass

        # Strategy 3: brute-force check on all transforms (slow but reliable)
        try:
            for node in cmds.ls(type="transform", long=True):
                try:
                    if cmds.attributeQuery(trigger_attr, node=node, exists=True):
                        log.debug("_find_trigger_in_scene: brute-force found %s", node)
                        return pm.PyNode(node)
                except Exception:
                    continue
        except Exception:
            pass

        # Strategy 4: check the audio set — if audio nodes exist, trace
        # back through all transforms with the file_map attr.
        try:
            audio_set = AudioEvents._find_audio_set(self.CATEGORY)
            if audio_set and audio_set.members():
                for node in cmds.ls(type="transform", long=True):
                    try:
                        if cmds.attributeQuery(
                            self.FILE_MAP_ATTR, node=node, exists=True
                        ):
                            log.debug(
                                "_find_trigger_in_scene: audio-set trace found %s",
                                node,
                            )
                            return pm.PyNode(node)
                    except Exception:
                        continue
        except Exception:
            pass

        log.debug("_find_trigger_in_scene: no trigger object found in scene")
        return None

    def _node_to_event_label(self, node_name):
        """Map audio node names to lowercase event labels.

        Reads the ``audio_event_stem`` attr stamped by
        ``AudioEvents._stamp_event_attrs`` when available, falling
        back to legacy name-based parsing for older nodes.
        """
        name = str(node_name)
        # Prefer stamped attrs (new-style)
        try:
            if cmds.attributeQuery(AudioEvents.NODE_TYPE_ATTR, node=name, exists=True):
                ntype = cmds.getAttr(f"{name}.{AudioEvents.NODE_TYPE_ATTR}") or ""
                if ntype == "composite":
                    return None
            if cmds.attributeQuery(AudioEvents.NODE_STEM_ATTR, node=name, exists=True):
                stem = cmds.getAttr(f"{name}.{AudioEvents.NODE_STEM_ATTR}")
                if stem:
                    return stem.lower()
        except Exception:
            pass
        # Fallback: legacy name-based convention
        if name.endswith("_composite"):
            return None
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0].lower()
        return name.lower()

    # -- File-map persistence ---------------------------------------------

    def _save_file_map(self, target):
        """Persist ``_audio_files`` as a JSON string attr on *target*.

        Creates the ``audio_file_map`` attribute if it doesn't exist.
        """
        if not target or not pm.objExists(target):
            return

        data = json.dumps(self._audio_files, sort_keys=True)
        node = str(target)
        if not cmds.attributeQuery(self.FILE_MAP_ATTR, node=node, exists=True):
            cmds.addAttr(node, ln=self.FILE_MAP_ATTR, dt="string")
        cmds.setAttr(f"{node}.{self.FILE_MAP_ATTR}", data, type="string")

    def _load_file_map(self, target):
        """Load the persisted audio-file map from *target*.

        Returns:
            dict  ``{lowercase_stem: absolute_path}`` or empty dict.
        """
        if not target or not pm.objExists(target):
            return {}

        node = str(target)
        if not cmds.attributeQuery(self.FILE_MAP_ATTR, node=node, exists=True):
            return {}

        raw = cmds.getAttr(f"{node}.{self.FILE_MAP_ATTR}") or ""
        if not raw:
            return {}

        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}


# ----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("audio_events", reload=True)
    ui.show(pos="screen", app_exec=True)
