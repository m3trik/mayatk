# !/usr/bin/python
# coding=utf-8
"""Per-run state and the scope helpers every Scene Exporter task and check
shares -- the base of the phase mixins (``_task_scene``, ``_task_textures``,
``_task_animation``, ``_task_checks``).
"""

import os
from typing import Optional, Dict, Any, List, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # the surface must import without Maya (registry, docs tooling)
    cmds = mel = None
import pythontk as ptk

# From this package:
from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.mat_utils._mat_utils import MatUtils


class _TaskDataMixin:
    """Per-run state and the scope helpers every task and check shares.

    The base of each phase mixin (``_task_scene``, ``_task_textures``,
    ``_task_animation``, ``_task_checks``): the run's modes, the markers one
    task leaves for a later one, the caches derived from the export set, and
    the texture-pass resolvers (container, size clamp, assessment) a task and
    its paired check judge through.
    """

    #: The modes of the run in flight -- adopted by :meth:`begin_run` from
    #: what ``perform_export`` parsed (``ptk.ExportRun.from_tasks``). A
    #: manager driven directly reads the defaults: no deliverable path, an
    #: FBX run, every pass at its off / staged setting.
    run: ptk.ExportRun = ptk.ExportRun()

    # Markers one task leaves for a later one (or for the post-write API),
    # declared with the value a fresh run has and reset by begin_run -- never
    # as a side effect of assigning ``objects``, which tasks do mid-run.
    #: The Animation Clips choice, read by ``create_glb`` after the write.
    _clip_mode: str = "both"
    #: The scene records ``export_data_node`` published this run (``None`` until
    #: a task publishes; ``apply_declared_takes`` publishes when none did).
    _scene_snapshot: Optional[Any] = None  # the ptk.ExportSnapshot this run published
    #: ``check_hierarchy_vs_existing_fbx`` ran this run, so the sidecar write
    #: rolls its baseline forward.
    _hierarchy_check_ran: bool = False
    #: The diff that check found, for the sidecar to record.
    _hierarchy_last_diff: Optional[Dict[str, Any]] = None
    #: The frame spans claimed through ``_require_range_coverage``, which
    #: ``set_bake_animation_range`` widens to cover.
    _required_range_coverage: Optional[Tuple[float, float]] = None
    #: The delivery-only-container note's once-per-run throttle.
    _delivery_only_clamp_said: bool = False
    #: ``smart_bake``'s session manifest and (a bake recorded without one)
    #: its override layer, undone by the restore the task stages.
    _bake_session_id: Optional[str] = None
    _bake_override_layer: Optional[str] = None
    #: ``convert_textures``' network snapshot, live for the length of its
    #: staged rewire (the texture checks link nodes through it).
    _texture_network_snapshot: Optional[Any] = None
    #: Caches derived from ``objects``; None = not computed this run.
    _cached_materials: Optional[List[str]] = None
    _cached_export_file_nodes: Optional[List[str]] = None
    #: ``(first, last)`` key time of the export, ``()`` for none, None = not
    #: asked this run -- what every range consumer actually needs, without
    #: the key list a full scan used to marshal.
    _key_range: Optional[tuple] = None
    #: The flatten task's scan and outcome, for the shear check to reuse
    #: (see ``_sheared_offenders_after_flatten``). Per-run: ``begin_run``.
    _shear_verdict: Optional[Dict[str, Any]] = None
    _assess_cache: Dict[tuple, Any]

    @property
    def export_path(self) -> str:
        """The deliverable this run writes (``run.export_path``).

        Empty for a manager driven directly, which every reader treats as
        "nothing durable to stage beside".
        """
        return self.run.export_path

    @export_path.setter
    def export_path(self, value: str) -> None:
        """DEPRECATED, for one release: write ``run.replace(export_path=...)``.

        A plain attribute through 0.14.20. The run is frozen, so an assignment
        replaces it with the new path and says what to write instead.
        """
        self.logger.warning(
            "Assigning TaskManager.export_path is deprecated and stops working "
            "next release: assign run = run.replace(export_path=...) instead."
        )
        self.run = self.run.replace(export_path=value)

    def begin_run(self, run: ptk.ExportRun) -> None:
        """Adopt *run*'s modes and reset every per-run marker -- the ONE reset.

        ``perform_export`` calls this after resolving the output path and
        before seeding the export set, so a run with no task checked still
        starts clean: left standing, a previous run's Animation Clips choice
        would convert this run's GLB, and its claimed frame spans would widen
        this run's bake range.
        """
        self.run = run
        self._clip_mode = "both"
        self._scene_snapshot = None
        self._hierarchy_check_ran = False
        self._hierarchy_last_diff = None
        self._required_range_coverage = None
        self._delivery_only_clamp_said = False
        self._bake_session_id = None
        self._bake_override_layer = None
        self._texture_network_snapshot = None
        self._shear_verdict = None
        self._invalidate_material_caches()
        self._invalidate_keyframe_cache()

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

        Binds the per-run ``run.texture_file_type`` mode (the Texture File Type
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
            template, self.run.texture_file_type
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
                if not self._delivery_only_clamp_said:
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

    #: ``run.texture_max_size`` sentinel: clamp to the active template's own
    #: :class:`~pythontk.DeliveryBudget` (``enforce_budget``) rather than to a
    #: pixel ceiling. Aliases the shared resolver's own sentinel so the combo
    #: row, the exporter and the optimizer cannot drift apart on its value.
    TEXTURE_MAX_SIZE_TEMPLATE = ptk.MapOptimizer.SIZE_CLAMP_TEMPLATE

    def _texture_size_clamp(self, template: Optional[str]) -> Dict[str, Any]:
        """The resize rule the optimization pass applies under *template*.

        Binds the per-run ``run.texture_max_size`` mode (the Optimize Textures
        combo's size half, stamped by ``perform_export`` — never a dispatched
        task) to the shared resolver, which owns the rule: see
        :meth:`pythontk.MapOptimizer.resolve_size_clamp` for the modes and
        why the budget's POT flag is deliberately not adopted.

        Returns:
            dict of keyword arguments for ``MapOptimizer.assess`` /
            ``optimize_map``. Empty when no clamp applies.
        """
        return ptk.MapOptimizer.resolve_size_clamp(
            self.run.texture_max_size, template, logger=self.logger
        )

    #: Containers a GLB can embed: glTF-core (``MeshConvert.IMAGE_MIME_TYPES``,
    #: the SSoT for what needs no extension) plus the two ``optimize_glb_textures``
    #: declares an extension for — WebP (``EXT_texture_webp``) and KTX2
    #: (``KHR_texture_basisu``). Everything else the Texture File Type dial offers
    #: is a scene-side container only, so the GLB falls back to PNG.
    GLB_CARRIER_FORMATS = frozenset(
        [e.lstrip(".") for e in ptk.MeshConvert.IMAGE_MIME_TYPES] + ["webp", "ktx2"]
    )

    #: Texture File Type token for KTX2 PLUS a core-readable PNG/JPEG twin of
    #: every map (``optimize_glb_textures(ktx2_fallback=True)``). The export
    #: parses it into the ``ktx2`` container and the per-run ``run.ktx2_fallback``
    #: flag, so no other consumer ever compares it.
    KTX2_WITH_FALLBACK = ptk.ExportRun.KTX2_WITH_FALLBACK

    def _glb_texture_params(self) -> Dict[str, Any]:
        """``optimize_glb_textures`` kwargs for this run's GLB deliverable.

        The GLB's half of the panel's two GENERAL texture dials — it has no
        dials of its own — resolved against
        :meth:`pythontk.MeshConvert.web_delivery_texture_params`, the ONE
        definition of what a web deliverable's textures are. Each dial
        *overrides* that policy; neither has to restate it:

        * **Container** — Texture File Type (``run.texture_file_type``), when it
          names something :attr:`GLB_CARRIER_FORMATS` covers. Anything else
          (and "Original") takes the policy's container, because a GLB from
          this panel IS the web deliverable: the FBX and USD formats beside it
          are the interchange ones. ``KTX2 + PNG/JPEG`` is the KTX2 container
          plus ``ktx2_fallback`` (``run.ktx2_fallback``): a core-readable copy of
          each map beside its KTX2, for a GLB that must also open in Blender,
          Unreal or stock Unity. Plain ``KTX2`` takes the policy's KTX2 alone.
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
        file_type = (self.run.texture_file_type or "").lower().lstrip(".")
        optimize = bool(self.run.optimize_textures)

        carrier = file_type if file_type in self.GLB_CARRIER_FORMATS else ""
        if file_type and not carrier:
            self.logger.info(
                f"GLB textures: {file_type.upper()} is not a container glTF can "
                f"embed — the GLB carries "
                f"{ptk.MeshConvert.WEB_DELIVERY_FORMAT} (the scene's own maps "
                f"still use {file_type.upper()})."
            )

        # ``or None`` on every part: an unset dial is "unspecified", which the
        # shared resolver answers with the policy, NOT a falsy value it would
        # read as a decision (0 there means "keep every pixel" — exactly the
        # 280 MB outcome this method exists to stop shipping by default).
        return ptk.MeshConvert.web_delivery_texture_params(
            image_format=self._glb_format_id(carrier) if carrier else None,
            max_size=(self._glb_max_size() if optimize else 0) or None,
            ktx2_fallback=bool(self.run.ktx2_fallback) or None,
            # The two GLB-only dials ride the same policy call; an
            # unset dial is None so the policy answers, as above.
            secondary_max_size=self.run.secondary_max_size or None,
            uastc_rdo=self.run.uastc_rdo or None,
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
        template = self.run.texture_template
        clamp = self._texture_size_clamp(template)
        if clamp.get("enforce_budget"):
            return int(ptk.OutputTemplates.budget(template).max_size or 0)
        return int(clamp.get("max_size") or 0)

    def _texture_size_clamp_desc(self, template: Optional[str]) -> str:
        """Human-readable form of :meth:`_texture_size_clamp` for log lines."""
        return ptk.MapOptimizer.describe_size_clamp(
            self.run.texture_max_size, template, logger=self.logger
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
        """Whether the export set carries any key -- a count, never the times.

        Read from a standing cache when one exists; otherwise ONE
        ``keyframeCount`` query (``AnimUtils.has_keyframes``). It used to
        populate the whole key-time list to answer, which after a bake meant
        marshalling millions of dense keys -- the framerate check alone
        cost 12 s of a production export for a yes/no (2026-09-13).
        """
        if self._key_range is not None:
            return bool(self._key_range)
        return AnimUtils.has_keyframes(self._exported_objects())

    def _keyframe_range(self) -> Optional[Tuple[float, float]]:
        """``(first, last)`` key time of the export, or ``None`` -- cached.

        The bake range, the tie's bookends and the shear scan's frame grid
        need only the ends; ``AnimUtils.keyframe_range`` answers from the
        curves' ends without listing what lies between.
        """
        if self._key_range is None:
            self._key_range = AnimUtils.keyframe_range(self._exported_objects()) or ()
        return self._key_range or None

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

    def _protect_scene_animation(self, keeps_edits: bool = True) -> bool:
        """Capture the export set's curves so the write can edit them freely.

        The Animation Output gate's whole mechanism, and the animation twin of
        the texture pass's staging: every task that edits keys calls this
        FIRST, the edits are made and read by the write, and one deferred
        restore (post-write, so the FBX and any GLB conversion both see the
        edited curves) puts the scene back.

        Idempotent by construction rather than by a flag: staging is keyed and
        first-wins (:meth:`stage_deferred_restore`), so every task calling this
        (the flatten, the bake, optimize, snap, tie) takes ONE snapshot -- the
        one from before the first of them ran, which is the only correct one
        to restore.

        Parameters:
            keeps_edits: Whether the caller's key edits stay in the scene in
                write-back mode, and so are recorded as kept there
                (``TaskFactory.record_kept_edit``). The flatten passes False:
                its own restore reverses its fitted curves in every mode.

        Returns:
            True when the animation is protected -- either because this call
            staged the snapshot or because an earlier task already did. False
            in write-back mode, where the edits are the point.
        """
        if self.run.animation_write_back:
            if keeps_edits:
                self.record_kept_edit("key edits")
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
        """Drop the cached key range (``_key_range``).

        Every task that moves or deletes keys must call this: a later task
        reading the cache would otherwise act on pre-edit ends -- e.g.
        ``tie_all_keyframes`` bookending to the fractional extremes
        ``snap_keys_to_frame`` just removed, re-creating the exact keys the
        snap existed to fix (and then failing ``check_floating_point_keys``).
        """
        self._key_range = None

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
        if self._cached_materials is None:
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
        cached = self._cached_export_file_nodes
        if cached is not None:
            return cached

        materials = [m for m in self._get_all_materials() if cmds.objExists(m)]
        if not materials:
            self._cached_export_file_nodes = []
            return self._cached_export_file_nodes

        history = cmds.listHistory(materials, pruneDagObjects=True) or []
        # Ordered dedupe: a set's order follows the process's hash seed, so
        # every task and check reporting over these nodes read differently
        # run to run.
        self._cached_export_file_nodes = list(
            dict.fromkeys(cmds.ls(history, type="file") or [])
        )
        return self._cached_export_file_nodes
