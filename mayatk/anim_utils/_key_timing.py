# !/usr/bin/python
# coding=utf-8
"""Key retiming behind :class:`mayatk.AnimUtils`.

Moving keys to a frame, adjusting their spacing, aligning, snapping to whole
frames and inverting them -- plus the tangent-preserving per-curve key move the
shot engine and the key-timing siblings build on. Reached through
:class:`mayatk.AnimUtils`; nothing here is called directly.
"""

from typing import Any, Dict, List, Optional, Set, Tuple, Union
import bisect
import contextlib
import math

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

import pythontk as ptk

from mayatk.anim_utils._tangents import _NEIGHBOUR_DERIVED_TANGENTS


class _KeyTimingInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    #: GUI Maya's preference that rounds every ``scaleKey`` result to a whole
    #: frame -- on by default; mayapy has none.
    _SCALE_SNAP_PREF = "scaleKeyAutoSnap"

    @classmethod
    @contextlib.contextmanager
    def exact_scale(cls):
        """``scaleKey`` lands every key where it computes it, inside the block.

        GUI Maya's ``scaleKeyAutoSnap`` preference rounds each scaled key to
        a whole frame, where mayapy, with no such preference, does not.  A
        retime that computes where its keys land -- a shot's, a gap's, with
        the shot ledger's claims following those frames -- was then
        off by the rounding.  It merged keys that rounded alike, and it
        stacked a gap compressed into one frame onto that frame's bound
        (measured 2026-10-12: a key scaled to 40.7 landed on 41).  The
        preference is switched off for the block and put back after.
        """
        name = cls._SCALE_SNAP_PREF
        was = (
            cmds.optionVar(q=name)
            if cmds is not None and cmds.optionVar(exists=name)
            else 0
        )
        if was:
            cmds.optionVar(intValue=(name, 0))
        try:
            yield
        finally:
            if was:
                cmds.optionVar(intValue=(name, was))

    @classmethod
    def _compute_motion_progress(
        cls,
        obj: str,
        time_range: Tuple[float, float],
        samples: Optional[int] = None,
        include_rotation: Union[bool, str] = False,
    ) -> Tuple[List[float], List[float], float]:
        """Sample an object's motion and return normalized progress values."""

        if obj is None or not cmds.objExists(obj):
            return [], [], 0.0

        if not time_range or len(time_range) != 2:
            return [], [], 0.0

        start, end = time_range
        if start is None or end is None or end <= start:
            return [], [], 0.0

        try:
            sample_count = int(samples) if samples is not None else 64
        except (TypeError, ValueError):
            sample_count = 64

        sample_count = max(3, sample_count)

        span = end - start
        if math.isclose(span, 0.0):
            return [], [], 0.0

        sample_times = [
            float(start + (span * index) / (sample_count - 1))
            for index in range(sample_count)
        ]

        current_time = cmds.currentTime(query=True)
        positions: List[Tuple[float, float, float]] = []
        rotations: List[Tuple[float, float, float]] = []

        try:
            for time_value in sample_times:
                cmds.currentTime(time_value, edit=True)

                # Sample position
                position = cmds.xform(
                    obj, query=True, worldSpace=True, translation=True
                )
                if not position or len(position) < 3:
                    return [], [], 0.0
                positions.append(
                    (float(position[0]), float(position[1]), float(position[2]))
                )

                # Sample rotation if needed
                if include_rotation:
                    rotation = cmds.xform(
                        obj, query=True, worldSpace=True, rotation=True
                    )
                    if rotation and len(rotation) >= 3:
                        rotations.append(
                            (float(rotation[0]), float(rotation[1]), float(rotation[2]))
                        )
                    else:
                        rotations.append((0.0, 0.0, 0.0))

        except Exception:
            return [], [], 0.0
        finally:
            cmds.currentTime(current_time, edit=True)

        if len(positions) < 2:
            return [], [], 0.0

        cumulative: List[float] = [0.0]
        total_distance = 0.0

        for index in range(1, len(positions)):
            # Translation distance
            dist_trans = 0.0
            if include_rotation != "only":
                dist_trans = ptk.MathUtils.distance_between_points(
                    positions[index - 1], positions[index]
                )

            dist_rot = 0.0

            # Rotation distance (arc length approximation)
            if include_rotation and index < len(rotations):
                r1_vals = rotations[index - 1]
                r2_vals = rotations[index]

                # Simplified rotation distance: Euclidean distance of Euler angles
                # Treats 1 degree of rotation as equivalent to 1 unit of translation
                d_rx = abs(r2_vals[0] - r1_vals[0])
                d_ry = abs(r2_vals[1] - r1_vals[1])
                d_rz = abs(r2_vals[2] - r1_vals[2])
                dist_rot = math.sqrt(d_rx * d_rx + d_ry * d_ry + d_rz * d_rz)

            # Use the maximum of translation or rotation distance
            step_distance = max(dist_trans, dist_rot)

            total_distance += step_distance
            cumulative.append(total_distance)

        if total_distance <= 1e-8:
            progress = [0.0 for _ in cumulative]
        else:
            progress = [value / total_distance for value in cumulative]

        return sample_times, progress, total_distance

    @classmethod
    def _move_keys_to_frame(
        cls,
        objects,
        frame,
        time_range,
        selected_keys_only,
        retain_spacing,
        channel_box_attrs_only,
        align,
        on_replace=None,
        on_move=None,
    ):
        """Body of :meth:`AnimUtils.move_keys_to_frame`."""
        # Get target frame (use current time if not specified)
        if frame is None:
            frame = cmds.currentTime(query=True)

        # Get objects to work with
        if objects is None:
            objects = cmds.ls(selection=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return False

        objects = cmds.ls(objects)  # Ensure we have Maya nodes

        # Get channel box attributes if filtering is requested
        channel_box_attrs = None
        if channel_box_attrs_only:
            channel_box_attrs = cls._get_channel_box_attrs()
            if not channel_box_attrs:
                cmds.warning("No attributes selected in channel box.")
                return False

        # If working with selected keys, validate there are selected keys
        if selected_keys_only:
            all_active_key_times = cmds.keyframe(query=True, sl=True, tc=True)
            if not all_active_key_times:
                cmds.warning("No keyframes selected.")
                return False

        range_kw = {"time": time_range} if time_range else {}

        def _query_times(target: Union[str, List[str]], selected: bool) -> List[float]:
            """Key times on *target*, optionally selected-only / range-limited."""
            if not target:
                return []
            sel_kw = {"sl": True} if selected else {}
            return (
                cmds.keyframe(target, query=True, tc=True, **sel_kw, **range_kw) or []
            )

        def _time_curves(target: str, selected: bool = False) -> List[str]:
            """Time-driven animCurves on *target* — unitless (set-driven-key)
            curves are excluded so their driver values are never time-shifted."""
            sel_kw = {"sl": True} if selected else {}
            names = cmds.keyframe(target, query=True, name=True, **sel_kw) or []
            return cls._filter_time_curves(names)

        # Resolve "auto" align by scanning all relevant key times
        if align == "auto":
            all_times = []
            for obj in objects:
                if selected_keys_only:
                    for _node in _time_curves(obj, selected=True):
                        all_times.extend(_query_times(_node, selected=True))
                else:
                    all_times.extend(_query_times(_time_curves(obj), selected=False))
            if all_times:
                midpoint = (min(all_times) + max(all_times)) / 2.0
                align = "end" if midpoint < frame else "start"
            else:
                align = "start"

        # Pick the anchor function based on align mode
        align_end = align == "end"
        _anchor = max if align_end else min
        # Comparison used for the retain_spacing scan across objects
        _is_better = (
            (lambda new, best: new > best)
            if align_end
            else (lambda new, best: new < best)
        )

        keys_moved = 0

        # If maintaining spacing, calculate the global offset from the anchor key across all objects
        global_offset = None
        if retain_spacing:
            anchor_key_time = None

            # Find the anchor key across all objects
            for obj in objects:
                if selected_keys_only:
                    keys = _time_curves(obj, selected=True)
                    for node in keys:
                        active_key_times = _query_times(node, selected=True)
                        if active_key_times:
                            obj_anchor = _anchor(active_key_times)
                            if anchor_key_time is None or _is_better(
                                obj_anchor, anchor_key_time
                            ):
                                anchor_key_time = obj_anchor
                else:
                    keys = _query_times(_time_curves(obj), selected=False)
                    if keys:
                        obj_anchor = _anchor(keys)
                        if anchor_key_time is None or _is_better(
                            obj_anchor, anchor_key_time
                        ):
                            anchor_key_time = obj_anchor

            if anchor_key_time is not None:
                global_offset = frame - anchor_key_time

        for obj in objects:
            # Get keyframes based on selection preference and time range
            if selected_keys_only:
                # Use AnimUtils pattern for selected keys
                keys = _time_curves(obj, selected=True)

                # Filter by channel box attributes if requested.  The
                # Channel Box reports SHORT names ('tx') while plugs carry
                # long names — match via the normalized name set.
                if channel_box_attrs_only:
                    cb_set = {a.lower() for a in channel_box_attrs}
                    filtered_keys = []
                    for node in keys:
                        connections = cmds.listConnections(
                            node, plugs=True, destination=True, source=False
                        )
                        if connections and any(
                            not cls._plug_attr_names(p).isdisjoint(cb_set)
                            for p in connections
                        ):
                            filtered_keys.append(node)
                    keys = filtered_keys

                for node in keys:
                    active_key_times = _query_times(node, selected=True)
                    if active_key_times:
                        # Calculate offset - use global offset if maintaining spacing
                        if retain_spacing and global_offset is not None:
                            offset = global_offset
                        else:
                            anchor_time = _anchor(active_key_times)
                            offset = frame - anchor_time

                        # Move exactly the selected keys — a (min, max) range
                        # edit would also drag unselected keys inside the span
                        # -- replacing an unselected key they land on.
                        keys_moved += cls._shift_replacing(
                            node, active_key_times, offset, on_replace, on_move
                        )
            else:
                # Handle all keys in time range
                if channel_box_attrs_only:
                    # Move keys only for channel box attributes
                    for attr in channel_box_attrs:
                        if not cmds.attributeQuery(attr, node=str(obj), exists=True):
                            continue

                        attr_name = f"{obj}.{attr}"
                        curves = _time_curves(attr_name)
                        keys = _query_times(curves, selected=False)
                        if keys:
                            # Calculate offset - use global offset if maintaining spacing
                            if retain_spacing and global_offset is not None:
                                offset = global_offset
                            else:
                                anchor_time = _anchor(keys)
                                offset = frame - anchor_time

                            # Replacing a key outside the range it lands on.
                            cls._shift_curves(
                                curves, offset, time_range, on_replace, on_move
                            )
                            keys_moved += len(keys)
                else:
                    # Move all keys on object
                    curves = _time_curves(obj)
                    keys = _query_times(curves, selected=False)
                    if keys:
                        # Calculate offset - use global offset if maintaining spacing
                        if retain_spacing and global_offset is not None:
                            offset = global_offset
                        else:
                            anchor_time = _anchor(keys)
                            offset = frame - anchor_time

                        # Replacing a key outside the range it lands on.
                        cls._shift_curves(
                            curves, offset, time_range, on_replace, on_move
                        )
                        keys_moved += len(keys)

        if keys_moved > 0:
            selection_type = "selected" if selected_keys_only else "all"
            range_info = f" in range {time_range}" if time_range else ""
            spacing_info = " (maintaining relative spacing)" if retain_spacing else ""
            print(
                f"Moved {keys_moved} {selection_type} keys to frame {frame}{range_info}{spacing_info}"
            )
            return True
        else:
            cmds.warning("No keyframes found to move.")
            return False

    @classmethod
    def _adjust_key_spacing(
        cls,
        objects,
        spacing,
        time,
        relative,
        preserve_keys,
        selected_keys_only,
        exact_gap,
        prevent_collisions,
        on_replace=None,
        on_move=None,
    ):
        """Body of :meth:`AnimUtils.adjust_key_spacing`."""
        if spacing == 0:
            return

        if exact_gap and spacing < 0:
            cmds.warning("exact_gap mode requires a positive spacing value.")
            return

        current_time = cmds.currentTime(query=True)

        # Auto-detect: use current playhead time
        if time is None:
            adjusted_time = current_time
        else:
            adjusted_time = time + current_time if relative else time

        # Get animation curves — selected_keys_only overrides objects
        if selected_keys_only:
            anim_curves = cls.get_anim_curves(
                objects=None, selected_keys_only=True, recursive=False
            )
        else:
            anim_curves = cls.get_anim_curves(
                objects=objects, selected_keys_only=False, recursive=False
            )
        # Unitless (set-driven-key) curves are excluded — their "times" are
        # driver values, and spacing shifts would corrupt the driven mapping.
        anim_curves = cls._filter_time_curves(anim_curves)
        if not anim_curves:
            if selected_keys_only:
                cmds.warning("No keyframes selected.")
            else:
                cmds.warning("No animation found.")
            return

        # In exact_gap mode, find the earliest key after adjusted_time to compute offset
        actual_spacing = spacing
        if exact_gap:
            gap_end = adjusted_time + spacing
            earliest_key = None
            for curve in anim_curves:
                if selected_keys_only:
                    kf = cmds.keyframe(curve, query=True, sl=True, timeChange=True)
                else:
                    kf = cmds.keyframe(curve, query=True, timeChange=True)
                if kf:
                    after = [k for k in kf if k > adjusted_time + 0.001]
                    if after:
                        curve_min = min(after)
                        if earliest_key is None or curve_min < earliest_key:
                            earliest_key = curve_min
            if earliest_key is None:
                cmds.warning(f"No keyframes found after frame {adjusted_time}.")
                return
            # Keep the offset fractional — truncating to int breaks the
            # "first key lands exactly at start + spacing" guarantee for
            # sub-frame key times.
            actual_spacing = gap_end - earliest_key
            if actual_spacing <= 1e-9:
                # Gap already clear
                return

        # If preserve_keys, save keyframes at adjusted_time before moving them
        preserved_keys = []  # [(attr_name, value, tangent_info)]
        tolerance = 0.0001

        # ---------- Collision pre-flight check ----------
        # Build per-curve move plans so we can detect collisions before
        # touching any keys.
        curve_plans = []  # [(curve, keys_to_move, keyframes)]
        for curve in anim_curves:
            if selected_keys_only:
                keyframes = cmds.keyframe(curve, query=True, sl=True, timeChange=True)
            else:
                keyframes = cmds.keyframe(curve, query=True, timeChange=True)
            if not keyframes:
                continue

            keys_to_move = sorted(
                [k for k in keyframes if k >= adjusted_time - tolerance],
                reverse=(actual_spacing > 0),
            )
            curve_plans.append((curve, keys_to_move, keyframes))

        if prevent_collisions:
            for curve, keys_to_move, keyframes in curve_plans:
                if not keys_to_move:
                    continue
                moving_set = set(keys_to_move)
                # Keys on this curve that are NOT being moved. Query the FULL
                # curve, not the (possibly sl=True-filtered) plan query: with
                # selected_keys_only, unselected keys are invisible to
                # `keyframes`, and option="over" below would silently
                # overwrite one if a moved key landed on it (the same blind
                # spot fixed in snap_keys_to_frames' occupied set).
                all_times = cmds.keyframe(curve, query=True, timeChange=True) or []
                stationary = {k for k in all_times if k not in moving_set}
                dests: List[float] = []
                for key in keys_to_move:
                    dest = max(key + actual_spacing, 0)
                    if dest == key:
                        # Unmoved (clamped in place) — still occupies its
                        # time; a later key clamping onto it must collide.
                        dests.append(dest)
                        continue
                    # Collision with a stationary key?
                    if any(abs(dest - s) < tolerance for s in stationary):
                        cmds.warning(
                            f"Cannot move keys: frame {int(key)} would collide "
                            f"with an existing key at frame {int(dest)}. "
                            f"Reduce the spacing amount or move the blocking key first."
                        )
                        return
                    # Collision between two MOVED keys — only possible when
                    # the clamp-at-0 flattens distinct sources onto the same
                    # destination (large negative spacing).
                    if any(abs(dest - d) < tolerance for d in dests):
                        cmds.warning(
                            f"Cannot move keys: multiple keys on {curve} would "
                            f"merge at frame {int(dest)} (clamped at 0). "
                            f"Reduce the negative spacing amount."
                        )
                        return
                    dests.append(dest)

        # ---------- Execute moves ----------
        for curve, keys_to_move, keyframes in curve_plans:
            # Get the attribute name connected to this curve for preserve_keys
            anchored = False
            if preserve_keys and any(
                abs(k - adjusted_time) < tolerance for k in keyframes
            ):
                connections = cmds.listConnections(
                    curve, plugs=True, source=False, destination=True
                )
                if connections:
                    attr_name = str(connections[0])
                    value = cmds.getAttr(attr_name, time=adjusted_time)
                    tangent_info = cls.get_tangent_info(attr_name, adjusted_time)
                    preserved_keys.append((attr_name, value, tangent_info))
                    anchored = True

            if not keys_to_move:
                continue

            # A key a moved one lands on is replaced, cut BEFORE the move:
            # option="over" stacks the mover a hair beside it (two keys on one
            # frame).  prevent_collisions refused every such landing above.
            pairs = [(key, max(key + actual_spacing, 0)) for key in keys_to_move]
            pairs = [(key, dest) for key, dest in pairs if dest != key]
            replaced = cls._landed_on(curve, pairs, moving=True)
            for t in replaced:
                cmds.cutKey(curve, time=(t, t), clear=True)

            # Batch move: group into contiguous ranges where possible
            for key in keys_to_move:
                new_time = max(key + actual_spacing, 0)
                if new_time != key:
                    try:
                        # option="over" lets a key travel past unmoved
                        # neighbors — the default move CLAMPS at the adjacent
                        # key, leaving it off-frame (see _shift_key_times and
                        # snap_keys_to_frames for the same trap). The
                        # prevent_collisions pre-flight already aborts on a
                        # destination that lands ON a stationary key.
                        cmds.keyframe(
                            curve,
                            edit=True,
                            time=(key,),
                            timeChange=new_time,
                            option="over",
                        )
                    except RuntimeError as e:
                        cmds.warning(
                            f"adjust_key_spacing: failed to move key at {key} "
                            f"on {curve}: {e}"
                        )
            if anchored:
                # The preserved key stays at the adjustment point (it is keyed
                # back there below), so its claims stay with it; a moved key
                # landing on that point is overwritten by the re-key, so its
                # claims go -- from its source frame, where they still are.
                replaced = replaced + [
                    key for key, dest in pairs if abs(dest - adjusted_time) < tolerance
                ]
                pairs = [
                    (key, dest)
                    for key, dest in pairs
                    if abs(key - adjusted_time) >= tolerance
                    and abs(dest - adjusted_time) >= tolerance
                ]
            # Replaced first, so a claim carried onto a frame is not the one
            # released there.
            if replaced and on_replace is not None:
                on_replace(curve, replaced)
            if pairs and on_move is not None:
                on_move(curve, pairs)

        # Restore preserved keyframes at the original time
        for attr_name, value, tangent_info in preserved_keys:
            cmds.setKeyframe(attr_name, time=(adjusted_time,), value=value)
            cls.set_tangent_info(attr_name, adjusted_time, tangent_info)

    @classmethod
    def _invert_keys(
        cls,
        objects,
        time,
        relative,
        delete_original,
        mode,
        value_pivot,
        on_replace,
        on_move,
    ):
        """Body of :meth:`AnimUtils.invert_keys`."""
        if objects is None:
            objects = cmds.ls(selection=True)
        else:
            objects = [str(o) for o in ptk.make_iterable(objects)]
        if not objects:
            raise RuntimeError("No objects selected.")

        selected_key_times = cmds.keyframe(query=True, sl=True, tc=True) or []
        use_selected = bool(selected_key_times)

        # Grouped per curve because tangent mirroring is order-dependent:
        # stepped segments have to migrate to the neighbouring key.
        times_by_curve: Dict[str, Set[float]] = {}

        for obj in objects:
            key_nodes = (
                cmds.keyframe(obj, query=True, name=True, selected=True) or []
                if use_selected
                else cmds.keyframe(obj, query=True, name=True) or []
            )

            for node in key_nodes:
                times = (
                    cmds.keyframe(node, query=True, selected=True, timeChange=True)
                    if use_selected
                    else cmds.keyframe(node, query=True, timeChange=True)
                )
                if not times:
                    continue

                times_by_curve.setdefault(str(node), set()).update(
                    float(t) for t in times
                )

        all_key_times: List[float] = [t for ts in times_by_curve.values() for t in ts]

        if not all_key_times:
            raise RuntimeError("No keyframes selected or found to invert.")

        max_time = max(all_key_times)
        min_time = min(all_key_times)

        if time is None:
            # In-place mirror: t' = min + (max - t) reverses the keys within
            # their own range; forcing delete_original turns the insert-then-
            # cut below into a move.
            inversion_point = min_time
            delete_original = True
        else:
            inversion_point = max_time + time if relative else time

        flip_time = mode in ("horizontal", "both")
        flip_value = mode in ("vertical", "both")

        # Snapshot every tangent BEFORE touching the curves: an in-place mirror
        # writes new keys over the originals it is still reading from.
        keyframe_data: List[Tuple[str, float, float, float, Dict[str, Any]]] = []
        for node, curve_times in times_by_curve.items():
            ordered = sorted(curve_times)
            # Neighbours are taken within the inverted set: with a partial
            # graph-editor selection the unselected keys stay put, so the
            # selection is the only run the mirror can be defined over.
            tangents = cls._mirror_tangent_data(
                [cls.get_tangent_info(node, t) for t in ordered],
                flip_time,
                flip_value,
            )

            for key_time, tangent_data in zip(ordered, tangents):
                key_value = cmds.keyframe(
                    node, query=True, time=(key_time,), eval=True
                )[0]
                inverted_time = (
                    inversion_point - (key_time - max_time) if flip_time else key_time
                )
                inverted_value = (
                    value_pivot - (key_value - value_pivot) if flip_value else key_value
                )
                keyframe_data.append(
                    (node, key_time, inverted_time, inverted_value, tangent_data)
                )

        # (source, landing) frames per curve, as the hooks report them.
        moves_by_curve: Dict[str, List[Tuple[float, float]]] = {}
        for node, key_time, inverted_time, _, _ in keyframe_data:
            moves_by_curve.setdefault(node, []).append((key_time, inverted_time))

        # setKeyframe below overwrites a key already on its frame: read which
        # ones BEFORE the writes. delete_original moves every source on to its
        # own landing (an in-place mirror forced it above), so none of those
        # is replaced.
        replaced: Dict[str, List[float]] = {}
        if on_replace is not None:
            for node, moves in moves_by_curve.items():
                frames = cls._landed_on(node, moves, moving=delete_original)
                if frames:
                    replaced[node] = frames

        for node, _, inverted_time, inverted_value, tangent_data in keyframe_data:
            cmds.setKeyframe(node, time=inverted_time, value=inverted_value)
            cls.set_tangent_info(node, inverted_time, tangent_data)

        if delete_original:
            inverted_positions = {
                (node, round(inverted_time, 3))
                for node, _, inverted_time, _, _ in keyframe_data
            }

            for node, key_time, _, _, _ in keyframe_data:
                rounded_time = round(key_time, 3)
                if (node, rounded_time) not in inverted_positions:
                    # clear: a bare cutKey put the sources on the animation
                    # clipboard, over whatever keys the user had copied.
                    cmds.cutKey(node, time=(key_time, key_time), clear=True)

        # Replaced first: a claim moved onto a frame would otherwise go with
        # the key released there.  Only a move moves -- a copy that keeps its
        # sources leaves every key where it was.
        for node, frames in replaced.items():
            on_replace(node, frames)
        if on_move is not None and delete_original:
            for node, moves in moves_by_curve.items():
                pairs = [(src, dst) for src, dst in moves if abs(dst - src) >= 1e-9]
                if pairs:
                    on_move(node, pairs)

    @classmethod
    def move_curve_keys(
        cls,
        curve: str,
        time_pairs: List[Tuple[float, ...]],
        tolerance: float = 1e-4,
        allow_merge: bool = False,
        on_replace=None,
        on_move=None,
    ) -> int:
        """Move keys on a curve to new times, preserving value and tangents.

        Uses a read-cut-set approach to avoid keyframe collisions during retiming.

        Parameters:
            curve: The animation curve to modify.
            time_pairs: List of ``(old_time, new_time)`` tuples, or ``(old_time,
                new_time, ideal_time)`` -- *ideal_time* the frame the key was
                meant for before rounding (a snapped scale: its exact scaled
                time); default *new_time*.
            tolerance: Tolerance for floating point comparisons.
            allow_merge: How keys MOVED here that land on one frame resolve.
                True: one is kept, the LEAST MOVED -- nearest its ideal time,
                an exact tie going to the later source (blendertk's
                ``AnimUtils._merge_landed``) -- and the others are replaced.
                False (default): each is nudged past the others, so none is
                lost.  Either way a key this call does not move is REPLACED
                by a mover landing on it.
            on_replace: ``on_replace(curve, frames)`` hears the keys replaced:
                an unmoved key, at its frame, or a moved key that lost a
                merge, at its SOURCE frame -- where whatever is held on it
                still is -- and that key is then no move.
            on_move: ``on_move(curve, pairs)`` hears the ``(old, new)`` frames
                of the keys moved, ``new`` being where each landed (a nudged
                key's nudged frame). Called after ``on_replace``.
        """

        if not time_pairs:
            return 0

        # 1. Collect data for all keys that need moving
        keys_to_move = []
        ideal_of = {}  # source time -> the frame it was meant for
        for pair in time_pairs:
            old_time, new_time = pair[0], pair[1]
            if abs(new_time - old_time) <= tolerance:
                continue
            ideal_of[old_time] = float(pair[2]) if len(pair) > 2 else float(new_time)

            # Maya's keyframe query is most reliable when the time argument is a
            # (start, end) pair, even when targeting a single key time.  The
            # epsilon must stay well below one frame — a wide window (e.g. 0.5)
            # can capture a NEIGHBORING key on sub-frame animation and move
            # the wrong key's value.
            eps = max(tolerance, 1e-3)
            values = cmds.keyframe(
                curve,
                query=True,
                time=(old_time - eps, old_time + eps),
                valueChange=True,
            )
            if not values:
                continue

            tangent_data = cls.get_tangent_info(curve, old_time) or None
            keys_to_move.append((old_time, new_time, values[0], tangent_data))

        if not keys_to_move:
            return 0

        # Add a temporary key to prevent the curve from being deleted if we cut all keys
        # Use a very large time value that is unlikely to conflict with actual animation
        temp_time = 1000000.0
        cmds.setKeyframe(curve, time=temp_time, value=0)

        # 2. Cut all old keys to clear the way (prevents overwriting/collisions)
        # Process in reverse order to maintain index stability if needed, though time-based cut is robust
        for old_time, _, _, _ in sorted(keys_to_move, key=lambda x: x[0], reverse=True):
            try:
                # clear: a bare cutKey put the moved keys on the animation
                # clipboard, over whatever keys the user had copied.
                cmds.cutKey(curve, time=(old_time, old_time), option="keys", clear=True)
            except RuntimeError as error:
                cmds.warning(f"Failed to cut key on {curve} at {old_time}: {error}")

        # 3. Set keys at new positions
        moved_count = 0
        # Collect remaining key times (keys not being moved) to avoid overwriting them.
        try:
            remaining_times = cmds.keyframe(curve, query=True, timeChange=True) or []
        except RuntimeError as error:
            # Without this list collision avoidance is blind — warn so a
            # silent overwrite of unmoved keys is at least diagnosable.
            cmds.warning(
                f"move_curve_keys: could not query remaining keys on "
                f"{curve} ({error}); collision avoidance is degraded."
            )
            remaining_times = []

        # Use an epsilon that's small but larger than tolerance so we can escape collisions.
        epsilon = max(1e-3, tolerance * 10.0)

        def _is_time_taken(candidate: float, taken: List[float]) -> bool:
            for t in taken:
                if abs(candidate - t) <= tolerance:
                    return True
            return False

        taken_times: List[float] = list(remaining_times)
        replaced: List[float] = []  # what a merge overwrote (see on_replace)
        landed: List[Tuple[float, float]] = []  # (source, frame) of each mover

        for old_time, new_time, value, tangent_data in keys_to_move:
            try:
                candidate_time = float(new_time)

                if not allow_merge:
                    # Nudge past the frames other MOVED keys took, by a tiny
                    # epsilon until free: no mover is lost, and a segment
                    # never collapses to 0 duration.  An unmoved key is
                    # replaced below -- nudging beside it left two keys on
                    # one frame.
                    safety = 0
                    while (
                        _is_time_taken(candidate_time, [at for _s, at in landed])
                        and safety < 1000
                    ):
                        candidate_time += epsilon
                        safety += 1
                hit = next(
                    (t for t in taken_times if abs(candidate_time - t) <= tolerance),
                    None,
                )
                if hit is not None:
                    prior = next(
                        (
                            i
                            for i, (_src, at) in enumerate(landed)
                            if abs(at - hit) <= tolerance
                        ),
                        None,
                    )
                    if prior is not None:
                        # Two movers on one frame: the LEAST moved stays --
                        # nearest its ideal time, an exact tie to the later
                        # source (blendertk's ``_merge_landed`` rule).
                        src = landed[prior][0]
                        stays = (abs(hit - ideal_of[src]), -src)
                        comes = (abs(candidate_time - ideal_of[old_time]), -old_time)
                        if stays <= comes:
                            replaced.append(old_time)  # its key was cut above
                            moved_count += 1
                            continue
                    # The key there is overwritten -- cut first: setKeyframe
                    # replaces only an exact time, a hair off it stacks.
                    cmds.cutKey(curve, time=(hit, hit), clear=True)
                    taken_times.remove(hit)
                    replaced.append(hit if prior is None else landed.pop(prior)[0])

                cmds.setKeyframe(curve, time=candidate_time, value=value)
                if tangent_data:
                    try:
                        # Angles first, types LAST (the public pair's order).
                        # Types first and angles second forced every moved key
                        # to ``fixed`` -- an angle write implies it -- and so
                        # fossilised the animator's adaptive tangents a little
                        # more on every retime.
                        cls.set_tangent_info(curve, candidate_time, tangent_data)
                    except RuntimeError:
                        pass  # locked or referenced curve -- the move still stands
                taken_times.append(candidate_time)
                landed.append((old_time, candidate_time))
                moved_count += 1
            except RuntimeError as error:
                cmds.warning(f"Failed to set key on {curve} at {new_time}: {error}")

        # Remove the temporary key
        try:
            cmds.cutKey(curve, time=(temp_time, temp_time), option="keys", clear=True)
        except RuntimeError as error:
            cmds.warning(
                f"move_curve_keys: failed to remove the temporary key at "
                f"{temp_time} on {curve}: {error}"
            )

        # Replaced first, so a claim carried onto a frame is not the one
        # released there.
        if replaced and on_replace is not None:
            on_replace(curve, replaced)
        if landed and on_move is not None:
            on_move(curve, landed)
        return moved_count

    @staticmethod
    def _shift_key_times(curve: str, times: List[float], offset: float) -> int:
        """Shift exactly the given key times on a curve by a relative offset.

        Unlike a (min, max) range edit, only the listed keys move — keys
        lying between them are untouched.  Keys are processed in an order
        that prevents a moved key from colliding with a later source key.

        Parameters:
            curve (str): The animation curve (or plug) to edit.
            times (List[float]): Key times to move.
            offset (float): Relative frame offset to apply.

        Returns:
            int: Number of keys moved.
        """
        if not times or abs(offset) < 1e-9:
            return 0

        moved = 0
        for t in sorted(set(times), reverse=offset > 0):
            try:
                # option="over" lets a key travel past unmoved neighbors —
                # Maya's default move clamps at the adjacent key.
                cmds.keyframe(
                    curve,
                    edit=True,
                    time=(t, t),
                    relative=True,
                    timeChange=offset,
                    option="over",
                )
                moved += 1
            except RuntimeError as e:
                cmds.warning(f"Failed to move key at {t} on {curve}: {e}")
        return moved

    @staticmethod
    def _landed_on(
        curve: str, moves: List[Tuple[float, float]], moving: bool, eps: float = 1e-4
    ) -> List[float]:
        """The keys of *curve* that ``(source, destination)`` *moves* land on
        and so REPLACE, read as the curve stands before the edit.

        Not one the edit carries on itself: with *moving* every source goes on
        to its own destination, and a key its own copy lands on is re-keyed.
        The frames returned are what an ``on_replace`` callback is handed.
        """
        dests = sorted((d, s) for s, d in moves)
        times = [d for d, _s in dests]
        sources = {s for s, _d in moves}
        hit = []
        for t in cmds.keyframe(curve, query=True, timeChange=True) or []:
            i = bisect.bisect_left(times, t - eps)
            if i == len(times) or times[i] - t > eps:
                continue  # nothing lands here
            if abs(dests[i][1] - t) <= eps or (moving and t in sources):
                continue
            hit.append(t)
        return hit

    @classmethod
    def _shift_replacing(
        cls, curve: str, times, offset: float, on_replace=None, on_move=None
    ) -> int:
        """:meth:`_shift_key_times`, REPLACING the keys the move lands on, and
        report both.

        A key the moved ones land on is cut first: ``keyframe -option over``
        does not overwrite it, it stacks the mover a hair beside it (two keys
        on one frame).  ``on_replace(curve, frames)`` then hears the frames cut
        and ``on_move(curve, pairs)`` the ``(old, new)`` frames of the keys
        moved -- replaced first, so a claim carried onto a frame is not the one
        released there.  Returns the number of keys moved.
        """
        times = sorted(set(times))
        if not times or abs(offset) < 1e-9:
            return 0
        pairs = [(t, t + offset) for t in times]
        replaced = cls._landed_on(curve, pairs, moving=True)
        for t in replaced:
            cmds.cutKey(curve, time=(t, t), clear=True)
        moved = cls._shift_key_times(curve, times, offset)
        if replaced and on_replace is not None:
            on_replace(curve, replaced)
        if on_move is not None:
            on_move(curve, pairs)
        return moved

    @classmethod
    def _shift_curves(
        cls, curves, offset: float, time_range=None, on_replace=None, on_move=None
    ) -> None:
        """Shift the keys of *curves* -- those in *time_range*, or every one --
        by *offset* in ONE edit, and report it as :meth:`_shift_replacing` does.

        A range move leaves the keys outside it behind, and one the moved keys
        land on is cut first (``option="over"`` would stack the mover beside
        it); a whole-curve move leaves nothing behind to land on.
        """
        if not curves or abs(offset) < 1e-9:
            return
        range_kw = {"time": time_range} if time_range else {}
        plan = []
        for crv in curves:
            times = cmds.keyframe(crv, query=True, timeChange=True, **range_kw) or []
            if not times:
                continue
            pairs = [(t, t + offset) for t in times]
            replaced = cls._landed_on(crv, pairs, moving=True) if time_range else []
            for t in replaced:
                cmds.cutKey(crv, time=(t, t), clear=True)
            plan.append((crv, replaced, pairs))
        if not plan:  # no key to move: no edit
            return
        if time_range:
            # option="over" lets a range move travel past unmoved out-of-range
            # neighbors -- the default move CLAMPS at the adjacent key.
            cmds.keyframe(
                curves,
                edit=True,
                time=time_range,
                relative=True,
                timeChange=offset,
                option="over",
            )
        else:
            cmds.keyframe(curves, edit=True, relative=True, timeChange=offset)
        for crv, replaced, pairs in plan:
            if replaced and on_replace is not None:
                on_replace(crv, replaced)
            if on_move is not None:
                on_move(crv, pairs)

    @classmethod
    def _shift_ranges(cls, moves, on_replace=None, on_move=None) -> None:
        """Shift several key ranges, each by its own offset, as ONE layout:
        every key lands where its own range's offset sends it, whatever order
        the ranges trade places in.

        Parameters:
            moves: ``(curves, offset, time_range)`` per range, in PLACEMENT
                order -- the order the layout lays them down. ``time_range``
                ``None`` is every key of *curves*.
            on_replace: ``on_replace(curve, frames)``, as :meth:`_shift_curves`
                reports it, once per hop.
            on_move: ``on_move(curve, pairs)``, likewise.

        One range move after another picks its keys by time from the curve as
        it stands, so when a layout reverses two ranges of one curve an
        earlier move lands on a later one's source range -- cutting the key
        there -- and the later move then carries the earlier one's keys
        again; no execution order avoids it.  On a curve two or more moves
        share, every one of them is therefore LIFTED to a park slot of its
        own above every key and every destination on that curve (a lift
        never lands in another range, and each park slot holds only its own
        keys), then LANDED in placement order: where two meet on one frame,
        the later-placed range's key replaces the earlier one's.  A curve
        only one move touches is shifted straight, as :meth:`_shift_curves`
        shifts it.  Every hop is a bulk ``keyframe -edit`` move (tangents and
        key flags travel with the keys) and is reported as it happens --
        source to park, park to destination -- so a claim follower matching
        each call against the claims as they stand carries every claim with
        its own key; one net pair per key would hand a claim another range
        just landed on a frame to the key that moves on from it.  A curve
        that refuses the edit (a locked node) is warned about and skipped.

        Each range moves RIGIDLY, the tangent halves on its boundary keys
        included (:meth:`_layout_boundaries`): a derived half (auto, spline,
        clamped...) takes its slope from neighbours the layout replaces, so
        one the layout re-derives is frozen back at its old angle (broken
        where its two halves now differ).  Where two ranges meet on one frame
        that key stands for both: in-half from the range arriving, value and
        out-half from the range leaving (maintainer 2026-10-11).
        """
        by_curve: Dict[str, List[Tuple[float, Any]]] = {}
        for curves, offset, time_range in moves:
            if abs(offset) < 1e-9:
                continue
            for crv in dict.fromkeys(str(c) for c in curves or ()):
                by_curve.setdefault(crv, []).append((float(offset), time_range))

        for crv, entries in by_curve.items():
            try:
                boundaries = cls._boundary_halves(crv, entries)
                if len(entries) == 1:
                    offset, time_range = entries[0]
                    cls._shift_curves([crv], offset, time_range, on_replace, on_move)
                else:
                    cls._lift_and_land(crv, entries, on_replace, on_move)
                cls._restore_boundary_halves(crv, boundaries)
            except RuntimeError as error:
                cmds.warning(f"Failed to move keys on {crv}: {error}")

    @staticmethod
    def _layout_boundaries(times, entries, eps: float = 1e-4) -> list:
        """The keys whose derived slopes a layout can change, and which
        original key each one's tangent halves and value belong to.

        Each key moves by the first of *entries* whose range holds it (none:
        it stays put); destinations within *eps* are ONE key, the
        latest-placed source's (:meth:`_lift_and_land`; a stationary key is
        replaced by anything landing on it).  A final key's in-half keeps the
        source whose ORIGINAL left neighbour is still its left neighbour --
        the span between them is one the layout carried whole -- and its
        out-half likewise on the right; a side facing a junction the layout
        made is left to re-derive.  The value is the out-half's source's (the
        later range's start), else the in-half's.  Listed: every merged key,
        every key with a new neighbour, and every key beside a merged one
        (whose value may now be the other side's, which re-derives both of
        its neighbour's halves).

        Parameters:
            times: The curve's key times before the layout.
            entries: ``(offset, time_range)`` per range, in placement order
                (``time_range`` ``None``: every key).
            eps: Frames within which two destinations are one key
                (:meth:`_landed_on`'s).

        Returns:
            ``[(frame, in_src, out_src, value_src), ...]`` -- *frame* the
            key's time after the layout, the rest original key times
            (``None``: no half kept on that side).
        """
        ts = sorted(times)
        dest, rank = [], []
        for t in ts:
            for i, (offset, time_range) in enumerate(entries):
                lo, hi = time_range if time_range else (-math.inf, math.inf)
                if lo <= t <= hi:
                    dest.append(t + offset)
                    rank.append(i)
                    break
            else:
                dest.append(t)
                rank.append(-1)
        frames: List[List[int]] = []
        for s in sorted(range(len(ts)), key=lambda s: dest[s]):
            if frames and dest[s] - dest[frames[-1][0]] <= eps:
                frames[-1].append(s)
            else:
                frames.append([s])

        out = []
        last = len(ts) - 1
        for k, group in enumerate(frames):
            prev = set(frames[k - 1]) if k else set()
            nxt = set(frames[k + 1]) if k + 1 < len(frames) else set()
            if len(group) == 1 and len(prev) < 2 and len(nxt) < 2:
                s = group[0]
                left_kept = s - 1 in prev if s else not prev
                right_kept = s + 1 in nxt if s < last else not nxt
                if left_kept and right_kept:
                    # Both neighbours its own and neither a merged key (whose
                    # value may be the other side's): nothing re-derives.
                    continue
            # Latest-placed first: the key that stays, then the ones it replaced.
            ranked = sorted(group, key=lambda s: rank[s], reverse=True)
            in_src = next((s for s in ranked if s - 1 in prev), None)
            out_src = next((s for s in ranked if s + 1 in nxt), None)
            if in_src is None and out_src is None:
                continue  # an island: no span of its own to keep
            value_src = out_src if out_src is not None else in_src
            out.append(
                (
                    dest[ranked[0]],
                    None if in_src is None else ts[in_src],
                    None if out_src is None else ts[out_src],
                    ts[value_src],
                )
            )
        return out

    @classmethod
    def _boundary_halves(cls, crv: str, entries) -> list:
        """:meth:`_layout_boundaries` for *crv*, read off the curve before the
        layout moves it: ``[(frame, value, in_half, out_half), ...]``, each
        half ``(type, angle, weight)`` or ``None``."""
        times = cmds.keyframe(crv, query=True, timeChange=True) or []
        return [
            (
                frame,
                cmds.keyframe(crv, query=True, time=(v_src,) * 2, valueChange=True)[0],
                cls.tangent_half(crv, in_src, "in"),
                cls.tangent_half(crv, out_src, "out"),
            )
            for frame, in_src, out_src, v_src in cls._layout_boundaries(times, entries)
        ]

    @classmethod
    def _restore_boundary_halves(cls, crv: str, boundaries, tol: float = 1e-3):
        """Give each key :meth:`_boundary_halves` read its value back, then
        every half the layout made play differently.  Values first: a value
        re-derives its neighbours' derived halves, never a fixed one."""
        if not boundaries:
            return
        weighted = bool(
            (cmds.keyTangent(crv, query=True, weightedTangents=True) or [False])[0]
        )
        keys = []
        for frame, value, in_half, out_half in boundaries:
            hit = cmds.keyframe(
                crv, query=True, time=(frame - 1e-3, frame + 1e-3), timeChange=True
            )
            if not hit:
                continue
            t = hit[0]
            now = cmds.keyframe(crv, query=True, time=(t, t), valueChange=True)[0]
            if abs(now - value) > 1e-9:
                cmds.keyframe(
                    crv, edit=True, time=(t, t), absolute=True, valueChange=value
                )
            keys.append((t, in_half, out_half))

        for t, *halves in keys:
            edits = {}
            for side, want in zip(("in", "out"), halves):
                if want is None:
                    continue
                kind, angle, weight = want
                now = cls.tangent_half(crv, t, side)
                if now is None:
                    continue
                now_kind, now_angle, now_weight = now
                drift = abs(now_angle - angle) > tol or (
                    weighted and abs(now_weight - weight) > tol
                )
                if kind in _NEIGHBOUR_DERIVED_TANGENTS:
                    if not drift:
                        continue  # re-derived to the slope it had: leave its type
                    edits[f"{side}TangentType"] = "fixed"
                else:  # its own slope: the type alone brings it back
                    if now_kind == kind and not (kind == "fixed" and drift):
                        continue
                    edits[f"{side}TangentType"] = kind
                    if kind != "fixed":
                        continue
                edits[f"{side}Angle"] = angle
                if weighted:
                    edits[f"{side}Weight"] = weight
            if not edits:
                continue
            # Broken only where its halves now differ: a key kept whole on
            # both sides stays unified.
            if edits.get("inAngle") is None or edits.get("inAngle") != edits.get(
                "outAngle"
            ):
                edits["lock"] = False
            cls._set_tangent_info(crv, t, edits)

    @classmethod
    def _lift_and_land(cls, crv: str, entries, on_replace=None, on_move=None):
        """:meth:`_shift_ranges` on a curve several ``(offset, time_range)``
        *entries* (in placement order) share: lift each to a park slot, then
        land each in order."""
        times = cmds.keyframe(crv, query=True, timeChange=True) or []
        plans = []  # (offset, range, first key, last key) of each holding keys
        for offset, time_range in entries:
            lo, hi = time_range if time_range else (-math.inf, math.inf)
            held = [t for t in times if lo <= t <= hi]
            if held:
                plans.append((offset, time_range, min(held), max(held)))
        if len(plans) < 2:  # only one of them holds keys here: nothing to trade
            for offset, time_range, _first, _last in plans:
                cls._shift_curves([crv], offset, time_range, on_replace, on_move)
            return

        pad = 1e-6  # the keys' own times: no neighbour lies this close
        gap = 1.0
        top = max([max(times)] + [last + offset for offset, _r, _f, last in plans])
        free = top + gap
        parked = []
        for offset, _range, first, last in plans:
            # A whole-frame lift: the round trip adds no float noise to a
            # whole-frame key.
            lift = float(math.ceil(free - first))
            cls._shift_curves(
                [crv], lift, (first - pad, last + pad), on_replace, on_move
            )
            parked.append((offset - lift, first + lift, last + lift))
            free = last + lift + gap
        for land, first, last in parked:
            cls._shift_curves(
                [crv], land, (first - pad, last + pad), on_replace, on_move
            )

    @classmethod
    def _align_selected_keyframes(
        cls, objects, target_frame, use_earliest, on_replace, on_move
    ):
        """Body of :meth:`AnimUtils.align_selected_keyframes`."""
        # Get objects to work with
        if objects is None:
            objects = cmds.ls(selection=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return False

        # Ensure we have transform nodes, not shape nodes
        objects = cmds.ls(objects, type="transform", flatten=True)

        if not objects:
            cmds.warning("No valid transform nodes found.")
            return False

        # Collect selected keyframe data for each object
        obj_keyframe_data = []

        for obj in objects:
            # Get animation curve nodes with selected keyframes
            curve_nodes = cmds.keyframe(obj, query=True, name=True, selected=True)

            if not curve_nodes:
                continue

            # Get the selected keyframe times
            obj_selected_times = cls.get_keyframe_times(
                curve_nodes, mode="selected", from_curves=True
            )

            if obj_selected_times:
                obj_keyframe_data.append(
                    {
                        "obj": obj,
                        "curve_nodes": curve_nodes,
                        "times": obj_selected_times,
                        "start": obj_selected_times[0],
                        "end": obj_selected_times[-1],
                    }
                )

        if not obj_keyframe_data:
            cmds.warning("No selected keyframes found on any objects.")
            return False

        # Determine the alignment target frame
        if target_frame is None:
            if use_earliest:
                target_frame = min(data["start"] for data in obj_keyframe_data)
            else:
                target_frame = max(data["start"] for data in obj_keyframe_data)

        # Align each object's selected keyframes
        for data in obj_keyframe_data:
            curve_nodes = data["curve_nodes"]
            current_start = data["start"]

            # Calculate the shift amount
            shift_amount = target_frame - current_start

            if abs(shift_amount) < 1e-6:  # Skip if already aligned (within tolerance)
                continue

            # Move exactly the selected keys per curve — an object-level
            # (min, max) range edit would also drag unselected keys lying
            # inside the span and touch curves without any selection.
            for node in curve_nodes:
                sel_times = cmds.keyframe(
                    node, query=True, selected=True, timeChange=True
                )
                if sel_times:
                    # A key the block lands on is replaced, not stacked beside.
                    cls._shift_replacing(
                        node, sel_times, shift_amount, on_replace, on_move
                    )

        print(
            f"Aligned selected keyframes for {len(obj_keyframe_data)} object(s) to frame {target_frame:.2f}"
        )
        return True

    @classmethod
    def _repair_fractional_keys(cls, objects, method, recursive, on_move=None):
        """Body of :meth:`AnimUtils.repair_fractional_keys`."""
        curves = cls.get_anim_curves(objects=objects, recursive=recursive)
        # Resolved to curves, not passed as objects: only get_anim_curves walks
        # descendants, and its None is the scene (snap_keys_to_frames' is the
        # selection). Driven-key curves hold driver values, not frames.
        curves = (cmds.ls(curves, type=list(cls.TIME_CURVE_TYPES)) or []) if curves else []
        if not curves:
            return {"curves": 0, "snapped": 0, "off_frame": 0}
        snapped = cls.snap_keys_to_frames(curves, method=method, on_move=on_move)
        times = cls.get_keyframe_times(curves) or []
        return {
            "curves": len(curves),
            "snapped": snapped or 0,
            "off_frame": sum(1 for t in times if t != int(t)),
        }

    @classmethod
    def _snap_keys_to_frames(
        cls,
        objects,
        method,
        selected_only,
        time_range,
        include_driven,
        through_blends,
        on_move=None,
    ):
        """Body of :meth:`AnimUtils.snap_keys_to_frames`."""
        # Get objects to work with
        if objects is None:
            objects = cmds.ls(selection=True)
        else:
            if isinstance(objects, str):
                objects = [objects]
            elif isinstance(objects, (list, tuple, set)):
                objects = [str(o) for o in objects]
            else:
                objects = [str(objects)]

        if not objects:
            cmds.warning("No objects specified or selected.")
            return 0

        objects = cmds.ls(objects, flatten=True)

        # Optimization: Batch query all curves
        if selected_only:
            all_curves = (
                cmds.keyframe(objects, query=True, name=True, selected=True) or []
            )
        else:
            # objects_to_curves, not a bare listConnections: it runs the same
            # batched query AND keeps any of *objects* that is ALREADY a curve.
            # Handed curves (a caller that resolved its own scope through
            # get_anim_curves), the raw query found nothing to snap — a curve
            # has no incoming animCurve — and the pass reported 0 silently.
            all_curves = cls.objects_to_curves(objects, through_blends=through_blends)

        # Unitless (set-driven-key) curves are excluded by default — their
        # "times" are driver values, and snapping those rewrites the rig's
        # driven-key mapping.  include_driven=True opts back in deliberately.
        all_curves = cls._filter_time_curves(list(set(all_curves)), include_driven)

        keys_snapped = 0

        for curve in all_curves:
            # Query keyframe times
            if selected_only:
                kw = {"selected": True}
            else:
                kw = {}
            if time_range:
                kw["time"] = time_range

            keyframe_times = cmds.keyframe(curve, query=True, timeChange=True, **kw)
            if not keyframe_times:
                continue

            # Collect fractional keys for this curve and snap them in one pass
            # Process in reverse time order to avoid time-shift collisions
            moves = []
            for t in keyframe_times:
                if t != int(t):
                    new_time = ptk.MathUtils.round_value(t, mode=method)
                    moves.append((t, new_time))

            if not moves:
                continue

            # Move keys in reverse order (highest time first) to avoid collisions
            moves.sort(key=lambda x: x[0], reverse=True)

            # Build a set of occupied times for collision detection.
            # Must include ALL whole-frame keys on the curve (selected AND
            # unselected, and outside any time_range) so a snapped key never
            # overwrites an unselected whole-frame key via option="over".
            # keyframe_times is the selected/time-range-filtered query and is
            # blind to those keys.
            all_curve_times = cmds.keyframe(curve, query=True, timeChange=True) or []
            occupied = set(t for t in all_curve_times if t == int(t))

            applied = []
            for old_time, new_time in moves:
                # Skip if another key already occupies the target time
                # (either an original whole-frame key or a previously
                # snapped key).  Moving would overwrite/merge and lose
                # the existing key's tangent data.
                if new_time in occupied:
                    continue

                try:
                    # Use keyframe -edit -timeChange to move in-place,
                    # preserving values and tangent data without
                    # delete+recreate overhead (~1 cmd vs ~8 cmds per key).
                    # option="over" lets the key travel past unmoved
                    # fractional neighbors — the default move CLAMPS at the
                    # adjacent key, leaving the key off-frame (see
                    # _shift_key_times for the same trap).
                    cmds.keyframe(
                        curve,
                        edit=True,
                        time=(old_time, old_time),
                        timeChange=new_time,
                        option="over",
                    )
                    occupied.add(new_time)
                    applied.append((old_time, new_time))
                    keys_snapped += 1
                except RuntimeError as e:
                    cmds.warning(
                        f"Failed to snap keyframe on {curve} at time {old_time}: {e}"
                    )
            # Never a replacement (an occupied target is skipped); the keys that
            # did move carry what is held on them.
            if applied and on_move is not None:
                on_move(curve, applied)

        if keys_snapped > 0:
            print(
                f"Snapped {keys_snapped} keyframe(s) to whole frames using '{method}' method"
            )
        else:
            print("No keyframes with decimal values found to snap")

        return keys_snapped
