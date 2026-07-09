# coding=utf-8
"""Commit resolved :class:`MovePlan`\\ s to the Maya scene.

Core shot-layer primitive that walks a plan built by
:mod:`mayatk.anim_utils.shots._shot_plan` and commits it in the plan's
predetermined execution order inside a single audio batch.

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
from typing import Callable, Iterable, Optional

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.anim_utils.shots._shot_plan import MovePlan, _INF, _content_top


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


def _batch_move_keys(
    cmds,
    objects: Iterable[str],
    env_start: float,
    env_end: float,
    delta: float,
    over: bool = False,
) -> None:
    """Shift keys inside the half-open envelope window by ``delta``.

    The query range is ``[env_start - eps, env_end - eps]`` so a key
    exactly on ``env_end`` (the next shot's start) stays with the next
    shot instead of being claimed by two envelopes.

    ``over=True`` uses ``option="over"`` so keys may pass neighboring
    keys on the same curve.  The park/land moves need it — they teleport
    a shot's keys across other shots' content on shared curves, and the
    default ``"move"`` semantics silently clamp at the first neighbor,
    stranding keys just short of it.  Ordered (phase-1) moves keep the
    default: the plan's topological order guarantees they never cross.
    """
    if not objects or abs(delta) < 1e-6:
        return
    long_names = cmds.ls(list(objects), long=True) or []
    if not long_names:
        return
    curves = (
        cmds.listConnections(long_names, type="animCurve", s=True, d=False) or []
    )
    curves = list(set(curves))
    if not curves:
        return

    tr = (env_start - _ENVELOPE_SLOP, env_end - _ENVELOPE_SLOP)
    option = "over" if over else "move"
    for crv in curves:
        if not cmds.keyframe(crv, q=True, time=tr):
            continue
        try:
            cmds.keyframe(
                crv, edit=True, relative=True, timeChange=delta, time=tr,
                option=option,
            )
        except RuntimeError:
            pass


def _shift_audio_range(
    env_start: float,
    env_end: float,
    delta: float,
    track_ids=None,
):
    """Shift audio keys whose timeline position falls within the envelope.

    Note: ``env_end`` extends to the next shot's current start (not just
    the current shot's ``end``), so audio keys sitting in the trailing
    gap travel with the preceding shot.  This mirrors the keyframe
    fade-tail fix — the old ``[shot.start, shot.end]`` window left gap
    audio stranded.  If a future use case requires gap audio to stay
    put, separate the audio envelope from the keyframe envelope here.

    The upper bound passed to ``audio_utils.shift_keys_in_range`` is
    pre-deflated by :data:`_AUDIO_UPPER_MARGIN` so its internal ±1e-3
    range inflation can't double-claim a key sitting exactly on a
    shot boundary — see the constant's comment for why.
    """
    if abs(delta) < 1e-6:
        return []
    from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

    hi = env_end if env_end < _INF else env_start + 1.0e7
    hi -= _AUDIO_UPPER_MARGIN
    if hi <= env_start:
        return []
    tids = audio_utils.shift_keys_in_range(
        env_start, hi, delta, track_ids=track_ids
    )
    return tids or []


def apply(
    store: ShotStore,
    plan: MovePlan,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> None:
    """Execute ``plan`` against the scene and ``store``.

    Walks the plan's precomputed sequence, moves object keys and audio
    inside each shot's owned envelope, then commits the new shot
    bounds.  All Maya writes happen inside one audio batch so derived
    DG audio nodes re-render exactly once.

    Shots in ``plan.parked`` (collision cycles — mixed-sign deltas) get
    a three-phase treatment: their keys are first shifted out of the
    way by ``plan.park_offset``, the normal sequence runs, then each
    parked shot lands at its final position.

    When Maya is unavailable only the in-memory bounds are committed.

    ``progress_callback`` (when given) is invoked once per shot with
    ``(current, total, message)``.
    """
    if not plan.sequence and not plan.parked:
        return

    total = len(plan.sequence) + len(plan.parked)
    park = plan.park_offset

    # The timeline-last shot's envelope end is the +INF sentinel (``_INF`` =
    # 1e9) so its trailing content travels with it.  Propagating that literal
    # 1e9 into a move window — and, after parking, into ``1e9 + park`` — is
    # doubly hazardous: (a) at that magnitude Maya's frame->tick conversion
    # loses ~1e-7 of precision, so a boundary key can fall on the wrong side
    # of a query range; and (b) the unbounded window sweeps already-parked
    # content into a second shift, stranding it past its phase-2 landing.
    # When any shot is parked, cap +INF at the plan's real content top plus
    # one frame.  Every real key lives at or below ``_content_top``; the park
    # zone sits ``+1000`` frames above it (see :func:`_shot_plan._park_offset`),
    # so a capped window clears the park zone by ~1000 frames — a wide,
    # precision-safe margin rather than a fragile sub-frame slop — while
    # still covering all of the last shot's own content.
    cap = _content_top(plan.moves) + 1.0 if plan.parked else _INF

    def _capped(env_end: float) -> float:
        return env_end if env_end < _INF / 2 else cap

    if cmds is not None:
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
                    tid for tid in track_ids
                    if cmds.keyframe(
                        f"{carrier}.{audio_utils.attr_for(tid)}",
                        q=True, keyframeCount=True,
                    )
                ]
            dirty: set = set()
            shots_by_id = {s.shot_id: s for s in store.shots}

            # Phase 0 — park cycle members' keys beyond every envelope.
            for shot_id in plan.parked:
                move = plan.moves[shot_id]
                shot = shots_by_id.get(shot_id)
                if shot is None:
                    continue
                _batch_move_keys(
                    cmds, shot.objects, move.env_start, _capped(move.env_end), park,
                    over=True,
                )
                tids = _shift_audio_range(
                    move.env_start, _capped(move.env_end), park, track_ids=track_ids
                )
                if tids:
                    dirty.update(tids)

            # Phase 1 — ordered moves.
            for i, shot_id in enumerate(plan.sequence):
                if progress_callback:
                    progress_callback(i, total, f"Applying shot: {shot_id}")
                move = plan.moves[shot_id]
                shot = shots_by_id.get(shot_id)
                if shot is None:
                    continue
                _batch_move_keys(
                    cmds, shot.objects, move.env_start, _capped(move.env_end), move.delta
                )
                tids = _shift_audio_range(
                    move.env_start, _capped(move.env_end), move.delta, track_ids=track_ids
                )
                if tids:
                    dirty.update(tids)
                shot.start = move.new_start
                shot.end = move.new_end

            # Phase 2 — land parked shots at their final positions.
            for i, shot_id in enumerate(plan.parked):
                if progress_callback:
                    progress_callback(
                        len(plan.sequence) + i, total, f"Applying shot: {shot_id}"
                    )
                move = plan.moves[shot_id]
                shot = shots_by_id.get(shot_id)
                if shot is None:
                    continue
                _batch_move_keys(
                    cmds,
                    shot.objects,
                    move.env_start + park,
                    _capped(move.env_end) + park,
                    move.delta - park,
                    over=True,
                )
                tids = _shift_audio_range(
                    move.env_start + park,
                    _capped(move.env_end) + park,
                    move.delta - park,
                    track_ids=track_ids,
                )
                if tids:
                    dirty.update(tids)
                shot.start = move.new_start
                shot.end = move.new_end

            if dirty:
                b.mark_dirty(dirty)
    else:
        shots_by_id = {s.shot_id: s for s in store.shots}
        for i, shot_id in enumerate(plan.sequence + plan.parked):
            if progress_callback:
                progress_callback(i, total, f"Applying shot: {shot_id}")
            move = plan.moves[shot_id]
            shot = shots_by_id.get(shot_id)
            if shot is not None:
                shot.start = move.new_start
                shot.end = move.new_end

    # Bounds were written directly (not via update_shot) — flag the
    # store so the mutation survives a scene save.
    store.mark_dirty()

    if progress_callback and total:
        progress_callback(total, total, "Done")
