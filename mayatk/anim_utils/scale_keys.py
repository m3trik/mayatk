# coding=utf-8
"""Dedicated scale-keys module to keep AnimUtils lean and testable."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya environment required
    print(__file__, error)

import pythontk as ptk

# Import CoreUtils using internal path to avoid circular imports
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils._anim_utils import KeyframeGrouper


@dataclass
class _ScaleKeysContext:
    utils: Any
    objects: List[Any]
    mode: str
    factor: float
    keys: Any
    pivot: Optional[float]
    channel_box_attrs_only: bool
    ignore: Optional[Union[str, List[str]]]
    group_mode: str
    snap_mode: Optional[str]
    samples: Optional[int]
    include_rotation: Union[bool, str]
    absolute: Optional[bool]
    prevent_overlap: bool
    flatten_tangents: bool = True
    split_static: bool = True
    by_speed: bool = False
    time_range: Tuple[Optional[float], Optional[float]] = (None, None)
    selected_keys_only: bool = False
    base_start: Optional[float] = None
    base_end: Optional[float] = None
    range_specified: bool = False
    channel_box_attrs: Optional[List[str]] = None
    objects_list: List[Any] = field(default_factory=list)
    object_info: List[Dict[str, Any]] = field(default_factory=list)
    segments: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


class _ScaleKeysInternal:
    """Internal helper methods for ScaleKeys - input normalization, target collection, and grouping."""

    # Input normalization helpers
    @staticmethod
    def _normalize_group_mode(mode: Optional[str]) -> str:
        """Validate group mode value.

        Valid modes: 'single_group', 'per_object', 'overlap_groups'
        """
        if mode is None:
            return "single_group"

        normalized = str(mode).strip().lower()
        valid_modes = {"single_group", "per_object", "overlap_groups"}

        if normalized not in valid_modes:
            raise ValueError(
                f"Invalid group_mode '{mode}'. Must be one of: {', '.join(sorted(valid_modes))}"
            )

        return normalized

    @staticmethod
    def _normalize_keys_to_time_range_and_selection(
        keys_value: Any,
    ) -> Tuple[Tuple[Optional[float], Optional[float]], bool]:
        """Normalize the ``keys`` argument into a time range and selection flag."""

        if keys_value is None:
            return (None, None), False

        if isinstance(keys_value, str):
            normalized = keys_value.strip().lower()
            if normalized == "selected":
                return (None, None), True
            try:
                numeric_value = float(keys_value)
            except (TypeError, ValueError):
                return (None, None), False
            else:
                return (numeric_value, numeric_value), False

        if isinstance(keys_value, (int, float)) and not isinstance(keys_value, bool):
            value = float(keys_value)
            return (value, value), False

        def _coerce(component: Any) -> Optional[float]:
            if component is None:
                return None
            if isinstance(component, (int, float)) and not isinstance(component, bool):
                return float(component)
            if isinstance(component, str):
                try:
                    return float(component.strip())
                except ValueError:
                    return None
            return None

        if isinstance(keys_value, (list, tuple)):
            values = list(keys_value)
            if not values:
                return (None, None), False

            if (
                len(values) == 2
                and not isinstance(values[0], (list, tuple))
                and not isinstance(values[1], (list, tuple))
            ):
                start = _coerce(values[0])
                end = _coerce(values[1])
                if start is not None or end is not None:
                    return (start, end), False

            times: List[float] = []
            for item in values:
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    times.append(float(item))
                elif isinstance(item, str):
                    parsed = _coerce(item)
                    if parsed is not None:
                        times.append(parsed)
                elif isinstance(item, (list, tuple)) and item:
                    start = _coerce(item[0])
                    if start is not None:
                        times.append(start)
                    if len(item) > 1:
                        end = _coerce(item[1])
                        if end is not None:
                            times.append(end)

            if times:
                times.sort()
                return (times[0], times[-1]), False

        return (None, None), False

    # Target collection and grouping helpers
    @classmethod
    def _build_object_groups(
        cls, object_info: List[Dict[str, Any]], group_mode: str
    ) -> List[List[Dict[str, Any]]]:
        """Create processing groups based on the requested grouping mode."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        if not object_info:
            return []

        valid_entries = [info for info in object_info if info]
        if not valid_entries:
            return []

        normalized_mode = cls._normalize_group_mode(group_mode)

        if normalized_mode == "per_object":
            return [[info] for info in valid_entries]

        if normalized_mode == "single_group":
            return [valid_entries]

        # overlap_groups
        overlap_payload: List[Dict[str, Any]] = []
        for info in valid_entries:
            start = info.get("start")
            end = info.get("end")
            clamped = ptk.clamp_range(start, end, validate=False)
            if not clamped or clamped[0] > clamped[1]:
                continue

            overlap_payload.append(
                {
                    "obj": info.get("object"),
                    "keyframes": info.get("key_times") or [],
                    "start": clamped[0],
                    "end": clamped[1],
                    "duration": clamped[1] - clamped[0],
                    "curves": info.get("curves_to_scale")
                    or info.get("all_curves")
                    or [],
                }
            )

        if not overlap_payload:
            return [[info] for info in valid_entries]

        grouped = AnimUtils._group_overlapping_keyframes(overlap_payload)
        if not grouped:
            return [[info] for info in valid_entries]

        info_lookup = {info.get("object"): info for info in valid_entries}
        groups: List[List[Dict[str, Any]]] = []

        for group in grouped:
            group_infos: List[Dict[str, Any]] = []
            for obj in group.get("objects", []):
                info = info_lookup.get(obj)
                if info and info not in group_infos:
                    group_infos.append(info)
            if group_infos:
                groups.append(group_infos)

        return groups if groups else [[info] for info in valid_entries]

    @classmethod
    def _resolve_group_bounds(
        cls,
        group: List[Dict[str, Any]],
        base_start: Optional[float],
        base_end: Optional[float],
    ) -> Optional[Tuple[float, float]]:
        """Compute the overall time range for a group of objects."""

        if not group:
            return None

        starts: List[float] = []
        ends: List[float] = []

        for info in group:
            range_tuple = ptk.clamp_range(
                info.get("start"), info.get("end"), base_start, base_end, validate=False
            )
            if not range_tuple or range_tuple[0] > range_tuple[1]:
                continue

            starts.append(range_tuple[0])
            ends.append(range_tuple[1])

        if not starts or not ends:
            return None

        return min(starts), max(ends)

    @classmethod
    def _resolve_range_for_object(
        cls,
        info: Dict[str, Any],
        group_range: Optional[Tuple[float, float]],
        group_mode: str,
        base_start: Optional[float],
        base_end: Optional[float],
    ) -> Optional[Tuple[float, float]]:
        """Determine the active time range for an object within a group."""

        clamped = ptk.clamp_range(
            info.get("start"), info.get("end"), base_start, base_end, validate=False
        )
        if not clamped or clamped[0] > clamped[1]:
            return None

        if group_mode == "per_object" or group_range is None:
            return clamped

        clamped_group = ptk.clamp_range(
            clamped[0], clamped[1], group_range[0], group_range[1], validate=False
        )
        return (
            clamped_group
            if (clamped_group and clamped_group[0] <= clamped_group[1])
            else None
        )

    @classmethod
    def _collect_scale_targets(
        cls,
        objects: List["pm.PyNode"],
        ignore: Optional[Union[str, List[str]]],
        channel_box_attrs: Optional[List[str]],
        selected_keys_only: bool,
        range_specified: bool,
        base_start: Optional[float],
        base_end: Optional[float],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Prepare per-object payload for scale_keys, respecting ignore filters."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        targets: List[Dict[str, Any]] = []
        diagnostics: Dict[str, Any] = {
            "objects_processed": 0,
            "objects_with_curves": 0,
            "filtered_by_ignore": 0,
            "filtered_by_channel_box": 0,
        }

        for obj in objects:
            diagnostics["objects_processed"] += 1

            curves_initial = AnimUtils.objects_to_curves(obj)
            if not curves_initial:
                continue

            diagnostics["objects_with_curves"] += 1

            curves_filtered = AnimUtils._filter_curves_by_ignore(curves_initial, ignore)
            if not curves_filtered:
                diagnostics["filtered_by_ignore"] += 1
                continue

            curves_all = cls._filter_curves_by_channel_box(
                curves_filtered, channel_box_attrs
            )
            if not curves_all:
                if channel_box_attrs:
                    diagnostics["filtered_by_channel_box"] += 1
                continue

            curves_all = list(dict.fromkeys(curves_all))
            info: Dict[str, Any] = {
                "object": obj,
                "all_curves": curves_all,
            }

            if selected_keys_only:
                selected_curves = (
                    pm.keyframe(obj, query=True, name=True, selected=True) or []
                )
                selected_curves = AnimUtils._filter_curves_by_ignore(
                    selected_curves, ignore
                )
                selected_curves = cls._filter_curves_by_channel_box(
                    selected_curves, channel_box_attrs
                )
                selected_curves = list(dict.fromkeys(selected_curves))
                if not selected_curves:
                    continue
                info["curves_to_scale"] = selected_curves
                key_mode = "selected"
            else:
                info["curves_to_scale"] = curves_all
                key_mode = "all"

            key_times_full_raw = AnimUtils.get_keyframe_times(
                info["curves_to_scale"], mode=key_mode, from_curves=True
            )
            key_times_full = (
                sorted({float(t) for t in key_times_full_raw})
                if key_times_full_raw
                else []
            )
            if not key_times_full:
                continue

            info["key_times_full"] = key_times_full
            info["start_full"] = key_times_full[0]
            info["end_full"] = key_times_full[-1]

            if range_specified:
                filtered_times = [
                    t
                    for t in key_times_full
                    if (base_start is None or t >= base_start)
                    and (base_end is None or t <= base_end)
                ]
            else:
                filtered_times = key_times_full

            info["key_times"] = filtered_times
            info["start"] = filtered_times[0] if filtered_times else None
            info["end"] = filtered_times[-1] if filtered_times else None

            targets.append(info)

        return targets, diagnostics

    @staticmethod
    def _filter_curves_by_channel_box(
        curves: Optional[List[Union[str, "pm.PyNode"]]],
        channel_box_attrs: Optional[List[str]],
    ) -> List["pm.PyNode"]:
        """Restrict curves to those whose attributes are selected in the channel box."""

        if not curves:
            return []

        if not channel_box_attrs:
            return [pm.PyNode(curve) for curve in curves]

        # Parse channel box attrs into full names and simple names (last component)
        allowed_full: Set[str] = set()
        allowed_simple: Set[str] = set()

        for attr in channel_box_attrs:
            if not attr:
                continue
            attr_lower = str(attr).lower()
            allowed_full.add(attr_lower)
            # Extract simple name (last component after . or |) using func parameter
            if "." in attr_lower:
                parts = ptk.split_delimited_string(
                    attr_lower, delimiter=".", func=lambda x: x[-1:]
                )
                allowed_simple.add(parts[0])
            elif "|" in attr_lower:
                parts = ptk.split_delimited_string(
                    attr_lower, delimiter="|", func=lambda x: x[-1:]
                )
                allowed_simple.add(parts[0])
            else:
                allowed_simple.add(attr_lower)

        if not allowed_full and not allowed_simple:
            return [pm.PyNode(curve) for curve in curves]

        filtered: List["pm.PyNode"] = []
        for curve in curves:
            try:
                curve_node = pm.PyNode(curve)
            except Exception:
                continue

            connections = (
                pm.listConnections(
                    curve_node, plugs=True, destination=True, source=False
                )
                or []
            )

            for conn in connections:
                attr_name = conn.attrName()
                if not attr_name:
                    continue

                attr_key = attr_name.lower()
                full_name = f"{conn.node().name()}.{attr_name}".lower()
                if attr_key in allowed_simple or full_name in allowed_full:
                    filtered.append(curve_node)
                    break

        return filtered

    # Processing helpers
    @classmethod
    def _process_speed(
        cls,
        ctx: _ScaleKeysContext,
        groups: List[List[Dict[str, Any]]],
        overlap_groups_data: List[Dict[str, Any]],
    ) -> int:
        keys_scaled = 0
        processed_objects = 0
        min_target_start: Optional[float] = None
        max_target_end: Optional[float] = None

        try:
            factor_val = float(ctx.factor)
        except (TypeError, ValueError):
            pm.warning("Factor must be a numeric value in speed mode.")
            return 0

        if factor_val <= 0.0:
            pm.warning("Factor must be greater than 0 in speed mode.")
            return 0

        for group in groups:
            group_range = (
                None
                if ctx.group_mode == "per_object"
                else cls._resolve_group_bounds(group, ctx.base_start, ctx.base_end)
            )
            if ctx.group_mode != "per_object":
                if not group_range or group_range[1] <= group_range[0]:
                    continue

            group_calculations = []
            for info in group:
                object_range = cls._resolve_range_for_object(
                    info, group_range, ctx.group_mode, ctx.base_start, ctx.base_end
                )
                if not object_range or object_range[1] <= object_range[0]:
                    continue

                num_curves = len(info.get("all_curves", []))
                if num_curves == 0:
                    pm.warning(
                        f"Object {info['object']} has no curves in range {object_range}"
                    )
                    continue

                sample_times, progress, total_distance = (
                    ctx.utils._compute_motion_progress(
                        info["object"],
                        object_range,
                        samples=ctx.samples,
                        include_rotation=ctx.include_rotation,
                    )
                )

                if not sample_times or total_distance <= 1e-6:
                    pm.warning(
                        f"No motion detected for {info['object']} in range {object_range}. "
                        f"Speed-based retiming requires spatial movement. Skipping {num_curves} curve(s)."
                    )
                    continue

                original_duration = object_range[1] - object_range[0]
                if original_duration <= 0.0:
                    continue

                if ctx.absolute:
                    effective_target_speed = factor_val
                else:
                    current_speed = total_distance / original_duration
                    effective_target_speed = current_speed * factor_val

                target_duration = total_distance / effective_target_speed

                group_calculations.append(
                    {
                        "info": info,
                        "object_range": object_range,
                        "sample_times": sample_times,
                        "progress": progress,
                        "target_duration": target_duration,
                        "total_distance": total_distance,
                        "original_duration": original_duration,
                    }
                )

            scale_factor = 1.0
            pivot = group_range[0] if group_range else None

            if ctx.absolute:
                max_ratio = 0.0
                for item in group_calculations:
                    if item["original_duration"] > 1e-6:
                        ratio = item["target_duration"] / item["original_duration"]
                        if ratio > max_ratio:
                            max_ratio = ratio
                scale_factor = max_ratio
            else:
                scale_factor = 1.0 / float(ctx.factor)

            if ctx.group_mode == "per_object":
                for item in group_calculations:
                    if item["original_duration"] > 1e-6:
                        obj_factor = item["target_duration"] / item["original_duration"]
                        cls.scale_keys(
                            objects=[item["info"]["object"]],
                            mode="uniform",
                            factor=obj_factor,
                            pivot=item["object_range"][0],
                            snap_mode=ctx.snap_mode,
                            absolute=False,
                            group_mode="per_object",
                            keys=item["object_range"],
                            channel_box_attrs_only=ctx.channel_box_attrs_only,
                            ignore=ctx.ignore,
                        )
                        keys_scaled += len(item["info"].get("curves_to_scale", []))
                        processed_objects += 1
            else:
                all_curves = []
                for item in group_calculations:
                    all_curves.extend(item["info"].get("curves_to_scale", []))

                if all_curves:
                    group_objects = [
                        item["info"]["object"] for item in group_calculations
                    ]
                    cls.scale_keys(
                        objects=group_objects,
                        mode="uniform",
                        factor=scale_factor,
                        pivot=pivot,
                        snap_mode=ctx.snap_mode,
                        absolute=False,
                        group_mode="single_group",
                        keys=group_range,
                        channel_box_attrs_only=ctx.channel_box_attrs_only,
                        ignore=ctx.ignore,
                    )
                    keys_scaled += len(all_curves)
                    processed_objects += len(group_objects)

        cls._apply_overlap_prevention(ctx, overlap_groups_data)
        cls._report_speed(
            ctx, keys_scaled, processed_objects, min_target_start, max_target_end
        )
        return keys_scaled

    @classmethod
    def _process_uniform(
        cls,
        ctx: _ScaleKeysContext,
        groups: List[List[Dict[str, Any]]],
        overlap_groups_data: List[Dict[str, Any]],
    ) -> int:
        if ctx.factor <= 0:
            pm.warning("Scale factor must be greater than 0.")
            return 0

        is_identity_scale = not ctx.absolute and abs(ctx.factor - 1.0) < 1e-6
        should_snap = ctx.snap_mode and ctx.snap_mode != "none"
        if is_identity_scale and not should_snap:
            pm.displayInfo("Scale factor is 1.0. No changes applied.")
            return 0

        keys_scaled = 0
        mode_label_map = {
            "single_group": "single-group pivot",
            "per_object": "per-object pivots",
            "overlap_groups": "overlap-group pivots",
        }
        global_pivot: Optional[float] = None

        for group in groups:
            group_range = (
                None
                if ctx.group_mode == "per_object"
                else cls._resolve_group_bounds(group, ctx.base_start, ctx.base_end)
            )

            if ctx.group_mode != "per_object":
                if not group_range or group_range[1] < group_range[0]:
                    continue
                group_pivot = ctx.pivot if ctx.pivot is not None else group_range[0]
                if global_pivot is None:
                    global_pivot = group_pivot
            else:
                group_pivot = None

            for info in group:
                curves_to_scale = info.get("curves_to_scale", [])
                if not curves_to_scale:
                    continue

                object_range = cls._resolve_range_for_object(
                    info, group_range, ctx.group_mode, ctx.base_start, ctx.base_end
                )

                # Determine pivot time based on mode
                # When split_static is True with per_segment mode (via group_mode=per_object),
                # each segment scales around its own start.
                # For overlap_groups and single_group, segments scale around the group pivot.
                if ctx.pivot is not None and ctx.group_mode != "per_object":
                    pivot_time = ctx.pivot
                elif ctx.group_mode == "per_object":
                    # For per_object mode (which maps to per_segment when split_static=True),
                    # use the segment's start as the pivot
                    segment_range = info.get("segment_range")
                    pivot_time = (
                        segment_range[0]
                        if segment_range
                        else (object_range[0] if object_range else None)
                    )
                else:
                    pivot_time = group_pivot

                if pivot_time is None:
                    continue
                pivot_time = float(pivot_time)

                effective_factor = ctx.factor
                if ctx.absolute:
                    current_duration = 0.0
                    if ctx.group_mode == "per_object":
                        # Use segment duration for per_object/per_segment mode
                        segment_range = info.get("segment_range")
                        if segment_range:
                            current_duration = segment_range[1] - segment_range[0]
                        elif object_range:
                            current_duration = object_range[1] - object_range[0]
                    else:
                        if group_range:
                            current_duration = group_range[1] - group_range[0]

                    if current_duration > 1e-6:
                        effective_factor = ctx.factor / current_duration
                    else:
                        continue

                # Determine time range for key selection
                # When split_static is enabled, we use the segment range for PIVOT calculation
                # but we should scale ALL keys on the object, not just those within the segment.
                # The segment boundaries define where animation is "active" for grouping purposes,
                # but flat curves at the same times should still be scaled.
                if ctx.range_specified and object_range:
                    time_arg = object_range
                else:
                    time_arg = None

                if ctx.selected_keys_only or (ctx.snap_mode is not None):
                    curve_times_map = {}
                    for curve in curves_to_scale:
                        kwargs = {"query": True, "tc": True}
                        if ctx.selected_keys_only:
                            kwargs["selected"] = True
                        if time_arg:
                            kwargs["time"] = time_arg

                        times = pm.keyframe(curve, **kwargs)
                        if times:
                            curve_times_map[curve] = list(times)

                    for curve, times in curve_times_map.items():
                        time_pairs = []
                        for old_time in times:
                            new_time = (
                                pivot_time + (old_time - pivot_time) * effective_factor
                            )
                            time_pairs.append((old_time, new_time))

                        moved = ctx.utils._move_curve_keys(curve, time_pairs)
                        keys_scaled += moved

                        if ctx.snap_mode and ctx.snap_mode != "none":
                            new_times = [new_time for _, new_time in time_pairs]
                            snapped = ctx.utils._snap_curve_keys(
                                curve, new_times, ctx.snap_mode
                            )
                            keys_scaled += snapped
                else:
                    for curve in curves_to_scale:
                        if time_arg:
                            keys = pm.keyframe(
                                curve, query=True, tc=True, time=time_arg
                            )
                        else:
                            keys = pm.keyframe(curve, query=True, tc=True)

                        if not keys:
                            continue

                        if time_arg:
                            pm.scaleKey(
                                curve,
                                time=time_arg,
                                timeScale=effective_factor,
                                timePivot=pivot_time,
                            )
                        else:
                            pm.scaleKey(
                                curve, timeScale=effective_factor, timePivot=pivot_time
                            )
                        keys_scaled += len(keys)

                        if ctx.snap_mode and ctx.snap_mode != "none":
                            if time_arg:
                                scaled_keys = pm.keyframe(
                                    curve, query=True, tc=True, time=time_arg
                                )
                            else:
                                scaled_keys = pm.keyframe(curve, query=True, tc=True)

                            if scaled_keys:
                                snapped = ctx.utils._snap_curve_keys(
                                    curve, scaled_keys, ctx.snap_mode
                                )
                                keys_scaled += snapped

        cls._apply_overlap_prevention(ctx, overlap_groups_data)
        cls._report_uniform(ctx, keys_scaled, mode_label_map, global_pivot)
        return keys_scaled

    # Shared helpers
    @classmethod
    def _apply_overlap_prevention(
        cls, ctx: _ScaleKeysContext, overlap_groups_data: List[Dict[str, Any]]
    ) -> None:
        if ctx.prevent_overlap and overlap_groups_data:
            for data in overlap_groups_data:
                times = ctx.utils.get_keyframe_times(
                    data.get("curves", []), from_curves=True, as_range=True
                )
                if times:
                    data["start"], data["end"] = times
                    data["duration"] = times[1] - times[0]

            overlap_groups_data.sort(key=lambda x: x["start"])
            start_frame = overlap_groups_data[0]["start"]

            ctx.utils._apply_stagger(
                overlap_groups_data,
                start_frame=start_frame,
                spacing=0,
                use_intervals=False,
                avoid_overlap=False,
                preserve_gaps=True,
            )

    @classmethod
    def _stagger_scaled_segments(
        cls,
        ctx: _ScaleKeysContext,
        groups: List[Dict[str, Any]],
    ) -> None:
        """Stagger scaled segments to prevent overlap.

        After scaling, segments may overlap each other. This method repositions
        them sequentially so that each segment starts after the previous one ends.

        Parameters:
            ctx: The scale keys context.
            groups: List of KeyframeGrouper groups (each with sub_groups containing segments).
        """
        if not groups:
            return

        # Collect segment data with updated time ranges after scaling
        stagger_data = []

        for group in groups:
            # Get all curves from the group's segments
            group_curves = []
            for seg in group.get("sub_groups", []):
                group_curves.extend(seg.get("curves", []))
            group_curves = list(dict.fromkeys(group_curves))  # Dedupe

            if not group_curves:
                continue

            # Query the CURRENT time range after scaling
            times = ctx.utils.get_keyframe_times(
                group_curves, from_curves=True, as_range=True
            )
            if not times:
                continue

            stagger_data.append(
                {
                    "obj": group.get("obj") or (group.get("objects", [None])[0]),
                    "curves": group_curves,
                    "start": times[0],
                    "end": times[1],
                    "duration": times[1] - times[0],
                    "keyframes": list(
                        ctx.utils.get_keyframe_times(group_curves, from_curves=True)
                        or []
                    ),
                }
            )

        if len(stagger_data) < 2:
            return  # Nothing to stagger with less than 2 groups

        # Sort by start time
        stagger_data.sort(key=lambda x: x["start"])

        # Apply stagger with spacing=0 and avoid_overlap behavior
        start_frame = stagger_data[0]["start"]

        ctx.utils._apply_stagger(
            stagger_data,
            start_frame=start_frame,
            spacing=0,
            use_intervals=False,
            avoid_overlap=False,
            preserve_gaps=False,  # Don't preserve gaps - place sequentially
        )

    @staticmethod
    def _report_speed(
        ctx: _ScaleKeysContext,
        keys_scaled: int,
        processed_objects: int,
        min_target_start: Optional[float],
        max_target_end: Optional[float],
    ) -> None:
        if keys_scaled > 0:
            mode_label = {
                "single_group": "single-group",
                "per_object": "per-object",
                "overlap_groups": "overlap-group",
            }[ctx.group_mode]
            range_info = ""
            if min_target_start is not None and max_target_end is not None:
                range_info = (
                    f" (target range: {min_target_start:.2f} -> {max_target_end:.2f})"
                )

            speed_info = (
                f"{float(ctx.factor):.3f} units/frame"
                if ctx.absolute
                else f"{float(ctx.factor):.2f}x speed"
            )
            pm.displayInfo(
                f"Retimed {keys_scaled} keyframes to {speed_info} using {mode_label} ranges (objects processed={processed_objects}){range_info}."
            )
        else:
            pm.warning(
                "No keyframes were retimed. Check the specified objects and time range."
            )

    # Preparation helpers
    @classmethod
    def _prepare_context(
        cls,
        utils_cls: Any,
        *,
        objects=None,
        mode="uniform",
        factor: float,
        keys=None,
        pivot: Optional[float],
        channel_box_attrs_only: bool,
        ignore: Optional[Union[str, List[str]]],
        group_mode: str,
        snap_mode: Optional[str],
        samples: Optional[int],
        include_rotation: Union[bool, str],
        absolute: Optional[bool],
        prevent_overlap: bool,
        flatten_tangents: bool = True,
        split_static: bool = True,
    ) -> Optional[_ScaleKeysContext]:
        by_speed = mode == "speed"
        if mode not in {"uniform", "speed"}:
            pm.warning(f"Invalid mode '{mode}'. Using 'uniform'.")
            mode = "uniform"
            by_speed = False

        if absolute is None:
            absolute = by_speed

        if samples is None and by_speed:
            samples = 64

        time_range, selected_keys_only = (
            cls._normalize_keys_to_time_range_and_selection(keys)
        )
        base_start_raw, base_end_raw = time_range

        def _coerce_to_float(value):
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                pm.warning(f"Invalid time_range value '{value}'. Ignoring component.")
                return None

        base_start = _coerce_to_float(base_start_raw)
        base_end = _coerce_to_float(base_end_raw)
        range_specified = base_start is not None or base_end is not None

        objects = pm.selected() if objects is None else objects
        if not objects:
            pm.warning("No objects specified or selected.")
            return None

        objects = pm.ls(objects, flatten=True)
        if not objects:
            pm.warning("No valid objects specified or selected.")
            return None

        channel_box_attrs = None
        if channel_box_attrs_only:
            channel_box_attrs = pm.channelBox(
                "mainChannelBox", query=True, selectedMainAttributes=True
            )
            if not channel_box_attrs:
                pm.warning("No attributes selected in channel box.")
                return None

        if selected_keys_only and not by_speed:
            all_selected_keys = pm.keyframe(query=True, sl=True, tc=True)
            if not all_selected_keys:
                pm.warning("No keyframes selected.")
                return None

        group_mode_normalized = cls._normalize_group_mode(group_mode)
        if group_mode_normalized not in {
            "single_group",
            "per_object",
            "overlap_groups",
        }:
            pm.warning(
                f"Unsupported group_mode '{group_mode}'. Falling back to 'single_group'."
            )
            group_mode_normalized = "single_group"

        if by_speed and selected_keys_only:
            pm.warning(
                "selected_keys_only is not supported with mode='speed'. Processing all keys in range."
            )
            selected_keys_only = False

        ctx = _ScaleKeysContext(
            utils=utils_cls,
            objects=objects,
            mode=mode,
            factor=factor,
            keys=keys,
            pivot=pivot,
            channel_box_attrs_only=channel_box_attrs_only,
            ignore=ignore,
            group_mode=group_mode_normalized,
            snap_mode=snap_mode,
            samples=samples,
            include_rotation=include_rotation,
            absolute=absolute,
            prevent_overlap=prevent_overlap,
            flatten_tangents=flatten_tangents,
            split_static=split_static,
            by_speed=by_speed,
            time_range=time_range,
            selected_keys_only=selected_keys_only,
            base_start=base_start,
            base_end=base_end,
            range_specified=range_specified,
            channel_box_attrs=channel_box_attrs,
            objects_list=objects,
        )
        return ctx

    @staticmethod
    def _warn_no_targets(ctx: _ScaleKeysContext) -> None:
        diagnostics = ctx.diagnostics or {}
        if diagnostics.get("filtered_by_channel_box") and ctx.channel_box_attrs:
            pm.warning(
                "No keyframes matched the selected channel box attributes. "
                "Clear the channel box selection or choose keyed attributes."
            )
        elif diagnostics.get("filtered_by_ignore") and ctx.ignore:
            pm.warning(
                "All keyed attributes were filtered out by the ignore list: "
                f"{ctx.ignore}"
            )
        else:
            pm.warning(
                "No animation curves found to retime."
                if ctx.by_speed
                else "No keyframes found to scale."
            )

    @classmethod
    def _flatten_tangents(cls, ctx: _ScaleKeysContext) -> None:
        """Flatten tangents on affected curves to 'auto' to prevent overshoot.

        After scaling keyframes, tangent angles can become skewed causing overshoot
        or undershoot. This method sets tangents to 'auto' which recalculates
        them based on surrounding key positions.

        Note: Stepped curves (like visibility) are preserved to maintain their
        instant on/off behavior.
        """
        for info in ctx.object_info:
            curves = info.get("curves_to_scale", []) or info.get("all_curves", [])
            for curve in curves:
                try:
                    # Get all key times on this curve
                    key_times = pm.keyframe(curve, query=True, timeChange=True)
                    if not key_times:
                        continue

                    # Check if this is a stepped curve (like visibility)
                    # Query the out tangent type of the first key
                    out_tangents = pm.keyTangent(
                        curve, query=True, outTangentType=True, time=(key_times[0],)
                    )
                    if out_tangents and out_tangents[0] in ("step", "stepnext"):
                        # Skip stepped curves - preserve their instant transitions
                        continue

                    # Set tangents to auto for all keys
                    pm.keyTangent(
                        curve,
                        edit=True,
                        time=(min(key_times), max(key_times)),
                        inTangentType="auto",
                        outTangentType="auto",
                    )
                except Exception:
                    # Silently skip if tangent adjustment fails
                    pass

    @classmethod
    def _build_overlap_groups(cls, ctx: _ScaleKeysContext) -> List[Dict[str, Any]]:
        if not ctx.prevent_overlap:
            return []

        all_objects_data = []
        for info in ctx.object_info:
            if info.get("start") is not None and info.get("end") is not None:
                all_objects_data.append(
                    {
                        "obj": info["object"],
                        "start": info["start"],
                        "end": info["end"],
                        "duration": info["end"] - info["start"],
                        "keyframes": info.get("key_times"),
                        "curves": info.get("curves_to_scale"),
                    }
                )

        if not all_objects_data:
            return []

        overlap_groups_data = ctx.utils._group_overlapping_keyframes(all_objects_data)
        overlap_groups_data.sort(key=lambda x: x["start"])
        return overlap_groups_data

    @staticmethod
    def _report_uniform(
        ctx: _ScaleKeysContext,
        keys_scaled: int,
        mode_label_map: Dict[str, str],
        global_pivot: Optional[float],
    ) -> None:
        if keys_scaled > 0:
            selection_type = "selected" if ctx.selected_keys_only else "all"
            range_info = ""
            if ctx.range_specified:
                start_text = (
                    f"{ctx.base_start:.2f}" if ctx.base_start is not None else "auto"
                )
                end_text = f"{ctx.base_end:.2f}" if ctx.base_end is not None else "auto"
                range_info = f" (range: {start_text} -> {end_text})"

            pivot_info = ""
            if ctx.group_mode == "single_group" and global_pivot is not None:
                pivot_info = f" around frame {global_pivot:.2f}"

            scale_info = f"{ctx.factor * 100:.2f}%"
            if ctx.absolute:
                scale_info = f"to {ctx.factor:.2f} frames"

            pm.displayInfo(
                f"Scaled {keys_scaled} {selection_type} keys {scale_info} using {mode_label_map[ctx.group_mode]}{pivot_info}{range_info}."
            )
        else:
            pm.warning("No keyframes found to scale.")


class ScaleKeys(_ScaleKeysInternal):
    """Encapsulates scale_keys logic for clarity and focused testing."""

    @classmethod
    @CoreUtils.undoable
    def scale_keys(
        cls,
        *,
        objects=None,
        mode: str = "uniform",
        factor: float = 1.0,
        keys=None,
        pivot: Optional[float] = None,
        channel_box_attrs_only: bool = False,
        ignore: Optional[Union[str, List[str]]] = None,
        group_mode: str = "single_group",
        snap_mode: Optional[str] = "nearest",
        samples: Optional[int] = None,
        include_rotation: Union[bool, str] = False,
        absolute: Optional[bool] = None,
        prevent_overlap: bool = False,
        flatten_tangents: bool = True,
        split_static: bool = True,
    ) -> int:
        """Scale keyframes uniformly or via motion-aware retiming.

        Parameters:
            split_static: If True (default), animation segments separated by static
                gaps (flat keys) are treated as independent groups and scaled separately.
            flatten_tangents: If True (default), flattens all tangents to 'auto' after
                scaling to prevent overshoot/undershoot from skewed tangent angles.
        """

        from mayatk.anim_utils._anim_utils import AnimUtils

        ctx = cls._prepare_context(
            AnimUtils,
            objects=objects,
            mode=mode,
            factor=factor,
            keys=keys,
            pivot=pivot,
            channel_box_attrs_only=channel_box_attrs_only,
            ignore=ignore,
            group_mode=group_mode,
            snap_mode=snap_mode,
            samples=samples,
            include_rotation=include_rotation,
            absolute=absolute,
            prevent_overlap=prevent_overlap,
            flatten_tangents=flatten_tangents,
            split_static=split_static,
        )

        if ctx is None:
            return 0

        # Use KeyframeGrouper for segment-based collection and grouping
        ctx.segments = KeyframeGrouper.collect_segments(
            ctx.objects_list,
            ignore=ctx.ignore,
            split_static=ctx.split_static,
            selected_keys_only=ctx.selected_keys_only,
            channel_box_attrs=ctx.channel_box_attrs,
            time_range=(ctx.base_start, ctx.base_end) if ctx.range_specified else None,
        )

        if not ctx.segments:
            cls._warn_no_targets(ctx)
            return 0

        # Convert segments to object_info format for compatibility
        ctx.object_info = cls._segments_to_object_info(ctx.segments)

        # Use KeyframeGrouper for grouping (maps group_mode to segment mode)
        segment_mode_map = {
            "per_object": "per_object",
            "single_group": "single_group",
            "overlap_groups": "overlap_groups",
        }
        segment_mode = segment_mode_map.get(ctx.group_mode, "per_segment")

        # When split_static is True and group_mode is per_object, use per_segment
        # so each segment is scaled independently
        if ctx.split_static and ctx.group_mode == "per_object":
            segment_mode = "per_segment"

        groups = KeyframeGrouper.group_segments(ctx.segments, mode=segment_mode)

        # Convert KeyframeGrouper groups to processing format
        processing_groups = cls._convert_groups_for_processing(groups)

        overlap_groups_data = cls._build_overlap_groups(ctx)

        if ctx.by_speed:
            keys_scaled = cls._process_speed(
                ctx, processing_groups, overlap_groups_data
            )
        else:
            keys_scaled = cls._process_uniform(
                ctx, processing_groups, overlap_groups_data
            )

        # After scaling, stagger segments only when prevent_overlap is enabled
        # This fixes overlaps caused by scaling but doesn't rearrange already-separate segments
        if ctx.split_static and ctx.prevent_overlap and keys_scaled > 0:
            cls._stagger_scaled_segments(ctx, groups)

        # Flatten tangents after scaling to prevent overshoot from skewed angles
        if ctx.flatten_tangents and keys_scaled > 0:
            cls._flatten_tangents(ctx)

        return keys_scaled

    @staticmethod
    def _segments_to_object_info(
        segments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Convert KeyframeGrouper segments to object_info format for compatibility."""
        object_info = []
        for seg in segments:
            # Filter keyframes to only those within the segment range
            segment_keys = [
                k for k in seg.get("keyframes", []) if seg["start"] <= k <= seg["end"]
            ]
            object_info.append(
                {
                    "object": seg["obj"],
                    "all_curves": seg.get("curves", []),
                    "curves_to_scale": seg.get("curves", []),
                    "key_times": segment_keys,
                    "key_times_full": seg.get("keyframes", []),
                    "start": seg["start"],
                    "end": seg["end"],
                    "start_full": (
                        seg.get("keyframes", [seg["start"]])[0]
                        if seg.get("keyframes")
                        else seg["start"]
                    ),
                    "end_full": (
                        seg.get("keyframes", [seg["end"]])[-1]
                        if seg.get("keyframes")
                        else seg["end"]
                    ),
                    "segment_range": seg.get(
                        "segment_range", (seg["start"], seg["end"])
                    ),
                }
            )
        return object_info

    @staticmethod
    def _convert_groups_for_processing(
        groups: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """Convert KeyframeGrouper groups to the List[List[Dict]] format expected by processing."""
        processing_groups = []
        for group in groups:
            # Each group becomes a list of object_info-like dicts from sub_groups
            group_infos = []
            for seg in group.get("sub_groups", []):
                # Filter keyframes to segment range
                segment_keys = [
                    k
                    for k in seg.get("keyframes", [])
                    if seg["start"] <= k <= seg["end"]
                ]
                group_infos.append(
                    {
                        "object": seg["obj"],
                        "all_curves": seg.get("curves", []),
                        "curves_to_scale": seg.get("curves", []),
                        "key_times": segment_keys,
                        "key_times_full": seg.get("keyframes", []),
                        "start": seg["start"],
                        "end": seg["end"],
                        "segment_range": seg.get(
                            "segment_range", (seg["start"], seg["end"])
                        ),
                    }
                )
            if group_infos:
                processing_groups.append(group_infos)
        return processing_groups
