# !/usr/bin/python
# coding=utf-8
"""High-level lightmap baking workflow for Maya -> game engines (Unity-first).

:class:`LightmapBaker` is the *workflow orchestrator*. It owns no low-level bake
or UV logic; it composes the ecosystem primitives into one lightmap pipeline:

* :meth:`UvUtils.create_lightmap_uvs` -- packed, non-overlapping lightmap UV (UV2)
* :meth:`TextureBaker.bake` ``(uv_set=)`` -- Arnold RTT into that set. That is
  the generic bake primitive (``mat_utils.texture_baker``) and is reusable on
  its own; the lightmap workflow lives here, the bake mechanics live there.
* :meth:`ImgUtils.dilate_image` -- gutter fill from the RTT alpha coverage mask
* ``MatUtils`` / ``UvUtils`` -- non-destructive commit (unlit material, UV0)

Two bake levels, both non-destructive and exposed in the panel:

* **Lighting only** (default) -- :meth:`bake_separated` bakes white-card
  irradiance (lighting only) onto a separate UV channel (index 1) and
  :meth:`commit_lightmap` records it. The object's full PBR material and its
  texture UV0 are **kept untouched** -- the engine composites
  ``albedo x lightmap``. The export is self-contained: mesh UV2 samples the
  map (atlas rects are repacked into the UVs by :meth:`pack_atlas`), so it
  works in any engine; a small manifest also rides the FBX on the shared
  ``data_export`` carrier (no sidecar file) so Unity's *native* lightmap slots
  can be auto-bound by the optional unitytk editor helper.
  Reversible via :meth:`revert_lightmap`.
* **Fused** -- :meth:`bake_fused` bakes albedo x lighting into one HDR map and
  :meth:`commit_unlit` makes it the primary UV (UV0) + assigns an unlit
  material, so the mesh exports to a **stock unlit shader, no sidecar** -- at the
  cost of dropping normals/specular and re-lighting. The lowest-end / fully
  baked option. Reversible via :meth:`revert_unlit`.

:meth:`revert` undoes whichever level an object is in (used by the panel and
before a re-bake).

Quality tiers come from :meth:`from_preset` (pythontk ``PresetStore``). HDR EXR
throughout; 8-bit/encoded targets are a later (mostly engine-side) stage. For the
bake primitive alone (no lightmap workflow), use :class:`TextureBaker` directly.
"""
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

import pythontk as ptk

try:  # UI-only helper; keep the headless workflow import clean if uitk is absent
    from uitk.widgets.mixins.tooltip_mixin import fmt
except ImportError:
    fmt = None

from mayatk.mat_utils.texture_baker import TextureBaker
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics


