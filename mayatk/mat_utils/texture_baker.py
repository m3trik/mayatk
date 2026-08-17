# !/usr/bin/python
# coding=utf-8
"""Bake an object's shaded surface (material under scene lighting) to a texture.

The low-level, generic **bake primitive** (mat_utils): it only renders each
object's shaded appearance to a per-object texture (with optional UV-set
targeting), independent of any higher-level pipeline. It captures whatever the
render shows -- material x lighting / GI -- not arbitrary AOVs (it does not bake
normal / AO / curvature maps). The lighting *workflow* on top of it (lightmap
UV2 generation, dilation, engine export prep, presets) is
:class:`mayatk.LightmapBaker`, which *composes* this class; use this directly
for one-off / preview bakes.

Two backends, picked automatically by :meth:`TextureBaker.bake`:

* **Arnold** (when the ``mtoa`` plugin is loaded) -- uses
  :func:`arnoldRenderToTexture`. Highest quality available natively in
  Maya 2025; respects all lights / aiSkyDomeLight / GI bounces.
* **convertSolidTx** (always available) -- the built-in MEL command that
  samples the assigned material with current scene lighting and writes
  a PNG. Lower quality than Arnold but zero external dependencies.

Standalone Maya utility: produces texture files on disk. Consumers (the
tentacle lighting UI, custom scripts) decide what to do with the output.
:meth:`TextureBaker.assign_to_diffuse` is provided as an optional,
reversible helper for previewing the result in the viewport.
"""
import contextlib
import glob
import os
import shutil
import statistics
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    cmds = None
    mel = None
    print(__file__, error)

import pythontk as ptk

from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes


# Heuristic: convertSolidTx is the lowest-common-denominator backend, but
# its output is noisy at default settings. Bumping samples here trades
# bake time for quality without changing the per-call signature.
_CONVERT_SOLID_TX_DEFAULTS: Dict[str, Any] = {
    "antiAlias": True,
    "samplePlane": 0,        # sample on the surface
    "shadows": True,
    "alpha": False,          # keep RGB; alpha handled separately if needed
    "doubleSided": False,
    "componentRange": False,
    "fillTextureSeams": True,
    "fileFormat": "png",
}


