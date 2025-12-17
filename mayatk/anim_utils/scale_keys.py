# coding=utf-8
"""Dedicated scale-keys module to keep AnimUtils lean and testable."""
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya environment required
    print(__file__, error)

import pythontk as ptk

# Import CoreUtils using internal path to avoid circular imports
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils.segment_keys import SegmentKeys


class ScaleKeys:
    """Encapsulates scale_keys logic for clarity and focused testing."""

    def __init__(
        self,
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
        ignore_holds: bool = False,
        merge_touching: bool = False,
        verbose: bool = False,
        verbose_header: str = None,
    ):
        from mayatk.anim_utils._anim_utils import AnimUtils

        self.utils = AnimUtils
        self.merge_touching = merge_touching
        self.ignore_holds = ignore_holds

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
            self._normalize_keys_to_time_range_and_selection(keys)
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
            # We'll handle validation failure by setting objects to empty list
            # and checking in execute
            objects = []
        else:
            objects = pm.ls(objects, flatten=True)

        channel_box_attrs = None
        if channel_box_attrs_only:
            channel_box_attrs = pm.channelBox(
                "mainChannelBox", query=True, selectedMainAttributes=True
            )

        if selected_keys_only and not by_speed:
            all_selected_keys = pm.keyframe(query=True, sl=True, tc=True)
            if not all_selected_keys:
                # Will handle in execute
                pass

        group_mode_normalized = self._normalize_group_mode(group_mode)
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

        # Initialize state
        self.objects = objects
        self.mode = mode
        self.factor = factor
        self.keys = keys
        self.pivot = pivot
        self.channel_box_attrs_only = channel_box_attrs_only
        self.ignore = ignore
        self.group_mode = group_mode_normalized
        self.snap_mode = snap_mode
        self.samples = samples
        self.include_rotation = include_rotation
        self.absolute = absolute
        self.prevent_overlap = prevent_overlap
        self.flatten_tangents = flatten_tangents
        self.split_static = split_static
        self.by_speed = by_speed
        self.verbose = verbose
        self.verbose_header = verbose_header
        self.time_range = time_range
        self.selected_keys_only = selected_keys_only
        self.base_start = base_start
        self.base_end = base_end
        self.range_specified = range_specified
        self.channel_box_attrs = channel_box_attrs
        self.objects_list = objects
        self.object_info: List[Dict[str, Any]] = []
        self.segments: List[Dict[str, Any]] = []
        self.diagnostics: Dict[str, Any] = {}

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

    @staticmethod
    def _execute_scale_operation(
        curves: List[Any],
        pivot: float,
        factor: float,
        time_range: Optional[Tuple[float, float]] = None,
    ) -> int:
        """Execute a scale operation directly."""
        keys_scaled = 0
        for curve in curves:
            if not pm.objExists(curve):
                continue

            kwargs = {
                "timeScale": factor,
                "timePivot": pivot,
            }
            if time_range:
                kwargs["time"] = time_range
                # Verify keys exist in range before scaling to avoid warnings/errors
                if not pm.keyframe(curve, query=True, time=time_range):
                    continue
                keys_scaled += pm.keyframe(
                    curve, query=True, keyframeCount=True, time=time_range
                )
            else:
                keys_scaled += pm.keyframe(curve, query=True, keyframeCount=True)

            pm.scaleKey(curve, **kwargs)
        return keys_scaled

    def _execute_move_operation(
        self,
        curve: Any,
        time_pairs: List[Tuple[float, float]],
        allow_merge: bool = False,
    ) -> int:
        """Execute a move operation directly."""
        if not pm.objExists(curve):
            return 0
        return self.utils._move_curve_keys(curve, time_pairs, allow_merge=allow_merge)

    @staticmethod
    def _execute_shift_operation(
        curves: List[Any],
        offset: float,
        time_range: Optional[Tuple[float, float]] = None,
    ) -> int:
        """Execute a shift operation directly."""
        keys_scaled = 0
        for curve in curves:
            if not pm.objExists(curve):
                continue

            kwargs = {
                "edit": True,
                "relative": True,
                "timeChange": offset,
            }
            if time_range:
                kwargs["time"] = time_range
                if not pm.keyframe(curve, query=True, time=time_range):
                    continue
                keys_scaled += pm.keyframe(
                    curve, query=True, keyframeCount=True, time=time_range
                )
            else:
                keys_scaled += pm.keyframe(curve, query=True, keyframeCount=True)

            pm.keyframe(curve, **kwargs)
        return keys_scaled

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

    def _execute_speed_scale(
        self,
        groups: List[List[Dict[str, Any]]],
        overlap_groups_data: List[Dict[str, Any]],
    ) -> int:
        """Execute speed-based scaling."""
        keys_scaled = 0
        processed_objects = 0
        min_target_start: Optional[float] = None
        max_target_end: Optional[float] = None

        try:
            factor_val = float(self.factor)
        except (TypeError, ValueError):
            pm.warning("Factor must be a numeric value in speed mode.")
            return 0

        if factor_val <= 0.0:
            pm.warning("Factor must be greater than 0 in speed mode.")
            return 0

        for group in groups:
            group_range = (
                None
                if self.group_mode == "per_object"
                else self._resolve_group_bounds(group, self.base_start, self.base_end)
            )
            if self.group_mode != "per_object":
                if not group_range or group_range[1] <= group_range[0]:
                    continue

            group_calculations = []
            for info in group:
                object_range = self._resolve_range_for_object(
                    info, group_range, self.group_mode, self.base_start, self.base_end
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
                    self.utils._compute_motion_progress(
                        info["object"],
                        object_range,
                        samples=self.samples,
                        include_rotation=self.include_rotation,
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

                if self.absolute:
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

            if self.absolute:
                max_ratio = 0.0
                for item in group_calculations:
                    if item["original_duration"] > 1e-6:
                        ratio = item["target_duration"] / item["original_duration"]
                        if ratio > max_ratio:
                            max_ratio = ratio
                scale_factor = max_ratio
            else:
                scale_factor = 1.0 / float(self.factor)

            if self.group_mode == "per_object":
                for item in group_calculations:
                    if item["original_duration"] > 1e-6:
                        obj_factor = item["target_duration"] / item["original_duration"]

                        # Temporarily override absolute for this call
                        original_absolute = self.absolute
                        self.absolute = False

                        try:
                            # Execute directly for this object
                            keys_scaled += self._execute_uniform_scale_for_group(
                                [[item["info"]]],
                                factor=obj_factor,
                                pivot=item["object_range"][0],
                                group_mode="per_object",
                                keys=item["object_range"],
                            )
                        finally:
                            self.absolute = original_absolute

                        processed_objects += 1
            else:
                all_curves = []
                for item in group_calculations:
                    all_curves.extend(item["info"].get("curves_to_scale", []))

                if all_curves:
                    group_objects = [
                        item["info"]["object"] for item in group_calculations
                    ]
                    # Reconstruct the group info list
                    reconstructed_group = [item["info"] for item in group_calculations]

                    # Temporarily override absolute for this call
                    original_absolute = self.absolute
                    self.absolute = False

                    try:
                        keys_scaled += self._execute_uniform_scale_for_group(
                            [reconstructed_group],
                            factor=scale_factor,
                            pivot=pivot,
                            group_mode="single_group",
                            keys=group_range,
                        )
                    finally:
                        self.absolute = original_absolute

                    processed_objects += len(group_objects)

        self._execute_overlap_prevention(overlap_groups_data)

        return keys_scaled

    def _execute_uniform_scale(
        self,
        groups: List[List[Dict[str, Any]]],
        overlap_groups_data: List[Dict[str, Any]],
    ) -> int:
        """Execute uniform scaling."""
        return self._execute_uniform_scale_for_group(
            groups,
            factor=self.factor,
            pivot=self.pivot,
            group_mode=self.group_mode,
            keys=None,  # Will be derived from context/groups
            overlap_groups_data=overlap_groups_data,
        )

    def _resolve_pivot_value(
        self, pivot: Union[float, str, None], start: float, end: float
    ) -> float:
        """Resolve pivot value from float, string ('center', 'start', 'end'), or None."""
        if pivot is None:
            return start
        if isinstance(pivot, (int, float)) and not isinstance(pivot, bool):
            return float(pivot)
        if isinstance(pivot, str):
            p = pivot.strip().lower()
            if p == "center":
                return start + (end - start) * 0.5
            if p == "end":
                return end
            if p == "start":
                return start
            try:
                return float(pivot)
            except ValueError:
                pass
        return start

    def _execute_uniform_scale_for_group(
        self,
        groups: List[List[Dict[str, Any]]],
        factor: float,
        pivot: Union[float, str, None],
        group_mode: str,
        keys: Optional[Tuple[float, float]] = None,
        overlap_groups_data: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Execute uniform scaling for a group."""
        if factor <= 0:
            pm.warning("Scale factor must be greater than 0.")
            return 0

        is_identity_scale = not self.absolute and abs(factor - 1.0) < 1e-6
        should_snap = self.snap_mode and self.snap_mode != "none"
        if is_identity_scale and not should_snap:
            # Even if identity, we might need overlap prevention
            if overlap_groups_data:
                self._execute_overlap_prevention(overlap_groups_data)
            return 0

        keys_scaled = 0
        global_pivot: Optional[float] = None
        aggregated_moves: Dict[Any, List[Tuple[float, float]]] = {}

        for group in groups:
            group_range = (
                None
                if group_mode == "per_object"
                else self._resolve_group_bounds(group, self.base_start, self.base_end)
            )

            # Override group range if keys provided (for speed mode calls)
            if keys and group_mode != "per_object":
                group_range = keys

            if group_mode != "per_object":
                if not group_range or group_range[1] < group_range[0]:
                    continue

                group_pivot = self._resolve_pivot_value(
                    pivot, group_range[0], group_range[1]
                )

                if global_pivot is None:
                    global_pivot = group_pivot
            else:
                group_pivot = None

            for info in group:
                curves_to_scale = info.get("curves_to_scale", [])
                if not curves_to_scale:
                    continue

                object_range = self._resolve_range_for_object(
                    info, group_range, group_mode, self.base_start, self.base_end
                )

                # Override object range if keys provided (for speed mode calls)
                if keys and group_mode == "per_object":
                    object_range = keys

                # Determine pivot time based on mode
                if group_mode != "per_object":
                    pivot_time = group_pivot
                else:
                    # Per object pivot resolution
                    calc_range = info.get("segment_range")
                    if not calc_range:
                        calc_range = object_range

                    if calc_range:
                        pivot_time = self._resolve_pivot_value(
                            pivot, calc_range[0], calc_range[1]
                        )
                    else:
                        pivot_time = None

                if pivot_time is None:
                    continue
                pivot_time = float(pivot_time)

                effective_factor = factor
                if self.absolute:
                    current_duration = 0.0
                    if group_mode == "per_object":
                        segment_range = info.get("segment_range")
                        if segment_range:
                            current_duration = segment_range[1] - segment_range[0]
                        elif object_range:
                            current_duration = object_range[1] - object_range[0]
                    else:
                        if group_range:
                            current_duration = group_range[1] - group_range[0]

                    if current_duration > 1e-6:
                        effective_factor = factor / current_duration
                        print(
                            f"DEBUG: Obj {info['object']} GroupMode {group_mode} GrpDur {current_duration} Factor {factor} -> EffFactor {effective_factor} Pivot {pivot_time}"
                        )
                    else:
                        continue

                # Determine time range for key selection
                if self.split_static:
                    segment_range = info.get("segment_range")
                    time_arg = segment_range if segment_range else object_range
                elif self.range_specified and object_range:
                    time_arg = object_range
                else:
                    time_arg = None

                # Override time_arg if keys provided (for speed mode calls)
                if keys:
                    time_arg = keys

                # Maya time-range queries can miss boundary keys due to floating point
                # representation (eg. 517.5000001 vs 517.5). Expand slightly to ensure
                # endpoints are included.
                query_time_arg = None
                if time_arg:
                    try:
                        eps = 1e-3
                        query_time_arg = (
                            float(time_arg[0]) - eps,
                            float(time_arg[1]) + eps,
                        )
                    except Exception:
                        query_time_arg = time_arg

                if self.selected_keys_only or (self.snap_mode is not None):
                    curve_times_map = {}
                    for curve in curves_to_scale:
                        kwargs = {"query": True, "tc": True}
                        if self.selected_keys_only:
                            kwargs["selected"] = True
                        if query_time_arg:
                            kwargs["time"] = query_time_arg

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

                        if self.snap_mode and self.snap_mode != "none":
                            # Apply snapping to new times
                            snapped_pairs = []
                            for old, new in time_pairs:
                                # Handle alias for aggressive
                                mode = self.snap_mode.lower()
                                if mode == "aggressive":
                                    mode = "aggressive_preferred"

                                snapped_new = ptk.MathUtils.round_value(new, mode=mode)
                                snapped_pairs.append((old, snapped_new))
                            time_pairs = snapped_pairs

                        if curve not in aggregated_moves:
                            aggregated_moves[curve] = []
                        aggregated_moves[curve].extend(time_pairs)
                else:
                    # Bulk scale
                    keys_scaled += self._execute_scale_operation(
                        curves_to_scale,
                        pivot_time,
                        effective_factor,
                        query_time_arg,
                    )

        # Execute aggregated moves
        allow_merge = bool(self.snap_mode and self.snap_mode != "none")
        for curve, pairs in aggregated_moves.items():
            keys_scaled += self._execute_move_operation(
                curve, pairs, allow_merge=allow_merge
            )

        if overlap_groups_data:
            self._execute_overlap_prevention(overlap_groups_data)

        return keys_scaled

    def _execute_overlap_prevention(
        self, overlap_groups_data: List[Dict[str, Any]]
    ) -> None:
        """Execute overlap prevention directly."""
        if self.prevent_overlap and overlap_groups_data:
            for data in overlap_groups_data:
                times = self.utils.get_keyframe_times(
                    data.get("curves", []), from_curves=True, as_range=True
                )
                if times:
                    data["start"], data["end"] = times
                    data["duration"] = times[1] - times[0]

            overlap_groups_data.sort(key=lambda x: x["start"])
            start_frame = overlap_groups_data[0]["start"]

            SegmentKeys.execute_stagger(
                overlap_groups_data,
                start_frame=start_frame,
                spacing=0,
                use_intervals=False,
                avoid_overlap=False,
                preserve_gaps=True,
            )

    def _stagger_scaled_segments(
        self,
        groups: List[Dict[str, Any]],
        scale_factor: float = 1.0,
    ) -> None:
        """Stagger scaled segments to prevent overlap.

        After scaling, segments may overlap each other. This method repositions
        them sequentially so that each segment starts after the previous one ends,
        preserving the original spacing between groups (scaled by scale_factor).

        Parameters:
            groups: List of KeyframeGrouper groups (each with sub_groups containing segments).
            scale_factor: The factor by which to scale the gaps.
        """
        if not groups:
            return

        # Sort groups by original start time to ensure correct gap calculation
        groups.sort(key=lambda x: x["start"])

        # Re-collect segments to get accurate current ranges
        # This is safer than guessing
        current_segments = SegmentKeys.collect_segments(
            self.objects_list,
            ignore=self.ignore,
            split_static=self.split_static,
            selected_keys_only=self.selected_keys_only,
            channel_box_attrs=self.channel_box_attrs,
            time_range=None,  # We want all segments now
            ignore_visibility_holds=self.split_static,
            ignore_holds=self.ignore_holds,
        )

        # Organize current segments by object for mapping
        # We cannot assume segment order is preserved per-object after scaling,
        # especially when objects have multiple segments. We'll match segments
        # by nearest expected scaled start (and roughly expected duration).
        segments_by_obj = {}
        for seg in current_segments:
            obj = seg["obj"]
            if obj not in segments_by_obj:
                segments_by_obj[obj] = []
            segments_by_obj[obj].append(seg)

        for obj, segs in segments_by_obj.items():
            segs.sort(key=lambda s: s["start"])

        def _pick_best_segment(
            obj,
            expected_start: Optional[float] = None,
            expected_duration: Optional[float] = None,
        ) -> Optional[Dict[str, Any]]:
            candidates = segments_by_obj.get(obj) or []
            if not candidates:
                return None

            if expected_start is None:
                return candidates.pop(0)

            best_index = None
            best_score = None
            for i, cand in enumerate(candidates):
                cand_start = float(cand.get("start", 0.0))
                cand_end = float(cand.get("end", cand_start))
                cand_duration = cand_end - cand_start

                start_diff = abs(cand_start - expected_start)
                dur_diff = (
                    abs(cand_duration - expected_duration)
                    if expected_duration is not None
                    else 0.0
                )

                # Start alignment dominates; duration is a secondary tie-breaker.
                score = start_diff + (dur_diff * 0.25)
                if best_score is None or score < best_score:
                    best_score = score
                    best_index = i

            if best_index is None:
                return candidates.pop(0)

            return candidates.pop(best_index)

        # Build stagger data preserving original grouping
        stagger_data = []

        for group in groups:
            group_start = float("inf")
            group_end = float("-inf")
            group_curves = []

            original_group_start = float("inf")
            original_group_end = float("-inf")

            # Iterate sub_groups (which are the original segments)
            sub_groups = group.get("sub_groups", [])
            if not sub_groups:
                continue

            for old_seg in sub_groups:
                obj = old_seg["obj"]

                try:
                    original_group_start = min(
                        original_group_start, float(old_seg.get("start", 0.0))
                    )
                    original_group_end = max(
                        original_group_end, float(old_seg.get("end", 0.0))
                    )
                except Exception:
                    pass

                expected_start = None
                expected_duration = None

                # Only attempt predictive matching in uniform relative scaling.
                if not self.absolute and not self.by_speed:
                    try:
                        # IMPORTANT: when scaling used overlap-group pivots, the pivot is the
                        # overlap group's start time (not the user/global pivot).
                        if self.group_mode == "overlap_groups":
                            pivot_time = float(original_group_start)
                        else:
                            pivot_time = (
                                float(self.pivot)
                                if (
                                    self.pivot is not None
                                    and self.group_mode != "per_object"
                                )
                                else float(original_group_start)
                            )
                        seg_start = float(old_seg["start"])
                        seg_end = float(old_seg["end"])
                        seg_duration = seg_end - seg_start

                        expected_start = pivot_time + (seg_start - pivot_time) * float(
                            scale_factor
                        )
                        expected_duration = seg_duration * float(scale_factor)
                    except Exception:
                        expected_start = None
                        expected_duration = None

                new_seg = _pick_best_segment(
                    obj,
                    expected_start=expected_start,
                    expected_duration=expected_duration,
                )
                if not new_seg:
                    continue

                s = new_seg["start"]
                e = new_seg["end"]
                group_start = min(group_start, s)
                group_end = max(group_end, e)
                group_curves.extend(new_seg.get("curves", []))

            if group_start == float("inf"):
                continue

            if original_group_start == float("inf"):
                # Fallback (should not happen if sub_groups is non-empty)
                original_group_start = float(group.get("start", group_start))
            if original_group_end == float("-inf"):
                original_group_end = float(group.get("end", group_end))

            group_curves = list(dict.fromkeys(group_curves))

            stagger_data.append(
                {
                    "start": group_start,
                    "end": group_end,
                    "duration": group_end - group_start,
                    "curves": group_curves,
                    "segment_range": (group_start, group_end),
                    "original_start": original_group_start,
                    "original_end": original_group_end,
                }
            )

        if len(stagger_data) < 2:
            return  # Nothing to stagger with less than 2 groups

        if self.verbose:
            print(f"[ScaleKeys] _stagger_scaled_segments: scale_factor={scale_factor}")
            for idx, item in enumerate(stagger_data[:6]):
                print(
                    "[ScaleKeys] group[{i}] orig=({os:.2f}-{oe:.2f}) cur=({cs:.2f}-{ce:.2f}) dur={d:.2f}".format(
                        i=idx,
                        os=float(item.get("original_start", 0.0)),
                        oe=float(item.get("original_end", 0.0)),
                        cs=float(item.get("start", 0.0)),
                        ce=float(item.get("end", 0.0)),
                        d=float(item.get("duration", 0.0)),
                    )
                )

        # Apply stagger using original gaps
        operations = []

        # Start from the first group's current position
        previous_end = stagger_data[0]["end"]
        previous_original_end = stagger_data[0]["original_end"]

        for i in range(1, len(stagger_data)):
            data = stagger_data[i]

            # Calculate gap from previous PROCESSED group
            # This handles cases where groups were skipped in stagger_data construction
            gap = data["original_start"] - previous_original_end

            # Scale the gap
            scaled_gap = gap * scale_factor

            # Ensure we don't introduce overlap if prevent_overlap is the goal
            if scaled_gap < 0:
                scaled_gap = 0

            target_start = previous_end + scaled_gap
            shift_amount = target_start - data["start"]

            if self.verbose and i <= 6:
                print(
                    "[ScaleKeys] step[{i}] gap={g:.2f} scaled_gap={sg:.2f} prev_end={pe:.2f} -> target_start={ts:.2f} cur_start={cs:.2f} shift={sh:.2f}".format(
                        i=i,
                        g=float(gap),
                        sg=float(scaled_gap),
                        pe=float(previous_end),
                        ts=float(target_start),
                        cs=float(data.get("start", 0.0)),
                        sh=float(shift_amount),
                    )
                )

            if abs(shift_amount) > 1e-6:
                # Use group-level data to prevent double-transforming shared curves
                curves = list(dict.fromkeys(data.get("curves", [])))
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

            # Update previous_end for next iteration
            previous_end = target_start + data["duration"]
            previous_original_end = data["original_end"]

        # Execute operations in safe order
        pos_ops = [op for op in operations if op["shift"] > 0]
        neg_ops = [op for op in operations if op["shift"] < 0]

        # Sort by start time
        def get_start_time(op):
            t = op.get("time")
            return t[0] if t else float("-inf")

        pos_ops.sort(key=get_start_time, reverse=True)
        neg_ops.sort(key=get_start_time)

        for op in pos_ops:
            SegmentKeys.shift_curves(op["curves"], op["shift"], op["time"])

        for op in neg_ops:
            SegmentKeys.shift_curves(op["curves"], op["shift"], op["time"])

    def _report_speed(
        self,
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
            }[self.group_mode]
            range_info = ""
            if min_target_start is not None and max_target_end is not None:
                range_info = (
                    f" (target range: {min_target_start:.2f} -> {max_target_end:.2f})"
                )

            speed_info = (
                f"{float(self.factor):.3f} units/frame"
                if self.absolute
                else f"{float(self.factor):.2f}x speed"
            )
            pm.displayInfo(
                f"Retimed {keys_scaled} keyframes to {speed_info} using {mode_label} ranges (objects processed={processed_objects}){range_info}."
            )
        else:
            pm.warning(
                "No keyframes were retimed. Check the specified objects and time range."
            )

    def _warn_no_targets(self) -> None:
        diagnostics = self.diagnostics or {}
        if diagnostics.get("filtered_by_channel_box") and self.channel_box_attrs:
            pm.warning(
                "No keyframes matched the selected channel box attributes. "
                "Clear the channel box selection or choose keyed attributes."
            )
        elif diagnostics.get("filtered_by_ignore") and self.ignore:
            pm.warning(
                "All keyed attributes were filtered out by the ignore list: "
                f"{self.ignore}"
            )
        else:
            pm.warning(
                "No animation curves found to retime."
                if self.by_speed
                else "No keyframes found to scale."
            )

    def _flatten_tangents(self) -> None:
        """Flatten all tangents on affected curves to 'auto' to prevent overshoot.

        After scaling keyframes, tangent angles can become skewed causing overshoot
        or undershoot. This method sets all tangents to 'auto' which recalculates
        them based on surrounding key positions.

        Note: Visibility curves (and other stepped curves) are explicitly set to 'step'
        to preserve their stepped tangents.
        """
        for info in self.object_info:
            curves = info.get("curves_to_scale", []) or info.get("all_curves", [])
            if curves:
                self.utils._set_smart_tangents(curves, tangent_type="auto")

    def _fix_visibility_tangents(self) -> None:
        """Ensure visibility curves are set to 'step' tangents."""
        for info in self.object_info:
            curves = info.get("curves_to_scale", []) or info.get("all_curves", [])
            if curves:
                vis_curves, _ = self.utils._get_visibility_curves(curves)
                if vis_curves:
                    self.utils._set_smart_tangents(vis_curves, tangent_type="auto")

    def _build_overlap_groups(self) -> List[Dict[str, Any]]:
        if not self.prevent_overlap:
            return []

        all_objects_data = []
        for info in self.object_info:
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

        overlap_groups_data = self.utils._group_overlapping_keyframes(all_objects_data)
        overlap_groups_data.sort(key=lambda x: x["start"])
        return overlap_groups_data

    def _report_uniform(
        self,
        keys_scaled: int,
        mode_label_map: Dict[str, str],
        global_pivot: Optional[float],
    ) -> None:
        if keys_scaled > 0:
            selection_type = "selected" if self.selected_keys_only else "all"
            range_info = ""
            if self.range_specified:
                start_text = (
                    f"{self.base_start:.2f}" if self.base_start is not None else "auto"
                )
                end_text = (
                    f"{self.base_end:.2f}" if self.base_end is not None else "auto"
                )
                range_info = f" (range: {start_text} -> {end_text})"

            pivot_info = ""
            if self.group_mode == "single_group" and global_pivot is not None:
                pivot_info = f" around frame {global_pivot:.2f}"

            scale_info = f"{self.factor * 100:.2f}%"
            if self.absolute:
                scale_info = f"to {self.factor:.2f} frames"

            pm.displayInfo(
                f"Scaled {keys_scaled} {selection_type} keys {scale_info} using {mode_label_map[self.group_mode]}{pivot_info}{range_info}."
            )
        else:
            pm.warning("No keyframes found to scale.")

    @staticmethod
    def _segments_to_object_info(
        segments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Convert SegmentKeys segments to object_info format for compatibility."""
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
        """Convert SegmentKeys groups to the List[List[Dict]] format expected by processing."""
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

    def execute(self) -> int:
        if not self.objects:
            return 0

        # For single_group mode, we must include all holds to ensure uniform scaling
        # of the entire group. Discarding holds would cause them to remain in place
        # while other keys move, breaking the animation.
        ignore_holds = self.ignore_holds
        ignore_visibility_holds = self.split_static

        if self.group_mode == "single_group":
            ignore_holds = False
            ignore_visibility_holds = False

        # Use SegmentKeys for segment-based collection and grouping
        self.segments = SegmentKeys.collect_segments(
            self.objects_list,
            ignore=self.ignore,
            split_static=self.split_static,
            selected_keys_only=self.selected_keys_only,
            channel_box_attrs=self.channel_box_attrs,
            time_range=(
                (self.base_start, self.base_end) if self.range_specified else None
            ),
            ignore_visibility_holds=ignore_visibility_holds,
            ignore_holds=ignore_holds,
        )

        if not self.segments:
            self._warn_no_targets()
            return 0

        # Capture original ranges if verbose
        original_ranges = []
        if self.verbose:
            # Re-collect with detailed view for reporting
            detailed_segments = SegmentKeys.collect_segments(
                self.objects_list,
                ignore=self.ignore,
                split_static=self.split_static,
                selected_keys_only=self.selected_keys_only,
                channel_box_attrs=self.channel_box_attrs,
                time_range=(
                    (self.base_start, self.base_end) if self.range_specified else None
                ),
                ignore_visibility_holds=ignore_visibility_holds,
                ignore_holds=ignore_holds,
            )
            original_ranges = SegmentKeys.get_time_ranges(detailed_segments)

        # Convert segments to object_info format for compatibility
        self.object_info = self._segments_to_object_info(self.segments)

        # Use SegmentKeys for grouping (maps group_mode to segment mode)
        segment_mode_map = {
            "per_object": "per_object",
            "single_group": "single_group",
            "overlap_groups": "overlap_groups",
        }
        segment_mode = segment_mode_map.get(self.group_mode, "per_segment")

        # When split_static is True and group_mode is per_object, use per_segment
        # so each segment is scaled independently
        if self.split_static and self.group_mode == "per_object":
            segment_mode = "per_segment"

        # If prevent_overlap is True, we MUST use per_segment mode to allow independent staggering
        # UNLESS we are in overlap_groups mode, where we want to stagger the groups themselves.
        if (
            self.prevent_overlap
            and self.split_static
            and self.group_mode != "overlap_groups"
        ):
            segment_mode = "per_segment"

        # For scaling, we want touching segments to group together in overlap mode
        # to preserve continuity of sequential actions.
        groups = SegmentKeys.group_segments(
            self.segments, mode=segment_mode, inclusive=self.merge_touching
        )

        # Convert SegmentKeys groups to processing format
        processing_groups = self._convert_groups_for_processing(groups)

        overlap_groups_data = self._build_overlap_groups()

        # If split_static is True, we handle overlap prevention via _stagger_scaled_segments later.
        # Passing overlap_groups_data to scale methods would cause incorrect staggering based on whole-curve ranges.
        overlap_groups_data_for_scale = (
            None if self.split_static else overlap_groups_data
        )

        if self.by_speed:
            keys_scaled = self._execute_speed_scale(
                processing_groups, overlap_groups_data_for_scale
            )
        else:
            keys_scaled = self._execute_uniform_scale(
                processing_groups, overlap_groups_data_for_scale
            )

        # After scaling, stagger segments only when prevent_overlap is enabled
        if self.split_static and self.prevent_overlap and keys_scaled > 0:
            # Calculate gap scale
            gap_scale = 1.0
            if not self.absolute and not self.by_speed:
                gap_scale = self.factor

            # print(f"DEBUG: Staggering with gap_scale={gap_scale} (factor={self.factor}, absolute={self.absolute})")

            self._stagger_scaled_segments(groups, scale_factor=gap_scale)

        # Flatten tangents after scaling to prevent overshoot from skewed angles
        if keys_scaled > 0:
            if self.flatten_tangents:
                self._flatten_tangents()
            else:
                # Enforce step on visibility even if not flattening others
                self._fix_visibility_tangents()

        # Reporting
        if self.by_speed:
            self._report_speed(keys_scaled, 0, None, None)
        else:
            mode_label_map = {
                "single_group": "single-group pivot",
                "per_object": "per-object pivots",
                "overlap_groups": "overlap-group pivots",
            }

            self._report_uniform(keys_scaled, mode_label_map, None)

        if self.verbose and original_ranges:
            # Determine headers
            header_orig = "Original Time Ranges:"
            header_new = "Scale Keys: New Time Ranges:"

            if self.verbose_header:
                header_orig = f"{self.verbose_header} Original Time Ranges:"
                header_new = f"{self.verbose_header} New Time Ranges:"

            # Print original ranges
            SegmentKeys.print_time_ranges(
                original_ranges,
                header=header_orig,
                per_segment=self.split_static,
                by_time=True,
            )

            # Capture and print new ranges
            # Re-collect segments to get updated times
            new_segments = SegmentKeys.collect_segments(
                self.objects_list,
                ignore=self.ignore,
                split_static=self.split_static,
                selected_keys_only=self.selected_keys_only,
                channel_box_attrs=self.channel_box_attrs,
                time_range=(
                    (self.base_start, self.base_end) if self.range_specified else None
                ),
                ignore_visibility_holds=self.split_static,
                ignore_holds=self.ignore_holds,
            )
            new_ranges = SegmentKeys.get_time_ranges(new_segments)
            SegmentKeys.print_time_ranges(
                new_ranges,
                header=header_new,
                per_segment=self.split_static,
                by_time=True,
            )

        return keys_scaled

    @classmethod
    @CoreUtils.undoable
    def scale_keys(cls, **kwargs) -> int:
        """Scale keyframes uniformly or via motion-aware retiming.

        Parameters:
            split_static: If True (default), animation segments separated by static
                gaps (flat keys) are treated as independent groups and scaled separately.
            flatten_tangents: If True (default), flattens all tangents to 'auto' after
                scaling to prevent overshoot/undershoot from skewed tangent angles.
            merge_touching: If True, touching segments (end == start) are merged into
                a single group when using 'overlap_groups' mode. Default is False.
            ignore_holds: If True, trailing holds are ignored entirely (not processed,
                not reported). If False (default), trailing holds are absorbed into
                each segment so they are scaled/shifted with the segment.
            verbose: If True, prints detailed information including original time ranges.
            verbose_header: Optional custom text to prefix the verbose output headers.
        """
        # In overlap-group pivot mode, touching segments (end == start) must be
        # treated as a single overlap group during scaling to avoid applying
        # conflicting pivot mappings to the shared boundary key.
        #
        # Keep this opt-out: callers can explicitly pass merge_touching=False.
        try:
            group_mode = kwargs.get("group_mode")
            normalized = cls._normalize_group_mode(group_mode)
        except Exception:
            normalized = None

        if normalized == "overlap_groups" and "merge_touching" not in kwargs:
            kwargs["merge_touching"] = True

        instance = cls(**kwargs)
        return instance.execute()
