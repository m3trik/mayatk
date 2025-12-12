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


class _StaggerKeysInternal:
    """Internal helper methods for StaggerKeys - shifting logic only.

    Grouping and filtering logic is delegated to KeyframeGrouper.
    """

    @staticmethod
    def _shift_curves_by_amount(
        curves: List["pm.PyNode"],
        shift_amount: float,
        time_range: Optional[Tuple[float, float]] = None,
    ) -> int:
        """Helper method to shift a list of animation curves by a given amount.

        Parameters:
            curves: List of animation curve nodes to shift.
            shift_amount: Number of frames to shift the curves by.
            time_range: Specific range of keys to shift.

        Returns:
            Number of curves successfully shifted.
        """
        shifted_count = 0
        for curve in curves:
            # Determine range to shift
            if time_range:
                range_args = {"time": time_range}
            else:
                curve_keyframes = pm.keyframe(curve, query=True, timeChange=True)
                if not curve_keyframes:
                    continue
                range_args = {"time": (min(curve_keyframes), max(curve_keyframes))}

            try:
                pm.keyframe(
                    curve,
                    edit=True,
                    relative=True,
                    timeChange=shift_amount,
                    **range_args,
                )
                shifted_count += 1
            except RuntimeError as e:
                pm.warning(f"Failed to move keys for {curve}: {e}")
        return shifted_count

    @classmethod
    def _apply_stagger(
        cls,
        groups_data: List[dict],
        start_frame: float,
        spacing: Union[int, float] = 0,
        use_intervals: bool = False,
        avoid_overlap: bool = False,
        preserve_gaps: bool = False,
    ) -> int:
        """Apply staggering logic to a list of grouped keyframe data.

        Shared logic used by stagger_keyframes and scale_keys (for overlap prevention).

        Parameters:
            groups_data: List of dicts with 'start', 'duration', 'curves' keys.
            start_frame: The frame to start the sequence from.
            spacing: Gap/overlap amount.
            use_intervals: Fixed interval mode.
            avoid_overlap: Skip intervals to avoid overlap (interval mode only).
            preserve_gaps: If True, only shifts forward to prevent overlap, never backward.

        Returns:
            Number of groups shifted.
        """
        shifted_count = 0

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

                if shift_amount != 0:
                    if "sub_groups" in data:
                        for sub in data["sub_groups"]:
                            cls._shift_curves_by_amount(
                                sub.get("curves", []),
                                shift_amount,
                                time_range=sub.get("segment_range"),
                            )
                    else:
                        curves_to_move = data.get("curves", [])
                        segment_range = data.get("segment_range")
                        cls._shift_curves_by_amount(
                            curves_to_move, shift_amount, time_range=segment_range
                        )
                    shifted_count += 1

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

                if shift_amount != 0:
                    if "sub_groups" in data:
                        for sub in data["sub_groups"]:
                            cls._shift_curves_by_amount(
                                sub.get("curves", []),
                                shift_amount,
                                time_range=sub.get("segment_range"),
                            )
                    else:
                        curves_to_move = data.get("curves", [])
                        segment_range = data.get("segment_range")
                        cls._shift_curves_by_amount(
                            curves_to_move, shift_amount, time_range=segment_range
                        )
                    shifted_count += 1

                # Update current frame for next object/group
                current_frame = current_frame + duration + spacing_frames

        return shifted_count


class StaggerKeys:
    """Class containing keyframe staggering operations."""

    @staticmethod
    @CoreUtils.undoable
    def stagger_keyframes(
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
        """
        # Import shared helpers
        from mayatk.anim_utils._anim_utils import AnimUtils, KeyframeGrouper

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
                curves_to_use = KeyframeGrouper._filter_curves_by_ignore(
                    selected_curves, ignore
                )
                keyframes = AnimUtils.get_keyframe_times(
                    curves_to_use, mode="selected", from_curves=True
                )
            else:
                all_curves = (
                    pm.listConnections(obj, type="animCurve", s=True, d=False) or []
                )
                curves_to_use = KeyframeGrouper._filter_curves_by_ignore(
                    all_curves, ignore
                )
                keyframes = AnimUtils.get_keyframe_times(
                    curves_to_use, from_curves=True
                )

            if keyframes:
                # If split_static is enabled, break the object's animation into active segments
                segments = []
                if split_static:
                    segments = KeyframeGrouper._get_active_animation_segments(
                        curves_to_use
                    )

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

        # Stage 2: Group segments if requested using KeyframeGrouper
        if group_overlapping:
            obj_keyframe_data = KeyframeGrouper._group_by_overlap(obj_keyframe_data)

        # Use provided start_frame or earliest keyframe
        base_frame = start_frame if start_frame is not None else first_keyframe

        # Apply stagger logic
        _StaggerKeysInternal._apply_stagger(
            obj_keyframe_data,
            start_frame=base_frame,
            spacing=spacing,
            use_intervals=use_intervals,
            avoid_overlap=avoid_overlap,
            preserve_gaps=False,
        )

        if smooth_tangents:
            for data in obj_keyframe_data:
                objects_in_group = data.get("objects", [data["obj"]])
                keyframes = data["keyframes"]
                for obj in objects_in_group:
                    try:
                        pm.keyTangent(
                            obj,
                            edit=True,
                            time=(keyframes[0], keyframes[-1]),
                            outTangentType="auto",
                            inTangentType="auto",
                        )
                    except RuntimeError as e:
                        pm.warning(f"Failed to adjust tangents for {obj}: {e}")
