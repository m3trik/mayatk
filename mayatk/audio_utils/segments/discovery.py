# !/usr/bin/python
# coding=utf-8
"""Segment discovery from the per-track keyed canonical store.

For each track attr on the carrier, the segment's timeline position is
the first ``value=1`` (start) key.  The effective end is either the
matching ``value=0`` (stop) key or the audio file's intrinsic duration.

Consumers (sequencer ``_build_audio_tracks``, manifest
``_post_build_audio``) call :func:`collect_all_segments` once per build
pass.  Results are not cached at this layer — consumers coalesce
through their own ``batch`` / compositor sync lifecycle.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from mayatk.audio_utils._audio_utils import AudioUtils


@dataclass
class AudioSegment:
    """A resolved audio segment for sequencer/manifest consumption.

    Attributes:
        track_id: Canonical track identifier.
        file_path: Source audio file path (may be non-WAV source).
        start: Timeline start frame.
        end: Timeline end frame (start + effective duration).
        duration: Effective duration in frames (truncated by stop key
            if present, else full file duration).
        label: User-facing label (defaults to track_id or file stem).
        waveform: Envelope points for rendering, empty if disabled.
    """

    track_id: str
    file_path: str
    start: float
    end: float
    duration: float
    label: str = ""
    waveform: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def is_audio(self) -> bool:
        return True


def _resolve_segments(
    track_id: str,
    file_path: str,
    fps: float,
    include_waveform: bool,
    scene_start: Optional[float],
    scene_end: Optional[float],
    carrier: str,
) -> List[AudioSegment]:
    """Materialize :class:`AudioSegment` list for a single track."""
    events = AudioUtils.read_events(track_id, carrier=carrier)
    if not events:
        return []

    duration_frames, resolved_wav = AudioUtils.audio_duration_frames(file_path, fps)
    wf = (
        AudioUtils.cached_waveform(resolved_wav)
        if include_waveform and resolved_wav and Path(resolved_wav).exists()
        else []
    )
    label = track_id

    out: List[AudioSegment] = []
    for evt in events:
        if evt.stop is not None:
            end = evt.stop
            dur = max(0.0, evt.stop - evt.start)
        else:
            dur = duration_frames
            end = evt.start + duration_frames

        # Scene-range filter (same semantics as legacy
        # AudioTrackManager): skip clips entirely outside [start, end].
        if scene_start is not None and end < scene_start:
            continue
        if scene_end is not None and evt.start > scene_end:
            continue

        out.append(
            AudioSegment(
                track_id=track_id,
                file_path=file_path,
                start=evt.start,
                end=end,
                duration=dur,
                label=label,
                waveform=wf,
            )
        )
    return out


def collect_all_segments(
    scene_start: Optional[float] = None,
    scene_end: Optional[float] = None,
    include_waveform: bool = True,
    carrier: Optional[str] = None,
) -> List[AudioSegment]:
    """Return every :class:`AudioSegment` visible on the canonical carrier.

    Parameters:
        scene_start: Filter out segments that end before this frame.
        scene_end: Filter out segments that start after this frame.
        include_waveform: When True, attach cached PCM envelopes.
        carrier: Override carrier; default = first discovered carrier.

    Returns:
        Segments sorted by ``start`` frame.
    """
    carriers = AudioUtils.find_carriers() if carrier is None else [carrier]
    if not carriers:
        return []

    fps = AudioUtils.get_fps()
    segments: List[AudioSegment] = []

    for node in carriers:
        file_map = AudioUtils.load_file_map(node)
        for tid in AudioUtils.list_tracks(node):
            file_path = file_map.get(tid, "")
            if not file_path:
                continue
            segments.extend(
                _resolve_segments(
                    tid,
                    file_path,
                    fps,
                    include_waveform,
                    scene_start,
                    scene_end,
                    carrier=node,
                )
            )

    segments.sort(key=lambda s: s.start)
    return segments


def collect_segments_for_track(
    track_id: str,
    include_waveform: bool = True,
    carrier: Optional[str] = None,
) -> List[AudioSegment]:
    """Return segments for a single *track_id*.

    Used by sequencer incremental refresh when a single track's keys
    changed.
    """
    node = carrier or AudioUtils.CARRIER_NODE
    file_map = AudioUtils.load_file_map(node)
    file_path = file_map.get(track_id, "")
    if not file_path:
        return []
    return _resolve_segments(
        track_id,
        file_path,
        AudioUtils.get_fps(),
        include_waveform,
        None,
        None,
        carrier=node,
    )
