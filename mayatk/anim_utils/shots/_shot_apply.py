# coding=utf-8
"""Commit resolved :class:`MovePlan`\\ s to the Maya scene.

The three-phase walk (park / ordered / land, +INF envelope capping) lives
once in :func:`pythontk.core_utils.engines.shots.shot_apply.apply`; this
module supplies the Maya *writer strategies* — the keyframe shifter
(:func:`_batch_move_keys`) and the audio-track shifter
(:func:`_shift_audio_range`) — and wraps the whole run in a single audio
batch so derived DG audio nodes re-render exactly once.

Layering: this sits beside :mod:`_shots` (model) and :mod:`_shot_plan`
(planner) so the core shots system is complete — it can describe AND
commit shot transformations without reaching up into the sequencer
package.  Downstream consumers (sequencer orchestrator, dry-run tools,
undo stacks) call :func:`apply` instead of re-implementing Maya writes.

Maya-soft: mirrors :mod:`_shots` in guarding the ``maya.cmds`` import.
When Maya is unavailable only in-memory shot bounds are committed,
matching the graceful-degradation contract of the rest of the shot
model.
"""

import logging
from typing import Any, Callable, Iterable, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    # Maya-soft: planner/tests import this module headless (see module doc).
    cmds = None
    logging.getLogger(__name__).debug("maya.cmds unavailable — headless mode")

from pythontk.core_utils.engines.shots.shot_apply import ShotApply as _PyShotApply

# The engine's single commit entry point (mayatk's ``ShotApply`` wraps it with
# Maya-bound writer callables). ``apply`` moved onto the engine class.
_engine_apply = _PyShotApply.apply

from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.anim_utils.shots._shot_plan import MovePlan, _INF


# Half-frame slop used so a key exactly on the upper envelope boundary
# belongs to the next shot rather than both.  Named distinctly from
# :data:`_shot_plan._EPS` (1e-6, used for delta-significance tests) —
# different scale, different purpose, same file neighborhood.
_ENVELOPE_SLOP = 1.0e-3

# ``audio_utils.shift_keys_in_range`` internally inflates the caller's
# range by ±1e-3 on both sides (float-safety for its own queries).
# That breaks our half-open envelope semantic: a key at ``env_end`` (=
# next shot's ``start``) ends up claimed by both envelopes and gets
# shifted twice on a round trip.  Deflating the upper bound we pass by
# this margin cancels the internal slop and preserves the [start, end)
# convention on the audio side.
_AUDIO_UPPER_MARGIN = 3.0e-3


