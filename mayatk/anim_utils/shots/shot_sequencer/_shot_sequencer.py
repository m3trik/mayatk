# coding=utf-8
"""Shot Sequencer — manages per-shot animation with ripple editing.

Shots are contiguous keyframe ranges ("blocks") along the timeline.
Changing one shot's duration or position ripples downstream shots.
"""

import bisect
import logging
from typing import List, Dict, Optional, Any

try:
    import maya.cmds as cmds
except ImportError:
    # Maya-soft: planner/tests import this module headless.
    cmds = None
    logging.getLogger(__name__).debug("maya.cmds unavailable — headless mode")


from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils.shots._shots import Detection, ShotBlock, ShotStore
from mayatk.anim_utils.shots._shot_plan import _INF as _PLAN_INF

# Half-width both key movers pad their envelope by, and therefore the
# tolerance membership adoption must use: adopting on a tighter window than
# the writer moves would list an object whose key the writer then leaves
# behind.  One constant so the two cannot drift.
_BATCH_MOVE_EPS = 1e-3

# Two poses count as the same pose below this.  Used only to decide whether
# samples converging on one frame can merge losslessly or must be refused.
_POSE_TOL = 1e-4

# Out-tangent types that hold their key's value across the whole segment that
# follows, so the segment's shape does not depend on any angle.
_STEP_TANGENTS = ("step", "stepnext")

# Tangent types Maya derives from the keys on BOTH sides of their own, so
# removing a neighbouring key silently re-computes them -- in AND out alike,
# since these are a single unbroken slope.  Everything else (fixed, linear,
# flat, step) either is authored outright or looks only at the key on its own
# side, and so cannot be disturbed by a cut on the far side.
_NEIGHBOUR_DERIVED_TANGENTS = (
    "spline",
    "auto",
    "clamped",
    "plateau",
    "autoease",
    "automix",
    "autocustom",
)

# A tangent this close to horizontal is flat.  Degrees: Maya reports tangent
# angles in degrees, and an angle below this cannot bow a segment between two
# keys of equal value by anything an animator could see.
_FLAT_ANGLE_TOL = 1e-4

