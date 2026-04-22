# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Audio Clips UI.

Provides ``AudioClipsSlots`` — a standalone window for registering
audio files as tracks, keying them on the timeline, and driving the
scene-wide composite WAV for Time-Slider scrubbing.

Single-scope model
------------------
All state lives on the canonical carrier (``data_internal``).  Tracks
are per-track enum attrs (``audio_clip_<track_id>``) with keyed
``off`` (value=0) / ``on`` (value=1) values.  The UI has no object
picker — selection is decoupled from track state.

Combo reflects the track currently "on" at the playhead.  Selecting a
track in the combo previews its audio DG node on the Time Slider; it
does *not* modify any attribute.
"""
import logging
import math
import os

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError:
    pass

import pythontk as ptk

from mayatk.audio_utils._audio_utils import AudioUtils as _audio_utils
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips
from mayatk.audio_utils.audio_clips.callbacks import CallbacksMixin
from mayatk.audio_utils.audio_clips.export_ops import ExportMixin


class AudioClipsSlots(ExportMixin, CallbacksMixin):
    """Switchboard slots for the Audio Clips UI.

    Layout
    ------
    - **Header**: Auto Convert, Export mode, Trim Silence, Suffix Range,
      Instructions.
    - **Tracks combo** (``cmb000``): lists every track on the carrier
      with a Browse option box + Tracks management menu.
    - **Sync** (``tb000``): reconcile DG nodes + rebuild composite.
    - **Key Audio Event** (``tb001``): key the selected track at the
      current frame (with Auto End None / Next Event / Key All).
    - **Remove** (``b002``): purge every track + DG node + composite.
    - **Footer**: status messages.
    """

    AUDIO_FILTER = (
        "Audio Files (*.wav *.aif *.aiff *.mp3 *.ogg *.m4a *.flac);;" "All Files (*)"
    )

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.audio_clips

        self._time_token = None
        self._scene_subs_installed = False
        self._attr_callback_ids = []
        self._syncing_combo = False
        self._last_active_tid = None
        self._deferred_sync_pending = False

        try:
            cmds.evalDeferred(self._ensure_sync_job)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu with tool description and workflow instructions."""
        widget.menu.add("Separator", setTitle="Import")
        widget.menu.add(
            "QCheckBox",
            setText="Auto Convert",
            setObjectName="chk_auto_convert",
            setToolTip=(
                "When enabled, non-Maya-playable audio formats\n"
                "(MP3, OGG, M4A, FLAC, etc.) are automatically\n"
                "converted to WAV on import via FFmpeg.\n\n"
                "When disabled, only natively playable formats\n"
                "(WAV, AIF, AIFF) are accepted — other formats\n"
                "are silently skipped."
            ),
            setChecked=True,
        )
        widget.menu.add("Separator", setTitle="Export")
        widget.menu.add(
            "QComboBox",
            setObjectName="cmb_export_mode",
            setToolTip=(
                "Choose what to export:\n"
                "• Composite — single mixed WAV of all keyed clips.\n"
                "• Keyed Tracks — individual source clips that are\n"
                "  keyed on the timeline (unused clips skipped).\n"
                "• All Tracks — every loaded clip regardless of\n"
                "  whether it has been keyed."
            ),
            addItems=["Composite", "Keyed Tracks", "All Tracks"],
        )
        btn_export = widget.menu.add(
            "QPushButton",
            setText="Export",
            setObjectName="btn_export",
            setToolTip="Export audio using the selected mode above.",
        )
        btn_export.clicked.connect(self._export)
        widget.menu.add(
            "QCheckBox",
            setText="Trim Silence",
            setObjectName="chk_trim_silence",
            setToolTip=(
                "When exporting Composite, remove leading and\n"
                "trailing silence from the exported WAV."
            ),
            setChecked=True,
        )
        widget.menu.add(
            "QCheckBox",
            setText="Suffix Time Range",
            setObjectName="chk_suffix_time_range",
            setToolTip=(
                "When exporting Keyed Tracks, append the keyed\n"
                "frame range to each filename.\n\n"
                "Example: Footstep_12-47.wav\n"
                "(start frame 12, end frame 47)"
            ),
            setChecked=False,
        )

        widget.menu.add("Separator", setTitle="Inspect")
        btn_attrs = widget.menu.add(
            "QPushButton",
            setText="Attribute Manager",
            setObjectName="btn_attribute_manager",
            setToolTip=(
                "Open the Attribute Manager pinned to the audio\n"
                "carrier node, filtered to per-track attributes."
            ),
        )
        btn_attrs.clicked.connect(self._launch_attribute_manager)

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Audio Clips — Scene-wide audio tracks keyed on the\n"
                "canonical data node; drives a single composite WAV for\n"
                "Time-Slider scrubbing.\n\n"
                "Workflow:\n"
                "  1. Click the folder icon on the tracks combo to\n"
                "     browse for audio files.\n"
                "     • File stems become track IDs.\n"
                "     • Re-adding a file with the same stem replaces\n"
                "       the path (keyframes are preserved).\n"
                "  2. Select a loaded track in the combo.\n"
                "  3. Move the timeline cursor to the desired start frame.\n"
                "  4. Press 'Key Audio Event' to key the track ON (value=1).\n"
                "     • Enable 'Auto End None' (option box ▸) to auto-key\n"
                "       an OFF value (0) at the clip's end frame.\n"
                "     • Enable 'Next Event' to auto-advance through tracks.\n"
                "  5. Repeat for each audio cue.\n"
                "  6. Click the refresh (↻) icon on the Key Audio Event\n"
                "     option box to sync DG nodes and rebuild the\n"
                "     composite WAV for scrub playback.\n\n"
                "Note: Adding or replacing tracks while keyed events exist\n"
                "triggers an automatic sync, so the composite reflects\n"
                "the updated audio immediately."
            ),
        )

    def _launch_attribute_manager(self, *_):
        """Open the Attribute Manager pinned to the audio carrier node."""
        carrier = _audio_utils.CARRIER_NODE
        if not cmds.objExists(carrier):
            self.sb.message_box(
                f"Carrier node '{carrier}' does not exist yet.\n"
                "Load or key an audio track first."
            )
            return

        from mayatk.node_utils.attributes.attribute_manager import launch

        launch(sb=self.sb, targets=[carrier], filter="Custom", search="audio_clip_*")

