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
import ctypes
import shutil
import logging
from typing import List, Dict, Optional, Callable, Union, Any

import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.usd import UsdUtils
from mayatk.env_utils.scene_exporter.task_manager import TaskManager
from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar


class SceneExporter(ptk.LoggingMixin):
    def __init__(
        self, log_level: str = "WARNING", log_handler: Optional[object] = None
    ):
        """ """
        self._setup_logging(log_level, log_handler)

        self.task_manager = TaskManager(self.logger)
        #: Checks a run failed but the user chose to override at the failure
        #: point (see confirm_check_override). Re-stamped by every
        #: ``perform_export``, so the success banner reports the deliverable
        #: as shipped-with-failures rather than claiming a clean pass.
        self._overridden_checks: List[str] = []
        #: The ``(current, total, message)`` stream of the run in flight and
        #: its bookkeeping -- see :meth:`_progress_begin`; cleared when
        #: ``perform_export`` returns.
        self._progress_callback: Optional[Callable] = None
        self._progress_current = 0
        self._progress_total = 0
        self._progress_base = 0
        self._progress_open = False
        self._progress_cancellable = False
        self._progress_cancel_ignored = False
        #: Whether the last run stopped on a cancel (vs. any other abort) --
        #: what the panel's footer reports after the run.
        self._export_cancelled = False
        self.logger.debug("Task manager initialized in SceneExporter.")

    def _setup_logging(
        self, log_level: Optional[str], log_handler: Optional[object]
    ) -> None:
        """Apply a log level and/or handler; ``None`` leaves the level as it is.

        ``perform_export`` calls this with its own ``log_level`` argument, so
        a level there would silently override the one the constructor set --
        ``SceneExporter(log_level="DEBUG").perform_export(...)`` used to run
        at WARNING and drop every per-task line the caller had asked for.
        """
        if log_level is not None:
            self.logger.setLevel(log_level)
        if log_handler:
            self.logger.addHandler(log_handler)

    def _setup_file_logging(self) -> None:
        """Setup file logging."""
        log_file_path = self.generate_log_file_path(self.export_path)
        self.logger.info(f"Generating log file path: {log_file_path}")
        self.setup_file_logging(log_file_path)

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

    def confirm(self, question: str) -> bool:
        """Yes/no consent for an export-time side effect (a tool download).

        The seam the panel overrides with a dialog. Headless it asks on the
        console when there is one -- an interactive mayapy user gets a
        ``[y/N]`` -- and answers no otherwise: nobody is there to consent, and
        the caller's own message names the manual install.

        Parameters:
            question: Plain-text question; newlines allowed.
        """
        return bool(ptk.AppInstaller.consent(True, question))

    def confirm_check_override(self) -> bool:
        """Ask, at the failure point, whether to export despite failed checks.

        The tasks have already run and the scene is still staged, so this is
        the ONE moment at which overriding costs nothing. Arming the panel's
        Override Checks toggle *after* a failed run instead means a second
        export from scratch: every task re-runs (re-bake, re-optimize the
        textures, re-rewrite the paths) on a scene the first run already
        mutated. Answering yes here continues the SAME run straight to the
        write.

        Consent only, never an automatic pass: it routes through
        :meth:`confirm`, whose default answers no when nobody is there to ask
        (a batch run still aborts on a failed check).
        """
        failed = list(getattr(self.task_manager, "_last_failed_checks", ()) or ())
        listed = ", ".join(failed[:10]) + (" \u2026" if len(failed) > 10 else "")
        headline = (
            f"{len(failed)} validation check(s) failed: {listed}."
            if failed
            else "A validation check failed."
        )
        return self.confirm(
            f"{headline}\n\n"
            "The export tasks have already run, so overriding now finishes THIS "
            "run instead of re-running the whole pipeline over an already-"
            "mutated scene.\n\n"
            "Override the checks and export anyway?"
        )

    def _warn_stopped_before_write(self, verdict: str) -> None:
        """Warn that the run stopped before its write, naming what it left behind.

        Every staged edit unwinds in :meth:`perform_export`'s ``finally``, on this
        exit as on any other. What stays is what a finished export keeps too --
        the repairs, and a write-back mode's in-place edits -- which the tasks
        record as they make them (``TaskFactory.kept_edits``). Named only when a
        task left one: a fixed list here said key snapping and tying remained
        after runs that had restored every key.
        """
        kept = self.task_manager.kept_edits
        if not kept:
            self.logger.warning(verdict)
            return
        self.logger.warning(
            f"{verdict} Kept in the scene, as a finished export keeps them: "
            f"{', '.join(kept)}. An export records none for undo — revert to the "
            "saved file if that is not what you want."
        )

    def _resume_skipped_tasks(self, tasks: Dict[str, Any]) -> None:
        """Run the tasks the failed check aborted, so an override still ships a
        fully processed file.

        The runner stops dispatching tasks at the first failed check -- every
        one below it in the schedule is work an aborted write would throw
        away. Overriding turns that write back on, so those tasks are no
        longer wasted and must run before it: without this an overridden
        export silently shipped a file that skipped, say, the texture
        conversion the user asked for.

        Only the skipped names are re-dispatched; the tasks above the failed
        check already ran, and re-running them would repeat their mutation.
        The first pass's staged state is still in effect (deferred restores
        unwind once, from perform_export's ``finally``), and the run's modes
        are NOT re-derived from this subset -- ``run_tasks`` reads them off
        the full dict, so the resume goes through the dispatcher directly.
        """
        tm = self.task_manager
        skipped = [
            n for n in (getattr(tm, "_last_skipped_tasks", ()) or ()) if n in tasks
        ]
        if not skipped:
            return
        self.logger.info(
            f"Resuming {len(skipped)} task(s) the failed check had stopped: "
            f"{', '.join(skipped)}."
        )
        # The second pass re-stamps the run counters the success banner reads.
        # The first pass already counted every REQUESTED task, so its numbers
        # are the ones that describe the run; keep them.
        counts = (
            getattr(tm, "_last_task_count", 0),
            getattr(tm, "_last_check_count", 0),
        )
        # ...and the checks the abort skipped, which the second pass (tasks
        # only) would clear: the banner must not count them as passed.
        skipped_checks = list(getattr(tm, "_last_skipped_checks", ()) or ())
        # The first pass closed its progress stream with every entry done,
        # these included; rewind so the resumed entries advance to, never
        # past, that mark.
        self._progress_base = max(0, self._progress_current - len(skipped))
        try:
            tm._execute_tasks_and_checks({name: tasks[name] for name in skipped}, {})
        finally:
            tm._last_task_count, tm._last_check_count = counts
            tm._last_skipped_checks = skipped_checks

    # ------------------------------------------------------------------
    # Progress -- one (current, total, message) stream for the whole run
    # ------------------------------------------------------------------

    def _progress_begin(
        self, callback: Optional[Callable], tasks: Dict[str, Any], phases: int
    ) -> None:
        """Arm the run's progress stream (see ``perform_export``).

        ``current`` counts finished steps: every pipeline entry that will
        dispatch is one (the task manager reports them through its
        ``progress_callback``), and each of the *phases* after the pipeline
        -- the write, a GLB conversion, the sidecar, ... -- is one more.
        """
        self._progress_callback = callback
        self._progress_total = self.task_manager._dispatchable_count(tasks) + phases
        self._progress_current = 0
        self._progress_base = 0
        self._progress_open = False
        self._progress_cancellable = True
        self._progress_cancel_ignored = False
        self._export_cancelled = False
        self.task_manager.progress_callback = self._on_pipeline_progress

    def _progress_end(self) -> None:
        """Disarm the stream; a later run of the task manager reports nothing."""
        self.task_manager.progress_callback = None
        self._progress_callback = None

    def _emit_progress(self, message: Optional[str]) -> bool:
        """Report the current position; False when the caller asked to stop.

        A ``False`` from the callback is honoured only while nothing has been
        written. Once the write starts the deliverable is finished regardless
        -- a GLB abandoned between its conversion and its texture pass is a
        file that looks complete and is not -- and the request is reported
        once instead. A callback that raises is a feedback bug: logged, never
        allowed to fail the export.
        """
        callback = self._progress_callback
        if callback is None:
            return True
        try:
            keep_going = callback(self._progress_current, self._progress_total, message)
        except ptk.OperationCancelled:
            raise
        except Exception as e:  # noqa: BLE001 -- feedback never fails an export
            self.logger.debug(f"Progress callback failed: {e}")
            return True
        if keep_going is not False:
            return True
        if self._progress_cancellable:
            return False
        if not self._progress_cancel_ignored:
            self._progress_cancel_ignored = True
            self.logger.warning(
                "Cancel requested after the write began — finishing the "
                "deliverable rather than leaving it half-written."
            )
        return True

    def _on_pipeline_progress(self, current, total, message) -> bool:
        """The task manager's hook: its entry index rides on the run's base.

        ``(None, None, text)`` is a text-only tick and leaves the count alone.
        """
        if current is not None:
            self._progress_current = self._progress_base + int(current)
            self._progress_open = False
        return self._emit_progress(message)

    def _progress_step(self, message: str) -> None:
        """Start a post-pipeline phase; the one before it is thereby done."""
        if self._progress_open:
            self._progress_current += 1
        self._progress_open = True
        if not self._emit_progress(message):
            raise ptk.OperationCancelled(f"cancelled before {message}")

    def _progress_note(self, message: str) -> None:
        """Narrate inside a phase without moving the count."""
        if not self._emit_progress(message):
            raise ptk.OperationCancelled(f"cancelled before {message}")

    def _progress_finish(self, message: str) -> None:
        """The last tick, snapped to the total (skipped checks leave a gap)."""
        self._progress_current = self._progress_total
        self._progress_open = False
        self._emit_progress(message)

    # ------------------------------------------------------------------
    # The export button's contract (the panel's b000, written once)
    # ------------------------------------------------------------------

    #: The Output Format row (``cmb004``): label -> ``output_format`` token.
    #: APPEND-ONLY -- the combo (and every saved preset) persists by index.
    OUTPUT_FORMATS = ptk.ExportProfile.OUTPUT_FORMATS

    def _definition_tables(self):
        """``(tasks, checks)`` -- the panel's two definition tables, built once.

        The two properties assemble every row's tooltip on each access, and a
        button press consults them more than once.
        """
        tables = getattr(self, "_definition_tables_cache", None)
        if tables is None:
            tm = self.task_manager
            tables = (tm.task_definitions, tm.check_definitions)
            self._definition_tables_cache = tables
        return tables

    def run_config_from_values(
        self,
        values: Dict[str, Any],
        override_checks: bool = False,
        ignore_groups_case_sensitive: bool = False,
    ) -> Dict[str, Any]:
        """Widget values -> the inputs :meth:`perform_export` takes.

        The export button's contract, through
        :meth:`pythontk.ExportProfile.run_config` (the one copy both DCC panels
        read their widgets with); this adds the settings row that is not a task
        definition: ``output_format`` (``cmb004``) into the tasks.

        Returns:
            ``{"tasks", "export_mode", "export_visible"}``.
        """
        tasks_def, checks_def = self._definition_tables()
        config = ptk.ExportProfile.run_config(
            values,
            tasks_def,
            checks_def,
            override_checks=override_checks,
            ignore_groups_case_sensitive=ignore_groups_case_sensitive,
        )
        output_format = values.get("cmb004")
        if output_format:
            config["tasks"]["output_format"] = output_format
        return config

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
        self.name_regex = name_regex
        self.timestamp = timestamp
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
            try:
                if not ptk.ImgUtils.ktx2_available():
                    self.logger.info(
                        "KTX2 delivery needs KTX-Software's toktx, which is "
                        "not installed: offering the managed install."
                    )
                installed = ptk.ImgUtils.ensure_ktx2_encoder(prompt=self.confirm)
            except FileNotFoundError as e:
                self.logger.error(f"Export aborted: {e}")
                return False
            if installed:
                self.logger.info(f"Installed KTX-Software (toktx): {installed}")
        # The Max Texture Size row's limit goes to the post-write pass: the
        # GLB's embedded images are measured against it (glb_image_bytes),
        # which is the only place a GLB-only export's textures can be -- its
        # size check steps aside, since the GLB pass re-encodes every map.
        image_limit = ptk.ExportProfile.texture_size_limit_bytes(
            tasks.get("check_texture_file_size")
        )
        glb_only, create_glb_enabled, usd = run.glb_only, run.create_glb, run.usd
        verify_deliverables = run.verify_deliverables

        # Resolve the export path. A {n} counter in the name takes the next
        # version among the files this format ships.
        resolved = self.resolve_export_path(
            self.output_name,
            self.export_dir,
            output_format=run.output_format,
            version_format=run.version_format,
            timestamp=self.timestamp,
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
        # Progress: the pipeline's entries, then the write, a GLB conversion,
        # the sidecar and an opt-in verification -- one count for the run.
        self._progress_begin(
            progress_callback,
            tasks,
            phases=2 + int(create_glb_enabled) + int(verify_deliverables),
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
                checks_passed = self.task_manager.run_tasks(tasks)
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
            # The export bracket: preparers have already run as a pipeline task,
            # so this re-run is a cheap idempotent refresh -- what it BUYS is the
            # matching finalize in the ``finally`` below, AFTER the GLB
            # conversion has read the scene. Export-time staging (suspended
            # viewport bindings, curve-proxy transport nodes) must outlive the
            # FBX write and reach the conversion, then be undone exactly once;
            # the session's after-export hook stands down while this is open.
            from mayatk.env_utils.fbx_utils import FbxUtils as _FbxUtils

            _FbxUtils.begin_export()
            try:
                # The preparers just REPUBLISHED the data_export channels from
                # scratch -- including the visibility channel's ``clip_span``,
                # whose whole-timeline entry they can only seed from the bake
                # range they happen to find. That is the frame every GLB clip
                # is cut against, and only the pipeline knows it: it alone has
                # the export set and the final curves. So the pipeline's
                # measurement is re-asserted HERE, after the preparers and
                # before the write -- the last writer, by construction rather
                # than by task order. It used to be published by the last
                # TASK, which the bracket then silently overwrote; the assembly
                # shipped 18 shots cut 81 frames early three times over before
                # ``check_clip_origin`` named it. Inside the bracket's try: a
                # raise here must still reach end_export() below, or the
                # session's export hooks stand down for the rest of it.
                self.task_manager.publish_clip_origin()
                # The Animation Clips mode rides the shot_metadata envelope
                # the same preparers just republished, so it is declared
                # here too, after them: the deliverable gates read it.
                self.task_manager.publish_clip_mode()
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

    #: Stamped by ``perform_export`` from the panel's fields. Class-level
    #: defaults so a name can be resolved before the first run -- the panel's
    #: live tooltip preview resolves one on every hover. ``timestamp`` is the
    #: retired Timestamp checkbox, honoured for one release
    #: (``ptk.ExportProfile.fold_legacy_naming``).
    output_name: Optional[str] = None
    name_regex: Optional[str] = None
    timestamp: bool = False

    #: The Output Filename's wildcard (``*``), version counter (``{n}``) and the
    #: files each output format ships: the contract ``ptk.ExportProfile`` owns
    #: for both DCC panels, re-exposed so the panel reads them off ``self``.
    NAME_WILDCARD = ptk.ExportProfile.NAME_WILDCARD
    VERSION_TOKEN = ptk.ExportProfile.VERSION_TOKEN
    OUTPUT_EXTENSIONS = ptk.ExportProfile.OUTPUT_EXTENSIONS

    #: Token -> meaning for every placeholder the Output Filename accepts, in
    #: tooltip order (meanings are tooltip markup). A subclass extends the
    #: vocabulary here; pythontk supplies the universal clock/user tokens.
    NAME_TOKENS: Dict[str, str] = {
        "scene": "the scene's basename &mdash; <b>untitled</b> while it is "
        "unsaved. Reshape it in place with a regex: "
        "<b>{scene:PATTERN-&gt;REPLACEMENT}</b>",
        "folder": "name of the folder the scene lives in",
        VERSION_TOKEN: "version number: one past the highest this name already "
        "has in the output folder &mdash; <b>{n:03d}</b> pads it to 3 digits",
        **ptk.StrUtils.NAME_PATTERN_TOKENS,
    }

    def _resolve_export_dir(self, export_dir: Optional[str]) -> str:
        """The folder an export writes to: *export_dir* expanded, else the saved
        scene's own folder; ``""`` when there is neither (an unsaved scene with
        no directory set, which :meth:`perform_export` refuses)."""
        if export_dir:
            return os.path.abspath(os.path.expandvars(export_dir))
        scene_path = EnvUtils.saved_scene_path()
        return os.path.dirname(scene_path) if scene_path else ""

    def name_context(self, name_regex: Optional[str] = None) -> Dict[str, str]:
        """Live value for every token in :attr:`NAME_TOKENS` but the counter.

        The scene's own tokens; pythontk adds the universal ones (date/time/user)
        and samples them once, so a pattern using several cannot straddle a tick.
        The counter resolves later, against the output folder
        (:meth:`resolve_export_path`).

        *name_regex* overrides :attr:`name_regex` -- the panel reads its field
        straight from the UI so the tooltip previews what the next export writes.
        """
        scene_path = cmds.file(query=True, sceneName=True) or ""
        basename = os.path.splitext(os.path.basename(scene_path))[0]
        # ONE token spells the name. A regex reaches it as an inline modifier on
        # {scene} now, so nothing is applied here -- the pattern says it.
        # "untitled" keeps an unsaved scene exporting as it always has, and is
        # what a blank field and ``*`` resolve to (``ExportProfile.NAME_KEY``).
        scene = basename or "untitled"
        return ptk.StrUtils.name_pattern_context(
            # DEPRECATED alias, honoured for one release and deliberately absent
            # from NAME_TOKENS: a saved pattern spelling the name {name} keeps
            # resolving instead of baking a literal "{name}" into a filename.
            name=scene,
            scene=scene,
            folder=os.path.basename(os.path.dirname(scene_path)),
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
            name_regex: Overrides :attr:`name_regex` (see :meth:`name_context`).
            report: Log what the pattern hit. The tooltip passes False: it
                resolves on every hover and shows the diagnostics itself.
            version_format: DEPRECATED -- the retired Version pattern.
            timestamp: DEPRECATED -- the retired Timestamp checkbox.

        Returns:
            ``ExportProfile.resolve_output_path``'s dict (``path``, ``paths``,
            ``n``, ``expanded``, ...) plus ``"context"``, the token values it
            resolved against.
        """
        context = self.name_context()
        resolved = ptk.ExportProfile.resolve_output_path(
            pattern,
            context,
            export_dir or "",
            output_format=output_format,
            version_format=version_format,
            timestamp=timestamp,
            # The retired field folds INTO the pattern here rather than shaping
            # the context value, so the preview and the log show the rule.
            name_regex=self.name_regex if name_regex is None else name_regex,
        )
        if report:
            for level, message in ptk.ExportProfile.naming_report(
                resolved,
                self.NAME_TOKENS,
                version_suffix=SceneDataSidecar.VERSION_SUFFIX_RE,
            ):
                getattr(self.logger, level)(message)
        return dict(resolved, context=context)

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
            version_format: DEPRECATED -- the retired Version pattern; write a
                ``{n}`` counter into the Output Filename instead.
            extension: ``.usd`` selects the USD format when *output_format* is
                not given; anything else FBX.
            output_format: An :attr:`OUTPUT_EXTENSIONS` key.
        """
        if output_format is None:
            output_format = "usd" if extension.lower() == ".usd" else "fbx"
        return self.resolve_export_path(
            self.output_name,
            self.export_dir,
            output_format=output_format,
            version_format=version_format,
            timestamp=self.timestamp,
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
        if any(n.split("|")[-1] == "data_export" for n in selection):
            self.logger.info(
                "USD: the data_export carrier ships as a prim; consumers reading its "
                "attributes as userProperties are not yet verified."
            )
        written = UsdUtils.export(
            file_path=usd_path, options=options, selection_only=True
        )
        self.logger.info(f"USD written: {written}")
        return written

    def format_export_name(self, name: str, name_regex: Optional[str] = None) -> str:
        """*name* reshaped by the retired free-standing RegEx field.

        DEPRECATED path, kept so a saved field keeps working. Both the grammar
        and the substitution now live in pythontk's token system
        (``ExportProfile.fold_legacy_regex`` -> ``StrUtils.apply_regex_modifier``),
        the same code an inline ``{name:PATTERN->REPLACEMENT}`` modifier runs
        through -- write the modifier into the Output Filename instead and the
        whole naming rule is ONE string.

        *name_regex* overrides :attr:`name_regex` (the panel passes its field's
        live text so a tooltip preview matches the next export).
        """
        name_regex = self.name_regex if name_regex is None else name_regex
        spec = ptk.ExportProfile.fold_legacy_regex(name_regex)
        if spec is None:
            return name
        result, error = ptk.StrUtils.apply_regex_modifier(name, spec)
        if error:
            self.logger.error(f"Output filename RegEx: {error}.")
        return result

    def generate_log_file_path(self, export_path: str) -> str:
        """Generate the log file path based on the export path."""
        base_name = os.path.splitext(os.path.basename(export_path))[0]
        return os.path.join(self.export_dir, f"{base_name}.log")

    def setup_file_logging(self, log_file_path: str):
        """Setup file logging to log actions during export."""
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.file_handler = file_handler
        root_logger = logging.getLogger(self.__class__.__name__)
        root_logger.addHandler(self.file_handler)
        self.logger.debug(f"File logging setup complete. Log file: {log_file_path}")

        if self.hide_log_file and os.name == "nt":
            ctypes.windll.kernel32.SetFileAttributesW(log_file_path, 2)

    def close_file_handlers(self):
        """Close and remove file handlers after logging is complete."""
        root_logger = logging.getLogger(self.__class__.__name__)
        handlers = root_logger.handlers[:]
        for handler in handlers:
            if isinstance(handler, logging.FileHandler):
                handler.close()
                root_logger.removeHandler(handler)
                self.logger.debug("File handler closed and removed.")

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
        that writes the preview: glTF has no light slot at all, and the viewer
        owns its own camera (``handoff.rendering`` records the lighting the
        asset was approved under, precisely because the asset carries none).
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
                cameras/lights dead weight.

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


def __getattr__(name):
    """``SceneExporterSlots`` lives in ``scene_exporter_slots`` (2026-09-13,
    the layout blendertk already had); this alias holds for one release."""
    if name == "SceneExporterSlots":
        from mayatk.env_utils.scene_exporter.scene_exporter_slots import (
            SceneExporterSlots,
        )

        return SceneExporterSlots
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