class _ShotApplyInternal(object):
    """Internal helpers for ShotApply."""

    @staticmethod
    def _batch_move_keys(
        cmds,
        objects: Iterable[str],
        env_start: float,
        env_end: float,
        delta: float,
        over: bool = False,
        lo_open: bool = False,
        hi_closed: bool = False,
    ) -> list:
        """Shift keys inside the envelope window by ``delta``.

        Each bound is deflated by :data:`_ENVELOPE_SLOP` to exclude a sample
        sitting exactly on it and inflated to include one, per the fencepost
        flags: contiguous shots share a sample, and it belongs to the
        PRECEDING shot (``hi_closed`` on that shot, ``lo_open`` on the next).
        With a gap both bounds deflate, which is the old half-open
        ``[env_start, env_end)`` behaviour.  Adjacent shots always set the
        flags consistently, so no sample is queried by two envelopes and
        none falls between them.

        ``over=True`` uses ``option="over"`` so keys may pass neighboring
        keys on the same curve.  The park/land moves need it — they teleport
        a shot's keys across other shots' content on shared curves, and the
        default ``"move"`` semantics silently clamp at the first neighbor,
        stranding keys just short of it.  Ordered (phase-1) moves keep the
        default: the plan's topological order guarantees they never cross.

        Returns ``[(curve, [key_time, ...]), ...]`` -- the keys it actually
        moved, per curve, so the caller can carry the shot system's
        edit-ledger claims on exactly those keys.  It used to return the
        WINDOW instead, and the ledger inflated that window by its own
        epsilon -- which undid the fencepost deflation and shifted the claim
        on a bound sample whose key had stayed put (measured 2026-09-06: a
        Move to Shot's end pin kept its key at 70 while its claim read 80).
        """
        if not objects or abs(delta) < 1e-6:
            return []
        long_names = cmds.ls(list(objects), long=True) or []
        if not long_names:
            return []
        curves = (
            cmds.listConnections(long_names, type="animCurve", s=True, d=False) or []
        )
        curves = list(set(curves))
        if not curves:
            return []

        tr = (
            env_start + _ENVELOPE_SLOP if lo_open else env_start - _ENVELOPE_SLOP,
            env_end + _ENVELOPE_SLOP if hi_closed else env_end - _ENVELOPE_SLOP,
        )
        option = "over" if over else "move"
        moved = []
        for crv in curves:
            times = cmds.keyframe(crv, q=True, time=tr, timeChange=True)
            if not times:
                continue
            try:
                cmds.keyframe(
                    crv,
                    edit=True,
                    relative=True,
                    timeChange=delta,
                    time=tr,
                    option=option,
                )
            except RuntimeError:
                continue
            moved.append((crv, [float(t) for t in times]))
        return moved

    @staticmethod
    def _scale_gap_keys(
        cmds,
        objects: Iterable[str],
        lo: float,
        hi: float,
        scale: float,
    ) -> Tuple[int, int]:
        """Retime the keys strictly INSIDE ``(lo, hi)`` about *lo*.

        Both bounds are deflated so the flanking shots' own bookend keys are
        never touched -- they belong to the shots and move rigidly with them,
        and scaling one would change the shot it bounds.

        ``scale == 0`` -- a gap collapsed to nothing -- is REFUSED rather than
        applied, and the count comes back so the caller can say so. There is no
        non-destructive answer available: scaling to zero width stacks every
        key onto the boundary frame (Maya does not refuse that, it stores a
        silent near-duplicate a fraction of a frame away and the pair travels
        together forever), and cutting them is deleting the artist's keys to
        make an operation succeed. This system's answer to a collapse it cannot
        perform losslessly is already an explicit refusal
        (:class:`ShotBoundaryConflict`, raised before any write when the two
        shots' boundary poses disagree); leaving the keys where they are and
        reporting it keeps that promise instead of quietly discarding data.

        Returns the number of curves it moved, and separately how many it
        declined for a collapsed gap: ``(moved, declined)``.
        """
        if not objects:
            return 0, 0
        long_names = cmds.ls(list(objects), long=True) or []
        if not long_names:
            return 0, 0
        curves = list(
            set(
                cmds.listConnections(long_names, type="animCurve", s=True, d=False)
                or []
            )
        )
        window = (lo + _ENVELOPE_SLOP, hi - _ENVELOPE_SLOP)
        if window[1] <= window[0]:
            return 0, 0
        moved = declined = 0
        for crv in curves:
            if not cmds.keyframe(crv, q=True, time=window):
                continue
            if scale <= 0.0:
                declined += 1
                continue
            try:
                cmds.scaleKey(crv, time=window, timePivot=lo, timeScale=scale)
            except RuntimeError:
                continue  # locked or referenced curve — leave it as it was
            moved += 1
        return moved, declined

    @staticmethod
    def _shift_audio_range(
        env_start: float,
        env_end: float,
        delta: float,
        track_ids=None,
        lo_open: bool = False,
        hi_closed: bool = False,
    ):
        """Shift audio keys whose timeline position falls within the envelope.

        Note: ``env_end`` extends to the next shot's current start (not just
        the current shot's ``end``), so audio keys sitting in the trailing
        gap travel with the preceding shot.  This mirrors the keyframe
        fade-tail fix — the old ``[shot.start, shot.end]`` window left gap
        audio stranded.  If a future use case requires gap audio to stay
        put, separate the audio envelope from the keyframe envelope here.

        Both bounds are adjusted by :data:`_AUDIO_UPPER_MARGIN` so
        ``audio_utils.shift_keys_in_range``'s internal ±1e-3 inflation
        cannot double-claim a clip sitting exactly on a shot boundary —
        see the constant's comment for why.  Which way each bound moves
        follows the fencepost flags: contiguous shots share a sample and it
        belongs to the preceding shot, so that shot inflates its upper
        bound to keep it and the next shot inflates its lower bound to
        decline it.  With a gap both bounds deflate, the old behaviour.
        """
        if abs(delta) < 1e-6:
            return []
        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        hi = env_end if env_end < _INF else env_start + 1.0e7
        hi += _AUDIO_UPPER_MARGIN if hi_closed else -_AUDIO_UPPER_MARGIN
        lo = env_start + _AUDIO_UPPER_MARGIN if lo_open else env_start
        if hi <= lo:
            return []
        tids = audio_utils.shift_keys_in_range(lo, hi, delta, track_ids=track_ids)
        return tids or []


