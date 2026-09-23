# !/usr/bin/python
# coding=utf-8
"""The Scene Exporter's task/check manager -- what ``perform_export`` drives.

:class:`TaskManager` composes the phase mixins (scene, textures, animation,
checks) and the panel's definitions over :class:`pythontk.TaskFactory`, which
dispatches the ``task_*`` / ``check_*`` methods by name in the shared order
(``ptk.ExportProfile.TASK_ORDER``, checks hoisted by its
``CHECK_DEPENDENCIES``). The class itself holds what spans a run: the
:class:`pythontk.ExportRun` modes it is begun with, the export set, and the
post-write API (``create_glb``, ``write_scene_data_sidecar``,
``verify_deliverables``) the exporter calls once the file is on disk.
"""

import os
from typing import Optional, Dict, Any

import pythontk as ptk
from pythontk import TaskFactory

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.hierarchy_sync.hierarchy_baseline import HierarchyBaseline
from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar
from mayatk.env_utils.scene_exporter._task_animation import _AnimationTasksMixin
from mayatk.env_utils.scene_exporter._task_checks import _TaskChecksMixin
from mayatk.env_utils.scene_exporter._task_scene import _SceneTasksMixin
from mayatk.env_utils.scene_exporter._task_textures import _TextureTasksMixin
from mayatk.env_utils.scene_exporter.task_definitions import _TaskDefinitionsMixin


