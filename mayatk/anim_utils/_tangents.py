# !/usr/bin/python
# coding=utf-8
"""Tangents behind :class:`mayatk.AnimUtils`.

Tangent snapshots (read / restore / mirror), stepped keys, the visibility-aware
"smart" tangent pass, and keying that preserves the existing tangent types
(with the setKeyframe / keyTangent tangent-name remaps). Reached through
:class:`mayatk.AnimUtils`; nothing here is called directly.
"""

from typing import Any, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except Exception:
    cmds = None


from mayatk.anim_utils._key_query import _KeyQueryInternal


_SETKEY_IN_TANGENT_REMAP = {"step": "stepnext", "fixed": "auto"}
"""In-tangent types ``cmds.setKeyframe`` rejects, mapped to accepted
equivalents ("step" is out-tangent-only; its in-tangent form is "stepnext").
``cmds.keyTangent`` accepts "fixed" directly — only remap for setKeyframe.
"""


_SETKEY_OUT_TANGENT_REMAP = {"fixed": "auto"}
"""Out-tangent types ``cmds.setKeyframe`` rejects ("Cannot set out-tangents to
fixed"), mapped to accepted equivalents.  "step"/"stepnext" are both valid
out-tangents, so only "fixed" needs remapping.  As with the in-tangent table,
``cmds.keyTangent`` accepts "fixed" directly — callers re-assert the original
type there after the key exists.
"""


_KEYTANGENT_IN_TANGENT_REMAP = {"step": "stepnext"}
"""In-tangent remap for ``cmds.keyTangent``, which — unlike setKeyframe —
accepts "fixed"; only the out-tangent-only "step" needs its in-side form.
"""


