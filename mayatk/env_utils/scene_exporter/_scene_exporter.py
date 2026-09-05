# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None
import maya.mel as mel

import os
import re
import time
import base64
import html
import ctypes
import shutil
import logging
from datetime import datetime
from typing import List, Dict, Optional, Callable, Union, Any

import pythontk as ptk

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.usd import UsdUtils
from mayatk.display_utils._display_utils import DisplayUtils
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
        Safe because every ``set_`` task here registers a deferred restore
        rather than a ``revert_`` pair, so the first pass's staged state is
        still in effect (see ``TaskFactory._get_revert_method``).
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
        # The first pass closed its progress stream with every entry done,
        # these included; rewind so the resumed entries advance to, never
        # past, that mark.
        self._progress_base = max(0, self._progress_current - len(skipped))
        try:
            tm.run_tasks({name: tasks[name] for name in skipped})
        finally:
            tm._last_task_count, tm._last_check_count = counts

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
        if not export_dir:
            scene_path = EnvUtils.saved_scene_path()
            if scene_path:
                export_dir = os.path.dirname(scene_path)
                self.logger.info(
                    f"No export directory given; exporting alongside the scene "
                    f"file: {export_dir}"
                )
            else:
                self.logger.error(
                    "Export directory not set and the scene is unsaved — save "
                    "the scene or specify an output directory."
                )
                return False

        # Set export configuration
        self.export_dir = os.path.abspath(os.path.expandvars(export_dir))

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

        # Pop UI-defined settings that aren't actual task-pipeline methods.
        # `version` influences path generation (resolved below); `output_format`
        # selects FBX / GLB / FBX+GLB and is consumed after the FBX is written.
        tasks = dict(tasks) if tasks else {}
        version_format = tasks.pop("version", "") or ""
        # Output format: "fbx" (default), "glb" (GLB only — the FBX is written to
        # a temp dir and discarded after conversion), or "fbx_glb" (both, side by
        # side). A legacy `create_glb=True` (older callers / saved templates)
        # maps to "fbx_glb".
        output_format = (tasks.pop("output_format", "") or "").lower()
        if not output_format:
            output_format = "fbx_glb" if tasks.pop("create_glb", False) else "fbx"
        else:
            tasks.pop("create_glb", None)  # format wins over any legacy flag
        create_glb_enabled = output_format in ("glb", "fbx_glb")
        glb_only = output_format == "glb"
        # "usd": the deliverable is a USD layer. Same pipeline up to the write;
        # the FBX-only knobs (preset, takes, GLB) are reported inert below rather
        # than silently ignored. USDZ is deliberately not offered (no consumer).
        usd = output_format == "usd"
        self._usd_options = dict(usd_options or {})
        if usd:
            for key in ("apply_declared_takes", "set_bake_animation_range"):
                if tasks.get(key):
                    self.logger.warning(
                        f"Task '{key}' sets FBX take/animation-range flags only; "
                        "a USD export samples the frames that carry motion instead."
                    )

        # Texture File Type: ONE container dial for every texture the export
        # ships — the scene maps the optimization pass writes AND a GLB's
        # embedded copies (each destination clamps what it cannot carry; see
        # TaskManager._resolved_output_type / _glb_texture_params). Parsed here
        # so the KTX2 gate can fail BEFORE any scene work, and stamped per run
        # on the task manager (the ``_optimize_keys_level`` pattern).
        #
        # ``glb_texture_format`` is the legacy key this replaced (it drove the
        # GLB alone, beside a redundant "Optimize GLB Textures" flag that the
        # general Optimize Textures now covers); an older template keeps
        # working, with the new key winning when both are present.
        texture_file_type = str(tasks.pop("texture_file_type", "") or "").lower()
        legacy_glb_format = str(tasks.pop("glb_texture_format", "") or "").lower()
        tasks.pop("glb_optimize_textures", None)  # redundant: see Optimize Textures
        if not texture_file_type and legacy_glb_format:
            texture_file_type = legacy_glb_format
            self.logger.debug(
                f"Legacy 'glb_texture_format' {legacy_glb_format!r} read as "
                "'texture_file_type'."
            )
        texture_file_type = texture_file_type.lstrip(".") or None
        known = set(TaskManager._texture_file_type_options.values()) - {None, ""}
        if texture_file_type and texture_file_type not in known:
            # A hand-edited template / headless caller can send anything; an
            # unknown value discovered here is a config error and aborts
            # loudly — discovered at encode time it would fail per-image and
            # ship an effectively-unencoded texture set behind warning noise.
            self.logger.error(
                f"Export aborted: unknown texture_file_type "
                f"{texture_file_type!r} (expected one of "
                f"{', '.join(sorted(known))}, or empty for Original)."
            )
            return False
        if texture_file_type == "ktx2":
            if not create_glb_enabled:
                # KTX2 is a delivery-only container: no scene file node or FBX
                # importer reads it, so with no GLB to carry it the choice has
                # nowhere to land. Inert, not an error.
                self.logger.info(
                    "Texture File Type 'KTX2' ignored: it can only ship inside "
                    "a GLB, and the output format produces none."
                )
                texture_file_type = None
            else:
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
        self.task_manager._texture_file_type = texture_file_type

        # Texture-processing inputs, stamped per run (the
        # ``_optimize_keys_level`` pattern): the Texture Output combo's
        # write-back flag (a mode read by convert_textures and
        # optimize_textures, never a dispatched task), and the GLB-only marker
        # the staging policy (temp vs durable) keys off, alongside whether an
        # embedded-media FBX will carry its own texture copies.
        # Legacy key, same shape as the ``create_glb`` mapping above: presets
        # saved before the rename carry ``optimize_textures_write_back``. Left
        # unmapped it survives the pop, reaches _execute_tasks_and_checks as an
        # unknown task, and TaskFactory logs "Missing method ... Skipping." --
        # so the run silently falls back to Export Copies and the user's saved
        # write-back setting is lost with only a debug line to show for it.
        _write_back = tasks.pop("texture_write_back", None)
        if _write_back is None:
            _write_back = tasks.pop("optimize_textures_write_back", False)
        else:
            tasks.pop("optimize_textures_write_back", None)  # new key wins
        self.task_manager._texture_write_back = bool(_write_back)
        # The Animation Output combo's twin of that flag: whether the four
        # key-editing tasks leave their edits in the scene, or have them
        # captured and restored around the write. A mode, never a dispatched
        # task — and popped rather than read, so an unknown-task warning
        # cannot be what tells the user their setting was ignored. Default
        # False (Export Copies): an export is an act of publishing, and until
        # this gate existed it silently rewrote the artist's curves.
        self.task_manager._animation_write_back = bool(
            tasks.pop("animation_write_back", False)
        )
        # Deliverable verification — the post-write pass that re-opens the
        # FBX/GLB and gates the bytes that shipped. A Checks-panel row, but
        # never a dispatched check (there is no ``check_`` method behind it and
        # it judges a file the pre-export phase has not produced yet), so it is
        # popped here with the other UI-only modes. Default OFF: it is the one
        # pass whose cost scales with the FBX rather than the scene, and it
        # runs at the END of a long export where that cost is least welcome.
        verify_deliverables = bool(tasks.pop("verify_deliverables", False))

        # The optimization pass's size dial (OFF / a pixel ceiling / the
        # template-budget sentinel), read by optimize_textures and its paired
        # check through _texture_size_clamp — a mode like the write-back flag,
        # never a dispatched task. In the panel it rides the Optimize Textures
        # combo (b000 decomposes the choice into this key); headless callers
        # pass it explicitly. Falsy = OFF, so a caller that omits it exports
        # exactly as before.
        self.task_manager._texture_max_size = tasks.pop("texture_max_size", None)
        # What the texture pass was asked for, read (not popped — they are real
        # tasks) so the GLB half can resolve the same two dials after the
        # pipeline has run (``TaskManager._glb_texture_params``). Stamped HERE
        # with every other per-run mode rather than inside
        # ``_execute_tasks_and_checks``: ``run_tasks`` returns early on an empty
        # task dict, so a run with nothing checked would otherwise leave the
        # PREVIOUS run's values standing and re-encode the GLB behind the user.
        optimize_textures = tasks.get("optimize_textures")
        self.task_manager._optimize_textures_enabled = bool(optimize_textures)
        template = tasks.get("convert_textures")
        self.task_manager._texture_template = (
            template
            if isinstance(template, str)
            else (optimize_textures if isinstance(optimize_textures, str) else None)
        )
        self.task_manager._glb_only = glb_only
        # Read by check_output_writable, which has to know whether a .glb
        # sibling is a destination this run will write.
        self.task_manager._create_glb_enabled = create_glb_enabled

        # Generate the export path (with versioning applied if requested).
        self.export_path = self.generate_export_path(
            version_format=version_format, extension=".usd" if usd else ".fbx"
        )
        self.logger.debug(f"Generated export path: {self.export_path}")

        if self.create_log_file:
            self._setup_file_logging()

        # Initialize objects
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

        # Make export path available to checks (e.g. hierarchy diff).  The
        # `_version_format` flag tells the hierarchy check to route sidecar
        # paths through SceneDataSidecar.base_stem so all versions of a
        # series share one manifest.
        self.task_manager.export_path = self.export_path
        self.task_manager._version_format = version_format

        export_succeeded = False
        self._overridden_checks = []  # per-run; see the attribute's __init__ note
        # Progress: the pipeline's entries, then the write, a GLB conversion,
        # the sidecar and an opt-in verification -- one count for the run.
        self._progress_begin(
            progress_callback,
            tasks,
            phases=2 + int(create_glb_enabled) + int(verify_deliverables),
        )
        try:
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
                        # Checks run AFTER tasks, and tasks mutate the scene with no
                        # automatic rollback (the undo-chunk restore was removed with
                        # the smart_bake redesign) — a blocked export must say so
                        # instead of leaving the mutation silent.
                        self.logger.warning(
                            "Export blocked by failed checks, but export tasks already "
                            "ran — task edits (material cleanup, key snapping/tying, "
                            "texture path rewrites, …) remain in the scene. Undo or "
                            "revert to the saved file if that is not what you want."
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
                if t_cnt or c_cnt:
                    export_info_lines.append("")
                    export_info_lines.append(f"Tasks Executed: {t_cnt}")
                    if c_cnt:
                        # Never "N/N" after an override: the deliverable shipped
                        # WITH known failures and the banner is the record of it.
                        export_info_lines.append(
                            f"Checks Passed: {c_cnt - f_cnt}/{c_cnt}"
                        )
                        if f_cnt:
                            export_info_lines.append(
                                f"Checks Overridden: {', '.join(overridden)}"
                            )

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
                self.task_manager.write_scene_data_sidecar()

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
                        deliverable_path, glb_alongside
                    )
                self._progress_finish("Export complete")
            except Exception as e:
                self.logger.error(f"Failed to export objects: {e}")
                raise RuntimeError(f"Failed to export objects: {e}")
            finally:
                _FbxUtils.end_export()
                if glb_tempdir:
                    shutil.rmtree(glb_tempdir, ignore_errors=True)
                if self.create_log_file:
                    self.close_file_handlers()
        except ptk.OperationCancelled as e:
            # The progress callback asked to stop (Esc held over the panel's
            # footer; a headless caller's own gate). Only reachable before the
            # write -- see _emit_progress -- so nothing shipped; the tasks that
            # already ran stay applied, exactly as after a failed check.
            export_succeeded = False
            self._export_cancelled = True
            self.logger.warning(
                f"Export {e or 'cancelled'}. Task edits already made remain in "
                "the scene — undo or revert to the saved file if that is not "
                "what you want."
            )
        finally:
            # The bake session unwinds FIRST -- smart_bake ran after every
            # staged task, so this is the innermost stage (LIFO). Concretely:
            # its matrix records reconnect offsetParentMatrix to whatever
            # drove it at bake time, which for flattened chains is the
            # flatten task's rewrap multMatrix -- a node the deferred flatten
            # restore below deletes.
            # Restore the scene state recorded by smart_bake's session
            # manifest: deletes the override layer, re-enables IK handles
            # (bakeResults' disableImplicitControl zeroes ikBlend even when
            # baking to a layer), and restores any baked visibility.
            _keep_bake = getattr(self.task_manager, "_animation_write_back", False)
            _session = getattr(self.task_manager, "_bake_session_id", None)
            if _session and _keep_bake:
                # Animation Output: Scene Keys (In Place) — the bake is the
                # point, so the override layer and its curves stay. The
                # manifest is left recorded so `SmartBake.restore('<id>')`
                # remains available by hand.
                self.logger.info(
                    f"Bake kept in the scene (session '{_session}') — Animation "
                    "Output is set to Scene Keys (In Place)."
                )
                # Except the matrix bakes: their keys were written in the
                # flatten task's staged parent space, and the deferred
                # flatten restore below reinstates the original
                # offsetParentMatrix wiring — composing it on top of the
                # kept keys would double-transform those nodes. Hand exactly
                # those channels back to their live drivers; the layer and
                # every scalar bake stay.
                try:
                    from mayatk.anim_utils.smart_bake._smart_bake import (
                        SmartBake,
                    )

                    _mw = SmartBake.restore_matrix_wiring(_session)
                    if _mw.matrix_restored:
                        self.logger.info(
                            "Matrix-driven channels handed back to their "
                            f"live drivers ({len(_mw.matrix_restored)} "
                            "object(s)) — baked matrix keys cannot survive "
                            "the hierarchy restore."
                        )
                except Exception as e:
                    self.logger.error(f"Matrix-wiring restore failed: {e}")
                self.task_manager._bake_session_id = None
                self.task_manager._bake_override_layer = None
                _session = None
            if _session:
                try:
                    from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

                    restore = SmartBake.restore(_session)
                    if restore.success:
                        self.logger.info(
                            f"Restored pre-bake scene state (session '{_session}')."
                        )
                        self.task_manager._bake_override_layer = None
                    else:
                        # SmartBake reports non-restorable sessions via
                        # cmds.warning only (script editor) — surface it in the
                        # export log too, with the session id so a manual
                        # SmartBake.restore('<id>') retry is possible. The
                        # layer-delete fallback below still runs, but IK blend
                        # and visibility state may need that manual restore.
                        self.logger.warning(
                            f"SmartBake restore failed for session '{_session}' — "
                            "the bake override layer is deleted as a fallback, but "
                            "IK/visibility state may need a manual "
                            f"SmartBake.restore('{_session}')."
                        )
                except Exception as e:
                    # Never mask an export exception from inside finally —
                    # the layer-delete fallback below still runs.
                    self.logger.error(f"SmartBake restore failed: {e}")
                self.task_manager._bake_session_id = None
            # Fallback for bakes recorded without a session manifest.
            _layer = getattr(self.task_manager, "_bake_override_layer", None)
            if _layer and cmds.objExists(_layer):
                cmds.delete(_layer)
                self.logger.info(
                    f"Deleted bake override layer '{_layer}' — scene restored."
                )
                self.task_manager._bake_override_layer = None
            # Scene state the FBX write itself reads (working linear unit,
            # active workspace) is staged rather than set_/revert_-paired,
            # because that pairing fires before the write. Undo it here, on
            # every exit path — a failed check, a raising task, or a bad write.
            self.task_manager.run_deferred_restores()
            self._progress_end()

        if not export_succeeded:
            return False

        # Tasks/checks already ran (and any GLB conversion completed) before
        # this point; a True return means the deliverable was written.
        return True

    #: The extensions an output name may carry -- stripped before the format's
    #: own is appended, so "asset.fbx" typed into a USD export lands as
    #: "asset.usd" rather than "asset.fbx.usd". The carrier vocabulary, not a
    #: second list (``CARRIER_BY_EXTENSION`` holds every USD spelling too).
    _DELIVERABLE_EXTENSIONS = tuple(ptk.CARRIER_BY_EXTENSION)

    def generate_export_path(
        self, version_format: str = "", extension: str = ".fbx"
    ) -> str:
        """Generate the full export file path.

        Parameters:
            version_format: If non-empty, treat as a pythontk-style
                placeholder template (e.g. ``{stem}_v{n:03d}``) and resolve
                the next-version path via ``FileUtils.next_version_path``.
            extension: The deliverable's extension (``.fbx`` / ``.usd``); the
                version scan and the wildcard match are per-extension.
        """
        extension = extension.lower()
        # Handle wildcard matching for output_name to overwrite existing files
        if self.output_name and any(char in self.output_name for char in "*?"):
            import glob

            pattern = self._strip_deliverable_extension(self.output_name)
            pattern += extension

            search_path = os.path.join(self.export_dir, pattern)
            matches = glob.glob(search_path)

            if matches:
                matches.sort()
                action = "using as version seed" if version_format else "overwriting"
                self.logger.info(
                    f"Wildcard '{self.output_name}' matched {len(matches)} files; "
                    f"{action}: {matches[-1]}"
                )
                # Wildcard + versioning composes: pick latest match, then bump.
                return self._apply_versioning(matches[-1], version_format)

        scene_path = cmds.file(query=True, sceneName=True) or "untitled"
        scene_name = os.path.splitext(os.path.basename(scene_path))[0]
        export_name = self._strip_deliverable_extension(self.output_name or scene_name)
        if self.timestamp:
            export_name += f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        export_name = self.format_export_name(export_name)
        path = os.path.join(self.export_dir, f"{export_name}{extension}")
        return self._apply_versioning(path, version_format)

    @classmethod
    def _strip_deliverable_extension(cls, name: str) -> str:
        """*name* without a trailing deliverable extension (whitelist strip: a
        dotted version token is not an extension)."""
        return ptk.StrUtils.strip_suffix(name, cls._DELIVERABLE_EXTENSIONS)

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

    def _apply_versioning(self, path: str, template: str) -> str:
        """Resolve a version template into a concrete versioned path.

        Two-stage substitution:
          - Stage 1: substitute ``{date}``, ``{user}``, ``{scene}`` via
            ``StrUtils.replace_placeholders`` (which preserves unresolved
            ``{stem}``/``{n:NNd}`` placeholders along with their format spec).
          - Stage 2: ``FileUtils.next_version_path`` resolves the next
            available ``{n}`` by scanning the parent directory.

        The user-facing template does not include ``{ext}``; the extension
        from ``path`` is appended internally so that on-disk versioned
        siblings (which carry the extension) are matched correctly.

        Returns the original path unchanged when the template is empty or
        a guard condition prevents safe versioning (logs a warning in that
        case so the user sees what happened).
        """
        if not template:
            return path

        if "{ext}" in template:
            self.logger.warning(
                "Version format should not include '{ext}' — extension is "
                "handled automatically. Versioning skipped."
            )
            return path

        stem, ext = os.path.splitext(os.path.basename(path))
        if not stem or stem.lower() == "untitled":
            self.logger.warning(
                "Skipping versioning: export name is untitled — save the scene "
                "or pass an explicit output_name."
            )
            return path

        # Stage 1: gather dynamic context and substitute.
        import getpass

        scene_path = cmds.file(query=True, sceneName=True) or ""
        scene_name = (
            os.path.splitext(os.path.basename(scene_path))[0] if scene_path else ""
        )

        if "{scene}" in template and not scene_name:
            self.logger.error(
                "Version format uses '{scene}' but the scene is unsaved. "
                "Save the scene or remove '{scene}' from the format. "
                "Versioning skipped."
            )
            return path

        expanded = ptk.StrUtils.replace_placeholders(
            template,
            date=datetime.now().date().isoformat(),
            user=getpass.getuser(),
            scene=scene_name,
        )

        # Warn only when the resulting name carries no source identity at all
        # — i.e., neither {stem} (output basename) nor {scene} (Maya scene
        # name) was used in the template.
        if "{stem}" not in expanded and "{scene}" not in template:
            self.logger.warning(
                "Version format missing '{stem}' and '{scene}' — output name "
                "and scene identity will not appear in the resulting filename."
            )

        # Stage 2: append {ext} for next_version_path's matching.
        internal_format = expanded + "{ext}"

        # Validation: does the resulting name end in `_v\d+` so the hierarchy
        # sidecar can pair across versions?  Use format_map with a defaulting
        # dict so any user-typo placeholders don't crash the validator.
        class _Dummy(dict):
            def __missing__(self, key):
                return "x"

        try:
            test_name = internal_format.format_map(_Dummy(stem="test", n=1, ext=ext))
            test_stem = os.path.splitext(test_name)[0]
            if not SceneDataSidecar.VERSION_SUFFIX_RE.search(test_stem):
                self.logger.warning(
                    f"Version format {template!r} produces names not matching "
                    "'_v<N>' — hierarchy diff baseline will not carry across "
                    "versions."
                )
        except (ValueError, IndexError, KeyError) as e:
            self.logger.warning(f"Could not validate version format: {e}")

        try:
            new_path = ptk.FileUtils.next_version_path(path, format=internal_format)
        except ValueError as e:
            self.logger.error(f"Version format invalid: {e}. Versioning skipped.")
            return path

        self.logger.info(
            f"Versioned export path: {os.path.basename(path)} -> "
            f"{os.path.basename(new_path)}"
        )
        return new_path

    def format_export_name(self, name: str) -> str:
        """Format the export name using a regex pattern and replacement (e.g. 'pattern->replace')."""
        if self.name_regex:
            # Try to find a delimiter
            for delim in ("->", "=>", "|"):
                if delim in self.name_regex:
                    pattern, replacement = self.name_regex.split(delim, 1)
                    break
            else:
                pattern, replacement = self.name_regex, ""
            # Strip whitespace and apply
            pattern = pattern.strip()
            replacement = replacement.strip()
            try:
                return re.sub(pattern, replacement, name)
            except re.error as e:
                self.logger.error(f"Invalid regex pattern: {pattern}. Error: {e}")
                return name
        return name

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


