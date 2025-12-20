# coding=utf-8
from typing import List, Dict, Optional, Union, Any, Tuple

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)


class SegmentKeysInfo:
    """Mixin for reporting animation segment information."""

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

    @classmethod
    def print_time_ranges(
        cls,
        source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]],
        header: Optional[str] = None,
        per_segment: bool = False,
        object_fmt: Optional[str] = None,
        segment_fmt: Optional[str] = None,
        by_time: bool = False,
        csv_output: bool = False,
    ):
        """Print formatted time ranges.

        Args:
            source: List of segments (dicts) or ranges (tuples).
            header: Optional header text.
            per_segment: If True, prints every item. If False, aggregates by object.
            object_fmt: Optional format string for object lines.
                Available keys: obj, start, end, duration, count, suffix
            segment_fmt: Optional format string for segment lines.
                Available keys: i, start, end, duration
            by_time: If True, prints a flat list of all segments sorted by start time.
            csv_output: If True, prints in CSV format.
        """
        if not source:
            return

        # Resolve ranges
        ranges = []
        first = source[0]
        if isinstance(first, dict):
            ranges = cls.get_time_ranges(source)
        elif isinstance(first, tuple):
            ranges = source
        else:
            return

        if not ranges:
            return

        if csv_output:
            if header is None:
                if by_time:
                    print("Object,Start,End,Duration,Segment Index,Total Segments")
                else:
                    print("Object,Start,End,Duration,Segments")
            elif header:
                print(header)
        else:
            if header is None:
                scene_name = pm.sceneName()
                if scene_name:
                    scene_name = scene_name.basename()
                else:
                    scene_name = "Untitled"
                header = f"Animation Info - {scene_name}"

            print(f"\\n{header}")
            print("-" * 60)

        from collections import defaultdict

        if by_time:
            # Flat list sorted by time
            # First, assign segment indices
            obj_counters = defaultdict(int)
            obj_totals = defaultdict(int)

            # Calculate totals first
            for obj, _, _ in ranges:
                obj_totals[obj] += 1

            annotated = []
            # We need to process in the order they appear in 'ranges' to assign indices correctly?
            # 'ranges' comes from 'collect_segments' which iterates objects.
            # So for each object, segments are chronological.

            for obj, start, end in ranges:
                obj_counters[obj] += 1
                annotated.append(
                    {
                        "obj": obj,
                        "start": start,
                        "end": end,
                        "index": obj_counters[obj],
                        "total": obj_totals[obj],
                    }
                )

            # Sort by start time
            annotated.sort(key=lambda x: x["start"])

            for item in annotated:
                duration = item["end"] - item["start"]

                if csv_output:
                    # CSV Format: Object,Start,End,Duration,SegmentIndex,TotalSegments
                    print(
                        f"{item['obj']},{item['start']},{item['end']},{duration},{item['index']},{item['total']}"
                    )
                else:
                    suffix = f" [{item['total']} segments]" if item["total"] > 1 else ""

                    # Format: Object (Seg i/N) : Start - End (Dur: D) [Total]
                    if item["total"] > 1:
                        label = f"{item['obj']} (Seg {item['index']}/{item['total']})"
                    else:
                        label = f"{item['obj']} (Seg {item['index']})"

                    print(
                        f"{label:<30} : {item['start']:8.2f} - {item['end']:8.2f} (Dur: {duration:6.2f}){suffix}"
                    )

        else:
            # Group by object
            obj_segments = defaultdict(list)
            for obj, start, end in ranges:
                obj_segments[obj].append((start, end))

            for obj, segments in obj_segments.items():
                # Calculate aggregate
                min_start = min(s[0] for s in segments)
                max_end = max(s[1] for s in segments)
                total_dur = max_end - min_start
                count = len(segments)

                if csv_output:
                    # CSV Format: Object,Start,End,Duration,SegmentCount
                    print(f"{obj},{min_start},{max_end},{total_dur},{count}")
                else:
                    suffix = f" [{count} segments]" if count > 1 else ""

                    # Print object line
                    if object_fmt:
                        print(
                            object_fmt.format(
                                obj=obj,
                                start=min_start,
                                end=max_end,
                                duration=total_dur,
                                count=count,
                                suffix=suffix,
                            )
                        )
                    else:
                        print(
                            f"{obj:<30} : {min_start:8.2f} - {max_end:8.2f} (Dur: {total_dur:6.2f}){suffix}"
                        )

                # Print segments if requested
                if per_segment:
                    for i, (start, end) in enumerate(segments, 1):
                        duration = end - start
                        if csv_output:
                            print(f"{obj} (Seg {i}),{start},{end},{duration},")
                        elif segment_fmt:
                            print(
                                segment_fmt.format(
                                    i=i, start=start, end=end, duration=duration
                                )
                            )
                        else:
                            print(
                                f"    Seg {i:<2}                         : {start:8.2f} - {end:8.2f} (Dur: {duration:6.2f})"
                            )

        if not csv_output:
            print("-" * 60)


