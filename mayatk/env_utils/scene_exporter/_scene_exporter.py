# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # the surface must import without Maya (registry, docs tooling)
    cmds = mel = None

import os
import time
import contextlib
import shutil
import logging
from typing import List, Dict, Optional, Callable, Union, Any

import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.usd import UsdUtils
from mayatk.env_utils.scene_exporter.task_manager import TaskManager

#: What a caller of a retired naming input does instead: fold it into the
#: Output Filename pattern, as :meth:`SceneExporter.perform_export` does once.
_FOLD_FIRST = (
    "Fold it into the pattern first: ptk.ExportProfile.fold_legacy_naming("
    "pattern, version_format, timestamp, name_regex)."
)


class SceneExporter(ptk.SceneExporterBase):
    """Maya's Scene Exporter: the export write, its scope and its FBX /
    USD options over ``ptk.SceneExporterBase`` (progress, consent, run
    config and the log file, shared with blendertk)."""

    TASK_MANAGER_CLASS = TaskManager
    KEPT_EDITS_ADVICE = (
        "An export records none for undo — revert to the saved file if "
        "that is not what you want."
    )

    def _saved_scene_path(self) -> str:
        """The open scene's saved path (``EnvUtils.saved_scene_path``)."""
        return EnvUtils.saved_scene_path()

    def _initialize_objects(
        self, objects: Optional[Union[List[str], Callable]]
    ) -> List:
        """Initialize objects for the scene, including all descendants that will be exported."""
        from maya import cmds
        from mayatk.cam_utils._cam_utils import CamUtils

        if objects is None:
            self.logger.debug(
                "No objects provided. Defaulting to all transforms in the scene."
            )
            objects = cmds.ls(selection=True, long=True)
        elif callable(objects):
            self.logger.debug(
                "Callable provided for objects. Resolving objects dynamically."
            )
            objects = objects()
        else:
            self.logger.debug("Static list or query provided for objects. Validating.")

        # Use cmds.ls to ensure we have a list of full path strings
        # This handles nodes, strings, or mixed lists
        objs = cmds.ls(objects, long=True, flatten=True) or []

        # Exclude default Maya cameras from export
        default_cams = CamUtils.DEFAULT_CAMERAS
        filtered = []
        for obj in objs:
            short_name = obj.rsplit("|", 1)[-1]
            if short_name in default_cams:
                shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
                if any(cmds.nodeType(s) == "camera" for s in shapes):
                    continue
            filtered.append(obj)

        excluded_count = len(objs) - len(filtered)
        if excluded_count:
            self.logger.debug(
                f"Excluded {excluded_count} default camera(s) from export."
            )
        objs = filtered

        if hasattr(self, "task_manager"):
            self.task_manager.objects = objs

        self.logger.info(f"{len(objs)} object(s) prepared for export.")
        return objs

    def _get_preset_dir(self) -> Optional[str]:
        """The FBX preset directory — Maya's user app directory, always.

        Fixed rather than user-configurable (the old Set / Use Default
        directory pair), mirroring blendertk's fixed per-user preset store:
        one less setting to drift, and the panels stay 1:1.
        """
        try:
            return EnvUtils.get_env_info("user_app_path")
        except (KeyError, ValueError):
            return None

    #: Where Maya itself keeps FBX presets, relative to the scan root. The scan
    #: root is the whole user app directory (``Documents/maya``), which is the
    #: right thing to SEARCH -- Maya's own editor saves into a versioned
    #: subfolder of this, and artists keep presets loose in it -- but the wrong
    #: place to WRITE: a restored preset landed loose in the app dir root,
    #: beside prefs and scripts, which is what "a copy of the preset at root"
    #: was. Loading is by absolute path, so the version subfolder is Maya's
    #: business and not ours.
    _PRESET_SUBDIR = os.path.join("FBX", "Presets")

    def _preset_write_dir(self) -> Optional[str]:
        """Where a preset this panel restores should be written.

        Inside the scan root, so it is found afterwards, but in Maya's own
        preset folder rather than loose at the top of the user app directory.
        """
        root = self._get_preset_dir()
        return os.path.join(root, self._PRESET_SUBDIR) if root else None

    def _invalidate_preset_cache(self) -> None:
        """Force the next :attr:`presets` read to re-scan the preset directory.

        Called by everything in this class that writes to that directory. The cache
        key also carries the directory's mtime, but a filesystem timestamp is coarse
        (~15ms on Windows) — a write and the refresh that immediately follows it can
        land in the same tick, so our own writers say so explicitly rather than
        relying on the clock.
        """
        self._preset_cache_key = None

    @property
    def presets(self) -> Dict[str, Optional[str]]:
        """Return available presets ({name: filepath}, plus a leading "None" entry).

        Cached: ``cmb000_init`` re-runs on every panel show and the scan is recursive
        over the whole Maya user app directory. The cache key carries the directory's
        modification time alongside its path, so the *contents* changing invalidates it
        too — keying on the path alone meant a preset added or deleted (same directory)
        was served back from the stale dict, leaving the combo showing a preset that no
        longer existed. That covers changes made outside the panel (Maya's preset
        editor, files dropped in by hand); this class's own writers additionally call
        :meth:`_invalidate_preset_cache`, which is not subject to mtime granularity.
        """
        preset_dir = self._get_preset_dir()
        try:  # A missing / unreadable dir stamps None: warn once, not every show.
            stamp = os.stat(preset_dir).st_mtime_ns if preset_dir else None
        except OSError:
            stamp = None
        cache_key = (preset_dir, stamp)

        # Only refresh the cached presets if the directory or its contents changed
        if cache_key != getattr(self, "_preset_cache_key", None):
            self.logger.debug(f"Preset directory: {preset_dir}")
            setattr(self, "_preset_cache_key", cache_key)
            presets = {"None": None}

            if stamp is None:
                self.logger.warning(
                    f"Preset directory not set or does not exist: {preset_dir}"
                )
            else:
                try:
                    files = ptk.FileUtils.get_dir_contents(
                        preset_dir,
                        content="filepath",
                        recursive=True,
                        inc_files=["*.fbxexportpreset"],
                    )
                    for f in files:
                        name = os.path.splitext(os.path.basename(f))[0]
                        # A name-keyed dict keeps the LAST file with that stem,
                        # and the scan is recursive over a tree that legitimately
                        # holds Maya's own versioned subfolder -- so two presets
                        # can share a name and one of them silently decides what
                        # every export uses. Say which, naming both: the pair is
                        # invisible from the combo, which shows one entry.
                        if name in presets and presets[name] != f:
                            self.logger.warning(
                                f"Two FBX presets are named {name!r}; the export "
                                f"will use {f} and ignore {presets[name]}. Rename "
                                "or delete one — a stale duplicate silently "
                                "decides your export settings."
                            )
                        presets[name] = f
                except Exception as e:
                    self.logger.error(f"Error accessing preset directory: {e}")

            setattr(self, "_cached_presets", presets)

        # Return the cached presets
        return getattr(self, "_cached_presets", {"None": None})

    @ptk.Deprecation.parameter(
        "timestamp",
        remove_in="0.20.0",
        since="2026-09-23",
        reason="Write '*_{date}_{time}' into output_name instead.",
    )
    @ptk.Deprecation.parameter(
        "name_regex",
        remove_in="0.20.0",
        since="2026-09-23",
        reason="Write '{scene:PATTERN->REPLACEMENT}' into output_name instead.",
    )
    def perform_export(
        self,
        export_dir: str,
        objects: Optional[Union[List[str], Callable]] = None,
        preset_file: Optional[str] = None,
        output_name: Optional[str] = None,
        export_visible: bool = True,
        file_format: Optional[str] = "FBX export",
        create_log_file: bool = False,
        timestamp: bool = False,
        name_regex: Optional[str] = None,
        log_level: Optional[str] = None,
        hide_log_file: Optional[bool] = None,
        log_handler: Optional[object] = None,
        tasks: Optional[Dict[str, Any]] = None,
        usd_options: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int, int, Optional[str]], Any]] = None,
    ) -> bool:
        """Perform the export operation, including initialization and task management.

        Returns True only when the deliverable was written -- every abort
        (no export dir, no objects, a failed check, a failed GLB-only
        conversion, a cancel) returns False, and a failed write raises. The
        panel's export button reads this to disarm its Override Checks toggle.

        *progress_callback* ``(current, total, message)`` receives ONE stream
        for the whole run -- the task manager's per-entry ticks and the
        post-pipeline phases (write, GLB, sidecar, verify) share a count --
        so a determinate bar can be driven from the first tick; uitk's
        ``sb.progress_adapter(update)`` is the panel's adapter. An explicit
        ``False`` cancels the run before its next step: nothing is written,
        and the tasks that already ran stay applied (as after a failed
        check). Once the write has begun a ``False`` is reported and ignored.

        *tasks* is EXACT: only the entries it names run, and an absent task or
        check is off -- there are no implicit defaults. The panel sends every
        row (``ptk.ExportProfile.read_values``), so a headless caller that wants
        the panel's behaviour passes every row it wants on -- an omitted
        ``apply_declared_takes``, for one, ships a scene's declared shots as one
        continuous clip.

        ``tasks["output_format"]`` picks the deliverable: ``fbx`` (default), ``glb``,
        ``fbx_glb``, or ``usd`` -- the same task pipeline and checks, written by
        ``mayaUSDExport`` instead of the FBX plugin. *usd_options* overrides
        :attr:`USD_EXPORT_OPTIONS` for that format (``convertMaterialsTo=["MaterialX"]``
        to ship a MaterialX network beside the UsdPreviewSurface default).

        *timestamp*, *name_regex* and ``tasks["version"]`` are the retired
        naming inputs (each warns; removed in 0.20.0, ``tasks["version"]`` in
        pythontk 0.12.0). They fold into *output_name* once, here
        (``ptk.ExportProfile.fold_legacy_naming``), and the log names the
        pattern that says the same thing.
        """
        from maya import cmds

        # First, so a caller's level/handler sees every message of this run,
        # the early aborts below included.
        self._setup_logging(log_level, log_handler)
        start_time = time.time()  # Track export duration
        self.logger.info("Starting export process ...")

        # Default to the open scene's directory when none is given — export
        # the FBX alongside the current scene file. saved_scene_path owns the
        # phantom-"untitled" rule (batch reports an unsaved scene as an
        # extensionless path where the GUI returns "").
        self.export_dir = self._resolve_export_dir(export_dir)
        if not self.export_dir:
            self.logger.error(
                "Export directory not set and the scene is unsaved — save "
                "the scene or specify an output directory."
            )
            return False
        if not export_dir:
            self.logger.info(
                f"No export directory given; exporting alongside the scene "
                f"file: {self.export_dir}"
            )

        # Validate export directory exists
        if not os.path.isdir(self.export_dir):
            self.logger.error(f"Export directory does not exist: {self.export_dir}")
            return False

        self.preset_file = preset_file  # Ensure the setter is called
        self.output_name = output_name
        self.create_log_file = create_log_file
        self.hide_log_file = hide_log_file

        # Every UI-only setting the export button's dict carries beside the
        # tasks -- output format, texture file type, the two write-back flags,
        # the size dial, the verification row and their legacy spellings --
        # is popped into ONE frozen value object (``ptk.ExportRun``), the
        # same parse both DCC exporters use, so none reaches the dispatcher
        # as an unknown task and nothing is stamped on the manager piecemeal.
        run, tasks, notes = ptk.ExportRun.from_tasks(
            tasks, TaskManager._texture_file_type_options.values()
        )
        for level, message in notes:
            getattr(self.logger, level)(message)
        if any(level == "error" for level, _ in notes):
            return False  # a config error (an unknown texture file type)
        # The retired naming inputs fold into the name ONCE, here; everything
        # past this point -- the path, the sidecar, the log -- reads the pattern.
        self.output_name = ptk.ExportProfile.fold_legacy_naming(
            output_name, run.version_format, timestamp, name_regex
        )
        if self.output_name != output_name:
            self.logger.warning(
                "The Version, Timestamp and RegEx inputs are retired: write them "
                "into the Output Filename instead -- this export resolves as "
                f"{self.output_name!r}."
            )
        # "usd": the deliverable is a USD layer. Same pipeline up to the write;
        # the FBX-only knobs (preset, takes, GLB) are reported inert below rather
        # than silently ignored. USDZ is deliberately not offered (no consumer).
        self._usd_options = dict(usd_options or {})
        if run.usd:
            for key in ("apply_declared_takes", "set_bake_animation_range"):
                if tasks.get(key):
                    self.logger.warning(
                        f"Task '{key}' sets FBX take/animation-range flags only; "
                        "a USD export samples the frames that carry motion instead."
                    )
        if run.texture_file_type == "ktx2":
            # Encoder presence is ENVIRONMENT state, so this gate is
            # unconditional (never a user-toggleable check row) and runs
            # before the first scene mutation — a missing toktx is settled
            # in second zero, not after N-1 objects already exported.
            # Missing = offer the managed KTX-Software install through
            # :meth:`confirm` (the panel's dialog; a console [y/N]
            # headless) and carry on when accepted; a decline or a failed
            # install aborts with the install URL. Abort idiom, not a
            # raise: the panel's export button reads the return value and
            # the log.
            if not ptk.ImgUtils.ktx2_available():
                self.logger.info(
                    "KTX2 delivery needs KTX-Software's toktx, which is "
                    "not installed: offering the managed install."
                )
            if not ptk.ImgUtils.settle_ktx2_encoder(
                prompt=self.confirm,
                refused=lambda why: self.logger.error(f"Export aborted: {why}"),
                installed=lambda path: self.logger.info(
                    f"Installed KTX-Software (toktx): {path}"
                ),
            ):
                return False
        # The Max Texture Size row's limit goes to the post-write pass: the
        # GLB's embedded images are measured against it (glb_image_bytes),
        # which is the only place a GLB-only export's textures can be -- its
        # size check steps aside, since the GLB pass re-encodes every map.
        image_limit = ptk.ExportProfile.texture_size_limit_bytes(
            tasks.get("check_texture_file_size")
        )
        glb_only, create_glb_enabled, usd = run.glb_only, run.create_glb, run.usd
        verify_deliverables = run.verify_deliverables
        drop_rig_apparatus = run.drop_rig_apparatus  # already off for a USD run

        # Resolve the export path. A {n} counter in the name takes the next
        # version among the files this format ships.
        resolved = self.resolve_export_path(
            self.output_name, self.export_dir, output_format=run.output_format
        )
        self.export_path = resolved["path"]
        # A versioned name routes the sidecar through SceneDataSidecar.base_stem
        # so every version of a series shares one manifest.
        run = run.replace(
            export_path=self.export_path, versioned=resolved["n"] is not None
        )
        self.logger.debug(f"Generated export path: {self.export_path}")
        # The ONE per-run reset: the manager adopts this run's modes and
        # drops every marker a previous run left, BEFORE the export set is
        # seeded -- so a run with no task checked still starts clean.
        self.task_manager.begin_run(run)

        if self.create_log_file:
            self._setup_file_logging()

        export_succeeded = False
        self._overridden_checks = []  # per-run; see the attribute's __init__ note
        # Progress: the pipeline's entries, then the write, the rig-helper
        # pass, a GLB conversion, the sidecar and an opt-in verification -- one
        # count for the run.
        self._progress_begin(
            progress_callback,
            tasks,
            phases=2
            + int(drop_rig_apparatus)
            + int(create_glb_enabled)
            + int(verify_deliverables),
        )
        # The run walks the timeline thousands of times (the shear scans, the
        # flatten's sampling, the bake), and in an interactive session every
        # currentTime would also redraw the viewport. Held for the whole run,
        # restores included; a no-op in batch, and re-entrant, so a task that
        # suspends refresh itself cannot resume the viewport early.
        #
        # The user's selection rides the same scope: the write selects the
        # export set (exportSelected) and the restores re-select as they
        # reparent and delete, so the run used to hand back whatever was
        # selected LAST -- a production room shell left under the highlight
        # wire read as a scene rendered solid green (2026-09-13). Put back
        # after every restore, deleted nodes dropped; blendertk's FbxUtils
        # already did this.
        run_scope = contextlib.ExitStack()
        # Outermost, so the selection put back last is not recorded either: the
        # undo queue is OFF for the whole run. The run reverses its own edits
        # (the deferred restores below), so recording them cost time and
        # memory -- every key a bake writes -- and left the user's next Ctrl+Z
        # undoing a RESTORE, which put a staged edit back into the scene.
        run_scope.enter_context(CoreUtils.undo_disabled())
        run_scope.enter_context(CoreUtils.suspended_refresh())
        run_scope.enter_context(CoreUtils.preserved_selection())
        try:
            # Inside the try, so the finally below closes the run's .log on these
            # exits too: an empty export set used to return, and a preset that
            # would not load to raise, before the try -- leaving the handler
            # attached (the next run wrote every line twice) and the file locked
            # on Windows.
            initialized_objs = self._initialize_objects(objects)
            if not initialized_objs:
                self.logger.error("Export aborted: No objects available for export.")
                return False

            # Apply preset before running tasks
            if self.preset_file and usd:
                self.logger.warning(
                    "The FBX export preset does not apply to a USD export "
                    f"(ignored: {self.preset_file})."
                )
            elif self.preset_file:
                self.load_fbx_export_preset(self.preset_file, verify=True)
            elif not usd:
                self._apply_default_fbx_options(create_glb_enabled)

            self._progress_note("Preparing export…")
            # Run tasks and checks
            if tasks:
                try:
                    checks_passed = self.task_manager.run_tasks(tasks)
                except Exception as e:
                    # A raising task stops the run before its write, as a failed
                    # check does: the staged edits unwind in the finally below,
                    # and what the tasks kept is named before the error goes on.
                    self._warn_stopped_before_write(f"Export stopped by an error: {e}.")
                    raise
                if not checks_passed:
                    # Offer the escape hatch HERE, while the staged scene the
                    # write needs is still standing, rather than leaving the
                    # user to arm Override Checks and pay for the whole
                    # pipeline a second time (see confirm_check_override).
                    if self.confirm_check_override():
                        self._overridden_checks = list(
                            getattr(self.task_manager, "_last_failed_checks", ()) or ()
                        )
                        self.logger.warning(
                            "Checks overridden — writing the file despite "
                            f"{len(self._overridden_checks)} failed check(s): "
                            f"{', '.join(self._overridden_checks)}."
                        )
                        self._resume_skipped_tasks(tasks)
                    else:
                        # The staged edits unwind in the finally below; what the
                        # tasks kept is named.
                        self._warn_stopped_before_write(
                            "Export blocked by failed checks."
                        )
                        return False

            # Select objects to export
            if export_visible:
                # "visible"/"all": the task pipeline's object set is authoritative.
                # Use cmds.select for performance (avoids node overhead).
                # Re-resolve first: cmds.select() on a list holding one stale
                # DAG path raises "No object matches name: [<the whole list>]"
                # and kills the export outright.  A node the pipeline lost is
                # worth a warning naming it, not an unreadable abort.
                objs_to_select = self.task_manager._live_objects()
                live = set(objs_to_select)
                declared = self.task_manager.objects or []
                missing = [o for o in declared if o not in live]
                if missing:
                    self.logger.warning(
                        f"{len(missing)} export object(s) no longer exist and will "
                        f"not ship: {', '.join(missing[:10])}"
                        + (" …" if len(missing) > 10 else "")
                    )
                # cmds.select([]) is a no-op that would leave a stale selection
                # in place — an empty export set must select nothing.
                if objs_to_select:
                    cmds.select(objs_to_select, replace=True)
                else:
                    cmds.select(clear=True)
                self.logger.info(f"Selected {len(objs_to_select)} objects for export.")
            else:
                # "selected": export the user's live selection, but fold in any
                # nodes the task pipeline added to the export set (e.g. the hidden
                # data_export carrier) — otherwise they'd silently never ship in
                # this mode, since it never re-selects from self.objects.
                current = set(cmds.ls(selection=True, long=True) or [])
                extras = [
                    o for o in (self.task_manager.objects or []) if o not in current
                ]
                if extras:
                    cmds.select(extras, add=True)
                    self.logger.info(
                        f"Added {len(extras)} pipeline object(s) to the export selection."
                    )

            if not cmds.ls(selection=True):
                self.logger.error("No objects to export.")
                return False

            # Perform the actual export. For GLB-only the FBX is written to a
            # throwaway temp dir (so it never lands in — or overwrites anything
            # in — the output directory) and removed once converted.
            glb_tempdir = None
            # The export bracket stages the scene (the curve-proxy transport
            # nodes; a preview standing down) for the write and undoes it in the
            # ``finally`` below, AFTER the GLB conversion has read the scene;
            # the session's before/after hooks stand down while it is open.
            # Opened with no context: the scene records were published ONCE
            # by the ``export_data_node`` task, with this run's decisions as
            # the producers' input, so nothing here republishes them -- the
            # bracket used to re-run every producer and overwrite the clip
            # origin and clip mode the pipeline had just published (the
            # assembly shipped 18 shots cut 81 frames early three times over
            # before ``check_clip_origin`` named it).
            from mayatk.env_utils.fbx_utils import FbxUtils as _FbxUtils

            _FbxUtils.begin_export()
            try:
                # A run with the carrier tasks off still publishes exactly once
                # (an ``all``-mode export ships the carrier regardless). Inside
                # the bracket's try: a raise here must still reach end_export()
                # below, or the session's export hooks stand down for the rest
                # of it.
                self.task_manager.ensure_scene_records_published()
                if glb_only:
                    glb_tempdir = ptk.TempArtifacts("scene_exporter_glb").dir_path()
                    fbx_write_path = os.path.join(
                        glb_tempdir, os.path.basename(self.export_path)
                    )
                else:
                    fbx_write_path = self.export_path

                # Use cmds.file for export to avoid object-wrapper overhead
                # cmds.exportSelected wraps cmds.file(..., exportSelected=True)
                # Written from the workspace root when the FBX settings embed
                # media (the plugin locates textures against the process CWD,
                # never the workspace) — set_workspace already aligns the CWD
                # in the default pipeline, but this also covers runs with that
                # task disabled or checks overridden (b009).
                self._progress_step("Writing USD…" if usd else "Writing FBX…")
                # From here the deliverable is finished regardless of a stop
                # request (see _emit_progress).
                self._progress_cancellable = False
                if usd:
                    self._write_usd(fbx_write_path)
                else:
                    from mayatk.env_utils.fbx_utils import FbxUtils

                    self._warn_if_animation_excluded()
                    with FbxUtils.embed_media_write_cwd():
                        cmds.file(
                            fbx_write_path,
                            force=True,
                            options="v=0;",
                            type=file_format,
                            exportSelected=True,
                        )
                    if drop_rig_apparatus:
                        # Before the GLB conversion reads the file, so both
                        # deliverables ship the same nodes and FBX2glTF --
                        # whose cost is nodes x baked frames -- never bakes the
                        # helpers. The selection is the set the write just read.
                        self._progress_step("Excluding rig helpers…")
                        FbxUtils.drop_rig_apparatus(
                            fbx_write_path,
                            cmds.ls(selection=True, long=True) or [],
                            logger=self.logger,
                        )
                export_succeeded = True

                # GLB conversion. For GLB-only, convert the temp FBX then move the
                # .glb into the output dir; the banner reports it as the
                # deliverable. A failed conversion has no deliverable, so the
                # export fails. For FBX+GLB the FBX is the deliverable and the GLB
                # is written alongside it *after* the banner.
                deliverable_path = self.export_path
                if glb_only:
                    self._progress_step("Converting to GLB…")
                    glb_path = self.task_manager.create_glb(
                        fbx_path=fbx_write_path, announce=False
                    )
                    if not (glb_path and os.path.exists(glb_path)):
                        self.logger.error(
                            "GLB-only export failed: FBX→GLB conversion produced "
                            "no file."
                        )
                        export_succeeded = False
                        return False
                    deliverable_path = os.path.splitext(self.export_path)[0] + ".glb"
                    try:
                        shutil.move(glb_path, deliverable_path)
                    except OSError as e:
                        # Everything succeeded except the final rename, which
                        # on Windows means the destination is held open. The
                        # temp dir is about to be removed, so the finished GLB
                        # -- the whole run's output -- would go with it over a
                        # file handle. Park it beside the destination instead
                        # and name it; the user closes the viewer and renames.
                        reason = ptk.FileUtils.describe_lock(deliverable_path) or str(e)
                        rescued = ptk.FileUtils.next_version_path(
                            deliverable_path, format="{stem}_unplaced_v{n:03d}{ext}"
                        )
                        try:
                            shutil.move(glb_path, rescued)
                        except OSError:  # the output dir itself is unwritable
                            rescued = glb_path
                            glb_tempdir = None  # read by the finally; keep it
                        self.logger.error(
                            f"Could not write {deliverable_path}: {reason}"
                        )
                        self.logger.warning(f"The finished GLB was kept at: {rescued}")
                        export_succeeded = False
                        return False
                    self.logger.success(f"GLB created: {deliverable_path}")

                # Build the single, consolidated success banner. Measure the
                # duration here (vs. right after the FBX write) so GLB-only
                # reflects the conversion time too.
                elapsed = time.time() - start_time
                export_info_lines = [
                    "✓ File written successfully",
                    "",
                    f"Path: {deliverable_path}",
                    f"Duration: {elapsed:.1f}s",
                ]
                # Include task/check counts from the pipeline phase
                tm = self.task_manager
                t_cnt = getattr(tm, "_last_task_count", 0)
                c_cnt = getattr(tm, "_last_check_count", 0)
                overridden = self._overridden_checks
                f_cnt = len(overridden)
                # Checks the failed one's abort dropped never ran; an override
                # resumes the tasks, not them, so they are not "passed".
                n_cnt = len(getattr(tm, "_last_skipped_checks", ()) or ())
                if t_cnt or c_cnt:
                    export_info_lines.append("")
                    export_info_lines.append(f"Tasks Executed: {t_cnt}")
                    if c_cnt:
                        # Never "N/N" after an override: the deliverable shipped
                        # WITH known failures and the banner is the record of it.
                        export_info_lines.append(
                            f"Checks Passed: {c_cnt - f_cnt - n_cnt}/{c_cnt}"
                        )
                        if f_cnt:
                            export_info_lines.append(
                                f"Checks Overridden: {', '.join(overridden)}"
                            )
                        if n_cnt:
                            export_info_lines.append(f"Checks Not Run: {n_cnt}")

                self.logger.log_box(
                    "EXPORT SUCCESSFUL", export_info_lines, level="SUCCESS"
                )

                # FBX+GLB: GLB sidecar runs after the banner so the FBX success
                # message isn't visually preceded by an unrelated GLB error if
                # conversion fails.
                glb_alongside = None
                if create_glb_enabled and not glb_only:
                    self._progress_step("Converting to GLB…")
                    glb_alongside = self.task_manager.create_glb()

                # Write the scene-data sidecar (hierarchy baseline for future
                # diff checks + data_export snapshot) as the single LAST step
                # of every mode, so it can describe the deliverable that
                # actually shipped rather than the state before the GLB
                # existed. Safe after create_glb because that never raises --
                # every failure path inside it logs and returns None -- so a
                # failed conversion still leaves the sidecar written, simply
                # without a section describing the GLB. An export that shipped
                # NOTHING still writes none: GLB-only returns above on a failed
                # conversion, and rolling the hierarchy baseline forward for a
                # phantom would make the next run's diff compare against it.
                # Keyed off the logical export path (output dir + stem),
                # independent of where the FBX was actually written.
                self._progress_step("Writing scene sidecar…")
                self.task_manager.write_scene_data_sidecar(
                    glb_path=deliverable_path if glb_only else glb_alongside
                )

                # Post-write file gates -- the last word on the deliverable,
                # read back from the bytes that shipped rather than from the
                # scene (see TaskManager.verify_deliverables). Opt-in (the
                # "Verify The Written File" row): it is the only pass whose
                # cost scales with the FBX rather than the scene, and paying it
                # on every iteration is what an export is least able to afford.
                # When armed it runs after the sidecar, because two of its
                # gates read that file, and is handed ONLY what a consumer
                # receives: GLB-only discards its temp FBX, so parsing it would
                # cost seconds and hundreds of MB of heap for a file nobody
                # gets. Reports without flipping the verdict -- the file is
                # already written.
                if verify_deliverables:
                    self._progress_step("Verifying deliverables…")
                    self.task_manager.verify_deliverables(
                        deliverable_path, glb_alongside, max_image_bytes=image_limit
                    )
                self._progress_finish("Export complete")
            except Exception as e:
                self.logger.error(f"Failed to export objects: {e}")
                raise RuntimeError(f"Failed to export objects: {e}")
            finally:
                _FbxUtils.end_export()
                if glb_tempdir:
                    shutil.rmtree(glb_tempdir, ignore_errors=True)
        except ptk.OperationCancelled as e:
            # The progress callback asked to stop (Esc held over the panel's
            # footer; a headless caller's own gate). Only reachable before the
            # write -- see _emit_progress -- so nothing shipped; the staged
            # edits unwind in the finally below and what the tasks kept is
            # named, exactly as after a failed check.
            export_succeeded = False
            self._export_cancelled = True
            self._warn_stopped_before_write(f"Export {e or 'cancelled'}.")
        finally:
            try:
                # Everything the tasks staged for the write -- the bake
                # session, the flatten, the working unit and workspace, the
                # texture pins, the material network, the armed takes --
                # unwinds LIFO here, on every exit path: a failed check, a
                # raising task, or a bad write. The bake session is the
                # innermost stage by construction: smart_bake stages its
                # restore after every earlier task's, so it is undone first.
                self.task_manager.run_deferred_restores()
                self._progress_end()
                # Here, on every exit, rather than around the write: a declined
                # override, an empty export set and an early cancel all return
                # before the write and used to leave the run's .log handler open
                # -- on exactly the paths a user then repeats. The restores
                # above land in the log too.
                if self.create_log_file:
                    self.close_file_handlers()
            finally:
                # Last, and unconditionally: a raising restore above must
                # not leave an interactive viewport suspended, nor the
                # export set selected.
                run_scope.close()

        if not export_succeeded:
            return False

        # Tasks/checks already ran (and any GLB conversion completed) before
        # this point; a True return means the deliverable was written.
        return True

    #: Token -> meaning for every placeholder the Output Filename accepts, in
    #: tooltip order (meanings are tooltip markup). A subclass extends the
    #: vocabulary here; pythontk supplies the universal clock/user tokens.
    NAME_TOKENS: Dict[str, str] = {
        "scene": "the scene's basename &mdash; <b>untitled</b> while it is "
        "unsaved. Reshape it in place with a regex: "
        "<b>{scene:PATTERN-&gt;REPLACEMENT}</b>",
        "folder": "name of the folder the scene lives in",
        ptk.ExportProfile.VERSION_TOKEN: "version number: one past the highest this name already "
        "has in the output folder &mdash; <b>{n:03d}</b> pads it to 3 digits",
        **ptk.StrUtils.NAME_PATTERN_TOKENS,
    }

    @ptk.Deprecation.parameter(
        "name_regex", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    def name_context(self, name_regex: Optional[str] = None) -> Dict[str, str]:
        """Live value for every token in :attr:`NAME_TOKENS` but the counter.

        The scene's own tokens; pythontk adds the universal ones (date/time/user)
        and samples them once, so a pattern using several cannot straddle a tick.
        The counter resolves later, against the output folder
        (:meth:`resolve_export_path`).

        *name_regex* is retired and ignored (warns; removed in 0.20.0): a regex
        is an inline modifier on the name token now, so the context never
        applied one.
        """
        scene_path = cmds.file(query=True, sceneName=True) or ""
        basename = os.path.splitext(os.path.basename(scene_path))[0]
        # ONE token spells the name. A regex reaches it as an inline modifier on
        # {scene} now, so nothing is applied here -- the pattern says it.
        # "untitled" keeps an unsaved scene exporting as it always has, and is
        # what a blank field and ``*`` resolve to (``ExportProfile.NAME_KEY``).
        scene = basename or "untitled"
        return ptk.StrUtils.name_pattern_context(
            # DEPRECATED alias (``ExportProfile.NAME_KEY_ALIASES``), still
            # honoured -- no removal release is set yet -- and deliberately
            # absent from NAME_TOKENS: a saved pattern spelling the name {name}
            # keeps resolving instead of baking a literal "{name}" into a
            # filename.
            name=scene,
            scene=scene,
            folder=os.path.basename(os.path.dirname(scene_path)),
        )

    @ptk.Deprecation.parameter(
        "name_regex", remove_in="0.20.0", since="2026-09-23", reason=_FOLD_FIRST
    )
    @ptk.Deprecation.parameter(
        "version_format", remove_in="0.20.0", since="2026-09-23", reason=_FOLD_FIRST
    )
    @ptk.Deprecation.parameter(
        "timestamp", remove_in="0.20.0", since="2026-09-23", reason=_FOLD_FIRST
    )
    def resolve_export_path(
        self,
        pattern: Optional[str] = None,
        export_dir: Optional[str] = None,
        output_format: str = "fbx",
        name_regex: Optional[str] = None,
        report: bool = True,
        version_format: str = "",
        timestamp: bool = False,
    ) -> Dict[str, Any]:
        """Resolve the Output Filename field into the file(s) an export writes.

        ONE call behind the write (:meth:`perform_export`,
        :meth:`generate_export_path`) and the panel's "writes" preview, so the
        two cannot disagree: this scene's tokens (:meth:`name_context`) through
        ``ptk.ExportProfile.resolve_output_path``, the rule both DCC panels
        share (wildcard, ``{n}`` counter, the files the format ships).

        Parameters:
            pattern: The Output Filename text.
            export_dir: The folder written to; the counter scans it.
            output_format: An :attr:`OUTPUT_EXTENSIONS` key.
            name_regex: DEPRECATED (warns; removed in 0.20.0) -- the retired
                RegEx field; write ``{scene:PATTERN->REPLACEMENT}`` into
                *pattern* instead.
            report: Log what the pattern hit. The tooltip passes False: it
                resolves on every hover and shows the diagnostics itself.
            version_format: DEPRECATED (warns; removed in 0.20.0) -- the
                retired Version pattern; write a ``{n}`` counter instead.
            timestamp: DEPRECATED (warns; removed in 0.20.0) -- the retired
                Timestamp checkbox; write ``*_{date}_{time}`` instead.

        Returns:
            ``ExportProfile.resolve_output_path``'s dict (``path``, ``paths``,
            ``n``, ``expanded``, ...) plus ``"context"``, the token values it
            resolved against.
        """
        context = self.name_context()
        resolved = ptk.ExportProfile.resolve_output_path(
            ptk.ExportProfile.fold_legacy_naming(
                pattern, version_format, timestamp, name_regex
            ),
            context,
            export_dir or "",
            output_format=output_format,
        )
        if report:
            for level, message in ptk.ExportProfile.naming_report(
                resolved, self.NAME_TOKENS
            ):
                getattr(self.logger, level)(message)
        return dict(resolved, context=context)

    @ptk.Deprecation.parameter(
        "version_format", remove_in="0.20.0", since="2026-09-23", reason=_FOLD_FIRST
    )
    def generate_export_path(
        self,
        version_format: str = "",
        extension: str = ".fbx",
        output_format: Optional[str] = None,
    ) -> str:
        """The full export path, from the fields :meth:`perform_export` stamps.

        A view of :meth:`resolve_export_path`, which the panel's preview calls
        too.

        Parameters:
            version_format: DEPRECATED (warns; removed in 0.20.0) -- the
                retired Version pattern; write a ``{n}`` counter into the
                Output Filename instead.
            extension: ``.usd`` selects the USD format when *output_format* is
                not given; anything else FBX.
            output_format: An :attr:`OUTPUT_EXTENSIONS` key.
        """
        if output_format is None:
            output_format = "usd" if extension.lower() == ".usd" else "fbx"
        return self.resolve_export_path(
            ptk.ExportProfile.fold_legacy_naming(self.output_name, version_format),
            self.export_dir,
            output_format=output_format,
        )["path"]

    #: ``mayaUSDExport`` flags for the USD output format: the shared interchange
    #: set (``UsdUtils.INTERCHANGE_EXPORT_OPTIONS``; a MaterialX network is an
    #: ``usd_options`` override), with textures referenced relative to the layer
    #: where possible -- a deliverable beside its scene, unlike a scratch payload.
    USD_EXPORT_OPTIONS: Dict[str, Any] = dict(
        UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
        exportRelativeTextures="automatic",
    )

    def _write_usd(self, usd_path: str) -> str:
        """Write the current selection as a USD layer (the ``usd`` output format).

        Samples animation only across the frames that carry motion
        (:meth:`UsdUtils.sampling_frame_range` -- ``frameRange`` is a direct
        multiplier on export cost); a static export writes no time samples.
        The ``data_export`` carrier exports as a prim like any other node; its
        custom attributes are not yet verified to arrive as ``userProperties``
        on every consumer, which the log says once per run.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        selection = cmds.ls(selection=True, long=True) or []
        options = dict(self.USD_EXPORT_OPTIONS)
        options.update(getattr(self, "_usd_options", None) or {})
        frame_range = UsdUtils.sampling_frame_range(selection)
        if frame_range:
            options.setdefault("frameRange", frame_range)
            self.logger.info(
                f"USD: sampling frames {frame_range[0]:g}-{frame_range[1]:g}."
            )
        # descendants=True: the export ships the whole subtree, so a scan of the
        # selected ROOTS alone sees nothing when a group of instances is selected.
        instanced = set(
            NodeUtils.get_instanced_shapes(selection, descendants=True) or []
        )
        if instanced:
            # A count of instance PATHS, not distinct shapes: an instanced shape has
            # one full DAG path per instance, which is the number that matters here
            # (each one becomes its own mesh).
            self.logger.warning(
                f"USD: {len(instanced)} instance(s) of shared shape(s) are written "
                "flat (USD's own instancing collapses material export); every "
                "instance ships as its own mesh."
            )
        from mayatk.node_utils.data_nodes import DataNodes

        if any(n.split("|")[-1] == DataNodes.EXPORT for n in selection):
            self.logger.info(
                "USD: the data_export carrier ships as a prim; consumers reading its "
                "attributes as userProperties are not yet verified."
            )
        written = UsdUtils.export(
            file_path=usd_path, options=options, selection_only=True
        )
        self.logger.info(f"USD written: {written}")
        return written

    @ptk.Deprecation.symbol(
        "an inline {scene:PATTERN->REPLACEMENT} modifier in the Output Filename",
        remove_in="0.20.0",
        since="2026-09-23",
    )
    def format_export_name(self, name: str, name_regex: Optional[str] = None) -> str:
        """*name* reshaped by the retired free-standing RegEx field.

        DEPRECATED (warns; removed in 0.20.0). Both the grammar and the
        substitution live in pythontk's token system
        (``ExportProfile.fold_legacy_regex`` -> ``StrUtils.apply_regex_modifier``),
        the same code an inline ``{scene:PATTERN->REPLACEMENT}`` modifier runs
        through -- write the modifier into the Output Filename instead and the
        whole naming rule is ONE string.
        """
        spec = ptk.ExportProfile.fold_legacy_regex(name_regex)
        if spec is None:
            return name
        result, error = ptk.StrUtils.apply_regex_modifier(name, spec)
        if error:
            self.logger.error(f"Output filename RegEx: {error}.")
        return result

    def _default_fbx_options(self, glb_deliverable: bool) -> Dict[str, Any]:
        """FBX export flags for a run that names NO preset.

        This path writes the FBX with ``cmds.file(...)`` directly and applies
        no options of its own, so without these the deliverable is shaped by
        whatever the last FBX operation in the session left behind -- and with
        a bare ``FBXResetExport``, by a factory state that is wrong for every
        deliverable this panel writes. Both halves were measured (2026-08-29,
        Maya 2025 / FBX 2020.3.6):

        * ``FBXExportInstances`` -- factory **off**. The substance bridge also
          turns it off for its own exports, which is why the hand-off mixin
          pins it back. Off, a production assembly exported 1511 meshes where
          the instanced truth is 537, and the per-instance lightmap atlas the
          whole bake pipeline rests on has nothing to share.
        * ``FBXExportEmbeddedTextures`` -- factory **off**, and FBX2glTF can
          only embed what the FBX carries. A GLB deliverable written from a
          session nobody had primed therefore arrived with ZERO images: the
          only reason this was not seen is that a WebXR preview push, which
          pins the flag, usually ran first in the same Maya. Pinned only for a
          GLB run: a loose-media FBX legitimately ships its textures beside
          itself, which is what ``convert_to_relative_paths`` is for.
        * ``FBXExportSmoothingGroups`` -- factory **off**, and the hard-edge
          information it carries cannot be reconstructed downstream. The mixin
          pins it for every hand-off for that reason.
        * ``FBXExportTangents`` -- factory **off**, which leaves a normal-mapped
          deliverable's tangent basis for the CONSUMER to invent. glTF says a
          client should compute one when ``TANGENT`` is absent, and clients
          disagree: three.js switches the whole material to a screen-space
          derivative basis and flips the green channel to compensate (r169
          ``GLTFLoader``, ``useDerivativeTangents``), so the same file can read
          bumps as dents somewhere else. Measured on a production assembly, all
          525 primitives shipped without tangents and the normal maps therefore
          rendered through that fallback. Pinned for a GLB, whose whole point is
          that it looks the same in a viewer nobody here controls.

        Cameras and lights are dropped **for a GLB only**, matching the mixin
        that writes the preview. glTF can carry lights (``KHR_lights_punctual``,
        which FBX2glTF writes unless told not to) and three.js's loader switches
        them on, so shipped lights would land ON TOP of the recipe the file
        publishes (``handoff.rendering``, the lighting the asset was approved
        under) -- and, on a baked material, on top of the light already in its
        lightmap. The viewer owns its camera the same way.
        Measured on a production assembly, this was the LAST difference between
        the two deliverables -- a scene camera called ``USER_POS_GEO`` reached
        the exporter's GLB and not the preview's, so an asset the artist
        approved with 1925 nodes arrived at the developer with 1926. Left at
        the factory value for an FBX or USD run, where cameras are ordinary
        content and this panel has no basis to override the choice.
        Triangulation keeps its factory value everywhere -- the converter
        triangulates on the way to glTF anyway, and Maya refuses it alongside
        smoothing groups.

        **What this means for ``fbx_glb``**: one FBX write serves both
        deliverables, so the GLB-shaped flags reshape the accompanying FBX too
        -- it embeds its media and drops its cameras. That trade is inherent to
        the mode rather than introduced here (embedding was already forced on
        it by the GLB needing pixels), and it is resolved in the GLB's favour
        deliberately: a GLB must not depend on which formats happen to ship
        beside it. An FBX that needs its cameras is the ``fbx`` format, or a
        preset, either of which leaves these untouched.

        Parameters:
            glb_deliverable: Whether this run writes a ``.glb`` (``glb`` or
                ``fbx_glb``), which is what makes embedded media mandatory and
                cameras/lights unwanted.

        Returns:
            ``{FBXExport* command: value}`` for :meth:`FbxUtils.set_fbx_options`.
        """
        options = {
            "FBXExportInstances": True,
            "FBXExportSmoothingGroups": True,
            "FBXExportEmbeddedTextures": bool(glb_deliverable),
        }
        if glb_deliverable:
            options["FBXExportTangents"] = True
            options["FBXExportCameras"] = False
            options["FBXExportLights"] = False
        return options

    def _warn_if_animation_excluded(self) -> bool:
        """Say so when an animated export set is about to ship with no animation.

        The last thing checked before the write, because it is the only moment
        that knows what will ACTUALLY happen: the Animation include group is a
        sticky global that an export PRESET carries its own value for, and a
        preset bypasses :meth:`_apply_default_fbx_options` entirely. With it
        off, the plugin writes zero AnimationStacks whatever the bake flags say
        -- so every animation task in the pipeline runs, logs its success, and
        is discarded. Measured on a production assembly (2026-08-30): a
        ``game_export`` preset with ``Animation`` off shipped a 70 MB FBX whose
        handoff declared 12 shots and whose animation array was empty, and
        nothing in the log mentioned it.

        :meth:`FbxUtils.apply_takes` REPAIRS the case it owns (declared takes);
        this covers the rest -- a keyframed scene with no shots, or with the
        takes task switched off -- where the export is legitimately allowed to
        proceed and the user simply has to be told.

        Returns:
            True when the warning fired (the export will carry no animation).
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        # Ordered so the normal path costs one property read: the keyframe query
        # walks the whole exported SUBTREE, and it is only worth paying once
        # animation is known to be excluded.
        #
        # `_has_keyframes` measures the exported SUBTREE (`_exported_objects`).
        # It did not always: the first version of this guard was DEAD on the
        # very production export it was written for, because the shallow scope
        # answered 0 keyframe times over 5 roots whose subtree holds 84
        # (measured 2026-08-30).
        if FbxUtils.animation_export_enabled() or not self.task_manager._has_keyframes:
            return False
        # getattr, not self.preset_file: this composes a WARNING, and a warning
        # path that raises is strictly worse than the silence it replaces.
        # ``perform_export`` always sets the attribute before the write, so this
        # only covers being called on its own (a check, a test, a future caller).
        preset = getattr(self, "preset_file", None)
        self.logger.warning(
            "This export will contain NO animation: the FBX plug-in's Animation "
            f"include group ({FbxUtils.ANIMATION_INCLUDE_PROPERTY}) is off, while "
            "the export set has keyframes. That switch belongs to the loaded FBX "
            f"preset{f' ({os.path.basename(preset)})' if preset else ''}"
            " — choose a preset that includes animation, or clear the preset to "
            "use the exporter's own defaults."
        )
        return True

    def _apply_default_fbx_options(self, glb_deliverable: bool) -> None:
        """Reset the plugin's sticky export state, then pin :meth:`_default_fbx_options`.

        Ordered reset-then-pin so the run starts from a KNOWN state rather than
        a session-dependent one, and then only the flags that would degrade the
        deliverable are moved off it. Runs before the task pipeline, because
        ``set_bake_animation_range`` and ``apply_declared_takes`` deliberately
        WRITE this same state and must not be undone by a later reset.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        options = self._default_fbx_options(glb_deliverable)
        FbxUtils.reset_export()
        FbxUtils.set_fbx_options(options)
        self.logger.debug(
            "No FBX preset: export options reset to factory defaults, then "
            f"pinned {options} so the write neither inherits this session's "
            "FBX state nor silently drops instancing, smoothing or media."
        )

    def load_fbx_export_preset(
        self, preset_file: str = None, verify: bool = False
    ) -> Optional[dict]:
        """Load an FBX export preset and optionally verify it.

        Parameters:
            preset_file (str, optional): The path to the preset file to be loaded.
            verify (bool, optional): If True, verifies the loaded FBX preset. Defaults to False.

        Returns:
            Optional[dict]: A dictionary of FBX settings and their current values if verification is performed, otherwise None.
        """
        # Ensure FBX plugin is loaded
        try:
            EnvUtils.load_plugin("fbxmaya")
        except ValueError as e:
            self.logger.error(f"Failed to ensure fbxmaya plugin is loaded: {e}")
            raise RuntimeError(f"Failed to ensure fbxmaya plugin is loaded: {e}") from e

        if preset_file:
            self.logger.debug(f"Loading FBX export preset: {preset_file}")
            preset_path_escaped = preset_file.replace("\\", "/")

            try:
                mel.eval(f'FBXLoadExportPresetFile -f "{preset_path_escaped}"')
                self.logger.info(
                    f"Loaded FBX export preset from {preset_path_escaped}."
                )
            except RuntimeError as e:
                self.logger.error(f"Failed to load FBX export preset: {e}")
                raise RuntimeError(f"Failed to load FBX export preset: {e}")

        # If verify is True, call the verify_fbx_preset method
        if verify:
            return self.verify_fbx_preset()

        return None

    def verify_fbx_preset(self) -> dict:
        """Verify a set of predefined FBX export settings and log their values.

        Returns:
            dict: A dictionary of FBX export settings and their current values.
        """
        settings = [
            "FBXExportBakeComplexAnimation",
            "FBXExportBakeComplexStart",
            "FBXExportBakeComplexEnd",
            "FBXExportBakeComplexStep",
            "FBXExportSmoothingGroups",
            "FBXExportHardEdges",
            "FBXExportTangents",
            "FBXExportSmoothMesh",
            "FBXExportInstances",
            "FBXExportReferencedAssetsContent",
            "FBXExportAnimationOnly",
            "FBXExportSkins",
            "FBXExportShapes",
            "FBXExportConstraints",
            "FBXExportCameras",
            "FBXExportLights",
            "FBXExportEmbeddedTextures",
            "FBXExportInputConnections",
            "FBXExportTriangulate",
            "FBXExportUseSceneName",
            "FBXExportBakeResampleAnimation",
            "FBXExportFileVersion",
        ]
        results = {}

        # Collected, not logged per setting: every log record is its own
        # paragraph in the output panel, so a line per option rendered this
        # ~18-entry dump as 18 blank-line-separated sections. One grouped
        # record instead — the same shape the Material Updater's "Run
        # Settings" block uses. Errors stay individual records: they're the
        # actionable lines and must not inherit the group's muted colour.
        lines = []
        for setting in settings:
            try:
                value = mel.eval(f"{setting} -q")
                results[setting] = value
                lines.append(f"{setting:<34}: {value}")
            except RuntimeError as e:
                self.logger.error(f"Error querying {setting}: {e}")

        if lines and self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group("FBX Export Settings", lines)

        return results