# ...and a tangent that MOVED by less than this did not really move.  A
# separate, looser constant on purpose: holding a boundary tangent converts
# it to ``fixed``, so testing "did it change" at the flatness tolerance would
# spend that conversion on float noise from Maya's own re-derivation.
_TANGENT_MOVED_TOL = 0.01


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
        start: float,
        end: float,
        value_tolerance: float = 1e-4,
        require_motion: bool = False,
    ) -> List[str]:
        """Return names of all transforms animated in [start, end].

        Only standard transform/visibility attributes are considered —
        custom user attributes (e.g. ``audio_trigger``) are ignored so
        marker objects don't appear as scene content.

        ``require_motion=True`` additionally drops objects whose curves are
        entirely constant (all values within *value_tolerance*) across the
        range.  That test belongs to shot *boundary detection*, where a
        held pose carries no cut information — it is the wrong test for
        shot *membership*: an object keyed on a hold for the whole shot is
        still that shot's content, and excluding it left it invisible in
        the panel and, worse, stranded when the shot moved (ripples shift
        ``shot.objects``, so anything missing from that list is left
        behind).  Membership therefore defaults to "has keys in range".
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.shots._shots import Detection

        transform_curves = Detection._map_standard_curves_to_transforms()
        if not transform_curves:
            return []

        result = []
        for xform, crvs in sorted(transform_curves.items()):
            for crv in crvs:
                vals = cmds.keyframe(crv, q=True, time=(start, end), valueChange=True)
                if not vals:
                    continue
                if require_motion and (max(vals) - min(vals)) <= value_tolerance:
                    continue
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

    @staticmethod
    def _disambiguate_matches(matches: list) -> str:
        """Pick a single node from several same-named DAG matches.

        Shared disambiguation policy: prefer the match carrying anim
        curves — that's the node a shot actually animates — else fall
        back to the first.  *matches* must be non-empty.
        """
        import maya.cmds as cmds

        for m in matches:
            if cmds.listConnections(m, type="animCurve", s=True, d=False):
                return m
        return matches[0]

    @staticmethod
    def _shot_nodes(shot: ShotBlock) -> list:
        """Return validated, unambiguous names for a shot's objects.

        Non-existent names are dropped.  A name that resolves to a single
        scene node is returned in its stored form (callers historically
        pass short names; downstream consumers like SegmentKeys roundtrip
        those names back into segment dicts).

        A stored short name can turn **non-unique** when the scene later
        gains a second node with the same leaf name (duplicate, import,
        namespace merge).  ``cmds.objExists`` still reports such a name as
        valid, but handing it to a query that demands a unique node
        (``cmds.listConnections`` et al. inside ``SegmentKeys``) raises
        ``More than one object matches name``.  Resolve those to one long
        DAG path via :meth:`_disambiguate_matches` so no ambiguous name
        ever reaches the segment collector.
        """
        import maya.cmds as cmds

        if not shot.objects:
            return []
        nodes: list = []
        for n in shot.objects:
            matches = cmds.ls(n, long=True) or []
            if not matches:
                continue  # non-existent → drop
            if len(matches) == 1:
                nodes.append(n)  # unique → preserve stored form
            else:
                nodes.append(ShotSequencer._disambiguate_matches(matches))
        return nodes

    @staticmethod
    def _renamed_target(leaf: str, memo: Optional[dict] = None):
        """Curve-name rename lookup, memoised across one reconcile pass.

        Every miss otherwise costs a full ``cmds.ls(type="animCurve")`` DG
        scan — and since reconciliation now KEEPS what it cannot resolve,
        those misses recur on every refresh, once per member.  The ``None``
        key caches the scan itself (never a valid leaf name).
        """
        from mayatk.anim_utils.shots._shots import Detection

        if memo is None:
            return Detection.transform_from_curve_names(leaf)
        if leaf not in memo:
            curves = memo.get(None)
            if curves is None:
                import maya.cmds as cmds

                curves = memo[None] = cmds.ls(type="animCurve") or []
            memo[leaf] = Detection.transform_from_curve_names(leaf, curves=curves)
        return memo[leaf]

    @staticmethod
    def _reconcile_stale_paths(shot: ShotBlock, rename_memo=None) -> bool:
        """Re-resolve stale long DAG paths, NEVER dropping membership.

        When a parent node is renamed the long paths of all children change,
        making the stored entries stale.  This helper extracts the short
        (leaf) name from each stale entry and substitutes the updated long
        path; when the leaf name is gone too — the object itself was renamed
        — it falls back to :meth:`Detection.transform_from_curve_names`,
        which follows the anim curves Maya named after the old node.

        An entry that resolves to nothing is **kept as stored**.  "Renamed"
        and "deleted" are indistinguishable from a name alone, this runs
        unattended on every refresh, and the costs are wildly asymmetric: an
        unresolvable name is inert (every consumer filters through
        :meth:`_shot_nodes` / ``cmds.ls``, and the ripple primitives return
        early on it), while dropping it destroys the shot's record of its own
        content — irreversibly, on the next store flush.  Pruning belongs to
        an explicit, user-driven action, not a read-only rebuild.  (blendertk
        already holds this line: its ``reconcile_all_shots`` is a no-op that
        surfaces deletions through ``assess`` instead of rewriting.)

        Returns ``True`` if any paths were updated.
        """
        import maya.cmds as cmds

        updated = False
        new_objects: list = []
        for obj in shot.objects:
            if cmds.objExists(obj):
                new_objects.append(obj)
                continue
            leaf = CoreUtils.leaf_name(obj)
            matches = cmds.ls(leaf, long=True, type="transform") or []
            if len(matches) == 1:
                new_objects.append(matches[0])
                updated = True
            elif matches:
                new_objects.append(ShotSequencer._disambiguate_matches(matches))
                updated = True
            else:
                renamed = ShotSequencer._renamed_target(leaf, rename_memo)
                new_objects.append(renamed or obj)
                updated = updated or bool(renamed)
        result = sorted(set(new_objects))
        if result != sorted(shot.objects):
            shot.objects = result
            updated = True
        return updated

    def reconcile_all_shots(self) -> bool:
        """Re-resolve stale DAG paths across every shot and persist changes.

        Should be called once per refresh cycle *before* segment collection
        so that all stored paths are current.  Re-pointing only: membership
        is never dropped here (see :meth:`_reconcile_stale_paths`).

        Returns ``True`` if any shot was modified.
        """
        changed = False
        rename_memo: dict = {}  # shared across the pass — see _renamed_target
        with self.store.batch_update():
            for shot in self.store.shots:
                nodes = self._shot_nodes(shot)
                if shot.objects and len(nodes) < len(set(shot.objects)):
                    if self._reconcile_stale_paths(shot, rename_memo):
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
        # post-cmds migration, but callers historically passed nodes.
        for seg in segments:
            seg["obj"] = str(seg["obj"])

        # Sequencer GUI invariant: every keyed object on the shot deserves
        # a track marker even when its keys are static-value only or its
        # motion sits below ``motion_rate``.  ``SegmentKeys.collect_segments``
        # filters those out under ignore_holds=True; backfill a single
        # span-of-keys segment for any node that has keys in range but
        # produced no segment.
        if ignore_holds and nodes:
            covered = {s["obj"] for s in segments}
            for n in nodes:
                if n in covered:
                    continue
                kt = cmds.keyframe(n, q=True, time=(shot.start, shot.end)) or []
                if not kt:
                    continue
                segments.append(
                    {
                        "obj": n,
                        "curves": [],
                        "keyframes": sorted(set(kt)),
                        "start": min(kt),
                        "end": max(kt),
                        "duration": max(kt) - min(kt),
                        "segment_range": (min(kt), max(kt)),
                    }
                )
        return segments

    # ---- unified sequence model (anim + audio) ---------------------------

    def _collect_audio_sequences(
        self, start: float, end: float
    ) -> List[Dict[str, Any]]:
        """Return audio events overlapping ``[start, end]`` as sequence dicts.

        Each dict carries ``{"kind": "audio", "obj": <track_id>, "start", "end"}``.
        Tracks with no defined stop frame fall back to ``end`` so a finite
        range can be reported.  Every call reads fresh so external audio
        edits are never masked.
        """
        if cmds is None:
            return []
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
            # One shared pairing state machine (AudioUtils) — only the
            # batched per-track key *queries* stay local for perf.
            events = AudioUtils.pair_on_off_events(pairs)
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
        if cmds is None:
            return
        anim_objs = {seg["obj"] for seg in self.collect_object_segments(shot_id)}
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

    def sequence_separation(self) -> float:
        """Room to leave between a moved sequence and what it lands after.

        Strictly MORE than the inter-shot gap, and never less than a frame.
        Both halves matter for how the result reads: at zero the arriving clip
        butts against the existing one and the two draw as a single merged
        run, which makes a non-destructive move look like it overwrote
        something; at exactly the shot gap the seam inside a shot is
        indistinguishable from a seam BETWEEN shots.  One frame past the gap
        is the tightest spacing that is unambiguously neither.
        """
        return self.store.snap(max(float(self.store.gap) + 1.0, 1.0))

    def move_sequences_to_shot(
        self,
        sequences: List[Dict[str, Any]],
        dest_shot_id: int,
    ) -> None:
        """Move *sequences* (anim and/or audio) into *dest_shot_id*.

        Sequences are grouped by source shot so each subgroup moves as a unit,
        preserving internal offsets — a multi-object selection keeps its
        shape.  Placement inside the destination is one rule, whichever
        direction the content travelled from:

            - If the destination already holds content on any of the group's
              objects, the group lands AFTER the last of it, separated by
              :meth:`sequence_separation`.
            - Otherwise it is anchored to the destination start: there is
              nothing to clear, so nothing is pushed.

        Appending unconditionally is the whole of the UX change.  Anchoring by
        direction of travel (before the existing content when the source lay
        downstream) meant the same gesture landed the clip somewhere different
        depending on which way the user dragged it, and the "before" case
        could place content ahead of the destination's own start — on top of
        the previous shot.  "It goes on the end" is the rule an editor already
        expects, and it cannot reach backwards.

        Nothing is overwritten and nothing is trimmed: the destination grows
        to enclose whatever landed (:meth:`extend_shot_to_fit`), rippling its
        neighbours so their spacing is preserved.

        After the move, ``shot.objects`` is recomputed for the destination and
        every source shot that lost content.

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

        separation = self.sequence_separation()

        # ---- 1. resolve every landing spot BEFORE anything moves ----------
        # Purely arithmetic, against the destination's CURRENT content: what
        # is already in the destination never moves, so these targets stay
        # valid across the room-making below.
        placements: List[tuple] = []  # (seq, source_shot_id, target_start)
        needed_end = dest.end
        # Earliest group first, so a multi-source move stacks in the order the
        # content sat on the timeline rather than in dict order.
        for source_id, group in sorted(
            groups.items(), key=lambda kv: min(s["start"] for s in kv[1])
        ):
            base = min(s["start"] for s in group)

            existing: List[Dict[str, Any]] = []
            for seq in group:
                existing.extend(dest_seqs_by_obj.get(seq["obj"], []))

            if existing:
                anchor = self.store.snap(max(e["end"] for e in existing) + separation)
            else:
                # Nothing of this group's to clear, so nothing is pushed.
                anchor = dest.start

            for seq in group:
                target = anchor + (seq["start"] - base)
                span = seq["end"] - seq["start"]
                placements.append((seq, source_id, target))
                needed_end = max(needed_end, target + span)
                # Stack later groups behind this one instead of stomping it.
                dest_seqs_by_obj.setdefault(seq["obj"], []).append(
                    {
                        "kind": seq["kind"],
                        "obj": seq["obj"],
                        "start": target,
                        "end": target + span,
                    }
                )
            if source_id is not None:
                affected_shots.add(source_id)

        # ---- 2. open the room, THEN land in it ----------------------------
        # Growing the destination afterwards cannot work: content that lands
        # past its end sits inside the NEXT shot's span, where the extend
        # probe disowns it (it cannot tell a neighbour's keys from its own)
        # and the following ripple would drag it straight back out again.
        # Making room first means the arriving content only ever lands on
        # empty timeline -- which is also what makes the operation read as
        # non-destructive rather than as an overwrite.
        with audio_utils.batch(), self.store.batch_update():
            room = self.store.snap(needed_end) - dest.end
            if room > 1e-6:
                old_end = dest.end
                # Source shots at or after the destination's end travel with
                # the ripple, and so does the content still sitting in them;
                # their recorded positions move by the same delta.
                travelled = {
                    sid
                    for sid in groups
                    if sid is not None
                    and (self.shot_by_id(sid) or dest).start >= old_end - 1e-6
                }
                dest.end = self.store.snap(needed_end)
                self.ripple_downstream(dest_shot_id, old_end, room)
                for seq, source_id, _target in placements:
                    if source_id in travelled:
                        seq["start"] += room
                        seq["end"] += room

            for seq, _source_id, target in placements:
                self._move_sequence(seq, target)

            for sid in affected_shots:
                self._recompute_shot_objects(sid)

        # A safety net for anything the arithmetic could not predict (audio
        # whose carrier resolved differently, a curve that refused a move):
        # extend-to-fit is implicit, never a separate user action.  Normally a
        # no-op now, because the room was already opened to size.
        self.extend_shot_to_fit(dest_shot_id)

    # ---- shot fit / trim / extend ----------------------------------------

    def fit_shot_to_content(
        self, shot_id: int, mode: str = "fit", edge: str = "both"
    ) -> tuple[float, float]:
        """Resize a shot's boundaries to its sequence content, rippling neighbors.

        Mode controls direction:
            ``"fit"`` — boundaries snap exactly to content (both expand and
                contract as needed).
            ``"trim"`` — only contract empty space; boundaries move *inward*
                and never past content.
            ``"extend"`` — only expand to enclose out-of-range content;
                boundaries move *outward* and never inward.

        *edge* restricts which end may move: ``"both"`` (default),
        ``"leading"`` (head only) or ``"trailing"`` (tail only).

        Neighbouring shots ripple by the head/tail deltas so spacing is
        preserved.  Audio shifts are batched.

        Returns:
            ``(head_delta, tail_delta)`` — the amount the start/end moved.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        sequences = self.collect_shot_sequences(shot_id)

        # ``sequences`` reports MOTION: ``collect_object_segments`` drops hold
        # spans so a clip reads as the animation it plays rather than as the
        # frames it occupies.  A BOUND may not move past a key, though — so
        # the in-bounds KEY extent is folded in beside the motion extent.
        # Without it a shot whose tail is a long hold read as empty at the
        # end: the trim put the bound in front of keys that then stayed
        # behind while the downstream ripple pulled the next shot's content
        # back on top of them.  Measured on a production assembly, "Step 4.1"
        # [991, 1590] collected motion out to 1313 while two of its members
        # hold keys to 1533, and one trailing trim moved "Step 4.8" from 1605
        # to 1328 — interleaving two shots' animation on six curves.
        #
        # For "extend" / "fit" the probe must ALSO look outside the current
        # range, which ``collect_shot_sequences`` clips away, to reveal
        # overrun.
        inner_start = inner_end = None
        outer_start = outer_end = None
        if shot.objects and cmds is not None:
            probe_outside = mode in ("extend", "fit")
            # Keys owned by OTHER shots must never be attributed to this
            # shot: with shared objects, an unbounded probe would drag
            # this shot's bounds over a neighbor and the follow-up
            # ripple would shift every other shot's keys.  Keys in gaps
            # (fade tails) still count.
            other_spans = [
                (s.start - 1e-6, s.end + 1e-6)
                for s in self.store.shots
                if s.shot_id != shot_id
            ]

            def _owned_elsewhere(t: float) -> bool:
                return any(lo <= t <= hi for lo, hi in other_spans)

            for obj in self._shot_nodes(shot):
                for t in cmds.keyframe(obj, q=True) or []:
                    if shot.start <= t <= shot.end:
                        inner_start = t if inner_start is None else min(inner_start, t)
                        inner_end = t if inner_end is None else max(inner_end, t)
                        continue
                    if not probe_outside or _owned_elsewhere(t):
                        continue
                    if t < shot.start:
                        outer_start = t if outer_start is None else min(outer_start, t)
                    else:
                        outer_end = t if outer_end is None else max(outer_end, t)

        probes = (inner_start, outer_start, outer_end)
        if not sequences and all(v is None for v in probes):
            return 0.0, 0.0

        seq_start = min(s["start"] for s in sequences) if sequences else None
        seq_end = max(s["end"] for s in sequences) if sequences else None

        def _combine(agg, *vals):
            present = [v for v in vals if v is not None]
            return agg(present) if present else None

        content_start = _combine(min, seq_start, inner_start, outer_start)
        content_end = _combine(max, seq_end, inner_end, outer_end)

        if mode == "extend":
            # One-sided rescue: content that drifted entirely past ONE edge
            # leaves the other side None — substituting the shot's own
            # boundary keeps extend usable in exactly the case it exists
            # for (enclosing out-of-range content).
            if content_start is None:
                content_start = shot.start
            if content_end is None:
                content_end = shot.end

        if content_start is None or content_end is None:
            return 0.0, 0.0

        if mode == "trim":
            new_start = max(shot.start, content_start)
            new_end = min(shot.end, content_end)
        elif mode == "extend":
            new_start = min(shot.start, content_start)
            new_end = max(shot.end, content_end)
        else:  # "fit"
            new_start = content_start
            new_end = content_end

        if edge == "leading":
            new_end = shot.end
        elif edge == "trailing":
            new_start = shot.start

        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        head_delta = new_start - shot.start
        tail_delta = new_end - shot.end
        if abs(head_delta) < 1e-6 and abs(tail_delta) < 1e-6:
            return 0.0, 0.0

        old_start, old_end = shot.start, shot.end

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch():
            shot.start = new_start
            shot.end = new_end
            if abs(tail_delta) > 1e-6:
                self.ripple_downstream(shot_id, old_end, tail_delta)
            if abs(head_delta) > 1e-6:
                self.ripple_upstream(shot_id, old_start, head_delta)

        # reconcile, not just enforce: this moved a shot BOUND, which is exactly
        # when a boundary sample the system created has to follow it or be
        # cleaned up.
        self.reconcile_system_edits()
        self.store.mark_dirty()
        return head_delta, tail_delta

    def trim_shot_to_content(
        self, shot_id: int, edge: str = "both"
    ) -> tuple[float, float]:
        """Shrink shot boundaries inward so they exactly enclose content.

        Empty leading/trailing space is removed; downstream/upstream shots
        ripple to preserve their spacing.  *edge* narrows the operation to
        one end — ``"leading"`` or ``"trailing"`` — leaving the other where
        the animator put it.
        """
        return self.fit_shot_to_content(shot_id, mode="trim", edge=edge)

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
        return Detection.detect_shot_regions(
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

        # Resolve to full DAG path to avoid crashes on ambiguous short names,
        # preferring the match that actually carries anim curves.
        matches = cmds.ls(obj, long=True)
        if not matches:
            return
        obj_path = self._disambiguate_matches(matches)

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
            times = cmds.keyframe(crv, q=True, time=tr) or []
            if not times:
                continue
            conns = cmds.listConnections(crv, plugs=True, d=True, s=False) or []
            self.move_curve_keys(
                crv,
                times,
                delta,
                plug=conns[0] if conns else None,
                eps=eps,
                ledger=self.ledger,
            )

    # ---- key motion primitives -------------------------------------------
    #
    # Moving keys and re-creating them are NOT equivalent: a re-created key is
    # born with default tangents, so a cut-and-recreate silently discards the
    # hand-tuned angles, weights, lock flags and breakdown markers that make a
    # curve look the way an animator left it.  Everything below therefore
    # prefers a real move (`keyframe -e -relative -timeChange -option over`,
    # which carries the whole key record) and falls back to cut-and-recreate --
    # with a FULL state snapshot/restore -- only for the two things a move
    # cannot express: a sparse selection inside a span, and a key landing
    # exactly on a frame that another key is keeping.

    #: Per-key tangent properties captured for a full-fidelity snapshot.
    #: ``keyTangent -q`` returns one entry per key in the queried range for
    #: each of these, in time order.
    _TANGENT_PROPS = (
        "inAngle",
        "outAngle",
        "inWeight",
        "outWeight",
        "inTangentType",
        "outTangentType",
        "weightLock",
        "lock",
    )

    @staticmethod
    def _nearest_index(sorted_times: list, t: float, eps: float) -> Optional[int]:
        """Index into *sorted_times* of the entry within *eps* of *t*, else None.

        Times reaching these primitives come from two places -- a curve query
        and a widget's drag record -- so they agree only to within rounding.
        Every match here is therefore a tolerance match, never ``==``.
        """
        i = bisect.bisect_left(sorted_times, t - eps)
        if i < len(sorted_times) and abs(sorted_times[i] - t) <= eps:
            return i
        return None

    @staticmethod
    def _key_time_at(crv: str, t: float, eps: float = _BATCH_MOVE_EPS):
        """Time of *crv*'s key within *eps* of *t*, or ``None``.

        A key sits where the last move left it -- the requested frame plus
        float noise -- so every lookup is a window query and the caller works
        from the time it gets BACK, never from the time it asked for.  The
        Maya-side twin of blendertk's ``_key_index_at``.
        """
        found = cmds.keyframe(crv, q=True, time=(t - eps, t + eps), timeChange=True)
        return float(found[0]) if found else None

    @classmethod
    def _destination_occupied(
        cls, crv: str, times: list, delta: float, eps: float = 1e-3
    ) -> bool:
        """True when a moved key would land exactly on a key that is staying put.

        ``keyframe(edit=True, relative=True, option="over")`` slides keys past
        neighbours happily — but two keys cannot share a frame, so an exact
        landing makes Maya nudge the arrival a hair short of the occupant,
        leaving a sub-frame twin instead of the intended overwrite.  That is
        the one case the move cannot express, and the only one that needs the
        cut-and-recreate path (whose ``setKeyframe`` overwrites, which is what
        a move onto an occupied frame should do).
        """
        all_times = cmds.keyframe(crv, q=True) or []
        if not all_times or not times:
            return False
        moving = sorted(times)
        stationary = sorted(
            t for t in all_times if cls._nearest_index(moving, t, eps) is None
        )
        if not stationary:
            return False
        return any(
            cls._nearest_index(stationary, t + delta, eps) is not None for t in moving
        )

    @classmethod
    def _snapshot_curve_keys(cls, crv: str, time_range: tuple) -> tuple:
        """Capture ``(weighted, records)`` for every key of *crv* in *time_range*.

        Each record holds the value, every entry of :attr:`_TANGENT_PROPS` and
        the breakdown flag — i.e. everything :meth:`_restore_curve_keys` needs
        to rebuild the key exactly as it was.  ``weighted`` is the curve-level
        ``weightedTangents`` state, which has to be re-applied first because a
        curve re-created by ``setKeyframe`` is born unweighted.
        """
        times = cmds.keyframe(crv, q=True, time=time_range) or []
        if not times:
            return False, []
        vals = cmds.keyframe(crv, q=True, time=time_range, valueChange=True) or []
        props = {
            name: (cmds.keyTangent(crv, q=True, time=time_range, **{name: True}) or [])
            for name in cls._TANGENT_PROPS
        }
        breakdowns = cmds.keyframe(crv, q=True, time=time_range, breakdown=True) or []
        weighted = bool(
            (cmds.keyTangent(crv, q=True, weightedTangents=True) or [False])[0]
        )

        records = []
        for i, t in enumerate(times):
            rec = {
                "time": t,
                "value": vals[i] if i < len(vals) else 0.0,
                "breakdown": any(abs(t - b) < 1e-6 for b in breakdowns),
            }
            for name, seq in props.items():
                if i < len(seq):
                    rec[name] = seq[i]
            records.append(rec)
        return weighted, records

    @classmethod
    def _restore_curve_keys(
        cls, target: str, records: list, weighted: bool, delta: float = 0.0
    ) -> None:
        """Re-create *records* on *target*, each shifted by *delta*.

        *target* is the anim curve, or the driven plug when ``cutKey`` deleted
        the curve along with its last key.

        Order matters: setting an angle on a key converts its tangent type to
        ``fixed``, so angles/weights go on FIRST and the recorded types are
        re-applied afterwards (a derived type then recomputes its own angle,
        which is what it did originally).  Locks are cleared up front because
        a locked tangent silently ignores angle and weight edits.
        """
        if not records:
            return
        for rec in records:
            cmds.setKeyframe(target, time=rec["time"] + delta, value=rec["value"])
        if weighted:
            cmds.keyTangent(target, edit=True, weightedTangents=True)

        for rec in records:
            tt = (rec["time"] + delta,) * 2
            # ``weightLock`` is rejected outright on a non-weighted curve, so
            # each lock is cleared on its own rather than as one edit.
            cls._try_key_tangent(target, tt, {"lock": False})
            cls._try_key_tangent(target, tt, {"weightLock": False})
            for group in (
                ("inAngle", "outAngle"),
                ("inWeight", "outWeight"),
                ("inTangentType", "outTangentType"),
                ("weightLock",),
                ("lock",),
            ):
                kw = {n: rec[n] for n in group if n in rec}
                if kw:
                    cls._try_key_tangent(target, tt, kw)
            if rec["breakdown"]:
                cmds.keyframe(target, edit=True, time=tt, breakdown=True)

    @staticmethod
    def _try_key_tangent(target: str, time_tuple: tuple, kwargs: dict) -> None:
        """``keyTangent`` edit that tolerates a property the curve won't take.

        Weights on an unweighted curve and angles on a stepped tangent are
        both rejected; neither is worth aborting the whole restore for.
        """
        try:
            cmds.keyTangent(target, edit=True, time=time_tuple, **kwargs)
        except RuntimeError:
            pass

    #: Frames a displaced key is pushed clear of the arriving cluster.  One
    #: frame is the quantum of an animation timeline: less would let the two
    #: read as a single beat, more would invent timing the user did not ask
    #: for.
    _PUSH_CLEARANCE = 1.0

    @classmethod
    def _is_contiguous_run(cls, crv: str, times: list, eps: float = 1e-3) -> bool:
        """True when *times* is EVERY key of *crv* between its first and last.

        The distinction the two callers need.  A contiguous run is a clip: it
        occupies a continuous region of the timeline, so anything inside that
        region is in its way, and a single relative move can express it.  A
        sparse set is a hand-picked group of keys with others deliberately
        left behind between them -- it occupies discrete frames, nothing more.
        """
        if not times:
            return False
        span = (min(times) - eps, max(times) + eps)
        return len(cmds.keyframe(crv, q=True, time=span) or []) == len(times)

    @classmethod
    def _absorb_holds(cls, crv: str, candidates: list, eps: float, ledger) -> list:
        """Cut every flat HOLD in *candidates*; return the ones that stayed.

        A hold carries no pose -- both neighbours already sit at its value, so
        the curve plays the same constant with or without it
        (:meth:`_sample_is_redundant`).  Cutting one is therefore free, and it
        is the common case: the frames between two clips are exactly where
        hold samples pile up.  Never cuts below two keys -- Maya deletes a
        keyless animCurve and takes the driving connection with it.
        """
        kept = []
        for t in candidates:
            if cls._key_time_at(crv, t, eps) is None:
                continue  # already gone; keeping it would be a phantom
            remaining = len(cmds.keyframe(crv, q=True, timeChange=True) or [])
            if remaining > 2 and cls._sample_is_redundant(crv, t):
                cmds.cutKey(crv, time=(t - eps, t + eps), clear=True)
                if ledger is not None:
                    ledger.release_step(crv, t)
                    ledger.release_key(crv, t)
            else:
                kept.append(t)
        return kept

    @classmethod
    def _clear_destination(
        cls,
        crv: str,
        times: list,
        delta: float,
        plug: Optional[str] = None,
        eps: float = 1e-3,
        ledger=None,
    ) -> None:
        """Make room on *crv* for ``times + delta``, without losing a pose.

        A cluster moved onto occupied frames used to land *interleaved* with
        whatever was already there -- the arriving keys threaded between the
        stationary ones, so the clip played as neither its own motion nor the
        old one.  On an exact frame collision it was worse: ``option="over"``
        and ``setKeyframe`` both overwrite, so the stationary key was simply
        gone.  Either way the animation came out malformed.

        Two rules, in order:

        1. Flat holds in the landing zone are absorbed (:meth:`_absorb_holds`).
        2. Whatever is left carries a pose, so it is PUSHED clear -- in the
           direction of travel, by ONE delta, as a single rigid block.

        The block is grown to a fixpoint before anything moves: a key the
        block would land on joins the block rather than being displaced
        separately.  That is what keeps the displaced material's timing.
        Pushing only the keys that literally overlapped, and letting each
        collision compute its own smaller push, tore a cluster in half
        whenever it straddled the edge of the landing zone -- the earlier half
        stayed put while the later half moved, which is the same "malformed"
        result in miniature.

        CONTIGUOUS RUNS ONLY.  The landing zone is a continuous REGION, which
        is what a clip is; a sparse selection (a hand-picked set of key dots,
        with others deliberately left between them) occupies discrete frames
        instead, so the span between its first and last arrival is not
        "occupied" in any sense a stationary key can block.  Applying the span
        rule there displaced keys the arrival never touched, and dropping a
        dragged key onto an occupied frame stays what it has always been --
        an overwrite, which is also what Maya's own Graph Editor does.
        """
        if not cls._is_contiguous_run(crv, times, eps):
            return
        moving = sorted(times)
        lo = moving[0] + delta
        hi = moving[-1] + delta

        # Ask about the landing window only.  This runs for every curve of
        # every ripple, where the window is almost always empty, and reading
        # the whole curve just to discover that is pure overhead.
        window = (
            cmds.keyframe(crv, q=True, time=(lo - eps, hi + eps), timeChange=True) or []
        )
        blocking = [t for t in window if cls._nearest_index(moving, t, eps) is None]
        if not blocking:
            return

        # Something is in the way, so now the whole curve is worth reading:
        # the fixpoint below needs the keys OUTSIDE the window too.
        all_times = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
        stationary = [
            t for t in all_times if cls._nearest_index(moving, t, eps) is None
        ]
        if not stationary:
            return

        displaced = cls._absorb_holds(crv, blocking, eps, ledger)
        if not displaced:
            return

        # One delta for the whole block, decided by the member that has to
        # travel furthest to clear the arrival.  Growing the block never
        # changes it: a leftward push only ever reaches EARLIER keys (and a
        # rightward one only later ones), so the deciding member is already in.
        if delta < 0:
            push = lo - cls._PUSH_CLEARANCE - max(displaced)
        else:
            push = hi + cls._PUSH_CLEARANCE - min(displaced)
        if abs(push) < eps:
            return

        # Grow to a fixpoint: anything the block would land on travels WITH it.
        # Seeded with every candidate already CONSIDERED, not just the ones
        # that survived: an absorbed key is still in `stationary` (a snapshot),
        # and re-offering it would fail the redundancy test -- it no longer
        # exists -- and be adopted into the block as a phantom.
        taken = set(blocking)
        for _ in range(len(stationary)):
            d_lo = min(displaced) + push - eps
            d_hi = max(displaced) + push + eps
            reached = [t for t in stationary if t not in taken and d_lo <= t <= d_hi]
            if not reached:
                break
            taken.update(reached)
            kept = cls._absorb_holds(crv, reached, eps, ledger)
            if not kept:
                continue
            displaced.extend(kept)

        restore_tangents = cls._hold_interior_tangents(
            crv, sorted(displaced), push, eps
        )
        cls._commit_curve_move(
            crv, sorted(displaced), push, plug=plug, eps=eps, ledger=ledger
        )
        restore_tangents()

    @classmethod
    def move_curve_keys(
        cls,
        crv: str,
        times: list,
        delta: float,
        plug: Optional[str] = None,
        eps: float = 1e-3,
        ledger=None,
    ) -> None:
        """Shift the keys of *crv* at *times* by *delta*, tangents intact.

        Prefers a real relative move so every key record travels untouched;
        falls back to a full-fidelity cut-and-recreate only where the move
        cannot express what was asked for.

        The landing zone is CLEARED first (:meth:`_clear_destination`): keys
        already sitting there are absorbed when they are flat holds and pushed
        aside when they carry a pose.  Without that, a cluster dropped onto
        occupied frames either interleaved with what was there or overwrote
        it — both of which read as the animation coming apart.

        PUBLIC because it has two consumers -- this class's own shot moves and
        ``ClipMotionMixin``'s drag handler -- so the underscore was describing
        a contract that no longer held. Not to be confused with the unrelated
        ``AnimUtils._move_curve_keys``, which stays private and takes
        ``(curve, time_pairs, allow_merge=...)``: that one moves keys to
        arbitrary per-key destinations for the key-scale tool, where this one
        shifts a whole set by one delta and preserves the full key record.

        Parameters:
            crv: Anim curve node.
            times: Times of the keys to move (matched to *crv* within *eps*).
            delta: Frames to add to each of *times*.
            plug: Driven plug, used when ``cutKey`` deletes the curve node.
            eps: Half-width of the per-key time window.
            ledger: :class:`~pythontk.ShotEditLedger` to carry along, so a
                claim on one of *times* travels with the key instead of being
                stranded at the frame it used to sit on.
        """
        if not times or abs(delta) < 1e-6:
            return
        restore_tangents = cls._hold_interior_tangents(crv, times, delta, eps)
        cls._clear_destination(crv, times, delta, plug=plug, eps=eps, ledger=ledger)
        cls._commit_curve_move(crv, times, delta, plug=plug, eps=eps, ledger=ledger)
        restore_tangents()

    @classmethod
    def _hold_interior_tangents(cls, crv: str, times: list, delta: float, eps: float):
        """Snapshot the run's INTERIOR-facing boundary tangents; return a restore.

        A moved run has two boundary tangents that face inward -- the first
        key's OUT and the last key's IN -- and those shape spans that live
        entirely inside the run.  A rigid slide moves both of their endpoints
        together, so the motion they describe cannot have changed and must
        come out identical.

        Maya disagrees, because a derived tangent (see
        :attr:`_NEIGHBOUR_DERIVED_TANGENTS`) is ONE unbroken slope computed
        from the keys on both sides.  Slide the run away from whatever
        precedes it and that outside key -- which did not move, and is not
        part of the run -- re-computes the inward half too, quietly reshaping
        the segment's own animation.  Measured: a three-key run slid 20
        frames came out with its first interior span 0.086 units off and its
        tangent down from 13.13 to 9.93 degrees, and on a production assembly
        the same effect walked one key from 2.12 to 0.81 degrees over four
        drags -- "the dragged segment's starting key tangent went flat".

        Snapshot BEFORE the landing zone is cleared: absorbing or pushing a
        key next to the run reshapes these same tangents, so a snapshot taken
        after it would faithfully preserve the already-damaged angle.

        The restore BREAKS the tangent rather than pinning the whole thing:
        the outward-facing half spans the gap to neighbouring content, which
        genuinely did change, so it is left derived and free to re-ease.  Only
        the inward half is held, and only when it actually moved.

        Returns a zero-argument callable; it is a no-op when there is nothing
        to hold, so the caller never branches.
        """
        moving = sorted(times)
        if len(moving) < 2:
            return lambda: None  # a lone key has no interior to protect

        held = []
        for t, half in ((moving[0], "out"), (moving[-1], "in")):
            # Resolve the key's ACTUAL time first: *times* reaches here from a
            # widget's drag record as readily as from a curve query, and the
            # two agree only to rounding -- an exact-time read would come back
            # empty and silently skip the hold.
            src = cls._key_time_at(crv, t, eps)
            if src is None:
                continue
            tt = (src, src)
            type_flag = "outTangentType" if half == "out" else "inTangentType"
            tan_type = (
                cmds.keyTangent(crv, q=True, time=tt, **{type_flag: True}) or [""]
            )[0]
            if tan_type not in _NEIGHBOUR_DERIVED_TANGENTS:
                continue  # authored outright, or reads only its own side
            angle_flag = "outAngle" if half == "out" else "inAngle"
            angle = cmds.keyTangent(crv, q=True, time=tt, **{angle_flag: True}) or []
            if angle:
                held.append((src + delta, half, float(angle[0])))
        if not held:
            return lambda: None

        def _restore():
            for dest, half, angle in held:
                landed = cls._key_time_at(crv, dest, eps)
                if landed is None:
                    continue  # the key did not arrive; nothing to hold
                tt = (landed, landed)
                angle_flag = "outAngle" if half == "out" else "inAngle"
                now = cmds.keyTangent(crv, q=True, time=tt, **{angle_flag: True}) or []
                if not now or abs(float(now[0]) - angle) <= _TANGENT_MOVED_TOL:
                    continue  # Maya left it alone, so leave the type alone too
                # Break first: that is what keeps the OTHER half derived.
                cls._try_key_tangent(crv, tt, {"lock": False})
                cls._try_key_tangent(crv, tt, {angle_flag: angle})

        return _restore

    @classmethod
    def _commit_curve_move(
        cls,
        crv: str,
        times: list,
        delta: float,
        plug: Optional[str] = None,
        eps: float = 1e-3,
        ledger=None,
    ) -> None:
        """The raw shift, with the destination assumed already clear.

        Split out of :meth:`move_curve_keys` because the collision handling
        has to move keys too (that is what "push out of the way" is) and must
        not recurse back into its own clearing pass.
        """
        if not times or abs(delta) < 1e-6:
            return

        # The range edit shifts EVERY key in the span, so it can only stand in
        # for the requested move when the moved set IS that whole span.  A
        # sparse selection (keys inside the span staying put) goes to the
        # recreate path, which carries a destination per key.
        span = (min(times) - eps, max(times) + eps)
        if cls._is_contiguous_run(crv, times, eps) and not cls._destination_occupied(
            crv, times, delta, eps
        ):
            # ``option="over"`` lets the cluster slide past keys that are
            # staying put — the default clamps against the first one — and the
            # whole key record travels with it: angles, weights, lock flags,
            # and an enclosed breakdown key still riding its neighbours.
            try:
                cmds.keyframe(
                    crv,
                    edit=True,
                    relative=True,
                    timeChange=delta,
                    time=span,
                    option="over",
                )
                if ledger is not None:
                    ledger.remap(crv, [(t, t + delta) for t in times])
                return
            except RuntimeError:
                pass  # fall through to the recreate path

        cls.recreate_curve_keys(
            crv, [(t, t + delta) for t in times], plug=plug, eps=eps, ledger=ledger
        )

    @classmethod
    def recreate_curve_keys(
        cls,
        crv: str,
        pairs: list,
        plug: Optional[str] = None,
        eps: float = 1e-3,
        ledger=None,
    ) -> None:
        """Cut the keys named by *pairs* and rebuild them at their new times.

        *pairs* is ``[(old_time, new_time), ...]``; the deltas need not agree.
        Every key is snapshotted and cut before any is re-created, so a key
        landing on another key's vacated slot can't corrupt the later read.

        *ledger* is remapped alongside, for the same reason
        :meth:`move_curve_keys` takes one.
        """
        pairs = sorted((p for p in pairs if abs(p[1] - p[0]) >= 1e-6))
        if not pairs:
            return
        old_times = [o for o, _ in pairs]
        span = (old_times[0] - eps, old_times[-1] + eps)
        weighted, records = cls._snapshot_curve_keys(crv, span)
        if not records:
            return

        # The span can also cover keys that are staying put; keep only ours,
        # and carry each one's own destination.
        moving = []
        for rec in records:
            i = cls._nearest_index(old_times, rec["time"], eps)
            if i is None:
                continue
            rec["time"] = pairs[i][1]
            moving.append(rec)
        if not moving:
            return

        for old_t in old_times:
            # cutKey deletes the curve node along with its last key, so stop
            # addressing it the moment it goes away.
            if not cmds.objExists(crv):
                break
            cmds.cutKey(crv, time=(old_t - eps, old_t + eps), clear=True)
        target = crv if cmds.objExists(crv) else plug
        if not target:
            return
        cls._restore_curve_keys(target, moving, weighted)
        if ledger is not None:
            ledger.remap(crv, pairs)

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

        A step this system claims travels with its key: the destination is
        where the hold has to be released from once it stops being a seam.
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
                if ot and ot[0] in _STEP_TANGENTS:
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
            self.ledger.remap(crv, [(old_time, new_time)])

    @staticmethod
    def _batch_move_keys(
        cmds,
        objects,
        env_lo,
        env_hi,
        delta,
        lo_open: bool = False,
        hi_closed: bool = False,
        ledger=None,
    ):
        """Shift every key of *objects* inside the envelope by *delta*.

        Resolves curves in a single batch rather than per-object, then
        shifts keys using a direct single-pass move.  Takes the same window
        (bounds plus fencepost flags) as the plan path's writer, so the two
        movers cannot disagree about which shot owns a shared sample.
        """
        if not objects or abs(delta) < 1e-6:
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

        eps = _BATCH_MOVE_EPS
        tr = (
            env_lo + eps if lo_open else env_lo - eps,
            env_hi + eps if hi_closed else env_hi - eps,
        )

        for crv in curves:
            times = cmds.keyframe(crv, q=True, time=tr) or []
            if not times:
                continue
            conns = cmds.listConnections(crv, plugs=True, d=True, s=False) or []
            # Same primitive as move_object_keys: a bare relative move used to
            # collapse the cluster against any key already sitting in the
            # destination window instead of falling back.
            ShotSequencer.move_curve_keys(
                crv,
                times,
                delta,
                plug=conns[0] if conns else None,
                eps=eps,
                ledger=ledger,
            )

    @staticmethod
    def _shift_audio(
        old_start: float,
        old_end: float,
        delta: float,
    ) -> None:
        """Shift audio clips whose timeline position falls within a range.

        Delegates to :func:`mayatk.audio_utils.shift_keys_in_range`
        which updates the canonical keyed store. Callers are expected
        to wrap bulk ops in an ``audio_utils.batch()`` so the compositor
        re-renders derived DG audio nodes in a single sync.

        Parameters:
            old_start: Start of the time range to shift.
            old_end: End of the time range to shift.
            delta: Frames to add to each audio key.
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
    ) -> None:
        """Shift all content (object keys and audio) for *shot* to *new_start*.

        Uses :meth:`_batch_move_keys` for animation curves and
        :meth:`_shift_audio` for DG audio nodes / event triggers,
        then updates the shot boundaries.  When Maya is not available
        only the boundaries are updated.

        Parameters:
            shot: The shot to move.
            new_start: Desired first frame after the move.
        """
        new_start = self.store.snap(new_start)
        old_start = shot.start
        old_end = shot.end
        delta = new_start - old_start
        if abs(delta) < 1e-6:
            return

        duration = old_end - old_start

        if cmds is not None:
            from mayatk.anim_utils.shots._shot_plan import ShotPlanner

            # The engine derives the pivot's envelope and one-move plan, so
            # this mover and the plan path cannot disagree about a shared
            # sample.  Falls back to the shot's own span only if the store
            # somehow does not hold it.
            plan = ShotPlanner.plan_pivot_move(self.store, shot.shot_id, new_start)
            move = plan.moves.get(shot.shot_id)
            if move is None:
                env_lo, env_hi, lo_open, hi_closed = old_start, old_end, False, True
            else:
                env_lo, env_hi = move.env_start, move.env_end
                lo_open, hi_closed = move.env_lo_open, move.env_hi_closed

            # A shot moving must carry everything keyed inside it, not just
            # what membership happens to list — stale entries (a renamed rig)
            # and objects animated after the shots were authored are both
            # invisible until the move strands them.
            self._adopt_keyed_objects(
                shot, env_lo, env_hi, lo_open=lo_open, hi_closed=hi_closed
            )
            finish = self._reconcile_boundaries(plan)
            self._batch_move_keys(
                cmds,
                shot.objects,
                env_lo,
                env_hi,
                delta,
                lo_open=lo_open,
                hi_closed=hi_closed,
                ledger=self.ledger,
            )
            # Audio keeps its own inclusive [old_start, old_end] window: clips
            # are a separate store with no fencepost sharing.
            self._shift_audio(old_start, old_end, delta)
            finish()

        shot.start = new_start
        shot.end = self.store.snap(new_start + duration)

    def move_object_in_shot(
        self,
        shot_id: int,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
    ) -> None:
        """Move one object's keys within a shot, expanding the shot and
        rippling downstream shots when the clip exceeds shot boundaries.

        Parameters:
            shot_id: Shot the object belongs to.
            obj: Transform node name to move.
            old_start: Original first frame of the object segment.
            old_end: Original last frame of the object segment.
            new_start: Desired first frame after the move.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        new_start = self.store.snap(new_start)
        dur = old_end - old_start
        new_end = self.store.snap(new_start + dur)

        # Boundaries FIRST, keys second.  Expanding past the next shot's
        # start ripples that shot through its envelope, and a key already
        # landed at/past the envelope's start is swept a SECOND time -- the
        # clip travels the drag distance plus the ripple.  Measured on a
        # production assembly: a segment dragged +20 past the shot end came
        # out +38, its keys interleaved with the next shot's and their
        # auto tangents recomputed flat in the new neighbourhood.  Before the
        # move the keys still sit at their old times inside the pivot, which
        # the ripple plan excludes, so nothing can reach them.  (The per-key
        # drag path already orders it this way -- see
        # ``ClipMotionMixin._expand_and_compensate``.)
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
                self.ripple_upstream(shot_id, prior_start, start_delta)

        # Ripple downstream by however much the shot tail grew
        if end_expanded:
            end_delta = shot.end - prior_end  # positive
            if abs(end_delta) > 1e-6:
                self.ripple_downstream(shot_id, prior_end, end_delta)
        if start_expanded or end_expanded:
            self.store.mark_dirty()

        # Move the object's keys into the room just opened for them.
        self.move_object_keys(obj, old_start, old_end, new_start)
        # Do NOT call _enforce_gap_holds() here — it iterates ALL objects
        # in every pre-gap shot and sets out-tangents to "step", corrupting
        # tangent types on objects the user didn't touch.  Gap holds are
        # enforced by respace() and move_shot() (whole-shot operations).

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

        # Resolve to a single DAG path first — shot.objects entries may be
        # short names that turn ambiguous when the scene gains a same-named
        # node, and cmds.scaleKey raises on ambiguity (see _shot_nodes).
        matches = cmds.ls(obj, long=True)
        if not matches:
            return
        if abs(old_end - old_start) < 1e-6:
            return
        cmds.scaleKey(
            self._disambiguate_matches(matches),
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
        self.slide_shot(shot_id, new_start, direction="downstream")

    def _clamp_slide_start(self, shot, new_start: float, rippled: Optional[str]):
        """Hold *new_start* inside the room the neighbours are NOT making.

        A slide moves the shot whole and ripples ONE side, or neither — so
        the other side has to hold.  Without that the pivot slides straight
        over its neighbour and the store ends up with two shots claiming one
        span, which makes key ownership (and every envelope derived from it)
        ambiguous: exactly the corruption the inner gap-edge drag already
        guards against (``GapManagerMixin._set_shot_edge``), reached instead
        through the gesture that moves a whole shot.  Measured on a
        production assembly: an outer gap drag pulled "Step 4.8" 212 frames
        earlier, from [1605, 2180] to [1393, 1968], while "Step 4.4"
        [1373, 1605] — which the downstream ripple never touches — stayed
        put, so the two shots shared 212 frames and the panel drew "Step 4.4"
        ending mid-content.

        Parameters:
            shot: The shot being slid.
            new_start: The requested start frame.
            rippled: ``"downstream"``, ``"upstream"``, or ``None`` when
                neither side moves and both therefore hold.

        Returns:
            *new_start*, clamped against whichever side is not rippling.
        """
        sorted_s = self.sorted_shots()
        idx = next(
            (i for i, s in enumerate(sorted_s) if s.shot_id == shot.shot_id), None
        )
        if idx is None:
            return new_start
        # Tail first, head second: the head clamp wins a tie, so a shot with
        # nowhere to go stays put rather than swapping which neighbour it
        # overlaps.  (A shot that started inside valid bounds always has
        # room, so the two cannot actually conflict.)
        if rippled != "downstream" and idx + 1 < len(sorted_s):
            new_start = min(
                new_start, sorted_s[idx + 1].start - (shot.end - shot.start)
            )
        if rippled != "upstream" and idx > 0:
            new_start = max(new_start, sorted_s[idx - 1].end)
        return new_start

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
        new_start = self._clamp_slide_start(shot, new_start, direction)
        delta = new_start - old_start
        if abs(delta) < 1e-6:
            return

        # Vacate the destination side before moving the pivot: when the
        # pivot moves TOWARD the shots about to ripple, moving it first
        # would deposit its keys inside a neighbor's not-yet-read
        # envelope and shared-object curves would be shifted twice.
        if direction == "downstream":
            if delta > 0:
                self.ripple_downstream(shot_id, old_end, delta)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                self.ripple_downstream(shot_id, old_end, delta)
        elif direction == "upstream":
            if delta < 0:
                self.ripple_upstream(shot_id, old_start, delta)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                self.ripple_upstream(shot_id, old_start, delta)

        if _enforce:
            self._enforce_gap_holds()
        self.store.mark_dirty()

    def ripple_downstream(
        self,
        shot_id: int,
        after_frame: float,
        delta: float,
    ):
        """Shift all shots starting at or after *after_frame* by *delta*.

        Routes through :mod:`_shot_plan` and :mod:`_shot_apply` so
        the whole downstream topology is resolved before any keyframe
        is touched — preventing envelope collisions between moved and
        not-yet-moved shots.  The public ripple entry point for
        callers outside the sequencer (settings panel, clip motion).
        """
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        plan = ShotPlanner.plan_ripple_downstream(
            self.store, shot_id, after_frame, delta
        )
        self._apply_plan(plan)

    def ripple_upstream(
        self,
        shot_id: int,
        before_frame: float,
        delta: float,
    ):
        """Shift all shots ending at or before *before_frame* by *delta*.

        Routes through the plan/executor pair — see
        :meth:`ripple_downstream` for the rationale.
        """
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        plan = ShotPlanner.plan_ripple_upstream(
            self.store, shot_id, before_frame, delta
        )
        self._apply_plan(plan)

    # ---- system-authored edits (ledger-backed) ---------------------------
    #
    # The two writes the shot system makes on the animator's curves — a gap
    # hold and a boundary sample — are claimed in ``store.edit_ledger`` as
    # they are made.  That is what lets them be RELEASED when the boundary
    # that justified them moves: without a claim a step is just a step and a
    # key is just a key, and the only safe thing to do with either is leave it
    # behind on every adjust.  Anything the system did not write is never
    # touched — a hold the animator put in is intentional by definition.

    @property
    def ledger(self):
        """The store's :class:`~pythontk.ShotEditLedger` (system-write claims)."""
        return self.store.edit_ledger

    def _gap_hold_seams(self) -> Dict[str, list]:
        """``{curve: [seam_time, ...]}`` — where gap holds currently belong.

        The seam is the last key before the NEXT shot's start (the envelope
        rule), not the pre-gap shot's end: a bounds-only shrink strands keys
        in the gap, and stepping the last key INSIDE the bounds while stranded
        keys interpolate beyond it puts a permanent step on a mid-content key.
        (Motion BETWEEN stranded keys is the shot's own content under the
        bounds-only contract and is left alone.)

        A LIST per curve, not one time: shot objects are routinely shared, so
        one curve is commonly the seam of several gaps and every one of them
        has to hold.  Collapsing to a single entry left all but one gap
        interpolating across the cut.

        A curve with NO key inside the pre-gap shot is skipped entirely.  Shot
        membership is per OBJECT, so every curve on a member comes back here
        -- including ones whose first key lands in the gap and whose motion
        runs on into the NEXT shot.  There is nothing for such a curve to hold
        across the gap (it has no pre-gap value), and its first key is the
        next shot's lead-in, not this shot's overhang.  Measured on the
        production assembly: ``FAILED_CMPT_LOC`` is a member of "Step 2.1"
        (33-65) through its opacity fade, while its translate/rotate curves
        start in the gap and run on into it -- the seam rule picked that
        first key on each of them and stepped its out-tangent, freezing
        "Step 3.1"'s own animation.  All six carried a system-claimed
        ``step`` there in the saved scene; a synthetic replay of the same
        shape read -16.8 at frame 83 where the curve plays -11.5.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        seams: Dict[str, list] = {}
        sorted_s = self.sorted_shots()
        eps = _BATCH_MOVE_EPS
        for i in range(len(sorted_s) - 1):
            pre = sorted_s[i]
            nxt = sorted_s[i + 1]
            if nxt.start - pre.end < 1e-6:
                continue  # no gap — shots are contiguous or overlapping
            if not pre.objects:
                continue
            curves = AnimUtils.objects_to_curves(pre.objects, as_strings=True)
            if not curves:
                continue
            for crv in curves:
                times = cmds.keyframe(
                    crv,
                    q=True,
                    time=(pre.start - eps, nxt.start - eps),
                    timeChange=True,
                )
                if not times:
                    continue
                if not any(t <= pre.end + eps for t in times):
                    continue  # starts inside the gap: lead-in, not overhang
                last_t = max(times)
                got = seams.setdefault(crv, [])
                # Two shots can share a seam (one ends where the probe of the
                # next begins); record it once so the claim count matches the
                # number of held keys.
                if not any(abs(last_t - t) <= eps for t in got):
                    got.append(last_t)
        return seams

    def _release_gap_holds(self, seams: Dict[str, list]) -> int:
        """Undo every claimed step that *seams* no longer asks for.

        The CLAIM is dropped whatever the scene says, so a curve that has
        since been deleted, re-tangented by hand, or moved out from under its
        claim cannot leave a permanent entry behind.  The WRITE is only taken
        back when the key is still there and still carries the system's
        ``step`` — an animator who re-tangented it since owns it now.

        Returns:
            The number of keys restored to their pre-hold out-tangent.
        """
        led = self.ledger
        eps = _BATCH_MOVE_EPS
        restored = 0
        for crv in led.stepped_curves():
            # A curve the seam scan just resolved is live by construction, so
            # the existence probe only runs for claims nothing asked about.
            if crv not in seams and not cmds.objExists(crv):
                led.forget_curve(crv)
                continue
            want = seams.get(crv, ())
            for t in led.step_times(crv):
                if any(abs(t - w) <= eps for w in want):
                    continue  # still a seam — the hold still belongs here
                types = led.release_step(crv, t)
                if types is None:
                    continue
                key_t = self._key_time_at(crv, t, eps)
                if key_t is None:
                    continue  # the key is gone; the claim went with it
                ott = (
                    cmds.keyTangent(
                        crv, q=True, time=(key_t, key_t), outTangentType=True
                    )
                    or [""]
                )[0]
                if ott != "step":
                    continue  # re-tangented since — not ours to take back
                self._try_key_tangent(crv, (key_t, key_t), {"outTangentType": types[1]})
                restored += 1
        return restored

    def _apply_gap_holds(self, seams: Dict[str, list]) -> int:
        """Step every seam in *seams* that is not stepped already.

        A key that ALREADY carries a step is left alone and, crucially, not
        claimed: it is either this system's own hold from an earlier pass
        (already claimed — re-recording would capture ``step`` as the
        original and make the release a no-op) or the animator's, which the
        system must never take back.

        Returns:
            The number of keys stepped.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        led = self.ledger
        step_dict: Dict[str, list] = {}
        for crv, times in seams.items():
            for t in times:
                tt = (t, t)
                ott = cmds.keyTangent(crv, q=True, time=tt, outTangentType=True) or []
                if not ott:
                    # No key there to read a tangent from, so there is nothing
                    # to restore it TO later.  Stepping without a recoverable
                    # original is exactly the permanent edit this pass exists
                    # to avoid.
                    continue
                if ott[0] == "step":
                    continue
                itt = (
                    cmds.keyTangent(crv, q=True, time=tt, inTangentType=True) or [""]
                )[0]
                led.record_step(crv, t, itt, ott[0])
                step_dict.setdefault(crv, []).append(t)
        if step_dict:
            AnimUtils.step_keys(keys=step_dict, tangent="out")
        return sum(len(v) for v in step_dict.values())

    def _enforce_gap_holds(self):
        """Hold every inter-shot gap, and release the holds that no longer are.

        A gap must not contain interpolated motion, so the last key before it
        gets a stepped out-tangent.  The release half is what keeps those from
        accumulating: a key the system stepped that is no longer the seam —
        because the boundary moved, the gap closed, the shot was deleted, or
        the animator dragged the key inward — gets its original out-tangent
        back.  Only keys this system stepped are ever restored (see
        :meth:`_release_gap_holds`).

        Called automatically after every timeline-modifying operation, and
        idempotent: a second run finds every seam already stepped and every
        claim still wanted, so it writes nothing.
        """
        if cmds is None:
            return
        seams = self._gap_hold_seams()
        self._release_gap_holds(seams)
        self._apply_gap_holds(seams)

    @classmethod
    def _sample_is_redundant(cls, crv: str, t: float) -> bool:
        """True when cutting the key at *t* cannot change what *crv* plays.

        Two conditions, and equal values alone is NOT one of them:

        1. the key sits in a flat plateau — both immediate neighbours carry
           its value; and
        2. the segment its removal leaves behind is flat too.

        (2) is what a released boundary sample satisfies: it was created as a
        duplicate of the pose across the seam, on a curve that was already
        holding.  Anything else carries shape, and shape is never cut to tidy
        up — see the comment below for what dropping (2) did.

        A classmethod because :meth:`move_curve_keys` asks the same question
        of the keys a move is about to land on top of — "is there a pose here,
        or only a hold?" is one test, not two.
        """
        times = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
        i = cls._nearest_index(times, t, _BATCH_MOVE_EPS)
        if i is None or i == 0 or i == len(times) - 1:
            return False  # no neighbour on one side: the hold beyond it is shape

        # One query over the three-key span, not three point queries: this runs
        # per orphaned claim, and ``valueChange`` already comes back in time
        # order so the triple is exactly what a plateau test needs.
        eps = _BATCH_MOVE_EPS
        span = (times[i - 1] - eps, times[i + 1] + eps)
        vals = cmds.keyframe(crv, q=True, time=span, valueChange=True) or []
        if len(vals) != 3:
            return False  # something else sits in the span; not a clean triple
        prev, here, nxt = (float(v) for v in vals)
        if abs(prev - here) > _POSE_TOL or abs(nxt - here) > _POSE_TOL:
            return False

        # Equal values are NOT enough: with smooth tangents this key is what
        # PINS the plateau.  Two more conditions, (a) and (b) below.
        out_types = cmds.keyTangent(crv, q=True, time=span, outTangentType=True) or []
        in_ang = cmds.keyTangent(crv, q=True, time=span, inAngle=True) or []
        out_ang = cmds.keyTangent(crv, q=True, time=span, outAngle=True) or []
        if any(len(x) != 3 for x in (out_types, in_ang, out_ang)):
            return False  # unreadable tangents: assume the key carries shape

        # (a) The segment left behind must PLAY constant.  It runs from the
        # previous key to the next and is shaped by the previous key's OUT
        # tangent and the next key's IN tangent, so it stays flat only when
        # those two are flat -- or when the previous key steps, which holds
        # its value across the span whatever the angles say.  Measured: cutting
        # the middle of a synthetic SPLINE plateau moved the curve 0.83 units.
        if out_types[0] not in _STEP_TANGENTS and not (
            abs(float(out_ang[0])) <= _FLAT_ANGLE_TOL
            and abs(float(in_ang[2])) <= _FLAT_ANGLE_TOL
        ):
            return False

        # (b) And no surviving key may be RESHAPED by the cut.  A derived
        # tangent is computed from the keys on both sides of its own, so
        # removing this one re-computes the neighbours' slopes -- including
        # the halves facing AWAY from the cut, which is how the damage hid:
        # on a production visibility curve the previous key's out-tangent was
        # ``step`` (so (a) passed) while its ``spline`` IN-tangent quietly
        # marched 2.12 -> 1.85 -> 1.12 -> 0.81 across four unrelated group
        # drags.  A flat derived tangent is safe: the neighbour it gains
        # carries this key's own value, so it recomputes flat again.
        in_types = cmds.keyTangent(crv, q=True, time=span, inTangentType=True) or []
        if len(in_types) != 3:
            return False
        for i in (0, 2):
            for tan_type, angle in (
                (in_types[i], in_ang[i]),
                (out_types[i], out_ang[i]),
            ):
                if (
                    tan_type in _NEIGHBOUR_DERIVED_TANGENTS
                    and abs(float(angle)) > _FLAT_ANGLE_TOL
                ):
                    return False
        return True

    def _reconcile_boundary_keys(self) -> tuple:
        """Make every claimed boundary sample follow — or leave — its bound.

        A sample the system created exists for ONE shot bound.  Once that
        bound has moved out from under it, it is neither the animator's pose
        nor a fencepost; leaving it is the clutter that builds up on every
        adjust.  Each claim resolves one of four ways:

        * the bound is still under it — nothing to do;
        * the key is gone (cut, or moved by an edit that carried the claim
          with it) — drop the claim;
        * the bound moved and its new frame is free — MOVE the sample there,
          full key record intact, carrying the claim with it;
        * the bound moved onto an occupied frame, or the owning shot is gone
          — cut the sample if it is provably redundant
          (:meth:`_sample_is_redundant`), otherwise disown it and leave it
          where it is.  A curve is never cut below two keys: Maya deletes a
          keyless animCurve and takes the connection with it.

        Returns:
            ``(moved, removed)`` — samples relocated, and samples cut.
        """
        if cmds is None:
            return 0, 0
        led = self.ledger
        eps = _BATCH_MOVE_EPS
        moved = removed = 0
        for crv in led.keyed_curves():
            if not cmds.objExists(crv):
                led.forget_curve(crv)
                continue
            for t, owner, edge in led.key_records(crv):
                shot = self.shot_by_id(owner) if owner >= 0 else None
                bound = None
                if shot is not None and edge in ("start", "end"):
                    bound = shot.start if edge == "start" else shot.end
                if bound is not None and abs(bound - t) <= eps:
                    continue  # still on its bound
                key_t = self._key_time_at(crv, t, eps)
                if key_t is None:
                    led.release_key(crv, t)
                    continue
                occupied = (
                    bound is not None and self._key_time_at(crv, bound, eps) is not None
                )
                if bound is not None and not occupied:
                    # The plug is the fallback target when a cut-and-recreate
                    # takes the curve node with its last key; the same
                    # insurance every other mover here carries.
                    conns = cmds.listConnections(crv, plugs=True, d=True, s=False) or []
                    self.move_curve_keys(
                        crv,
                        [key_t],
                        bound - key_t,
                        plug=conns[0] if conns else None,
                        ledger=led,
                    )
                    moved += 1
                    continue
                # Nowhere to follow to: cut it only where that is provably a
                # no-op, and disown it either way.
                if (
                    self._sample_is_redundant(crv, key_t)
                    and (cmds.keyframe(crv, q=True, keyframeCount=True) or 0) > 2
                ):
                    try:
                        cmds.cutKey(crv, time=(key_t - eps, key_t + eps), clear=True)
                        removed += 1
                    except RuntimeError:
                        pass  # locked or referenced curve — leave it as it was
                led.release_key(crv, key_t)
        return moved, removed

    def reconcile_system_edits(self) -> Dict[str, int]:
        """Release every shot-system write whose boundary has moved on.

        The single maintenance entry point, safe to call after any mutation:
        boundary samples follow their bound (or are cleaned up), then gap
        holds are released and re-applied at the current seams.

        Returns:
            ``{"keys_moved", "keys_removed", "holds"}`` counts.
        """
        moved, removed = self._reconcile_boundary_keys()
        self._enforce_gap_holds()
        return {
            "keys_moved": moved,
            "keys_removed": removed,
            "holds": self.ledger.step_count,
        }

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
        self.ripple_downstream(shot_id, old_end, delta)
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
        prior_start = shot.start
        prior_end = shot.end
        shot.start = min(shot.start, new_start)
        shot.end = max(shot.end, new_end)

        # Ripple upstream shots by the change at the head
        head_delta = shot.start - prior_start
        if abs(head_delta) > 1e-6:
            self.ripple_upstream(shot_id, prior_start, head_delta)

        # Ripple downstream shots by the change at the tail
        delta = shot.end - prior_end
        if abs(delta) > 1e-6:
            self.ripple_downstream(shot_id, prior_end, delta)
        self._enforce_gap_holds()
        self.store.mark_dirty()

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
        self.ripple_downstream(shot_id, old_end, delta)
        self._enforce_gap_holds()
        self.store.mark_dirty()

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
        # Same inversion normalization as resize_shot_bounds — inverted
        # bounds would store start > end and hand scaleKey an inverted
        # target range.
        if new_end < new_start:
            new_start, new_end = new_end, new_start
        old_start, old_end = shot.start, shot.end
        if abs(new_start - old_start) < 1e-6 and abs(new_end - old_end) < 1e-6:
            return

        # Scale keyframes within this shot
        for obj in shot.objects:
            self.scale_object_keys(obj, old_start, old_end, new_start, new_end)
        shot.start = new_start
        shot.end = new_end

        # Shift downstream shots by however much the tail moved
        tail_delta = new_end - old_end
        if abs(tail_delta) > 1e-6:
            self.ripple_downstream(shot_id, old_end, tail_delta)

        # Shift upstream shots by however much the head moved
        head_delta = new_start - old_start
        if abs(head_delta) > 1e-6:
            self.ripple_upstream(shot_id, old_start, head_delta)

        if _enforce:
            # reconcile, not just enforce: this moved a shot BOUND, which
            # is exactly when a boundary sample the system created has to
            # follow it or be cleaned up.
            self.reconcile_system_edits()
        self.store.mark_dirty()

    def resize_shot_bounds(
        self,
        shot_id: int,
        new_start: float,
        new_end: float,
        _enforce: bool = True,
    ) -> None:
        """Move a shot's boundaries to ``[new_start, new_end]`` WITHOUT
        touching its keyframes, rippling neighbours by the edge deltas.

        The counterpart to :meth:`resize_shot`: same envelope bookkeeping,
        but the shot's own content stays exactly where the animator put it.
        Dragging a shot edge means "this shot covers a different span",
        which is a far more common intent than retiming everything inside
        it — retiming is the Shift-modified gesture.

        Because content is left alone, a shrink can leave keys outside the
        new bounds.  They are not deleted; they simply stop being counted
        as this shot's content until a boundary covers them again.

        EVERY edge move ripples the neighbours on that side by the same
        delta, so a gap keeps its width unless it is the thing being
        dragged: growing pushes them away, shrinking pulls them in behind
        the bound.  A shrink used to leave them where they were, which
        silently widened the adjacent gap on every resize — the one place
        a gap changed width without anyone asking it to.

        The ripple runs BEFORE the pivot's bounds are written, and that
        order is load-bearing in BOTH directions: a neighbour's move window
        is bounded by the pivot's boundary, so while that boundary is still
        the OLD one the window cannot reach the keys a shrink is about to
        strand.  Write the new bounds first and the window widens over them:
        measured on a two-shot scene sharing one curve, shrinking B's head
        from 60 to 80 swept B's own stranded key at 70 along with the
        upstream ripple, to 90.  Landing positions are unaffected — the
        neighbour ends one gap-width from the pivot's NEW bound either way,
        so a ripple can never run it onto the pivot.

        Parameters:
            shot_id: ID of the shot to resize.
            new_start: Desired start frame.
            new_end: Desired end frame.
            _enforce: If True (default), call :meth:`_enforce_gap_holds`
                after the operation.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        if new_end < new_start:
            new_start, new_end = new_end, new_start
        old_start, old_end = shot.start, shot.end
        if abs(new_start - old_start) < 1e-6 and abs(new_end - old_end) < 1e-6:
            return

        tail_delta = new_end - old_end
        head_delta = new_start - old_start

        # Ripple BEFORE the pivot's bounds are written, in both directions
        # (see the docstring).
        if abs(tail_delta) > 1e-6:
            self.ripple_downstream(shot_id, old_end, tail_delta)
        if abs(head_delta) > 1e-6:
            self.ripple_upstream(shot_id, old_start, head_delta)

        shot.start = new_start
        shot.end = new_end

        if _enforce:
            # reconcile, not just enforce: this moved a shot BOUND, which
            # is exactly when a boundary sample the system created has to
            # follow it or be cleaned up.
            self.reconcile_system_edits()
        self.store.mark_dirty()

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

        new_start = self._clamp_slide_start(
            shot, new_start, "downstream" if ripple else None
        )
        delta = new_start - shot.start
        if abs(delta) < 1e-6:
            return

        old_end = shot.end

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch():
            # Vacate the destination side first for forward moves (see
            # slide_shot) so the pivot's keys can't be double-shifted.
            if ripple and delta > 0:
                self.ripple_downstream(shot_id, old_end, delta)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                if ripple:
                    self.ripple_downstream(shot_id, old_end, delta)
        # reconcile, not just enforce: this moved a shot BOUND, which is exactly
        # when a boundary sample the system created has to follow it or be
        # cleaned up.
        self.reconcile_system_edits()
        self.store.mark_dirty()

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
        if self.shot_by_id(shot_id) is None:
            raise ValueError(f"No shot with id {shot_id}")

        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        # Routed through the planner rather than a local park/land loop.
        # The hand-rolled version moved each shot's keys over the window
        # ``[shot.start, shot.end]``, which loses anything sitting in the
        # trailing gap (fade tails) and re-derives the collision ordering
        # that ``_finalize_plan`` already solves.  The planner also honours
        # locked gaps by ADJACENCY, so a lock only survives between shots
        # that stay neighbours.
        plan = ShotPlanner.plan_reorder(self.store, shot_id, target_pos, self.store.gap)
        if not plan.sequence and not plan.parked:
            return
        self._apply_plan(plan)
        self._enforce_gap_holds()
        self.store.mark_dirty()

    def insert_shot(
        self,
        name: str,
        duration: float,
        after_shot_id: Optional[int] = None,
        at_position: Optional[int] = None,
        gap: Optional[float] = None,
        objects: Optional[List[str]] = None,
        description: str = "",
    ) -> ShotBlock:
        """Create a shot BETWEEN existing shots, pushing later ones downstream.

        Appending was the only way to add a shot, so making room in the
        middle meant hand-rippling every following shot.  This opens the
        space first — every shot at or after the insertion point (and its
        keyframes and audio) moves by ``duration + gap`` — then defines the
        new shot in the hole.

        Parameters:
            name: Human-readable label.
            duration: Length of the new shot in frames.
            after_shot_id: Insert directly after this shot.  ``None`` with
                *at_position* unset appends at the end.
            at_position: 1-based slot the new shot should occupy, as an
                alternative to *after_shot_id* (1 = before every shot).
            gap: Frames between the preceding shot's content and the new
                shot (defaults to the store's gap).  Downstream shots ripple
                rigidly by ``duration + gap``, so the spacing between the
                new shot and its follower stays whatever the preceding↔
                follower gap was; at position 1 there is no preceding shot
                and the gap falls after the new shot instead.
            objects: Transform names to seed the shot with.
            description: Optional description.

        Returns:
            The newly created :class:`ShotBlock`.

        Raises:
            ValueError: If *after_shot_id* does not exist.
        """
        gap = self.store.gap if gap is None else gap
        shots = self.sorted_shots()

        if after_shot_id is not None:
            idx = next(
                (i for i, s in enumerate(shots) if s.shot_id == after_shot_id), None
            )
            if idx is None:
                raise ValueError(f"No shot with id {after_shot_id}")
            insert_idx = idx + 1
        elif at_position is not None:
            insert_idx = max(0, min(int(at_position) - 1, len(shots)))
        else:
            insert_idx = len(shots)

        if not shots:
            start = self.store.snap(1.0)
        elif insert_idx == 0:
            # Before everything: keep the timeline's existing head frame and
            # push the whole sequence out of the way.
            start = shots[0].start
        elif insert_idx == len(shots):
            # Appending after the LAST shot: its trailing envelope content
            # (fade tails past .end, trailing audio — the +INF-envelope
            # content every ripple elsewhere protects) must not be built
            # over.
            prev = shots[-1]
            start = self.store.snap(
                max(prev.end, self._trailing_content_extent(prev)) + gap
            )
        else:
            start = self.store.snap(shots[insert_idx - 1].end + gap)

        new_end = self.store.snap(start + duration)

        # Open the hole before the shot exists, so the ripple can't pick up
        # the new shot as one of the shots it should move.  A pivot id no
        # shot owns means "shift everything at or after the frame".
        if insert_idx < len(shots):
            delta = (new_end - start) + gap
            if abs(delta) > 1e-6:
                from mayatk.anim_utils.shots._shot_plan import ShotPlanner

                plan = ShotPlanner.plan_ripple_downstream(
                    self.store, -1, shots[insert_idx].start, delta
                )
                self._apply_plan(plan)

        block = self.define_shot(
            name=name,
            start=start,
            end=new_end,
            objects=objects if objects is not None else [],
            description=description,
        )
        self._enforce_gap_holds()
        self.store.mark_dirty()
        return block

    def _trailing_content_extent(self, shot: ShotBlock) -> float:
        """Last frame of *shot*'s content past its end (keys and audio).

        Keys owned by OTHER shots are excluded (shared objects), matching
        the outer-content probe in :meth:`fit_shot_to_content`.  Audio past
        the last shot's end has no other owner, so every trailing event
        counts.  Returns ``shot.end`` when nothing trails.
        """
        extent = shot.end
        if cmds is None:
            return extent
        other_spans = [
            (s.start - 1e-6, s.end + 1e-6)
            for s in self.store.shots
            if s.shot_id != shot.shot_id
        ]

        def _owned_elsewhere(t: float) -> bool:
            return any(lo <= t <= hi for lo, hi in other_spans)

        for obj in self._shot_nodes(shot):
            for t in cmds.keyframe(obj, q=True) or []:
                if t > extent and not _owned_elsewhere(t):
                    extent = t
        for events in self._read_all_audio_events().values():
            for ev_start, ev_stop in events:
                ev_end = ev_stop if ev_stop is not None else ev_start
                if ev_end > extent:
                    extent = ev_end
        return extent

    # ---- shot lifecycle (delete / merge / split / pad) --------------------

    def _shot_envelope(self, shot_id: int) -> Optional[tuple]:
        """``(lo, hi, lo_open, hi_closed)`` — the key window a shot owns.

        The planner's fencepost rule, resolved for one shot, so every
        lifecycle operation here reads the same window the movers write.
        """
        shots = self.sorted_shots()
        idx = next((i for i, s in enumerate(shots) if s.shot_id == shot_id), None)
        if idx is None:
            return None
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        return ShotPlanner.envelope_for(shots, idx)

    def _unique_shot_name(self, base: str) -> str:
        """*base*, or the first ``base_2``, ``base_3``... no shot is using."""
        taken = {s.name for s in self.store.shots}
        if base not in taken:
            return base
        n = 2
        while f"{base}_{n}" in taken:
            n += 1
        return f"{base}_{n}"

    def _cut_shot_content(self, shot_id: int) -> int:
        """Delete every key inside *shot_id*'s owned window.

        The window comes from :meth:`_shot_envelope`, so a sample shared with
        a contiguous NEIGHBOUR stays with the neighbour that owns it —
        deleting a shot must not take the previous shot's closing pose with
        it.  Audio events are out of scope: they are a separate keyed store
        with no per-event delete, and silently clearing whole tracks would
        take more than the shot.

        Returns:
            The number of curves keys were cut from.
        """
        if cmds is None:
            return 0
        shot = self.shot_by_id(shot_id)
        env = self._shot_envelope(shot_id)
        if shot is None or env is None:
            return 0
        lo, hi, lo_open, hi_closed = env
        eps = _BATCH_MOVE_EPS
        # The LAST shot's envelope runs to +INF so its trailing content (fade
        # tails past ``end``) belongs to it.  That is the right ownership for a
        # delete too, but the sentinel itself must not reach ``cutKey`` -- cap
        # it at the curve-space bound the audio shifter uses for the same
        # reason.
        if hi >= _PLAN_INF:
            hi = lo + 1.0e7
        window = (
            lo + eps if lo_open else lo - eps,
            hi + eps if hi_closed else hi - eps,
        )
        from mayatk.anim_utils._anim_utils import AnimUtils

        names = self._shot_nodes(shot)
        curves = AnimUtils.objects_to_curves(names, as_strings=True) if names else []
        cut = 0
        led = self.ledger
        for crv in sorted(set(curves or [])):
            if not cmds.keyframe(crv, q=True, time=window):
                continue
            try:
                cmds.cutKey(crv, time=window, clear=True)
            except RuntimeError:
                continue  # locked or referenced curve — leave it as it was
            cut += 1
            # Whatever the system claimed in there went with the keys.
            for t in led.step_times(crv):
                if window[0] <= t <= window[1]:
                    led.release_step(crv, t)
            for t in led.key_times(crv):
                if window[0] <= t <= window[1]:
                    led.release_key(crv, t)
        return cut

    def delete_shot(
        self,
        shot_id: int,
        delete_contents: bool = True,
        close_gap: bool = True,
    ) -> Dict[str, Any]:
        """Remove a shot — by default with its keys, and closing up behind it.

        Removing only the RECORD leaves the shot's animation orphaned in the
        middle of the timeline and a hole where the shot was, which is almost
        never what "delete this shot" means.  The default therefore cuts the
        shot's own content (:meth:`_cut_shot_content`) and slides everything
        downstream back by the span the shot occupied — its own range plus
        the gap that followed it — so the next shot lands where this one
        started.

        Both halves are opt-out for the caller that really does want just the
        record gone (``delete_contents=False``) or the timeline left alone
        (``close_gap=False``).

        Parameters:
            shot_id: The shot to remove.
            delete_contents: Cut the keys the shot owns.
            close_gap: Ripple later shots upstream into the vacated span.

        Returns:
            ``{"curves_cut", "closed", "name"}`` — curves the cut reached,
            frames the timeline closed by, and the removed shot's name.

        Raises:
            ValueError: If *shot_id* does not exist.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        shots = self.sorted_shots()
        idx = next(i for i, s in enumerate(shots) if s.shot_id == shot_id)
        nxt = shots[idx + 1] if idx + 1 < len(shots) else None
        # The span the shot occupies is its range PLUS the gap after it: the
        # next shot slides into both, or the hole simply moves downstream.
        span_end = nxt.start if nxt is not None else shot.end
        vacated = max(0.0, span_end - shot.start)
        name = shot.name

        curves_cut = self._cut_shot_content(shot_id) if delete_contents else 0

        self.ledger.disown_shot(shot_id)
        self.store.remove_shot(shot_id)

        closed = 0.0
        if close_gap and nxt is not None and vacated > 1e-6:
            from mayatk.anim_utils.shots._shot_plan import ShotPlanner

            # Pivot -1: no shot is exempt, everything at or after the vacated
            # span comes back by its width.  The shot's record is already gone
            # so the plan cannot pick it up as one of the shots to move.
            plan = ShotPlanner.plan_ripple_downstream(
                self.store, -1, span_end, -vacated
            )
            if plan.sequence or plan.parked:
                self._apply_plan(plan)
                closed = vacated

        self.reconcile_system_edits()
        self.store.mark_dirty()
        return {"curves_cut": curves_cut, "closed": closed, "name": name}

    def merge_shots(self, shot_ids: List[int], name: Optional[str] = None) -> ShotBlock:
        """Fuse two or more shots into one spanning all of them.

        The earliest shot is kept and grown to the union range; the others are
        removed and their objects folded in.  Nothing MOVES — a merge is a
        statement about how the timeline is divided, not about where content
        sits — so any gap between the merged shots becomes ordinary empty
        space inside the result.  The holds that were guarding those gaps stop
        being seams and are released by :meth:`reconcile_system_edits`, which
        is exactly right: there is no longer a cut there.

        Parameters:
            shot_ids: Two or more ids.  Unknown ids are ignored; order does
                not matter (the earliest START wins).
            name: Name for the merged shot.  Defaults to the keeper's.

        Returns:
            The surviving :class:`ShotBlock`.

        Raises:
            ValueError: If fewer than two of *shot_ids* resolve to shots.
        """
        shots = [s for s in (self.shot_by_id(i) for i in shot_ids) if s is not None]
        if len(shots) < 2:
            raise ValueError("merge_shots needs at least two existing shots")
        shots.sort(key=lambda s: (s.start, s.shot_id))
        keeper = shots[0]

        new_start = min(s.start for s in shots)
        new_end = max(s.end for s in shots)
        objects: List[str] = []
        for s in shots:  # union, first-seen order, so track order is stable
            for obj in s.objects:
                if obj not in objects:
                    objects.append(obj)
        notes = [s.description for s in shots if s.description]

        with self.store.batch_update():
            for s in shots[1:]:
                self.ledger.disown_shot(s.shot_id)
                self.store.remove_shot(s.shot_id)
            self.store.update_shot(
                keeper.shot_id,
                name=name or keeper.name,
                start=new_start,
                end=new_end,
                objects=objects,
                description=" / ".join(notes),
            )

        self.reconcile_system_edits()
        self.store.mark_dirty()
        return self.shot_by_id(keeper.shot_id)

    def split_shot(
        self,
        shot_id: int,
        at_frame: float,
        name: Optional[str] = None,
        gap: float = 0.0,
    ) -> ShotBlock:
        """Cut a shot in two at *at_frame*, leaving its content where it is.

        The head keeps the original record (name, description, id) and ends at
        the cut; the tail is a new shot from the cut to the original end.  The
        two are contiguous and therefore SHARE the sample on the cut frame,
        which is the same fencepost convention every other operation uses.

        With ``gap`` the tail (and everything after it) ripples downstream by
        that many frames, so the split lands as a real cut with room between
        the halves rather than an invisible division.

        Membership is recomputed per side from the keys actually there, so
        neither half claims objects that only animate in the other.

        Parameters:
            shot_id: The shot to split.
            at_frame: Frame to cut on.  Must lie strictly inside the shot.
            name: Name for the tail.  Defaults to a unique ``<name>_2``.
            gap: Frames to open between the halves (0 = contiguous).

        Returns:
            The newly created tail :class:`ShotBlock`.

        Raises:
            ValueError: If *shot_id* does not exist, or *at_frame* is not
                strictly inside it (a cut on a bound divides nothing).
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        at = self.store.snap(float(at_frame))
        if not (shot.start + 1e-6 < at < shot.end - 1e-6):
            raise ValueError(
                f"Split frame {at:g} is not inside {shot.name} "
                f"[{shot.start:g}-{shot.end:g}]"
            )

        tail_end = shot.end
        tail_name = name or self._unique_shot_name(f"{shot.name}_2")

        self.store.update_shot(shot_id, end=at)
        # The tail INHERITS the shot's object list and is then narrowed to
        # what actually animates in it.  Seeding it empty instead makes
        # ``collect_object_segments`` fall back to a scene-wide probe for
        # keyed transforms, which adopts objects this shot never claimed.
        tail = self.define_shot(
            name=tail_name,
            start=at,
            end=tail_end,
            objects=list(shot.objects),
            description=shot.description,
        )
        self._recompute_shot_objects(shot_id)
        self._recompute_shot_objects(tail.shot_id)

        gap = float(gap)
        if abs(gap) > 1e-6:
            from mayatk.anim_utils.shots._shot_plan import ShotPlanner

            plan = ShotPlanner.plan_ripple_downstream(self.store, -1, at, gap)
            if plan.sequence or plan.parked:
                self._apply_plan(plan)

        self.reconcile_system_edits()
        self.store.mark_dirty()
        return self.shot_by_id(tail.shot_id)

    def _leading_room(self, shot_id: int) -> float:
        """Empty frames between a shot's start and its first piece of content.

        Zero when the shot is empty — there is no room to reclaim from a shot
        that holds nothing, and treating its whole span as slack would let a
        pad silently resize it to a point.
        """
        sequences = self.collect_shot_sequences(shot_id)
        if not sequences:
            return 0.0
        shot = self.shot_by_id(shot_id)
        return max(0.0, min(s["start"] for s in sequences) - shot.start)

    def add_shot_space(
        self, shot_id: int, frames: float, edge: str = "leading"
    ) -> tuple:
        """Insert empty room at a shot's head and/or tail, rippling downstream.

        Both edges open room *forward in time* — the shot's start is an
        anchor, never something padding drags backwards:

        * ``"leading"`` — the start stays exactly where it is and everything
          from it onward shifts later by *frames*: this shot's own keys and
          audio, its end, and every downstream shot.  The new room lands at
          the head, in front of the content.
        * ``"trailing"`` — the end moves later by *frames* and the downstream
          shots follow.  The shot's own content stays put; the new room lands
          behind it.
        * ``"both"`` — each of the above, so the shot grows by ``2 * frames``
          and its content sits *frames* further along.

        Spacing between shots is preserved throughout, so the padding is
        genuinely new room rather than an existing gap being eaten.

        A negative *frames* removes that much room, which is how the same
        control does both directions.

        Parameters:
            shot_id: The shot to pad.
            frames: Frames of room to add (negative removes).
            edge: ``"leading"``, ``"trailing"`` or ``"both"``.

        Returns:
            ``(head_delta, tail_delta)`` — how far each bound actually moved.
            The head delta is always 0 for a leading pad: that is the point.

        Raises:
            ValueError: If *shot_id* does not exist.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")
        frames = float(frames)
        if abs(frames) < 1e-6:
            return 0.0, 0.0

        head = frames if edge in ("leading", "both") else 0.0
        tail = frames if edge in ("trailing", "both") else 0.0
        if head == 0.0 and tail == 0.0:
            return 0.0, 0.0

        old_start, old_end = shot.start, shot.end
        if head < 0:
            # Removing head room pulls the content back toward the anchored
            # start, so it may only reclaim room that is actually EMPTY --
            # past that it would drag keys out through the head and into the
            # upstream gap, which is a delete dressed up as a pad.
            head = -min(-head, self._leading_room(shot_id))
            if abs(head) < 1e-6 and abs(tail) < 1e-6:
                return 0.0, 0.0
        # A shot may not be padded into nothing; both ends push the tail out,
        # so the guard is on the end alone (removing room is the negative
        # -frames case).
        if self.store.snap(old_end + head + tail) <= old_start:
            return 0.0, 0.0

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch():
            if abs(head) > 1e-6:
                # Slide the shot bodily downstream, then put the start back:
                # the content and every following shot end up *frames* later
                # while the head holds, which is exactly "empty room at the
                # front".  ``slide_shot`` owns the ordering that keeps the
                # pivot's keys out of a neighbour's not-yet-read envelope.
                self.slide_shot(
                    shot_id, old_start + head, direction="downstream", _enforce=False
                )
                shot.start = old_start
            if abs(tail) > 1e-6:
                pre_tail_end = shot.end
                shot.end = self.store.snap(pre_tail_end + tail)
                # Ripple by what the END ACTUALLY moved rather than by the
                # amount asked for -- the same value ``fit_shot_to_content``
                # passes.  The plan snaps its own destinations, so the two
                # agree today; deriving it from the bound is what keeps them
                # agreeing if either side's rounding ever changes.
                self.ripple_downstream(shot_id, pre_tail_end, shot.end - pre_tail_end)

        head_delta = shot.start - old_start
        tail_delta = shot.end - old_end
        # Holds are deliberately left to the reconcile below rather than
        # enforced per step: the head slide is asked NOT to enforce so the
        # seams are read once, after the start has been put back.
        self.reconcile_system_edits()
        self.store.mark_dirty()
        return head_delta, tail_delta

    # ---- timing redistribution -------------------------------------------

    def _backfill_envelope_membership(self, plan) -> bool:
        """Give every moving shot the objects actually keyed in its envelope.

        :class:`ShotApply` shifts ``shot.objects`` within ``[env_start,
        env_end)``, so an object keyed inside that window but missing from
        the list is **left behind**: the shot moves and part of its
        animation does not, landing inside a neighbouring shot.

        Membership goes stale for ordinary reasons the user cannot see
        before the move — a renamed rig leaves entries nothing resolves
        (reconciliation keeps them, inert, rather than destroying the
        record), and an object animated after the shots were authored
        belongs to no shot at all.  :meth:`_find_keyed_transforms` already
        defines membership as "has keys in range" for exactly this reason;
        this applies that rule at the moment it matters.

        Runs against the plan's own envelopes, so what is collected is
        precisely what :class:`ShotApply` is about to move — no second,
        drifting definition of a shot's window.

        Returns ``True`` if any shot gained an object.
        """
        targets = [
            (sid, mv)
            for sid, mv in plan.moves.items()
            if mv.moves or sid in plan.parked
        ]
        if not targets:
            return False
        keyed = self._keyed_transform_times()
        changed = False
        for shot_id, move in targets:
            shot = self.shot_by_id(shot_id)
            if shot is None:
                continue
            if self._adopt_keyed_objects(
                shot,
                move.env_start,
                move.env_end,
                keyed,
                lo_open=move.env_lo_open,
                hi_closed=move.env_hi_closed,
            ):
                changed = True
        return changed

    @staticmethod
    def _keyed_transform_times() -> dict:
        """Map every unambiguous transform's long path to its key times.

        Standard transform/visibility channels only (the shared
        :meth:`Detection._map_standard_curves_to_transforms` rule), so
        marker/trigger attributes never make an object look like content.
        """
        import maya.cmds as cmds

        transform_curves = Detection._map_standard_curves_to_transforms()
        if not transform_curves:
            return {}
        keyed: dict = {}
        for xform, crvs in transform_curves.items():
            matches = cmds.ls(xform, long=True) or []
            if len(matches) != 1:
                # Defensive.  Measured: the names reaching here come from
                # listConnections(plugs=True), which Maya returns in
                # SHORTEST-UNIQUE form — two objects sharing a leaf name
                # arrive as `gA|dupe` / `gB|dupe` and each resolves to one
                # node — so duplicates are adopted correctly rather than
                # skipped.  If a genuinely ambiguous name ever does arrive,
                # never guess which node a shot owns.
                continue
            # One query for the transform's whole curve set: attribution is
            # per transform, which is all this needs, so a per-curve loop
            # would cost ~14x the commands for the same answer.
            times = cmds.keyframe(crvs, q=True, timeChange=True) or []
            if times:
                keyed[matches[0]] = times
        return keyed

    def _adopt_keyed_objects(
        self,
        shot,
        lo: float,
        hi: float,
        keyed=None,
        lo_open: bool = False,
        hi_closed: bool = False,
        eps: float = _BATCH_MOVE_EPS,
    ) -> bool:
        """Add to *shot* the objects keyed in the window it is about to move.

        The caller supplies the window its own writer will move — bounds,
        flags and tolerance alike — so this never invents a second
        definition of what a shot covers, and can never list an object whose
        key the writer then leaves behind.

        The selection rule is :meth:`ShotPlanner.objects_to_adopt`, shared
        with blendertk so the two cannot drift; only discovery and name
        resolution are Maya-specific.  No ownership exemption is needed:
        the window's fencepost flags already partition the timeline, so a
        key on a shared sample is inside exactly one shot's window.

        Returns ``True`` if the shot gained an object.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        if keyed is None:
            keyed = self._keyed_transform_times()
        if not keyed:
            return False
        owned = set()
        for name in self._shot_nodes(shot):
            owned.update(cmds.ls(name, long=True) or [])

        add = ShotPlanner.objects_to_adopt(
            keyed, owned, lo, hi, lo_open=lo_open, hi_closed=hi_closed, eps=eps
        )
        if not add:
            return False
        shot.objects = sorted(set(shot.objects) | set(add))
        self.store.update_shot(shot.shot_id, objects=shot.objects)
        return True

    def _plan_curves(self, plan) -> dict:
        """``{curve: [key time, ...]}`` for every curve *plan* will move."""
        import maya.cmds as cmds
        from mayatk.anim_utils._anim_utils import AnimUtils

        names: set = set()
        for shot_id, move in plan.moves.items():
            if not move.moves:
                continue
            shot = self.shot_by_id(shot_id)
            if shot is not None:
                names.update(self._shot_nodes(shot))
        if not names:
            return {}
        curves = AnimUtils.objects_to_curves(sorted(names), as_strings=True) or []
        out: dict = {}
        for crv in sorted(set(curves)):
            times = cmds.keyframe(crv, q=True, timeChange=True) or []
            if times:
                out[crv] = sorted(times)
        return out

    def _reconcile_boundaries(self, plan, retimes=()):
        """Keep fencepost samples whole across boundaries *plan* changes.

        *retimes* names the gaps whose content a later stage will RESCALE into
        a new width. Keys strictly inside one are excluded from the collision
        analysis below, because the rescale places them strictly inside the
        NEW gap -- a span disjoint from every shot -- so they cannot land on a
        shot's sample whatever the plan does to the shots. Predicting them from
        where they sit right now instead reports a collision with the very
        content they are about to make room for: a shot's opening pose moving
        onto a gap key that has not been retimed yet, which refused a respace
        that had nothing wrong with it.

        Contiguous shots share one sample — the preceding shot's closing
        pose IS the following shot's opening pose, on the same frame.  A
        plan that changes a gap therefore has to split that sample in two
        or merge two into one, and neither happens by itself:

        * **Split** (gap opened).  The sample stays with the preceding shot,
          leaving the following shot opening on nothing.  Its value is
          captured here, before anything moves, and re-keyed at that shot's
          new start once the moves are done — so both shots keep a
          fencepost and the following shot's first segment keeps its
          timing.  Only curves the following shot actually animates past
          the boundary get a copy; a pose that was never its own is not
          invented for it.
        * **Merge** (gap collapsed).  Two samples converge on one frame.
          Maya would neither refuse nor overwrite — it stacks a duplicate a
          fraction of a frame away, and the pair then travels together
          forever — so the loser is cut here, before the move, leaving the
          destination clear.  When the two poses disagree the merge is
          lossy no matter who wins, so the whole operation is refused
          (:class:`ShotBoundaryConflict`) before it writes anything.

        Assumes membership is already complete (the back-fill runs
        first): a collision is predicted for every key inside a moving
        window, and that only matches what the writer does because the
        writer moves each shot's OWN objects and every object keyed in
        the window has just been adopted into it.

        Returns a callable to invoke after the plan has been applied.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils._anim_utils import AnimUtils
        from mayatk.anim_utils.shots._shot_plan import (
            ShotBoundaryConflict,
            ShotPlanner,
        )

        def _noop():
            return None

        windows = ShotPlanner.move_windows(plan)
        if not windows:
            return _noop

        # ---- merges: detect every conflict BEFORE cutting anything -------
        # Open intervals, so a shot's own bookend ON a bound is still analysed;
        # only what lives strictly between two shots is deferred to the retime.
        deferred = [(g.lo, g.hi) for g in retimes]

        def retimed(t: float) -> bool:
            return any(lo < t < hi for lo, hi in deferred)

        conflicts: list = []
        losers: list = []
        for crv, times in self._plan_curves(plan).items():
            times = [t for t in times if not retimed(t)]
            for dest, movers, still in ShotPlanner.key_collisions(windows, times):
                vals = {}
                for t in movers + still:
                    got = (
                        cmds.keyframe(crv, q=True, time=(t, t), valueChange=True) or []
                    )
                    if got:
                        vals[t] = float(got[0])
                if not vals:
                    continue
                if max(vals.values()) - min(vals.values()) > _POSE_TOL:
                    conflicts.append((crv, float(dest), sorted(vals.values())))
                else:  # lossless: keep one mover, clear what it lands on
                    losers.extend((crv, t) for t in movers[1:] + still)
        if conflicts:
            raise ShotBoundaryConflict(conflicts)

        # ---- splits: capture while the shared sample still exists ---------
        captures: list = []
        for prev_id, shot_id, boundary, new_start in ShotPlanner.boundary_splits(
            self.store, plan
        ):
            shot = self.shot_by_id(shot_id)
            prev_shot = self.shot_by_id(prev_id)
            if shot is None or prev_shot is None:
                continue
            names = self._shot_nodes(shot)
            curves = (
                AnimUtils.objects_to_curves(names, as_strings=True) if names else []
            )
            for crv in sorted(set(curves or [])):
                # Find the key BY TOLERANCE, then work from its own time: a
                # key sits where the last move left it, which is the shot
                # bound plus float noise, so an exact-frame query would miss
                # it and silently skip the split.
                window = (
                    boundary - _BATCH_MOVE_EPS,
                    boundary + _BATCH_MOVE_EPS,
                )
                found = cmds.keyframe(crv, q=True, time=window, timeChange=True) or []
                if not found:
                    continue  # this curve has no pose on the shared sample
                key_t = float(found[0])
                at = (
                    cmds.keyframe(crv, q=True, time=(key_t, key_t), valueChange=True)
                    or []
                )
                if not at:
                    continue
                interior = (
                    cmds.keyframe(
                        crv,
                        q=True,
                        time=(key_t + _BATCH_MOVE_EPS, shot.end + _BATCH_MOVE_EPS),
                    )
                    or []
                )
                if not interior:
                    continue  # the following shot does not animate this curve
                tangents = (
                    cmds.keyTangent(
                        crv, q=True, time=(key_t, key_t), itt=True, ott=True
                    )
                    or []
                )
                # Shared only where the PRECEDING shot animates this curve
                # too.  Where it does not, the sample was never a shared
                # fencepost — it is the following shot's opening pose alone,
                # so it travels with that shot instead of being duplicated
                # and left behind on a curve its neighbour has no stake in.
                shared = bool(
                    cmds.keyframe(
                        crv,
                        q=True,
                        time=(
                            prev_shot.start - _BATCH_MOVE_EPS,
                            key_t - _BATCH_MOVE_EPS,
                        ),
                    )
                )
                captures.append(
                    (crv, float(new_start), float(at[0]), tuple(tangents), shared)
                )
                if not shared:
                    losers.append((crv, key_t))

        # Never cut a curve down to nothing: Maya deletes a keyless animCurve
        # node, which would take the connection with it.
        for crv, t in losers:
            if (cmds.keyframe(crv, q=True, keyframeCount=True) or 0) > 1:
                try:
                    cmds.cutKey(crv, time=(t, t), clear=True)
                except RuntimeError:
                    pass  # locked or referenced curve — leave it as it was

        if not captures:
            return _noop

        # Which shot each capture is opening, so the sample it creates can be
        # claimed FOR that shot's start bound and follow it from then on.
        owners = {
            float(new_start): shot_id
            for _prev, shot_id, _boundary, new_start in ShotPlanner.boundary_splits(
                self.store, plan
            )
        }
        led = self.ledger

        def _finish():
            for crv, frame, value, tangents, _shared in captures:
                if not cmds.objExists(crv):
                    continue
                occupied = cmds.keyframe(
                    crv,
                    q=True,
                    time=(frame - _BATCH_MOVE_EPS, frame + _BATCH_MOVE_EPS),
                    keyframeCount=True,
                )
                if occupied:
                    continue  # something already landed here; leave it alone
                try:
                    cmds.setKeyframe(crv, time=(frame,), value=value)
                    if len(tangents) >= 2:
                        cmds.keyTangent(
                            crv,
                            e=True,
                            time=(frame, frame),
                            itt=tangents[0],
                            ott=tangents[1],
                        )
                except RuntimeError:
                    pass  # locked or referenced curve — the move still stands
                else:
                    led.record_key(crv, frame, owners.get(frame, -1), "start")

        return _finish

    def _apply_plan(self, plan, retime_gaps: bool = False) -> None:
        """Execute *plan*, membership completed and fenceposts reconciled.

        The single chokepoint for every whole-shot mutation (respace,
        reorder, ripple, slide) so no path can move a shot while leaving
        part of its animation behind, or split/collapse a shared sample
        without accounting for it.

        ``retime_gaps`` is opt-in, and asked for by the caller rather than
        inferred from the store, because only the caller knows whether the
        bounds it is handing over are the ones the edit STARTED from. A
        bounds-only resize writes the new bounds before planning the ripple
        that follows it, so a gap that the user sees as unchanged reads as
        changed from here -- and would be retimed on the strength of that
        misreading. :meth:`respace` is the operation whose definition is
        "change every gap", so it is the one that asks.

        With it, a move that changes any GAP's width gets two more stages,
        because a rigid move is only lossless while every gap keeps its width:

        1. Each shot is PINNED -- a key on both of its bounds, inserted
           shape-preservingly -- so nothing outside a shot can change what
           plays inside it.
        2. Each changed gap's content is RETIMED into its new width, before
           the moves where the gap shrinks and after them where it grows.

        Both are no-ops for a pure translation (every shot moving by the same
        delta keeps every gap), so ripple and slide are untouched.

        Ordering is load-bearing. The pin is lossless, so it can run before
        the boundary check; the RETIME is not, so it runs after it. A collapsed
        gap is refused (:class:`ShotBoundaryConflict`) and a refusal has to
        leave the scene evaluating exactly as it did -- pinning it does, having
        retimed half its gaps does not. Pinning first is also what lets the
        check see the conflict at all: it is the shots' opening and closing
        poses that cannot share one frame, and until the pin those poses are
        not keys.
        """
        from mayatk.anim_utils.shots._shot_apply import ShotApply
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        retimes = ShotPlanner.plan_gap_retimes(self.store, plan) if retime_gaps else []
        # ONE object set for both stages: whatever a boundary can cut, the
        # retime has to be able to reach. Resolved once, before the pin adds
        # keys, because neither stage's set depends on the other's writes.
        content = self._content_objects() if retimes and cmds is not None else []
        if content:
            # Claimed as they are inserted: a pin is the system's own sample,
            # and once the bound it pins moves the claim is what lets it be
            # moved with it (or cleaned up) instead of left behind.
            bound_owner = {}
            for shot in self.store.sorted_shots():
                bound_owner.setdefault(float(shot.start), (shot.shot_id, "start"))
                bound_owner.setdefault(float(shot.end), (shot.shot_id, "end"))
            pinned = ShotApply.pin_shot_bounds(self.store, content, report=True)
            for crv, frame in pinned:
                owner, edge = bound_owner.get(float(frame), (-1, ""))
                self.ledger.record_key(crv, frame, owner, edge)
            if pinned:
                logging.getLogger(__name__).debug(
                    "Respace: pinned %d shot-boundary key(s) so each shot's "
                    "content is its own.",
                    len(pinned),
                )

        self._backfill_envelope_membership(plan)
        finish = self._reconcile_boundaries(plan, retimes) if cmds is not None else None
        if content:
            ShotApply.retime_gaps(retimes, content, after_move=False)
        ShotApply.apply(self.store, plan)
        if finish is not None:
            finish()

        if content:
            ShotApply.retime_gaps(retimes, content, after_move=True)

    def _content_objects(self) -> list:
        """Every object a whole-shot move can reach, for the pin and the retime.

        :meth:`_keyed_transform_times` is the sequencer's OWN definition of
        content (standard transform/visibility channels, so a marker attribute
        never makes an object look animated), and this reuses it rather than
        deciding again — a second rule that drifted would pin one set and
        retime another.

        Union with what the shots claim, which covers the one case the keyed
        walk excludes by design: a shot object animated only on a non-standard
        channel. Stale entries are harmless — the resolution downstream drops
        names nothing resolves.

        Deliberately NOT the shots' object lists alone: a node no shot claims
        still has its curve cut by the shots' bounds, and only shots that MOVE
        get their membership backfilled — so a stationary shot's list is
        whatever the store happened to hold, which is what let an unmoved shot
        lose 42 of its 109 frames.
        """
        claimed = {obj for shot in self.store.shots for obj in shot.objects}
        if cmds is None:
            return sorted(claimed)
        return sorted(claimed | set(self._keyed_transform_times()))

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

        Each gap's own content is RETIMED into the gap's new width rather than
        carried rigidly with the shot before it: a gap's width is the thing a
        respace changes, so content living in one has nowhere to be carried to.
        See :meth:`_apply_plan`.

        Parameters:
            gap: Frames of gap between consecutive shots.
            start_frame: Timeline frame for the first shot.
        """
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        plan = ShotPlanner.plan_respace(self.store, gap, start_frame)
        self._apply_plan(plan, retime_gaps=True)
        self._enforce_gap_holds()

    def apply_gap(
        self,
        gap: float,
        scope: str = "all",
        shot_id: Optional[int] = None,
    ) -> bool:
        """Re-space shots so the given *gap* separates them, per *scope*.

        Parameters:
            gap: Desired gap width in frames.
            scope: ``"all"`` respaces every shot from the first shot's
                current start.  ``"start"`` moves *shot_id* so it sits
                *gap* after its predecessor; ``"end"`` moves the
                successor so it sits *gap* after *shot_id*;
                ``"start_end"`` does both.
            shot_id: Anchor shot for the scoped modes (typically the
                active shot).  Ignored for ``"all"``.

        Returns:
            ``True`` when any shot was repositioned.
        """
        sorted_s = self.sorted_shots()
        if not sorted_s:
            return False

        if scope == "all":
            self.respace(gap=gap, start_frame=sorted_s[0].start)
            return True

        if shot_id is None:
            return False
        idx = next((i for i, s in enumerate(sorted_s) if s.shot_id == shot_id), None)
        if idx is None:
            return False

        moved = False
        if scope in ("start", "start_end") and idx > 0:
            self.move_shot(shot_id, sorted_s[idx - 1].end + gap)
            moved = True
            # move_shot ripples neighbors — re-derive the ordering before
            # positioning the successor.
            sorted_s = self.sorted_shots()
            idx = next(
                (i for i, s in enumerate(sorted_s) if s.shot_id == shot_id),
                idx,
            )
        if scope in ("end", "start_end") and idx < len(sorted_s) - 1:
            self.move_shot(sorted_s[idx + 1].shot_id, sorted_s[idx].end + gap)
            moved = True
        return moved

    # ---- serialisation ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise shots and settings to a plain dict."""
        return self.store.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ShotSequencer":
        """Restore from serialised data."""
        return cls(store=ShotStore.from_dict(data))
