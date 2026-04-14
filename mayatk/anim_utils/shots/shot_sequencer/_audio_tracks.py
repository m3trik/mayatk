# coding=utf-8
"""Audio track management for the Slot Sequencer.

Discovers Maya ``audio`` nodes from the scene (or from an FBX import)
and exposes them as clip-like segments suitable for the
:class:`~uitk.widgets.sequencer._sequencer.SequencerWidget`.

Waveform data (min/max envelope) is computed from the WAV file so the
widget can paint it inside the clip rectangle.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import json
import logging
import wave

try:
    import pymel.core as pm
    from maya import cmds
except ImportError:
    pm = None
    cmds = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class AudioClipInfo:
    """Lightweight descriptor for one Maya audio node."""

    node_name: str
    file_path: str
    offset: float  # frame offset on the timeline
    duration_frames: float  # length in frames (based on fps)
    sample_rate: int = 44100
    num_channels: int = 1
    num_frames: int = 0  # total PCM frames

    @property
    def end_frame(self) -> float:
        return self.offset + self.duration_frames


# ---------------------------------------------------------------------------
# Waveform helpers — delegated to pythontk
# ---------------------------------------------------------------------------

from pythontk.audio_utils._audio_utils import AudioUtils

compute_waveform_envelope = AudioUtils.compute_waveform_envelope


# ---------------------------------------------------------------------------
# AudioTrackManager (Maya-dependent)
# ---------------------------------------------------------------------------


def _get_fps() -> float:
    """Resolve the current scene FPS once."""
    if pm is not None:
        return pm.mel.eval("float $fps = `currentTimeUnitToFPS`")
    return 24.0


class AudioTrackManager:
    """Discovers and manages Maya audio nodes for sequencer integration.

    Instantiate once and reuse — the instance caches waveform envelopes
    and resolved audio segments so that repeated ``_sync_to_widget``
    calls do not redundantly read WAV headers.

    Call :meth:`invalidate` when the scene changes to clear cached data.
    """

    def __init__(self):
        self._waveform_cache: Dict[str, List[tuple]] = {}
        self._segment_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_range: Optional[tuple] = None

    def invalidate(self) -> None:
        """Clear cached segments so the next query re-reads the scene."""
        self._segment_cache = None
        self._cache_range = None

    def _cached_waveform(self, wav_path: str) -> List[tuple]:
        """Return a waveform envelope, using a per-path cache."""
        if wav_path not in self._waveform_cache:
            self._waveform_cache[wav_path] = compute_waveform_envelope(wav_path)
        return self._waveform_cache[wav_path]

    def find_audio_nodes(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
    ) -> List[AudioClipInfo]:
        """Return :class:`AudioClipInfo` for every ``audio`` DG node.

        Parameters:
            start: If given, only include nodes whose offset falls at or
                after this frame.
            end: If given, only include nodes that start before this frame.
        """
        if cmds is None:
            return []

        fps = _get_fps()
        nodes = cmds.ls(type="audio") or []
        result: List[AudioClipInfo] = []

        for node_name in nodes:
            file_path = cmds.getAttr(f"{node_name}.filename") or ""
            offset = cmds.getAttr(f"{node_name}.offset") or 0.0

            duration_frames = 0.0
            sample_rate = 44100
            num_channels = 1
            num_pcm_frames = 0

            if file_path and Path(file_path).exists():
                try:
                    with wave.open(file_path, "rb") as wf:
                        sample_rate = wf.getframerate()
                        num_channels = wf.getnchannels()
                        num_pcm_frames = wf.getnframes()
                        duration_seconds = num_pcm_frames / sample_rate
                        duration_frames = duration_seconds * fps
                except Exception:
                    logger.warning("Cannot read audio file: %s", file_path)

            clip = AudioClipInfo(
                node_name=node_name,
                file_path=file_path,
                offset=offset,
                duration_frames=duration_frames,
                sample_rate=sample_rate,
                num_channels=num_channels,
                num_frames=num_pcm_frames,
            )

            if start is not None and clip.end_frame < start:
                continue
            if end is not None and clip.offset > end:
                continue

            result.append(clip)

        result.sort(key=lambda c: c.offset)
        return result

    def collect_audio_segments(
        self,
        scene_start: Optional[float] = None,
        scene_end: Optional[float] = None,
        include_waveform: bool = True,
    ) -> List[Dict[str, Any]]:
        """Return audio clip dicts from DG ``audio`` nodes.

        Each dict contains ``node``, ``file_path``, ``start``, ``end``,
        ``duration``, ``label``, ``waveform``, ``is_audio``.
        """
        clips = self.find_audio_nodes(scene_start, scene_end)
        segments: List[Dict[str, Any]] = []

        for clip in clips:
            seg: Dict[str, Any] = {
                "node": clip.node_name,
                "file_path": clip.file_path,
                "start": clip.offset,
                "end": clip.end_frame,
                "duration": clip.duration_frames,
                "label": (
                    Path(clip.file_path).stem if clip.file_path else clip.node_name
                ),
                "is_audio": True,
                "audio_source": "dg",
            }
            if include_waveform and clip.file_path and Path(clip.file_path).exists():
                seg["waveform"] = self._cached_waveform(clip.file_path)
            else:
                seg["waveform"] = []

            segments.append(seg)

        return segments

    # ------------------------------------------------------------------
    # AudioEvents (keyed enum triggers on locators) discovery
    # ------------------------------------------------------------------

    _TRIGGER_ATTR = "audio_trigger"
    _FILEMAP_ATTR = "audio_file_map"

    @staticmethod
    def find_event_carriers() -> List[str]:
        """Return names of transforms that carry AudioEvents trigger attrs."""
        if cmds is None:
            return []
        carriers = []
        for node in cmds.ls(type="transform") or []:
            if cmds.attributeQuery(
                AudioTrackManager._TRIGGER_ATTR, node=node, exists=True
            ):
                carriers.append(node)
        return sorted(carriers)

    @staticmethod
    def _parse_enum_labels(node: str) -> List[str]:
        """Return the ordered enum label list for *audio_trigger*."""
        raw = cmds.attributeQuery(
            AudioTrackManager._TRIGGER_ATTR, node=node, listEnum=True
        )
        if not raw:
            return []
        return raw[0].split(":")

    @staticmethod
    def _load_file_map(node: str) -> Dict[str, str]:
        """Load the JSON file map from *audio_file_map* attr."""
        attr = f"{node}.{AudioTrackManager._FILEMAP_ATTR}"
        if not cmds.objExists(attr):
            return {}
        raw = cmds.getAttr(attr) or ""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid JSON in %s", attr)
            return {}

    @staticmethod
    def _get_audio_duration_frames(file_path: str, fps: float) -> Tuple[float, str]:
        """Return ``(duration_frames, resolved_path)`` for an audio file.

        Handles WAV natively; non-WAV files return 0 duration unless a
        converted .wav exists in a sibling ``_maya_audio_cache`` dir.
        The cache file may have a hash suffix (e.g. ``stem_abc123.wav``).
        """
        p = Path(file_path)
        wav_path = file_path
        if p.suffix.lower() not in (".wav", ".aif", ".aiff"):
            cache_dir = p.parent / "_maya_audio_cache"
            cached = None
            exact = cache_dir / (p.stem + ".wav")
            if exact.exists():
                cached = exact
            elif cache_dir.exists():
                candidates = list(cache_dir.glob(f"{p.stem}_*.wav"))
                if len(candidates) > 1:
                    logger.warning(
                        "Multiple cache hits for %s: %s — using first",
                        p.stem,
                        [c.name for c in candidates],
                    )
                if candidates:
                    cached = candidates[0]
            if cached:
                wav_path = str(cached)
            else:
                return 0.0, file_path

        try:
            with wave.open(wav_path, "rb") as wf:
                dur_secs = wf.getnframes() / wf.getframerate()
                return dur_secs * fps, wav_path
        except Exception:
            logger.debug("Cannot read WAV for duration: %s", wav_path)
            return 0.0, file_path

    def collect_event_audio_segments(
        self,
        scene_start: Optional[float] = None,
        scene_end: Optional[float] = None,
        include_waveform: bool = True,
    ) -> List[Dict[str, Any]]:
        """Discover audio clips from AudioEvents keyed-enum carriers.

        Each keyed non-None event on an ``audio_trigger`` enum produces
        one segment.  The segment starts at the key's frame and its
        duration is derived from the audio file length.
        """
        if cmds is None:
            return []

        fps = _get_fps()
        carriers = self.find_event_carriers()
        segments: List[Dict[str, Any]] = []

        for carrier in carriers:
            labels = self._parse_enum_labels(carrier)
            file_map = self._load_file_map(carrier)
            if not labels or not file_map:
                continue

            attr = f"{carrier}.{self._TRIGGER_ATTR}"
            keys = cmds.keyframe(attr, q=True) or []
            vals = cmds.keyframe(attr, q=True, valueChange=True) or []

            for frame, val in zip(keys, vals):
                idx = int(round(val))
                if idx < 0 or idx >= len(labels):
                    continue
                label = labels[idx]
                if label.lower() == "none":
                    continue

                file_path = file_map.get(label.lower(), "")
                if not file_path:
                    continue

                dur_frames, resolved = self._get_audio_duration_frames(file_path, fps)
                end_frame = frame + dur_frames

                if scene_start is not None and end_frame < scene_start:
                    continue
                if scene_end is not None and frame > scene_end:
                    continue

                seg: Dict[str, Any] = {
                    "node": carrier,
                    "file_path": file_path,
                    "start": frame,
                    "end": end_frame,
                    "duration": dur_frames,
                    "label": label,
                    "is_audio": True,
                    "audio_source": "event",
                    "event_key_frame": frame,
                }

                if include_waveform and Path(resolved).exists():
                    seg["waveform"] = self._cached_waveform(resolved)
                else:
                    seg["waveform"] = []

                segments.append(seg)

        segments.sort(key=lambda s: s["start"])
        return segments

    def collect_all_audio_segments(
        self,
        scene_start: Optional[float] = None,
        scene_end: Optional[float] = None,
        include_waveform: bool = True,
    ) -> List[Dict[str, Any]]:
        """Collect audio segments from both DG audio nodes and event triggers.

        Results are cached per ``(scene_start, scene_end)`` range.
        Call :meth:`invalidate` to force a refresh.
        """
        cache_key = (scene_start, scene_end, include_waveform)
        if self._segment_cache is not None and self._cache_range == cache_key:
            return self._segment_cache

        dg = self.collect_audio_segments(scene_start, scene_end, include_waveform)
        events = self.collect_event_audio_segments(
            scene_start, scene_end, include_waveform
        )
        combined = dg + events
        combined.sort(key=lambda s: s["start"])
        self._segment_cache = combined
        self._cache_range = cache_key
        return combined

    @staticmethod
    def set_audio_offset(node_name: str, new_offset: float) -> None:
        """Move a DG audio node to a new timeline offset."""
        if cmds is None:
            return
        cmds.setAttr(f"{node_name}.offset", new_offset)

    @staticmethod
    def move_event_key(carrier: str, old_frame: float, new_frame: float) -> None:
        """Shift a keyed audio event trigger from *old_frame* to *new_frame*."""
        if cmds is None:
            return
        attr = f"{carrier}.{AudioTrackManager._TRIGGER_ATTR}"
        cmds.keyframe(
            attr, edit=True, time=(old_frame, old_frame), timeChange=new_frame
        )
