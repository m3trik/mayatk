# !/usr/bin/python
# coding=utf-8
"""Bookend (tied) keys behind :class:`mayatk.AnimUtils`.

:meth:`AnimUtils.tie_keyframes` adds flat bookend keys at the range ends and
records their times on the curve (:data:`TIED_KEYS_ATTR`), so
:meth:`AnimUtils.untie_keyframes` removes exactly those and
:meth:`AnimUtils.get_tied_keyframes` can detect them. Reached through
:class:`mayatk.AnimUtils`; nothing here is called directly.
"""

from typing import List, Optional
import json

try:
    import maya.cmds as cmds
except Exception:
    cmds = None


TIED_KEYS_ATTR = "mayatkTiedKeys"
"""String attribute added to anim curve nodes by tie_keyframes, holding a
JSON list of the bookend key times it inserted.  untie_keyframes uses this
record to remove exactly those keys instead of guessing from value equality.
"""


class _TiedKeysInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _find_adjacent_key(fn, frame, n):
        """Find the index of the first key at or after *frame*.

        Returns None if no key is found (all keys are before *frame*).
        """
        for ki in range(n):
            if fn.input(ki).value >= frame - 1e-4:
                return ki
        return None

    @staticmethod
    def _read_tied_key_metadata(curve: str) -> Optional[List[float]]:
        """Return the bookend times recorded on *curve* by tie_keyframes, or
        None if the curve carries no (or unreadable) metadata.
        """
        try:
            if not cmds.attributeQuery(TIED_KEYS_ATTR, node=curve, exists=True):
                return None
            raw = cmds.getAttr(f"{curve}.{TIED_KEYS_ATTR}")
            if not raw:
                return []
            return [float(t) for t in json.loads(raw)]
        except (RuntimeError, ValueError, TypeError):
            return None

    @staticmethod
    def _write_tied_key_metadata(curve: str, times: List[float]) -> None:
        """Merge *times* into the bookend-time record stored on *curve*.

        Silently skips curves that can't take the attribute (e.g. referenced or
        locked nodes) — untie then falls back to heuristic detection.
        """
        if not times:
            return
        try:
            existing = _TiedKeysInternal._read_tied_key_metadata(curve) or []
            if not cmds.attributeQuery(TIED_KEYS_ATTR, node=curve, exists=True):
                cmds.addAttr(curve, longName=TIED_KEYS_ATTR, dataType="string")
            merged = sorted({round(t, 4) for t in [*existing, *times]})
            cmds.setAttr(f"{curve}.{TIED_KEYS_ATTR}", json.dumps(merged), type="string")
        except RuntimeError:
            pass

    @staticmethod
    def _clear_tied_key_metadata(curve: str) -> None:
        """Remove the bookend-time record from *curve*, if present."""
        try:
            if cmds.attributeQuery(TIED_KEYS_ATTR, node=curve, exists=True):
                cmds.deleteAttr(f"{curve}.{TIED_KEYS_ATTR}")
        except RuntimeError:
            pass

    @classmethod
    def _get_tied_keyframes(cls, objects, tolerance):
        """Body of :meth:`AnimUtils.get_tied_keyframes`."""
        objects = cls._resolve_keyed_objects(objects)
        if not objects:
            return {}

        tied_keyframes = {}

        for obj in objects:
            # Get all animation curves for this object
            keyed_curves = cmds.keyframe(obj, query=True, name=True)

            if not keyed_curves:
                continue

            obj_tied_keys = {}

            for curve in keyed_curves:
                # cmds.keyframe returns keys in time order; times and values
                # are index-aligned.
                keyframe_times = cmds.keyframe(curve, query=True, timeChange=True)
                if not keyframe_times:
                    continue

                recorded = cls._read_tied_key_metadata(curve)
                if recorded is not None:
                    # Exact record of what tie_keyframes inserted. Keep only
                    # times that still have a key (the user may have removed
                    # or moved some since).
                    tied_times = [
                        t
                        for t in recorded
                        if any(abs(t - k) < 1e-4 for k in keyframe_times)
                    ]
                else:
                    # Heuristic fallback for curves tied before metadata
                    # existed.  Need at least 3 keys: a 2-key curve is a
                    # deliberate hold, not animation plus a bookend.
                    tied_times = []
                    if len(keyframe_times) >= 3:
                        values = cmds.keyframe(curve, query=True, valueChange=True)
                        if abs(values[0] - values[1]) < tolerance and (
                            cls._key_is_flat_or_stepped(curve, keyframe_times[0])
                        ):
                            tied_times.append(keyframe_times[0])
                        if abs(values[-1] - values[-2]) < tolerance and (
                            cls._key_is_flat_or_stepped(curve, keyframe_times[-1])
                        ):
                            tied_times.append(keyframe_times[-1])

                # Store tied keyframes for this attribute if any were found
                if tied_times:
                    obj_tied_keys[curve] = tied_times

            # Store object's tied keyframes if any were found
            if obj_tied_keys:
                tied_keyframes[obj] = obj_tied_keys

        return tied_keyframes

    @classmethod
    def _tie_keyframes(cls, objects, absolute, padding, custom_range):
        """Body of :meth:`AnimUtils.tie_keyframes`."""
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        from mayatk.core_utils.undo_recorder import UndoRecorder

        objects = cls._resolve_keyed_objects(objects)
        if not objects:
            cmds.warning("No keyed objects found.")
            return

        # Determine the keyframe range
        if custom_range:
            start_frame, end_frame = custom_range
        elif absolute:
            range_result = cls.get_keyframe_times(objects, as_range=True)
            if range_result is None:
                cmds.warning("No keyframes found on any objects.")
                return
            start_frame, end_frame = range_result
        else:
            start_frame = cmds.playbackOptions(query=True, minTime=True)
            end_frame = cmds.playbackOptions(query=True, maxTime=True)

        tie_start_frame = start_frame - padding
        tie_end_frame = end_frame + padding

        # Collect unique animation curves.  Unitless (set-driven-key) curves
        # are excluded: bookends belong at time-range extremes, and an SDK
        # curve's x-axis is a driver value, not a frame (om2's
        # MFnAnimCurve.input() even returns a bare float there, which would
        # crash the MTime path below).
        all_keyed_curves = cls._filter_time_curves(
            list(
                set(
                    cmds.listConnections(
                        objects, type="animCurve", source=True, destination=False
                    )
                    or []
                )
            )
        )

        if not all_keyed_curves:
            print(
                f"Keyframes tied to frames {tie_start_frame} and {tie_end_frame} for keyed attributes."
            )
            return

        # Tangent type constants
        kStep = oma2.MFnAnimCurve.kTangentStep
        kStepNext = oma2.MFnAnimCurve.kTangentStepNext
        kFlat = oma2.MFnAnimCurve.kTangentFlat
        _step_types = {kStep, kStepNext}
        _auto_types = {
            oma2.MFnAnimCurve.kTangentAuto,
            oma2.MFnAnimCurve.kTangentSmooth,
            oma2.MFnAnimCurve.kTangentClamped,
        }

        start_mtime = om2.MTime(tie_start_frame, om2.MTime.uiUnit())
        end_mtime = om2.MTime(tie_end_frame, om2.MTime.uiUnit())

        # Build MSelectionList for all curves at once
        sel = om2.MSelectionList()
        for curve_name in all_keyed_curves:
            try:
                sel.add(curve_name)
            except RuntimeError:
                continue  # Curve may have been deleted

        with UndoRecorder.record() as recorder:
            for i in range(sel.length()):
                dep = sel.getDependNode(i)
                fn = oma2.MFnAnimCurve(dep)
                n = fn.numKeys
                if n == 0:
                    continue

                inserted_bookends = []

                first_t = fn.input(0).value
                last_t = fn.input(n - 1).value

                # Determine if curve is fully stepped (early-exit check)
                is_fully_stepped = True
                for ki in range(n):
                    if fn.outTangentType(ki) not in _step_types:
                        is_fully_stepped = False
                        break

                bookend_tt = kStep if is_fully_stepped else kFlat

                # --- Start bookend ---
                if (
                    abs(tie_start_frame - first_t) < 1e-4
                    or fn.find(start_mtime) is not None
                ):
                    # A key already exists at the bookend time (range boundary or
                    # an interior key) — skip so it's never corrupted or recorded
                    # (and later removed) as a tie.
                    pass
                else:
                    if tie_start_frame < first_t:
                        # Bookend is BEFORE the curve range.
                        # Freeze first key's auto tangents before inserting.
                        if not is_fully_stepped:
                            cls._freeze_adjacent_tangent(
                                fn,
                                0,
                                is_in=True,
                                bookend_facing=True,
                                auto_types=_auto_types,
                                step_types=_step_types,
                                recorder=recorder,
                            )
                            cls._freeze_adjacent_tangent(
                                fn,
                                0,
                                is_in=False,
                                bookend_facing=False,
                                auto_types=_auto_types,
                                step_types=_step_types,
                                recorder=recorder,
                            )
                    else:
                        # Bookend is INSIDE the curve range — freeze neighbors.
                        if not is_fully_stepped:
                            adj_idx = cls._find_adjacent_key(fn, tie_start_frame, n)
                            if adj_idx is not None:
                                # Key after insertion: freeze its in-tangent
                                cls._freeze_adjacent_tangent(
                                    fn,
                                    adj_idx,
                                    is_in=True,
                                    bookend_facing=False,
                                    auto_types=_auto_types,
                                    step_types=_step_types,
                                    recorder=recorder,
                                )
                                # Key before insertion: freeze its out-tangent
                                if adj_idx > 0:
                                    cls._freeze_adjacent_tangent(
                                        fn,
                                        adj_idx - 1,
                                        is_in=False,
                                        bookend_facing=False,
                                        auto_types=_auto_types,
                                        step_types=_step_types,
                                        recorder=recorder,
                                    )

                    # Evaluate curve value at the bookend time and insert
                    start_val = fn.evaluate(start_mtime)
                    fn.addKey(
                        start_mtime, start_val, bookend_tt, bookend_tt, **recorder.anim
                    )
                    inserted_bookends.append(tie_start_frame)

                # --- End bookend ---
                # Re-read numKeys since we may have added a key
                n = fn.numKeys
                # Re-read last_t from the actual last key
                last_t = fn.input(n - 1).value

                if abs(tie_end_frame - last_t) < 1e-4 or fn.find(end_mtime) is not None:
                    # A key already exists at the bookend time — skip (see start)
                    pass
                else:
                    if tie_end_frame > last_t:
                        # Bookend is AFTER the curve range.
                        last_idx = n - 1
                        if not is_fully_stepped:
                            cls._freeze_adjacent_tangent(
                                fn,
                                last_idx,
                                is_in=False,
                                bookend_facing=True,
                                auto_types=_auto_types,
                                step_types=_step_types,
                                recorder=recorder,
                            )
                            cls._freeze_adjacent_tangent(
                                fn,
                                last_idx,
                                is_in=True,
                                bookend_facing=False,
                                auto_types=_auto_types,
                                step_types=_step_types,
                                recorder=recorder,
                            )
                    else:
                        # Bookend is INSIDE the curve range — freeze neighbors.
                        if not is_fully_stepped:
                            adj_idx = cls._find_adjacent_key(fn, tie_end_frame, n)
                            if adj_idx is not None:
                                cls._freeze_adjacent_tangent(
                                    fn,
                                    adj_idx,
                                    is_in=True,
                                    bookend_facing=False,
                                    auto_types=_auto_types,
                                    step_types=_step_types,
                                    recorder=recorder,
                                )
                                if adj_idx > 0:
                                    cls._freeze_adjacent_tangent(
                                        fn,
                                        adj_idx - 1,
                                        is_in=False,
                                        bookend_facing=False,
                                        auto_types=_auto_types,
                                        step_types=_step_types,
                                        recorder=recorder,
                                    )

                    end_val = fn.evaluate(end_mtime)
                    fn.addKey(
                        end_mtime, end_val, bookend_tt, bookend_tt, **recorder.anim
                    )
                    inserted_bookends.append(tie_end_frame)

                # Record exactly which keys were inserted so untie_keyframes can
                # remove them without relying on value-equality guesswork.
                cls._write_tied_key_metadata(fn.name(), inserted_bookends)

        print(
            f"Keyframes tied to frames {tie_start_frame} and {tie_end_frame} for keyed attributes."
        )

    @classmethod
    def _untie_keyframes(cls, objects):
        """Body of :meth:`AnimUtils.untie_keyframes`."""
        # Use the helper method to detect tied keyframes
        tied_keyframes = cls.get_tied_keyframes(objects)

        keys_removed = 0

        # Remove all detected tied keyframes
        for obj, attr_dict in tied_keyframes.items():
            for attr, tied_times in attr_dict.items():
                for time in tied_times:
                    cmds.cutKey(attr, time=(time, time), clear=True)
                    keys_removed += 1

        # The scene is untied now — drop the bookend records so stale entries
        # can't linger on these objects' curves.
        for obj in cls._resolve_keyed_objects(objects):
            for curve in cmds.keyframe(obj, query=True, name=True) or []:
                cls._clear_tied_key_metadata(curve)

        if keys_removed > 0:
            print(f"Removed {keys_removed} bookend keyframe(s).")
        else:
            print("No bookend keyframes found to remove.")

        return tied_keyframes
