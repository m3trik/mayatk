# !/usr/bin/python
# coding=utf-8
"""Key creation and removal behind :class:`mayatk.AnimUtils`.

Baking, keying attributes, intermediate keys (add / remove), shape-preserving
key insertion, visibility keys, and deleting / selecting keys over a time
range. Reached through :class:`mayatk.AnimUtils`; nothing here is called
directly.
"""

from typing import List, Optional, Tuple
import bisect
import collections

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None


class _KeyEditInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    @staticmethod
    def hold_segments(curve: str, times: List[float]) -> List[bool]:
        """Per key segment of *curve* (``times[i]`` to ``times[i + 1]``), True
        when it plays ONE value end to end: the left key steps, or both keys
        carry the same value and the tangents facing the segment are flat.

        Four whole-curve queries, not four per segment -- this is asked once
        per curve by :meth:`AnimUtils.insert_keys`, over every bound at once.

        Parameters:
            curve: The animation curve.
            times: Its key times, in time order.

        Returns:
            One flag per segment, ``len(times) - 1`` of them.
        """
        values = cmds.keyframe(curve, q=True, valueChange=True) or []
        out_types = cmds.keyTangent(curve, q=True, outTangentType=True) or []
        out_angles = cmds.keyTangent(curve, q=True, outAngle=True) or []
        in_angles = cmds.keyTangent(curve, q=True, inAngle=True) or []
        n = len(times)
        if not (
            len(values) == len(out_types) == len(out_angles) == len(in_angles) == n
        ):
            return [False] * max(n - 1, 0)  # unreadable: assume every segment has shape
        holds = []
        for i in range(n - 1):
            if out_types[i] in ("step", "stepnext"):
                holds.append(True)
                continue
            holds.append(
                abs(values[i] - values[i + 1]) <= 1e-4
                and abs(out_angles[i]) <= 1e-4
                and abs(in_angles[i + 1]) <= 1e-4
            )
        return holds

    @classmethod
    def _bake(
        cls,
        objects,
        attributes,
        time_range,
        sample_by,
        preserve_outside_keys,
        simulation,
        destination_layer,
        remove_baked_attr_from_layer,
        bake_on_override_layer,
        minimize_rotation,
        sparse_anim_curve_bake,
        disable_implicit_control,
        control_points,
        shape,
        only_keyed,
    ):
        """Body of :meth:`AnimUtils.bake`."""
        # 1. Normalize objects
        if not objects:
            return []

        if not isinstance(objects, (list, tuple, set)):
            objects = [objects]

        valid_objects = []
        for o in objects:
            o = str(o)
            if cmds.objExists(o):
                valid_objects.append(o)

        if not valid_objects:
            return []

        # 2. Normalize attributes
        req_attrs = None
        if attributes:
            if isinstance(attributes, str):
                req_attrs = [attributes]
            else:
                req_attrs = attributes

        # 3. Group objects by bakeable attributes to batch efficiently
        # Map: tuple(sorted_attr_names) -> list[objects]
        groups = collections.defaultdict(list)

        for obj in valid_objects:
            attrs_to_bake = []

            # If specific attributes requested
            if req_attrs:
                for attr_name in req_attrs:
                    if not cmds.attributeQuery(attr_name, node=str(obj), exists=True):
                        continue

                    if only_keyed:
                        if not cmds.listConnections(
                            f"{obj}.{attr_name}", type="animCurve"
                        ):
                            continue

                    attrs_to_bake.append(attr_name)

                # If no requested attributes found valid, skip object
                if not attrs_to_bake:
                    continue

                key = tuple(sorted(attrs_to_bake))
                groups[key].append(obj)

            # If no attributes requested (bake all)
            else:
                if only_keyed:
                    # bakeResults without -at bakes all keyable, so keyed-only
                    # must be enforced manually: bake just the attributes that
                    # already have animation curves.
                    curves = cmds.keyframe(str(obj), query=True, name=True) or []
                    attrs_to_bake = cls._curves_to_attributes(curves, obj)
                    if not attrs_to_bake:
                        continue
                    groups[tuple(sorted(attrs_to_bake))].append(obj)
                else:
                    groups[None].append(obj)

        # 4. Execute Batches
        results = []

        # Build common kwargs
        kwargs = {
            "sampleBy": sample_by,
            "preserveOutsideKeys": preserve_outside_keys,
            "simulation": simulation,
            "minimizeRotation": minimize_rotation,
            "sparseAnimCurveBake": sparse_anim_curve_bake,
            "disableImplicitControl": disable_implicit_control,
            "controlPoints": control_points,
            "shape": shape,
        }
        if time_range:
            kwargs["time"] = time_range
        if destination_layer:
            kwargs["destinationLayer"] = destination_layer
            kwargs["removeBakedAttributeFromLayer"] = remove_baked_attr_from_layer
            kwargs["bakeOnOverrideLayer"] = bake_on_override_layer

        for attr_tuple, objs_in_group in groups.items():
            run_kwargs = kwargs.copy()
            if attr_tuple is not None:
                run_kwargs["attribute"] = list(attr_tuple)

            try:
                cmds.bakeResults(objs_in_group, **run_kwargs)
                # bakeResults returns None but does not raise on
                # success.  Record the objects as successfully baked.
                results.extend([str(o) for o in objs_in_group])
            except Exception as e:
                cmds.warning(f"AnimUtils.bake batch failed: {e}")

        return results

    @classmethod
    def _set_keys_for_attributes(
        cls, objects, target_times, refresh_channel_box, **kwargs
    ):
        """Body of :meth:`AnimUtils.set_keys_for_attributes`."""
        if target_times is None:
            target_times = [cmds.currentTime(query=True)]
        elif isinstance(target_times, (int, float)) and not isinstance(
            target_times, bool
        ):
            target_times = [target_times]

        # Auto-detect per-object mode: if first remaining kwarg value is a dict of attributes
        per_object_mode = False
        if kwargs:
            first_value = next(iter(kwargs.values()))
            # Per-object mode if the value is a dict (and likely contains attribute mappings)
            if isinstance(first_value, dict):
                per_object_mode = True

        if per_object_mode:
            # Per-object mode: Each object gets its specific attribute values
            # kwargs structure: {obj_name: {attr: value, ...}, ...}
            per_object_data = kwargs

            for obj in cmds.ls(objects, long=True):
                obj_name = str(obj)

                # Try to find matching stored data
                obj_attrs = per_object_data.get(obj_name)

                if not obj_attrs:
                    # Try short name if full path didn't match
                    short_name = str(obj).split("|")[-1]
                    obj_attrs = per_object_data.get(short_name)

                    # Try matching stored short names against current long name
                    if not obj_attrs:
                        for stored_name in per_object_data.keys():
                            if stored_name.split("|")[-1] == short_name:
                                obj_attrs = per_object_data.get(stored_name)
                                break

                if obj_attrs:
                    for attr, value in obj_attrs.items():
                        attr_full_name = f"{obj}.{attr}"
                        for time in target_times:
                            cls._set_key_preserving_tangents(
                                attr_full_name, time, value
                            )
        else:
            # Shared mode: All objects get the same attribute values
            # kwargs structure: {attr: value, attr2: value2, ...}
            for obj in cmds.ls(objects):
                for attr, value in kwargs.items():
                    attr_full_name = f"{obj}.{attr}"
                    for time in target_times:
                        cls._set_key_preserving_tangents(attr_full_name, time, value)

        if refresh_channel_box:
            mel.eval("channelBoxCommand -update;")

    @classmethod
    def _add_intermediate_keys(cls, objects, time_range, percent, include_flat, ignore):
        """Body of :meth:`AnimUtils.add_intermediate_keys`."""
        from math import isclose

        targets = cmds.ls(objects, flatten=True)
        cb_attrs = cls._get_channel_box_attrs()
        if cb_attrs:
            # The Channel Box reports SHORT names ('tx'); normalize to long
            # names so the ignore filter matches user input ('translateX').
            attrs = set()
            for obj in targets:
                for attr in cb_attrs:
                    try:
                        attrs.add(
                            cmds.attributeQuery(attr, node=str(obj), longName=True)
                        )
                    except RuntimeError:
                        continue
            attrs = list(attrs)
        else:
            attrs = set()
            for obj in targets:
                keyable = cmds.listAttr(obj, keyable=True, scalar=True) or []
                for attr in keyable:
                    plug = f"{obj}.{attr}"
                    if cmds.listConnections(plug, source=True, destination=False):
                        attrs.add(attr)
            attrs = list(attrs)

        if not attrs:
            cmds.warning("No keyable or connected attributes found.")
            return

        # Filter out ignored attributes
        attrs = cls._filter_attributes_by_ignore(attrs, ignore)
        if not attrs:
            cmds.warning("All attributes were ignored.")
            return

        # Build per-attribute key data with auto-detected or explicit ranges
        attr_key_data = {}
        for obj in targets:
            for attr in attrs:
                plug = f"{obj}.{attr}"
                if not cmds.objExists(plug):
                    continue
                if not cmds.listConnections(
                    plug, source=True, destination=False
                ) or not cmds.getAttr(plug, keyable=True):
                    continue

                # Determine range based on time_range parameter
                if time_range is None:
                    # Auto-detect full range
                    key_times = cmds.keyframe(plug, query=True, timeChange=True)
                    if not key_times or len(key_times) < 2:
                        continue
                    attr_start = int(key_times[0])
                    attr_end = int(key_times[-1])
                elif isinstance(time_range, tuple):
                    # Tuple (start, end) - either can be None for auto-detect
                    start_val, end_val = time_range
                    key_times = None

                    if start_val is None or end_val is None:
                        key_times = cmds.keyframe(plug, query=True, timeChange=True)
                        if not key_times:
                            continue

                    attr_start = int(key_times[0]) if start_val is None else start_val
                    attr_end = int(key_times[-1]) if end_val is None else end_val
                else:
                    # Single int - start from first key, end at specified frame
                    key_times = cmds.keyframe(plug, query=True, timeChange=True)
                    if not key_times:
                        continue
                    attr_start = int(key_times[0])
                    attr_end = time_range

                # Calculate frames to key (excluding bookends).
                # Coerce caller-supplied bounds to int the same way the
                # auto-detect path does — range() rejects float arguments.
                attr_start, attr_end = int(attr_start), int(attr_end)
                frames = list(range(attr_start + 1, attr_end))
                if percent is not None:
                    count = max(1, int(len(frames) * (percent / 100.0)))
                    step = max(1, len(frames) // count)
                    frames = frames[::step]

                if frames:
                    attr_key_data.setdefault((obj, attr), []).extend(frames)

        # Collect values, then set keys — both phases scrub the playhead,
        # so restore it afterward regardless of failures.
        original_time = cmds.currentTime(query=True)
        try:
            frame_values = {}
            for (obj, attr), frames in attr_key_data.items():
                plug = f"{obj}.{attr}"
                for frame in frames:
                    cmds.currentTime(frame, edit=True)
                    frame_values.setdefault(frame, {}).setdefault(obj, {})[attr] = (
                        cmds.getAttr(plug)
                    )

            for frame, obj_data in frame_values.items():
                cmds.currentTime(frame, edit=True)
                for obj, attr_values in obj_data.items():
                    for attr, value in attr_values.items():
                        plug = f"{obj}.{attr}"
                        if not include_flat:
                            try:
                                val_prev = cmds.getAttr(plug, time=frame - 1)
                                val_next = cmds.getAttr(plug, time=frame + 1)
                                if isclose(value, val_prev, abs_tol=1e-6) and isclose(
                                    value, val_next, abs_tol=1e-6
                                ):
                                    continue
                            except RuntimeError:
                                # Flatness probe failed (unreadable plug) —
                                # skip this frame rather than key blindly.
                                continue
                        cmds.setAttr(plug, value)
                        cmds.setKeyframe(plug)
        finally:
            cmds.currentTime(original_time, edit=True)

    @classmethod
    def _remove_intermediate_keys(
        cls, objects, time_range, ignore, attributes, on_delete=None
    ):
        """Body of :meth:`AnimUtils.remove_intermediate_keys`."""
        targets = cmds.ls(objects, flatten=True)
        if not targets:
            cmds.warning("No valid objects provided.")
            return 0

        # Explicit attribute scope, else the Channel Box selection (SHORT
        # names, e.g. 'tx' -- both spellings normalize below).
        if isinstance(attributes, str):
            attributes = [attributes]
        cb_attrs = list(attributes) if attributes else cls._get_channel_box_attrs()

        def _resolve_range(target: str) -> Optional[Tuple[float, float]]:
            """Resolve the (start, end) strip range for a plug or curve."""
            key_times = cmds.keyframe(target, query=True, timeChange=True)
            if time_range is None:
                if not key_times or len(key_times) < 2:
                    return None
                return (min(key_times), max(key_times))
            if isinstance(time_range, tuple):
                start_val, end_val = time_range
                if (start_val is None or end_val is None) and not key_times:
                    return None
                start = min(key_times) if start_val is None else start_val
                end = max(key_times) if end_val is None else end_val
                return (start, end)
            # Single int — start from first key, end at specified frame
            if not key_times:
                return None
            return (min(key_times), time_range)

        def _strip_intermediate(target: str) -> int:
            """Cut keys strictly between the resolved range ends of *target*."""
            rng = _resolve_range(target)
            if rng is None:
                return 0
            start, end = rng
            window = (start + 0.001, end - 0.001)
            intermediate_keys = cmds.keyframe(
                target, query=True, timeChange=True, time=window
            )
            if not intermediate_keys:
                return 0
            curve = (cmds.keyframe(target, query=True, name=True) or [target])[0]
            cmds.cutKey(target, time=window, clear=True)
            if on_delete is not None:
                on_delete(curve, list(intermediate_keys))
            return len(intermediate_keys)

        keys_removed = 0

        for obj in targets:
            if cb_attrs:
                # Narrow to the Channel Box selection.  Normalize the short
                # names to long names so the ignore filter matches user
                # input like 'visibility'.  If ignore removes the entire
                # selection, honor the user's narrowed intent and remove
                # NOTHING rather than falling through to every attribute.
                obj_attrs = []
                for attr in cb_attrs:
                    try:
                        obj_attrs.append(
                            cmds.attributeQuery(attr, node=str(obj), longName=True)
                        )
                    except RuntimeError:
                        continue
                # Curve by curve, as the unscoped branch below: a compound
                # (translate) thinned as one plug took its children's shared
                # range -- cutting a child's own first and last key -- and
                # reported every frame against its first child's curve.
                curves = []
                for attr in cls._filter_attributes_by_ignore(obj_attrs, ignore):
                    curves.extend(
                        cmds.keyframe(f"{obj}.{attr}", query=True, name=True) or []
                    )
                for curve in dict.fromkeys(curves):
                    keys_removed += _strip_intermediate(curve)
            else:
                # All keyed curves on the object, minus ignored ones
                keyed_curves = cmds.keyframe(obj, query=True, name=True)
                for curve in cls._filter_curves_by_ignore(keyed_curves, ignore):
                    keys_removed += _strip_intermediate(curve)

        if keys_removed > 0:
            print(f"Removed {keys_removed} intermediate keyframe(s).")
        else:
            print("No intermediate keyframes found to remove.")

        return keys_removed

    @classmethod
    def _set_visibility_keys(
        cls, objects, visible, when, offset, group_overlapping, on_replace=None
    ):
        """Body of :meth:`AnimUtils.set_visibility_keys`."""
        # Get objects to work with
        if objects is None:
            objects = cmds.ls(selection=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return 0

        # Ensure we have transform nodes
        objects = cmds.ls(objects, type="transform", flatten=True)

        if not objects:
            cmds.warning("No valid transform nodes found.")
            return 0

        # Collect keyframe data for each object
        obj_keyframe_data = []

        for obj in objects:
            keyframes = cls.get_keyframe_times(obj)

            if keyframes:
                obj_keyframe_data.append(
                    {
                        "obj": obj,
                        "keyframes": keyframes,
                        "start": keyframes[0],
                        "end": keyframes[-1],
                        "duration": keyframes[-1] - keyframes[0],
                    }
                )

        if not obj_keyframe_data:
            cmds.warning("No keyframes found on the provided objects.")
            return 0

        # Group overlapping objects if requested
        if group_overlapping:
            obj_keyframe_data = cls._group_overlapping_keyframes(obj_keyframe_data)

        # Determine visibility value (0 or 1)
        visibility_value = 1 if visible else 0

        # Set visibility keyframes based on 'when' parameter
        keys_created = 0

        for data in obj_keyframe_data:
            objects_in_group = data.get("objects", [data["obj"]])
            start_frame = data["start"]
            end_frame = data["end"]

            # Determine target frames based on 'when' parameter
            target_frames = []

            if when == "start":
                target_frames = [start_frame + offset]
            elif when == "end":
                target_frames = [end_frame + offset]
            elif when == "both":
                target_frames = [start_frame + offset, end_frame + offset]
            elif when == "before_start":
                target_frames = [start_frame - 1 + offset]
            elif when == "after_end":
                target_frames = [end_frame + 1 + offset]
            else:
                cmds.warning(f"Invalid 'when' parameter: {when}. Using 'start'.")
                target_frames = [start_frame + offset]

            # Set visibility keys for each object in the group -- replacing a
            # visibility key already on the frame that holds the other state.
            for obj in objects_in_group:
                crv, replaced = (
                    cls._keyed_over(
                        f"{obj}.visibility",
                        [(frame, visibility_value) for frame in target_frames],
                    )
                    if on_replace is not None
                    else (None, [])
                )
                for frame in target_frames:
                    cmds.setKeyframe(
                        obj, attribute="visibility", time=frame, value=visibility_value
                    )
                    keys_created += 1
                if replaced:
                    on_replace(crv, replaced)

        print(
            f"Created {keys_created} visibility keyframe(s) for {len(obj_keyframe_data)} object(s)/group(s)"
        )
        return keys_created

    @classmethod
    def _delete_keys(cls, objects, *attributes, time, channel_box_only, on_delete=None):
        """Body of :meth:`AnimUtils.delete_keys`."""
        if objects is None:
            objects = cmds.ls(selection=True)

        objects = cmds.ls(objects, flatten=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return

        # Handle channel box filtering
        if channel_box_only:
            cb_attrs = cls._get_channel_box_attrs()
            if not cb_attrs:
                cmds.warning("No attributes selected in channel box.")
                return
            # Override attributes with channel box selection
            attributes = cb_attrs

        # Parse time range using helper method
        time_range = cls.parse_time_range(time)

        # Handle recursive cases (pipe-separated or multi-element sequences)
        if isinstance(time_range, list):
            for t in time_range:
                cls.delete_keys(
                    objects,
                    *attributes,
                    time=t,
                    channel_box_only=False,
                    on_delete=on_delete,
                )
            return

        range_kw = {"time": time_range} if time_range else {}

        def _cut(targets):
            # The keys about to go, per curve, for on_delete -- read first: a
            # curve that loses every key is deleted with them.
            doomed = []
            if on_delete is not None:
                try:
                    names = cmds.keyframe(targets, query=True, name=True) or []
                except RuntimeError:
                    names = []
                for crv in dict.fromkeys(names):
                    times = cmds.keyframe(crv, query=True, timeChange=True, **range_kw)
                    if times:
                        doomed.append((crv, list(times)))
            cmds.cutKey(targets, clear=True, **range_kw)
            for crv, times in doomed:
                on_delete(crv, times)

        # Optimized: batch cutKey operations
        if attributes:
            # Build list of attribute plugs for all objects
            attr_plugs = [f"{obj}.{attr}" for obj in objects for attr in attributes]
            if attr_plugs:
                _cut(attr_plugs)
        else:
            # Delete keyframes for all attributes - pass all objects at once
            _cut(objects)

    @staticmethod
    def _euler_filter(objects):
        """Body of :meth:`AnimUtils.euler_filter`."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        curves = [
            c
            for c in AnimUtils.objects_to_curves(objects)
            if cmds.nodeType(c) == "animCurveTA"  # time -> angle: a rotation
        ]
        if curves:
            cmds.filterCurve(curves, filter="euler")
        return curves

    @staticmethod
    def _shift_key_values(edits):
        """Body of :meth:`AnimUtils.shift_key_values`."""
        changed = 0
        for curve, pairs in edits.items():
            for t, dv in pairs:
                if abs(dv) <= 1e-12:
                    continue
                # One key per call: ``keyframe -e`` takes a single -time range.
                changed += (
                    cmds.keyframe(
                        str(curve),
                        edit=True,
                        relative=True,
                        time=(t - 1e-3, t + 1e-3),
                        valueChange=dv,
                    )
                    or 0
                )
        return changed

    @staticmethod
    def _clear_key_selection() -> None:
        """Body of :meth:`AnimUtils.clear_key_selection`."""
        if cmds.keyframe(query=True, selected=True, name=True):
            cmds.selectKey(clear=True)

    @classmethod
    def _select_keys(
        cls, objects, *attributes, time, channel_box_only, add_to_selection
    ):
        """Body of :meth:`AnimUtils.select_keys`."""
        if objects is None:
            objects = cmds.ls(selection=True)

        objects = cmds.ls(objects, flatten=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return 0

        # Handle channel box filtering
        if channel_box_only:
            cb_attrs = cls._get_channel_box_attrs()
            if not cb_attrs:
                cmds.warning("No attributes selected in channel box.")
                return 0
            # Override attributes with channel box selection
            attributes = cb_attrs

        # Parse time range using helper method
        time_range = cls.parse_time_range(time)

        # Handle recursive cases (pipe-separated or multi-element sequences)
        if isinstance(time_range, list):
            total_selected = 0
            for i, t in enumerate(time_range):
                # Only replace selection on first iteration, add to selection afterward
                add = add_to_selection or (i > 0)
                count = cls.select_keys(
                    objects,
                    *attributes,
                    time=t,
                    channel_box_only=False,
                    add_to_selection=add,
                )
                total_selected += count
            return total_selected

        # Clear selection if not adding to it
        if not add_to_selection:
            cls.clear_key_selection()

        range_kw = {"time": time_range} if time_range else {}

        def _select_and_count(target: str) -> int:
            """Select keys on *target*, returning only the NEWLY selected count.

            selectKey(add=True) accumulates, so counting all selected keys
            after the call would re-count keys selected by earlier targets
            or a pre-existing selection.
            """
            before = (
                cmds.keyframe(target, query=True, selected=True, keyframeCount=True)
                or 0
            )
            cmds.selectKey(target, add=True, **range_kw)
            after = (
                cmds.keyframe(target, query=True, selected=True, keyframeCount=True)
                or 0
            )
            return max(0, after - before)

        keys_selected = 0

        for obj in objects:
            if attributes:  # Select keyframes for specified attributes
                for attr in attributes:
                    keys_selected += _select_and_count(f"{obj}.{attr}")
            else:  # Select keyframes for all attributes
                keys_selected += _select_and_count(obj)

        return keys_selected

    @classmethod
    def _insert_keys(cls, objects, times, tolerance, report):
        """Body of :meth:`AnimUtils.insert_keys`."""
        wanted = sorted({float(t) for t in times})
        if not wanted:
            return [] if report else 0
        curves = cls._filter_time_curves(cls.objects_to_curves(objects) or [])
        inserted = []
        for curve in curves:
            existing = cmds.keyframe(curve, query=True, timeChange=True) or []
            if len(existing) < 2:
                continue  # nothing between two keys to split
            first, last = existing[0], existing[-1]
            holds = None  # read once per curve, and only if a time needs it
            for t in wanted:
                if not first < t < last:
                    continue  # outside the keys: a hold, not a shape
                if any(abs(t - k) <= tolerance for k in existing):
                    continue
                if holds is None:
                    holds = cls.hold_segments(curve, existing)
                if holds[bisect.bisect_right(existing, t) - 1]:
                    continue  # between two keys that play one value: a hold
                try:
                    cmds.setKeyframe(curve, time=(t, t), insert=True)
                except RuntimeError:
                    continue  # locked or referenced curve — leave it as it was
                inserted.append((curve, t))
        return inserted if report else len(inserted)
