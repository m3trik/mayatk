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
        ignore_visibility_holds: bool = True,
        verbose: bool = False,
        verbose_header: str = None,
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
            ignore_visibility_holds: If True (default), visibility curves are treated as
                active only during transitions, preventing long static holds from
                bridging gaps between segments.
            verbose: If True, prints detailed information including original time ranges.
            verbose_header: Optional custom text to prefix the verbose output headers.
        """
        # Import shared helpers
        from mayatk.anim_utils._anim_utils import AnimUtils
        from mayatk.anim_utils.segment_keys import SegmentKeys

        if not objects:
            pm.warning("No objects provided.")
            return

        objects = pm.ls(objects, type="transform", flatten=True)
        # Invert logic is applied after collection and sorting to ensure consistent behavior

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
                    segments = SegmentKeys._get_active_animation_segments(
                        curves_to_use,
                        ignore_visibility_holds=ignore_visibility_holds,
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

        # Capture original ranges if verbose
        original_ranges = []
        if verbose:
            # Re-collect with detailed view for reporting (ignoring visibility holds)
            # This ensures the report matches the "Animation Info" tool
            detailed_segments = SegmentKeys.collect_segments(
                objects,
                ignore=ignore,
                split_static=split_static,
                ignore_visibility_holds=ignore_visibility_holds,
            )
            original_ranges = SegmentKeys.get_time_ranges(detailed_segments)

        # Stage 2: Group segments if requested using SegmentKeys
        if group_overlapping:
            obj_keyframe_data = SegmentKeys._group_by_overlap(obj_keyframe_data)

        # Always merge groups that share curves to prevent double-transforming
        obj_keyframe_data = SegmentKeys.merge_groups_sharing_curves(obj_keyframe_data)

        # Sort by start time to ensure deterministic, time-based staggering
        # This fixes issues where selection order causes early objects to be pushed to the end
        obj_keyframe_data.sort(key=lambda x: x["start"])

        if invert:
            obj_keyframe_data.reverse()

        # Use provided start_frame or earliest keyframe
        base_frame = start_frame if start_frame is not None else first_keyframe

        # Apply stagger logic
        SegmentKeys.execute_stagger(
            obj_keyframe_data,
            start_frame=base_frame,
            spacing=spacing,
            use_intervals=use_intervals,
            avoid_overlap=avoid_overlap,
            preserve_gaps=False,
        )

        if smooth_tangents:
            for data in obj_keyframe_data:
                curves = data.get("curves", [])
                if curves:
                    # Use shared helper for smart tangent setting
                    AnimUtils._set_smart_tangents(curves, tangent_type="auto")

        if verbose and original_ranges:
            # Determine headers
            header_orig = "Original Time Ranges:"
            header_new = "Stagger Keys: New Time Ranges:"

            if verbose_header:
                header_orig = f"{verbose_header} Original Time Ranges:"
                header_new = f"{verbose_header} New Time Ranges:"

            # Print original ranges
            SegmentKeys.print_time_ranges(
                original_ranges,
                header=header_orig,
                per_segment=split_static,
                by_time=True,
            )

            # Capture and print new ranges
            # Re-collect segments to get updated times
            new_segments = SegmentKeys.collect_segments(
                objects,
                ignore=ignore,
                split_static=split_static,
                ignore_visibility_holds=split_static,
            )
            new_ranges = SegmentKeys.get_time_ranges(new_segments)
            SegmentKeys.print_time_ranges(
                new_ranges,
                header=header_new,
                per_segment=split_static,
                by_time=True,
            )
