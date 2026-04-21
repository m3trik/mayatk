# !/usr/bin/python
# coding=utf-8
"""Range resolution algorithm for the Shot Manifest.

Converts user-entered ranges + gap-detected boundaries into a fully
resolved ``(step_id, start, end, is_user)`` list for every build step.
This module is pure logic â€” no Qt or Maya imports.
"""
from typing import Dict, List, Optional, Tuple

from mayatk.anim_utils.shots.shot_manifest._shot_manifest import BuilderStep
from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
    prune_to_top_boundaries,
)


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
) -> List[Tuple[str, float, Optional[float], bool]]:
    """Compute a resolved ``(start, end)`` for every step.

    Merges user-entered ranges with gap-detected auto-fill boundaries.

    Parameters
    ----------
    steps
        Ordered list of build steps from CSV or detection.
    user_ranges
        Map of ``step_id â†’ (start, end_or_None)`` for user-entered values.
    gap_starts
        Detected animation-region start frames (pre-sorted).
    gap_end_map
        Map of ``region_start â†’ region_end`` for detected regions that
        have an explicit end (e.g. from ``zero_as_end`` mode).
    gap
        Inter-shot gap in frames (from ShotStore settings).
    use_selected_keys
        When ``True``, steps without a matching detected region are
        skipped rather than placed sequentially.
    last_resolved
        Previously resolved list â€” entries before *from_step_idx* are
        reused as a frozen prefix.
    from_step_idx
        Only re-resolve from this index onward.
    default_duration
        When positive and no animation regions are detected, each step
        is assigned this uniform duration instead of behavior-derived
        durations.  Set to ``0`` to use the old behavior.

    Returns
    -------
    list[tuple[str, float, float | None, bool]]
        ``(step_id, start, end_or_None, is_user)`` in step order.
    """
    if not steps:
        return []

    from mayatk.anim_utils.shots.shot_manifest.behaviors import compute_duration

    # When use_selected_keys is active and no regions were found,
    # abort instead of falling through to sequential placement.
    if use_selected_keys and not gap_starts:
        return []

    # When more regions than steps, keep only the largest
    # boundaries so each step maps to a major animation section.
    if len(gap_starts) > len(steps):
        gap_starts = prune_to_top_boundaries(gap_starts, len(steps))

    # Build the resolved list
    resolved: List[Tuple[str, float, Optional[float], bool]] = []
    gap_idx = 0
    # When no animation is detected and a default_duration is set, use
    # uniform placement so steps get sensible ranges (e.g. 200f each).
    use_default = default_duration > 0 and not gap_starts and not use_selected_keys

    cursor = 0.0 if use_default else 1.0  # start at 0 for default ranges
    cursor_forced = False  # True once a user range advances cursor

    # Frozen prefix: reuse last-resolved values for steps before from_step_idx
    if from_step_idx > 0 and last_resolved:
        for i in range(min(from_step_idx, len(last_resolved), len(steps))):
            resolved.append(last_resolved[i])
        # Advance cursor past the frozen prefix
        if resolved:
            _, _, prev_end, _ = resolved[-1]
            if prev_end is not None:
                cursor = prev_end + gap
                cursor_forced = True
        # Advance gap_idx past gaps consumed by the frozen prefix
        for gs in gap_starts:
            if gs < cursor:
                gap_idx += 1
            else:
                break

    for i, step in enumerate(steps):
        if i < len(resolved):
            continue  # already in frozen prefix

        user = user_ranges.get(step.step_id)
        if user is not None:
            start, end = user
            resolved.append((step.step_id, start, end, True))
            # Advance cursor past this user-defined range
            if end is not None:
                cursor = end + gap
            else:
                cursor = start + compute_duration(step.objects) + gap
            cursor_forced = True
        elif gap_starts and gap_idx < len(gap_starts):
            # If a prior user range pushed the cursor past this gap
            # boundary, place the step at the cursor instead so
            # downstream steps never overlap with earlier ranges.
            raw_start = gap_starts[gap_idx]
            start = max(raw_start, cursor) if cursor_forced else raw_start
            # Preserve the detected end (e.g. from zero_as_end mode)
            # so that gaps aren't collapsed to next_start - gap.
            # However, if the step was pushed past its detected end,
            # the original end is no longer valid for this position.
            detected_end = gap_end_map.get(raw_start)
            if detected_end is not None and start > detected_end:
                detected_end = None
            gap_idx += 1
            resolved.append((step.step_id, start, detected_end, False))
            if detected_end is not None:
                cursor = detected_end + gap
            else:
                cursor = start + compute_duration(step.objects) + gap
        else:
            # In selected-keys mode, do not fabricate ranges for
            # steps that have no corresponding detected region.
            if use_selected_keys:
                continue
            # Sequential placement from cursor.
            # In use_default mode, still consult compute_duration so audio
            # (from_source) and behavior-derived durations drive per-step
            # sizing; default_duration is only the fallback for steps with
            # no resolvable behavior duration.
            start = cursor
            if use_default:
                dur = compute_duration(step.objects, fallback=default_duration)
            else:
                dur = compute_duration(step.objects)
            resolved.append((step.step_id, start, None, False))
            cursor = start + dur + gap

    # Second pass: resolve None ends as next_start - gap (or last key)
    step_by_id = {s.step_id: s for s in steps}
    for i in range(len(resolved)):
        step_id, start, end, is_user = resolved[i]
        if end is None:
            if i + 1 < len(resolved):
                end = resolved[i + 1][1] - gap
            else:
                step_obj = step_by_id.get(step_id)
                objs = step_obj.objects if step_obj else []
                if use_default:
                    end = start + compute_duration(
                        objs, fallback=default_duration
                    )
                else:
                    end = start + compute_duration(objs)
        resolved[i] = (step_id, start, end, is_user)

    return resolved
