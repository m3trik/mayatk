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
        channel_box_attrs_only: bool = False,
        split_static: bool = True,
        merge_touching: bool = False,
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
            channel_box_attrs_only: If True, only affect attributes currently selected
                in the Channel Box. This provides precise control over which channels
                are staggered.
            split_static: If True, treats segments of animation separated by static gaps
                (flat keys) as separate groups. Each segment will be staggered independently.
            merge_touching: If True, touching segments (end == start) are merged into a
                single group when using group_overlapping. Default False.
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

        # Auto-enable group_overlapping if merge_touching is requested
        if merge_touching:
            group_overlapping = True

        objects = pm.ls(objects, type="transform", flatten=True)
        # Invert logic is applied after collection and sorting to ensure consistent behavior

        # Get channel box attributes if requested
        channel_box_attrs = None
        if channel_box_attrs_only:
            channel_box_attrs = pm.channelBox(
                "mainChannelBox", query=True, selectedMainAttributes=True
            )
            if not channel_box_attrs:
                pm.warning(
                    "Channel Box Attrs Only is enabled but no attributes are selected "
                    "in the Channel Box. Select attributes or disable this option."
                )
                return

        # Stage 1: Collect segments using SegmentKeys
        # Check if we should operate on selected keys only
        selected_keys_only = False
        try:
            # Check if any keys are selected on the target objects
            if pm.keyframe(objects, query=True, name=True, selected=True):
                selected_keys_only = True
        except Exception:
            pass

        obj_keyframe_data = SegmentKeys.collect_segments(
            objects,
            ignore=ignore,
            split_static=split_static,
            selected_keys_only=selected_keys_only,
            channel_box_attrs=channel_box_attrs,
            ignore_visibility_holds=ignore_visibility_holds,
            ignore_holds=False,  # Ensure trailing holds are absorbed/moved
            exclude_next_start=True,
        )

        if not obj_keyframe_data:
            pm.warning("No keyframes found on the provided objects.")
            return

        # Calculate bounds
        first_keyframe = min(seg["start"] for seg in obj_keyframe_data)
        last_keyframe = max(seg["end"] for seg in obj_keyframe_data)

        # Capture original ranges if verbose
        original_ranges = []
        if verbose:
            # Use the already collected data for reporting
            original_ranges = SegmentKeys.get_time_ranges(obj_keyframe_data)

        # Stage 2: Group segments if requested using SegmentKeys
        if group_overlapping:
            obj_keyframe_data = SegmentKeys._group_by_overlap(
                obj_keyframe_data, inclusive=merge_touching
            )

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

        # Ensure visibility tangents are always step, regardless of smooth_tangents
        for data in obj_keyframe_data:
            curves = data.get("curves", [])
            if not curves:
                continue

            if smooth_tangents:
                # Use shared helper for smart tangent setting (handles both types)
                AnimUtils._set_smart_tangents(curves, tangent_type="auto")
            else:
                # Only fix visibility curves
                vis_curves, _ = AnimUtils._get_visibility_curves(curves)
                if vis_curves:
                    # We can use _set_smart_tangents with only vis curves
                    # It will see them as vis curves and set them to step
                    # It won't touch anything else because we passed nothing else
                    AnimUtils._set_smart_tangents(vis_curves, tangent_type="auto")

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
                channel_box_attrs=channel_box_attrs,
                ignore_visibility_holds=split_static,
                exclude_next_start=True,
            )
            new_ranges = SegmentKeys.get_time_ranges(new_segments)
            SegmentKeys.print_time_ranges(
                new_ranges,
                header=header_new,
                per_segment=split_static,
                by_time=True,
            )