class ShotApply(_ShotApplyInternal):
    """ShotApply — module namespace."""

    @staticmethod
    def pin_shot_bounds(store: ShotStore, objects: Iterable[str], report: bool = False):
        """Give every shot a key on both of its bounds, changing nothing.

        The precondition that makes a multi-shot move safe. A shot's content is
        only its own while a key sits on each end of it: without that, the
        segment spanning a boundary is shared with whatever is on the other
        side, and moving that neighbour retimes the shared segment -- reaching
        back into frames that never moved, and further still through auto
        tangents. Measured on a 12-shot production assembly, a respace changed
        42 of the 109 frames of a shot whose position did not change at all.

        Shape-preserving (:meth:`AnimUtils.insert_keys`, i.e. Maya's own
        ``setKeyframe -insert``) and idempotent, so this is lossless on a scene
        that is already pinned and on one that never needed it.

        Returns the number of keys inserted, or -- with ``report=True`` --
        the ``[(curve, time), ...]`` it inserted, which the caller needs in
        order to claim them in the shot system's edit ledger.
        """
        if cmds is None:
            return [] if report else 0
        from mayatk.anim_utils._anim_utils import AnimUtils

        targets = list(objects)
        bounds = sorted(
            {float(b) for shot in store.shots for b in (shot.start, shot.end)}
        )
        if not targets or not bounds:
            return [] if report else 0
        return AnimUtils.insert_keys(targets, bounds, report=report)

    @staticmethod
    def retime_gaps(
        retimes: Iterable[Any], objects: Iterable[str], after_move: bool
    ) -> int:
        """Scale each gap's content into the width the plan gives that gap.

        Called TWICE around the shot moves, and which gaps run when is the
        whole trick -- a scale is only safe while the timeline it is writing
        into is empty:

        * **Shrinking gaps, BEFORE the moves.** The content compresses toward
          the gap's left edge, so it lands inside the gap it is already in.
        * **Growing gaps, AFTER the moves.** By then the gap's content has
          travelled with the preceding shot and the following shot has opened
          the room, so the wider target is empty. Doing this one first would
          push content into the following shot's still-unmoved content.

        Either way no key ever crosses a shot's content, which is what lets
        this run without the park/land machinery the shot moves need.

        *objects* is the scene's CONTENT, not the flanking shot's object list.
        The list would be the intuitive choice and is wrong: membership is
        backfilled only for shots that MOVE, so a stationary shot's list is
        whatever the store happened to hold -- and a gap whose content it does
        not name would be left behind while the shot after it moved away,
        which is the exact stranding this whole pass exists to prevent. Naming
        extra objects costs nothing: the window is the gap's interior, and a
        curve with no key in it is skipped.

        Returns the number of curves moved.
        """
        if cmds is None:
            return 0
        targets = list(objects)
        moved = stranded = 0
        for gap in retimes:
            if gap.grows is not after_move:
                continue
            # The gap's content travels with the preceding shot, so after the
            # moves it sits one delta further along -- and its left edge is
            # that shot's new end. Its WIDTH is unchanged either way: the move
            # was rigid, and changing the width is what this call is for.
            lo = gap.lo + (gap.left_delta if after_move else 0.0)
            hi = lo + gap.width
            one, declined = _ShotApplyInternal._scale_gap_keys(
                cmds, targets, lo, hi, gap.scale
            )
            moved += one
            stranded += declined
        if stranded:
            # Said out loud because the alternative was deleting those keys:
            # the operation succeeded, and content that used to sit between
            # two shots is now inside one of them.
            logging.getLogger(__name__).warning(
                "Respace: %d curve(s) have keys in a gap that collapsed to "
                "zero width. They were left where they are rather than cut -- "
                "use a gap of at least 1 frame to keep them between the shots.",
                stranded,
            )
        return moved

    @staticmethod
    def apply(
        store: ShotStore,
        plan: MovePlan,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        objects: Optional[Iterable[str]] = None,
    ) -> None:
        """Execute ``plan`` against the scene and ``store``.

        *objects*, when given, is what every envelope moves -- the scene's
        keyed content -- instead of each shot's own member list; the
        envelopes partition the timeline, so one list serves them all and a
        curve with no key in a window costs nothing.

        Delegates the three-phase park / ordered / land walk (including the
        +INF-envelope capping) to the engine's
        :func:`~pythontk.core_utils.engines.shots.shot_apply.apply`, passing
        :func:`_batch_move_keys` / :func:`_shift_audio_range` as the Maya writer
        strategies.  All Maya writes happen inside one audio batch so derived
        DG audio nodes re-render exactly once.

        When Maya is unavailable only the in-memory bounds are committed
        (the engine's bounds-only path).

        ``progress_callback`` (when given) is invoked once per shot with
        ``(current, total, message)``.
        """
        if cmds is None:
            _engine_apply(plan, store, progress_callback=progress_callback)
            return
        if not plan.sequence and not plan.parked:
            return

        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch() as b:
            # Hoist the carrier's track list to once-per-apply, then drop
            # fully-empty tracks.  The per-shot range queries inside
            # shift_keys_in_range can't hit a key on a track that has none,
            # so filtering once here saves O(empty_tracks × shots) wasted
            # keyframe queries on plans with many shots.
            carriers = audio_utils.find_carriers()
            carrier = carriers[0] if carriers else None
            track_ids = audio_utils.list_tracks(carrier) if carrier else []
            if carrier and track_ids:
                track_ids = [
                    tid
                    for tid in track_ids
                    if cmds.keyframe(
                        f"{carrier}.{audio_utils.attr_for(tid)}",
                        q=True,
                        keyframeCount=True,
                    )
                ]
            dirty: set = set()

            def _move_keys(objects, env_lo, env_hi, delta, over=False, **window):
                # The writer reports the KEYS it actually moved per curve so
                # the ledger's claims travel with exactly those -- never with
                # a bound sample the fencepost rule left in place.
                for crv, times in _ShotApplyInternal._batch_move_keys(
                    cmds, objects, env_lo, env_hi, delta, over=over, **window
                ):
                    store.edit_ledger.remap(crv, [(t, t + delta) for t in times])

            def _shift_audio(env_lo, env_hi, delta, **window):
                tids = _ShotApplyInternal._shift_audio_range(
                    env_lo, env_hi, delta, track_ids=track_ids, **window
                )
                if tids:
                    dirty.update(tids)

            content = None if objects is None else list(objects)
            _engine_apply(
                plan,
                store,
                move_keys=_move_keys,
                shift_audio=_shift_audio,
                progress_callback=progress_callback,
                objects_for=None if content is None else (lambda _sid: content),
            )

            if dirty:
                b.mark_dirty(dirty)
