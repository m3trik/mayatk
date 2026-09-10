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
        require_motion: bool = True,
    ) -> List[str]:
        """Return names of all transforms that MOVE in [start, end].

        Only content attributes count (``Detection.CONTENT_ATTRS``: the
        standard transform/visibility channels and the render-effect
        channels) — any other custom attribute (e.g. ``audio_trigger``) is
        ignored so marker objects don't appear as scene content.

        Membership needs motion: at least one standard curve whose values
        vary by more than *value_tolerance* inside the range.  Keys alone
        are not content -- a baked rig carries a key on every frame of every
        shot and holds still through most of them.  Measured on a production
        assembly: 531 of the 603 memberships across 12 shots were objects
        whose keys in that shot were all flat (forty proxy joints in every
        shot), and every one of them drew a track.  Discovery used to accept
        any key in range so a hold-only object would not be stranded when
        its shot moved; the movers now carry the whole keyed content of an
        envelope whatever the list says (:meth:`_content_objects`), so that
        guarantee no longer needs the list.  ``require_motion=False`` is the
        old rule, for a caller that wants every keyed object.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils.shots._shots import Detection

        transform_curves = Detection._map_standard_curves_to_transforms()
        if not transform_curves:
            return []

        result = []
        for xform, crvs in sorted(transform_curves.items()):
            for crv in crvs:
                if require_motion:
                    hit = Detection.curve_moves_in(crv, start, end, value_tolerance)
                else:
                    hit = bool(
                        cmds.keyframe(
                            crv, q=True, time=(start, end), keyframeCount=True
                        )
                    )
                if hit:
                    result.append(xform)
                    break
        return result

    @staticmethod
    def _content_curves(node: str) -> List[str]:
        """*node*'s anim curves that drive a content channel, through blends.

        The per-node form of :meth:`_find_keyed_transforms`'s map -- one
        node's curves rather than a scene-wide one -- shared by the motion
        test and the mark scan so both read the same channels.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        curves = AnimUtils.objects_to_curves(
            [node], as_strings=True, through_blends=True
        )
        if not curves:
            return []
        standard = Detection._map_standard_curves_to_transforms(curves)
        return [crv for crvs in standard.values() for crv in crvs]

    def _animator_marks(self, curves: List[str], shot: ShotBlock) -> List[float]:
        """The keys of *curves* inside *shot* that are the animator's own marks.

        The samples the system planted for a bound are left out
        (:meth:`_animator_key_times`), and so is an unclaimed key sitting ON
        a bound that provably holds nothing (:meth:`_sample_is_redundant`)
        -- the shape a released bound sample takes.  Measured 2026-09-07 on
        "Step 9.1.1-3" [2358, 2791] of the production assembly: seven of the
        ten members drawn had exactly two keys in the shot, its own two
        bound samples, flat on every channel, and were shown as members on
        the strength of them ("confusing clutter within the shot").
        """
        eps = _BATCH_MOVE_EPS
        marks: set = set()
        for crv in curves:
            for t in self._animator_key_times(crv, (shot.start, shot.end)):
                if (
                    abs(t - shot.start) <= eps or abs(t - shot.end) <= eps
                ) and self._sample_is_redundant(crv, t, terminal=False):
                    continue
                marks.add(float(t))
        return sorted(marks)

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

        # A member that MOVES in the shot but produced no segment -- its
        # motion sits below ``motion_rate`` (a slow drift), which
        # ``SegmentKeys.collect_segments`` drops under ignore_holds=True --
        # still gets one span-of-keys track.  A member whose keys in range
        # are all flat gets none: keys are not animation, and on a baked rig
        # every object has a key in every shot (see
        # :meth:`_find_keyed_transforms`).
        if ignore_holds and nodes:
            covered = {s["obj"] for s in segments}
            for n in nodes:
                if n in covered:
                    continue
                kt = sorted(
                    set(cmds.keyframe(n, q=True, time=(shot.start, shot.end)) or [])
                )
                if not kt:
                    continue
                curves = self._content_curves(n)
                if any(
                    Detection.curve_moves_in(crv, shot.start, shot.end)
                    for crv in curves
                ):
                    segments.append(
                        {
                            "obj": n,
                            "curves": [],
                            "keyframes": kt,
                            "start": kt[0],
                            "end": kt[-1],
                            "duration": kt[-1] - kt[0],
                            "segment_range": (kt[0], kt[-1]),
                        }
                    )
                else:
                    # A FEW value-less keys of the animator's OWN are marks --
                    # typically the lone key Move to Shot just brought here --
                    # and drawn as stepped points so the move is visible where
                    # it landed (2026-09-07: "the key did not arrive").  Many
                    # flat keys are a bake, and a bake is not a track; the
                    # system's bound samples are never marks, whatever their
                    # count (see _animator_marks).
                    marks = self._animator_marks(curves, shot)
                    if 0 < len(marks) <= self.ISOLATED_KEY_LIMIT:
                        segments.extend(self._point_segment(n, t) for t in marks)
        return segments

    #: A flat member with this many keys in a shot (or fewer) shows them as
    #: stepped points; more is a bake, which draws nothing.
    ISOLATED_KEY_LIMIT = 4

    @staticmethod
    def _point_segment(obj: str, t: float) -> Dict[str, Any]:
        """The zero-length, stepped segment the widget draws as a key marker."""
        return {
            "obj": obj,
            "curves": [],
            "keyframes": [t],
            "start": t,
            "end": t,
            "duration": 0.0,
            "segment_range": (t, t),
            "is_stepped": True,
            "stepped_key_time": t,
            # A mark, not motion: drawn, but never content for a trim, a fit
            # or a membership backfill (see collect_shot_sequences).
            "marker": True,
        }

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
        # A value-less key drawn as a marker is not a sequence: counting it
        # made a flat member's key on the bound untrimmable again.
        result: List[Dict[str, Any]] = [
            {
                "kind": "anim",
                "obj": seg["obj"],
                "start": seg["start"],
                "end": seg["end"],
            }
            for seg in anim
            if not seg.get("marker")
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
            if seq.get("attr") or seq.get("times"):
                # A key selection: one attribute, and only the keys named.
                self.move_attribute_keys(
                    seq["obj"],
                    seq.get("attr"),
                    delta,
                    times=seq.get("times"),
                    window=(seq["start"], seq["end"]),
                )
            else:
                self.move_object_keys(seq["obj"], seq["start"], seq["end"], new_start)
        elif seq["kind"] == "audio":
            from mayatk.audio_utils._audio_utils import AudioUtils

            with AudioUtils.batch() as b:
                tids = AudioUtils.shift_keys_in_range(
                    seq["start"], seq["end"], delta, track_ids=[seq["obj"]]
                )
                if tids:
                    b.mark_dirty(tids)

    def _recompute_shot_objects(self, shot_id: int, only=None, keep=()) -> None:
        """Rebuild ``shot.objects`` from animation that actually lives in the shot.

        Scans every anim sequence inside the shot's frame range and keeps
        only the transforms that contribute keys.  Locked / pinned objects
        are preserved even when they have no remaining keys.  Audio is
        out of scope — audio tracks are not part of ``shot.objects``.

        *only* narrows the re-decision to those objects: every other member
        stands as it was.  A move re-decides what it moved and nothing else --
        a member keyed nowhere in the shot (manifest-authored, or pinned by
        hand before its keys existed) is not the move's to drop.  Measured on
        a production assembly: one clip moved out of an 8-member shot left it
        with 2, and the 10-member destination with 5.

        *keep* names objects this shot owns whatever the scan finds -- what a
        caller just placed here.  Membership is derived from MOTION, so a lone
        flat key (a render-effect off-state, an anim bookend) moved into a
        shot was disowned by it the moment it arrived: no track, no clip,
        nothing to show the user the key had landed.
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            return
        if cmds is None:
            return
        anim_objs = {seg["obj"] for seg in self.collect_object_segments(shot_id)}
        keep = self.store.pinned_objects | self.store.locked_objects | set(keep)
        current = set(shot.objects)
        if only is None:
            new_objs = current & (anim_objs | keep) | anim_objs
        else:
            subject = set(only)
            new_objs = (current - subject) | (subject & (anim_objs | keep))
        new_objs = sorted(new_objs)
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
        shape.  Content keeps its ORDER: a group that sat before the
        destination is the head of what is already there, one that sat after
        it is the tail.

            - From before, onto objects the destination already animates: the
              shot's time is inserted at its start (:meth:`add_shot_space`,
              leading) -- every object's content and every downstream shot
              move later by the room the block needs -- and the group lands
              at the destination start, in front.  The block carries its own
              overhang (:meth:`_absorb_gap_overhang`), and when it sat within
              a shot-gap of the destination the room is its own displacement,
              so the block and the content it joins shift as one run.
              Measured on a production assembly: the on-ramp of a highlight
              pulse sat one frame inside the previous shot, its partner in
              the gap, the rest of the train in the destination; appending
              sent the key to the far end of the shot, and moving it alone
              flipped the ramp.
            - From after, onto objects the destination already animates: the
              group lands AFTER the last of that content, separated by
              :meth:`sequence_separation`.
            - Onto objects with nothing there: anchored to the destination
              start, nothing pushed.

        Neither direction can reach outside the destination: the head case
        makes its room by insertion rather than by placing content ahead of
        the shot's start (which put it on top of the previous shot), and the
        tail case grows the shot.

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

        def content_by_obj() -> Dict[str, List[Dict[str, Any]]]:
            """The destination's sequences keyed by object, as they sit NOW."""
            by_obj: Dict[str, List[Dict[str, Any]]] = {}
            for s in self.collect_shot_sequences(dest_shot_id):
                by_obj.setdefault(s["obj"], []).append(s)
            return by_obj

        dest_seqs_by_obj = content_by_obj()

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

        # ---- 1. the head block: insert its room at the start ---------------
        # Groups that lie entirely before the destination land in front, as
        # ONE block in their own order.  Where their objects already have
        # content there, the shot's time is inserted at its start so the
        # arrivals meet empty timeline and nothing is reordered; the pad is a
        # real move of the destination's content and every downstream shot,
        # so what it carried is re-read before any landing spot is resolved.
        head_groups = {
            sid: grp
            for sid, grp in groups.items()
            if max(s["end"] for s in grp) < dest.start - 1e-6
        }
        block_min = 0.0
        if head_groups:
            head_seqs = [s for grp in head_groups.values() for s in grp]
            for s in head_seqs:
                self._absorb_gap_overhang(s, dest.start)
            block_min = min(s["start"] for s in head_seqs)
            block_max = max(s["end"] for s in head_seqs)
            if any(dest_seqs_by_obj.get(s["obj"]) for s in head_seqs):
                # The block's own displacement when it already sat within a
                # shot-gap of the destination -- it and the content it joins
                # then shift as ONE run, spacing intact -- and otherwise its
                # span plus the standard clip separation.
                room = self.store.snap(
                    block_max - block_min + min(dest.start - block_max, separation)
                )
                self.add_shot_space(dest_shot_id, room, edge="leading")
                # Everything from the destination's start onward travelled
                # with the pad -- its own content, the gap behind it, the
                # downstream shots -- and so did the tail groups' keys.
                for sid, grp in groups.items():
                    if sid in head_groups:
                        continue
                    if min(s["start"] for s in grp) >= dest.start - 1e-6:
                        for s in grp:
                            s["start"] += room
                            s["end"] += room
                dest_seqs_by_obj = content_by_obj()

        # ---- 2. resolve every landing spot BEFORE anything moves ----------
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

            if source_id in head_groups:
                # In front, keeping the block's own spacing.
                anchor = dest.start + (base - block_min)
            elif existing:
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

        # ---- 3. open the room, THEN land in it ----------------------------
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

            moved_objs = {
                seq["obj"] for seq, _s, _t in placements if seq["kind"] == "anim"
            }
            for sid in affected_shots:
                # What landed here is this shot's, motion or not.
                self._recompute_shot_objects(
                    sid,
                    only=moved_objs,
                    keep=moved_objs if sid == dest_shot_id else (),
                )

        # A safety net for anything the arithmetic could not predict (audio
        # whose carrier resolved differently, a curve that refused a move):
        # extend-to-fit is implicit, never a separate user action.  Normally a
        # no-op now, because the room was already opened to size.
        self.extend_shot_to_fit(dest_shot_id)
        # The room above was opened by writing the destination's END by hand,
        # and the keys that left changed the source shot's seam: both are
        # what the system's own samples answer to, and extend reconciles
        # only when IT moved a bound.  Measured on a production assembly: one
        # Move to Shot grew "Shot 3.4-5" 748 -> 781 and left 38 of its 39
        # claimed end samples -- stepped -- at 748, inside the shot.
        self.reconcile_system_edits()

    # ---- shot fit / trim / extend ----------------------------------------

    def fit_shot_to_content(
        self,
        shot_id: int,
        mode: str = "fit",
        edge: str = "both",
        reach: Optional[float] = None,
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

        *reach* bounds how far outside the shot the ``"extend"`` / ``"fit"``
        probe looks, in frames, and widens it to BOTH gaps: the user's
        "extend to the keys I set" gesture, whose keys sit just past either
        bound.  ``None`` (the default, the implicit auto-extend) keeps the
        envelope rule -- the trailing gap only, the leading gap belonging to
        the previous shot (see :meth:`_key_extent`).  A neighbour's span is
        never read either way.

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
        probe_outside = mode in ("extend", "fit")
        inner_start, inner_end, outer_start, outer_end, on_bound = self._key_extent(
            shot, probe_outside, reach=reach
        )

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
            # A redundant key sitting ON a bound that is moving inward goes
            # first (see _key_extent), then the shot's own samples follow the
            # shrinking bounds BEFORE the neighbours ripple onto the frames it
            # is giving up.  A GROWING edge waits for the reconcile after the
            # ripple: with carry_gap the frames it uncovers are still the
            # gap's until the ripple has moved that content away.
            self._cut_passed_bound_keys(
                on_bound, old_start, old_end, new_start, new_end
            )
            self._reconcile_pending_bounds(
                shot_id,
                new_start if head_delta > 1e-6 else old_start,
                new_end if tail_delta < -1e-6 else old_end,
            )
            shot.start = new_start
            shot.end = new_end
            # A GROWING edge ripples from the NEW bound: everything beyond
            # what the shot now covers moves, and the keys it grew over stay
            # where they are -- enclosed, which is the whole point of an
            # extend.  Rippled from the OLD bound (carry_gap's reading of "the
            # pivot's trailing gap rides"), the very keys the grow reached for
            # rode away with the neighbour and landed outside the shot again:
            # measured, extending "S0" [0, 50] over its gap key at 55 grew the
            # shot to 55 and moved the key to 60.  A SHRINKING edge still
            # ripples from the old bound -- nothing of the shot's is left
            # between the two (a trim stops at content).
            if abs(tail_delta) > 1e-6:
                self.ripple_downstream(
                    shot_id, new_end if tail_delta > 0 else old_end, tail_delta
                )
            if abs(head_delta) > 1e-6:
                self.ripple_upstream(
                    shot_id, new_start if head_delta < 0 else old_start, head_delta
                )

        # reconcile, not just enforce: this moved a shot BOUND, which is exactly
        # when a boundary sample the system created has to follow it or be
        # cleaned up.
        self.reconcile_system_edits()
        self.store.mark_dirty()
        return head_delta, tail_delta

    def _key_extent(
        self, shot: ShotBlock, probe_outside: bool, reach: Optional[float] = None
    ) -> tuple:
        """Where *shot*'s content actually sits, as the keys tell it.

        Returns ``(inner_start, inner_end, outer_start, outer_end, on_bound)``:
        the first and last animator key inside the shot's span, the same
        outside it when *probe_outside* (the shot's own envelope: the trailing
        gap up to the next shot, and the open timeline before the first
        shot), and the keys that sit ON a bound but hold nothing (below).
        Each is ``None`` when there is no such key.  The one scan behind
        :meth:`fit_shot_to_content` and the plain drag's clamp in
        :meth:`resize_shot_bounds`, so trim and drag agree on what "content"
        is.

        The outer probe reads this shot's ENVELOPE and nothing more: the
        trailing gap up to the next shot's start -- which is what the ripple
        planner moves WITH this shot -- and, only when no shot precedes it,
        the open timeline before its start.  A key anywhere else is some
        other shot's: inside a neighbour's span outright, or in a gap that
        neighbour's envelope carries.  The old probe skipped only the spans,
        so a fade tail parked in a FAR gap -- routine on a shared object --
        read as this shot's own overhang.  Measured on a production assembly:
        one 15-frame Move to Shot into "Step 3.1" [80-430] grew it to
        [66-3468], slid the source shot back 14 frames and rippled the other
        ten shots by 3038.

        Per curve, and the animator's keys only: a pin the system planted on
        the bound is not content (see :meth:`_animator_key_times`; counting
        it made a pinned end untrimmable).  Content is MOTION -- the model
        every track and member list here follows -- so a curve that never
        moves inside the probed window is a hold, and a hold's keys hold no
        bound.  The key rule protects the hold TAIL of a curve that does move
        in the shot (2026-09-03, "Step 4.1"), not a proxy joint baked flat
        across the whole timeline: forty of those, keyed every two frames,
        pinned "Step 2.1" [34, 66] to 66 while its last clip ended at 64
        (2026-09-06), and deleting the keys on the bound by hand only exposed
        the next baked key two frames back.

        ``on_bound`` is the one exception to the key rule: an UNCLAIMED key
        sitting exactly on a bound that is provably redundant
        (:meth:`_sample_is_redundant`: a flat plateau whose removal cannot
        change what plays).  Such a key is what a released bound sample
        becomes -- the system's own pin, disowned by an earlier edit and
        indistinguishable from the animator's key from then on -- and it
        pinned "Step 4.2-3" [975, 1312] to 1312 on four channels of
        ``ITA_DA2_CFG_B_LOC`` while their motion ended at 1297 (reported
        2026-09-06: "why can't I trim step 4.2-3?"; the ``ITA_DA2_CFG_A_LOC``
        twins still carried their claim at 1312).  It holds no bound, and
        :meth:`_cut_passed_bound_keys` removes it once a bound has moved past
        it, so nothing lands around it later.

        *reach* (frames) is the explicit "extend to the keys I set" probe:
        the outer window becomes BOTH gaps, each cut at *reach* from the
        bound -- the leading gap too, up to (never on) the previous shot's
        end, since the animator who set a key just before the shot means it
        for this shot whatever the ripple planner's envelope says.  A
        neighbour's own span is never read.
        """
        inner_start = inner_end = None
        outer_start = outer_end = None
        on_bound: list = []
        if not shot.objects or cmds is None:
            return inner_start, inner_end, outer_start, outer_end, on_bound
        from mayatk.anim_utils._anim_utils import AnimUtils

        ordered = self.sorted_shots()
        idx = next(i for i, s in enumerate(ordered) if s.shot_id == shot.shot_id)
        head_is_open = idx == 0
        prev_end = ordered[idx - 1].end if idx > 0 else None
        tail_ceiling = ordered[idx + 1].start if idx + 1 < len(ordered) else None
        if probe_outside:
            lo = -1e9 if head_is_open else shot.start
            hi = 1e9 if tail_ceiling is None else tail_ceiling
            if reach is not None:
                lo = max(shot.start - reach, -1e9 if prev_end is None else prev_end)
                hi = min(shot.end + reach, hi)
        else:
            lo, hi = shot.start, shot.end
        eps = _BATCH_MOVE_EPS
        curves = AnimUtils.objects_to_curves(self._shot_nodes(shot), as_strings=True)
        for crv in sorted(set(curves or [])):
            if not Detection.curve_moves_in(crv, lo, hi):
                continue
            for t in self._animator_key_times(crv):
                if shot.start <= t <= shot.end:
                    if (
                        abs(t - shot.start) <= eps or abs(t - shot.end) <= eps
                    ) and self._sample_is_redundant(crv, t):
                        on_bound.append((crv, t))
                        continue
                    inner_start = t if inner_start is None else min(inner_start, t)
                    inner_end = t if inner_end is None else max(inner_end, t)
                    continue
                if not probe_outside:
                    continue
                if t < shot.start:
                    if reach is not None:
                        # Within reach, and past the previous shot's closing
                        # sample (that key is its fencepost, not this gap's).
                        if t < lo - eps or (
                            prev_end is not None and t <= prev_end + eps
                        ):
                            continue
                    elif not head_is_open:
                        continue  # the leading gap is the previous shot's
                    outer_start = t if outer_start is None else min(outer_start, t)
                elif tail_ceiling is None or t < tail_ceiling - 1e-6:
                    if reach is not None and t > hi + eps:
                        continue
                    outer_end = t if outer_end is None else max(outer_end, t)
        return inner_start, inner_end, outer_start, outer_end, on_bound

    def _cut_passed_bound_keys(
        self, on_bound, old_start, old_end, new_start, new_end
    ) -> int:
        """Cut the redundant on-bound keys (:meth:`_key_extent`) a bound has
        just moved past, so the frames they sit on are clear before anything
        ripples onto them.  A key on a bound that did not move stays.

        Returns the number cut.
        """
        if cmds is None or not on_bound:
            return 0
        eps = _BATCH_MOVE_EPS
        cut = 0
        for crv, t in on_bound:
            passed = (abs(t - old_end) <= eps and new_end < t - eps) or (
                abs(t - old_start) <= eps and new_start > t + eps
            )
            if not passed or not cmds.objExists(crv):
                continue
            if (cmds.keyframe(crv, q=True, keyframeCount=True) or 0) <= 2:
                continue  # never below two keys (Maya deletes a keyless curve)
            try:
                cmds.cutKey(crv, time=(t - eps, t + eps), clear=True)
                cut += 1
            except RuntimeError:
                pass  # locked or referenced curve -- leave it as it was
        return cut

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

    def extend_shot_to_fit(
        self, shot_id: int, edge: str = "both", reach: Optional[float] = None
    ) -> tuple[float, float]:
        """Expand shot boundaries outward to enclose all of its sequences.

        If sequences extend past the current head or tail, the shot grows
        to cover them and neighbouring shots ripple outward.  *edge* limits
        the growth to one end; *reach* (frames) is the user's "extend to the
        keys I set" form -- keys within *reach* of either bound, in the gaps
        only (see :meth:`fit_shot_to_content`).
        """
        return self.fit_shot_to_content(shot_id, mode="extend", edge=edge, reach=reach)

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
        self.move_attribute_keys(
            obj, None, new_start - old_start, window=(old_start, old_end)
        )

    def _anim_curves_of(self, obj: str, attr: Optional[str]) -> List[str]:
        """The anim curves driving ``obj.attr``, or every one on *obj*.

        Ambiguity in *obj* is resolved toward the match that carries anim
        curves (:meth:`_disambiguate_matches`); an absent plug is no curves.
        """
        import maya.cmds as cmds

        matches = cmds.ls(obj, long=True)
        if not matches:
            return []
        obj_path = self._disambiguate_matches(matches)
        if attr:
            plug = f"{obj_path}.{attr}"
            if not cmds.objExists(plug):
                return []
            curves = cmds.listConnections(plug, type="animCurve", s=True, d=False)
        else:
            curves = cmds.listConnections(obj_path, type="animCurve", s=True, d=False)
        return list(set(curves or []))

    def _animator_key_times(self, crv: str, window=None) -> List[float]:
        """*crv*'s key times that are the animator's own.

        The samples the system itself planted for a shot bound -- the
        ledger's claimed keys -- are left out.  Planted BECAUSE a bound is
        there, such a sample follows the bound when it moves, so no scan that
        decides where content begins or ends may count it.  Measured
        2026-09-06 on the production assembly: "Step 9.1.1-3" [2430, 2863]
        would not trim because every one of its 144 curves had its last
        in-range key at 2863, its own end sample; the content ended at 2817.
        A hold the system merely STEPPED onto the animator's key is not a
        claimed key, so that key still counts.

        Parameters:
            crv: The animCurve node.
            window: Optional ``(lo, hi)`` time range to read.

        Returns:
            The unclaimed key times, in curve order.
        """
        import maya.cmds as cmds

        kwargs = {"time": tuple(window)} if window is not None else {}
        system = self.ledger.key_times(crv)
        return [
            t
            for t in cmds.keyframe(crv, q=True, **kwargs) or []
            if not any(abs(t - k) <= self.ledger.eps for k in system)
        ]

    def _absorb_gap_overhang(self, seq: Dict[str, Any], dest_start: float) -> None:
        """Widen a head-bound *seq* over its own keys hanging in the gap.

        A run's keys that continue past its shot's end into the gap facing
        the destination are the run's own overhang -- the on-ramp of a pulse
        whose train sits in the destination, a fade-out -- and the panel
        cannot even select them (nothing draws a gap key).  Left behind, they
        would sit AFTER the landed run in time and the ramp between them
        would flip: measured on a production assembly, an on-ramp
        ``2279 (0) -> 2288 (1)`` moved by its first key alone read
        ``2288 (1) -> 2295 (0)``, a flicker at the shot start.

        Only a pure overhang is taken: if any key of the run's curves lies
        between the run and the destination INSIDE a shot (the rest of the
        source shot, a shot in between), those gap keys belong to that later
        run and *seq* is left as it is.  Samples the system itself wrote for
        a shot bound (the ledger's claimed keys -- a seam hold at the source's
        end) are nobody's run: they neither pin the overhang nor travel.
        """
        if seq.get("kind") != "anim":
            return
        eps = 1e-3
        lo, hi = seq["end"] + eps, dest_start - eps
        if hi <= lo:
            return
        found: List[float] = []
        for crv in self._anim_curves_of(seq["obj"], seq.get("attr")):
            found.extend(self._animator_key_times(crv, (lo, hi)))
        if not found:
            return
        shots = self.store.shots
        if any(sh.start - eps <= t <= sh.end + eps for t in found for sh in shots):
            return
        seq["end"] = max(found)
        if seq.get("times") is not None:
            seq["times"] = sorted(set(seq["times"]) | set(found))

    def move_attribute_keys(
        self,
        obj: str,
        attr: Optional[str],
        delta: float,
        times: Optional[List[float]] = None,
        window: Optional[tuple] = None,
    ) -> int:
        """Shift keys of *obj* by *delta* frames -- one attribute's, or all.

        The key-level primitive under :meth:`move_object_keys` (which
        delegates here with ``attr=None``), and what a key selection's Move
        to Shot sends.  Which keys travel:

        * *attr* narrows the curves to those driving ``obj.attr``; ``None``
          takes every anim curve on the object.
        * *times* names the keys outright -- matched on each curve within a
          frame tolerance, since a widget's drag record and a curve query
          agree only to rounding -- otherwise every key inside *window*.

        Every curve goes through :meth:`move_curve_keys`, so the whole key
        record travels, the landing zone is cleared first, and the edit
        ledger's claims ride along.  A sparse *times* set takes that
        primitive's recreate path; a contiguous run is one relative move.

        Parameters:
            obj: Transform node name (short or long; ambiguity is resolved
                toward the match that carries anim curves).
            attr: Attribute name, or ``None`` for every animated attribute.
            delta: Frames to add to each key's time.
            times: Key times to move.  Takes precedence over *window*.
            window: ``(start, end)`` -- move every key inside it (inclusive).

        Returns:
            The number of keys moved.
        """
        import maya.cmds as cmds

        if abs(delta) < 1e-6:
            return 0
        curves = self._anim_curves_of(obj, attr)
        if not curves:
            return 0

        eps = 1e-3
        moved = 0
        for crv in curves:
            pairs = []
            if times is not None:
                present = []
                for t in times:
                    found = self._key_time_at(crv, t, eps)
                    if found is not None:
                        present.append(found)
                        pairs.append((t, found))
            elif window is not None:
                present = (
                    cmds.keyframe(crv, q=True, time=(window[0] - eps, window[1] + eps))
                    or []
                )
            else:
                present = cmds.keyframe(crv, q=True) or []
            if not present:
                continue
            # The curve can answer with a key a fraction of a frame from the
            # time the caller named -- a near-duplicate left by an earlier
            # move (measured on a production assembly: keys at 2278.0 AND
            # 2278.000000212585 on the same channel).  Moving THAT key by the
            # caller's delta lands it the same fraction off its target, and
            # for a Move to Shot the target IS the destination's first frame:
            # the key ends up a hair before it, in the gap, where the shot
            # neither owns nor draws it.  Correcting by the earliest matched
            # pair puts the named key exactly where the caller placed it.
            crv_delta = delta
            if pairs:
                named_t, found_t = min(pairs, key=lambda p: p[1])
                crv_delta = delta + (named_t - found_t)
            conns = cmds.listConnections(crv, plugs=True, d=True, s=False) or []
            self.move_curve_keys(
                crv,
                present,
                crv_delta,
                plug=conns[0] if conns else None,
                eps=eps,
                ledger=self.ledger,
            )
            moved += len(present)
        return moved

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
            # invisible until the move strands them.  The envelope is the
            # contract, so the whole keyed content goes; the list stays the
            # label it is (see :meth:`_content_objects`).
            finish = self._reconcile_boundaries(plan)
            self._batch_move_keys(
                cmds,
                self._content_objects(),
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
        # A bound may have moved and the seam may have: a boundary sample
        # follows its bound or is cleaned up, and a hold the system stepped
        # is released where it no longer belongs and applied where it now
        # does.  (Before the ledger this pass could not tell its own steps
        # from the animator's, which is why it was skipped here.)
        self.reconcile_system_edits()

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
            nxt = sorted_s[idx + 1]
            # Not just the span: the shot's content -- its trailing tail in
            # the gap included -- must stop short of the neighbour.  Measured
            # 2026-09-07: "Step 6" slid onto "Step 7" arrived with its
            # highlight lead-out (2131.2) inside Step 7's span, and the
            # landing-zone push then moved Step 7's keys by a different
            # delta, tearing the pulse.  A tail lands before the neighbour's
            # start; a shot with no tail may still close the gap entirely
            # (contiguous shots share their boundary sample).
            # The shot's own envelope, read by the ONE content scan
            # (:meth:`_key_extent`): its trailing gap up to the neighbour.
            # A second walk here would be the same question asked twice, and
            # would answer it differently -- it counted a flat bake's keys,
            # which are not content and block no slide.
            _s, _e, _os, outer_end, _ob = self._key_extent(shot, True)
            reach = shot.end if outer_end is None else max(shot.end, outer_end)
            limit = nxt.start - (reach - shot.start)
            if reach > shot.end + 1e-6:
                limit -= 1.0
            new_start = min(new_start, limit)
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
                ``"upstream"`` ripples shots before this one; ``None``
                ripples neither -- the shot slides between its neighbours,
                clamped by both (a gap handle's plain drag).
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
            # The pivot carries its own trailing gap; the ripple must not.
            if delta > 0:
                self.ripple_downstream(shot_id, old_end, delta, carry_gap=False)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                self.ripple_downstream(shot_id, old_end, delta, carry_gap=False)
        elif direction == "upstream":
            if delta < 0:
                self.ripple_upstream(shot_id, old_start, delta, carry_gap=False)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                self.ripple_upstream(shot_id, old_start, delta, carry_gap=False)
        else:
            self._move_shot_content(shot, new_start)

        if _enforce:
            self._enforce_gap_holds()
        self.store.mark_dirty()

    def ripple_downstream(
        self, shot_id: int, after_frame: float, delta: float, carry_gap: bool = True
    ):
        """Shift all shots starting at or after *after_frame* by *delta*.

        Routes through :mod:`_shot_plan` and :mod:`_shot_apply` so
        the whole downstream topology is resolved before any keyframe
        is touched -- preventing envelope collisions between moved and
        not-yet-moved shots.  The public ripple entry point for
        callers outside the sequencer (settings panel, clip motion).

        Every moved shot's window is its envelope, so it moves WHOLE, and
        with *carry_gap* (the default) so does whatever is keyed between
        *after_frame* and the first moved shot -- the pivot's trailing gap.
        That is the user's rule for a BOUND change (2026-09-06: "only the
        current shot changes, everything else ripples"): a fade tail parked
        in the gap is "everything else".  Left behind, a grow swallowed it
        into the shot and a shrink landed the neighbour on it; a retime's
        stretched keys landed on it too (measured: one Shift drag merged 80
        keys away on every baked curve of the production assembly).  A
        WHOLE-SHOT move already carries its gap inside its own envelope
        (:meth:`_move_shot_content`) and passes ``carry_gap=False``, or the
        same keys would move twice.  The sample ON *after_frame* is the
        pivot's closing pose and stays with it either way.
        """
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        plan = ShotPlanner.plan_ripple_downstream(
            self.store, shot_id, after_frame, delta, carry_gap=carry_gap
        )
        self._apply_plan(plan)

    def ripple_upstream(
        self, shot_id: int, before_frame: float, delta: float, carry_gap: bool = True
    ):
        """Shift all shots ending at or before *before_frame* by *delta*.

        Routes through the plan/executor pair; see :meth:`ripple_downstream`
        for *carry_gap* (here: the last moved shot's window is capped at
        *before_frame*, so the pivot keeps the sample on its bound).
        """
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        plan = ShotPlanner.plan_ripple_upstream(
            self.store, shot_id, before_frame, delta, carry_gap=carry_gap
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

        A curve with NO key inside the pre-gap shot is skipped entirely.
        Content is per OBJECT, so every curve on a keyed object comes back
        here -- including ones whose first key lands in the gap and whose motion
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
        # The scene's CONTENT, not each shot's member list: a gap must hold
        # on every curve that crosses it, and membership is a motion label
        # -- an object posed once in a shot is not listed there, while its
        # curve still interpolates across the gap toward its next key.
        curves = (
            AnimUtils.objects_to_curves(self._content_objects(), as_strings=True)
            if len(sorted_s) > 1
            else []
        )
        for i in range(len(sorted_s) - 1):
            pre = sorted_s[i]
            nxt = sorted_s[i + 1]
            if nxt.start - pre.end < 1e-6:
                continue  # no gap — shots are contiguous or overlapping
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
    def _sample_is_redundant(cls, crv: str, t: float, terminal: bool = True) -> bool:
        """True when cutting the key at *t* cannot change what *crv* plays.

        *terminal* admits the curve's first/last key to the test (see
        :meth:`_terminal_sample_is_redundant`); the MARKER scan turns it
        off, because a flat member's bookend on a bound is the animator's
        own mark to draw even though the curve would play the same without
        it -- the rule exists to unblock bounds, not to hide keys.

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
        if i is None or len(times) < 2:
            return False
        if i == 0 or i == len(times) - 1:
            if not terminal:
                return False
            # No neighbour on one side.  The hold beyond a terminal key is
            # shape ONLY while something can differ there: under constant
            # infinity the curve holds its terminal value forever, so a
            # terminal key that duplicates its one neighbour across a flat
            # span changes nothing by going.  This is where the LAST shot's
            # end samples ended up: never "redundant" by the plateau test
            # (nothing follows them), never cut, and once disowned they held
            # every trailing trim of the last shot at its old end.
            return cls._terminal_sample_is_redundant(crv, times, i)

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

    @classmethod
    def _terminal_sample_is_redundant(cls, crv: str, times: list, i: int) -> bool:
        """The first/last key case of :meth:`_sample_is_redundant`.

        Redundant when (1) the infinity beyond it is constant, (2) its one
        neighbour carries its value, (3) the span between them plays flat
        (a step out of the earlier key, or flat facing tangents), and (4)
        the neighbour's own tangents are not neighbour-derived non-flat
        slopes that the cut would recompute.
        """
        last = i == len(times) - 1
        infinity = cmds.getAttr(f"{crv}.postInfinity" if last else f"{crv}.preInfinity")
        if infinity != 0:  # anything but constant plays PAST the key
            return False
        j = i - 1 if last else i + 1
        eps = _BATCH_MOVE_EPS
        span = (min(times[i], times[j]) - eps, max(times[i], times[j]) + eps)
        vals = cmds.keyframe(crv, q=True, time=span, valueChange=True) or []
        out_types = cmds.keyTangent(crv, q=True, time=span, outTangentType=True) or []
        in_types = cmds.keyTangent(crv, q=True, time=span, inTangentType=True) or []
        in_ang = cmds.keyTangent(crv, q=True, time=span, inAngle=True) or []
        out_ang = cmds.keyTangent(crv, q=True, time=span, outAngle=True) or []
        if any(len(x) != 2 for x in (vals, out_types, in_types, in_ang, out_ang)):
            return False
        if abs(float(vals[0]) - float(vals[1])) > _POSE_TOL:
            return False
        # Time order within the span: index 0 is the earlier key.
        earlier, later = 0, 1
        if out_types[earlier] not in _STEP_TANGENTS and not (
            abs(float(out_ang[earlier])) <= _FLAT_ANGLE_TOL
            and abs(float(in_ang[later])) <= _FLAT_ANGLE_TOL
        ):
            return False
        keep = earlier if last else later  # the neighbour that survives
        for tan_type, angle in (
            (in_types[keep], in_ang[keep]),
            (out_types[keep], out_ang[keep]),
        ):
            if (
                tan_type in _NEIGHBOUR_DERIVED_TANGENTS
                and abs(float(angle)) > _FLAT_ANGLE_TOL
            ):
                return False
        return True

    def _reconcile_boundary_keys(
        self, bounds: Optional[Dict[int, tuple]] = None
    ) -> tuple:
        """Make every claimed boundary sample follow — or leave — its bound.

        *bounds* (``{shot_id: (start, end)}``) narrows the pass to those
        shots and resolves their claims against the bounds given instead of
        the ones the store holds — the PENDING form
        (:meth:`_reconcile_pending_bounds`), for an edit that has not written
        its new bounds yet.

        A sample the system created exists for ONE shot bound.  Once that
        bound has moved out from under it, it is neither the animator's pose
        nor a fencepost; leaving it is the clutter that builds up on every
        adjust.  Each claim resolves one of four ways:

        * the bound is still under it — nothing to do;
        * the key is gone (cut, or moved by an edit that carried the claim
          with it) — drop the claim;
        * the bound moved and its new frame is free — MOVE the sample there,
          full key record intact, carrying the claim with it — unless it
          holds nothing (a flat plateau, provably), in which case it is CUT:
          carried, it would hold nothing at the new bound either, and the
          system plants no such sample any more (``insert_keys`` declines a
          hold), so this is how the ones already planted drain away;
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
            records = [
                rec
                for rec in led.key_records(crv)
                if bounds is None or rec[1] in bounds
            ]
            if not records:
                continue  # nothing here belongs to the shots named in *bounds*
            if not cmds.objExists(crv):
                if bounds is None:
                    led.forget_curve(crv)
                continue
            for t, owner, edge in records:
                shot = self.shot_by_id(owner) if owner >= 0 else None
                bound = None
                if edge in ("start", "end"):
                    if bounds is not None:
                        bound = bounds[owner][0 if edge == "start" else 1]
                    elif shot is not None:
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
                if (
                    bound is not None
                    and not occupied
                    and not self._sample_is_redundant(crv, key_t)
                ):
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
                # Nowhere to follow to, or nothing worth carrying (a sample in
                # a flat plateau holds nothing at the new bound either): cut it
                # only where that is provably a no-op, and disown it either way.
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

    def _reconcile_pending_bounds(
        self, shot_id: int, new_start: float, new_end: float
    ) -> None:
        """Resolve *shot_id*'s claimed samples against the bounds it is ABOUT
        to have, before anything else moves onto the frames they sit on.

        A bound move on this shot is a ripple on its neighbours, and the
        ripple lands their keys on exactly the frames a shrinking bound just
        gave up — where this shot's own end/start samples still sit.  Run
        afterwards, the reconcile finds those samples wedged between the
        neighbour's arrivals: the frame the bound moved to is occupied, and
        the plateau test that would call the sample redundant now reads the
        NEIGHBOUR's poses, so it is disowned and left behind as content of a
        shot that never authored it.

        Measured 2026-09-06 on the production assembly, and the report that
        found it ("when i trimmed step 3.1, Step 3.3 became broken -- namely
        the plug animation now jumps"): Trim Trailing Space on "Step 3.1"
        [81, 431] moved the end to 401 and rippled "Shot 3.3" to [416, 604];
        the three ``CFG_A_PLUG_*_LOC.translateY`` curves kept the claimed 431
        sample (0.0) between the landed 421 (0.0) and 441 (-5.86), so a
        20-frame ramp played as a hold and a 10-frame ramp — 19 frames off on
        each of the three.  Run first, the same sample reads as the flat
        plateau it is (401 .. 446, both 0.0, stepped) and is cut.

        The four-way resolution is :meth:`_reconcile_boundary_keys`'s, called
        with the pending bounds; only this shot's claims are touched.
        """
        self._reconcile_boundary_keys(bounds={shot_id: (new_start, new_end)})

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
        self.reconcile_system_edits()
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

        # Ripple FIRST by however much the shot's envelope has to grow, then
        # scale: a key scaled past the old end into the neighbour's window
        # rode the ripple (the order :meth:`resize_shot` fixed the same way).
        prior_start = shot.start
        prior_end = shot.end
        grown_start = min(shot.start, new_start)
        grown_end = max(shot.end, new_end)
        head_delta = grown_start - prior_start
        if abs(head_delta) > 1e-6:
            self.ripple_upstream(shot_id, prior_start, head_delta)
        delta = grown_end - prior_end
        if abs(delta) > 1e-6:
            self.ripple_downstream(shot_id, prior_end, delta)

        # Scale only this object's keys
        self.scale_object_keys(obj, old_start, old_end, new_start, new_end)
        shot.start = grown_start
        shot.end = grown_end
        self.reconcile_system_edits()
        self.store.mark_dirty()

    def scale_shot_keys(
        self,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Retime every key inside ``[old_start, old_end]`` into the new span.

        Acts on the scene's keyed CONTENT (:meth:`_content_objects`), not on
        a shot's member list: membership is a label, and a retime that read
        the list left every unlisted object's keys where they were.  Measured
        on the production assembly: a Shift-drag of "Step 6" to 2145 scaled
        nothing on ``DA1_LOC``, whose highlight pulse the saved list never
        named.  The one retime under :meth:`resize_shot`,
        :meth:`set_shot_duration` and the gap handles' Shift drag.
        """
        import maya.cmds as cmds

        # The content set names a member as the shot listed it (often short)
        # AND as the keyed walk found it (a long path); scaling both scaled
        # the node twice.  Resolve to DAG paths first.
        nodes = sorted(set(cmds.ls(self._content_objects(), long=True) or []))
        for obj in nodes:
            self.scale_object_keys(obj, old_start, old_end, new_start, new_end)

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

        # Ripple first, then scale (see :meth:`resize_shot`).
        self.ripple_downstream(shot_id, old_end, delta)
        self.scale_shot_keys(shot.start, old_end, shot.start, new_end)
        shot.end = new_end
        self.reconcile_system_edits()
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

        # Ripple FIRST, then scale.  A neighbour's move window is read from
        # ITS start, so a key scaled past the old end into that window rode
        # away with the ripple: measured, doubling [0, 50] to [0, 100] beside
        # a shot at 60 sent the key scaled to 80 on to 130.  Moving the
        # neighbours while this shot's keys still sit inside its old span
        # vacates the new span, and the scale lands in it.
        tail_delta = new_end - old_end
        if abs(tail_delta) > 1e-6:
            self.ripple_downstream(shot_id, old_end, tail_delta)
        head_delta = new_start - old_start
        if abs(head_delta) > 1e-6:
            self.ripple_upstream(shot_id, old_start, head_delta)

        self.scale_shot_keys(old_start, old_end, new_start, new_end)
        shot.start = new_start
        shot.end = new_end

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
        clamp: bool = True,
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
        so a ripple can never run it onto the pivot.  Earlier still, this
        shot's own claimed bound samples are resolved against the bounds it
        is about to have (:meth:`_reconcile_pending_bounds`): the ripple lands
        the neighbour on exactly the frames a shrink gave up, and a sample
        still sitting there afterwards is stranded mid-content.

        Everything beyond the moved bound moves WHOLE: a neighbour's window
        is its envelope (its span and the gap after it), so its head keys and
        the lead-out hanging in the gap before a head grow ride with it, and
        a grow never claims what it covers.  The user's rule (2026-09-06):
        "we should only be modifying the current shot, everything else
        ripples unless ctrl is held" -- keeping the keys a bound is dragged
        over is Ctrl's gesture (GapManagerMixin._set_shot_edge, which moves
        nothing else).  Measured on the production assembly with a claim in
        this path: Step 7's start dragged -30 moved Step 6 -30 but left its
        highlight lead-out (2131.2 .. 2142) where it was, inside Step 7; the
        end dragged +30 moved Step 8 +30 but left its first two keys behind.

        A shrink STOPS at the shot's content (*clamp*, the default): the bound
        never passes an animator's key of a curve that moves in the shot --
        the same rule :meth:`fit_shot_to_content` trims by, read from the same
        scan (:meth:`_key_extent`).  Keys a bound passed used to be stranded
        where the neighbour's ripple then landed, interleaving two shots'
        animation; cutting content off to the neighbour is Ctrl's gesture
        (``GapManagerMixin._set_shot_edge``), which moves nothing else.  The
        one key a shrink may pass is a redundant, unclaimed key sitting ON
        the bound (a disowned pin), which is cut as the bound goes by.

        Parameters:
            shot_id: ID of the shot to resize.
            new_start: Desired start frame.
            new_end: Desired end frame.
            _enforce: If True (default), call :meth:`_enforce_gap_holds`
                after the operation.
            clamp: Hold a shrinking bound at the shot's content (see above).
        """
        shot = self.shot_by_id(shot_id)
        if shot is None:
            raise ValueError(f"No shot with id {shot_id}")

        new_start = self.store.snap(new_start)
        new_end = self.store.snap(new_end)
        if new_end < new_start:
            new_start, new_end = new_end, new_start
        old_start, old_end = shot.start, shot.end
        on_bound: list = []
        if clamp and (new_start > old_start + 1e-6 or new_end < old_end - 1e-6):
            first, last, _o1, _o2, on_bound = self._key_extent(shot, False)
            if new_start > old_start + 1e-6 and first is not None:
                new_start = self.store.snap(min(new_start, first))
            if new_end < old_end - 1e-6 and last is not None:
                new_end = self.store.snap(max(new_end, last))
        if abs(new_start - old_start) < 1e-6 and abs(new_end - old_end) < 1e-6:
            return

        tail_delta = new_end - old_end
        head_delta = new_start - old_start

        # A redundant key ON a bound moving inward goes first, then the
        # shot's own samples follow the SHRINKING bounds BEFORE the
        # neighbours ripple onto the frames it is giving up
        # (:meth:`_reconcile_pending_bounds`).  A growing edge waits for the
        # reconcile after the ripple: with carry_gap the frames it uncovers
        # are still the gap's until the ripple has moved that content away.
        self._cut_passed_bound_keys(on_bound, old_start, old_end, new_start, new_end)
        self._reconcile_pending_bounds(
            shot_id,
            new_start if head_delta > 1e-6 else old_start,
            new_end if tail_delta < -1e-6 else old_end,
        )

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
            # The pivot carries its own trailing gap; the ripple must not.
            if ripple and delta > 0:
                self.ripple_downstream(shot_id, old_end, delta, carry_gap=False)
                self._move_shot_content(shot, new_start)
            else:
                self._move_shot_content(shot, new_start)
                if ripple:
                    self.ripple_downstream(shot_id, old_end, delta, carry_gap=False)
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

        # The shot's own bound samples go with it -- cut where provably
        # redundant, released otherwise -- BEFORE the gap closes.  Disowned
        # in place they sat exactly where the ripple landed the next shot:
        # measured 2026-09-06 (an empty inserted shot, a respace, a delete),
        # "Shot 3.3" played 33 frames differently on four curves because the
        # deleted shot's end pin now stood mid-ramp with a recomputed tangent.
        self._reconcile_boundary_keys(bounds={shot_id: (None, None)})
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
                # Inner bounds vanish with the merge; their pins go too where
                # provably redundant (see delete_shot).
                self._reconcile_boundary_keys(bounds={s.shot_id: (None, None)})
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

    @staticmethod
    def _keyed_transform_times() -> dict:
        """Map every unambiguous transform's long path to its key times.

        Content channels only (the shared
        :meth:`Detection._map_standard_curves_to_transforms` rule: standard
        transform/visibility plus the render-effect channels), so
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
        """Execute *plan* over the scene's keyed content, fenceposts reconciled.

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
        # ONE object set for every stage: what each envelope moves, what a
        # boundary can cut, what the retime has to reach. Resolved once,
        # before the pin adds keys, because no stage's set depends on
        # another's writes.
        content = self._content_objects() if cmds is not None else []
        if content and retimes:
            # Claimed as they are inserted: a pin is the system's own sample,
            # and once the bound it pins moves the claim is what lets it be
            # moved with it (or cleaned up) instead of left behind.
            bound_owner = {}
            for shot in self.store.sorted_shots():
                bound_owner.setdefault(float(shot.start), (shot.shot_id, "start"))
                bound_owner.setdefault(float(shot.end), (shot.shot_id, "end"))
            # EVERY bound, not only the edges of the gaps this edit retimes:
            # the gap hold that follows (_enforce_gap_holds) steps each gap's
            # last key, and it is the key on the NEXT shot's start that stops
            # the hold there -- measured 2026-09-07, pinning only the changed
            # gaps' edges let the hold reshape the first 20 frames of a shot
            # the respace never moved.  What keeps this from planting a key on
            # every flat curve is insert_keys itself, which declines a hold.
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

        finish = self._reconcile_boundaries(plan, retimes) if cmds is not None else None
        if retimes:
            ShotApply.retime_gaps(retimes, content, after_move=False)
        # Every envelope moves the whole keyed content, so nothing keyed
        # inside a moving shot is left behind whatever its member list says
        # -- and the list is not written to.  It used to be BACKFILLED with
        # everything keyed in the envelope to get the same guarantee, which
        # made a baked rig's forty proxy joints members of all twelve shots.
        ShotApply.apply(self.store, plan, objects=content or None)
        if finish is not None:
            finish()

        if retimes:
            ShotApply.retime_gaps(retimes, content, after_move=True)

    def _content_objects(self) -> list:
        """Every object a whole-shot move can reach: what the movers, the pin
        and the retime all act on.

        :meth:`_keyed_transform_times` is the sequencer's OWN definition of
        content (``CONTENT_ATTRS``: standard transform/visibility and the
        render-effect channels, so a marker attribute never makes an object
        look animated), and this reuses it rather than
        deciding again — a second rule that drifted would pin one set and
        retime another.

        Union with what the shots claim, which covers the one case the keyed
        walk excludes by design: a shot object animated only on a non-standard
        channel. Stale entries are harmless — the resolution downstream drops
        names nothing resolves.

        Deliberately NOT the shots' object lists: a node no shot claims still
        has its curve cut by the shots' bounds and carried by their envelopes.
        The lists used to be backfilled with everything keyed in a moving
        envelope so the movers would carry it -- which is how a baked rig's
        forty proxy joints became members of all twelve shots of a production
        assembly (531 flat-keyed memberships, each drawing a track).  Handing
        the movers this set keeps the guarantee and leaves membership a label.
        """
        claimed = {obj for shot in self.store.shots for obj in shot.objects}
        if cmds is None:
            return sorted(claimed)
        return sorted(claimed | set(self._keyed_transform_times()))

    def respace(
        self, gap: float = 0, start_frame: float = 1, respect_locks: bool = True
    ) -> None:
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
            respect_locks: When False, spend *gap* on locked gaps too.  The
                locks are left set either way.
        """
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        plan = ShotPlanner.plan_respace(
            self.store, gap, start_frame, respect_locks=respect_locks
        )
        self._apply_plan(plan, retime_gaps=True)
        self._enforce_gap_holds()

    def apply_gap(
        self,
        gap: float,
        scope: str = "all",
        shot_id: Optional[int] = None,
        respect_locks: bool = True,
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
            respect_locks: When False, a locked gap is re-spaced like any
                other.  Only ``"all"`` consults the lock table at all -- the
                scoped modes move one shot through ``move_shot``, whose
                ripple never asks -- so it changes nothing for those.

        Returns:
            ``True`` when any shot was repositioned.
        """
        sorted_s = self.sorted_shots()
        if not sorted_s:
            return False

        if scope == "all":
            self.respace(
                gap=gap, start_frame=sorted_s[0].start, respect_locks=respect_locks
            )
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