class SegmentKeys(SegmentKeysInfo):
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
        ignore_visibility_holds: bool = False,
        ignore_holds: bool = False,
        exclude_next_start: bool = True,
    ) -> List[Dict[str, Any]]:
        """Collect animation segments from objects.

        Stage 1 of the grouping pipeline. Gathers curves, filters by ignore patterns,
        and optionally splits by static gaps.

        Parameters:
            objects: Transform nodes to collect segments from.
            ignore: Attribute name(s) to exclude (e.g., 'visibility').
            split_static: If True, segments separated by static gaps are split.
            selected_keys_only: If True, only process selected keyframes.
            static_tolerance: Value tolerance for detecting static segments.
            time_range: Optional (start, end) tuple to limit keyframe collection.
            ignore_visibility_holds: If True, visibility curves are treated like any
                other curve (static holds are ignored). If False (default), visibility
                curves are treated as always active to preserve holds.
            ignore_holds: If True, trailing holds are ignored entirely (not processed,
                not reported). If False (default), trailing holds are absorbed into
                each segment so they are scaled/shifted with the segment.
            exclude_next_start: If True (default), the segment end will exclude the
                start key of the next segment (using epsilon). This is useful for
                operations like stagger where gaps should be collapsible. If False,
                the segment absorbs keys up to and including the next start, which
                is useful for scaling to preserve continuity.

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
                    curves_to_use,
                    tolerance=static_tolerance,
                    ignore_visibility_holds=ignore_visibility_holds,
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
                active_segments = [(keyframes[0], keyframes[-1])]

            # Default behavior: absorb trailing holds into the segment so hold keys
            # move with the segment (prevents collisions during shifting/staggering).
            # Optional behavior (ignore_holds=True): keep segments active-only.
            if split_static and active_segments and not ignore_holds:
                eps = 1e-3
                active_segments = sorted(active_segments, key=lambda x: (x[0], x[1]))
                expanded_segments = []
                for i, (seg_start, seg_end) in enumerate(active_segments):
                    next_start = (
                        active_segments[i + 1][0]
                        if i + 1 < len(active_segments)
                        else None
                    )

                    # Determine an upper bound for trailing holds.
                    # - If there's a next segment, absorb keys up to its start.
                    # - Otherwise absorb through the last key in the range.
                    if next_start is not None:
                        if exclude_next_start:
                            eps = 1e-3
                            upper = float(next_start) - eps
                        else:
                            eps = 1e-3
                            upper = float(next_start)
                    else:
                        upper = float(keyframes[-1]) + eps

                    # Extend segment end to the last keyframe time within the trailing-hold window.
                    seg_end_expanded = float(seg_end)
                    for k in reversed(keyframes):
                        if k <= upper and k >= float(seg_end) - eps:
                            seg_end_expanded = float(k)
                            break

                    # Extend segment start for the first segment to include leading holds
                    seg_start_expanded = float(seg_start)
                    if i == 0 and keyframes:
                        first_key = float(keyframes[0])
                        if first_key < seg_start - eps:
                            seg_start_expanded = first_key

                    expanded_segments.append(
                        (float(seg_start_expanded), float(seg_end_expanded))
                    )

                active_segments = expanded_segments

            # Create segment entry for each active range
            for seg_start, seg_end in sorted(
                active_segments, key=lambda x: (x[0], x[1])
            ):
                # Filter keyframes to those within this segment
                segment_keys = [k for k in keyframes if seg_start <= k <= seg_end]

                segments.append(
                    {
                        "obj": obj,
                        "curves": list(curves_to_use),
                        "keyframes": segment_keys,
                        "start": seg_start,
                        "end": seg_end,
                        "duration": seg_end - seg_start,
                        "segment_range": (seg_start, seg_end),
                    }
                )

        return segments

    @classmethod
    def print_scene_info(
        cls,
        objects: Optional[List["pm.PyNode"]] = None,
        detailed: bool = True,
        csv_output: bool = False,
        by_time: bool = False,
        ignore_holds: bool = True,
    ):
        """Print animation info for the scene or provided objects.

        Args:
            objects: List of objects to analyze. If None, uses selection or all objects.
            detailed: If True, prints individual segments. If False, aggregates per object.
            csv_output: If True, prints in CSV format.
            by_time: If True, sorts output by start time.
            ignore_holds: If True, reports only active animation (excludes static holds).
        """
        if not objects:
            objects = pm.selected(type="transform")
        if not objects:
            objects = pm.ls(type="transform")

        if not objects:
            pm.warning("No objects found to analyze.")
            return

        # Collect segments with split_static=True to get full detail
        # Pass ignore_visibility_holds=True to see visual segments even if visibility bridges them
        segments = cls.collect_segments(
            objects,
            split_static=True,
            ignore_visibility_holds=detailed,
            ignore_holds=ignore_holds,
        )

        if not segments:
            pm.warning("No animation found on the specified objects.")
            return

        cls.print_time_ranges(
            segments, per_segment=detailed, csv_output=csv_output, by_time=by_time
        )

    @classmethod
    def group_segments(
        cls,
        segments: List[Dict[str, Any]],
        mode: str = "per_segment",
        **kwargs,
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
            **kwargs: Additional arguments passed to specific grouping methods.
                - inclusive (bool): For 'overlap_groups', if True, touching segments
                  (end == start) are merged. Default False.

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
            return cls._group_by_overlap(segments, **kwargs)
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
    def _group_by_overlap(
        segments: List[Dict[str, Any]], inclusive: bool = False
    ) -> List[Dict[str, Any]]:
        """Group segments with overlapping time ranges.

        Args:
            segments: List of segments to group.
            inclusive: If True, touching segments (end == start) are merged.
                If False (default), they are treated as separate groups.
        """
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

            # Check overlap based on inclusive flag
            threshold = current_group["end"]
            if inclusive:
                # Allow for small epsilon gaps (e.g. from exclude_next_start)
                # Epsilon used in collect_segments is 1e-3, so we use slightly more.
                is_overlap = seg["start"] <= (threshold + 2e-3)
            else:
                is_overlap = seg["start"] < threshold

            if is_overlap:
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
    def merge_groups_sharing_curves(
        groups: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge groups that share any animation curves.

        This prevents double-transforming curves when multiple objects share the same
        animation curve (e.g. instances or connected attributes).
        """
        if not groups:
            return []

        # Map curve to group indices
        curve_to_indices = {}
        for i, group in enumerate(groups):
            curves = group.get("curves", [])
            # print(f"DEBUG: Group {i} curves: {[c.name() for c in curves]}")
            for curve in curves:
                # Use name to ensure identity across PyNode instances
                try:
                    curve_id = curve.name()
                except Exception:
                    curve_id = str(curve)

                if curve_id not in curve_to_indices:
                    curve_to_indices[curve_id] = []
                curve_to_indices[curve_id].append(i)

        # Find connected components using Union-Find
        parent = list(range(len(groups)))

        def find(i):
            if parent[i] != i:
                parent[i] = find(parent[i])
            return parent[i]

        def union(i, j):
            root_i = find(i)
            root_j = find(j)
            if root_i != root_j:
                parent[root_i] = root_j

        def ranges_overlap(r1, r2):
            return max(r1[0], r2[0]) < min(r1[1], r2[1])

        for indices in curve_to_indices.values():
            if len(indices) > 1:
                for k in range(len(indices)):
                    for m in range(k + 1, len(indices)):
                        idx1 = indices[k]
                        idx2 = indices[m]

                        r1 = groups[idx1].get(
                            "segment_range",
                            (groups[idx1]["start"], groups[idx1]["end"]),
                        )
                        r2 = groups[idx2].get(
                            "segment_range",
                            (groups[idx2]["start"], groups[idx2]["end"]),
                        )

                        if ranges_overlap(r1, r2):
                            union(idx1, idx2)

        # Group indices by root
        root_to_indices = {}
        for i in range(len(groups)):
            root = find(i)
            if root not in root_to_indices:
                root_to_indices[root] = []
            root_to_indices[root].append(i)

        # Build merged groups
        merged_groups = []
        for indices in root_to_indices.values():
            if len(indices) == 1:
                merged_groups.append(groups[indices[0]])
            else:
                # Merge multiple groups
                first = groups[indices[0]]
                merged = {
                    "objects": list(first.get("objects", [first.get("obj")])),
                    "curves": list(first.get("curves", [])),
                    "keyframes": list(first.get("keyframes", [])),
                    "start": first["start"],
                    "end": first["end"],
                    "obj": first.get("obj"),
                    "sub_groups": list(first.get("sub_groups", [first])),
                }

                # Collect names for warning
                merged_obj_names = [str(first.get("obj") or "Unknown")]

                for k in range(1, len(indices)):
                    other = groups[indices[k]]
                    # Merge objects
                    other_objs = other.get("objects", [other.get("obj")])
                    for obj in other_objs:
                        if obj not in merged["objects"]:
                            merged["objects"].append(obj)

                    obj_name = str(other.get("obj") or "Unknown")
                    if obj_name not in merged_obj_names:
                        merged_obj_names.append(obj_name)

                    # Merge curves
                    merged["curves"].extend(other.get("curves", []))

                    # Merge keyframes
                    merged["keyframes"].extend(other.get("keyframes", []))

                    # Update range
                    merged["start"] = min(merged["start"], other["start"])
                    merged["end"] = max(merged["end"], other["end"])

                    # Merge sub_groups
                    other_subs = other.get("sub_groups", [other])
                    merged["sub_groups"].extend(other_subs)

                # Dedupe curves and keyframes
                merged["curves"] = list(dict.fromkeys(merged["curves"]))
                merged["keyframes"] = sorted(set(merged["keyframes"]))
                merged["duration"] = merged["end"] - merged["start"]

                # Ensure segment_range is set (use overall range)
                merged["segment_range"] = (merged["start"], merged["end"])

                merged_groups.append(merged)

                # Warn about merge
                pm.warning(
                    f"Merged {len(indices)} groups sharing curves: {', '.join(merged_obj_names)}. Shared curves prevent independent staggering."
                )

        return merged_groups

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
        ignore_visibility_holds: bool = False,
    ) -> List[Tuple[float, float]]:
        """Identify segments of active animation, excluding static gaps.

        A segment is considered active if at least one curve has changing values.
        Static gaps (where all curves hold the same value) are excluded.

        Note: Visibility curves (and other stepped curves) are treated as active
        between all keys to preserve holds during scaling, unless ignore_visibility_holds
        is True.

        Parameters:
            curves: List of animation curves to analyze.
            tolerance: Value tolerance for detecting static segments.
            ignore_visibility_holds: If True, visibility curves are treated like any
                other curve (static holds are ignored).

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
            if not ignore_visibility_holds:
                try:
                    if "visibility" in curve.name().lower():
                        is_visibility = True
                    else:
                        plugs = curve.outputs(plugs=True)
                        for plug in plugs:
                            if (
                                "visibility"
                                in plug.partialName(includeNode=False).lower()
                            ):
                                is_visibility = True
                                break
                except Exception:
                    pass

            # Identify segments where value changes
            for i in range(len(times) - 1):
                t1, t2 = times[i], times[i + 1]
                v1, v2 = values[i], values[i + 1]

                # For visibility, treat all intervals as active to preserve holds
                # unless ignore_visibility_holds is True
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

    @staticmethod
    def shift_curves(
        curves: List[Any],
        offset: float,
        time_range: Optional[Tuple[float, float]] = None,
    ):
        """Shift keys on curves by offset.

        Args:
            curves: List of animation curves to shift.
            offset: Amount to shift (in frames).
            time_range: Optional (start, end) tuple to limit the shift to specific keys.
        """
        if not curves or abs(offset) < 1e-6:
            return

        try:
            kwargs = {
                "edit": True,
                "relative": True,
                "timeChange": offset,
            }
            if time_range:
                try:
                    eps = 1e-3
                    start, end = float(time_range[0]), float(time_range[1])
                    kwargs["time"] = (start - eps, end + eps)
                except Exception:
                    kwargs["time"] = time_range

            for curve in curves:
                pm.keyframe(curve, **kwargs)
        except RuntimeError as e:
            pm.warning(f"Failed to move keys for {curves}: {e}")

    @classmethod
    def execute_stagger(
        cls,
        groups_data: List[dict],
        start_frame: float,
        spacing: Union[int, float] = 0,
        use_intervals: bool = False,
        avoid_overlap: bool = False,
        preserve_gaps: bool = False,
    ):
        """Calculate and execute staggering on groups of segments.

        Args:
            groups_data: List of group dictionaries (from group_segments or similar).
            start_frame: Frame to start staggering from.
            spacing: Spacing between groups (frames or percentage).
            use_intervals: If True, use fixed intervals.
            avoid_overlap: If True (and use_intervals=True), skip intervals to avoid overlap.
            preserve_gaps: If True (and use_intervals=False), ensure we don't pull back.
        """
        operations = []

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
                previous_end = target_start + duration

                if abs(shift_amount) > 1e-6:
                    # Use group-level data to prevent double-transforming shared curves
                    # Dedupe curves just in case
                    curves = list(dict.fromkeys(data.get("curves", [])))

                    # Determine time range
                    time_range = data.get("segment_range")
                    if not time_range:
                        time_range = (data.get("start"), data.get("end"))

                    operations.append(
                        {
                            "curves": curves,
                            "shift": shift_amount,
                            "time": time_range,
                        }
                    )

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

                # Update current frame for next object/group
                current_frame = current_frame + duration + spacing_frames

                if abs(shift_amount) > 1e-6:
                    # Use group-level data to prevent double-transforming shared curves
                    # Dedupe curves just in case
                    curves = list(dict.fromkeys(data.get("curves", [])))

                    # Determine time range
                    time_range = data.get("segment_range")
                    if not time_range:
                        time_range = (data.get("start"), data.get("end"))

                    operations.append(
                        {
                            "curves": curves,
                            "shift": shift_amount,
                            "time": time_range,
                        }
                    )

        # Execute operations in safe order to prevent collisions
        # Positive shifts (moving right): Process Reverse (End to Start)
        # Negative shifts (moving left): Process Forward (Start to End)

        pos_ops = [op for op in operations if op["shift"] > 0]
        neg_ops = [op for op in operations if op["shift"] < 0]

        # Sort by start time
        # Handle None time range (treat as -inf)
        def get_start_time(op):
            t = op.get("time")
            return t[0] if t else float("-inf")

        pos_ops.sort(key=get_start_time, reverse=True)
        neg_ops.sort(key=get_start_time)

        for op in pos_ops:
            cls.shift_curves(op["curves"], op["shift"], op["time"])

        for op in neg_ops:
            cls.shift_curves(op["curves"], op["shift"], op["time"])
