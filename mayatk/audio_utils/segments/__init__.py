# !/usr/bin/python
# coding=utf-8
"""Consumer-facing discovery helpers for sequencer + manifest.

Produces :class:`~mayatk.audio_utils.AudioSegment` lists derived from
the per-track keyed canonical store on ``data_internal``.  Replaces
the legacy ``AudioTrackManager`` which read from DG audio nodes plus
a separate enum-driven AudioClips carrier.

The sequencer and manifest are the only intended consumers.  UI code
should treat :class:`AudioSegment` as read-only snapshots — mutations
go through the ``audio_utils`` primitive API (``write_key``,
``shift_keys_in_range``, ``set_path``) plus ``audio_utils.sync()``.
"""
from mayatk.audio_utils.segments.discovery import (
    AudioSegment,
    collect_all_segments,
    collect_segments_for_track,
)

__all__ = [
    "AudioSegment",
    "collect_all_segments",
    "collect_segments_for_track",
]
