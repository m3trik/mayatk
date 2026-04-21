# coding=utf-8
"""Pure planning layer for multi-shot topology transformations.

NO MAYA IMPORTS — this module is part of the shot *model* layer and
must not import ``pymel`` or ``maya.cmds``.  It computes WHAT should
move WHERE given a :class:`ShotStore`, producing a :class:`MovePlan`
dataclass with no side effects.  Anything that actually writes to the
Maya scene lives in the sibling module :mod:`_shot_apply`.

Two-stage discipline for every multi-shot operation:
    1. Build a :class:`MovePlan` from the current :class:`ShotStore`.
    2. Hand the plan to :func:`_shot_apply.apply`.

The split exists because interleaved resolve → mutate loops (the old
``respace`` / ``_ripple_*`` shape) corrupted keyframes when a shot's
new envelope overlapped an unmoved neighbor's old envelope.  Keeping
planning pure makes that bug unwritable here.

Location rationale: this module sits beside :mod:`_shots` at the shot
package root because the plan is a *model-layer* description of how
shots transform — independent of any committer.  Future consumers
beyond the sequencer (dry-run preview, undo description, manifest
bulk reshape) can use it without reaching into ``shot_sequencer/``.

The core shots layer is therefore complete on its own: :mod:`_shots`
models the topology, :mod:`_shot_plan` resolves transformations, and
:mod:`_shot_apply` commits them.
"""
from dataclasses import dataclass, field
from typing import Dict, List

from mayatk.anim_utils.shots._shots import ShotStore


# Sentinel used for an unbounded envelope edge on the last shot.
_INF = 1.0e9
_EPS = 1.0e-6


@dataclass
class ShotMove:
    """A single shot's source and destination ranges.

    ``env_start`` / ``env_end`` describe the *owned* keyframe window —
    typically ``[old_start, next_shot.old_start)``.  Extending the
    window to the next shot's start ensures fade tails that live in
    the trailing gap travel with their owning shot rather than being
    stranded by a tight ``[old_start, old_end]`` key window.
    """

    shot_id: int
    old_start: float
    old_end: float
    new_start: float
    new_end: float
    env_start: float
    env_end: float

    @property
    def delta(self) -> float:
        return self.new_start - self.old_start

    @property
    def moves(self) -> bool:
        return abs(self.delta) > _EPS


