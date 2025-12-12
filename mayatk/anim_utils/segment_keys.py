# coding=utf-8
from typing import List, Dict, Optional, Union, Any, Tuple

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)


class SegmentKeys:
    """Shared helper for collecting and grouping animation segments.

    This class provides Stage 1 (collection) and Stage 2 (grouping) operations
    that are used by both ScaleKeys and StaggerKeys.

    Segment Structure:
        {
            'obj': pm.PyNode,           # The source transform object
            'curves': List[pm.PyNode],  # Animation curves for this segment
            'keyframes': List[float],   # All keyframe times
            'start': float,             # Segment start time
            'end': float,               # Segment end time
            'duration': float,          # Segment duration
            'segment_range': Tuple[float, float],  # (start, end) tuple
        }

    Group Structure:
        {
            'objects': List[pm.PyNode],      # All objects in the group
            'curves': List[pm.PyNode],       # All curves in the group
            'keyframes': List[float],        # Combined keyframe times
            'start': float,                  # Group start time
            'end': float,                    # Group end time
            'duration': float,               # Group duration
            'obj': pm.PyNode,                # Representative object (first)
            'sub_groups': List[dict],        # Original segment dicts
        }

    Group Modes:
        - 'per_segment': Each segment is its own group (default)
        - 'per_object': Segments from the same object are grouped together
        - 'overlap_groups': Overlapping segments are merged into groups
        - 'single_group': All segments form one group
    """

    @staticmethod
    def get_time_ranges(
        segments: List[Dict[str, Any]],
    ) -> List[Tuple[str, float, float]]:
        """Extract time ranges from segment data.

        Args:
            segments: List of dictionaries containing 'obj', 'start', and 'end' keys.

        Returns:
            List of tuples (object_name, start_time, end_time).
        """
        ranges = []
        for seg in segments:
            obj = seg.get("obj")
            obj_name = str(obj) if obj else "Unknown"
            start = seg.get("start", 0.0)
            end = seg.get("end", 0.0)
            ranges.append((obj_name, start, end))
        return ranges

    @staticmethod
    def print_time_ranges(
        ranges: List[Tuple[str, float, float]],
        header: str = "Time Ranges:",
        per_segment: bool = False,
    ):
        """Print formatted time ranges.

        Args:
            ranges: List of (object_name, start, end) tuples.
            header: Title to print before the list.
            per_segment: If True, prints each segment individually. If False,
                aggregates ranges per object (min start, max end).
        """
        if not ranges:
            return

        print(f"\n{header}")
        print("-" * 60)

        if per_segment:
            # Print every segment as is
            for obj, start, end in ranges:
                duration = end - start
                print(f"{obj:<30} : {start:8.2f} - {end:8.2f} (Dur: {duration:6.2f})")
        else:
            # Aggregate per object
            from collections import defaultdict

            obj_ranges = defaultdict(list)
            for obj, start, end in ranges:
                obj_ranges[obj].append((start, end))

            for obj, range_list in obj_ranges.items():
                min_start = min(r[0] for r in range_list)
                max_end = max(r[1] for r in range_list)
                duration = max_end - min_start
                print(
                    f"{obj:<30} : {min_start:8.2f} - {max_end:8.2f} (Dur: {duration:6.2f})"
                )

        print("-" * 60)

    @classmethod
    def print_segment_info(cls, objects: Optional[List[Any]] = None):
        """Collect and print segment info for objects.

        Args:
            objects: List of objects. If None, uses selection. If no selection, uses all transforms.
        """
        if not objects:
            objects = pm.selected(type="transform")
            if not objects:
                objects = pm.ls(type="transform")

        if not objects:
            pm.warning("No objects found.")
            return

        segments = cls.collect_segments(objects, split_static=True)
        if not segments:
            pm.warning("No animation segments found.")
            return

        ranges = cls.get_time_ranges(segments)
        cls.print_time_ranges(
            ranges, header="Segment Info (Split Static):", per_segment=True
        )

    @classmethod
    def collect_segments(
        cls,
        objects: List["pm.PyNode"],
        ignore: Optional[Union[str, List[str]]] = None,
        split_static: bool = False,
        selected_keys_only: bool = False,
        channel_box_attrs: Optional[List[str]] = None,
        static_tolerance: float = 1e-4,
        time_range: Optional[Tuple[Optional[float], Optional[float]]] = None,
    ) -> List[Dict[str, Any]]:
        """Collect animation segments from objects.

        Stage 1 of the grouping pipeline. Gathers curves, filters by ignore patterns,
        and optionally splits by static gaps.

        Parameters:
            objects: Transform nodes to collect segments from.
            ignore: Attribute name(s) to exclude (e.g., 'visibility').
            split_static: If True, segments separated by static gaps are split.
            selected_keys_only: If True, only process selected keyframes.
            channel_box_attrs: If provided, only process curves for these attributes.
            static_tolerance: Value tolerance for detecting static segments.
            time_range: Optional (start, end) tuple to limit keyframe collection.

        Returns:
            List of segment dictionaries.
        """
        # Import here to use helper methods
        from mayatk.anim_utils._anim_utils import AnimUtils

        segments: List[Dict[str, Any]] = []

        # Parse time range
        range_start = time_range[0] if time_range else None
        range_end = time_range[1] if time_range else None

        for obj in objects:
            # Get curves based on selection state
            if selected_keys_only:
                selected_curves = pm.keyframe(obj, query=True, name=True, selected=True)
                if not selected_curves:
                    continue
                curves_to_use = cls._filter_curves_by_ignore(selected_curves, ignore)
                keyframes = AnimUtils.get_keyframe_times(
                    curves_to_use, mode="selected", from_curves=True
                )
            else:
                all_curves = (
                    pm.listConnections(obj, type="animCurve", s=True, d=False) or []
                )
                curves_to_use = cls._filter_curves_by_ignore(all_curves, ignore)
                keyframes = AnimUtils.get_keyframe_times(
                    curves_to_use, from_curves=True
                )

            # Apply channel box filter if specified
            if channel_box_attrs and curves_to_use:
                curves_to_use = cls._filter_curves_by_channel_box(
                    curves_to_use, channel_box_attrs
                )
                if not curves_to_use:
                    continue
                # Re-query keyframes after filtering
                if selected_keys_only:
                    keyframes = AnimUtils.get_keyframe_times(
                        curves_to_use, mode="selected", from_curves=True
                    )
                else:
                    keyframes = AnimUtils.get_keyframe_times(
                        curves_to_use, from_curves=True
                    )

            if not keyframes or not curves_to_use:
                continue

            keyframes = sorted(set(keyframes))

            # Apply time range filter if specified
            if range_start is not None or range_end is not None:
                keyframes = [
                    k
                    for k in keyframes
                    if (range_start is None or k >= range_start)
                    and (range_end is None or k <= range_end)
                ]
                if not keyframes:
                    continue

            # Determine segment ranges
            if split_static:
                active_segments = cls._get_active_animation_segments(
                    curves_to_use, tolerance=static_tolerance
                )
                # Filter active segments to time range
                if range_start is not None or range_end is not None:
                    filtered_segments = []
                    for seg_start, seg_end in active_segments:
                        # Clip segments to time range
                        if range_start is not None:
                            seg_start = max(seg_start, range_start)
                        if range_end is not None:
                            seg_end = min(seg_end, range_end)
                        if seg_start < seg_end:
                            filtered_segments.append((seg_start, seg_end))
                    active_segments = filtered_segments
            else:
                active_segments = []

            # If no active segments found, treat entire range as one segment
            if not active_segments:
                active_segments = [(keyframes[0], keyframes[-1])]

            # Create segment entry for each active range
            for seg_start, seg_end in active_segments:
                segments.append(
                    {
                        "obj": obj,
                        "curves": list(curves_to_use),
                        "keyframes": keyframes,
                        "start": seg_start,
                        "end": seg_end,
                        "duration": seg_end - seg_start,
                        "segment_range": (seg_start, seg_end),
                    }
                )

        return segments

    @classmethod
    def group_segments(
        cls,
        segments: List[Dict[str, Any]],
        mode: str = "per_segment",
    ) -> List[Dict[str, Any]]:
        """Group segments based on the specified mode.

        Stage 2 of the grouping pipeline. Takes collected segments and organizes
        them into processing groups.

        Parameters:
            segments: List of segment dictionaries from collect_segments().
            mode: Grouping mode:
                - 'per_segment': Each segment is its own group (default)
                - 'per_object': Segments from the same object are grouped
                - 'overlap_groups': Overlapping segments are merged
                - 'single_group': All segments form one group

        Returns:
            List of group dictionaries.
        """
        if not segments:
            return []

        mode = mode.lower().strip()

        if mode == "per_segment":
            return cls._group_per_segment(segments)
        elif mode == "per_object":
            return cls._group_per_object(segments)
        elif mode in ("overlap_groups", "overlap", "overlapping"):
            return cls._group_by_overlap(segments)
        elif mode in ("single_group", "single", "all"):
            return cls._group_as_single(segments)
        else:
            # Default to per_segment
            return cls._group_per_segment(segments)

    @staticmethod
    def _group_per_segment(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Each segment becomes its own group."""
        groups = []
        for seg in segments:
            groups.append(
                {
                    "objects": [seg["obj"]],
                    "curves": list(seg.get("curves", [])),
                    "keyframes": seg["keyframes"],
                    "start": seg["start"],
                    "end": seg["end"],
                    "duration": seg["duration"],
                    "obj": seg["obj"],
                    "sub_groups": [seg],
                }
            )
        return groups

    @staticmethod
    def _group_per_object(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Group segments by their source object."""
        from collections import defaultdict

        obj_segments: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
        for seg in segments:
            obj_segments[seg["obj"]].append(seg)

        groups = []
        for obj, segs in obj_segments.items():
            all_keyframes = sorted(set(k for seg in segs for k in seg["keyframes"]))
            all_curves = []
            for seg in segs:
                all_curves.extend(seg.get("curves", []))
            all_curves = list(dict.fromkeys(all_curves))  # Dedupe preserving order

            start = min(seg["start"] for seg in segs)
            end = max(seg["end"] for seg in segs)

            groups.append(
                {
                    "objects": [obj],
                    "curves": all_curves,
                    "keyframes": all_keyframes,
                    "start": start,
                    "end": end,
                    "duration": end - start,
                    "obj": obj,
                    "sub_groups": segs,
                }
            )
        return groups

    @staticmethod
    def _group_by_overlap(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Group segments with overlapping time ranges."""
        if not segments:
            return []

        # Sort by start frame
        sorted_segments = sorted(segments, key=lambda x: x["start"])

        groups = []
        current_group = {
            "objects": [sorted_segments[0]["obj"]],
            "curves": list(sorted_segments[0].get("curves", [])),
            "keyframes": sorted_segments[0]["keyframes"],
            "start": sorted_segments[0]["start"],
            "end": sorted_segments[0]["end"],
            "duration": sorted_segments[0]["duration"],
            "obj": sorted_segments[0]["obj"],
            "sub_groups": [sorted_segments[0]],
        }

        for i in range(1, len(sorted_segments)):
            seg = sorted_segments[i]

            # Use strict inequality (<) for overlap detection
            # Touching keys (end == start) are treated as separate groups
            if seg["start"] < current_group["end"]:
                # Overlapping - merge into current group
                if seg["obj"] not in current_group["objects"]:
                    current_group["objects"].append(seg["obj"])
                current_group["curves"].extend(seg.get("curves", []))
                current_group["keyframes"] = sorted(
                    set(current_group["keyframes"] + seg["keyframes"])
                )
                current_group["sub_groups"].append(seg)
                current_group["end"] = max(current_group["end"], seg["end"])
                current_group["duration"] = (
                    current_group["end"] - current_group["start"]
                )
            else:
                # Not overlapping - finalize current and start new
                groups.append(current_group)
                current_group = {
                    "objects": [seg["obj"]],
                    "curves": list(seg.get("curves", [])),
                    "keyframes": seg["keyframes"],
                    "start": seg["start"],
                    "end": seg["end"],
                    "duration": seg["duration"],
                    "obj": seg["obj"],
                    "sub_groups": [seg],
                }

        # Add the last group
        groups.append(current_group)

        return groups

    @staticmethod
    def _group_as_single(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Combine all segments into a single group."""
        if not segments:
            return []

        all_objects = list(dict.fromkeys(seg["obj"] for seg in segments))
        all_curves = []
        for seg in segments:
            all_curves.extend(seg.get("curves", []))
        all_curves = list(dict.fromkeys(all_curves))

        all_keyframes = sorted(set(k for seg in segments for k in seg["keyframes"]))
        start = min(seg["start"] for seg in segments)
        end = max(seg["end"] for seg in segments)

        return [
            {
                "objects": all_objects,
                "curves": all_curves,
                "keyframes": all_keyframes,
                "start": start,
                "end": end,
                "duration": end - start,
                "obj": all_objects[0] if all_objects else None,
                "sub_groups": segments,
            }
        ]

    @staticmethod
    def _filter_curves_by_ignore(
        curves: List["pm.PyNode"],
        ignore: Optional[Union[str, List[str]]],
    ) -> List["pm.PyNode"]:
        """Filter out curves connected to ignored attributes.

        Parameters:
            curves: List of animation curve nodes.
            ignore: Attribute name(s) to exclude.

        Returns:
            Filtered list of curves.
        """
        if not ignore or not curves:
            return list(curves)

        # Normalize ignore patterns
        if isinstance(ignore, str):
            ignore = [ignore]

        ignored_attrs = set()
        ignored_full = set()

        for pattern in ignore:
            pattern_lower = pattern.lower().strip()
            if "|" in pattern_lower or ":" in pattern_lower:
                ignored_full.add(pattern_lower)
            else:
                ignored_attrs.add(pattern_lower)

        ignored_suffixes: Tuple[str, ...] = tuple(
            list(f"_{attr}" for attr in ignored_attrs)
            + list(f".{attr}" for attr in ignored_attrs)
        )

        filtered: List["pm.PyNode"] = []
        for curve in curves:
            try:
                curve_node = pm.PyNode(curve)
            except Exception:
                continue

            curve_name = str(curve_node).lower()
            curve_short = (
                curve_node.name().lower() if hasattr(curve_node, "name") else curve_name
            )

            # Check if the curve matches any ignored pattern
            if curve_short in ignored_full or curve_name in ignored_full:
                continue

            if curve_short.endswith(ignored_suffixes) or curve_name.endswith(
                ignored_suffixes
            ):
                continue

            filtered.append(curve_node)

        return filtered

    @staticmethod
    def _filter_curves_by_channel_box(
        curves: List["pm.PyNode"],
        channel_box_attrs: Optional[List[str]],
    ) -> List["pm.PyNode"]:
        """Filter curves to only those connected to channel box selected attributes.

        Parameters:
            curves: List of animation curve nodes.
            channel_box_attrs: List of attribute names from channel box selection.

        Returns:
            Filtered list of curves.
        """
        if not channel_box_attrs or not curves:
            return list(curves)

        attr_set = set(a.lower() for a in channel_box_attrs)

        filtered = []
        for curve in curves:
            try:
                curve_name = str(curve).lower()
                # Check if any attribute matches the curve name suffix
                for attr in attr_set:
                    if curve_name.endswith(f"_{attr}") or curve_name.endswith(
                        f".{attr}"
                    ):
                        filtered.append(curve)
                        break
            except Exception:
                continue

        return filtered

    @staticmethod
    def _get_active_animation_segments(
        curves: List["pm.PyNode"],
        tolerance: float = 1e-4,
    ) -> List[Tuple[float, float]]:
        """Identify segments of active animation, excluding static gaps.

        A segment is considered active if at least one curve has changing values.
        Static gaps (where all curves hold the same value) are excluded.

        Note: Visibility curves (and other stepped curves) are treated as active
        between all keys to preserve holds during scaling.

        Parameters:
            curves: List of animation curves to analyze.
            tolerance: Value tolerance for detecting static segments.

        Returns:
            List of (start, end) tuples representing active animation segments.
        """
        if not curves:
            return []

        # Collect all active intervals from all curves
        all_intervals = []

        for curve in curves:
            times = pm.keyframe(curve, query=True, timeChange=True)
            values = pm.keyframe(curve, query=True, valueChange=True)

            if not times or len(times) < 2:
                continue

            # Check if curve is visibility
            is_visibility = False
            try:
                if "visibility" in curve.name().lower():
                    is_visibility = True
                else:
                    plugs = curve.outputs(plugs=True)
                    for plug in plugs:
                        if "visibility" in plug.partialName(includeNode=False).lower():
                            is_visibility = True
                            break
            except Exception:
                pass

            # Identify segments where value changes
            for i in range(len(times) - 1):
                t1, t2 = times[i], times[i + 1]
                v1, v2 = values[i], values[i + 1]

                # For visibility, treat all intervals as active to preserve holds
                if is_visibility or abs(v1 - v2) > tolerance:
                    all_intervals.append((t1, t2))

        if not all_intervals:
            return []

        # Merge overlapping intervals
        all_intervals.sort(key=lambda x: x[0])

        merged = []
        current_start, current_end = all_intervals[0]

        for i in range(1, len(all_intervals)):
            next_start, next_end = all_intervals[i]

            if next_start <= current_end:
                # Overlap or adjacent - extend current segment
                current_end = max(current_end, next_end)
            else:
                # Gap found - push current segment and start new one
                merged.append((current_start, current_end))
                current_start, current_end = next_start, next_end

        merged.append((current_start, current_end))

        return merged
