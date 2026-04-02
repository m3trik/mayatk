# coding=utf-8
"""Shot Sequencer — manages per-shot animation with ripple editing.

Shots are contiguous keyframe ranges ("blocks") along the timeline.
Changing one shot's duration or position ripples downstream shots.
"""
from typing import List, Dict, Optional, Any

try:
    import pymel.core as pm
except ImportError as error:
    pm = None
    print(__file__, error)


from mayatk.anim_utils.shots._shots import ShotBlock, ShotStore, detect_shot_regions


# ---------------------------------------------------------------------------
# Tangent types whose weights and angles are meaningless (i.e. fully
# determined by the tangent type alone).  All other tangent types carry
# weight/angle data that must be explicitly restored after setKeyframe.
# ---------------------------------------------------------------------------
_SIMPLE_TANGENT_TYPES = frozenset({"step", "stepnext", "flat", "linear"})


def _retime_curve_keys(cmds, crv, tr, time_fn):
    """Snapshot keys on *crv* inside time-range *tr*, delete them, and
    recreate each at ``time_fn(old_time)`` with full tangent preservation.

    This replaces ``cmds.keyframe(edit, timeChange)`` and ``cmds.scaleKey``
    which silently misplace stepped/fixed tangent keys and round stepped
    keys to integer frames respectively.

    Parameters:
        cmds: ``maya.cmds`` module reference.
        crv: Animation curve node name.
        tr: ``(start, end)`` time-range tuple.
        time_fn: Callable ``(float) -> float`` mapping old time to new.
    """
    times = cmds.keyframe(crv, q=True, time=tr, timeChange=True) or []
    if not times:
        return
    vals = cmds.keyframe(crv, q=True, time=tr, valueChange=True) or []
    in_tans = cmds.keyTangent(crv, q=True, time=tr, inTangentType=True) or []
    out_tans = cmds.keyTangent(crv, q=True, time=tr, outTangentType=True) or []
    in_weights = cmds.keyTangent(crv, q=True, time=tr, inWeight=True) or []
    out_weights = cmds.keyTangent(crv, q=True, time=tr, outWeight=True) or []
    in_angles = cmds.keyTangent(crv, q=True, time=tr, inAngle=True) or []
    out_angles = cmds.keyTangent(crv, q=True, time=tr, outAngle=True) or []

    keys = list(
        zip(times, vals, in_tans, out_tans, in_weights, out_weights, in_angles, out_angles)
    )

    # Resolve the driven plug BEFORE cutKey, because cutKey may
    # auto-delete the curve node if it removes the last key.
    conns = cmds.listConnections(crv, plugs=True, d=True, s=False) or []
    cmds.cutKey(crv, time=tr, clear=True)

    target = crv if cmds.objExists(crv) else (conns[0] if conns else None)
    if not target:
        return

    for t, v, itt, ott, iw, ow, ia, oa in keys:
        new_t = time_fn(t)
        cmds.setKeyframe(target, time=new_t, value=v)
        tt = (new_t, new_t)
        cmds.keyTangent(target, time=tt, inTangentType=itt, outTangentType=ott)
        # Restore weights and angles for tangent types that carry them
        kw = {}
        if itt not in _SIMPLE_TANGENT_TYPES:
            kw["inWeight"] = iw
            kw["inAngle"] = ia
        if ott not in _SIMPLE_TANGENT_TYPES:
            kw["outWeight"] = ow
            kw["outAngle"] = oa
        if kw:
            cmds.keyTangent(target, time=tt, **kw)


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

    def collect_object_segments(
        self,
        shot_id: int,
        ignore: Optional[str] = None,
        motion_rate: float = 1e-3,
    ) -> List[Dict[str, Any]]:
        """Collect per-object animation segments within a shot's range.

        Each returned dict has ``"obj"`` (str), ``"start"``, ``"end"``,
        and ``"duration"`` keys — suitable for populating per-object
        tracks in the sequencer widget.

        Flat/constant-value intervals are always excluded so only
        actual motion is shown.

        Parameters:
            shot_id: The shot whose objects and range to query.
            ignore: Attribute pattern(s) to exclude.
            motion_rate: Per-frame rate-of-change threshold.

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
                nodes = self._shot_nodes(shot)
            if not nodes:
                return []

        from mayatk.anim_utils.segment_keys import SegmentKeys

        segments = SegmentKeys.collect_segments(
            nodes,
            split_static=True,
            ignore=ignore,
            time_range=(shot.start, shot.end),
            ignore_holds=True,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=motion_rate,
        )
        # Normalise obj to str — defensive; values are already strings
        # post-cmds migration, but callers historically passed PyNodes.
        for seg in segments:
            seg["obj"] = str(seg["obj"])
        return segments

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
            # Always use cutKey + setKeyframe to avoid Maya's
            # cmds.keyframe(edit, timeChange) bug which silently
            # misplaces keys with stepped, fixed, and other tangent
            # types — especially at large offsets.
            self._move_curve_keys(cmds, crv, tr, delta)

    @staticmethod
    def _move_curve_keys(cmds, crv, tr, delta):
        """Move all keys on *crv* in *tr* by *delta*.

        Delegates to :func:`_retime_curve_keys` with a simple offset
        function.  Avoids Maya's ``cmds.keyframe(edit, timeChange)`` bug.
        """
        _retime_curve_keys(cmds, crv, tr, lambda t: t + delta)

    @staticmethod
    def _scale_curve_keys(cmds, crv, old_start, old_end, new_start, new_end):
        """Scale keys on *crv* from [old_start, old_end] → [new_start, new_end].

        Delegates to :func:`_retime_curve_keys` with a linear mapping
        function.  Avoids ``cmds.scaleKey`` rounding stepped keys to
        integers.
        """
        old_dur = old_end - old_start
        new_dur = new_end - new_start
        _retime_curve_keys(
            cmds,
            crv,
            (old_start, old_end),
            lambda t: new_start + (t - old_start) / old_dur * new_dur,
        )

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

        Delegates to :func:`_retime_curve_keys` for tangent-safe key
        movement.
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
            curves = cmds.listConnections(plug, type="animCurve", s=True, d=False) or []
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
                    curves.append(crv)

        delta = new_time - old_time
        for crv in curves:
            _retime_curve_keys(cmds, crv, tr, lambda t: t + delta)

    @staticmethod
    def _batch_move_keys(cmds, objects, old_start, old_end, new_start):
        """Move keyframes for all *objects* from [old_start, old_end] to new_start.

        Resolves curves in a single batch rather than per-object, then
        retimes keys via cutKey + setKeyframe (see :func:`_retime_curve_keys`).
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
            ShotSequencer._move_curve_keys(cmds, crv, tr, delta)

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

        dur = old_end - old_start
        new_end = new_start + dur

        # Move the object's keys
        self.move_object_keys(obj, old_start, old_end, new_start)

        # Optionally push overlapping objects within the same shot
        if prevent_overlap:
            self._push_overlapping_objects(shot, obj, new_start, new_end)

        # Check if the clip now exceeds the shot boundaries
        prior_end = shot.end
        expanded = False

        if new_start < shot.start:
            shot.start = new_start
            expanded = True

        if new_end > shot.end:
            shot.end = new_end
            expanded = True

        # Ripple downstream shots by however much the shot tail grew
        if expanded:
            delta = shot.end - prior_end
            if abs(delta) > 1e-6:
                self._ripple_downstream(shot_id, prior_end, delta)
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

        # Resolve to full DAG path and iterate per-curve so scaleKey
        # works on non-transform nodes (locators, shapes, etc.).
        # Use cutKey + setKeyframe instead of cmds.scaleKey because
        # scaleKey rounds stepped keys to integer frames.
        matches = cmds.ls(obj, long=True)
        if not matches:
            return
        obj_path = matches[0]
        curves = cmds.listConnections(obj_path, type="animCurve", s=True, d=False) or []
        curves = list(set(curves))
        for crv in curves:
            self._scale_curve_keys(cmds, crv, old_start, old_end, new_start, new_end)

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

        duration = old_end - old_start

        # Shift all keys in this shot
        for obj in shot.objects:
            self.move_object_keys(obj, old_start, old_end, new_start)

        shot.start = new_start
        shot.end = new_start + duration

        # Ripple every downstream shot by the same delta
        for s in self.sorted_shots():
            if s.shot_id == shot_id:
                continue
            if s.start >= old_end - 1e-6:
                for obj in s.objects:
                    self.move_object_keys(obj, s.start, s.end, s.start + delta)
                s.start += delta
                s.end += delta

        self._enforce_gap_holds()

    def _ripple_downstream(self, shot_id: int, after_frame: float, delta: float):
        """Shift all shots starting at or after *after_frame* by *delta*."""
        for s in self.sorted_shots():
            if s.shot_id == shot_id:
                continue
            if s.start >= after_frame:
                for obj in s.objects:
                    self.move_object_keys(obj, s.start, s.end, s.start + delta)
                s.start += delta
                s.end += delta

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
        shot.end = new_end
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
        new_end = shot.start + new_duration

        # Scale keyframes within this shot
        for obj in shot.objects:
            self.scale_object_keys(obj, shot.start, old_end, shot.start, new_end)
        shot.end = new_end

        # Shift downstream shots
        self._ripple_downstream(shot_id, old_end, delta)
        self._enforce_gap_holds()

    def resize_shot(self, shot_id: int, new_start: float, new_end: float) -> None:
        """Resize a shot to [new_start, new_end], scaling all keys and rippling.

        Both edges may move.  Keyframes are scaled from the old range
        into the new one, and downstream shots are shifted by the
        change in the shot's end frame.

        Parameters:
            shot_id: ID of the shot to resize.
            new_start: Desired start frame.
            new_end: Desired end frame.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        old_start, old_end = shot.start, shot.end
        if abs(new_start - old_start) < 1e-6 and abs(new_end - old_end) < 1e-6:
            return

        # Scale keyframes within this shot
        for obj in shot.objects:
            self.scale_object_keys(obj, old_start, old_end, new_start, new_end)
        shot.start = new_start
        shot.end = new_end

        # Shift downstream shots by however much the tail moved
        delta = new_end - old_end
        if abs(delta) > 1e-6:
            self._ripple_downstream(shot_id, old_end, delta)
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

        # Move this shot's keys
        for obj in shot.objects:
            self.move_object_keys(obj, shot.start, shot.end, new_start)
        shot.start += delta
        shot.end += delta

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
        new_second_start = first_start
        new_first_start = first_start + second_dur + gap

        # Use a large temporary offset so keys don't collide during the swap
        _PARK = 500000.0

        # Move keyframes (only when Maya is available)
        if pm is not None:
            # 1) Park first shot's keys at temp offset
            for obj in a.objects:
                self.move_object_keys(obj, first_start, first_end, _PARK)

            # 2) Move second shot to the first shot's original position
            for obj in b.objects:
                self.move_object_keys(obj, second_start, second_end, new_second_start)

            # 3) Move first shot from park to its new position
            parked_end = _PARK + first_dur
            for obj in a.objects:
                self.move_object_keys(obj, _PARK, parked_end, new_first_start)

        # 4) Update ShotBlock ranges
        a.start = new_first_start
        a.end = new_first_start + first_dur
        b.start = new_second_start
        b.end = new_second_start + second_dur

        # 5) Ripple downstream if durations differ
        # The swap region now ends at (new_first_start + first_dur)
        # versus the old end at second_end.  The delta is the difference.
        new_region_end = a.end
        delta = new_region_end - second_end
        if abs(delta) > 1e-6:
            for s in self.sorted_shots():
                if s.shot_id in (a.shot_id, b.shot_id):
                    continue
                if s.start >= second_end:
                    if pm is not None:
                        for obj in s.objects:
                            self.move_object_keys(obj, s.start, s.end, s.start + delta)
                    s.start += delta
                    s.end += delta
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
            new_positions[s.shot_id] = (cursor, cursor + dur)
            effective_gap = locked_widths.get(i, gap)
            cursor += dur + effective_gap

        # Move keyframes via park technique to avoid collisions
        _PARK_BASE = 500000.0
        if pm is not None:
            park_offset = _PARK_BASE
            parked = {}
            for s in new_order:
                old_start, old_end = s.start, s.end
                new_start = new_positions[s.shot_id][0]
                if abs(old_start - new_start) > 1e-6:
                    for obj in s.objects:
                        self.move_object_keys(obj, old_start, old_end, park_offset)
                    parked[s.shot_id] = (park_offset, park_offset + s.duration)
                    park_offset += s.duration + 1000

            for sid, (park_s, park_e) in parked.items():
                shot = self.shot_by_id(sid)
                new_start = new_positions[sid][0]
                for obj in shot.objects:
                    self.move_object_keys(obj, park_s, park_e, new_start)

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

        Parameters:
            gap: Frames of gap between consecutive shots.
            start_frame: Timeline frame for the first shot.
        """
        shots = self.sorted_shots()
        if not shots:
            return

        # Capture locked gap widths before repositioning.
        locked_widths: dict = {}
        for i in range(len(shots) - 1):
            left, right = shots[i], shots[i + 1]
            if self.store.is_gap_locked(left.shot_id, right.shot_id):
                locked_widths[i] = max(0, right.start - left.end)

        cursor = start_frame
        for i, shot in enumerate(shots):
            duration = shot.end - shot.start
            new_start = cursor
            if abs(new_start - shot.start) > 1e-6:
                if pm is not None:
                    import maya.cmds as cmds

                    self._batch_move_keys(
                        cmds, shot.objects, shot.start, shot.end, new_start
                    )
                shot.start = new_start
                shot.end = new_start + duration
            effective_gap = locked_widths.get(i, gap)
            cursor = shot.end + effective_gap

        self._enforce_gap_holds()

    # ---- serialisation ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise shots and settings to a plain dict."""
        return self.store.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ShotSequencer":
        """Restore from serialised data."""
        return cls(store=ShotStore.from_dict(data))