class TextureBaker(ptk.LoggingMixin):
    """Bake scene lighting per object to a texture file (PNG, EXR, ...).

    Usage::

        baker = TextureBaker(file_format="exr")
        out = baker.bake(cmds.ls(selection=True), output_dir="C:/tmp/bakes")
        # out: {object_long_name: baked_file_path}

    The caller can then either:
      * import the textures externally (e.g. as anchors/layers in DCC tools), or
      * call :meth:`assign_to_diffuse` to wire each baked texture into the
        object's existing material's color slot for viewport preview.

    Both Arnold and ``convertSolidTx`` backends require:
      * The mesh has UVs (no overlapping checks are performed).
      * At least one material is assigned to the mesh.
      * The scene has lights (otherwise the bake is the material's
        unlit base color).
    """

    def __init__(
        self,
        resolution: int = 2048,
        samples: int = 5,
        file_format: str = "png",
        render_settings: Optional[Dict[str, Any]] = None,
        extend_edges: bool = True,
        translation_guard: bool = True,
        pixel_filter: str = "gaussian",
        filter_width: float = 2.0,
    ):
        super().__init__()
        # Per-instance knobs -- overriding ``TextureBaker.resolution`` at the
        # class scope would mutate global state, so they live on the instance.
        self.resolution = resolution
        self.samples = samples
        self.file_format = file_format
        # Reconstruction filter for the RTT render. Gaussian 2.0 (Arnold's
        # own default) is RIGHT for a bake and box 1.0 is measurably worse,
        # which is the opposite of the usual "a bake is a texture, use box"
        # intuition -- so it is pinned, and measured. At an island border the
        # neighbouring texels are the edge-extension region, whose shading
        # belongs to a different place on the model (past a wall panel's top
        # edge is the brighter wall above it). A gaussian is CENTER-weighted,
        # so a border texel stays mostly its own content; a box takes
        # everything in its footprint at full weight. Measured end to end on
        # two stacked production wall panels, one parameter apart -- border
        # texel vs its own extrapolated interior: gaussian +2.5%/+1.7% (0.8%
        # discontinuity across the joint), box +34.1%/-0.5% (34.5%). A
        # parameter rather than a constant because callers baking isolated
        # props with no shared edges may still prefer the sharper filter.
        self.pixel_filter = str(pixel_filter)
        self.filter_width = float(filter_width)
        # Bake past the UV island border (RTT's own -extend_edges). On by
        # default: without it Arnold writes partial-coverage edge texels with
        # RGB premultiplied by coverage -- a dark ring around every island, and
        # a dark seam wherever two tiles meet. Off is for callers that need the
        # island footprint to stay legible in the output (the UV-targeting
        # tests read which layout rendered from where the content lands, and
        # edge extension deliberately fills the background).
        self.extend_edges = bool(extend_edges)
        # Stand in for game (ShaderFX) materials during Arnold bakes. MtoA
        # cannot translate them and renders their surfaces ERROR MAGENTA, and
        # with GI on that magenta BOUNCES: measured on a production room, the
        # floor around StingrayPBS racks baked magenta-tinted shadows
        # (dark-texel chroma R/G/B 3.00/0.21/2.89 -- ~85% pure (1,0,1)) while
        # objects away from them stayed neutral. See
        # :meth:`arnold_translation_guard`.
        self.translation_guard = bool(translation_guard)
        # ``defaultArnoldRenderOptions`` attrs to pin for the bake (e.g.
        # {"GIDiffuseDepth": 3, "GIDiffuseSamples": 4}). The RTT command only
        # takes aa_samples as a flag -- GI depth/samples come from the scene's
        # render options, so an untouched scene bakes at Arnold's 1-bounce,
        # 2-sample defaults AND the user's settings leak into the bake. This
        # dict is snapshot/set/restored around the bake (Arnold backend only),
        # making quality deterministic. None/empty leaves the scene untouched.
        self.render_settings: Dict[str, Any] = dict(render_settings or {})
        # State for assign_to_diffuse / restore_diffuse_connections.
        # Each entry: (color_attr, prev_source_plug, prev_static_value, baked_path).
        # prev_source_plug is "" if the slot was driven by a static setAttr.
        # prev_static_value is None when an incoming connection was in place.
        self._restore_state: List[Tuple[str, str, Optional[tuple], str]] = []

    # ------------------------------------------------------------------
    # Backend detection
    # ------------------------------------------------------------------

    @staticmethod
    def arnold_available() -> bool:
        """True if the ``mtoa`` plugin is loaded AND its bake cmd is registered."""
        if cmds is None:
            return False
        from mayatk.env_utils._env_utils import EnvUtils

        if not EnvUtils.is_plugin_loaded("mtoa"):
            return False
        # mtoa registers the bake command on load. Maya 2025 cmds has no
        # listCommands(), so probe the command attribute directly.
        return hasattr(cmds, "arnoldRenderToTexture")

    # ------------------------------------------------------------------
    # Top-level bake API
    # ------------------------------------------------------------------

    def _place_output(self, src: str, dst: str, used: set) -> str:
        """Move a finished bake to *dst*, taking an adjacent name if *dst* is locked.

        ``os.replace`` onto a destination another process holds open raises
        ``WinError 32`` on Windows, and the caller treated that as a failed bake --
        so a previous map still held by Maya's texture cache, a viewer, or (measured
        on a synced project drive) a cloud-sync client mid-upload would silently
        cost the artist that object's map after the render had already been paid
        for. Losing a finished bake to a file lock is never the right answer: the
        render is the expensive part, the filename is not.

        Reports the OUTCOME, never the first symptom. A refused rename says
        nothing about which file is held -- when it is the freshly written
        SOURCE (the common case on a synced drive) every adjacent name is
        refused too and the map still lands under its intended name a moment
        later, so warning on the first failure alarms the artist about files
        that turned out fine. Nothing is logged unless the map ended up
        somewhere other than *dst*, or had to be copied to get there.

        Returns the path actually written (the caller records THAT, so the manifest
        and the committed marker never name a file the bake did not produce).
        Mirrors ``blendertk.LightmapBaker._place``'s contract.

        BOUNDED: a lock on the destination FILE clears under a new name on the first
        retry, but a locked SOURCE (the sync client indexing the just-written
        render) or a locked directory fails every name equally -- and an unbounded
        rename loop there would hang Maya rather than report anything. After
        :data:`_PLACE_ATTEMPTS` there is one paused retry and then a COPY (a
        read-share lock still permits reading); only a truly unwritable directory
        raises, for the caller to log as a real failure for that object.
        """
        if os.path.abspath(src) == os.path.abspath(dst):
            return dst
        stem, ext = os.path.splitext(dst)
        candidate = dst
        for attempt in range(self._PLACE_ATTEMPTS):
            try:
                os.replace(src, candidate)
                if attempt:
                    self.logger.warning(
                        "%s is held by another process (cloud sync, or open in "
                        "a viewer); wrote this bake as %s instead.",
                        os.path.basename(dst),
                        os.path.basename(candidate),
                    )
                return candidate
            except PermissionError:
                # Only a locked destination is retryable under a new name. A missing
                # source or an unwritable directory raises something else (or runs
                # out of attempts below) instead of spinning.
                k = attempt + 1
                candidate = f"{stem}_{k}{ext}"
                while candidate in used or os.path.exists(candidate):
                    k += 1
                    candidate = f"{stem}_{k}{ext}"
        # Every candidate was refused, so no destination NAME is the problem:
        # either the SOURCE itself is held (the sync client indexing the
        # just-written render -- measured: 4 of a production room's 46 maps
        # stayed under their raw RTT names, dropped out of the atlas, and
        # rendered as BLACK objects in the preview) or the directory is
        # unwritable. A brief pause clears most sync locks; failing that, a
        # read-share lock still permits COPYING, so the finished bake always
        # lands at the recorded path and only the locked stray is left to the
        # sync client. A truly unwritable directory makes the copy raise --
        # bounded, and a real failure for the caller to log.
        time.sleep(0.25)
        try:
            os.replace(src, dst)
            return dst
        except PermissionError:
            pass
        shutil.copy2(src, dst)
        self.logger.warning(
            "%s was still held while being placed (cloud sync indexing the "
            "fresh render?); copied it to %s instead -- the locked original "
            "may linger beside it until the sync finishes.",
            os.path.basename(src),
            os.path.basename(dst),
        )
        try:
            os.remove(src)
        except OSError:
            pass
        return dst

    #: Adjacent-name retries before the paused-retry-then-copy tail takes over.
    #: Small on purpose -- one retry clears a single locked FILE; needing many means
    #: the SOURCE or the directory is the locked thing, which renaming cannot fix.
    _PLACE_ATTEMPTS: int = 5

    @staticmethod
    def default_output_dir(subdir: str = "baked_textures") -> str:
        """``<subdir>`` next to the saved scene, else under the workspace root.

        The base :meth:`bake` writes to when no ``output_dir`` is given, exposed
        so a caller resolving a user-entered *subdirectory* has the same
        absolute base to join onto instead of handing on a relative path
        (``os.makedirs`` would create that against the process CWD -- in Maya,
        wherever the app was launched from). Mirrors blendertk's twin.
        """
        scene = cmds.file(query=True, sceneName=True)
        root = (
            os.path.dirname(scene)
            if scene
            else cmds.workspace(query=True, rootDirectory=True)
        )
        return os.path.join(root, subdir)

    @staticmethod
    def resolve_meshes(objects=None) -> List[str]:
        """Normalize *objects* (names / components / ``None`` = selection) to mesh transforms.

        Both backends render a SURFACE: a light, a locator or an empty group has
        nothing to render, and handing one to Arnold RTT does not degrade -- it
        raises per object (``quad_light nodes are not supported types`` /
        ``not exported to Arnold world``) and reports success while writing no
        file, so the caller sees a pile of warnings instead of an answer. A
        selection is a rough gesture ("bake this room"), so filtering here is
        what makes it one: every caller -- the panel's scopes, the API, the
        bridges -- gets the same definition of bakeable instead of restating it.

        Mirrors ``blendertk.TextureBaker.resolve_meshes`` (name + behavior, not
        signature: Maya passes node strings, bpy passes object refs).

        Returns deduped long transform names, each owning a non-intermediate
        mesh shape.
        """
        if cmds is None:
            return []
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        pool: List[str] = []
        for node in cmds.ls(ptk.make_iterable(objects), long=True) or []:
            # A component ("pCube1.f[0]") or a shape both resolve through their
            # transform, so a face selection bakes the object it belongs to.
            transform = node.split(".")[0]
            if cmds.objectType(transform, isAType="shape"):
                parent = cmds.listRelatives(transform, parent=True, fullPath=True)
                transform = parent[0] if parent else transform
            if transform in pool:
                continue
            if cmds.listRelatives(
                transform, shapes=True, fullPath=True, noIntermediate=True, type="mesh"
            ):
                pool.append(transform)
        return pool

    def bake(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        prefix: str = "bake_",
        suffix: str = "",
        backend: str = "auto",
        uv_set: Optional[Union[str, Dict[str, str]]] = None,
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        stem: Optional[Union[Callable[[str], str], Dict[str, str]]] = None,
        shader: Optional[str] = None,
        batch: bool = False,
    ) -> Dict[str, str]:
        """Bake lighting per object to texture files (EXR on Arnold).

        Parameters:
            objects: Mesh transforms to bake. Defaults to current selection.
                Normalized through :meth:`resolve_meshes`, so shapes and
                components resolve to their transform and non-mesh nodes
                (lights, locators, empty groups) are dropped rather than
                handed to a renderer that cannot bake them.
            output_dir: Where the baked files go. Created if missing.
                Defaults to ``<scene_dir>/baked_lighting``.
            prefix: Filename prefix wrapped around the output stem.
            suffix: Filename suffix. Final name is ``{prefix}{stem}{suffix}.{fmt}``
                (applied idempotently via ``StrUtils.apply_affix``), so callers
                can follow the ``<base>_Lightmap`` texture-set convention.
            stem: Output base name per object — the object leaf name by default.
                Pass a ``callable(long_name) -> str`` or a ``{long_name: stem}``
                dict to name the file after something else (e.g. the material's
                texture-set base, so a long node name doesn't become a long
                texture name). A falsy / missing / erroring resolution falls
                back to the leaf. Names that collide (objects sharing a material,
                or duplicate leaf names) are disambiguated with a numeric suffix
                so no bake silently overwrites another.
            backend: ``"auto"`` (default), ``"arnold"``, or ``"convertSolidTx"``.
            uv_set: Bake into this UV set (e.g. the lightmap channel). Arnold
                receives it as ``arnoldRenderToTexture``'s own ``uv_set``
                flag -- the command IGNORES the scene's current UV set
                (probe-measured), which is exactly how a bake can land on the
                texture layout while the engine samples the lightmap layout.
                ``convertSolidTx`` does sample the current set, so it is made
                current per object and restored. Pass a ``str`` to use one
                set for every object, or a ``{long_object_name: set_name}``
                dict to target a different set per object (a real scene's
                lightmap set is not named uniformly -- some reuse a
                pre-existing ``UV2`` etc.). ``None`` bakes the default
                layout. A shape lacking its set is baked on its default set
                (logged). Batching needs one agreed set (one flag per RTT
                call); a mixed dict falls back to per-object bakes.
            on_progress: Optional ``(done, total, name) -> bool`` callback
                invoked as each object's bake starts (``done`` = objects
                finished so far, 0..N-1), plus one final ``(total, total,
                last_name)`` call on completion so a determinate bar reaches
                100%. Return ``False`` to cancel the remaining bakes. Lets a UI
                drive a progress bar without this primitive knowing about Qt;
                exceptions from it never break the bake. In ``batch`` mode
                there is one opaque render call, so only the initial
                ``(0, total)`` tick (cancellable) and the final completion
                tick fire.
            shader: Optional shader node to bake with instead of each object's
                assigned material (Arnold's ``-shader`` override). MEASURED
                (mtoa 5.4.5): the override applies **per shape being baked** --
                every other object, selected or not, keeps its real material
                during that shape's render. That makes it a native white-card
                for lighting-only bakes: correct neighbor bounce/color bleed
                with no material swapping. The per-object path *guarantees*
                it lands (:meth:`_forced_shader`) -- the flag alone is
                silently lost on an instance that owns a shared mesh's
                shading assignment -- and the batch path verifies after the
                fact, re-baking only the tile that lost it
                (:meth:`_rebake_override_outliers`). Ignored (warned) by
                convertSolidTx.
            batch: Bake every object in ONE ``arnoldRenderToTexture`` call
                instead of per-object calls. The per-object loop re-translates
                the whole scene N times; batching amortizes it (measured 7.45x
                on 8 objects in a 40-object scene). Requires the Arnold
                backend and unique shape leaf names (RTT names files after the
                shape leaf, so duplicates would overwrite each other) -- when
                either fails, this falls back to the per-object loop with a
                warning. Mid-run cancellation is unavailable in batch mode.

        Returns:
            ``{long_object_name: absolute_file_path}`` for every successful bake.
            Failures are logged and excluded from the dict.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        requested = objects
        objects = self.resolve_meshes(objects)
        if not objects:
            self.logger.error(
                "Nothing to bake. Pass objects= or select a mesh."
                if not requested
                else "Nothing to bake: none of the given objects has a mesh shape."
            )
            return {}

        if output_dir is None:
            output_dir = self.default_output_dir("baked_lighting")
        os.makedirs(output_dir, exist_ok=True)

        backend = self._resolve_backend(backend)
        # arnoldRenderToTexture has no format flag and always writes EXR --
        # honor that in the output paths instead of renaming EXR bytes to a
        # mismatched extension (which the dir-diff glob would then also miss).
        fmt = self.file_format
        if backend == "arnold" and fmt.lower() != "exr":
            self.logger.warning(
                "Arnold RTT always writes EXR (requested %r); output uses .exr.",
                fmt,
            )
            fmt = "exr"
        if shader and backend != "arnold":
            # The override is semantic, not cosmetic: bake_separated's white
            # card rides it to produce LIGHTING-ONLY maps. Dropping it and
            # baking the real materials via convertSolidTx would commit
            # albedo x lighting as a "lightmap" (the engine composites albedo
            # twice) — fail loud instead of silently changing what the maps
            # mean.
            self.logger.error(
                "shader= override requires the Arnold backend (mtoa "
                "unavailable?); bake aborted rather than baking the real "
                "materials."
            )
            return {}
        if batch and backend != "arnold":
            self.logger.warning(
                "batch=True requires the Arnold backend; using per-object bakes."
            )
            batch = False
        # Arnold drops the -shader override on the instance that owns a
        # shared mesh's shading assignment (measured: that tile bakes
        # albedo x lighting -- see _forced_shader), and the owner cannot be
        # identified up front: ``instObjGroups`` connections are reported
        # relative to whatever DAG path you query through, so every instance
        # claims ownership (probed on the production room). Forcing the
        # override across the batch is no answer either -- carding every
        # target at once kills the neighbor color bleed the override exists
        # to preserve. So the batch is KEPT (the whole win is the single
        # scene translation -- measured 21.3x on 4 objects) and the tile
        # that lost the override is detected AFTER the fact and re-baked
        # per-object, where _forced_shader guarantees the card
        # (:meth:`_rebake_override_outliers` -- self-correcting, and
        # fail-safe: a false positive just re-bakes a tile correctly).
        verify_override = bool(batch and shader and self._any_instanced(objects))
        self.logger.info(
            "Baking %d object(s) -> %s (backend=%s, %dx%d)",
            len(objects), output_dir, backend, self.resolution, self.resolution,
        )

        results: Dict[str, str] = {}
        total = len(objects)
        used: set = set()
        last_leaf = ""
        cancelled = False
        guard = (
            self.arnold_translation_guard()
            if backend == "arnold" and self.translation_guard
            else contextlib.nullcontext()
        )
        with self._pinned_render_settings(backend), guard:
            if batch:
                batched = self._bake_with_arnold_batch(
                    objects, output_dir, prefix, suffix, uv_set,
                    on_progress, stem, fmt, shader,
                )
                if batched is not None:
                    if verify_override and batched:
                        self._rebake_override_outliers(
                            batched, output_dir, uv_set, shader
                        )
                    return batched
                # Unbatchable (colliding RTT filenames) -> per-object loop.
            for i, obj in enumerate(objects):
                long_name = cmds.ls(obj, long=True)
                if not long_name:
                    self.logger.warning("Skipping unknown object: %s", obj)
                    continue
                long_name = long_name[0]
                leaf = long_name.rsplit("|", 1)[-1].replace(":", "_")
                last_leaf = leaf
                if not self._tick(on_progress, i, total, leaf):
                    self.logger.info("Bake cancelled by caller at %d/%d.", i, total)
                    cancelled = True
                    break
                name = ptk.StrUtils.apply_affix(
                    self._resolve_stem(stem, long_name, leaf), prefix, suffix
                )
                out_path = self._unique_path(output_dir, name, used, fmt)
                target_set = (
                    uv_set.get(long_name) if isinstance(uv_set, dict) else uv_set
                )
                prev_uv: Dict[str, str] = {}
                try:
                    if target_set:
                        # Validation + convertSolidTx targeting. Arnold does
                        # NOT read the current set (see _rtt_kwargs) -- for it
                        # this is only the missing-set warning; the real
                        # targeting is the uv_set flag passed below.
                        prev_uv = self._set_current_uv_set(long_name, target_set)
                    if backend == "arnold":
                        # Arnold names the file after the mesh shape, so the
                        # actual written path is detected by _bake_with_arnold
                        # (dir-diff) rather than assumed; map it to our
                        # prefixed convention.
                        with self._forced_shader(long_name, shader):
                            arnold_out = self._bake_with_arnold(
                                long_name,
                                output_dir,
                                shader,
                                uv_set=self._uv_set_flag(long_name, target_set),
                            )
                        if arnold_out:
                            out_path = self._place_output(
                                arnold_out, out_path, used
                            )
                            used.add(out_path)
                    else:
                        self._bake_with_convert_solid_tx(long_name, out_path)
                except Exception as e:
                    self.logger.error("Bake failed for %s: %s", long_name, e)
                    continue
                finally:
                    self._restore_uv_sets(prev_uv)

                if os.path.exists(out_path):
                    results[long_name] = out_path
                    self.logger.info("Baked %s -> %s", leaf, out_path)
                else:
                    self.logger.warning(
                        "Bake reported success for %s but output missing: %s",
                        leaf, out_path,
                    )

        # Final completion tick so a determinate progress bar reaches 100%
        # (the per-object ticks above report the count STARTED, i.e. 0..N-1).
        if not cancelled and total:
            self._tick(on_progress, total, total, last_leaf)

        return results

    @staticmethod
    def _any_instanced(objects: List[str]) -> bool:
        """Does any of *objects* sit on a mesh shared with another transform?"""
        return any(
            NodeUtils.get_instanced_shapes(o, intermediate=False) for o in objects
        )

    @staticmethod
    def _rtt_stem(long_name: str, shape: str) -> str:
        """The filename stem ``arnoldRenderToTexture`` will write for *shape*.

        Bare shape leaf for a sole-path shape; ``<transformLeaf>_<shapeLeaf>``
        for an INSTANCED one (multiple DAG paths force qualified Arnold node
        names) -- measured on mtoa 5.5, and true even when a single instance is
        baked alone. Predicting it is what makes the batch's collision test
        exact: instances of one shape do NOT collide (their stems carry the
        transform), which is precisely the case the old leaf-only test rejected
        and the case every instanced environment is made of.
        """
        shape_leaf = shape.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        instanced = len(cmds.ls(shape, long=True, allPaths=True) or []) > 1
        if not instanced:
            return shape_leaf
        return f"{long_name.rsplit('|', 1)[-1].replace(':', '_')}_{shape_leaf}"

    #: Tolerated deviation of one instance's mean map value from its sibling
    #: group's median before the tile is treated as having lost the batch
    #: ``-shader`` override. Measured (OFFICE_ENV, mtoa 5.4.5): the owning
    #: tile bakes ~16% off its siblings while the GI noise floor between
    #: correct tiles is ~3% -- 8% sits comfortably between.
    OVERRIDE_OUTLIER_TOLERANCE = 0.08

    @staticmethod
    def _map_mean(path: str) -> Optional[float]:
        """Mean RGB value of a baked map, or None when unreadable.

        Alpha is excluded: RTT writes alpha 1.0 across the WHOLE frame
        (measured), which would compress every ratio toward 1.
        """
        # cv2 ships with EXR reading DISABLED unless this is set before the
        # module loads -- same guard every EXR reader in lightmap_baker and
        # pythontk's img_utils carries.
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        try:
            import cv2
            import numpy as np
        except Exception:
            return None
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        if img is None:
            return None
        arr = np.nan_to_num(
            np.asarray(img, dtype="float64"), nan=0.0, posinf=0.0, neginf=0.0
        )
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[..., :3]
        return float(arr.mean())

    def _override_outlier_suspects(self, results: Dict[str, str]) -> List[str]:
        """Batch tiles that plausibly lost the ``-shader`` override.

        Groups the baked objects by shared mesh (an uninstanced object never
        loses the override, so it is never grouped), then flags the members
        whose map mean deviates from the group's median by more than
        :data:`OVERRIDE_OUTLIER_TOLERANCE`. Groups too small for a
        trustworthy median (fewer than three baked members) and groups whose
        maps cannot be read (cv2 unavailable, unreadable file) return ALL
        their members: the check is fail-safe by design -- a false positive
        costs one per-object bake that produces a correct tile, a false
        negative ships an albedo x lighting tile.
        """
        groups: Dict[str, List[str]] = {}
        for long_name in results:
            try:
                shapes = NodeUtils.get_instanced_shapes(
                    long_name, intermediate=False
                )
            except Exception:
                shapes = []
            if not shapes:
                continue
            key = (cmds.ls(shapes[0], uuid=True) or [shapes[0]])[0]
            groups.setdefault(key, []).append(long_name)

        suspects: List[str] = []
        for members in groups.values():
            if len(members) < 3:
                suspects.extend(members)
                continue
            means = {m: self._map_mean(results[m]) for m in members}
            if any(v is None for v in means.values()):
                suspects.extend(members)
                continue
            med = statistics.median(means.values())
            tol = self.OVERRIDE_OUTLIER_TOLERANCE * max(med, 1e-6)
            suspects.extend(m for m, v in means.items() if abs(v - med) > tol)
        return suspects

    def _rebake_override_outliers(
        self,
        results: Dict[str, str],
        output_dir: str,
        uv_set: Optional[Union[str, Dict[str, str]]],
        shader: str,
    ) -> None:
        """Re-bake, per-object, the batch tiles that lost the override.

        The batch keeps its single-scene-translation win (measured 21.3x on
        4 objects); this pass buys back the batch's one correctness hole.
        Arnold silently drops ``-shader`` on the instance that owns a shared
        mesh's shading assignment, the owner cannot be identified up front
        (see the gate comment in :meth:`bake`), but the deviant tile IS
        identifiable after the fact: it baked its assigned material instead
        of the card, ~16% off its siblings against a ~3% noise floor. Each
        suspect re-bakes through the per-object path, where
        :meth:`_forced_shader` guarantees the card lands, and the new map
        replaces the batch's file in place -- *results* keeps its paths
        unless a file lock forces an adjacent name.
        """
        suspects = self._override_outlier_suspects(results)
        if not suspects:
            self.logger.info(
                "Batch override verify: every instance group is consistent."
            )
            return
        self.logger.info(
            "Batch override verify: re-baking %d tile(s) whose map deviates "
            "from its instance group (the -shader override does not survive "
            "the batch on a shared mesh's assignment owner).",
            len(suspects),
        )
        for long_name in suspects:
            target = results[long_name]
            flag = self._uv_set_flag(
                long_name,
                uv_set.get(long_name) if isinstance(uv_set, dict) else uv_set,
            )
            try:
                with self._forced_shader(long_name, shader):
                    arnold_out = self._bake_with_arnold(
                        long_name, output_dir, shader, uv_set=flag
                    )
            except Exception as e:
                self.logger.error(
                    "Override re-bake failed for %s (keeping the batch "
                    "tile): %s", long_name, e,
                )
                continue
            if arnold_out:
                placed = self._place_output(arnold_out, target, set())
                if placed != target:
                    results[long_name] = placed
            else:
                # The suspect tile SHIPS as the batch baked it -- say so
                # rather than letting a possibly albedo x lighting tile pass
                # silently.
                self.logger.warning(
                    "Override re-bake produced no output for %s; keeping the "
                    "batch tile.", long_name,
                )

    #: Surface-shader node types MtoA cannot translate: hardware/ShaderFX
    #: graphs render ERROR MAGENTA in Arnold. Their VIEWPORT look is fine,
    #: which is exactly why the pollution ships -- nothing looks wrong in Maya.
    _UNTRANSLATABLE_SHADER_TYPES = frozenset(
        {"StingrayPBS", "ShaderfxShader", "ShaderfxGameHair"}
    )

    @contextlib.contextmanager
    def arnold_translation_guard(self):
        """Bridge untranslatable (game/ShaderFX) materials for the bake.

        MtoA renders a surface whose shader it cannot translate as ERROR
        MAGENTA, and with GI enabled that magenta is not cosmetic: every
        nearby surface receives (1, 0, 1)-tinted bounce. Measured on a
        production room whose racks were StingrayPBS head to toe, the floor
        around them baked magenta shadows (dark-texel chroma 3.00/0.21/2.89,
        ~85% pure magenta) and the racks' own maps were worse -- while a
        neutral prop across the room stayed clean.

        The stand-in IS :class:`mayatk.ArnoldBridge` -- the existing
        ``aiSurfaceShader`` bridge tool, applied temporarily: every material
        of an :attr:`_UNTRANSLATABLE_SHADER_TYPES` type on an assigned
        shading group gets a bridge for the duration of the bake and has it
        removed after. That reuses the one implementation of Stingray->Arnold
        parity (map-type resolution from the file names, packed-mask
        layouts, DEDICATED file nodes with correct per-map colorSpace --
        sharing the game material's file nodes cannot satisfy both
        renderers), and it makes guarded materials bounce identically to
        hand-bridged ones: the production room's walls carried exactly such
        an authored bridge (``MAT_OFFICE_ENV_ai`` -- this tool's own naming),
        which is why THEY never showed the magenta. ``surfaceShader`` (the
        viewport look / FBX export) is never touched; a material that
        already has ANY ``aiSurfaceShader`` override is respected; teardown
        removes only the bridges added here. An untextured game material
        bridges to the ``aiStandardSurface`` defaults -- neutral grey bounce,
        which is the point (not-magenta), not albedo fidelity.
        """
        bridged: List[str] = []
        bridge = None
        try:
            if cmds is not None:
                from mayatk.mat_utils.arnold_bridge import ArnoldBridge

                bridge = ArnoldBridge()
                candidates: List[str] = []
                for sg in cmds.ls(type="shadingEngine") or []:
                    if sg in ("initialShadingGroup", "initialParticleSE"):
                        continue
                    surf = (
                        cmds.listConnections(f"{sg}.surfaceShader") or [None]
                    )[0]
                    if (
                        not surf
                        or cmds.nodeType(surf)
                        not in self._UNTRANSLATABLE_SHADER_TYPES
                    ):
                        continue
                    if not cmds.sets(sg, query=True):
                        continue  # no members -> contributes no bounce
                    candidates.append(str(surf))
                to_bridge = [
                    m
                    for m in dict.fromkeys(candidates)  # dedupe, keep order
                    if not bridge.has_bridge(m)  # authored override -- respect
                ]
                if to_bridge:
                    try:
                        bridge.add(materials=to_bridge)
                    except Exception as e:
                        self.logger.warning(
                            "Translation guard: bridging failed (%s); "
                            "unbridged game shaders will bake error-magenta.",
                            e,
                        )
                    # Track what actually got a bridge -- that (and only
                    # that) is what teardown removes; a material add()
                    # skipped keeps whatever it has.
                    bridged = [m for m in to_bridge if bridge.has_bridge(m)]
                if bridged:
                    self.logger.info(
                        "Arnold translation guard: %d game-shader material(s) "
                        "bridged for the bake.",
                        len(bridged),
                    )
            yield
        finally:
            if bridge is not None and bridged:
                # Logged, never raised: teardown must not mask the bake
                # result, but a bridge left behind must not go unnoticed.
                with ptk.CoreUtils.teardown_guard(
                    self.logger, "Arnold translation guard (bridges)"
                ):
                    bridge.remove(
                        materials=[m for m in bridged if cmds.objExists(m)]
                    )

    @contextlib.contextmanager
    def _forced_shader(self, obj: str, shader: Optional[str]):
        """Make *obj* actually render with *shader* for the duration of its bake.

        Arnold's ``-shader`` flag is a per-bake override and holds for ordinary
        objects, but it is silently LOST on the one instance that owns a shared
        mesh's shading-group membership: that instance renders its assigned
        material, so a lighting-only bake comes back as albedo x lighting while
        every sibling comes back correct. MEASURED on a 24-instance wall (mtoa
        5.4.5, OFFICE_ENV): the owning tile baked 16% hot with a 10-17% step at
        each of its three shared edges, where the other 25 boundaries were
        continuous to 3% -- one bright rectangle with hard edges, faithfully
        carried through the atlas to the viewer.

        Assigning the shader is unconditional and *per-instance*, so *obj* bakes
        with it while every other object -- an unselected sibling of the very
        same mesh included -- keeps its real material and the indirect light
        stays true.

        ONE object, deliberately: in batch mode Arnold applies the flag per
        shape *as it renders each one*, which is what preserves the neighbor
        bleed between co-selected objects (pinned by the lightmap suite's GI
        colour-bleed test). Carding a whole batch up front would destroy
        exactly that, so :meth:`bake` keeps the batch un-carded and instead
        verifies afterwards, re-baking only the tile that lost the flag
        through this guarantee (:meth:`_rebake_override_outliers`).

        The assignment is restored on the way out, including "had none" (the
        object is dropped from the bake shader's group rather than parked on
        ``initialShadingGroup``, which would invent an assignment it never had).
        A shader that can't be assigned degrades to the flag alone rather than
        risking the scene.
        """
        snapshot: Optional[Dict[str, Any]] = None
        if shader and cmds is not None:
            # Snapshot BEFORE any mutation, and keep it even when empty -- an
            # object with no material of its own still has to be put back.
            snapshot = self._shading_snapshot(obj)
            try:
                MatUtils.assign_mat(obj, shader)
            except Exception:
                # Keep the snapshot: the assign mutates last, so a failure can
                # still have landed, and restoring an untouched object is a
                # no-op. Losing the bake shader here costs quality, not the
                # scene -- the -shader flag still covers the common case.
                self.logger.debug(
                    "Could not assign %s to %s; falling back to the -shader "
                    "flag alone.", shader, obj, exc_info=True,
                )
        try:
            yield
        finally:
            if snapshot is not None:
                with ptk.CoreUtils.teardown_guard(
                    self.logger,
                    f"shading assignment of {obj} (it may still carry {shader})",
                ):
                    if snapshot:
                        MatUtils.apply_shading_assignments(obj, snapshot)
                    else:
                        for sg in cmds.listConnections(
                            shader, type="shadingEngine"
                        ) or []:
                            cmds.sets(obj, edit=True, remove=sg)

    @staticmethod
    def _shading_snapshot(obj: str) -> Dict[str, Any]:
        """``{shading_group: faces}`` for *obj*, or ``{}`` if it has none.

        :meth:`MatUtils.get_shading_assignments` is the source of truth (it
        alone carries per-face assignments), but it matches set members against
        the object's own paths -- so a shape whose membership is expressed
        under a *sibling instance's* path can come back empty even though the
        object plainly renders a material. Restoring from an empty snapshot
        would then strip that material, so fall back to the object's shading
        engines, which are instance-independent.
        """
        assignments = MatUtils.get_shading_assignments(obj)
        if assignments:
            return assignments
        shapes = (
            cmds.listRelatives(obj, shapes=True, noIntermediate=True, fullPath=True)
            or []
        )
        groups = cmds.listSets(object=shapes[0], type=1) if shapes else None
        return {sg: None for sg in (groups or [])}

    def _tick(
        self,
        on_progress: Optional[Callable[[int, int, str], bool]],
        done: int,
        total: int,
        name: str,
    ) -> bool:
        """Invoke the progress callback (if any); never let it break the bake.

        Returns ``True`` to continue, ``False`` only when the callback explicitly
        returns ``False`` (cancel). A missing callback or one that raises is
        treated as "continue" -- the bake is never blocked by progress reporting.
        """
        if on_progress is None:
            return True
        try:
            return on_progress(done, total, name) is not False
        except Exception:
            self.logger.debug("on_progress raised; ignoring.", exc_info=True)
            return True

    def _resolve_stem(
        self,
        stem: Optional[Union[Callable[[str], str], Dict[str, str]]],
        long_name: str,
        leaf: str,
    ) -> str:
        """Output base name for *long_name* — *leaf* unless *stem* resolves one."""
        if stem is None:
            return leaf
        try:
            resolved = stem.get(long_name) if isinstance(stem, dict) else stem(long_name)
        except Exception:
            self.logger.debug(
                "stem resolver raised for %s; using leaf.", long_name, exc_info=True
            )
            return leaf
        return resolved or leaf

    def _unique_path(
        self, output_dir: str, name: str, used: set, fmt: Optional[str] = None
    ) -> str:
        """Collision-free output path for *name*, tracking *used* across the bake.

        Objects that share a material (texture-set stem) or have duplicate leaf
        names would otherwise resolve to the same file and overwrite each other;
        the second gets ``{name}_1``, the third ``{name}_2``, and so on. *fmt*
        is the backend's effective format (Arnold is always EXR); default
        ``file_format``.
        """
        fmt = fmt or self.file_format
        candidate = os.path.join(output_dir, f"{name}.{fmt}")
        k = 1
        while candidate in used:
            candidate = os.path.join(output_dir, f"{name}_{k}.{fmt}")
            k += 1
        used.add(candidate)
        return candidate

    def _resolve_backend(self, requested: str) -> str:
        if requested == "auto":
            return "arnold" if self.arnold_available() else "convertSolidTx"
        if requested == "arnold":
            if not self.arnold_available():
                self.logger.warning(
                    "Arnold backend requested but mtoa not loaded; "
                    "falling back to convertSolidTx."
                )
                return "convertSolidTx"
            return "arnold"
        if requested == "convertSolidTx":
            return "convertSolidTx"
        raise ValueError(
            f"Unknown backend: {requested!r}. "
            "Expected 'auto', 'arnold', or 'convertSolidTx'."
        )

    @contextlib.contextmanager
    def _pinned_render_settings(self, backend: str):
        """Pin :attr:`render_settings` on ``defaultArnoldRenderOptions`` for the bake.

        RTT's only quality flag is ``aa_samples``; GI bounce depth / diffuse
        samples are read from the scene's render options at translate time.
        Snapshotting and restoring exactly the attrs we set
        (:meth:`Attributes.pinned`) keeps the bake deterministic without
        permanently touching the user's render setup. No-op for non-Arnold
        backends or an empty dict.
        """
        if backend != "arnold" or not self.render_settings:
            yield
            return
        try:  # the options node only exists after mtoa initializes it
            from mtoa.core import createOptions

            createOptions()
        except Exception as e:
            self.logger.warning("Could not ensure Arnold options node: %s", e)
            yield
            return

        # Pass the baker's logger so a declined or failed render-setting pin
        # lands in the bake panel's log box, where the user is looking, rather
        # than only on the attributes module logger.
        with Attributes.pinned(
            "defaultArnoldRenderOptions", _logger=self.logger, **self.render_settings
        ):
            yield

    # ------------------------------------------------------------------
    # UV-set targeting (convertSolidTx samples the current set; Arnold gets
    # the set as RTT's own uv_set flag -- it ignores the current set)
    # ------------------------------------------------------------------

    def _set_current_uv_set(self, obj: str, uv_set: str) -> Dict[str, str]:
        """Make *uv_set* current on every shape of *obj* that has it.

        ``convertSolidTx``'s targeting, and the missing-set warning for both
        backends. NOT Arnold's targeting: RTT ignores the current set
        (probe-measured), so the Arnold paths pass the set as the command's
        own ``uv_set`` flag and this switch is validation only there.

        Returns ``{shape: previous_current_set}`` for restore. Warns (and
        returns ``{}``) when no shape carries *uv_set* -- the bake then falls
        back to the shape's default layout.
        """
        shapes = cmds.listRelatives(
            obj, shapes=True, noIntermediate=True, fullPath=True
        ) or []
        prev: Dict[str, str] = {}
        for shape in shapes:
            all_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            if uv_set not in all_sets:
                continue
            cur = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
            if cur:
                prev[shape] = cur
            if cur != uv_set:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
        if not prev:
            self.logger.warning(
                "UV set %r not found on %s; baking the current set instead.",
                uv_set, obj,
            )
        return prev

    @staticmethod
    def _restore_uv_sets(prev: Dict[str, str]) -> None:
        """Restore current UV sets captured by :meth:`_set_current_uv_set`."""
        for shape, cur in prev.items():
            try:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=cur)
            except RuntimeError:
                pass

    @staticmethod
    def _uv_set_flag(obj: str, target: Optional[str]) -> Optional[str]:
        """The RTT ``uv_set`` flag value for baking *obj* into *target*.

        ``None`` means omit the flag. RTT renders the mesh's index-0 set by
        default, and naming that set explicitly CORRUPTS the output on mtoa
        5.5 (measured: a 2.5KB unreadable EXR where the flagless render of
        the same layout is healthy) -- so the flag is passed only for a real
        secondary set.
        """
        if not target:
            return None
        # *obj* may itself be the mesh shape (bake() accepts either spelling).
        shapes = cmds.listRelatives(
            obj, shapes=True, noIntermediate=True, fullPath=True
        ) or (cmds.ls(obj, type="mesh", long=True) or [None])
        shape = shapes[0]
        if not shape:
            return target
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        return None if (sets and sets[0] == target) else target

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------

    def _bake_with_convert_solid_tx(self, obj: str, out_path: str) -> None:
        """Bake one mesh via ``convertSolidTx``.

        ``convertSolidTx`` requires a *shading group* (or material) for its
        first arg. We pick the first SG assigned to *obj*.
        """
        sg = self._first_shading_group(obj)
        if sg is None:
            raise RuntimeError(f"No shading group assigned to {obj!r}.")

        kwargs = dict(_CONVERT_SOLID_TX_DEFAULTS)
        kwargs.update({
            "resolutionX": self.resolution,
            "resolutionY": self.resolution,
            "fileImageName": out_path,
            "fileFormat": self.file_format,
        })
        # The cmd signature is convertSolidTx(material, geom, ...).
        cmds.convertSolidTx(sg, obj, **kwargs)

    @staticmethod
    def _output_snapshot(pattern: str) -> Dict[str, float]:
        """``{path: mtime}`` under *pattern* -- the overwrite-aware baseline.

        A stray raw-named file from a previously FAILED placement gets
        overwritten in place by the next render of the same object, so a
        name-set diff sees no new file and silently drops the object from the
        bake again -- self-perpetuating (measured: the same meshes went black
        in consecutive production pushes until the strays were removed). An
        mtime change is a new output.
        """
        snap: Dict[str, float] = {}
        for p in glob.glob(pattern):
            try:
                snap[p] = os.path.getmtime(p)
            except OSError:
                snap[p] = -1.0
        return snap

    @staticmethod
    def _new_outputs(pattern: str, before: Dict[str, float]) -> List[str]:
        """Paths under *pattern* that are new or REWRITTEN since *before*."""
        new: List[str] = []
        for p in glob.glob(pattern):
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if p not in before or m != before[p]:
                new.append(p)
        return new

    def _rtt_kwargs(
        self,
        output_dir: str,
        shader: Optional[str],
        uv_set: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The ``arnoldRenderToTexture`` call args (single source for both paths)."""
        kwargs: Dict[str, Any] = dict(
            folder=output_dir,
            resolution=self.resolution,
            aa_samples=self.samples,
            # Bake PAST the UV island border. Without it Arnold writes
            # partial-coverage edge texels whose RGB is premultiplied by that
            # coverage, i.e. a dark ring around every island: measured on a lit
            # cube at 128px, island-edge texels came back 83.7% darker than the
            # interior with 7.40% of the map partially covered, and with the flag
            # the partial texels drop to 0.00% while the interior is unchanged
            # (1.109 vs 1.129, inside GI noise). The dark ring is what reads as
            # a hard outline on every object and, on tiled/instanced geometry,
            # as a seam at each shared edge -- both tiles put their dark border
            # on the same line. Dilation's alpha division only ever recovered
            # part of it (45.3% -> 17.1% on the same fixture); this removes the
            # artifact at the source instead of undoing it afterwards.
            extend_edges=self.extend_edges,
            # Pin the pixel filter (see __init__ for why the default is box
            # 1.0, not Arnold's gaussian 2.0). GI depth/samples are already
            # pinned via render_settings; unpinned, the filter rode the
            # SCENE's render setting -- silently varying island-edge quality
            # between users and sessions.
            filter=self.pixel_filter,
            filter_width=self.filter_width,
        )
        if shader:
            # Per-shape override (measured): only the shape being baked wears
            # it; every other object keeps its real material for that render.
            kwargs["shader"] = str(shader)
        if uv_set:
            # The target set MUST ride the command's own flag: RTT ignores the
            # scene's current UV set entirely (probe-measured -- with the
            # target set current and no flag, content still rendered over the
            # default set's layout, which shipped a production room whose
            # every wall sampled empty atlas texels).
            kwargs["uv_set"] = str(uv_set)
        return kwargs

    def _bake_with_arnold(
        self,
        obj: str,
        output_dir: str,
        shader: Optional[str] = None,
        uv_set: Optional[str] = None,
    ) -> Optional[str]:
        """Bake one mesh via Arnold's ``arnoldRenderToTexture``.

        Arnold names the output after the mesh *shape* (e.g. ``pCubeShape``),
        not the transform, so the written file is found by diffing the output
        directory rather than assuming a name (output is always ``.exr`` --
        the command has no format flag). The diff is mtime-aware: a stray
        from a failed placement sits under the exact name RTT writes again,
        and a name-set diff would miss the overwrite (see
        :meth:`_output_snapshot`). A multi-shape transform writes one
        file per shape; the one matching a shape leaf name is preferred and
        the extras are logged. Returns the written path (the caller maps it
        to the prefixed convention), or None if none appeared.
        """
        pattern = os.path.join(output_dir, "*.exr")
        before = self._output_snapshot(pattern)
        prev = cmds.ls(selection=True, long=True) or []
        cmds.select(obj, replace=True)
        try:
            cmds.arnoldRenderToTexture(
                **self._rtt_kwargs(output_dir, shader, uv_set)
            )
        finally:
            if prev:
                cmds.select(prev, replace=True)
            else:
                cmds.select(clear=True)
        new = sorted(self._new_outputs(pattern, before))
        if len(new) <= 1:
            return new[-1] if new else None
        # Multiple shapes wrote multiple files; keep the one named after one
        # of this transform's shape leaves (deterministic), not sorted()[-1].
        shapes = cmds.listRelatives(
            obj, shapes=True, noIntermediate=True, fullPath=True
        ) or []
        leaves = {s.rsplit("|", 1)[-1].rsplit(":", 1)[-1] for s in shapes}
        matches = [
            p for p in new
            if os.path.splitext(os.path.basename(p))[0] in leaves
        ]
        self.logger.warning(
            "%s wrote %d maps (multi-shape transform); keeping %s.",
            obj, len(new), os.path.basename((matches or new)[-1]),
        )
        return (matches or new)[-1]

    def _bake_with_arnold_batch(
        self,
        objects: List[str],
        output_dir: str,
        prefix: str,
        suffix: str,
        uv_set: Optional[Union[str, Dict[str, str]]],
        on_progress: Optional[Callable[[int, int, str], bool]],
        stem: Optional[Union[Callable[[str], str], Dict[str, str]]],
        fmt: str,
        shader: Optional[str],
    ) -> Optional[Dict[str, str]]:
        """Bake every object in ONE RTT call; map per-shape files to objects.

        Returns the results dict, or ``None`` when the selection can't be
        batched (duplicate shape leaf names -- RTT names files by shape leaf,
        so duplicates would silently overwrite each other); the caller then
        falls back to the per-object loop.
        """
        longs: List[str] = []
        leaves: Dict[str, List[str]] = {}
        shape_paths: Dict[str, List[str]] = {}
        for obj in objects:
            long_name = cmds.ls(obj, long=True)
            if not long_name:
                self.logger.warning("Skipping unknown object: %s", obj)
                continue
            long_name = long_name[0]
            shapes = cmds.listRelatives(
                long_name, shapes=True, noIntermediate=True, fullPath=True
            ) or []
            shape_paths[long_name] = shapes
            raw_leaves = [s.rsplit("|", 1)[-1] for s in shapes]
            leaves[long_name] = [l.rsplit(":", 1)[-1] for l in raw_leaves]
            if any(":" in l for l in raw_leaves):
                # A namespaced shape's RTT filename is NOT its raw leaf (":"
                # is illegal in Windows filenames), so the stem match below
                # would miss every referenced asset. The per-object path
                # detects its file by dir-diff and is immune.
                self.logger.warning(
                    "Namespaced shape names in the batch (RTT filename "
                    "mapping is ambiguous); falling back to per-object bakes."
                )
                return None
            longs.append(long_name)
        if not longs:
            return {}
        # Collision test on the stems RTT will ACTUALLY write, not on shape
        # leaves: instances of one shape share a leaf but get transform-
        # qualified filenames, so they do not collide -- and an instanced
        # environment (24 wall tiles on one mesh) is exactly the case the
        # leaf-only test rejected, forcing 46 scene translations where one
        # would do. A real collision is two DIFFERENT shapes whose predicted
        # stems match.
        stems = [
            self._rtt_stem(long_name, shape)
            for long_name in longs
            for shape in shape_paths[long_name]
        ]
        if len(set(stems)) != len(stems):
            self.logger.warning(
                "Two targets would write the same RTT filename; falling back "
                "to per-object bakes."
            )
            return None

        total = len(longs)
        first_leaf = longs[0].rsplit("|", 1)[-1].replace(":", "_")
        last_leaf = longs[-1].rsplit("|", 1)[-1].replace(":", "_")
        if not self._tick(on_progress, 0, total, first_leaf):
            self.logger.info("Bake cancelled by caller before batch start.")
            return {}

        # ONE uv_set flag serves the whole RTT call (and the command ignores
        # the scene's current set -- see _rtt_kwargs), so the batch requires
        # the objects to agree on the EFFECTIVE flag (per _uv_set_flag: a
        # target that is an object's index-0 set means "omit the flag").
        # Mixed flags fall back to the per-object loop, which passes each
        # object its own.
        flags = {
            l: self._uv_set_flag(
                l, uv_set.get(l) if isinstance(uv_set, dict) else uv_set
            )
            for l in longs
        }
        distinct = set(flags.values())
        if len(distinct) > 1:
            self.logger.warning(
                "Mixed target UV sets in the batch (RTT takes one uv_set for "
                "the whole call); falling back to per-object bakes."
            )
            return None
        batch_uv_set = next(iter(distinct)) if distinct else None

        pattern = os.path.join(output_dir, "*.exr")
        before = self._output_snapshot(pattern)
        prev_sel = cmds.ls(selection=True, long=True) or []
        cmds.select(longs, replace=True)
        try:
            cmds.arnoldRenderToTexture(
                **self._rtt_kwargs(output_dir, shader, batch_uv_set)
            )
        except Exception as e:
            self.logger.error("Batch bake failed: %s", e)
            # Mirror the per-object path's guarantee: a determinate progress
            # bar still reaches 100% on failure (empty results tell the tale).
            self._tick(on_progress, total, total, last_leaf)
            return {}
        finally:
            if prev_sel:
                cmds.select(prev_sel, replace=True)
            else:
                cmds.select(clear=True)

        by_stem = {
            os.path.splitext(os.path.basename(p))[0]: p
            for p in self._new_outputs(pattern, before)
        }
        results: Dict[str, str] = {}
        used: set = set()
        for long_name in longs:
            leaf = long_name.rsplit("|", 1)[-1].replace(":", "_")
            # RTT names a file after the Arnold node: the bare shape leaf for a
            # sole-path shape, but "<transform>_<shapeLeaf>" for an INSTANCED
            # shape (multiple DAG paths force qualified node names) -- measured
            # on mtoa 5.5, and true even when only one instance is in this
            # batch (siblings elsewhere in the scene are enough). Match either
            # spelling, bare leaf first.
            matches = [
                s
                for l in leaves[long_name]
                for s in (l, f"{leaf}_{l}")
                if s in by_stem
            ]
            if not matches:
                self.logger.warning(
                    "Batch bake produced no output for %s.", long_name
                )
                continue
            if len(matches) > 1:
                # Match the per-object path's multi-shape transparency: only
                # the first shape's map is claimed under the object's name.
                self.logger.warning(
                    "%s wrote %d maps (multi-shape transform); claiming %s, "
                    "leaving %s in %s.",
                    long_name, len(matches), matches[0],
                    ", ".join(matches[1:]), output_dir,
                )
            raw = by_stem[matches[0]]
            name = ptk.StrUtils.apply_affix(
                self._resolve_stem(stem, long_name, leaf), prefix, suffix
            )
            out_path = self._unique_path(output_dir, name, used, fmt)
            out_path = self._place_output(raw, out_path, used)
            used.add(out_path)
            results[long_name] = out_path
            self.logger.info("Baked %s -> %s", leaf, out_path)

        self._tick(on_progress, total, total, last_leaf)
        return results

    @staticmethod
    def _first_shading_group(obj: str) -> Optional[str]:
        """Return the first non-default SG connected to any of *obj*'s shapes.

        Falls back to ``initialShadingGroup`` only if no shape on the
        transform has anything else attached -- prevents an early-return
        on a shape that happens to only carry the default SG when a later
        shape has a real one.
        """
        shapes = cmds.listRelatives(
            obj, shapes=True, noIntermediate=True, fullPath=True
        ) or []
        all_sgs: List[str] = []
        for shape in shapes:
            all_sgs.extend(cmds.listConnections(shape, type="shadingEngine") or [])
        for sg in all_sgs:
            if sg != "initialShadingGroup":
                return sg
        return all_sgs[0] if all_sgs else None

    # ------------------------------------------------------------------
    # Optional: hook baked textures into the material for viewport preview
    # ------------------------------------------------------------------

    def assign_to_diffuse(self, mapping: Dict[str, str]) -> None:
        """Wire each baked PNG into the object's material color slot.

        Mutates the scene -- :meth:`restore_diffuse_connections` undoes it.

            paths = baker.bake(selection)
            baker.assign_to_diffuse(paths)
            # ... preview / export / etc ...
            baker.restore_diffuse_connections()    # leave the scene as found

        Parameters:
            mapping: ``{object_long_name: baked_png_path}`` from :meth:`bake`.
        """
        for obj, path in mapping.items():
            sg = self._first_shading_group(obj)
            if not sg:
                self.logger.warning("No SG for %s; skipping assign.", obj)
                continue
            mat = self._material_from_sg(sg)
            if not mat:
                self.logger.warning("No material on %s; skipping.", sg)
                continue
            color_attr = self._color_attr_for_material(mat)
            if not color_attr:
                self.logger.warning(
                    "Don't know how to set diffuse on %s (type=%s); skipping.",
                    mat, cmds.nodeType(mat),
                )
                continue

            # Remember whatever's currently driving the color so we can
            # restore it later. Two shapes:
            #  - incoming connection -> capture the source plug
            #  - static value        -> capture the tuple of raw floats
            incoming = cmds.listConnections(
                color_attr, plugs=True, source=True, destination=False
            ) or []
            static_value: Optional[tuple] = None
            if not incoming:
                raw = cmds.getAttr(color_attr)
                # Color attrs come back as [(r, g, b)] from cmds.
                static_value = raw[0] if isinstance(raw, list) else raw
            self._restore_state.append((
                color_attr,
                incoming[0] if incoming else "",
                static_value,
                path,
            ))
            if incoming:
                cmds.disconnectAttr(incoming[0], color_attr)

            file_node, _placement = MatUtils.create_file_node(
                path, name=f"baked_{cmds.nodeType(mat)}_{time.time_ns()}"
            )
            cmds.connectAttr(f"{file_node}.outColor", color_attr, force=True)

    def restore_diffuse_connections(self) -> None:
        """Undo :meth:`assign_to_diffuse` -- reconnects previous drivers."""
        while self._restore_state:
            color_attr, prev_source, prev_static, baked_path = self._restore_state.pop()
            try:
                current = cmds.listConnections(
                    color_attr, plugs=True, source=True, destination=False
                ) or []
                # Disconnect whatever assign_to_diffuse hooked up.
                for src in current:
                    cmds.disconnectAttr(src, color_attr)
                # Reconnect the original driver, or restore the static value.
                if prev_source and cmds.objExists(prev_source.split(".")[0]):
                    cmds.connectAttr(prev_source, color_attr, force=True)
                elif prev_static is not None:
                    cmds.setAttr(color_attr, *prev_static, type="double3")
            except RuntimeError as e:
                self.logger.warning(
                    "Could not restore %s: %s", color_attr, e
                )

    @staticmethod
    def _material_from_sg(sg: str) -> Optional[str]:
        mats = cmds.listConnections(f"{sg}.surfaceShader") or []
        return mats[0] if mats else None

    @staticmethod
    def _color_attr_for_material(material: str) -> Optional[str]:
        """Return the plug to wire color into for known material types."""
        node_type = cmds.nodeType(material)
        # Common Maya/Arnold/Stingray base-color slots.
        candidates_by_type = {
            "lambert": "color",
            "blinn": "color",
            "phong": "color",
            "phongE": "color",
            "anisotropic": "color",
            "aiStandardSurface": "baseColor",
            "standardSurface": "baseColor",
            "StingrayPBS": "TEX_color_map",
            "openPBRSurface": "baseColor",
        }
        attr = candidates_by_type.get(node_type)
        if attr and cmds.attributeQuery(attr, node=material, exists=True):
            return f"{material}.{attr}"
        return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick manual smoke test: bake selection into the current workspace.
    paths = TextureBaker().bake()
    for obj, p in paths.items():
        print(f"  {obj} -> {p}")
