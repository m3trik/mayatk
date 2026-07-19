# coding=utf-8
import html as _html
import logging
import math
from typing import List, Dict, Optional, Union, Any, Tuple, Callable

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

# Module-level loggers — avoid per-call getLogger + handler creation
_log_segments = logging.getLogger("mayatk.segment_keys.active_segments")
if not _log_segments.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(logging.WARNING)
    _log_segments.addHandler(_h)
_log_segments.setLevel(logging.WARNING)

_log_shift = logging.getLogger("mayatk.segment_keys.shift_curves")
if not _log_shift.handlers:
    _h2 = logging.StreamHandler()
    _h2.setLevel(logging.WARNING)
    _log_shift.addHandler(_h2)
_log_shift.setLevel(logging.WARNING)


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
    def _format_time_ranges_lines(
        cls,
        source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]],
        header: Optional[str] = None,
        per_segment: bool = False,
        object_fmt: Optional[str] = None,
        segment_fmt: Optional[str] = None,
        by_time: bool = False,
        csv_output: bool = False,
    ) -> List[str]:
        """Build the formatted-line list used by both the print and the
        text/HTML formatters. Returns ``[]`` when the source is empty.

        Note:
            ``object_fmt`` and ``segment_fmt`` are honored only in the default
            (per-object) layout. When ``by_time=True`` the segments are
            re-ordered chronologically and emitted with a fixed per-segment
            layout, so both custom templates are ignored in that mode.
        """
        if not source:
            return []

        # Resolve ranges
        ranges = []
        first = source[0]
        if isinstance(first, dict):
            ranges = cls.get_time_ranges(source)
        elif isinstance(first, tuple):
            ranges = source
        else:
            return []

        if not ranges:
            return []

        lines: List[str] = []

        if csv_output:
            if header is None:
                if by_time:
                    lines.append(
                        "Object,Start,End,Duration,Segment Index,Total Segments"
                    )
                else:
                    lines.append("Object,Start,End,Duration,Segments")
            elif header:
                lines.append(header)
        else:
            if header is None:
                try:
                    scene_path = cmds.file(query=True, sceneName=True)
                except Exception:
                    scene_path = ""
                scene_name = (
                    scene_path.replace("\\", "/").rsplit("/", 1)[-1]
                    if scene_path
                    else "Untitled"
                )
                header = f"Animation Info - {scene_name}"

            lines.append("")  # leading blank to match the prior "\n{header}"
            lines.append(header)
            lines.append("-" * 60)

        from collections import defaultdict

        if by_time:
            obj_counters = defaultdict(int)
            obj_totals = defaultdict(int)
            for obj, _, _ in ranges:
                obj_totals[obj] += 1

            annotated = []
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

            annotated.sort(key=lambda x: x["start"])

            for item in annotated:
                duration = item["end"] - item["start"]

                if csv_output:
                    lines.append(
                        f"{item['obj']},{item['start']},{item['end']},{duration},{item['index']},{item['total']}"
                    )
                else:
                    suffix = (
                        f" [{item['total']} segments]" if item["total"] > 1 else ""
                    )
                    if item["total"] > 1:
                        label = f"{item['obj']} (Seg {item['index']}/{item['total']})"
                    else:
                        label = f"{item['obj']} (Seg {item['index']})"
                    lines.append(
                        f"{label:<30} : {item['start']:8.2f} - {item['end']:8.2f} (Dur: {duration:6.2f}){suffix}"
                    )

        else:
            obj_segments = defaultdict(list)
            for obj, start, end in ranges:
                obj_segments[obj].append((start, end))

            for obj, segments in obj_segments.items():
                min_start = min(s[0] for s in segments)
                max_end = max(s[1] for s in segments)
                total_dur = max_end - min_start
                count = len(segments)

                if csv_output:
                    lines.append(f"{obj},{min_start},{max_end},{total_dur},{count}")
                else:
                    suffix = f" [{count} segments]" if count > 1 else ""
                    if object_fmt:
                        lines.append(
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
                        lines.append(
                            f"{obj:<30} : {min_start:8.2f} - {max_end:8.2f} (Dur: {total_dur:6.2f}){suffix}"
                        )

                if per_segment:
                    for i, (start, end) in enumerate(segments, 1):
                        duration = end - start
                        if csv_output:
                            lines.append(f"{obj} (Seg {i}),{start},{end},{duration},")
                        elif segment_fmt:
                            lines.append(
                                segment_fmt.format(
                                    i=i, start=start, end=end, duration=duration
                                )
                            )
                        else:
                            lines.append(
                                f"    Seg {i:<2}                         : {start:8.2f} - {end:8.2f} (Dur: {duration:6.2f})"
                            )

        if not csv_output:
            lines.append("-" * 60)

        return lines

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
        """Print formatted time ranges to stdout. See
        :meth:`format_time_ranges_text` for the same output as a string,
        and :meth:`format_time_ranges_html` for HTML.

        ``object_fmt`` / ``segment_fmt`` customize the default per-object
        layout only; they are ignored when ``by_time=True`` (which uses a
        fixed chronological per-segment layout).
        """
        for line in cls._format_time_ranges_lines(
            source,
            header=header,
            per_segment=per_segment,
            object_fmt=object_fmt,
            segment_fmt=segment_fmt,
            by_time=by_time,
            csv_output=csv_output,
        ):
            print(line)

    @classmethod
    def format_time_ranges_text(
        cls,
        source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]],
        **kwargs,
    ) -> str:
        """Return the same output as :meth:`print_time_ranges` as a
        single newline-joined string. Kwargs are forwarded.
        """
        return "\n".join(cls._format_time_ranges_lines(source, **kwargs))

    @classmethod
    def format_time_ranges_html(
        cls,
        source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]],
        title: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Wrap :meth:`format_time_ranges_text` in styled HTML suitable
        for ``sb.text_view_dialog``. ``title`` adds an ``<h2>`` above
        the monospaced body; pass ``None`` to omit.

        Both the title and body are HTML-escaped — Maya node names can
        contain ``& < >`` and would otherwise break the rendering.
        """
        body = _html.escape(cls.format_time_ranges_text(source, **kwargs))
        head = (
            f"<h2 style='color:#9cf; margin:0 0 6px 0;'>{_html.escape(title)}</h2>"
            if title
            else ""
        )
        return (
            head
            + "<pre style='font-family:monospace; color:#ddd;'>"
            + body
            + "</pre>"
        )


class SegmentKeys(SegmentKeysInfo):
    """Shared helper for collecting and grouping animation segments.

    This class provides Stage 1 (collection) and Stage 2 (grouping) operations
    that are used by both ScaleKeys and StaggerKeys.

    Segment Structure:
        {
            'obj': str,                 # The source transform object
            'curves': List[str],        # Animation curves for this segment
            'keyframes': List[float],   # All keyframe times
            'start': float,             # Segment start time
            'end': float,               # Segment end time
            'duration': float,          # Segment duration
            'segment_range': Tuple[float, float],  # (start, end) tuple
        }

    Group Structure:
        {
            'objects': List[str],            # All objects in the group
            'curves': List[str],             # All curves in the group
            'keyframes': List[float],        # Combined keyframe times
            'start': float,                  # Group start time
            'end': float,                    # Group end time
            'duration': float,               # Group duration
            'obj': str,                      # Representative object (first)
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
        objects: List[Any],
        ignore: Optional[Union[str, List[str]]] = None,
        split_static: bool = False,
        selected_keys_only: bool = False,
        channel_box_attrs: Optional[List[str]] = None,
        static_tolerance: float = 1e-4,
        time_range: Optional[Tuple[Optional[float], Optional[float]]] = None,
        ignore_visibility_holds: bool = False,
        ignore_holds: bool = False,
        exclude_next_start: bool = True,
        motion_only: bool = False,
        motion_rate: float = 1e-3,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
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
        if isinstance(objects, str):
            objects = [objects]
        if not objects:
            return segments

        # Parse time range
        range_start = time_range[0] if time_range else None
        range_end = time_range[1] if time_range else None

        import maya.cmds as cmds

        total_objs = len(objects)
        for i, obj in enumerate(objects):
            obj_str = str(obj)
            if progress_callback:
                progress_callback(i, total_objs, f"Scanning: {obj_str}")
            # Get curves based on selection state
            if selected_keys_only:
                selected_curves = cmds.keyframe(
                    obj_str, query=True, name=True, selected=True
                )
                if not selected_curves:
                    continue
                curves_to_use = cls._filter_curves_by_ignore(selected_curves, ignore)
                keyframes = AnimUtils.get_keyframe_times(
                    curves_to_use, mode="selected", from_curves=True
                )
            else:
                all_curves = (
                    cmds.listConnections(obj_str, type="animCurve", s=True, d=False)
                    or []
                )
                curves_to_use = cls._filter_curves_by_ignore(all_curves, ignore)
                # When split_static is requested, _get_active_animation_segments
                # already queries all keyframe times internally.  Defer the
                # get_keyframe_times call to avoid duplicating that work.
                if not split_static:
                    keyframes = AnimUtils.get_keyframe_times(
                        curves_to_use, from_curves=True
                    )
                else:
                    keyframes = None  # will be populated below

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
                    if not split_static:
                        keyframes = AnimUtils.get_keyframe_times(
                            curves_to_use, from_curves=True
                        )
                    else:
                        keyframes = None

            # Determine segment ranges
            if split_static:
                active_segments, _, all_kf = (
                    cls._get_active_animation_segments(
                        curves_to_use,
                        tolerance=static_tolerance,
                        ignore_visibility_holds=ignore_visibility_holds,
                        motion_only=motion_only,
                        motion_rate=motion_rate,
                        time_range=time_range,
                    )
                )
                # Use keyframes collected inside _get_active_animation_segments
                # so we don't query maya a second time.
                if keyframes is None:
                    keyframes = all_kf

            # Common validation and filtering for both branches
            if not keyframes or not curves_to_use:
                continue

            keyframes = sorted(set(keyframes))

            # Apply time range filter if specified
            if range_start is not None or range_end is not None:
                eps = 1e-3
                keyframes = [
                    k
                    for k in keyframes
                    if (range_start is None or k >= range_start - eps)
                    and (range_end is None or k <= range_end + eps)
                ]
                if not keyframes:
                    continue

            if split_static:
                # Filter active segments to time range.
                # Use a small tolerance so boundary keys that land at
                # shot.end via float-imprecise ripple deltas are not
                # dropped (seg_start may exceed seg_end by ~1 ULP).
                if range_start is not None or range_end is not None:
                    _BOUNDARY_EPS = 1e-4
                    filtered_segments = []
                    for seg_start, seg_end in active_segments:
                        if range_start is not None:
                            seg_start = max(seg_start, range_start)
                        if range_end is not None:
                            seg_end = min(seg_end, range_end)
                        if seg_start > seg_end + _BOUNDARY_EPS:
                            continue
                        # Clamp so output always has start <= end
                        filtered_segments.append(
                            (seg_start, max(seg_start, seg_end))
                        )
                    active_segments = filtered_segments
            else:
                active_segments = [(keyframes[0], keyframes[-1])]

            # Default behavior: absorb trailing holds into the segment so hold keys
            # move with the segment (prevents collisions during shifting/staggering).
            # Optional behavior (ignore_holds=True): keep segments active-only.
            #
            # When there are NO active segments but there ARE keyframes (hold-only
            # object) and ignore_holds is False, synthesise a single hold segment
            # spanning all keyframes so the object remains visible.
            # Only applies in motion_only mode — in non-motion_only mode, flat
            # curves are legitimately static and should produce 0 segments.
            if (
                split_static
                and not active_segments
                and not ignore_holds
                and keyframes
                and motion_only
            ):
                active_segments = [(keyframes[0], keyframes[-1])]

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

        if progress_callback and total_objs:
            progress_callback(total_objs, total_objs, "Done")
        return segments

    @classmethod
    def get_scene_info(
        cls,
        objects: Optional[List[str]] = None,
        detailed: bool = True,
        ignore_holds: bool = True,
        traversal: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Collect animation segments for the scene info report.

        Data-only counterpart to :meth:`print_scene_info` — returns the
        segments list rather than formatting it. Feed the result to
        :meth:`format_scene_info_text` / :meth:`format_scene_info_html`
        or to :meth:`format_time_ranges_*` directly.

        Args:
            objects: List of objects to analyze. ``None`` falls back to
                the active selection, then to every scene transform.
            detailed: If True, segments are split at static gaps and
                visibility holds are reported as active.
            ignore_holds: If True, trailing holds are excluded.
            traversal: Optional dependency-graph expansion of *objects*:
                ``"upstream"`` includes nodes feeding into them,
                ``"downstream"`` includes nodes they drive, ``"both"``
                includes both directions. ``None`` (default) leaves the
                input set untouched. Ignored when *objects* is empty.

        Returns:
            List of segment dictionaries (see :meth:`collect_segments`),
            or an empty list when nothing animates in the resolved set.
        """
        if isinstance(objects, str):
            objects = [objects]
        if not objects:
            objects = cmds.ls(selection=True, type="transform") or []
        if not objects:
            objects = cmds.ls(type="transform") or []

        if not objects:
            return []

        if traversal in ("upstream", "downstream", "both"):
            expanded: List[str] = list(objects)
            seen = set(expanded)
            for obj in list(objects):
                # ``pdo=False`` (default) keeps DAG nodes in the result so
                # the transform filter below has something to find;
                # ``pdo=True`` would prune them and the traversal would
                # silently no-op.
                if traversal == "upstream":
                    extra = cmds.listHistory(obj) or []
                elif traversal == "downstream":
                    extra = cmds.listHistory(obj, future=True) or []
                else:  # both
                    extra = (cmds.listHistory(obj) or []) + (
                        cmds.listHistory(obj, future=True) or []
                    )
                # Restrict to transforms — only those carry animatable
                # keyframes the report expects.
                for n in extra:
                    if n in seen:
                        continue
                    try:
                        if cmds.objectType(n, isAType="transform"):
                            seen.add(n)
                            expanded.append(n)
                    except Exception:
                        continue
            objects = expanded

        return cls.collect_segments(
            objects,
            split_static=True,
            ignore_visibility_holds=detailed,
            ignore_holds=ignore_holds,
            progress_callback=progress_callback,
        )

    @classmethod
    def format_scene_info_text(
        cls,
        objects: Optional[List[str]] = None,
        detailed: bool = True,
        csv_output: bool = False,
        by_time: bool = False,
        ignore_holds: bool = True,
        traversal: Optional[str] = None,
    ) -> str:
        """Plain-text scene-info report. Empty string when nothing animates."""
        segments = cls.get_scene_info(
            objects=objects,
            detailed=detailed,
            ignore_holds=ignore_holds,
            traversal=traversal,
        )
        if not segments:
            return ""
        return cls.format_time_ranges_text(
            segments,
            per_segment=detailed,
            csv_output=csv_output,
            by_time=by_time,
        )

    @classmethod
    def format_scene_info_html(
        cls,
        objects: Optional[List[str]] = None,
        detailed: bool = True,
        csv_output: bool = False,
        by_time: bool = False,
        ignore_holds: bool = True,
        traversal: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> str:
        """HTML scene-info report for ``sb.text_view_dialog``.

        Returns a self-contained HTML fragment. Empty string when
        nothing animates.
        """
        segments = cls.get_scene_info(
            objects=objects,
            detailed=detailed,
            ignore_holds=ignore_holds,
            traversal=traversal,
            progress_callback=progress_callback,
        )
        if not segments:
            return ""
        scene_path = cmds.file(query=True, sceneName=True)
        scene_name = (
            scene_path.replace("\\", "/").rsplit("/", 1)[-1]
            if scene_path
            else "Untitled"
        )
        return cls.format_time_ranges_html(
            segments,
            title=f"Animation Info — {scene_name}",
            per_segment=detailed,
            csv_output=csv_output,
            by_time=by_time,
        )

    @classmethod
    def print_scene_info(
        cls,
        objects: Optional[List[str]] = None,
        detailed: bool = True,
        csv_output: bool = False,
        by_time: bool = False,
        ignore_holds: bool = True,
    ):
        """Print animation info to stdout. Thin wrapper over
        :meth:`get_scene_info` + :meth:`print_time_ranges` kept for
        backward compatibility; new callers should prefer
        :meth:`format_scene_info_text` / :meth:`format_scene_info_html`
        and route the result to their target widget or log.
        """
        segments = cls.get_scene_info(
            objects=objects, detailed=detailed, ignore_holds=ignore_holds
        )
        if not segments:
            cmds.warning("No animation found on the specified objects.")
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
                # Use name to ensure identity across node instances
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
                cmds.warning(
                    f"Merged {len(indices)} groups sharing curves: {', '.join(merged_obj_names)}. Shared curves prevent independent staggering."
                )

        return merged_groups

    @staticmethod
    def _filter_curves_by_ignore(
        curves: List[str],
        ignore: Optional[Union[str, List[str]]],
    ) -> List[str]:
        """Filter out curves connected to ignored attributes.

        Parameters:
            curves: List of animation curve node names.
            ignore: Attribute name(s) to exclude.

        Returns:
            Filtered list of curve names.
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

        filtered = []
        for curve in curves:
            curve_name = str(curve).lower()
            # Short name: take portion after last '|' (or whole string)
            curve_short = curve_name.rsplit("|", 1)[-1]

            # Check if the curve matches any ignored pattern
            if curve_short in ignored_full or curve_name in ignored_full:
                continue

            if curve_short.endswith(ignored_suffixes) or curve_name.endswith(
                ignored_suffixes
            ):
                continue

            filtered.append(curve)

        return filtered

    @staticmethod
    def _filter_curves_by_channel_box(
        curves: List[str],
        channel_box_attrs: Optional[List[str]],
    ) -> List[str]:
        """Filter curves to only those connected to channel box selected attributes.

        Parameters:
            curves: List of animation curve node names.
            channel_box_attrs: List of attribute names from channel box selection.

        Returns:
            Filtered list of curve names.
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
        curves: List[str],
        tolerance: float = 1e-4,
        ignore_visibility_holds: bool = False,
        motion_only: bool = False,
        motion_rate: float = 1e-3,
        time_range: Optional[Tuple[Optional[float], Optional[float]]] = None,
    ) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[float]]:
        """Identify segments of active animation, excluding static gaps.

        A segment is considered active if at least one curve has changing values.
        Static gaps (where all curves hold the same value) are excluded.

        When *motion_only* is ``True``, stepped tangent intervals are
        treated specially: same-value holds are skipped entirely, and
        value-change intervals emit the full ``[t1, t2]`` range so
        downstream clips span both keys.  When *time_range* is also
        provided, stepped intervals whose boundary keys both fall
        outside the range are dropped before the merge step so they
        cannot swallow nearby intervals from other curves.

        Note: Visibility curves are treated as active between all keys
        to preserve holds during scaling, unless *ignore_visibility_holds*
        is ``True``.

        Parameters:
            curves: List of animation curves to analyze (nodes or strings).
            tolerance: Value tolerance for detecting static segments.
            ignore_visibility_holds: If True, visibility curves are treated like any
                other curve (static holds are ignored).
            motion_rate: Per-frame rate threshold used when *motion_only* is
                ``True``.  An interval is considered motion only when
                ``abs(v2 - v1) / max(t2 - t1, 1) > motion_rate``.  This
                normalises by interval duration so sparse/baked keys that
                drift slowly are correctly classified as static.
            time_range: Optional ``(start, end)`` used to pre-filter stepped
                intervals.  Intervals whose boundary keys are both outside
                this range are dropped before the merge step.

        Returns:
            A tuple ``(merged_spans, empty_list, all_keyframe_times)``
            where *merged_spans* is a list of ``(start, end)`` tuples for
            active animation, *empty_list* is always ``[]`` (kept for API
            compatibility), and *all_keyframe_times* is a sorted list of
            every keyframe time across all curves.
        """
        import maya.cmds as cmds

        _log = _log_segments

        if not curves:
            return ([], [], [])

        all_intervals = []
        all_keyframe_times: set = set()

        for curve in curves:
            crv = str(curve)
            times = cmds.keyframe(crv, query=True, timeChange=True)
            values = cmds.keyframe(crv, query=True, valueChange=True)

            if not times:
                continue

            all_keyframe_times.update(times)

            if len(times) == 1:
                all_intervals.append((times[0], times[0]))
                continue

            # Check if curve is visibility
            is_visibility = False
            if not ignore_visibility_holds:
                crv_lower = crv.lower()
                if "visibility" in crv_lower:
                    is_visibility = True
                else:
                    try:
                        dest_plugs = (
                            cmds.listConnections(crv, plugs=True, d=True, s=False) or []
                        )
                        for plug_str in dest_plugs:
                            attr = (
                                plug_str.rsplit(".", 1)[-1] if "." in plug_str else ""
                            )
                            if "visibility" in attr.lower():
                                is_visibility = True
                                break
                    except Exception:
                        pass

            # Query out-tangent types for debug logging
            out_tangents = cmds.keyTangent(crv, query=True, outTangentType=True) or []

            _log.debug(
                "[SEGMENTS] curve=%s is_vis=%s times=%s vals=%s tangents=%s",
                crv,
                is_visibility,
                times,
                values,
                out_tangents,
            )

            for i in range(len(times) - 1):
                t1, t2 = times[i], times[i + 1]
                v1, v2 = values[i], values[i + 1]

                # Stepped tangents: no gradual motion during the
                # interval — value holds at v1, then jumps at the
                # boundary.  When motion_only is active, skip the
                # interval entirely (it's a hold) and emit only the
                # point where the value actually changes.
                ot = out_tangents[i] if i < len(out_tangents) else ""
                is_stepped = ot in ("step", "stepnext")

                # For visibility, treat all intervals as active to preserve holds
                # unless ignore_visibility_holds is True.
                # Static intervals (same value) are also kept as zero-duration
                # points so the object still appears in the sequencer.
                if motion_only:
                    if is_stepped:
                        # Stepped: value is constant for the interval
                        # (no gradual motion).  Same-value holds are
                        # skipped.  When a time range is given, trim
                        # intervals whose boundary keys fall outside:
                        #   - Both outside → drop (pass-through hold)
                        #   - One outside  → emit only the in-range
                        #     boundary as a point (the out-of-range
                        #     portion is a pre-existing hold)
                        #   - Both inside  → emit full [t1, t2]
                        if abs(v2 - v1) <= 1e-6:
                            continue
                        if time_range is not None:
                            r_lo = time_range[0] if time_range[0] is not None else -1e18
                            r_hi = time_range[1] if time_range[1] is not None else 1e18
                            t1_in = t1 >= r_lo
                            t2_in = t2 <= r_hi
                            if not t1_in and not t2_in:
                                _log.debug(
                                    "[SEGMENTS]   interval %s-%s: STEPPED pass-through [SKIPPED]",
                                    t1, t2,
                                )
                                continue
                            if not t1_in or not t2_in:
                                # Partial: keep only the in-range key
                                # as a zero-duration point.
                                pt = t2 if t1_in is False else t1
                                all_intervals.append((pt, pt))
                                _log.debug(
                                    "[SEGMENTS]   interval %s-%s: STEPPED partial -> point at %s",
                                    t1, t2, pt,
                                )
                                continue
                        # Both keys in range (or no time_range) — emit
                        # full interval so clips span both keys.
                        is_value_change = True
                    else:
                        dt = max(t2 - t1, 1.0)
                        is_value_change = abs(v2 - v1) / dt > motion_rate
                else:
                    is_value_change = abs(v1 - v2) > tolerance

                if is_visibility or is_value_change:
                    all_intervals.append((t1, t2))
                    _log.debug(
                        "[SEGMENTS]   interval %s-%s: ACTIVE (vals %s->%s)",
                        t1,
                        t2,
                        v1,
                        v2,
                    )
                else:
                    _log.debug(
                        "[SEGMENTS]   interval %s-%s: STATIC [SKIPPED] (val=%s)",
                        t1,
                        t2,
                        v1,
                    )

        if not all_intervals:
            return ([], [], sorted(all_keyframe_times))

        # Merge overlapping span intervals
        all_intervals.sort(key=lambda x: x[0])
        merged = []
        current_start, current_end = all_intervals[0]
        for i in range(1, len(all_intervals)):
            next_start, next_end = all_intervals[i]
            if next_start <= current_end:
                current_end = max(current_end, next_end)
            else:
                merged.append((current_start, current_end))
                current_start, current_end = next_start, next_end
        merged.append((current_start, current_end))

        return (merged, [], sorted(all_keyframe_times))

    @staticmethod
    def shift_curves(
        curves: List[Any],
        offset: float,
        time_range: Optional[Tuple[float, float]] = None,
        remove_flat_at_dest: bool = False,
    ):
        """Shift keys on curves by offset in a single relative move,
        optionally pre-cleaning static (flat/hold) keys at the destination
        so they can't collide with the incoming keys.

        Args:
            curves: List of animation curves to shift.
            offset: Amount to shift (in frames).
            time_range: Optional (start, end) tuple to limit the shift to specific keys.
            remove_flat_at_dest: If True, remove static (flat/hold) keys at the
                destination range before moving.  This prevents collisions with
                keys that aren't part of active animation.
        """
        _log = _log_shift

        _log.debug(
            "[SHIFT] curves=%s offset=%s range=%s remove_flat=%s",
            curves,
            offset,
            time_range,
            remove_flat_at_dest,
        )

        if not curves or abs(offset) < 1e-6:
            _log.debug("[SHIFT] early return: no curves or zero offset")
            return

        eps = 1e-3

        # Pre-clean: remove flat keys at the destination that would collide
        if remove_flat_at_dest and time_range:
            src_start, src_end = float(time_range[0]), float(time_range[1])
            dst_start = src_start + offset
            dst_end = src_end + offset
            dst_range = (dst_start - eps, dst_end + eps)
            for curve in curves:
                try:
                    dest_keys = cmds.keyframe(
                        curve, q=True, time=dst_range, timeChange=True
                    )
                    if not dest_keys:
                        continue
                    dest_vals = cmds.keyframe(
                        curve, q=True, time=dst_range, valueChange=True
                    )
                    if not dest_vals or len(dest_vals) != len(dest_keys):
                        continue
                    # Remove keys whose value matches their neighbours (flat)
                    for kt, kv in zip(dest_keys, dest_vals):
                        # Skip keys that are also in the source range (being moved)
                        if src_start - eps <= kt <= src_end + eps:
                            continue
                        # Check if value matches curve value just before/after
                        try:
                            v_before = cmds.keyframe(
                                curve,
                                q=True,
                                eval=True,
                                time=(kt - 1, kt - 1),
                            )
                            v_after = cmds.keyframe(
                                curve,
                                q=True,
                                eval=True,
                                time=(kt + 1, kt + 1),
                            )
                            is_flat = True
                            if v_before and abs(v_before[0] - kv) > 1e-4:
                                is_flat = False
                            if v_after and abs(v_after[0] - kv) > 1e-4:
                                is_flat = False
                            if is_flat:
                                _log.debug(
                                    "[SHIFT] pre-clean: removing flat key on %s at %s",
                                    curve,
                                    kt,
                                )
                                cmds.cutKey(curve, time=(kt, kt), clear=True)
                        except Exception:
                            pass
                except Exception:
                    continue

        for curve in curves:
            try:
                curve = str(curve)
                kw_range = {}
                if time_range:
                    start, end = float(time_range[0]), float(time_range[1])
                    kw_range["time"] = (start - eps, end + eps)
                else:
                    pass

                # Skip if no keys match
                matched_keys = cmds.keyframe(curve, q=True, **kw_range)
                if not matched_keys:
                    _log.debug("[SHIFT] %s: no keys in range — skipping", curve)
                    continue

                _log.debug(
                    "[SHIFT] %s: matched=%s",
                    curve,
                    matched_keys,
                )

                # Single-pass: move keys directly to the destination
                cmds.keyframe(
                    curve,
                    edit=True,
                    relative=True,
                    timeChange=offset,
                    **kw_range,
                )
            except RuntimeError as e:
                _log.error("[SHIFT] Exception for %s: %s", curve, e)
                cmds.warning(f"Failed to move keys for {curve}: {e}")

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
                        if spacing > 0:
                            # Skip forward whole intervals to clear the overlap.
                            skips = math.ceil((previous_end - target_start) / spacing)
                            target_start += skips * spacing
                        else:
                            # Non-positive spacing can never clear an overlap
                            # by skipping intervals (the old loop hung Maya
                            # here) — butt against the previous group instead.
                            target_start = previous_end

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