@ptk.ExportProfile.scoped_tables
class TaskManager(
    TaskFactory,
    _SceneTasksMixin,
    _TextureTasksMixin,
    _AnimationTasksMixin,
    _TaskChecksMixin,
    _TaskDefinitionsMixin,
):
    """The export pipeline's tasks and checks, run in the shared order.

    ``TASK_ORDER`` and ``CHECK_DEPENDENCIES`` are ``ptk.ExportProfile``'s
    tables scoped to this class by the decorator (this is the reference
    implementation, so nothing is scoped away -- :attr:`PARITY_GAPS` pins
    that). A run is bracketed by :meth:`begin_run` (the modes and the per-run
    reset) and the exporter's ``run_deferred_restores`` (every mutation a
    task staged for the write, unwound LIFO).
    """

    #: Shared-table names this manager has no method for, with the reason --
    #: the reference implementation has none; the blendertk mirror declares
    #: its gaps here so its tests can pin them.
    PARITY_GAPS: Dict[str, str] = {}

    def __init__(self, logger):
        super().__init__(logger)
        self._objects = None
        self._invalidate_material_caches()

    def run_tasks(self, tasks: Dict[str, Any]) -> bool:
        """Run *tasks*, first adopting the two modes derived from them.

        Read off the FULL dict here rather than in
        ``_execute_tasks_and_checks``: an override that resumes the tasks a
        failed check stopped hands that method a subset, from which
        ``optimize_keys_level`` would come back False and smart_bake would stop
        optimizing its own output (see ``ptk.ExportRun.with_tasks``).
        """
        self.run = self.run.with_tasks(tasks)
        return super().run_tasks(tasks)

    @property
    def objects(self):
        return self._objects

    @objects.setter
    def objects(self, value):
        """Drop the caches derived from the export set.

        Nothing else: tasks assign this mid-run (a filter, the carrier
        fold-in, a re-resolve after a bake), and the per-run markers are
        :meth:`begin_run`'s to reset.
        """
        self._objects = value
        self._invalidate_material_caches()
        self._invalidate_keyframe_cache()

    def create_glb(self, fbx_path: Optional[str] = None, announce: bool = True):
        """Convert an exported FBX to a GLB through the shared build.

        Runs after the FBX has been written; ``perform_export`` invokes this
        explicitly rather than as part of the pre-export task pipeline.

        The build is :class:`pythontk.GlbPipeline` -- the SAME chain the WebXR
        preview publishes through -- handed this run's dials: the scene sidecar
        built from the export set (:class:`~mayatk.env_utils.scene_state.SceneState`,
        the readers the preview shares), where the maps live NOW
        (:meth:`_lightmap_search_dirs`) and the GLB's half of the panel's
        texture rows (:meth:`pythontk.ExportRun.glb_texture_params`, the method the
        preview resolves the same rows with). Neither producer has a
        chain of its own, so the preview cannot show a channel the deliverable
        drops. A sidecar read failure degrades to a bare conversion rather than
        costing the deliverable; a failed conversion or texture pass fails it
        (the deliverable must not lie).

        Parameters:
            fbx_path: FBX to convert. Defaults to ``self.export_path`` (the
                FBX-alongside case). The GLB-only path passes the temp FBX so the
                ``.glb`` lands beside it (then gets moved into the output dir).
            announce: When True, log the resulting path. The GLB-only path sets
                this False and logs the final (moved) path itself.

        Returns:
            The created ``.glb`` path, or ``None`` if the build failed.
        """
        from mayatk.env_utils.scene_state import SceneState

        src = fbx_path or self.export_path
        sidecar = ptk.GlbPipeline.envelope(
            lambda: SceneState.read(self._live_objects()),
            source=SceneState.source(),
            asset=os.path.basename(src),
            # The lighting recipe with this run's choices (Baked Reflections):
            # decided by the export, carried by the deliverable.
            rendering=self.run.rendering,
            logger=self.logger,
        )
        try:
            built = ptk.GlbPipeline.build(
                src,
                sidecar=sidecar,
                # Where the maps are NOW: the manifest riding the FBX records
                # the folder the bake was committed from, which goes stale the
                # moment the project is reorganised -- and then the GLB ships
                # unlit while the bake sits one folder away.
                lightmap_dirs=self._lightmap_search_dirs(),
                # The panel's texture dials resolved against the shared
                # web-delivery policy: this GLB IS the web deliverable.
                texture_params=self.run.glb_texture_params(logger=self.logger),
                # GLB Key Tolerance: the deviation bound, or None for the
                # converter's per-frame keys.
                key_tolerance=self.run.glb_key_tolerance,
                # Which clips survive the rebuild. Decided by the Animation
                # Clips row and stashed by apply_declared_takes; "both" when
                # that task never ran, which is the shape every export had
                # before the row existed.
                clip_mode=self._clip_mode,
                progress=lambda message: self._report_progress(None, None, message),
                logger=self.logger,
            )
        except (OSError, RuntimeError, ValueError) as e:
            # The destination being HELD OPEN is the likeliest failure here
            # (PermissionError): the build REPLACES the .glb, and Windows
            # refuses while a viewer has a handle out -- so say which process,
            # and keep a locked file from reading as a broken conversion.
            reason = ptk.FileUtils.describe_lock(os.path.splitext(src)[0] + ".glb")
            if reason:
                self.logger.error(f"GLB build could not write its output: {reason}")
            else:
                self.logger.error(f"GLB build failed: {e}")
            return None

        glb_path = built["glb"]
        if announce:
            self.logger.success(f"GLB created: {glb_path}")
        return glb_path

    # ------------------------------------------------------------------
    # Scene-data sidecar — delegates to SceneDataSidecar
    # ------------------------------------------------------------------

    # Backward-compatible aliases so existing call-sites still work.
    _manifest_path_for = staticmethod(SceneDataSidecar.manifest_path_for)
    _diff_report_path_for = staticmethod(SceneDataSidecar.diff_report_path_for)
    _build_clean_path_set = staticmethod(SceneDataSidecar.build_clean_path_set)
    _get_top_level = staticmethod(SceneDataSidecar.get_top_level)

    def _build_full_hierarchy_set(self) -> set:
        """Build a clean path set including all descendants of ``self.objects``."""
        return SceneDataSidecar.build_full_path_set(self._live_objects())

    def _sidecar_kwargs(self) -> dict:
        """Return sidecar path-derivation kwargs based on versioning state.

        When SceneExporter has set ``run.versioned`` (the Output Filename carries a
        ``{n}`` counter), sidecar paths route through the base stem so every
        version in a series shares one manifest.
        """
        return {"base_stem": bool(self.run.versioned)}

    def _data_export_snapshot(self) -> dict:
        """Decoded copy of every ``data_export`` channel, as shipped in the FBX.

        Empty dict when the carrier is absent, empty, or not part of the
        export set — the carrier is a hidden node, so outside the ``all``
        mode it only ships when ``export_data_node`` folded it in, and the
        record must only claim what actually shipped.  Never raises — the
        record must not break the export it records.
        """
        try:
            from mayatk.node_utils.data_nodes import DataNodes

            if not any(
                str(o).split("|")[-1] == DataNodes.EXPORT for o in (self.objects or [])
            ):
                return {}
            return DataNodes.dump(decode=True).get(DataNodes.EXPORT) or {}
        except Exception:
            self.logger.debug("data_export snapshot skipped.", exc_info=True)
            return {}

    def _write_temp_diff_report(
        self,
        export_path: str,
        missing: list,
        extra: list,
        reparented: list,
        *,
        base_stem: bool = False,
    ) -> Optional[str]:
        """Write the human-readable hierarchy diff report to a temp artifact.

        The report is a session courtesy (the log links it), not a
        deliverable — the durable record is the manifest's
        ``hierarchy.last_diff``, so nothing lands in the export folder.
        Deterministic name per stem (self-overwriting) and the age-gated
        sweep reclaims leftovers.  Never raises: a failed report must not
        fail the check that produced it.
        """
        try:
            report = SceneDataSidecar.format_diff_report(
                missing, extra, reparented=reparented
            )
            path = ptk.TempArtifacts("hierarchy_diff").path(
                extension=".txt",
                name=SceneDataSidecar._stem_for(export_path, base_stem),
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
            return path
        except Exception:
            self.logger.debug("Temp hierarchy diff report skipped.", exc_info=True)
            return None

    def write_scene_data_sidecar(self, glb_path: Optional[str] = None) -> None:
        """Write the sidecar JSON recording what shipped in the export.

        The manifest carries the exported hierarchy paths (the diff-check
        baseline), the diff the check flagged this export (if any — see
        ``hierarchy.last_diff`` in the sidecar module), plus a snapshot of
        the ``data_export`` carrier channels.  The hierarchy section is
        maintained when the check is in play (it ran this export, or a
        manifest already exists); the data section is recorded whenever the
        carrier shipped content.  A metadata-free export with the check off
        leaves no sidecar.

        With a GLB written, its ``lightmap_metadata`` is recorded as the GLB
        ships it: the GLB pass corrects that copy to the encoded map and the
        scalar restoring the bake range, while the scene's still names the
        pre-encode ``.exr`` at 1.0. The FBX carries the scene's copy in-band.

        Parameters:
            glb_path: The GLB this export wrote, if any.
        """
        export_path = self.export_path
        if not export_path or not self.objects:
            return

        paths = self._build_full_hierarchy_set()

        # Adopt any on-disk baselines before rolling forward, so history is not
        # lost for a scene whose hierarchy CHECK is switched off -- the check
        # migrates too, but it is optional and the write is not. No-ops once the
        # scene carries a record of its own.
        HierarchyBaseline.migrate_from_sidecar(os.path.dirname(export_path))

        # The BASELINE first, and unconditionally: it goes to the SCENE, not the
        # sidecar, so it must not be skipped by the sidecar's own "nothing to
        # write" shortcut below. It is a property of this scene's hierarchy, not
        # of the name this export happens to carry; only the exported scope
        # rolls forward, so one record serves every export the scene makes.
        if not HierarchyBaseline.write(paths):
            self.logger.warning(
                "Could not record the hierarchy baseline on the scene — the "
                "diff baseline for the next export was NOT updated."
            )
        elif not EnvUtils.saved_scene_path():
            # data_internal persists with the FILE: an unsaved scene holds the
            # record in memory only, so the next session starts from nothing.
            # Warn rather than block -- refusing an export over a check's own
            # bookkeeping inverts the priority.
            self.logger.warning(
                "Hierarchy baseline recorded, but the scene is unsaved — save "
                "the scene to keep it for the next session."
            )

        sk = self._sidecar_kwargs()

        # Symmetric with check_hierarchy_vs_existing_fbx: bring any
        # legacy-named (and, when versioning, per-version) sidecar up to
        # the current name so subsequent writes find it via the "manifest
        # already exists" condition below.
        SceneDataSidecar.migrate_legacy(export_path, **sk)

        manifest_path = SceneDataSidecar.manifest_path_for(export_path, **sk)

        data = self._data_export_snapshot()
        key = ptk.MeshConvert.LIGHTMAP_METADATA_KEY
        if glb_path and key in data:
            try:
                shipped = ptk.MeshConvert.read_glb_lightmap_manifest(glb_path)
            except (OSError, ValueError) as error:
                self.logger.warning(
                    "Scene-data sidecar: the GLB's lightmap manifest could not be "
                    f"read ({error}); recording the scene's."
                )
                shipped = None
            if shipped is not None:
                data = {**data, key: shipped}
        check_ran = self._hierarchy_check_ran
        if not check_ran and not data and not os.path.exists(manifest_path):
            return

        # Consume-and-clear: the stash belongs to THIS export's check; a
        # later export in the same session must not inherit it.  The path
        # tag guards the cancelled-A-then-export-B case, where the check
        # never re-ran to reset the stash.
        last_diff = self._hierarchy_last_diff
        self._hierarchy_last_diff = None
        if last_diff and last_diff.pop("export_path", None) != export_path:
            last_diff = None

        if (
            SceneDataSidecar.write_manifest(
                export_path, paths, data=data, last_diff=last_diff, **sk
            )
            is None
        ):
            self.logger.warning(
                "Could not write the scene-data sidecar — the metadata shipped "
                "alongside this deliverable was NOT updated."
            )

    #: Above this, the FBX gates step aside instead of parsing (see
    #: :meth:`verify_deliverables`). The record tree costs roughly twice the
    #: file in heap, and this runs at the END of a long export, when losing
    #: the Maya session is most expensive.
    MAX_VERIFY_FBX_BYTES = 512 * 1024 * 1024
    #: Gates whose passing row is a measurement worth reading, so
    #: :meth:`verify_deliverables` logs it like a warning.
    VERIFY_MEASUREMENTS = ("glb_image_bytes",)

    def verify_deliverables(
        self,
        *paths: str,
        max_fbx_bytes: Optional[int] = None,
        max_image_bytes: Optional[int] = None,
    ) -> Optional[Any]:
        """Read the shipped files back and run pythontk's file-level gates.

        Every check elsewhere in this module reads the SCENE. These read the
        written bytes, which is the only way to catch what the write itself
        got wrong: a truncated container, a take the FBX dropped, a NaN that
        reached an accessor, a clip whose span disagrees with its take. Runs
        last, after :meth:`write_scene_data_sidecar`, because two gates
        (``clips_vs_takes``, ``fbx_takes``) read that sidecar -- and it is
        handed over explicitly, since a versioned export keys its manifest to
        the base stem and the verifier's own beside-the-file lookup would
        miss it.

        Cost stays proportional to what shipped. Only the given paths are
        opened, so a GLB-only export never parses the temp FBX it is about to
        discard (~4.5 s and ~326 MB of heap for a 163 MB file); paths that
        never reached disk are dropped; the GLB is read JSON-chunk-only
        (~0.08 s at 145 MB); an FBX past *max_fbx_bytes* is skipped rather
        than risk the session's memory; and no baseline is passed, which
        would parse a second GLB for a comparison the exporter has no opinion
        about (that gate SKIPs). The verifier is released before returning so
        the FBX record tree does not outlive the report.

        A failing report does not unwrite the deliverable or flip the
        export's verdict — the file shipped, and the pre-export checks are
        the gating mechanism (the same soft-degrade contract ``create_glb``
        follows). It is logged per failing gate at ERROR instead, and each
        warned gate is named at INFO.

        Parameters:
            paths: The deliverables a consumer actually receives. Anything
                that is not a readable ``.fbx``/``.glb`` is ignored, so a USD
                export passes through as a no-op.
            max_fbx_bytes: Size bound for opening the FBX. Defaults to
                :attr:`MAX_VERIFY_FBX_BYTES`.
            max_image_bytes: The Max Texture Size row's limit, in bytes: the
                ``glb_image_bytes`` gate lists the GLB's embedded images by
                size and warns past it. ``None`` lists without a bound. The
                only place a GLB-only export's textures CAN be measured --
                :meth:`check_texture_file_size` steps aside there, since the
                GLB pass re-encodes every source map.

        Returns:
            The ``ptk.ExportVerifier`` report, or None when nothing
            verifiable shipped.
        """
        bound = self.MAX_VERIFY_FBX_BYTES if max_fbx_bytes is None else max_fbx_bytes
        inputs = {}
        for path in paths:
            if not isinstance(path, str):
                # Total over whatever a caller hands it -- a None from a failed
                # GLB conversion, or the bare True this method's UI row carries
                # if a future caller forgets the pop.
                continue
            kind = {".glb": "glb", ".fbx": "fbx"}.get(os.path.splitext(path)[1].lower())
            if not kind or not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            if kind == "fbx" and size > bound:
                self.logger.info(
                    f"Skipped FBX verification: {os.path.basename(path)} is "
                    f"{size / 1048576:.0f} MB, past the "
                    f"{bound / 1048576:.0f} MB parse bound."
                )
                continue
            inputs.setdefault(kind, path)
        if not inputs:
            return None

        # Hand over the sidecar this export actually wrote rather than
        # letting the verifier guess: with versioning on, the manifest is
        # keyed to the BASE stem so a series shares one, while the verifier
        # looks beside the file for `.{stem}.scene_data.json`. Guessing made
        # `clips_vs_takes` and `fbx_takes` -- the gates that catch a dropped
        # take -- SKIP silently on every versioned export. A path that is not
        # there degrades exactly as auto-discovery would.
        export_path = self.export_path
        if export_path:
            inputs["sidecar"] = SceneDataSidecar.manifest_path_for(
                export_path, **self._sidecar_kwargs()
            )
        if max_image_bytes:
            inputs["max_image_bytes"] = max_image_bytes

        try:
            report = ptk.ExportVerifier(**inputs).run()
        except Exception as e:
            # QA over a file that already shipped must never be the thing
            # that fails an export.
            self.logger.warning(f"Deliverable verification could not run: {e}")
            return None

        counts = report.counts()
        headline = (
            f"Deliverable verification: {counts.get('PASS', 0)} passed, "
            f"{counts.get('WARN', 0)} warned, {counts.get('FAIL', 0)} failed, "
            f"{counts.get('SKIP', 0)} skipped."
        )
        if report.ok:
            self.logger.info(headline)
        else:
            self.logger.error(headline)
        for row in report.rows:
            if row.status == "FAIL":
                self.logger.error(f"  [FAIL] {row.check}: {row.detail}")
            elif row.status == "WARN" or (
                row.status == "PASS" and row.check in self.VERIFY_MEASUREMENTS
            ):
                # Named, not shouted: a WARN does not fail the report, and a
                # headline counting warnings nobody can read is noise. A
                # measurement is the reason its gate exists, so it is named
                # when it passes too (a SKIP says nothing worth a line).
                self.logger.info(f"  [{row.status}] {row.check}: {row.detail}")
        return report


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
