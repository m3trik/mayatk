# !/usr/bin/python
# coding=utf-8
"""Key reduction behind :class:`mayatk.AnimUtils`.

The :attr:`AnimUtils.OPTIMIZE_LEVELS` resolution and the passes each level
runs: static-curve removal, redundant flat keys, ``filterCurve`` simplify,
reduce-to-extremes with refit tangents, and the corrupted-curve repair.

Reached through :class:`mayatk.AnimUtils`; nothing here is called directly.
"""

from typing import Any, Callable, Dict, List, Sequence

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

import pythontk as ptk


class _CurveOptimizeInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _curve_value_to_ui(fn) -> Callable[[float], float]:
        """Return a converter from *fn*'s internal value units to UI units
        (radians -> UI angle for angular curves, cm -> UI distance for linear
        ones, identity for unitless)."""
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        kind = fn.animCurveType
        if kind in (oma2.MFnAnimCurve.kAnimCurveTA, oma2.MFnAnimCurve.kAnimCurveUA):
            ui = om2.MAngle.uiUnit()
            return lambda v: om2.MAngle(v, om2.MAngle.kRadians).asUnits(ui)
        if kind in (oma2.MFnAnimCurve.kAnimCurveTL, oma2.MFnAnimCurve.kAnimCurveUL):
            ui = om2.MDistance.uiUnit()
            return lambda v: om2.MDistance(v, om2.MDistance.kCentimeters).asUnits(ui)
        if kind in (oma2.MFnAnimCurve.kAnimCurveTT, oma2.MFnAnimCurve.kAnimCurveUT):
            # Time-valued curves evaluate to an MTime.
            ui = om2.MTime.uiUnit()
            return lambda v: v.asUnits(ui)
        return lambda v: v

    @staticmethod
    def _is_per_frame_dense(times: Sequence[float], tol: float = 1e-6) -> bool:
        """True if consecutive key *times* are never more than one frame apart.

        The shape a ``sample_by=1`` bake has, and the one shape on which a
        tangent type cannot change the motion at frame resolution.
        """
        return len(times) >= 2 and all(
            b - a <= 1.0 + tol for a, b in zip(times, times[1:])
        )

    @classmethod
    def _normalize_optimize_level(cls, level):
        """Body of :meth:`AnimUtils.normalize_optimize_level`."""
        if not level:  # None/False/0/"" -- OFF.  Tested BEFORE the string
            return None  # branch: "" is a falsy config value, not a bad level
        if not isinstance(level, str):  # True, or a legacy truthy bool flag
            return cls.DEFAULT_OPTIMIZE_LEVEL
        key = cls._resolve_retired_level(level.strip().lower())
        if key not in cls.OPTIMIZE_LEVELS:
            raise ValueError(
                f"Unknown optimize level {level!r}; expected one of "
                f"{', '.join(cls.OPTIMIZE_LEVELS)}."
            )
        return key

    @classmethod
    def _resolve_optimize_level(cls, level):
        """Body of :meth:`AnimUtils.resolve_optimize_level`."""
        key = cls.normalize_optimize_level(level)
        return dict(cls.OPTIMIZE_LEVELS[key]) if key else None

    @classmethod
    def _get_static_curves(cls, objects, value_tolerance, recursive, as_strings):
        """Body of :meth:`AnimUtils.get_static_curves`."""
        from math import isclose
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        curves = cls.objects_to_curves(objects, recursive=recursive)
        static_curves = []

        # Use OpenMaya for fast first/last/mid value checks to skip
        # non-static curves before the expensive full-value query.
        sel = om2.MSelectionList()
        for curve in curves:
            sel.clear()
            try:
                sel.add(curve)
            except RuntimeError:
                continue
            fn = oma2.MFnAnimCurve(sel.getDependNode(0))
            n = fn.numKeys
            # A 0-key curve holds no value at all -- nothing to compare and
            # nothing to preserve, so it is not this function's business.
            # A 1-key curve is the MOST static curve possible (one value held
            # forever); it simply cannot be sampled by the first/last/mid
            # comparisons below, which is why it used to be dropped here
            # outright. Both fall through to the shared default-value guard,
            # so a single key off its default is still preserved.
            if n == 0:
                continue

            if n > 1:
                # Quick check: first, last, mid values.  fn.value() returns
                # internal units (radians for rotations) but comparisons are
                # within the same curve / same unit, so the tolerance is
                # self-consistent (stricter for radians than degrees).
                first_val = fn.value(0)
                if abs(fn.value(n - 1) - first_val) > value_tolerance:
                    continue
                if abs(fn.value(n // 2) - first_val) > value_tolerance:
                    continue

                # Full check via om2 (same-unit, fast C++ loop).
                is_static = True
                for i in range(1, n):
                    if abs(fn.value(i) - first_val) > value_tolerance:
                        is_static = False
                        break
                if not is_static:
                    continue

            # Check whether the constant value matches the driven
            # attribute's default.  Use cmds for both sides so the
            # units agree (e.g. degrees for rotations).
            driven = cmds.listConnections(
                curve, source=False, destination=True, plugs=True
            )
            if not driven:
                continue

            try:
                node, attr = driven[0].split(".", 1)
                defaults = cmds.attributeQuery(attr, node=node, listDefault=True)
                if defaults is not None:
                    default_val = defaults[0]
                    first_ui = cmds.keyframe(
                        curve, index=(0, 0), q=True, valueChange=True
                    )
                    if not first_ui or not isclose(
                        first_ui[0], default_val, abs_tol=value_tolerance
                    ):
                        continue
            except (ValueError, RuntimeError):
                continue

            static_curves.append(curve)

        return static_curves

    @classmethod
    def _get_redundant_flat_keys(
        cls,
        objects,
        value_tolerance,
        remove,
        recursive,
        as_strings,
        time_range,
        selected_only,
    ):
        """Body of :meth:`AnimUtils.get_redundant_flat_keys`."""
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        from mayatk.core_utils.undo_recorder import UndoRecorder

        curves = cls.objects_to_curves(objects, recursive=recursive)
        redundant = []
        # cmds.keyframe answers in UI DISPLAY units, but the tolerance is
        # tuned in centimeters (Maya's internal linear unit). In a scene
        # set to meters -- the web-export pipeline does exactly that before
        # optimizing -- every linear value shrinks 100x, so real slow motion
        # reads as flat and a loop-closing drift (equal endpoints defeat any
        # span guard) collapses to its boundary pair: a wire loom froze at
        # its rest pose, 2.7 cm from truth, in a shipped deliverable.
        # Normalize linear-output curves (nodeType animCurve?L) back to cm;
        # angular (degrees) and unitless curves are display-unit-stable.
        lin_to_cm = om2.MDistance(1.0, om2.MDistance.uiUnit()).asCentimeters()

        for curve in curves:
            if not cmds.objExists(curve):
                continue
            times = cmds.keyframe(curve, query=True, timeChange=True) or []
            if len(times) < 3:
                continue

            values = cmds.keyframe(curve, query=True, valueChange=True) or []
            if len(values) != len(times):
                continue

            if lin_to_cm != 1.0 and cmds.nodeType(curve).endswith("L"):
                values = [v * lin_to_cm for v in values]

            remove_indices, seg_starts, seg_lasts = ptk.find_flat_interior_indices(
                values, value_tolerance
            )
            if len(remove_indices) == 0:
                continue

            scoped = time_range is not None or selected_only
            if scoped:
                allowed = set(range(len(times)))
                if time_range is not None:
                    lo, hi = time_range
                    allowed &= {i for i, t in enumerate(times) if lo <= t <= hi}
                if selected_only:
                    sel = cmds.keyframe(
                        curve, query=True, selected=True, timeChange=True
                    )
                    sel_set = {round(t, 6) for t in (sel or ())}
                    allowed &= {
                        i for i, t in enumerate(times) if round(t, 6) in sel_set
                    }
                remove_indices = [int(i) for i in remove_indices if int(i) in allowed]
                if not remove_indices:
                    continue
                # Re-derive the boundary pairs from what SURVIVED the scope:
                # each contiguous block of removable keys is faced by the key
                # before it and the key after it, which is exactly what an
                # unscoped run's (start, last) pair already means.
                seg_pairs = []
                block = [remove_indices[0]]
                for i in remove_indices[1:]:
                    if i == block[-1] + 1:
                        block.append(i)
                    else:
                        seg_pairs.append((block[0] - 1, block[-1] + 1))
                        block = [i]
                seg_pairs.append((block[0] - 1, block[-1] + 1))
            else:
                seg_pairs = [(int(s), int(e)) for s, e in zip(seg_starts, seg_lasts)]

            if remove:
                # --- Removal: boundary tangents are frozen first (flat on
                # the hold-facing side, fixed on the interior-facing side)
                # so the curve keeps its shape when its neighbors vanish;
                # keys away from a run keep both neighbors and recompute
                # their auto tangents to identical values.  Every edit
                # lands in the undo chunk opened by @CoreUtils.undoable --
                # the cmds ones natively, the MFnAnimCurve ones through
                # UndoRecorder. ---
                _AUTO_TANGENTS = {"auto", "spline", "clamped", "autoease", "automix"}

                # No lock handling needed: cutKey/keyTangent addressed at
                # the CURVE NODE edit keys even when the driven attribute
                # (or its parent compound) is locked — locks only guard the
                # plug connection the old delete/reconnect rebuild touched.
                in_types = cmds.keyTangent(curve, query=True, inTangentType=True) or []
                out_types = (
                    cmds.keyTangent(curve, query=True, outTangentType=True) or []
                )

                try:
                    # 1) Freeze auto tangents to 'fixed' (locks each key's
                    # current angle).  Unscoped, on a sparse curve: EVERY
                    # key, not just the boundary ones — downstream FBX export
                    # reinterprets 'auto' tangents with its own algorithm,
                    # corrupting the curve shape between sparse keys, so no
                    # survivor may remain auto.  Contiguous runs are edited
                    # with one index-range call.
                    #
                    # Otherwise only the two keys left facing each vanished
                    # block.  An auto tangent re-solves from its neighbours,
                    # so those two would shift when the run between them goes
                    # — every other key keeps both neighbours and cannot
                    # move.  SCOPED: freezing the rest would re-type tangents
                    # OUTSIDE the user's selection, which is the whole thing a
                    # scoped edit promises not to do.  PER-FRAME DENSE (a
                    # sample_by=1 bake): every survivor but those two keeps
                    # neighbours one frame away, where no tangent algorithm
                    # can move a frame, so the whole-curve freeze was pure
                    # cost -- two keyTangent edits across every key of every
                    # baked curve (measured: the same removal, the same
                    # drift in Maya and through an FBX round trip).
                    if scoped or cls._is_per_frame_dense(times):
                        for s, e in seg_pairs:
                            for idx, tt_in, tt_out in (
                                (s, in_types, out_types),
                                (e, in_types, out_types),
                            ):
                                if not 0 <= idx < len(tt_in):
                                    continue
                                for flag, tlist in (
                                    ("inTangentType", tt_in),
                                    ("outTangentType", tt_out),
                                ):
                                    if tlist[idx] in _AUTO_TANGENTS:
                                        cmds.keyTangent(
                                            curve,
                                            edit=True,
                                            index=(idx, idx),
                                            **{flag: "fixed"},
                                        )
                    else:
                        for flag, tlist in (
                            ("inTangentType", in_types),
                            ("outTangentType", out_types),
                        ):
                            run = None
                            for i, tt in enumerate(tlist):
                                if tt in _AUTO_TANGENTS:
                                    if run is None:
                                        run = i
                                elif run is not None:
                                    cmds.keyTangent(
                                        curve,
                                        edit=True,
                                        index=(run, i - 1),
                                        **{flag: "fixed"},
                                    )
                                    run = None
                            if run is not None:
                                cmds.keyTangent(
                                    curve,
                                    edit=True,
                                    index=(run, len(tlist) - 1),
                                    **{flag: "fixed"},
                                )

                    # 2) Boundary tangents facing a flat run go flat for
                    # proper hold handles — only where the original type
                    # was auto-computed (shaped/stepped sides stay put).
                    # 3) Then the interiors go, by index, highest first so
                    # earlier indices stay valid.  One recorded block per
                    # curve, committed right after the cmds freeze above, so
                    # the queue holds the two in the order they were made.
                    sel = om2.MSelectionList()
                    sel.add(curve)
                    fn = oma2.MFnAnimCurve(sel.getDependNode(0))
                    flat_type = oma2.MFnAnimCurve.kTangentFlat
                    with UndoRecorder.record() as recorder:
                        for s, e in seg_pairs:
                            if s < len(out_types) and out_types[s] in _AUTO_TANGENTS:
                                fn.setOutTangentType(s, flat_type, **recorder.anim)
                            if e < len(in_types) and in_types[e] in _AUTO_TANGENTS:
                                fn.setInTangentType(e, flat_type, **recorder.anim)
                        for index in sorted(
                            (int(i) for i in remove_indices), reverse=True
                        ):
                            fn.remove(index, **recorder.anim)
                except RuntimeError as exc:
                    # Skip this curve rather than aborting the whole
                    # batch (and every later optimize phase) — the
                    # remaining curves are independent.
                    cmds.warning(
                        f"AnimUtils.get_redundant_flat_keys: key removal on "
                        f"'{curve}' failed ({exc}); curve skipped."
                    )
                    continue

            redundant.append((curve, [times[int(i)] for i in remove_indices]))

        return redundant

    @classmethod
    def _simplify_curve(
        cls,
        objects,
        value_tolerance,
        time_tolerance,
        recursive,
        as_strings,
        time_range,
        selected_only,
    ):
        """Body of :meth:`AnimUtils.simplify_curve`."""
        curves = cls.objects_to_curves(objects, recursive=recursive)
        simplified_curves = []

        kwargs: Dict[str, Any] = {
            "filter": "keyReducer",
            "precisionMode": 0,  # value precision
            "precision": value_tolerance,
        }
        if time_range is not None:
            kwargs["startTime"], kwargs["endTime"] = time_range
        if selected_only:
            kwargs["selectedKeys"] = True

        for curve in curves:
            try:
                before = cmds.keyframe(curve, q=True, keyframeCount=True) or 0
                cmds.filterCurve(curve, **kwargs)
                after = cmds.keyframe(curve, q=True, keyframeCount=True) or 0
                if after < before:
                    simplified_curves.append(curve)
            except RuntimeError:
                pass

        return simplified_curves

    @classmethod
    def _repair_corrupted_curves(
        cls,
        objects,
        recursive,
        delete_corrupted,
        fix_infinite,
        fix_invalid_times,
        time_range_threshold,
        value_threshold,
        quiet,
    ):
        """Body of :meth:`AnimUtils.repair_corrupted_curves`."""

        from mayatk.core_utils.diagnostics.animation_diag import AnimCurveDiagnostics

        return AnimCurveDiagnostics.repair_corrupted_curves(
            objects=objects,
            recursive=recursive,
            delete_corrupted=delete_corrupted,
            fix_infinite=fix_infinite,
            fix_invalid_times=fix_invalid_times,
            time_range_threshold=time_range_threshold,
            value_threshold=value_threshold,
            quiet=quiet,
        )

    @classmethod
    def _reduce_to_extremes(
        cls, objects, value_tolerance, recursive, quiet, stats, max_error
    ):
        """Body of :meth:`AnimUtils.reduce_to_extremes`."""
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        from mayatk.core_utils.undo_recorder import UndoRecorder

        curves = cls.objects_to_curves(
            cls._resolve_keyed_objects(objects), recursive=recursive
        )

        step_types = {"step", "stepnext"}
        reduced: List[str] = []
        keys_removed = 0
        worst_error = 0.0
        sel = om2.MSelectionList()
        for curve in curves:
            if not cmds.objExists(curve):
                continue
            sel.clear()
            sel.add(curve)
            fn = oma2.MFnAnimCurve(sel.getDependNode(0))
            # A driven curve (unitless input) answers to the float flags,
            # not the time ones.
            time_input = fn.isTimeInput
            range_kw = "time" if time_input else "float"
            if fn.numKeys < 3:
                continue
            # One query per property pair rather than per property: on a
            # production bake (~10M keys) the four separate reads were 90 s.
            tangent_types = (
                cmds.keyTangent(curve, q=True, inTangentType=True, outTangentType=True)
                or []
            )
            if step_types.intersection(tangent_types):
                continue
            pairs = (
                cmds.keyframe(
                    curve, q=True, valueChange=True, **{f"{range_kw}Change": True}
                )
                or []
            )
            times, values = pairs[0::2], pairs[1::2]
            if len(times) < 3 or len(values) != len(times):
                continue

            keep, in_slopes, out_slopes = ptk.MathUtils.reduce_samples(
                times, values, value_tolerance=value_tolerance, max_error=max_error
            )
            if len(keep) == len(times):
                continue

            if fn.isWeighted:
                cmds.keyTangent(curve, edit=True, weightedTangents=False)
            # The tweens go through om2, recorded with the tangents below. With
            # the undo queue off, a time-input curve is REPLACED by its kept
            # keys in one addKeys call: removing tweens one index at a time was
            # 56 s of a production pass (~10M keys at ~3 us each). A driven
            # (unitless-input) curve cannot take an MTimeArray, and a recorded
            # edit must not replace -- MAnimCurveChange undoes a replacing
            # addKeys one key short (measured, Maya 2025: 39 of 40 keys back) --
            # so both remove per index, highest first so lower indices stay valid.
            with UndoRecorder.record() as recorder:
                if time_input and not recorder.recording:
                    ui_unit = om2.MTime.uiUnit()
                    kept_times = om2.MTimeArray()
                    kept_values = om2.MDoubleArray()
                    for index in keep:
                        kept_times.append(om2.MTime(times[index], ui_unit))
                        kept_values.append(fn.value(index))  # internal units
                    fn.addKeys(
                        kept_times,
                        kept_values,
                        oma2.MFnAnimCurve.kTangentFixed,
                        oma2.MFnAnimCurve.kTangentFixed,
                        False,  # keepExistingKeys: replace the curve's keys
                        **recorder.anim,
                    )
                else:
                    kept = set(keep)
                    for index in range(len(times) - 1, -1, -1):
                        if index not in kept:
                            fn.remove(index, **recorder.anim)
                keys_removed += len(times) - len(keep)

                # setTangent reads x as UI-time frames and y as UI value units
                # (probed exact for TL/TA/TU at film and ntsc, cm and m).  It
                # applies the frames->seconds conversion to a unitless-input
                # (driven) curve as well, whose x is plain driver units, so one
                # driver unit has to be handed over as one second's worth of
                # frames.
                x_unit = (
                    1.0
                    if time_input
                    else om2.MTime(1.0, om2.MTime.kSeconds).asUnits(om2.MTime.uiUnit())
                )
                fixed = oma2.MFnAnimCurve.kTangentFixed
                flat = oma2.MFnAnimCurve.kTangentFlat
                for k in range(len(keep)):
                    m_in, m_out = in_slopes[k], out_slopes[k]
                    fn.setTangentsLocked(k, False, **recorder.anim)
                    fn.setInTangentType(
                        k, flat if m_in == 0.0 else fixed, **recorder.anim
                    )
                    fn.setOutTangentType(
                        k, flat if m_out == 0.0 else fixed, **recorder.anim
                    )
                    if m_in != 0.0:
                        fn.setTangent(k, x_unit, m_in, True, **recorder.anim)
                    if m_out != 0.0:
                        fn.setTangent(k, x_unit, m_out, False, **recorder.anim)
                    fn.setTangentsLocked(k, m_in == m_out, **recorder.anim)

            # Largest deviation of the refit curve from the bake, in UI units --
            # read back off the LIVE curve (so a tangent-unit slip shows), and
            # only when someone reads it: one evaluate per baked sample was
            # 24 s of a quiet production pass that reported nothing.
            if stats is not None or not quiet:
                to_ui = cls._curve_value_to_ui(fn)
                for t, v in zip(times, values):
                    at = om2.MTime(t, om2.MTime.uiUnit()) if time_input else t
                    worst_error = max(worst_error, abs(to_ui(fn.evaluate(at)) - v))
            reduced.append(curve)

        if not quiet:
            print(
                f"[extremes] {len(reduced)} curves reduced, {keys_removed} keys removed, "
                f"max deviation {worst_error:.6f}"
            )
        if stats is not None:
            stats.update(
                {
                    "reduced": len(reduced),
                    "reduce_keys_removed": keys_removed,
                    "reduce_max_error": worst_error,
                }
            )
        return reduced

    @classmethod
    def _optimize_keys(
        cls,
        objects,
        value_tolerance,
        time_tolerance,
        remove_flat_keys,
        remove_static_curves,
        simplify_keys,
        recursive,
        quiet,
        stats,
        progress_callback,
        through_blends,
    ):
        """Body of :meth:`AnimUtils.optimize_keys`."""
        # Guard: Maya's lazy initialization (triggered on first use)
        # runs initialStartup.mel which can reset the scene time-unit
        # to Maya's default (film/24fps), rescaling all keyframe times.
        # Capture the time-unit now (before any cmds call) and restore
        # it at the end if it changed.
        _saved_time_unit = cmds.currentUnit(query=True, time=True)

        # The extremes sentinel carries no magnitude: the static/flat passes
        # keep the default tolerance.
        extremes = value_tolerance < 0
        if extremes:
            value_tolerance = 0.001

        # Convert the input objects into curves once (avoid 3 redundant calls)
        if isinstance(objects, str):
            objects = [objects]
        elif isinstance(objects, (list, tuple, set)):
            objects = [str(o) for o in objects]
        else:
            objects = [str(objects)]

        targets = cmds.ls(objects, flatten=True)
        anim_curves = cls.objects_to_curves(
            targets, recursive=recursive, through_blends=through_blends
        )

        curves_before_count = len(anim_curves)
        # For ``stats`` alone, so counted only when asked for -- and off the
        # curves' own counts: ``keyframeCount`` walks each curve's keys, and
        # the scene exporter's calls run over thousands of dense fitted curves.
        keys_before_count = (
            sum(fn.numKeys for fn in cls._anim_curve_fns(anim_curves))
            if stats is not None
            else None
        )

        if not quiet:
            print(f"[optimize] Processing {len(anim_curves)} curves...")

        # Suspend viewport refresh for the duration of the optimization.
        # In interactive Maya this prevents per-operation viewport updates
        # that dominate wall-clock time.
        cmds.refresh(suspend=True)
        try:
            return cls._optimize_keys_inner(
                anim_curves,
                value_tolerance=value_tolerance,
                remove_flat_keys=remove_flat_keys,
                remove_static_curves=remove_static_curves,
                simplify_keys=simplify_keys,
                quiet=quiet,
                keys_before_count=keys_before_count,
                curves_before_count=curves_before_count,
                stats=stats,
                _saved_time_unit=_saved_time_unit,
                progress_callback=progress_callback,
                extremes=extremes,
            )
        finally:
            cmds.refresh(suspend=False)

    @classmethod
    def _optimize_keys_inner(
        cls,
        anim_curves,
        *,
        value_tolerance,
        remove_flat_keys,
        remove_static_curves,
        simplify_keys,
        quiet,
        keys_before_count,
        curves_before_count,
        stats,
        _saved_time_unit,
        progress_callback=None,
        extremes=False,
    ):
        # Recorded like any other edit, so one undo reverts the call: the
        # OpenMaya key edits go on the queue through UndoRecorder, and a batch
        # caller that wants no undo turns the queue off around its own run.
        # This used to run under undo_disabled(), which left Ctrl+Z to skip the
        # optimization and revert the edit BEFORE it, against the optimized
        # curves (measured 2026-09-14).
        _autokey_was_on = cmds.autoKeyframe(q=True, state=True)
        try:
            if _autokey_was_on:
                cmds.autoKeyframe(state=False)
            return cls.__optimize_keys_body(
                anim_curves,
                value_tolerance=value_tolerance,
                remove_flat_keys=remove_flat_keys,
                remove_static_curves=remove_static_curves,
                simplify_keys=simplify_keys,
                quiet=quiet,
                keys_before_count=keys_before_count,
                curves_before_count=curves_before_count,
                stats=stats,
                _saved_time_unit=_saved_time_unit,
                progress_callback=progress_callback,
                extremes=extremes,
            )
        finally:
            if _autokey_was_on:
                cmds.autoKeyframe(state=True)

    @classmethod
    def __optimize_keys_body(
        cls,
        anim_curves,
        *,
        value_tolerance,
        remove_flat_keys,
        remove_static_curves,
        simplify_keys,
        quiet,
        keys_before_count,
        curves_before_count,
        stats,
        _saved_time_unit,
        progress_callback=None,
        extremes=False,
    ):
        static_curves_deleted = 0
        flat_keys_deleted = 0
        simplified_curves_count = 0

        # Phase 1: Remove static curves (if remove_static_curves is True)
        if progress_callback:
            progress_callback(0, 4, "Removing static curves")
        if remove_static_curves:
            static_curves = cls.get_static_curves(
                anim_curves, value_tolerance=value_tolerance
            )
            static_curves_deleted += len(static_curves)
            if static_curves:
                cmds.delete(static_curves)
                # Remove deleted curves from anim_curves list
                static_set = set(static_curves)
                anim_curves = [c for c in anim_curves if c not in static_set]

        # Phase 2: Remove redundant flat keys (if remove_flat_keys is True).
        # In extremes mode the smooth curves are reduced to their extrema with
        # refit tangents instead; only the stepped curves it leaves alone
        # still go through the flat-key pass.
        if progress_callback:
            progress_callback(
                1,
                4,
                "Reducing curves to extremes" if extremes else "Removing flat keys",
            )
        rebuilt_curves = set()
        extremes_stats: dict = {}
        flat_candidates = anim_curves
        if extremes:
            reduced = cls.reduce_to_extremes(
                anim_curves,
                value_tolerance=value_tolerance,
                recursive=False,
                quiet=True,
                # The deviation measure costs an evaluate per baked sample;
                # ask for it only when it will be printed or returned.
                stats=extremes_stats if (stats is not None or not quiet) else None,
            )
            rebuilt_curves.update(reduced)  # tangents already explicit
            flat_candidates = [c for c in anim_curves if c not in rebuilt_curves]
        if remove_flat_keys and flat_candidates:
            redundant_keys_to_delete = cls.get_redundant_flat_keys(
                flat_candidates,
                value_tolerance=value_tolerance,
                remove=True,
            )
            flat_keys_deleted += sum(len(keys) for _, keys in redundant_keys_to_delete)
            # The flat pass froze what these curves need for export -- every
            # survivor of a sparse curve, a per-frame-dense one's holds'
            # boundaries -- so Phase 3 skips them unless the key reducer runs.
            rebuilt_curves.update(c for c, keys in redundant_keys_to_delete if keys)

        # Phase 3: Freeze auto tangent types to fixed.
        # Maya's auto tangent recomputes based on neighbors, which
        # produces correct results in Maya.  However FBX maps 'auto'
        # to eTangentAuto — a different algorithm that corrupts
        # sparse curves (after flat-key removal).  Freezing to 'fixed'
        # captures the exact current angle as eTangentUser.
        # This also gives the key reducer explicit angles to work with,
        # allowing it to remove more keys while preserving shape.
        #
        # A PER-FRAME-DENSE curve that lost no key is left alone unless the
        # key reducer is about to run: every tangent algorithm passes through
        # every sample, so at frame resolution 'auto' and 'fixed' describe
        # the same motion and eTangentAuto has no room to bend it.  The
        # freeze exists for SPARSE survivors -- the flat pass froze those at
        # rebuild and the extremes pass wrote theirs explicitly.  Measured:
        # freezing every tangent of every dense baked curve was 55% of a
        # flat pass (2.3 s of 4.2 s over 1130 curves x 300 keys; ~25 s at
        # 1580 x 1134).  A sparse hand-keyed curve still freezes as before.
        if progress_callback:
            progress_callback(2, 4, "Freezing tangents")
        auto_tangents_frozen = 0
        freeze_dense = simplify_keys and not extremes
        for curve in anim_curves:
            if curve in rebuilt_curves and not freeze_dense:
                # Frozen during rebuild.  Before the reducer a dense one's
                # interior is still 'auto', and the keys it keeps would face
                # the gaps it opens with an auto angle.
                continue
            if not cmds.objExists(curve):
                continue
            times = cmds.keyframe(curve, q=True, timeChange=True) or []
            if not times:
                continue
            if not freeze_dense and cls._is_per_frame_dense(times):
                continue
            out_types = cmds.keyTangent(curve, q=True, outTangentType=True) or []
            in_types = cmds.keyTangent(curve, q=True, inTangentType=True) or []

            for types_list, kw in [
                (out_types, {"outTangentType": "fixed"}),
                (in_types, {"inTangentType": "fixed"}),
            ]:
                # Group consecutive auto keys into time ranges for
                # batch keyTangent calls instead of per-key overhead.
                seg_start = None
                for i, tt in enumerate(types_list):
                    if tt == "auto":
                        if seg_start is None:
                            seg_start = i
                        auto_tangents_frozen += 1
                    elif seg_start is not None:
                        cmds.keyTangent(
                            curve,
                            time=(times[seg_start], times[i - 1]),
                            lock=False,
                            **kw,
                        )
                        seg_start = None
                if seg_start is not None:
                    cmds.keyTangent(
                        curve,
                        time=(times[seg_start], times[len(types_list) - 1]),
                        lock=False,
                        **kw,
                    )

        # Phase 4: Simplify curves (if simplify_keys is True).
        # Uses filterCurve(keyReducer) to remove keys whose absence
        # changes the curve by less than value_tolerance.  Runs after
        # auto-freeze so the reducer has explicit tangent angles.
        if progress_callback:
            progress_callback(3, 4, "Simplifying curves")
        if simplify_keys and not extremes:
            simplified = cls.simplify_curve(
                anim_curves, value_tolerance=value_tolerance
            )
            simplified_curves_count += len(simplified)

        if progress_callback:
            progress_callback(4, 4, "Done")
        if not quiet:
            print(f"[optimize] {static_curves_deleted} static curves deleted")
            print(f"[optimize] {flat_keys_deleted} flat keys removed")
            print(f"[optimize] {simplified_curves_count} curves simplified")
            print(f"[optimize] {auto_tangents_frozen} auto tangents frozen")
            if extremes:
                print(
                    f"[optimize] {extremes_stats.get('reduced', 0)} curves reduced "
                    f"({extremes_stats.get('reduce_keys_removed', 0)} tweens removed, "
                    f"max deviation {extremes_stats.get('reduce_max_error', 0.0):.6f})"
                )

        # Restore time-unit if Maya init changed it during this call.
        if cmds.currentUnit(query=True, time=True) != _saved_time_unit:
            cmds.currentUnit(time=_saved_time_unit)

        surviving = [c for c in anim_curves if cmds.objExists(c)]

        if stats is not None:
            keys_after_count = sum(fn.numKeys for fn in cls._anim_curve_fns(surviving))
            stats.update(
                {
                    "keys_before": keys_before_count,
                    "keys_after": keys_after_count,
                    "curves_before": curves_before_count,
                    "curves_after": len(surviving),
                    "static_deleted": static_curves_deleted,
                    "flat_removed": flat_keys_deleted,
                    "simplified": simplified_curves_count,
                    "auto_frozen": auto_tangents_frozen,
                    **extremes_stats,
                }
            )

        return surviving
