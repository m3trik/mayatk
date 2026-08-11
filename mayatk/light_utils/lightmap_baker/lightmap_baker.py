# !/usr/bin/python
# coding=utf-8
"""High-level lightmap baking workflow for Maya -> game engines (Unity-first).

:class:`LightmapBaker` is the *workflow orchestrator*. It owns no low-level bake
or UV logic; it composes the ecosystem primitives into one lightmap pipeline:

* :meth:`UvUtils.create_lightmap_uvs` -- packed, non-overlapping lightmap UV (UV2)
* :meth:`TextureBaker.bake` ``(uv_set=)`` -- Arnold RTT into that set. That is
  the generic bake primitive (``mat_utils.texture_baker``) and is reusable on
  its own; the lightmap workflow lives here, the bake mechanics live there.
* :meth:`ImgUtils.dilate_image` -- gutter fill, from the texels the lightmap UV
  layout actually covers (:meth:`UvUtils.get_uv_triangles` ->
  :meth:`ImgUtils.rasterize_uv_triangles`) rather than from RTT's alpha, which
  ``-extend_edges`` leaves at 1.0 across the whole frame
* ``MatUtils`` / ``UvUtils`` -- non-destructive commit bookkeeping

**One bake level, and it is real lightmapping.** :meth:`bake_separated` bakes
white-card irradiance (lighting only) onto a separate UV channel (index 1) and
:meth:`commit_lightmap` records it. The object's full PBR material and its
texture UV0 are **kept untouched** -- the engine composites
``albedo x lightmap``. A per-object map is self-contained (mesh UV2 samples it
directly, any engine); a :meth:`pack_atlas` atlas additionally carries one
scaleOffset rect per object -- per INSTANCE -- on the marker, applied at
sample time (Unity ``lightmapScaleOffset`` / glTF ``KHR_texture_transform``).
A small manifest rides the FBX on the shared ``data_export`` carrier (no
sidecar file) so Unity's *native* lightmap slots can be auto-bound by the
optional unitytk editor helper.

:meth:`revert` (== :meth:`revert_lightmap`) undoes it -- used by the panel and
before a re-bake. A *fused unlit* level (albedo x lighting flattened onto UV0
behind a stock unlit shader) was removed: it is not lightmapping, it discards
every other map, and it only ever added a mode to choose wrongly from.

Quality tiers come from :meth:`from_preset` (pythontk ``PresetStore``). HDR EXR
throughout; 8-bit/encoded targets are a later (mostly engine-side) stage. For the
bake primitive alone (no lightmap workflow), use :class:`TextureBaker` directly.
"""

import json
import math
import os
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

import pythontk as ptk

from mayatk.mat_utils.texture_baker import TextureBaker
from mayatk.light_utils._light_utils import LightUtils
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics


