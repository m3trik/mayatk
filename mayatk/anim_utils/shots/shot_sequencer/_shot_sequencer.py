# coding=utf-8
"""Shot Sequencer — manages per-shot animation with ripple editing.

Shots are contiguous keyframe ranges ("blocks") along the timeline.
Changing one shot's duration or position ripples downstream shots.
"""
from contextlib import contextmanager
from typing import List, Dict, Optional, Any

try:
    import pymel.core as pm
except ImportError as error:
    pm = None
    print(__file__, error)


from mayatk.anim_utils.shots._shots import ShotBlock, ShotStore, detect_shot_regions


# ---------------------------------------------------------------------------
# ShotSequencer
# ---------------------------------------------------------------------------


class ShotSequencer:
    """Manages a :class:`ShotStore` and provides ripple editing and
    keyframe manipulation on top of it.

    Parameters:
        shots: Initial shot list (creates an internal ShotStore).
        store: Existing ShotStore to wrap.  Takes precedence over *shots*.
    """

    def __init__(
        self,
        shots: Optional[List[ShotBlock]] = None,
        store: Optional[ShotStore] = None,
    ):
        if store is not None:
            self.store = store
        else:
            self.store = ShotStore(shots)
        # Populated only inside :meth:`audio_prefetch` to amortise
        # Maya reads across many ``collect_shot_sequences`` calls.
        self._audio_events_cache: Optional[Dict[str, List[tuple]]] = None

    # ---- delegated properties -------------------------------------------

    @property
    def shots(self) -> List[ShotBlock]:
        return self.store.shots

    @shots.setter
    def shots(self, value: List[ShotBlock]):
        self.store.shots = value

    @property
    def hidden_objects(self) -> set:
        return self.store.hidden_objects

    @hidden_objects.setter
    def hidden_objects(self, value: set):
        self.store.hidden_objects = value

    @property
    def markers(self) -> List[Dict[str, Any]]:
        return self.store.markers

    @markers.setter
    def markers(self, value: List[Dict[str, Any]]):
        self.store.markers = value

    def is_object_hidden(self, obj_name: str) -> bool:
        return self.store.is_object_hidden(obj_name)

    def set_object_hidden(self, obj_name: str, hidden: bool = True) -> None:
        self.store.set_object_hidden(obj_name, hidden)

    # ---- query -----------------------------------------------------------

    def sorted_shots(self) -> List[ShotBlock]:
        return self.store.sorted_shots()

    def shot_by_id(self, shot_id: int) -> Optional[ShotBlock]:
        return self.store.shot_by_id(shot_id)

    def shot_by_name(self, name: str) -> Optional[ShotBlock]:
        return self.store.shot_by_name(name)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _find_keyed_transforms(
        start: float, end: float, value_tolerance: float = 1e-4
    ) -> List[str]:
        """Return names of all transforms with non-flat animation in [start, end].

        Objects whose curves are entirely constant (all values within
        *value_tolerance*) across the range are excluded.  Only standard
        transform/visibility attributes are considered — custom user
        attributes (e.g. ``audio_trigger``) are ignored so marker objects
        don't appear as scene content.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.shots._shots import _map_standard_curves_to_transforms

        transform_curves = _map_standard_curves_to_transforms()
        if not transform_curves:
            return []

        # Keep only transforms where at least one curve changes value
        # within the requested range.
        result = []
        for xform, crvs in sorted(transform_curves.items()):
            for crv in crvs:
                vals = cmds.keyframe(crv, q=True, time=(start, end), valueChange=True)
                if vals and (max(vals) - min(vals)) > value_tolerance:
                    result.append(xform)
                    break
        return result

    # ---- manual definition -----------------------------------------------

    def define_shot(
        self,
        name: str,
        start: float,
        end: float,
        objects: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        locked: bool = False,
        description: str = "",
    ) -> ShotBlock:
        """Define a shot manually from a name and range.

        Parameters:
            name: Human-readable label.
            start: First frame.
            end: Last frame.
            objects: Transform node names.  If ``None``, automatically
                discovers all transforms with keyframes in [start, end].
            metadata: Arbitrary key/value pairs to persist with the shot.
            locked: Mark this shot as user-finalized.
            description: Human-readable description of the shot.

        Returns:
            The newly created :class:`ShotBlock`.
        """
        if objects is None:
            objects = self._find_keyed_transforms(start, end)
        return self.store.define_shot(
            name=name,
            start=start,
            end=end,
            objects=objects,
            metadata=metadata,
            locked=locked,
            description=description,
        )

    @classmethod
    def from_current_range(
        cls,
        name: str = "Shot",
        objects: Optional[List[str]] = None,
    ) -> "ShotSequencer":
        """Create a ShotSequencer with one shot spanning Maya's current
        playback range.

        Parameters:
            name: Label for the shot.
            objects: Transform node names.  If ``None``, automatically
                discovers all transforms with keyframes in the range.

        Returns:
            A new :class:`ShotSequencer` with a single shot.
        """
        start = pm.playbackOptions(q=True, min=True)
        end = pm.playbackOptions(q=True, max=True)
        if objects is None:
            objects = cls._find_keyed_transforms(start, end)
        block = ShotBlock(
            shot_id=0,
            name=name,
            start=float(start),
            end=float(end),
            objects=sorted(set(objects)),
        )
        return cls([block])

    @staticmethod
    def _shot_nodes(shot: ShotBlock) -> list:
        """Return long DAG path strings for a shot's objects.

        Uses ``cmds.ls`` with ``long=True`` to validate each name exists
        in the scene and return unambiguous DAG paths.
        """
        import maya.cmds as cmds

        if not shot.objects:
            return []
        return cmds.ls(shot.objects, long=True) or []

    @staticmethod
    def _reconcile_stale_paths(shot: ShotBlock) -> bool:
        """Re-resolve stale long DAG paths by short name.

        When a parent node is renamed the long paths of all children
        change, making the stored entries stale.  This helper extracts
        the short (leaf) name from each stale entry, looks it up in
        the current scene, and substitutes the updated long path.

        Returns ``True`` if any paths were updated.
        """
        import maya.cmds as cmds

        updated = False
        new_objects: list = []
        for obj in shot.objects:
            if cmds.objExists(obj):
                new_objects.append(obj)
                continue
            short = obj.rsplit("|", 1)[-1]
            matches = cmds.ls(short, long=True, type="transform") or []
            if len(matches) == 1:
                new_objects.append(matches[0])
                updated = True
            elif matches:
                # Multiple matches — prefer one with anim curves,
                # fall back to the first match.
                resolved = matches[0]
                for m in matches:
                    if cmds.listConnections(m, type="animCurve", s=True, d=False):
                        resolved = m
                        break
                new_objects.append(resolved)
                updated = True
            # else: no scene node with this short name → truly deleted.
        result = sorted(set(new_objects))
        if result != sorted(shot.objects):
            shot.objects = result
            updated = True
        return updated

    def reconcile_all_shots(self) -> bool:
        """Re-resolve stale DAG paths across every shot and persist changes.

        Should be called once per refresh cycle *before* segment collection
        so that all stored paths are current.

        Returns ``True`` if any shot was modified.
        """
        changed = False
        with self.store.batch_update():
            for shot in self.store.shots:
                nodes = self._shot_nodes(shot)
                if shot.objects and len(nodes) < len(set(shot.objects)):
                    if self._reconcile_stale_paths(shot):
                        self.store.update_shot(shot.shot_id, objects=shot.objects)
                        changed = True
        return changed

    def collect_object_segments(
        self,
        shot_id: int,
        ignore: Optional[str] = None,
        motion_rate: float = 1e-3,
        ignore_holds: bool = True,
    ) -> List[Dict[str, Any]]:
        """Collect per-object animation segments within a shot's range.

        Each returned dict has ``"obj"`` (str), ``"start"``, ``"end"``,
        and ``"duration"`` keys — suitable for populating per-object
        tracks in the sequencer widget.

        Parameters:
            shot_id: The shot whose objects and range to query.
            ignore: Attribute pattern(s) to exclude.
            motion_rate: Per-frame rate-of-change threshold.
            ignore_holds: If True (default), flat-key hold spans are
                excluded so only actual motion is shown.  When False,
                trailing holds are absorbed into adjacent motion
                segments (wider clips) and hold-only objects (flat keys,
                no motion) produce a single segment spanning all keys.

        Returns:
            A list of segment dicts grouped by object.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            return []

        nodes = self._shot_nodes(shot)
        if not nodes:
            # Shot has no (valid) objects — auto-discover animated transforms
            discovered = self._find_keyed_transforms(shot.start, shot.end)
            if discovered:
                shot.objects = sorted(set(discovered))
                self.store.update_shot(shot.shot_id, objects=shot.objects)
                nodes = self._shot_nodes(shot)
            if not nodes:
                return []

        from mayatk.anim_utils.segment_keys import SegmentKeys

        segments = SegmentKeys.collect_segments(
            nodes,
            split_static=True,
            ignore=ignore,
            time_range=(shot.start, shot.end),
            ignore_holds=ignore_holds,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=motion_rate,
        )
        # Normalise obj to str — defensive; values are already strings
        # post-cmds migration, but callers historically passed PyNodes.
        for seg in segments:
            seg["obj"] = str(seg["obj"])
        return segments

    # ---- unified sequence model (anim + audio) ---------------------------

    def _collect_audio_sequences(
        self, start: float, end: float
    ) -> List[Dict[str, Any]]:
        """Return audio events overlapping ``[start, end]`` as sequence dicts.

        Each dict carries ``{"kind": "audio", "obj": <track_id>, "start", "end"}``.
        Tracks with no defined stop frame fall back to ``end`` so a finite
        range can be reported.

        Inside an :meth:`audio_prefetch` block the full-track events are
        read once and reused across calls; outside, every call reads
        fresh (safe default — no staleness risk from external writers).
        """
        if pm is None:
            return []
        all_events = self._audio_events_cache
        if all_events is None:
            all_events = self._read_all_audio_events()
        sequences: List[Dict[str, Any]] = []
        for tid, events in all_events.items():
            for ev_start, ev_stop in events:
                ev_end = ev_stop if ev_stop is not None else float(end)
                if ev_end < start or ev_start > end:
                    continue
                sequences.append(
                    {
                        "kind": "audio",
                        "obj": tid,
                        "start": ev_start,
                        "end": ev_end,
                    }
                )
        return sequences

    @contextmanager
    def audio_prefetch(self):
        """Cache per-track audio events for the duration of the block.

        Intended for UI-refresh loops that call
        :meth:`collect_shot_sequences` once per visible shot — inside the
        block, Maya is read one time total; outside, every call reads
        fresh so external audio edits are never masked.  Not nestable
        safely across mutations: do not write audio inside the block.
        """
        self._audio_events_cache = self._read_all_audio_events()
        try:
            yield
        finally:
            self._audio_events_cache = None

    @staticmethod
    def _read_all_audio_events() -> Dict[str, List[tuple]]:
        """Return ``{track_id: [(start, stop), ...]}`` for every audio track.

        Bypasses :meth:`AudioUtils.read_events` per-track
        ``has_track``/``attributeQuery`` round trip by trusting the
        attrs returned from :meth:`list_track_attrs`.
        """
        try:
            import maya.cmds as cmds
            from mayatk.audio_utils._audio_utils import AudioUtils
        except ImportError:
            return {}
        carriers = AudioUtils.find_carriers()
        if not carriers:
            return {}
        carrier = carriers[0]
        out: Dict[str, List[tuple]] = {}
        for attr_name in AudioUtils.list_track_attrs(carrier):
            plug = f"{carrier}.{attr_name}"
            frames = cmds.keyframe(plug, q=True) or []
            if not frames:
                continue
            vals = cmds.keyframe(plug, q=True, valueChange=True) or []
            pairs = sorted(zip(frames, vals), key=lambda p: p[0])
            events: List[tuple] = []
            pending_start: Optional[float] = None
            for frame, val in pairs:
                is_on = int(round(val)) >= 1
                if is_on:
                    if pending_start is not None:
                        events.append((pending_start, None))
                    pending_start = float(frame)
                else:
                    if pending_start is not None:
                        events.append((pending_start, float(frame)))
                        pending_start = None
            if pending_start is not None:
                events.append((pending_start, None))
            if events:
                out[AudioUtils.track_id_from_attr(attr_name)] = events
        return out

    def collect_shot_sequences(
        self,
        shot_id: int,
        include_audio: bool = True,
    ) -> List[Dict[str, Any]]:
        """Return all sequences (anim + audio) inside a shot's range.

        Each item is a dict with ``"kind"`` (``"anim"`` or ``"audio"``),
        ``"obj"`` (transform name or audio track id), ``"start"``, ``"end"``.
        Anim segments come from :meth:`collect_object_segments`; audio
        comes from :meth:`_collect_audio_sequences`.
        """
        anim = self.collect_object_segments(shot_id)
        result: List[Dict[str, Any]] = [
            {
                "kind": "anim",
                "obj": seg["obj"],
                "start": seg["start"],
                "end": seg["end"],
            }
            for seg in anim
        ]
        if include_audio:
            shot = self.shot_by_id(shot_id)
            if shot is not None:
                result.extend(self._collect_audio_sequences(shot.start, shot.end))
        return result

    def _move_sequence(self, seq: Dict[str, Any], new_start: float) -> None:
        """Dispatch a sequence move based on ``seq["kind"]``.

        Anim sequences re-use :meth:`move_object_keys`; audio sequences
        delegate to :func:`AudioUtils.shift_keys_in_range` via
        :meth:`_shift_audio`.  Caller is responsible for any wrapping
        ``audio_utils.batch()`` / ``store.batch_update()`` context.
        """
        delta = new_start - seq["start"]
        if abs(delta) < 1e-6:
            return
        if seq["kind"] == "anim":
            self.move_object_keys(seq["obj"], seq["start"], seq["end"], new_start)
        elif seq["kind"] == "audio":
            from mayatk.audio_utils._audio_utils import AudioUtils

            with AudioUtils.batch() as b:
                tids = AudioUtils.shift_keys_in_range(
                    seq["start"], seq["end"], delta, track_ids=[seq["obj"]]
                )
                if tids:
                    b.mark_dirty(tids)

    def _recompute_shot_objects(self, shot_id: int) -> None:
        """Rebuild ``shot.objects`` from animation that actually lives in the shot.

        Scans every anim sequence inside the shot's frame range and keeps
        only the transforms that contribute keys.  Locked / pinned objects
        are preserved even when they have no remaining keys.  Audio is
        out of scope — audio tracks are not part of ``shot.objects``.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            return
        if pm is None:
            return
        anim_objs = {
            seg["obj"] for seg in self.collect_object_segments(shot_id)
        }
        keep = self.store.pinned_objects | self.store.locked_objects
        new_objs = sorted(set(shot.objects) & (anim_objs | keep) | anim_objs)
        if new_objs != sorted(shot.objects):
            self.store.update_shot(shot_id, objects=new_objs)

    # ---- move sequences across shots -------------------------------------

    def _source_shot_id_for(self, seq: Dict[str, Any]) -> Optional[int]:
        """Return the shot_id that currently contains *seq* (by frame range)."""
        for sh in self.store.shots:
            if sh.start - 1e-6 <= seq["start"] and seq["end"] <= sh.end + 1e-6:
                return sh.shot_id
        return None

    def move_sequences_to_shot(
        self,
        sequences: List[Dict[str, Any]],
        dest_shot_id: int,
    ) -> None:
        """Move *sequences* (anim and/or audio) into *dest_shot_id*.

        Sequences are grouped by source shot so each subgroup moves as a
        unit, preserving internal offsets.  Placement inside the
        destination depends on whether the destination already contains a
        sequence on the same object:

            - If yes: the subgroup is placed adjacent — *after* the
              existing range when the source shot lies upstream of the
              destination, *before* when downstream.
            - If no: the subgroup is anchored to the destination start.

        After the move, ``shot.objects`` is recomputed for the destination
        and every source shot that lost content.

        Parameters:
            sequences: dicts with ``"kind"``, ``"obj"``, ``"start"``,
                ``"end"`` (as produced by :meth:`collect_shot_sequences`).
            dest_shot_id: Target shot's id.
        """
        dest = self.shot_by_id(dest_shot_id)
        if dest is None:
            raise ValueError(f"No shot with id {dest_shot_id}")
        if not sequences:
            return

        dest_seqs_by_obj: Dict[str, List[Dict[str, Any]]] = {}
        for s in self.collect_shot_sequences(dest_shot_id):
            dest_seqs_by_obj.setdefault(s["obj"], []).append(s)

        groups: Dict[Optional[int], List[Dict[str, Any]]] = {}
        for seq in sequences:
            sid = self._source_shot_id_for(seq)
            if sid == dest_shot_id:
                continue  # already in destination — skip
            groups.setdefault(sid, []).append(seq)

        if not groups:
            return

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        affected_shots: set = {dest_shot_id}

        # Pre-register moved anim objects on dest so that the post-move
        # _recompute_shot_objects pass actually scans them.  Without this,
        # collect_object_segments would only see dest's existing objects
        # and the newly-moved transforms would never make it into
        # dest.objects.
        dest_anim_additions = {
            seq["obj"]
            for grp in groups.values()
            for seq in grp
            if seq["kind"] == "anim"
        }
        if dest_anim_additions:
            merged = sorted(set(dest.objects) | dest_anim_additions)
            if merged != sorted(dest.objects):
                self.store.update_shot(dest_shot_id, objects=merged)

        with audio_utils.batch(), self.store.batch_update():
            for source_id, group in groups.items():
                src = self.shot_by_id(source_id) if source_id is not None else None
                direction = "right"
                if src is not None and src.start > dest.start:
                    direction = "left"

                base = min(s["start"] for s in group)
                group_end = max(s["end"] for s in group)
                group_dur = group_end - base

                existing: List[Dict[str, Any]] = []
                for seq in group:
                    existing.extend(dest_seqs_by_obj.get(seq["obj"], []))

                if existing:
                    if direction == "right":
                        anchor = max(e["end"] for e in existing)
                    else:
                        anchor = min(e["start"] for e in existing) - group_dur
                else:
                    anchor = dest.start

                for seq in group:
                    offset = seq["start"] - base
                    self._move_sequence(seq, anchor + offset)

                    # Track new dest range for subsequent groups so multiple
                    # subgroups stack instead of stomping each other.
                    new_start = anchor + offset
                    new_end = new_start + (seq["end"] - seq["start"])
                    dest_seqs_by_obj.setdefault(seq["obj"], []).append(
                        {
                            "kind": seq["kind"],
                            "obj": seq["obj"],
                            "start": new_start,
                            "end": new_end,
                        }
                    )

                if source_id is not None:
                    affected_shots.add(source_id)

            for sid in affected_shots:
                self._recompute_shot_objects(sid)

        # Auto-extend the destination shot if any moved content overruns
        # its current boundaries.  This ripples upstream/downstream as
        # needed, preserving spacing — extend-to-fit is implicit, never a
        # separate user action.
        self.extend_shot_to_fit(dest_shot_id)

    # ---- shot fit / trim / extend ----------------------------------------

    def fit_shot_to_content(
        self, shot_id: int, mode: str = "fit"
    ) -> tuple[float, float]:
        """Resize a shot's boundaries to its sequence content, rippling neighbors.

        Mode controls direction:
            ``"fit"`` — boundaries snap exactly to content (both expand and
                contract as needed).
            ``"trim"`` — only contract empty space; boundaries move *inward*
                and never past content.
            ``"extend"`` — only expand to enclose out-of-range content;
                boundaries move *outward* and never inward.

        Neighbouring shots ripple by the head/tail deltas so spacing is
        preserved.  Audio shifts are batched.

        Returns:
            ``(head_delta, tail_delta)`` — the amount the start/end moved.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        sequences = self.collect_shot_sequences(shot_id)
        if not sequences:
            return 0.0, 0.0

        content_start = min(s["start"] for s in sequences)
        content_end = max(s["end"] for s in sequences)

        if mode == "trim":
            new_start = max(shot.start, content_start)
            new_end = min(shot.end, content_end)
        elif mode == "extend":
            new_start = min(shot.start, content_start)
            new_end = max(shot.end, content_end)
        else:  # "fit"
            new_start = content_start
            new_end = content_end

        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        head_delta = new_start - shot.start
        tail_delta = new_end - shot.end
        if abs(head_delta) < 1e-6 and abs(tail_delta) < 1e-6:
            return 0.0, 0.0

        old_start, old_end = shot.start, shot.end

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        shifted_audio: set = set()
        with audio_utils.batch():
            shot.start = new_start
            shot.end = new_end
            if abs(tail_delta) > 1e-6:
                self._ripple_downstream(shot_id, old_end, tail_delta, shifted_audio)
            if abs(head_delta) > 1e-6:
                self._ripple_upstream(shot_id, old_start, head_delta, shifted_audio)

        self._enforce_gap_holds()
        return head_delta, tail_delta

    def trim_shot_to_content(self, shot_id: int) -> tuple[float, float]:
        """Shrink shot boundaries inward so they exactly enclose content.

        Empty leading/trailing space is removed; downstream/upstream shots
        ripple to preserve their spacing.
        """
        return self.fit_shot_to_content(shot_id, mode="trim")

    def extend_shot_to_fit(self, shot_id: int) -> tuple[float, float]:
        """Expand shot boundaries outward to enclose all of its sequences.

        If sequences extend past the current head or tail, the shot grows
        to cover them and neighbouring shots ripple outward.
        """
        return self.fit_shot_to_content(shot_id, mode="extend")

    # ---- automatic shot detection ----------------------------------------

    def detect_shots(
        self,
        objects: Optional[List[str]] = None,
        gap_threshold: float = 5.0,
        ignore: Optional[str] = None,
        motion_rate: float = 1e-3,
        min_duration: float = 2.0,
    ) -> List[Dict[str, Any]]:
        """Detect shot boundaries from existing animation on *objects*.

        Delegates to :func:`~mayatk.anim_utils.shots._shots.detect_shot_regions`
        for the actual clustering.  Flat/constant-value intervals are
        always excluded.

        Parameters:
            objects: Transform node names to scan.  If ``None``, all
                transforms with animation curves are discovered.
            gap_threshold: Minimum gap (frames) between clusters to
                split them into separate shots.
            ignore: Attribute pattern(s) to exclude.
            motion_rate: Per-frame rate-of-change threshold.
            min_duration: Minimum shot duration in frames.

        Returns:
            A list of candidate shot dicts, each with ``"name"``,
            ``"start"``, ``"end"``, and ``"objects"`` keys — suitable
            for passing to :meth:`define_shot`.
        """
        return detect_shot_regions(
            objects=objects,
            gap_threshold=gap_threshold,
            ignore=ignore,
            motion_rate=motion_rate,
            min_duration=min_duration,
        )

    def detect_next_shot(
        self,
        gap_threshold: float = 5.0,
        ignore: Optional[str] = None,
        motion_rate: float = 1e-3,
    ) -> Optional[Dict[str, Any]]:
        """Detect the first animation cluster after all existing shots.

        Useful for incremental shot building — discovers the next
        unregistered animation region without re-scanning the entire
        timeline.

        Parameters:
            gap_threshold: Minimum gap (frames) between clusters.
            ignore: Attribute pattern(s) to exclude.
            motion_rate: Per-frame rate threshold (see :meth:`detect_shots`).

        Returns:
            A candidate shot dict (``name``, ``start``, ``end``,
            ``objects``) or ``None`` if no uncovered animation remains.
        """
        candidates = self.detect_shots(
            gap_threshold=gap_threshold,
            ignore=ignore,
            motion_rate=motion_rate,
        )
        if not candidates:
            return None

        existing = self.store.sorted_shots()
        if not existing:
            return candidates[0]

        # Find the first candidate whose start is beyond all existing shots
        last_end = max(s.end for s in existing)
        for cand in candidates:
            if cand["start"] >= last_end:
                return cand

        # Fall back: find candidates that don't overlap any existing shot
        for cand in candidates:
            overlaps = False
            for shot in existing:
                if cand["start"] < shot.end and cand["end"] > shot.start:
                    overlaps = True
                    break
            if not overlaps:
                return cand

        return None

    # ---- per-object keyframe editing -------------------------------------

    def move_object_keys(
        self,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
    ) -> None:
        """Offset all keyframes of *obj* that fall within [old_start, old_end]
        so the segment begins at *new_start*.

        Parameters:
            obj: Transform node name.
            old_start: Original first frame of the segment.
            old_end: Original last frame of the segment.
            new_start: Desired first frame after the move.
        """
        import maya.cmds as cmds

        # Resolve to full DAG path to avoid crashes on ambiguous short names
        matches = cmds.ls(obj, long=True)
        if not matches:
            return
        obj_path = matches[0]

        delta = new_start - old_start
        if abs(delta) < 1e-6:
            return

        # Operate on individual anim curves so we only affect keys in the
        # requested time range without colliding with keys elsewhere.
        curves = cmds.listConnections(obj_path, type="animCurve", s=True, d=False) or []
        curves = list(set(curves))  # deduplicate
        if not curves:
            return

        eps = 1e-3
        tr = (old_start - eps, old_end + eps)

        for crv in curves:
            # Skip curves that have no keys in this range
            if not cmds.keyframe(crv, q=True, time=tr):
                continue
            try:
                cmds.keyframe(
                    crv,
                    edit=True,
                    relative=True,
                    timeChange=delta,
                    time=tr,
                )
            except RuntimeError:
                pass

    def move_stepped_keys(
        self,
        obj: str,
        old_time: float,
        new_time: float,
        attr_name: str | None = None,
        eps: float = 1e-3,
    ) -> None:
        """Move stepped keys at *old_time* to *new_time* via delete-and-recreate.

        If *attr_name* is given, only that attribute's curves are moved.
        Otherwise all curves on *obj* with a stepped key at *old_time*.

        Uses cutKey + setKeyframe instead of timeChange to avoid Maya
        silently misplacing keys at large offsets.
        """
        import maya.cmds as cmds

        if abs(new_time - old_time) < 1e-6:
            return

        matches = cmds.ls(obj, long=True)
        if not matches:
            return
        obj_path = matches[0]
        tr = (old_time - eps, old_time + eps)

        # Resolve which curves to move
        if attr_name:
            plug = f"{obj_path}.{attr_name}"
            if not cmds.objExists(plug):
                return
            raw = cmds.listConnections(plug, type="animCurve", s=True, d=False) or []
            curves = [(c, plug) for c in raw]
        else:
            all_curves = list(
                set(
                    cmds.listConnections(obj_path, type="animCurve", s=True, d=False)
                    or []
                )
            )
            curves = []
            for crv in all_curves:
                if not cmds.keyframe(crv, q=True, time=tr):
                    continue
                ot = cmds.keyTangent(crv, q=True, time=tr, outTangentType=True)
                if ot and ot[0] in ("step", "stepnext"):
                    conns = cmds.listConnections(crv, plugs=True, d=True, s=False) or []
                    curves.append((crv, conns[0] if conns else None))

        # Delete-and-recreate each key
        for crv, plug in curves:
            vals = cmds.keyframe(crv, q=True, time=tr, valueChange=True)
            in_tan = cmds.keyTangent(crv, q=True, time=tr, inTangentType=True)
            out_tan = cmds.keyTangent(crv, q=True, time=tr, outTangentType=True)
            if not vals:
                continue
            val = vals[0]
            itt = in_tan[0] if in_tan else "stepnext"
            ott = out_tan[0] if out_tan else "step"

            cmds.cutKey(crv, time=tr, clear=True)
            # cutKey may delete the curve node if it was the last key;
            # fall back to the driven plug so setKeyframe recreates it.
            target = crv if cmds.objExists(crv) else plug
            if not target:
                target = obj_path
            cmds.setKeyframe(target, time=new_time, value=val)
            cmds.keyTangent(
                target,
                time=(new_time, new_time),
                inTangentType=itt,
                outTangentType=ott,
            )

    @staticmethod
    def _batch_move_keys(cmds, objects, old_start, old_end, new_start):
        """Move keyframes for all *objects* from [old_start, old_end] to new_start.

        Resolves curves in a single batch rather than per-object, then
        shifts keys using a direct single-pass move.
        """
        if not objects:
            return
        delta = new_start - old_start
        if abs(delta) < 1e-6:
            return

        # Batch-resolve: one ls + one listConnections for all objects
        long_names = cmds.ls(objects, long=True) or []
        if not long_names:
            return
        curves = (
            cmds.listConnections(long_names, type="animCurve", s=True, d=False) or []
        )
        curves = list(set(curves))
        if not curves:
            return

        eps = 1e-3
        tr = (old_start - eps, old_end + eps)

        for crv in curves:
            if not cmds.keyframe(crv, q=True, time=tr):
                continue
            try:
                cmds.keyframe(
                    crv,
                    edit=True,
                    relative=True,
                    timeChange=delta,
                    time=tr,
                )
            except RuntimeError:
                pass

    @staticmethod
    def _shift_audio(
        cmds,
        old_start: float,
        old_end: float,
        delta: float,
        shifted: Optional[set] = None,
    ) -> None:
        """Shift audio clips whose timeline position falls within a range.

        Delegates to :func:`mayatk.audio_utils.shift_keys_in_range`
        which updates the canonical keyed store. Callers are expected
        to wrap bulk ops in an ``audio_utils.batch()`` so the compositor
        re-renders derived DG audio nodes in a single sync.

        Parameters:
            cmds: Unused (retained for historical signature).
            old_start: Start of the time range to shift.
            old_end: End of the time range to shift.
            delta: Frames to add to each audio key.
            shifted: Unused (per-node dedup was needed for the legacy
                offset-attr path; the new per-track primitive is
                intrinsically dedup-safe).
        """
        if abs(delta) < 1e-6:
            return
        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch() as b:
            tids = audio_utils.shift_keys_in_range(old_start, old_end, delta)
            if tids:
                b.mark_dirty(tids)

    def _move_shot_content(
        self,
        shot: "ShotBlock",
        new_start: float,
        shifted_audio: Optional[set] = None,
    ) -> None:
        """Shift all content (object keys and audio) for *shot* to *new_start*.

        Uses :meth:`_batch_move_keys` for animation curves and
        :meth:`_shift_audio` for DG audio nodes / event triggers,
        then updates the shot boundaries.  When Maya is not available
        only the boundaries are updated.

        Parameters:
            shot: The shot to move.
            new_start: Desired first frame after the move.
            shifted_audio: Optional set forwarded to :meth:`_shift_audio`
                to prevent double-shifting nodes across sequential calls.
        """
        new_start = self.store.snap(new_start)
        old_start = shot.start
        old_end = shot.end
        delta = new_start - old_start
        if abs(delta) < 1e-6:
            return

        duration = old_end - old_start

        if pm is not None:
            import maya.cmds as cmds

            self._batch_move_keys(cmds, shot.objects, old_start, old_end, new_start)
            self._shift_audio(cmds, old_start, old_end, delta, shifted_audio)

        shot.start = new_start
        shot.end = self.store.snap(new_start + duration)

    def move_object_in_shot(
        self,
        shot_id: int,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        prevent_overlap: bool = False,
    ) -> None:
        """Move one object's keys within a shot, expanding the shot and
        rippling downstream shots when the clip exceeds shot boundaries.

        Parameters:
            shot_id: Shot the object belongs to.
            obj: Transform node name to move.
            old_start: Original first frame of the object segment.
            old_end: Original last frame of the object segment.
            new_start: Desired first frame after the move.
            prevent_overlap: If True, push other objects in the same shot
                that would overlap with the moved object's new range.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        new_start = self.store.snap(new_start)
        dur = old_end - old_start
        new_end = self.store.snap(new_start + dur)

        # Move the object's keys
        self.move_object_keys(obj, old_start, old_end, new_start)

        # Optionally push overlapping objects within the same shot
        if prevent_overlap:
            self._push_overlapping_objects(shot, obj, new_start, new_end)

        # Check if the clip now exceeds the shot boundaries
        prior_start = shot.start
        prior_end = shot.end
        start_expanded = False
        end_expanded = False

        if new_start < shot.start:
            shot.start = new_start
            start_expanded = True

        if new_end > shot.end:
            shot.end = new_end
            end_expanded = True

        # Ripple upstream by however much the shot head grew
        if start_expanded:
            start_delta = shot.start - prior_start  # negative
            if abs(start_delta) > 1e-6:
                shifted_audio: set = set()
                self._ripple_upstream(shot_id, prior_start, start_delta, shifted_audio)

        # Ripple downstream by however much the shot tail grew
        if end_expanded:
            end_delta = shot.end - prior_end  # positive
            if abs(end_delta) > 1e-6:
                if not start_expanded:
                    shifted_audio = set()
                self._ripple_downstream(shot_id, prior_end, end_delta, shifted_audio)
        # Do NOT call _enforce_gap_holds() here — it iterates ALL objects
        # in every pre-gap shot and sets out-tangents to "step", corrupting
        # tangent types on objects the user didn't touch.  Gap holds are
        # enforced by respace() and move_shot() (whole-shot operations).

    def _push_overlapping_objects(
        self,
        shot: ShotBlock,
        moved_obj: str,
        moved_start: float,
        moved_end: float,
    ) -> None:
        """Push other objects in *shot* to resolve overlaps with the moved object.

        Objects whose animation range overlaps with [moved_start, moved_end]
        are shifted forward so they start at moved_end.  This cascades: if
        pushing one object causes a new overlap with the next, that object
        is pushed too.
        """
        segments = self.collect_object_segments(shot.shot_id)
        # Build per-object ranges (excluding the moved object)
        obj_ranges = {}
        for seg in segments:
            name = seg["obj"]
            if name == moved_obj:
                continue
            if name in obj_ranges:
                obj_ranges[name] = (
                    min(obj_ranges[name][0], seg["start"]),
                    max(obj_ranges[name][1], seg["end"]),
                )
            else:
                obj_ranges[name] = (seg["start"], seg["end"])

        # Sort by start time and cascade pushes
        sorted_objs = sorted(obj_ranges.items(), key=lambda x: x[1][0])
        push_end = moved_end
        for name, (s, e) in sorted_objs:
            if s < push_end and e > moved_start:
                delta = push_end - s
                self.move_object_keys(name, s, e, s + delta)
                push_end = e + delta
            else:
                push_end = max(push_end, e)

    def scale_object_keys(
        self,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Scale (and optionally shift) keyframes of *obj* from
        [old_start, old_end] into [new_start, new_end].

        Parameters:
            obj: Transform node name.
            old_start: Original first frame.
            old_end: Original last frame.
            new_start: Desired first frame.
            new_end: Desired last frame.
        """
        import maya.cmds as cmds

        if not cmds.objExists(obj):
            return
        if abs(old_end - old_start) < 1e-6:
            return
        cmds.scaleKey(
            obj,
            time=(old_start, old_end),
            newStartTime=new_start,
            newEndTime=new_end,
        )

    # ---- ripple editing --------------------------------------------------

    def move_shot(self, shot_id: int, new_start: float) -> None:
        """Move an entire shot (all object keys) to *new_start*, rippling downstream.

        The shot's duration is preserved.  All keyframes belonging to
        the shot's objects are shifted by the same delta.  Downstream
        shots are then shifted to maintain their original spacing
        relative to this shot's end.

        Parameters:
            shot_id: The shot to move.
            new_start: Desired new start frame.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        old_start = shot.start
        old_end = shot.end
        delta = new_start - old_start
        if abs(delta) < 1e-6:
            return

        shifted_audio: set = set()
        self._move_shot_content(shot, new_start, shifted_audio)

        # Ripple every downstream shot by the same delta
        for s in self.sorted_shots():
            if s.shot_id == shot_id:
                continue
            if s.start >= old_end - 1e-6:
                self._move_shot_content(s, s.start + delta, shifted_audio)

        self._enforce_gap_holds()

    def slide_shot(
        self,
        shot_id: int,
        new_start: float,
        direction: str = "downstream",
        _enforce: bool = True,
    ) -> None:
        """Slide a shot intact to *new_start*, rippling only in *direction*.

        Unlike :meth:`move_shot` which always ripples downstream, this
        method lets the caller choose which side of the timeline absorbs
        the displacement.  The shot's duration and internal keyframes
        are preserved (translated, not scaled).

        Parameters:
            shot_id: The shot to slide.
            new_start: Desired new start frame.
            direction: ``"downstream"`` ripples shots after this one;
                ``"upstream"`` ripples shots before this one.
            _enforce: If True (default), call :meth:`_enforce_gap_holds`
                after the operation.  Pass False when batching multiple
                edits and calling it once at the end.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        old_start = shot.start
        old_end = shot.end
        delta = new_start - old_start
        if abs(delta) < 1e-6:
            return

        shifted_audio: set = set()
        self._move_shot_content(shot, new_start, shifted_audio)

        if direction == "downstream":
            self._ripple_downstream(shot_id, old_end, delta, shifted_audio)
        elif direction == "upstream":
            self._ripple_upstream(shot_id, old_start, delta, shifted_audio)

        if _enforce:
            self._enforce_gap_holds()

    def _ripple_downstream(
        self,
        shot_id: int,
        after_frame: float,
        delta: float,
        shifted_audio: Optional[set] = None,
    ):
        """Shift all shots starting at or after *after_frame* by *delta*.

        Routes through :mod:`_shot_plan` and :mod:`_shot_apply` so
        the whole downstream topology is resolved before any keyframe
        is touched — preventing envelope collisions between moved and
        not-yet-moved shots.  The ``shifted_audio`` argument is retained
        for signature compatibility and is unused (audio dedup is
        intrinsic to the audio batch primitive).
        """
        from mayatk.anim_utils.shots._shot_plan import (
            plan_ripple_downstream,
        )
        from mayatk.anim_utils.shots._shot_apply import apply

        plan = plan_ripple_downstream(self.store, shot_id, after_frame, delta)
        apply(self.store, plan)

    def _ripple_upstream(
        self,
        shot_id: int,
        before_frame: float,
        delta: float,
        shifted_audio: Optional[set] = None,
    ):
        """Shift all shots ending at or before *before_frame* by *delta*.

        Routes through the plan/executor pair — see
        :meth:`_ripple_downstream` for the rationale.
        """
        from mayatk.anim_utils.shots._shot_plan import (
            plan_ripple_upstream,
        )
        from mayatk.anim_utils.shots._shot_apply import apply

        plan = plan_ripple_upstream(self.store, shot_id, before_frame, delta)
        apply(self.store, plan)

    def _enforce_gap_holds(self):
        """Set stepped out-tangents on the last key before every inter-shot gap.

        Iterates sorted shot pairs and, for each gap (where the next
        shot starts after the previous shot ends), finds the last
        keyframe on each object's curves in the pre-gap shot and sets
        its out-tangent to ``step`` — producing a flat hold through
        the gap.  Already-stepped keys are skipped.

        This is called automatically after every timeline-modifying
        operation so that gaps never contain interpolated motion.
        """
        if pm is None:
            return

        import maya.cmds as cmds
        from mayatk.anim_utils._anim_utils import AnimUtils

        sorted_s = self.sorted_shots()
        if len(sorted_s) < 2:
            return

        for i in range(len(sorted_s) - 1):
            pre = sorted_s[i]
            nxt = sorted_s[i + 1]
            gap = nxt.start - pre.end
            if gap < 1e-6:
                continue  # no gap — shots are contiguous or overlapping

            if not pre.objects:
                continue

            # Batch-resolve all curves for this shot's objects at once
            curves = AnimUtils.objects_to_curves(pre.objects, as_strings=True)
            if not curves:
                continue

            step_dict = {}
            eps = 1e-3
            for crv in curves:
                times = cmds.keyframe(
                    crv,
                    q=True,
                    time=(pre.start - eps, pre.end + eps),
                    timeChange=True,
                )
                if not times:
                    continue
                last_t = max(times)
                ott = cmds.keyTangent(
                    crv,
                    q=True,
                    time=(last_t, last_t),
                    outTangentType=True,
                )
                if ott and ott[0] == "step":
                    continue
                step_dict.setdefault(crv, []).append(last_t)

            if step_dict:
                AnimUtils.step_keys(keys=step_dict, tangent="out")

    def expand_shot(
        self,
        shot_id: int,
        new_end: float,
    ) -> float:
        """Expand a shot's end frame and ripple downstream shots.

        Only expands — if *new_end* is not greater than the current end,
        no change is made.

        Parameters:
            shot_id: ID of the shot to expand.
            new_end: Desired new end frame.

        Returns:
            The delta by which the shot was expanded (0 if unchanged).
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        if new_end <= shot.end:
            return 0.0
        delta = new_end - shot.end
        old_end = shot.end
        shot.end = self.store.snap(new_end)
        self._ripple_downstream(shot_id, old_end, delta)
        self._enforce_gap_holds()
        return delta

    def resize_object(
        self,
        shot_id: int,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Scale one object's keys and ripple-shift all downstream shots.

        Only the named *obj* is scaled.  Other objects in the same shot
        are untouched.  Downstream shots are shifted by the end-frame
        delta so the gap is preserved.

        Parameters:
            shot_id: Shot the object belongs to.
            obj: Transform node name to resize.
            old_start: Original first frame of the object segment.
            old_end: Original last frame of the object segment.
            new_start: Desired first frame after the resize.
            new_end: Desired last frame after the resize.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)

        # Scale only this object's keys
        self.scale_object_keys(obj, old_start, old_end, new_start, new_end)

        # The shot envelope may need updating
        prior_end = shot.end
        shot.start = min(shot.start, new_start)
        shot.end = max(shot.end, new_end)

        # Ripple downstream shots by the change at the tail
        delta = shot.end - prior_end
        if abs(delta) > 1e-6:
            self._ripple_downstream(shot_id, prior_end, delta)
        self._enforce_gap_holds()

    def set_shot_duration(self, shot_id: int, new_duration: float) -> None:
        """Change a shot's duration and ripple-shift all downstream shots.

        The shot's *start* stays fixed; its *end* moves, and every
        downstream shot shifts by the same delta.

        Parameters:
            shot_id: ID of the shot to resize.
            new_duration: Desired duration in frames.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        delta = new_duration - shot.duration
        if abs(delta) < 1e-6:
            return

        old_end = shot.end
        new_end = self.store.snap(shot.start + new_duration)

        # Scale keyframes within this shot
        for obj in shot.objects:
            self.scale_object_keys(obj, shot.start, old_end, shot.start, new_end)
        shot.end = new_end

        # Shift downstream shots
        self._ripple_downstream(shot_id, old_end, delta)
        self._enforce_gap_holds()

    def resize_shot(
        self,
        shot_id: int,
        new_start: float,
        new_end: float,
        _enforce: bool = True,
    ) -> None:
        """Resize a shot to [new_start, new_end], scaling all keys and rippling.

        Both edges may move.  Keyframes are scaled from the old range
        into the new one.  Downstream shots are shifted by any change
        in the tail, and upstream shots are shifted by any change in
        the head.

        Parameters:
            shot_id: ID of the shot to resize.
            new_start: Desired start frame.
            new_end: Desired end frame.
            _enforce: If True (default), call :meth:`_enforce_gap_holds`
                after the operation.  Pass False when batching multiple
                edits and calling it once at the end.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        old_start, old_end = shot.start, shot.end
        if abs(new_start - old_start) < 1e-6 and abs(new_end - old_end) < 1e-6:
            return

        shifted_audio: set = set()

        # Scale keyframes within this shot
        for obj in shot.objects:
            self.scale_object_keys(obj, old_start, old_end, new_start, new_end)
        shot.start = new_start
        shot.end = new_end

        # Shift downstream shots by however much the tail moved
        tail_delta = new_end - old_end
        if abs(tail_delta) > 1e-6:
            self._ripple_downstream(shot_id, old_end, tail_delta, shifted_audio)

        # Shift upstream shots by however much the head moved
        head_delta = new_start - old_start
        if abs(head_delta) > 1e-6:
            self._ripple_upstream(shot_id, old_start, head_delta, shifted_audio)

        if _enforce:
            self._enforce_gap_holds()

    def set_shot_start(
        self, shot_id: int, new_start: float, ripple: bool = True
    ) -> None:
        """Move a shot to a new start time.

        Parameters:
            shot_id: ID of the shot to move.
            new_start: New start frame.
            ripple: If True, downstream shots shift by the same delta.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        delta = new_start - shot.start
        if abs(delta) < 1e-6:
            return

        old_end = shot.end

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch():
            # Move this shot's content (animation + audio).
            self._move_shot_content(shot, new_start)

            if ripple:
                self._ripple_downstream(shot_id, old_end, delta)
        self._enforce_gap_holds()

    def reorder_shots(self, shot_id_a: int, shot_id_b: int) -> None:
        """Swap two shots' timeline positions non-destructively.

        Each shot's keyframes are moved so that they occupy the other
        shot's original slot.  The gap between shots is preserved.
        If the two shots have different durations, all shots downstream
        of the swap region are rippled by the net delta so the timeline
        stays contiguous.

        Parameters:
            shot_id_a: First shot to swap.
            shot_id_b: Second shot to swap.

        Raises:
            ValueError: If either ID does not exist or both are the same.
        """
        if shot_id_a == shot_id_b:
            raise ValueError("Cannot reorder a shot with itself")

        a = self.shot_by_id(shot_id_a)
        b = self.shot_by_id(shot_id_b)
        if a is None:
            raise ValueError(f"No shot with id {shot_id_a}")
        if b is None:
            raise ValueError(f"No shot with id {shot_id_b}")

        # Normalise so 'first' starts earlier on the timeline
        if a.start > b.start:
            a, b = b, a

        first_start, first_end = a.start, a.end
        second_start, second_end = b.start, b.end
        gap = second_start - first_end  # gap between scenes
        first_dur = first_end - first_start
        second_dur = second_end - second_start

        # New positions: second shot goes to old first_start,
        # first shot goes right after it, preserving the original gap.
        new_second_start = self.store.snap(first_start)
        new_first_start = self.store.snap(first_start + second_dur + gap)

        # Use a large temporary offset so keys don't collide during the swap
        _PARK = 500000.0

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch():
            # Move keyframes (only when Maya is available)
            if pm is not None:
                # 1) Park first shot's keys at temp offset
                for obj in a.objects:
                    self.move_object_keys(obj, first_start, first_end, _PARK)
                self._shift_audio(None, first_start, first_end, _PARK - first_start)

                # 2) Move second shot to the first shot's original position
                for obj in b.objects:
                    self.move_object_keys(
                        obj, second_start, second_end, new_second_start
                    )
                self._shift_audio(
                    None,
                    second_start,
                    second_end,
                    new_second_start - second_start,
                )

                # 3) Move first shot from park to its new position
                parked_end = _PARK + first_dur
                for obj in a.objects:
                    self.move_object_keys(obj, _PARK, parked_end, new_first_start)
                self._shift_audio(None, _PARK, parked_end, new_first_start - _PARK)

            # 4) Update ShotBlock ranges
            a.start = new_first_start
            a.end = self.store.snap(new_first_start + first_dur)
            b.start = new_second_start
            b.end = self.store.snap(new_second_start + second_dur)

            # 5) Ripple downstream if durations differ
            new_region_end = a.end
            delta = new_region_end - second_end
            if abs(delta) > 1e-6:
                for s in self.sorted_shots():
                    if s.shot_id in (a.shot_id, b.shot_id):
                        continue
                    if s.start >= second_end:
                        if pm is not None:
                            for obj in s.objects:
                                self.move_object_keys(
                                    obj, s.start, s.end, s.start + delta
                                )
                            self._shift_audio(None, s.start, s.end, delta)
                        s.start = self.store.snap(s.start + delta)
                        s.end = self.store.snap(s.end + delta)
        self._enforce_gap_holds()

    def move_shot_to_position(self, shot_id: int, target_pos: int) -> None:
        """Move a shot to a new 1-based position in the timeline order.

        Other shots shift to accommodate.  Keyframes move with their
        shots.  Durations are preserved; gaps use the store's current
        gap setting (locked gaps are honoured).

        Parameters:
            shot_id:    The shot to relocate.
            target_pos: Desired 1-based position (clamped to valid range).

        Raises:
            ValueError: If *shot_id* does not exist.
        """
        shots = self.sorted_shots()
        n = len(shots)
        if n < 2:
            return

        current_idx = next(
            (i for i, s in enumerate(shots) if s.shot_id == shot_id), None
        )
        if current_idx is None:
            raise ValueError(f"No shot with id {shot_id}")

        target_idx = max(0, min(target_pos - 1, n - 1))
        if current_idx == target_idx:
            return

        # Build new ordering
        new_order = list(shots)
        moving = new_order.pop(current_idx)
        new_order.insert(target_idx, moving)

        # Capture locked gap widths from the *old* ordering before we move
        # anything.  After reorder the pair identities change, so we
        # preserve any locked width that still applies between adjacent
        # shots in the new order.
        locked_widths: dict = {}
        for i in range(len(new_order) - 1):
            left, right = new_order[i], new_order[i + 1]
            if self.store.is_gap_locked(left.shot_id, right.shot_id):
                # Use the gap as it was in the old timeline
                locked_widths[i] = max(0, right.start - left.end)

        # Compute new positions preserving each shot's duration
        gap = self.store.gap
        start_frame = shots[0].start
        cursor = start_frame
        new_positions = {}
        for i, s in enumerate(new_order):
            dur = s.duration
            ns = self.store.snap(cursor)
            ne = self.store.snap(cursor + dur)
            new_positions[s.shot_id] = (ns, ne)
            effective_gap = locked_widths.get(i, gap)
            cursor += dur + effective_gap

        # Move keyframes via park technique to avoid collisions
        _PARK_BASE = 500000.0

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch():
            if pm is not None:
                park_offset = _PARK_BASE
                parked = {}
                for s in new_order:
                    old_start, old_end = s.start, s.end
                    new_start = new_positions[s.shot_id][0]
                    if abs(old_start - new_start) > 1e-6:
                        for obj in s.objects:
                            self.move_object_keys(obj, old_start, old_end, park_offset)
                        self._shift_audio(
                            None, old_start, old_end, park_offset - old_start
                        )
                        parked[s.shot_id] = (park_offset, park_offset + s.duration)
                        park_offset += s.duration + 1000

                for sid, (park_s, park_e) in parked.items():
                    shot = self.shot_by_id(sid)
                    new_start = new_positions[sid][0]
                    for obj in shot.objects:
                        self.move_object_keys(obj, park_s, park_e, new_start)
                    self._shift_audio(None, park_s, park_e, new_start - park_s)

            # Update ShotBlock ranges
            for s in new_order:
                s.start, s.end = new_positions[s.shot_id]

        self._enforce_gap_holds()

    # ---- timing redistribution -------------------------------------------

    def respace(self, gap: float = 0, start_frame: float = 1) -> None:
        """Redistribute all shots sequentially with uniform gaps.

        Each shot keeps its current duration but is repositioned so the
        first shot starts at *start_frame* and subsequent shots follow
        with *gap* frames between them.  Locked gaps preserve their
        current width instead of using the uniform *gap* value.
        Keyframes are moved with their shots when Maya is available.

        Delegates to :func:`_shot_plan.plan_respace` and
        :func:`_shot_apply.apply` so the full topology is resolved
        in memory before any Maya write, eliminating envelope
        collisions between moved and not-yet-moved shots.

        Parameters:
            gap: Frames of gap between consecutive shots.
            start_frame: Timeline frame for the first shot.
        """
        from mayatk.anim_utils.shots._shot_plan import plan_respace
        from mayatk.anim_utils.shots._shot_apply import apply

        plan = plan_respace(self.store, gap, start_frame)
        apply(self.store, plan)
        self._enforce_gap_holds()

    # ---- serialisation ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise shots and settings to a plain dict."""
        return self.store.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ShotSequencer":
        """Restore from serialised data."""
        return cls(store=ShotStore.from_dict(data))
