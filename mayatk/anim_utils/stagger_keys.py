# coding=utf-8
"""Dedicated stagger-keys module to keep AnimUtils lean and testable."""
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya environment required
    print(__file__, error)

import pythontk as ptk

# Import CoreUtils using internal path to avoid circular imports
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils.anim_structs import AnimPlan, ShiftOperation


class _StaggerKeysInternal:
    """Internal helper methods for StaggerKeys - shifting logic only.

    Grouping and filtering logic is delegated to SegmentKeys.
    """

    @staticmethod
    def _apply_plan(plan: AnimPlan) -> int:
        """Execute the stagger plan."""
        shifted_count = 0
        for op in plan.operations:
            if isinstance(op, ShiftOperation):
                try:
                    # Determine range args
                    range_args = {}
                    if op.time_range:
                        range_args["time"] = op.time_range

                    # If no time range specified, we might want to query the curve range
                    # But ShiftOperation usually implies shifting the whole curve if range is None
                    # However, pm.keyframe without time arg shifts everything.

                    for curve in op.curves:
                        pm.keyframe(
                            curve,
                            edit=True,
                            relative=True,
                            timeChange=op.offset,
                            **range_args,
                        )
                    shifted_count += 1
                except RuntimeError as e:
                    pm.warning(f"Failed to move keys for {op.curves}: {e}")
        return shifted_count

    @classmethod
    def _calculate_stagger_plan(
        cls,
        groups_data: List[dict],
        start_frame: float,
        spacing: Union[int, float] = 0,
        use_intervals: bool = False,
        avoid_overlap: bool = False,
        preserve_gaps: bool = False,
    ) -> AnimPlan:
        """Generate an AnimPlan for staggering."""
        plan = AnimPlan()

        if use_intervals:
            # Fixed interval mode: place animations at regular frame intervals
            previous_end = None

            for i, data in enumerate(groups_data):
                group_start = data["start"]
                duration = data["duration"]

                target_start = start_frame + (i * spacing)

                # Check for overlap if avoid_overlap is enabled
                if avoid_overlap and previous_end is not None:
                    if target_start < previous_end:
                        overlap_count = 1
                        while target_start < previous_end:
                            target_start = (
                                start_frame + (i * spacing) + (overlap_count * spacing)
                            )
                            overlap_count += 1

                shift_amount = target_start - group_start

                if abs(shift_amount) > 1e-6:
                    if "sub_groups" in data:
                        for sub in data["sub_groups"]:
                            plan.operations.append(
                                ShiftOperation(
                                    curves=sub.get("curves", []),
                                    offset=shift_amount,
                                    time_range=sub.get("segment_range"),
                                )
                            )
                    else:
                        plan.operations.append(
                            ShiftOperation(
                                curves=data.get("curves", []),
                                offset=shift_amount,
                                time_range=data.get("segment_range"),
                            )
                        )

                previous_end = target_start + duration
        else:
            # Sequential stagger mode: animations placed end-to-end with spacing offset
            current_frame = start_frame

            for data in groups_data:
                group_start = data["start"]
                duration = data["duration"]

                # Calculate spacing in frames
                # If spacing is between -1.0 and 1.0, treat as percentage of duration
                if -1.0 < spacing < 1.0:
                    spacing_frames = duration * spacing
                else:
                    spacing_frames = spacing

                # If preserving gaps, ensure we don't pull back
                if preserve_gaps:
                    current_frame = max(current_frame, group_start)

                shift_amount = current_frame - group_start

                if abs(shift_amount) > 1e-6:
                    if "sub_groups" in data:
                        for sub in data["sub_groups"]:
                            plan.operations.append(
                                ShiftOperation(
                                    curves=sub.get("curves", []),
                                    offset=shift_amount,
                                    time_range=sub.get("segment_range"),
                                )
                            )
                    else:
                        plan.operations.append(
                            ShiftOperation(
                                curves=data.get("curves", []),
                                offset=shift_amount,
                                time_range=data.get("segment_range"),
                            )
                        )

                # Update current frame for next object/group
                current_frame = current_frame + duration + spacing_frames

        return plan


