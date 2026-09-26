# !/usr/bin/python
# coding=utf-8
"""Animation-phase export tasks: the smart bake and its restore, key
optimization / snap / tie, the bake range and clip origin, the data_export
carrier and the declared takes.
"""

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
        level = self.run.optimize_keys_level
        if level and self._write_resamples_layers():
            self.logger.info(
                "Smart bake: its own key optimization is skipped -- this FBX "
                "write splits the declared takes, which re-samples the override "
                "layer per frame, so reduced layer keys would never ship."
            )
            level = False
        baker = SmartBake(
            # `_live_objects`, not the raw set: this is the first task to walk
            # every node one at a time, so it is where a path invalidated by an
            # earlier task surfaces -- as a RuntimeError out of a query, eleven
            # tasks into a run. Every other bulk consumer in this class already
            # goes through the same guard.
            objects=self._live_objects(),
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=level,
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

    def _write_resamples_layers(self) -> bool:
        """Whether this run's FBX write re-samples the bake's override layer
        per frame, so a key reduction inside the layer could never ship: the
        write splits the declared takes (``ExportRun.splits_takes`` over a scene
        that declares some -- ``FbxUtils.declared_takes``, the read
        :meth:`apply_declared_takes` realizes them from), a split forces
        ``FBXExportBakeComplexAnimation`` over the shot union, and nothing is
        written back to the scene.

        A wrong answer costs FIDELITY, not just file size, so answering it is
        worth the four reads: the write bakes the layer either way, but
        reducing the layer FIRST and the scene's curves after it thins the same
        motion twice.  Measured against the production rig itself (4 clips x 40
        nodes x 40 frames, the frames the two exports disagree on most): with
        this skip the shipped worst-case orientation error is 1.35 deg against
        4.56 without it, and the sample is closer in 1040-1564 of every 1600 --
        so the skip is the faithful path, not merely the smaller one."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        return bool(
            self.run.splits_takes
            and not self.run.usd
            and not self.run.animation_write_back
            and FbxUtils.declared_takes()
        )

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
        export view uses, so this range and the published takes cannot
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
        self._stage_bake_range_restore()
        mel.eval(f"FBXExportBakeComplexStart -v {start}")
        mel.eval(f"FBXExportBakeComplexEnd -v {end}")
        self.logger.info(f"Set bake range to {start}-{end} ({source}).")

    def _stage_bake_range_restore(self) -> None:
        """Stage the FBX bake range's restore to the range it holds NOW.

        Captured before the write, and staged rather than revert-paired: the
        write has to read the range, so a revert when ``run_tasks`` returns
        would undo it before the export.  First-wins keying means the EARLIEST
        caller's capture is the one restored, so :meth:`apply_declared_takes`
        captures before ``apply_takes`` sets the shot union.  Captured only by
        :meth:`set_bake_animation_range`, after it, the restore wrote the union
        back whenever the auto-export hook's ``reset_takes`` had already
        consumed the pre-takes capture mid-write (2026-09-24).
        """
        if "bake_range" in self._deferred_restores:
            return
        from mayatk.env_utils.fbx_utils import FbxUtils

        # Nothing has necessarily loaded fbxmaya yet -- a USD route applies no
        # FBX options -- and the query is its command.
        FbxUtils.load_plugin()
        prior_start = mel.eval("FBXExportBakeComplexStart -q")
        prior_end = mel.eval("FBXExportBakeComplexEnd -q")

        def _restore_bake_range() -> None:
            mel.eval(f"FBXExportBakeComplexStart -v {prior_start}")
            mel.eval(f"FBXExportBakeComplexEnd -v {prior_end}")

        self.stage_deferred_restore("bake_range", _restore_bake_range)

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
        """Publish the scene records and ship the carrier (default on).

        The ONE publish of an export: every producer in ``FbxUtils.PRODUCERS``
        runs here, in dependency order, with the run's decisions as INPUT --
        the Animation Clips mode from the run, the clip origin measured from
        the keys the export will carry -- and the carrier is committed once.
        This task sits after every key-editing task, so what it measures is
        what ships, and nothing later patches a record: the exporter used to
        publish the clip origin and the clip mode AFTER the producers, and a
        second producer run before the write overwrote both (measured on the
        PROPS assembly, three exports cut 81 frames early while logging the
        right number).  Then the carrier(s) join the export set so the records
        ride into the FBX regardless of export mode -- the ``visible`` set is
        geometry-only and ``selected`` ships only what the user picked.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        # Stage the write NOW (the curve-proxy transport; a preview that must
        # detach): the checks after this task and the hierarchy baseline the
        # write records must see the same nodes, and producers read the staged
        # scene. The bracket stages again (idempotent) and finishes after the
        # write -- but a run that stops before the bracket opens (a declined
        # failed check, an empty export set, a cancel, a raising task) never
        # reaches it, so the finish is also a deferred restore, run on every
        # exit. Finishing twice is safe: each stager's finish is idempotent.
        table = FbxUtils.stage()
        self.stage_deferred_restore(
            "export_stagers", lambda: FbxUtils._run_stagers("finish", table)
        )
        # Carriers that already exist join the export set BEFORE the clip span
        # is measured: their keyed weights (emissive groups) ship in the stack
        # too. A carrier the publish creates joins after it.
        self._include_data_export_node()
        self._scene_snapshot = self._publish_scene_records()
        self._include_data_export_node()
        self._log_data_node_summary()

    def ensure_scene_records_published(self):
        """Publish once if no task did: the bracket's fallback for a run with
        the carrier tasks off, so an ``all``-mode export never ships a carrier
        whose records predate the artist's last edit.  What it left out is
        logged as the carrier task's publish logs it
        (:meth:`_log_snapshot_notes`): with that task off, this is the run's
        only publish, and the only place a stale shot is named."""
        if self._scene_snapshot is None:
            self._scene_snapshot = self._publish_scene_records()
            self._log_snapshot_notes()

    def _publish_scene_records(self, only=None):
        """``FbxUtils.publish`` with THIS run's context.

        The clip span is measured from the keys the export will carry
        (:meth:`_bake_range_from_keys`), never taken from the bake range: the
        range bounds what the plugin RE-BAKES, while a plainly keyed curve is
        written whole (measured on Maya 2025 / FBX 2020.3.6: a curve keyed
        0-100 exports as 0-100 under a 20-80 bake range).  Never raises -- a
        record that cannot be produced is logged and left as stored.

        Outside the write's bracket the publish PREPARES the session stagers
        (a shadow preview stands down so no producer reads it), and a run that
        stops before its write -- a declined check, a cancel -- never reaches
        the bracket that finishes them; so their finish is staged here too,
        under the key :meth:`export_data_node` stages it by (first wins), as
        blendertk's mirror does.  A finish tolerates running twice.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        try:
            ctx = FbxUtils.export_context(
                clip_mode=ptk.ExportRun.clip_mode(self.run.animation_clips_mode),
                clip_span=self._bake_range_from_keys(),
                # The FBX's handoff record publishes the same lighting recipe
                # the GLB's envelope does, with this run's choices.
                rendering=self.run.rendering,
            )
            if not FbxUtils._export_depth:
                staged = dict(FbxUtils._session_stagers)
                self.stage_deferred_restore(
                    "export_stagers", lambda: FbxUtils._run_stagers("finish", staged)
                )
            return FbxUtils.publish(ctx, only=only)
        except Exception:  # noqa: BLE001 - the write goes on; say what ships
            self.logger.warning(
                "Scene records not published; the carrier ships as last stored.",
                exc_info=True,
            )
            return None

    #: The panel that acts on a record's export notes: record key -> (link
    #: label, panel name).  The note's link opens it (``action://show``, which
    #: the Scene Exporter panel handles) -- the Shots window's All Shots group
    #: holds Delete Stale Shots.
    NOTE_PANELS = {ptk.SceneRecords.SHOTS.key: ("Open Shots", "shots")}

    def _log_data_node_summary(self):
        """Log what this run published on ``data_export`` -- the snapshot's
        own summary, so a silently-empty export is distinguishable from a
        populated one -- and what a producer left out of it
        (:meth:`_log_snapshot_notes`).  Best-effort: never aborts the export."""
        snapshot = self._scene_snapshot
        if snapshot is None:
            return
        try:
            summary = snapshot.summary()
            if summary:
                self.logger.info(f"Embedded on data_export: {summary}.")
        except Exception:  # a summary must never break the export it describes
            self.logger.debug("data_export summary skipped.", exc_info=True)
        self._log_snapshot_notes()

    def _log_snapshot_notes(self):
        """Log, as warnings, what a producer left out of this run's publish
        (``ExportSnapshot.noted``: the stale shots a copied scene still holds),
        each with a link to the panel that acts on it (:attr:`NOTE_PANELS`).
        Every publish path calls it -- the carrier task's summary and the
        fallback publish of a run with that task off.  Best-effort: never
        aborts the export."""
        snapshot = self._scene_snapshot
        if snapshot is None:
            return
        try:
            for key, notes in snapshot.noted.items():
                link = self._note_link(key)
                for note in notes:
                    self.logger.warning(f"{note} {link}" if link else note)
        except Exception:  # a note must never break the export it describes
            self.logger.debug("data_export notes skipped.", exc_info=True)

    def _note_link(self, key: str) -> str:
        """The link to the panel that acts on *key*'s notes, or ``""`` (none
        declared, or a plain logger that builds no links)."""
        entry = self.NOTE_PANELS.get(key)
        build = getattr(self.logger, "log_link", None)
        if entry is None or build is None:
            return ""
        label, panel = entry
        return build(label, "show", ui=panel)

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

    def apply_declared_takes(self, mode: Union[bool, str, None] = "both"):
        """Ship the declared shots, the whole sequence, or both.

        Producer-agnostic: publishes the scene records once when no task has
        this run (``ensure_scene_records_published`` -- ``export_data_node``
        normally has), then realizes the takes the shot record declares
        (``ptk.SceneRecords.declared_takes``: the clips' own ranges, else a
        legacy ``fbx_takes``) into FBX export state, folding the carrier into
        the export selection with them.  Runs BEFORE
        ``set_bake_animation_range``, which widens whatever range it sets to
        cover the union these takes claim.  A scene that declares no takes is
        a true no-op: nothing is armed and nothing joins the export set.

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

        mode = ptk.ExportRun.clip_mode(mode)
        # Read by create_glb, after the FBX is written. Set even on the paths
        # that return early: the GLB is converted whether or not a take was
        # ever realized, and it still has to know which clips to keep.
        self._clip_mode = mode

        # The carrier task is off: publish here, once, so the takes realized
        # below are the ones the carrier declares.
        self.ensure_scene_records_published()

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

        # The range as it stands, before apply_takes sets the shot union.
        self._stage_bake_range_restore()
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
                "takes; shot metadata embedded on data_export."
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
