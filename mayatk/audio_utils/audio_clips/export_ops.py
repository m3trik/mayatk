# !/usr/bin/python
# coding=utf-8
"""Export operations for Audio Clips.

Provides ``ExportMixin`` — handles exporting the composite WAV and
individual audio clips to disk via file dialogs.

Data model
----------
All authoritative state lives on the canonical carrier
(``data_internal``) and is read via :mod:`mayatk.audio_utils`.  Export
flows do **not** touch keyframe data — they just read the file map and,
for keyed-range suffixing, query per-track events.
"""
import logging
import math
import os
import shutil

try:
    import maya.cmds as cmds
except ImportError:
    pass

import pythontk as ptk

from mayatk.audio_utils._audio_utils import AudioUtils as _audio_utils
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips


class ExportMixin:
    """Composite and per-clip WAV export."""

    def _export(self):
        """Dispatch export based on the mode combobox selection."""
        combo = getattr(
            getattr(getattr(self.ui, "header", None), "menu", None),
            "cmb_export_mode",
            None,
        )
        mode = combo.currentText() if combo else "Composite"
        if mode == "Composite":
            self._export_composite()
        elif mode == "Keyed Tracks":
            self._export_clips(keyed_only=True)
        else:  # "All Tracks"
            self._export_clips(keyed_only=False)

    def _export_composite(self):
        """Export the scene-wide composite WAV to a user-chosen path.

        Rebuilds the composite first (via :meth:`AudioClips.sync`) so
        the on-disk file reflects the latest keys.
        """
        log = logging.getLogger(__name__)

        # Rebuild composite so exports reflect current scene state.
        result = AudioClips.sync(composite=True, activate=False)
        comp_node = result.get("composite")
        if not comp_node:
            self.ui.footer.setText("No composite WAV — key tracks and sync first.")
            return

        try:
            comp_path = cmds.getAttr(f"{comp_node}.filename") or ""
        except Exception:
            comp_path = ""
        if not comp_path or not os.path.isfile(comp_path):
            self.ui.footer.setText("Composite file not found on disk.")
            return

        # Navigate up out of the cache directory so the dialog opens in
        # the project's audio folder.
        export_dir = os.path.dirname(comp_path)
        _CACHE_DIRS = {"_audio_cache", "_maya_audio_cache"}
        while os.path.basename(export_dir) in _CACHE_DIRS:
            export_dir = os.path.dirname(export_dir)

        dest = self.sb.save_file_dialog(
            file_types=["*.wav"],
            title="Export Composite WAV",
            start_dir=os.path.join(export_dir, "audio_composite.wav"),
            filter_description="WAV Files",
        )
        if not dest:
            return

        try:
            shutil.copy2(comp_path, dest)

            trimmed = False
            trim = getattr(self.ui.header.menu, "chk_trim_silence", None)
            if trim and trim.isChecked():
                try:
                    ptk.AudioUtils.trim_silence(dest)
                    trimmed = True
                except Exception as exc:
                    log.warning("trim failed: %s", exc)

            suffix = ""
            if trim and trim.isChecked() and not trimmed:
                suffix = " (trim failed)"
            self.ui.footer.setText(
                f"Exported composite → {os.path.basename(dest)}{suffix}"
            )
            log.info("exported composite to '%s'", dest)
        except OSError as exc:
            self.ui.footer.setText(f"Export failed: {exc}")
            log.warning("export failed: %s", exc)

    def _export_clips(self, keyed_only=True):
        """Export audio clips to a user-chosen directory.

        Parameters:
            keyed_only: When True, only tracks that have start keys on
                the canonical carrier are exported.  When False, every
                registered track is exported.

        When *Suffix Time Range* is enabled (keyed-only mode), each
        filename is appended with the keyed frame range (e.g.
        ``Footstep_12-47.wav``).  If a track has multiple start keys,
        one copy per occurrence is exported.
        """
        log = logging.getLogger(__name__)

        file_map = _audio_utils.load_file_map()
        if not file_map:
            self.ui.footer.setText("No audio tracks loaded.")
            return

        # Build {track_id: [(start, end), ...]} (empty ranges for non-keyed mode)
        keyed_ranges = {}

        if keyed_only:
            fps = _audio_utils.get_fps()
            for tid in _audio_utils.list_tracks():
                events = _audio_utils.read_events(tid)
                if not events:
                    continue
                source = file_map.get(tid)
                duration = 0.0
                if source:
                    try:
                        dur, _ = _audio_utils.audio_duration_frames(source, fps)
                        duration = float(dur or 0.0)
                    except Exception:
                        duration = 0.0
                ranges = []
                for ev in events:
                    start = int(ev.start)
                    if ev.stop is not None:
                        end = int(ev.stop)
                    elif duration > 0:
                        end = int(math.ceil(ev.start + duration))
                    else:
                        end = start
                    ranges.append((start, end))
                if ranges:
                    keyed_ranges[tid] = ranges

            if not keyed_ranges:
                self.ui.footer.setText("No keyed tracks — nothing to export.")
                return
        else:
            for tid in file_map:
                keyed_ranges[tid] = []

        # Pick export directory (start from first file's dir).
        start_dir = os.path.dirname(next(iter(file_map.values()), ""))
        export_dir = self.sb.dir_dialog(
            title="Export Audio Clips",
            start_dir=start_dir,
        )
        if not export_dir:
            return

        suffix_range = getattr(
            getattr(getattr(self.ui, "header", None), "menu", None),
            "chk_suffix_time_range",
            None,
        )
        use_suffix = keyed_only and suffix_range and suffix_range.isChecked()

        exported = 0
        errors = []
        already_exported = set()

        for tid, ranges in keyed_ranges.items():
            source_path = file_map.get(tid)
            if not source_path or not os.path.isfile(source_path):
                errors.append(tid)
                continue

            ext = os.path.splitext(source_path)[1]
            original_stem = os.path.splitext(os.path.basename(source_path))[0]

            if use_suffix and ranges:
                for start_f, end_f in ranges:
                    out_name = f"{original_stem}_{start_f}-{end_f}{ext}"
                    dest = os.path.join(export_dir, out_name)
                    try:
                        shutil.copy2(source_path, dest)
                        exported += 1
                    except OSError as exc:
                        log.warning("clip export failed: %s", exc)
            else:
                if tid in already_exported:
                    continue
                already_exported.add(tid)
                dest = os.path.join(export_dir, f"{original_stem}{ext}")
                try:
                    shutil.copy2(source_path, dest)
                    exported += 1
                except OSError as exc:
                    log.warning("clip export failed: %s", exc)
                    errors.append(tid)

        suffix = f" ({len(errors)} failed)" if errors else ""
        self.ui.footer.setText(
            f"Exported {exported} clip(s) → {os.path.basename(export_dir)}{suffix}"
        )
        log.info("exported %d clip(s) to '%s'", exported, export_dir)
