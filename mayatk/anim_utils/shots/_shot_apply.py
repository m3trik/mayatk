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

Maya-soft: mirrors :mod:`_shots` in using a ``try: import pymel``
guard.  When Maya is unavailable only in-memory shot bounds are
committed, matching the graceful-degradation contract of the rest of
the shot model.
"""
from typing import Iterable

try:
    import pymel.core as pm
except ImportError as error:
    pm = None
    print(__file__, error)

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


def _batch_move_keys(
    cmds, objects: Iterable[str], env_start: float, env_end: float, delta: float
) -> None:
    """Shift keys inside the half-open envelope window by ``delta``.

    The query range is ``[env_start - eps, env_end - eps]`` so a key
    exactly on ``env_end`` (the next shot's start) stays with the next
    shot instead of being claimed by two envelopes.
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
    for crv in curves:
        if not cmds.keyframe(crv, q=True, time=tr):
            continue
        try:
            cmds.keyframe(
                crv, edit=True, relative=True, timeChange=delta, time=tr
            )
        except RuntimeError:
            pass


def _shift_audio_range(env_start: float, env_end: float, delta: float):
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
    tids = audio_utils.shift_keys_in_range(env_start, hi, delta)
    return tids or []


def apply(store: ShotStore, plan: MovePlan) -> None:
    """Execute ``plan`` against the scene and ``store``.

    Walks the plan's precomputed sequence, moves object keys and audio
    inside each shot's owned envelope, then commits the new shot
    bounds.  All Maya writes happen inside one audio batch so derived
    DG audio nodes re-render exactly once.

    When Maya is unavailable only the in-memory bounds are committed.
    """
    if not plan.sequence:
        return

    if pm is not None:
        import maya.cmds as cmds
        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        with audio_utils.batch() as b:
            dirty: set = set()
            for shot_id in plan.sequence:
                move = plan.moves[shot_id]
                shot = store.shot_by_id(shot_id)
                if shot is None:
                    continue
                _batch_move_keys(
                    cmds, shot.objects, move.env_start, move.env_end, move.delta
                )
                tids = _shift_audio_range(move.env_start, move.env_end, move.delta)
                if tids:
                    dirty.update(tids)
                shot.start = move.new_start
                shot.end = move.new_end
            if dirty:
                b.mark_dirty(dirty)
    else:
        for shot_id in plan.sequence:
            move = plan.moves[shot_id]
            shot = store.shot_by_id(shot_id)
            if shot is not None:
                shot.start = move.new_start
                shot.end = move.new_end
