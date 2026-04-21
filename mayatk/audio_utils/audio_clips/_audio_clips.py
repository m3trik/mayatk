# !/usr/bin/python
# coding=utf-8
"""Scene-wide audio event manager — thin facade over ``audio_utils``.

All authoritative state lives on the canonical carrier
(``data_internal``): one keyed enum ``audio_clip_<track_id>`` per track
plus a shared ``audio_file_map``.  :mod:`mayatk.audio_utils` manages the
derived per-track DG nodes.

This module adds exactly **one** concern on top of ``audio_utils``:
the single scene-wide **composite WAV**.  Maya's Time Slider has only
one audio slot, so during scrubbing we play a pre-mixed WAV of every
keyed start event.  The composite is rebuilt on demand and stamped
with a marker attr so ``audio_utils.sync`` leaves it alone.

Design
------
- **Single scope.**  No ``objects`` or ``category`` arguments — the
  canonical carrier is always the target.  Callers that need filtering
  should pass ``track_ids`` to ``audio_utils.sync`` directly.
- **No per-instance DG nodes.**  ``audio_utils.sync`` creates one DG
  node per track (offset = first start key).  The composite WAV
  handles everything else.
- **Composite is optional.**  ``AudioClips.sync()`` calls
  ``audio_utils.sync`` unconditionally and ``rebuild_composite`` when
  there is something to mix.  Callers can disable composite via the
  ``composite=False`` flag for unit-tests or headless export flows.
"""
import os
from typing import Dict, List, Optional

import pythontk as ptk

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    pass

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.audio_utils._audio_utils import AudioUtils as _audio_utils
from mayatk.audio_utils import nodes as _nodes
from mayatk.audio_utils.nodes import _CACHE_DIR_NAME


