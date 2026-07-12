# !/usr/bin/python
# coding=utf-8
"""Range resolution for the Shot Manifest build pipeline (Maya-bound facade).

The resolver math lives once, DCC-agnostic, in
:mod:`pythontk.core_utils.engines.shots.manifest.range_resolver` (shared with
blendertk).  This facade binds its injectable ``duration_fn`` to mayatk's
:func:`~mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration` —
the Maya-bound one that probes audio sources against the scene FPS and
resolves registered track paths — so audio steps size to their clip length
exactly as before the extraction.
"""
from typing import Callable, Dict, List, Optional, Tuple

from pythontk.core_utils.engines.shots.manifest.range_resolver import (  # noqa: F401
    prune_to_top_boundaries,
)
from pythontk.core_utils.engines.shots.manifest.range_resolver import (
    resolve_ranges as _engine_resolve_ranges,
)

from mayatk.anim_utils.shots.shot_manifest._shot_manifest import BuilderStep


def resolve_ranges(
    steps: List[BuilderStep],
    user_ranges: Dict[str, Tuple[Optional[float], Optional[float]]],
    gap_starts: List[float],
    gap_end_map: Dict[float, float],
    gap: float,
    use_selected_keys: bool,
    last_resolved: List[Tuple[str, float, Optional[float], bool]],
    from_step_idx: int = 0,
    default_duration: float = 0,
    duration_fn: Optional[Callable[..., float]] = None,
) -> List[Tuple[str, float, Optional[float], bool]]:
    """Compute a resolved ``(start, end)`` for every step.

    See :func:`pythontk.core_utils.engines.shots.manifest.range_resolver.resolve_ranges`
    for the full parameter reference.  When *duration_fn* is ``None`` the
    Maya-bound ``behaviors.compute_duration`` is injected (imported lazily
    from the package so the established mock seam keeps working).
    """
    if duration_fn is None:
        from mayatk.anim_utils.shots.shot_manifest.behaviors import compute_duration

        duration_fn = compute_duration
    return _engine_resolve_ranges(
        steps,
        user_ranges,
        gap_starts,
        gap_end_map,
        gap,
        use_selected_keys,
        last_resolved,
        from_step_idx=from_step_idx,
        default_duration=default_duration,
        duration_fn=duration_fn,
    )
