# coding=utf-8
"""Shot Sequencer — manages per-shot animation with ripple editing.

Shots are contiguous keyframe ranges ("blocks") along the timeline.
Changing one shot's duration or position ripples downstream shots.
"""

import bisect
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    # Maya-soft: planner/tests import this module headless.
    cmds = None
    logging.getLogger(__name__).debug("maya.cmds unavailable — headless mode")


from pythontk import ShotSequencer as _ShotSequencerCore

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils.shots._shots import (
    Detection,
    MayaShotsHost,
    ShotBlock,
    ShotStore,
)
from mayatk.anim_utils.shots._shot_plan import _INF as _PLAN_INF
from mayatk.anim_utils._tangents import _NEIGHBOUR_DERIVED_TANGENTS

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

# A tangent this close to horizontal is flat.  Degrees: Maya reports tangent
# angles in degrees, and an angle below this cannot bow a segment between two
# keys of equal value by anything an animator could see.
_FLAT_ANGLE_TOL = 1e-4

# ...and a tangent that MOVED by less than this did not really move: the one
# threshold of the shot system's tangent hold (``_hold_half``).  Holding a
# half converts it to ``fixed``, so it must sit above the float noise of
# Maya's own re-derivation -- measured 2026-10-11 over every SOL_REPLACE_ASSEMBLY
# op at gap 0 and 10, every drift the hold was asked about was 0 or under
# 1e-12 -- and below the smallest real one: a 1-frame slide of a production
# curve re-derived a slope by 0.003 degrees and played the shot 0.0004 off,
# which the old 0.01 let through.
_TANGENT_MOVED_TOL = 1e-6


# ---------------------------------------------------------------------------
# ShotSequencer
# ---------------------------------------------------------------------------