class AudioClips(ptk.LoggingMixin):
    """Scene-wide audio event facade.

    Wraps :mod:`mayatk.audio_utils` and owns the composite WAV + its
    backing DG node.
    """

    COMPOSITE_NODE: str = "audio_composite"
    """Canonical name for the mixed composite DG node."""

    COMPOSITE_MARKER_ATTR: str = "audio_composite_owner"
    """String attr stamped on the composite DG node."""

    COMPOSITE_MARKER_VALUE: str = "AudioClips"
    """Marker value identifying a composite as managed by this class."""

    COMPOSITE_FILENAME: str = "_composite.wav"
    """Default filename for the composite WAV in the cache directory."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def sync(
        cls,
        track_ids: Optional[List[str]] = None,
        composite: bool = True,
        activate: bool = True,
    ) -> Dict[str, list]:
        """Reconcile DG nodes and rebuild the composite WAV.

        Parameters:
            track_ids: Restrict DG sync to these tracks.  ``None`` ⇒
                full reconciliation (including orphan cleanup).  The
                composite is always built from *all* tracks regardless.
            composite: When True (default), rebuild the composite WAV.
            activate: When True (default) and a composite exists, make
                it the active Time Slider sound.

        Returns:
            Dict with ``created`` / ``updated`` / ``deleted`` lists
            from :func:`audio_utils.sync`, plus ``composite`` — the
            composite node name or ``None``.
        """
        result = dict(_audio_utils.sync(tracks=track_ids))
        comp_node: Optional[str] = None
        if composite:
            comp_node = cls.rebuild_composite()
        result["composite"] = comp_node
        if activate and comp_node:
            cls.set_active(comp_node)
        return result

    @classmethod
    @CoreUtils.undoable
    def rebuild_composite(cls) -> Optional[str]:
        """Rebuild the scene-wide composite WAV from keyed start events.

        Reads every track's start keys (value ≥ 1) and mixes the source
        WAVs into a single cache file.  Creates or updates the
        ``audio_composite`` DG node to point at that file.

        Returns:
            Name of the composite DG node, or ``None`` when nothing is
            keyed / no valid file_map entries exist.
        """
        events, audio_map, output_dir = cls._gather_composite_inputs()
        if not events or not audio_map:
            # Nothing to mix — also clean up any stale composite.
            cls._remove_composite_node()
            return None

        fps = _audio_utils.get_fps()
        cache_dir = os.path.join(output_dir, _CACHE_DIR_NAME).replace("\\", "/")
        os.makedirs(cache_dir, exist_ok=True)
        out_path = os.path.join(cache_dir, cls.COMPOSITE_FILENAME).replace("\\", "/")

        comp_path = ptk.AudioUtils.build_composite_wav(
            events=events,
            audio_map=audio_map,
            fps=fps,
            output_path=out_path,
            logger=cls.logger,
        )
        if not comp_path:
            cls._remove_composite_node()
            return None

        node = cls._get_or_create_composite_node()
        cmds.setAttr(f"{node}.filename", comp_path, type="string")
        cmds.setAttr(f"{node}.offset", 0)
        cls.logger.debug(f"Composite WAV rebuilt: {comp_path}")
        return node

    @classmethod
    @CoreUtils.undoable
    def remove(cls) -> int:
        """Delete every managed DG node, the composite, and all tracks.

        Steps:
        1. Delete the composite DG node and its on-disk WAV.
        2. Delete every ``audio_clip_*`` track attr and its file_map
           entry on the canonical carrier.
        3. Sweep orphan per-track DG nodes via ``audio_utils.sync``
           (it deletes any managed DG whose track has vanished).

        Returns:
            Number of tracks removed.
        """
        # 1. Composite
        cls._remove_composite_node()

        # 2. Delete every track attr + its file_map entry.
        tracks = _audio_utils.list_tracks()
        with _audio_utils.batch():
            for tid in tracks:
                _audio_utils.delete_track(tid)
                _audio_utils.remove_path(tid)

        # 3. Sweep orphan DG nodes.
        _audio_utils.sync()

        cls.logger.info(f"Removed {len(tracks)} audio track(s).")
        return len(tracks)

    @classmethod
    @CoreUtils.undoable
    def load_tracks(
        cls,
        audio_files: List[str],
    ) -> List[str]:
        """Register audio files as tracks (no keys authored).

        For each path: derive a track_id from the file stem, ensure the
        per-track attr exists on the canonical carrier, and record the
        path in ``audio_file_map``.  Does **not** write any keyframes —
        the UI authors keys separately.

        Re-adding a file with the same stem replaces the file map entry
        (keyframes and enum index are preserved).

        Parameters:
            audio_files: Absolute paths to audio files.

        Returns:
            List of track_ids registered or updated.
        """
        if not audio_files:
            return []

        registered: List[str] = []

        with _audio_utils.batch():
            for path in audio_files:
                path = path.replace("\\", "/")
                stem = os.path.splitext(os.path.basename(path))[0]
                try:
                    tid = _audio_utils.normalize_track_id(stem)
                except ValueError as exc:
                    cls.logger.warning(f"Cannot register {path!r}: {exc}")
                    continue
                _audio_utils.ensure_track_attr(tid)
                _audio_utils.set_path(tid, path)
                registered.append(tid)

        if registered:
            cls.logger.info(f"Registered {len(registered)} track(s).")
        return registered

    @classmethod
    def list_nodes(cls) -> List[str]:
        """Return names of every managed DG audio node plus the composite."""
        if cmds is None:
            return []
        nodes: List[str] = []
        for tid in _audio_utils.list_tracks():
            node = _audio_utils.find_dg_node_for_track(tid)
            if node:
                nodes.append(node)
        comp = cls._find_composite_node()
        if comp:
            nodes.append(comp)
        return nodes

    @classmethod
    def set_active(
        cls,
        node_name: str,
        time_slider: bool = True,
    ) -> None:
        """Set an audio node as the active Time Slider sound.

        Parameters:
            node_name: Name of the audio node.
            time_slider: If True (default), assign to the Time Slider.
        """
        if cmds is None or not cmds.objExists(node_name):
            cls.logger.warning(f"Audio node '{node_name}' not found.")
            return
        if not time_slider:
            return
        try:
            slider = mel.eval("$tmpVar=$gPlayBackSlider")
            if not slider:
                return
            cmds.timeControl(slider, edit=True, sound=node_name, displaySound=True)
        except Exception as exc:
            cls.logger.warning(f"Could not set Time Slider sound: {exc}")

    # ------------------------------------------------------------------
    # Composite helpers (internal)
    # ------------------------------------------------------------------

    @classmethod
    def _find_composite_node(cls) -> Optional[str]:
        """Return the managed composite node name, or None."""
        if cmds is None:
            return None
        for node in cmds.ls(type="audio") or []:
            if cmds.attributeQuery(cls.COMPOSITE_MARKER_ATTR, node=node, exists=True):
                val = cmds.getAttr(f"{node}.{cls.COMPOSITE_MARKER_ATTR}") or ""
                if val == cls.COMPOSITE_MARKER_VALUE:
                    return node
        # Fall back to the canonical name (legacy scenes or externally
        # created nodes).  Stamp the marker so we find it faster next
        # time, but only if nothing else already owns that name.
        if cmds.objExists(cls.COMPOSITE_NODE):
            if cmds.nodeType(cls.COMPOSITE_NODE) == "audio":
                cls._stamp_composite_marker(cls.COMPOSITE_NODE)
                return cls.COMPOSITE_NODE
        return None

    @classmethod
    def _get_or_create_composite_node(cls) -> str:
        existing = cls._find_composite_node()
        if existing:
            return existing
        node = cmds.createNode("audio", name=cls.COMPOSITE_NODE, skipSelect=True)
        cls._stamp_composite_marker(node)
        return node

    @classmethod
    def _stamp_composite_marker(cls, node: str) -> None:
        if not cmds.attributeQuery(cls.COMPOSITE_MARKER_ATTR, node=node, exists=True):
            cmds.addAttr(node, longName=cls.COMPOSITE_MARKER_ATTR, dataType="string")
        cmds.setAttr(
            f"{node}.{cls.COMPOSITE_MARKER_ATTR}",
            cls.COMPOSITE_MARKER_VALUE,
            type="string",
        )

    @classmethod
    def _remove_composite_node(cls) -> None:
        if cmds is None:
            return
        node = cls._find_composite_node()
        if not node:
            return
        filepath = (cmds.getAttr(f"{node}.filename") or "").replace("\\", "/")
        cmds.lockNode(node, lock=False)
        cmds.delete(node)
        if (
            filepath
            and os.path.isfile(filepath)
            and os.path.basename(filepath) == cls.COMPOSITE_FILENAME
        ):
            try:
                os.remove(filepath)
            except OSError as exc:
                cls.logger.debug(f"Cannot remove composite WAV {filepath!r}: {exc}")

    @classmethod
    def _gather_composite_inputs(cls):
        """Return ``(events, audio_map, output_dir)`` for the composite build.

        - **events**: ``[(frame, track_id), ...]`` time-ordered, every
          start key (value ≥ 1) across every track.
        - **audio_map**: ``{track_id: playable_path}`` — keys are
          normalized (lowercase) track_ids; values are FFmpeg-converted
          WAVs when needed.
        - **output_dir**: Directory to place the cache file.  Derived
          from the first file_map entry; falls back to the workspace
          ``sound/`` dir.
        """
        file_map = _audio_utils.load_file_map()
        if not file_map:
            return [], {}, ""

        events: List[tuple] = []
        audio_map: Dict[str, str] = {}
        for tid in _audio_utils.list_tracks():
            path = file_map.get(tid)
            if not path:
                continue
            for frame, val in _audio_utils.read_keys(tid):
                if int(round(val)) >= 1:
                    events.append((frame, tid))
            # Resolve to a playable path once per track.
            playable = _nodes.resolve_playable_path(path)
            if playable:
                audio_map[tid] = playable

        events.sort(key=lambda e: e[0])

        # Output dir: directory of the first source file.
        first_path = next(iter(file_map.values()), None)
        output_dir = (
            os.path.dirname(first_path).replace("\\", "/") if first_path else ""
        )
        # Strip trailing cache dir so we don't nest caches.
        while os.path.basename(output_dir) in {"_audio_cache", _CACHE_DIR_NAME}:
            output_dir = os.path.dirname(output_dir)
        if not output_dir:
            output_dir = _nodes.workspace_sound_dir() or ""
        return events, audio_map, output_dir
