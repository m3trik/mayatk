# !/usr/bin/python
# coding=utf-8
"""Animation-phase export tasks: the smart bake and its restore, key
optimization / snap / tie, the bake range and clip origin, the data_export
carrier and the declared takes.
"""

import json
import math
from typing import Optional, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # the surface must import without Maya (registry, docs tooling)
    cmds = mel = None
import pythontk as ptk

# From this package:
from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.env_utils.scene_exporter._task_data import _TaskDataMixin


class _AnimationTasksMixin(_TaskDataMixin):
    """Animation-phase export tasks: the smart bake and its restore, key
    optimization / snap / tie, the bake range and clip origin, the data_export carrier and the declared takes."""

    def smart_bake(self):
        """Pre-bake constrained and driven channels before export.

        Uses SmartBake to detect objects with constraints, driven keys,
        expressions, IK, motion paths, and blend shapes, then bakes only
        those specific channels onto an override animation layer.
        FBX export with FBXExportBakeComplexAnimation samples the final
        evaluated output THROUGH layers, so the override layer produces
        correct results without deleting driver nodes. The one thing it does
        NOT evaluate per frame is a connected offsetParentMatrix with a
        non-FBX upstream (frozen at the export frame -- see SmartBake's
        matrix pass), so matrix drives are baked directly onto their plugs
        in both modes and restored by the session manifest.  After export,
        the layer is deleted to restore the original scene state
        non-destructively.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        self.logger.info("Analyzing scene for bake requirements...")
        # Honor the UI contract ("Optimize Keys … also controls key
        # optimization inside Smart Bake"): baked override-layer curves sit
        # behind animBlendNodes that listConnections can't traverse, so the
        # separate optimize_keys task can never reach them — SmartBake must
        # optimize its own output, at the same level.  run.optimize_keys_level is
        # derived per run by run_tasks (ptk.ExportRun.with_tasks, off the full
        # task dict); SmartBake resolves the token itself against
        # AnimUtils.OPTIMIZE_LEVELS.
        baker = SmartBake(
            # `_live_objects`, not the raw set: this is the first task to walk
            # every node one at a time, so it is where a path invalidated by an
            # earlier task surfaces -- as a RuntimeError out of a query, eleven
            # tasks into a run. Every other bulk consumer in this class already
            # goes through the same guard.
            objects=self._live_objects(),
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=self.run.optimize_keys_level,
            use_override_layer=True,  # Non-destructive: bake to override layer
            delete_inputs=False,  # Keep constraints — layer overrides them
        )

        analysis = baker.analyze()
        if not any(a.requires_bake for a in analysis.values()):
            self.logger.info(
                "No constrained/driven objects found. Skipping smart bake."
            )
            return

        # The bake's own session manifest reverses the LAYER, IK state and
        # visibility; the curve snapshot covers what it cannot -- anything a
        # later key task edits on the base layer. Both are governed by the one
        # Animation Output gate.
        self._protect_scene_animation()

        # Log what will be baked
        bake_count = sum(1 for a in analysis.values() if a.requires_bake)
        self.logger.info(f"Found {bake_count} objects requiring bake.")

        result = baker.bake(analysis)

        # The session manifest (SmartBake.restore reverses the layer, IK state
        # and visibility) and, for a bake recorded without one, the layer to
        # delete -- undone by _restore_bake_session, staged HERE, after the
        # animation snapshot and every earlier task's restore, so it unwinds
        # FIRST (LIFO): its matrix records reconnect offsetParentMatrix to
        # whatever drove it at bake time, which for flattened chains is the
        # flatten task's rewrap node -- a node the flatten restore deletes.
        if result.session_id:
            self._bake_session_id = result.session_id
        if result.override_layer:
            self._bake_override_layer = result.override_layer
        if result.session_id or result.override_layer:
            self.stage_deferred_restore("smart_bake", self._restore_bake_session)
        # Build detailed log message
        log_parts = [
            f"Smart bake completed: {result.baked_count} objects baked",
            f"range {result.time_range[0]}-{result.time_range[1]}",
        ]
        if result.override_layer:
            log_parts.append(f"layer '{result.override_layer}'")
        if result.optimized:
            log_parts.append(f"{len(result.optimized)} objects optimized")
        narrowed = [
            obj
            for obj, rng in result.object_time_ranges.items()
            if rng != tuple(result.time_range)
        ]
        if narrowed:
            static = sum(
                1
                for obj in narrowed
                if result.object_time_ranges[obj][0]
                == result.object_time_ranges[obj][1]
            )
            log_parts.append(
                f"{len(narrowed)} sampled over their own driver range"
                + (f" ({static} static, one frame)" if static else "")
            )

        self.logger.info(", ".join(log_parts) + ".")

        # Refresh self.objects (no deletions expected, but re-validate). The
        # objects.setter already invalidates the key-range cache, so no
        # explicit invalidation is needed here.
        self.objects = self._live_objects()

    def optimize_keys(self, level: Union[bool, str, None] = True):
        """Optimize baked animation keys at the requested level.

        Parameters:
            level: A key of ``AnimUtils.OPTIMIZE_LEVELS`` (``"static"``,
                ``"flat"``, ``"simplify"``, ``"extremes"``), ``True`` for the
                default level, or anything falsy for OFF.  The panel's Optimize
                Keys combo supplies the token; a headless caller's legacy
                ``True`` keeps behaving exactly as it did.  An unknown level
                raises out of the resolver rather than silently optimizing the
                user's curves at a setting they did not choose.
        """
        kwargs = AnimUtils.resolve_optimize_level(level)
        if not kwargs:  # OFF — a headless caller's falsy value; the panel's
            return  # own OFF row never reaches the dispatcher (b000 filters it)
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping optimization.")
            return

        self._protect_scene_animation()
        resolved = AnimUtils.normalize_optimize_level(level)
        self.logger.info(f"Optimizing baked animation keys ({resolved})...")
        # Base-layer curves only: SmartBake optimizes the override layer it
        # creates itself (the smart_bake task passes this run's level
        # through), so walking behind its blend nodes here would re-scan a
        # production bake's millions of keys, and the base curves that layer
        # overrides, for nothing. objects_to_curves walks layers by default.
        AnimUtils.optimize_keys(
            self.objects, recursive=True, quiet=True, through_blends=False, **kwargs
        )
        # Static curves may have been deleted — drop the cached key times so
        # later tasks (tie/snap/range) re-query the surviving curves.
        self._invalidate_keyframe_cache()
        self.logger.info("Optimization completed.")

    #: Bake Range sources, in the order the combo offers them.  Tokens, not
    #: labels: this is what a headless caller passes and what the task logs.
    BAKE_RANGE_MODES: Tuple[str, ...] = ("auto", "keys", "scene")

    def _require_range_coverage(self, start, end) -> None:
        """Claim a frame span the export's bake range MUST cover.

        The seam every claimant uses instead of writing the range itself.
        ``set_bake_animation_range`` runs last and widens to the union of every
        claim, so a task that stages animation the write has to carry cannot
        have its span silently clipped by whichever range source the user
        picked -- and a future claimant registers here rather than editing the
        range task.

        Two claimants today: the declared takes (a shot can outrun the last
        keyframe, and shipping metadata for a clip the file truncates is wrong
        in every deliverable at once) and, in blendertk, the staged
        keyed-weight curve proxies (whose keys sit outside the exported
        objects' own extent by construction).
        """
        current = self._required_range_coverage
        if current is None:
            self._required_range_coverage = (start, end)
        else:
            self._required_range_coverage = (
                min(current[0], start),
                max(current[1], end),
            )

    def _bake_range_from_shots(self) -> Optional[Tuple[int, int]]:
        """The union of the scene's declared shots, or None when there are none.

        Reads the ShotStore, never the published ``data_export`` carrier: the
        carrier is a projection, and refreshing it to answer a question about
        RANGE would stamp a metadata node as a side effect of computing a
        number -- on scenes where the user deliberately switched that off.
        ``declared_range`` rounds through the same ``resolve_clip_specs`` the
        export view uses, so this range and the published ``fbx_takes`` cannot
        disagree about a fractional shot boundary.
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        return ShotStore.declared_range()

    def _bake_range_from_keys(self) -> Optional[Tuple[int, int]]:
        """The exported subtree's first/last keyframe, or None when it has none.

        Measures the whole exported SUBTREE (:meth:`_exported_objects`, through
        :meth:`_keyframe_range`), not just the named nodes: the write ships
        descendants, and on a hierarchy export the animation is on them.
        Reading the shallow scope made this skip itself on a fully animated
        production assembly and leave the plugin's factory 1-48 range to ship
        in its place.

        Fractional bookend keys are ENCLOSED -- floor the start, ceil the end
        -- so keys like -0.5 or 100.6 are not truncated inward as int() does.
        """
        span = self._keyframe_range()
        if not span:
            return None
        return math.floor(span[0]), math.ceil(span[1])

    def _bake_range_from_scene(self) -> Tuple[int, int]:
        """The scene's authored animation range.

        Reads through ``AnimUtils.scene_animation_range`` -- the one definition
        of "the authored extent", shared with
        :meth:`FbxUtils.set_bake_range_from_scene`, which is the reading the
        auto-export hook takes by default and which this row exists to let the
        panel ask for too.
        """
        start, end = AnimUtils.scene_animation_range()
        return math.floor(start), math.ceil(end)

    def publish_clip_origin(self) -> None:
        """Publish the clip origin from the animation the write will CARRY.

        The published ``clip_span["*"]`` is the authoring frame the exported
        stack puts at its own ``t=0``, and every GLB clip is cut against it
        (``MeshConvert._clip_zero``).

        Called from the EXPORT BRACKET -- after ``FbxUtils.begin_export`` and
        before the write -- and deliberately not from a task. The bracket
        re-runs every producer's preparer, and the visibility producer
        republishes its whole channel from scratch, seeding ``*`` from
        whatever bake range it happens to find. A value published by a task,
        however late in TASK_ORDER, is therefore overwritten before the file
        is written: being the last writer is what makes this correct, and this
        is the only position that guarantees it. Publishing it from the last
        task instead shipped the PROPS assembly's 18 shots cut 81 frames early
        three exports running, with a log line each time saying the right
        number had been published.

        Only the pipeline can answer this: the producer is a no-arg preparer
        with no export set and no view of the final curves, while this
        measures :meth:`_bake_range_from_keys` over the objects that ship,
        after every task that edits keys.

        Measured from the KEYS, never from the bake range. The two answer
        different questions: the bake range bounds what the plugin RE-BAKES
        (constrained / IK / expression-driven nodes), while a plainly keyed
        curve -- which is what every animation task upstream leaves behind --
        is written whole. Measured on Maya 2025 / FBX 2020.3.6: a curve keyed
        0-100 exports as 0-100 under a 20-80 bake range, with
        ``FBXExportBakeResampleAnimation`` off AND on. Publishing the bake
        range as the stack's span therefore describes a file that was never
        written. On the PROPS assembly it claimed 161-4275 over a stack
        carrying 80-4281, so every clip was cut 81 frames early and played the
        tail of the shot before it, while the visibility gates -- which ARE
        written against the published span -- switched on time.

        Measuring also subsumes the defect ``restamp_stack_span`` was added
        for: reading the preset's untouched ``[0, 10000]`` slid every clip by
        the bake start. Sourcing the number from the content rather than from
        any range setting closes that class instead of narrowing it.

        Silent when the export set carries no keys: there is no stack, and no
        span to describe one.
        """
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        carried = self._bake_range_from_keys()
        if carried is None:
            return
        if RenderEffects.restamp_stack_span(*carried):
            # INFO, beside the bake-range line, because these are the two
            # numbers this task produces and they are the two that were
            # confused. The range was logged and the origin was not, so three
            # exports read as correct while every clip was cut against a frame
            # nobody could see.
            self.logger.info(
                f"Clip origin published as {carried[0]:g}-{carried[1]:g} "
                "(exported keyframe extent — the frame every GLB clip is cut "
                "against, not the bake range)."
            )

    def publish_clip_mode(self) -> None:
        """Declare the run's Animation Clips mode on the ``shot_metadata`` envelope.

        ``fbx_takes`` lists the scene's shots in every mode, so a Full Sequence
        Only file carries one stack beside takes naming every shot -- which the
        deliverable gates read as missing takes unless the mode is on the record
        (``ExportVerifier`` reads ``MeshConvert.SHOT_CLIP_MODE_KEY``; measured:
        ``fbx_takes`` failed a correct full-mode export "declared but absent").
        Declared, never inferred: one stack alone is also what a split that
        silently failed leaves.

        Called from the export bracket right after :meth:`publish_clip_origin`,
        for the same reason: the bracket's preparers republish ``shot_metadata``
        from scratch, so only the last writer's value ships. A scene with no shots publishes no envelope, and nothing is
        declared.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        raw = DataNodes.get_export_string(DataNodes.SHOT_METADATA)
        try:
            meta = json.loads(raw) if raw else None
        except ValueError:
            return
        if not isinstance(meta, dict):
            return
        meta[ptk.MeshConvert.SHOT_CLIP_MODE_KEY] = self._clip_mode
        DataNodes.set_export_json(DataNodes.SHOT_METADATA, meta)

    def set_bake_animation_range(self, mode: Union[bool, str, None] = "auto"):
        """Set the FBX bake range from the selected source, if baking is on.

        The ONE task that owns the bake range.  It used to share it with
        ``apply_declared_takes``, which set a shot union as an undeclared side
        effect of SPLITTING -- so the only way to clamp an export to its shots
        was to arm a take split you might not want (a GLB deliverable never
        does; its clips are rebuilt from the whole stack), and which of the two
        won was decided by TASK_ORDER rather than by anything the user could
        see.  This task now runs LAST, and the split task's job is only to
        split.

        Every mode then WIDENS to cover any takes realized this run, so no
        source can write a range that clips a clip the same export declared --
        a file whose metadata describes animation it does not contain is the
        one outcome that is wrong in both deliverables at once.  A shot may
        legitimately outrun the last keyframe (a hold authored on the
        sequencer), which is exactly when a raw override would do that.

        Parameters:
            mode: ``"auto"`` (shot union, falling back to the keyframe extent
                when the scene declares no shots), ``"keys"`` (keyframe
                extent), ``"scene"`` (the scene's authored animation range), or
                anything falsy for OFF -- keep whatever range the FBX preset
                carries.  A legacy ``True`` reads as ``"keys"``, the behavior
                this task had when it was a checkbox.

        Notes:
            The prior range is captured and staged for deferred restore before
            the write.  Without it the range was sticky global exporter state:
            an export left its measurement armed for every later export in the
            session, including hand-driven ones through Maya's own dialog.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        if not mode:  # OFF — as optimize_keys: the panel filters its own OFF
            return  # row out, so this is a headless caller's falsy value
        mode = "keys" if mode is True else str(mode).strip().lower()
        if mode not in self.BAKE_RANGE_MODES:
            raise ValueError(
                f"Unknown bake range mode {mode!r}; expected one of "
                f"{', '.join(self.BAKE_RANGE_MODES)}."
            )

        if not FbxUtils.baking_enabled():
            self.logger.info(
                "Baking complex animation is disabled. Skipping frame range setting."
            )
            return

        if mode == "auto":
            resolved, source = self._bake_range_from_shots(), "shot union"
            if resolved is None:
                resolved = self._bake_range_from_keys()
                source = "keyframe extent (no shots declared)"
        elif mode == "keys":
            resolved, source = self._bake_range_from_keys(), "keyframe extent"
        else:
            resolved, source = self._bake_range_from_scene(), "scene animation range"

        # Never clip a span another task claimed (:meth:`_require_range_coverage`),
        # whatever the selected source measured.
        required = self._required_range_coverage
        if required:
            if resolved is None:
                resolved, source = required, "required coverage"
            else:
                widened = (
                    min(resolved[0], required[0]),
                    max(resolved[1], required[1]),
                )
                if widened != resolved:
                    resolved = widened
                    source += ", widened to cover the required span"

        if resolved is None:
            self.logger.debug(
                f"Nothing to measure for bake range mode {mode!r}. Skipping."
            )
            return

        start, end = int(math.floor(resolved[0])), int(math.ceil(resolved[1]))
        # Capture BEFORE the write, and stage rather than revert-pair: the
        # write itself has to read this range, so a revert that runs when
        # run_tasks returns would undo it before the export.  Staging is
        # first-wins and unwinds LIFO, so with apply_declared_takes' own
        # "fbx_takes" restore also staged (earlier, since it runs first) the
        # pair composes back to the true pre-run state.
        prior_start = mel.eval("FBXExportBakeComplexStart -q")
        prior_end = mel.eval("FBXExportBakeComplexEnd -q")

        def _restore_bake_range() -> None:
            mel.eval(f"FBXExportBakeComplexStart -v {prior_start}")
            mel.eval(f"FBXExportBakeComplexEnd -v {prior_end}")

        self.stage_deferred_restore("bake_range", _restore_bake_range)

        mel.eval(f"FBXExportBakeComplexStart -v {start}")
        mel.eval(f"FBXExportBakeComplexEnd -v {end}")
        self.logger.info(f"Set bake range to {start}-{end} ({source}).")

    def tie_all_keyframes(self):
        """Use AnimUtils to tie all keyframes for the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping tie operation.")
            return

        self._protect_scene_animation()
        self.logger.info("Tying keyframes for all objects.")

        # The bookends' range, from the curves' ends (cached), not a key list.
        custom_range = self._keyframe_range()

        # The exported subtree, for the reason snap_keys_to_frame names: this
        # helper takes an explicit object list and does not recurse.
        AnimUtils.tie_keyframes(
            self._exported_objects(), absolute=True, custom_range=custom_range
        )
        self.logger.info("Keyframes have been tied.")

    def snap_keys_to_frame(self):
        """Snap all keyframes to the nearest whole frame."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping snap operation.")
            return

        self._protect_scene_animation()
        self.logger.info("Snapping keyframes to nearest whole frame.")
        # The exported subtree, not the named roots: this helper takes an
        # explicit object list and does not recurse, so handing it the export
        # set would snap nothing on a hierarchy export.
        # Base-layer keys only, like optimize_keys: a baked layer is already on
        # whole frames, and the write resamples every layer per frame anyway.
        AnimUtils.snap_keys_to_frames(self._exported_objects(), through_blends=False)
        # Key times just changed — a stale cache would make tie_all_keyframes
        # re-insert the fractional bookends this task removed.
        self._invalidate_keyframe_cache()
        self.logger.info("Keyframes have been snapped.")

    def export_data_node(self):
        """Include the shared ``data_export`` carrier in the export (default on).

        ``data_export`` is the single node every metadata system stamps
        (Shots → ``shot_metadata`` + ``fbx_takes``; Audio → ``audio_manifest``;
        …).  The ``visible`` mode's object set is geometry-only and the
        ``selected`` mode ships only what the user picked, so in both the
        carrier would silently never ship.  This refreshes the carrier from the
        live producers, then appends it to the export set so the data rides
        into the FBX regardless of export mode — independent of any one
        subsystem, so a scene with only audio still carries its manifest.
        """
        self._refresh_scene_data_node()
        self._data_node_refreshed = True
        self._include_data_export_node()
        self._log_data_node_summary()

    def _log_data_node_summary(self):
        """Log what metadata actually shipped on ``data_export``.

        Makes a silently-empty export distinguishable from a populated one — the
        single most useful signal that the carrier reached the FBX with content.
        Channel-agnostic: every user-defined string attr on the carrier is
        summarized by entry count (JSON array / dict-of-list / whitespace-token
        wire string), so new producers show up with no exporter edits.  Pure
        logging convenience — fully best-effort so it can never abort the export.
        """
        try:
            import json
            from mayatk.node_utils.data_nodes import DataNodes

            def entry_count(raw: str) -> int:
                try:
                    data = json.loads(raw)
                except ValueError:
                    return len(raw.split())  # wire strings, e.g. "frame:label …"
                if isinstance(data, list):
                    return len(data)
                if isinstance(data, dict):
                    for value in data.values():
                        if isinstance(value, list):
                            return len(value)
                return 1

            # dump() owns channel discovery (and the duplicate-name
            # tie-break); non-string channels (keyable weight floats) are
            # skipped here just as the raw type check used to.
            parts = []
            channels = DataNodes.dump(decode=False).get(DataNodes.EXPORT) or {}
            for attr, raw in channels.items():
                if isinstance(raw, str) and raw:
                    n = entry_count(raw)
                    parts.append(f"{attr} ({n} entr{'y' if n == 1 else 'ies'})")

            if parts:
                self.logger.info("Embedded on data_export: " + ", ".join(parts) + ".")
        except Exception:  # a summary must never break the export it describes
            self.logger.debug("data_export summary skipped.", exc_info=True)

    def _include_data_export_node(self):
        """Append the ``data_export`` carrier(s) to the export set.

        Idempotent: a no-op when no carrier exists (nothing to ship) and skips
        any already in the set.  Shared by :meth:`export_data_node` and
        :meth:`apply_declared_takes`.

        EVERY carrier, not just the canonical one: an assembly's referenced
        modules publish onto their own ``NS:data_export``, so shipping the root
        alone dropped a referenced module's lightmap manifest from the
        deliverable (see ``DataNodes.get_export_nodes``).
        """
        from mayatk.node_utils.data_nodes import DataNodes

        nodes = DataNodes.get_export_nodes()
        if not nodes:
            self.logger.debug("No data_export node in scene — nothing to include.")
            return
        added = [n for n in nodes if n not in (self.objects or [])]
        if added:
            self.objects = list(self.objects or []) + added
            self.logger.info(
                f"data_export carrier(s) added to the export set: {len(added)}."
            )

    def _refresh_scene_data_node(self):
        """Refresh ``data_export`` channels from the live metadata producers.

        Delegates to :meth:`FbxUtils.run_export_preparers` — the single
        producer registry (session preparers + known producers), so a new
        metadata system ships without touching the exporter.  Each producer
        no-ops when it has nothing to write (no shots / no audio carrier),
        leaving no node behind in a metadata-free scene, and is isolated so
        an absent or erroring subsystem never blocks the export.
        """
        try:
            from mayatk.env_utils.fbx_utils import FbxUtils

            FbxUtils.run_export_preparers()
        except Exception:
            self.logger.debug("data_export refresh skipped.", exc_info=True)

    @classmethod
    def _animation_clips_mode(cls, mode) -> str:
        """Resolve a row value to one of ``ANIMATION_CLIP_MODES``.

        Accepts the boolean a pre-combo preset stored, the same way
        :meth:`set_bake_animation_range` accepts the one ITS checkbox left
        behind -- a stored preset is a contract, and a widget-type change must
        not silently re-point it at a different deliverable. ``True`` kept the
        whole-timeline stack beside the split takes, so it is ``both``; every
        FALSY value (the unticked box, and the ``None`` a headless caller
        passes for OFF) split nothing and shipped the sequence alone, so it is
        ``full``.
        """
        if not mode or isinstance(mode, bool):
            return "both" if mode else "full"
        resolved = str(mode).strip().lower()
        if resolved not in ptk.MeshConvert.ANIMATION_CLIP_MODES:
            raise ValueError(
                f"Unknown animation clips mode {mode!r}; expected one of "
                f"{', '.join(ptk.MeshConvert.ANIMATION_CLIP_MODES)}."
            )
        return resolved

    def apply_declared_takes(self, mode: Union[bool, str, None] = "both"):
        """Ship the declared shots, the whole sequence, or both.

        Producer-agnostic: refreshes every producer's ``data_export`` channel
        (skipped when ``export_data_node`` already did so this run — the two
        tasks are default-on neighbors, and one refresh per export is enough),
        then realizes whatever ``fbx_takes`` the scene declares into FBX export
        state, folding the carrier into the export selection with them.  Runs
        after ``set_bake_animation_range`` so its union range wins.  A scene
        that declares no takes is a true no-op: nothing is armed and nothing
        joins the export set.

        **This is the FBX/Unity leg only.**  The GLB does not take its clips
        from here: Maya's split is lossy — it restricts each curve to the
        take's window before baking, so a curve with no key inside a shot
        contributes no channel to it — and
        ``ptk.MeshConvert.apply_glb_clips`` therefore REBUILDS the declared
        clips from the whole-timeline stack the same export retains, which is
        baked per frame and measured correct on all of it.  The task still
        earns its place on a GLB export: it is what sets the bake range to the
        union of the declared shots, so the stack the rebuild slices covers
        exactly the shots and no more.

        The GLB half of the choice is carried to the conversion rather than
        acted on here (:meth:`create_glb` passes it as ``clip_mode``): the
        sequence has to be CUT before it can be dropped, so which clips survive
        is decided on the deliverable, not in the scene.

        Parameters:
            mode: ``"both"`` (shots + the whole-timeline sequence),
                ``"shots"``, or ``"full"``. A legacy ``True``/``False`` from a
                preset written when this row was a checkbox maps onto
                ``"both"``/``"full"`` respectively.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        mode = self._animation_clips_mode(mode)
        # Read by create_glb, after the FBX is written. Set even on the paths
        # that return early: the GLB is converted whether or not a take was
        # ever realized, and it still has to know which clips to keep.
        self._clip_mode = mode

        if not self._data_node_refreshed:
            self._refresh_scene_data_node()

        if mode == "full":
            # No split: the FBX ships its whole-timeline take, and the
            # converter is told to keep only that. The declared shots still
            # ride on the carrier as METADATA -- they describe the scene, and
            # extras.animation_web publishes each one's frame range so a player
            # can seek the window inside the single clip.
            self.logger.info(
                "Animation clips: shipping the full sequence only; the "
                "declared shots are published as metadata, not as clips."
            )
            return

        count = FbxUtils.apply_takes_from_node()
        if count:
            # The carrier ships WITH the clips, never instead of them: its
            # metadata names each shot by take name, so it is folded in only
            # once takes were realized. Ordering is load-bearing now that this
            # task is default-on -- included unconditionally, it handed the
            # carrier back to a user who had deliberately unchecked "Export
            # Scene Data Node", on a scene with no shots at all.
            self._include_data_export_node()
            # Take splits + bake-complex are sticky global FBX exporter state
            # that must live THROUGH the write, so the cleanup is staged
            # deferred (post-write) rather than revert-paired.  Without it, a
            # session with no auto-export hook installed kept the splits and
            # bake range armed for every later export.  Idempotent alongside
            # the hook's own kAfterExport reset.
            self.stage_deferred_restore("fbx_takes", FbxUtils.reset_takes)
            # The union apply_takes just wrote, read back as ground truth
            # rather than recomputed, and CLAIMED: set_bake_animation_range runs
            # after this and widens to cover every claim, so no bake range can
            # clip a clip this export declared.  Read, not derived, so the two
            # cannot disagree.
            realized = FbxUtils.bake_range()
            if realized:
                self._require_range_coverage(*realized)
            self.logger.info(
                f"Animation takes: {count} clip(s) realized from the declared "
                "fbx_takes; shot metadata embedded on data_export."
            )
        else:
            self.logger.debug("No takes declared. Skipping animation takes.")

    def _restore_bake_session(self) -> None:
        """Undo :meth:`smart_bake`'s session -- the restore that task stages.

        Animation Output at Scene Keys (In Place) keeps the bake: the override
        layer and its curves stay (the manifest stays recorded, so a manual
        ``SmartBake.restore('<id>')`` remains available), and only the
        matrix-driven channels are handed back to their live drivers -- their
        keys were written in the flatten task's staged parent space, and the
        flatten restore that runs after this reinstates the original
        offsetParentMatrix wiring, which composed on top of kept keys would
        double-transform those nodes. Otherwise the session manifest is
        restored (layer deleted, IK handles re-enabled, baked visibility put
        back); a bake recorded without one falls back to deleting its
        override layer. Never raises: a failure is logged, and every other
        staged restore still runs.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        session, layer = self._bake_session_id, self._bake_override_layer
        self._bake_session_id = self._bake_override_layer = None
        if self.run.animation_write_back:
            self.logger.info(
                f"Bake kept in the scene (session '{session}') — Animation "
                "Output is set to Scene Keys (In Place)."
            )
            if session:
                try:
                    handed = SmartBake.restore_matrix_wiring(session)
                    if handed.matrix_restored:
                        self.logger.info(
                            "Matrix-driven channels handed back to their live "
                            f"drivers ({len(handed.matrix_restored)} object(s)) "
                            "— baked matrix keys cannot survive the hierarchy "
                            "restore."
                        )
                except Exception as e:  # noqa: BLE001 -- a restore never raises
                    self.logger.error(f"Matrix-wiring restore failed: {e}")
            return
        if session:
            try:
                restore = SmartBake.restore(session)
                if restore.success:
                    self.logger.info(
                        f"Restored pre-bake scene state (session '{session}')."
                    )
                    layer = None
                else:
                    # SmartBake reports non-restorable sessions via
                    # cmds.warning only (script editor) — surface it in the
                    # export log too, with the session id so a manual retry is
                    # possible. The layer-delete fallback below still runs,
                    # but IK blend and visibility may need that manual restore.
                    self.logger.warning(
                        f"SmartBake restore failed for session '{session}' — "
                        "the bake override layer is deleted as a fallback, but "
                        "IK/visibility state may need a manual "
                        f"SmartBake.restore('{session}')."
                    )
            except Exception as e:  # noqa: BLE001 -- a restore never raises
                self.logger.error(f"SmartBake restore failed: {e}")
        if layer:
            try:
                if cmds.objExists(layer):
                    cmds.delete(layer)
                    self.logger.info(
                        f"Deleted bake override layer '{layer}' — scene restored."
                    )
            except Exception as e:  # noqa: BLE001 -- see above
                self.logger.error(
                    f"Bake override layer '{layer}' could not be deleted: {e}"
                )
