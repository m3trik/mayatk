# !/usr/bin/python
# coding=utf-8
import contextlib
import os
import re
import math
import logging
from typing import Optional, Dict, Any, List

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    cmds = None
    mel = None
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.xform_utils._xform_utils import XformUtils
from pythontk import TaskFactory
from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar


class _TaskDataMixin:
    """ """

    #: Tiled-texture filename tokens (single-file operations must skip these).
    _TEXTURE_TOKEN_RE = re.compile(r"<udim>|<f>|<uvtile>", re.IGNORECASE)

    def _scene_safe_output_type(self, path: str, template: str) -> Optional[str]:
        """The container the optimization pass may write for *path* under
        *template* — clamped to what a scene file node can read.

        A template's per-map-type :class:`~pythontk.OutputSpec` can name a
        delivery container (:attr:`~pythontk.ImgUtils.DELIVERY_FORMATS`, e.g.
        KTX2) that the DCC viewport cannot display and no FBX importer reads
        — those stay with the GLB carrier pass (cmb006). Returns the source's
        own extension to pin the container in that case, None otherwise (an
        explicit ``output_type`` outranks the profile's, so None lets the
        profile drive).
        """
        map_type = ptk.MapFactory.resolve_map_type(path, key=True)
        spec_ext = (
            ptk.OutputTemplates.resolve(map_type, template).ext or ""
        ).lower().lstrip(".")
        if spec_ext in ptk.ImgUtils.DELIVERY_FORMATS:
            return os.path.splitext(path)[1].lower().lstrip(".") or None
        return None

    #: ``_texture_max_size`` sentinel: clamp to the active template's own
    #: :class:`~pythontk.DeliveryBudget` (``enforce_budget``) rather than to a
    #: pixel ceiling. Aliases the shared resolver's own sentinel so the combo
    #: row, the exporter and the optimizer cannot drift apart on its value.
    TEXTURE_MAX_SIZE_TEMPLATE = ptk.MapOptimizer.SIZE_CLAMP_TEMPLATE

    def _texture_size_clamp(self, template: Optional[str]) -> Dict[str, Any]:
        """The resize rule the optimization pass applies under *template*.

        Binds the per-run ``_texture_max_size`` mode (the Max Texture Size
        combo, stamped by ``perform_export`` — never a dispatched task) to
        the shared resolver, which owns the rule: see
        :meth:`pythontk.MapOptimizer.resolve_size_clamp` for the modes and
        why the budget's POT flag is deliberately not adopted.

        Returns:
            dict of keyword arguments for ``MapOptimizer.assess`` /
            ``optimize_map``. Empty when no clamp applies.
        """
        return ptk.MapOptimizer.resolve_size_clamp(
            getattr(self, "_texture_max_size", None), template, logger=self.logger
        )

    def _texture_size_clamp_desc(self, template: Optional[str]) -> str:
        """Human-readable form of :meth:`_texture_size_clamp` for log lines."""
        return ptk.MapOptimizer.describe_size_clamp(
            getattr(self, "_texture_max_size", None), template, logger=self.logger
        )

    def _assess_optimization(self, path: str, template: Optional[str]):
        """What the optimization pass would do to *path* — judged once.

        The one criterion the task (skip already-optimal sources, re-verify a
        reused staged file) and the check (name residuals) share, via
        ``ptk.MapOptimizer.assess``: the per-map-type pass (mode / bit depth),
        plus the *template*'s per-map-type container when one is active, plus
        the Max Texture Size clamp when one is set
        (:meth:`_texture_size_clamp`). Without a clamp the template's
        :class:`~pythontk.DeliveryBudget` stays ADVISORY — assess reports it in
        ``warnings`` and nothing here plans a resample.

        Returns:
            None when the file cannot be read (missing / unreadable is
            :meth:`check_valid_paths`' domain); else a dict with ``needed``
            (bool), ``reasons`` (list[str], including a container change the
            plan itself does not model), ``warnings`` (list[str] —
            advisory budget notes, declined-lossy notes, channel loss), and
            ``predicted_name`` (str — the basename ``optimize_map`` would
            write for *path* under this *template*; the same resolve call
            ``optimize_map`` itself makes, so a caller can key a collision
            decision on the OUTPUT name before ever touching disk).
        """
        output_type = self._scene_safe_output_type(path, template) if template else None
        result = ptk.MapOptimizer.assess(
            path,
            output_profile=template,
            output_type=output_type,
            optimize_bit_depth=True,
            **self._texture_size_clamp(template),
        )
        if result.get("error"):
            return None
        reasons = list(result["reasons"])
        src_ext = os.path.splitext(path)[1].lower().lstrip(".")
        new_ext = (result["predicted"].get("ext") or src_ext).lower().lstrip(".")
        if new_ext != src_ext:
            reasons.append(f"Container: {src_ext} -> {new_ext} (template)")
        predicted_path = result["predicted"].get("path") or path
        return {
            "needed": bool(reasons),
            "reasons": reasons,
            "warnings": list(result["warnings"]),
            "output_type": output_type,
            "predicted_name": os.path.basename(predicted_path),
        }

    def _tiled_representative(self, resolved: str) -> Optional[str]:
        """One concrete file standing in for a tiled/sequence texture *resolved* path.

        ``<udim>`` resolves to its first tile, ``1001``; ``<uvtile>`` resolves
        to ITS OWN first tile, ``u1_v1`` — UDIM and UV-tile numbering are not
        interchangeable, so collapsing both onto ``"1001"`` silently pointed a
        ``<uvtile>`` set at a file that was never written (the representative
        never existed, so the caller's ``os.path.isfile`` gate always failed
        it, and a Blender-authored uvtile set could never be measured). ``<f>``
        has no fixed "first" value — frame numbering, padding, and start frame
        all vary per render — so it globs the token's position for the first
        frame file that actually exists on disk.

        Returns:
            The representative path (for ``<udim>``/``<uvtile>`` it may not
            exist — the caller's own ``os.path.isfile`` check is what gates
            that), or ``None`` when a ``<f>`` token's glob finds no frame file
            (distinct from the fixed-token miss: the caller can only tell the
            two apart via this return value, so the two must not be
            conflated).
        """
        basename = os.path.basename(resolved)
        directory = os.path.dirname(resolved)

        def _fixed(match: "re.Match") -> str:
            return "1001" if match.group(0).lower() == "<udim>" else "u1_v1"

        if "<f>" in basename.lower():
            import glob as _glob

            pattern = self._TEXTURE_TOKEN_RE.sub(
                lambda m: "*" if m.group(0).lower() == "<f>" else _fixed(m),
                basename,
            )
            matches = sorted(_glob.glob(os.path.join(directory, pattern)))
            return matches[0] if matches else None

        return os.path.join(directory, self._TEXTURE_TOKEN_RE.sub(_fixed, basename))

    def _export_texture_sources(
        self, include_tiled: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        """Deduplicated shipping textures: ``{key: {"path", "nodes", "tiled"}}``.

        Scoped to the file nodes feeding the export materials and read from
        their CURRENT stored paths, so post-task callers (checks) see what a
        prior task staged. Deduped by normcased resolved path — a map shared
        by several materials is one entry with every consuming node listed.

        Tiled nodes — a ``<UDIM>``/``<f>``/``<uvtile>`` token path, or Maya's
        ``uvTilingMode`` set with a plain tile path — are skipped by default
        (the optimizer is single-file; logged so the skip is auditable).
        ``include_tiled=True`` instead includes them, resolved to a single
        representative tile/frame (see :meth:`_tiled_representative`): the
        budget check wants to MEASURE a tiled set it cannot fix, so an
        oversized one fails aloud instead of slipping past the gate. Paths
        Maya cannot resolve are skipped either way (missing files are
        :meth:`check_valid_paths`' domain).
        """
        sources: Dict[str, Dict[str, Any]] = {}
        skipped_tokens: List[str] = []
        no_frame_nodes: List[str] = []
        for node in self._get_export_file_nodes():
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue
            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                continue
            tiled = bool(
                self._TEXTURE_TOKEN_RE.search(os.path.basename(path))
            ) or bool(
                cmds.attributeQuery("uvTilingMode", node=node, exists=True)
                and cmds.getAttr(f"{node}.uvTilingMode")
            )
            if tiled and not include_tiled:
                skipped_tokens.append(node)
                continue
            resolved = MatUtils.resolve_path(path, search=False)
            if not resolved:
                continue
            if tiled:
                # A concrete file stands in for the set (the same collapse
                # check_valid_paths probes with): <udim>/<uvtile> resolve to
                # their own first tile, <f> globs for the first frame
                # actually on disk (no fixed "first" frame exists to
                # assume). A representative that isn't on disk is the
                # valid-paths check's problem, not this scan's.
                representative = self._tiled_representative(resolved)
                if representative is None:
                    no_frame_nodes.append(node)
                    continue
                resolved = representative
                if not os.path.isfile(resolved):
                    continue
            key = os.path.normcase(os.path.normpath(resolved))
            entry = sources.setdefault(
                key, {"path": resolved, "nodes": [], "tiled": tiled}
            )
            entry["nodes"].append(node)
        if skipped_tokens:
            self.logger.info(
                f"{len(skipped_tokens)} tiled texture node(s) "
                f"(<UDIM>/uvTilingMode) skipped — tiled sets are not "
                f"optimized: {', '.join(sorted(skipped_tokens))}"
            )
        if no_frame_nodes:
            self.logger.info(
                f"{len(no_frame_nodes)} tiled texture node(s) with a <f> "
                f"frame token had no frame file on disk — skipped: "
                f"{', '.join(sorted(no_frame_nodes))}"
            )
        return sources

    def _live_objects(self) -> List[str]:
        """``self.objects`` re-resolved to the nodes that still exist.

        Tasks mutate the export set's DAG paths — ``conform_shape_names``
        renames nodes, ``smart_bake`` can delete driver nodes — and a single
        stale path poisons EVERY bulk ``cmds`` call over the list:
        ``cmds.listRelatives(objects, ...)`` raises
        ``ValueError: No object matches name: [<the entire list>]``, an error
        that names every object except the offender and aborts the whole run
        from whichever check happens to run first (alphabetically,
        ``check_duplicate_locator_names``).

        Mutating tasks refresh ``self.objects`` themselves (by UUID, so a
        rename is tracked rather than dropped); this is the read-side guard
        for everything downstream.
        """
        if not self.objects:
            return []
        return cmds.ls([str(o) for o in self.objects], long=True) or []

    @staticmethod
    def _repath_renamed(objects: List[str], uuids: List[str]) -> List[str]:
        """*objects* with every path a rename invalidated re-derived from *uuids*.

        ``uuids`` is the pre-rename snapshot, positionally aligned with
        ``objects``.  Order is preserved; an entry still holding its own node
        is kept VERBATIM (only what actually broke is touched), and one whose
        node is gone outright drops out.

        Identity is decided by UUID, never by ``objExists``: a repair that
        frees up a name (deleting ``FOO`` lets ``FOO_FBXASC03203`` clean to
        ``FOO``) leaves the deleted entry's path occupied by a DIFFERENT
        node, which a path-existence test would silently resurrect — as a
        duplicate of the entry that legitimately moved there.
        """
        refreshed: List[str] = []
        for obj, uuid in zip(objects, uuids):
            if not uuid:  # never resolved to begin with
                continue
            if (cmds.ls(obj, uuid=True) or [None])[0] == uuid:
                refreshed.append(obj)  # same node, same path
                continue
            new = (cmds.ls(uuid, long=True) or [None])[0]
            if new:  # renamed; else deleted
                refreshed.append(new)
        return refreshed

    @property
    def _has_keyframes(self) -> bool:
        """Check if the current objects have keyframes."""
        if hasattr(self, "_key_times"):
            return bool(self._key_times)
        return bool(self._get_all_keyframes())

    def _get_all_keyframes(self) -> List[float]:
        """Return a sorted list of all unique keyframe times for the specified objects.

        Delegates to ``AnimUtils.get_keyframe_times`` for the actual query and
        caches the result set in ``_key_times`` for downstream consumers.
        """
        # Filter to objects that still exist (smart_bake may delete
        # constraints/expressions, removing nodes from the scene).
        existing = self._live_objects()
        if not existing:
            return []

        times = AnimUtils.get_keyframe_times(existing)
        if times is None:
            return []

        self._key_times = set(times)
        return times

    def _invalidate_keyframe_cache(self) -> None:
        """Drop the cached keyframe times (``_key_times``).

        Every task that moves or deletes keys must call this: a later task
        reading the cache would otherwise act on pre-edit times — e.g.
        ``tie_all_keyframes`` bookending to the fractional extremes
        ``snap_keys_to_frame`` just removed, re-creating the exact keys the
        snap existed to fix (and then failing ``check_floating_point_keys``).
        """
        if hasattr(self, "_key_times"):
            delattr(self, "_key_times")

    def _invalidate_material_caches(self) -> None:
        """Drop the derived material/texture caches.

        Both are derived from the same source (the materials assigned to
        ``self.objects``), so they must always be cleared together — a lone
        ``_cached_materials = None`` would leave the file-node cache describing
        a material set that no longer exists.
        """
        self._cached_materials = None
        self._cached_export_file_nodes = None

    def _get_all_materials(self) -> List[str]:
        """Return a list of all materials assigned to the specified objects.

        Results are cached per export run. The cache is invalidated when
        ``objects`` is reassigned via ``_initialize_objects``.
        """
        if not hasattr(self, "_cached_materials") or self._cached_materials is None:
            # include_displacement: displacement/volume/aiSurfaceShader maps
            # must be validated, sized, and staged like every other texture —
            # the surface-shader-only default left them invisible to the
            # whole pipeline.
            self._cached_materials = MatUtils.filter_materials_by_objects(
                self._live_objects(), as_strings=True, include_displacement=True
            )
        return self._cached_materials

    def _get_export_file_nodes(self) -> List[str]:
        """Return the deduplicated ``file`` nodes feeding the export materials.

        Walks the shading history of the materials assigned to ``self.objects``
        (filtering any an earlier task may have deleted) and collects the
        connected ``file`` texture nodes.  Shared by the texture-oriented tasks
        and checks so they all scope to exactly the textures that will ship,
        rather than every ``file`` node in the scene.

        Cached alongside ``_cached_materials`` and invalidated with it: the walk
        is a ``listHistory`` over every export material, and three tasks/checks
        (``resolve_invalid_texture_paths``, ``check_valid_paths``,
        ``check_texture_file_size``) each want the same answer in one run.
        """
        cached = getattr(self, "_cached_export_file_nodes", None)
        if cached is not None:
            return cached

        materials = [m for m in self._get_all_materials() if cmds.objExists(m)]
        if not materials:
            self._cached_export_file_nodes = []
            return self._cached_export_file_nodes

        history = cmds.listHistory(materials, pruneDagObjects=True) or []
        self._cached_export_file_nodes = list(set(cmds.ls(history, type="file") or []))
        return self._cached_export_file_nodes


class _TaskActionsMixin(_TaskDataMixin):
    """ """

    def set_workspace(self, enable=True):
        """Switch to the workspace matching the scene path, and align the
        process working directory with it, for the export write.

        Two staged mutations, one purpose \u2014 make write-time path resolution
        match Maya's:

        - **Workspace**: how Maya (and the checks) resolve project-relative
          texture paths.
        - **Process CWD**: how the fbxmaya plugin locates those textures when
          it WRITES \u2014 plain OS resolution against the working directory; the
          workspace is never consulted (probe-proven 2026-08-04: with a
          correct workspace and a foreign CWD the plugin silently drops every
          relative texture from the embed \u2014 "The following texture(s) will
          not be embedded" \u2014 while with a foreign workspace and the CWD at
          the project root, embedding succeeds).

        **Staged, not ``set_``/``revert_``-paired**: both mutations must still
        be applied when the FBX is written, while the paired revert fires when
        ``run_tasks`` returns \u2014 before the write. Returns ``None`` so the
        too-early pairing stays disarmed; see
        ``TaskFactory.stage_deferred_restore``.
        """
        original_workspace = cmds.workspace(query=True, rootDirectory=True)

        if enable:
            new_workspace = EnvUtils.find_workspace_using_path()
            if new_workspace and new_workspace != original_workspace:
                self.stage_deferred_restore(
                    "workspace", lambda: self._restore_workspace(original_workspace)
                )
                cmds.workspace(new_workspace, openWorkspace=True)
                self.logger.debug(
                    f"Changed workspace from {original_workspace} to {new_workspace}"
                )
            elif not new_workspace:
                self.logger.warning(
                    "No workspace.mel found in scene path hierarchy "
                    f"\u2014 using current workspace: {original_workspace}"
                )
            else:
                self.logger.debug("Workspace already matches scene path.")

            # Align the process CWD with the (now) active workspace root \u2014
            # even when the workspace itself needed no switch, the CWD can
            # still be foreign (GUI Maya never chdirs on Set Project).
            ws_root = cmds.workspace(query=True, rootDirectory=True)
            original_cwd = os.getcwd()
            if (
                ws_root
                and os.path.isdir(ws_root)
                and os.path.normcase(os.path.normpath(original_cwd))
                != os.path.normcase(os.path.normpath(ws_root))
            ):
                self.stage_deferred_restore("cwd", lambda: os.chdir(original_cwd))
                os.chdir(ws_root)
                self.logger.debug(
                    "Aligned process working directory with the workspace root "
                    f"for the FBX write: {original_cwd} -> {ws_root}"
                )

        return None

    def _restore_workspace(self, original):
        cmds.workspace(original, openWorkspace=True)
        self.logger.debug(f"Reverted workspace to: {original}")

    def set_linear_unit(self, linear_unit):
        """Set Maya's working linear unit for the export.

        **Staged, not ``set_``/``revert_``-paired** \u2014 same reason as
        :meth:`set_workspace`: the FBX plugin stamps the file's unit from the
        working unit at WRITE time (proven: exporting the same cube under
        ``cm`` vs ``m`` yields different files), and the paired revert fires
        before the write, which made this task inert. Returns ``None`` so that
        pairing stays disarmed.
        """
        original_linear_unit = cmds.currentUnit(query=True, linear=True)

        if linear_unit and linear_unit != "OFF":
            self.stage_deferred_restore(
                "linear_unit", lambda: self._restore_linear_unit(original_linear_unit)
            )
            cmds.currentUnit(linear=linear_unit)
            self.logger.debug(
                f"Changed linear unit from {original_linear_unit} to {linear_unit}"
            )
        else:
            self.logger.debug(f"Linear unit change skipped (value: {linear_unit})")

        return None

    def _restore_linear_unit(self, original):
        cmds.currentUnit(linear=original)
        self.logger.debug(f"Reverted linear unit to: {original}")

    def conform_shape_names(self):
        """Repair scratch/mangled names in the export set, then conform shapes.

        Delegates to ``SceneDiagnostics.repair_mangled_names``: cleans
        mangled TRANSFORM (and other non-shape) names — accumulated
        ``__uninst_tmp`` tokens, ``__RZTMP`` suffixes, FBXASC escapes,
        underscore runs — then conforms shapes to ``<transform>Shape``.
        Shape-only conforming could never clear ``check_mangled_names``
        (which scans all descendants), leaving the check failing after
        every repair pass.  Permanent scene improvement (deliberately not
        reverted after export).
        """
        # `or []` — a None/empty export set must stay a no-op; passing None
        # through would repair the WHOLE scene.
        objects = [str(o) for o in (self.objects or [])]
        # Snapshot UUIDs FIRST: a rename invalidates the stored DAG path, and
        # nothing else in the pipeline re-derives it.  Every later cmds call
        # over self.objects — and cmds.select() for the export itself — would
        # then die on the stale path (or, if smart_bake's cmds.ls refresh ran
        # first, silently drop the renamed node from the FBX).  Resolved one
        # at a time so the snapshot stays positionally aligned with `objects`
        # (a bulk cmds.ls silently drops an unresolvable name and expands an
        # ambiguous one, which would shift every pairing after it).
        uuids = [(cmds.ls(o, uuid=True) or [None])[0] for o in objects]

        result = SceneDiagnostics.repair_mangled_names(objects)
        if result["renamed"]:
            self.logger.info(
                f"Repaired {len(result['renamed'])} mangled node name(s)."
            )
            self.objects = self._repath_renamed(objects, uuids)
        if result["shapes_conformed"]:
            self.logger.info(
                f"Conformed {result['shapes_conformed']} shape name(s)."
            )

    def convert_to_relative_paths(self):
        """Copy external textures into sourceimages, then convert paths to relative.

        A project-relative texture path only resolves if the file physically
        lives under ``sourceimages``.  Any texture stored elsewhere is first
        copied in (via ``MatUtils.copy_textures_to_sourceimages``); without
        that step, remapping it to a relative path would point at a file that
        isn't there and silently break the material on import.  Textures
        already under sourceimages are left in place.

        The staged copies are a real, persistent asset consolidation, and the
        node *path* edits persist too — the exporter has no automatic
        post-export rollback (the undo-chunk restore was removed with the
        smart_bake redesign), so both survive the export by design.  The
        user's undo queue can back the path edits out (each write is
        undo-anchored inside ``stage_textures_relative``).

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

        results = MatUtils.stage_textures_relative(file_nodes)

        copied = [
            n
            for n, s in results.items()
            if s in ("copied+relativized", "variant+relativized")
        ]
        variants = [n for n, s in results.items() if s == "variant+relativized"]
        converted = [n for n, s in results.items() if s.endswith("relativized")]
        if copied:
            self.logger.info(
                f"Copied {len(copied)} external texture(s) into sourceimages."
            )
        if variants:
            self.logger.warning(
                f"{len(variants)} texture(s) hit a name collision in sourceimages "
                "and were staged under a '_N' variant name — verify the colliding "
                f"files are genuinely different: {', '.join(sorted(variants))}"
            )
        if converted:
            self.logger.info(
                f"Stored project-relative paths on {len(converted)} file node(s)."
            )
        for node, status in results.items():
            if status in ("skipped:name-collision", "skipped:copy-failed"):
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

        - ``True`` (checkbox on, Textures = "As Authored") — generic
          per-map-type optimization; each map keeps its container.
        - a workflow template name (folded from cmb005 by ``b000``) — the
          template's per-map-type :class:`~pythontk.OutputSpec` additionally
          drives container and bit depth, clamped to scene-readable
          containers (:meth:`_scene_safe_output_type` — delivery containers
          like KTX2 stay with the GLB carrier pass). The template's
          ``DeliveryBudget`` stays ADVISORY unless the size dial below asks
          for it: reported by the paired check, not resampled.

        The **Max Texture Size** combo (``_texture_max_size``, a per-run mode
        stamped by ``perform_export`` like the write-back flag) is the pass's
        one size dial — OFF by default (never resamples), a fixed longest-edge
        ceiling, or "Template Budget" (enforce the selected template's own
        budget's size ceiling). Resolved by :meth:`_texture_size_clamp`; a
        ceiling only ever shrinks and keeps aspect. Inert unless this task is
        on — the
        clamp is a rule of the optimization pass, not a pass of its own.

        The check half is :meth:`check_texture_optimization`; both judge
        through :meth:`_assess_optimization`, so the task and its gate cannot
        drift. Already-optimal maps ship as-is, untouched — re-encoding them
        would be pure churn (for a JPEG source, a lossy generational copy),
        and a write-back re-run must never re-archive an optimized file over
        its true original. (Foreign-packing migration is ``convert_textures``'
        job, which runs before this task and is gated by
        ``check_material_compatibility``.)

        **Non-destructive by default** (``_texture_write_back`` unset — the
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
                f"Max Texture Size is 'Template Budget' but the {tpl!r} "
                "template is unbudgeted (an authoring target) — no size clamp "
                "applied. Choose an explicit ceiling to resize."
            )

        # Only maps the pass would actually CHANGE are touched — sorted so the
        # collision-subdir assignment below is deterministic across runs. An
        # unreadable source drops out here (None verdict) — check_valid_paths
        # is its gate.
        pending = []
        for _key, entry in sorted(sources.items()):
            verdict = self._assess_optimization(entry["path"], tpl)
            if verdict and verdict["needed"]:
                pending.append((entry, verdict))
        if not pending:
            self.logger.info(
                f"Texture optimization: all {len(sources)} shipping "
                f"texture(s) already optimal for {pass_desc}."
            )
            return

        write_back = getattr(self, "_texture_write_back", False)
        staging_dir = None
        temp_staging = False
        if not write_back:
            staging_dir, temp_staging = self._texture_staging_dir("texopt")

        self.logger.info(
            f"Optimizing {len(pending)} of {len(sources)} texture(s) for "
            f"{pass_desc}"
            + (
                " — writing back to the scene's texture files..."
                if write_back
                else " — staging for export only (scene untouched)..."
            )
        )

        # Staged repoints are Attributes.pinned scopes under ONE ExitStack (a
        # temp staging dir's removal rides the same stack), handed to
        # stage_deferred_context so the write still sees the staged paths.
        scope = contextlib.ExitStack()
        if not write_back and temp_staging:
            scope.callback(shutil.rmtree, staging_dir, ignore_errors=True)
        repathed: set = set()  # nodes already pinned (LIFO restores the original)
        claimed: Dict[str, str] = {}  # predicted-output key -> claiming source
        used_names: Dict[str, int] = {}
        optimized = failed = 0
        total_before = total_after = 0

        for entry, verdict in pending:
            src = entry["path"]
            output_type = verdict["output_type"]
            # The name optimize_map WILL write, predicted before it ever
            # runs (the same resolve call it makes internally — see
            # _assess_optimization). Two different SOURCE basenames can
            # collapse onto this ONE output name (suffix-alias
            # normalization, or a container change the template picked
            # collapsing e.g. wood.png/wood.jpg -> wood.jpg) — keying the
            # collision decision on the source basename missed exactly that
            # case, letting the second optimize_map call overwrite the
            # first's file on disk *after* the first's nodes were already
            # repointed at it.
            predicted_name = verdict.get("predicted_name") or os.path.basename(src)
            size_before = os.path.getsize(src) if os.path.isfile(src) else 0

            if write_back:
                # No alt-subdir escape hatch here — write-back writes into
                # the source's own folder by design (that's the point of
                # "write back to the scene's textures"). So a predicted
                # collision must be caught BEFORE optimize_map runs: the
                # loser is skipped outright rather than having its original
                # archived into original_textures/ while its node keeps
                # pointing at the now-moved path.
                out_dir = os.path.dirname(src) or "."
                claim_key = os.path.normcase(
                    os.path.join(out_dir, predicted_name)
                )
            else:
                # Two different source folders can hold same-named maps —
                # a flat staging dir would silently collapse them, so the
                # second+ claimant of a PREDICTED output name stages into a
                # subdir (keyed on the name optimize_map will actually
                # write, not the source's own basename).
                base = predicted_name.lower()
                nth = used_names.get(base, 0)
                used_names[base] = nth + 1
                out_dir = (
                    staging_dir
                    if nth == 0
                    else os.path.join(staging_dir, f"alt{nth}")
                )
                claim_key = os.path.normcase(
                    os.path.join(out_dir, predicted_name)
                )

            prior_src = claimed.get(claim_key)
            if prior_src and prior_src != src:
                failed += 1
                self.logger.warning(
                    f"Optimized name collision: {os.path.basename(src)} "
                    f"would write as {predicted_name!r}, already claimed by "
                    f"{os.path.basename(prior_src)} for this pass — "
                    f"{os.path.basename(src)} ships unmodified to avoid "
                    "overwriting the survivor."
                )
                continue
            claimed[claim_key] = src

            try:
                if write_back:
                    written = ptk.MapOptimizer.optimize_map(
                        src,
                        output_profile=tpl,
                        output_type=output_type,
                        old_files_folder="original_textures",
                        **clamp,
                    )
                else:
                    written = ptk.MapOptimizer.optimize_map(
                        src,
                        output_dir=out_dir,
                        output_profile=tpl,
                        output_type=output_type,
                        check_existing=not temp_staging,
                        **clamp,
                    )
                    if not temp_staging:
                        # check_existing keys reuse on mtime alone, so a
                        # staged file from an earlier run under DIFFERENT
                        # settings (another template, or none) is "newer than
                        # the source" and gets reused while still needing
                        # work — the task would then report success and its
                        # own paired check would name it as a residual with
                        # no UI way out. Re-verify the reused file against
                        # THIS run's pass.
                        stale = self._assess_optimization(written, tpl)
                        if stale and stale["needed"]:
                            written = ptk.MapOptimizer.optimize_map(
                                src,
                                output_dir=out_dir,
                                output_profile=tpl,
                                output_type=output_type,
                                check_existing=False,
                                **clamp,
                            )
            except Exception as e:  # noqa: BLE001 — per-texture fallback
                failed += 1
                self.logger.warning(
                    f"Texture optimization failed for "
                    f"{os.path.basename(src)} — the original ships instead: {e}"
                )
                continue

            optimized += 1
            total_before += size_before
            total_after += (
                os.path.getsize(written) if os.path.isfile(written) else 0
            )

            # Repoint the consuming nodes wherever the written file is not the
            # node's current target (always, when staging; on a normalized
            # filename, when writing back).
            if os.path.normcase(os.path.normpath(written)) != os.path.normcase(
                os.path.normpath(src)
            ):
                new_path = written.replace("\\", "/")
                for node in entry["nodes"]:
                    if write_back or node in repathed:
                        Attributes.set_plug(f"{node}.fileTextureName", new_path)
                    else:
                        scope.enter_context(
                            Attributes.pinned(
                                node,
                                _logger=self.logger,
                                fileTextureName=new_path,
                            )
                        )
                        # Count the node only once the pin actually took.
                        # Attributes.pinned DECLINES silently (a warning, then
                        # `continue`) when the plug is locked or driven by a
                        # connection -- a referenced or published asset. Adding
                        # to `repathed` before that decision meant such a node
                        # reported success while the export shipped the
                        # original, unoptimized file.
                        # Same normalization the staging comparison above
                        # uses. An exact string compare would false-negative on
                        # a separator/case difference Maya introduced, and a
                        # false negative here is worse than the bug this guard
                        # fixes: with `repathed` left empty the scope closes
                        # immediately, restoring every path BEFORE the export
                        # instead of after it.
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

        if optimized:
            sizes = ptk.FileUtils.format_bytes_delta(total_before, total_after)
            destination = (
                "written back to the scene's texture files (originals archived "
                "in 'original_textures')"
                if write_back
                else (
                    "staged for the write only — scene paths restored after "
                    "export"
                    if temp_staging
                    else f"staged beside the export in {staging_dir!r} (the FBX "
                    "references them; scene paths restored after export)"
                )
            )
            self.logger.info(
                f"Optimized {optimized} texture(s): {sizes}; {destination}."
            )
        if failed:
            self.logger.warning(
                f"{failed} texture(s) could not be optimized and ship as-is."
            )

    def reassign_duplicate_materials(self):
        """Reassign duplicate materials in the scene."""
        self.logger.debug("Reassigning duplicate materials")
        materials = self._get_all_materials()
        MatUtils.reassign_duplicate_materials(materials, delete=True)
        # Duplicates were deleted — drop every cache derived from the old set.
        self._invalidate_material_caches()
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
        """
        file_nodes = self._get_export_file_nodes()
        if not file_nodes:
            self.logger.debug(
                "No export texture file nodes found. Skipping texture path resolution."
            )
            return

        import fnmatch

        _TOKEN_RE = re.compile(r"<udim>|<f>|<uvtile>", re.IGNORECASE)
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
            if _TOKEN_RE.search(basename):
                pattern = _TOKEN_RE.sub("*", basename).lower()
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
            self.logger.info(f"Resolved {resolved_count} broken texture path(s).")
        if unresolved:
            self.logger.warning(
                f"{len(unresolved)} texture path(s) could not be resolved:"
            )
            for entry in unresolved:
                self.logger.warning(f"  {entry}")
        if not resolved_count and not unresolved:
            self.logger.debug("All texture paths are valid.")

    def smart_bake(self):
        """Pre-bake constrained and driven channels before export.

        Uses SmartBake to detect objects with constraints, driven keys,
        expressions, IK, motion paths, and blend shapes, then bakes only
        those specific channels onto an override animation layer.
        FBX export with FBXExportBakeComplexAnimation samples the final
        evaluated output, so the override layer produces correct results
        without deleting driver nodes.  After export, the layer is deleted
        to restore the original scene state non-destructively.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        self.logger.info("Analyzing scene for bake requirements...")
        # Honor the UI contract ("Optimize Keys … also controls key
        # optimization inside Smart Bake"): baked override-layer curves sit
        # behind animBlendNodes that listConnections can't traverse, so the
        # separate optimize_keys task can never reach them — SmartBake must
        # optimize its own output.  _optimize_keys_enabled is set per run by
        # _execute_tasks_and_checks.
        baker = SmartBake(
            objects=self.objects,
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=getattr(self, "_optimize_keys_enabled", False),
            use_override_layer=True,  # Non-destructive: bake to override layer
            delete_inputs=False,  # Keep constraints — layer overrides them
        )

        analysis = baker.analyze()
        if not any(a.requires_bake for a in analysis.values()):
            self.logger.info(
                "No constrained/driven objects found. Skipping smart bake."
            )
            return

        # Log what will be baked
        bake_count = sum(1 for a in analysis.values() if a.requires_bake)
        self.logger.info(f"Found {bake_count} objects requiring bake.")

        result = baker.bake(analysis)

        # Store the restore-manifest session for cleanup after export
        # (SmartBake.restore() reverses the layer, IK state, and visibility).
        if result.session_id:
            self._bake_session_id = result.session_id
        # Legacy fallback path (no session recorded).
        if result.override_layer:
            self._bake_override_layer = result.override_layer
        # Build detailed log message
        log_parts = [
            f"Smart bake completed: {result.baked_count} objects baked",
            f"range {result.time_range[0]}-{result.time_range[1]}",
        ]
        if result.override_layer:
            log_parts.append(f"layer '{result.override_layer}'")
        if result.optimized:
            log_parts.append(f"{len(result.optimized)} objects optimized")

        self.logger.info(", ".join(log_parts) + ".")

        # Refresh self.objects (no deletions expected, but re-validate). The
        # objects.setter already invalidates the _key_times cache, so no
        # explicit invalidation is needed here.
        self.objects = self._live_objects()

    def optimize_keys(self):
        """Optimize baked animation keys."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping optimization.")
            return

        self.logger.info("Optimizing baked animation keys...")
        # Optimizes base-layer curves only — the layer blend nodes smart_bake
        # creates aren't traversed by listConnections, so baked override-layer
        # curves can't be reached from here.  SmartBake optimizes those itself
        # (the smart_bake task passes this run's optimize_keys setting through).
        AnimUtils.optimize_keys(self.objects, recursive=True, quiet=True)
        # Static curves may have been deleted — drop the cached key times so
        # later tasks (tie/snap/range) re-query the surviving curves.
        self._invalidate_keyframe_cache()
        self.logger.info("Optimization completed.")

    def set_bake_animation_range(self):
        """Set the animation export range to the first and last keyframes of the specified objects if baking is enabled."""
        all_keyframes = self._get_all_keyframes()
        if not all_keyframes:
            self.logger.debug("No keyframes found. Skipping frame range setting.")
            return

        if not mel.eval("FBXExportBakeComplexAnimation -q"):
            self.logger.info(
                "Baking complex animation is disabled. Skipping frame range setting."
            )
            return

        first_key, last_key = all_keyframes[0], all_keyframes[-1]
        # Enclose fractional bookend keys: floor the start and ceil the end so
        # keys like -0.5 or 100.6 are not truncated inward (int() clips them).
        start, end = math.floor(first_key), math.ceil(last_key)
        mel.eval(f"FBXExportBakeComplexStart -v {start}")
        mel.eval(f"FBXExportBakeComplexEnd -v {end}")

        self.logger.info(f"Set animation range to start: {start}, end: {end}")

    def tie_all_keyframes(self):
        """Use AnimUtils to tie all keyframes for the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping tie operation.")
            return

        self.logger.info("Tying keyframes for all objects.")

        # Optimization: Pass cached keyframe range to avoid re-querying
        custom_range = None
        if hasattr(self, "_key_times") and self._key_times:
            # _key_times is a set, need to sort it to get min/max
            sorted_times = sorted(self._key_times)
            custom_range = (sorted_times[0], sorted_times[-1])

        AnimUtils.tie_keyframes(self.objects, absolute=True, custom_range=custom_range)
        self.logger.info("Keyframes have been tied.")

    def snap_keys_to_frame(self):
        """Snap all keyframes to the nearest whole frame."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping snap operation.")
            return

        self.logger.info("Snapping keyframes to nearest whole frame.")
        AnimUtils.snap_keys_to_frames(self.objects)
        # Key times just changed — a stale cache would make tie_all_keyframes
        # re-insert the fractional bookends this task removed.
        self._invalidate_keyframe_cache()
        self.logger.info("Keyframes have been snapped.")

    def create_glb(self, fbx_path: Optional[str] = None, announce: bool = True):
        """Convert an exported FBX to a GLB via pythontk's MeshConvert.

        Runs after the FBX has been written; ``perform_export`` invokes this
        explicitly rather than as part of the pre-export task pipeline.

        The conversion is handed the scene sidecar built from the export set
        (:class:`~mayatk.env_utils.scene_state.SceneState` -- the same readers
        the WebXR preview uses), so the production GLB gets the same
        translation repairs the preview shows: without it a modern shader
        arrives as white plastic with no emissive, and the deliverable would
        silently disagree with the preview that approved it. The envelope is
        also embedded in the GLB's ``extras``, so the artifact leaves
        self-describing. A sidecar read failure degrades to a bare conversion
        rather than costing the deliverable.

        Parameters:
            fbx_path: FBX to convert. Defaults to ``self.export_path`` (the
                FBX-alongside case). The GLB-only path passes the temp FBX so the
                ``.glb`` lands beside it (then gets moved into the output dir).
            announce: When True, log the resulting path. The GLB-only path sets
                this False and logs the final (moved) path itself.

        Returns:
            The created ``.glb`` path, or ``None`` if conversion failed.
        """
        from mayatk.env_utils.scene_state import SceneState

        src = fbx_path or self.export_path
        sidecar = None
        try:
            sections = SceneState.read(self._live_objects())
            sidecar = ptk.MeshConvert.build_scene_sidecar(
                sections,
                source=SceneState.source(),
                asset=os.path.basename(src),
            )
            if sections:
                self.logger.info(
                    "Scene sidecar (%s) riding the GLB.", ", ".join(sorted(sections))
                )
        except Exception:  # noqa: BLE001 — a bare GLB still beats no GLB
            self.logger.warning("Scene sidecar skipped.", exc_info=True)

        self.logger.info("Converting FBX to GLB...")
        try:
            glb_path = ptk.MeshConvert.fbx_to_glb(
                src,
                overwrite=True,
                auto_install=True,
                prompt=False,
                sidecar=sidecar,
            )
        except (FileNotFoundError, RuntimeError) as e:
            self.logger.error(f"GLB conversion failed: {e}")
            return None

        # GLB texture pass (both dials stamped per run by ``perform_export`` —
        # the ``_optimize_keys_enabled`` pattern). Runs LAST: a KTX2 GLB is
        # opaque to every PIL-based post-tool, so nothing may follow the
        # encode.
        #
        # Two orthogonal dials, ONE ``optimize_glb_textures`` call — a second
        # call would re-decode and re-encode every image, and a KTX2 payload
        # cannot be re-encoded at all:
        #
        # * CARRIER (cmb006 → ``_glb_texture_format``) — the container. None
        #   is "Original Textures", which writes PNG: glTF-core, lossless, no
        #   extension added, and the pass keeps the original bytes for any
        #   image the re-encode cannot beat. So Original stays Original.
        # * RESOLUTION (Optimize GLB Textures → ``_glb_optimize_textures``) —
        #   ON omits ``max_size`` entirely, taking ``optimize_glb_textures``'
        #   own default ceiling: the exact call shape the WebXR preview uses,
        #   so this panel authors no second resize policy (the optimizer
        #   already exempts lightmaps, re-encodes them losslessly, and keeps
        #   original bytes when a re-encode comes out larger). OFF pins
        #   ``max_size=0`` — resolution is never resampled behind the user,
        #   which is what an archival / engine-import export needs.
        #
        # Neither dial set = no pass at all (byte-stable conversion, the
        # default). A failure fails the deliverable — the user asked for this
        # pass, so shipping the untouched GLB anyway would be a silent
        # fallback.
        texture_format = getattr(self, "_glb_texture_format", None)
        optimize = bool(getattr(self, "_glb_optimize_textures", False))
        if texture_format or optimize:
            carrier = texture_format or "PNG"
            params: Dict[str, Any] = {"image_format": carrier}
            if not optimize:
                params["max_size"] = 0
            try:
                summary = ptk.MeshConvert.optimize_glb_textures(glb_path, **params)
            except Exception as e:  # noqa: BLE001 — deliverable must not lie
                self.logger.error(f"GLB texture pass ({carrier}) failed: {e}")
                return None
            scope = "resized" if optimize else "container only"
            if summary:
                self.logger.info(
                    f"GLB textures delivered as {carrier} ({scope}): "
                    f"{summary['images']} image(s), "
                    f"{summary['bytes_before'] / 1e6:.1f} MB -> "
                    f"{summary['bytes_after'] / 1e6:.1f} MB."
                )
            else:
                # An empty summary means the pass ran and replaced nothing —
                # no images, no Pillow, or every re-encode came out larger
                # than the source it would replace. Said out loud so "asked
                # for and got nothing" is distinguishable from "never ran".
                self.logger.info(
                    f"GLB texture pass ({carrier}, {scope}) changed nothing: "
                    "no embedded image improved on its original bytes."
                )

        if announce:
            self.logger.success(f"GLB created: {glb_path}")
        return glb_path

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
        self._include_data_export_node()
        # Mark AFTER _include_data_export_node — assigning self.objects there
        # re-clears the flag via the setter.
        self._data_node_refreshed = True
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
        """Append the ``data_export`` carrier to the export set.

        Idempotent: a no-op when the node is absent (nothing to ship) or already
        in the set.  Shared by :meth:`export_data_node` and
        :meth:`apply_declared_takes`.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        node = DataNodes.get_export_node(create=False)
        if node is None:
            self.logger.debug("No data_export node in scene — nothing to include.")
            return
        # Long path for the set-membership check (the canonical resolve keeps
        # a duplicate imported carrier from being folded in over the root one).
        export_node = cmds.ls(node, long=True)[0]
        if export_node not in (self.objects or []):
            self.objects = list(self.objects or []) + [export_node]
            self.logger.info("data_export carrier added to the export set.")

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

    def apply_declared_takes(self):
        """Export each declared take as a named Unity clip.

        Producer-agnostic: refreshes every producer's ``data_export`` channel
        (skipped when ``export_data_node`` already did so this run — the two
        tasks are default-on neighbors, and one refresh per export is enough),
        ensures the carrier is in the export selection, then realizes whatever
        ``fbx_takes`` the scene declares into FBX export state.  Runs after
        ``set_bake_animation_range`` so its union range wins.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        if not getattr(self, "_data_node_refreshed", False):
            self._refresh_scene_data_node()
        self._include_data_export_node()

        count = FbxUtils.apply_takes_from_node()
        if count:
            # Take splits + bake-complex are sticky global FBX exporter state
            # that must live THROUGH the write, so the cleanup is staged
            # deferred (post-write) rather than revert-paired.  Without it, a
            # session with no auto-export hook installed kept the splits and
            # bake range armed for every later export.  Idempotent alongside
            # the hook's own kAfterExport reset.
            self.stage_deferred_restore("fbx_takes", FbxUtils.reset_takes)
            self.logger.info(
                f"Animation takes: {count} clip(s) realized from the declared "
                "fbx_takes; shot metadata embedded on data_export."
            )
        else:
            self.logger.debug("No takes declared. Skipping animation takes.")