class SceneExporterSlots(SceneExporter):
    _log_level_options: Dict[str, Any] = {
        "Log Level: DEBUG": 10,
        "Log Level: INFO": 20,
        "Log Level: WARNING": 30,
        "Log Level: ERROR": 40,
    }

    def __init__(self, switchboard, log_level="WARNING"):
        # Initialize the parent SceneExporter class first
        super().__init__(log_level=log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.scene_exporter

        self.ui.txt001.setText("")  # Output Name
        self.ui.txt003.setText("")  # Log Output

        self._wire_dependencies()

        self._init_override_button(self.ui.b009)

        self.logger.setLevel(log_level)
        self.logger.hide_logger_name(True)  # Hide the logger name in output
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)

        # Connect clickable log links (action:// URIs in QTextBrowser)
        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

    @staticmethod
    def _init_override_button(widget) -> None:
        """Arm-once styling for the Override Checks toggle.

        ``restore_state = False`` is the load-bearing line: a registered widget
        persists through QSettings by default and its restore runs AFTER the
        slots ``__init__``, so the toggle used to come back armed in the next
        session -- right over the ``setChecked(False)`` here. A per-run escape
        hatch that survives a restart is a validation pass silently disabled.
        """
        widget.restore_state = False
        widget.setEnabled(True)
        widget.setChecked(False)
        widget.setStyleSheet("QPushButton:checked {background-color: #FF9999;}")

    def _wire_dependencies(self) -> None:
        """Grey out a setting while a lower-level choice makes it irrelevant.

        One ``sb.enable_when`` rule per dependency — declared once here, order-
        independent (the rows register later; the rule picks them up), and
        re-applied by the trigger's own change signal, so there is no per-
        trigger slot and no ``_sync_*`` helper to keep in step. A preset load
        applies with signals unblocked (``cmb007_init``), so these follow it too.
        """
        sb, ui = self.sb, self.ui
        # Texture File Type is the container dial for every texture the export
        # ships, so it is NOT gated on Optimize Textures: a GLB deliverable is
        # re-encoded to it whether or not the scene pass runs. The pass's size
        # ceiling needs no rule at all any more — it rides the Optimize
        # Textures combo itself ("Optimize + Max …"), so a ceiling with
        # nothing to apply it is unrepresentable rather than greyed out.
        # Texture Output only matters once a texture-processing task runs —
        # Optimize Textures, or the conversion a Texture Template arms.
        sb.enable_when(
            ui,
            "texture_write_back",
            ["texture_optimize", "cmb005"],
            lambda optimize, template: bool(optimize) or bool(template),
        )
        # Exclude HDR: the visible-geometry scope never contains a skydome
        # (surface shapes only); All / Selected can.
        sb.enable_when(
            ui,
            "exclude_hdr",
            "export_visible_objects",
            lambda scope: scope != "visible",
        )
        # Verify The Written File reads the shipped bytes with pythontk's
        # FBX/GLB gates; a USD deliverable gives them nothing to open, so the
        # row would sit armed and inert.
        sb.enable_when(
            ui,
            "verify_deliverables",
            "cmb004",
            lambda output_format: output_format != "usd",
        )

    def confirm(self, question: str) -> bool:
        """The engine's consent seam as the panel's modal Yes/No.

        ``message_box`` takes HTML and hands it to Qt's rich-text engine, which
        collapses a newline to a space -- so the seam's plain text (documented
        as "newlines allowed") arrived as one run-on paragraph. Translate here,
        at the one place that knows the destination is a rich-text widget,
        rather than making every caller author HTML.
        """
        body = html.escape(question).replace("\n", "<br>")
        return self.sb.message_box(body, "Yes", "No") == "Yes"

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    @property
    def workspace(self) -> Optional[str]:
        workspace_path = EnvUtils.get_env_info("workspace")
        if not workspace_path:
            self.logger.error("Workspace directory not found.")
        return workspace_path

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

    def header_init(self, widget):
        """Initialize the header widget (log options; the export preset lives
        in the panel as ``cmb007``)."""
        widget.menu.add(
            "QCheckBox",
            setText="Create Log File",
            setObjectName="b011",
            setChecked=False,
            setToolTip="Export a log file along with the fbx.",
        )
        widget.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb003",  # Renamed from cmb001 to avoid collision
            add=self._log_level_options,
            setCurrentIndex=1,  # Default to INFO
            setToolTip="Set the log level.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Scene Exporter",
                body="Batch-export scene objects to FBX using configurable "
                "task pipelines and YAML presets.",
                steps=[
                    "Pick an export <b>Preset</b> — the whole panel's "
                    "configuration under a name (Save / Rename / Delete from "
                    "its toolbar).",
                    "Adjust <b>Settings</b> (FBX preset, format, units, scope, "
                    "texture template), <b>Tasks</b> (scene prep) and "
                    "<b>Checks</b> (validation gates), and set the output path.",
                    "Press <b>Export</b> to run.",
                ],
                sections=[
                    (
                        "Header menu",
                        [
                            "<b>Create Log File</b> — write a sidecar log next to "
                            "each FBX.",
                            "<b>Log Level</b> — DEBUG / INFO / WARNING / ERROR / "
                            "CRITICAL output verbosity.",
                        ],
                    ),
                ],
            )
        )

    def cmb000_init(self, widget) -> None:
        """Init FBX Preset — a Settings row (``cmb008``), created by
        :meth:`cmb008_init` and registered by objectName.

        Preset management (open the directory, edit the selection) lives in
        this row's own option box — the ☐ beside the combo — so it sits on the
        widget it configures instead of the parent combo's actions section,
        alongside a refresh button that re-scans the preset directory. The
        directory itself is fixed (see :meth:`_get_preset_dir`), mirroring
        blendertk.
        The option-box wrap swaps the combo for its container in the row
        layout (``replaceWidget``); the row's bookkeeping keys off the widget
        itself, so the swap is invisible to it.
        """
        if not widget.is_initialized:
            widget.restore_state = True  # Enable state restore
            widget.refresh_on_show = True  # Call this method on show
            # Persist the selection by preset NAME, not combo index: the item
            # list is rebuilt from a directory scan each show, so an index saved
            # one session points at a different preset (or out of range -> "None")
            # the next. See StateManager.restore_by / _RESTORE_MODES.
            widget.restore_by = "text"

            widget.option_box.menu.setTitle("FBX Preset:")
            widget.option_box.menu.add_defaults_button = False
            widget.option_box.menu.add(
                "QPushButton",
                setText="Open FBX Preset Directory",
                setObjectName="b007",
                setToolTip="Open the FBX preset directory in the file browser.",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Edit FBX Preset",
                setObjectName="b008",
                setToolTip="Load the selected preset and open the FBX preset editor.",
            )

            # Sorts ahead of the option-box menu button (DEFAULT_OPTION_ORDER:
            # "action" before "menu"). ``refresh_on_show`` already re-scans when
            # the panel opens; this is for a preset added, renamed or deleted
            # while it is sitting open — Maya's own preset editor is the common
            # case, and the panel has no way to hear about it.
            widget.option_box.add_action(
                callback=self._refresh_presets,
                icon="refresh",
                tooltip="Re-scan the FBX preset directory for presets added, renamed or removed since the panel opened.",
            )

        # Store current selection before refresh
        current_data = widget.currentData() if widget.count() > 0 else None
        current_text = widget.currentText() if widget.count() > 0 else ""

        # Refresh the preset data. Read the scan ONCE — the warning and the
        # selection-restore below must agree with the list actually shown
        # (mirrors blendertk's cmb000_init).
        presets = self.presets
        widget.add(presets, clear=True)

        # Warn if no presets or directory issues
        if hasattr(self.ui, "txt003"):
            preset_dir = self._get_preset_dir()
            if not preset_dir or not os.path.exists(preset_dir):
                self.ui.txt003.setHtml(
                    "<span style='color:orange'>Warning: Maya's user preset directory was not found.</span>"
                )
            elif len(presets) <= 1:  # Only "None"
                self.ui.txt003.setHtml(
                    "<span style='color:orange'>Warning: No presets found in the preset directory.<br>"
                    "Drop .fbxexportpreset files into it (FBX Preset ▸ option box ▸ Open FBX Preset Directory).</span>"
                )

        # Restore previous selection if it still exists
        if current_data and current_data in presets.values():
            # Find the text key for the preset path
            for text, path in presets.items():
                if path == current_data:
                    widget.setCurrentText(text)
                    self.logger.debug(f"Restored preset selection: {text}")
                    break
        elif current_text and current_text in presets:
            widget.setCurrentText(current_text)
            self.logger.debug(f"Restored preset selection by text: {current_text}")

    def txt000_init(self, widget) -> None:
        """Init Output Directory"""
        widget.option_box.menu.setTitle("Output Directory:")
        widget.option_box.menu.add_defaults_button = False
        widget.option_box.menu.add(
            "QPushButton",
            setToolTip="Set the output directory.",
            setText="Set Output Directory",
            setObjectName="b010",
        )
        widget.option_box.menu.add(
            "QPushButton",
            setToolTip="Open the output directory.",
            setText="Open Output Directory",
            setObjectName="b006",
        )

        # Recent output directories — option box button with history popup
        from uitk.widgets.optionBox.options.recent_values import RecentValuesOption

        self._recent_dirs_option = RecentValuesOption(
            wrapped_widget=widget,
            settings_key="scene_exporter_output_dirs",
            max_recent=10,
            display_format=lambda p: (
                "\u2026/" + "/".join(ptk.format_path(p).split("/")[-3:])
                if len(ptk.format_path(p).split("/")) > 3
                else str(p)
            ),
            text_align="left",
        )
        widget.option_box.add_option(self._recent_dirs_option)

        # Seed from legacy QSettings if the plugin's store is empty
        if not self._recent_dirs_option.recent_values:
            for d in self._get_legacy_output_dirs():
                self._recent_dirs_option.add_recent_value(d)

    def txt001_init(self, widget) -> None:
        """Init Output Name"""
        widget.option_box.menu.setTitle("Output Name:")
        widget.option_box.menu.add_defaults_button = False
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QPushButton",
            setToolTip=(
                "Name the export after an existing file.\n\n"
                "Opens a file browser at the current output directory. The chosen "
                "file's name (without extension) becomes the output name, and the "
                "output directory follows the file if it lives elsewhere."
            ),
            setText="Browse for File",
            setObjectName="b012",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setToolTip="Add a timestamp suffix to the output filename.",
            setText="Timestamp",
            setObjectName="chk004",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setToolTip=(
                "Regex pattern for formatting the output name.\n\n"
                "Format:  PATTERN->REPLACEMENT\n"
                "Examples:\n"
                "  _bar.*->       Remove '_bar' and everything after\n"
                "  (foo|bar)->baz    Replace 'foo' or 'bar' with 'baz'\n"
                "Use standard Python regular expressions. If no '->', everything matching PATTERN is removed."
            ),
            setPlaceholderText="RegEx",
            setObjectName="txt002",
        )

        # Recent output filenames — option box button with history popup
        from uitk.widgets.optionBox.options.recent_values import RecentValuesOption

        self._recent_names_option = RecentValuesOption(
            wrapped_widget=widget,
            settings_key="scene_exporter_output_filenames",
            max_recent=10,
            display_format="basename",
            text_align="left",
        )
        widget.option_box.add_option(self._recent_names_option)

    # Rows of the Settings combo (cmb008), by group. Names resolve to a UI-only
    # widget spec (``_SETTINGS_WIDGETS``) or to a ``task_definitions`` entry
    # tagged ``"panel": "settings"`` — a task the engine dispatches (or a flag
    # ``perform_export`` pops) that the USER experiences as a write/scope
    # setting rather than scene prep. Order here is display order; a name a
    # DCC's definitions lack (blendertk has no set_workspace) is skipped, so
    # the layout is shared verbatim between the two panels.
    _SETTINGS_LAYOUT = (
        (
            "Output",
            (
                "cmb000",
                "cmb004",
                "set_linear_unit",
                "set_workspace",
                "version",
            ),
        ),
        (
            "Scope",
            (
                "export_visible_objects",
                "ignore_groups",
                "exclude_hdr",
                "export_data_node",
            ),
        ),
        # No Textures section: every texture dial — Texture Output included —
        # lives in the Tasks combo's Textures group, the gate row directly
        # above the three rows it governs (see task_definitions).
    )

    #: Settings rows with no task/check definition behind them. Each keeps the
    #: objectName it had as a main-layout / option-box widget so its ``_init``
    #: slot, ``b000``'s reads and every saved export preset stay valid.
    _SETTINGS_WIDGETS = {
        "cmb000": {
            "widget_type": "ComboBox",
            "set_row_label": "FBX Preset",
            "setToolTip": (
                "FBX export preset applied to the write — the FBX plug-in's own "
                "options (units, axis, geometry, animation).\n"
                "It governs a GLB output too: the GLB is converted from this "
                "FBX write, so the preset's geometry/animation choices carry "
                "through.\n"
                "'None' writes with Maya's current FBX settings.\n"
                "The option box beside this row opens the preset folder "
                "or the FBX preset editor."
            ),
        },
        "cmb004": {
            "widget_type": "ComboBox",
            "set_row_label": "Format",
            "setToolTip": "Output file format: FBX, GLB, or both.",
        },
    }

    #: Definition keys that describe the row, not the widget — stripped before
    #: the remainder is applied as widget attributes.
    _DEFINITION_META_KEYS = (
        "widget_type",
        "panel",
        "group",
        "object_name",
        "value_method",
    )

    def _make_definition_widget(self, name, params, object_name=None):
        """Instantiate the widget a task/check/settings definition describes."""
        params = dict(params)
        widget_type = params.get("widget_type", "QCheckBox")
        object_name = object_name or params.get(
            "object_name", self.sb.convert_to_legal_name(name)
        )
        widget_class = getattr(self.sb.QtWidgets, widget_type, None)
        if widget_class is None:
            widget_class = getattr(self.sb.registered_widgets, widget_type, None)
            if widget_class is None:
                raise ValueError(f"Unknown widget type: {widget_type}")
        for key in self._DEFINITION_META_KEYS:
            params.pop(key, None)
        widget = widget_class()
        self.ui.set_attributes(widget, setObjectName=object_name, **params)
        return widget

    def _definition_rows(self, definitions, panel=None):
        """``[(widget, label)]`` for a WidgetComboBox: one row per definition
        whose ``panel`` tag matches, with a titled Separator wherever the
        ``group`` tag changes — the group sequence IS the section order, so no
        hand-placed separator entries."""
        rows = []
        current_group = None
        for name, params in definitions.items():
            if params.get("panel") != panel:
                continue
            group = params.get("group")
            if group and group != current_group:
                rows.append((self.sb.registered_widgets.Separator(title=group), group))
                current_group = group
            rows.append((self._make_definition_widget(name, params), name))
        return rows

    def cmb001_init(self, widget) -> None:
        """Tasks — scene-prep steps the engine dispatches (``TASK_ORDER``),
        grouped by their ``group`` tag; entries tagged ``panel: settings``
        render in ``cmb008`` instead."""
        widget.add(
            self._definition_rows(self.task_manager.task_definitions),
            header="Tasks",
            clear=True,
        )

    def cmb002_init(self, widget) -> None:
        """Validation Checks — the gates that abort the write, grouped by tag."""
        widget.add(
            self._definition_rows(self.task_manager.check_definitions),
            header="Validation Checks",
            clear=True,
        )

    def cmb007_init(self, widget) -> None:
        """Export Preset — the whole panel's run configuration under a name.

        The window's ``PresetManager`` wired onto this main-layout combo (the
        canonical Refresh / Save / ⋯ toolbar comes from ``wire_combo``), the
        same pattern curtain's ``cmb000`` uses. ``scope="window"`` captures
        every registered value-bearing widget — the Settings / Tasks / Checks
        rows (they register by objectName like any main-layout widget), the
        header menu's log options — minus the machine/scene-specific fields:
        output dir (txt000), output filename (txt001), log output (txt003).
        The preset combo itself is always excluded internally. The selected
        FBX preset file rides along as embedded metadata so a preset shared
        to another machine restores it (``_fbx_preset_metadata_provider``).
        """
        mgr = self.ui.presets
        # Adopt this panel's logger (instance-scoped) so the manager's
        # user-facing lines -- notably the schema-drift "preset doesn't cover
        # N new panel settings" warning -- reach the txt003 log sink instead
        # of only the console. Must precede wire_combo: the active-preset
        # restore it triggers is exactly the load that warns.
        mgr.use_logger(self.logger)
        mgr.setup(
            preset_dir="mayatk/scene_exporter",
            metadata_provider=self._fbx_preset_metadata_provider,
            on_metadata_loaded=self._on_fbx_preset_metadata_loaded,
        )
        mgr.scope = "window"
        mgr.exclude("txt000", "txt001", "txt003")
        # No on_loaded: a preset then applies with signals UNBLOCKED, so the
        # enable_when dependencies (see _wire_dependencies) follow the loaded
        # values on their own.
        mgr.wire_combo(widget, placeholder="Preset…")

    def cmb008_init(self, widget) -> None:
        """Settings — what is written and from what (the scene-prep steps are
        Tasks). Rows come from :attr:`_SETTINGS_LAYOUT`; the FBX-preset
        management lives on the ``cmb000`` row's own option box
        (``cmb000_init``)."""
        definitions = self.task_manager.task_definitions
        rows = []
        for group, names in self._SETTINGS_LAYOUT:
            rows.append((self.sb.registered_widgets.Separator(title=group), group))
            for name in names:
                spec = self._SETTINGS_WIDGETS.get(name)
                if spec is not None:
                    rows.append(
                        (
                            self._make_definition_widget(name, spec, object_name=name),
                            name,
                        )
                    )
                elif name in definitions:
                    rows.append(
                        (self._make_definition_widget(name, definitions[name]), name)
                    )
        widget.add(rows, header="Settings", clear=True)

    def _refresh_presets(self) -> None:
        """Re-scan the FBX preset directory (the ``cmb000`` refresh button).

        Drops the scan cache before re-running :meth:`cmb000_init`, which
        re-reads ``self.presets`` and restores the current selection if it
        survived. Invalidating explicitly rather than leaning on the cache's
        mtime key is the point of the button: that key is a filesystem
        timestamp (~15ms granularity on Windows), so a preset dropped in and
        a refresh clicked in the same tick would be served the stale dict —
        the button has to mean "re-scan", unconditionally.
        """
        self._invalidate_preset_cache()
        self.ui.cmb000.init_slot()
        self.logger.debug("Refreshed the FBX preset list.")

    #: The Ignore row's case toggle, set by :meth:`ignore_groups_init`. Held on
    #: the slots instance (the ``_recent_names_option`` idiom below) rather than
    #: re-resolved off the row each export: it is a plain Python object, so
    #: unlike a Qt wrapper it cannot be invalidated by the option box's reparent.
    #: ``None`` until that slot runs — :meth:`_ignore_groups_case_sensitive`
    #: then reports the task's own ``case_sensitive=False`` default.
    _case_toggle = None

    def ignore_groups_init(self, widget) -> None:
        """Init Ignore Groups — a Settings row (``cmb008``), created by
        :meth:`cmb008_init` and registered by objectName.

        The row's own option box carries an "Aa" toggle for the match mode, so
        the case switch sits on the field it governs instead of costing a
        second row. Off (case-insensitive) is the default and the behavior the
        task has always had; :meth:`b000` reads the toggle back when it builds
        the task payload.
        """
        if widget.is_initialized:
            return
        from uitk.widgets.optionBox.options.toggle import ToggleOption

        widget.option_box.set_toggle(
            icon="font",  # an "Aa" glyph — the conventional match-case mark
            tooltip_on="Case-sensitive: names must match exactly. Click to ignore case.",
            tooltip_off="Ignoring case. Click to match case exactly.",
            initial=False,
            # ToggleOption tints its off state the project error red, which suits
            # a toggle whose off state STOPS something; here "off" is the
            # ordinary, default match mode, so it takes the neutral "locked"
            # token. The on state keeps the auto theme colour, so the button
            # reads like every sibling in the option box.
            disabled_color=ptk.Palette.status()["locked"][0],
            # Explicit namespace, and the toggle's ONLY persistence: its button
            # is registered by objectName (``register_children`` sweeps the
            # option box) but carries ``restore_state=False``, so the preset
            # manager's value-only window scope skips it — the export preset
            # stores the Ignore field's text, never the match mode. uitk
            # host-namespaces the key, so sharing this string with the
            # blendertk mirror does not share the state.
            settings_key="scene_exporter_ignore_groups_case_sensitive",
        )
        self._case_toggle = widget.option_box.find_option(ToggleOption)

    def _ignore_groups_case_sensitive(self) -> bool:
        """Whether the Ignore row's case toggle is on.

        ``False`` when :meth:`ignore_groups_init` never ran (no row, or an
        option box that never got wrapped) — the task's own default, so the
        panel still exports.
        """
        return bool(self._case_toggle and self._case_toggle.is_on)

    def cmb004_init(self, widget) -> None:
        """Init Output Format — FBX (default), GLB, FBX + GLB, or USD.

        A Settings row (``cmb008``). ``currentData()`` yields the
        ``output_format`` token ``b000`` forwards to ``perform_export``.
        GLB-only writes the FBX to a temp dir and keeps only the converted
        ``.glb``; FBX + GLB keeps both side by side. The container its embedded
        textures are written in is the general ``texture_file_type`` row (a
        GLB carries what glTF accepts — see ``TaskManager._glb_texture_params``).
        USD writes a ``.usd`` layer through mayaUSDExport (UsdPreviewSurface
        materials; the FBX preset / takes / GLB rows do not apply and say so).
        Items are APPEND-ONLY: the combo persists by index.
        """
        if not widget.is_initialized:
            widget.restore_state = True
        widget.add(
            {"FBX": "fbx", "GLB": "glb", "FBX + GLB": "fbx_glb", "USD": "usd"},
            clear=True,
        )

    def cmb005_init(self, widget) -> None:
        """Init Texture Template — optionally convert textures to a registry workflow.

        The ``convert_textures`` row of the Tasks combo (``cmb001``, Materials
        group), which is where it acts: it arms a pipeline task rather than
        describing the write. The definition loop collects it as
        ``convert_textures`` (task phase) and ``b000`` mirrors it onto
        ``check_material_compatibility`` (check phase), so there are no separate
        rows to keep in sync. "As Authored" (the default) sends textures exactly
        as the scene references them and arms neither.

        Populated from ``ptk.MapRegistry.get_workflow_presets()`` — the same
        registry surface the Map Updater, game shader and converter panels
        render — with each preset's description as its item tooltip.
        """
        from qtpy import QtCore

        if not widget.is_initialized:
            widget.restore_state = True
        presets = ptk.MapRegistry.instance().get_workflow_presets()
        widget.add(
            {"As Authored": None, **{name: name for name in presets}},
            clear=True,
        )
        for index in range(widget.count()):
            description = (presets.get(widget.itemData(index)) or {}).get("description")
            if description:
                widget.setItemData(index, description, QtCore.Qt.ToolTipRole)

    def b000(self) -> None:
        """Export: run the scene export with the configured tasks and settings."""
        self.ui.txt003.clear()
        task_params = {}
        check_params = {}

        # Collect task parameters
        for task_name, params in self.task_manager.task_definitions.items():
            widget_type = params.get("widget_type", "QCheckBox")
            object_name = params.get(
                "object_name", self.sb.convert_to_legal_name(task_name)
            )
            value_method = params.get("value_method")

            widget = getattr(self.ui, object_name, None)

            if not value_method:
                value_method = (
                    "isChecked" if widget_type == "QCheckBox" else "currentData"
                )

            if widget and hasattr(widget, value_method):
                value = getattr(widget, value_method)()
                task_params[task_name] = value

        # Collect check parameters
        for check_name, params in self.task_manager.check_definitions.items():
            widget_type = params.get("widget_type", "QCheckBox")
            object_name = params.get(
                "object_name", self.sb.convert_to_legal_name(check_name)
            )
            value_method = params.get("value_method")

            widget = getattr(self.ui, object_name, None)

            if not value_method:
                value_method = (
                    "isChecked" if widget_type == "QCheckBox" else "currentData"
                )

            if widget and hasattr(widget, value_method):
                value = getattr(widget, value_method)()
                check_params[check_name] = value

        # Texture template: the ``convert_textures`` Tasks row (``cmb005``),
        # already collected above by the definition loop. Mirror it onto the
        # check half here — the gate has no row of its own; the template arms
        # it. Folded BEFORE the override filter so "override checks" keeps the
        # conversion but skips the gate.
        texture_template = task_params.get("convert_textures")
        if texture_template:
            check_params["check_material_compatibility"] = texture_template

        # Optimize Textures (one combo): its value carries the pass switch AND
        # the size ceiling — decomposed here into the two inputs the engine
        # has always taken. The ceiling (an int, or the template-budget
        # sentinel) rides the tasks payload as ``texture_max_size``, which
        # perform_export pops into the per-run mode, so headless callers'
        # explicit key keeps working unchanged. The pass then rides cmb005's
        # template when one is selected — the template's per-map-type output
        # spec drives container/bit depth, its budget stays advisory unless
        # the ceiling half asks for it — else it is the generic per-map-type
        # pass (True). Folded BEFORE the override filter for the same reason
        # as the template: "override checks" keeps the optimization, skips the
        # gate. Where both land (export copies vs the scene's files) is the
        # Texture Output combo, collected above as the ``texture_write_back``
        # flag perform_export pops.
        optimize_choice = task_params.get("optimize_textures")
        if optimize_choice:
            if optimize_choice is not True:
                task_params["texture_max_size"] = optimize_choice
            optimize_value = texture_template or True
            task_params["optimize_textures"] = optimize_value
            check_params["check_texture_optimization"] = optimize_value

        override = self.ui.b009.isChecked()

        # Filter parameters based on override
        if override:  # Only run tasks, skip checks
            task_params = {k: v for k, v in task_params.items() if v}
            check_params = {}  # Skip all checks
        else:  # Run both tasks and checks, but only if checked
            task_params = {k: v for k, v in task_params.items() if v}
            check_params = {k: v for k, v in check_params.items() if v}

        # Ignore Groups: the match mode lives on the row's option-box toggle
        # rather than a row of its own, so the value goes out as the kwargs
        # dict the task dispatcher unpacks instead of a bare string (which
        # ``ignore_groups`` still accepts, at its insensitive default). Folded
        # AFTER the falsy filter above — a dict is always truthy, so folding
        # earlier would keep an empty field in the payload and dispatch a
        # no-op task that still counts toward the run's task total.
        if "ignore_groups" in task_params:
            task_params["ignore_groups"] = {
                "names": task_params["ignore_groups"],
                "case_sensitive": self._ignore_groups_case_sensitive(),
            }

        self.logger.debug(f"Task parameters: {task_params}")
        self.logger.debug(f"Check parameters: {check_params}")

        export_mode = task_params.pop("export_visible_objects", "visible")

        def objects_to_export():
            from maya import cmds

            if export_mode == "visible":
                return DisplayUtils.get_visible_geometry(
                    consider_templated_visible=False,
                    inherit_parent_visibility=True,
                    consider_animated_visible=True,
                )
            elif export_mode == "selected":
                return cmds.ls(selection=True, long=True)
            elif export_mode == "all":
                return cmds.ls(transforms=True, geometry=True, long=True)
            else:
                # Default to visible if unknown mode
                return DisplayUtils.get_visible_geometry(
                    consider_templated_visible=False,
                    inherit_parent_visibility=True,
                    consider_animated_visible=True,
                )

        # Output format (FBX / GLB / FBX+GLB) is the cmb004 Settings row, not
        # the task list; fold it into the tasks payload perform_export consumes.
        export_tasks = {**task_params, **check_params}
        export_tasks["output_format"] = self.ui.cmb004.currentData()

        # The footer's bar (determinate -- the run's own step count arrives
        # with the first tick) plus its busy spinner, because single steps
        # (the FBX write, a GLB conversion) hold the event loop for seconds
        # and a parked bar reads as hung. Esc held over the panel cancels
        # through the same ``update``: perform_export stops before its next
        # step while nothing has been written. ``sb.progress`` is a no-op on
        # a UI without a footer, so the run itself never depends on one.
        with self.sb.progress(
            ui=self.ui, text="Export: preparing…", busy=True
        ) as update:
            exported = self.perform_export(
                objects=objects_to_export,
                export_dir=self.ui.txt000.text(),
                preset_file=self.ui.cmb000.currentData(),
                export_visible=(
                    export_mode != "selected"
                ),  # True unless export mode is "selected"
                output_name=self.ui.txt001.text(),
                name_regex=self.ui.txt002.text(),
                timestamp=self.ui.chk004.isChecked(),
                create_log_file=self.ui.b011.isChecked(),
                log_level=self.ui.cmb003.currentData(),  # Updated from cmb001 to cmb003
                tasks=export_tasks,
                progress_callback=self.sb.progress_adapter(update),
            )
        footer = getattr(self.ui, "footer", None)
        if footer is not None:
            if exported:
                footer.setText("Export complete", level="success")
            elif self._export_cancelled:
                footer.setText("Export cancelled", level="warning")
            else:
                footer.setText("Export aborted — see the log", level="warning")

        output_dir = self.ui.txt000.text()
        self.save_output_dir(output_dir)
        self.save_output_name(self.ui.txt001.text())

        # Override Checks is a per-run escape hatch, not a mode: a successful
        # export disarms it so the next run is validated again. Left armed on a
        # failed export -- the user is still mid-troubleshooting and would
        # otherwise have to re-arm it for every retry.
        if exported:
            self.ui.b009.setChecked(False)

    def b010(self) -> None:
        """Set Output Directory"""
        output_dir = self.sb.dir_dialog(
            title="Select an output directory:", start_dir=self.workspace
        )
        if output_dir:
            self.ui.txt000.setText(output_dir)

    def b012(self) -> None:
        """Browse for Output File -- name the export after an existing file.

        Opens at the currently specified output directory (falling back to the
        workspace when it is unset or gone) and filters to the extensions the
        selected output format (``cmb004``) writes. The pick sets the output
        name to the file's basename and, when the file was chosen from another
        directory, retargets the output directory to match -- so the file the
        user pointed at is the file the next export overwrites.
        """
        start_dir = self.ui.txt000.text()
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = self.workspace or ""

        file_types = {
            "fbx": ["*.fbx"],
            "glb": ["*.glb"],
            "fbx_glb": ["*.fbx", "*.glb"],
            "usd": ["*.usd", "*.usda", "*.usdc"],
        }.get(self.ui.cmb004.currentData(), ["*.fbx", "*.glb", "*.usd"])

        file_path = self.sb.file_dialog(
            file_types=file_types,
            title="Select a file to name the export after:",
            start_dir=start_dir,
            filter_description="Export Files",
            allow_multiple=False,
        )
        if not file_path:
            return

        self.ui.txt001.setText(ptk.format_path(file_path, "name"))

        # Second pass restores the trailing slash on a drive root ("O:" -> "O:/",
        # which Windows resolves to that drive's CWD rather than its root).
        file_dir = ptk.format_path(ptk.format_path(file_path, "path"))
        if file_dir and file_dir != ptk.format_path(self.ui.txt000.text()):
            self.ui.txt000.setText(file_dir)
            self.logger.info(f"Output directory set to: {file_dir}")

    def b006(self) -> None:
        """Open Output Directory"""
        output_dir = self.ui.txt000.text()
        if os.path.exists(output_dir):
            os.startfile(output_dir)

    def b007(self) -> None:
        """Open Preset Directory.

        Maya's FBX preset folder, which is what the button says and where a
        dropped-in preset belongs — not the user app directory the SCAN walks
        (presets are found anywhere under it, so opening its root sent artists
        to a folder full of prefs and scripts).
        """
        preset_dir = self._preset_write_dir() or self._get_preset_dir()
        if not preset_dir:
            self.logger.error("Maya's user preset directory was not found.")
            return
        os.makedirs(preset_dir, exist_ok=True)
        os.startfile(preset_dir)

    def b008(self) -> None:
        """Edit Preset"""
        # Load the preset.
        self.load_fbx_export_preset(self.ui.cmb000.currentData())

        # Reset the layout to ensure it updates.
        mel.eval("refresh")
        mel.eval('FBXUICallBack -1 "updateUIWithProperties"')

        def _launch_editor():
            if not cmds.window("gameExporterWindow", exists=True):
                try:
                    mel.eval('FBXUICallBack -1 "editExportPresetInNewWindow" "fbx"')
                except Exception as e:
                    self.logger.error(
                        f"Failed to open the FBX export preset editor: {e}"
                    )

        # Defer launch to ensure initialization completes
        self.sb.defer_with_timer(_launch_editor, ms=200)

    def _get_legacy_output_dirs(self) -> List[str]:
        """Load recent output directories from legacy QSettings.

        Used only for one-time migration into ``RecentValuesOption``.
        """
        prev_output_dirs = self.ui.settings.value("prev_output_dirs", [])
        return [i for i in prev_output_dirs if not i == "/"][-10:]

    def save_output_dir(self, output_dir: str) -> None:
        """Record the output directory into the recent values plugin."""
        if output_dir and hasattr(self, "_recent_dirs_option"):
            self._recent_dirs_option.record(ptk.format_path(output_dir))

    def save_output_name(self, output_name: str) -> None:
        """Record the output filename into the recent values plugin."""
        if output_name and hasattr(self, "_recent_names_option"):
            self._recent_names_option.record(output_name)

    def _fbx_preset_metadata_provider(self) -> dict:
        """Return the currently selected FBX preset as embeddable metadata."""
        path = self.ui.cmb000.currentData()
        if not path or not os.path.isfile(path):
            return {}
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return {
            "fbx_preset_name": os.path.splitext(os.path.basename(path))[0],
            "fbx_preset_data": encoded,
        }

    def _on_fbx_preset_metadata_loaded(self, meta: dict) -> None:
        """Restore an embedded FBX preset to disk if it doesn't exist locally."""
        name = meta.get("fbx_preset_name")
        data = meta.get("fbx_preset_data")
        if not name or not data:
            return
        preset_dir = self._get_preset_dir()
        if not preset_dir:
            return
        # Ask the SCAN, not one path. :attr:`presets` searches the preset
        # directory RECURSIVELY -- Maya's own editor saves into a versioned
        # subfolder (``.../Presets/2020.3.6/export/``), and those presets are
        # what the combo lists -- while this check used to look only at
        # ``<preset_dir>/<name>.fbxexportpreset``. So loading a template whose
        # preset lives in a subfolder wrote a SECOND copy at the root under the
        # same name, and `presets` is a ``{name: path}`` dict: the two collapse
        # to whichever the scan reached last. The root copy is a frozen
        # snapshot taken when the template was saved, so from then on the panel
        # could silently export with stale FBX settings while the artist edited
        # the real preset in Maya's editor.
        if name in self.presets:
            return  # Local copy is authoritative, wherever it lives
        write_dir = self._preset_write_dir() or preset_dir
        target = os.path.join(write_dir, f"{name}.fbxexportpreset")
        os.makedirs(write_dir, exist_ok=True)
        with open(target, "wb") as f:
            f.write(base64.b64decode(data))
        self.logger.info(f"Restored embedded FBX preset: {target}")
        self._invalidate_preset_cache()
        self.ui.cmb000.init_slot()  # Refresh FBX preset combo


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("scene_exporter", reload=True)
    ui.show(pos="screen", app_exec=True)
