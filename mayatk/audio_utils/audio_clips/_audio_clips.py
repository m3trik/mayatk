# !/usr/bin/python
# coding=utf-8
"""Scene-wide audio event manager — thin facade over ``audio_utils``.

All authoritative state lives on the canonical carrier
(``data_internal``): one keyed enum ``audio_clip_<track_id>`` per track
plus a shared ``audio_file_map``.  :mod:`mayatk.audio_utils` manages the
derived per-track DG nodes.

On top of ``audio_utils`` this module owns the single scene-wide
**composite WAV**, and the Audio Clips panel's operations (tentacle's
shared ``slots/tools/audio_clips.py``; blendertk's ``AudioClips`` mirrors
them over the Sequencer).  Maya's Time Slider has only
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

import math
import os
import shutil
from typing import Callable, Dict, List, Optional, Tuple

import pythontk as ptk

try:  # guarded like every mayatk module: the surface must import without Maya
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    cmds = mel = None

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.audio_utils._audio_utils import AudioUtils as _audio_utils
from mayatk.audio_utils._audio_utils import LEGACY_CACHE_DIR_NAMES
from mayatk.audio_utils import nodes as _nodes


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
    """Suffix of the composite WAV's file name in the audio cache: the file is
    ``<scene stem>_<scene path hash>_composite.wav`` (``untitled_composite.wav``
    while unsaved), so scenes sharing one cache never play each other's mix."""

    MANIFEST_ATTR: str = ptk.SceneRecords.AUDIO.key
    """Wire-format string attr baked onto the carrier for FBX → game-engine
    consumption -- the key of the ``ptk.SceneRecords.AUDIO`` record."""

    MANIFEST_VERSION: int = ptk.SceneRecords.AUDIO.version
    """Schema version of the ``audio`` JSON payload (stamped by the
    ``ptk.SceneRecords.AUDIO`` declaration, never spelled here).

    v2: ``{"version": 2, "events": [{"clip", "frame", "name"}, ...], "fps"}``
    — an event's ``clip`` names the shot take (Unity AnimationClip) it
    belongs to, with ``frame`` relative to that take's start; an empty
    ``clip`` means "every clip" (no takes published), its ``frame`` counted
    from the first frame the file's animation holds; ``fps`` is the rate the
    frames count at, which travels with them. v1 was the flat unversioned
    ``"12:footstep 24:jump"`` wire string (unscoped; consumers keep a legacy
    parser for pre-v2 FBX).
    """

    # The Audio Clips panel's wording and formats -- one vocabulary with
    # blendertk's ``AudioClips``, so the shared panel never asks which host.

    ITEM_NOUN: str = "Track"
    """What one entry is called here: a track keyed on the carrier (blendertk:
    ``"Clip"``, a Sequencer strip)."""

    PLACE_LABEL: str = "Key Audio Event"
    """The panel's place action (:meth:`key_track`): a track keys an ON event
    at the playhead and may play many times (blendertk moves its one strip)."""

    REVEAL_LABEL: str = "Select Carrier Node"
    """The panel's :meth:`reveal` action."""

    END_KEYS: bool = True
    """A placed track carries an explicit end: the OFF key :meth:`key_track`'s
    *auto_end* writes (a strip's end is intrinsic; blendertk's is ``False``)."""

    PLAYABLE_EXTENSIONS = frozenset(ptk.AudioUtils.PLAYABLE_EXTENSIONS)
    """What the Time Slider plays as it is; any other
    ``ptk.AudioUtils.SOURCE_EXTENSIONS`` file plays once FFmpeg converts it."""

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
        events, audio_map, cache_dir = cls._gather_composite_inputs()
        if not events or not audio_map or not cache_dir:
            # Nothing to mix — also clean up any stale composite.
            cls._remove_composite_node()
            return None

        fps = _audio_utils.get_fps()
        os.makedirs(cache_dir, exist_ok=True)
        out_path = os.path.join(cache_dir, cls._composite_filename()).replace("\\", "/")

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

    # ------------------------------------------------------------------
    # The Audio Clips panel's operations (tentacle's shared panel calls
    # these; blendertk's ``AudioClips`` mirrors the shared ones)
    # ------------------------------------------------------------------

    @staticmethod
    def _current_frame() -> float:
        """The Time Slider's current frame (0 outside Maya)."""
        return float(cmds.currentTime(query=True)) if cmds is not None else 0.0

    @staticmethod
    def list_tracks() -> List[str]:
        """Every track on the carrier, sorted (``[]`` outside Maya)."""
        return _audio_utils.list_tracks()

    @staticmethod
    def find_track(name: str) -> Optional[str]:
        """The track answering to *name* -- the id it normalizes to -- or
        ``None`` when no track does (the Shot Manifest's "Open in Audio Clips")."""
        try:
            track_id = _audio_utils.normalize_track_id(name)
        except ValueError:
            return None
        return track_id if track_id in _audio_utils.list_tracks() else None

    @staticmethod
    def tracks_at(frame: float) -> List[str]:
        """The tracks playing at *frame* (``AudioUtils.tracks_on_at_frame``):
        what the panel's list follows while the playhead moves."""
        return _audio_utils.tracks_on_at_frame(frame)

    @staticmethod
    def next_track() -> Optional[str]:
        """The track Next Event keys: the one after the most recently started
        track (wrapping), the first when none is keyed, ``None`` with no tracks."""
        tracks = _audio_utils.list_tracks()
        if not tracks:
            return None
        latest, latest_frame = None, float("-inf")
        for track_id in tracks:
            for frame, value in _audio_utils.read_keys(track_id):
                if int(round(value)) >= 1 and frame > latest_frame:
                    latest, latest_frame = track_id, frame
        if latest is None:
            return tracks[0]
        return tracks[(tracks.index(latest) + 1) % len(tracks)]

    @classmethod
    @CoreUtils.undoable
    def add_tracks(cls, audio_files: List[str]) -> List[str]:
        """Register *audio_files* as tracks (:meth:`load_tracks`; a file whose
        stem is already a track swaps its path, keys kept) and, when a track is
        keyed, re-sync so the composite plays the new files.

        Returns:
            The track ids registered or updated; a stem that makes no valid
            id is skipped (logged).
        """
        track_ids = cls.load_tracks(audio_files)
        if track_ids and any(
            _audio_utils.read_keys(t) for t in _audio_utils.list_tracks()
        ):
            cls.sync()
        return track_ids

    @classmethod
    def _retarget(cls, track_id: str, new_id: str) -> None:
        """Rename *track_id* to *new_id* (keys follow), refusing in the user's words."""
        if new_id != track_id and not _audio_utils.rename_track(track_id, new_id):
            raise ValueError(
                f"Cannot rename '{track_id}' to '{new_id}': the track is gone "
                "or the name is taken."
            )

    @classmethod
    @CoreUtils.undoable
    def rename_track(cls, track_id: str, new_name: str) -> str:
        """Rename *track_id* to the id *new_name* normalizes to; its keys, file
        and sound node follow.

        Returns:
            The new track id.

        Raises:
            ValueError: *new_name* makes no valid id, or the track is gone or
                the id taken -- worded for the user.
        """
        try:
            new_id = _audio_utils.normalize_track_id(new_name)
        except ValueError as error:
            raise ValueError(f"Invalid track name: {error}") from error
        cls._retarget(track_id, new_id)
        cls.sync()
        return new_id

    @classmethod
    @CoreUtils.undoable
    def replace_track(cls, track_id: str, path: str) -> str:
        """Point *track_id* at the audio file *path*, keeping its keys. A track
        is named by its file, so a file of another stem renames the track to it
        (blendertk's strip keeps its name).

        Returns:
            The track id afterwards.

        Raises:
            ValueError: The new stem makes no valid id, or it is taken.
        """
        path = path.replace("\\", "/")
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            new_id = _audio_utils.normalize_track_id(stem)
        except ValueError as error:
            raise ValueError(f"Invalid track name: {error}") from error
        cls._retarget(track_id, new_id)
        _audio_utils.set_path(new_id, path)
        cls.sync()
        return new_id

    @classmethod
    @CoreUtils.undoable
    def remove_track(cls, track_id: str) -> bool:
        """Delete *track_id*: its attribute and keys, its file-map entry and
        (on the sync after) its sound node; the composite is rebuilt.

        Returns:
            Whether the track existed.
        """
        with _audio_utils.batch():
            removed = _audio_utils.delete_track(track_id)
            _audio_utils.remove_path(track_id)
        cls.sync()
        return removed

    @classmethod
    @CoreUtils.undoable
    def cleanup_unused(cls) -> List[str]:
        """Delete every track that holds no key (registered, never placed),
        with its sound node.

        Returns:
            The track ids removed.
        """
        unused = [
            t for t in _audio_utils.list_tracks() if not _audio_utils.read_keys(t)
        ]
        if not unused:
            return []
        with _audio_utils.batch():
            for track_id in unused:
                _audio_utils.delete_track(track_id)
                _audio_utils.remove_path(track_id)
        cls.sync()
        return unused

    @classmethod
    @CoreUtils.undoable
    def key_track(
        cls, track_id: str, frame: Optional[float] = None, auto_end: bool = False
    ) -> float:
        """Key an audio event: *track_id* ON at *frame* (the playhead when
        omitted) and, with *auto_end*, OFF where its file ends -- through
        ``AudioUtils.key_clip``, the writer the Shot Manifest's build uses --
        then sync. A track may play any number of times (blendertk's strip is
        one placement, so its twin moves it).

        Returns:
            The frame keyed.
        """
        frame = cls._current_frame() if frame is None else float(frame)
        _audio_utils.key_clip(track_id, frame, auto_end=auto_end)
        cls.sync()
        return frame

    @classmethod
    @CoreUtils.undoable
    def key_all(
        cls, frame: Optional[float] = None, auto_end: bool = False, stagger: int = 0
    ) -> List[str]:
        """Key every track once, end to end from *frame* (the playhead when
        omitted): each starts where the last one's file ended plus *stagger*
        frames (*stagger*, at least one, after a track whose length is
        unreadable); then sync.

        Returns:
            The track ids keyed, in order.
        """
        cursor = cls._current_frame() if frame is None else float(frame)
        keyed: List[str] = []
        with _audio_utils.batch():
            for track_id in _audio_utils.list_tracks():
                duration = _audio_utils.clip_length_frames(track_id)
                _audio_utils.key_clip(
                    track_id, cursor, duration=duration, auto_end=auto_end
                )
                keyed.append(track_id)
                if duration > 0:
                    cursor = math.ceil(cursor + duration) + stagger
                else:
                    cursor += max(stagger, 1)
        cls.sync()
        return keyed

    @classmethod
    def activate_track(cls, track_id: str) -> Optional[str]:
        """Make *track_id*'s own sound node the Time Slider's (it writes no
        attribute).

        Returns:
            The node, or ``None`` when the track has none yet (a sync makes it).
        """
        node = _audio_utils.find_dg_node_for_track(track_id)
        if node:
            cls.set_active(node)
        return node or None

    @classmethod
    def attach_composite(cls) -> Optional[str]:
        """Reconcile the sound nodes without rebuilding the mix, then put an
        existing composite on the Time Slider (a scene just opened).

        Returns:
            The composite node, or ``None`` when there is none.
        """
        cls.sync(composite=False, activate=False)
        node = cls._find_composite_node()
        if node:
            cls.set_active(node)
        return node

    @staticmethod
    def reveal(track_id: Optional[str] = None) -> str:
        """Select the carrier node every track's attribute lives on, so the
        tracks show in the Channel Box (*track_id* is accepted for blendertk's
        twin, which reveals that one strip in the Sequencer).

        Returns:
            The carrier's name.

        Raises:
            ValueError: No carrier yet (no track added), worded for the user.
        """
        carrier = _audio_utils.CARRIER_NODE
        if cmds is None or not cmds.objExists(carrier):
            raise ValueError(f"'{carrier}' does not exist yet: add a track first.")
        cmds.select(carrier, replace=True)
        return carrier

    @staticmethod
    def channels_config() -> Dict[str, object]:
        """How the Channels panel shows the tracks: pinned to the carrier and
        filtered to the per-track attributes -- its ``apply_launch_config``
        keywords.

        Raises:
            ValueError: No carrier yet, worded for the user.
        """
        carrier = _audio_utils.CARRIER_NODE
        if cmds is None or not cmds.objExists(carrier):
            raise ValueError(
                f"Carrier node '{carrier}' does not exist yet: add or key an "
                "audio track first."
            )
        return {
            "targets": [carrier],
            "filter": "Custom",
            "search": f"{_audio_utils.ATTR_PREFIX}*",
        }

    @staticmethod
    def has_legacy_data() -> bool:
        """Whether the carrier still holds the legacy ``audio_trigger`` schema."""
        try:
            return bool(_audio_utils.detect_legacy())
        except Exception:
            return False

    @classmethod
    @CoreUtils.undoable
    def migrate_legacy(cls) -> List[str]:
        """Move the carrier's legacy ``audio_trigger`` keys and file map onto
        per-track attributes (``AudioUtils.migrate_legacy_triggers``), then sync.

        Returns:
            The track ids written.
        """
        migrated = _audio_utils.migrate_legacy_triggers(_audio_utils.CARRIER_NODE)
        cls.sync()
        return migrated

    @staticmethod
    def get_snap_frames() -> bool:
        """Whether audio keys snap to whole frames: the scene's effect recipe
        (``AudioUtils.get_snap_frames``)."""
        return _audio_utils.get_snap_frames()

    @staticmethod
    def set_snap_frames(value: bool) -> None:
        """Set the recipe's whole-frame snap (``AudioUtils.set_snap_frames``).

        Raises:
            RuntimeError: No shot store to hold the recipe.
        """
        _audio_utils.set_snap_frames(value)

    @staticmethod
    def watch_snap(callback, widget=None) -> Optional[Callable[[], None]]:
        """Call *callback* whenever the scene's recipe changes (another panel,
        an undo, a scene opened), so a Snap To Frame toggle shows it. The shot
        store holds the recipe; it sits above this layer, so it is reached
        through the record owners.

        Parameters:
            callback: Called with the store's change arguments.
            widget: A Qt widget whose destruction ends the watch.

        Returns:
            The unwatch callable, or ``None`` without a shot store.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        store = DataNodes.owner(ptk.SceneRecords.SHOT_STORE.key)
        if store is None:
            return None
        unwatch = store.watch_settings(callback)
        if widget is not None:
            widget.destroyed.connect(lambda *_: unwatch())
        return unwatch

    @classmethod
    def export_composite(
        cls, choose_path: Callable[[str], Optional[str]], trim_silence: bool = True
    ) -> Tuple[Optional[str], bool]:
        """Write the scene's composite WAV where *choose_path* says, rebuilt
        first so it holds the current keys.

        Parameters:
            choose_path: ``(suggested path) -> path or None`` -- the destination
                (the panel's save dialog); ``None`` cancels. The suggestion is
                in the project's sound folder, never the audio cache.
            trim_silence: Trim the copy's leading and trailing silence.

        Returns:
            ``(destination, trimmed)``; ``(None, False)`` when cancelled.

        Raises:
            ValueError: Nothing keyed to mix, or the mix is missing on disk --
                worded for the user.
            OSError: The copy failed.
        """
        node = cls.sync(composite=True, activate=False).get("composite")
        if not node:
            raise ValueError("No composite WAV — key tracks and sync first.")
        try:
            source = cmds.getAttr(f"{node}.filename") or ""
        except Exception:
            source = ""
        if not source or not os.path.isfile(source):
            raise ValueError("Composite file not found on disk.")
        folder = _nodes.Nodes.workspace_sound_dir() or ""
        if not folder:
            # An older scene's composite sits in a legacy cache folder beside
            # its sources: step out of that one.
            folder = os.path.dirname(source)
            while os.path.basename(folder) in LEGACY_CACHE_DIR_NAMES:
                folder = os.path.dirname(folder)
        dest = choose_path(os.path.join(folder, "audio_composite.wav"))
        if not dest:
            return None, False
        shutil.copy2(source, dest)
        trimmed = False
        if trim_silence:
            try:
                ptk.AudioUtils.trim_silence(dest)
                trimmed = True
            except Exception as error:
                cls.logger.warning(f"Trimming {dest!r} failed: {error}")
        cls.logger.info(f"Exported the composite to {dest!r}.")
        return dest, trimmed

    @classmethod
    def export_tracks(
        cls,
        choose_dir: Callable[[str], Optional[str]],
        keyed_only: bool = True,
        suffix_range: bool = False,
    ) -> Tuple[int, List[str], Optional[str]]:
        """Copy the tracks' source files into the folder *choose_dir* names.

        Parameters:
            choose_dir: ``(suggested folder) -> folder or None`` (the panel's
                folder dialog); ``None`` cancels.
            keyed_only: Only tracks with a start key; otherwise every track.
            suffix_range: With *keyed_only*, one copy per keying, its frame
                range in the name (``Footstep_12-47.wav``).

        Returns:
            ``(files written, track ids whose source is missing, folder)``;
            ``(0, [], None)`` when cancelled.

        Raises:
            ValueError: No tracks, or none keyed under *keyed_only* -- worded
                for the user.
        """
        file_map = _audio_utils.load_file_map()
        if not file_map:
            raise ValueError("No audio tracks loaded.")
        ranges: Dict[str, List[Tuple[int, int]]] = {}
        if keyed_only:
            fps = _audio_utils.get_fps()
            for track_id in _audio_utils.list_tracks():
                events = _audio_utils.read_events(track_id)
                if not events:
                    continue
                duration = 0.0
                if file_map.get(track_id):
                    try:
                        frames, _ = _audio_utils.audio_duration_frames(
                            file_map[track_id], fps
                        )
                        duration = float(frames or 0.0)
                    except Exception:
                        duration = 0.0
                spans = []
                for event in events:
                    start = int(event.start)
                    if event.stop is not None:
                        end = int(event.stop)
                    elif duration > 0:
                        end = int(math.ceil(event.start + duration))
                    else:
                        end = start
                    spans.append((start, end))
                ranges[track_id] = spans
            if not ranges:
                raise ValueError("No keyed tracks — nothing to export.")
        else:
            ranges = {track_id: [] for track_id in file_map}
        folder = choose_dir(os.path.dirname(next(iter(file_map.values()), "")))
        if not folder:
            return 0, [], None
        exported, missing = 0, []
        for track_id, spans in ranges.items():
            source = file_map.get(track_id)
            if not source or not os.path.isfile(source):
                missing.append(track_id)
                continue
            stem, ext = os.path.splitext(os.path.basename(source))
            names = (
                [f"{stem}_{start}-{end}{ext}" for start, end in spans]
                if keyed_only and suffix_range and spans
                else [f"{stem}{ext}"]
            )
            for name in names:
                try:
                    shutil.copy2(source, os.path.join(folder, name))
                    exported += 1
                except OSError as error:
                    cls.logger.warning(f"Exporting {name!r} failed: {error}")
        cls.logger.info(f"Exported {exported} audio file(s) to {folder!r}.")
        return exported, missing, folder

    # ------------------------------------------------------------------
    # The scene's events, for the panel
    # ------------------------------------------------------------------

    @classmethod
    def watch(
        cls, owner, on_scene=None, on_tracks=None, on_time=None, widget=None
    ) -> List[int]:
        """Tell *owner* what happens to the scene's audio, through the
        ``ScriptJobManager``:

        * *on_scene* -- a scene opened or a new one began (deferred to idle);
        * *on_tracks* -- a track attribute on the carrier was set, added,
          removed or disconnected (the Graph Editor, a script, an undo),
          coalesced into one call per idle tick; the carrier watch is re-made
          for each scene;
        * *on_time(frame)* -- the playhead moved.

        Every subscription is *owner*'s (``ScriptJobManager.unsubscribe_all``);
        with *widget*, they end when it is destroyed. Outside Maya nothing is
        subscribed. The time subscription persists across scenes (an
        ephemeral one was pruned by the very scene change that re-made it).

        Returns:
            The scene and time subscription tokens.
        """
        if cmds is None:
            return []
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        jobs = ScriptJobManager.instance()
        carrier_tokens: List[int] = []
        pending = [False]

        def flush():
            pending[0] = False
            on_tracks()

        def tracks_changed():
            if pending[0]:
                return
            pending[0] = True
            try:
                cmds.evalDeferred(flush, lowestPriority=True)
            except Exception:  # Maya not ready: the next edit retries
                pending[0] = False

        def watch_carrier():
            for token in carrier_tokens:
                jobs.unsubscribe(token)
            carrier_tokens[:] = []
            if on_tracks is not None:
                token = cls._watch_carrier(tracks_changed, owner)
                if token is not None:
                    carrier_tokens.append(token)

        def scene_opened():
            watch_carrier()
            if on_scene is not None:
                cmds.evalDeferred(on_scene)

        tokens = [
            jobs.subscribe(event, scene_opened, owner=owner)
            for event in ("SceneOpened", "NewSceneOpened")
        ]
        if on_time is not None:
            tokens.append(
                jobs.subscribe(
                    "timeChanged",
                    lambda: on_time(cls._current_frame()),
                    owner=owner,
                )
            )
        watch_carrier()
        if widget is not None:
            jobs.connect_cleanup(widget, owner=owner)
        return tokens

    @staticmethod
    def _watch_carrier(callback, owner) -> Optional[int]:
        """One ``MNodeMessage`` attribute-changed callback on the carrier,
        calling *callback* when an ``audio_clip_*`` attribute is set, added,
        removed or disconnected; ``None`` without a carrier yet."""
        carrier = _audio_utils.CARRIER_NODE
        if cmds is None or not cmds.objExists(carrier):
            return None
        try:
            import maya.api.OpenMaya as om2

            selection = om2.MSelectionList()
            selection.add(carrier)
            node = selection.getDependNode(0)
        except Exception:  # no OpenMaya, or the carrier would not resolve
            return None
        message = om2.MNodeMessage
        relevant = (
            message.kAttributeSet
            | message.kConnectionBroken
            | message.kAttributeAdded
            | message.kAttributeRemoved
        )
        prefix = _audio_utils.ATTR_PREFIX

        def changed(msg, plug, *_args):
            try:
                if msg & relevant and plug.partialName(useLongNames=True).startswith(
                    prefix
                ):
                    callback()
            except RuntimeError:
                pass  # the node went with its scene

        from mayatk.core_utils.script_job_manager import ScriptJobManager

        return ScriptJobManager.instance().add_om_callback(
            message.addAttributeChangedCallback, node, changed, owner=owner
        )

    @classmethod
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """The ``audio`` record for this scene, or ``None`` when there
        is nothing to publish -- the ``ptk.SceneRecords.AUDIO`` producer
        (``FbxUtils.PRODUCERS``).  Pure: it reads the keyed tracks and the
        takes and never writes.

        Reads every keyed track via :meth:`AudioUtils.bake_events` and scopes
        each event to its clip.  The takes are the ones the shot record
        declares (``ptk.SceneRecords.declared_takes``: produced earlier in the
        same assembly -- the shots producer runs first, the record declares
        ``after=("animation",)`` -- else as stored; each clip carries its
        range), exactly what ``FbxUtils.apply_takes_from_node``
        realizes into FBX takes, so scoping against them is correct by
        construction.  A take entry missing its name or range is dropped; when
        *no* entry carries a usable range the bake falls back to unscoped
        instead of silently dropping every event.  A ``full`` Animation Clips
        mode (``ctx.clip_mode``) realizes no take -- the FBX ships one
        whole-timeline clip -- so its events ship unscoped too: scoped to the
        shots, they would name clips the file does not have. An unscoped
        event counts from the first frame the file's animation holds:
        ``ctx.clip_span`` when the pipeline measured it, else the range the
        write bakes (``FbxUtils.planned_bake_range``), else the playback start
        -- blendertk's chain. One before it can never fire, and is dropped
        with a warning.

        Parameters:
            ctx: The export's decisions and the records produced before this
                one.

        Returns:
            The record (``{"version", "events": [...], "fps"}``: the frames
            count at the scene's rate, which travels with them), or ``None``
            when the carrier is missing or no track has keys.
        """
        if cmds is None:
            return None

        from mayatk.node_utils.data_nodes import DataNodes

        carrier = _audio_utils.CARRIER_NODE
        if not cmds.objExists(carrier):
            cls.logger.debug(
                f"export_record: no carrier {carrier!r} — nothing to export."
            )
            return None

        # Unity (and most game engines) start a clip's clock at 0, on the
        # first frame the file's animation holds, so an unscoped event counts
        # from that frame and a consumer computing ``frame / fps`` lands on
        # its moment: the clip origin the pipeline measured (``ctx.clip_span``),
        # else the range the write bakes, else the playback start --
        # blendertk's chain, so either DCC ships one scene's events at the
        # same frames.
        from mayatk.env_utils.fbx_utils import FbxUtils

        stack = ctx.clip_span or FbxUtils.planned_bake_range()
        if stack:
            origin = float(stack[0])
        else:
            try:
                origin = float(cmds.playbackOptions(q=True, min=True))
            except Exception:
                origin = 0.0

        events = _audio_utils.bake_events(carrier=carrier)
        takes = (
            []
            if ctx.clip_mode == "full"
            else ptk.SceneRecords.declared_takes(lambda key: ctx.record(key, DataNodes))
        )
        scoped = cls._scope_events(events, takes, origin)
        if not scoped:
            return None
        return ptk.SceneRecords.AUDIO.make(
            {"events": scoped, "fps": _audio_utils.get_fps()}
        )

    @classmethod
    @CoreUtils.undoable
    def prepare_for_export(cls) -> str:
        """Bake the scene-wide audio manifest for FBX export.

        The authoring-time publish of :meth:`export_record`: the record is
        produced and committed through ``FbxUtils.publish_authored`` onto the
        FBX export surface so it survives as a user property.  An export
        pipeline does not call this -- ``FbxUtils.PRODUCERS`` names
        :meth:`export_record`, which reads the takes the shots producer built
        in the same assembly.

        Clip scoping: when the Shots system has published its takes, each
        event is assigned to every take whose range contains it, with the
        frame rebased to that take's start — the same frames the imported
        AnimationClip counts from.  Events outside every take are dropped
        with a warning (they could never fire in any clip).  With no takes,
        events ship unscoped (``clip: ""``), counted from the first frame the
        file's animation holds (:meth:`export_record`) -- time 0 of the single
        imported clip.

        The manifest is a regenerated export artifact — authoring state (the
        keyed ``audio_clip_<id>`` enums and ``audio_file_map``) stays on
        ``data_internal``; only this baked projection ships, as the
        ``ptk.SceneRecords.AUDIO`` record on ``data_export``.  The value rides
        out as a string user-prop on the ``data_export`` GameObject in the
        imported FBX.  Downstream importers (e.g. unitytk
        ``AudioEventImporter``) attach a single scene-wide audio-event
        component from it and inject each event only into its own clip.
        Scenes written when the manifest was still proxy-mirrored are healed
        in place by the store's write (``DataNodes._drop_retired_proxy``), on
        this path and the export pipeline's alike.

        Idempotent — overwrites any prior value on the attr.  Called once
        before FBX export, typically from a scene-exporter pre-export hook.

        Returns:
            The baked manifest JSON string.  Empty when the carrier is
            missing or no tracks have keys (the channel is then cleared).
        """
        if cmds is None:
            return ""

        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        # The manifest is a regenerated export artifact, so it lives as a
        # plain string channel on data_export — not as authored state
        # mirrored from data_internal.
        record = cls.export_record(ptk.ExportContext(mode=ptk.ExportContext.AUTHORING))
        FbxUtils.publish_authored({ptk.SceneRecords.AUDIO: record})
        n_entries = len(record.payload["events"]) if record is not None else 0
        cls.logger.info(
            "prepare_for_export: stamped %s.%s with %d entr%s.",
            DataNodes.EXPORT,
            cls.MANIFEST_ATTR,
            n_entries,
            "y" if n_entries == 1 else "ies",
        )
        return record.text if record is not None else ""

    @classmethod
    def _scope_events(cls, events: list, takes: list, origin: float) -> list:
        """Assign baked ``(frame, name)`` events to their shot takes.

        ``ptk.AudioUtils.scope_events`` -- the rule blendertk's producer ships
        by too: an event lands in every take whose ``[start, end]`` range
        (inclusive) contains it, its ``frame`` rebased to that take's start; a
        take missing its name or range scopes nothing; with no usable take
        events ship unscoped (``clip: ""``), counted from *origin*, the first
        frame the file's animation holds. An event that can never fire --
        outside every take, or before *origin* -- is dropped with a warning
        that says which.
        """
        scoped, dropped = ptk.AudioUtils.scope_events(events, takes, origin)
        if dropped:
            where = (
                "outside every shot take"
                if takes
                else f"before the clip's first frame ({origin:g})"
            )
            cls.logger.warning(
                "Audio: %d event(s) %s were dropped (they can never fire in a "
                "clip): %s",
                len(dropped),
                where,
                ", ".join(dropped),
            )
        return scoped

    #: Explicit user opt-out (``disable_auto_export``) — session-global; wins
    #: over the automatic registration that creating a track performs.
    _auto_export_disabled = False

    @classmethod
    def enable_auto_export(cls) -> None:
        """Bake the audio manifest onto ``data_export`` before **every** FBX export.

        Opts the ``ptk.SceneRecords.AUDIO`` producer (:meth:`export_record`)
        into the shared before-export hook
        (:meth:`mayatk.env_utils.fbx_utils.FbxUtils.enable_export_producer`), so
        the manifest rides into **any** FBX export — File ▸ Export, the Game
        Exporter, a script — with no Scene Exporter and no staleness window.
        Session-global, and automatic once a track is created (authoring opts
        you in); composes with the Shots auto-export (both stamp distinct attrs
        on the shared ``data_export`` node).  Call :func:`disable_auto_export`
        to remove it for the session.
        """
        cls._auto_export_disabled = False
        cls._register_export_preparer()

    @classmethod
    def disable_auto_export(cls) -> None:
        """Opt the producer out of the before-export hook for the rest of the
        session.

        An explicit opt-out: the automatic registration performed by track
        creation respects it and won't re-install the hook.
        """
        cls._auto_export_disabled = True
        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.disable_export_producer(ptk.SceneRecords.AUDIO)

    @classmethod
    def _register_export_preparer(cls) -> None:
        """Opt the producer into the session hook unless the user explicitly
        opted out."""
        if cls._auto_export_disabled:
            return
        try:
            from mayatk.env_utils.fbx_utils import FbxUtils

            FbxUtils.enable_export_producer(ptk.SceneRecords.AUDIO)
        except Exception:  # outside Maya / hooks unavailable — never block authoring
            pass

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
            and os.path.basename(filepath).endswith(cls.COMPOSITE_FILENAME)
        ):
            try:
                os.remove(filepath)
            except OSError as exc:
                cls.logger.debug(f"Cannot remove composite WAV {filepath!r}: {exc}")

    @classmethod
    def _gather_composite_inputs(cls):
        """Return ``(events, audio_map, cache_dir)`` for the composite build.

        - **events**: ``[(frame, track_id), ...]`` time-ordered, every
          start key (value ≥ 1) across every track.
        - **audio_map**: ``{track_id: playable_path}`` — keys are
          normalized (lowercase) track_ids; values are FFmpeg-converted
          WAVs when needed.
        - **cache_dir**: Directory to place the composite in: the audio
          cache of the first file_map entry (``AudioUtils._cache_dirs_for`` --
          its project's ``cache/audio``, else the open scene's, else the
          per-user cache), where its transcodes live too.
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
            playable = _nodes.Nodes.resolve_playable_path(path)
            if playable:
                audio_map[tid] = playable

        events.sort(key=lambda e: e[0])

        first_path = next((p for p in file_map.values() if p), None)
        cache_dir = _audio_utils._cache_dirs_for(first_path)[0] if first_path else ""
        return events, audio_map, cache_dir

    @classmethod
    def _composite_filename(cls) -> str:
        """The open scene's composite file name (:attr:`COMPOSITE_FILENAME`)."""
        from mayatk.node_utils.data_nodes import DataNodes

        scene = DataNodes.scene_path()
        if not scene:
            return f"untitled{cls.COMPOSITE_FILENAME}"
        stem = os.path.splitext(os.path.basename(scene))[0]
        key = ptk.CachedArtifact.key(os.path.normcase(os.path.abspath(scene)), length=8)
        return f"{stem}_{key}{cls.COMPOSITE_FILENAME}"