class _TangentInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _set_smart_tangents(
        curves: List[str],
        tangent_type: str = "auto",
        time_range: Optional[Tuple[float, float]] = None,
        preserve_stepped: bool = True,
    ) -> None:
        """Apply tangent type to curves, enforcing 'step' for visibility attributes.

        Parameters:
            curves: List of animation curves to modify.
            tangent_type: The tangent type to apply to standard curves (default: 'auto').
            time_range: Optional (start, end) tuple to limit the effect.
            preserve_stepped: If True, existing 'step' tangents on non-visibility curves
                            will be preserved. Default is True.
        """
        if not curves:
            return

        curves_to_step, curves_to_smooth = _KeyQueryInternal._get_visibility_curves(
            curves
        )

        range_args = {"time": time_range} if time_range else {}

        if curves_to_smooth:
            for curve in curves_to_smooth:
                try:
                    # If preserving stepped tangents, we need to check existing types
                    restore_steps = []
                    if preserve_stepped:
                        # Query existing tangent types
                        # Note: keyTangent query returns list of strings
                        # We must query times first to match them up, as keyTangent might return types for all keys in range
                        times = cmds.keyframe(curve, query=True, tc=True, **range_args)
                        if times:
                            types = cmds.keyTangent(
                                curve, query=True, outTangentType=True, **range_args
                            )
                            if types and len(types) == len(times):
                                # Identify stepped keys
                                for t, type_name in zip(times, types):
                                    if type_name in ("step", "stepnext"):
                                        restore_steps.append(t)

                    # Apply smoothing to all (bulk operation is faster)
                    cmds.keyTangent(
                        curve,
                        edit=True,
                        outTangentType=tangent_type,
                        inTangentType=tangent_type,
                        **range_args,
                    )

                    # Restore stepped tangents if any were found
                    if restore_steps:
                        for t in restore_steps:
                            cmds.keyTangent(
                                curve,
                                edit=True,
                                time=(t,),
                                outTangentType="step",
                                inTangentType="clamped",
                            )

                except RuntimeError as e:
                    cmds.warning(f"Failed to adjust smooth tangents for {curve}: {e}")

        if curves_to_step:
            try:
                # 'step' is only valid for out-tangents.
                # For in-tangents, we use 'clamped' to avoid errors, though it doesn't affect the step behavior.
                cmds.keyTangent(
                    curves_to_step,
                    edit=True,
                    outTangentType="step",
                    inTangentType="clamped",
                    **range_args,
                )
            except RuntimeError as e:
                cmds.warning(f"Failed to adjust step tangents: {e}")

    # Stepped tangents are an OUT-side-only property: Maya rejects
    # ``inTangentType="step"`` outright and accepts ``inTangentType="stepnext"``
    # while ignoring it — a segment's shape is governed entirely by the OUT
    # tangent of the key that PRECEDES it.  Mirroring in time therefore has to
    # migrate a step flag to the neighbouring key (and swap its sense), not
    # swap it onto the in side like an ordinary tangent handle.
    _STEP_TANGENT_TYPES: Tuple[str, str] = ("step", "stepnext")

    _MIRRORED_STEP_TYPES: Dict[str, str] = {"step": "stepnext", "stepnext": "step"}

    @classmethod
    def _mirror_tangent_data(
        cls,
        data: List[Dict[str, Any]],
        flip_time: bool,
        flip_value: bool,
    ) -> List[Dict[str, Any]]:
        """Mirror per-key tangent snapshots for a time and/or value inversion.

        A time flip swaps each key's handles (in <-> out, angles negated) and
        relocates stepped segments: the hold described by key *i*'s out tangent
        spans the segment to key *i+1*, and that segment's later-in-time end is
        key *i* once reversed — so the flag moves to key *i+1*'s out tangent as
        its opposite (``step`` <-> ``stepnext``).  A value flip only negates the
        angles; a hold is a hold regardless of which way the values run.

        Angles ride along for every type, including the self-computing ones
        (``auto``, ``linear``, ...) whose angle Maya recomputes: ``set_tangent_info``
        writes the types LAST, so a type that owns its own angle simply
        discards what was written and the caller needs no per-type gating.

        Parameters:
            data: One snapshot per key of a SINGLE curve, in ascending time
                order (entries as returned by ``get_tangent_info``; an empty
                dict for a time carrying no key).
            flip_time: The keys are being reversed in time.
            flip_value: The key values are being flipped about a pivot.

        Returns:
            List[Dict[str, Any]]: Parallel to *data* — entry ``i`` is the
            snapshot to apply to the key that entry ``i`` described, at its
            new (inverted) time.
        """
        if not flip_time:  # value-only flip: handles keep their sides
            mirrored = []
            for snapshot in data:
                if not snapshot:
                    mirrored.append(snapshot)
                    continue
                entry = dict(snapshot)
                if flip_value:
                    for key in ("inAngle", "outAngle"):
                        entry[key] = -entry[key]
                mirrored.append(entry)
            return mirrored

        # Time flip: swap each key's handles.  A second negation cancels out
        # when the values are flipped too, so "both" is a plain swap.
        sign = 1.0 if flip_value else -1.0
        mirrored = []
        for snapshot in data:
            if not snapshot:
                mirrored.append(snapshot)
                continue
            in_type = snapshot["inTangentType"]
            out_type = snapshot["outTangentType"]
            mirrored.append(
                {
                    # A stepped tangent's own handle is flat (angle 0) and it
                    # cannot live on the in side, so it crosses over as "flat";
                    # the migration pass below re-homes the hold itself.
                    "inTangentType": (
                        "flat" if out_type in cls._STEP_TANGENT_TYPES else out_type
                    ),
                    "outTangentType": (
                        "flat" if in_type in cls._STEP_TANGENT_TYPES else in_type
                    ),
                    "inAngle": sign * snapshot["outAngle"],
                    "outAngle": sign * snapshot["inAngle"],
                    "inWeight": snapshot["outWeight"],
                    "outWeight": snapshot["inWeight"],
                }
            )

        # Migrate stepped segments onto the key that now precedes them.  The
        # last key's out tangent only drives extrapolation, so it is dropped.
        for index, snapshot in enumerate(data):
            if not snapshot or index + 1 >= len(data):
                continue
            out_type = snapshot["outTangentType"]
            if out_type in cls._STEP_TANGENT_TYPES and mirrored[index + 1]:
                mirrored[index + 1]["outTangentType"] = cls._MIRRORED_STEP_TYPES[
                    out_type
                ]

        return mirrored

    @staticmethod
    def _freeze_adjacent_tangent(
        fn, idx, is_in, bookend_facing, auto_types, step_types, recorder=None
    ):
        """Freeze an auto tangent to kFixed (preserving its current XY), or set
        the bookend-facing side to kFlat for a constant-value hold.

        Parameters:
            fn (MFnAnimCurve): The animation curve function set.
            idx (int): Key index whose tangent to freeze.
            is_in (bool): True = in-tangent, False = out-tangent.
            bookend_facing (bool): True if this tangent handle faces the bookend
                key (should become flat).  False if it faces the curve interior
                (should lock its current angle via kFixed).
            auto_types (set): Set of MFnAnimCurve tangent type constants that are
                auto-computed (kTangentAuto, kTangentSmooth, kTangentClamped).
            step_types (set): Set of step tangent type constants to skip.
            recorder: The open ``UndoRecorder`` block to record the edits in,
                or None.
        """
        import maya.api.OpenMayaAnim as oma2

        tt = fn.inTangentType(idx) if is_in else fn.outTangentType(idx)
        if tt in step_types:
            return  # Stepped tangents are never recalculated — nothing to freeze.

        if tt in auto_types:
            anim = recorder.anim if recorder is not None else {}
            if bookend_facing:
                # Set to flat for a clean constant-value hold into the bookend.
                if is_in:
                    fn.setInTangentType(idx, oma2.MFnAnimCurve.kTangentFlat, **anim)
                else:
                    fn.setOutTangentType(idx, oma2.MFnAnimCurve.kTangentFlat, **anim)
            else:
                # Interior-facing: snapshot current XY, then convert to kFixed
                # so Maya won't recalculate it when a neighbor key is added.
                xy = fn.getTangentXY(idx, is_in)
                if is_in:
                    fn.setInTangentType(idx, oma2.MFnAnimCurve.kTangentFixed, **anim)
                    fn.setTangent(idx, xy[0], xy[1], True, **anim)
                else:
                    fn.setOutTangentType(idx, oma2.MFnAnimCurve.kTangentFixed, **anim)
                    fn.setTangent(idx, xy[0], xy[1], False, **anim)

    @staticmethod
    def _key_is_flat_or_stepped(
        curve: str, time: float, angle_tol: float = 0.01
    ) -> bool:
        """True if the key at *time* has flat or stepped tangents on both sides —
        the shape tie_keyframes always gives its bookend keys.

        A shaped (sloped) key is genuine animation and must survive untie.
        """
        t = (time, time)
        types = cmds.keyTangent(
            curve, query=True, time=t, inTangentType=True, outTangentType=True
        )
        angles = cmds.keyTangent(curve, query=True, time=t, inAngle=True, outAngle=True)
        if not types or not angles:
            return False
        for side_type, side_angle in zip(types, angles):
            if side_type in ("step", "stepnext"):
                continue
            if abs(side_angle) > angle_tol:
                return False
        return True

    @staticmethod
    def _set_key_preserving_tangents(
        plug: str, time: float, value: float, **kwargs
    ) -> None:
        """Set a keyframe while preserving existing tangent types.

        If a key already exists at *time* its in/out tangent types are
        snapshotted before the value is written and restored afterwards.
        If no key exists, the tangent type of the nearest preceding key on
        the same curve is inherited so that stepped (or other non-default)
        tangent styles propagate correctly.

        Any extra *kwargs* are forwarded to ``cmds.setKeyframe``.

        Note:
            If *kwargs* includes ``inTangentType`` or ``outTangentType``
            they will override the automatically preserved tangent types.
        """
        existing_key = cmds.keyframe(
            plug, query=True, time=(time, time), timeChange=True
        )
        tangent_types = None

        if existing_key:
            try:
                itt = cmds.keyTangent(
                    plug, query=True, time=(time, time), inTangentType=True
                )
                ott = cmds.keyTangent(
                    plug, query=True, time=(time, time), outTangentType=True
                )
                if itt and ott:
                    tangent_types = (itt[0], ott[0])
            except Exception:
                pass
        else:
            # No key at this time — inherit from nearest preceding key.
            try:
                all_times = cmds.keyframe(plug, query=True, timeChange=True)
                if all_times:
                    preceding = [kt for kt in all_times if kt < time]
                    if preceding:
                        pt = max(preceding)
                        itt = cmds.keyTangent(
                            plug, query=True, time=(pt, pt), inTangentType=True
                        )
                        ott = cmds.keyTangent(
                            plug, query=True, time=(pt, pt), outTangentType=True
                        )
                        if itt and ott:
                            tangent_types = (itt[0], ott[0])
            except Exception:
                pass

        # Set the keyframe — pass tangent types at creation time when
        # available so Maya doesn't need a second edit pass (which can
        # silently drop "step" in-tangent types).
        kw = dict(time=time, value=value)
        if tangent_types:
            # setKeyframe rejects types keyTangent accepts ("fixed" on either
            # side, "step" as an in-tangent) — remap for creation only; the
            # keyTangent pass below re-asserts the ORIGINAL types.
            kw["inTangentType"] = _SETKEY_IN_TANGENT_REMAP.get(
                tangent_types[0], tangent_types[0]
            )
            kw["outTangentType"] = _SETKEY_OUT_TANGENT_REMAP.get(
                tangent_types[1], tangent_types[1]
            )
        kw.update(kwargs)
        cmds.setKeyframe(plug, **kw)

        # Belt-and-suspenders: re-apply tangent types after creation
        # in case setKeyframe's auto-tangent logic overrode them.
        # Skip sides the caller explicitly set via kwargs — the preserved
        # types must not override an intentional caller choice.
        if tangent_types:
            if "outTangentType" not in kwargs:
                try:
                    cmds.keyTangent(
                        plug,
                        time=(time, time),
                        edit=True,
                        outTangentType=tangent_types[1],
                    )
                except Exception:
                    pass
            if "inTangentType" not in kwargs:
                try:
                    cmds.keyTangent(
                        plug,
                        time=(time, time),
                        edit=True,
                        inTangentType=_KEYTANGENT_IN_TANGENT_REMAP.get(
                            tangent_types[0], tangent_types[0]
                        ),
                    )
                except Exception:
                    pass

    @staticmethod
    def _get_tangent_info(attr_name, time):
        """Body of :meth:`AnimUtils.get_tangent_info`."""
        itt = cmds.keyTangent(attr_name, query=True, time=(time,), inTangentType=True)
        if not itt:
            return {}
        return {
            "inTangentType": itt[0],
            "outTangentType": cmds.keyTangent(
                attr_name, query=True, time=(time,), outTangentType=True
            )[0],
            "inAngle": cmds.keyTangent(
                attr_name, query=True, time=(time,), inAngle=True
            )[0],
            "outAngle": cmds.keyTangent(
                attr_name, query=True, time=(time,), outAngle=True
            )[0],
            "inWeight": cmds.keyTangent(
                attr_name, query=True, time=(time,), inWeight=True
            )[0],
            "outWeight": cmds.keyTangent(
                attr_name, query=True, time=(time,), outWeight=True
            )[0],
        }

    @staticmethod
    def _set_tangent_info(attr_name, time, tangent_info):
        """Body of :meth:`AnimUtils.set_tangent_info`."""
        types = {}
        weights_angles = {}
        for k, v in tangent_info.items():
            if "TangentType" in k:
                types[k] = v
            else:
                weights_angles[k] = v

        # Angles/weights first (these may implicitly set type to 'fixed')
        if weights_angles:
            cmds.keyTangent(attr_name, time=(time,), edit=True, **weights_angles)
        # Types last so they take precedence
        if types:
            cmds.keyTangent(attr_name, time=(time,), edit=True, **types)

    @classmethod
    def _step_keys(cls, objects, keys, tangent, resolution_order):
        """Body of :meth:`AnimUtils.step_keys`."""
        # --- Auto resolution: resolve keys via _resolve_keys helper ---
        if keys == "auto":
            resolved = cls._resolve_keys(
                objects, mode="auto", resolution_order=resolution_order
            )
            # Empty dict → None so the "step all" path handles no-result.
            keys = resolved["curves"] if resolved["curves"] else None
            objects = resolved["objects"]

        result = {"curves": 0, "objects": 0}

        # Build tangent kwargs from the tangent parameter.
        tan_kw: dict = {}
        if tangent in ("out", "both"):
            tan_kw["outTangentType"] = "step"
        if tangent in ("in", "both"):
            tan_kw["inTangentType"] = "stepnext"
        if not tan_kw:
            tan_kw["outTangentType"] = "step"  # fallback

        # When setting only one side we must preserve the opposite side's
        # type, angle, and weight.  Maya's tangent lock couples the
        # handles geometrically, so a naive ``lock=False`` causes the
        # untouched side's handle to jump.  Instead we snapshot → apply
        # both sides → restore the untouched side.
        _one_side = tangent in ("in", "out")

        def _apply(curve: str, time_arg=None):
            """Apply tangent kwargs to *curve*, optionally at *time_arg*."""
            if _one_side and time_arg is not None:
                # Per-key: snapshot the opposite side, apply, restore.
                _apply_one_side(curve, time_arg, tangent)
                return
            if _one_side and time_arg is None:
                # Whole-curve: iterate every key individually.
                all_times = cmds.keyframe(curve, q=True, timeChange=True) or []
                for t in all_times:
                    _apply_one_side(curve, (t, t), tangent)
                return
            # "both" — set out=step + in=stepnext on current key.
            kw = dict(edit=True, **tan_kw)
            if time_arg is not None:
                kw["time"] = time_arg
            try:
                cmds.keyTangent(curve, **kw)
            except RuntimeError as e:
                cmds.warning(f"step_keys: failed to step {curve}: {e}")
            # For "both" with a specific time, also set out=step on
            # the predecessor key so the incoming segment is stepped.
            # inTangentType="stepnext" alone does NOT produce step
            # interpolation — the predecessor's out-tangent must be
            # "step" for the segment to hold flat.
            if tangent == "both" and time_arg is not None:
                all_times = cmds.keyframe(curve, q=True, timeChange=True) or []
                t = time_arg[0]
                prev_times = [pt for pt in all_times if pt < t]
                if prev_times:
                    prev_t = max(prev_times)
                    ott = cmds.keyTangent(
                        curve,
                        q=True,
                        time=(prev_t, prev_t),
                        outTangentType=True,
                    )
                    if not (ott and ott[0] == "step"):
                        _apply_one_side(curve, (prev_t, prev_t), "out")

        def _apply_one_side(curve: str, time_arg, side: str):
            """Set one tangent side while preserving the other side completely.

            Parameters:
                side: ``"in"`` to set inTangentType=stepnext (preserving out),
                      ``"out"`` to set outTangentType=step (preserving in).

            Angle and weight are only restored when the original tangent type
            is ``"fixed"`` — the only type where those values are user-defined.
            For auto-computed types (auto, spline, linear, flat, plateau,
            clamped) restoring the type alone lets Maya recompute the handle
            geometry.  Restoring angle on an auto tangent would silently
            convert it to ``"fixed"``, breaking auto-computation.
            """
            try:
                if side == "in":
                    # Preserve out-tangent
                    ott = cmds.keyTangent(
                        curve, q=True, time=time_arg, outTangentType=True
                    )
                    restore_geometry = ott and ott[0] == "fixed"
                    if restore_geometry:
                        oa = cmds.keyTangent(
                            curve, q=True, time=time_arg, outAngle=True
                        )
                        ow = cmds.keyTangent(
                            curve, q=True, time=time_arg, outWeight=True
                        )
                    cmds.keyTangent(
                        curve,
                        edit=True,
                        time=time_arg,
                        lock=False,
                        inTangentType="stepnext",
                    )
                    # Restore out-tangent type (always)
                    if ott:
                        cmds.keyTangent(
                            curve, edit=True, time=time_arg, outTangentType=ott[0]
                        )
                    # Restore angle/weight only for "fixed" tangents
                    if restore_geometry:
                        if oa is not None:
                            cmds.keyTangent(
                                curve, edit=True, time=time_arg, outAngle=oa[0]
                            )
                        if ow is not None:
                            cmds.keyTangent(
                                curve, edit=True, time=time_arg, outWeight=ow[0]
                            )
                else:  # side == "out"
                    # Preserve in-tangent
                    itt = cmds.keyTangent(
                        curve, q=True, time=time_arg, inTangentType=True
                    )
                    restore_geometry = itt and itt[0] == "fixed"
                    if restore_geometry:
                        ia = cmds.keyTangent(curve, q=True, time=time_arg, inAngle=True)
                        iw = cmds.keyTangent(
                            curve, q=True, time=time_arg, inWeight=True
                        )
                    cmds.keyTangent(
                        curve,
                        edit=True,
                        time=time_arg,
                        lock=False,
                        outTangentType="step",
                    )
                    # Restore in-tangent type (always)
                    if itt:
                        cmds.keyTangent(
                            curve, edit=True, time=time_arg, inTangentType=itt[0]
                        )
                    # Restore angle/weight only for "fixed" tangents
                    if restore_geometry:
                        if ia is not None:
                            cmds.keyTangent(
                                curve, edit=True, time=time_arg, inAngle=ia[0]
                            )
                        if iw is not None:
                            cmds.keyTangent(
                                curve, edit=True, time=time_arg, inWeight=iw[0]
                            )
            except RuntimeError as e:
                cmds.warning(
                    f"step_keys: tangent edit on {curve} at {time_arg} failed "
                    f"({e}); the opposite side may not be fully restored."
                )

        # --- Dict: per-curve per-time stepping ---
        if isinstance(keys, dict) and keys:
            for curve, key_times in keys.items():
                if key_times is None:
                    _apply(curve)
                else:
                    for t in key_times:
                        _apply(curve, time_arg=(t, t))
            result["curves"] = len(keys)
            return result

        # --- Curve names passed directly (e.g. graph-editor selection) ---
        # Query the *selected* key times per curve so that only those
        # keys are stepped, not the entire curve.
        if isinstance(keys, (list, tuple)) and keys:
            unique = set(keys)
            for curve in unique:
                sel_times = cmds.keyframe(
                    curve, query=True, selected=True, timeChange=True
                )
                if sel_times:
                    for t in sel_times:
                        _apply(curve, time_arg=(t, t))
                else:
                    # Fallback: no selection info — step the whole curve
                    _apply(curve)
            result["curves"] = len(unique)
            return result

        # --- Resolve objects ---
        if objects is None:
            objects = cmds.ls(selection=True)
        if not objects:
            return result

        curves = cls._filter_time_curves(cls.objects_to_curves(objects))
        if not curves:
            return result

        # --- Time value: step only keys at that frame ---
        if isinstance(keys, (int, float)):
            time_arg = (keys, keys)
            touched = [
                c
                for c in curves
                if cmds.keyframe(c, query=True, time=time_arg, keyframeCount=True)
            ]
            for curve in touched:
                _apply(curve, time_arg=time_arg)
            result["curves"] = len(touched)
            result["objects"] = len(objects)
            return result

        # --- None: step all keys ---
        for curve in curves:
            _apply(curve)
        result["curves"] = len(curves)
        result["objects"] = len(objects)
        return result