class LightmapBaker(ptk.LoggingMixin):
    """Orchestrate the lightmap workflow: bake -> dilate -> engine export prep.

    Usage::

        baker = LightmapBaker.from_preset("desktop")          # or (resolution=)
        baker.revert_unlit(objects)                            # bake the SOURCE mat
        out = baker.bake_fused(objects)                        # {obj: exr_path}
        baker.commit_unlit(out)                                # lightmap->UV0, unlit
        # The object now shows the baked result and exports correctly to Unity;
        # nothing is destroyed -- baker.revert_unlit() puts the source material /
        # UV order back (the restore data is stamped on the mesh, so revert works
        # across save/reload and from a fresh baker instance).

    The injected/created :class:`TextureBaker` must emit EXR (the default does);
    the alpha-driven seam dilation depends on Arnold's float RGBA output.
    """

    # Dynamic string attr stamped on a committed shape: a JSON restore record
    # (original UV-set order, shading snapshot, created node names). Persisting
    # it on the mesh -- not in memory -- is what makes commit non-destructive
    # across save/reload and independent of the baker instance.
    COMMIT_ATTR: str = "lightmapCommit"

    # Per-shape JSON marker for a lighting-only ("separated") lightmap: which
    # map, UV set, intensity. Non-destructive bookkeeping (the material and UVs
    # are untouched) -- it records what the engine should composite and what to
    # republish into the export manifest; cleared by :meth:`revert_lightmap`.
    LIGHTMAP_INFO_ATTR: str = "lightmapInfo"

    # ``data_export`` channel: a scene-wide JSON manifest of every lighting-only
    # lightmap, regenerated from the per-shape markers. Rides the FBX as a user
    # property (:meth:`DataNodes.set_export_string`) -- purely informational
    # unless consumed; unitytk's optional editor helper reads it to auto-bind
    # Unity's *native* lightmap slots ("sidecar benefits, no sidecar file").
    LIGHTMAP_METADATA: str = "lightmap_metadata"
    LIGHTMAP_METADATA_VERSION: int = 1

    def __init__(
        self,
        resolution: int = 1024,
        samples: int = 5,
        baker: Optional[TextureBaker] = None,
        gi_depth: int = 3,
        gi_samples: int = 4,
    ):
        super().__init__()
        self.resolution = resolution
        self.samples = samples
        # GI quality is a scene render setting, not an RTT flag: without
        # pinning it, every bake runs at Arnold's 1-bounce / 2-sample scene
        # defaults (or whatever the user last rendered with). Multi-bounce
        # indirect is the single biggest lightmap quality lever, so it is a
        # first-class dial here and in the presets.
        self.gi_depth = gi_depth
        self.gi_samples = gi_samples
        # Dependency-injected so tests / callers can swap the bake backend;
        # the default targets the fused-HDR path (Arnold + EXR). An injected
        # baker keeps its own render_settings (caller's responsibility).
        self.baker = baker or TextureBaker(
            resolution=resolution,
            samples=samples,
            file_format="exr",
            render_settings={
                "GIDiffuseDepth": gi_depth,
                "GIDiffuseSamples": gi_samples,
            },
        )
        # One no-lights warning per baker instance (bake_separated fans out to
        # N single-object bake_fused calls; warning each would spam the log).
        self._warned_no_lights = False

    # ------------------------------------------------------------------
    # Quality-tier presets (pythontk PresetStore: built-in + user tiers)
    # ------------------------------------------------------------------

    @staticmethod
    def preset_store() -> "ptk.PresetStore":
        """Shared store of lightmap quality presets (built-in + user tiers).

        Built-ins ship as JSON in this subpackage's ``presets/`` dir; user
        presets live under the consolidated config root (the same one uitk's
        ``PresetManager`` uses), so headless and GUI paths resolve to one place.
        """
        builtin = os.path.join(os.path.dirname(__file__), "presets")
        return ptk.PresetStore("lightmap", package="mayatk", builtin_dir=builtin)

    @classmethod
    def from_preset(cls, name: str, **overrides) -> "LightmapBaker":
        """Construct a baker from a named quality preset.

        A preset is a small JSON dict; only the quality dials need storing
        (``resolution``, ``samples``, ``gi_depth``, ``gi_samples``) -- the rest
        of the pipeline derives from resolution (gutter padding, dilation
        width) or has a sound default. ``overrides`` win over the preset (e.g.
        ``from_preset("quest", resolution=1536)``); extra preset keys
        (``description``) are ignored.

        Built-ins: ``preview`` (256/2), ``quest`` (1024/4), ``desktop`` (2048/8).
        """
        store = cls.preset_store()
        if not store.exists(name):
            raise ValueError(
                f"Unknown lightmap preset {name!r}. Available: {store.list()}"
            )
        data = {**store.load(name), **overrides}
        # Pass only the keys the preset provides; absent ones fall back to the
        # constructor's own defaults (no duplicated default literals to drift).
        kwargs = {
            k: int(data[k])
            for k in ("resolution", "samples", "gi_depth", "gi_samples")
            if k in data
        }
        return cls(**kwargs)

    def bake_fused(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        uv_set: Optional[str] = None,
        map_size: Optional[int] = None,
        create_uvs: bool = True,
        dilate: bool = True,
        dilate_iterations: Optional[int] = None,
        alpha_threshold: float = 1e-3,
        prefix: str = "lightmap_",
        suffix: str = "",
        backend: str = "arnold",
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        stem: Optional[Any] = None,
        shader: Optional[str] = None,
        batch: bool = False,
    ) -> Dict[str, str]:
        """Bake a fused HDR lightmap per object into the UV2 channel.

        Parameters:
            objects: Mesh transforms. Defaults to current selection.
            output_dir: Output directory (created if missing). Defaults to
                :meth:`TextureBaker.bake`'s ``<scene_dir>/baked_lighting``.
            uv_set: Lightmap UV set name. Default ``LIGHTMAP_UV_SET``.
            map_size: UV-padding target for ``create_lightmap_uvs``. Defaults
                to ``resolution`` so the gutter matches the bake resolution.
            create_uvs: Ensure a packed lightmap UV2 first (reuses a valid one).
            dilate: Edge-pad island gutters using the RTT alpha coverage mask.
            dilate_iterations: Gutter width in px. ``None`` -> a resolution-
                scaled default; ``-1`` -> fill all background.
            alpha_threshold: Coverage cutoff; ``alpha > threshold`` is "baked".
            prefix: Output filename prefix wrapped around the object name.
            suffix: Output filename suffix (e.g. ``"_Lightmap"`` to follow the
                ``<base>_Lightmap`` texture-set convention). Forwarded to
                :meth:`TextureBaker.bake`.
            backend: Bake backend (``"arnold"`` for HDR/coverage; falls back
                with a warning if mtoa is unavailable, but dilation then no-ops
                since there is no alpha channel).
            on_progress: Forwarded to :meth:`TextureBaker.bake` -- a
                ``(done, total, name) -> bool`` per-object callback (return
                ``False`` to cancel) so a UI can drive a progress bar.
            stem: Output base-name resolver forwarded to :meth:`TextureBaker.bake`.
                ``None`` defaults to :meth:`_texture_set_stem` (name the lightmap
                after the object's material texture set, e.g.
                ``Plants_Metal_Base_01_Lightmap``, not the long node name).
            shader: Optional bake-time shader override forwarded to
                :meth:`TextureBaker.bake` (Arnold ``-shader``; applies per
                shape being baked, neighbors keep their real materials --
                :meth:`bake_separated` passes its white card through this).
            batch: Bake all objects in one RTT call (forwarded to
                :meth:`TextureBaker.bake`; measured 7.45x on multi-object
                scenes, falls back per-object on duplicate shape leaf names).

        Returns:
            ``{long_object_name: lightmap_path}`` for each successful bake.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        if objects is None:
            objects = cmds.ls(selection=True, long=True, transforms=True) or []
        if not objects:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        # A prior atlas commit repacked the lightmap UVs into an atlas rect;
        # restore the unit square before baking (else the bake would fill only
        # that fraction of the map, and re-packing would compound the shrink).
        self._restore_atlased_uvs(objects)

        self._warn_if_unlit_scene()

        uv_set = uv_set or UvDiagnostics.LIGHTMAP_UV_SET
        map_size = map_size or self.resolution

        if create_uvs:
            UvUtils.create_lightmap_uvs(
                objects, uv_set=uv_set, map_size=map_size, quiet=True
            )

        # A real scene's lightmap set is not named uniformly: create_lightmap_uvs
        # reuses a pre-existing one under its own name (UV2, lightmapUV, ...).
        # Resolve each object's actual set so the bake targets the right channel
        # per object instead of a single hardcoded name.
        targets: Dict[str, str] = {}
        for obj in objects:
            long = cmds.ls(obj, long=True)
            if not long:
                continue
            shape = NodeUtils.get_shape(long[0])
            found = UvDiagnostics.find_lightmap_uv_set(shape) if shape else None
            targets[long[0]] = found or uv_set

        result = self.baker.bake(
            objects,
            output_dir=output_dir,
            prefix=prefix,
            suffix=suffix,
            backend=backend,
            uv_set=targets,
            on_progress=on_progress,
            # Name the lightmap after the object's material texture set by
            # default (a callable -- the real materials stay assigned even
            # during a shader-override bake, so it resolves correctly).
            stem=stem if stem is not None else self._texture_set_stem,
            shader=shader,
            batch=batch,
        )

        if dilate and result:
            if dilate_iterations is None:
                # A bounded gutter is enough for mip safety; full fill (-1) is
                # opt-in. Scales with resolution: 512->8, 1024->16, 4096->64.
                dilate_iterations = max(8, self.resolution // 64)
            for name, path in result.items():
                try:
                    self._dilate_lightmap(path, alpha_threshold, dilate_iterations)
                except Exception as e:  # never fail the whole bake on one image
                    self.logger.warning("Dilation skipped for %s: %s", path, e)

        return result

    def bake_separated(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        prefix: str = "lightmap_irr_",
        batch: bool = True,
        **kwargs,
    ) -> Dict[str, str]:
        """Bake a **lighting-only** (white-card) irradiance lightmap per object.

        The opt-in *separated* path (guideline #1): albedo stays on UV1, the
        lightmap on UV2 holds lighting only, to be combined ``albedo x lightmap``
        by Unity's built-in lightmap system or a custom shader. This is **not**
        the no-sidecar fused path -- it trades a shader/import dependency for an
        albedo-independent lightmap.

        Mechanism: the bake runs with a true-white Lambert card (Kd = 1) passed
        as Arnold's ``-shader`` override, so each map captures diffuse
        irradiance normalized to white albedo (Phase 0b measured white-card
        beats divide-by-albedo, which is catastrophic on dark albedo). The
        override applies **per shape being baked** (measured, mtoa 5.4.5):
        every other object -- selected or not -- keeps its real material during
        that shape's render, so indirect light carries the true scene
        albedo/color (correct bounce energy, color bleed, emissive/transparent
        neighbors), with **no material swapping at all** -- the scene's shading
        is never touched. The only white-normalized term left is an object's
        own self-interreflection. Everything else -- UV2 generation, per-object
        set targeting, alpha-mask dilation -- is the same :meth:`bake_fused`
        pipeline.

        Parameters mirror :meth:`bake_fused` (``**kwargs``); ``prefix`` defaults
        to ``"lightmap_irr_"`` so irradiance output never clobbers fused output,
        and ``batch`` defaults to True here (one RTT call for all objects --
        measured 7.45x over per-object calls; falls back automatically on
        duplicate shape leaf names).

        Returns:
            ``{long_object_name: lightmap_path}`` for each successful bake.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        if objects is None:
            objects = cmds.ls(selection=True, long=True, transforms=True) or []
        if not objects:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        card = self._create_white_card()
        try:
            return self.bake_fused(
                objects,
                output_dir=output_dir,
                prefix=prefix,
                shader=card,
                batch=batch,
                **kwargs,
            )
        finally:
            if cmds.objExists(card):
                cmds.delete(card)

    @staticmethod
    def _create_white_card() -> str:
        """A true-white Lambert (Kd = 1) for the ``-shader`` override bake.

        Maya lambert's ``.diffuse`` (Kd) defaults to 0.8: left alone, the
        "white" card is an 80% grey card and every lighting-only map bakes
        ~20% dark (measured 0.8006). Never assigned to anything -- it rides
        the bake as a per-shape render override; the caller deletes it after.
        """
        mat = MatUtils.create_mat("lambert", name="lm_whitecard")
        cmds.setAttr(f"{mat}.color", 1, 1, 1, type="double3")
        cmds.setAttr(f"{mat}.diffuse", 1.0)
        return mat

    @staticmethod
    def _texture_set_stem(obj: str) -> Optional[str]:
        """Base name of *obj*'s existing texture set (e.g. ``Plants_Metal_Base_01``).

        So a baked lightmap follows the material's texture-set naming
        (``<base>_Lightmap``) instead of the object's often long, import-
        namespaced node name. Strips the map-type suffix (``_BaseColor`` /
        ``_Normal`` / …) via ``ptk.MapFactory.get_base_texture_name`` -- the same
        helper ``game_shader`` uses. Returns ``None`` when the object has no file
        textures, so the bake falls back to the object leaf name.
        """
        try:
            paths = MatUtils.get_texture_paths(objects=[obj], absolute=False)
        except Exception:
            return None
        if not paths:
            return None
        return ptk.MapFactory.get_base_texture_name(paths[0]) or None

    # ------------------------------------------------------------------
    # Unity consumption (fused -> stock unlit, no sidecar) -- non-destructive
    # ------------------------------------------------------------------

    def commit_unlit(self, mapping: Dict[str, str]) -> Dict[str, str]:
        """Make the fused bake each object's live appearance (non-destructive).

        A stock Unity *Unlit/Texture* shader samples ``TEXCOORD0``, so a fused
        lightmap must end up on **UV0** in the exported FBX. This makes the
        lightmap the mesh's primary UV channel (texture UVs slide to UV1) and
        assigns an unlit ``surfaceShader`` driven by the fused EXR -- so the
        object shows the baked result in Maya *and* exports correctly to Unity
        with no custom shader, no sidecar, and no export-time fix-up.

        It is **non-destructive**: nothing is deleted (the source material /
        SGs stay in the scene, just un-assigned; the texture UVs move, they
        aren't lost), and a JSON restore record (original UV order, shading
        snapshot, created node names) is stamped on the shape via
        :attr:`COMMIT_ATTR`. :meth:`revert_unlit` reads that back -- so revert
        works after save/reload and from a fresh baker. Idempotent: a shape
        already carrying the marker is left untouched (re-committing would
        capture the unlit state as "source").

        Parameters:
            mapping: ``{object_long_name: fused_exr_path}`` from :meth:`bake_fused`.

        Returns:
            ``{object_long_name: surfaceShader}`` for each newly committed object.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; commit aborted.")
            return {}

        wired: Dict[str, str] = {}
        for obj, path in mapping.items():
            shape = NodeUtils.get_shape(obj)
            if not shape:
                self.logger.warning("No shape for %s; skipping commit.", obj)
                continue
            if cmds.attributeQuery(self.COMMIT_ATTR, node=shape, exists=True):
                self.logger.debug("%s already committed; skipping.", obj)
                continue

            prev_order = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            # Snapshot shading on the TRANSFORM (MatUtils resolves the shape; a
            # shape arg would no-op), so revert rebuilds per-face materials too.
            prev_shading = MatUtils.get_shading_assignments(obj)

            # Promote the lightmap unwrap to UV0 (texture UVs slide to UV1).
            lm = UvDiagnostics.find_lightmap_uv_set(shape)
            if lm and lm in prev_order and prev_order[0] != lm:
                UvUtils.reorder_uv_sets(shape, [lm] + [s for s in prev_order if s != lm])
            elif not lm:
                self.logger.warning(
                    "No lightmap UV set on %s; leaving UV order as-is.", obj
                )

            # Unlit surfaceShader driven by the fused EXR (raw linear HDR). Named
            # after the baked file so shader / file-node / texture-set names line
            # up and inherit the caller's prefix/suffix affix.
            shader_name = os.path.splitext(os.path.basename(path))[0]
            shader = MatUtils.create_mat("surfaceShader", name=shader_name)
            file_node, placement = MatUtils.create_file_node(path, color_space="Raw")
            cmds.connectAttr(f"{file_node}.outColor", f"{shader}.outColor", force=True)
            MatUtils.assign_mat(obj, shader)  # creates the SG and assigns
            sg = (cmds.listConnections(shader, type="shadingEngine") or [None])[0]
            created = [n for n in (shader, sg, file_node, placement) if n]

            self._stamp_commit(shape, prev_order, prev_shading, created)
            wired[obj] = shader

        return wired

    def revert_unlit(self, objects: Optional[List[str]] = None) -> List[str]:
        """Undo :meth:`commit_unlit` -- restore the source material + UV order.

        Reads the JSON record stamped by :meth:`commit_unlit`, so it works on
        any committed mesh regardless of who committed it (a fresh baker, a
        reopened scene). With ``objects=None`` it reverts **every** committed
        mesh in the scene; pass transforms to scope it.

        Re-baking calls this first so the bake samples the real (source)
        material, not the flat unlit one.

        Returns the long names of the shapes reverted.
        """
        if cmds is None:
            return []

        shapes = self._collect_marked_shapes(self.COMMIT_ATTR, objects)

        reverted: List[str] = []
        for shape in shapes:
            if not shape or not cmds.attributeQuery(
                self.COMMIT_ATTR, node=shape, exists=True
            ):
                continue
            try:
                record = json.loads(cmds.getAttr(f"{shape}.{self.COMMIT_ATTR}") or "{}")
            except ValueError:
                record = {}

            transform = NodeUtils.get_transform_node(shape)
            try:
                prev_order = record.get("order") or []
                now = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                if prev_order and prev_order != now and set(prev_order) == set(now):
                    UvUtils.reorder_uv_sets(shape, prev_order)
                if transform and record.get("shading") is not None:
                    MatUtils.apply_shading_assignments(transform, record["shading"])
                for node in record.get("nodes") or []:
                    if cmds.objExists(node):
                        cmds.delete(node)
                cmds.deleteAttr(f"{shape}.{self.COMMIT_ATTR}")
                reverted.append(shape)
            except RuntimeError as e:
                self.logger.warning("Could not revert %s: %s", shape, e)
        return reverted

    # ------------------------------------------------------------------
    # Engine consumption (lighting-only -> keep maps + metadata bridge)
    # ------------------------------------------------------------------

    # Identity atlas transform: the object's 0-1 lightmap UVs map to the whole
    # texture (the per-object, non-atlased case).
    _IDENTITY_SCALE_OFFSET: Tuple[float, float, float, float] = (1.0, 1.0, 0.0, 0.0)

    def pack_atlas(
        self,
        mapping: Dict[str, str],
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
    ) -> Dict[str, Tuple[str, List[float]]]:
        """Consolidate per-object lightmaps into one atlas EXR per primary material.

        Post-process for the **lighting-only** path: takes the ``{object:
        per_object_exr}`` result of :meth:`bake_separated` and packs each
        material group into a single shared atlas. Every object is assigned an
        area-weighted :func:`rect <pythontk.ImgUtils.compute_atlas_layout>` (by
        world surface area, so bigger objects get more texels) and its lightmap
        UVs are **repacked into that rect** -- the exported mesh samples the
        atlas directly through its UV2, so the atlas is plug-and-play in any
        engine (no scaleOffset binding, no engine-side script). Each rect is
        inset by a resolution-scaled pixel gutter (the freed border is
        dilate-filled from the content) so mips / bilinear taps can't bleed
        between neighbors. The per-object bake is reused unchanged
        (bake-full-then-pack) -- only the images and UVs are repacked -- so
        this can't regress the bake itself. The UV remap is recorded on the
        commit marker (``uvRect``) and fully undone by :meth:`revert_lightmap`
        (or automatically before the next bake).

        An object whose lightmap UVs can't be repacked (no lightmap set, or the
        edit fails) keeps its own per-object map with an identity rect --
        degraded but always engine-correct. Instanced transforms share one
        shape (one UV set), so only the first owns a rect (warned).

        One EXR + one scaleOffset per object means re-running with more objects of
        the same material reuses the same texture-set name (the atlas is named
        ``<texture-set-base><suffix>``, deterministic per group), so there is no
        per-object texture explosion and no cross-bake naming collision. A
        single-object group is left as its own map with an identity rect.

        Requires cv2 (EXR IO / resize). Pairs with :meth:`commit_lightmap`
        (pass the rects as its ``uv_rects`` so the remap is revertible).

        Parameters:
            mapping: ``{object_long_name: per_object_exr}`` to consolidate.
            output_dir: Where the atlas EXRs go. Defaults to the directory of the
                first input map.
            prefix / suffix: Name affix for the atlas file, wrapped around the
                group's texture-set base (default ``<base>_Lightmap``).

        Returns:
            ``{object_long_name: (atlas_path, [scaleX, scaleY, offsetX, offsetY])}``.
            The rect is the UV remap already **applied** to the object's
            lightmap set (identity for per-object fallbacks / solo groups) --
            bookkeeping for the commit marker, not an engine binding. Objects
            whose source map can't be read are dropped (logged).
        """
        if cmds is None or not mapping:
            return {}
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        # Fail fast (before ANY side effects) when cv2 is unavailable -- the
        # caller's fallback then commits the per-object maps untouched.
        import cv2  # noqa: F401  (availability gate; used in _pack_group)

        output_dir = output_dir or os.path.dirname(next(iter(mapping.values())))

        # Instanced transforms share one shape (one lightmap UV set), so
        # baked-in atlas UVs can't differ per instance -- and per-instance
        # shading could even land two instances in *different* groups, which
        # would remap the shared set twice. Dedupe globally (before grouping):
        # only the first transform owns a rect; the rest resolve to the same
        # shape / marker anyway.
        by_shape: Dict[str, str] = {}
        objects: List[str] = []
        for obj in sorted(mapping):  # deterministic winner + rect order
            s = NodeUtils.get_shape(obj)
            # Key on the shape's UUID: get_shape returns a DAG path, which is
            # unique PER INSTANCE PATH of a shared shape (|a|shape vs |b|shape)
            # — a path key never collides, so instanced transforms would each
            # get a rect and remap the one shared UV set twice (compounded).
            sid = cmds.ls(s, uuid=True)[0] if s else None
            if sid and sid in by_shape:
                self.logger.warning(
                    "Atlas: %s instances the same mesh as %s; instances share "
                    "one lightmap UV set and atlas rect.", obj, by_shape[sid],
                )
                continue
            if sid:
                by_shape[sid] = obj
            objects.append(obj)

        # Group objects by their primary (dominant-face) material assignment.
        groups: Dict[str, List[str]] = {}
        for obj in objects:
            key = self._primary_material(obj) or "__no_material__"
            groups.setdefault(key, []).append(obj)

        # Every source map, so an atlas name can't land on a *different* group's
        # not-yet-consumed source (e.g. duplicated materials sharing a texture
        # set -> same stem, different group). A group may overwrite its OWN
        # sources (read into memory first), so those are excluded per group.
        all_sources = {os.path.abspath(p) for p in mapping.values()}

        out: Dict[str, Tuple[str, List[float]]] = {}
        used: set = set()
        for key, objs in groups.items():
            try:
                self._pack_group(
                    key, objs, mapping, all_sources, output_dir,
                    prefix, suffix, out, used,
                )
            except Exception as e:
                # Never lose a bake or leave a half-consumed group: a source
                # map is only deleted after its object's UV repack succeeded,
                # so everything this group didn't finish still has its
                # per-object map -- keep it (identity rect). Objects already
                # consolidated (in ``out``) stay valid: their atlas was
                # written before any of their side effects. Other groups are
                # unaffected.
                self.logger.warning(
                    "Atlas: packing group %r failed (%s); keeping per-object "
                    "maps for its unfinished objects.", key, e,
                )
                for o in objs:
                    if o not in out and os.path.exists(mapping[o]):
                        out[o] = (mapping[o], list(self._IDENTITY_SCALE_OFFSET))
        return out

    def _pack_group(
        self,
        key: str,
        objs: List[str],
        mapping: Dict[str, str],
        all_sources: set,
        output_dir: str,
        prefix: str,
        suffix: str,
        out: Dict[str, Tuple[str, List[float]]],
        used: set,
    ) -> None:
        """Pack one material group's maps into its atlas (see :meth:`pack_atlas`).

        Consolidates *objs*' per-object maps into one shared EXR, repacks each
        object's lightmap UVs into its rect, and records results into *out*
        (mutated; *used* tracks atlas paths claimed this pack). Split out so
        :meth:`pack_atlas` can guard each group independently -- a group-level
        failure falls back to per-object maps without poisoning other groups.
        *objs* is pre-sorted and instance-deduped by the caller.
        """
        import cv2
        import numpy as np

        foreign = all_sources - {os.path.abspath(mapping[o]) for o in objs}
        base = self._texture_set_stem(objs[0]) or key.rsplit("|", 1)[-1].rsplit(
            ":", 1
        )[-1]
        name = ptk.StrUtils.apply_affix(base, prefix, suffix)
        atlas_path = self._unique_atlas_path(output_dir, name, used, foreign)

        if len(objs) == 1:
            # A one-object group is its own atlas (identity rect): just adopt
            # the texture-set name, no re-encode.
            src = mapping[objs[0]]
            if os.path.abspath(src) != os.path.abspath(atlas_path):
                os.replace(src, atlas_path)
            out[objs[0]] = (atlas_path, list(self._IDENTITY_SCALE_OFFSET))
            return

        weights = [self._surface_area(o) for o in objs]
        rects = ptk.ImgUtils.compute_atlas_layout(weights)
        # Free a pixel gutter around every rect (content is inset, then the
        # atlas is dilated into the freed border below) so mip levels and
        # bilinear taps can't bleed across neighboring objects. The inset
        # rect IS the applied UV rect, so sampling stays exact.
        gutter = max(2, self.resolution // 256)
        rects = ptk.ImgUtils.inset_atlas_rects(rects, self.resolution, gutter)

        images: List[Any] = []
        placed: List[Tuple[str, List[float]]] = []
        for obj, rect in zip(objs, rects):
            img = cv2.imread(
                mapping[obj], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH
            )
            if img is None:
                self.logger.warning(
                    "Atlas: unreadable map for %s; skipping.", obj
                )
                continue
            if img.ndim == 3 and img.shape[2] == 4:
                img = img[..., :3]  # lightmaps are opaque RGB; drop any alpha
            images.append(img)
            placed.append((obj, [float(v) for v in rect]))
        if not images:
            return

        atlas = ptk.ImgUtils.assemble_atlas(
            images, [so for _, so in placed], self.resolution
        )
        # Fill the gutters from the placed content. The coverage mask is
        # exact (the placed pixel rects) -- a luminance mask would treat
        # valid near-black texels as empty.
        mask = np.zeros(atlas.shape[:2], dtype=bool)
        for row0, row1, col0, col1 in ptk.ImgUtils.atlas_pixel_rects(
            [so for _, so in placed], self.resolution
        ):
            mask[max(row0, 0):max(row1, 0), max(col0, 0):max(col1, 0)] = True
        atlas = ptk.ImgUtils.dilate_image(atlas, mask=mask, iterations=gutter + 1)
        self._write_lightmap_exr(atlas_path, atlas)

        for obj, so in placed:
            # Repack the object's lightmap UVs into its rect -- only now
            # that the atlas file exists on disk (a write failure must not
            # leave meshes remapped against a map that was never written).
            # The exported mesh then samples the atlas directly through
            # UV2: correct in any engine with no scaleOffset binding. If
            # the UVs can't be repacked the object keeps its own
            # per-object map (identity rect, source not deleted): degraded
            # but never engine-wrong.
            shape = NodeUtils.get_shape(obj)
            lm_set = (
                UvDiagnostics.find_lightmap_uv_set(shape) if shape else None
            )
            try:
                if not lm_set:
                    raise RuntimeError("no lightmap UV set on the shape")
                self._transform_lightmap_uvs(shape, lm_set, so)
            except Exception as e:
                self.logger.warning(
                    "Atlas: lightmap UVs for %s could not be repacked (%s); "
                    "keeping its per-object map.", obj, e,
                )
                out[obj] = (mapping[obj], list(self._IDENTITY_SCALE_OFFSET))
                continue
            out[obj] = (atlas_path, so)
            # Record the applied remap on the marker NOW, not at commit time
            # (see _stamp_uv_rect) — the scene mutation must be revertible
            # from the moment it happens.
            self._stamp_uv_rect(shape, so)
            # Drop the now-consolidated per-object map.
            try:
                if os.path.abspath(mapping[obj]) != os.path.abspath(atlas_path):
                    os.remove(mapping[obj])
            except OSError:
                pass

    @staticmethod
    def _primary_material(obj: str) -> Optional[str]:
        """The shading group covering the most faces of *obj* (its dominant material).

        A whole-object (single-material) assignment wins outright; otherwise the
        per-face group with the most faces. Used to group objects that should
        share one lightmap atlas. Returns ``None`` when nothing is assigned.
        """
        assigns = MatUtils.get_shading_assignments(obj)
        if not assigns:
            return None
        return max(
            assigns.items(),
            key=lambda kv: float("inf") if kv[1] is None else len(kv[1]),
        )[0]

    @staticmethod
    def _surface_area(obj: str) -> float:
        """World-space surface area of *obj* (atlas texel weight); 1.0 on failure."""
        try:
            area = cmds.polyEvaluate(obj, worldArea=True)
            area = area[0] if isinstance(area, (list, tuple)) else area
            return float(area) if area and float(area) > 0 else 1.0
        except Exception:
            return 1.0

    @staticmethod
    def _unique_atlas_path(
        output_dir: str, name: str, used: set, avoid: "set" = frozenset()
    ) -> str:
        """Atlas path for *name*, unique within one pack and clear of *avoid*.

        Re-running a bake should overwrite the same per-material atlas (the whole
        point of consolidation), so collisions with the atlas's *own* prior file
        are allowed; only two groups resolving to the same name in a single pack
        (``used``) or a name landing on another group's not-yet-consumed source
        map (*avoid*, a set of abspaths) are disambiguated (``{name}_1`` ...).
        """
        candidate = os.path.join(output_dir, f"{name}.exr")
        k = 1
        while candidate in used or os.path.abspath(candidate) in avoid:
            candidate = os.path.join(output_dir, f"{name}_{k}.exr")
            k += 1
        used.add(candidate)
        return candidate

    @staticmethod
    def _transform_lightmap_uvs(
        shape: str, uv_set: str, rect: List[float], invert: bool = False
    ) -> None:
        """Affine-transform *shape*'s *uv_set* by a ``[sx, sy, ox, oy]`` rect.

        Forward (default) maps the unit square into the rect (``uv' = uv * s +
        o`` -- the atlas placement used by :meth:`pack_atlas`); ``invert=True``
        applies the exact inverse, restoring the original layout. Operates via
        a current-set swap (``polyEditUV`` edits the current UV set only) and
        restores the previous current set.
        """
        sx, sy, ox, oy = (float(v) for v in rect)
        prev = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
        cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
        try:
            uvs = f"{shape}.map[*]"
            if invert:
                cmds.polyEditUV(uvs, uValue=-ox, vValue=-oy, relative=True)
                cmds.polyEditUV(
                    uvs, pivotU=0.0, pivotV=0.0,
                    scaleU=1.0 / sx, scaleV=1.0 / sy, scale=True,
                )
            else:
                cmds.polyEditUV(
                    uvs, pivotU=0.0, pivotV=0.0, scaleU=sx, scaleV=sy, scale=True
                )
                cmds.polyEditUV(uvs, uValue=ox, vValue=oy, relative=True)
        finally:
            if prev and prev != uv_set:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev)

    def _restore_lightmap_uvs(self, shape: str, info: Dict[str, Any]) -> bool:
        """Undo the pack-time UV remap recorded on *shape*'s marker (``uvRect``).

        Restores the lightmap UV set to its original unit-square layout so a
        re-bake or a fresh pack starts from 0-1 UVs. Returns True when a
        non-identity rect was present and successfully inverted.
        """
        rect = (info or {}).get("uvRect")
        if not rect or [float(v) for v in rect] == list(self._IDENTITY_SCALE_OFFSET):
            return False
        uv_set = info.get("uv_set") or UvDiagnostics.find_lightmap_uv_set(shape)
        if not uv_set:
            return False
        try:
            self._transform_lightmap_uvs(shape, uv_set, rect, invert=True)
        except Exception as e:
            self.logger.warning(
                "Could not restore atlased lightmap UVs on %s: %s", shape, e
            )
            return False
        return True

    def _marker_info(self, shape: str) -> Dict[str, Any]:
        """The shape's :attr:`LIGHTMAP_INFO_ATTR` marker as a dict ({} if
        absent/unparsable)."""
        try:
            if cmds.attributeQuery(
                self.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            ):
                return json.loads(
                    cmds.getAttr(f"{shape}.{self.LIGHTMAP_INFO_ATTR}") or "{}"
                )
        except ValueError:
            pass
        return {}

    def _stamp_uv_rect(self, shape: str, rect: List[float]) -> None:
        """Merge an applied UV remap into the shape's marker immediately.

        :meth:`_pack_group` calls this the moment it repacks the UVs — a crash
        (or a direct-API caller who never reaches :meth:`commit_lightmap`)
        must not leave silently remapped UVs that neither
        :meth:`revert_lightmap` nor the pre-bake guard can see.
        """
        if [float(v) for v in rect] == list(self._IDENTITY_SCALE_OFFSET):
            return
        info = self._marker_info(shape)
        info["uvRect"] = [float(v) for v in rect]
        self._set_string_attr(shape, self.LIGHTMAP_INFO_ATTR, json.dumps(info))

    def _restore_atlased_uvs(self, objects: List[str]) -> None:
        """Restore any atlas-remapped lightmap UVs on *objects* before a bake.

        A prior atlas commit repacked each object's lightmap UVs into its atlas
        rect (``uvRect`` on the marker). Baking against that layout would fill
        only the rect's fraction of the map, so restore the unit square first
        and rewrite the marker without the rect. The panel's pre-bake revert
        already covers this; it guards direct API re-bakes.
        """
        for obj in objects:
            shape = NodeUtils.get_shape(obj)
            if not shape or not cmds.attributeQuery(
                self.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            ):
                continue
            try:
                info = json.loads(
                    cmds.getAttr(f"{shape}.{self.LIGHTMAP_INFO_ATTR}") or "{}"
                )
            except ValueError:
                continue
            if self._restore_lightmap_uvs(shape, info):
                info.pop("uvRect", None)
                self._set_string_attr(
                    shape, self.LIGHTMAP_INFO_ATTR, json.dumps(info)
                )

    def commit_lightmap(
        self,
        mapping: Dict[str, str],
        intensity: float = 1.0,
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        uv_rects: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, str]:
        """Record a lighting-only bake for the engine (fully non-destructive).

        Unlike :meth:`commit_unlit`, this changes **nothing** about the object's
        material or UV order: the full PBR material and texture UV0 are kept, and
        the lightmap stays a separate HDR on UV channel index 1 (where engines
        bind the lightmap), to be composited ``albedo x lightmap`` by the engine.
        Per object it stamps a small JSON marker (:attr:`LIGHTMAP_INFO_ATTR`),
        then republishes the scene-wide manifest onto the shared ``data_export``
        carrier so it rides the FBX (informational; consumed by unitytk's
        optional Unity-native binder -- see :meth:`_publish_lightmap_metadata`).

        Parameters:
            mapping: ``{object_long_name: lightmap_path}`` from
                :meth:`bake_separated` (or the atlas map from :meth:`pack_atlas`).
            intensity: Lightmap multiplier (default 1.0). Unity's native
                lightmap system has no per-lightmap multiplier, so a non-1.0
                value is **applied into the texels here** (each unique file
                scaled once -- atlases shared by several objects included); the
                manifest field is informational after that. Because Arnold
                bakes physical radiance (``albedo x E / pi``) while Unity's
                realtime lights use the un-normalized ``NdotL x color``
                convention, a fully-baked scene matches the Maya render at 1.0;
                pass ``math.pi`` to instead match Unity-native-light intensity.
                Note it mutates the file: re-committing the same bake with a
                non-1.0 intensity re-applies it (the panel always commits a
                fresh bake).
            scale_offsets: Optional ``{object_long_name: [scaleX, scaleY,
                offsetX, offsetY]}`` -- a rect the **engine** must apply when
                sampling (published as the renderer's ``lightmapScaleOffset``).
                Legacy / compat only: the atlas path now repacks the rect into
                the lightmap UVs instead (see ``uv_rects``), so absent entries
                default to identity and nothing engine-side is required.
            uv_rects: Optional ``{object_long_name: [scaleX, scaleY, offsetX,
                offsetY]}`` -- the UV remap :meth:`pack_atlas` already
                **applied** to the object's lightmap set. Recorded on the
                marker (``uvRect``) purely so :meth:`revert_lightmap` (and the
                pre-bake guard) can restore the original 0-1 layout; engines
                need nothing.

        Returns:
            ``{object_long_name: lightmap_path}`` for each object recorded.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; commit aborted.")
            return {}

        scale_offsets = scale_offsets or {}
        uv_rects = uv_rects or {}
        recorded: Dict[str, str] = {}
        for obj, path in mapping.items():
            shape = NodeUtils.get_shape(obj)
            if not shape:
                self.logger.warning("No shape for %s; skipping.", obj)
                continue
            uv_set = (
                UvDiagnostics.find_lightmap_uv_set(shape)
                or UvDiagnostics.LIGHTMAP_UV_SET
            )
            so = scale_offsets.get(obj) or self._IDENTITY_SCALE_OFFSET
            info = {
                "map": os.path.basename(path),
                "uv_set": uv_set,
                "intensity": float(intensity),
                "scaleOffset": [float(v) for v in so],
                "mode": "separated",
            }
            rect = uv_rects.get(obj)
            if rect is None:
                # pack_atlas stamps the applied remap at pack time; a commit
                # that wasn't handed the rects must carry it forward — a
                # rewritten marker without it would make the remap invisible
                # to revert_lightmap and the pre-bake guard.
                rect = self._marker_info(shape).get("uvRect")
            if rect and [float(v) for v in rect] != list(
                self._IDENTITY_SCALE_OFFSET
            ):
                info["uvRect"] = [float(v) for v in rect]
            self._set_string_attr(shape, self.LIGHTMAP_INFO_ATTR, json.dumps(info))
            recorded[obj] = path

        if recorded:
            # Scale texels only once at least one marker actually resolved: a
            # commit that records nothing must not mutate files on disk (the
            # retry would re-apply the multiplier on top).
            if float(intensity) != 1.0:
                self._apply_intensity(recorded.values(), intensity)
            self._publish_lightmap_metadata()
        return recorded

    def _apply_intensity(self, paths, intensity: float) -> None:
        """Scale each unique lightmap file's texels by *intensity*, once.

        Files shared by several objects (an atlas) are deduped by abspath so
        they scale exactly once per commit. A file that can't be read is left
        untouched and logged -- the commit itself still proceeds (the marker /
        manifest are more valuable than the multiplier).
        """
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        try:
            import cv2
        except ImportError as e:
            self.logger.warning(
                "Intensity %.3f NOT applied (cv2 unavailable): %s", intensity, e
            )
            return

        for path in {os.path.abspath(p) for p in paths}:
            try:
                img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
                if img is None:
                    raise RuntimeError("unreadable EXR")
                bgr = img[..., :3] if img.ndim == 3 else img
                self._write_lightmap_exr(path, bgr * float(intensity))
            except Exception as e:
                self.logger.warning(
                    "Intensity %.3f NOT applied to %s: %s",
                    intensity, os.path.basename(path), e,
                )

    def _publish_lightmap_metadata(self) -> Optional[str]:
        """(Re)build the lightmap manifest on the shared ``data_export`` carrier.

        Scans every mesh carrying a :attr:`LIGHTMAP_INFO_ATTR` marker and writes
        a single JSON manifest (``{"version", "objects": [...]}``) to the
        ``data_export`` node via :meth:`DataNodes.set_export_string`, so the data
        rides into the FBX as a user property (unitytk's optional editor helper
        reads it to auto-bind Unity's native lightmap slots -- the maps
        themselves work without it via plain UV2 sampling). Regenerating from
        the markers (not the last bake) keeps
        incremental bakes additive and a revert subtractive. Clears the channel
        when no lightmapped meshes remain; never creates the carrier just to
        write an empty manifest.

        Returns the ``data_export`` node name, or ``None`` when nothing shipped.
        """
        objects: List[Dict[str, Any]] = []
        for shape in cmds.ls(type="mesh", long=True) or []:
            if not cmds.attributeQuery(
                self.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            ):
                continue
            try:
                info = json.loads(
                    cmds.getAttr(f"{shape}.{self.LIGHTMAP_INFO_ATTR}") or "{}"
                )
            except ValueError:
                continue
            transform = NodeUtils.get_transform_node(shape) or shape
            # The engine matches by the GameObject (transform) name: strip the
            # DAG path and any namespace.
            name = transform.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            # Publish the lightmap set's REAL channel index. Unity's native
            # lightmaps only ever sample uv2 (index 1) -- anything else means
            # the export will sample the wrong channel, so warn loudly instead
            # of shipping a hardcoded 1 that hides the problem.
            uv_set = info.get("uv_set")
            sets = list(
                dict.fromkeys(
                    cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                )
            )
            uv_index = sets.index(uv_set) if uv_set in sets else 1
            if uv_set and uv_set not in sets:
                self.logger.warning(
                    "%s: committed lightmap set %r no longer exists; "
                    "publishing uvIndex 1 on faith. Re-run create_lightmap_uvs "
                    "if the set was renamed or removed.",
                    name, uv_set,
                )
            if uv_index != 1:
                self.logger.warning(
                    "%s: lightmap set %r sits at UV index %d, but Unity samples "
                    "uv2 (index 1). Re-run create_lightmap_uvs (it reorders to "
                    "index 1) before exporting.",
                    name, uv_set, uv_index,
                )
            objects.append(
                {
                    # camelCase keys: Unity's JsonUtility matches C# field names
                    # exactly, so these mirror LightmapRecord in unitytk's
                    # LightmapMetadataController.cs.
                    "name": name,
                    "map": info.get("map"),
                    "uvIndex": uv_index,
                    "intensity": info.get("intensity", 1.0),
                    # The object's rect into its (possibly shared) lightmap: the
                    # identity transform for a per-object map, or a real atlas
                    # rect from pack_atlas. Old markers predate the key -> identity.
                    "scaleOffset": info.get(
                        "scaleOffset", list(self._IDENTITY_SCALE_OFFSET)
                    ),
                }
            )

        names = [o["name"] for o in objects]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            self.logger.warning(
                "Duplicate object name(s) in the lightmap manifest: %s. Unity "
                "matches renderers by GameObject name (first match wins) -- "
                "rename to disambiguate.",
                ", ".join(dupes),
            )

        if not objects:
            # set_export_string clears an existing channel without creating
            # data_export just to write an empty manifest.
            DataNodes.set_export_string(self.LIGHTMAP_METADATA, "")
            return None

        manifest = json.dumps(
            {"version": self.LIGHTMAP_METADATA_VERSION, "objects": objects}
        )
        return DataNodes.set_export_string(self.LIGHTMAP_METADATA, manifest)

    def revert_lightmap(self, objects: Optional[List[str]] = None) -> List[str]:
        """Undo :meth:`commit_lightmap` -- drop the markers + republish.

        The material and texture UV0 were never changed, so this removes the
        :attr:`LIGHTMAP_INFO_ATTR` markers (the objects leave the export
        manifest) and, for an atlas commit, restores the lightmap UV set to its
        original unit-square layout (inverting the recorded ``uvRect``); the
        baked texture and the UV set itself are left in place (harmless, reused
        by the next bake). With ``objects=None`` it clears **every** marked
        mesh.

        Returns the long names of the shapes cleared.
        """
        if cmds is None:
            return []

        shapes = self._collect_marked_shapes(self.LIGHTMAP_INFO_ATTR, objects)

        cleared: List[str] = []
        for shape in shapes:
            if not shape or not cmds.attributeQuery(
                self.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            ):
                continue
            try:
                info = json.loads(
                    cmds.getAttr(f"{shape}.{self.LIGHTMAP_INFO_ATTR}") or "{}"
                )
            except ValueError:
                info = {}
            try:
                cmds.deleteAttr(f"{shape}.{self.LIGHTMAP_INFO_ATTR}")
                cleared.append(shape)
            except RuntimeError as e:
                self.logger.warning(
                    "Could not clear lightmap marker on %s: %s", shape, e
                )
                continue  # marker intact -> leave the UV remap recorded too
            self._restore_lightmap_uvs(shape, info)
        if cleared:
            self._publish_lightmap_metadata()
        return cleared

    def revert(self, objects: Optional[List[str]] = None) -> List[str]:
        """Undo any lightmap wiring -- fused commit and/or lighting-only marker.

        Convenience for the panel and the pre-bake clear: reverts whichever
        level each object is in (a mesh is only ever in one). Returns the
        combined list of reverted shape names.
        """
        return self.revert_unlit(objects) + self.revert_lightmap(objects)

    def _stamp_commit(
        self,
        shape: str,
        prev_order: List[str],
        prev_shading: Dict[str, Any],
        created: List[str],
    ) -> None:
        """Persist the restore record for :meth:`revert_unlit` on *shape*."""
        record = json.dumps(
            {"order": prev_order, "shading": prev_shading, "nodes": created}
        )
        self._set_string_attr(shape, self.COMMIT_ATTR, record)

    @staticmethod
    def _collect_marked_shapes(attr: str, objects: Optional[List[str]]) -> List[str]:
        """Shapes to revert: those carrying *attr* (``objects=None`` → all in scene).

        Shared by :meth:`revert_unlit` / :meth:`revert_lightmap` — ``None`` means
        "every mesh marked with this commit attr"; an explicit list maps each
        transform to its shape (callers still re-check the marker per shape).
        """
        if objects is None:
            return [
                s for s in (cmds.ls(type="mesh", long=True) or [])
                if cmds.attributeQuery(attr, node=s, exists=True)
            ]
        return [NodeUtils.get_shape(o) for o in objects]

    @staticmethod
    def _set_string_attr(node: str, attr: str, value: str) -> None:
        """Create (if missing) and set a string attr on *node*.

        ``Attributes.set_attributes`` can't be used here: it omits the
        ``-type "string"`` flag and Maya rejects a string ``setAttr`` without it.
        Shared by the commit / lightmap markers so the explicit
        ``addAttr(dataType="string")`` lives in one place.
        """
        if not cmds.attributeQuery(attr, node=node, exists=True):
            cmds.addAttr(node, longName=attr, dataType="string")
        cmds.setAttr(f"{node}.{attr}", value, type="string")

    def _warn_if_unlit_scene(self) -> None:
        """Warn (once per instance) when the scene has no light source to bake.

        A lightless bake silently produces a black lightmap -- worth a loud
        hint. Emissive-material-only scenes still trip this; it is a warning,
        not a gate.
        """
        if self._warned_no_lights or cmds is None:
            return
        if cmds.ls(lights=True):
            return
        try:  # Arnold light types only exist with mtoa loaded
            arnold = cmds.ls(
                type=[
                    "aiSkyDomeLight",
                    "aiAreaLight",
                    "aiMeshLight",
                    "aiPhotometricLight",
                ]
            )
        except RuntimeError:
            arnold = []
        if arnold:
            return
        self._warned_no_lights = True
        self.logger.warning(
            "No lights found in the scene -- the lightmap will bake black "
            "(unless emissive materials are the only light source)."
        )

    # Sanitize + write policy for every lightmap EXR this pipeline emits.
    # Irradiance is non-negative and the maps are consumed as half-precision
    # (Unity BC6H is half), so values are clamped to [0, 65504] -- a float32
    # firefly above half-max would otherwise become inf in the half encode.
    _HALF_MAX: float = 65504.0

    @classmethod
    def _write_lightmap_exr(cls, path: str, bgr) -> None:
        """Sanitize *bgr* and write it as a half-float EXR (in place policy).

        NaN -> 0 and +/-inf -> clamp: one bad ray in a raw bake would
        otherwise spread through gutter dilation / atlas resize into clean
        texels. Half-float halves disk + Unity import cost with no visible
        loss for lightmap data.
        """
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2
        import numpy as np

        bgr = np.asarray(bgr, dtype=np.float32)
        if not np.isfinite(bgr).all():
            bad = int((~np.isfinite(bgr)).sum())
            cls.logger.warning(
                "%s: %d non-finite texel value(s) sanitized.",
                os.path.basename(path), bad,
            )
            bgr = np.nan_to_num(bgr, nan=0.0, posinf=cls._HALF_MAX, neginf=0.0)
        np.clip(bgr, 0.0, cls._HALF_MAX, out=bgr)
        # cv2 returns False (no exception) when EXR write support is missing:
        # callers delete per-object maps once this returns, so a silent failure
        # would destroy the source with no atlas on disk -- raise to enforce it.
        ok = cv2.imwrite(path, bgr, [cv2.IMWRITE_EXR_TYPE, cv2.IMWRITE_EXR_TYPE_HALF])
        if not ok:
            raise RuntimeError(f"failed to write EXR: {path}")

    @classmethod
    def _dilate_lightmap(cls, path: str, alpha_threshold: float, iterations: int) -> bool:
        """Edge-pad one baked EXR in place using its alpha coverage channel.

        The alpha channel from ``arnoldRenderToTexture`` is the only reliable
        coverage signal -- a luminance heuristic would wrongly treat dark-but-
        valid texels (shadow contact, near-black albedo) as empty. The alpha is
        dropped on write: a fused lightmap is consumed as opaque RGB, and a
        partial-coverage alpha would be misread as transparency.

        Returns False (a no-op) when the image has no alpha channel.
        """
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        if img is None:
            raise RuntimeError(f"unreadable EXR: {path}")
        if img.ndim != 3 or img.shape[2] < 4:
            return False  # no coverage channel -> nothing safe to dilate from

        bgr = img[..., :3]
        alpha = img[..., 3]
        mask = alpha > alpha_threshold
        # RTT premultiplies RGB by texel coverage: island-edge texels carry
        # alpha-darkened lighting (measured: edge/interior ratio == alpha),
        # and dilation would then smear that darkening into the gutters.
        # Dividing by alpha recovers the radiance estimate -- rgb == alpha*L,
        # so the division is bounded by scene radiance, not a noise blow-up.
        partial = mask & (alpha < 1.0)
        if partial.any():
            bgr[partial] /= alpha[partial][:, None]
        if not mask.all():
            bgr = ptk.ImgUtils.dilate_image(bgr, mask=mask, iterations=iterations)
        cls._write_lightmap_exr(path, bgr)  # opaque RGB (alpha dropped)
        return True


class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the ``lightmap_baker.ui`` panel.

    Composition over inheritance: a thin driver over :class:`LightmapBaker`
    (the workflow) — no bake logic lives here. **Bake Lightmaps** (``b000``)
    runs the whole pipeline for the selected objects and wires the result up so
    nothing is left to do afterward; the **Mode** combobox picks the bake level:

    * **Lighting Only** (default) — :meth:`LightmapBaker.bake_separated` +
      :meth:`~LightmapBaker.commit_lightmap`. Keeps the full PBR material and
      texture UVs; bakes lighting onto UV1 and stamps Unity metadata on the
      shared ``data_export`` carrier. The maps survive; the engine composites.
    * **Fused Unlit** — :meth:`LightmapBaker.bake_fused` +
      :meth:`~LightmapBaker.commit_unlit`. Bakes albedo×lighting into one map,
      makes it UV0 + an unlit material (stock Unlit shader, no sidecar) at the
      cost of the other maps. The lowest-end / fully baked option.

    Either way ``b000`` first calls :meth:`LightmapBaker.revert` to clear any
    prior wiring so the bake samples the real material. It is non-destructive
    (source material / UVs preserved, restore data stamped on the mesh): the
    header menu's **Revert to Source** undoes it. The Quality combobox is
    populated from :meth:`LightmapBaker.preset_store` and fills the Resolution /
    Samples dials, which are the source of truth at bake time.
    """

    # Bake-level labels for the Mode combobox (cmb001). Lighting Only is index 0
    # (the default): it keeps every PBR map. _mode() reads the selection back.
    _MODE_LABELS = ("Lighting Only (keep maps)", "Fused Unlit (single map)")

    # Packing labels for the Packing combobox (cmb002). Per-Object (index 0, the
    # default) keeps one full-resolution map per object; Atlas by Material
    # consolidates a material group into one shared EXR + a per-object
    # scaleOffset rect. Atlas applies to Lighting Only only (the fused/unlit
    # stock shader has no scaleOffset to bind); _packing() reads it back.
    _PACKING_LABELS = ("Per-Object (one map each)", "Atlas by Material (shared map)")

    # Fixed lightmap sizes (square, px) for the Resolution combobox
    # (cmb_resolution). Power-of-two atlas sizes; every Quality preset lands on
    # one of these. _resolution() reads the selection back as an int.
    _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

    # Scope labels for the Scope combobox (cmb_scope): which objects b000 bakes.
    # Selected (index 0, default) preserves the prior selection-only behavior;
    # _scope() / _scope_objects() resolve it to the mesh transforms to bake.
    _SCOPE_LABELS = ("Selected", "Visible", "Scene")

    # Footer tail for a plain (non-atlas) lighting-only commit. Shared by b000's
    # per-object branch and _commit_atlas's fallback so the two can't drift.
    _LIGHTING_ONLY_TAIL = "Maps kept; lightmap + Unity metadata stamped. Export the FBX."

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.lightmap_baker

        # Output dir of the most recent bake (reported in the footer).
        self._last_output_dir: Optional[str] = None
        # Workflow instance, rebuilt per bake from the current dials. commit /
        # revert persist their state on the mesh, so revert works even from a
        # fresh instance / reopened scene.
        self._baker: Optional[LightmapBaker] = None

        # Deferred to the next tick: the switchboard builds this instance
        # mid-load, before child widgets (footer, combos) are wired onto self.ui.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        """Sync the dials to the selected preset and report backend state.

        Deferred from __init__ (QTimer) so the full UI is wired first: the
        ``cmb000`` handler isn't connected during ``cmb000_init``, so the
        preset chosen there never reaches the dials -- do it here so the shown
        preset and the Resolution / Samples fields can't drift apart at open.
        """
        self._apply_preset(self.ui.cmb000.currentText())
        if not TextureBaker.arnold_available():
            self.ui.footer.setText(
                "Arnold (mtoa) not loaded — bakes fall back to LDR (no HDR/dilation)."
            )

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget) -> None:
        """Configure the header menu and help text."""
        widget.config_buttons("menu", "collapse", "hide")
        widget.menu.add(
            "QPushButton",
            setText="Revert to Source",
            setObjectName="revert_to_source",
            setToolTip="Undo the bake's wiring — restore the original material "
            "and UV order on the selected (or all baked) objects.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Open Sourceimages Folder",
            setObjectName="open_sourceimages",
            setToolTip="Open the project's sourceimages folder (where bakes are "
            "written) in Explorer.",
        )
        if fmt is not None:
            widget.set_help_text(
                fmt(
                    title="Lightmap Baker",
                    body="Bake Maya scene lighting into a texture per object for "
                    "game engines (Unity-first; the fallback when Bakery isn't an "
                    "option) and wire it up in one step — no manual export prep.",
                    steps=[
                        "Choose a <b>Scope</b> — bake the <b>Selected</b> objects "
                        "(default), all <b>Visible</b> meshes, or the whole "
                        "<b>Scene</b>.",
                        "Pick a <b>Mode</b> and <b>Packing</b> (see below) and a "
                        "<b>Quality</b> preset (fills Resolution / Samples; override "
                        "either to taste).",
                        "Press <b>Bake Lightmaps</b>, then export the FBX. "
                        "<b>Include the hidden <i>data_export</i> node</b> in the "
                        "export (use <i>Export All</i>, or mayatk's Scene Exporter, "
                        "which adds it automatically) — a plain <i>Export Selection</i> "
                        "of just the meshes omits it and the Unity wiring won't ship.",
                    ],
                    sections=[
                        ("Mode: Lighting Only — real lightmapping (default)", [
                            "This is how you normally light-map. Bakes <i>lighting "
                            "only</i> (white-card irradiance) onto a second UV "
                            "channel; your full PBR material — albedo, normal, "
                            "metallic/roughness — is <b>kept untouched</b>.",
                            "The lightmap is a <b>separate texture asset</b> (written "
                            "to the project's <i>sourceimages</i>, alongside your "
                            "other maps) — the engine multiplies albedo × lightmap "
                            "at runtime and your normal map still lights normally. "
                            "Exactly how Unity's own lightmaps work.",
                            "The export is <b>self-contained</b>: the mesh's UV2 "
                            "samples the map directly, so it works in any engine "
                            "(or any shader that reads a lightmap on UV2) with no "
                            "extra setup. Import <i>sourceimages</i> as usual so "
                            "the lightmap is in the project.",
                            "To bind Unity's <b>native</b> lightmap slots "
                            "(standard Lit shaders, no custom work), drop unitytk's "
                            "<i>LightmapMetadataController.cs</i> into the project "
                            "once — it reads the FBX wiring and assigns "
                            "everything on import. Optional: without it you wire "
                            "the map by hand or sample UV2 in your shader.",
                            "Use this for normal game assets — nothing is thrown "
                            "away.",
                            "<b>Packing</b>: <i>Per-Object</i> (default) gives each "
                            "object its own full-resolution lightmap. For many small "
                            "objects, <i>Atlas by Material</i> consolidates everything "
                            "sharing a material into <b>one shared map</b> — each "
                            "object's lightmap UVs are repacked into its atlas rect "
                            "(area-weighted, so bigger objects get more texels), so "
                            "the atlas needs <b>no engine-side binding at all</b>. "
                            "Fewer textures, no naming collisions. The bake itself "
                            "is unchanged either way, and Revert restores the UVs.",
                        ]),
                        ("Mode: Fused Unlit — flatten to one texture (NOT lightmapping)", [
                            "<b>Not</b> a lightmap. Bakes albedo × lighting into one "
                            "HDR texture and assigns an <i>unlit</i> material, so the "
                            "surface becomes a single flat painted image — normal, "
                            "metallic and roughness are <b>discarded</b> and it can "
                            "never be re-lit.",
                            "Only for things you intend to flatten forever: a skybox, "
                            "a far LOD, or a lowest-end / mobile prop where one "
                            "texture lookup is the whole budget. It exports to a "
                            "stock <i>Unlit/Texture</i> shader with zero setup.",
                            "If your asset has a normal map you want to keep, this is "
                            "the wrong mode — use <b>Lighting Only</b>.",
                        ]),
                        ("Non-destructive", [
                            "Nothing is deleted — the source material and UVs stay "
                            "in the scene and the restore data is stamped on the "
                            "mesh.",
                            "<b>Revert to Source</b> (header menu) undoes the wiring "
                            "on the selected, or all baked, objects.",
                            "Re-baking auto-reverts first, so it always bakes the "
                            "real material.",
                        ]),
                    ],
                    notes=[
                        "The lightmap texture (in <i>sourceimages</i>) and its UV "
                        "channel both ride along regardless — <i>data_export</i> only "
                        "carries the optional wiring that lets Unity's native "
                        "lightmap slots be bound automatically. Without it nothing "
                        "is lost: the mesh's UV2 already samples the right texels.",
                        "Arnold (mtoa) is strongly recommended — it provides the "
                        "HDR output and alpha coverage the dilation relies on. "
                        "Without it the bake falls back to an LDR convertSolidTx "
                        "pass and dilation no-ops.",
                    ],
                )
            )

    # ------------------------------------------------------------------
    # Quality preset combobox
    # ------------------------------------------------------------------

    def cmb000_init(self, widget) -> None:
        """Populate the Quality combobox from the shared preset store."""
        store = LightmapBaker.preset_store()
        names = store.list()
        widget.clear()
        widget.addItems(names)
        # Default to "quest" (the balanced tier) when present.
        idx = widget.findText("quest")
        if idx >= 0:
            widget.setCurrentIndex(idx)

    def cmb000(self, index, widget) -> None:
        """Apply the selected preset's dials to the Resolution / Samples fields."""
        if self._apply_preset(widget.currentText()):
            self.ui.footer.setText(f"Preset: {widget.currentText()}")

    def cmb001_init(self, widget) -> None:
        """Populate the bake-level (Mode) combobox; Lighting Only is the default."""
        widget.clear()
        widget.addItems(self._MODE_LABELS)
        widget.setCurrentIndex(0)  # Lighting Only — keeps the PBR maps

    def _mode(self) -> str:
        """``"fused"`` or ``"separated"`` from the Mode combobox (default separated)."""
        text = (self.ui.cmb001.currentText() or "").lower()
        return "fused" if "fused" in text else "separated"

    def cmb002_init(self, widget) -> None:
        """Populate the Packing combobox; Per-Object is the safe default."""
        widget.clear()
        widget.addItems(self._PACKING_LABELS)
        widget.setCurrentIndex(0)  # Per-Object — one full-resolution map each

    def _packing(self) -> str:
        """``"atlas"`` or ``"per_object"`` from the Packing combobox (default per_object)."""
        text = (self.ui.cmb002.currentText() or "").lower()
        return "atlas" if "atlas" in text else "per_object"

    def cmb_scope_init(self, widget) -> None:
        """Populate the Scope combobox; Selected (current selection) is the default."""
        widget.clear()
        widget.addItems(self._SCOPE_LABELS)
        widget.setCurrentIndex(0)  # Selected — the prior selection-only behavior

    def _scope(self) -> str:
        """``"selected"`` (default), ``"visible"`` or ``"scene"`` from cmb_scope."""
        return (self.ui.cmb_scope.currentText() or "Selected").split()[0].lower()

    def _scope_objects(self) -> List[str]:
        """The mesh transforms to bake for the current Scope.

        ``selected`` is the raw selection (unchanged behavior); ``visible`` and
        ``scene`` gather mesh transforms across the scene so a bake needn't be
        preceded by a manual select-all.
        """
        scope = self._scope()
        if scope == "visible":
            from mayatk.display_utils._display_utils import DisplayUtils

            return DisplayUtils.get_visible_geometry(inherit_parent_visibility=True) or []
        if scope == "scene":
            meshes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []
            xforms = (
                cmds.listRelatives(meshes, parent=True, fullPath=True, type="transform")
                if meshes
                else []
            ) or []
            return list(dict.fromkeys(xforms))  # de-dupe (multi-shape transforms)
        return cmds.ls(selection=True, long=True, transforms=True) or []

    def cmb_resolution_init(self, widget) -> None:
        """Populate the Resolution combobox (value carried as item data); default 1024."""
        widget.clear()
        for r in self._RESOLUTIONS:
            widget.addItem(f"Resolution:\t{r}", r)
        widget.setCurrentIndex(self._RESOLUTIONS.index(1024))

    def _resolution(self) -> int:
        """The selected lightmap resolution (px) from cmb_resolution (its item data)."""
        value = self.ui.cmb_resolution.currentData()
        return int(value) if value is not None else 1024

    def _set_resolution(self, value: int) -> None:
        """Select *value* in the Resolution combobox, snapping to the nearest fixed size."""
        nearest = min(self._RESOLUTIONS, key=lambda r: abs(r - value))
        cmb = self.ui.cmb_resolution
        cmb.blockSignals(True)
        try:
            cmb.setCurrentIndex(self._RESOLUTIONS.index(nearest))
        finally:
            cmb.blockSignals(False)

    def txt000_init(self, widget) -> None:
        """Add the Prefix / Suffix / Auto picker to the name-affix field."""
        widget.option_box.set_affix(default="auto")

    def _apply_preset(self, name: str) -> bool:
        """Load *name*'s dials into the Resolution combobox / Samples spinbox.

        Single source for preset → dials (used by :meth:`cmb000` and the
        deferred :meth:`_initialize_ui`). Returns False if the preset is
        unknown (e.g. user deleted a built-in), leaving the dials untouched.
        """
        store = LightmapBaker.preset_store()
        if not name or not store.exists(name):
            return False
        data = store.load(name)
        if "resolution" in data:
            self._set_resolution(int(data["resolution"]))
        if "samples" in data:
            spin = self.ui.spn_samples
            spin.blockSignals(True)
            try:
                spin.setValue(int(data["samples"]))
            finally:
                spin.blockSignals(False)
        # GI dials have no panel widgets — carry them to the bake (from_preset
        # semantics). Without this, preset gi_depth/gi_samples silently no-op
        # for every panel bake ("preview"'s single bounce still baked 3).
        self._preset_gi = {
            k: int(data[k]) for k in ("gi_depth", "gi_samples") if k in data
        }
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def b000(self) -> None:
        """Bake lightmaps for the selection in the chosen Mode (revert → bake → commit)."""
        objects = self._scope_objects()
        if not objects:
            self.ui.footer.setText(
                "Select one or more mesh objects to bake."
                if self._scope() == "selected"
                else f"No meshes found for scope '{self._scope()}'."
            )
            return

        self._baker = LightmapBaker(
            resolution=self._resolution(),
            samples=self.ui.spn_samples.value(),
            **getattr(self, "_preset_gi", {}),
        )
        # Clear any prior wiring (fused commit or lighting-only marker) so the
        # bake samples the real material and the result starts clean.
        self._baker.revert(objects)

        # Write into the project's sourceimages (the conventional, portable home
        # for material-referenced textures); falls back to the workflow default
        # (<scene>/baked_lighting) when there's no project.
        src = self._sourceimages_dir()
        # Name the output <object><affix> per the field (e.g. "<object>_Lightmap"),
        # following the texture-set convention; the shader inherits the name.
        prefix, suffix = self.ui.txt000.option_box.resolve_affix(default="suffix")
        fused = self._mode() == "fused"
        bake = self._baker.bake_fused if fused else self._baker.bake_separated
        # Indeterminate "busy" marquee, NOT a 0..100% bar: a single Arnold bake
        # is one opaque blocking call with no sub-progress, so a determinate bar
        # would just sit at 0% then jump (the symptom seen in mtoa's own popup).
        # The marquee pulses while mtoa pumps the event loop during the render,
        # and the text still reports object i / N so multi-object runs read
        # clearly. (The bake itself can't be backgrounded — Maya cmds aren't
        # thread-safe — so this plus the OS wait cursor is the honest feedback.)
        with self.ui.footer.progress(text="Baking lightmaps…") as update:
            result = bake(
                objects,
                output_dir=src,
                prefix=prefix,
                suffix=suffix,
                on_progress=lambda done, total, name: update(
                    None,
                    f"Baking {name}…  ({min(done + 1, total)}/{total})"
                    if done < total
                    else f"Baked {total} object{'s' if total != 1 else ''}.",
                ),
            )
        if not result:
            self._last_output_dir = None
            self.ui.footer.setText("Bake produced no output (see Script Editor).")
            return

        if fused:
            self._baker.commit_unlit(result)
            tail = "Exports to a stock Unlit shader. Revert to Source to undo."
        elif self._packing() == "atlas":
            result, tail = self._commit_atlas(result, src, prefix, suffix)
        else:
            self._baker.commit_lightmap(result)
            tail = self._LIGHTING_ONLY_TAIL
        self._last_output_dir = os.path.dirname(next(iter(result.values())))
        count = len(result)
        self.ui.footer.setText(
            f"Baked {count} object{'s' if count != 1 else ''} → "
            f"{self._last_output_dir}. {tail}"
        )

    def _commit_atlas(
        self, result: Dict[str, str], output_dir: Optional[str], prefix: str, suffix: str
    ) -> Tuple[Dict[str, str], str]:
        """Consolidate a lighting-only bake into per-material atlases, then commit.

        Returns ``(mapping, footer_tail)``. Degrades gracefully: if packing
        produces nothing (e.g. cv2 unavailable), the per-object maps are
        committed as-is so a bake is never lost.
        """
        try:
            packed = self._baker.pack_atlas(
                result, output_dir=output_dir, prefix=prefix, suffix=suffix
            )
        except Exception as e:  # never lose the bake to a packing error
            self.logger.warning(
                "Atlas packing failed (%s); keeping per-object maps.", e
            )
            packed = {}
        if not packed:
            self._baker.commit_lightmap(result)
            return result, self._LIGHTING_ONLY_TAIL

        mapping = {obj: path for obj, (path, _so) in packed.items()}
        self._baker.commit_lightmap(
            mapping, uv_rects={obj: so for obj, (_path, so) in packed.items()}
        )
        n = len(set(mapping.values()))
        return mapping, (
            f"Consolidated into {n} atlas map{'s' if n != 1 else ''}; lightmap "
            "UVs repacked into each object's rect. Export the FBX."
        )

    # ------------------------------------------------------------------
    # Header-menu actions
    # ------------------------------------------------------------------

    def revert_to_source(self) -> None:
        """Undo the bake wiring on the selected objects (or all baked ones)."""
        if self._baker is None:
            self._baker = LightmapBaker()
        selection = cmds.ls(selection=True, long=True, transforms=True) or None
        reverted = self._baker.revert(selection)
        if reverted:
            self.ui.footer.setText(
                f"Reverted {len(reverted)} object{'s' if len(reverted) != 1 else ''} "
                "to source material + UV order."
            )
        else:
            self.ui.footer.setText("No baked objects to revert.")

    def open_sourceimages(self) -> None:
        """Open the project's sourceimages folder (where bakes go) in Explorer."""
        src = self._sourceimages_dir()
        if src and os.path.isdir(src):
            os.startfile(src)
        else:
            self.ui.footer.setText(
                "No sourceimages directory — set a Maya project first."
            )

    @staticmethod
    def _sourceimages_dir() -> Optional[str]:
        """The project's sourceimages path, or None (no project / lookup failed).

        Lazily imported so the headless workflow import stays lean; returns the
        path even if the folder doesn't exist yet (the bake creates it).
        """
        try:
            from mayatk.env_utils._env_utils import EnvUtils

            return EnvUtils.get_env_info("sourceimages") or None
        except Exception:
            return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("lightmap_baker", reload=True)
    ui.show(pos="screen", app_exec=True)
