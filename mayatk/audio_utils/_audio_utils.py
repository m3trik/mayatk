# !/usr/bin/python
# coding=utf-8
"""Unified audio system for Maya scenes.

Core audio utilities: schema constants, track identity, carrier
discovery, file-map CRUD, FPS resolution, waveform helpers, and
per-track keyed-event primitives.

Companion modules provide orthogonal concerns:

- :mod:`.nodes`        — low-level DG audio-node primitives
- :mod:`.compositor`   — DG-node reconciliation from keyed state
- :mod:`.batch`        — undo-chunk + compositor-sync orchestration
- :mod:`.migrate`      — legacy schema migration
- :mod:`.segments` — segment discovery for sequencer / manifest
"""
import json
import logging
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

import pythontk as ptk
from pythontk.audio_utils._audio_utils import AudioUtils as _PtkAudioUtils


@dataclass
class TrackEvent:
    """One keyed play-event on a track.

    Attributes:
        track_id: Canonical track identifier.
        start: Frame of the ``value=1`` start key.
        stop: Frame of the optional ``value=0`` stop key, or ``None``
            when the clip plays through to the file duration.
    """

    track_id: str
    start: float
    stop: Optional[float] = None

try:
    import pymel.core as pm
except ImportError:
    pm = None

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants (importable without the class)
# ---------------------------------------------------------------------------

CARRIER_NODE: str = "data_internal"
"""Single carrier: the shared ``data_internal`` network node."""

ATTR_PREFIX: str = "audio_clip_"
"""Per-track keyed enum attrs have names of the form ``audio_clip_<track_id>``."""

FILE_MAP_ATTR: str = "audio_file_map"
"""Shared JSON map ``{track_id: path}`` on the carrier."""

MARKER_ATTR: str = "audio_node_source"
"""String attr stamped on compositor-produced DG audio nodes; value = track_id."""

RESERVED_TRACK_IDS: FrozenSet[str] = frozenset({"off", "none", ""})
"""Track IDs forbidden by design (clash with enum ``off`` value or empty)."""

_TRACK_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DEFAULT_FPS: float = 24.0
_WAVEFORM_CACHE: Dict[str, List[Tuple[float, float]]] = {}

_SNAP_FRAMES: bool = True
"""Global default for whole-frame snapping on audio key writes."""