class _TaskChecksMixin(_TaskDataMixin):
    """ """

    _LOD_SUFFIX_REGEX = re.compile(r"_lod\d*$", re.IGNORECASE)
    _MAX_LISTED_OBJECTS = 25
    _DEFAULT_FLOOR_TOLERANCE = 0.5

    def _obj_link(self, node: str, action: str = "reveal") -> str:
        """Return a clickable log link for a Maya scene node.

        Parameters:
            node:   Full or short DAG path (used as both label and param).
            action: ``"select"`` or ``"reveal"`` (default).
        """
        short = node.rsplit("|", 1)[-1]
        return self.logger.log_link(short, action, node=node)

    def _truncate_obj_entries(
        self, entries: List[str], limit: Optional[int] = None
    ) -> List[str]:
        """Cap per-object log entries with a summary tail when the list is long.

        Returns the entries unchanged when ``len(entries) <= limit``; otherwise
        returns the first ``limit`` entries followed by ``"... and N more (omitted)"``.
        """
        cap = self._MAX_LISTED_OBJECTS if limit is None else limit
        if len(entries) <= cap:
            return entries
        remaining = len(entries) - cap
        return entries[:cap] + [f"... and {remaining} more (omitted)"]

    def check_geometry_lod_suffix(self) -> tuple:
        """Check for geometry whose names end with '_LOD' or '_LOD' followed by digits.

        Returns:
            tuple: (status: bool, messages: list)

        Notes:
            - This check is informational. It returns True regardless, and lists any matches.
            - Suffix examples matched: '_LOD', '_LOD0', '_LOD1', '_LOD02', etc. (case-insensitive)
        """
        messages: List[str] = []

        matches = {}
        for obj in self._live_objects():
            # Check if geometry (has shapes)
            # Use cmds for speed
            shapes = cmds.listRelatives(obj, shapes=True)
            if not shapes:
                continue

            name = obj.split("|")[-1]
            if self._LOD_SUFFIX_REGEX.search(name):
                matches.setdefault(name, obj)

        if matches:
            items = [f"  - {self._obj_link(matches[n], 'reveal')}" for n in sorted(matches)]
            messages.append("Geometry with LOD suffix detected (informational):")
            messages.extend(items)
            # The runner only surfaces messages from FAILING checks; this one
            # always passes, so its listing must be logged directly or the
            # check is a silent no-op. As ONE grouped record: every log record
            # is its own paragraph in the export panel, so a line per match
            # rendered the listing as N blank-line-separated sections.
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.log_group(
                    f"LOD suffixes detected ({len(matches)})", items
                )

        return True, messages

    def ignore_groups(self, names: str) -> None:
        """Exclude top-level groups matching *names* (case-insensitive) and all
        their descendants from the export object list.

        Parameters:
            names: Comma-separated group names to exclude (e.g. ``"temp, proxy"``).
        """
        if not self.objects or not names:
            return

        # Parse comma-separated names, strip whitespace, lowercase for matching
        target_names = {n.strip().lower() for n in names.split(",") if n.strip()}
        if not target_names:
            return

        # self.objects contains only geometry transforms (never assemblies),
        # so derive each object's root ancestor from its long DAG path. Unlike
        # check_root_default_transforms (which requires len(parts) > 2 AND gates
        # on NodeUtils.is_group), this uses len(parts) > 1 and no is_group gate,
        # so it also matches a top-level *ungrouped* node whose short name equals
        # a target — intentional here (a target need not be a group), but note
        # the boundaries deliberately differ.
        root_groups = set()
        for obj in self.objects:
            parts = obj.split("|")  # "|root|...|geo" — root is segment [1]
            if len(parts) > 1:
                root_groups.add("|" + parts[1])

        # Find top-level groups whose short name matches any target
        root_nodes = cmds.ls(list(root_groups), long=True) or []
        matched_roots = [
            node for node in root_nodes if node.split("|")[-1].lower() in target_names
        ]

        if not matched_roots:
            self.logger.debug(f"No top-level groups matching {target_names} found.")
            return

        # Gather the matched roots and all their descendants
        exclude = set(matched_roots)
        for root in matched_roots:
            descendants = (
                cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
            )
            exclude.update(descendants)

        original_count = len(self.objects)
        self.objects = [obj for obj in self.objects if obj not in exclude]
        removed = original_count - len(self.objects)

        # ONE grouped record with the summary as its title — a line per group
        # plus a separate total rendered as N+1 blank-line-separated sections.
        # (``matched_roots`` is non-empty here: the early return above covers
        # the no-match case, which logs at debug.)
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group(
                f"Excluded {removed} object(s) under "
                f"{len(matched_roots)} group(s) from export",
                list(matched_roots),
            )

    def exclude_hdr(self) -> None:
        """Remove Arnold HDR environment lights (``aiSkyDomeLight``) from the export set.

        The HDR skydome is image-based scene lighting, not deliverable
        geometry, so it should not ride into a game-engine FBX. In the
        'All Scene Objects' mode the skydome transform is otherwise picked up
        by ``cmds.ls(transforms=True)``; this strips the skydome transform(s)
        and their shapes back out of ``self.objects``.

        A no-op when mtoa is unloaded (no skydome can exist) or the export set
        contains none.
        """
        if not self.objects:
            return

        # Guard the plugin first: querying ``cmds.ls(type="aiSkyDomeLight")``
        # for an unregistered type emits an "Unknown object type" warning, and
        # without mtoa loaded no skydome can exist anyway.
        if not EnvUtils.is_plugin_loaded("mtoa"):
            return

        skydomes = cmds.ls(type="aiSkyDomeLight", long=True) or []
        if not skydomes:
            return

        exclude = set()
        for shape in skydomes:
            exclude.add(shape)
            exclude.update(
                cmds.listRelatives(shape, parent=True, fullPath=True) or []
            )

        original_count = len(self.objects)
        self.objects = [obj for obj in self.objects if obj not in exclude]
        removed = original_count - len(self.objects)
        if removed:
            self.logger.info(
                f"Excluded {removed} HDR environment node(s) (aiSkyDomeLight) from export."
            )
        else:
            self.logger.debug("No HDR skydome in the export set — nothing to exclude.")

    def check_root_default_transforms(self) -> tuple:
        """Check if all root group nodes have default transforms.

        A frozen root reads identity on every channel, so the live values alone
        cannot tell "authored at identity" from "identity because someone froze
        it" — and the second case still carries a pre-freeze transform in its
        bake history that a downstream un-freeze would reinstate. Those roots
        are reported (with the transform the freeze consumed) but do NOT fail
        the check: the scene as it stands really is at identity, which is what
        the exporter needs.
        """
        log_messages = []
        box_logged = False
        frozen_box_logged = False
        tolerance = 1e-5
        has_non_default_transforms = False
        frozen_messages = []

        # self.objects contains only geometry transforms (never assemblies),
        # so we walk up each object's DAG path to find the root ancestor.
        root_groups = set()
        for obj in self.objects:
            # Long path: "|root|child|...|geo" — the root is segment [1]
            parts = obj.split("|")
            if len(parts) > 2:
                root_long = "|" + parts[1]
                root_groups.add(root_long)

        root_nodes = cmds.ls(list(root_groups), long=True) or []

        for node in root_nodes:
            if not NodeUtils.is_group(node):
                continue

            translate = cmds.getAttr(f"{node}.translate")[0]
            rotate = cmds.getAttr(f"{node}.rotate")[0]
            scale = cmds.getAttr(f"{node}.scale")[0]

            if (
                not all(abs(val) < tolerance for val in translate)
                or not all(abs(val) < tolerance for val in rotate)
                or not all(abs(val - 1) < tolerance for val in scale)
            ):
                if not box_logged:
                    log_messages.append(
                        "Root level group nodes found with non-default transforms:"
                    )
                    box_logged = True

                has_non_default_transforms = True
                link = self._obj_link(node)
                log_messages.append(
                    f"Node: {link}, Translate: {translate}, Rotate: {rotate}, Scale: {scale}"
                )
                continue

            # Reads default — but a freeze is one of the ways a node gets here.
            stored = XformUtils.get_stored_transforms(node)
            if stored is not None:
                if not frozen_box_logged:
                    frozen_messages.append(
                        "Root level group nodes at default transforms because "
                        "they were FROZEN (not authored at identity):"
                    )
                    frozen_box_logged = True
                frozen_messages.append(
                    f"Node: {self._obj_link(node)}, baked Translate: "
                    f"{tuple(round(v, 6) for v in stored['translate'])}, "
                    f"baked Scale: {tuple(round(v, 6) for v in stored['scale'])}"
                )

        # Frozen roots are reported after any real failures, and never change
        # the verdict — the exported scene is at identity either way.
        log_messages.extend(frozen_messages)

        if has_non_default_transforms:
            return (
                False,
                log_messages,
            )  # Failed, log the nodes with non-default transforms

        return True, log_messages  # All checks passed, no non-default transforms

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
        temp_staging = bool(getattr(self, "_glb_only", False))
        if not temp_staging:
            try:
                temp_staging = bool(mel.eval("FBXExportEmbeddedTextures -q"))
            except Exception:  # noqa: BLE001 — plugin not loaded yet
                temp_staging = False
        export_path = getattr(self, "export_path", "")
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

        **Non-destructive by default** (``_texture_write_back`` unset -- the
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
        write_back = getattr(self, "_texture_write_back", False)
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
            scope.enter_context(MatSnapshot.network_scope(materials))
            self.stage_deferred_context("convert_textures", scope)
            config = {
                "preset": template,
                "move_to_folder": staging_dir,
                "transfer_mode": "copy",
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
        if write_back and getattr(self, "_relative_paths_enabled", False):
            file_nodes = self._get_export_file_nodes()
            if file_nodes:
                MatUtils.stage_textures_relative(file_nodes)
        return None

    def check_material_compatibility(self, template) -> tuple:
        """Every mask map matches the chosen texture template (post-conversion).

        The check half of the Texture Template combobox: armed only when a
        template is selected, alongside :meth:`convert_textures`. Checks run
        after the task phase, so this validates the **converted** state -- it
        fails only for a mask map the conversion could not bring to the
        template (unreadable source, missing inputs, an unsupported material
        type), naming the residuals rather than blocking the fix. The override
        button skips it like any other check.

        The judgement is pythontk's (``MeshConvert.sidecar_foreign_packings``
        -> ``MapFactory.foreign_packings``), read off the same sidecar the GLB
        conversion will carry and keyed by the registry workflow the combobox
        named -- so no engine name or channel layout is spelled out here and
        blendertk's twin cannot drift from it.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not template:
            return True, []
        from mayatk.env_utils.scene_state import SceneState

        log_messages = []
        try:
            sections = SceneState.read(self._live_objects())
        except Exception:  # noqa: BLE001 — a read failure must not block an export
            self.logger.warning("Material compatibility check skipped.", exc_info=True)
            return True, log_messages

        foreign = ptk.MeshConvert.sidecar_foreign_packings(
            {"sections": sections}, workflow=template
        )
        if not foreign:
            return True, log_messages

        # Count header then indented offenders, as check_path_length does.
        log_messages.append(
            f"{len(foreign)} mask map(s) do not match the {template!r} template "
            "after conversion:"
        )
        log_messages.extend(
            f"  - {map_type}: {os.path.basename(path)}"
            for path, map_type in sorted(foreign.items())
        )
        log_messages.append(
            "See the Map Updater log above for why these did not convert, or "
            "set Textures back to 'As Authored' to ship them as they are."
        )
        return False, log_messages

    def check_texture_optimization(self, template) -> tuple:
        """Every shipping texture is optimized for its map type (post-task).

        The check half of the Optimize Textures checkbox: armed alongside
        :meth:`optimize_textures` by the same setting, judged through the same
        :meth:`_assess_optimization`, and — because checks run after tasks —
        validating the **staged/written** state the export will actually
        read. It FAILS only for a texture the task should have optimized but
        could not (a per-texture failure), naming the residuals rather than
        blocking the fix.

        Everything the pass deliberately does not touch is reported without
        failing: tiled/UDIM sets (measured via their 1001 tile — the task is
        single-file), and the active template's ``DeliveryBudget`` advisories
        — advisory means REPORTED, not resampled, and never a blocked export.
        With a Max Texture Size clamp set the resize IS part of the pass, so
        an over-size residual the task could not shrink fails here like any
        other unoptimized map. Those notes are logged directly (the runner only surfaces
        messages from failing checks). Unreadable or missing files are
        :meth:`check_valid_paths`' domain and are skipped here.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not template:
            return True, []
        tpl = template if isinstance(template, str) else None

        offenders: List[str] = []
        notes: List[str] = []
        # include_tiled: the task cannot TOUCH a tiled set, but the gate
        # should still measure it (via its 1001 tile) so an unoptimized one
        # is at least reported instead of slipping past the scan.
        for _key, entry in sorted(
            self._export_texture_sources(include_tiled=True).items()
        ):
            verdict = self._assess_optimization(entry["path"], tpl)
            if verdict is None:
                continue
            name = os.path.basename(entry["path"])
            if verdict["needed"]:
                links = ", ".join(
                    self._obj_link(n, "select") for n in sorted(entry["nodes"])
                )
                line = f"  - {links} -> {name}: {'; '.join(verdict['reasons'])}"
                if entry["tiled"]:
                    notes.append(line + " (tiled set — not auto-optimized)")
                else:
                    offenders.append(line)
            for warning in verdict["warnings"]:
                notes.append(f"  - {name}: {warning}")

        # Advisory tier: budget notes and untouchable residuals inform, never
        # gate. Logged directly — the runner only surfaces messages from
        # FAILING checks, so returning them on a pass would be a silent no-op.
        if notes and self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group(
                f"Texture optimization notes ({len(notes)})", notes
            )

        if offenders:
            pass_desc = f"the {tpl!r} template" if tpl else "their map type"
            header = [
                f"{len(offenders)} texture(s) are not optimized for "
                f"{pass_desc} after the optimization task:"
            ]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []

    def check_path_length(self, max_length: Optional[int] = None) -> tuple:
        """Check that no export path exceeds the OS path-length limit.

        Covers the export destination and every texture feeding the export
        materials, measured in ABSOLUTE form — that is the string the
        filesystem, the FBX plug-in and the receiving pipeline all see.  An
        over-long path fails late and opaquely (a write that reports success
        but produced nothing, a texture the plug-in silently can't embed), and
        a path that fits here still breaks on the next machine: the Windows
        260-character cap applies unless that machine opted into long paths.

        Sidecars written beside the export (``.scene_data.json``, ``.fbm``
        folders) are longer than the export path itself, so leave headroom by
        setting a budget below the OS limit.

        Parameters:
            max_length: Maximum allowed path length — the spin box's value.
                ``None`` uses this OS's limit
                (``ptk.FileUtils.path_length_limit``); ``0`` (the spin box's
                "OFF" position) or ``"OFF"`` disables the check.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if max_length is not None:
            if not max_length or str(max_length).upper() == "OFF":
                return True, []
            try:
                limit = int(max_length)
            except (TypeError, ValueError):
                self.logger.warning(
                    f"Invalid max path length '{max_length}'. Skipping length check."
                )
                return True, []
        else:
            limit = ptk.FileUtils.path_length_limit()

        # A relative texture path must be measured the way MAYA resolves it —
        # against the project root. os.path.abspath would resolve it against
        # the process CWD, which is only the same directory when the
        # set_workspace task happened to run.
        project_root = cmds.workspace(query=True, rootDirectory=True) or ""

        def absolute(path: str) -> str:
            expanded = os.path.expandvars(path)
            if not os.path.isabs(expanded):
                # With no project open there is no better base than the CWD
                # (abspath's own) — resolve here either way, so the length the
                # message reports is the length the verdict was made on.
                expanded = (
                    os.path.join(project_root, expanded)
                    if project_root
                    else os.path.abspath(expanded)
                )
            return os.path.normpath(expanded).replace("\\", "/")

        offenders = []

        export_path = getattr(self, "export_path", None)
        if export_path:
            resolved = absolute(export_path)
            if ptk.FileUtils.exceeds_path_length(resolved, limit):
                offenders.append(
                    f"  - export path ({len(resolved)} chars) -> {resolved}"
                )

        seen_paths = set()
        for node in self._get_export_file_nodes():
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue
            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)

            # Measure what the pipeline will see: the stored path resolved the
            # way Maya resolves it, falling back to a project-root join when it
            # doesn't resolve (a missing texture is check_valid_paths' domain,
            # but its path length is still this check's).
            resolved = absolute(MatUtils.resolve_path(path, search=False) or path)
            if ptk.FileUtils.exceeds_path_length(resolved, limit):
                link = self._obj_link(node, "select")
                offenders.append(f"  - {link} ({len(resolved)} chars) -> {resolved}")

        if offenders:
            header = [
                f"{len(offenders)} path(s) exceed the {limit}-character limit:",
            ]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []

    def check_valid_paths(self) -> tuple:
        """Check that every export texture and scene reference resolves on disk
        — the way Maya resolves it AND the way the FBX plugin will locate it
        at write time.

        Texture scope is the ``file`` nodes feeding the export materials
        (``_get_export_file_nodes``), not every ``file`` node in the scene.
        Scene-wide scanning flagged maps that never ship: the Arnold skydome's
        HDR (already dropped from the export set by ``exclude_hdr``) and the
        orphaned file nodes left behind when ``reassign_duplicate_materials``
        deletes a duplicate shader — the file nodes outlive the shader they fed.

        Maya-side resolution goes through ``MatUtils.resolve_path(search=False)``:
        env vars and ``workspace(expandName=...)`` — i.e. exactly how Maya
        itself resolves the stored path — plus ``<UDIM>`` expansion, which the
        previous hand-rolled lookup lacked entirely (every tiled texture read
        as missing).  ``search=False`` is load-bearing: the default hunt would
        match any same-named file under ``sourceimages``, so a node pointing at
        a stale directory would pass validation and still ship broken.

        A Maya-resolvable path is then re-probed the way the **fbxmaya plugin**
        locates media when it writes: plain OS resolution — absolute paths
        as-is, relative paths against the process CWD; the workspace is never
        consulted (probe-proven 2026-08-04).  Without this second gate the
        check passed workspace-relative paths that the plugin then reported as
        "The following texture(s) will not be embedded" after the export — or,
        in batch (or with embedding off), shipped silently broken with no
        console warning at all.  The ``set_workspace`` task aligns the CWD
        with the workspace root, so in the default pipeline both probes agree.

        Three texture verdicts, because they have three different remedies:
        *Missing Texture* (Maya resolves nothing — repoint the node),
        *Unresolved tile/frame pattern* (same, but the stored value carries a
        token, so the useful question is whether the tile exists rather than
        whether the filename does) and *Not locatable at write time* (Maya
        resolves it through the workspace, the plugin's CWD resolution will
        not — enable Auto Set Workspace).  The token split belongs to the
        FIRST gate: anything reaching the second has already resolved, so a
        token there means only that the CWD is wrong.

        Entries are grouped by path so a texture shared by several file nodes
        logs once.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages = []
        all_valid = True

        # 1. Texture paths — scoped to the maps that will actually ship.
        missing_textures: Dict[str, List[str]] = {}
        fbx_unlocatable: Dict[str, List[str]] = {}
        unresolved_tokens: Dict[str, List[str]] = {}
        for node in self._get_export_file_nodes():
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                # Some empty file nodes might exist?
                continue

            # Tile/frame tokens collapse through MatUtils' single token table
            # rather than a local <UDIM>-only replace: a <uvtile>, <u>_<v>,
            # <f> or <frame> path failed that narrower probe and was then
            # reported as an FBX working-directory problem, so the remedy
            # offered ("Auto Set Workspace") could not fix it. A token-free
            # path probes back unchanged, so the comparison IS the "did this
            # carry a token" test -- no second match. (``None`` means a glob
            # token found nothing, which is also a token.)
            expanded = os.path.expandvars(path)
            probe = MatUtils.probe_texture_path(expanded)
            carries_token = probe != expanded

            if not MatUtils.resolve_path(path, search=False):
                # Neither the env-expanded nor the workspace-expanded form
                # names a file, so no working directory can rescue it. The
                # token split happens HERE, at the resolution gate, and not at
                # the FBX gate below: a tokened path that reaches the FBX gate
                # has by definition already resolved somewhere, so classifying
                # it there sent every RELATIVE tiled set -- the normal storage
                # form in a Maya project, resolvable only through the
                # workspace -- to "no workspace setting fixes it", negating
                # the one remedy that does.
                if carries_token:
                    unresolved_tokens.setdefault(path, []).append(node)
                else:
                    missing_textures.setdefault(path, []).append(node)
                continue

            # Maya resolves it — now probe it the way the FBX plugin will at
            # write time (os.path.abspath resolves relative paths against the
            # CWD, never the workspace).
            if probe is None or not os.path.isfile(os.path.abspath(probe)):
                fbx_unlocatable.setdefault(path, []).append(node)

        if missing_textures:
            all_valid = False
            entries = []
            for path in sorted(missing_textures):
                links = ", ".join(
                    self._obj_link(n, "select") for n in sorted(missing_textures[path])
                )
                entries.append(f"Missing Texture: {links} -> {path}")
            log_messages.extend(self._truncate_obj_entries(entries))

        if fbx_unlocatable:
            all_valid = False
            log_messages.append(
                f"{len(fbx_unlocatable)} texture path(s) resolve in Maya but the "
                "FBX plug-in will not locate them at write time (it resolves "
                "relative paths against the process working directory, not the "
                "workspace) — the export would end with 'The following "
                "texture(s) will not be embedded'. Enable the 'Auto Set "
                "Workspace' task to align the working directory."
            )
            entries = []
            for path in sorted(fbx_unlocatable):
                links = ", ".join(
                    self._obj_link(n, "select") for n in sorted(fbx_unlocatable[path])
                )
                entries.append(f"Not locatable at write time: {links} -> {path}")
            log_messages.extend(self._truncate_obj_entries(entries))

        if unresolved_tokens:
            all_valid = False
            log_messages.append(
                f"{len(unresolved_tokens)} texture path(s) carry a tile/frame "
                "token (<UDIM>, <uvtile>, <u>_<v>, <f>, <frame>) that matches no "
                "file on disk. This is NOT the FBX working-directory case — the "
                "pattern itself resolves to nothing, so no workspace setting "
                "fixes it. Check the tile/frame actually exists, or repoint the "
                "file node."
            )
            entries = []
            for path in sorted(unresolved_tokens):
                links = ", ".join(
                    self._obj_link(n, "select")
                    for n in sorted(unresolved_tokens[path])
                )
                entries.append(f"Unresolved tile/frame pattern: {links} -> {path}")
            log_messages.extend(self._truncate_obj_entries(entries))

        # 2. Reference Paths
        references = cmds.ls(references=True) or []
        for ref in references:
            try:
                # withoutCopyNumber=True gets actual file path
                path = cmds.referenceQuery(ref, filename=True, withoutCopyNumber=True)
                if path:
                    expanded_path = os.path.expandvars(path)
                    if not os.path.exists(expanded_path):
                        all_valid = False
                        link = self._obj_link(ref, "select")
                        log_messages.append(f"Missing Reference: {link} -> {path}")
            except Exception:
                continue

        if all_valid:
            log_messages.append("All checked paths exist on disk.")

        return all_valid, log_messages

    def check_texture_file_size(self, max_size_mb: Optional[float] = 16.0) -> tuple:
        """Check that no export texture exceeds a maximum on-disk file size.

        Oversized source textures bloat the exported asset and usually signal an
        un-downsized authoring map (e.g. an 8K master) that shouldn't ship to a
        game engine.  Scoped to the textures feeding the export materials, so it
        only flags maps that will actually travel with the FBX.

        Parameters:
            max_size_mb: Maximum allowed texture size in megabytes — the spin
                box's value, or any numeric-ish string (e.g. ``"16"``).
                ``None``, ``0`` (the spin box's "OFF" position), ``""``, or
                ``"OFF"`` disables the check (returns pass); a non-numeric
                value logs a warning and skips.  Defaults to 16 MB.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not max_size_mb or str(max_size_mb).upper() == "OFF":
            return True, []

        try:
            limit_mb = float(max_size_mb)
        except (TypeError, ValueError):
            self.logger.warning(
                f"Invalid max texture size '{max_size_mb}'. Skipping size check."
            )
            return True, []
        limit_bytes = limit_mb * 1024 * 1024

        offenders: List[str] = []
        seen_paths = set()

        for node in self._get_export_file_nodes():
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                continue

            # Resolve to the on-disk file via MatUtils.resolve_path so
            # project-relative paths still resolve — the default-on
            # convert_to_relative_paths task runs before checks and rewrites
            # texture paths to workspace-relative form.  Missing files are the
            # domain of check_valid_paths, so an unresolved path is skipped.
            # search=False for the same reason that check applies it: the
            # basename hunt would size-probe a same-named file the node does
            # not actually point at.
            resolved = MatUtils.resolve_path(path, search=False)
            if not resolved:
                continue

            # Collapse the tile/frame token to a concrete file for the size
            # probe (resolve_path deliberately returns a path that still
            # carries it). Through the shared table, not a <UDIM>-only
            # replace: a <uvtile>/<u>_<v>/<f>/<frame> path did not collapse,
            # so os.path.isfile failed and the texture was skipped -- an
            # oversized frame sequence escaped the size limit entirely.
            probe = MatUtils.probe_texture_path(resolved)
            if probe is None:  # frame pattern with nothing on disk
                continue
            if probe in seen_paths:
                continue
            seen_paths.add(probe)

            if not os.path.isfile(probe):
                continue

            size = os.path.getsize(probe)
            if size > limit_bytes:
                link = self._obj_link(node, "select")
                offenders.append(
                    f"  - {link} -> {os.path.basename(probe)} "
                    f"({size / (1024 * 1024):.2f} MB)"
                )

        if offenders:
            header = [f"{len(offenders)} texture(s) exceed the {limit_mb:g} MB limit:"]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []

    # Scratch/mangled name signatures — the diagnostics repair
    # (SceneDiagnostics.repair_mangled_names) owns the definition; this
    # check and that repair must always agree on what "mangled" means.
    MANGLED_NAME_RE = SceneDiagnostics.MANGLED_NAME_RE

    def check_mangled_names(self) -> tuple:
        """Check the export set (including shapes) for scratch/mangled names.

        Catches names no tool should ever ship: uninstance ``__uninst_tmp``
        scratch tokens, Rizom ``__RZTMP`` round-trip suffixes, ``FBXASC###``
        import escapes, and underscore runs.  Instanced shapes make one bad
        node fan out across every instance path in the exported hierarchy
        (and its scene_data.json sidecar), so a single offender is worth
        failing on.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages = []
        nodes = self._live_objects()
        if not nodes:  # listRelatives([]) would fall back to the selection
            return True, log_messages
        nodes += cmds.listRelatives(nodes, allDescendents=True, fullPath=True) or []

        offenders = []
        seen = set()
        for node in nodes:
            if node in seen:
                continue
            seen.add(node)
            leaf = node.split("|")[-1].split(":")[-1]
            if self.MANGLED_NAME_RE.search(leaf):
                offenders.append(leaf)

        if not offenders:
            return True, log_messages

        log_messages.append(f"{len(offenders)} node(s) carry scratch/mangled names:")
        for leaf in offenders[:20]:
            link = self._obj_link(leaf, "select")
            log_messages.append(f"  - {link}")
        if len(offenders) > 20:
            log_messages.append(f"  … and {len(offenders) - 20} more")
        log_messages.append(
            "Repair via the 'Fix Mangled Names' task "
            "(or SceneDiagnostics.repair_mangled_names)."
        )
        return False, log_messages

    def check_duplicate_locator_names(self) -> tuple:
        """Check for duplicate locator short names among the specified objects.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages = []
        # Use cmds for speed
        # Get all shapes of type locator from self.objects (which are transforms)
        objects = self._live_objects()
        if not objects:  # listRelatives([]) would fall back to the selection
            return True, log_messages

        locator_shapes = (
            cmds.listRelatives(objects, shapes=True, type="locator", fullPath=True)
            or []
        )
        if not locator_shapes:
            return True, log_messages

        locator_transforms = (
            cmds.listRelatives(locator_shapes, parent=True, fullPath=True) or []
        )

        seen = {}
        duplicates = set()
        for loc in locator_transforms:
            name = loc.split("|")[-1]
            if name in seen:
                duplicates.add(name)
            else:
                seen[name] = loc

        if duplicates:
            for name in sorted(duplicates):
                # Short names may be ambiguous; link uses the first full path
                full_path = seen.get(name, name)
                link = self._obj_link(full_path, "reveal")
                log_messages.append(f"Duplicate locator name: {link}")
            return False, log_messages
        return True, log_messages

    def check_duplicate_materials(self) -> tuple:
        """Check if any duplicate materials are present in the scene."""
        log_messages = []

        materials = self._get_all_materials()
        duplicate_mapping = MatUtils.find_materials_with_duplicate_textures(materials)

        if duplicate_mapping:
            for original, duplicates in duplicate_mapping.items():
                for duplicate in duplicates:
                    dup_link = self._obj_link(str(duplicate), "select")
                    orig_link = self._obj_link(str(original), "select")
                    log_messages.append(f"Duplicate: {dup_link} -> {orig_link}")
            return False, log_messages  # Failed, log the duplicates

        return True, log_messages  # All checks passed, no duplicates found

    def check_referenced_objects(self) -> tuple:
        """Check if any referenced objects are present in the scene."""
        log_messages = []
        # Check all referenced objects in the scene, not just the selected objects
        referenced_objects = cmds.ls(references=True) or []

        if referenced_objects:
            for ref in referenced_objects:
                link = self._obj_link(ref, "select")
                log_messages.append(f"Referenced Object: {link}")
            return False, log_messages  # Failed, log the referenced objects

        return True, log_messages  # All checks passed, no referenced objects found

    def check_framerate(self, target_framerate: Optional[str]) -> tuple:
        """Check if the scene's current framerate matches the target framerate."""
        if not target_framerate or str(target_framerate).upper() == "OFF":
            return True, []

        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping framerate check.")
            return True, []

        current_time_unit = cmds.currentUnit(query=True, time=True)
        if current_time_unit != target_framerate:
            return False, [
                f"Framerate mismatch: Current time unit is {current_time_unit}, expected {target_framerate}."
            ]

        return True, []

    def check_objects_below_floor(
        self, tolerance: float = _DEFAULT_FLOOR_TOLERANCE
    ) -> tuple:
        """Check if any object's geometry is below the floor plane (Y=0).

        Args:
            tolerance: Allowable distance (in scene units) beneath the plane
                before failing.  The UI exposes this as a checkbox, so enabling
                the check passes ``True``; that is treated as "use the default
                tolerance" rather than coerced to ``1.0``.  An explicit ``None``
                still means a strict ``0.0``.
        """
        offenders: List[str] = []

        # ``True`` (checkbox enabled) is a bool, not a real distance — honor the
        # documented default instead of float(True) == 1.0.
        if tolerance is True:
            tolerance = self._DEFAULT_FLOOR_TOLERANCE
        tolerance = 0.0 if tolerance is None else max(0.0, float(tolerance))
        limit = -tolerance

        geometry_types = NodeUtils.SURFACE_TYPES
        for obj in self._live_objects():
            # Surface geometry only — the check is named "geometry below
            # floor", but the old any-shape guard also failed control curves
            # and locators dipping under Y=0 in 'selected'/'all' modes.
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
            if not shapes:
                continue
            if not any(cmds.nodeType(s) in geometry_types for s in shapes):
                continue

            bbox = cmds.xform(obj, query=True, ws=True, bb=True)
            if not bbox:
                continue

            ymin = bbox[1]
            if ymin < limit:
                link = self._obj_link(obj)
                offenders.append(
                    f"Object: {link} - Below Floor: True (Y-min: {ymin:.3f})"
                )

        if offenders:
            header = [
                f"{len(offenders)} object(s) below floor "
                f"(tolerance: {tolerance:.3f} unit{'s' if tolerance != 1 else ''})"
            ]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []  # All checks passed, no objects below the floor

    def check_overlapping_duplicate_mesh(self) -> tuple:
        """Check for duplicate overlapping geometry among the export objects.

        Returns:
            tuple: (status: bool, messages: list)
        """
        duplicates = EditUtils.get_overlapping_duplicates(objects=self._live_objects())
        if duplicates:
            messages = [
                f"Overlapping duplicate object: {self._obj_link(obj)}"
                for obj in duplicates
            ]
            return False, messages  # Failed, duplicates found
        return True, []  # Passed, no duplicates

    def check_hidden_geometry(self) -> tuple:
        """Check for geometry that will ship in the FBX while hidden.

        Beyond the plain ``.visibility`` flag this also reads DISPLAY-LAYER
        hiding (the old check's blind spot — layer-hidden geometry shipped
        unflagged in every mode).  Objects whose visibility has an incoming
        connection are deliberately NOT flagged: the 'visible' export mode
        includes animated-visibility objects on purpose (the animation is
        baked and ships), so flagging them made the check fail exactly the
        content that mode exists to carry.
        """
        hidden_objects = []
        geometry_types = NodeUtils.SURFACE_TYPES

        for obj in self._live_objects():
            # Check if geometry (has shapes)
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
            if not shapes:
                continue

            # Check if any shape is actually geometry
            is_geometry = False
            for shape in shapes:
                if cmds.nodeType(shape) in geometry_types:
                    is_geometry = True
                    break

            if not is_geometry:
                continue

            # Animated/driven visibility is intentional export content, not
            # hidden geometry — skip regardless of the current-frame value.
            if cmds.listConnections(
                f"{obj}.visibility", source=True, destination=False
            ):
                continue

            layer_hidden = False
            for layer in set(cmds.listConnections(obj, type="displayLayer") or []):
                if layer == "defaultLayer":
                    continue
                try:
                    if not cmds.getAttr(f"{layer}.visibility"):
                        layer_hidden = True
                        break
                except ValueError:
                    continue

            if layer_hidden:
                hidden_objects.append((obj, "display layer"))
            elif not cmds.getAttr(f"{obj}.visibility"):
                hidden_objects.append((obj, "visibility off"))

        if hidden_objects:
            return False, [
                f"Hidden geometry detected ({reason}): {self._obj_link(obj)}"
                for obj, reason in hidden_objects
            ]
        return True, []

    def check_untied_keyframes(self) -> tuple:
        """Check if there are any untied keyframes on the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping untied keyframe check.")
            return True, []

        log_messages = []
        untied_keyframes_found = False

        # Optimization: Get all connections at once to avoid N calls to listConnections
        # connections=True returns [source, dest, source, dest...]
        # plugs=True returns [obj.plug, curve.output, ...]
        connections = (
            cmds.listConnections(
                self._live_objects(),
                type="animCurve",
                source=True,
                destination=False,
                connections=True,
                plugs=True,
            )
            or []
        )

        # Drop unitless (set-driven-key) curves: their first/last "keys" are
        # driver values, not frames, so comparing them against the object's
        # time range false-positives on every SDK-rigged object.
        pairs = list(zip(connections[::2], connections[1::2]))
        time_curves = set(
            cmds.ls(
                [c.split(".")[0] for _, c in pairs],
                type=list(AnimUtils.TIME_CURVE_TYPES),
            )
            or []
        )

        # Parse into a dict: obj_name -> set(curves)
        obj_curves = {}
        for obj_plug, curve_plug in pairs:
            obj_name = obj_plug.split(".")[0]  # e.g. "pCube1.translateX"
            curve_name = curve_plug.split(".")[0]  # e.g. "animCurveTL1.output"
            if curve_name not in time_curves:
                continue

            if obj_name not in obj_curves:
                obj_curves[obj_name] = set()
            obj_curves[obj_name].add(curve_name)

        for obj, curves in obj_curves.items():
            if not curves:
                continue

            # Get start/end for each curve
            curve_data = []
            min_start = float("inf")
            max_end = float("-inf")

            for curve in curves:
                # findKeyframe on a curve is fast
                s = cmds.findKeyframe(curve, which="first")
                e = cmds.findKeyframe(curve, which="last")
                curve_data.append((curve, s, e))

                if s < min_start:
                    min_start = s
                if e > max_end:
                    max_end = e

            # Check for mismatches
            obj_link = self._obj_link(obj)
            for curve, s, e in curve_data:
                if s > min_start:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj_link} (Start {s} != {min_start})"
                    )
                if e < max_end:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj_link} (End {e} != {max_end})"
                    )

        if untied_keyframes_found:
            return False, log_messages  # Failed, log untied keyframes

        return True, log_messages  # All checks passed, no untied keyframes

    def check_floating_point_keys(self) -> tuple:
        """Check if there are any floating point keyframes on the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping floating point key check.")
            return True, []

        log_messages = []
        offenders = []

        # Optimization: Iterate curves instead of objects
        # This is much faster than querying keyframes per object
        all_curves = (
            cmds.listConnections(
                self._live_objects(), type="animCurve", source=True, destination=False
            )
            or []
        )
        # Time-driven curves only: a set-driven key's inbetweens (driver
        # values like 0.25/0.5) would otherwise read as "floating point keys".
        all_curves = (
            cmds.ls(list(set(all_curves)), type=list(AnimUtils.TIME_CURVE_TYPES))
            or []
        )

        for curve in all_curves:
            times = cmds.keyframe(curve, query=True, timeChange=True)
            if not times:
                continue

            for t in times:
                if not math.isclose(t, round(t), abs_tol=1e-4):
                    # Find object name
                    conn = cmds.listConnections(
                        curve, plugs=True, destination=True, source=False
                    )
                    obj_name = conn[0].split(".")[0] if conn else curve
                    offenders.append(f"{obj_name} (frame {t:.3f})")
                    break

        # Remove duplicates
        offenders = sorted(list(set(offenders)))

        if offenders:
            log_messages.append("Floating point keys found on:")
            for offender in offenders:
                # offender format: "objName (frame N.NNN)" — link the object part
                name = offender.split(" (frame")[0]
                link = self._obj_link(name, "select")
                detail = offender[len(name) :]
                log_messages.append(f"  - {link}{detail}")
            return False, log_messages

        return True, log_messages

    # ------------------------------------------------------------------
    # Scene-data sidecar — delegates to SceneDataSidecar
    # ------------------------------------------------------------------

    # Backward-compatible aliases so existing call-sites still work.
    _manifest_path_for = staticmethod(SceneDataSidecar.manifest_path_for)
    _diff_report_path_for = staticmethod(SceneDataSidecar.diff_report_path_for)
    _build_clean_path_set = staticmethod(SceneDataSidecar.build_clean_path_set)
    _get_top_level = staticmethod(SceneDataSidecar.get_top_level)
    rename_sidecar = SceneDataSidecar.rename

    def _build_full_hierarchy_set(self) -> set:
        """Build a clean path set including all descendants of ``self.objects``."""
        return SceneDataSidecar.build_full_path_set(self._live_objects())

    def _sidecar_kwargs(self) -> dict:
        """Return sidecar path-derivation kwargs based on versioning state.

        When SceneExporter has set ``_version_format`` (i.e. the ``version``
        UI field is non-empty), sidecar paths route through the base stem so
        every version in a series shares one manifest.
        """
        return {"base_stem": bool(getattr(self, "_version_format", ""))}

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
                str(o).split("|")[-1] == DataNodes.EXPORT
                for o in (self.objects or [])
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

    def write_scene_data_sidecar(self) -> None:
        """Write the sidecar JSON recording what shipped in the export.

        The manifest carries the exported hierarchy paths (the diff-check
        baseline), the diff the check flagged this export (if any — see
        ``hierarchy.last_diff`` in the sidecar module), plus a snapshot of
        the ``data_export`` carrier channels.  The hierarchy section is
        maintained when the check is in play (it ran this export, or a
        manifest already exists); the data section is recorded whenever the
        carrier shipped content.  A metadata-free export with the check off
        leaves no sidecar.
        """
        export_path = getattr(self, "export_path", None)
        if not export_path or not self.objects:
            return

        sk = self._sidecar_kwargs()

        # Symmetric with check_hierarchy_vs_existing_fbx: bring any
        # legacy-named (and, when versioning, per-version) sidecar up to
        # the current name so subsequent writes find it via the "manifest
        # already exists" condition below.
        SceneDataSidecar.migrate_legacy(export_path, **sk)

        manifest_path = SceneDataSidecar.manifest_path_for(export_path, **sk)

        data = self._data_export_snapshot()
        check_ran = getattr(self, "_hierarchy_check_ran", False)
        if not check_ran and not data and not os.path.exists(manifest_path):
            return

        # Consume-and-clear: the stash belongs to THIS export's check; a
        # later export in the same session must not inherit it.  The path
        # tag guards the cancelled-A-then-export-B case, where the check
        # never re-ran to reset the stash.
        last_diff = getattr(self, "_hierarchy_last_diff", None)
        self._hierarchy_last_diff = None
        if last_diff and last_diff.pop("export_path", None) != export_path:
            last_diff = None

        paths = self._build_full_hierarchy_set()
        if (
            SceneDataSidecar.write_manifest(
                export_path, paths, data=data, last_diff=last_diff, **sk
            )
            is None
        ):
            # A silently-stale baseline corrupts the next run's hierarchy
            # diff (false diffs, or a masked revert) — this must be visible
            # at the default WARNING log level, not buried at DEBUG.
            self.logger.warning(
                "Could not write the scene-data sidecar — the hierarchy-diff "
                "baseline for the next export was NOT updated."
            )

    def check_hierarchy_vs_existing_fbx(self) -> tuple:
        """Check export objects against the hierarchy manifest of the previous export.

        Compares namespace-stripped DAG paths of the current export objects
        against the ``.scene_data.json`` sidecar written during the last
        successful export to the same path.  Detects missing or extra nodes
        that would indicate accidental structural changes.  A mismatch is
        stashed for the post-export sidecar write (``hierarchy.last_diff``)
        and its full report goes to a temp artifact linked from the log —
        never into the export folder.
        """
        self._hierarchy_check_ran = True
        self._hierarchy_last_diff = None

        export_path = getattr(self, "export_path", None)
        if not export_path:
            return True, []

        sk = self._sidecar_kwargs()

        # Migrate any legacy-named (and, when versioning, per-version)
        # sidecar to the current name so the diff baseline carries forward.
        SceneDataSidecar.migrate_legacy(export_path, **sk)

        manifest_path = SceneDataSidecar.manifest_path_for(export_path, **sk)

        messages = []
        if not os.path.exists(manifest_path):
            if os.path.exists(manifest_path + ".prev"):
                # Manifest deleted but a v2-era backup survives — compare()
                # falls back to it, and the fresh manifest written after
                # this export sweeps it.
                messages.append(
                    "Hierarchy manifest missing — compared against its "
                    ".prev backup (a fresh manifest will be written after "
                    "this export)."
                )
            elif os.path.exists(export_path):
                return True, [
                    "No hierarchy manifest found for existing FBX. "
                    "A manifest will be created after this export."
                ]
            else:
                return True, []
        elif SceneDataSidecar.read_manifest(export_path, **sk) is None:
            # The manifest file exists but nothing readable backs it —
            # without a .prev shadow copy this must be SEEN, not silently
            # passed: the baseline is lost either way, and the user should
            # know this export went structurally unchecked. A PASSING
            # check's return value never reaches the user — the task
            # runner only surfaces messages from FAILING checks (see
            # check_texture_optimization's advisory notes for the same
            # rule) — so log it directly, same as that method does.
            message = (
                "Hierarchy manifest exists but is unreadable — the "
                "hierarchy check was skipped. A fresh baseline will be "
                "written after this export."
            )
            self.logger.warning(message)
            return True, [message]

        current_paths = self._build_full_hierarchy_set()

        match, missing, extra = SceneDataSidecar.compare(
            export_path, current_paths, **sk
        )

        if match:
            SceneDataSidecar.clean_stale_diff(export_path, **sk)
            return True, messages

        # Detect reparenting patterns for a cleaner summary
        reparented = SceneDataSidecar.detect_reparenting(missing, extra)

        # Stash for the post-export sidecar write: if the user proceeds,
        # the manifest records the diff they accepted.  Tagged with the
        # export path so a cancelled export's diff can never attach to a
        # different asset exported later in the same session.
        self._hierarchy_last_diff = {
            "export_path": export_path,
            "missing": missing,
            "extra": extra,
            "reparented": reparented,
        }

        diff_path = self._write_temp_diff_report(
            export_path, missing, extra, reparented, **sk
        )

        if reparented:
            for root, new_parent, count in reparented:
                messages.append(
                    f"Reparenting detected: '{root}' moved under '{new_parent}' "
                    f"({count} node(s) affected)"
                )
            # Report any remaining missing/extra not explained by reparenting
            explained_missing = set()
            explained_extra = set()
            for root, new_parent, _ in reparented:
                for p in missing:
                    if p.split("|")[0] == root:
                        explained_missing.add(p)
                        explained_extra.add(f"{new_parent}|{p}")
                explained_extra.add(new_parent)
            remaining_missing = [p for p in missing if p not in explained_missing]
            remaining_extra = [p for p in extra if p not in explained_extra]
        else:
            remaining_missing = missing
            remaining_extra = extra

        if remaining_missing:
            top_missing = SceneDataSidecar.get_top_level(remaining_missing)
            messages.append(
                f"{len(remaining_missing)} node(s) in previous export but missing now "
                f"({len(top_missing)} top-level):"
            )
            for p in top_missing[:20]:
                messages.append(f"  − {p}")
            if len(top_missing) > 20:
                messages.append(f"  … and {len(top_missing) - 20} more")

        if remaining_extra:
            top_extra = SceneDataSidecar.get_top_level(remaining_extra)
            messages.append(
                f"{len(remaining_extra)} new node(s) not in previous export "
                f"({len(top_extra)} top-level):"
            )
            for p in top_extra[:20]:
                messages.append(f"  + {p}")
            if len(top_extra) > 20:
                messages.append(f"  … and {len(top_extra) - 20} more")

        if diff_path:
            link = self.logger.log_link(
                "Open full diff report", "open", filepath=diff_path
            )
            messages.append(link)

        return False, messages


class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin):
    """Contains all task-related UI definitions for the Scene Exporter."""

    # Explicit execution order for export tasks.  Tasks not listed here
    # are appended at the end in alphabetical order.  This prevents the
    # alphabetical-sort default from running tasks in the wrong sequence
    # (e.g. set_bake_animation_range before smart_bake, or
    # delete_unused_materials before reassign_duplicate_materials).
    TASK_ORDER = [
        # Phase 1 — Environment setup
        "set_workspace",
        "set_linear_unit",
        # Phase 2 — Object filtering
        "ignore_groups",
        "exclude_hdr",
        # Phase 2.5 — Name hygiene (before checks/sidecar record the names)
        "conform_shape_names",
        # Phase 3 — Material cleanup (reassign THEN resolve THEN relativize;
        # the texture-processing pair runs LAST: convert then optimize what
        # will actually ship, and their staged absolute paths must never be
        # seen by convert_to_relative_paths, which would copy them into
        # sourceimages).
        "reassign_duplicate_materials",
        "resolve_invalid_texture_paths",
        "convert_to_relative_paths",
        "convert_textures",
        "optimize_textures",
        # Phase 4 — Animation (bake THEN optimize THEN snap/tie THEN set range)
        "smart_bake",
        "optimize_keys",
        "snap_keys_to_frame",
        "tie_all_keyframes",
        "set_bake_animation_range",
        "export_data_node",
        "apply_declared_takes",
    ]

    _frame_rate_options: Dict[str, Any] = {
        (
            f"{k}"
            if v is None
            else (
                f"{v:g} fps"
                if any(c.isdigit() for c in k)
                else f"{k} ({v:g} fps)"
            )
        ): (k if v is not None else None)
        for k, v in ptk.insert_into_dict(ptk.VidUtils.FRAME_RATES, "OFF", None).items()
    }

    _scene_unit_options: Dict[str, Any] = {
        k: v
        for k, v in ptk.insert_into_dict(
            EnvUtils.SCENE_UNIT_VALUES, "OFF", None
        ).items()
    }

    def __init__(self, logger):
        super().__init__(logger)

        self.logger = logger
        self._objects = None
        self._invalidate_material_caches()

    def _execute_tasks_and_checks(self, tasks_only, checks_only):
        # smart_bake needs its sibling's setting: baked override-layer curves
        # sit behind animBlendNodes that listConnections can't traverse, so
        # the separate optimize_keys task can never reach them — SmartBake
        # must optimize its own output, gated by the same UI toggle ("Also
        # controls key optimization inside Smart Bake").  The generic
        # TaskFactory knows nothing about either task, so the flag is set
        # here, in the consumer that reads it (same idiom as blendertk).
        self._optimize_keys_enabled = bool(tasks_only.get("optimize_keys", False))
        # convert_textures (write-back mode) runs after convert_to_relative_paths
        # and relativizes its own rewired paths only if that task is on.
        self._relative_paths_enabled = bool(
            tasks_only.get("convert_to_relative_paths", False)
        )
        return super()._execute_tasks_and_checks(tasks_only, checks_only)

    @property
    def objects(self):
        return self._objects

    @objects.setter
    def objects(self, value):
        """Invalidate the materials and keyframe caches whenever objects change."""
        self._objects = value
        self._invalidate_material_caches()
        # Each export run re-seeds the object set before tasks execute, so this
        # doubles as the per-run reset of the producer-refresh marker
        # (export_data_node sets it; apply_declared_takes reads it) and of the
        # hierarchy-check marker — without the latter, one hierarchy-checked
        # export makes every later export in the session write sidecar
        # baselines the user didn't ask for.
        self._data_node_refreshed = False
        self._hierarchy_check_ran = False
        self._invalidate_keyframe_cache()

    # Texture Output — do the texture-processing tasks (convert_textures,
    # optimize_textures) modify the scene's textures, or stage copies for the
    # export and restore the scene afterwards? Data is the write-back flag
    # perform_export pops (never a dispatched task).
    _texture_output_options: Dict[str, Any] = {
        "Export Copies (Scene Untouched)": False,
        "Scene Files (In Place)": True,
    }

    #: Longest-edge ceilings offered by Max Texture Size — the Map Converter's
    #: clamp choices, minus 256 (a scene export never wants that small).
    _TEXTURE_MAX_SIZES = (512, 1024, 2048, 4096, 8192)

    # Max Texture Size — the optimization pass's size dial. Data is the clamp
    # perform_export pops (never a dispatched task): 0 = OFF (falsy so the
    # task filter drops it), a pixel ceiling, or TEXTURE_MAX_SIZE_TEMPLATE
    # (enforce the selected template's budget). OFF is index 0 (default) and
    # the sentinel is LAST — combos persist by index.
    _texture_max_size_options: Dict[str, Any] = {
        "OFF": 0,
        **{f"{s}": s for s in _TEXTURE_MAX_SIZES},
        "Template Budget": _TaskDataMixin.TEXTURE_MAX_SIZE_TEMPLATE,
    }

    _export_mode_options: Dict[str, Any] = {
        "All Scene Objects": "all",
        "All Visible Objects": "visible",
        "Selected Objects Only": "selected",
    }

    @property
    def task_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the task definitions for the UI.

        Tooltips are built with uitk's rich-text DSL (imported lazily so this
        engine module still imports Qt-free in a headless session).  Keep the
        ``TooltipFormat.fmt`` call form and literal arguments — that is what
        ``m3trik/scripts/check_tooltips.py`` statically renders and validates.
        """
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        return {
            "export_visible_objects": {
                "widget_type": "ComboBox",
                "panel": "settings",
                "set_row_label": "Scope",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Scope",
                    body="Which objects the export set is built from, resolved "
                    "fresh each time you export.",
                    bullets=[
                        "<b>All Scene Objects</b> — every transform and geometry "
                        "node, visible or not.",
                        "<b>All Visible Objects</b> — visible geometry only, "
                        "honoring inherited parent visibility. Templated objects "
                        "are excluded; objects with animated visibility are kept, "
                        "since their animation is baked and ships.",
                        "<b>Selected Objects Only</b> — exactly the current "
                        "selection.",
                    ],
                    notes=[
                        "The data_export metadata carrier is a hidden helper node, "
                        "not geometry, so <b>Export Scene Data Node</b> is what "
                        "puts it in the set."
                    ],
                ),
                "add": self._export_mode_options,
                "value_method": "currentData",
            },
            "export_data_node": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Export Scene Data Node",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Scene Data Node",
                    body="Ship the shared <b>data_export</b> carrier node inside "
                    "the FBX, carrying whatever metadata the scene's subsystems "
                    "have stamped on it.",
                    bullets=[
                        "Shots writes <b>shot_metadata</b> and <b>fbx_takes</b>.",
                        "Audio writes <b>audio_manifest</b>.",
                        "Any other producer's channel rides along the same way.",
                    ],
                    notes=[
                        "The carrier is hidden, so the Visible and Selected scopes "
                        "would otherwise drop it.",
                        "Refreshed from the live scene at export; no-op when there "
                        "is no metadata to carry.",
                        "A readable copy is also written beside the export as "
                        ".scene_data.json.",
                        "This ships the metadata only — it never changes the "
                        "animation. Splitting the timeline into clips is "
                        "<b>Export Shots as Animation Takes</b>.",
                    ],
                ),
                "setChecked": True,
            },
            "set_linear_unit": {
                "widget_type": "ComboBox",
                "panel": "settings",
                "set_row_label": "Units",
                "setToolTip": TooltipFormat.fmt(
                    title="Linear Unit",
                    body="Working linear unit Maya is switched to for the FBX "
                    "write, then switched back.",
                    notes=[
                        "The FBX plug-in stamps the file's unit from the working "
                        "unit at write time, so this is the scale the receiving "
                        "engine reads.",
                        "<b>OFF</b> writes in the scene's current unit.",
                    ],
                ),
                "add": self._scene_unit_options,
            },
            "set_workspace": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Auto Set Workspace",
                "setToolTip": TooltipFormat.fmt(
                    title="Auto Set Workspace",
                    body="Derive the workspace from the scene path and point the "
                    "process working directory at it for the FBX write.",
                    notes=[
                        "The FBX plug-in resolves relative texture paths against "
                        "the working directory, not the workspace — without this, "
                        "embedding fails with 'The following texture(s) will not "
                        "be embedded'.",
                        "Both changes are restored after the export.",
                    ],
                ),
                "setChecked": True,
            },
            "exclude_hdr": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Exclude HDR Environment",
                "setToolTip": TooltipFormat.fmt(
                    title="Exclude HDR Environment",
                    body="Keep the Arnold HDR environment light (aiSkyDomeLight) "
                    "out of the export set.",
                    notes=[
                        "The skydome is image-based scene lighting, not "
                        "deliverable geometry — under <b>All Scene Objects</b> it "
                        "would otherwise ride into the FBX.",
                        "No-op when the scene has no skydome.",
                    ],
                ),
                "setChecked": True,
            },
            "reassign_duplicate_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Reassign Duplicate Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Reassign Duplicate Materials",
                    body="Collapse materials that are genuinely identical onto a "
                    "single keeper and reassign every object using them.",
                    bullets=[
                        "Candidates are grouped by node type and texture set, "
                        "matched on file name — so the same map loaded from two "
                        "folders still groups.",
                        "Each candidate is then verified against its keeper: "
                        "unconnected attribute values, placement and color space "
                        "per texture slot, and texture content (size plus a "
                        "partial hash) whenever the stored paths differ.",
                    ],
                    notes=[
                        "Only verified duplicates are merged — the merge deletes "
                        "what it collapses, so the verification is what makes it "
                        "safe.",
                        "Reports the same materials as <b>Check For Duplicate "
                        "Materials</b>.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "convert_to_relative_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Convert To Relative Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Convert To Relative Paths",
                    body="Rewrite the export materials' texture paths as "
                    "project-relative paths.",
                    notes=[
                        "A relative path only resolves if the file physically "
                        "lives under sourceimages, so external textures are "
                        "copied in first — otherwise relativizing would point at "
                        "a file that isn't there and silently break the material "
                        "on import.",
                        "The copies and the path edits both persist after the "
                        "export; the path edits are undo-anchored, so Maya's undo "
                        "can back them out.",
                    ],
                ),
                "setChecked": True,
            },
            "resolve_invalid_texture_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Resolve Invalid Texture Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Resolve Invalid Texture Paths",
                    body="Rebind broken texture paths by hunting for the missing "
                    "file anywhere under sourceimages, scoped to the materials "
                    "being exported.",
                    notes=[
                        "Rebinding by name is a guess — the original file is gone, "
                        "so nothing can verify content. The hunt is therefore "
                        "gated: the basename must match exactly one file. A unique "
                        "hit is rebound and logged old → new; an ambiguous name is "
                        "reported instead of guessed at.",
                        "&lt;UDIM&gt; / &lt;f&gt; names match by pattern and keep "
                        "their token.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "optimize_textures": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Optimize Textures",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Textures",
                    body="Run the Map Converter's per-map-type optimization "
                    "pass on the textures shipping with this export — mode "
                    "and bit depth corrected per map type; the export reads "
                    "the optimized copies.",
                    bullets=[
                        "With a <b>Textures</b> template selected, the "
                        "template's per-map-type output spec also drives each "
                        "map's container and bit depth (delivery containers "
                        "like KTX2 stay with the GLB Textures pass).",
                        "With Textures at <b>As Authored</b>, a generic "
                        "per-map-type pass — each map keeps its container.",
                    ],
                    notes=[
                        "Never resizes on its own: a template's size budget "
                        "is advisory and only reported. <b>Max Texture "
                        "Size</b> is the pass's size dial.",
                        "Where the optimized maps go — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                        "Already-optimal maps are left untouched; the paired "
                        "check names anything the pass could not optimize.",
                    ],
                ),
            },
            "texture_max_size": {
                "widget_type": "ComboBox",
                "group": "Materials",
                "set_row_label": "Max Texture Size",
                "setToolTip": TooltipFormat.fmt(
                    title="Max Texture Size",
                    body="Longest-edge ceiling for the textures shipping with "
                    "this export — larger maps are downsampled by "
                    "<b>Optimize Textures</b>; smaller ones are never grown.",
                    bullets=[
                        "<b>OFF</b> — never resample (a template's size "
                        "budget is only reported).",
                        "<b>512 … 8192</b> — hard ceiling in pixels, "
                        "whatever the template says.",
                        "<b>Template Budget</b> — enforce the selected "
                        "<b>Textures</b> template's own size budget "
                        "(e.g. glTF/URP 2048, HDRP/Unreal 4096; the "
                        "power-of-two rule is not applied). No-op with "
                        "Textures at <b>As Authored</b> or an unbudgeted "
                        "template.",
                    ],
                    notes=[
                        "Inert unless <b>Optimize Textures</b> is on — the "
                        "clamp is a rule of that pass.",
                        "Follows <b>Texture Output</b>: export copies by "
                        "default (scene untouched); with Scene Files (In "
                        "Place) the resized map replaces the scene's file "
                        "and the original is archived in "
                        "<b>original_textures</b>.",
                    ],
                ),
                "add": self._texture_max_size_options,
            },
            "texture_write_back": {
                "widget_type": "ComboBox",
                "panel": "settings",
                "set_row_label": "Texture Output",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Output",
                    body="Whether the texture-processing tasks — the "
                    "<b>Textures</b> template conversion and <b>Optimize "
                    "Textures</b> (including its <b>Max Texture Size</b> "
                    "clamp) — modify the scene's textures, or leave the "
                    "scene as it was.",
                    bullets=[
                        "<b>Export Copies (Scene Untouched)</b> — "
                        "non-destructive: processed maps are staged for the "
                        "write (a temp folder when the deliverable embeds "
                        "its media, else <b>textures/</b> beside it), the "
                        "materials read them for the export, and the scene's "
                        "networks and paths are restored afterwards.",
                        "<b>Scene Files (In Place)</b> — permanent: the "
                        "conversion migrates the materials and the "
                        "optimization overwrites the scene's own texture "
                        "files (originals archived beside each texture in an "
                        "<b>original_textures</b> folder). Not reverted after "
                        "export.",
                    ],
                    notes=[
                        "Inert unless a template is selected or Optimize "
                        "Textures is on.",
                    ],
                ),
                "add": self._texture_output_options,
            },
            "smart_bake": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Smart Bake",
                "setToolTip": TooltipFormat.fmt(
                    title="Smart Bake",
                    body="Bake the rig's indirect animation — constraints, driven "
                    "keys, expressions, IK, motion paths, blend shapes — down to "
                    "plain keyframes, which is all an FBX can carry.",
                    notes=[
                        "The time range is detected from the drivers themselves.",
                        "Bakes onto an override layer; the pre-bake scene state is "
                        "restored after the export.",
                        "<b>Optimize Keys</b> also governs the optimization pass "
                        "inside this bake.",
                    ],
                ),
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Optimize Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Keys",
                    body="Delete static curves and redundant flat keys from the "
                    "exported objects.",
                    notes=[
                        "Stepped tangents are preserved.",
                        "Also controls key optimization inside <b>Smart Bake</b> — "
                        "that pass reaches the baked override-layer curves this "
                        "one cannot.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Tie All Keyframes",
                "setToolTip": TooltipFormat.fmt(
                    title="Tie All Keyframes",
                    body="Insert bookend keys at the first and last keyframe of "
                    "the whole export set, on every channel that is already "
                    "animated, so no animated channel stops short of the range.",
                    notes=[
                        "Fixes what <b>Check For Untied Keyframes</b> reports.",
                        "Tangents on the neighboring keys are frozen first, so the "
                        "inserted keys do not reshape the curve.",
                        "Permanent scene change, and the insert bypasses Maya's "
                        "undo queue — revert with AnimUtils.untie_keyframes rather "
                        "than Ctrl+Z.",
                    ],
                ),
                "setChecked": True,
            },
            "snap_keys_to_frame": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Snap Keys To Frame",
                "setToolTip": TooltipFormat.fmt(
                    title="Snap Keys To Frame",
                    body="Round every key on the exported objects to the nearest "
                    "whole frame.",
                    notes=[
                        "Fixes what <b>Check For Floating Point Keys</b> reports — "
                        "fractional key times left behind by retiming, scaling, or "
                        "an import at a different rate.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": False,
            },
            "set_bake_animation_range": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Auto Set Bake Animation Range",
                "setToolTip": TooltipFormat.fmt(
                    title="Auto Set Bake Animation Range",
                    body="Set the FBX bake range to the first and last keyframe of "
                    "the exported objects (start floored, end ceiled), overriding "
                    "the range stored in the FBX preset.",
                    notes=[
                        "Applies only when Bake Animation is enabled in the FBX "
                        "export settings; otherwise it is skipped.",
                        "Runs last of the animation tasks, so it measures the "
                        "final keyframe extent — but <b>Export Shots as Animation "
                        "Takes</b> runs after it and widens the range again.",
                    ],
                ),
                "setChecked": True,
            },
            "apply_declared_takes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Export Shots as Animation Takes",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Shots as Animation Takes",
                    body="Split the exported animation into one named FBX take per "
                    "shot, so the file arrives in Unity as separate "
                    "AnimationClips instead of a single continuous clip.",
                    notes=[
                        "Requires shots defined in the Shots panel; no-op when the "
                        "scene declares none.",
                        "This is <b>not</b> what ships the shot metadata — "
                        "<b>Export Scene Data Node</b> already does that, and the "
                        "two share one refresh. Leave this off when you want the "
                        "same metadata with an unsplit timeline.",
                        "Turning it on forces Bake Animation on and widens the "
                        "bake range to the union of all shots, overriding "
                        "<b>Auto Set Bake Animation Range</b>. Both are restored "
                        "after the write.",
                    ],
                ),
                "setChecked": False,
            },
            "conform_shape_names": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy",
                "setText": "Fix Mangled Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Fix Mangled Names",
                    body="Repair scratch and mangled names across the export set — "
                    "transforms and shapes alike — then conform each shape to "
                    "Maya's '&lt;transform&gt;Shape' convention.",
                    bullets=[
                        "Accumulated '__uninst_tmp' scratch tokens",
                        "'__RZTMP' Rizom round-trip suffixes",
                        "'FBXASC###' import escapes",
                        "Runs of three or more underscores",
                    ],
                    notes=[
                        "Clears the <b>Check For Mangled Names</b> failure.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": False,
            },
            "ignore_groups": {
                "widget_type": "QLineEdit",
                "panel": "settings",
                "set_row_label": "Ignore",
                "setPlaceholderText": "Group names to ignore (comma-separated)",
                "setToolTip": TooltipFormat.fmt(
                    title="Ignore Groups",
                    body="Comma-separated names of top-level groups to drop from "
                    "the export set (case-insensitive).",
                    notes=[
                        "Example: temp, proxy",
                        "Leave empty to skip.",
                    ],
                ),
                "setText": "temp",
                "value_method": "text",
            },
            # NOTE: `glb_optimize_textures` and `version` are UI-only fields —
            # consumed by SceneExporter (pop'd before run_tasks), never
            # executed by the task pipeline.
            # The output format (FBX / GLB / FBX+GLB) is the same kind of UI-only
            # field, but it lives in its own `cmb004` Format combo rather than the
            # task list, so it isn't defined here.
            "glb_optimize_textures": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Optimize GLB Textures",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize GLB Textures",
                    body="Cap the resolution of the textures embedded in the "
                    "GLB deliverable — the same web-delivery pass the WebXR "
                    "preview runs, which is why a preview of a scene is a "
                    "fraction of that scene's exported GLB.",
                    bullets=[
                        "<b>OFF</b> (default) — the GLB ships the authored "
                        "resolution, whatever it is.",
                        "<b>ON</b> — every embedded image is downsized to the "
                        "optimizer's delivery ceiling and re-encoded into the "
                        "container <b>GLB Textures</b> selects.",
                    ],
                    notes=[
                        "OFF by default because downsizing is unrecoverable "
                        "from the deliverable — an archival or engine-import "
                        "export wants the authored resolution.",
                        "Lightmaps are exempt from the resize and re-encode "
                        "losslessly; any image the re-encode cannot beat keeps "
                        "its original bytes.",
                        "Independent of <b>Max Texture Size</b>, which is the "
                        "scene-texture pass's dial — this one only ever "
                        "touches the finished GLB.",
                        "Inert for FBX-only output.",
                    ],
                ),
                "setChecked": False,
            },
            "version": {
                "widget_type": "QLineEdit",
                "panel": "settings",
                "set_row_label": "Version",
                "setPlaceholderText": "{stem}_v{n:03d}  — empty disables",
                "setToolTip": TooltipFormat.fmt(
                    title="Version",
                    body="Filename pattern for the exported file. Leave empty to "
                    "export without versioning.",
                    rows=[
                        ("{stem}", "output basename"),
                        ("{n:NNd}", "version number, zero-padded to NN digits"),
                        ("{date}", "YYYY-MM-DD"),
                        (
                            "{user}",
                            "OS username — embeds dev identity, so beware on "
                            "shared exports",
                        ),
                        ("{scene}", "Maya scene basename (requires a saved scene)"),
                    ],
                    notes=[
                        "The extension is added automatically — do not include "
                        "{ext}.",
                        "Use a '_v&lt;N&gt;' suffix (e.g. '_v{n:03d}') so the "
                        "hierarchy diff baseline can carry across versions.",
                    ],
                ),
                "setText": "",  # off by default — opt-in
                "value_method": "text",
            },
        }

    @property
    def check_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the check definitions for the UI.

        A failed check aborts the export, so each tooltip below leads with what
        makes it fail.  Tooltip authoring rules: see :attr:`task_definitions`.
        """
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        return {
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "group": "General",
                "setText": "Check For Referenced Objects",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Referenced Objects",
                    body="Fails the export when the scene contains file "
                    "references.",
                    notes=[
                        "Scans the whole scene, not just the export set.",
                        "Import the reference (or remove it) to pass.",
                    ],
                ),
                "setChecked": True,
            },
            "check_geometry_lod_suffix": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Geometry LOD Suffix (_LODx)",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Geometry LOD Suffix (_LODx)",
                    body="Lists geometry named with an LOD suffix — '_LOD' alone "
                    "or followed by digits ('_LOD1', '_LOD02'), case-insensitive.",
                    notes=[
                        "Informational only: it reports what it finds and never "
                        "fails the export."
                    ],
                ),
                "setChecked": True,
            },
            "check_duplicate_locator_names": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check For Duplicate Locator Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Locator Names",
                    body="Fails the export when two locators in the export set "
                    "share a name.",
                    notes=[
                        "Compares short names, so locators under different parents "
                        "still collide — which is what a consumer matching them by "
                        "name downstream will see."
                    ],
                ),
                "setChecked": True,
            },
            "check_mangled_names": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check For Mangled Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Mangled Names",
                    body="Fails the export when any node in the set — shapes "
                    "included — carries a scratch or mangled name.",
                    bullets=[
                        "Accumulated '__uninst_tmp' scratch tokens",
                        "'__RZTMP' Rizom round-trip suffixes",
                        "'FBXASC###' import escapes",
                        "Runs of three or more underscores",
                    ],
                    notes=["Repair with the <b>Fix Mangled Names</b> task."],
                ),
                "setChecked": True,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Root Default Transforms",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Root Default Transforms",
                    body="Fails the export when a root group node is not at "
                    "identity — translate and rotate (0, 0, 0), scale (1, 1, 1).",
                    notes=[
                        "A root that was frozen reads identity but still carries "
                        "the consumed transform in its history, which an un-freeze "
                        "downstream would reinstate. Those are reported for "
                        "information and do not fail the check — as the scene "
                        "stands it really is at identity, which is what the "
                        "exporter needs."
                    ],
                ),
                "setChecked": True,
            },
            "check_hierarchy_vs_existing_fbx": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Hierarchy vs Existing FBX",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Hierarchy vs Existing FBX",
                    body="Fails the export when the hierarchy differs from the "
                    "previous export — nodes that went missing or appeared, the "
                    "signature of an accidental change.",
                    notes=[
                        "Compares against a lightweight sidecar manifest written "
                        "beside the last export, so no FBX reimport is needed.",
                        "Version the filename (see <b>Version</b>) with a "
                        "'_v&lt;N&gt;' suffix so the baseline carries across "
                        "versions.",
                    ],
                ),
                "setChecked": False,
            },
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
                "setText": "Check For Hidden Geometry",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Hidden Geometry",
                    body="Fails the export when geometry in the set is hidden — "
                    "by its own visibility flag or by a display layer.",
                    notes=[
                        "The FBX exporter writes hidden geometry anyway, so this "
                        "check is the only warning you get before it ships.",
                        "Objects with animated visibility are deliberately not "
                        "flagged: the Visible scope includes them on purpose and "
                        "their animation ships with them.",
                    ],
                ),
                "setChecked": True,
            },
            "check_overlapping_duplicate_mesh": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
                "setText": "Check For Overlapping Duplicates",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Overlapping Duplicates",
                    body="Fails the export when two meshes occupy the same space — "
                    "typically a duplicate left sitting on top of the original.",
                    notes=[
                        "Matches on world-space bounding box, topology counts, and "
                        "sampled world-space vertex positions, so same-size "
                        "different-shape meshes are not confused for each other."
                    ],
                ),
                "setChecked": True,
            },
            "check_objects_below_floor": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
                "setText": "Check For Objects Below Floor",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Objects Below Floor",
                    body="Fails the export when geometry dips below Y=0.",
                    notes=[
                        "A 0.5 unit tolerance means shallow penetrations (a tire "
                        "settling into the ground) do not fail on their own.",
                        "Callers can override it with a 'tolerance' keyword "
                        "argument.",
                    ],
                ),
                "setChecked": True,
            },
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
                "setText": "Check For Duplicate Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Materials",
                    body="Fails the export when two of the export materials are "
                    "verified duplicates of each other.",
                    notes=[
                        "Same texture set, placement, color space and texture "
                        "content — near-misses like same-name-different-content "
                        "are not reported.",
                        "The <b>Reassign Duplicate Materials</b> task merges "
                        "exactly what this reports.",
                    ],
                ),
                "setChecked": True,
            },
            "check_path_length": {
                # A character budget is a bounded number, so it gets a spin box
                # (same rationale as the texture size limit): the default is
                # THIS machine's OS limit, and 0 reads back as "OFF".
                "widget_type": "SpinBox",
                "group": "Materials & Paths",
                "set_row_label": "Max Path Length",
                "set_limits": [0, 32767, 1, 0],
                "setValue": ptk.FileUtils.path_length_limit(),
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Path Length",
                    body="Fails the export when the destination, or any texture "
                    "feeding the export materials, resolves to a path longer than "
                    "this many characters.",
                    notes=[
                        "Over-long paths fail late and opaquely — a write that "
                        "reports success but produced nothing, or a texture the "
                        "FBX plug-in silently cannot embed.",
                        "A path that fits on this machine can still break on one "
                        "without long paths enabled (260 characters).",
                        "Sidecars written beside the export are longer than the "
                        "export path itself, so leave headroom.",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_valid_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
                "setText": "Check For Valid Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Valid Paths",
                    body="Fails the export when a texture feeding the export "
                    "materials — or a scene reference — does not resolve on disk.",
                    notes=[
                        "Resolves each path twice: the way Maya resolves it, and "
                        "the way the FBX plug-in will locate it at write time.",
                        "Catches what would otherwise surface after the export as "
                        "'The following texture(s) will not be embedded'.",
                        "Textures on objects that will not ship (the HDR skydome, "
                        "file nodes orphaned by the duplicate-material cleanup) "
                        "are not reported.",
                    ],
                ),
                "setChecked": True,
            },
            "check_texture_file_size": {
                # A megabyte budget is a bounded number, so it gets a spin box:
                # steppable, no free text to typo, and 0 reads back as "OFF"
                # (the check treats a falsy limit as disabled).
                "widget_type": "SpinBox",
                "group": "Materials & Paths",
                "set_row_label": "Max Size (MB)",
                "set_limits": [0, 4096, 1, 0],
                "setValue": 16,
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Texture Size (MB)",
                    body="Fails the export when any texture feeding the export "
                    "materials is larger than this on disk.",
                    notes=[
                        "Catches un-downsized authoring maps — an 8K master left "
                        "wired up — that would bloat the shipped asset.",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_framerate": {
                "widget_type": "ComboBox",
                "group": "Animation",
                "set_row_label": "Framerate",
                "setToolTip": TooltipFormat.fmt(
                    title="Scene Framerate",
                    body="Fails the export when the scene's time unit is not the "
                    "framerate selected here.",
                    notes=[
                        "Skipped when the scene has no keyframes.",
                        "<b>OFF</b> disables the check.",
                    ],
                ),
                "add": self._frame_rate_options,
            },
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Check For Untied Keyframes",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Untied Keyframes",
                    body="Fails the export when an object has an animated channel "
                    "whose keys stop short of that object's own keyed range.",
                    notes=[
                        "The <b>Tie All Keyframes</b> task inserts the missing "
                        "bookend keys.",
                        "Set-driven-key curves are ignored — their key 'times' are "
                        "driver values, not frames.",
                    ],
                ),
                "setChecked": True,
            },
            "check_floating_point_keys": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Check For Floating Point Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Floating Point Keys",
                    body="Fails the export when a key sits on a fractional frame.",
                    notes=[
                        "The <b>Snap Keys To Frame</b> task rounds them to whole "
                        "frames."
                    ],
                ),
                "setChecked": True,
            },
        }

    @property
    def definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return all definitions combined for backward compatibility."""
        return {**self.task_definitions, **self.check_definitions}


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