# ------------------------------------------------------------------
    # Tracks combo
    # ------------------------------------------------------------------

    def cmb000_init(self, widget):
        """Init track combo with browse option_box and management menu."""
        from uitk.widgets.optionBox.options.browse import BrowseOption

        widget.option_box.add_option(
            BrowseOption(
                wrapped_widget=widget,
                file_types=self.AUDIO_FILTER,
                mode="files",
                title="Select Audio Tracks",
                tooltip="Browse for audio files to add or replace tracks.",
                callback=self._browse_audio_files_cb,
            )
        )

        widget.option_box.menu.setTitle("Tracks")

        btn_rename = widget.option_box.menu.add(
            "QPushButton",
            setText="Rename Track",
            setObjectName="btn_rename_track",
            setToolTip=(
                "Rename the currently selected track.\n"
                "Updates the track id, attr name, DG node, and file\n"
                "map.  Keyframes are preserved."
            ),
        )
        btn_rename.clicked.connect(self.b006)

        btn_replace = widget.option_box.menu.add(
            "QPushButton",
            setText="Replace Track",
            setObjectName="btn_replace_track",
            setToolTip=(
                "Replace the selected track with a different audio file.\n"
                "Preserves the track id and all keyframes."
            ),
        )
        btn_replace.clicked.connect(self.b005)

        btn_cleanup = widget.option_box.menu.add(
            "QPushButton",
            setText="Cleanup Unused",
            setObjectName="btn_cleanup_unused",
            setToolTip=(
                "Remove tracks that have no keyframes and delete\n" "their DG nodes."
            ),
        )
        btn_cleanup.clicked.connect(self.b004)

        btn_remove = widget.option_box.menu.add(
            "QPushButton",
            setText="Remove Audio",
            setObjectName="btn_remove_audio",
            setToolTip=(
                "Delete every track, its DG node, the composite WAV,\n"
                "and the file map."
            ),
        )
        btn_remove.clicked.connect(self.b002)

        self._ensure_sync_job()

    def _browse_audio_files_cb(self, paths):
        """BrowseOption callback — import selected audio file paths."""
        if paths:
            self._import_audio_paths(paths if isinstance(paths, list) else [paths])

    @CoreUtils.undoable
    def _import_audio_paths(self, paths):
        """Register *paths* as tracks and sync if keys already exist.

        Re-adding a file with the same stem replaces the path (same
        track id = same attr = same keyframes).
        """
        paths = self._prepare_selected_paths(paths)
        if not paths:
            self.ui.footer.setText("No audio files selected for import.")
            return

        tids = AudioClips.load_tracks(paths)
        if not tids:
            self.ui.footer.setText("No valid tracks registered.")
            return

        # If any track has existing keys, re-sync so the composite WAV
        # reflects the updated file paths.
        has_keys = any(
            _audio_utils.read_keys(tid) for tid in _audio_utils.list_tracks()
        )
        self._refresh_combo(_audio_utils.list_tracks())
        if has_keys:
            result = AudioClips.sync()
            count = len(result.get("created", [])) + len(result.get("updated", []))
            self.ui.footer.setText(
                f"Registered {len(tids)} track(s), synced {count} DG node(s)."
            )
        else:
            self.ui.footer.setText(f"Registered {len(tids)} track(s).")

    def _prepare_selected_paths(self, paths):
        """Filter selected paths and prompt for FFmpeg conversion if needed."""
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
            auto_convert = getattr(
                getattr(getattr(self.ui, "header", None), "menu", None),
                "chk_auto_convert",
                None,
            )
            allow_convert = (
                auto_convert.isChecked() if auto_convert is not None else True
            )
            if not allow_convert:
                return playable

            if not ptk.AudioUtils.resolve_ffmpeg(required=False):
                reply = QMessageBox.question(
                    self.ui,
                    "FFmpeg Not Found",
                    "FFmpeg is required to convert selected files to WAV.\n\n"
                    "Would you like to download and install it now?\n"
                    "(This may take a moment for the initial download.)",
                    QMessageBox.Yes,
                    QMessageBox.No,
                )
                if reply == QMessageBox.Yes:
                    self.ui.footer.setText("Downloading FFmpeg …")
                    from qtpy.QtWidgets import QApplication

                    QApplication.processEvents()
                    path = ptk.AudioUtils.resolve_ffmpeg(
                        required=False, auto_install=True
                    )
                    self.ui.footer.setText("")
                    if not path:
                        QMessageBox.warning(
                            self.ui,
                            "Install Failed",
                            "FFmpeg could not be installed automatically.\n"
                            "Please install it manually and ensure it is on "
                            "your system PATH.",
                        )
                        return playable
                else:
                    return playable

            return playable + convertible

        return playable

    @CoreUtils.undoable
    def _cleanup_unused_tracks(self):
        """Delete tracks that have no keyframes and their DG nodes."""
        all_tracks = _audio_utils.list_tracks()
        unused = [tid for tid in all_tracks if not _audio_utils.read_keys(tid)]
        if not unused:
            self.ui.footer.setText("All tracks are keyed — nothing to clean up.")
            return

        with _audio_utils.batch():
            for tid in unused:
                _audio_utils.delete_track(tid)
                _audio_utils.remove_path(tid)

        AudioClips.sync()
        self._refresh_combo(_audio_utils.list_tracks())
        self.ui.footer.setText(f"Cleaned up {len(unused)} unused track(s).")

    def cmb000(self, index, widget):
        """Track selection — activate the track's DG node on the Time Slider.

        Selecting in the combo does NOT write to any attr — it just
        previews the track's audio on the Time Slider.  Use the Key
        Audio Event button to author keys.
        """
        if self._syncing_combo:
            return
        label = widget.currentText()
        if not label:
            return

        node = _audio_utils.find_dg_node_for_track(label)
        if node:
            try:
                AudioClips.set_active(node)
                self.ui.footer.setText(f"Active: {node}")
            except Exception:
                pass
        else:
            self.ui.footer.setText(f"No DG node for '{label}' — sync to create it.")

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def tb000(self, widget=None):
        """Sync Audio to Timeline — reconcile DG nodes and rebuild composite."""
        if not _audio_utils.list_tracks():
            self.ui.footer.setText("No tracks — browse for audio files first.")
            return

        result = AudioClips.sync()
        total = (
            len(result.get("created", []))
            + len(result.get("updated", []))
            + len(result.get("deleted", []))
        )
        comp = "composite rebuilt" if result.get("composite") else "no composite"
        if total == 0 and not result.get("composite"):
            self.ui.footer.setText("Already synced — nothing changed.")
        else:
            self.ui.footer.setText(f"Synced {total} DG change(s); {comp}.")

    # ------------------------------------------------------------------
    # Key Audio Event
    # ------------------------------------------------------------------

    def tb001_init(self, widget):
        """Init Key Audio Event option-box menu."""
        widget.option_box.set_action(
            self._select_carrier,
            icon="select",
            tooltip=(
                "Select the data_internal node in the viewport so "
                "its track attrs appear in the Channel Box."
            ),
        )
        widget.option_box.add_action(
            self.tb000,
            icon="refresh",
            tooltip=(
                "Sync audio to timeline.\n"
                "Reconciles DG nodes and rebuilds the composite WAV\n"
                "from the current track map and keyframes."
            ),
        )
        widget.option_box.menu.setTitle("Key Audio Event")
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Auto End None",
            setObjectName="chk_auto_end_none",
            setToolTip=(
                "When enabled, keying a track will also auto-key an\n"
                "OFF value at the clip's end frame (start + clip length).\n"
                "Useful for Graph Editor visualization and for the\n"
                "sequencer to know clip boundaries."
            ),
        )
        chk_snap = widget.option_box.menu.add(
            "QCheckBox",
            setText="Snap To Frame",
            setObjectName="chk_snap_frames",
            setChecked=_audio_utils.get_snap_frames(),
            setToolTip=(
                "Round audio key times to the nearest whole frame.\n"
                "Applies globally to all audio key writes.  Disable\n"
                "only if you need sub-frame precision."
            ),
        )
        chk_snap.toggled.connect(
            lambda checked: _audio_utils.set_snap_frames(checked)
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Next Event",
            setObjectName="chk_next_event",
            setToolTip=(
                "Automatically key the next track in the list.\n"
                "The next track is determined by the most recent key\n"
                "across all tracks:\n"
                "  • If any track has been keyed, the track after the\n"
                "    most-recently-keyed one is used (wrapping around).\n"
                "  • Otherwise, the first track is used.\n"
                "The combo selection updates to reflect the chosen track."
            ),
        )
        chk_key_all = widget.option_box.menu.add(
            "QCheckBox",
            setText="Key All",
            setObjectName="chk_key_all",
            setToolTip=(
                "Key every loaded track sequentially starting from the\n"
                "current frame.  Each clip is placed end-to-end using\n"
                "clip length plus the stagger amount as spacing.\n"
                "When Auto End None is disabled, track length is still\n"
                "used to calculate the stagger interval."
            ),
        )
        spn_stagger = widget.option_box.menu.add(
            "QSpinBox",
            setText="Stagger",
            setObjectName="spn_stagger",
            setToolTip=(
                "Extra frames added between each clip when Key All\n"
                "is enabled.  Interval = clip_length + stagger."
            ),
            setMinimum=0,
            setMaximum=9999,
            setValue=0,
            setPrefix="Stagger: ",
            setSuffix=" fr",
        )

        def _on_key_all_toggled(checked):
            spn_stagger.setEnabled(checked)
            if checked:
                widget.option_box.menu.chk_next_event.setChecked(False)
            widget.option_box.menu.chk_next_event.setEnabled(not checked)

        chk_key_all.toggled.connect(_on_key_all_toggled)
        spn_stagger.setEnabled(chk_key_all.isChecked())

    @CoreUtils.undoable
    def tb001(self, widget=None):
        """Key Audio Event — write ON (1) at current frame, optionally OFF at end."""
        tracks = _audio_utils.list_tracks()
        if not tracks:
            self.ui.footer.setText("No tracks — browse for audio files first.")
            return

        key_all = widget.option_box.menu.chk_key_all.isChecked()
        auto_end = self.ui.tb001.option_box.menu.chk_auto_end_none.isChecked()

        if key_all:
            self._key_all_tracks(widget, tracks, auto_end)
            return

        next_event = widget.option_box.menu.chk_next_event.isChecked()
        if next_event:
            tid = self._resolve_next_track(tracks)
            if not tid:
                self.ui.footer.setText("No audio tracks available.")
                return
            self._syncing_combo = True
            try:
                idx = self.ui.cmb000.findText(tid)
                if idx >= 0:
                    self.ui.cmb000.setCurrentIndex(idx)
            finally:
                self._syncing_combo = False
        else:
            tid = self.ui.cmb000.currentText()
            if not tid:
                self.ui.footer.setText("Select an audio track first.")
                return

        current_frame = float(pm.currentTime(query=True))
        self._write_track_keys(tid, current_frame, auto_end=auto_end)

        result = AudioClips.sync()
        total = len(result.get("created", [])) + len(result.get("updated", []))
        end_info = " + end-off" if auto_end else ""
        self.ui.footer.setText(
            f"Keyed '{tid}' @ {int(current_frame)}{end_info}"
            f" — {total} DG node(s) synced."
        )

    def _select_carrier(self):
        """Select the data_internal node in the Maya viewport."""
        carrier = _audio_utils.CARRIER_NODE
        if not cmds.objExists(carrier):
            self.ui.footer.setText(f"'{carrier}' does not exist yet.")
            return
        pm.select(carrier, replace=True)
        self.ui.footer.setText(f"Selected '{carrier}'.")

    def _key_all_tracks(self, widget, tracks, auto_end):
        """Key every track sequentially from the current frame."""
        stagger = widget.option_box.menu.spn_stagger.value()
        current_frame = float(pm.currentTime(query=True))
        cursor = current_frame
        keyed_count = 0

        with _audio_utils.batch():
            for tid in tracks:
                duration = self._get_clip_length_frames(tid)
                self._write_track_keys(
                    tid, cursor, auto_end=auto_end, duration=duration
                )
                keyed_count += 1
                if duration > 0:
                    cursor = math.ceil(cursor + duration) + stagger
                else:
                    cursor += max(stagger, 1)

        result = AudioClips.sync()
        total = len(result.get("created", [])) + len(result.get("updated", []))
        end_info = " + end-off" if auto_end else ""
        stagger_info = f" stagger={stagger}" if stagger else ""
        self.ui.footer.setText(
            f"Key All: {keyed_count} track(s){end_info}{stagger_info}"
            f" — {total} DG node(s) synced."
        )

    def _write_track_keys(self, tid, frame, auto_end=False, duration=None):
        """Write an ON key (and optionally an end-off key) for *tid*.

        With per-track attrs each track is independent, so there is no
        cross-track collision to worry about.  Any existing key at
        *frame* is overwritten; the optional end-off key is suppressed
        when a later start key of the same track would be inside the
        clip's footprint.
        """
        eps = 1e-3
        _audio_utils.write_key(tid, frame, 1)

        if not auto_end:
            return

        if duration is None:
            duration = self._get_clip_length_frames(tid)
        if duration <= 0:
            return
        end_frame = math.ceil(frame + duration)

        # Don't stomp a later start key of the same track.
        existing = _audio_utils.read_keys(tid)
        for f, v in existing:
            if abs(f - frame) < eps:
                continue
            if f > frame and f <= end_frame and int(round(v)) >= 1:
                # Another start on this track already inside the
                # clip's footprint — leave the end-off out.
                return

        _audio_utils.write_key(tid, end_frame, 0)

    def _resolve_next_track(self, tracks):
        """Return the track to key next based on the most recent key."""
        if not tracks:
            return None

        # Find the most-recently-keyed track by scanning every track's
        # latest start key and picking the one with the highest frame.
        latest_tid = None
        latest_frame = float("-inf")
        for tid in tracks:
            for f, v in _audio_utils.read_keys(tid):
                if int(round(v)) >= 1 and f > latest_frame:
                    latest_frame = f
                    latest_tid = tid

        if latest_tid is None:
            return tracks[0]

        try:
            idx = tracks.index(latest_tid)
            return tracks[(idx + 1) % len(tracks)]
        except ValueError:
            return tracks[0]

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    def b002(self):
        """Remove Audio — nuke every track, DG node, and the composite."""
        count = AudioClips.remove()
        self._refresh_combo([])
        self.ui.footer.setText(
            f"Removed {count} track(s)." if count else "Nothing to remove."
        )

    def b004(self):
        """Cleanup Unused — delete unkeyed tracks and their DG nodes."""
        self._cleanup_unused_tracks()

    @CoreUtils.undoable
    def b005(self):
        """Replace Selected Track — swap the selected track's audio file."""
        from qtpy.QtWidgets import QFileDialog

        current = self.ui.cmb000.currentText()
        if not current:
            self.ui.footer.setText("Select a track in the combo first.")
            return

        paths, _ = QFileDialog.getOpenFileNames(
            self.ui, "Replace Track Audio", "", self.AUDIO_FILTER
        )
        if not paths:
            return

        paths = self._prepare_selected_paths(paths)
        if not paths:
            return

        new_path = paths[0].replace("\\", "/")
        new_stem = os.path.splitext(os.path.basename(new_path))[0]
        try:
            new_tid = _audio_utils.normalize_track_id(new_stem)
        except ValueError as exc:
            self.ui.footer.setText(f"Invalid track name: {exc}")
            return

        if new_tid == current:
            # Same stem — just swap the path, keys stay put.
            _audio_utils.set_path(current, new_path)
        else:
            # Rename first, then update path (rename migrates keys to new attr).
            try:
                _audio_utils.rename_track(current, new_tid)
            except ValueError as exc:
                self.ui.footer.setText(f"Rename failed: {exc}")
                return
            _audio_utils.set_path(new_tid, new_path)

        AudioClips.sync()
        self._refresh_combo(_audio_utils.list_tracks())
        self.ui.footer.setText(f"Replaced '{current}' → '{new_tid}'.")

    @CoreUtils.undoable
    def b006(self):
        """Rename Track — rename the currently selected track's id."""
        current = self.ui.cmb000.currentText()
        if not current:
            self.ui.footer.setText("Select a track in the combo first.")
            return

        new_name = self.sb.input_dialog(
            title="Rename Track",
            label=f"Rename '{current}' to:",
            text=current,
            parent=self.ui,
            placeholder="e.g. footstep_left",
            validate=lambda t: bool(t.strip()) and t.strip() != current,
            error_text="Name cannot be empty or unchanged.",
        )
        if not new_name:
            return

        try:
            new_tid = _audio_utils.normalize_track_id(new_name)
        except ValueError as exc:
            self.ui.footer.setText(f"Invalid track name: {exc}")
            return

        try:
            _audio_utils.rename_track(current, new_tid)
        except ValueError as exc:
            self.ui.footer.setText(f"Rename failed: {exc}")
            return

        AudioClips.sync()
        self._refresh_combo(_audio_utils.list_tracks())
        self.ui.footer.setText(f"Renamed '{current}' → '{new_tid}'.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_combo(self, tracks):
        """Repopulate the tracks combo with *tracks* (list of track_ids)."""
        cmb = self.ui.cmb000
        self._syncing_combo = True
        try:
            cmb.blockSignals(True)
            cmb.clear()
            if tracks:
                cmb.addItems(tracks)
            cmb.blockSignals(False)
            cmb.repaint()
        finally:
            self._syncing_combo = False

    def _get_clip_length_frames(self, tid):
        """Return the clip duration in timeline frames for *tid*.

        Uses the file map to locate the source, then queries duration
        via :func:`audio_utils.audio_duration_frames`.  Returns 0.0 when
        the source is missing or unreadable.
        """
        path = _audio_utils.get_path(tid)
        if not path:
            return 0.0
        try:
            dur, _ = _audio_utils.audio_duration_frames(path, _audio_utils.get_fps())
            return float(dur or 0.0)
        except Exception:
            return 0.0
