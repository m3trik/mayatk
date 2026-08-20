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
import ctypes
import shutil
import logging
from datetime import datetime
from typing import List, Dict, Optional, Callable, Union, Any

import pythontk as ptk
# From this package:
from mayatk.env_utils._env_utils import EnvUtils
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
        self.logger.debug("Task manager initialized in SceneExporter.")

    def _setup_logging(self, log_level: str, log_handler: Optional[object]) -> None:
        """Setup logging configuration."""
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
        log_level: str = "WARNING",
        hide_log_file: Optional[bool] = None,
        log_handler: Optional[object] = None,
        tasks: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, bool]]:
        """Perform the export operation, including initialization and task management."""
        from maya import cmds

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

        # Setup logging
        self._setup_logging(log_level, log_handler)

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

        # Texture File Type: ONE container dial for every texture the export
        # ships — the scene maps the optimization pass writes AND a GLB's
        # embedded copies (each destination clamps what it cannot carry; see
        # TaskManager._resolved_output_type / _glb_texture_params). Parsed here
        # so the KTX2 gate can fail BEFORE any scene work, and stamped per run
        # on the task manager (the ``_optimize_keys_enabled`` pattern).
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
                # before the first scene mutation — a missing toktx fails the
                # batch in second zero with the install URL, not after N-1
                # objects already exported. Abort idiom, not a raise: the panel's
                # export button reads the return value and the log.
                try:
                    ptk.ImgUtils.resolve_ktx2_encoder(required=True)
                except FileNotFoundError as e:
                    self.logger.error(f"Export aborted: {e}")
                    return False
        self.task_manager._texture_file_type = texture_file_type

        # Texture-processing inputs, stamped per run (the
        # ``_optimize_keys_enabled`` pattern): the Texture Output combo's
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

        # Generate the export path (with versioning applied if requested).
        self.export_path = self.generate_export_path(version_format=version_format)
        self.logger.debug(f"Generated export path: {self.export_path}")

        if self.create_log_file:
            self._setup_file_logging()

        # Initialize objects
        initialized_objs = self._initialize_objects(objects)
        if not initialized_objs:
            self.logger.error("Export aborted: No objects available for export.")
            return False

        # Apply preset before running tasks
        if self.preset_file:
            self.load_fbx_export_preset(self.preset_file, verify=True)

        # Make export path available to checks (e.g. hierarchy diff).  The
        # `_version_format` flag tells the hierarchy check to route sidecar
        # paths through SceneDataSidecar.base_stem so all versions of a
        # series share one manifest.
        self.task_manager.export_path = self.export_path
        self.task_manager._version_format = version_format

        export_succeeded = False
        try:
            # Run tasks and checks
            if tasks:
                tasks_successful = self.task_manager.run_tasks(tasks)
                if not tasks_successful:  # If any tasks failed, return them
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
                from mayatk.env_utils.fbx_utils import FbxUtils

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
                    shutil.move(glb_path, deliverable_path)
                    self.logger.success(f"GLB created: {deliverable_path}")

                # Write the scene-data sidecar (hierarchy baseline for future
                # diff checks + data_export snapshot) only now that a
                # deliverable exists — in GLB-only mode a failed conversion
                # returns above, and rolling the baseline forward for an
                # export that shipped nothing would make the next run's
                # hierarchy diff compare against a phantom. Keyed off the
                # logical export path (output dir + stem), independent of
                # where the FBX was actually written.
                self.task_manager.write_scene_data_sidecar()

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
                if t_cnt or c_cnt:
                    export_info_lines.append("")
                    export_info_lines.append(f"Tasks Executed: {t_cnt}")
                    if c_cnt:
                        export_info_lines.append(f"Checks Passed: {c_cnt}/{c_cnt}")

                self.logger.log_box(
                    "EXPORT SUCCESSFUL", export_info_lines, level="SUCCESS"
                )

                # FBX+GLB: GLB sidecar runs after the banner so the FBX success
                # message isn't visually preceded by an unrelated GLB error if
                # conversion fails.
                if create_glb_enabled and not glb_only:
                    self.task_manager.create_glb()
            except Exception as e:
                self.logger.error(f"Failed to export objects: {e}")
                raise RuntimeError(f"Failed to export objects: {e}")
            finally:
                if glb_tempdir:
                    shutil.rmtree(glb_tempdir, ignore_errors=True)
                if self.create_log_file:
                    self.close_file_handlers()
        finally:
            # Scene state the FBX write itself reads (working linear unit,
            # active workspace) is staged rather than set_/revert_-paired,
            # because that pairing fires before the write. Undo it here, on
            # every exit path — a failed check, a raising task, or a bad write.
            self.task_manager.run_deferred_restores()
            # Restore the scene state recorded by smart_bake's session
            # manifest: deletes the override layer, re-enables IK handles
            # (bakeResults' disableImplicitControl zeroes ikBlend even when
            # baking to a layer), and restores any baked visibility.
            _session = getattr(self.task_manager, "_bake_session_id", None)
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

        if not export_succeeded:
            return False

        # Tasks/checks already ran (and any GLB conversion completed) before
        # this point; a True return means the deliverable was written.
        return True

    def generate_export_path(self, version_format: str = "") -> str:
        """Generate the full export file path.

        Parameters:
            version_format: If non-empty, treat as a pythontk-style
                placeholder template (e.g. ``{stem}_v{n:03d}``) and resolve
                the next-version path via ``FileUtils.next_version_path``.
        """
        # Handle wildcard matching for output_name to overwrite existing files
        if self.output_name and any(char in self.output_name for char in "*?"):
            import glob

            pattern = self.output_name
            if not pattern.lower().endswith((".fbx", ".FBX")):
                pattern += ".fbx"

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
        export_name = self.output_name or scene_name
        export_name = export_name.removesuffix(".fbx").removesuffix(".FBX")
        if self.timestamp:
            export_name += f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        export_name = self.format_export_name(export_name)
        path = os.path.join(self.export_dir, f"{export_name}.fbx")
        return self._apply_versioning(path, version_format)

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

        # Initialize the export override button
        self.ui.b009.setEnabled(True)
        self.ui.b009.setChecked(False)
        self.ui.b009.setStyleSheet("QPushButton:checked {background-color: #FF9999;}")

        self.logger.setLevel(log_level)
        self.logger.hide_logger_name(True)  # Hide the logger name in output
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)

        # Connect clickable log links (action:// URIs in QTextBrowser)
        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

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
            ui, "exclude_hdr", "export_visible_objects", lambda scope: scope != "visible"
        )

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
        """Get the preset directory from settings, defaulting to Maya's preset directory."""
        preset_dir = self.ui.settings.value("preset_dir")
        if not preset_dir:
            try:
                preset_dir = EnvUtils.get_env_info("user_app_path")
                if preset_dir:
                    self.ui.settings.setValue("preset_dir", preset_dir)
            except (KeyError, ValueError):
                pass
        return preset_dir

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
        # Retrieve the preset directory using settings
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

        Directory management (default / custom directory, open, edit) lives in
        this row's own option box — the ☐ beside the combo — so it sits on the
        widget it configures instead of the parent combo's actions section.
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
            widget.option_box.menu.add(
                "QPushButton",
                setText="Set FBX Preset Directory",
                setObjectName="b005",
                setToolTip="Choose the directory the preset list is scanned from.",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Use Default FBX Preset Directory",
                setObjectName="b013",
                setToolTip="Point the preset scan back at Maya's user presets directory.",
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
                    "<span style='color:orange'>Warning: Preset directory not set or does not exist.<br>"
                    "Please set a valid directory (FBX Preset ▸ option box ▸ Set FBX Preset Directory).</span>"
                )
            elif len(presets) <= 1:  # Only "None"
                self.ui.txt003.setHtml(
                    "<span style='color:orange'>Warning: No presets found in the current directory.<br>"
                    "Drop .fbxexportpreset files into it (FBX Preset ▸ option box ▸ Open FBX Preset Directory), "
                    "or set a custom directory.</span>"
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
                "The option box beside this row opens the preset folder, "
                "changes it, or opens the FBX preset editor."
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
                rows.append(
                    (self.sb.registered_widgets.Separator(title=group), group)
                )
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
        directory management lives on the ``cmb000`` row's own option box
        (``cmb000_init``)."""
        definitions = self.task_manager.task_definitions
        rows = []
        for group, names in self._SETTINGS_LAYOUT:
            rows.append((self.sb.registered_widgets.Separator(title=group), group))
            for name in names:
                spec = self._SETTINGS_WIDGETS.get(name)
                if spec is not None:
                    rows.append(
                        (self._make_definition_widget(name, spec, object_name=name), name)
                    )
                elif name in definitions:
                    rows.append(
                        (self._make_definition_widget(name, definitions[name]), name)
                    )
        widget.add(rows, header="Settings", clear=True)

    def b013(self) -> None:
        """Use Default FBX Preset Directory — point the preset scan back at
        Maya's user presets directory (a ``cmb000`` option-box button)."""
        try:
            default_dir = EnvUtils.get_env_info("user_app_path")
        except Exception:
            default_dir = None
        if not default_dir:
            self.logger.error("Maya user app directory not found.")
            return
        self.ui.settings.setValue("preset_dir", default_dir)
        self._invalidate_preset_cache()
        self.ui.cmb000.init_slot()
        self.logger.info(f"Reverted to default preset directory: {default_dir}")

    def cmb004_init(self, widget) -> None:
        """Init Output Format — FBX (default), GLB, or FBX + GLB.

        A Settings row (``cmb008``). ``currentData()`` yields the
        ``output_format`` token ``b000`` forwards to ``perform_export``.
        GLB-only writes the FBX to a temp dir and keeps only the converted
        ``.glb``; FBX + GLB keeps both side by side. The container its embedded
        textures are written in is the general ``texture_file_type`` row (a
        GLB carries what glTF accepts — see ``TaskManager._glb_texture_params``).
        """
        if not widget.is_initialized:
            widget.restore_state = True
        widget.add(
            {"FBX": "fbx", "GLB": "glb", "FBX + GLB": "fbx_glb"},
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
            description = (presets.get(widget.itemData(index)) or {}).get(
                "description"
            )
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

        self.perform_export(
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
        )

        output_dir = self.ui.txt000.text()
        self.save_output_dir(output_dir)
        self.save_output_name(self.ui.txt001.text())

    def b010(self) -> None:
        """Set Output Directory"""
        output_dir = self.sb.dir_dialog(
            title="Select an output directory:", start_dir=self.workspace
        )
        if output_dir:
            self.ui.txt000.setText(output_dir)

    def b005(self) -> None:
        """Set Preset Directory."""
        preset_dir = self.sb.dir_dialog(
            title="Select a directory containing export presets:"
        )
        if preset_dir:
            self.ui.settings.setValue("preset_dir", preset_dir)
            self.ui.cmb000.init_slot()
            self.logger.info(f"Preset directory set to: {preset_dir}")

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
        }.get(self.ui.cmb004.currentData(), ["*.fbx", "*.glb"])

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
        """Open Preset Directory."""
        preset_dir = self._get_preset_dir()
        if preset_dir and os.path.exists(preset_dir):
            os.startfile(preset_dir)
        else:
            self.logger.error(
                "Preset directory is not set or does not exist. Please set it first."
            )

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
        target = os.path.join(preset_dir, f"{name}.fbxexportpreset")
        if os.path.exists(target):
            return  # Local copy is authoritative
        os.makedirs(preset_dir, exist_ok=True)
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