class AudioUtils(ptk.HelpMixin):
    """Unified audio system API for Maya scenes.

    Single source of truth for audio clips and game-event triggers:
    keyed enum attrs on ``data_internal`` (one per track), with DG
    ``audio`` nodes derived as a rendered view by the compositor.

    Design rule: no module outside ``audio_utils`` constructs audio
    attr names, parses audio data on the carrier, or writes audio
    scene data directly.
    """

    CARRIER_NODE = CARRIER_NODE
    ATTR_PREFIX = ATTR_PREFIX
    FILE_MAP_ATTR = FILE_MAP_ATTR
    MARKER_ATTR = MARKER_ATTR
    RESERVED_TRACK_IDS = RESERVED_TRACK_IDS

    # ------------------------------------------------------------------
    # Global settings
    # ------------------------------------------------------------------

    @staticmethod
    def get_snap_frames() -> bool:
        """Return the global whole-frame snap default for key writes."""
        return _SNAP_FRAMES

    @staticmethod
    def set_snap_frames(value: bool) -> None:
        """Set the global whole-frame snap default for key writes."""
        global _SNAP_FRAMES
        _SNAP_FRAMES = bool(value)

    snap_frames = property(
        lambda self: _SNAP_FRAMES,
        lambda self, v: AudioUtils.set_snap_frames(v),
    )

    # ------------------------------------------------------------------
    # Schema / Identity
    # ------------------------------------------------------------------

    @staticmethod
    def validate_track_id(track_id: str) -> None:
        """Raise ``ValueError`` if *track_id* violates schema rules."""
        if not isinstance(track_id, str):
            raise ValueError(f"track_id must be str, got {type(track_id).__name__}")
        if track_id in RESERVED_TRACK_IDS:
            raise ValueError(f"track_id {track_id!r} is reserved")
        if not _TRACK_ID_RE.match(track_id):
            raise ValueError(
                f"track_id {track_id!r} must match regex {_TRACK_ID_RE.pattern} "
                f"(lowercase alphanumeric + underscore, leading letter)"
            )

    @classmethod
    def normalize_track_id(cls, raw: str) -> str:
        """Derive a canonical ``track_id`` from arbitrary text.

        Rules: lowercase, non-alphanumeric → underscore, collapsed
        consecutive underscores, stripped leading/trailing underscores,
        leading digit → prefixed with ``t_``.
        """
        if not isinstance(raw, str):
            raise ValueError(f"raw must be str, got {type(raw).__name__}")
        s = raw.lower()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        if not s:
            raise ValueError(f"cannot normalize {raw!r} to a valid track_id")
        if s[0].isdigit():
            s = "t_" + s
        if s in RESERVED_TRACK_IDS:
            raise ValueError(f"normalized track_id {s!r} is reserved")
        cls.validate_track_id(s)
        return s

    @classmethod
    def attr_for(cls, track_id: str) -> str:
        """Return the attr name for *track_id* (e.g. ``audio_clip_footstep``)."""
        cls.validate_track_id(track_id)
        return ATTR_PREFIX + track_id

    @classmethod
    def track_id_from_attr(cls, attr_name: str) -> str:
        """Inverse of :meth:`attr_for`.  Raise if *attr_name* lacks the prefix."""
        if not attr_name.startswith(ATTR_PREFIX):
            raise ValueError(
                f"attr {attr_name!r} does not start with prefix {ATTR_PREFIX!r}"
            )
        track_id = attr_name[len(ATTR_PREFIX) :]
        cls.validate_track_id(track_id)
        return track_id

    # ------------------------------------------------------------------
    # Carrier Discovery
    # ------------------------------------------------------------------

    @staticmethod
    def find_carriers() -> List[str]:
        """Return carriers holding audio data (``[CARRIER_NODE]`` or ``[]``)."""
        if cmds is None:
            return []
        if cmds.objExists(CARRIER_NODE):
            return [CARRIER_NODE]
        return []

    @staticmethod
    def list_track_attrs(carrier: str) -> List[str]:
        """List all per-track audio attrs on *carrier* (sorted)."""
        if cmds is None:
            return []
        if not cmds.objExists(carrier):
            return []
        attrs = cmds.listAttr(carrier, userDefined=True) or []
        return sorted(a for a in attrs if a.startswith(ATTR_PREFIX))

    # ------------------------------------------------------------------
    # File Map
    # ------------------------------------------------------------------

    @staticmethod
    def load_file_map(carrier: Optional[str] = None) -> Dict[str, str]:
        """Return the ``{track_id: path}`` dict from the carrier's JSON attr.

        Returns an empty dict when the carrier or attr does not exist.
        """
        if cmds is None:
            return {}
        carrier = carrier or CARRIER_NODE
        if not cmds.objExists(carrier):
            return {}
        attr = f"{carrier}.{FILE_MAP_ATTR}"
        if not cmds.objExists(attr):
            return {}
        raw = cmds.getAttr(attr) or ""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid JSON in %s; treating as empty", attr)
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def set_path(cls, track_id: str, path: str, carrier: Optional[str] = None) -> None:
        """Store *path* for *track_id* in the file map (creates attr if needed)."""
        cls.validate_track_id(track_id)
        if cmds is None:
            return
        carrier = carrier or CARRIER_NODE
        if not cmds.objExists(carrier):
            from mayatk.node_utils.data_nodes import DataNodes

            DataNodes.ensure_internal()
        data = cls.load_file_map(carrier)
        data[track_id] = path.replace("\\", "/")
        cls._save_file_map(carrier, data)

    @classmethod
    def get_path(cls, track_id: str, carrier: Optional[str] = None) -> Optional[str]:
        """Return the stored path for *track_id*, or ``None``."""
        cls.validate_track_id(track_id)
        return cls.load_file_map(carrier).get(track_id)

    @classmethod
    def remove_path(cls, track_id: str, carrier: Optional[str] = None) -> bool:
        """Remove *track_id* from the file map.  Return True if present."""
        cls.validate_track_id(track_id)
        if cmds is None:
            return False
        carrier = carrier or CARRIER_NODE
        if not cmds.objExists(carrier):
            return False
        data = cls.load_file_map(carrier)
        if track_id not in data:
            return False
        del data[track_id]
        cls._save_file_map(carrier, data)
        return True

    @staticmethod
    def _ensure_file_map_attr(carrier: str) -> None:
        """Create the ``audio_file_map`` string attr if missing."""
        if not cmds.attributeQuery(FILE_MAP_ATTR, node=carrier, exists=True):
            cmds.addAttr(carrier, longName=FILE_MAP_ATTR, dataType="string")
            cmds.setAttr(f"{carrier}.{FILE_MAP_ATTR}", "{}", type="string")

    @classmethod
    def _save_file_map(cls, carrier: str, data: Dict[str, str]) -> None:
        """Overwrite the carrier's file_map JSON attr."""
        cls._ensure_file_map_attr(carrier)
        cmds.setAttr(f"{carrier}.{FILE_MAP_ATTR}", json.dumps(data), type="string")

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------

    @staticmethod
    def get_fps() -> float:
        """Return the current Maya scene framerate (or 24.0 outside Maya)."""
        if pm is None:
            return _DEFAULT_FPS
        try:
            fps = pm.mel.eval("float $fps = `currentTimeUnitToFPS`")
            return float(fps) if fps else _DEFAULT_FPS
        except Exception:
            return _DEFAULT_FPS

    # ------------------------------------------------------------------
    # Waveform / Duration
    # ------------------------------------------------------------------

    compute_waveform_envelope = staticmethod(_PtkAudioUtils.compute_waveform_envelope)
    """Re-export: ``(wav_path) -> List[(min, max)]``."""

    @staticmethod
    def cached_waveform(wav_path: str) -> List[Tuple[float, float]]:
        """Return the waveform envelope for *wav_path*, computing once per path."""
        if wav_path not in _WAVEFORM_CACHE:
            _WAVEFORM_CACHE[wav_path] = _PtkAudioUtils.compute_waveform_envelope(
                wav_path
            )
        return _WAVEFORM_CACHE[wav_path]

    @staticmethod
    def clear_waveform_cache() -> None:
        """Drop all cached waveform envelopes."""
        _WAVEFORM_CACHE.clear()

    @staticmethod
    def audio_duration_frames(file_path: str, fps: float) -> Tuple[float, str]:
        """Return ``(duration_in_frames, resolved_wav_path)`` for *file_path*.

        Non-WAV sources are resolved against a sibling ``_maya_audio_cache``
        directory.  Returns ``(0.0, file_path)`` when no readable audio is
        found.
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

    # ------------------------------------------------------------------
    # Events — attr creation / existence
    # ------------------------------------------------------------------

    @classmethod
    def ensure_track_attr(cls, track_id: str, carrier: Optional[str] = None) -> str:
        """Create the per-track enum attr if missing. Return ``<carrier>.<attr>``.

        Creates the carrier node itself via ``DataNodes.ensure_internal``
        when it doesn't exist yet.  Attr is hidden by default; use
        :meth:`show_track_attrs` to surface it in the Channel Box.
        """
        cls.validate_track_id(track_id)
        if cmds is None:
            return ""
        carrier = carrier or CARRIER_NODE

        if not cmds.objExists(carrier):
            from mayatk.node_utils.data_nodes import DataNodes

            DataNodes.ensure_internal()

        attr = cls.attr_for(track_id)
        full = f"{carrier}.{attr}"
        if cmds.attributeQuery(attr, node=carrier, exists=True):
            # Opportunistic migration: older scenes labeled this enum
            # "off:<track_id>"; normalise to "off:on".
            try:
                current = cmds.addAttr(full, query=True, enumName=True) or ""
                if current != "off:on":
                    cmds.addAttr(full, edit=True, enumName="off:on")
            except Exception:
                pass
            return full

        cmds.addAttr(
            carrier,
            longName=attr,
            attributeType="enum",
            enumName="off:on",
            keyable=True,
            hidden=True,
        )
        return full

    @classmethod
    def has_track(cls, track_id: str, carrier: Optional[str] = None) -> bool:
        """Return True if *track_id* has a per-track attr on the carrier."""
        cls.validate_track_id(track_id)
        if cmds is None:
            return False
        carrier = carrier or CARRIER_NODE
        if not cmds.objExists(carrier):
            return False
        return bool(
            cmds.attributeQuery(cls.attr_for(track_id), node=carrier, exists=True)
        )

    @classmethod
    def list_tracks(cls, carrier: Optional[str] = None) -> List[str]:
        """Return all track_ids with attrs on *carrier* (sorted)."""
        if cmds is None:
            return []
        if carrier is None:
            carriers = cls.find_carriers()
            if not carriers:
                return []
            carrier = carriers[0]
        return [cls.track_id_from_attr(a) for a in cls.list_track_attrs(carrier)]

    # ------------------------------------------------------------------
    # Events — key read
    # ------------------------------------------------------------------

    @classmethod
    def read_keys(cls, track_id: str, carrier: Optional[str] = None) -> List[tuple]:
        """Return ``[(frame, value), ...]`` for *track_id* (time-ordered)."""
        cls.validate_track_id(track_id)
        if cmds is None:
            return []
        carrier = carrier or CARRIER_NODE
        if not cls.has_track(track_id, carrier):
            return []
        attr = f"{carrier}.{cls.attr_for(track_id)}"
        frames = cmds.keyframe(attr, q=True) or []
        vals = cmds.keyframe(attr, q=True, valueChange=True) or []
        pairs = list(zip(frames, vals))
        pairs.sort(key=lambda p: p[0])
        return pairs

    @classmethod
    def read_events(
        cls, track_id: str, carrier: Optional[str] = None
    ) -> List[TrackEvent]:
        """Return :class:`TrackEvent` list for *track_id*.

        Consecutive start/stop keys are paired into one event.  A trailing
        unmatched start becomes an event with ``stop=None`` (plays to file
        duration).
        """
        events: List[TrackEvent] = []
        pending_start: Optional[float] = None
        for frame, val in cls.read_keys(track_id, carrier):
            is_on = int(round(val)) >= 1
            if is_on:
                if pending_start is not None:
                    events.append(TrackEvent(track_id, pending_start, stop=None))
                pending_start = frame
            else:
                if pending_start is not None:
                    events.append(TrackEvent(track_id, pending_start, stop=frame))
                    pending_start = None
        if pending_start is not None:
            events.append(TrackEvent(track_id, pending_start, stop=None))
        return events

    # ------------------------------------------------------------------
    # Events — key write
    # ------------------------------------------------------------------

    @classmethod
    def write_key(
        cls,
        track_id: str,
        frame: float,
        value: int = 1,
        carrier: Optional[str] = None,
        snap: Optional[bool] = None,
    ) -> None:
        """Set a key at *frame* with *value* (0=off, 1=on) on the track attr.

        Creates the attr if missing.

        Parameters:
            snap: Whether to snap ``frame`` to the nearest whole frame.
                ``None`` (default) uses the global :func:`get_snap_frames`
                setting.
        """
        cls.validate_track_id(track_id)
        if cmds is None:
            return
        cls.ensure_track_attr(track_id, carrier)
        carrier = carrier or CARRIER_NODE
        attr = f"{carrier}.{cls.attr_for(track_id)}"
        if snap is None:
            snap = _SNAP_FRAMES
        if snap:
            frame = round(float(frame))
        cmds.setKeyframe(attr, time=frame, value=int(bool(value)))

    @classmethod
    def remove_key(
        cls,
        track_id: str,
        frame: float,
        carrier: Optional[str] = None,
    ) -> bool:
        """Remove the key at *frame* on the track attr.  Return True if removed."""
        cls.validate_track_id(track_id)
        if cmds is None:
            return False
        carrier = carrier or CARRIER_NODE
        if not cls.has_track(track_id, carrier):
            return False
        attr = f"{carrier}.{cls.attr_for(track_id)}"
        eps = 1e-3
        existing = cmds.keyframe(attr, q=True, time=(frame - eps, frame + eps))
        if not existing:
            return False
        cmds.cutKey(attr, time=(frame - eps, frame + eps), clear=True)
        return True

    @classmethod
    def clear_keys(
        cls,
        track_id: str,
        carrier: Optional[str] = None,
    ) -> bool:
        """Remove every key on *track_id*'s attr.  Return True if any were cleared.

        Used when rewriting a track's start/stop pair so stale keys from
        prior builds or manual authoring don't produce overlapping events.
        The attr itself is preserved.
        """
        cls.validate_track_id(track_id)
        if cmds is None:
            return False
        carrier = carrier or CARRIER_NODE
        if not cls.has_track(track_id, carrier):
            return False
        attr = f"{carrier}.{cls.attr_for(track_id)}"
        if not cmds.keyframe(attr, q=True):
            return False
        cmds.cutKey(attr, clear=True)
        return True

    # ------------------------------------------------------------------
    # Events — range shift
    # ------------------------------------------------------------------

    @classmethod
    def shift_keys_in_range(
        cls,
        old_start: float,
        old_end: float,
        delta: float,
        track_ids: Optional[List[str]] = None,
        carrier: Optional[str] = None,
    ) -> List[str]:
        """Shift audio keys in ``[old_start, old_end]`` by *delta*.

        Uses a set-then-cut pattern to work around Maya's broken
        ``cmds.keyframe(edit=True, timeChange=delta)`` for enum attrs.

        When *track_ids* is supplied, the caller asserts every tid has
        a live attr on *carrier*; the per-tid existence check is
        skipped for performance. When ``None`` the attrs are discovered
        and validated once.

        Returns list of track_ids that had at least one key shifted.
        """
        if cmds is None or abs(delta) < 1e-6:
            return []
        carrier = carrier or CARRIER_NODE
        if not cmds.objExists(carrier):
            return []

        # When track_ids is supplied, every tid is assumed to be
        # carrier-resident (the caller derived it from list_tracks) — the
        # existence snapshot is redundant.  Only pay for it when we had to
        # discover the tracks ourselves.
        if track_ids is None:
            track_ids = cls.list_tracks(carrier)
            existing_attrs = set(cls.list_track_attrs(carrier))
        else:
            existing_attrs = None

        eps = 1e-3
        tr = (old_start - eps, old_end + eps)
        shifted: List[str] = []

        for tid in track_ids:
            attr_name = cls.attr_for(tid)
            if existing_attrs is not None and attr_name not in existing_attrs:
                continue
            attr = f"{carrier}.{attr_name}"
            keys = cmds.keyframe(attr, q=True, time=tr) or []
            if not keys:
                continue
            vals = cmds.keyframe(attr, q=True, time=tr, valueChange=True) or []
            pairs = list(zip(keys, vals))
            new_frames = [f + delta for f, _ in pairs]
            any_ok = False
            # Phase 1: write new keys.
            for f, v in pairs:
                try:
                    cmds.setKeyframe(attr, time=f + delta, value=int(round(v)))
                    any_ok = True
                except RuntimeError as exc:
                    logger.debug(
                        "shift set: %s key %s -> %s failed: %s",
                        attr,
                        f,
                        f + delta,
                        exc,
                    )
            # Phase 2: cut old positions not occupied by a new key.
            for f, _ in pairs:
                if any(abs(f - nf) < eps for nf in new_frames):
                    continue
                try:
                    cmds.cutKey(attr, time=(f - eps, f + eps), clear=True)
                except RuntimeError as exc:
                    logger.debug(
                        "shift cut: %s key %s failed: %s",
                        attr,
                        f,
                        exc,
                    )
            if any_ok:
                shifted.append(tid)
        return shifted

    # ------------------------------------------------------------------
    # Events — playhead / manifest queries
    # ------------------------------------------------------------------

    @classmethod
    def tracks_on_at_frame(
        cls,
        frame: float,
        carrier: Optional[str] = None,
        track_ids: Optional[List[str]] = None,
    ) -> List[str]:
        """Return track_ids currently "on" (value=1) at *frame*.

        A track is on at *frame* when its last key at-or-before *frame*
        has value >= 1.
        """
        if cmds is None:
            return []
        carrier = carrier or CARRIER_NODE
        if track_ids is None:
            track_ids = cls.list_tracks(carrier)
        on: List[str] = []
        for tid in track_ids:
            keys = cls.read_keys(tid, carrier)
            if not keys:
                continue
            val = None
            for f, v in keys:
                if f <= frame + 1e-6:
                    val = v
                else:
                    break
            if val is not None and int(round(val)) >= 1:
                on.append(tid)
        on.sort()
        return on

    @classmethod
    def bake_manifest(
        cls,
        carrier: Optional[str] = None,
        display_map: Optional[dict] = None,
    ) -> str:
        """Return a space-separated ``"<frame>:<label>"`` manifest string.

        Iterates all start keys across all tracks in time order.
        Used for game-export wire format.
        """
        if cmds is None:
            return ""
        carrier = carrier or CARRIER_NODE
        display_map = display_map or {}
        entries: List[tuple] = []
        for tid in cls.list_tracks(carrier):
            label = display_map.get(tid, tid)
            for frame, val in cls.read_keys(tid, carrier):
                if int(round(val)) >= 1:
                    entries.append((frame, label))
        entries.sort(key=lambda e: e[0])
        return " ".join(f"{int(round(f))}:{lbl}" for f, lbl in entries)

    # ------------------------------------------------------------------
    # Events — track lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def delete_track(cls, track_id: str, carrier: Optional[str] = None) -> bool:
        """Remove the per-track attr and its keys.  Return True if deleted.

        Does NOT touch the DG audio node — the compositor handles that
        on its next ``sync()`` (orphan cleanup via marker attr).
        """
        cls.validate_track_id(track_id)
        if cmds is None:
            return False
        carrier = carrier or CARRIER_NODE
        if not cls.has_track(track_id, carrier):
            return False
        full = f"{carrier}.{cls.attr_for(track_id)}"
        cmds.deleteAttr(full)
        return True

    @classmethod
    def rename_track(
        cls,
        old_id: str,
        new_id: str,
        carrier: Optional[str] = None,
    ) -> bool:
        """Rename a track's attr + enum labels + file_map key.

        Preserves all keyframes (values 0/1 are integer-valued; the enum
        label string is display-only).  Caller is responsible for calling
        :func:`_compositor.sync` afterwards so the managed DG node marker
        and name stay in step.
        """
        cls.validate_track_id(old_id)
        cls.validate_track_id(new_id)
        if cmds is None:
            return False
        carrier = carrier or CARRIER_NODE
        if not cls.has_track(old_id, carrier):
            return False
        if old_id == new_id:
            return True
        if cls.has_track(new_id, carrier):
            return False

        old_attr = cls.attr_for(old_id)
        new_attr = cls.attr_for(new_id)

        cmds.renameAttr(f"{carrier}.{old_attr}", new_attr)
        cmds.addAttr(f"{carrier}.{new_attr}", edit=True, enumName="off:on")

        data = cls.load_file_map(carrier)
        if old_id in data:
            data[new_id] = data.pop(old_id)
            cls._save_file_map(carrier, data)

        return True

    # ------------------------------------------------------------------
    # Events — visibility
    # ------------------------------------------------------------------

    @classmethod
    def show_track_attrs(
        cls, track_id: Optional[str] = None, carrier: Optional[str] = None
    ) -> List[str]:
        """Un-hide track attrs in the Channel Box."""
        return cls._set_hidden(False, track_id, carrier)

    @classmethod
    def hide_track_attrs(
        cls, track_id: Optional[str] = None, carrier: Optional[str] = None
    ) -> List[str]:
        """Hide track attrs from the Channel Box."""
        return cls._set_hidden(True, track_id, carrier)

    @classmethod
    def _set_hidden(
        cls, hide: bool, track_id: Optional[str], carrier: Optional[str]
    ) -> List[str]:
        if cmds is None:
            return []
        carrier = carrier or CARRIER_NODE
        if not cmds.objExists(carrier):
            return []
        tids = [track_id] if track_id else cls.list_tracks(carrier)
        affected: List[str] = []
        for tid in tids:
            if not cls.has_track(tid, carrier):
                continue
            attr = f"{carrier}.{cls.attr_for(tid)}"
            cmds.setAttr(attr, channelBox=not hide, keyable=not hide)
            affected.append(tid)
        return affected

    # ------------------------------------------------------------------
    # Compositor / batch / migration  (routed to companion modules)
    # ------------------------------------------------------------------

    @staticmethod
    def sync(tracks=None, carrier=None):
        """Reconcile managed DG audio nodes with keyed track state.

        See :func:`._compositor.sync` for full documentation.
        """
        from mayatk.audio_utils.compositor import sync

        return sync(tracks=tracks, carrier=carrier)

    @staticmethod
    def find_dg_node_for_track(track_id):
        """Return the managed DG audio node for *track_id*, or ``None``."""
        from mayatk.audio_utils.compositor import find_dg_node_for_track

        return find_dg_node_for_track(track_id)

    @staticmethod
    def is_managed_dg(node):
        """True if *node* has the ``audio_node_source`` marker attr."""
        from mayatk.audio_utils.compositor import is_managed_dg

        return is_managed_dg(node)

    @staticmethod
    def batch(auto_sync=True, undo=True):
        """Context manager grouping audio edits into one undo + one sync.

        See :func:`._batch.batch` for full documentation.
        """
        from mayatk.audio_utils.batch import batch

        return batch(auto_sync=auto_sync, undo=undo)

    @staticmethod
    def detect_legacy(obj="data_internal", category="audio"):
        """Return True if *obj* has legacy ``<category>_trigger`` attr.

        See :func:`._migrate.detect_legacy` for full documentation.
        """
        from mayatk.audio_utils.migrate import detect_legacy

        return detect_legacy(obj, category)

    @staticmethod
    def migrate_legacy_triggers(obj, category="audio", keep_old_attrs=False):
        """Migrate legacy trigger keys to per-track attrs.

        See :func:`._migrate.migrate_legacy_triggers` for full documentation.
        """
        from mayatk.audio_utils.migrate import migrate_legacy_triggers

        return migrate_legacy_triggers(obj, category, keep_old_attrs)