class LightmapBaker(ptk.LoggingMixin):
    """Orchestrate the lightmap workflow: bake -> dilate -> engine export prep.

    Usage::

        baker = LightmapBaker.from_preset("desktop")          # or (resolution=)
        baker.revert(objects)                                 # bake the SOURCE mat
        out = baker.bake_separated(objects)                   # {obj: exr_path}
        baker.commit_lightmap(out)                            # marker + manifest
        # The object keeps its full PBR material; the lightmap rides UV channel 1
        # and the wiring rides the FBX on the data_export carrier -- nothing is
        # destroyed, and baker.revert() clears it (the marker lives on the mesh,
        # so revert works across save/reload and from a fresh baker instance).

    The injected/created :class:`TextureBaker` must emit EXR (the default does);
    the alpha-driven seam dilation depends on Arnold's float RGBA output.
    """

    # Per-shape JSON marker for a lighting-only ("separated") lightmap: which
    # map, UV set, intensity. Non-destructive bookkeeping (the material and UVs
    # are untouched) -- it records what the engine should composite and what to
    # republish into the export manifest; cleared by :meth:`revert_lightmap`.
    LIGHTMAP_INFO_ATTR: str = "lightmapInfo"

    # ``data_export`` channel: a scene-wide JSON manifest of every lighting-only
    # lightmap, regenerated from the per-transform markers. Rides the FBX as a user
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
        # the default targets the HDR path (Arnold + EXR). An injected
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
        # One no-lights warning per baker instance (a bake fans out to N
        # single-object passes; warning on each would spam the log).
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

    def _bake_to_lightmap_uvs(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        uv_set: Optional[str] = None,
        map_size: Optional[int] = None,
        create_uvs: bool = True,
        dilate: bool = True,
        dilate_iterations: Optional[int] = None,
        alpha_threshold: float = 0.05,
        prefix: str = "lightmap_",
        suffix: str = "",
        backend: str = "arnold",
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        stem: Optional[Any] = None,
        shader: Optional[str] = None,
        batch: bool = False,
    ) -> Dict[str, str]:
        """Bake one HDR map per object into the lightmap (UV2) channel.

        The shared bake core -- UV2 preparation, per-object set targeting, the
        RTT call and alpha-mask dilation. Private because what the map MEANS is
        decided by the caller's ``shader``: :meth:`bake_separated` passes a
        white card and gets lighting-only irradiance, which is the only thing
        this workflow produces.

        Parameters:
            objects: Mesh transforms. Defaults to current selection.
            output_dir: Output directory (created if missing). Defaults to
                :meth:`TextureBaker.bake`'s ``<scene_dir>/baked_lighting``.
            uv_set: Lightmap UV set name. Default ``LIGHTMAP_UV_SET``.
            map_size: UV-padding target for ``create_lightmap_uvs``. Defaults
                to ``resolution`` so the gutter matches the bake resolution.
            create_uvs: Ensure a packed lightmap UV2 first (reuses a valid one).
            dilate: Edge-pad island gutters, keeping only the texels the
                object's lightmap UV layout fully covers and refilling the
                rest (border slivers, gutters, background) from them. See
                :meth:`_dilate_lightmap` for why the UV layout -- not RTT's
                alpha -- is what separates an island from the edge extension
                baked past its border.
            dilate_iterations: Smooth-averaged gutter ring width in px.
                ``None`` -> a resolution-scaled default; ``-1`` -> flood the
                whole background with the averaging kernel instead. Either
                way, everything the ring did not reach is then nearest-filled
                (:meth:`ImgUtils.fill_empty_texels`) -- background texels are
                what GPU mip chains average into island edges as dark halos,
                so none may survive.
            alpha_threshold: Coverage cutoff; ``alpha > threshold`` is "baked".
                Below it a texel is treated as background and dilation
                replaces it from its neighbors -- which is also why the
                default is 0.05, not epsilon: unpremultiplying a texel by a
                near-zero alpha multiplies mostly filter noise by up to
                1000x, and one such firefly then spreads through the gutter
                averaging. At 5%+ coverage the recovery is bounded (<= 20x)
                and dominated by real signal.
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

        # Resolve to bakeable meshes HERE, not just inside TextureBaker.bake: the UV
        # generation and the atlas-UV restore below run first, and handing either a
        # light or a locator is a warning per object for a node that was never going
        # to bake. One definition of bakeable, shared with blendertk's twin.
        objects = TextureBaker.resolve_meshes(objects)
        if not objects:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        # A LEGACY atlas commit (pre rect-binding) repacked the lightmap UVs
        # into an atlas rect; restore the unit square before baking (else the
        # bake would fill only that fraction of the map). No-op on scenes
        # packed by the current rect-binding code, which never edits UVs.
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
                    self._dilate_lightmap(
                        path,
                        alpha_threshold,
                        dilate_iterations,
                        uv_triangles=self._lightmap_uv_triangles(name),
                    )
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

        THE bake: albedo stays on UV1, the lightmap on UV2 holds lighting only,
        to be combined ``albedo x lightmap`` by Unity's built-in lightmap system
        or a custom shader.

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
        set targeting, alpha-mask dilation -- is the shared
        :meth:`_bake_to_lightmap_uvs` core.

        Extra ``**kwargs`` are forwarded to that core (``uv_set``, ``map_size``,
        ``create_uvs``, ``dilate``, ``suffix``, ``stem``, ...). ``batch``
        defaults to True (one RTT call for all objects -- measured 7.45x over
        per-object calls; falls back automatically on duplicate shape leaf
        names).

        Returns:
            ``{long_object_name: lightmap_path}`` for each successful bake.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        # Resolved before the white card is created so a selection with nothing
        # bakeable in it doesn't leave a stray card node behind.
        objects = TextureBaker.resolve_meshes(objects)
        if not objects:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        card = self._create_white_card()
        try:
            return self._bake_to_lightmap_uvs(
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
        keep_sources: bool = False,
    ) -> Dict[str, Tuple[str, List[float]]]:
        """Consolidate per-object lightmaps into one atlas EXR per primary material.

        Post-process for the **lighting-only** path: takes the ``{object:
        per_object_exr}`` result of :meth:`bake_separated` and packs each
        material group into a single shared atlas. Every object is assigned an
        area-weighted :func:`rect <pythontk.ImgUtils.compute_atlas_layout>` (by
        world surface area, so bigger objects get more texels). **The rect is
        the deliverable, not a UV edit**: the object's 0-1 lightmap unwrap
        stays untouched and the engine applies the rect per object at sample
        time (Unity's ``Renderer.lightmapScaleOffset``; glTF
        ``KHR_texture_transform`` -- commit the rects via
        :meth:`commit_lightmap`'s ``scale_offsets``). Instanced transforms are
        first-class: every copy shares one shape / UV set but keeps its OWN
        rect and its OWN baked lighting (Arnold RTT renders the selected
        instance path at its world transform -- probe-verified), which a
        physical UV repack of the shared set could never express. Each rect is
        inset by a resolution-scaled pixel gutter (the freed border is
        dilate-filled from the content) so mips / bilinear taps can't bleed
        between neighbors, and the PUBLISHED rect aims the island's bbox at
        its border-texel CENTERS -- an edge on a texel boundary would split
        every tap along a shared 3D edge onto the neighboring cell's gutter,
        up to half its weight on another object's lighting. A lightmap unwrap
        that covers only part of 0-1 is
        CROPPED to its island bbox and the crop is folded into the published
        rect (still pure scale/offset, but it may extend past the unit square
        -- the engine only ever samples it at island UVs): the island fills
        its whole cell instead of sharing it with dead black texels that
        would both waste density and darken every border tap. The per-object bake is reused unchanged
        (bake-full-then-pack) -- only the images are composited -- so this
        can't regress the bake itself.

        One EXR + one scaleOffset per object means re-running with more objects of
        the same material reuses the same texture-set name (the atlas is named
        ``<texture-set-base><suffix>``, deterministic per group), so there is no
        per-object texture explosion and no cross-bake naming collision. A
        single-object group is left as its own map with an identity rect.

        Requires cv2 (EXR IO / resize). Mirrors ``blendertk.LightmapBaker.
        pack_atlas`` (same rect-deliverable contract).

        Parameters:
            mapping: ``{object_long_name: per_object_exr}`` to consolidate.
            output_dir: Where the atlas EXRs go. Defaults to the directory of the
                first input map.
            prefix / suffix: Name affix for the atlas file, wrapped around the
                group's texture-set base (default ``<base>_Lightmap``).
            keep_sources: Leave the per-object maps on disk instead of
                consuming them. They are the expensive half of a bake (a
                production room measured 37.6 min of Arnold time against
                seconds to assemble an atlas from maps already rendered), and
                nothing about them depends on the atlas resolution, the affix
                or the object set -- so keeping them makes a re-pack free:
                change the resolution, add an object, or re-run after a
                packing fix by calling this again with the same *mapping*.
                Off by default because the normal one-shot bake would
                otherwise litter the destination with intermediates.

        Returns:
            ``{object_long_name: (atlas_path, [scaleX, scaleY, offsetX, offsetY])}``.
            The rect is the object's engine binding (identity for solo groups /
            fallbacks) -- pass it to :meth:`commit_lightmap` as
            ``scale_offsets``. Objects whose source map can't be read are
            dropped (logged).
        """
        if cmds is None or not mapping:
            return {}
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        # Fail fast (before ANY side effects) when cv2 is unavailable -- the
        # caller's fallback then commits the per-object maps untouched.
        import cv2  # noqa: F401  (availability gate; used in _pack_group)

        output_dir = output_dir or os.path.dirname(next(iter(mapping.values())))

        # Group objects by their primary (dominant-face) material assignment.
        # Instanced transforms are NOT deduped: each copy carries its own bake
        # and earns its own rect -- per-instance data lives entirely in the
        # rect, so the shared UV set is never touched.
        groups: Dict[str, List[str]] = {}
        for obj in sorted(mapping):  # deterministic rect order
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
                    key,
                    objs,
                    mapping,
                    all_sources,
                    output_dir,
                    prefix,
                    suffix,
                    out,
                    used,
                    keep_sources,
                )
            except Exception as e:
                # Never lose a bake or leave a half-consumed group: a source
                # map is only deleted after its object landed in a written
                # atlas, so everything this group didn't finish still has its
                # per-object map -- keep it (identity rect). Objects already
                # consolidated (in ``out``) stay valid: their atlas was
                # written before any of their side effects. Other groups are
                # unaffected.
                self.logger.warning(
                    "Atlas: packing group %r failed (%s); keeping per-object "
                    "maps for its unfinished objects.",
                    key,
                    e,
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
        keep_sources: bool = False,
    ) -> None:
        """Pack one material group's maps into its atlas (see :meth:`pack_atlas`).

        Consolidates *objs*' per-object maps into one shared EXR and records
        each object's ``(atlas_path, rect)`` into *out* (mutated; *used* tracks
        atlas paths claimed this pack). UVs are never edited -- the rect is the
        engine binding. Split out so :meth:`pack_atlas` can guard each group
        independently -- a group-level failure falls back to per-object maps
        without poisoning other groups. *objs* is pre-sorted by the caller;
        instanced siblings each pack their own map into their own rect.
        """
        import cv2
        import numpy as np

        foreign = all_sources - {os.path.abspath(mapping[o]) for o in objs}
        base = (
            self._texture_set_stem(objs[0]) or key.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        )
        name = ptk.StrUtils.apply_affix(base, prefix, suffix)
        atlas_path = self._unique_atlas_path(output_dir, name, used, foreign)

        if len(objs) == 1:
            # A one-object group is its own atlas (identity rect): adopt the
            # texture-set name without a re-encode -- unless the map still
            # carries exact-zero texels (legacy bakes / no-alpha sources that
            # skipped the dilate rescue: rendered-dead geometry, unfilled
            # background), which every mip level would average into the
            # island as a dark halo. Those are healed on the way in.
            src = mapping[objs[0]]
            img = cv2.imread(src, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
            rgb = img[..., :3] if img is not None and img.ndim == 3 else None
            empty = None if rgb is None else ~(rgb > 0).any(axis=2)
            if empty is not None and empty.any() and not empty.all():
                self._write_lightmap_exr(
                    atlas_path, ptk.ImgUtils.fill_empty_texels(rgb, mask=~empty)
                )
                if not keep_sources and os.path.abspath(src) != os.path.abspath(
                    atlas_path
                ):
                    try:
                        os.remove(src)
                    except OSError:
                        pass
            elif os.path.abspath(src) != os.path.abspath(atlas_path):
                if keep_sources:
                    shutil.copy2(src, atlas_path)
                else:
                    os.replace(src, atlas_path)
            out[objs[0]] = (atlas_path, list(self._IDENTITY_SCALE_OFFSET))
            return

        weights = [self._surface_area(o) for o in objs]
        rects = ptk.ImgUtils.compute_atlas_layout(weights)
        # Free a pixel gutter around every rect (content is inset, then the
        # atlas is dilated into the freed border below) so mip levels and
        # bilinear taps can't bleed across neighboring objects. The cell is
        # SNAPPED to the texel grid so placement (assemble_atlas writes at
        # rounded pixel edges) and the published rect derive from the same
        # integer window; publishing then re-aims each rect at border-texel
        # centers (below) so edge taps never straddle into a neighbor.
        gutter = max(2, self.resolution // 256)
        rects = ptk.ImgUtils.snap_atlas_rects(
            ptk.ImgUtils.inset_atlas_rects(rects, self.resolution, gutter),
            self.resolution,
        )

        images: List[Any] = []
        cells: List[List[float]] = []  # placement rects (the layout's cells)
        placed: List[Tuple[str, List[float]]] = []  # published (engine) rects
        for obj, rect in zip(objs, rects):
            img = cv2.imread(mapping[obj], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
            if img is None:
                self.logger.warning("Atlas: unreadable map for %s; skipping.", obj)
                continue
            if img.ndim == 3 and img.shape[2] == 4:
                img = img[..., :3]  # lightmaps are opaque RGB; drop any alpha
            cell = [float(v) for v in rect]
            # A partial-coverage lightmap island wastes its cell on dead
            # space, and the lit signal gets only coverage-fraction of the
            # cell's texels. Crop the source to the island's bbox and fold
            # the crop into the published rect: the engine's uv*scale+offset
            # lands identically, at full-cell density.
            img, published, bounds = self._crop_to_island(
                img, self._lightmap_uv_bbox(obj), cell
            )
            # Publish the rect aimed at border-texel CENTERS: a cell edge
            # published on a texel BOUNDARY makes every engine tap along a
            # shared 3D edge blend onto the neighboring cell's gutter -- up
            # to half its weight on another object's lighting. Aimed at the
            # bounds that map onto the cell (NOT the island bbox, whose
            # sub-texel overhang past a crop would aim outside the cell);
            # placement still uses the snapped cell, only sampling re-aims.
            published = list(
                ptk.ImgUtils.inset_rects_to_texel_centers(
                    [published], self.resolution, bboxes=[bounds]
                )[0]
            )
            images.append(img)
            cells.append(cell)
            placed.append((obj, published))
        if not images:
            return

        atlas = ptk.ImgUtils.assemble_atlas(images, cells, self.resolution)
        # Fill the gutters from the placed content. The coverage mask is
        # exact (the placed pixel rects) -- a luminance mask would treat
        # valid near-black texels as empty. Bounds are clamped BOTH ways: a
        # rect edge that rounds past the canvas would otherwise leave the
        # atlas frame outside the mask and never dilated.
        mask = np.zeros(atlas.shape[:2], dtype=bool)
        h, w = mask.shape
        for row0, row1, col0, col1 in ptk.ImgUtils.atlas_pixel_rects(
            cells, self.resolution
        ):
            mask[max(row0, 0) : min(max(row1, 0), h), max(col0, 0) : min(max(col1, 0), w)] = True
        atlas = ptk.ImgUtils.dilate_image(atlas, mask=mask, iterations=gutter + 1)
        # Then fill EVERYTHING still exactly zero -- background beyond the
        # dilation ring AND any zero that arrived INSIDE a cell (legacy
        # sources that skipped the dilate rescue: geometry below the floor
        # slab / behind trim bakes full-coverage black -- the 12:45 room
        # atlas shipped 1440 such texels, banded along the wall/floor
        # junctions; 0 after this). Cell rects are deliberately NOT blanket-
        # trusted as content; only genuinely non-zero texels spread, and
        # real near-black shadow (> 0) is untouched.
        atlas = ptk.ImgUtils.fill_empty_texels(atlas, mask=(atlas > 0).any(axis=2))
        self._write_lightmap_exr(atlas_path, atlas)

        for obj, so in placed:
            # The atlas file exists on disk before any result is recorded, so
            # a write failure can never hand out rects against a map that was
            # never written. No scene mutation happens here: the rect is
            # carried on the commit marker (scaleOffset) and applied by the
            # engine at sample time.
            out[obj] = (atlas_path, so)
            # Drop the now-consolidated per-object map (kept when the caller
            # wants them as a re-pack cache -- see pack_atlas(keep_sources)).
            if keep_sources:
                continue
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

    #: Crop a source into its cell only when the island's bbox leaves real
    #: dead space (either axis under this coverage). Auto-unwraps run near
    #: full 0-1 (a few percent of margin) and gain nothing from a crop --
    #: and their published rects then stay within the unit square.
    _CROP_MAX_COVERAGE: float = 0.85

    #: Rendered-dead rescue (see :meth:`_dilate_lightmap`): a texel at or
    #: below ``max(_DEAD_TEXEL_ABS, _DEAD_TEXEL_FRACTION * median lit
    #: luminance)`` is occluded geometry (below the floor slab, behind trim /
    #: a door leaf), not signal, and is refilled from lit neighbors. 1% of
    #: median sits ~20x under real contact shadow and ~10x over the GI
    #: leak-through measured inside occluded corridors (OFFICE_ENV walls).
    _DEAD_TEXEL_ABS: float = 1e-4
    _DEAD_TEXEL_FRACTION: float = 0.01

    #: Coverage-mask refill (see :meth:`_dilate_lightmap`). A texel is this
    #: object's lighting only if the lightmap UV layout covers ALL of it --
    #: :meth:`pythontk.ImgUtils.rasterize_uv_triangles` reports 255 for that.
    #: A partially covered texel is part island, part edge EXTENSION, and the
    #: extension is not this object's lighting: Arnold renders it physically,
    #: and a point just past a wall panel's edge is coplanar with the
    #: neighbouring panel, so its rays hit that panel and it bakes dark. At
    #: 40% island / 60% extension the texel lands at ~0.4x its true value.
    _COVERAGE_FULL: int = 255

    #: Extra texels eroded off the fully-covered mask, in units of the
    #: reconstruction filter's REACH (``TextureBaker.filter_width`` 2.0 spans
    #: +/-1 texel). A texel can be fully covered and still collect extension
    #: samples through the filter tail, and the refill continues the interior
    #: outward over whatever this drops.
    #:
    #: This is LOAD-BEARING, not a refinement -- do not set it to 0 expecting a
    #: milder version of the same fix. A/B over one production bake, 12 panels
    #: at 256: coverage with NO erosion barely moves the delivered border
    #: (mean deviation 5.32% -> 5.00%, texels off by >10% 573 -> 547), because
    #: the texels carrying the contamination are mostly FULLY covered ones
    #: sitting a filter-tail away from the border, not the partial ones. One
    #: ring takes it to 1.87% / 54. A second ring trades further (1.35% / 60):
    #: better on the mean, no better on the count, and a texel of real signal
    #: more expensive -- which stops being free after the plan-first port,
    #: where an island is tens of texels across rather than hundreds.
    _COVERAGE_ERODE: int = 1

    #: Supersampling for the coverage raster, by map size. The rasterizer's
    #: scratch is dominated by its ``(size * ss)^2`` byte grid, so 4 costs ~5 MB
    #: at 512 and ~83 MB at 2048 -- fine -- but ~335 MB at 4096, inside a DCC
    #: already holding the scene being baked. 2 brings that to ~134 MB and
    #: still resolves coverage to a quarter texel, far finer than the
    #: all-or-nothing test it feeds.
    _COVERAGE_SUPERSAMPLE_MAX_SIZE: int = 2048

    @staticmethod
    def _lightmap_set(obj: str) -> Optional[Tuple[str, str]]:
        """``(shape, uv_set)`` for *obj*'s lightmap layout, or ``None``.

        THE definition of "the set the bake rendered", shared by every reader
        of that layout (:meth:`_lightmap_uv_bbox` for the crop,
        :meth:`_lightmap_uv_triangles` for the coverage mask). One resolution
        so a crop and the mask applied to the image it crops can never
        disagree about which set they are describing. ``None`` on anything
        missing -- no shape, no lightmap set -- so callers degrade instead of
        raising: a bake must never be lost to a diagnostic.
        """
        try:
            shape = NodeUtils.get_shape(obj)
            if not shape:
                return None
            uv_set = UvDiagnostics.find_lightmap_uv_set(shape)
            return (shape, uv_set) if uv_set else None
        except Exception:
            return None

    @classmethod
    def _lightmap_uv_bbox(cls, obj: str) -> Optional[Tuple[float, float, float, float]]:
        """``(u0, v0, u1, v1)`` of *obj*'s lightmap-set islands, or ``None``.

        The bbox of the SET THE BAKE RENDERED (the same one the commit marker
        records), so a crop can never disagree with the layout the engine
        samples. ``None`` -- no shape, no lightmap set, or any query failure
        -- means "don't crop"; the pack must never lose a bake to a
        diagnostic.
        """
        resolved = cls._lightmap_set(obj)
        if resolved is None:
            return None
        shape, uv_set = resolved
        try:
            prev = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
            cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
            try:
                (u0, u1), (v0, v1) = cmds.polyEvaluate(shape, boundingBox2d=True)
            finally:
                if prev and prev != uv_set:
                    cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev)
            return (float(u0), float(v0), float(u1), float(v1))
        except Exception:
            return None

    @classmethod
    def _lightmap_uv_triangles(cls, obj: str):
        """*obj*'s lightmap-set UV triangles ``(N, 3, 2)``, or ``None``.

        The layout the bake RENDERED, so a coverage mask built from this
        cannot disagree with the image it masks. ``None`` -- no shape, no
        lightmap set, an empty layout, or any query failure -- means "no
        coverage evidence", and the refill falls back to alpha alone: a bake
        must never be lost to a diagnostic.
        """
        resolved = cls._lightmap_set(obj)
        if resolved is None:
            return None
        shape, uv_set = resolved
        try:
            triangles = UvUtils.get_uv_triangles(shape, uv_set)
            return triangles if len(triangles) else None
        except Exception:
            return None

    @classmethod
    def _crop_to_island(
        cls, img: Any, bbox: Optional[Tuple[float, float, float, float]], cell: List[float]
    ) -> Tuple[Any, List[float], Tuple[float, float, float, float]]:
        """Crop *img* to *bbox* and fold the crop into the published rect.

        Returns ``(image, rect, bounds)``, where *bounds* is the uv range
        that maps onto the FULL cell -- ``(0, 0, 1, 1)`` when no crop was
        taken (``bbox`` ``None``, degenerate, or already near-full coverage,
        :attr:`_CROP_MAX_COVERAGE`). Callers publish through
        :func:`~pythontk.ImgUtils.inset_rects_to_texel_centers` with those
        bounds, so the cell's own edges -- not the island's, which may
        overhang by a sub-texel sliver -- are what land on border-texel
        centers, and no sample can fall outside the cell.

        The crop keeps exactly the texels the island TOUCHES -- no pad. A pad
        admits edge-EXTENSION texels, and those are not this object's
        lighting: Arnold renders the extension physically, and a point just
        past a wall panel's edge is COPLANAR with the neighbouring panel, so
        its rays hit that panel immediately and it bakes dark.

        The old ``+1`` pad was ASYMMETRIC -- the island's low edge already
        began mid-texel so the clamp at 0 added nothing there, while the high
        edge gained a FULL extension texel -- and after the ~3:1 atlas
        downscale that texel was ~1/3 of the cell's border texel. That is
        what put a line at every stacked-panel joint and none at the
        side-by-side ones (u is the panel's VERTICAL): measured on the
        shipped room, every tile's top edge sat ~5% off its own interior
        trend while its bottom read ~0%. A/B at production density over one
        set of baked maps, crop rule the only variable -- contaminated edge
        +6.4% -> -1.2%, mean per-side error 4.23% -> 1.64%.

        Touched rather than fully-covered texels because the bounds must
        CONTAIN the island: cropping inside it leaves a sub-texel overhang
        that samples past the cell (measured marginally better, 1.54%, and
        not worth the invariant -- pinned by test).

        The rect is composed from the bounds actually taken:
        ``uv in [cu0, cu1] x [cv0, cv1] -> the full cell``, so the engine's
        ``uv * scale + offset`` lands exactly where the texels went.
        """
        full = (0.0, 0.0, 1.0, 1.0)
        if bbox is None:
            return img, cell, full
        u0, v0, u1, v1 = (min(max(v, 0.0), 1.0) for v in bbox)
        if (u1 - u0) >= cls._CROP_MAX_COVERAGE and (v1 - v0) >= cls._CROP_MAX_COVERAGE:
            return img, cell, full
        h, w = img.shape[:2]
        eps = 1e-6  # an edge ON a texel boundary must not claim the next one
        c0 = max(0, math.floor(u0 * w + eps))
        c1 = min(w, math.ceil(u1 * w - eps))
        r0 = max(0, math.floor((1.0 - v1) * h + eps))
        r1 = min(h, math.ceil((1.0 - v0) * h - eps))
        if c1 - c0 < 2 or r1 - r0 < 2:
            return img, cell, full
        cu0, cu1 = c0 / w, c1 / w
        cv0, cv1 = 1.0 - r1 / h, 1.0 - r0 / h
        sx = cell[0] / (cu1 - cu0)
        sy = cell[1] / (cv1 - cv0)
        return (
            img[r0:r1, c0:c1],
            [sx, sy, cell[2] - cu0 * sx, cell[3] - cv0 * sy],
            (cu0, cv0, cu1, cv1),
        )

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

        LEGACY-revert primitive: current packs never edit UVs (the rect is an
        engine binding), so the only live caller is the ``uvRect`` restore path
        for scenes packed before the rect-binding contract. Forward (default)
        maps the unit square into the rect (``uv' = uv * s + o``);
        ``invert=True`` applies the exact inverse, restoring the original
        layout. Operates via a current-set swap (``polyEditUV`` edits the
        current UV set only) and restores the previous current set.
        """
        sx, sy, ox, oy = (float(v) for v in rect)
        prev = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
        cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
        try:
            uvs = f"{shape}.map[*]"
            if invert:
                cmds.polyEditUV(uvs, uValue=-ox, vValue=-oy, relative=True)
                cmds.polyEditUV(
                    uvs,
                    pivotU=0.0,
                    pivotV=0.0,
                    scaleU=1.0 / sx,
                    scaleV=1.0 / sy,
                    scale=True,
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
        """Undo a LEGACY pack-time UV remap recorded on *shape*'s marker (``uvRect``).

        Old packs physically repacked the lightmap UVs into the atlas rect;
        this restores the original unit-square layout so a re-bake or a fresh
        pack starts from 0-1 UVs. Current packs never write ``uvRect``.
        Returns True when a non-identity rect was present and successfully
        inverted.
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

    def _marker_node(self, obj: str) -> Optional[str]:
        """The node carrying *obj*'s lightmap marker, or ``None``.

        Current commits stamp the TRANSFORM (a transform is per-instance, so
        every copy of a shared shape can hold its own atlas rect); commits
        that predate the move stamped the shape. Transform wins when both
        exist. *obj* may be either node.
        """
        transform = NodeUtils.get_transform_node(obj) or obj
        # Long form: the returned name feeds getAttr/deleteAttr later, and a
        # short name is ambiguous the moment two groups share a leaf name.
        transform = (cmds.ls(transform, long=True) or [transform])[0]
        if cmds.attributeQuery(self.LIGHTMAP_INFO_ATTR, node=transform, exists=True):
            return transform
        shape = NodeUtils.get_shape(obj)
        if shape and cmds.attributeQuery(
            self.LIGHTMAP_INFO_ATTR, node=shape, exists=True
        ):
            return shape
        return None

    def _marker_info(self, obj: str) -> Dict[str, Any]:
        """*obj*'s :attr:`LIGHTMAP_INFO_ATTR` marker as a dict ({} if
        absent/unparsable). Reads through :meth:`_marker_node`, so it finds the
        marker whether the commit stamped the transform (current) or the shape
        (legacy)."""
        node = self._marker_node(obj)
        if node:
            try:
                return json.loads(
                    cmds.getAttr(f"{node}.{self.LIGHTMAP_INFO_ATTR}") or "{}"
                )
            except ValueError:
                pass
        return {}

    def _restore_atlased_uvs(self, objects: List[str]) -> None:
        """Restore any atlas-remapped lightmap UVs on *objects* before a bake.

        LEGACY-scene guard: packs that predate the rect-binding contract
        physically repacked each object's lightmap UVs into its atlas rect
        (``uvRect`` on the marker). Baking against that layout would fill only
        the rect's fraction of the map, so restore the unit square first and
        rewrite the marker without the rect. Current packs never edit UVs, so
        on a current-format scene this is a no-op.
        """
        for obj in objects:
            marker = self._marker_node(obj)
            shape = NodeUtils.get_shape(obj)
            if not marker or not shape:
                continue
            info = self._marker_info(obj)
            if self._restore_lightmap_uvs(shape, info):
                info.pop("uvRect", None)
                self._set_string_attr(marker, self.LIGHTMAP_INFO_ATTR, json.dumps(info))

    def commit_lightmap(
        self,
        mapping: Dict[str, str],
        intensity: float = 1.0,
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        uv_rects: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, str]:
        """Record a lighting-only bake for the engine (fully non-destructive).

        This changes **nothing** about the object's
        material or UV order: the full PBR material and texture UV0 are kept, and
        the lightmap stays a separate HDR on UV channel index 1 (where engines
        bind the lightmap), to be composited ``albedo x lightmap`` by the engine.
        Per object it stamps a small JSON marker (:attr:`LIGHTMAP_INFO_ATTR`)
        on the TRANSFORM (per-instance: every copy of a shared shape carries
        its own atlas rect), then republishes the scene-wide manifest onto the
        shared ``data_export`` carrier so it rides the FBX (informational;
        consumed by unitytk's optional Unity-native binder -- see
        :meth:`_publish_lightmap_metadata`).

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
                offsetX, offsetY]}`` -- THE atlas binding: the per-instance
                rect the engine applies when sampling (Unity's
                ``Renderer.lightmapScaleOffset``; glTF ``KHR_texture_transform``).
                Stamped per TRANSFORM, which is what lets every instance of a
                shared shape own its own rect over the one shared unwrap.
                Absent entries default to identity (a per-object map).
            uv_rects: Optional ``{object_long_name: [scaleX, scaleY, offsetX,
                offsetY]}`` -- legacy-marker compat only: a UV remap an old
                pack already **applied** to the object's lightmap set, recorded
                on the marker (``uvRect``) so :meth:`revert_lightmap` (and the
                pre-bake guard) can restore the original 0-1 layout. The
                Arnold pack path still uses this; new atlas code passes
                ``scale_offsets`` instead.

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
            transform = NodeUtils.get_transform_node(obj) or obj
            transform = (cmds.ls(transform, long=True) or [transform])[0]
            uv_set = (
                UvDiagnostics.find_lightmap_uv_set(shape)
                or UvDiagnostics.LIGHTMAP_UV_SET
            )
            so = scale_offsets.get(obj) or self._IDENTITY_SCALE_OFFSET
            info = {
                "map": os.path.basename(path),
                # Where the map lives, so a consumer holding only the manifest (a
                # GLB post-process reading it back OUT of the deliverable) can find
                # the file with no caller passing paths. Host-local by nature --
                # readers fall back to searching near the deliverable when stale.
                "dir": os.path.dirname(os.path.abspath(path)),
                "uv_set": uv_set,
                "intensity": float(intensity),
                "scaleOffset": [float(v) for v in so],
                "mode": "separated",
            }
            rect = uv_rects.get(obj)
            if rect is None:
                # The Arnold pack stamps the applied remap at pack time; a commit
                # that wasn't handed the rects must carry it forward — a
                # rewritten marker without it would make the remap invisible
                # to revert_lightmap and the pre-bake guard.
                rect = self._marker_info(obj).get("uvRect")
            if rect and [float(v) for v in rect] != list(self._IDENTITY_SCALE_OFFSET):
                info["uvRect"] = [float(v) for v in rect]
            # The TRANSFORM is the marker home: it is per-instance, so every
            # copy of a shared shape can carry its own rect. A leftover legacy
            # shape marker is cleared so the publisher can't double-count.
            self._set_string_attr(transform, self.LIGHTMAP_INFO_ATTR, json.dumps(info))
            if cmds.attributeQuery(self.LIGHTMAP_INFO_ATTR, node=shape, exists=True):
                try:
                    cmds.deleteAttr(f"{shape}.{self.LIGHTMAP_INFO_ATTR}")
                except RuntimeError:
                    pass
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
                    intensity,
                    os.path.basename(path),
                    e,
                )

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Rebuild the ``lightmap_metadata`` export channel from the scene's markers.

        The no-arg producer entry point (``FbxUtils._KNOWN_PRODUCERS``): the
        manifest is regenerated purely from the per-transform (and legacy
        per-shape) :attr:`LIGHTMAP_INFO_ATTR` markers, so bake settings are irrelevant —
        a default-configured instance is just a namespace here.
        """
        return cls()._publish_lightmap_metadata()

    def _publish_lightmap_metadata(self) -> Optional[str]:
        """(Re)build the lightmap manifest on the shared ``data_export`` carrier.

        Scans every TRANSFORM carrying a :attr:`LIGHTMAP_INFO_ATTR` marker (one
        record per instance, each with its own atlas ``scaleOffset``), plus any
        legacy shape-stamped markers, and writes a single JSON manifest
        (``{"version", "objects": [...]}``) to the ``data_export`` node via
        :meth:`DataNodes.set_export_string`, so the data rides into the FBX as
        a user property (unitytk's optional editor helper reads it to auto-bind
        Unity's native lightmap slots -- ``renderer.lightmapScaleOffset`` per
        record). Regenerating from the markers (not the last bake) keeps
        incremental bakes additive and a revert subtractive. Clears the channel
        when no lightmapped meshes remain; never creates the carrier just to
        write an empty manifest.

        Returns the ``data_export`` node name, or ``None`` when nothing shipped.
        """
        # (transform, shape) per record. Primary scan: TRANSFORM markers (one
        # per instance -- the current marker home). Legacy scan: shape markers
        # from commits that predate the transform move; an instanced shape has
        # one full path per instance, so those are deduped by UUID and keyed by
        # their first transform (legacy scenes are necessarily non-instanced --
        # the old flow refused instances outright).
        pairs: List[Tuple[str, Optional[str]]] = []
        marked_transforms: set = set()
        for transform in cmds.ls(type="transform", long=True) or []:
            if cmds.attributeQuery(
                self.LIGHTMAP_INFO_ATTR, node=transform, exists=True
            ):
                pairs.append((transform, NodeUtils.get_shape(transform)))
                marked_transforms.add(transform)
        seen_shape_uuids: set = set()
        for shape in cmds.ls(type="mesh", long=True) or []:
            if not cmds.attributeQuery(
                self.LIGHTMAP_INFO_ATTR, node=shape, exists=True
            ):
                continue
            uuid = (cmds.ls(shape, uuid=True) or [None])[0]
            if uuid in seen_shape_uuids:
                continue
            seen_shape_uuids.add(uuid)
            parents = (
                cmds.listRelatives(shape, allParents=True, fullPath=True) or []
            )
            transform = parents[0] if parents else shape
            if transform in marked_transforms:
                continue  # already represented by a transform marker
            pairs.append((transform, shape))

        objects: List[Dict[str, Any]] = []
        marker_infos: List[Dict[str, Any]] = []
        for transform, shape in pairs:
            info = self._marker_info(transform)
            if not info:
                continue
            marker_infos.append(info)
            # The engine matches by the GameObject (transform) name, so publish
            # exactly what the export carries: the DAG path goes (no format has
            # one) but the NAMESPACE stays. Measured end to end -- Maya writes
            # `NS:leaf` as the FBX Model name and FBX2glTF preserves the colon
            # into the glTF node name -- so stripping it did two kinds of damage
            # on a referenced scene: it invented duplicates between modules that
            # merely share leaf names (VDATS_DA:vdat352 vs VDATS_RF:vdat352 are
            # distinct everywhere downstream), and it broke the join outright,
            # since Unity's FindRenderer compares against `VDATS_DA:vdat352`
            # while the manifest offered `vdat352`. blendertk needs no
            # equivalent: Blender enforces scene-unique object names, so its
            # published name is already the exported one.
            name = transform.rsplit("|", 1)[-1]
            # Publish the lightmap set's REAL channel index. Unity's native
            # lightmaps only ever sample uv2 (index 1) -- anything else means
            # the export will sample the wrong channel, so warn loudly instead
            # of shipping a hardcoded 1 that hides the problem.
            uv_set = info.get("uv_set")
            sets = (
                list(
                    dict.fromkeys(
                        cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                    )
                )
                if shape
                else []
            )
            uv_index = sets.index(uv_set) if uv_set in sets else 1
            if shape and uv_set and uv_set not in sets:
                self.logger.warning(
                    "%s: committed lightmap set %r no longer exists; "
                    "publishing uvIndex 1 on faith. Re-run create_lightmap_uvs "
                    "if the set was renamed or removed.",
                    name,
                    uv_set,
                )
            if uv_index != 1:
                self.logger.warning(
                    "%s: lightmap set %r sits at UV index %d, but Unity samples "
                    "uv2 (index 1). Re-run create_lightmap_uvs (it reorders to "
                    "index 1) before exporting.",
                    name,
                    uv_set,
                    uv_index,
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

        payload: Dict[str, Any] = {
            "version": self.LIGHTMAP_METADATA_VERSION,
            "objects": objects,
        }
        # The maps' common home, lifted from the markers: the locate hint for
        # consumers that only hold the manifest (ptk.MeshConvert reads it back out
        # of a converted GLB). Optional and additive -- unitytk's JsonUtility
        # ignores unknown fields, and readers fall back to searching when absent.
        dirs = {d for d in (m.get("dir") for m in marker_infos) if d}
        if len(dirs) == 1:
            payload["dir"] = next(iter(dirs))
        manifest = json.dumps(payload)
        return DataNodes.set_export_string(self.LIGHTMAP_METADATA, manifest)

    def revert_lightmap(self, objects: Optional[List[str]] = None) -> List[str]:
        """Undo :meth:`commit_lightmap` -- drop the markers + republish.

        The material and texture UV0 were never changed, so this removes the
        :attr:`LIGHTMAP_INFO_ATTR` markers (the objects leave the export
        manifest) and, for a LEGACY atlas commit, restores the lightmap UV set
        to its original unit-square layout (inverting the recorded ``uvRect``;
        current commits bind the rect as ``scaleOffset`` and never touch UVs);
        the baked texture and the UV set itself are left in place (harmless,
        reused by the next bake). Markers are cleared from BOTH possible homes
        -- the transform (current) and the shape (legacy). With
        ``objects=None`` it clears **every** marked object.

        Returns the long names of the nodes cleared.
        """
        if cmds is None:
            return []

        if objects is None:
            candidates = [
                n
                for kind in ("transform", "mesh")
                for n in (cmds.ls(type=kind, long=True) or [])
                if cmds.attributeQuery(self.LIGHTMAP_INFO_ATTR, node=n, exists=True)
            ]
        else:
            candidates = list(objects)

        cleared: List[str] = []
        for obj in candidates:
            marker = self._marker_node(obj)
            if not marker:
                continue
            info = self._marker_info(obj)
            # Clear every home the marker occupies: the resolved node, plus a
            # stale twin on the other node (a legacy shape marker superseded by
            # a transform re-commit, or vice versa).
            transform = NodeUtils.get_transform_node(obj) or obj
            shape = NodeUtils.get_shape(obj)
            failed = False
            for node in dict.fromkeys(n for n in (marker, transform, shape) if n):
                if not cmds.attributeQuery(
                    self.LIGHTMAP_INFO_ATTR, node=node, exists=True
                ):
                    continue
                try:
                    cmds.deleteAttr(f"{node}.{self.LIGHTMAP_INFO_ATTR}")
                    if node not in cleared:
                        cleared.append(node)
                except RuntimeError as e:
                    self.logger.warning(
                        "Could not clear lightmap marker on %s: %s", node, e
                    )
                    failed = True
            if failed:
                continue  # marker intact -> leave the UV remap recorded too
            if shape:
                self._restore_lightmap_uvs(shape, info)
        if cleared:
            self._publish_lightmap_metadata()
        return cleared

    def revert(self, objects: Optional[List[str]] = None) -> List[str]:
        """Undo the lightmap wiring -- the spelling the panel and pre-bake use.

        Kept as its own name (rather than callers reaching for
        :meth:`revert_lightmap`) because it is the stable "undo whatever this
        workflow did" entry point.
        """
        return self.revert_lightmap(objects)

    @staticmethod
    def _collect_marked_shapes(attr: str, objects: Optional[List[str]]) -> List[str]:
        """Shapes to revert: those carrying *attr* (``objects=None`` → all in scene).

        ``None`` means "every mesh marked with this attr"; an explicit list maps
        each transform to its shape (callers still re-check the marker per shape).
        """
        if objects is None:
            return [
                s
                for s in (cmds.ls(type="mesh", long=True) or [])
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
                os.path.basename(path),
                bad,
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
    def _coverage_mask(cls, uv_triangles, size) -> Optional[Any]:
        """Bool mask of the texels the lightmap layout FULLY covers, or ``None``.

        *size* is the map's ``(height, width)`` in texels -- not a Maya shape,
        which is what ``shape`` means everywhere else in this module.

        Rasterizes *uv_triangles* at the map's own resolution and keeps only
        texels reported at complete coverage (:attr:`_COVERAGE_FULL`), then
        erodes the reconstruction filter's reach off that
        (:attr:`_COVERAGE_ERODE`). ``None`` when the map is not square, which
        is the one shape :meth:`pythontk.ImgUtils.rasterize_uv_triangles`
        cannot describe (lightmaps are square by construction).

        Both fallbacks are deliberate: an empty raster (a layout that missed
        the map entirely) and an empty erosion (an island thinner than the
        filter) return the wider mask rather than nothing, because a mask that
        covers no texel would refill the whole image from its own gutters.
        """
        import cv2
        import numpy as np

        h, w = size
        if h != w:
            return None
        supersample = 4 if w <= cls._COVERAGE_SUPERSAMPLE_MAX_SIZE else 2
        cover = ptk.ImgUtils.rasterize_uv_triangles(
            uv_triangles, size=w, supersample=supersample
        )
        full = cover >= cls._COVERAGE_FULL
        if not full.any():
            return None
        if cls._COVERAGE_ERODE > 0:
            # cv2's erode border value is +inf, so a texel is never eroded for
            # merely sitting on the frame -- an island legitimately running to
            # u/v 0 or 1 keeps its edge.
            eroded = cv2.erode(
                full.astype(np.uint8),
                np.ones((3, 3), np.uint8),
                iterations=cls._COVERAGE_ERODE,
            ).astype(bool)
            if eroded.any():
                full = eroded
        return full

    @classmethod
    def _dilate_lightmap(
        cls,
        path: str,
        alpha_threshold: float,
        iterations: int,
        uv_triangles: Optional[Any] = None,
    ) -> bool:
        """Edge-pad one baked EXR in place, keeping only texels the bake owns.

        Three independent pieces of evidence decide which texels are this
        object's lighting; everything else is refilled from those that are:

        * **Alpha** from ``arnoldRenderToTexture`` -- the nominal coverage
          signal. Partial-coverage texels are unpremultiplied first (RTT
          stores ``alpha * L``).
        * **UV coverage** (*uv_triangles*, see :meth:`_coverage_mask`) -- the
          decisive one in practice, because RTT with ``-extend_edges`` writes
          alpha 1.0 across the WHOLE frame (measured, mtoa 5.5): it RENDERS
          the edge extension rather than leaving it uncovered, so alpha cannot
          separate an island's own texels from the ring baked past its border.
          That ring is not this object's lighting -- a point just past a wall
          panel's edge is coplanar with the neighbouring panel, so its rays hit
          that panel and it bakes dark. Profiled on a shipped room, island
          border texels ran from 0.015x to 1.09x their interior, and the
          atlas resample folded that into a dashed outline around every panel.
        * **Radiance** -- RENDERED-DEAD texels (full alpha, ~zero radiance:
          geometry below a floor slab, behind trim, inside a panel overlap)
          are occlusion, not signal (see :attr:`_DEAD_TEXEL_FRACTION`).

        Coverage is applied BEFORE the dead-texel test on purpose: extension
        texels are dark, and leaving them in would drag the lit median the
        test calibrates against.

        The alpha is dropped on write: a lightmap is consumed as opaque RGB,
        and a partial-coverage alpha would be misread as transparency.

        Returns False (a no-op) when the image has no alpha channel.
        """
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        if img is None:
            raise RuntimeError(f"unreadable EXR: {path}")
        if img.ndim != 3 or img.shape[2] < 4:
            return False  # no coverage channel -> nothing safe to dilate from

        import numpy as np

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
        # The UV layout is the only evidence that separates the island from the
        # extension ring rendered past its border (see this method's docstring);
        # intersected here, before the radiance test calibrates on the survivors.
        if uv_triangles is not None:
            covered = cls._coverage_mask(uv_triangles, bgr.shape[:2])
            # Only when something survives: a layout that does not intersect
            # the alpha coverage at all is a mismatched set, not an empty bake,
            # and an empty mask would refill the map from its own gutters.
            if covered is not None and (mask & covered).any():
                mask &= covered
        # Alpha alone is not sufficient: RTT can write alpha == 1.0 across
        # the WHOLE frame (measured: OFFICE_ENV walls, mtoa 5.5), and a texel
        # whose geometry is buried -- below the floor slab, behind a
        # baseboard or door leaf, inside a panel overlap -- renders with full
        # coverage and ~zero radiance. Those texels are not signal: packed
        # and downscaled, they smear into visible dark borders at the
        # junctions they hide behind. Radiance relative to the map's own lit
        # level is the only thing that separates them from real content --
        # the cut sits ~10x above the occluded corridor's GI leak-through and
        # ~20x below genuine contact shadow (see _DEAD_TEXEL_FRACTION).
        lum = bgr.max(axis=-1)
        lit = mask & (lum > cls._DEAD_TEXEL_ABS)
        if lit.any():
            dead = mask & (
                lum
                <= max(
                    cls._DEAD_TEXEL_ABS,
                    cls._DEAD_TEXEL_FRACTION * float(np.median(lum[lit])),
                )
            )
            if dead.any():
                mask &= ~dead
        if not mask.all():
            bgr = ptk.ImgUtils.dilate_image(bgr, mask=mask, iterations=iterations)
            # Then fill the REST of the background: anything left at zero is
            # averaged into content by every coarser mip level the engine
            # generates -- a black background reads as a dark halo around the
            # island at distance/grazing angles, i.e. a seam on tiled
            # geometry. The bounded ring above keeps the near-island gutter
            # smooth; nearest-fill covers the far field in one O(n) pass.
            grown = mask | (bgr > 0).any(axis=-1)
            bgr = ptk.ImgUtils.fill_empty_texels(bgr, mask=grown)
        cls._write_lightmap_exr(path, bgr)  # opaque RGB (alpha dropped)
        return True


class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the ``lightmap_baker.ui`` panel.

    Composition over inheritance: a thin driver over :class:`LightmapBaker`
    (the workflow) — no bake logic lives here. **Bake Lightmaps** (``b000``)
    runs the whole pipeline for the selected objects and wires the result up so
    nothing is left to do afterward: :meth:`LightmapBaker.bake_separated` +
    :meth:`~LightmapBaker.commit_lightmap` keep the full PBR material and
    texture UVs, bake lighting onto UV1, and stamp Unity metadata on the shared
    ``data_export`` carrier. The maps survive; the engine composites.

    ``b000`` first calls :meth:`LightmapBaker.revert` to clear any
    prior wiring so the bake samples the real material. It is non-destructive
    (source material / UVs preserved, restore data stamped on the mesh): the
    header menu's **Revert to Source** undoes it. The Quality combobox is
    populated from :meth:`LightmapBaker.preset_store` and fills the Resolution /
    Samples dials, which are the source of truth at bake time.
    """

    # Packing labels for the Packing combobox (cmb002). Per-Object (index 0, the
    # default) keeps one full-resolution map per object; Atlas by Material
    # consolidates a material group into one shared EXR + a per-object
    # scaleOffset rect; _packing() reads it back.
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
    _LIGHTING_ONLY_TAIL = (
        "Maps kept; lightmap + Unity metadata stamped. Export the FBX."
    )

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
            setToolTip="Open the folder the lightmaps are written to (the "
            "Output Directory field, or the project's sourceimages) in Explorer.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
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
                    "Optionally set an <b>Output Directory</b> — empty writes to "
                    "the project's <i>sourceimages</i>; a relative entry (e.g. "
                    "<i>lightmaps</i>) lands under it, so the setting travels "
                    "with the project; an absolute one is used as-is.",
                    "Press <b>Bake Lightmaps</b>, then export the FBX. "
                    "<b>Include the hidden <i>data_export</i> node</b> in the "
                    "export (use <i>Export All</i>, or mayatk's Scene Exporter, "
                    "which adds it automatically) — a plain <i>Export Selection</i> "
                    "of just the meshes omits it and the Unity wiring won't ship.",
                ],
                sections=[
                    (
                        "Mode: Lighting Only — real lightmapping (default)",
                        [
                            "This is how you normally light-map. Bakes <i>lighting "
                            "only</i> (white-card irradiance) onto a second UV "
                            "channel; your full PBR material — albedo, normal, "
                            "metallic/roughness — is <b>kept untouched</b>.",
                            "The lightmap is a <b>separate texture asset</b> (written "
                            "to the project's <i>sourceimages</i>, alongside your "
                            "other maps) — the engine multiplies albedo × lightmap "
                            "at runtime and your normal map still lights normally. "
                            "Exactly how Unity's own lightmaps work.",
                            "A <b>Per-Object</b> export is self-contained: the "
                            "mesh's UV2 samples its map directly, so it works in "
                            "any engine (or any shader that reads a lightmap on "
                            "UV2) with no extra setup. Import <i>sourceimages</i> "
                            "as usual so the lightmap is in the project.",
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
                            "object gets an area-weighted atlas rect (bigger objects "
                            "get more texels) bound at engine time, exactly Unity's "
                            "native <i>lightmapScaleOffset</i> / glTF "
                            "<i>KHR_texture_transform</i>. UVs are never edited, and "
                            "<b>instances are fully supported</b> — every copy keeps "
                            "its own rect and its own lighting. Fewer textures, no "
                            "naming collisions. The bake itself is unchanged either "
                            "way.",
                        ],
                    ),
                    (
                        "Non-destructive",
                        [
                            "Nothing is deleted — the source material and UVs stay "
                            "in the scene and the restore data is stamped on the "
                            "mesh.",
                            "<b>Revert to Source</b> (header menu) undoes the wiring "
                            "on the selected, or all baked, objects.",
                            "Re-baking auto-reverts first, so it always bakes the "
                            "real material.",
                        ],
                    ),
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

        ``visible`` and ``scene`` gather across the scene so a bake needn't be
        preceded by a manual select-all; ``selected`` takes the selection as-is.

        Every scope resolves through ``TextureBaker.resolve_meshes`` -- the one
        definition of "bakeable" -- so a selection that also holds the room's
        LIGHTS (which the Blender-bridge bake genuinely needs selected, and this
        Arnold path does not) bakes the geometry instead of asking Arnold to
        render a quad_light. It also keeps the empty-scope message below honest:
        a lights-only selection reads as nothing to bake, not as a bake that
        silently produced no maps.
        """
        scope = self._scope()
        if scope == "visible":
            from mayatk.display_utils._display_utils import DisplayUtils

            pool = DisplayUtils.get_visible_geometry(inherit_parent_visibility=True)
        elif scope == "scene":
            pool = cmds.ls(type="mesh", noIntermediate=True, long=True)
        else:
            pool = cmds.ls(selection=True, long=True)
        return TextureBaker.resolve_meshes(pool or [])

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

    def txt_output_dir_init(self, widget) -> None:
        """Add a directory browser to the optional output-directory field.

        No clear button: the value arrives from the browse dialog as often as
        it is typed, and a mis-click would drop a path the user picked and
        can't retype -- the field's *empty* default is one keystroke away
        anyway (see :meth:`_output_dir`).
        """
        widget.option_box.browse(
            mode="directory",
            title="Lightmap output directory",
            tooltip="Browse for the lightmap output directory…",
            start_dir=self._output_dir,
            callback=self._relativize_output_dir,
        )

    def _relativize_output_dir(self, path: str) -> None:
        """Store a browsed dir under sourceimages as a *relative* path.

        The dialog can only hand back an absolute path, but the portable form
        is the relative one: a project moved (or a teammate's copy) still bakes
        into the same subfolder. Anything outside sourceimages is left absolute
        -- that is what the user picked, and there is no shorter honest way to
        write it.
        """
        base = self._sourceimages_dir()
        if not (path and base and ptk.FileUtils.is_under(path, base)):
            return
        rel = ptk.FileUtils.convert_to_relative_path(path, base, prepend_base=False)
        self.ui.txt_output_dir.setText("" if rel == "." else rel)

    def _output_dir(self) -> Optional[str]:
        """The bake's output directory: the field, resolved against sourceimages.

        Empty field -> the project's sourceimages (the conventional, portable
        home for material-referenced textures). A subdirectory entry is joined
        onto it so the setting survives a project move; a full path is taken
        as-is. The directory itself is created by the bake.

        Falls back to the base :meth:`TextureBaker.default_output_dir` would
        pick when there is no project, rather than handing the workflow a
        *relative* directory: ``os.makedirs`` would create that against the
        process CWD, which in Maya is wherever the app was launched from.
        """
        # "baked_lighting", not the signature default: that is the subdir the
        # bake would have landed in on its own, so an empty field resolves to
        # exactly where it used to.
        base = self._sourceimages_dir() or TextureBaker.default_output_dir(
            "baked_lighting"
        )
        return ptk.FileUtils.resolve_output_dir(self.ui.txt_output_dir.text(), base)

    def txt000_init(self, widget) -> None:
        """Add the Prefix / Suffix / Auto picker to the name-affix field."""
        widget.option_box.clear_option = True
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
        """Bake lightmaps for the selection (revert → bake → commit)."""
        objects = self._scope_objects()
        if not objects:
            self.ui.footer.setText(
                "Select one or more mesh objects to bake."
                if self._scope() == "selected"
                else f"No meshes found for scope '{self._scope()}'."
            )
            return

        # A saved scene keeps the authored lights' marker but not the session
        # that made them: lights authored before per-area emission reopen
        # NORMALIZED and bake ~100x dim, and a manual Normalize fix evaporates
        # with every reopen. Upgrade the tool's OWN artifacts before the bake
        # renders them; hand-authored lights are never touched.
        upgraded = LightUtils.upgrade_authored_lights()
        if upgraded:
            self.logger.warning(
                "Upgraded %d authored light(s) to per-area emission "
                "(Normalize off): %s",
                len(upgraded),
                ", ".join(n.rsplit("|", 1)[-1] for n in upgraded),
            )

        self._baker = LightmapBaker(
            resolution=self._resolution(),
            samples=self.ui.spn_samples.value(),
            **getattr(self, "_preset_gi", {}),
        )
        # Clear any prior lightmap marker so the bake samples the real material
        # and the result starts clean.
        self._baker.revert(objects)

        # Where the maps land: the Output Directory field resolved against the
        # project's sourceimages, or against <scene>/baked_lighting when there
        # is no project (see _output_dir -- resolved HERE, so the workflow is
        # never handed a relative directory).
        src = self._output_dir()
        # Name the output <object><affix> per the field (e.g. "<object>_Lightmap"),
        # following the texture-set convention; the shader inherits the name.
        # An empty field falls back to the placeholder default (the .ui's
        # single source for it), so a cleared field never bakes affix-less
        # files that could collide with source texture names.
        field = self.ui.txt000
        affix = field.text().strip() or field.placeholderText()
        prefix, suffix = field.option_box.resolve_affix(affix, default="suffix")
        # Indeterminate marquee + per-object text in OUR footer. Deliberately not a
        # determinate 0..100% bar: a single Arnold bake is one opaque blocking call
        # with no sub-progress, so a percentage would sit at 0 and jump -- which is
        # exactly what mtoa's own popup does. The text still reports object i / N,
        # which is the part that tells the artist the run is alive and how far in.
        with self.ui.footer.progress(text="Baking lightmaps…") as update:
            result = self._baker.bake_separated(
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

        if self._packing() == "atlas":
            result, tail = self._commit_atlas(result, src, prefix, suffix)
        else:
            self._baker.commit_lightmap(result)
            tail = self._LIGHTING_ONLY_TAIL
        self._last_output_dir = os.path.dirname(next(iter(result.values())))
        count = len(result)
        self.ui.footer.setText(
            f"Baked {count} object{'s' if count != 1 else ''} → "
            f"{self._last_output_dir}. {tail}"
            + self._black_bake_warning(result)
        )

    # A committed lightmap whose brightest map's mean sits below this is not a
    # dark look, it is an unlit render (measured: a production room lit only by
    # intensity-1 normalized area lights baked to 0.008; the same room lit
    # properly means 1.0+ -- two orders of magnitude, so the line is not fine).
    _BLACK_BAKE_MEAN: float = 0.02

    def _black_bake_warning(self, mapping: Dict[str, str]) -> str:
        """A footer warning when the committed maps are essentially unlit, else ''.

        The bake pipeline renders whatever light the scene supplies -- a black
        result is FAITHFUL, so nothing upstream errors, and the artist finds
        out in the web preview where it reads as a pipeline bug (measured: a
        session whose generated area lights sat at the default intensity 1
        baked a 0.008-mean atlas that shipped all the way to a black WebXR
        room). This closes that gap at the moment of bake. cv2-gated;
        unreadable maps are simply skipped -- the guard must never break a
        finished bake.
        """
        try:
            os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
            import cv2

            means = []
            for path in set(mapping.values()):
                img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
                if img is not None:
                    means.append(float(img[..., :3].mean()))
            if not means or max(means) >= self._BLACK_BAKE_MEAN:
                return ""
            peak = max(means)
        except Exception:
            return ""
        self.logger.warning(
            "Bake is essentially BLACK (brightest map mean %.4f). The bake "
            "renders the scene's own lights: a NORMALIZED area light at "
            "fixture scale bakes ~100x dimmer than its intensity suggests -- "
            "turn Normalize OFF on area lights (per-area emission; the panel "
            "does this automatically for lights it authored, so a normalized "
            "light here is hand-made) -- and check lights are "
            "visible/unmuted. StingrayPBS emissive lights a bake only when "
            "the translation guard bridges it to Arnold (TextureBaker."
            "arnold_translation_guard, on by default).\n"
            "Scene lights at bake time:\n%s",
            peak,
            self._light_audit(),
        )
        return (
            "  WARNING: bake is essentially BLACK — check light intensities "
            "(see Script Editor)."
        )

    @staticmethod
    def _light_audit() -> str:
        """One line per scene light: the attrs that decide whether a bake is lit.

        Attached to the black-bake warning so a dark result carries its own
        diagnosis -- intensity, exposure, normalize, emitter scale and
        visibility are exactly the dials a black bake was traced to in
        production, and none of them are visible in the bake output itself.
        """
        rows = []
        for shape in cmds.ls(lights=True, long=True) or []:
            try:
                t = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
                sx, sy, _sz = cmds.getAttr(f"{t}.scale")[0]
                bits = [
                    f"intensity={cmds.getAttr(f'{shape}.intensity'):g}",
                    f"scale={sx:g}x{sy:g}",
                    f"visible={cmds.getAttr(f'{t}.visibility')}",
                ]
                for attr, label in (("aiExposure", "exposure"), ("aiNormalize", "normalize")):
                    if cmds.attributeQuery(attr, node=shape, exists=True):
                        bits.append(f"{label}={cmds.getAttr(f'{shape}.{attr}'):g}")
                rows.append(f"  {t.rsplit('|', 1)[-1]}: " + "  ".join(bits))
            except Exception:
                rows.append(f"  {shape}: <unreadable>")
        return "\n".join(rows) or "  <no lights in the scene>"

    def _commit_atlas(
        self,
        result: Dict[str, str],
        output_dir: Optional[str],
        prefix: str,
        suffix: str,
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
        # scale_offsets is THE engine binding (Unity lightmapScaleOffset / glTF
        # KHR_texture_transform): it survives into the export manifest, and it
        # is what lets every INSTANCE of a shared mesh own a distinct rect.
        self._baker.commit_lightmap(
            mapping, scale_offsets={obj: so for obj, (_path, so) in packed.items()}
        )
        n = len(set(mapping.values()))
        return mapping, (
            f"Consolidated into {n} atlas map{'s' if n != 1 else ''}; each "
            "object samples its own atlas rect at engine time. Export the FBX."
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
        """Open the bake's output folder in Explorer.

        The Output Directory field's resolved target when it points somewhere
        that exists, else the project's sourceimages it resolves against -- a
        menu item labelled "where the bakes go" that opened the *base* of a
        custom relative path would be one click short of the truth.
        """
        src = self._output_dir()
        if src and not os.path.isdir(src):  # not baked into yet
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
