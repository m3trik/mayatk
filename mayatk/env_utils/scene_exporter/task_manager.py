# !/usr/bin/python
# coding=utf-8
import contextlib
import os
import re
import math
import logging
from typing import Optional, Dict, Any, List, Tuple, Union

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

    def _scene_safe_output_type(self, path: str, template: str) -> Optional[str]:
        """The container the optimization pass may write for *path* under
        *template* — clamped to what a scene file node can read.

        A template's per-map-type :class:`~pythontk.OutputSpec` can name a
        delivery-only container (:attr:`~pythontk.ImgUtils.DELIVERY_ONLY_FORMATS`
        — KTX2, WebP) that the DCC viewport cannot display and no FBX importer
        reads — those stay with the GLB texture pass
        (:meth:`_glb_texture_params`). Returns the source's own extension to pin
        the container in that case, None otherwise (an explicit ``output_type``
        outranks the profile's, so None lets the profile drive).
        """
        map_type = ptk.MapFactory.resolve_map_type(path, key=True)
        spec_ext = (
            (ptk.OutputTemplates.resolve(map_type, template).ext or "")
            .lower()
            .lstrip(".")
        )
        if spec_ext in ptk.ImgUtils.DELIVERY_ONLY_FORMATS:
            return self._source_container(path)
        return None

    @staticmethod
    def _source_container(path: str) -> Optional[str]:
        """*path*'s own container — what "keep what the scene can already read"
        resolves to, for both the template and the Texture File Type dial."""
        return os.path.splitext(path)[1].lower().lstrip(".") or None

    def _resolved_output_type(
        self, path: str, template: Optional[str]
    ) -> Optional[str]:
        """The container the optimization pass writes for *path*.

        Binds the per-run ``_texture_file_type`` mode (the Texture File Type
        combo, stamped by ``perform_export`` — never a dispatched task) to the
        shared rule: :meth:`pythontk.OutputTemplates.resolve_selection` owns
        "a concrete container outranks the profile's template", so naming a
        file type here writes every map as that, while the template still
        supplies the budget and bit depth. Falsy (Original) defers to
        :meth:`_scene_safe_output_type` — the template's own per-map-type
        container, clamped to what a scene file node can read.

        Distinct from :meth:`_glb_texture_params`, which reads the same dial for
        the converted ``.glb``'s embedded copies; this is the container the
        textures shipping BESIDE (or inside) the FBX are written in.
        """
        _, chosen = ptk.OutputTemplates.resolve_selection(
            template, getattr(self, "_texture_file_type", None)
        )
        if chosen:
            # A delivery-only container (KTX2, WebP) gets the same clamp a
            # template's would: no scene file node or FBX importer reads it, so
            # the scene's own maps keep their container and that choice lands on
            # the GLB carrier instead (:meth:`_glb_texture_params`). WebP joined
            # this clamp on measurement (2026-08-25): a Maya `file` node reports
            # a .webp as 0x0, and a shipped hand-off exported with Texture File
            # Type = WEBP embedded webp maps in its FBX -- textures that bind in
            # no consumer, with nothing in the log to say so. Said once per run.
            if chosen in ptk.ImgUtils.DELIVERY_ONLY_FORMATS:
                if not getattr(self, "_delivery_only_clamp_said", False):
                    self._delivery_only_clamp_said = True
                    self.logger.info(
                        f"{chosen.upper()} is a delivery-only container: no DCC "
                        f"texture node or FBX importer reads it, so the scene's "
                        f"own maps keep their container"
                        + (
                            " (the GLB still carries it)."
                            if chosen in self.GLB_CARRIER_FORMATS
                            else "."
                        )
                    )
                return self._source_container(path)
            return chosen
        return self._scene_safe_output_type(path, template) if template else None

    #: ``_texture_max_size`` sentinel: clamp to the active template's own
    #: :class:`~pythontk.DeliveryBudget` (``enforce_budget``) rather than to a
    #: pixel ceiling. Aliases the shared resolver's own sentinel so the combo
    #: row, the exporter and the optimizer cannot drift apart on its value.
    TEXTURE_MAX_SIZE_TEMPLATE = ptk.MapOptimizer.SIZE_CLAMP_TEMPLATE

    def _texture_size_clamp(self, template: Optional[str]) -> Dict[str, Any]:
        """The resize rule the optimization pass applies under *template*.

        Binds the per-run ``_texture_max_size`` mode (the Optimize Textures
        combo's size half, stamped by ``perform_export`` — never a dispatched
        task) to the shared resolver, which owns the rule: see
        :meth:`pythontk.MapOptimizer.resolve_size_clamp` for the modes and
        why the budget's POT flag is deliberately not adopted.

        Returns:
            dict of keyword arguments for ``MapOptimizer.assess`` /
            ``optimize_map``. Empty when no clamp applies.
        """
        return ptk.MapOptimizer.resolve_size_clamp(
            getattr(self, "_texture_max_size", None), template, logger=self.logger
        )

    #: Containers a GLB can embed: glTF-core (``MeshConvert.IMAGE_MIME_TYPES``,
    #: the SSoT for what needs no extension) plus the two ``optimize_glb_textures``
    #: declares an extension for — WebP (``EXT_texture_webp``) and KTX2
    #: (``KHR_texture_basisu``). Everything else the Texture File Type dial offers
    #: is a scene-side container only, so the GLB falls back to PNG.
    GLB_CARRIER_FORMATS = frozenset(
        [e.lstrip(".") for e in ptk.MeshConvert.IMAGE_MIME_TYPES] + ["webp", "ktx2"]
    )

    def _glb_texture_params(self) -> Dict[str, Any]:
        """``optimize_glb_textures`` kwargs for this run's GLB deliverable.

        The GLB's half of the panel's two GENERAL texture dials — it has no
        dials of its own — resolved against
        :meth:`pythontk.MeshConvert.web_delivery_texture_params`, the ONE
        definition of what a web deliverable's textures are. Each dial
        *overrides* that policy; neither has to restate it:

        * **Container** — Texture File Type (``_texture_file_type``), when it
          names something :attr:`GLB_CARRIER_FORMATS` covers. Anything else
          (and "Original") takes the policy's container, because a GLB from
          this panel IS the web deliverable: the FBX and USD formats beside it
          are the interchange ones.
        * **Resolution** — the Optimize Textures combo (its "Optimize + Max …"
          half), through the same :meth:`_texture_size_clamp` every scene map
          goes through, so the export has ONE size policy rather than a second
          one hiding in the GLB. The budget sentinel resolves to the template's
          own ceiling here (the GLB pass takes pixels, not a rule). A dial that
          names no ceiling takes the policy's.

        **Behaviour change (2026-08-29).** This used to return ``None`` for
        untouched dials, meaning no pass at all — a byte-stable conversion.
        Measured on a production assembly through every leg in one session,
        that default was not a neutral choice but a broken deliverable: the
        WebXR preview published 8.71 MB of WebP and this path published
        280.13 MB of full-resolution PNG from the same scene, with nothing in
        either log saying they differed. Setting the dials to WebP still gave
        22.06 MB, because the ceiling resolved from an absent template budget
        to "never resample" — so the old defaults could not reach the preview's
        output at all. A byte-stable GLB remains available to programmatic
        callers through ``MeshConvert.fbx_to_glb`` alone, which runs no pass.
        """
        file_type = (
            (getattr(self, "_texture_file_type", None) or "").lower().lstrip(".")
        )
        optimize = bool(getattr(self, "_optimize_textures_enabled", False))

        carrier = file_type if file_type in self.GLB_CARRIER_FORMATS else ""
        if file_type and not carrier:
            self.logger.info(
                f"GLB textures: {file_type.upper()} is not a container glTF can "
                f"embed — the GLB carries "
                f"{ptk.MeshConvert.WEB_DELIVERY_FORMAT} (the scene's own maps "
                f"still use {file_type.upper()})."
            )

        # ``or None`` on both halves: an unset dial is "unspecified", which the
        # shared resolver answers with the policy, NOT a falsy value it would
        # read as a decision (0 there means "keep every pixel" — exactly the
        # 280 MB outcome this method exists to stop shipping by default).
        return ptk.MeshConvert.web_delivery_texture_params(
            image_format=self._glb_format_id(carrier) if carrier else None,
            max_size=(self._glb_max_size() if optimize else 0) or None,
        )

    @staticmethod
    def _glb_format_id(ext: str) -> str:
        """*ext* as the format id ``optimize_glb_textures`` needs.

        It passes ``image_format`` straight to Pillow AND builds the glTF mime
        as ``image/<lowercased>``, so the container's file extension is not
        always the right token: ``jpg`` is a legal choice on this dial (and a
        legal filename suffix), but Pillow only knows ``JPEG`` and glTF only
        accepts ``image/jpeg`` — ``JPG`` would raise ``KeyError`` mid-encode
        and, if it hadn't, write an invalid glTF. Canonicalized through
        ``MeshConvert.IMAGE_MIME_TYPES`` rather than a private alias table, so
        the mapping stays the one glTF itself is keyed on.
        """
        mime = ptk.MeshConvert.IMAGE_MIME_TYPES.get(f".{ext}", "")
        return (mime.split("/")[-1] or ext).upper()

    def _glb_max_size(self) -> int:
        """The size-ceiling half of Optimize Textures, as pixels for the GLB pass.

        ``optimize_glb_textures`` takes pixels, while :meth:`_texture_size_clamp`
        speaks the optimizer's richer rule (a ceiling OR the template's budget),
        so the sentinel is resolved to the template's own ``max_size`` here.
        ``0`` means "never resample", which is also what an unbudgeted template
        under the sentinel yields — the same no-op the scene pass reports.
        """
        template = getattr(self, "_texture_template", None)
        clamp = self._texture_size_clamp(template)
        if clamp.get("enforce_budget"):
            return int(ptk.OutputTemplates.budget(template).max_size or 0)
        return int(clamp.get("max_size") or 0)

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
        the size ceiling when one is set
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
        output_type = self._resolved_output_type(path, template)
        clamp = self._texture_size_clamp(template)
        # Memoised per run on the file's identity (path + mtime + size) and
        # the pass it is judged against: ``assess`` decodes the image every
        # time, and one export asks the same question of the same file up to
        # three times (the task's plan, its post-write re-verification, and
        # the check). A rewritten file changes its stat and misses.
        try:
            st = os.stat(path)
            key = (
                os.path.normcase(os.path.normpath(path)),
                st.st_mtime_ns,
                st.st_size,
                template,
                output_type,
                tuple(sorted(clamp.items())),
            )
        except OSError:
            key = None
        cache = self._assess_cache
        if key in cache:
            return cache[key]
        result = ptk.MapOptimizer.assess(
            path,
            output_profile=template,
            output_type=output_type,
            optimize_bit_depth=True,
            **clamp,
        )
        if result.get("error"):
            return None
        reasons = list(result["reasons"])
        src_ext = os.path.splitext(path)[1].lower().lstrip(".")
        new_ext = (result["predicted"].get("ext") or src_ext).lower().lstrip(".")
        if new_ext != src_ext:
            reasons.append(f"Container: {src_ext} -> {new_ext} (template)")
        predicted_path = result["predicted"].get("path") or path
        verdict = {
            "needed": bool(reasons),
            "reasons": reasons,
            "warnings": list(result["warnings"]),
            "output_type": output_type,
            "predicted_name": os.path.basename(predicted_path),
        }
        if key is not None:
            cache[key] = verdict
        return verdict

    @staticmethod
    def _is_tiled_path(path: str) -> bool:
        """Does *path* name a tile/frame SET? The exporter's name for the
        shared classifier.

        It listed ``<udim>|<f>|<uvtile>`` privately, so ``<u>_<v>`` and
        ``<frame>`` — which every other stage resolves — arrived here untiled,
        skipped the representative collapse, and left the scan unclassified.
        """
        return MatUtils.has_path_token(os.path.basename(path))

    @staticmethod
    def _tiled_representative(resolved: str) -> Optional[str]:
        """One concrete file standing in for a tiled/sequence texture *resolved* path.

        The exporter's name for :meth:`MatUtils.probe_texture_path`, which is
        where this rule now lives in full: ``<udim>`` resolves to its first
        tile, ``1001``, while ``<uvtile>`` resolves to ITS OWN first tile,
        ``u1_v1`` — the two numberings are not interchangeable, and folding
        both onto ``"1001"`` pointed a ``<uvtile>`` set at a file that was
        never written. ``<f>`` has no fixed "first" value, so it globs.

        This was a second implementation of that collapse, listing three of
        the six tokens; the stand-in it produced had to agree with the one the
        shared probe produces (the token table says so in as many words), and
        two copies of a rule that MUST agree is one copy too many.

        Returns:
            The representative path (for the fixed tokens it may not exist —
            the caller's own ``os.path.isfile`` check is what gates that), or
            ``None`` when a frame token's glob finds no file (distinct from
            the fixed-token miss: the caller tells the two apart by this
            return value, so they must not be conflated).
        """
        return MatUtils.probe_texture_path(resolved)

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
            tiled = self._is_tiled_path(path) or bool(
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

    def _exported_objects(self) -> List[str]:
        """The export set AND its descendants — what the write actually ships.

        An FBX export ships the selection's whole subtree, and a hierarchy
        export names roots, so every question about "this export's animation"
        has to be asked of the subtree. Measured on a production
        assembly scene (5 roots / 2717 transforms): the roots answer **0**
        keyframe times and the subtree answers **84**, spanning frames 0-1778 —
        so asking the shallow set read a fully animated assembly as static.
        """
        return cmds.ls(self._live_objects(), dag=True, long=True) or []

    def _get_all_keyframes(self) -> List[float]:
        """Return a sorted list of all unique keyframe times for the export.

        Delegates to ``AnimUtils.get_keyframe_times`` for the actual query and
        caches the result set in ``_key_times`` for downstream consumers.
        Scoped to :meth:`_exported_objects`, so the answer describes the
        deliverable rather than the handful of nodes that happen to name it.
        """
        # Served from the cache while it stands: every key-editing task
        # invalidates it (``_invalidate_keyframe_cache``), and the
        # ``objects`` setter drops it with the rest, so a cached answer is
        # the current one. It used to be written here and read only by
        # ``_has_keyframes`` -- the shear scan alone asked twice per pass.
        cached = getattr(self, "_key_times", None)
        if cached is not None:
            return sorted(cached)

        # Filter to objects that still exist (smart_bake may delete
        # constraints/expressions, removing nodes from the scene).
        existing = self._exported_objects()
        if not existing:
            return []

        times = AnimUtils.get_keyframe_times(existing)
        if times is None:
            self._key_times = set()
            return []

        self._key_times = set(times)
        return times

    def _protect_scene_animation(self) -> bool:
        """Capture the export set's curves so the write can edit them freely.

        The Animation Output gate's whole mechanism, and the animation twin of
        the texture pass's staging: every task that edits keys calls this
        FIRST, the edits are made and read by the write, and one deferred
        restore (post-write, so the FBX and any GLB conversion both see the
        edited curves) puts the scene back.

        Idempotent by construction rather than by a flag: staging is keyed and
        first-wins (:meth:`stage_deferred_restore`), so four tasks calling this
        take ONE snapshot -- the one from before the first of them ran, which
        is the only correct one to restore.

        Returns:
            True when the animation is protected -- either because this call
            staged the snapshot or because an earlier task already did. False
            in write-back mode, where the edits are the point.
        """
        if getattr(self, "_animation_write_back", False):
            return False
        if "animation" in self._deferred_restores:
            return True  # an earlier task already captured the scene
        snapshot = AnimUtils.snapshot_curves(self._live_objects(), recursive=True)
        self.stage_deferred_restore(
            "animation", lambda: self._restore_animation(snapshot)
        )
        self.logger.debug(
            f"Animation Output: captured {len(snapshot.get('records') or [])} "
            "curve(s); the scene's keys are restored after the write."
        )
        return True

    def _restore_animation(self, snapshot: Dict[str, Any]) -> None:
        """Put the captured curves back and say how many, once per export."""
        restored = AnimUtils.restore_curves(snapshot)
        if restored:
            self.logger.info(
                f"Restored {restored} animation curve(s) — the export's key edits "
                "were staged for the write only (Animation Output: Export Copies)."
            )

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
        self._assess_cache = {}

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
            self.logger.info(f"Repaired {len(result['renamed'])} mangled node name(s).")
        if result["shapes_conformed"]:
            self.logger.info(f"Conformed {result['shapes_conformed']} shape name(s).")
        # Re-derive on EITHER outcome. Conforming a shape to `<transform>Shape`
        # renames it just as surely as repairing a mangled transform does, and
        # a shape sitting in the export set then holds a path that no longer
        # resolves -- with nothing else re-deriving it. Measured in a
        # production export: `..._settings_CTRL_xzShape` was conformed here and
        # `smart_bake` died on the stale path eleven tasks later, aborting the
        # run after two minutes of texture work.
        if result["renamed"] or result["shapes_conformed"]:
            self.objects = self._repath_renamed(objects, uuids)

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

        The node *path* edits persist — the exporter has no automatic
        post-export rollback (the undo-chunk restore was removed with the
        smart_bake redesign), so they survive the export by design.  The
        user's undo queue can back them out (each write is undo-anchored
        inside ``stage_textures_relative``).

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

        The size ceiling (``_texture_max_size``, a per-run mode stamped by
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
                f"Optimize Textures is at 'Optimize + Template Budget' but "
                f"the {tpl!r} template is unbudgeted (an authoring target) — "
                "no size clamp applied. Choose an explicit 'Optimize + Max …' "
                "ceiling to resize."
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
                claim_key = os.path.normcase(os.path.join(out_dir, predicted_name))
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
                    staging_dir if nth == 0 else os.path.join(staging_dir, f"alt{nth}")
                )
                claim_key = os.path.normcase(os.path.join(out_dir, predicted_name))

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
            total_after += os.path.getsize(written) if os.path.isfile(written) else 0

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
                    "staged for the write only — scene paths restored after export"
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

        The same hunt heals the lightmap markers first
        (:meth:`LightmapBaker.heal_lightmap_paths`): a committed lightmap is
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
    # The engine is LightmapBaker (mayatk.light_utils); these three are the
    # exporter's thin reads of it, scoped to the live export set. Imported
    # lazily: the baker pulls in the Arnold texture baker, which a headless
    # export that never baked anything should not pay for at import time.

    def _lightmap_dependencies(self) -> List[Dict[str, Any]]:
        """The lightmaps the export set's markers name, resolved on disk NOW
        (:meth:`LightmapBaker.lightmap_dependencies`); ``[]`` when none."""
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        objects = self._live_objects()
        if not objects:
            return []
        return LightmapBaker().lightmap_dependencies(objects)

    def _lightmap_search_dirs(self) -> List[str]:
        """Folders the GLB applier joins the manifest's basenames against
        (:meth:`LightmapBaker.search_dirs`, scoped to the export set)."""
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker.search_dirs(self._live_objects() or None)

    def _heal_lightmap_hints(self) -> None:
        """Rewrite stale lightmap marker hints to where the maps were found.

        Logged at WARNING like the texture rebinds -- a hint moved by name is
        a guess the user should be able to audit -- and what stays missing is
        named, since the exporter's path check is about to fail on it.
        """
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        objects = self._live_objects()
        if not objects:
            return
        report = LightmapBaker().heal_lightmap_paths(objects)
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

    def _shear_scan_nodes(self) -> List[str]:
        """Export-set transforms plus every joint under them.

        The scan set shared by the shear check and the flatten task: skin
        influences carry the geometry, but any animated transform loses the
        same way, so the whole export set is included.
        """
        objects = self._live_objects()
        if not objects:
            return []
        nodes = set(cmds.ls(objects, type="transform", long=True) or [])
        if nodes:  # one descendant walk for the whole set, not one per node
            nodes.update(
                cmds.listRelatives(
                    list(nodes), allDescendents=True, type="joint", fullPath=True
                )
                or []
            )
        return sorted(nodes)

    def flatten_sheared_chains(self, tolerance: float = 0.05) -> tuple:
        """Flatten transforms whose parent-relative matrices shear -- the
        auto-fix for what :meth:`check_sheared_local_transforms` detects.

        FBX and glTF store an animated node as translate/rotate/scale; a
        sheared parent-relative matrix has no TRS form, so the shear is
        dropped and the residual compounds down a chain. The flagged nodes'
        WORLD matrices are orthogonal (that is the squash/stretch shape --
        see the check), so relative to a nearest *similarity* ancestor each
        local is exactly TRS-representable. Detection is the shared
        :meth:`_sheared_offenders` scan (coarse grid everywhere plus every
        frame over scale-dynamic candidates) and a flagged joint pulls in
        its whole chain -- half-flattened chains leak sub-tolerance shear
        from every remaining link.

        The transform is a WORLD-FITTED BAKE, not a live rewrap: every
        planned node's world matrix is sampled per frame from the UNTOUCHED
        scene first, then the node is reparented under its target with
        fitted TRS keys written and its offsetParentMatrix, TRS drivers,
        segmentScaleCompensate and joint orients neutralised (all recorded).
        Two disproven alternatives, both shipped and measured on the production
        wire looms: leaving the live offsetParentMatrix rewrap for FBX
        freezes it whenever its upstream does not translate to FBX; and
        keeping it for smart_bake to sample later still fails because an IK
        solver writes the joints' locals FROM the chain's parent structure
        -- the reparent changes the solve itself (15.9 cm exactly during
        the IK-active shot, with direct jumps equal to sequential
        evaluation). Sampling before any mutation is correct by
        construction. A staged deferred restore puts hierarchy, wiring and
        values back after the write.

        Returns:
            tuple: (True, messages) -- a task, not a check: it repairs
            rather than blocks. Nodes with no clean ancestor are left in
            place for the check to report.
        """
        if tolerance is True:
            # The pre-dial QCheckBox hands this its RAW value, and
            # True == 1.0 — the loosest possible cosine tolerance, which
            # would make the task silently fix nothing (same idiom as
            # check_duplicate_names' pre-dial True).
            tolerance = 0.05
        log_messages: List[str] = []
        if not tolerance:
            return True, log_messages
        offenders = self._sheared_offenders(tolerance)
        if not offenders:
            return True, log_messages
        offenders = self._expand_chain_offenders(offenders)

        frames = self._shear_dense_frames() or self._shear_sample_frames()
        if not frames:
            # The set-scoped key query can answer empty while the members
            # still MOVE -- their driver keys live outside the export set
            # (measured: an ikHandle parented at world level). The playback
            # animation range is the outermost statement of intent.
            start, end = (int(f) for f in AnimUtils.scene_animation_range())
            if end > start:
                frames = [float(f) for f in range(start, end + 1)]
            else:
                frames = [float(cmds.currentTime(query=True))]
        qualifies = self._similarity_ancestors(offenders, frames, tolerance)
        # The scan above may stride past its sample cap; the BAKE below may
        # not. A world-fitted key every OTHER frame leaves the frames between
        # to interpolation, and a fast-moving basis does not interpolate:
        # measured on VDATS_ASSEMBLY (3436 frames, so stride 2), the flattened
        # looms were exact to 1e-13 on the frames sampled and up to 2.0 of
        # world-basis error on the frames between -- the whole of the residual
        # a smart bake was being blamed for.
        bake_frames = self._shear_dense_frames(max_samples=None) or frames
        # Paths go stale the moment the first offender moves, so resolve
        # every later one by UUID; same for the export set itself.
        object_uuids = [(cmds.ls(o, uuid=True) or [None])[0] for o in self.objects]

        plan: List[Tuple[str, str, str, bool]] = []  # (path, uuid, target, reparent)
        unplaced: List[str] = []
        for path in sorted(offenders, key=lambda p: p.count("|")):
            uuid = (cmds.ls(path, uuid=True) or [None])[0]
            if not uuid:
                continue
            target = self._flatten_target(path, qualifies)
            current_parent = (
                cmds.listRelatives(path, parent=True, fullPath=True) or [None]
            )[0]
            if not target:
                unplaced.append(path)
                continue
            if target == current_parent:
                # Chain-complete expansion reaches members already sitting
                # under the target (chain roots). They still need the bake:
                # a solver keeps writing their locals live, against a chain
                # whose other members are about to leave -- so key them in
                # place from the same pristine samples, without reparenting.
                plan.append((path, uuid, target, False))
                continue
            plan.append((path, uuid, target, True))

        records: List[dict] = []
        # Staged BEFORE the first node moves: the lambda closes over the live
        # list, so an exception mid-loop still restores every node already
        # flattened when the export's finally runs the deferred restores.
        self.stage_deferred_restore(
            "flatten_sheared_chains",
            lambda recs=records: self._restore_flattened(recs),
        )
        per_target: Dict[str, int] = {}
        failed: List[str] = []
        # IK handle census BEFORE any mutation: a handle whose chain loses
        # members to the reparent reports an empty jointList afterwards.
        planned_paths = {path for path, _, _, _ in plan}
        handle_chains: Dict[str, set] = {}
        if plan:
            for handle in cmds.ls(type="ikHandle", long=True) or []:
                chain = set(
                    cmds.ls(
                        cmds.ikHandle(handle, query=True, jointList=True) or [],
                        long=True,
                    )
                )
                if chain.intersection(planned_paths):
                    handle_chains[handle] = chain

        baked_uuids: List[str] = []
        baked_paths: set = set()
        if plan:
            samples = self._sample_flatten_locals(plan, bake_frames)
            for path, uuid, target, reparent in plan:
                node = (cmds.ls(uuid, long=True) or [None])[0]
                if not node or (path, target) not in samples:
                    continue
                try:
                    records.append(
                        self._flatten_bake_node(
                            node,
                            target,
                            bake_frames,
                            samples[(path, target)],
                            reparent=reparent,
                        )
                    )
                    baked_uuids.append(uuid)
                    baked_paths.add(path)
                except RuntimeError as e:
                    # A locked or referenced node refuses the reparent --
                    # leave it for the check to report rather than aborting
                    # the export.
                    failed.append(path)
                    self.logger.warning(f"Could not flatten '{node}': {e}")
                    continue
                per_target[target] = per_target.get(target, 0) + 1

        if baked_uuids:
            # An IK solver writes its joints' locals with NO plug
            # connections, so cutting connections does not detach it -- and
            # a handle whose chain lost members to the reparent now solves
            # a different rig. Disable every handle whose PRE-move chain
            # touched a node that actually BAKED (a handle serving only
            # failed/skipped nodes keeps solving; recorded; the restore
            # re-enables it).
            for handle, chain in handle_chains.items():
                if not chain.intersection(baked_paths):
                    continue
                try:
                    prior = cmds.getAttr(f"{handle}.ikBlend")
                    cmds.setAttr(f"{handle}.ikBlend", 0.0)
                except RuntimeError as e:
                    self.logger.warning(f"Could not disable IK handle '{handle}': {e}")
                    continue
                records.append(
                    {
                        "mode": "ikblend",
                        "handle": (cmds.ls(handle, uuid=True) or [None])[0],
                        "value": prior,
                    }
                )

        if records:
            self.objects = self._repath_renamed(self.objects, object_uuids)
            log_messages.append(
                f"Flattened {len(records)} transform(s) whose parent-relative "
                f"matrices shear (> {tolerance:g}): reparented under a clean "
                f"ancestor with world-fitted TRS keys baked at {len(bake_frames)} "
                "frame(s) sampled from the untouched scene. The hierarchy, "
                "wiring and values are restored after the write:"
            )
            for target, count in sorted(per_target.items(), key=lambda kv: -kv[1]):
                log_messages.append(
                    f"    {count} node(s) -> {target.rsplit('|', 1)[-1]}"
                )
        if unplaced:
            log_messages.append(
                f"    {len(unplaced)} sheared node(s) have no similarity "
                "ancestor to flatten under and were left in place (the shear "
                "check will report them):"
            )
            for path in unplaced[:5]:
                log_messages.append(f"        {path.rsplit('|', 1)[-1]}")
        if failed:
            log_messages.append(
                f"    {len(failed)} sheared node(s) refused the reparent "
                "(locked or referenced) and were left in place:"
            )
            for path in failed[:5]:
                log_messages.append(f"        {path.rsplit('|', 1)[-1]}")
        return True, log_messages

    def _similarity_ancestors(
        self, offenders, frames, tolerance: float
    ) -> Dict[str, bool]:
        """``{ancestor path: qualifies}`` for every ancestor of *offenders*.

        A qualifying flatten target is a similarity transform at every
        sampled frame: orthogonal world axes AND uniform axis lengths --
        relative to such a node, an orthogonal world matrix decomposes to
        TRS exactly. A zero-scale sample frame disqualifies a candidate:
        the rewrap references its worldInverseMatrix, which a degenerate
        matrix cannot supply. Precomputed in one time pass over all
        candidates so the flatten loop never touches the timeline.
        """
        from mayatk.core_utils.diagnostics.transform_diag import (
            TransformDiagnostics,
        )

        candidates = set()
        for node in offenders:
            parts = node.split("|")
            for i in range(2, len(parts)):
                candidates.add("|".join(parts[:i]))
        verdict = {c: True for c in candidates}
        if not candidates:
            return verdict

        restore_time = cmds.currentTime(query=True)
        try:
            for frame in list(frames) if frames else [None]:
                if frame is not None:
                    cmds.currentTime(frame)
                for candidate, ok in verdict.items():
                    if not ok:
                        continue
                    m = cmds.xform(candidate, query=True, worldSpace=True, matrix=True)
                    axes = (m[0:3], m[4:7], m[8:11])
                    lengths = [math.sqrt(sum(v * v for v in a)) for a in axes]
                    longest = max(lengths)
                    if longest < 1e-6:
                        # Degenerate at this frame: it can't be judged AND the
                        # rewrap can't invert it -- disqualify outright. (Maya
                        # hides via .visibility, which never touches scale; a
                        # zero here is a scale-keyed pop-in.)
                        verdict[candidate] = False
                        continue
                    if (longest - min(lengths)) / longest > 0.01:
                        verdict[candidate] = False
                        continue
                    if TransformDiagnostics._matrix_skew(m) > tolerance:
                        verdict[candidate] = False
        finally:
            cmds.currentTime(restore_time)
        return verdict

    @staticmethod
    def _flatten_target(node: str, qualifies: Dict[str, bool]) -> Optional[str]:
        """Deepest qualifying ancestor of *node* (by its pre-flatten path).

        Nearest-first keeps the node inside its own rig group -- and inside
        any visibility-toggled subtree above it, so an animated hide keeps
        applying to it exactly as before.
        """
        parts = node.split("|")
        for i in range(len(parts) - 1, 1, -1):
            candidate = "|".join(parts[:i])
            if qualifies.get(candidate):
                return candidate
        return None

    #: TRS channels the baked flatten writes, in xform order.
    _FLATTEN_CHANNELS = (
        ("translateX", "TL"),
        ("translateY", "TL"),
        ("translateZ", "TL"),
        ("rotateX", "TA"),
        ("rotateY", "TA"),
        ("rotateZ", "TA"),
        ("scaleX", "TU"),
        ("scaleY", "TU"),
        ("scaleZ", "TU"),
    )

    def _sample_flatten_locals(
        self, plan: List[Tuple[str, str, str, bool]], frames: List[float]
    ) -> Dict[Tuple[str, str], List[List[float]]]:
        """``{(node, target): [[tx..sz] per frame]}`` from the UNTOUCHED scene.

        One timeline pass for the whole plan (currentTime is the expensive
        step). Sampling BEFORE any mutation is the load-bearing choice: an
        IK solver computes the joints' locals from the chain's parent
        structure, so any post-reparent evaluation answers a different rig.
        Rotations are unwound against the previous frame so the keyed euler
        curves stay continuous.
        """
        import math as _math

        import maya.api.OpenMaya as om2

        targets = sorted({t for _, _, t, _ in plan})
        out: Dict[Tuple[str, str], List[List[float]]] = {
            (p, t): [] for p, _, t, _ in plan
        }
        prev_euler: Dict[str, List[float]] = {}
        orders: Dict[str, int] = {}
        for path, _, _, _ in plan:
            orders[path] = cmds.getAttr(f"{path}.rotateOrder")
        restore_time = cmds.currentTime(query=True)
        try:
            for frame in frames:
                cmds.currentTime(frame)
                inverses = {
                    t: om2.MMatrix(cmds.getAttr(f"{t}.worldInverseMatrix[0]"))
                    for t in targets
                }
                for path, _, target, _ in plan:
                    world = om2.MMatrix(cmds.getAttr(f"{path}.worldMatrix[0]"))
                    local = world * inverses[target]
                    xf = om2.MTransformationMatrix(local)
                    t3 = xf.translation(om2.MSpace.kWorld)
                    euler = xf.rotation(asQuaternion=True).asEulerRotation()
                    euler = euler.reorder(orders[path])
                    cur = [euler.x, euler.y, euler.z]
                    prev = prev_euler.get(path)
                    if prev is not None:
                        for i in range(3):
                            while cur[i] - prev[i] > _math.pi:
                                cur[i] -= 2.0 * _math.pi
                            while prev[i] - cur[i] > _math.pi:
                                cur[i] += 2.0 * _math.pi
                    prev_euler[path] = cur
                    s3 = xf.scale(om2.MSpace.kWorld)
                    out[(path, target)].append(
                        [t3.x, t3.y, t3.z, cur[0], cur[1], cur[2], *s3]
                    )
        finally:
            cmds.currentTime(restore_time)
        return out

    def _flatten_bake_node(
        self,
        node: str,
        target: str,
        frames: List[float],
        rows: List[List[float]],
        reparent: bool = True,
    ) -> dict:
        """Reparent *node* under *target* and key the pre-sampled locals.

        Neutralises everything that would fight the keys: TRS driver
        connections are cut (recorded), offsetParentMatrix is reset to
        identity (source/value recorded), and on joints the orient/axis/
        segmentScaleCompensate are zeroed so the keyed rotate IS the local
        rotation. Returns the restore record for :meth:`_restore_flattened`.
        """
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        record: dict = {
            "mode": "baked",
            "reparented": reparent,
            "node": (cmds.ls(node, uuid=True) or [None])[0],
            "old_parent": None,
            "opm_source": None,
            "opm_value": None,
            "cut": [],
            "originals": {},
            "curves": [],
            "joint": {},
        }
        parent = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
        record["old_parent"] = (cmds.ls(parent, uuid=True) or [None])[0]

        opm_plug = f"{node}.offsetParentMatrix"
        record["opm_source"] = (
            cmds.listConnections(opm_plug, source=True, destination=False, plugs=True)
            or [None]
        )[0]
        record["opm_value"] = cmds.getAttr(opm_plug)

        for attr, _ in self._FLATTEN_CHANNELS:
            plug = f"{node}.{attr}"
            src_plug = (
                cmds.listConnections(plug, source=True, destination=False, plugs=True)
                or [None]
            )[0]
            if src_plug:
                record["cut"].append([src_plug, attr])
            else:
                record["originals"][attr] = cmds.getAttr(plug)
        for attr in ("shearXY", "shearXZ", "shearYZ"):
            record["originals"][attr] = cmds.getAttr(f"{node}.{attr}")
        if cmds.attributeQuery("jointOrient", node=node, exists=True):
            record["joint"] = {
                "jointOrient": list(cmds.getAttr(f"{node}.jointOrient")[0]),
                "rotateAxis": list(cmds.getAttr(f"{node}.rotateAxis")[0]),
                "segmentScaleCompensate": cmds.getAttr(
                    f"{node}.segmentScaleCompensate"
                ),
            }

        # The reparent is the one call expected to refuse (locked/referenced);
        # everything before it was read-only, so a raise there leaves the
        # scene untouched for this node. Anything failing AFTER it rolls the
        # node back through its own record -- a moved node without a record
        # would be invisible to the deferred restore.
        # relative=True: absolute parenting inserts a compensating
        # 'transform1' buffer above a joint whenever jointOrient cannot
        # absorb the move -- and the fitted keys are relative to TARGET,
        # not target x buffer. Local values are overwritten by the keys.
        if reparent:
            moved = cmds.parent(node, target, relative=True)[0]
            node = (cmds.ls(moved, long=True) or [moved])[0]
        try:
            for src_plug, attr in record["cut"]:
                try:
                    cmds.disconnectAttr(src_plug, f"{node}.{attr}")
                except RuntimeError:
                    pass
            if record["opm_source"]:
                cmds.disconnectAttr(record["opm_source"], f"{node}.offsetParentMatrix")
            cmds.setAttr(
                f"{node}.offsetParentMatrix",
                [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                type="matrix",
            )
            if record["joint"]:
                cmds.setAttr(f"{node}.jointOrient", 0, 0, 0)
                cmds.setAttr(f"{node}.rotateAxis", 0, 0, 0)
                cmds.setAttr(f"{node}.segmentScaleCompensate", False)
            cmds.setAttr(f"{node}.shear", 0, 0, 0)

            sel = om2.MSelectionList()
            sel.add(node)
            dep = om2.MFnDependencyNode(sel.getDependNode(0))
            time_array = om2.MTimeArray()
            unit = om2.MTime.uiUnit()
            for frame in frames:
                time_array.append(om2.MTime(frame, unit))
            kinds = {
                "TL": oma2.MFnAnimCurve.kAnimCurveTL,
                "TA": oma2.MFnAnimCurve.kAnimCurveTA,
                "TU": oma2.MFnAnimCurve.kAnimCurveTU,
            }
            for column, (attr, kind) in enumerate(self._FLATTEN_CHANNELS):
                values = om2.MDoubleArray()
                for row in rows:
                    values.append(row[column])
                fn = oma2.MFnAnimCurve()
                fn.create(dep.findPlug(attr, False), kinds[kind])
                fn.addKeys(
                    time_array,
                    values,
                    oma2.MFnAnimCurve.kTangentLinear,
                    oma2.MFnAnimCurve.kTangentLinear,
                )
                record["curves"].append((cmds.ls(fn.name(), uuid=True) or [None])[0])
        except Exception as e:
            self._restore_baked_flatten(record)
            raise RuntimeError(f"flatten bake failed mid-mutation: {e}")
        return record

    def _restore_flattened(self, records: List[dict]) -> None:
        """Deferred restore: reverse every flatten record (LIFO)."""
        restored = 0
        for record in reversed(records):
            try:
                if self._restore_baked_flatten(record):
                    restored += 1
            except RuntimeError as e:
                self.logger.warning(f"Flatten restore failed for one node: {e}")
        if restored:
            self.logger.info(
                f"Restored {restored} flattened transform(s) to their original "
                "parents -- the flatten was staged for the write only."
            )

    def _restore_baked_flatten(self, record: dict) -> bool:
        """Reverse one :meth:`_flatten_bake_node` record."""

        def _resolve(uuid):
            return (cmds.ls(uuid, long=True) or [None])[0] if uuid else None

        if record.get("mode") == "ikblend":
            handle = _resolve(record.get("handle"))
            if handle:
                try:
                    cmds.setAttr(f"{handle}.ikBlend", record.get("value", 1.0))
                except RuntimeError:
                    return False
                return True
            return False
        node = _resolve(record.get("node"))
        old_parent = _resolve(record.get("old_parent"))
        for curve_uuid in record.get("curves", []):
            curve = _resolve(curve_uuid)
            if curve:
                cmds.delete(curve)
        if not node or not old_parent:
            return False
        if record.get("reparented", True):
            # relative=True for the same buffer-insertion reason as the
            # bake; the recorded values/wiring restore the true local.
            moved = cmds.parent(node, old_parent, relative=True)[0]
            node = (cmds.ls(moved, long=True) or [moved])[0]
        joint = record.get("joint") or {}
        if joint:
            cmds.setAttr(f"{node}.jointOrient", *joint["jointOrient"])
            cmds.setAttr(f"{node}.rotateAxis", *joint["rotateAxis"])
            cmds.setAttr(
                f"{node}.segmentScaleCompensate",
                joint["segmentScaleCompensate"],
            )
        for attr, value in (record.get("originals") or {}).items():
            try:
                cmds.setAttr(f"{node}.{attr}", value)
            except RuntimeError:
                pass
        for src_plug, attr in record.get("cut", []):
            try:
                cmds.connectAttr(src_plug, f"{node}.{attr}", force=True)
            except RuntimeError:
                pass
        opm_plug = f"{node}.offsetParentMatrix"
        if record.get("opm_source"):
            try:
                cmds.connectAttr(record["opm_source"], opm_plug, force=True)
            except RuntimeError:
                pass
        elif record.get("opm_value") is not None:
            cmds.setAttr(opm_plug, record["opm_value"], type="matrix")
        return True

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
        # optimize its own output, at the same level.  _optimize_keys_level is
        # set per run by _execute_tasks_and_checks; SmartBake resolves the
        # token itself against AnimUtils.OPTIMIZE_LEVELS.
        baker = SmartBake(
            # `_live_objects`, not the raw set: this is the first task to walk
            # every node one at a time, so it is where a path invalidated by an
            # earlier task surfaces -- as a RuntimeError out of a query, eleven
            # tasks into a run. Every other bulk consumer in this class already
            # goes through the same guard.
            objects=self._live_objects(),
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=getattr(self, "_optimize_keys_level", False),
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
        # objects.setter already invalidates the _key_times cache, so no
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
        # Optimizes base-layer curves only — the layer blend nodes smart_bake
        # creates aren't traversed by listConnections, so baked override-layer
        # curves can't be reached from here.  SmartBake optimizes those itself
        # (the smart_bake task passes this run's optimize_keys level through).
        AnimUtils.optimize_keys(self.objects, recursive=True, quiet=True, **kwargs)
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
        current = getattr(self, "_required_range_coverage", None)
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
        :meth:`_get_all_keyframes`), not just the named nodes: the write ships
        descendants, and on a hierarchy export the animation is on them.
        Reading the shallow scope made this skip itself on a fully animated
        production assembly and leave the plugin's factory 1-48 range to ship
        in its place.

        Fractional bookend keys are ENCLOSED -- floor the start, ceil the end
        -- so keys like -0.5 or 100.6 are not truncated inward as int() does.
        """
        all_keyframes = self._get_all_keyframes()
        if not all_keyframes:
            return None
        return math.floor(all_keyframes[0]), math.ceil(all_keyframes[-1])

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

    def _restamp_clip_origin(self) -> None:
        """Re-publish the clip origin from the bake range now in force.

        The published ``clip_span["*"]`` is the authoring frame the exported
        stack puts at its own ``t=0``, and every GLB clip is cut against it.
        Its producer runs well before this task, so it can only read the range
        the FBX preset happens to carry -- which is why this, the task that
        owns the range, restamps it. Reads the range back LIVE rather than
        trusting a local variable, so it is right whichever task last wrote it
        (``apply_declared_takes`` claims a union of its own).

        Silent when nothing will bake: ``bake_range`` answers None there, the
        file carries the scene's own keys, and no single range describes it.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        live = FbxUtils.bake_range()
        if not live:
            return
        if RenderEffects.restamp_stack_span(live[0], live[1]):
            self.logger.debug(f"Clip origin published as {live[0]:g}-{live[1]:g}.")

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
            # OFF means "keep the preset's range", not "publish a stale one":
            # whatever is in force is what bakes, so the origin still restamps.
            self._restamp_clip_origin()
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
        required = getattr(self, "_required_range_coverage", None)
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
            self._restamp_clip_origin()
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
        # The clip origin is DERIVED from this range; publishing it here is
        # what keeps the two from drifting apart across a task reordering.
        self._restamp_clip_origin()

    def tie_all_keyframes(self):
        """Use AnimUtils to tie all keyframes for the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping tie operation.")
            return

        self._protect_scene_animation()
        self.logger.info("Tying keyframes for all objects.")

        # Optimization: Pass cached keyframe range to avoid re-querying
        custom_range = None
        if hasattr(self, "_key_times") and self._key_times:
            # _key_times is a set, need to sort it to get min/max
            sorted_times = sorted(self._key_times)
            custom_range = (sorted_times[0], sorted_times[-1])

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
        AnimUtils.snap_keys_to_frames(self._exported_objects())
        # Key times just changed — a stale cache would make tie_all_keyframes
        # re-insert the fractional bookends this task removed.
        self._invalidate_keyframe_cache()
        self.logger.info("Keyframes have been snapped.")

    def create_glb(self, fbx_path: Optional[str] = None, announce: bool = True):
        """Convert an exported FBX to a GLB through the shared build.

        Runs after the FBX has been written; ``perform_export`` invokes this
        explicitly rather than as part of the pre-export task pipeline.

        The build is :class:`pythontk.GlbPipeline` -- the SAME chain the WebXR
        preview publishes through -- handed this run's dials: the scene sidecar
        built from the export set (:class:`~mayatk.env_utils.scene_state.SceneState`,
        the readers the preview shares), where the maps live NOW
        (:meth:`_lightmap_search_dirs`) and the GLB's half of the panel's two
        texture dials (:meth:`_glb_texture_params`). Neither producer has a
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
                texture_params=self._glb_texture_params(),
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

    def apply_declared_takes(self):
        """Export each declared take as a named Unity clip.

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
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        if not getattr(self, "_data_node_refreshed", False):
            self._refresh_scene_data_node()

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


class _TaskChecksMixin(_TaskDataMixin):
    """ """

    _LOD_SUFFIX_REGEX = re.compile(r"_lod\d*$", re.IGNORECASE)
    _MAX_LISTED_OBJECTS = 25
    _DEFAULT_FLOOR_TOLERANCE = 0.5

    def _obj_link(
        self, node: str, action: str = "reveal", label: Optional[str] = None
    ) -> str:
        """Return a clickable log link for a Maya scene node.

        Parameters:
            node:   Full or short DAG path (the link's param, and its label
                    when *label* is omitted).
            action: ``"select"`` or ``"reveal"`` (default).
            label:  Visible text, for a report where the leaf name is not
                    enough — a same-name collision, where every link would
                    otherwise read the same word.
        """
        return self.logger.log_link(label or node.rsplit("|", 1)[-1], action, node=node)

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
            items = [
                f"  - {self._obj_link(matches[n], 'reveal')}" for n in sorted(matches)
            ]
            messages.append("Geometry with LOD suffix detected (informational):")
            messages.extend(items)
            # The runner only surfaces messages from FAILING checks; this one
            # always passes, so its listing must be logged directly or the
            # check is a silent no-op. As ONE grouped record: every log record
            # is its own paragraph in the export panel, so a line per match
            # rendered the listing as N blank-line-separated sections.
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.log_group(f"LOD suffixes detected ({len(matches)})", items)

        return True, messages

    def ignore_groups(self, names: str, case_sensitive: bool = False) -> None:
        """Exclude top-level groups matching *names* and all their descendants
        from the export object list.

        Parameters:
            names: Comma-separated group name patterns to exclude (e.g.
                ``"temp, proxy"``). Each entry is a shell-style glob, so
                ``"temp*"`` catches ``temp_01``/``tempRig`` and ``"*_proxy"``
                catches ``hull_proxy``. A pattern with no wildcard character
                still matches only that exact name, as before.
            case_sensitive: Match names exactly. Off by default, so ``"temp"``
                catches ``TEMP``. The UI arms it from the Ignore row's option-box
                toggle; a headless caller passes the pair as the dict the task
                dispatcher unpacks -- ``{"names": "Temp", "case_sensitive": True}``
                -- while a bare string still selects the insensitive default.
        """
        if not self.objects or not names:
            return

        # Parse comma-separated patterns. The parse stays here rather than being
        # handed to ``filter_list`` as a raw string because an all-whitespace
        # field must return early: ``filter_list`` with no patterns is a no-op
        # that returns the list unfiltered, which here would mean matching --
        # and so excluding -- every root.
        patterns = ptk.split_delimited_string(
            str(names), delimiter=",", strip_whitespace=True, remove_empty=True
        )
        if not patterns:
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

        # Find top-level groups whose short name matches any pattern. The glob,
        # the case fold and the pattern list all live in ``filter_list``, so the
        # match rules stay identical here, in blendertk's mirror of this task,
        # and in every other filter field in the ecosystem. ``map_func`` reduces
        # the long DAG path to its short name for matching while the filter
        # still returns the full paths.
        root_nodes = cmds.ls(list(root_groups), long=True) or []
        matched_roots = ptk.filter_list(
            root_nodes,
            inc=patterns,
            map_func=lambda n: n.split("|")[-1],
            ignore_case=not case_sensitive,
        )

        if not matched_roots:
            self.logger.debug(f"No top-level groups matching {patterns} found.")
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
            exclude.update(cmds.listRelatives(shape, parent=True, fullPath=True) or [])

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
                # Same scope as the task itself — re-applying the conversion to
                # the rewired nodes must not consolidate externals the task
                # deliberately left alone.
                MatUtils.stage_textures_relative(file_nodes, external_mode="skip")
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
        With a size ceiling set the resize IS part of the pass, so
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
            self.logger.log_group(f"Texture optimization notes ({len(notes)})", notes)

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

    def _deliverable_paths(self) -> List[str]:
        """The files this run will write, in the order it writes them.

        A GLB-only run writes its FBX to a throwaway temp dir, so only the
        ``.glb`` is a destination there; every other mode writes the export
        path itself, plus a sibling ``.glb`` when one is produced.
        """
        export_path = getattr(self, "export_path", "") or ""
        if not export_path:
            return []
        glb_only = bool(getattr(self, "_glb_only", False))
        paths = [] if glb_only else [export_path]
        if glb_only or getattr(self, "_create_glb_enabled", False):
            paths.append(os.path.splitext(export_path)[0] + ".glb")
        return paths

    def check_output_writable(self) -> tuple:
        """Check that every file this run will write can actually be replaced.

        Windows refuses to delete a file, or rename onto it, while another
        process holds it open -- so a deliverable someone is previewing fails
        the write with ``[WinError 32]``. The cost is not the failure but its
        TIMING: the write is the last thing an export does, so a file handle
        that was there all along discards the entire pipeline's work, and
        (for GLB-only) a finished conversion with it.

        Which is why this check declares no task dependencies: it is decidable
        before the first mutation, the scheduler hoists it ahead of everything,
        and a locked destination stops the run in milliseconds with the name of
        the process to close rather than in minutes with an errno.

        A path that does not exist yet cannot be held, and passes.

        Returns:
            tuple: (status: bool, messages: list)
        """
        # Resolved rather than called directly: mayatk and pythontk update
        # independently, and a Maya running an older pythontk would raise
        # AttributeError HERE -- aborting the very export this check exists to
        # protect. Measured in mayapy against the installed pythontk.
        describe = getattr(ptk.FileUtils, "describe_lock", None)
        if describe is None:
            self.logger.warning(
                "Output-writability check skipped: this pythontk predates "
                "FileUtils.describe_lock. A destination held open by another "
                "process will not be caught until the write fails."
            )
            return True, []
        blocked = [(path, describe(path)) for path in self._deliverable_paths()]
        blocked = [(path, why) for path, why in blocked if why]
        if not blocked:
            return True, []
        return False, [
            f"{len(blocked)} destination file(s) cannot be replaced:",
        ] + [
            f"  - {os.path.basename(path)} is {why} -> {path}" for path, why in blocked
        ] + [
            "Close whatever holds the file (a viewer, the WebXR preview, an "
            "engine import) and re-run.",
        ]

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
                    self._obj_link(n, "select") for n in sorted(unresolved_tokens[path])
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

        # 3. Lightmap dependencies -- baked maps the markers name. They live
        # outside every file node (the marker records a basename and the
        # folder the bake was COMMITTED from), so the two gates above never
        # see them, and a scene migrated with its textures ships its GLB
        # unlit and its FBX manifest pointing at nothing -- with one converter
        # warning nobody reads. Resolved the way the GLB applier resolves
        # them (hint, then the live texture folders, then the sourceimages
        # walk); a map found only by search still ships -- the conversion is
        # handed the folder it was found in -- but says so, since the FBX
        # manifest's hint is stale until the Auto-Resolve task rewrites it.
        missing_lightmaps = []
        stale_lightmaps = []
        for dep in self._lightmap_dependencies():
            if not dep["path"]:
                missing_lightmaps.append(dep)
            elif dep["found_by"] != "hint":
                stale_lightmaps.append(dep)

        if missing_lightmaps:
            all_valid = False
            log_messages.append(
                f"{len(missing_lightmaps)} lightmap(s) the bake markers name are "
                "not on disk. The GLB would ship unlit and the FBX manifest would "
                "point at nothing. Relocate them (Texture Path Editor ▸ Find & "
                "Copy Textures, lightmaps included) or revert the bake (Lightmap "
                "Baker ▸ Revert)."
            )
            entries = []
            for dep in missing_lightmaps:
                links = ", ".join(
                    self._obj_link(o, "select") for o in sorted(dep["objects"])
                )
                where = f"{dep['dir']}/{dep['map']}" if dep["dir"] else dep["map"]
                note = f" ({dep['note']})" if dep.get("note") else ""
                entries.append(f"Missing Lightmap: {links} -> {where}{note}")
            log_messages.extend(self._truncate_obj_entries(entries))

        for dep in stale_lightmaps:
            log_messages.append(
                f"Lightmap {dep['map']}: the recorded folder "
                f"{dep['dir'] or '<none>'} no longer holds it; found at "
                f"{dep['path']} (shipped from there; enable the Resolve Invalid "
                "Texture Paths task to rewrite the marker)."
            )

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

    #: Duplicate Names — how wide the short-name scan casts, narrowest first;
    #: each tier is a superset of the one above it.  Keys are the combo's
    #: labels, values the scope token :meth:`check_duplicate_names` resolves
    #: (``None`` = OFF, which the panel's falsy filter drops before the check
    #: is ever dispatched).
    #:
    #: The tiers answer "whose name is load-bearing downstream?".  FBX keeps
    #: the hierarchy, so a short-name collision only bites where a consumer
    #: resolves nodes by their FLAT name: sockets, skeleton bones, and the
    #: animation/metadata that is re-bound by node name.  Plain groups collide
    #: harmlessly all the time, which is why they arrive only under the
    #: explicitly strictest option and never in a middle tier.
    _duplicate_name_options: Dict[str, Any] = {
        "OFF": None,
        "Locators": "locators",
        "Locators & Joints": "joints",
        "Connected & Animated": "connected",
        "All Export Objects": "all",
    }

    #: Scope token -> its combo label, for the failure report's header.
    _duplicate_name_labels: Dict[str, str] = {
        v: k for k, v in _duplicate_name_options.items() if v
    }

    #: Transform channels whose INCOMING connections make a node's name
    #: load-bearing: a constraint, an anim curve, a driver, an expression or an
    #: IK solver writes here, and whatever rebuilds that plumbing downstream
    #: re-resolves it by name.  Compounds AND their children: a point
    #: constraint drives ``.translateX/Y/Z`` while a float3 output drives
    #: ``.translate`` itself, and ``listConnections`` on a compound does not
    #: report its children's connections.
    _CONNECTED_CHANNELS = (
        "translate",
        "translateX",
        "translateY",
        "translateZ",
        "rotate",
        "rotateX",
        "rotateY",
        "rotateZ",
        "scale",
        "scaleX",
        "scaleY",
        "scaleZ",
        "visibility",
    )

    @staticmethod
    def _ambiguous_leaf_names(nodes: List[str]) -> List[str]:
        """*nodes* whose leaf name is shared with another entry in the list.

        A node nobody shares a name with cannot be half of a collision, and
        every scope tier draws from the export set — so resolving a tier
        against this pool instead of the whole set is exactly equivalent, and
        turns the per-node connection probe from "every object in the export"
        into "the handful that were already ambiguous".  Pure string work.
        """
        leaves = [n.rsplit("|", 1)[-1] for n in nodes]
        counts: Dict[str, int] = {}
        for leaf in leaves:
            counts[leaf] = counts.get(leaf, 0) + 1
        return [n for n, leaf in zip(nodes, leaves) if counts[leaf] > 1]

    def _duplicate_name_scope(self, scope: str) -> List[str]:
        """The export-set nodes *scope* puts in front of the duplicate scan.

        *scope* is validated by :meth:`check_duplicate_names`; anything it did
        not recognize never reaches here (the widest branch is the fallthrough,
        so an unvalidated typo would silently scan a NARROWER tier and pass).
        """
        objects = self._live_objects()
        if not objects:  # listRelatives([]) would fall back to the selection
            return []
        objects = self._ambiguous_leaf_names(objects)
        if not objects:
            return []
        if scope == "all":
            # Transforms only (joints included — cmds.ls matches derived
            # types): the 'all' export scope puts SHAPES in the set too, and
            # two cubes both named CRATE also carry two CRATEShape nodes, so
            # reporting shapes doubles every row with a name the FBX consumer
            # never resolves against.  Shape-name hygiene is conform_shape_names'.
            return cmds.ls(objects, type="transform", long=True) or []

        # Locator TRANSFORMS: the set holds transforms and the type lives on
        # the shape, so this is a shape query with a hop back up to the parent.
        locator_shapes = (
            cmds.listRelatives(objects, shapes=True, type="locator", fullPath=True)
            or []
        )
        nodes = set(
            cmds.listRelatives(locator_shapes, parent=True, fullPath=True) or []
            if locator_shapes
            else []
        )
        if scope == "locators":
            return sorted(nodes)

        nodes.update(cmds.ls(objects, type="joint", long=True) or [])
        if scope == "joints":
            return sorted(nodes)

        nodes.update(self._connected_transforms(objects))
        return sorted(nodes)

    def _connected_transforms(self, objects: List[str]) -> List[str]:
        """*objects* carrying an incoming connection on a transform channel."""
        connected = []
        for obj in objects:
            # Shapes reach the set too ('all' scope lists geometry); they have
            # none of these channels, and their transform is what gets named.
            if not cmds.objectType(obj, isAType="transform"):
                continue
            plugs = [f"{obj}.{attr}" for attr in self._CONNECTED_CHANNELS]
            if cmds.listConnections(
                plugs, source=True, destination=False, skipConversionNodes=True
            ):
                connected.append(obj)
        return connected

    def check_duplicate_names(self, scope: Optional[str] = None) -> tuple:
        """Check for duplicate short names within the export set.

        Parameters:
            scope: One of :attr:`_duplicate_name_options`' values —
                ``"locators"``, ``"joints"``, ``"connected"`` or ``"all"``.
                Falsy (or ``"OFF"``) skips the check; ``True`` is read as
                ``"locators"``, the scope the pre-dial checkbox had.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages: List[str] = []
        if not scope or str(scope).upper() == "OFF":
            return True, log_messages
        scope = "locators" if scope is True else str(scope).lower()
        if scope not in self._duplicate_name_labels:
            # Loud, not a fallthrough: the resolver's widest branch is its
            # default, so a typo'd scope would quietly scan a NARROWER tier
            # than the caller asked for and PASS the export on that basis.
            valid = ", ".join(sorted(self._duplicate_name_labels))
            return False, [f"Unknown duplicate-name scope {scope!r}. Valid: {valid}."]

        nodes = self._duplicate_name_scope(scope)
        if not nodes:
            return True, log_messages

        seen: Dict[str, str] = {}
        collisions: Dict[str, List[str]] = {}
        for node in nodes:
            name = node.rsplit("|", 1)[-1]
            if name in seen:
                collisions.setdefault(name, [seen[name]]).append(node)
            else:
                seen[name] = node

        if not collisions:
            return True, log_messages

        label = self._duplicate_name_labels.get(scope, scope)
        log_messages.append(
            f"{len(collisions)} duplicate short name(s) in scope '{label}':"
        )
        entries = [
            f"  - {name} (x{len(paths)}): "
            # FULL path as the link label: every link in a collision row would
            # otherwise read the same leaf name and tell the user nothing
            # about WHICH pair collided.
            + ", ".join(self._obj_link(p, "reveal", label=p) for p in paths)
            for name, paths in sorted(collisions.items())
        ]
        return False, log_messages + self._truncate_obj_entries(entries)

    def check_duplicate_locator_names(self, enabled=True) -> tuple:
        """Deprecated alias for ``check_duplicate_names("locators")``.

        Kept for one release: headless callers (and presets saved before the
        check grew its scope dial) still pass this key as a bool.
        """
        return self.check_duplicate_names("locators" if enabled else None)

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

    #: Shading groups that mean "nobody assigned this one". ``initialShadingGroup``
    #: is Maya's own fallback; a shape wired to neither it nor anything else is the
    #: same story one step earlier.
    DEFAULT_SHADING_GROUPS = ("initialShadingGroup", "initialParticleSE")

    def check_default_materials(self) -> tuple:
        """Geometry shipping on Maya's fallback shader instead of an authored one.

        A mesh nobody assigned a material to still exports: Maya hands it
        ``lambert1`` through ``initialShadingGroup``, FBX carries it, and
        FBX2glTF writes it out as ``Default_Material`` -- an untextured grey
        with no base colour and **no normal map**. It is invisible in the
        exporter's other checks because nothing about it is missing or
        malformed; the object simply renders wrong, and only in the deliverable.

        Measured on the production assembly this was added for: 54 of its 55
        GLB materials carried their normal map and the 55th was
        ``Default_Material`` -- found only by auditing the shipped file, which
        is exactly the work this check exists to make unnecessary. Two separate
        causes put geometry there, and only the first is the obvious one; see
        the intermediate-shape paragraph below for the one that actually
        shipped.

        Scoped to the export set (``_live_objects``) rather than the scene: an
        unassigned mesh that never ships is not this export's problem. Reports
        per SHAPE, because a per-face assignment can leave part of one mesh on
        the default while the rest is authored.

        ``descend=True`` is load-bearing. ``_live_objects`` returns the export
        ROOTS re-resolved, not the hierarchy under them, so a walk of direct
        shapes finds nothing at all on a scene exported by its top groups --
        measured against the assembly this was written for, where the check
        passed clean while the deliverable carried the very material it is
        looking for.

        Intermediate shapes are read too, and this is the case that actually
        shipped. An orig shape belongs to the mesh it deforms and never exports
        -- unless it has been parented under a transform that is not that mesh,
        where it reaches the FBX carrying no shading group (an orig shape is in
        none) and WINS over that transform's real shape. Measured:
        ``|STATIC|DA2|INSTRUMENTS|vdat533`` shipped its 20-vertex orig cage on
        ``Default_Material`` while its DA1 twin shipped the authored 45-vertex
        mesh -- the wrong geometry, untextured, and invisible to every other
        check because the transform's own assignment is perfectly correct.

        Having several parents is NOT the test: instancing a deformed mesh
        legitimately shares its orig across every instance, and on the scene
        this was written against 5 of the 6 multi-parent orig shapes were
        exactly that (one at 276 parents). The test is how many DISTINCT real
        shapes those parents carry -- one means ordinary instancing, more than
        one means the orig is riding geometry it does not belong to. That
        separated the single genuine offender from the five false ones.
        """
        offenders = []
        for shape in NodeUtils.get_shapes(
            self._live_objects(), descend=True, type="mesh", no_intermediate=False
        ):
            if NodeUtils.is_intermediate(shape):
                parents = (
                    cmds.listRelatives(shape, allParents=True, fullPath=True) or []
                )
                if len(parents) < 2:
                    continue  # an ordinary orig shape never leaves Maya
                # By UUID, not by path: an instanced shape is ONE node with many
                # paths, and every path is the same geometry.
                real = {
                    uuid
                    for parent in parents
                    for sibling in (
                        cmds.listRelatives(
                            parent, shapes=True, fullPath=True, noIntermediate=True
                        )
                        or []
                    )
                    for uuid in cmds.ls(sibling, uuid=True) or []
                }
                if len(real) > 1:
                    offenders.append(
                        (shape, f"orig shape riding {len(real)} different meshes")
                    )
                continue
            engines = set(cmds.listConnections(shape, type="shadingEngine") or [])
            if not engines:
                offenders.append((shape, "no shading group"))
            elif engines & set(self.DEFAULT_SHADING_GROUPS):
                # Named so a partially-assigned mesh reads as such rather than
                # as a wholly unassigned one.
                offenders.append(
                    (
                        shape,
                        "default shader" + (" (partial)" if len(engines) > 1 else ""),
                    )
                )

        if offenders:
            return False, [
                f"Default material ({reason}): {self._obj_link(shape)}"
                for shape, reason in offenders
            ]
        return True, []

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

    def check_sheared_local_transforms(self, tolerance: float = 0.05) -> tuple:
        """Fail when a node's LOCAL matrix is sheared past *tolerance*.

        FBX and glTF store an animated node as translation/rotation/scale.
        A sheared local matrix has no TRS form, so the exporter drops the
        shear -- and in a chain the residual compounds joint by joint.

        A clean scene reaches this state without any authored shear. A
        spline-IK squash/stretch rig gives every joint the SAME non-uniform
        world scale ``S`` (an ``offsetParentMatrix`` cancels the cascade), so
        each world matrix is perfectly orthogonal. The LOCAL matrix between
        two of them is ``S . R_child . R_parent^-1 . S^-1`` -- a rotation
        conjugated by a non-uniform scale, which is sheared whenever the two
        joints differ in orientation. Checking world matrices finds nothing;
        the loss is entirely in the local ones.

        Measured on a production wire-loom rig: a chain carrying 47%
        stretch reached 0.307 local shear and landed its last joint 7.5 cm
        from Maya's answer -- four times its true distance from the plug it is
        anchored to. A sibling chain at 11% stretch (0.104 shear) stayed
        within 0.5 cm, and one at 0.2% (0.003) was exact.

        Parameters:
            tolerance: Maximum |cos(angle)| between normalised local axes.
                0 is perfectly orthogonal. Falsy skips the check.

        Returns:
            tuple: (status: bool, messages: list)
        """

        if tolerance is True:
            # The pre-dial QCheckBox delivers its raw value; True == 1.0
            # would pass every scene (see flatten_sheared_chains).
            tolerance = 0.05
        log_messages: List[str] = []
        if not tolerance:
            return True, log_messages

        offenders = self._sheared_offenders(tolerance)
        if not offenders:
            return True, log_messages

        # Report per rig group, not per node: a sheared chain lists every joint,
        # and a 20-deep DAG path per joint buries the one fact that matters --
        # which rig is bad, and by how much.
        worst_by_group: Dict[str, tuple] = {}
        counts_by_group: Dict[str, int] = {}
        for node, skew in offenders.items():
            parts = node.split("|")
            group = next(
                (p for p in reversed(parts[:-1]) if p.endswith("_GRP")),
                parts[1] if len(parts) > 1 else node,
            )
            counts_by_group[group] = counts_by_group.get(group, 0) + 1
            current = worst_by_group.get(group)
            if current is None or skew > current[0]:
                worst_by_group[group] = (skew, node.rsplit("|", 1)[-1])

        log_messages.append(
            f"{len(offenders)} node(s) across {len(worst_by_group)} group(s) "
            f"have a local transform FBX/glTF cannot represent (> "
            f"{tolerance:g}): a sheared local has no TRS form, and a "
            "segment-scale-compensated joint under a scaling parent "
            "recomposes with the parent scale the rig cancels. Either way "
            "the error compounds along a chain:"
        )
        for group, (skew, leaf) in sorted(
            worst_by_group.items(), key=lambda kv: -kv[1][0]
        ):
            log_messages.append(
                f"    {skew:.4f}  {group}  "
                f"({counts_by_group[group]} node(s), worst at {leaf})"
            )
        log_messages.append(
            "    Usual cause: squash/stretch scale on a joint chain "
            "(sheared locals, or segmentScaleCompensate the formats lack). "
            "The Flatten Sheared Chains task re-anchors these "
            "automatically; otherwise reduce the stretch at the source."
        )
        return False, log_messages

    def _shear_sample_frames(self, limit: int = 5) -> List[float]:
        """COARSE grid: *limit* frames spread across the scene's keyed range.

        Catches static shear and anything sheared most of the time. It is NOT
        sufficient alone: production wire looms sheared only inside Shot_2/3
        (f146-250 of 1818) and this grid never landed there -- the dense pass
        over :meth:`_shear_candidates` covers the gaps. Returns an empty list
        for a static scene, which the diagnostic reads as "current frame
        only".
        """
        keys = self._get_all_keyframes()
        if not keys:
            return []
        start, end = float(keys[0]), float(keys[-1])
        if end <= start:
            return [start]
        step = (end - start) / (limit - 1)
        return [start + step * i for i in range(limit)]

    def _shear_dense_frames(self, max_samples: Optional[int] = 2000) -> List[float]:
        """Every integer frame across the keyed range (strided past the cap).

        The coarse grid ships broken rigs -- a shear spike between its
        samples is invisible (measured: wire looms sheared only during their
        own shot). Dense scanning is affordable because it only runs over
        :meth:`_shear_candidates`, the small set whose scale can actually
        change over time.

        Parameters:
            max_samples: Cap on the number of frames; the range is strided to
                fit. None asks for EVERY integer frame -- what a bake needs,
                as opposed to a scan, since the frames a strided bake skips
                are left to interpolation.
        """
        keys = self._get_all_keyframes()
        if not keys:
            return []
        start = int(math.floor(keys[0]))
        end = int(math.ceil(keys[-1]))
        if end <= start:
            return [float(start)]
        span = end - start
        stride = 1 if not max_samples else max(1, -(-span // max_samples))
        frames = [float(f) for f in range(start, end + 1, stride)]
        if frames[-1] != float(end):
            frames.append(float(end))
        return frames

    def _shear_candidates(self, nodes: List[str]) -> List[str]:
        """The subset of *nodes* whose parent-relative matrix can shear over
        TIME: nodes with a connection-driven scale or offsetParentMatrix on
        themselves or any ancestor.

        Static non-uniform scale shears identically at every frame -- the
        coarse grid already sees it. Only a scale that CHANGES (driven scale
        channels, or a matrix input, which can carry scale) produces the
        frame-local shear the coarse grid misses, and such a drive marks the
        node and every descendant as candidates.
        """
        paths = set(nodes)
        for node in nodes:
            parts = node.split("|")
            for i in range(2, len(parts)):
                paths.add("|".join(parts[:i]))
        # ONE connection query over every plug, read back through the
        # destination side. Per-path attributeQuery + listConnections cost
        # ~0.13 ms x 2 per node (measured 0.32 s over a 2400-path scan, run
        # twice per export -- the flatten task and the check share this);
        # a single batched call is ~10 ms. Every DAG transform carries
        # offsetParentMatrix (Maya 2020+), so the existence probe is gone
        # too; ``cmds.ls`` drops the paths an earlier task has deleted.
        plugs = [
            f"{path}.{attr}"
            for path in (cmds.ls(list(paths), long=True) or [])
            for attr in ("scale", "scaleX", "scaleY", "scaleZ", "offsetParentMatrix")
        ]
        # Destination nodes come back shortest-unique; the keys must be the
        # full paths the caller walks, so map them back through ls.
        dynamic = set(
            cmds.ls(
                [
                    dest.rsplit(".", 1)[0]
                    for dest, _ in NodeUtils.incoming_connections(plugs)
                ],
                long=True,
            )
            or []
        )
        if not dynamic:
            return []
        out = []
        for node in nodes:
            parts = node.split("|")
            if any("|".join(parts[:i]) in dynamic for i in range(2, len(parts) + 1)):
                out.append(node)
        return out

    def _ssc_offenders(
        self, tolerance: float, nodes: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """``{joint: worst |parent scale - 1|}`` for compensated joints whose
        parent actually scales.

        Maya's segmentScaleCompensate cancels the parent joint's scale
        before the child's transform; FBX and glTF recompose plain per-node
        TRS, so the parent scale compounds down the chain instead (measured
        +17.8% bone stretch by the 11th link of a production wire loom).
        The loss needs no shear at all -- a straight compensated chain under
        a scaling parent still ships wrong -- so this scan complements the
        skew metric instead of extending it: active compensation (an
        actually-wired ``inverseScale``) under a parent whose scale leaves
        1.0 beyond *tolerance* -- statically, on a key, or through a
        connection-driven value at any dense-scan frame -- flags the joint
        for the same flatten.
        """
        out: Dict[str, float] = {}
        driven: Dict[str, List[str]] = {}
        scan = self._shear_scan_nodes() if nodes is None else nodes
        # Only joints carry segmentScaleCompensate: one typed ``ls`` instead
        # of a ``nodeType`` per node of the whole export set.
        for node in cmds.ls(scan, type="joint", long=True) or []:
            if not cmds.getAttr(f"{node}.segmentScaleCompensate"):
                continue
            if not cmds.listConnections(
                f"{node}.inverseScale", source=True, destination=False
            ):
                # An UNWIRED inverseScale holds its default (1,1,1) and
                # compensates nothing (non-joint parents are never wired).
                # A hand-set static non-unit value on an unwired plug is out
                # of scope here -- when its shear shows, the skew scan has it.
                continue
            parent = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
            if not parent:
                continue
            worst = max(abs(v - 1.0) for v in cmds.getAttr(f"{parent}.scale")[0])
            keyed = cmds.keyframe(
                parent,
                attribute=["scaleX", "scaleY", "scaleZ"],
                query=True,
                valueChange=True,
            )
            if keyed:
                worst = max(worst, max(abs(v - 1.0) for v in keyed))
            sources = (
                cmds.listConnections(
                    f"{parent}.scale",
                    f"{parent}.scaleX",
                    f"{parent}.scaleY",
                    f"{parent}.scaleZ",
                    source=True,
                    destination=False,
                )
                or []
            )
            if any(not cmds.nodeType(src).startswith("animCurve") for src in sources):
                # Keys are covered by the valueChange query above; anything
                # else driving scale needs evaluation over time.
                driven.setdefault(parent, []).append(node)
            if worst > tolerance:
                out[node] = max(out.get(node, 0.0), worst)
        if driven:
            frames = self._shear_dense_frames()
            if frames:
                restore = cmds.currentTime(query=True)
                try:
                    for frame in frames:
                        cmds.currentTime(frame, edit=True)
                        for parent, children in driven.items():
                            if not cmds.objExists(parent):
                                continue
                            worst = max(
                                abs(v - 1.0) for v in cmds.getAttr(f"{parent}.scale")[0]
                            )
                            if worst <= tolerance:
                                continue
                            for node in children:
                                if worst > out.get(node, 0.0):
                                    out[node] = worst
                finally:
                    cmds.currentTime(restore, edit=True)
        return out

    def _sheared_offenders(self, tolerance: float) -> Dict[str, float]:
        """``{node: worst skew}`` -- the one scan the check and the flatten
        task share: the coarse grid over everything, plus every integer frame
        over the scale-dynamic candidates.
        """
        from mayatk.core_utils.diagnostics.transform_diag import (
            TransformDiagnostics,
        )

        nodes = self._shear_scan_nodes()
        if not nodes:
            return {}
        offenders = TransformDiagnostics.get_non_orthogonal_local(
            nodes, tolerance=tolerance, frames=self._shear_sample_frames()
        )
        candidates = self._shear_candidates(nodes)
        if candidates:
            dense = self._shear_dense_frames()
            if dense:
                for node, skew in TransformDiagnostics.get_non_orthogonal_local(
                    candidates, tolerance=tolerance, frames=dense
                ).items():
                    if skew > offenders.get(node, 0.0):
                        offenders[node] = skew
        for node, severity in self._ssc_offenders(tolerance, nodes).items():
            if severity > offenders.get(node, 0.0):
                offenders[node] = severity
        for node, severity in self._opm_offenders(tolerance, nodes).items():
            if severity > offenders.get(node, 0.0):
                offenders[node] = severity
        return offenders

    def _opm_offenders(
        self, tolerance: float, nodes: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """``{node: cumulative OPM non-similarity}`` for connected
        ``offsetParentMatrix`` networks a plain TRS cannot carry.

        A CONNECTED offsetParentMatrix never reaches FBX -- the export
        folds ``TRS x OPM`` onto the plugs -- and that product is
        TRS-representable only while the OPM is a SIMILARITY. Non-uniform
        scale (the production _01 wire looms' tweak-follow networks:
        ~3% per link) leaves shear in the folded local that FBX/glTF
        drop. Per link the loss sits under any sane tolerance; down a
        27-joint chain it compounded to 0.65 cm at the tip even after a
        COMPLETE fold -- so severity ACCUMULATES from the nearest
        OPM-connected ancestor (parent's cumulative + this node's
        non-uniformity + shear), evaluated over the dense frames (the
        plug is connection-driven by definition). A flagged node joins
        the same world-fitted flatten, whose similarity-ancestor refit
        is exact.
        """
        import maya.api.OpenMaya as om2

        scan = self._shear_scan_nodes() if nodes is None else nodes
        # One connection query over every OPM plug (a per-node call cost
        # 0.2 s over a 1200-node export set, twice per export); the
        # destination side comes back shortest-unique, so it is mapped to
        # the scan's long paths through ``ls``.
        pairs = NodeUtils.incoming_connections(
            [f"{node}.offsetParentMatrix" for node in scan]
        )
        connected = set(
            cmds.ls([dest.rsplit(".", 1)[0] for dest, _ in pairs], long=True) or []
        )
        targets: List[str] = [node for node in scan if node in connected]
        if not targets:
            return {}
        parent_of = {
            n: (cmds.listRelatives(n, parent=True, fullPath=True) or [None])[0]
            for n in targets
        }
        target_set = set(targets)
        order = sorted(targets, key=lambda p: p.count("|"))
        frames = self._shear_dense_frames() or self._shear_sample_frames()
        if not frames:
            # The set-scoped key query can answer empty while the OPM still
            # ANIMATES -- its driver keys live outside the export set (the
            # unit fixture keys a composeMatrix; production tweak rigs key
            # utility nodes). Same fallback as the flatten task: the
            # playback range is the outermost statement of intent.
            start, end = (int(f) for f in AnimUtils.scene_animation_range())
            frames = (
                [float(f) for f in range(start, end + 1)]
                if end > start
                else [float(cmds.currentTime(query=True))]
            )
        out: Dict[str, float] = {}
        restore = cmds.currentTime(query=True)
        try:
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                cum: Dict[str, float] = {}
                for node in order:
                    if not cmds.objExists(node):
                        continue
                    xf = om2.MTransformationMatrix(
                        om2.MMatrix(cmds.getAttr(f"{node}.offsetParentMatrix"))
                    )
                    s = xf.scale(om2.MSpace.kWorld)
                    sh = xf.shear(om2.MSpace.kWorld)
                    dev = max(
                        abs(s[0] - s[1]),
                        abs(s[1] - s[2]),
                        abs(s[0] - s[2]),
                        abs(sh[0]),
                        abs(sh[1]),
                        abs(sh[2]),
                    )
                    parent = parent_of[node]
                    total = dev + (
                        cum.get(parent, 0.0) if parent in target_set else 0.0
                    )
                    cum[node] = total
                    if total > tolerance and total > out.get(node, 0.0):
                        out[node] = total
        finally:
            cmds.currentTime(restore, edit=True)
        return out

    def _expand_chain_offenders(self, offenders: Dict[str, float]) -> Dict[str, float]:
        """Flagged joints pull in their ENTIRE chain.

        Sub-tolerance members of a flagged chain each leak up to the
        tolerance in dropped shear, and twenty of them compound to visible
        drift -- the mixed half-flattened chains the first production fix
        shipped. Expansion walks joint-to-joint links both ways; a member
        already sitting under the flatten target is skipped quietly later.
        """
        expanded = dict(offenders)
        for path in list(offenders):
            if not cmds.objExists(path):
                continue
            parts = path.split("|")
            for i in range(len(parts) - 1, 2, -1):
                ancestor = "|".join(parts[:i])
                if not cmds.objExists(ancestor):
                    break
                if cmds.nodeType(ancestor) != "joint":
                    break
                expanded.setdefault(ancestor, 0.0)
            for descendant in (
                cmds.listRelatives(
                    path, allDescendents=True, type="joint", fullPath=True
                )
                or []
            ):
                expanded.setdefault(descendant, 0.0)
        return expanded

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
            cmds.ls(list(set(all_curves)), type=list(AnimUtils.TIME_CURVE_TYPES)) or []
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

    #: Above this, the FBX gates step aside instead of parsing (see
    #: :meth:`verify_deliverables`). The record tree costs roughly twice the
    #: file in heap, and this runs at the END of a long export, when losing
    #: the Maya session is most expensive.
    MAX_VERIFY_FBX_BYTES = 512 * 1024 * 1024

    def verify_deliverables(
        self, *paths: str, max_fbx_bytes: Optional[int] = None
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
        follows). It is logged per failing gate at ERROR instead.

        Parameters:
            paths: The deliverables a consumer actually receives. Anything
                that is not a readable ``.fbx``/``.glb`` is ignored, so a USD
                export passes through as a no-op.
            max_fbx_bytes: Size bound for opening the FBX. Defaults to
                :attr:`MAX_VERIFY_FBX_BYTES`.

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
        export_path = getattr(self, "export_path", None)
        if export_path:
            inputs["sidecar"] = SceneDataSidecar.manifest_path_for(
                export_path, **self._sidecar_kwargs()
            )

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
        return report

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
        # Phase 4 — Animation (flatten THEN bake THEN optimize THEN snap/tie
        # THEN split THEN set range). Flatten first: smart_bake and the FBX
        # write must see the export-representable hierarchy.  set_bake_
        # animation_range is LAST — it owns the bake range, and it can only
        # honor its "never clip a declared take" rule once apply_declared_takes
        # has realized the takes it must cover.  (It used to run BEFORE the
        # split, which then overwrote its range with a shot union as an
        # undeclared side effect; that coupling is what the Bake Range combo
        # replaced.)
        "flatten_sheared_chains",
        "smart_bake",
        "optimize_keys",
        "snap_keys_to_frame",
        "tie_all_keyframes",
        "export_data_node",
        "apply_declared_takes",
        "set_bake_animation_range",
    ]

    # --- Check scheduling ------------------------------------------------
    # The three answers most checks share, named once so a task added to a
    # phase is added to every check that reads that phase's output.
    #: Tasks that REMOVE nodes from the export set, so every check reading
    #: ``_live_objects()`` / ``self.objects`` is downstream of them.
    _OBJECT_SET_TASKS = ("ignore_groups", "exclude_hdr")
    #: Tasks that rewrite what a file node points at -- which materials own it
    #: (reassign), where the path resolves (resolve/relativize) and what file
    #: is actually there (convert/optimize).
    _TEXTURE_PATH_TASKS = (
        "reassign_duplicate_materials",
        "resolve_invalid_texture_paths",
        "convert_to_relative_paths",
        "convert_textures",
        "optimize_textures",
    )
    #: Tasks that edit anim curves. ``flatten_sheared_chains`` belongs here as
    #: well as in the hierarchy list: it re-wraps a chain's local matrices, and
    #: a sampled flatten writes keys.
    _KEY_EDIT_TASKS = (
        "flatten_sheared_chains",
        "smart_bake",
        "optimize_keys",
        "snap_keys_to_frame",
        "tie_all_keyframes",
    )

    # For each check, the tasks whose execution can change its verdict --
    # TaskFactory._schedule reads this to hoist each check above the tasks it
    # does not read, so a gate that was always going to fail fails BEFORE the
    # texture and animation phases have burned minutes on a deliverable that
    # will not be written (and, for a check that depends on nothing enabled,
    # before the scene is touched at all).  TASK_ORDER itself is never
    # reordered: it encodes which task must see another's output, and only the
    # checks move.
    #
    # Adding a task means auditing this map. Over-declaring only costs an
    # early abort; UNDER-declaring makes a check judge a scene the pipeline
    # has not finished preparing, so when in doubt, declare the dependency.
    CHECK_DEPENDENCIES: Dict[str, tuple] = {
        # --- General -----------------------------------------------------
        # Scans the scene's references; no task creates, imports or removes
        # one, so this is decidable before the first mutation.
        "check_referenced_objects": (),
        # Reads the destination files, which no task writes or touches -- and
        # the whole point is to fail before the pipeline spends anything.
        "check_output_writable": (),
        # Reads the scene time unit, but only once the export set is known to
        # carry keys at all -- which the filters can empty, and smart_bake can
        # fill (a constraint-driven node has no curves until it is baked).
        "check_framerate": _OBJECT_SET_TASKS + ("smart_bake",),
        # --- Hierarchy & Naming ------------------------------------------
        "check_geometry_lod_suffix": _OBJECT_SET_TASKS + ("conform_shape_names",),
        # Its widest scope ("Connected & Animated") selects by INCOMING
        # transform connections -- which flatten cuts and smart_bake creates --
        # and a reparent silently number-suffixes a name that collides under
        # its new parent, so both hierarchy tasks move this verdict.
        "check_duplicate_names": _OBJECT_SET_TASKS
        + ("conform_shape_names", "flatten_sheared_chains", "smart_bake"),
        "check_duplicate_locator_names": _OBJECT_SET_TASKS
        + ("conform_shape_names", "flatten_sheared_chains", "smart_bake"),
        "check_mangled_names": _OBJECT_SET_TASKS
        + ("conform_shape_names", "flatten_sheared_chains"),
        # set_linear_unit rescales every translate the check reads against
        # identity; flatten can bake a root's local matrix away.
        "check_root_default_transforms": ("set_linear_unit",)
        + _OBJECT_SET_TASKS
        + ("flatten_sheared_chains",),
        # flatten_sheared_chains exists to clear this one; smart_bake writes
        # the matrices it then samples.
        "check_sheared_local_transforms": _OBJECT_SET_TASKS
        + ("flatten_sheared_chains", "smart_bake"),
        # Diffs the FULL export hierarchy against the sidecar baseline, so
        # every task that renames a node, re-parents one, or appends the
        # data_export carrier moves it.
        "check_hierarchy_vs_existing_fbx": _OBJECT_SET_TASKS
        + (
            "conform_shape_names",
            "flatten_sheared_chains",
            "export_data_node",
            "apply_declared_takes",
        ),
        # --- Geometry ----------------------------------------------------
        # smart_bake bakes visibility, which is half of what this reads.
        "check_hidden_geometry": _OBJECT_SET_TASKS + ("smart_bake",),
        "check_overlapping_duplicate_mesh": _OBJECT_SET_TASKS,
        # The floor tolerance is in SCENE UNITS and the bbox is world-space:
        # set_linear_unit rescales both sides, smart_bake can move the object.
        "check_objects_below_floor": ("set_linear_unit",)
        + _OBJECT_SET_TASKS
        + ("smart_bake",),
        # --- Materials & Paths -------------------------------------------
        "check_default_materials": _OBJECT_SET_TASKS
        + ("reassign_duplicate_materials",),
        "check_duplicate_materials": _OBJECT_SET_TASKS
        + ("reassign_duplicate_materials",),
        "check_material_compatibility": _OBJECT_SET_TASKS + _TEXTURE_PATH_TASKS,
        "check_texture_optimization": _OBJECT_SET_TASKS + _TEXTURE_PATH_TASKS,
        # set_workspace is what a relative texture path resolves AGAINST, so
        # both path gates read its result.
        "check_path_length": ("set_workspace",)
        + _OBJECT_SET_TASKS
        + _TEXTURE_PATH_TASKS,
        "check_valid_paths": ("set_workspace",)
        + _OBJECT_SET_TASKS
        + _TEXTURE_PATH_TASKS,
        "check_texture_file_size": _OBJECT_SET_TASKS + _TEXTURE_PATH_TASKS,
        # --- Animation ---------------------------------------------------
        "check_untied_keyframes": _OBJECT_SET_TASKS + _KEY_EDIT_TASKS,
        "check_floating_point_keys": _OBJECT_SET_TASKS + _KEY_EDIT_TASKS,
    }

    _frame_rate_options: Dict[str, Any] = {
        (
            f"{k}"
            if v is None
            else (f"{v:g} fps" if any(c.isdigit() for c in k) else f"{k} ({v:g} fps)")
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
        self._objects = None
        self._invalidate_material_caches()

    def _execute_tasks_and_checks(self, tasks_only, checks_only):
        # smart_bake needs its sibling's setting: baked override-layer curves
        # sit behind animBlendNodes that listConnections can't traverse, so
        # the separate optimize_keys task can never reach them — SmartBake
        # must optimize its own output, at the same LEVEL the UI selected
        # ("Also controls key optimization inside Smart Bake").  Passed
        # through unresolved: SmartBake resolves it against
        # AnimUtils.OPTIMIZE_LEVELS, so there is one table and no second
        # translation to drift.  The generic TaskFactory knows nothing about
        # either task, so it is set here, in the consumer that reads it (same
        # idiom as blendertk).
        self._optimize_keys_level = tasks_only.get("optimize_keys", False)
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
        # ... and of every frame span claimed through _require_range_coverage,
        # which set_bake_animation_range widens to cover.  Left standing, a run
        # with no takes would widen to the PREVIOUS export's shots.
        self._required_range_coverage = None
        self._invalidate_keyframe_cache()

    # Texture Output — do the texture-processing tasks (convert_textures,
    # optimize_textures) modify the scene's textures, or stage copies for the
    # export and restore the scene afterwards? Data is the write-back flag
    # perform_export pops (never a dispatched task).
    _texture_output_options: Dict[str, Any] = {
        "Export Copies (Scene Untouched)": False,
        "Scene Files (In Place)": True,
    }

    # Animation Output — the same question for the tasks that edit KEYS
    # (smart_bake, optimize_keys, tie_all_keyframes, snap_keys_to_frame).
    # Until this existed those four were permanent by default and said so one
    # tooltip at a time, so an export -- an act of publishing -- silently
    # rewrote the artist's curves: optimize DELETES static curves and redundant
    # keys, snap MOVES every key, tie inserts bookends through an API that
    # bypasses the undo queue. Same two choices and the same mechanism as its
    # texture twin: the edits are made, the write reads them, and one deferred
    # restore puts the scene back afterwards.
    _animation_output_options: Dict[str, Any] = {
        "Export Copies (Scene Untouched)": False,
        "Scene Keys (In Place)": True,
    }

    #: Longest-edge ceilings offered by Optimize Textures — the Map Converter's
    #: clamp choices, minus 256 (a scene export never wants that small).
    _TEXTURE_MAX_SIZES = (512, 1024, 2048, 4096, 8192)

    # Optimize Textures — the pass switch and its size dial in ONE combo, so
    # no state is representable where a ceiling is set but nothing would apply
    # it (the widget the old checkbox+combo pairing had to grey out). Data is
    # decomposed by b000 into the two engine inputs the run has always taken:
    # falsy 0 = OFF (the task filter drops it), True = optimize without
    # resampling, an int = optimize + hard pixel ceiling, and
    # TEXTURE_MAX_SIZE_TEMPLATE = optimize + enforce the selected template's
    # own budget. OFF is index 0 (default) and the sentinel is LAST — combos
    # persist by index.
    _optimize_textures_options: Dict[str, Any] = {
        "OFF": 0,
        "Optimize": True,
        **{f"Optimize + Max {s}": s for s in _TEXTURE_MAX_SIZES},
        "Optimize + Template Budget": _TaskDataMixin.TEXTURE_MAX_SIZE_TEMPLATE,
    }

    # Texture File Type — the container dial for EVERY texture the export
    # ships (scene/FBX maps and a GLB's embedded copies alike; the per-
    # destination clamps live in _resolved_output_type / _glb_texture_params).
    # Built from the shared registry so a container added to ImgUtils appears
    # here and in the Map Converter's own Format menu without an edit, plus
    # KTX2 — a delivery-only container no scene file node reads, offered here
    # because a GLB deliverable can carry it. "Original" is index 0 and the
    # falsy sentinel: a TEMPLATE contract (templates persist combos by index),
    # so never reorder or insert above it.
    _texture_file_type_options: Dict[str, Any] = {
        **dict(
            ptk.OutputTemplates.format_choices(sentinel="Original", sentinel_first=True)
        ),
        "KTX2": "ktx2",
    }

    _export_mode_options: Dict[str, Any] = {
        "All Scene Objects": "all",
        "All Visible Objects": "visible",
        "Selected Objects Only": "selected",
    }

    # Bake Range — ONE dial owning the FBX bake range.  Until this existed the
    # range was set by TWO tasks (this one's keyframe extent and
    # apply_declared_takes' shot union) and which won was decided by
    # TASK_ORDER, so clamping an export to its shots meant arming a take SPLIT
    # you might not want — the GLB never does, its clips are rebuilt from the
    # whole stack — and nothing in the panel said so.  OFF is index 0 and the
    # falsy sentinel (b000's filter drops the task): a TEMPLATE contract, since
    # combos persist by index, so never reorder or insert above it.
    _bake_range_options: Dict[str, Any] = {
        "OFF": None,
        "Auto (Shots → Keyframes)": "auto",
        "Keyframe Extent": "keys",
        "Scene Animation Range": "scene",
    }

    # Optimize Keys — the pass switch and its aggressiveness in ONE combo, the
    # same merge Optimize Textures made and for the same reason: a level with
    # nothing to apply it is unrepresentable rather than greyed out.  The
    # SEMANTICS live in AnimUtils.OPTIMIZE_LEVELS (one table, shared with
    # SmartBake and blendertk); these are the labels for them, kept here
    # because presentation is the panel's business.  Same index contract as
    # every other combo: a level added later APPENDS, even if that breaks the
    # least-to-most ordering the rows currently happen to read in.
    _optimize_keys_options: Dict[str, Any] = {
        "OFF": None,
        "Static Curves Only": "static",
        "Static + Flat Keys": "flat",
        "+ Simplify (lossy)": "simplify",
        "Reduce To Extremes": "extremes",
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
                        "<b>Selected Objects Only</b> — exactly the current selection.",
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
                        "Scoped to textures already under <b>sourceimages</b> "
                        "(subfolders included). A texture stored anywhere else "
                        "keeps its absolute path — an external reference is "
                        "usually deliberate, and this task never relocates it. "
                        "The log names any it left alone.",
                        "A relative path only resolves if the file physically "
                        "lives under sourceimages, which is why an external one "
                        "is skipped rather than rewritten: relativizing it would "
                        "point at a file that isn't there and silently break the "
                        "material on import.",
                        "The path edits persist after the export, and are "
                        "undo-anchored so Maya's undo can back them out.",
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
                    "being exported. Committed lightmaps get the same hunt: a "
                    "bake marker whose recorded folder no longer holds its map "
                    "is rewritten to where the map was found, and the FBX "
                    "manifest republished.",
                    notes=[
                        "Rebinding by name is a guess — the original file is gone, "
                        "so nothing can verify content. The hunt is therefore "
                        "gated: the basename must match exactly one file. A unique "
                        "hit is rebound and logged old → new; an ambiguous name is "
                        "reported instead of guessed at.",
                        "&lt;UDIM&gt; / &lt;f&gt; names match by pattern and keep "
                        "their token.",
                        "Lightmap files are never moved — only the marker's "
                        "recorded folder changes. To gather them into the "
                        "project use Texture Path Editor ▸ Find &amp; Copy.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            # -- Textures group: the Texture Output gate FIRST, then the three
            # dials it governs directly beneath it, so the gate and the gated
            # read as one block in the Tasks combo.
            "texture_write_back": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Texture Output",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Output",
                    body="Whether the texture rows below — the <b>Textures</b> "
                    "template conversion and the <b>Optimize Textures</b> "
                    "pass (its size ceiling included) — modify the scene's "
                    "textures, or leave the scene as it was.",
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
            "convert_textures": {
                "widget_type": "ComboBox",
                "group": "Textures",
                # The widget keeps the objectName it had as a Settings row, so
                # every saved template key, ``cmb005_init`` and b000's reads
                # stay valid across the move into the Tasks combo.
                "object_name": "cmb005",
                "set_row_label": "Texture Template",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Template",
                    body="Convert the export's textures to a target texture "
                    "template (a pythontk map-registry workflow) before the "
                    "write — channel packing and shading model re-authored to "
                    "match what the destination engine expects.",
                    bullets=[
                        "<b>As Authored</b> (default) — send textures exactly "
                        "as the scene references them; converts nothing.",
                        "A template — materials are rebuilt through the Map "
                        "Updater, and a paired check fails the export if any "
                        "mask map still does not match.",
                    ],
                    notes=[
                        "Also drives <b>Optimize Textures</b>: the template's "
                        "per-map-type output spec supplies each map's bit "
                        "depth and container, and its size budget is what "
                        "that combo's Template Budget option enforces.",
                        "Where the rebuilt maps land — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                    ],
                ),
            },
            "optimize_textures": {
                "widget_type": "ComboBox",
                "group": "Textures",
                # NOT the old checkbox's objectName: a preset saved before the
                # merge carries optimize_textures (a bool) plus a separate
                # texture_max_size (an index), and letting the bool restore
                # onto this combo would keep the pass while silently dropping
                # the preset's size ceiling. A fresh name makes such a preset
                # trip the PresetManager's uncovered-keys warning instead, so
                # the user re-saves and the template is whole again. (The TASK
                # key stays optimize_textures — b000 decomposes this widget's
                # value back into the optimize_textures + texture_max_size
                # inputs the engine has always taken, so headless callers and
                # TASK_ORDER see no change.)
                "object_name": "texture_optimize",
                "set_row_label": "Optimize Textures",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Textures",
                    body="Run the Map Converter's per-map-type optimization "
                    "pass on the textures shipping with this export — mode "
                    "and bit depth corrected per map type, the export reads "
                    "the optimized copies — with an optional longest-edge "
                    "ceiling: larger maps are downsampled, smaller ones "
                    "never grown.",
                    bullets=[
                        "<b>OFF</b> — ship every map as it is.",
                        "<b>Optimize</b> — the pass without resampling (a "
                        "template's size budget is only reported).",
                        "<b>Optimize + Max 512 … 8192</b> — the pass plus a "
                        "hard pixel ceiling, whatever the template says.",
                        "<b>Optimize + Template Budget</b> — the pass plus "
                        "the selected <b>Textures</b> template's own size "
                        "budget (e.g. glTF/URP 2048, HDRP/Unreal 4096; the "
                        "power-of-two rule is not applied). No resize with "
                        "Textures at <b>As Authored</b> or an unbudgeted "
                        "template.",
                    ],
                    notes=[
                        "With a <b>Textures</b> template selected, the "
                        "template's per-map-type output spec also drives each "
                        "map's container and bit depth (delivery containers "
                        "like KTX2 stay with the GLB half of <b>Texture File "
                        "Type</b>); at <b>As Authored</b> it is a generic "
                        "per-map-type pass and each map keeps its container.",
                        "The ceiling also caps a GLB deliverable's embedded "
                        "copies — one size policy for everything the export "
                        "ships.",
                        "Where the optimized maps go — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                        "Already-optimal maps are left untouched; the paired "
                        "check names anything the pass could not optimize.",
                    ],
                ),
                "add": self._optimize_textures_options,
            },
            "texture_file_type": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Texture File Type",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture File Type",
                    body="Container every texture shipping with this export is "
                    "written in — the maps beside (or inside) the FBX and the "
                    "images embedded in a GLB alike.",
                    bullets=[
                        "<b>Original</b> — keep each source's container; with "
                        "a <b>Textures</b> template selected, the template's "
                        "per-map-type container decides.",
                        "<b>PNG … HDR</b> — write every map as that format.",
                        "<b>KTX2</b> — GPU-compressed Basis for web/XR "
                        "runtimes (UASTC for normals/data, ETC1S for color; "
                        "lightmaps stay lossless WebP). Ships only inside a "
                        "GLB, and the GLB stays importable everywhere: each "
                        "compressed texture embeds a standard PNG/JPEG "
                        "fallback (the KHR_texture_basisu escape hatch), so "
                        "Blender, Unreal or stock Unity read the fallbacks "
                        "while basisu-capable viewers get the GPU-resident "
                        "set. Requires KTX-Software's <b>toktx</b>.",
                    ],
                    notes=[
                        "Naming a type outranks the template's per-map-type "
                        "container, which still supplies bit depth and budget.",
                        "Each destination clamps what it cannot carry: a "
                        "scene file node and an FBX cannot read KTX2, so the "
                        "scene keeps its own container there, and a GLB falls "
                        "back to PNG for anything glTF cannot embed "
                        "(PNG/JPEG/WebP/KTX2 are the ones it can).",
                        "Applied by <b>Optimize Textures</b> for scene maps; "
                        "a GLB deliverable is re-encoded whether or not that "
                        "pass runs.",
                    ],
                ),
                "add": self._texture_file_type_options,
            },
            # -- Animation group: the Animation Output gate FIRST, then the
            # rows it governs, the same way the Textures group reads.
            "animation_write_back": {
                "widget_type": "ComboBox",
                "group": "Animation",
                "set_row_label": "Animation Output",
                "setToolTip": TooltipFormat.fmt(
                    title="Animation Output",
                    body="Whether the key-editing rows below — <b>Smart Bake</b>, "
                    "<b>Optimize Keys</b>, <b>Tie All Keyframes</b> and "
                    "<b>Snap Keys To Frame</b> — change the scene's animation, "
                    "or leave the scene as it was.",
                    bullets=[
                        "<b>Export Copies (Scene Untouched)</b> — "
                        "non-destructive: the curves are captured first, the "
                        "edits are made and written into the deliverable, and "
                        "the scene's keys are restored afterwards.",
                        "<b>Scene Keys (In Place)</b> — permanent: the "
                        "optimized, snapped, tied and baked curves stay in the "
                        "scene. Not reverted after export.",
                    ],
                    notes=[
                        "Inert unless one of those four rows is on.",
                        "Restores the CONTENT of each curve, so animation "
                        "layers, driven keys and constraints are untouched.",
                    ],
                ),
                "add": self._animation_output_options,
            },
            "flatten_sheared_chains": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Flatten Sheared Chains",
                "setToolTip": TooltipFormat.fmt(
                    title="Flatten Sheared Chains",
                    body="Re-anchor joints whose parent-relative transform is "
                    "sheared, so the export can represent them. FBX and glTF "
                    "store animated nodes as translate/rotate/scale \u2014 "
                    "shear is silently dropped and the error compounds down "
                    "a chain.",
                    notes=[
                        "A squash/stretch chain shears with NO authored "
                        "shear: every joint carries the same non-uniform "
                        "world scale, so world matrices look clean while the "
                        "matrices BETWEEN joints skew. Measured: 47% stretch "
                        "put a chain's end 7.5 cm off in the deliverable.",
                        "Live, not baked: each flagged joint is reparented "
                        "under its nearest clean ancestor with its "
                        "offsetParentMatrix rewrapped, so the rig's drivers "
                        "keep working and worlds are preserved exactly.",
                        "The hierarchy and wiring are restored after the write.",
                        "<b>Check For Sheared Local Transforms</b> verifies "
                        "the result.",
                    ],
                ),
                "setChecked": True,
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
                        "Bakes onto an override layer; whether the scene keeps it "
                        "is <b>Animation Output</b>'s call, and by default the "
                        "pre-bake state is restored after the write.",
                        "<b>Optimize Keys</b> also sets the level of the "
                        "optimization pass inside this bake — and <b>Reduce To Extremes</b> "
                        "is the level that suits its per-frame output.",
                    ],
                ),
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "ComboBox",
                "group": "Animation",
                # NOT the old checkbox's objectName. A template saved before
                # this merge carries optimize_keys as a BOOL, and combos
                # persist by index — restoring `true` onto this widget would
                # silently select index 1 (Static Curves Only), a level the
                # user never chose. A fresh name makes such a template trip
                # the PresetManager's uncovered-keys warning instead, so the
                # user re-saves and the template is whole again. (The TASK key
                # stays optimize_keys — the task method takes the level, and
                # a headless caller's legacy True still means what it did.)
                "object_name": "optimize_level",
                "set_row_label": "Optimize Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Keys",
                    body="Remove animation data the deliverable does not need, "
                    "at the chosen level.",
                    bullets=[
                        "<b>OFF</b> — ship every curve and key as authored.",
                        "<b>Static Curves Only</b> — delete curves whose value "
                        "never changes; every surviving curve keeps all of its "
                        "keys. The conservative rung: nothing carrying motion "
                        "is touched.",
                        "<b>Static + Flat Keys</b> — also drop the redundant "
                        "interior keys of a flat run.",
                        "<b>+ Simplify (lossy)</b> — also drop keys whose "
                        "absence changes the curve by less than the tolerance. "
                        "That is a judgement about the tolerance, so the "
                        "result is worth eyeballing.",
                        "<b>Reduce To Extremes</b> — reduce smooth "
                        "curves to their endpoints, peaks, valleys and hold "
                        "boundaries, with tangents refit to the baked motion. "
                        "The one to reach for after <b>Smart Bake</b>: a "
                        "per-frame bake has no redundant flat keys for the "
                        "other levels to find. It thins a bake, it does not "
                        "reverse one — that is Smart Bake's <b>Unbake</b>.",
                    ],
                    notes=[
                        "Stepped tangents are preserved at every level.",
                        "Also sets the level used inside <b>Smart Bake</b> — "
                        "that pass reaches the baked override-layer curves "
                        "this one cannot.",
                        "Whether the scene keeps this is <b>Animation Output</b>'s "
                        "call; by default the curves are restored after the write.",
                        "<b>Reduce To Extremes</b> rewrites tangents through the API, which "
                        "bypasses Maya's undo queue — so at <b>Animation "
                        "Output: Scene Keys (In Place)</b> it is not reversible "
                        "with Ctrl+Z. At the default it is, because the export's "
                        "own curve snapshot is restored either way.",
                    ],
                ),
                "add": self._optimize_keys_options,
                # Applied after 'add' (which lands on index 0): index 2 is
                # Static + Flat Keys, exactly what the old checked box did.
                "setCurrentIndex": 2,
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
                        "Whether the scene keeps this is <b>Animation Output</b>'s "
                        "call; by default the curves are restored after the write. "
                        "Kept in place, the insert bypasses Maya's undo queue — "
                        "revert with AnimUtils.untie_keyframes rather than Ctrl+Z.",
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
                        "Whether the scene keeps this is <b>Animation Output</b>'s "
                        "call; by default the curves are restored after the write.",
                    ],
                ),
                "setChecked": False,
            },
            "set_bake_animation_range": {
                "widget_type": "ComboBox",
                "group": "Animation",
                # New objectName for the same reason as optimize_level above:
                # the retired checkbox's `true` would restore as index 1 here.
                # Index 1 happens to be Auto — the right default — but that is
                # a coincidence, not a migration, and the next inserted row
                # would end it.
                "object_name": "bake_range",
                "set_row_label": "Bake Range",
                "setToolTip": TooltipFormat.fmt(
                    title="Bake Range",
                    body="Which frames the FBX bakes — overriding the range "
                    "stored in the FBX preset, whose factory value (1-48) is "
                    "not the scene's anything.",
                    bullets=[
                        "<b>OFF</b> — keep the preset's range.",
                        "<b>Auto (Shots → Keyframes)</b> — the span of the "
                        "shots declared in the <b>Shots</b> panel; a scene "
                        "with no shots falls back to the keyframe extent. "
                        "With shots authored, this is what keeps animation "
                        "outside them out of the deliverable.",
                        "<b>Keyframe Extent</b> — the first and last keyframe "
                        "of the exported objects (start floored, end ceiled).",
                        "<b>Scene Animation Range</b> — the scene's authored "
                        "range, not the playback slider. What an export "
                        "through Maya's own dialog gets by default.",
                    ],
                    notes=[
                        "Applies only when Bake Animation is enabled in the FBX "
                        "export settings; otherwise it is skipped.",
                        "Runs last, so it measures the final state of the "
                        "curves — and every mode is widened to cover the clips "
                        "<b>Export Shots as Animation Takes</b> declared, so no "
                        "choice here can ship metadata describing animation the "
                        "file does not contain.",
                        "A GLB rebuilds its clips by slicing the whole-timeline "
                        "stack, so this is what decides how much of the timeline "
                        "it has to slice — <b>Auto</b> is the setting that makes "
                        "a GLB cover exactly the shots.",
                        "The preset's range is restored after the write.",
                    ],
                ),
                "add": self._bake_range_options,
                # Applied after 'add' (which lands on index 0): index 1 is
                # Auto. With shots declared this reproduces what the old
                # default pair did (the split's union won); with none, the
                # keyframe extent the old checkbox measured. The one behavior
                # change is a scene WITH shots and the split switched off —
                # which now clamps to them instead of shipping everything.
                "setCurrentIndex": 1,
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
                        "Additive: the exporter keeps the unsplit whole-timeline "
                        "take alongside the split ones, so turning this on never "
                        "costs you the continuous clip.",
                        "This is <b>not</b> what ships the shot metadata — "
                        "<b>Export Scene Data Node</b> already does that, and the "
                        "two share one refresh.",
                        "The <b>GLB</b> does not take its clips from here: Maya's "
                        "split drops a curve that has no key inside a shot, so the "
                        "converter rebuilds each shot from the whole-timeline take "
                        "instead (which ships as <b>FULL_SEQUENCE</b>). A GLB-only "
                        "export can leave this off — what makes its clips cover "
                        "exactly the shots is <b>Bake Range: Auto</b>.",
                        "Forces Bake Animation on, and guarantees a range covering "
                        "the takes it declares; <b>Bake Range</b> then widens to "
                        "cover them, so the two cannot disagree. Both are restored "
                        "after the write.",
                    ],
                ),
                # Default ON. It was off because splitting reads like a
                # destructive choice about the timeline, and it is not: measured
                # on Maya 2025, the FBX ships the whole-range take PLUS one per
                # shot (and so does the converted GLB). Off, a scene with shots
                # exported metadata describing clips the file did not contain --
                # the one combination that is wrong in both deliverables at once.
                # A scene with no shots is unaffected: the task no-ops.
                "setChecked": True,
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
                "setPlaceholderText": "Group names to ignore (comma-separated, wildcards ok)",
                "setToolTip": TooltipFormat.fmt(
                    title="Ignore Groups",
                    body="Comma-separated name patterns of top-level groups to "
                    "drop from the export set.",
                    notes=[
                        "Example: temp, proxy",
                        "Wildcards: <b>*</b> any run of characters, <b>?</b> a "
                        "single one &mdash; <b>temp*</b> catches temp_01 and "
                        "tempRig, <b>*_proxy</b> catches hull_proxy.",
                        "A pattern with no wildcard matches that exact name.",
                        "Leave empty to skip.",
                        "Matching ignores case unless the <b>Aa</b> button beside "
                        "the field is on.",
                    ],
                ),
                "setText": "temp",
                "value_method": "text",
            },
            # NOTE: `version` is a UI-only field — consumed by SceneExporter
            # (pop'd before run_tasks), never executed by the task pipeline.
            # The output format (FBX / GLB / FBX+GLB) is the same kind of UI-only
            # field, but it lives in its own `cmb004` Format combo rather than the
            # task list, so it isn't defined here.
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
                        "The extension is added automatically — do not include {ext}.",
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
                    body="Fails the export when the scene contains file references.",
                    notes=[
                        "Scans the whole scene, not just the export set.",
                        "Import the reference (or remove it) to pass.",
                    ],
                ),
                "setChecked": True,
            },
            "check_output_writable": {
                "widget_type": "QCheckBox",
                "group": "General",
                "setText": "Check Output File Is Writable",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Output File Is Writable",
                    body="Fails the export when a file it is about to write is "
                    "held open by another process.",
                    notes=[
                        "Windows will not let anything replace a file while a "
                        "viewer, a preview or an engine has it open.",
                        "Runs before the first scene change, so a locked "
                        "destination costs milliseconds instead of the whole "
                        "pipeline — the write is the LAST thing an export does.",
                        "Names the process to close whenever Windows will say.",
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
            "check_duplicate_names": {
                "widget_type": "ComboBox",
                "group": "Hierarchy & Naming",
                "set_row_label": "Duplicate Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Names",
                    body="Fails the export when two nodes in the export set "
                    "share a short name. The dial is how wide it looks — each "
                    "step includes the one above it.",
                    bullets=[
                        "<b>Locators</b> — attach points and sockets, which "
                        "whatever consumes them downstream matches by name.",
                        "<b>Locators &amp; Joints</b> — adds the skeleton the "
                        "FBX writes as bones; duplicate bone names break "
                        "skinning and retargeting on import.",
                        "<b>Connected &amp; Animated</b> — adds every transform "
                        "with an incoming connection on a transform or "
                        "visibility channel: constraints, keys, drivers, "
                        "expressions, IK. Their names are what the take and "
                        "metadata bindings resolve against.",
                        "<b>All Export Objects</b> — every node in the set, "
                        "plain groups included. The strictest setting: nested "
                        "groups sharing a name are legal in Maya and harmless "
                        "in the FBX, so expect noise.",
                    ],
                    notes=[
                        "Compares short names, so nodes under different parents "
                        "still collide — which is what a consumer matching them "
                        "by name downstream will see.",
                        "<b>OFF</b> disables the check.",
                    ],
                ),
                "add": self._duplicate_name_options,
                # Applied after 'add' (which lands on index 0): Locators is the
                # scope the check shipped with as a plain checkbox.
                "setCurrentIndex": 1,
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
            "check_sheared_local_transforms": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check For Sheared Local Transforms",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Sheared Local Transforms",
                    body="Fails the export when a node's local matrix is "
                    "sheared. FBX and glTF store animated nodes as "
                    "translate/rotate/scale, which cannot represent shear, so "
                    "it is silently dropped.",
                    notes=[
                        "Needs no authored shear: a squash/stretch joint chain "
                        "gives every joint the same non-uniform world scale, "
                        "and the LOCAL matrix between two differently-oriented "
                        "joints is then sheared. World matrices look clean.",
                        "The residual compounds down a chain. Measured on a "
                        "wire-loom rig: 47% stretch put the last joint 7.5 cm "
                        "off; 11% stayed within 0.5 cm.",
                        "The <b>Flatten Sheared Chains</b> task re-anchors "
                        "the flagged joints automatically; otherwise reduce "
                        "the stretch at the source.",
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
                        "Callers can override it with a 'tolerance' keyword argument.",
                    ],
                ),
                "setChecked": True,
            },
            "check_default_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
                "setText": "Check For Default Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Default Materials",
                    body="Fails the export when a mesh in the export set is on "
                    "Maya's fallback shader (<b>initialShadingGroup</b> / "
                    "lambert1), or on no shading group at all.",
                    notes=[
                        "Such a mesh still exports: it arrives as "
                        "'Default_Material' — untextured, and with no normal "
                        "map — so it renders wrong only in the deliverable.",
                        "Reports per SHAPE, so a per-face assignment that "
                        "leaves part of a mesh on the default is named too.",
                        "Assign a material, or drop the object from the export "
                        "set, to pass.",
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
                    "materials, a committed lightmap, or a scene reference does "
                    "not resolve on disk.",
                    notes=[
                        "Resolves each path twice: the way Maya resolves it, and "
                        "the way the FBX plug-in will locate it at write time.",
                        "Catches what would otherwise surface after the export as "
                        "'The following texture(s) will not be embedded'.",
                        "Lightmaps have no file node — the bake marker records "
                        "the folder it was committed from. A map that folder no "
                        "longer holds is looked for where the GLB conversion "
                        "looks (the project's texture folders, then all of "
                        "sourceimages); found elsewhere it ships and is noted, "
                        "found nowhere it fails the export.",
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
                    title="Max Texture File Size (MB)",
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
            # Not a pipeline check: the pop in ``SceneExporter.perform_export``
            # turns this row into the flag that arms the POST-write pass
            # (:meth:`verify_deliverables`), the same idiom the Texture/Animation
            # Output modes ride. It lives here because it is a check in the
            # user's sense -- and because "Override Checks" should switch it off
            # with the rest -- but it never reaches the task dispatcher, and it
            # is the one entry in this map with no ``check_`` method behind it.
            "verify_deliverables": {
                "widget_type": "QCheckBox",
                "group": "Deliverable (after the write)",
                "setText": "Verify The Written File",
                "setToolTip": TooltipFormat.fmt(
                    title="Verify The Written File",
                    body="Re-opens the FBX/GLB that just shipped and runs "
                    "pythontk's file-level gates over the bytes on disk — a "
                    "truncated container, a take the FBX dropped, a NaN that "
                    "reached an accessor, a clip whose span disagrees with its "
                    "take.",
                    notes=[
                        "Reports only. The file is already written, so a failure "
                        "is logged per gate at ERROR and never unwrites the "
                        "deliverable or flips the export's verdict.",
                        "Off by default because it is the one pass that costs "
                        "time proportional to the FBX rather than the scene "
                        "(seconds and hundreds of MB of heap on a large file); "
                        "arm it for a delivery, not for every iteration.",
                        "Reads the FBX and the GLB independently, so a GLB-only "
                        "export never parses the temp FBX it is about to "
                        "discard.",
                    ],
                ),
                "setChecked": False,
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