class ShotSequencer(_ShotSequencerCore):
    """Manages a :class:`ShotStore` and provides ripple editing and
    keyframe manipulation on top of it.

    The timeline operations themselves (define / ripple / slide / resize /
    insert / delete / merge / split / pad / respace / move sequences / fit)
    are the DCC-free :class:`pythontk.ShotSequencer` orchestration, shared
    with blendertk; this class supplies the Maya scene I/O it reaches
    through hooks (key movers and retimers, the boundary-sample ledger,
    gap holds, audio carriers, content scans).

    Parameters:
        shots: Initial shot list (creates an internal ShotStore).
        store: Existing ShotStore to wrap.  Takes precedence over *shots*.
    """

    #: The Maya store: scene persistence plus the Maya acquisition hooks.
    STORE_CLASS = ShotStore

    # ---- scene hooks (the pythontk ShotSequencer contract) ----------------

    def _scene_available(self) -> bool:
        """Maya is importable; headless (``cmds`` is ``None``) edits bounds only."""
        return cmds is not None

    def _curve_exists(self, curve: str) -> bool:
        """A ledger key is the anim curve's node name."""
        return cmds is None or bool(cmds.objExists(curve))

    def _content_batch(self):
        """One audio batch around a multi-step edit, so derived DG audio nodes
        re-render exactly once."""
        from mayatk.audio_utils._audio_utils import AudioUtils

        return AudioUtils.batch()

    def _move_content_keys(
        self, objects, env_lo, env_hi, delta, lo_open=False, hi_closed=False
    ) -> None:
        """Move the envelope's keyed content (:meth:`_batch_move_keys`, ledger-aware)."""
        self._batch_move_keys(
            cmds,
            objects,
            env_lo,
            env_hi,
            delta,
            lo_open=lo_open,
            hi_closed=hi_closed,
            ledger=self.ledger,
        )

    def _move_audio_sequence(self, seq: Dict[str, Any], delta: float) -> None:
        """Shift one audio track's events inside the sequence's range."""
        from mayatk.audio_utils._audio_utils import AudioUtils

        with AudioUtils.batch() as b:
            tids = AudioUtils.shift_keys_in_range(
                seq["start"],
                seq["end"],
                delta,
                track_ids=[seq["obj"]],
                ledger=self.ledger,
            )
            if tids:
                b.mark_dirty(tids)

    def _object_key_probe(self, obj: str):
        """``frame -> bool`` over *obj*'s anim curves (:meth:`_any_key_at`)."""
        curves = self._anim_curves_of(obj, None)
        return lambda frame: self._any_key_at(curves, frame)

    def _animator_times_in(self, obj: str, attr: Optional[str], window) -> List[float]:
        """The animator's key times on *obj*'s (*attr*'s) curves inside *window*."""
        found: List[float] = []
        for crv in self._anim_curves_of(obj, attr):
            found.extend(self._animator_key_times(crv, window))
        return found

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _find_keyed_transforms(
        start: float,
        end: float,
        value_tolerance: float = 1e-4,
        require_motion: bool = True,
        evaluated: bool = False,
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
        old rule, for a caller that wants every keyed object.  *evaluated*
        is ``Detection.curve_moves_in``'s: the curve's values at the range's
        ends count too (the frames a bound hands over).
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
                    hit = Detection.curve_moves_in(
                        crv, start, end, value_tolerance, evaluated=evaluated
                    )
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

    def _member_names(self, shot: ShotBlock, names: List[str]) -> tuple:
        """``(listed, unlisted)`` matched by NODE: a member stored as a short
        name and the long path a scan found are one object, so the long one
        is neither added again nor missed when the stored one is re-decided."""
        if cmds is None:
            return super()._member_names(shot, names)
        by_node: dict = {}
        for member in shot.objects:
            for path in cmds.ls(member, long=True) or []:
                by_node.setdefault(path, member)
        listed: list = []
        unlisted: list = []
        for name in names:
            paths = cmds.ls(name, long=True) or [name]
            member = next((by_node[p] for p in paths if p in by_node), None)
            if member is None:
                unlisted.append(name)
            elif member not in listed:
                listed.append(member)
        return listed, unlisted

    @staticmethod
    def _content_curves(node: str) -> List[str]:
        """*node*'s anim curves that drive a content channel, through blends.

        The per-node form of :meth:`_find_keyed_transforms`'s map -- one
        node's curves rather than a scene-wide one -- shared by the motion
        test and the mark scan so both read the same channels.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        curves = AnimUtils.objects_to_curves([node], through_blends=True)
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
    def _resolved_name(name: str, rename_memo=None) -> Optional[str]:
        """The long path *name* should now be stored by, or ``None`` to keep it.

        A name that answers to ONE node is stored as that node's long path.  A
        build keeps a doc name it cannot resolve as written, and once the node
        exists the scene scan -- which names nodes by long path -- adds it
        beside that name: one object, two members, shipped twice.  A name that
        answers to several nodes is kept: which one the doc meant is a finding
        (Assess's ``ambiguous_object``), never a silent pick.

        A name that answers to nothing is a stale path (a parent renamed):
        re-resolved by its leaf; when the leaf is gone too -- the object itself
        was renamed -- through :meth:`Detection.transform_from_curve_names`,
        which follows the anim curves Maya named after the old node.  What
        resolves to nothing is **kept as stored**: "renamed" and "deleted" are
        indistinguishable from a name alone, this runs unattended on every
        refresh, and an unresolvable name is inert (every consumer filters
        through :meth:`_shot_nodes` / ``cmds.ls``) while dropping it destroys
        the shot's record of its own content.  Pruning belongs to an explicit,
        user-driven action.  (blendertk holds the same line: its
        ``follow_renames`` keeps what resolves to nothing, leaving deletions to
        ``assess``.)
        """
        import maya.cmds as cmds

        matches = cmds.ls(name, long=True) or []
        if len(matches) == 1:
            return None if matches[0] == name else matches[0]
        if matches:
            return None
        # A doc name the scene holds only inside a namespace (a referenced
        # asset, ``AC:door_geo`` for ``door_geo``): the store's own lookup
        # (``ShotStore.member_nodes``, answered by its host), so a build and a
        # reconcile agree.  Unkeyed it had no curve to follow and was never
        # re-pointed (BACKLOG 2026-10-10).
        matches = MayaShotsHost.member_nodes(name)
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
        leaf = CoreUtils.leaf_name(name)
        matches = cmds.ls(leaf, long=True, type="transform") or []
        if len(matches) == 1:
            return matches[0]
        if matches:
            return ShotSequencer._disambiguate_matches(matches)
        return ShotSequencer._renamed_target(leaf, rename_memo)

    def reconcile_all_shots(self) -> bool:
        """Point every name the store holds an object by at the node it is now.

        Call once per refresh cycle *before* segment collection, so the store
        and the scan name each node alike.  Re-pointing only -- membership is
        never dropped (:meth:`_resolved_name`) -- and through
        ``ShotStore.rename_members``, so a pin and the hidden / locked state
        follow their member (a pin left on a stale path keeps nothing).

        Returns ``True`` if anything was re-pointed.
        """
        rename_memo: dict = {}  # shared across the pass — see _renamed_target
        renames = {}
        for name in sorted(self.store.held_names()):
            resolved = self._resolved_name(name, rename_memo)
            if resolved:
                renames[name] = resolved
        return self.store.rename_members(renames)

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
            # Shot has no (valid) objects — auto-discover animated transforms.
            # The system's own fill, not a hand edit (the manifest's
            # edited_by_hand reads past it); unresolved members stay, inert.
            discovered = self._find_keyed_transforms(shot.start, shot.end)
            if discovered:
                self.store.add_system_members(shot.shot_id, discovered)
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
        from mayatk.audio_utils._audio_utils import AudioUtils

        all_events = AudioUtils.read_all_events()
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

    def _drop_claimed_seam_copy(
        self, seq: Dict[str, Any], target: float, seam: float, eps: float = 1e-3
    ) -> None:
        """Cut the key a head landing claims: on each curve of *seq* whose own
        key lands exactly on *seam* (the sequence moving to start at
        *target*), the stationary key already sitting there -- the source's
        closing-pose copy a leading room's split left (see
        :meth:`move_sequences_to_shot`). The block's own key at the source
        frame stays, so no curve is emptied (Maya deletes a keyless one); a
        two-key curve (that key and the seam pose) is the common on/off
        shape, and a "never below two keys" guard left its seam pose to be
        pushed a frame into the destination."""
        src = seam - (target - seq["start"])
        if not seq["start"] - eps <= src <= seq["end"] + eps:
            return
        times = seq.get("times")
        if times and not any(abs(t - src) <= eps for t in times):
            return
        for crv in self._anim_curves_of(seq["obj"], seq.get("attr")):
            if not cmds.keyframe(crv, q=True, time=(src - eps, src + eps)):
                continue
            if not cmds.keyframe(crv, q=True, time=(seam - eps, seam + eps)):
                continue
            cmds.cutKey(crv, time=(seam - eps, seam + eps), clear=True)
            self.ledger.release(crv, seam)

    # ---- shot fit / trim / extend ----------------------------------------

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
        curves = AnimUtils.objects_to_curves(
            self._shot_nodes(shot), through_blends=False
        )
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
            except RuntimeError:
                continue  # locked or referenced curve -- leave it as it was
            cut += 1
            # A gap hold's step claim on it goes too, or what ripples onto the
            # frame inherits it -- and _release_gap_holds would then restore
            # the claim's pre-hold tangent onto a stepped key that landed there.
            self.ledger.release(crv, t)
        return cut

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

    # ---- per-object keyframe editing -------------------------------------

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
        eps = 1e-3
        moved = 0
        for crv in self._anim_curves_of(obj, attr):
            present, correction = self._named_keys(crv, times, window, eps)
            if not present:
                continue
            conns = cmds.listConnections(crv, plugs=True, d=True, s=False) or []
            self.move_curve_keys(
                crv,
                present,
                delta + correction,
                plug=conns[0] if conns else None,
                eps=eps,
                ledger=self.ledger,
            )
            moved += len(present)
        return moved

    def copy_attribute_keys(
        self,
        obj: str,
        attr: Optional[str],
        delta: float,
        times: Optional[List[float]] = None,
        window: Optional[tuple] = None,
    ) -> int:
        """Copy keys of *obj* *delta* frames on -- one attribute's, or all --
        the originals left where they are.

        :meth:`move_attribute_keys`' twin, naming its keys the same way
        (:meth:`_named_keys`), and what Copy to Shot sends.  Each copy is the
        whole key record (:meth:`_snapshot_curve_keys` /
        :meth:`_restore_curve_keys`: value, tangents, breakdown) and it is the
        animator's key: a claim the shot system holds on a source key stays
        on that key.  The landing zone is cleared first, as a move clears it
        (:meth:`_clear_destination`).

        Returns:
            The number of keys copied.
        """
        import maya.cmds as cmds

        if abs(delta) < 1e-6:
            return 0
        eps = 1e-3
        copied = 0
        for crv in self._anim_curves_of(obj, attr):
            present, correction = self._named_keys(crv, times, window, eps)
            if not present:
                continue
            # A window is one snapshot; named keys are taken one by one, so a
            # key between two of them stays behind.
            spans = (
                [(window[0] - eps, window[1] + eps)]
                if times is None and window is not None
                else [(t - eps, t + eps) for t in sorted(present)]
            )
            weighted, records = False, []
            for span in spans:
                w, recs = self._snapshot_curve_keys(crv, span)
                weighted = weighted or w
                records.extend(recs)
            if not records:
                continue
            conns = cmds.listConnections(crv, plugs=True, d=True, s=False) or []
            self._clear_destination(
                crv,
                [r["time"] for r in records],
                delta + correction,
                plug=conns[0] if conns else None,
                eps=eps,
                ledger=self.ledger,
            )
            self._restore_curve_keys(crv, records, weighted, delta + correction)
            copied += len(records)
        return copied

    def _named_keys(
        self, crv: str, times: Optional[List[float]], window: Optional[tuple], eps
    ) -> tuple:
        """``(present, correction)``: the keys of *crv* a move or a copy names
        -- *times* matched within *eps*, else every key inside *window*, else
        all of them -- and the shift that puts the first named one exactly
        where the caller placed it.

        The curve can answer with a key a fraction of a frame from the time
        the caller named -- a near-duplicate left by an earlier move (measured
        on a production assembly: keys at 2278.0 AND 2278.000000212585 on the
        same channel).  Sending THAT key by the caller's delta lands it the
        same fraction off its target, and for a Move to Shot the target IS the
        destination's first frame: the key ends up a hair before it, in the
        gap, where the shot neither owns nor draws it.  Correcting by the
        earliest matched pair puts the named key exactly where it belongs.
        """
        import maya.cmds as cmds

        if times is None:
            if window is not None:
                rng = (window[0] - eps, window[1] + eps)
                return cmds.keyframe(crv, q=True, time=rng) or [], 0.0
            return cmds.keyframe(crv, q=True) or [], 0.0
        pairs = []
        for t in times:
            found = self._key_time_at(crv, t, eps)
            if found is not None:
                pairs.append((t, found))
        if not pairs:
            return [], 0.0
        named_t, found_t = min(pairs, key=lambda p: p[1])
        return [found for _t, found in pairs], named_t - found_t

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

    @staticmethod
    def _same_value(
        crv: str, t_a: float, t_b: float, eps: float = _BATCH_MOVE_EPS
    ) -> bool:
        """Whether *crv*'s keys at *t_a* and *t_b* hold the same value (to
        ``1e-6``, relative above 1) -- a pose already waiting at *t_b*."""
        a = cmds.keyframe(crv, q=True, time=(t_a - eps, t_a + eps), valueChange=True)
        b = cmds.keyframe(crv, q=True, time=(t_b - eps, t_b + eps), valueChange=True)
        if not a or not b:
            return False
        return abs(a[0] - b[0]) <= 1e-6 * max(1.0, abs(a[0]), abs(b[0]))

    @classmethod
    def _any_key_at(cls, curves, frame: float, eps: float = _BATCH_MOVE_EPS) -> bool:
        """Whether any curve of *curves* holds a key within *eps* of *frame*:
        the ``seam_keyed`` question ``ShotStore.enclosing_bounds`` asks of the
        curves a landing rides on."""
        return any(cls._key_time_at(str(c), frame, eps) is not None for c in curves)

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
                    ledger.release(crv, t)
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
           direction of travel, by ONE delta, as a single rigid block -- or,
           when it is one key whose pose already waits where the push would
           lay it, merged into that key.

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

        # A LONE displaced key the push would lay exactly onto a stationary key
        # of the SAME value merges into it instead: that pose already waits
        # there, so nothing is lost, while pushing both shifts what follows.
        # Measured 2026-09-23: a key dragged onto a contiguous seam pushed the
        # old seam pose onto the neighbour's identical opening pose, and that
        # pose a frame into the neighbour's motion. Alone, because the rest of
        # a block are still pushed by the same delta, PAST the key the merged
        # one joined: 50 (5), 55 (8) ahead of 56 (5) came out 56 (5), 61 (8),
        # the return to 5 after the 8 gone.
        if (
            len(displaced) == 1
            and cls._nearest_index(stationary, displaced[0] + push, eps) is not None
            and cls._same_value(crv, displaced[0], displaced[0] + push, eps)
            and len(cmds.keyframe(crv, q=True, timeChange=True) or []) > 2
        ):
            d = displaced[0]
            cmds.cutKey(crv, time=(d - eps, d + eps), clear=True)
            if ledger is not None:
                ledger.release(crv, d)
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
        the Shot Sequencer panel's key drags (``SequencerHost.commit_curve_moves``)
        -- so the underscore was describing
        a contract that no longer held. Not to be confused with the unrelated
        ``AnimUtils.move_curve_keys``, which takes
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
        after it would faithfully preserve the already-damaged angle.  Only
        the inward half is held (:meth:`_hold_half`), and only when it
        actually moved.

        Returns a zero-argument callable (the number of halves it held).
        """
        moving = sorted(times)
        if len(moving) < 2:
            return lambda: 0  # a lone key has no interior to protect
        held = cls._held_halves(
            crv, ((moving[0], "out"), (moving[-1], "in")), delta, eps
        )
        return lambda: cls._hold_halves(held, eps=eps)

    @staticmethod
    def _derived_half(
        crv: str, t: float, half: str, kinds: tuple = _NEIGHBOUR_DERIVED_TANGENTS
    ) -> Optional[Tuple[float, float]]:
        """``(angle, weight)`` of *half* of *crv*'s key at *t* when its type is
        one of *kinds* -- by default the ones Maya re-derives from both
        neighbours (:attr:`_NEIGHBOUR_DERIVED_TANGENTS`), the halves a move or
        a split disturbs -- else ``None``.  Read through
        :meth:`AnimUtils.tangent_half`."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        got = AnimUtils.tangent_half(crv, t, half)
        return (got[1], got[2]) if got and got[0] in kinds else None

    @classmethod
    def _held_halves(
        cls,
        crv: str,
        halves,
        delta: float = 0.0,
        eps: float = _BATCH_MOVE_EPS,
        kinds: tuple = _NEIGHBOUR_DERIVED_TANGENTS,
    ) -> list:
        """``[(crv, t + delta, half, (angle, weight)), ...]`` for each ``(t,
        half)`` of *halves* whose type is one of *kinds*: what
        :meth:`_hold_halves` puts back once the key has moved by *delta*.

        The key's ACTUAL time is read first: a time can reach here from a
        widget's drag record as readily as from a curve query, and the two
        agree only to rounding -- an exact-time read would come back empty
        and silently skip the hold."""
        held = []
        for t, half in halves:
            src = cls._key_time_at(crv, t, eps)
            got = None if src is None else cls._derived_half(crv, src, half, kinds)
            if got is not None:
                held.append((crv, src + delta, half, got))
        return held

    @classmethod
    def _hold_halves(
        cls,
        held,
        tol: float = _TANGENT_MOVED_TOL,
        kinds: Optional[tuple] = None,
        eps=_BATCH_MOVE_EPS,
    ) -> int:
        """:meth:`_hold_half` over :meth:`_held_halves`' list; the number held."""
        return sum(
            cls._hold_half(crv, t, half, angle_weight, tol, kinds, eps)
            for crv, t, half, angle_weight in held
        )

    @classmethod
    def _hold_half(
        cls,
        crv: str,
        t: float,
        half: str,
        held: Tuple[float, float],
        tol: float = _TANGENT_MOVED_TOL,
        kinds: Optional[tuple] = None,
        eps: float = _BATCH_MOVE_EPS,
    ) -> int:
        """Put *held* ``(angle, weight)`` back on *half* of *crv*'s key within
        *eps* of *t* where it drifted past *tol* -- a negative *tol* holds it
        whatever (a freeze) -- and return 1, else 0.

        The ONE tangent hold of the shot system: the drag's interior hold, the
        plan writer's boundary hold, a split's held halves and the respace's
        hold freeze all end here.  It BREAKS the tangent first, so the other
        half (facing the gap, or a new neighbour, which genuinely changed)
        keeps its own type and stays derived; a half that did not really move
        keeps its type too.  The weight is compared, and put back where it
        drifted, only on a weighted curve (an unweighted one reports a fixed
        1.0); setting the angle has never been seen to move it -- the drag
        hold, which never set it, kept every weight of four derived types
        exact (2026-10-11).  *kinds*: hold only while the half's current type
        is one of them (a pin or a copy set outright since is its own now).
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        landed = cls._key_time_at(crv, t, eps)
        now = None if landed is None else AnimUtils.tangent_half(crv, landed, half)
        if now is None or (kinds is not None and now[0] not in kinds):
            return 0
        angle, weight = held
        weighted = bool(
            (cmds.keyTangent(crv, q=True, weightedTangents=True) or [False])[0]
        )
        if (
            tol >= 0
            and abs(now[1] - angle) <= tol
            and not (weighted and abs(now[2] - weight) > tol)
        ):
            return 0  # Maya left it alone, so leave the type alone too
        tt = (landed, landed)
        cls._try_key_tangent(crv, tt, {"lock": False})
        cls._try_key_tangent(crv, tt, {f"{half}Angle": angle})
        if weighted:  # read after the angle is set, in case it moved the weight
            after = AnimUtils.tangent_half(crv, landed, half)
            if after is not None and abs(after[2] - weight) > tol:
                cls._try_key_tangent(crv, tt, {f"{half}Weight": weight})
        return 1

    @classmethod
    def _cut_carried_sample(cls, crv: str, t: float, value: float, ledger=None) -> None:
        """Cut the carried seam sample left at *t* once its re-key is down.

        Only a key still holding the carried pose (*value*) is cut -- anything
        else there now is not the sample -- and never a curve's last key: Maya
        deletes a keyless animCurve node, and the connection with it. Its
        claims in *ledger* go with it, as with every key the system cuts.
        """
        landed = cls._key_time_at(crv, t, _BATCH_MOVE_EPS)
        if landed is None:
            return
        got = cmds.keyframe(crv, q=True, time=(landed, landed), valueChange=True)
        if not got or abs(float(got[0]) - value) > _POSE_TOL:
            return
        if (cmds.keyframe(crv, q=True, keyframeCount=True) or 0) <= 1:
            return
        try:
            cmds.cutKey(crv, time=(landed, landed), clear=True)
        except RuntimeError:
            return  # locked or referenced curve — leave it as it was
        if ledger is not None:
            ledger.release(crv, landed)

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
        :meth:`move_curve_keys` takes one -- and a key ``setKeyframe``
        OVERWRITES on a destination takes every claim on it along: left on the
        frame, its step and a behavior's authored claim passed to the key that
        landed there (the next Build's ``release_authored`` then deleted it).
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

        if ledger is not None:
            # Claims of the keys about to be overwritten go before the remap
            # below lands the arrivals' own claims on the same frames.
            stationary = [
                t
                for t in sorted(cmds.keyframe(crv, q=True) or [])
                if cls._nearest_index(old_times, t, eps) is None
            ]
            for _old_t, new_t in pairs:
                hit = cls._nearest_index(stationary, new_t, eps)
                if hit is not None:
                    ledger.release(crv, stationary[hit])

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

    def _point_clip_curves(
        self,
        obj: str,
        t: float,
        attr_name: Optional[str] = None,
        shot: Optional[ShotBlock] = None,
        eps: float = 1e-3,
    ) -> List[str]:
        """The anim curves a point clip of *obj* at *t* stands for -- the
        pythontk hook behind ``move_stepped_keys``.

        *attr_name* (a sub-row clip): that attribute's curves.  Otherwise the
        curves the mark was drawn from: the content curves
        (:meth:`_content_curves`) whose key at *t* is one of the marks *shot*
        shows (:meth:`_animator_marks`), stepped or not -- so a claimed bound
        sample on another channel stays on its bound.  Without *shot*, the
        content curves with an animator's key at *t*.
        """
        import maya.cmds as cmds

        matches = cmds.ls(obj, long=True)
        if not matches:
            return []
        obj_path = matches[0]
        if attr_name:
            plug = f"{obj_path}.{attr_name}"
            if not cmds.objExists(plug):
                return []
            return cmds.listConnections(plug, type="animCurve", s=True, d=False) or []
        out = []
        for crv in self._content_curves(obj_path):
            if shot is None:
                marks = self._animator_key_times(crv, (t - eps, t + eps))
            else:
                marks = self._animator_marks([crv], shot)
            if any(abs(m - t) <= eps for m in marks):
                out.append(crv)
        return out

    def _move_point_key(
        self, obj: str, curve: str, old_time: float, new_time: float, eps: float = 1e-3
    ) -> None:
        """Move *curve*'s key at *old_time* to *new_time* via delete-and-recreate
        -- the pythontk hook behind ``move_stepped_keys``.

        Uses cutKey + setKeyframe instead of timeChange to avoid Maya
        silently misplacing keys at large offsets.  The whole key record
        travels (:meth:`_snapshot_curve_keys` / :meth:`_restore_curve_keys`):
        restoring the tangent TYPES alone left a fixed or weighted tangent
        with neither its angles nor its weights, and a drag recreates the key
        twice (lifted clear, then landed).  A step this system claims travels
        with its key: the destination is where the hold has to be released
        from once it stops being a seam.
        """
        import maya.cmds as cmds

        tr = (old_time - eps, old_time + eps)
        weighted, records = self._snapshot_curve_keys(curve, tr)
        if not records:
            return
        record = records[0]
        conns = cmds.listConnections(curve, plugs=True, d=True, s=False) or []

        cmds.cutKey(curve, time=tr, clear=True)
        # cutKey may delete the curve node if it was the last key;
        # fall back to the driven plug so setKeyframe recreates it.
        target = curve if cmds.objExists(curve) else (conns[0] if conns else None)
        if not target:
            target = (cmds.ls(obj, long=True) or [obj])[0]
        if target == curve:
            # setKeyframe OVERWRITES a key already on new_time: its claims
            # go with it, before the remap below lands the moved key's.
            landed = self._key_time_at(curve, new_time, eps)
            if landed is not None:
                self.ledger.release(curve, landed)
        self._restore_curve_keys(
            target, [record], weighted, delta=new_time - record["time"]
        )
        self.ledger.remap(curve, [(old_time, new_time)])

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

    def _shift_audio(
        self,
        old_start: float,
        old_end: float,
        delta: float,
    ) -> None:
        """Shift audio clips whose timeline position falls within a range.

        Delegates to :func:`mayatk.audio_utils.shift_keys_in_range`
        which updates the canonical keyed store. Callers are expected
        to wrap bulk ops in an ``audio_utils.batch()`` so the compositor
        re-renders derived DG audio nodes in a single sync. The store's
        ledger rides along: the Shot Manifest claims the clips it keys, and
        a claim must land where its key moved.

        Parameters:
            old_start: Start of the time range to shift.
            old_end: End of the time range to shift.
            delta: Frames to add to each audio key.
        """
        if abs(delta) < 1e-6:
            return
        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch() as b:
            tids = audio_utils.shift_keys_in_range(
                old_start, old_end, delta, ledger=self.ledger
            )
            if tids:
                b.mark_dirty(tids)

    def scale_object_keys(
        self,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
        _scaled: Optional[set] = None,
    ) -> None:
        """Scale (and optionally shift) keyframes of *obj* from
        [old_start, old_end] into [new_start, new_end].

        The shot system's claims on the keys it retimes travel with them
        (``_ShotApplyInternal._claims_follow``): a respace pin on the shot's
        end scaled onto the new end is still the system's sample there, and
        a gap hold stepped onto a retimed seam is still the system's to take
        back.  Left on the frame the key left, the reconcile released each
        one and the sample became an animator key -- measured on the
        production assembly as one key more per pinned curve after a -6
        duration change.

        Parameters:
            obj: Transform node name.
            old_start: Original first frame.
            old_end: Original last frame.
            new_start: Desired first frame.
            new_end: Desired last frame.
            _scaled: The curves a pass over several nodes already scaled
                (:meth:`scale_shot_keys`); this node's are added to it, and
                a curve in it is not scaled again.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils._anim_utils import AnimUtils
        from mayatk.anim_utils.shots._shot_apply import ShotApply

        # Resolve to a single DAG path first — shot.objects entries may be
        # short names that turn ambiguous when the scene gains a same-named
        # node, and cmds.scaleKey raises on ambiguity (see _shot_nodes).
        matches = cmds.ls(obj, long=True)
        if not matches:
            return
        if abs(old_end - old_start) < 1e-6:
            return
        node = self._disambiguate_matches(matches)
        ratio = (new_end - new_start) / (old_end - old_start)
        curves = self._anim_curves_of(node, None)
        targets = [node]
        if _scaled is not None:
            todo = [c for c in curves if c not in _scaled]
            _scaled.update(curves)
            if curves and not todo:
                return  # a node sharing every curve with one already scaled
            if len(todo) < len(curves):
                targets, curves = sorted(todo), todo  # the shared ones are done
        with ShotApply._claims_follow(
            self.ledger,
            curves,
            (old_start, old_end),
            lambda t: new_start + (t - old_start) * ratio,
        ):
            with AnimUtils.exact_scale():  # GUI Maya rounds a scaled key
                cmds.scaleKey(
                    targets,
                    time=(old_start, old_end),
                    newStartTime=new_start,
                    newEndTime=new_end,
                )

    # ---- system-authored edits (ledger-backed) ---------------------------
    #
    # The two writes the shot system makes on the animator's curves — a gap
    # hold and a boundary sample — are claimed in ``store.edit_ledger`` as
    # they are made.  That is what lets them be RELEASED when the boundary
    # that justified them moves: without a claim a step is just a step and a
    # key is just a key, and the only safe thing to do with either is leave it
    # behind on every adjust.  Anything the system did not write is never
    # touched — a hold the animator put in is intentional by definition.

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
            AnimUtils.objects_to_curves(self._content_objects(), through_blends=False)
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
        back when the key is still the one the system stepped
        (``ShotEditLedger.is_same_key``) and still carries its ``step`` — an
        animator who re-tangented it since owns it now, and so does one who
        pasted a key over it: releasing onto that key un-stepped the
        animator's own stepped key (2026-10-10, a Graph Editor paste).

        Returns:
            The number of keys restored to their pre-hold out-tangent.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        led = self.ledger
        eps = _BATCH_MOVE_EPS
        restored = 0
        for crv in led.stepped_curves():
            # A curve the seam scan just resolved is live by construction, so
            # the existence probe only runs for claims nothing asked about.
            if crv not in seams and not self._curve_exists(crv):
                led.forget_curve(crv)
                continue
            want = seams.get(crv, ())
            for t in led.step_times(crv):
                if any(abs(t - w) <= eps for w in want):
                    continue  # still a seam — the hold still belongs here
                claim = led.claimed_step(crv, t)
                types = led.release_step(crv, t)
                if types is None:
                    continue
                key_t = self._key_time_at(crv, t, eps)
                if key_t is None:
                    continue  # the key is gone; the claim went with it
                tt = (key_t, key_t)
                ott = cmds.keyTangent(crv, q=True, time=tt, outTangentType=True)
                if (ott or [""])[0] != "step":
                    continue  # re-tangented since — not ours to take back
                itt = cmds.keyTangent(crv, q=True, time=tt, inTangentType=True)
                value = cmds.keyframe(crv, q=True, time=tt, valueChange=True)
                in_now = AnimUtils.tangent_half(crv, key_t, "in")
                if not led.is_same_key(
                    claim,
                    (itt or [""])[0],
                    (value or [0.0])[0],
                    in_half=in_now[1:] if in_now else None,
                ):
                    continue  # written over or re-posed since -- the animator's
                later = [
                    k
                    for k in cmds.keyframe(crv, q=True, timeChange=True) or []
                    if k > key_t + eps
                ]
                if self._hold_runs_through_shots(key_t, min(later) if later else None):
                    continue  # other shots play this hold: it stays, unclaimed
                self._try_key_tangent(
                    crv, tt, {"outTangentType": self._own_tangent_type(types[1])}
                )
                if types[1] == "fixed" and claim.out_half is not None:
                    # Exactly as it was: the step discarded angle and weight.
                    angle, weight = claim.out_half[:2]
                    self._try_key_tangent(crv, tt, {"lock": False})
                    self._try_key_tangent(crv, tt, {"outWeight": weight})
                    self._try_key_tangent(crv, tt, {"outAngle": angle})
                elif types[1] == "fixed":
                    self._restore_fixed_out_tangent(crv, key_t)  # format 1
                if (
                    claim.in_half is not None
                    and claim[0] in _NEIGHBOUR_DERIVED_TANGENTS
                ):
                    # The hold froze a derived in half: no longer a seam, it
                    # derives again (maintainer 2026-10-11).
                    self._try_key_tangent(crv, tt, {"inTangentType": claim[0]})
                restored += 1
        return restored

    @staticmethod
    def _own_tangent_type(recorded: Optional[str]) -> str:
        """The out-tangent type a released hold restores, from the type its
        claim recorded.  A claim can come from Blender: ``ShotTransfer``
        carries the ledger verbatim, so a pushed scene's holds name Blender
        interpolations -- which ``keyTangent`` refused, silently, and the hold
        stayed stepped once it was no longer a seam.  Mirror of blendertk's
        ``_own_interpolation``: a foreign type maps by kind (CONSTANT steps,
        LINEAR stays linear, any curved segment is ``auto``)."""
        kind = str(recorded or "")
        if kind.islower() or not kind:
            return kind  # Maya's own (every Maya tangent type is lowercase)
        if kind == "CONSTANT":
            return "step"
        return "linear" if kind == "LINEAR" else "auto"

    @classmethod
    def _restore_fixed_out_tangent(cls, crv: str, t: float) -> None:
        """Give a released FIXED out-tangent its slope back from its in side.

        Maya's ``step`` discards the out-tangent's angle and weight, so the
        ``fixed`` type restored alone comes back flat and weightless -- and
        the seam key beside a pin is fixed (``setKeyframe -insert`` fixes the
        inserted key's neighbours, in and out alike, one slope).  Measured
        2026-10-05: a merge that released such a hold played the shot's last
        10 frames 0.8 off.  The out side is refilled from a FIXED in side --
        exact for that one-slope key on an unweighted curve, the angle alone
        on a weighted one, and for a tangent that was broken before its hold
        the continuation of its in side rather than a flat one.  The hold
        broke the tangent (``AnimUtils.step_keys``); it is left broken, as
        every release leaves it.
        """
        tt = (t, t)
        in_type = cmds.keyTangent(crv, q=True, time=tt, inTangentType=True) or [""]
        if in_type[0] != "fixed":
            return  # a derived in side is no record of the out side's slope
        angle = cmds.keyTangent(crv, q=True, time=tt, inAngle=True) or []
        weight = cmds.keyTangent(crv, q=True, time=tt, inWeight=True) or []
        if not angle or not weight:
            return
        # The weight first: a zero-weight out side takes no angle.
        cls._try_key_tangent(crv, tt, {"lock": False})
        cls._try_key_tangent(crv, tt, {"outWeight": weight[0]})
        cls._try_key_tangent(crv, tt, {"outAngle": angle[0]})

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
        claims: list = []
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
                value = cmds.keyframe(crv, q=True, time=tt, valueChange=True) or []
                # The OUT half the step discards (a weighted tangent's weight
                # with it), and the IN half frozen where it plays: derived, it
                # re-derived from the next shot's every move and re-shaped the
                # held shot's last segment (ledger format 2, maintainer
                # 2026-10-11; BACKLOG 2026-10-11, the held tail).
                out_half = AnimUtils.tangent_half(crv, t, "out")
                self._freeze_derived_half(crv, t, "in")
                claims.append(
                    (crv, t, itt, ott[0], value[0] if value else None, out_half)
                )
                step_dict.setdefault(crv, []).append(t)
        if step_dict:
            AnimUtils.step_keys(keys=step_dict, tangent="out")
        for crv, t, itt, ott, value, out_half in claims:
            # The in half as the key carries it once held: the claim knows its
            # key by that and the value (ShotEditLedger.is_same_key).
            in_half = AnimUtils.tangent_half(crv, t, "in")
            led.record_step(
                crv,
                t,
                itt,
                ott,
                value,
                in_half=in_half[1:] if in_half else None,
                out_half=out_half[1:] if out_half else None,
            )
        return sum(len(v) for v in step_dict.values())

    def _pin_bound_keys(self, pins: Dict[str, list]) -> int:
        """Key each curve on the bound frames *pins* names (``{curve: [(frame,
        shot_id, edge), ...]}``) without changing what it plays, and claim
        every key inserted for its bound -- the pythontk hook that stops a gap
        hold where it has to (``ShotSequencer._enforce_gap_holds``).

        The respace pin's writer (:meth:`AnimUtils.insert_keys`, Maya's own
        ``setKeyframe -insert``) under its rules: only inside the curve's key
        range, never on a key, never inside a hold -- and called only for a
        frame :meth:`_pinnable_frames` says it can pin, from one read of the
        curve.  Every pass offers every unheld seam its pin (the pass keeps
        no memory, so no edit by another path can leave one skipped), and
        almost all of them are already pinned or held: measured 2026-10-11 on
        a production assembly, 190 ``insert_keys`` calls per New Shot -- 1.0 s
        of 1.5 -- inserted nothing.  Returns the number of keys inserted.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        inserted = 0
        for crv, bounds in pins.items():
            owners: dict = {}
            for frame, shot_id, edge in bounds:
                owners.setdefault(float(frame), (shot_id, edge))
            frames = self._pinnable_frames(crv, sorted(owners))
            if not frames:
                continue
            for _crv, t in AnimUtils.insert_keys([crv], frames, report=True):
                shot_id, edge = owners.get(float(t), (-1, ""))
                self.ledger.record_key(crv, t, shot_id, edge)
                inserted += 1
        return inserted

    def _pin_bound_poses(
        self, objects, window, pins, attr=None, crossing: bool = False
    ) -> int:
        """Key the pose each of *objects*' (*attr*'s) curves plays on every
        bound frame *pins* names (``[(frame, shot_id, edge), ...]``), on the
        curves keyed inside *window* -- content about to LEAVE a shot, so the
        shots beside it keep what it gave them (the pythontk hook behind
        ``ShotSequencer._hold_neighbour_poses``).

        Where the curve has shape on the bound, the respace pin's writer
        (:meth:`AnimUtils.insert_keys`); where it does not, the pose the
        leaving keys made is keyed outright, because those keys are going:

        * a NEXT shot's start inside a hold -- the held value, stepping on as
          the hold did (a flat hold: flat), the key after it held at its
          slopes so it plays on unchanged;
        * a next shot's start past the last key, or a previous shot's end
          before the first one -- the value the curve's constant
          extrapolation held there.

        A previous shot's end inside a hold is left alone: that hold is its
        own held tail (BACKLOG 2026-10-11, the held tail's derived in-half).
        Every key put in is claimed for its bound, ``stays``.  Returns the
        number of keys put in.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        lo, hi = window
        eps = _BATCH_MOVE_EPS
        curves = sorted(
            {c for obj in objects or () for c in self._anim_curves_of(obj, attr)}
        )
        pinned = 0
        for crv in curves:
            times = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
            if not any(lo <= t <= hi for t in times) and not (
                crossing and times and times[0] < lo and times[-1] > hi
            ):
                continue  # nothing of this curve leaves, nor runs across
            steps = None
            for frame, shot_id, edge in pins:
                frame = float(frame)
                on = [t for t in times if abs(t - frame) <= eps]
                if on:
                    # Keyed: the pose is its own, but a derived slope there is
                    # re-derived from whatever replaces the keys leaving -- the
                    # half facing the neighbour's own content is held (a gap
                    # hold's stepped key's IN half excepted: BACKLOG
                    # 2026-10-11, the held tail).
                    half = "in" if edge == "end" else "out"
                    if half == "in":
                        steps = self.ledger.step_times(crv) if steps is None else steps
                        if any(abs(on[0] - s) <= eps for s in steps):
                            continue
                    self._freeze_derived_half(crv, on[0], half)
                    continue
                put = bool(
                    self._pinnable_frames(crv, [frame])
                    and AnimUtils.insert_keys([crv], [frame], report=True)
                ) or self._key_held_pose(crv, frame, edge, times)
                if put:
                    self.ledger.record_key(crv, frame, shot_id, edge, stays=True)
                    pinned += 1
                    times = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
        return pinned

    def _freeze_left_behind(self, objects, window, attr=None) -> int:
        """Hold the derived slope of the keys a run inside *window* leaves
        behind, on each of *objects*' (*attr*'s) curves keyed there: the key
        before it keeps its IN half, the key after it its OUT half, at the
        angle each plays now (:meth:`_freeze_derived_half`; the pythontk hook
        behind Move to Shot).  Maya derives an auto / spline / clamped
        tangent from both neighbours, so with the run gone the key before it
        re-derived from whatever followed it instead -- measured: a run
        leaving its shot's tail re-shaped the 10 frames into the shot's last
        stayed key, 0.29 off.  A gap hold's stepped key keeps its IN half
        derived (BACKLOG 2026-10-11, the held tail).  Returns the halves held.
        """
        lo, hi = window
        eps = _BATCH_MOVE_EPS
        held = 0
        for obj in objects or ():
            for crv in self._anim_curves_of(obj, attr):
                times = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
                i0 = bisect.bisect_left(times, lo)
                i1 = bisect.bisect_right(times, hi)
                if i1 <= i0:
                    continue  # nothing of this curve leaves
                if i0 > 0 and not any(
                    abs(times[i0 - 1] - s) <= eps for s in self.ledger.step_times(crv)
                ):
                    held += self._freeze_derived_half(crv, times[i0 - 1], "in")
                if i1 < len(times):
                    held += self._freeze_derived_half(crv, times[i1], "out")
        return held

    @classmethod
    def _key_held_pose(cls, crv: str, frame: float, edge: str, times: list) -> bool:
        """Key on *crv* at *frame* the value it holds there with no shape to
        split -- a hold, or constant extrapolation -- for a ``"start"`` bound
        inside or past its keys, or an ``"end"`` bound before its first
        (:meth:`_pin_bound_poses`).  True when a key went in."""
        if not times:
            return False
        if times[0] < frame < times[-1]:
            if edge != "start":
                return False
        elif frame > times[-1]:
            if edge != "start" or cmds.getAttr(f"{crv}.postInfinity") != 0:
                return False
        elif edge != "end" or cmds.getAttr(f"{crv}.preInfinity") != 0:
            return False
        value = cmds.keyframe(crv, q=True, eval=True, time=(frame, frame)) or []
        if not value:
            return False
        out_type = "flat"
        before = [t for t in times if t < frame]
        if before:
            kind = cmds.keyTangent(
                crv, q=True, time=(before[-1], before[-1]), outTangentType=True
            ) or [""]
            if kind[0] in ("step", "stepnext"):
                out_type = kind[0]
        after = [t for t in times if t > frame]
        if after:  # the next key keeps the slopes its old neighbour gave it
            for half in ("in", "out"):
                cls._freeze_derived_half(crv, after[0], half)
        try:
            cmds.setKeyframe(
                crv,
                time=frame,
                value=float(value[0]),
                inTangentType="flat",
                outTangentType=out_type,
            )
        except RuntimeError:
            return False  # locked or referenced curve -- left as it was
        return bool(cmds.keyframe(crv, q=True, time=(frame, frame)))

    @staticmethod
    def _pinnable_frames(crv: str, frames: list, tol: float = 1e-4) -> list:
        """The *frames* :meth:`AnimUtils.insert_keys` could key on *crv*: strictly
        inside its key range, not on a key (within *tol*, insert_keys' own),
        and not after a key that steps.  The cheap half of that writer's
        rules, read from the curve as it is now (mirror of blendertk's
        ``_pinnable``); a frame it lets through may still be a flat hold,
        which ``insert_keys`` declines itself."""
        times = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
        if len(times) < 2:
            return []
        out = []
        for t in frames:
            if not times[0] < t < times[-1]:
                continue
            i = bisect.bisect_right(times, t)
            if abs(times[i - 1] - t) <= tol or abs(times[i] - t) <= tol:
                continue  # already keyed
            left = (times[i - 1], times[i - 1])
            kind = cmds.keyTangent(crv, q=True, time=left, outTangentType=True) or []
            if kind[:1] in (["step"], ["stepnext"]):
                continue  # inside a hold
            out.append(t)
        return out

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
        self, bounds: Optional[Dict[int, tuple]] = None, follow: bool = True
    ) -> tuple:
        """Make every claimed boundary sample follow — or leave — its bound.

        *bounds* (``{shot_id: (start, end)}``) narrows the pass to those
        shots and resolves their claims against the bounds given instead of
        the ones the store holds — the PENDING form
        (:meth:`_reconcile_pending_bounds`), for an edit that has not written
        its new bounds yet.

        *follow* ``False`` never MOVES a sample: one whose bound moved is cut
        when provably redundant and disowned in place otherwise.  The Ctrl
        edge drag's rule -- the bound moves and nothing else does -- where
        following re-timed a pin's ramp (measured: 27 frames of changed
        playback for a 3-frame grow) and, dragged past a gap key, slid the
        pin over it.  Nothing moves in that gesture, so nothing can be in a
        sample's way.  A claim made ``stays`` (a split's seam pin,
        :meth:`_pin_keyless_seams`) takes that rule under every gesture.

        A sample the system created exists for ONE shot bound.  Once that
        bound has moved out from under it, it is neither the animator's pose
        nor a fencepost; leaving it is the clutter that builds up on every
        adjust.  Each claim resolves one of four ways:

        * the key is gone (cut, or moved by an edit that carried the claim
          with it) — drop the claim, on its bound or not;
        * the bound is still under it — nothing to do;
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
            if not self._curve_exists(crv):
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
                # Gone first: a sample deleted ON its bound -- where the system
                # makes them -- would otherwise keep its claim for the next key
                # to land there.
                key_t = self._key_time_at(crv, t, eps)
                if key_t is None:
                    led.release(crv, t)  # the key is gone, and every claim with it
                    continue
                if bound is not None and abs(bound - t) <= eps:
                    continue  # still on its bound
                occupied = (
                    bound is not None and self._key_time_at(crv, bound, eps) is not None
                )
                given_up = bound is not None and (
                    (edge == "end" and key_t > bound + eps)
                    or (edge == "start" and key_t < bound - eps)
                )
                if (
                    occupied
                    and (given_up or not led.key_stays(crv, t))
                    and any(
                        abs(rt - bound) <= eps and (ro, re) == (owner, edge)
                        for rt, ro, re in led.key_records(crv)
                    )
                    and (cmds.keyframe(crv, q=True, keyframeCount=True) or 0) > 2
                ):
                    # Superseded: the bound already has its own sample claimed
                    # for this very edge -- the pose the shot plays there,
                    # pinned before the bound moved (pythontk
                    # ``_hold_edge_neighbours``) -- so this one is a duplicate
                    # fencepost (a ``stays`` one too, once it sits on frames the
                    # shot gave up).  Disowned in place it was the gap's last
                    # key, the gap hold moved onto it, and the shot before
                    # played its held tail 1.18 off (SOL "A01_1", a head shrink
                    # of "A01_2" at gap 10); or the neighbour rippled onto it.
                    try:
                        cmds.cutKey(crv, time=(key_t - eps, key_t + eps), clear=True)
                    except RuntimeError:
                        pass  # locked or referenced curve — leave it as it was
                    else:
                        removed += 1
                        led.release(crv, key_t)
                        continue
                if (
                    follow
                    and bound is not None
                    and not occupied
                    # A seam pin is the shot's own pose: disowned in place,
                    # as follow=False leaves every sample (_pin_keyless_seams).
                    and not led.key_stays(crv, t)
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
                    except RuntimeError:
                        pass  # locked or referenced curve — leave it as it was
                    else:
                        removed += 1
                        led.release(crv, key_t)  # cut: every claim goes with it
                        continue
                led.release_key(crv, key_t)  # kept: disowned; a hold on it stays
        return moved, removed

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
        restore = self._hold_retimed_bounds(
            nodes, old_start, old_end, new_start, new_end
        )
        # A curve two nodes share (a duplicate with input connections) is
        # scaled once: per node, every sharer scaled it again (BACKLOG
        # 2026-10-05: [10, 15] retimed to [10, 20] played [10, 12.5]).
        done: set = set()
        for obj in nodes:
            self.scale_object_keys(
                obj, old_start, old_end, new_start, new_end, _scaled=done
            )
        restore()

    def _hold_retimed_bounds(self, nodes, old_start, old_end, new_start, new_end):
        """Before a retime of ``[old_start, old_end]``: what the neighbours
        play across its bounds, to put back once it is done.  Returns the
        restore (a zero-argument callable).

        The key on each bound faces OUT of the shot with one half -- the
        next shot's opening slope on its end, the previous shot's closing one
        on its start -- and ``scaleKey`` scaled that half with the shot's own.
        A seam the retime's ripple already split left the neighbour's copy
        exactly where the shot's own sample lands: the copy (same pose) is
        cut first and its half is the one put back, so one key holds the seam
        (BACKLOG 2026-10-11: two keys at 60; SOL "A01_1" +8 stacked KEYS).  A
        gap hold's stepped key is the hold's to restore (the held-tail
        decision), and a stepped OUT half is put back by type.
        """
        eps = _BATCH_MOVE_EPS
        held: list = []
        stepped: list = []
        led = self.ledger
        curves = sorted({c for n in nodes for c in self._anim_curves_of(n, None)})
        for crv in curves:
            times = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
            inside = [t for t in times if old_start - eps <= t <= old_end + eps]
            for bound, dest, half in (
                (old_end, new_end, "out"),
                (old_start, new_start, "in"),
            ):
                own = next((t for t in inside if abs(t - bound) <= eps), None)
                if own is None or led.owns_step(crv, own):
                    continue
                src = own
                stack = next(
                    (t for t in times if abs(t - dest) <= eps and t not in inside), None
                )
                if stack is not None:
                    vals = [
                        (
                            cmds.keyframe(crv, q=True, time=(t, t), valueChange=True)
                            or [None]
                        )[0]
                        for t in (own, stack)
                    ]
                    if None not in vals and abs(vals[0] - vals[1]) <= _POSE_TOL:
                        src = stack
                kind = (
                    cmds.keyTangent(crv, q=True, time=(src, src), outTangentType=True)
                    or [""]
                )[0]
                if half == "out" and kind in ("step", "stepnext"):
                    stepped.append((crv, dest, kind))
                else:
                    held += self._held_halves(
                        crv, [(src, half)], dest - src, kinds=self._ANY_TANGENT
                    )
                if (
                    src == stack
                    and (cmds.keyframe(crv, q=True, keyframeCount=True) or 0) > 1
                ):
                    try:
                        cmds.cutKey(crv, time=(stack, stack), clear=True)
                    except RuntimeError:
                        continue  # locked or referenced curve -- left as it was
                    led.release(crv, stack)

        def restore() -> int:
            done = self._hold_halves(held)
            for crv, frame, kind in stepped:
                landed = self._key_time_at(crv, frame, eps)
                if landed is not None:
                    self._try_key_tangent(
                        crv, (landed, landed), {"outTangentType": kind}
                    )
            return done

        return restore

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
        from mayatk.audio_utils._audio_utils import AudioUtils

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
        for events in AudioUtils.read_all_events().values():
            for ev_start, ev_stop in events:
                ev_end = ev_stop if ev_stop is not None else ev_start
                if ev_end > extent:
                    extent = ev_end
        return extent

    # ---- shot lifecycle (delete / merge / split / pad) --------------------

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
        lo, hi, lo_open, _hi_closed = env
        eps = _BATCH_MOVE_EPS
        # The LAST shot's envelope runs to +INF so its trailing content (fade
        # tails past ``end``) belongs to it.  That is the right ownership for a
        # delete too, but the sentinel itself must not reach ``cutKey`` -- cap
        # it at the curve-space bound the audio shifter uses for the same
        # reason.
        if hi >= _PLAN_INF:
            hi = lo + 1.0e7
        # A closing sample shared with a flush next shot is that shot's
        # opening pose too, and the next shot's alone once this one is gone:
        # it stays (the window stops short of it), where the closing rule
        # decides how the next shot lands (maintainer 2026-10-11).  Cut, the
        # next shot opened on whatever came before -- KEYS 80 off over all
        # 81 frames of a production shot.
        window = (lo + eps if lo_open else lo - eps, hi - eps)
        from mayatk.anim_utils._anim_utils import AnimUtils

        names = self._shot_nodes(shot)
        curves = (
            AnimUtils.objects_to_curves(names, through_blends=False) if names else []
        )
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
            led.release(crv, window[0], window[1])
        return cut

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
        """``{curve: [key time, ...]}`` for every curve *plan* will move.

        The writer's own set (:meth:`_content_objects`: every envelope moves
        the whole keyed content), not the moving shots' members: a curve a
        moving shot does not list still has its keys in that shot's window
        moved, and a collision there went unseen -- measured 2026-10-11 on a
        production assembly, a slide landed its shot's closing sample on the
        copy the ripple before it had made, and pushed the copy a frame into
        the next shot ("C04_1" SCREW_UPPER 0.375 off over 59 frames).  A
        CONFLICT there is refused like any other: the writer would land two
        poses on one frame of that curve all the same.
        """
        import maya.cmds as cmds
        from mayatk.anim_utils._anim_utils import AnimUtils

        if not any(move.moves for move in plan.moves.values()):
            return {}
        names = self._content_objects()
        if not names:
            return {}
        curves = AnimUtils.objects_to_curves(sorted(names), through_blends=False) or []
        out: dict = {}
        for crv in sorted(set(curves)):
            times = cmds.keyframe(crv, q=True, timeChange=True) or []
            if times:
                out[crv] = sorted(times)
        return out

    def _pin_keyless_seams(self, opened) -> int:
        """Key the preceding shot's own pose on every KEYLESS seam *opened*
        pulls apart (``[(prev_id, shot_id, boundary, new_start), ...]``,
        :meth:`_opened_seams`), shape-preservingly, and claim each pin as that
        shot's end.

        The one rule (maintainer 2026-10-10): an op on one shot never changes
        what another shot plays inside its own span.  With no key on the
        seam, the segment crossing it belongs to both shots, so the following
        shot's move re-timed it -- the preceding shot's last key gained a
        moved neighbour and its derived tangent re-derived from its first
        frame on, the gap hold then stepped its tail, and the following shot
        opened at a different value (measured 2026-10-07: keys 0/40/100 under
        A [0,50] and B [50,100], B moved 10: A 1.21 off, B 1.10; on a
        production assembly 1 of 16 curves over 4096 samples).  The pin is
        ``setKeyframe -insert`` (:meth:`AnimUtils.insert_keys`: A's evaluated
        value and slope; a hold is declined), so the seam is keyed before
        anything moves and the split below copies it onto the following
        shot's new start like any shared sample; the pin is A's last key, so
        the gap hold carries its pose across the new gap.

        Pinned HERE, at the chokepoint every whole-shot move passes, and not
        where the seam was made: flush keyless seams come from an open set of
        ops (a Split, ``define_shot``, Build over motion, an animator's keys,
        a deleted seam key, scenes saved before any of this), the seam only
        matters once something pulls it apart, and that is always a plan.

        Claimed ``stays`` (``ShotEditLedger.key_stays``): the pin is a pose of
        A's motion, so when A's end later moves it is disowned where it is
        (cut only where provably redundant), as a Ctrl edge drag leaves every
        sample -- followed, it would re-time the ramp it pins.

        Every content curve is offered (:meth:`_content_objects`, what the
        movers carry), not the two shots' members: a segment can cross the
        seam from a key shots away, through shots that list neither its
        object nor any key of it (2026-10-11, a production door ramp across
        30 shots; :meth:`ShotPlanner.keyless_seam`).

        Returns the number of pins inserted.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        pinned = 0
        keyed = None
        for prev_id, shot_id, boundary, _new_start in opened:
            if self.shot_by_id(prev_id) is None or self.shot_by_id(shot_id) is None:
                continue
            if keyed is None:  # read once, only when a seam opens
                content = self._content_objects()
                curves = (
                    AnimUtils.objects_to_curves(content, through_blends=False)
                    if content
                    else []
                )
                keyed = {
                    crv: cmds.keyframe(crv, q=True, timeChange=True) or []
                    for crv in sorted(set(curves or []))
                }
            want = [
                crv
                for crv, times in keyed.items()
                if ShotPlanner.keyless_seam(times, boundary, _BATCH_MOVE_EPS)
            ]
            if not want:
                continue
            for crv, t in AnimUtils.insert_keys(want, [boundary], report=True):
                self.ledger.record_key(crv, t, prev_id, "end", stays=True)
                pinned += 1
        return pinned

    def _pin_held_bounds(self, content) -> int:
        """Pin every shot bound a HOLD spans at its held pose: the respace's
        pin where ``insert_keys`` declines a key.

        A shot's frames after its last key HOLD its pose (the gap hold steps
        that key), so a bound inside the hold has no shape to key -- but the
        two keys around it still do: each derived half facing its own shot
        (the held key's IN, the next key's OUT) is one slope Maya computes
        from both neighbours, the other one across the gap.  The respace's
        bound pin used to fix them as a side effect of keying the curve
        played ON toward the next shot (``setKeyframe -insert`` fixes its
        neighbours); held first (``ShotSequencer.respace``), the tail is a
        hold the pin declines, and moving the next shot re-derived the held
        shot's last segment -- 0.34 off for a 190-frame respace (2026-10-11).
        Each such half is held at its angle instead (``fixed``, broken from
        the other half), so the respace reshapes neither side and the tail
        keeps holding (BACKLOG 2026-10-05; maintainer 2026-10-10).

        Returns the number of halves held.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        bounds = sorted(
            {float(b) for shot in self.store.shots for b in (shot.start, shot.end)}
        )
        curves = AnimUtils.objects_to_curves(content, through_blends=False) or []
        held = 0
        for crv in sorted(set(curves)):
            times = cmds.keyframe(crv, q=True, timeChange=True) or []
            if len(times) < 2:
                continue
            holds = None
            for b in bounds:
                if not times[0] < b < times[-1]:
                    continue
                if any(abs(b - k) <= _BATCH_MOVE_EPS for k in times):
                    continue  # keyed: nothing spans it
                if holds is None:
                    holds = AnimUtils.hold_segments(crv, times)
                i = bisect.bisect_right(times, b) - 1
                if not holds[i]:
                    continue  # shape: insert_keys pinned it
                for t, half in ((times[i], "in"), (times[i + 1], "out")):
                    held += self._freeze_derived_half(crv, t, half)
        return held

    def _hold_moved_boundary_slopes(self, plan, content) -> Callable[[], int]:
        """Snapshot the inward boundary halves of every run *plan* moves --
        each moving shot's first key's OUT, last key's IN -- and return the
        restore, which holds each one that came out changed at its angle.

        The plan writer's form of :meth:`_hold_interior_tangents` (the drag
        path's): a derived tangent is one slope Maya computes from the keys
        on both sides, so a shot moved away from a key it does not take
        along re-derives the half facing its own content, and plays
        differently inside its own span.  Measured 2026-10-11 on a
        production assembly at gap 10: a pose held from frame 12 to "B06_1"
        at 941 re-derived 941's out slope (-2.984 -> -2.996 degrees) on
        every op that moved B06_1 and not frame 12 -- SCREWDRIVER 0.003 to
        0.1 off over its first 46 frames.  Compared after the WHOLE plan,
        so a neighbour that moved by the same delta (every ripple) leaves
        nothing changed and nothing held.  The IN half of a key the gap hold
        steps is left alone: holding it breaks the step claim's key identity
        (BACKLOG 2026-10-11, the held tail's derived in-half).

        Returns the restore; it returns the number of halves it held.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        windows = [w for w in ShotPlanner.move_windows(plan) if abs(w[4]) >= 1e-6]
        if not windows or not content:
            return lambda: 0
        eps = _BATCH_MOVE_EPS
        curves = AnimUtils.objects_to_curves(content, through_blends=False) or []
        spans = [
            (
                lo + eps if lo_open else lo - eps,
                hi + eps if hi_closed else hi - eps,
                delta,
            )
            for lo, hi, lo_open, hi_closed, delta in windows
        ]

        def moves(t: float) -> bool:
            return any(a <= t <= b for a, b, _d in spans)

        held: list = []
        for crv in sorted(set(curves)):
            # One read per curve: every window's run is cut from it.
            keyed = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
            if len(keyed) < 2:
                continue
            steps = self.ledger.step_times(crv)

            def stepped(t: float, steps=steps) -> bool:  # a gap hold's IN half
                return any(abs(t - s) <= eps for s in steps)

            # The keys the runs leave behind re-derive too, facing their own
            # shots: the closing key of a shot a run moved away from (a seam
            # left one frame apart, 2026-10-11) and the opening one of the
            # shot after it.  Gathered once per key: every window a sparse
            # curve keys nothing in names the same two (a ripple of 30 shots
            # read them 30 times).
            still: dict = {}
            for w_lo, w_hi, delta in spans:
                i0 = bisect.bisect_left(keyed, w_lo)
                i1 = bisect.bisect_right(keyed, w_hi)
                halves = []
                if i1 - i0 >= 2:  # a lone key has no interior to protect
                    halves.append((keyed[i0], "out"))
                    if not stepped(keyed[i1 - 1]):
                        halves.append((keyed[i1 - 1], "in"))
                held += self._held_halves(crv, halves, delta, eps)
                if i0 > 0 and not moves(keyed[i0 - 1]) and not stepped(keyed[i0 - 1]):
                    still[(keyed[i0 - 1], "in")] = None
                if i1 < len(keyed) and not moves(keyed[i1]):
                    still[(keyed[i1], "out")] = None
            held += self._held_halves(crv, list(still), 0.0, eps)
        # A half set outright since (a pin, a copy) is its own now.
        return lambda: self._hold_halves(held, kinds=_NEIGHBOUR_DERIVED_TANGENTS)

    @classmethod
    def _freeze_derived_half(cls, crv: str, t: float, half: str) -> int:
        """Hold *half* of *crv*'s key at *t* at its current angle and weight
        when its type is derived (:attr:`_NEIGHBOUR_DERIVED_TANGENTS`),
        breaking the tangent so the other half keeps its own type
        (:meth:`_hold_half`).  Returns 1 when it was held, else 0."""
        return cls._hold_halves(cls._held_halves(crv, [(t, half)]), tol=-1.0)

    def _pose_collisions(self, plan, retimes=()):
        """``(conflicts, merges)`` of the keys *plan* lands on one frame:
        ``conflicts`` ``[(curve, frame, values), ...]`` where two different
        poses meet, ``merges`` ``[(curve, frame, movers, still), ...]`` where
        they agree -- the first mover survives, the rest are cut
        (:meth:`ShotPlanner.key_collisions` per curve the writer moves,
        :meth:`_plan_curves`) -- the analysis behind every refusal
        (:meth:`_reconcile_boundaries`).

        Open intervals, so a shot's own bookend ON a bound is still analysed;
        only what lives strictly between two shots (*retimes*) is deferred.
        """
        from mayatk.anim_utils.shots._shot_plan import ShotPlanner

        windows = ShotPlanner.move_windows(plan)
        if not windows:
            return [], []
        deferred = [(g.lo, g.hi) for g in retimes]
        conflicts: list = []
        merges: list = []
        for crv, keyed in self._plan_curves(plan).items():
            times = [t for t in keyed if not any(lo < t < hi for lo, hi in deferred)]
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
                    merges.append((crv, float(dest), list(movers), list(still)))
        return conflicts, merges

    #: Every tangent type a merged key's half can take over: what it plays,
    #: not only what Maya would re-derive (see :meth:`_merge_halves`).
    _ANY_TANGENT = _NEIGHBOUR_DERIVED_TANGENTS + ("fixed", "linear", "flat")

    @classmethod
    def _merge_halves(cls, merges, held_steps=None) -> tuple:
        """``(held, stepped)``: the halves a lossless merge keeps for each
        ``(curve, frame, movers, still)`` of *merges* -- the merged key's IN
        half from the EARLIEST key landing on the frame, its OUT half from
        the LATEST.  *held* as :meth:`_held_halves` reports angles; a stepped
        OUT half is its type, not an angle, so *stepped* is ``[(curve,
        frame, type), ...]`` (an angle set on a ``stepnext`` made it fixed,
        and a shot's held opening ramped: SOL "C02" 69.5 off, 2026-10-11).
        A survivor a gap hold claims (*held_steps*: ``(curve, time) ->
        bool``) is the hold's: its release puts its own halves back, and a
        half set here first made the release pass it by (SOL, a respace
        0 -> 10 -> 0: "C02_2" 0.17 off).

        Two shots meeting on one frame with the same pose share one key, and
        the one that survives the merge is the first mover -- whose own
        halves face the gap it crossed, not the shot on that side.  The key
        it replaced closed the shot before (or opened the shot after), so
        its slope there is what that shot plays.  Measured 2026-10-11: B
        band-moved flush onto A, the poses agreeing, played A's last 40
        frames 0.67 off on B's opening key's derived in half (0.37 with a
        linear one).  The survivor's own half is read here too, before the
        cut re-derives it against its new neighbour (B 0.59 off).
        """
        held: list = []
        stepped: list = []
        for crv, dest, movers, still in merges:
            if held_steps is not None and held_steps(crv, movers[0]):
                continue
            times = movers + still
            first, last = min(times), max(times)
            held += cls._held_halves(
                crv, [(first, "in")], dest - first, kinds=cls._ANY_TANGENT
            )
            src = cls._key_time_at(crv, last, _BATCH_MOVE_EPS)
            kind = (
                cmds.keyTangent(crv, q=True, time=(src, src), outTangentType=True)
                if src is not None
                else None
            ) or [""]
            if kind[0] in ("step", "stepnext"):
                stepped.append((crv, dest, kind[0]))
            else:
                held += cls._held_halves(
                    crv, [(last, "out")], dest - last, kinds=cls._ANY_TANGENT
                )
        return held, stepped

    def _poses_differ(self, pairs, objects=None) -> List[bool]:
        """Per ``(frame_a, frame_b)`` of *pairs*: whether any content curve --
        or *objects*' (``[(obj, attr or None), ...]``) -- plays a different
        value at the two frames, beyond :data:`_POSE_TOL`: the key there, or
        the hold, interpolation or extrapolation the curve plays where it has
        none.  The pythontk hook behind the gap-0 closing rule
        (``ShotSequencer._close_flush_seams``); evaluated through
        ``MFnAnimCurve``, one read per curve per frame."""
        if cmds is None or not pairs:
            return [False] * len(pairs)
        import maya.api.OpenMaya as om
        import maya.api.OpenMayaAnim as oma
        from mayatk.anim_utils._anim_utils import AnimUtils

        if objects is None:
            names = self._content_objects()
            curves = (
                AnimUtils.objects_to_curves(sorted(names), through_blends=False)
                if names
                else []
            )
        else:
            curves = [
                c for obj, attr in objects for c in self._anim_curves_of(obj, attr)
            ]
        fns = []
        for crv in sorted(set(curves or [])):
            sel = om.MSelectionList()
            try:
                sel.add(crv)
                fns.append(oma.MFnAnimCurve(sel.getDependNode(0)))
            except (RuntimeError, TypeError):
                continue  # gone, or not a curve this API reads
        unit = om.MTime.uiUnit()
        out = []
        for a, b in pairs:
            ta, tb = om.MTime(float(a), unit), om.MTime(float(b), unit)
            out.append(
                any(abs(fn.evaluate(ta) - fn.evaluate(tb)) > _POSE_TOL for fn in fns)
            )
        return out

    def _gaps_keyed(self, windows) -> List[bool]:
        """Per ``(start, end)`` window: whether a content curve -- the curves
        a respace retimes (``ShotApply.retime_gaps``) -- keys a frame inside
        it that is not one of the system's bound samples (``edit_ledger``).
        The pythontk hook behind the respace's closing rule."""
        import bisect

        out = [False] * len(windows)
        if cmds is None or not windows:
            return out
        names = cmds.ls(sorted(self._content_objects()), long=True) or []
        curves = (
            set(cmds.listConnections(names, type="animCurve", s=True, d=False) or [])
            if names
            else set()
        )
        led = self.ledger
        for crv in sorted(curves):
            times = sorted(cmds.keyframe(crv, q=True, timeChange=True) or [])
            for i, (lo, hi) in enumerate(windows):
                if out[i] or hi <= lo:
                    continue
                inside = times[
                    bisect.bisect_left(times, lo) : bisect.bisect_right(times, hi)
                ]
                out[i] = any(not led.owns_key(crv, t) for t in inside)
        return out

    def _retime_closing_gaps(self, retimes) -> int:
        """The pythontk hook behind a slide's closed gap: the shrink stage of
        ``ShotApply.retime_gaps`` over the scene's content, before anything
        moves, the ledger's claims with the keys."""
        if cmds is None or not retimes:
            return 0
        from mayatk.anim_utils.shots._shot_apply import ShotApply

        return ShotApply.retime_gaps(
            retimes, self._content_objects(), after_move=False, ledger=self.ledger
        )

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
          timing.  Only curves with a key after the boundary -- the segment
          the following shot plays, keyed in it or not -- get a copy; a
          curve that only holds the pose on needs none.  A derived tangent (:attr:`_NEIGHBOUR_DERIVED_TANGENTS`)
          on the sample is held too: the original's IN half and the copy's
          OUT half, each at the angle it had while shared -- after the split
          each has a new neighbour, and Maya re-derives the slope from it
          (measured: a spline sample played 0.343 off in the preceding shot
          and 0.197 off in the following one; :meth:`_hold_half`).
          A sample with no key before it -- nothing the preceding shot
          plays into -- is not copied but CARRIED: re-keyed at the new start, its OUT half held the same way
          (it too re-derives against a neighbour that is now further away),
          and only then cut from its old frame (:meth:`_cut_carried_sample`).
        * **Merge** (gap collapsed).  Two samples converge on one frame.
          Maya would neither refuse nor overwrite — it stacks a duplicate a
          fraction of a frame away, and the pair then travels together
          forever — so the loser is cut here, before the move, leaving the
          destination clear.  When the two poses disagree the merge is
          lossy no matter who wins, so the whole operation is refused
          (:class:`ShotBoundaryConflict`) before it writes anything.

        Every stage reads the curves the writer moves -- the whole keyed
        content (:meth:`_content_objects`), not the moving shots' members:
        a collision is predicted for every key inside a moving window, and
        that only matches what the writer does over the same set.  (It once
        read the members, back-filled with everything keyed in the window;
        the back-fill went and the narrower read stayed, 2026-10-11.)

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
        conflicts, merges = self._pose_collisions(plan, retimes)
        if conflicts:
            raise ShotBoundaryConflict(conflicts)
        losers = [
            (crv, t) for crv, _d, movers, still in merges for t in movers[1:] + still
        ]
        merged, merged_steps = self._merge_halves(merges, self.ledger.owns_step)

        # ---- keyless seams: pin the preceding shot's own pose first --------
        # A seam the split opens with NO key on it is a segment both shots
        # share, so the move re-timed the preceding shot from its first frame.
        # Pinned, it is a keyed seam: the capture below copies it.
        self._pin_keyless_seams(self._opened_seams(plan))

        # ---- splits: capture while the shared sample still exists ---------
        # Over every content curve, not the following shot's members: a shot
        # PLAYS a segment it keys nothing of (2026-10-11, a production door
        # ramp through 30 flush shots), and the sample opening it is its pose.
        captures: list = []
        content_curves = None
        for prev_id, shot_id, boundary, new_start in ShotPlanner.boundary_splits(
            self.store, plan
        ):
            shot = self.shot_by_id(shot_id)
            prev_shot = self.shot_by_id(prev_id)
            if shot is None or prev_shot is None:
                continue
            # The shared sample is the preceding shot's, so it moves with that
            # shot's content -- by its delta, or not at all.
            prev_move = plan.moves.get(prev_id)
            prev_delta = (
                prev_move.delta if prev_move is not None and prev_move.moves else 0.0
            )
            # A window the plan moves the following shot by that reaches back
            # over the seam (a ripple carrying the gap: Move to Shot's room)
            # takes the sample with that shot -- it is not left behind.
            next_move = plan.moves.get(shot_id)
            if next_move is not None and not next_move.moves:
                next_move = None
            if content_curves is None:
                content = self._content_objects()
                content_curves = sorted(
                    set(
                        AnimUtils.objects_to_curves(content, through_blends=False)
                        if content
                        else []
                    )
                )
            for crv in content_curves:
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
                if next_move is not None and ShotPlanner.in_window(
                    key_t,
                    next_move.env_start,
                    next_move.env_end,
                    next_move.env_lo_open,
                    next_move.env_hi_closed,
                    _BATCH_MOVE_EPS,
                ):
                    continue  # it rides the following shot's own window
                at = (
                    cmds.keyframe(crv, q=True, time=(key_t, key_t), valueChange=True)
                    or []
                )
                if not at:
                    continue
                # The following shot plays the segment from the sample to the
                # next key, wherever that key is.
                all_times = cmds.keyframe(crv, q=True, timeChange=True) or []
                if not any(t > key_t + _BATCH_MOVE_EPS for t in all_times):
                    continue  # nothing after: the curve holds the pose on
                tangents = (
                    cmds.keyTangent(
                        crv, q=True, time=(key_t, key_t), itt=True, ott=True
                    )
                    or []
                )
                # Shared wherever the PRECEDING shot plays into it: a key
                # before it, however far back -- the ramp from there is that
                # shot's motion (the one rule; the old test, a key inside the
                # preceding shot, carried the sample off a ramp it closed:
                # 0.2 to 0.6 off, 2026-10-11).  With nothing before it the
                # sample was never shared -- it is the following shot's
                # opening pose alone, the curve holds it before, so it
                # travels with that shot instead of being duplicated and left
                # behind on a curve its neighbour has no stake in.
                shared = any(t < key_t - _BATCH_MOVE_EPS for t in all_times)
                if shared and self.ledger.owns_key(crv, key_t):
                    # The sample stays the preceding shot's closing pose, so a
                    # claim on it serves THAT shot's end from here, whoever it
                    # was made for.  Left naming the following shot's start (a
                    # copy an earlier split made), deleting the preceding shot
                    # never found it, and the ripple landed the following
                    # shot over it: 0.0 held mid-ramp (measured 2026-09-23).
                    stays = self.ledger.key_stays(crv, key_t)
                    self.ledger.release_key(crv, key_t)
                    self.ledger.record_key(crv, key_t, prev_id, "end", stays=stays)
                captures.append(
                    (
                        crv,
                        float(new_start),
                        float(at[0]),
                        tuple(tangents),
                        shared,
                        key_t + prev_delta,
                        # The halves a split disturbs, FIXED ones too: re-keyed
                        # with setKeyframe and set fixed, a copy freezes the slope
                        # Maya derives at its new frame, not the original's (a
                        # keyless seam's pin is all fixed, and its copy opened
                        # the next shot ~1.0 off until angle and weight came).
                        {
                            half: got
                            for half in ("in", "out")
                            if (
                                got := self._derived_half(
                                    crv,
                                    key_t,
                                    half,
                                    _NEIGHBOUR_DERIVED_TANGENTS + ("fixed",),
                                )
                            )
                        },
                        # A copy is the system's; a carried sample stays whose
                        # it was -- claimed, an animator's opening pose would be
                        # skipped by content scans and moved or cut with the bound.
                        shared or self.ledger.owns_key(crv, key_t),
                    )
                )
                # A carried sample is NOT cut here with the merge losers: see
                # the end of _finish.

        # Never cut a curve down to nothing: Maya deletes a keyless animCurve
        # node, which would take the connection with it.  A cut key's claims
        # go with it: the move remaps only the keys it finds, so a claim left
        # on the frame is inherited by whatever lands there next.
        for crv, t in losers:
            if (cmds.keyframe(crv, q=True, keyframeCount=True) or 0) > 1:
                try:
                    cmds.cutKey(crv, time=(t, t), clear=True)
                except RuntimeError:
                    continue  # locked or referenced curve — leave it as it was
                self.ledger.release(crv, t)

        if not captures and not merged and not merged_steps:
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
            # A merged key closes the earlier side and opens the later one.
            self._hold_halves(merged)
            for crv, frame, kind in merged_steps:
                landed = self._key_time_at(crv, frame, _BATCH_MOVE_EPS)
                if landed is not None:
                    self._try_key_tangent(
                        crv, (landed, landed), {"outTangentType": kind}
                    )
            for crv, frame, value, tangents, shared, original, held, claim in captures:
                if not cmds.objExists(crv):
                    continue
                occupied = cmds.keyframe(
                    crv,
                    q=True,
                    time=(frame - _BATCH_MOVE_EPS, frame + _BATCH_MOVE_EPS),
                    keyframeCount=True,
                )
                keyed = bool(occupied)  # something already landed here
                if not occupied:
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
                        keyed = True
                        if claim:
                            led.record_key(crv, frame, owners.get(frame, -1), "start")
                        # Each side plays on as it did before the gap opened:
                        # the following shot's opening pose (a copy, or the
                        # carried sample itself) and, where it stays, the
                        # preceding shot's closing one.
                        if shared and "in" in held:
                            self._hold_half(crv, original, "in", held["in"])
                        if "out" in held:
                            self._hold_half(crv, frame, "out", held["out"])
                        # A FIXED in half (a seam pin's) goes onto the copy as
                        # well: it faces the held gap, so it plays nothing
                        # while the gap is open -- and when the gap closes
                        # again the merge keeps the copy, which then closes
                        # the preceding shot exactly as the pin did
                        # (measured: 0.20 off when it kept its own slope).
                        if shared and tangents[:1] == ("fixed",) and "in" in held:
                            self._hold_half(crv, frame, "in", held["in"])
                if not shared and keyed:
                    # Only now does a carried sample leave its old frame, and
                    # only once the new start holds a pose. Cut before the
                    # moves, its absence re-derived the following shot's first
                    # key while the move was snapshotting that key's tangents,
                    # and _hold_interior_tangents then pinned the damaged
                    # angle (measured: 0.48 degrees where the curve had -1.43,
                    # the shot 0.099 off).
                    self._cut_carried_sample(crv, original, value, led)

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
            # A bound a HOLD spans gets no key (insert_keys declines one): it
            # is pinned at its held pose instead (_pin_held_bounds).
            self._pin_held_bounds(content)
            if pinned:
                logging.getLogger(__name__).debug(
                    "Respace: pinned %d shot-boundary key(s) so each shot's "
                    "content is its own.",
                    len(pinned),
                )

        hold_slopes = (
            self._hold_moved_boundary_slopes(plan, content)
            if cmds is not None
            else (lambda: 0)
        )
        finish = self._reconcile_boundaries(plan, retimes) if cmds is not None else None
        if retimes:
            ShotApply.retime_gaps(
                retimes, content, after_move=False, ledger=self.ledger
            )
        # Every envelope moves the whole keyed content, so nothing keyed
        # inside a moving shot is left behind whatever its member list says
        # -- and the list is not written to.  It used to be BACKFILLED with
        # everything keyed in the envelope to get the same guarantee, which
        # made a baked rig's forty proxy joints members of all twelve shots.
        ShotApply.apply(self.store, plan, objects=content or None)
        if finish is not None:
            finish()

        if retimes:
            ShotApply.retime_gaps(retimes, content, after_move=True, ledger=self.ledger)
        hold_slopes()