@dataclass
class MovePlan:
    """Resolved multi-shot timeline mutation.

    ``moves`` is keyed by ``shot_id`` and covers every shot considered
    by the planner.  ``sequence`` is the execution order the executor
    must honour to avoid transient envelope collisions.  Only shots
    that actually move appear in ``sequence``.
    """

    moves: Dict[int, ShotMove] = field(default_factory=dict)
    sequence: List[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sequence (collision-safe topo sort)
# ---------------------------------------------------------------------------


def _overlaps(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> bool:
    return a_lo < b_hi and b_lo < a_hi


def _plan_sequence(moves: Dict[int, ShotMove]) -> List[int]:
    """Topo-sort shot_ids so each shot moves before any shot whose new
    envelope lands inside its current (old) envelope.

    If j's *new* envelope overlaps i's *old* envelope then executing j
    first would deposit j's keys inside i's source window and corrupt
    i's subsequent read.  The correct order is therefore i → j.

    Only shots whose ``moves`` flag is True are returned.  Raises
    :class:`ValueError` when the dependency graph contains a cycle —
    which would require a temporary parking pass no current caller
    needs.
    """
    active = [m for m in moves.values() if m.moves]
    if not active:
        return []

    incoming: Dict[int, int] = {m.shot_id: 0 for m in active}
    outgoing: Dict[int, List[int]] = {m.shot_id: [] for m in active}

    for i in active:
        for j in active:
            if i.shot_id == j.shot_id:
                continue
            j_new_lo = j.env_start + j.delta
            j_new_hi = j.env_end + j.delta
            if _overlaps(j_new_lo, j_new_hi, i.env_start, i.env_end):
                outgoing[i.shot_id].append(j.shot_id)
                incoming[j.shot_id] += 1

    ready = [sid for sid, deg in incoming.items() if deg == 0]
    out: List[int] = []
    while ready:
        sid = ready.pop()
        out.append(sid)
        for nxt in outgoing[sid]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                ready.append(nxt)

    if len(out) != len(active):
        raise ValueError(
            "shot move plan contains a collision cycle — "
            "temp-parking pass required"
        )
    return out


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def _envelope_for(sorted_shots: List, index: int) -> tuple:
    """Return ``(env_start, env_end)`` for the shot at ``sorted_shots[index]``.

    Envelope rule: ``env_start`` is the shot's own ``start``; ``env_end``
    is the next shot's ``start`` if one exists, otherwise ``+INF`` so a
    final shot's trailing content (including fade tails) travels with it.
    """
    shot = sorted_shots[index]
    env_start = shot.start
    env_end = (
        sorted_shots[index + 1].start if index + 1 < len(sorted_shots) else _INF
    )
    return env_start, env_end


# ---------------------------------------------------------------------------
# Plan constructors
# ---------------------------------------------------------------------------


def plan_respace(
    store: ShotStore, gap: float, start_frame: float
) -> MovePlan:
    """Build a plan that lays shots out sequentially with uniform gaps.

    Locked gaps preserve their current width.  Durations are preserved;
    only start frames change.  All new positions are snapped through
    ``store.snap`` so the in-memory model stays integer-clean.
    """
    shots = store.sorted_shots()
    if not shots:
        return MovePlan()

    locked_widths: dict = {}
    for i in range(len(shots) - 1):
        if store.is_gap_locked(shots[i].shot_id, shots[i + 1].shot_id):
            locked_widths[i] = max(0.0, shots[i + 1].start - shots[i].end)

    moves: Dict[int, ShotMove] = {}
    cursor = start_frame
    for i, shot in enumerate(shots):
        duration = shot.end - shot.start
        new_start = store.snap(cursor)
        new_end = store.snap(new_start + duration)
        env_start, env_end = _envelope_for(shots, i)
        moves[shot.shot_id] = ShotMove(
            shot_id=shot.shot_id,
            old_start=shot.start,
            old_end=shot.end,
            new_start=new_start,
            new_end=new_end,
            env_start=env_start,
            env_end=env_end,
        )
        effective_gap = locked_widths.get(i, gap)
        cursor = new_end + effective_gap

    return MovePlan(moves=moves, sequence=_plan_sequence(moves))


def plan_ripple_downstream(
    store: ShotStore,
    pivot_shot_id: int,
    after_frame: float,
    delta: float,
) -> MovePlan:
    """Build a plan that shifts every shot starting at or after
    ``after_frame`` by ``delta`` frames.

    The pivot shot is excluded — the caller's primary edit already
    placed it.  Snapping is applied to the resulting bounds.
    """
    shots = store.sorted_shots()
    if not shots or abs(delta) < _EPS:
        return MovePlan()

    moves: Dict[int, ShotMove] = {}
    for i, shot in enumerate(shots):
        if shot.shot_id == pivot_shot_id:
            continue
        if shot.start < after_frame:
            continue
        env_start, env_end = _envelope_for(shots, i)
        moves[shot.shot_id] = ShotMove(
            shot_id=shot.shot_id,
            old_start=shot.start,
            old_end=shot.end,
            new_start=store.snap(shot.start + delta),
            new_end=store.snap(shot.end + delta),
            env_start=env_start,
            env_end=env_end,
        )

    return MovePlan(moves=moves, sequence=_plan_sequence(moves))


def plan_ripple_upstream(
    store: ShotStore,
    pivot_shot_id: int,
    before_frame: float,
    delta: float,
) -> MovePlan:
    """Build a plan that shifts every shot ending at or before
    ``before_frame`` by ``delta`` frames.
    """
    shots = store.sorted_shots()
    if not shots or abs(delta) < _EPS:
        return MovePlan()

    moves: Dict[int, ShotMove] = {}
    for i, shot in enumerate(shots):
        if shot.shot_id == pivot_shot_id:
            continue
        if shot.end > before_frame + _EPS:
            continue
        env_start, env_end = _envelope_for(shots, i)
        moves[shot.shot_id] = ShotMove(
            shot_id=shot.shot_id,
            old_start=shot.start,
            old_end=shot.end,
            new_start=store.snap(shot.start + delta),
            new_end=store.snap(shot.end + delta),
            env_start=env_start,
            env_end=env_end,
        )

    return MovePlan(moves=moves, sequence=_plan_sequence(moves))
