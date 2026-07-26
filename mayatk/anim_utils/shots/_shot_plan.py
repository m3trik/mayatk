# coding=utf-8
"""Pure planning layer for multi-shot topology transformations (facade).

The planner lives once, DCC-agnostic, in
:mod:`pythontk.core_utils.engines.shots.shot_plan` (shared with blendertk);
this module re-exports it so mayatk-internal imports and the public path
``mayatk.anim_utils.shots._shot_plan`` keep working.

Two-stage discipline for every multi-shot operation:
    1. Build a :class:`MovePlan` from the current :class:`ShotStore`.
    2. Hand the plan to :func:`_shot_apply.apply`.

The split exists because interleaved resolve → mutate loops (the old
``respace`` / ``_ripple_*`` shape) corrupted keyframes when a shot's
new envelope overlapped an unmoved neighbor's old envelope.  Keeping
planning pure makes that bug unwritable here.
"""

from pythontk.core_utils.engines.shots.shot_plan import (  # noqa: F401
    _EPS,
    _INF,
    MovePlan,
    ShotMove,
    ShotPlanner,
)

__all__ = [
    "ShotMove",
    "MovePlan",
    "ShotPlanner",
]
