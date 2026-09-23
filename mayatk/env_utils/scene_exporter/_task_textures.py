# !/usr/bin/python
# coding=utf-8
"""Texture-phase export tasks: material cleanup, path repair, the texture
template conversion and the optimization pass, plus the lightmap hints the
GLB build reads.
"""

import contextlib
import os
from typing import Dict, Any, List

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # the surface must import without Maya (registry, docs tooling)
    cmds = mel = None
import pythontk as ptk

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.env_utils.scene_exporter._task_data import _TaskDataMixin


class _TextureTasksMixin(_TaskDataMixin):
    """Texture-phase export tasks: material cleanup, path repair, the texture
    template conversion and the optimization pass, plus the lightmap hints the GLB build reads."""

    def convert_to_relative_paths(self):
        """Convert texture paths under ``sourceimages`` to project-relative form.

        Scoped to what already lives under ``sourceimages`` (subfolders
        included).  A texture stored ANYWHERE ELSE keeps its absolute path and
        is only reported: an external reference is usually deliberate — a
        shared library, another project's published maps — and this task must
        not quietly relocate it.  (Relativizing it in place is not an
        alternative: the path would resolve to a file that isn't under
        sourceimages and would break the material on import, which is why
        ``external_mode="skip"`` skips rather than rewrites.)

        Consolidating externals INTO the project is the deliberate, separate
        act ``MatUtils.stage_textures_relative`` still performs by default for
        callers that want it.

        The node *path* edits persist by design: no restore unwinds them, so
        they are recorded as kept (``TaskFactory.record_kept_edit``) for a run
        that stops before its write to name. An export runs with the undo
        queue off, so nothing records them for undo there; called on its own,
        each write is undo-anchored inside ``stage_textures_relative``.

        Single per-node pass via ``MatUtils.stage_textures_relative`` — the
        old copy-then-remap pair coupled two functions through basename keys
        and could rebind a node to an unrelated same-named file the copy step
        had refused, flatten valid ``sourceimages/sub/…`` paths, or remap
        UDIM sets whose tiles were never copied.
        """
        self.logger.debug("Converting absolute paths to relative")
        file_nodes = self._get_export_file_nodes()
        if not file_nodes:
            self.logger.debug("No export texture file nodes — nothing to convert.")
            return

        results = MatUtils.stage_textures_relative(file_nodes, external_mode="skip")

        converted = [n for n, s in results.items() if s.endswith("relativized")]
        external = [n for n, s in results.items() if s == "skipped:external"]
        if converted:
            self.record_kept_edit("project-relative texture paths")
            self.logger.info(
                f"Stored project-relative paths on {len(converted)} file node(s)."
            )
        if external:
            # Not a warning: keeping an external link intact is this task's
            # contract, not a failure. Named so the user can see which maps
            # will ship with absolute paths and consolidate them if they meant
            # to (the Texture Path Editor's "Make Paths Absolute" twin).
            self.logger.info(
                f"{len(external)} texture(s) live outside sourceimages — left on "
                f"their absolute paths: {', '.join(sorted(external))}"
            )
        for node, status in results.items():
            if status.startswith("skipped:") and status != "skipped:external":
                self.logger.warning(
                    f"{node}: {status.split(':', 1)[1]} — path left unchanged."
                )
        self.logger.debug("Path conversion completed.")

    def optimize_textures(self, template):
        """Optimize the maps shipping with this export, by map type.

        The export-time twin of the Map Converter's Optimize pass: each
        shipping texture is run through ``ptk.MapOptimizer.optimize_map``,
        whose per-map-type rules (mode coercion, bit depth, palette handling)
        do the work. *template* selects the tier, exactly as the converter's
        Target combo does:

        - ``True`` (the combo at any Optimize tier, Textures = "As
          Authored") — generic per-map-type optimization; each map keeps
          its container.
        - a workflow template name (folded from cmb005 by ``b000``) — the
          template's per-map-type :class:`~pythontk.OutputSpec` additionally
          drives container and bit depth, clamped to scene-readable
          containers (:meth:`_scene_safe_output_type` — delivery containers
          like KTX2 stay with the GLB carrier pass). The template's
          ``DeliveryBudget`` stays ADVISORY unless the size dial below asks
          for it: reported by the paired check, not resampled.

        The size ceiling (``run.texture_max_size``, a per-run mode stamped by
        ``perform_export`` like the write-back flag) is the pass's one size
        dial — unset by default (never resamples), a fixed longest-edge
        ceiling, or the template-budget sentinel (enforce the selected
        template's own budget's size ceiling). In the panel it rides the same
        **Optimize Textures** combo as the pass switch ("Optimize + Max …" —
        b000 decomposes the choice back into these two inputs); headless
        callers still pass ``texture_max_size`` separately. Resolved by
        :meth:`_texture_size_clamp`; a ceiling only ever shrinks and keeps
        aspect. The clamp is a rule of the optimization pass, not a pass of
        its own — with the pass off there is nothing to apply it.

        The check half is :meth:`check_texture_optimization`; both judge
        through :meth:`_assess_optimization`, so the task and its gate cannot
        drift. Already-optimal maps ship as-is, untouched — re-encoding them
        would be pure churn (for a JPEG source, a lossy generational copy),
        and a write-back re-run must never re-archive an optimized file over
        its true original. (Foreign-packing migration is ``convert_textures``'
        job, which runs before this task and is gated by
        ``check_material_compatibility``.)

        One exception, where the scene's own maps ship (staged, not GLB-only):
        an optimal map in a lossless container a plain re-encode can shrink
        (``ptk.MapOptimizer.RECOMPRESSIBLE_FORMATS``) is still re-encoded,
        because "optimal" judges mode, depth, size and container, never how
        well the file is compressed — a production 57.34 MB normal map
        re-encoded to 24.10 MB. The copy ships only when it saves
        ``ptk.MapOptimizer.RECOMPRESS_MIN_SAVING``; otherwise the source does.
        It is not part of the paired check's verdict (knowing costs an encode).
        A GLB-only run skips it — its GLB pass re-encodes every map — and so
        does write-back, whose re-run would archive the re-encode.

        The file half -- judge, claim every output name, encode in parallel
        (one 4K PNG is 1.6-7 s), verify each write -- is
        ``ptk.MapOptimizer.stage_maps``, shared with blendertk; only the
        repoint below knows Maya.

        **Non-destructive by default** (``run.texture_write_back`` unset — the
        Texture Output combo at "Export Copies"): sources are never touched. Optimized copies are staged, the
        export file nodes are repointed at them for the write, and ONE
        deferred restore (post-write — the same mechanism
        :meth:`set_workspace` uses, so the FBX write and any GLB conversion
        both read the staged paths) puts every original path back. Where the
        staged files go — and whether they outlive the export — depends on
        who references them afterwards (:meth:`_texture_staging_dir`, shared
        with ``convert_textures``):

        - GLB-only output, or an FBX preset that embeds media: the
          deliverable carries its own copies, so staging is a
          ``TempArtifacts`` dir deleted by the deferred restore (with the
          age-gated sweep as the crash backstop).
        - A loose-media FBX references the staged files on disk, so they ARE
          part of the deliverable: staged durably into ``textures/`` beside
          the export (relative to the FBX, so the pair ships together) and
          kept. ``check_existing=True`` makes re-exports incremental.

        **Write-back mode** (Texture Output at "Scene Files (In Place)"): the
        optimization is written over the scene's own texture files (originals
        archived beside them in ``original_textures/``) and persists — same
        philosophy as ``convert_textures`` in that mode, choosing it means
        migrating the assets.

        Runs LAST in the material phase: after ``convert_textures`` (optimize
        what will actually ship) and after ``convert_to_relative_paths``
        (staged absolute paths must not be copied into sourceimages; the FBX
        plug-in resolves absolute paths fine at write time).

        Per-texture failures fall back to the original file with a warning —
        the paired check then names anything left unoptimized.
        """
        import shutil

        if not template:
            return
        tpl = template if isinstance(template, str) else None

        sources = self._export_texture_sources()
        if not sources:
            self.logger.debug("No export texture file nodes — nothing to optimize.")
            return

        pass_desc = f"the {tpl!r} template" if tpl else "map type (generic)"
        clamp = self._texture_size_clamp(tpl)
        clamp_desc = self._texture_size_clamp_desc(tpl)
        if clamp_desc:
            pass_desc += f", {clamp_desc}"
        if clamp.get("enforce_budget") and not ptk.OutputTemplates.budget(tpl).max_size:
            self.logger.warning(
                f"Optimize Textures is at 'Optimize + Template Budget' but "
                f"the {tpl!r} template is unbudgeted (an authoring target) — "
                "no size clamp applied. Choose an explicit 'Optimize + Max …' "
                "ceiling to resize."
            )

        write_back = self.run.texture_write_back
        # The file half -- judge, claim, encode in parallel, verify -- is the
        # shared pass; the staging dir is resolved only once something is
        # pending, so a run with every map already optimal creates none.
        # Re-encode candidates only where the scene's own maps ship: a GLB-only
        # run's GLB pass re-encodes every map itself, and in write-back a
        # re-run would archive the re-encode over the true original.
        report = ptk.MapOptimizer.stage_maps(
            sources,
            lambda path: self._assess_optimization(path, tpl),
            output_profile=tpl,
            clamp=clamp,
            staging_dir=lambda: self._texture_staging_dir("texopt"),
            write_back=write_back,
            recompress=not (write_back or self.run.glb_only),
            pass_desc=pass_desc,
            logger=self.logger,
        )
        if not report["pending"]:
            return
        staging_dir, temp_staging = report["staging_dir"], report["temp_staging"]

        # Staged repoints are Attributes.pinned scopes under ONE ExitStack (a
        # temp staging dir's removal rides the same stack), handed to
        # stage_deferred_context so the write still sees the staged paths.
        scope = contextlib.ExitStack()
        if not write_back and temp_staging:
            scope.callback(shutil.rmtree, staging_dir, ignore_errors=True)
        repathed: set = set()  # nodes already pinned (LIFO restores the original)
        for record in report["results"]:
            if record["status"] != "optimized":
                continue  # the source ships (kept / failed / name collision)
            src, written = record["path"], record["written"]
            # Repoint the consuming nodes wherever the written file is not the
            # node's current target (always, when staging; on a normalized
            # filename, when writing back).
            if os.path.normcase(os.path.normpath(written)) == os.path.normcase(
                os.path.normpath(src)
            ):
                continue
            new_path = written.replace("\\", "/")
            for node in record["entry"]["nodes"]:
                if write_back or node in repathed:
                    Attributes.set_plug(f"{node}.fileTextureName", new_path)
                    continue
                scope.enter_context(
                    Attributes.pinned(
                        node, _logger=self.logger, fileTextureName=new_path
                    )
                )
                # Count the node only once the pin actually took.
                # Attributes.pinned DECLINES silently (a warning, then
                # `continue`) when the plug is locked or driven by a
                # connection -- a referenced or published asset. Adding to
                # `repathed` before that decision meant such a node reported
                # success while the export shipped the original, unoptimized
                # file. Same normalization as the staging comparison above: an
                # exact string compare would false-negative on a separator or
                # case difference Maya introduced, and with `repathed` left
                # empty the scope closes immediately, restoring every path
                # BEFORE the export instead of after it.
                landed = cmds.getAttr(f"{node}.fileTextureName") or ""
                if os.path.normcase(os.path.normpath(landed)) == (
                    os.path.normcase(os.path.normpath(new_path))
                ):
                    repathed.add(node)
                else:
                    self.logger.warning(
                        f"{node}.fileTextureName could not be repointed "
                        "(locked or connected) -- the export ships this "
                        "node's ORIGINAL texture, not the optimized copy."
                    )

        if not write_back and repathed:
            self.stage_deferred_context("optimize_textures", scope)
        else:
            scope.close()  # nothing pinned: drop a temp dir right away

        if report["optimized"]:
            if write_back:
                self.record_kept_edit("texture files optimized in place")
            sizes = ptk.FileUtils.format_bytes_delta(
                report["bytes_before"], report["bytes_after"]
            )
            destination = (
                "written back to the scene's texture files (originals archived "
                "in 'original_textures')"
                if write_back
                else (
                    "staged for the write only — scene paths restored after export"
                    if temp_staging
                    else f"staged beside the export in {staging_dir!r} (the FBX "
                    "references them; scene paths restored after export)"
                )
            )
            self.logger.info(
                f"Optimized {report['optimized']} texture(s): {sizes}; {destination}."
            )

    def reassign_duplicate_materials(self):
        """Reassign duplicate materials in the scene."""
        self.logger.debug("Reassigning duplicate materials")
        materials = self._get_all_materials()
        MatUtils.reassign_duplicate_materials(materials, delete=True)
        # Duplicates were deleted — drop every cache derived from the old set.
        self._invalidate_material_caches()
        # One query for the lot: ``ls`` drops a name that is gone and lists a
        # repeated one once.
        if len(cmds.ls(materials) or []) < len(set(materials)):
            self.record_kept_edit("merged duplicate materials")
        self.logger.debug("Reassignment completed.")

    def resolve_invalid_texture_paths(self):
        """Attempt to resolve missing texture paths via a gated sourceimages hunt.

        Scoped to the file nodes feeding the export materials.  A rebind by
        name is inherently a guess — the original file is gone, so nothing
        can verify content — which is why the hunt is gated: the broken
        basename must match exactly ONE file under the sourceimages tree
        (recursive; the old hunt only saw the root).  A unique match is
        rebound and logged at WARNING (old → new, auditable); an ambiguous
        name is reported instead of guessed at.  ``<UDIM>``/``<f>`` token
        names match by pattern and rebind with the token preserved.

        The same hunt heals the lightmap markers first
        (:meth:`LightmapRecords.heal_lightmap_paths`): a committed lightmap is
        a texture dependency with no file node -- its marker records the
        folder the bake was committed FROM -- so a project reorganised since
        leaves the FBX manifest pointing at nothing while the EXR sits one
        folder away. A map found by the same unique-match rule gets its
        recorded folder rewritten and the manifest republished; files are
        never touched.
        """
        self._heal_lightmap_hints()

        file_nodes = self._get_export_file_nodes()
        if not file_nodes:
            self.logger.debug(
                "No export texture file nodes found. Skipping texture path resolution."
            )
            return

        import fnmatch

        index: Dict[str, List[str]] = {}
        src_dir = EnvUtils.get_env_info("sourceimages")
        if src_dir and os.path.isdir(src_dir):
            for walk_root, _dirs, files in os.walk(src_dir):
                for f in files:
                    index.setdefault(f.lower(), []).append(
                        os.path.join(walk_root, f).replace("\\", "/")
                    )

        resolved_count = 0
        unresolved = []

        for node in file_nodes:
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                continue

            # "Already valid" must mean what check_valid_paths means: env-var
            # + workspace expansion with <UDIM> handling (resolve_path,
            # search=False).  The previous bare os.path.exists test resolved
            # workspace-relative paths against the process CWD, so every valid
            # relative path failed the guard and was rewritten back to
            # absolute on each run.
            if MatUtils.resolve_path(path, search=False):
                continue  # Path is already valid

            basename = os.path.basename(os.path.expandvars(path))
            if MatUtils.has_path_token(basename):
                # Widen to the directories holding any tile of the set; the
                # token itself stays in the candidate path below, so the
                # rebind is still to the PATTERN, never to one tile.
                pattern = MatUtils.token_wildcard(basename).lower()
                tile_dirs = sorted(
                    {
                        os.path.dirname(p)
                        for name, paths in index.items()
                        if fnmatch.fnmatchcase(name, pattern)
                        for p in paths
                    }
                )
                candidates = [f"{d}/{basename}" for d in tile_dirs]
            else:
                candidates = index.get(basename.lower(), [])

            if len(candidates) == 1:
                new_path = candidates[0]
                cmds.setAttr(f"{node}.fileTextureName", new_path, type="string")
                resolved_count += 1
                # WARNING, not INFO: a rebind by name is a guess the user
                # should be able to audit after the export.
                self.logger.warning(
                    f"Rebound texture by unique name match: {node}: "
                    f"{path} -> {new_path}"
                )
            elif candidates:
                unresolved.append(
                    f"{node} -> {path} (ambiguous: {len(candidates)} same-named "
                    "files under sourceimages — not guessing)"
                )
            else:
                unresolved.append(f"{node} -> {path}")

        if resolved_count:
            self.record_kept_edit("rebound missing texture paths")
            self.logger.info(f"Resolved {resolved_count} broken texture path(s).")
        if unresolved:
            self.logger.warning(
                f"{len(unresolved)} texture path(s) could not be resolved:"
            )
            for entry in unresolved:
                self.logger.warning(f"  {entry}")
        if not resolved_count and not unresolved:
            self.logger.debug("All texture paths are valid.")

    # -- lightmap dependencies -------------------------------------------
    # The engine is LightmapRecords (mayatk.light_utils); these three are the
    # exporter's thin reads of it, scoped to the live export set. Imported
    # lazily: a headless export that never baked anything should not load the
    # lightmap package at import time.

    def _lightmap_dependencies(self) -> List[Dict[str, Any]]:
        """The lightmaps the export set's markers name, resolved on disk NOW
        (:meth:`LightmapRecords.lightmap_dependencies`); ``[]`` when none."""
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        objects = self._live_objects()
        if not objects:
            return []
        return LightmapRecords.lightmap_dependencies(objects)

    def _lightmap_search_dirs(self) -> List[str]:
        """Folders the GLB applier joins the manifest's basenames against
        (:meth:`LightmapRecords.search_dirs`, scoped to the export set)."""
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        return LightmapRecords.search_dirs(self._live_objects() or None)

    def _heal_lightmap_hints(self) -> None:
        """Rewrite stale lightmap marker hints to where the maps were found.

        Logged at WARNING like the texture rebinds -- a hint moved by name is
        a guess the user should be able to audit -- and what stays missing is
        named, since the exporter's path check is about to fail on it.
        """
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        objects = self._live_objects()
        if not objects:
            return
        report = LightmapRecords.heal_lightmap_paths(objects)
        if report["healed"]:
            self.record_kept_edit("re-pointed lightmap folders")
        for basename, old_dir, new_dir in report["healed"]:
            self.logger.warning(
                f"Rebound lightmap by unique name match: {basename}: "
                f"{old_dir or '<no folder recorded>'} -> {new_dir}"
            )
        for dep in report["missing"]:
            note = f" ({dep['note']})" if dep.get("note") else ""
            self.logger.warning(
                f"Lightmap could not be resolved: {dep['map']} "
                f"(recorded in {dep['dir'] or '<no folder recorded>'}){note}"
            )

    def _texture_staging_dir(self, tag: str):
        """Where staged (non-write-back) texture processing lands for this run.

        Shared by :meth:`convert_textures` and :meth:`optimize_textures`, so
        both halves of a run stage into ONE place. Staged files are temp only
        when nothing after the export references them (the deliverable embeds
        its own copies): a GLB-only run, or an FBX preset that embeds media —
        the embed query needs fbxmaya; if it isn't loaded yet, fall through
        to durable staging (the safe direction: durable files are kept
        whether or not the write embeds). Otherwise a loose-media FBX
        references the staged files, so they land durably in ``textures/``
        beside the deliverable. Direct TaskManager use with no export path
        has nothing durable to stage beside, so temp is the only coherent
        mode there.

        Returns:
            tuple: ``(staging_dir, temp_staging)``.
        """
        temp_staging = bool(self.run.glb_only)
        if not temp_staging:
            try:
                temp_staging = bool(mel.eval("FBXExportEmbeddedTextures -q"))
            except Exception:  # noqa: BLE001 — plugin not loaded yet
                temp_staging = False
        export_path = self.export_path
        if not temp_staging and not export_path:
            temp_staging = True
        if temp_staging:
            return ptk.TempArtifacts(f"scene_exporter_{tag}").dir_path(), True
        staging_dir = os.path.join(os.path.dirname(export_path), "textures")
        os.makedirs(staging_dir, exist_ok=True)
        return staging_dir, False

    def convert_textures(self, template) -> None:
        """Convert the export materials' textures to *template* via the Map Updater.

        The task half of the Texture Template combobox (``cmb005``) -- the check
        half is :meth:`check_material_compatibility`, and ``b000`` folds the one
        selection into both, so there is a single definition to manage.
        Delegates wholesale to
        :meth:`mayatk.mat_utils.mat_updater.MatUpdater.update_materials` with
        the template as its workflow config -- exactly the conversion the Map
        Updater panel runs, scoped to the export materials.

        **Non-destructive by default** (``run.texture_write_back`` unset -- the
        Texture Output combo at "Export Copies"): the export materials'
        upstream wiring is snapshotted verbatim
        (:meth:`~mayatk.mat_utils.mat_snapshot.MatSnapshot.capture_network`),
        the Map Updater runs in its copy mode into this run's staging dir
        (:meth:`_texture_staging_dir` -- the same place ``optimize_textures``
        stages, temp or durable by the same rule), the FBX/GLB write reads
        the rewired graph, and ONE deferred restore puts the original network
        back (new nodes gone, stale connections broken, recorded ones
        re-made, file paths reset) and deletes a temp staging dir. The scope
        is :meth:`MatSnapshot.network_scope` -- the same object a script
        would ``with`` -- handed to ``stage_deferred_context`` because the
        write must still see the rewired graph. Sources on disk are never
        touched in this mode.

        **Write-back mode** (Texture Output at "Scene Files (In Place)"): the
        Map Updater's plain in-place migration -- the rewiring persists,
        converted maps land beside their sources, and the rewired paths are
        relativized here if ``convert_to_relative_paths`` is on (this task
        now runs after it, so the staged mode's absolute paths are never
        copied into sourceimages).

        Runs in TASK_ORDER's material-cleanup phase after
        ``resolve_invalid_texture_paths`` (sources must resolve to convert)
        and ``convert_to_relative_paths``, and before ``optimize_textures``
        (which optimizes what will actually ship).
        """
        import shutil

        if not template:
            return None
        from mayatk.mat_utils.mat_updater import MatUpdater
        from mayatk.mat_utils.mat_snapshot import MatSnapshot

        materials = self._get_all_materials()
        if not materials:
            self.logger.info("Texture template: no export materials to convert.")
            return None
        write_back = self.run.texture_write_back
        self.logger.info(
            f"Converting textures for {len(materials)} material(s) "
            f"to the {template!r} template"
            + (
                " — migrating the scene's materials..."
                if write_back
                else " — staging for export only (scene restored after)..."
            )
        )
        if write_back:
            config: Any = template
        else:
            staging_dir, temp_staging = self._texture_staging_dir("texconv")
            scope = contextlib.ExitStack()
            if temp_staging:
                scope.callback(shutil.rmtree, staging_dir, ignore_errors=True)
            # Kept for the texture checks, which run while this rewire is live
            # and must link nodes the restore leaves standing
            # (_texture_node_links); forgotten by that same restore.
            snapshot = scope.enter_context(MatSnapshot.network_scope(materials))
            self._texture_network_snapshot = snapshot

            def _forget_snapshot():
                if self._texture_network_snapshot is snapshot:
                    self._texture_network_snapshot = None

            scope.callback(_forget_snapshot)
            self.stage_deferred_context("convert_textures", scope)
            config = {
                "preset": template,
                "move_to_folder": staging_dir,
                "transfer_mode": "copy",
                # One worker per texture SET: serially this was 265 s of a 4K
                # production export (PNG encode alone).
                "max_workers": ptk.ImgUtils.encode_workers(),
            }
        # Guarded because a task exception ABORTS the pipeline (TaskFactory
        # re-raises after logging) -- one unreadable texture would kill the
        # whole export with a traceback. The designed failure path is the
        # paired check instead: it validates the actual post-task state, so
        # masks this conversion could not bring to the template fail the
        # export cleanly, with the residuals named and this error above them.
        try:
            MatUpdater.update_materials(materials=materials, config=config)
        except Exception:  # noqa: BLE001 — the paired check is the gate
            self.logger.error(
                f"Texture conversion to {template!r} failed; "
                "check_material_compatibility will gate on what remains.",
                exc_info=True,
            )
        # The conversion rewires file nodes: every cached read of the material
        # set and its textures is now stale, including the one the
        # post-conversion compatibility check is about to make.
        self._invalidate_material_caches()
        if write_back:
            self.record_kept_edit("textures converted in place")
        if write_back and self.run.relative_paths:
            file_nodes = self._get_export_file_nodes()
            if file_nodes:
                # Same scope as the task itself — re-applying the conversion to
                # the rewired nodes must not consolidate externals the task
                # deliberately left alone.
                MatUtils.stage_textures_relative(file_nodes, external_mode="skip")
        return None