class StaggerKeys:
    """Class containing keyframe staggering operations."""

    @staticmethod
    @CoreUtils.undoable
    def stagger_keys(
        objects: list,
        start_frame: int = None,
        spacing: Union[int, float] = 0,
        use_intervals: bool = False,
        avoid_overlap: bool = False,
        smooth_tangents: bool = False,
        invert: bool = False,
        group_overlapping: bool = False,
        ignore: Union[str, List[str]] = None,
        split_static: bool = True,
        verbose: bool = False,
    ):
        """Stagger the keyframes of selected objects with various positioning controls.

        If keys are selected, only those keys are staggered. If no keys are selected,
        all keys are staggered.

        Parameters:
            objects: List of objects whose keyframes need to be staggered.
            start_frame: Override starting frame. If None, uses earliest keyframe.
            spacing: Controls how animations are spaced. Behavior depends on use_intervals:

                When use_intervals=False (sequential stagger, default):
                    - Positive value: Gap in frames between animations (e.g., 10 = 10 frame gap)
                    - Zero: End-to-start with no gap (default)
                    - Negative value: Overlap in frames (e.g., -5 = 5 frames of overlap)
                    - Float between -1.0 and 1.0: Percentage of animation duration
                      (e.g., 0.5 = 50% of duration gap, -0.3 = 30% overlap)

                When use_intervals=True (fixed intervals):
                    - Places each animation at regular frame intervals
                      (e.g., spacing=100 → animations start at frames 0, 100, 200, 300...)
                    - avoid_overlap can skip to next interval if needed

            use_intervals: If True, uses spacing as fixed frame intervals instead of
                sequential offsets.
            avoid_overlap: Only applies when use_intervals=True. If an animation would
                overlap with the previous one, skip to the next interval position.
            smooth_tangents: If True, adjusts tangents for smooth transitions.
            invert: If True, the objects list is processed in reverse order.
            group_overlapping: If True, treats objects with overlapping keyframes as a
                single block. Objects in the same group will be moved together.
            ignore: Attribute name(s) to ignore when staggering (e.g., 'visibility').
                Curves connected to these attributes will not be moved.
            split_static: If True, treats segments of animation separated by static gaps
                (flat keys) as separate groups. Each segment will be staggered independently.
            verbose: If True, prints detailed information including original time ranges.
        """
        # Import shared helpers
        from mayatk.anim_utils._anim_utils import AnimUtils
        from mayatk.anim_utils.segment_keys import SegmentKeys

        if not objects:
            pm.warning("No objects provided.")
            return

        objects = pm.ls(objects, type="transform", flatten=True)
        if invert:
            objects = list(reversed(objects))

        # Stage 1: Collect segments using KeyframeGrouper
        # Note: We need custom collection here because stagger supports selected_keys
        obj_keyframe_data = []
        first_keyframe = None
        last_keyframe = None

        for obj in objects:
            # Get animation curves - check for selected keys first
            selected_curves = pm.keyframe(obj, query=True, name=True, selected=True)

            # Determine which curves to use based on whether keys are selected
            if selected_curves:
                curves_to_use = SegmentKeys._filter_curves_by_ignore(
                    selected_curves, ignore
                )
                keyframes = AnimUtils.get_keyframe_times(
                    curves_to_use, mode="selected", from_curves=True
                )
            else:
                all_curves = (
                    pm.listConnections(obj, type="animCurve", s=True, d=False) or []
                )
                curves_to_use = SegmentKeys._filter_curves_by_ignore(all_curves, ignore)
                keyframes = AnimUtils.get_keyframe_times(
                    curves_to_use, from_curves=True
                )

            if keyframes:
                # If split_static is enabled, break the object's animation into active segments
                segments = []
                if split_static:
                    segments = SegmentKeys._get_active_animation_segments(curves_to_use)

                # If no segments found, treat as one block
                if not segments:
                    segments = [(keyframes[0], keyframes[-1])]

                for seg_start, seg_end in segments:
                    obj_keyframe_data.append(
                        {
                            "obj": obj,
                            "curves": curves_to_use,
                            "keyframes": keyframes,
                            "start": seg_start,
                            "end": seg_end,
                            "duration": seg_end - seg_start,
                            "segment_range": (seg_start, seg_end),
                        }
                    )

                if first_keyframe is None or keyframes[0] < first_keyframe:
                    first_keyframe = keyframes[0]
                if last_keyframe is None or keyframes[-1] > last_keyframe:
                    last_keyframe = keyframes[-1]

        if not obj_keyframe_data:
            pm.warning("No keyframes found on the provided objects.")
            return

        # Capture original ranges if verbose
        original_ranges = []
        if verbose:
            original_ranges = SegmentKeys.get_time_ranges(obj_keyframe_data)

        # Stage 2: Group segments if requested using SegmentKeys
        if group_overlapping:
            obj_keyframe_data = SegmentKeys._group_by_overlap(obj_keyframe_data)

        # Use provided start_frame or earliest keyframe
        base_frame = start_frame if start_frame is not None else first_keyframe

        # Apply stagger logic
        plan = _StaggerKeysInternal._calculate_stagger_plan(
            obj_keyframe_data,
            start_frame=base_frame,
            spacing=spacing,
            use_intervals=use_intervals,
            avoid_overlap=avoid_overlap,
            preserve_gaps=False,
        )

        _StaggerKeysInternal._apply_plan(plan)

        if smooth_tangents:
            for data in obj_keyframe_data:
                curves = data.get("curves", [])
                if curves:
                    # Use shared helper for smart tangent setting
                    AnimUtils._set_smart_tangents(curves, tangent_type="auto")

        if verbose and original_ranges:
            # Print original ranges
            SegmentKeys.print_time_ranges(
                original_ranges,
                header="Original Time Ranges:",
                per_segment=split_static,
            )

            # Capture and print new ranges
            # Re-collect segments to get updated times
            # Note: We need to re-collect using the same logic as above
            new_segments = []
            for obj in objects:
                # Get animation curves - check for selected keys first
                selected_curves = pm.keyframe(obj, query=True, name=True, selected=True)

                if selected_curves:
                    curves_to_use = SegmentKeys._filter_curves_by_ignore(
                        selected_curves, ignore
                    )
                else:
                    all_curves = (
                        pm.listConnections(obj, type="animCurve", s=True, d=False) or []
                    )
                    curves_to_use = SegmentKeys._filter_curves_by_ignore(
                        all_curves, ignore
                    )

                if curves_to_use:
                    if split_static:
                        active_segments = SegmentKeys._get_active_animation_segments(
                            curves_to_use
                        )
                        for seg_start, seg_end in active_segments:
                            new_segments.append(
                                {
                                    "obj": obj,
                                    "start": seg_start,
                                    "end": seg_end,
                                }
                            )
                    else:
                        # Get full range
                        times = AnimUtils.get_keyframe_times(
                            curves_to_use, from_curves=True
                        )
                        if times:
                            new_segments.append(
                                {
                                    "obj": obj,
                                    "start": times[0],
                                    "end": times[-1],
                                }
                            )

            new_ranges = SegmentKeys.get_time_ranges(new_segments)
            SegmentKeys.print_time_ranges(
                new_ranges,
                header="New Time Ranges:",
                per_segment=split_static,
            )
