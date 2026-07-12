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
from typing import Callable, Iterable, Optional

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

from pythontk.core_utils.engines.shots.shot_apply import apply as _engine_apply

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
                tid for tid in track_ids
                if cmds.keyframe(
                    f"{carrier}.{audio_utils.attr_for(tid)}",
                    q=True, keyframeCount=True,
                )
            ]
        dirty: set = set()

        def _move_keys(objects, env_lo, env_hi, delta, over=False):
            _batch_move_keys(cmds, objects, env_lo, env_hi, delta, over=over)

        def _shift_audio(env_lo, env_hi, delta):
            tids = _shift_audio_range(env_lo, env_hi, delta, track_ids=track_ids)
            if tids:
                dirty.update(tids)

        _engine_apply(
            plan,
            store,
            move_keys=_move_keys,
            shift_audio=_shift_audio,
            progress_callback=progress_callback,
        )

        if dirty:
            b.mark_dirty(dirty)
