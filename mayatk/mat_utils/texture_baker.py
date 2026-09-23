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
    "samplePlane": 0,  # sample on the surface
    "shadows": True,
    "alpha": False,  # keep RGB; alpha handled separately if needed
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
        device: Optional[str] = None,
        adaptive: bool = True,
    ):
        super().__init__()
        # Per-instance knobs -- overriding ``TextureBaker.resolution`` at the
        # class scope would mutate global state, so they live on the instance.
        self.resolution = resolution
        self.samples = samples
        self.file_format = file_format
        # Which device Arnold renders on: "GPU", "CPU", "AUTO", or None to
        # leave the scene's own setting alone (the historical behaviour, and
        # still the default -- a bake must not silently change renderer).
        # See :meth:`_device_settings` for what each means and what AUTO
        # measured.
        self.device = device
        # How a GPU bake spends its sample budget: adaptively (the default) or
        # the whole budget on every texel. See :meth:`_sampling_settings`.
        self.adaptive = bool(adaptive)
        #: Whether the last batch call was stopped before it wrote a map
        #: (:meth:`_bake_with_arnold_batch`); ``bake`` reads it to tell a
        #: cancelled render from a selection that cannot be batched.
        self._batch_cancelled = False
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

    @classmethod
    def ensure_arnold(cls) -> bool:
        """Load mtoa if it isn't loaded, then answer :meth:`arnold_available`.

        What a caller about to bake WITH Arnold asks. mtoa ships with Maya but
        is often not auto-loaded, and mayatk loads it on demand wherever a tool
        needs it (``EnvUtils.load_plugin("mtoa")``) rather than sending the
        artist to the Plug-in Manager. Loading boots the renderer, which takes
        seconds, so :meth:`arnold_available` stays the side-effect-free probe
        for anything merely reporting state. ``False`` only when mtoa cannot
        be loaded at all.
        """
        if cls.arnold_available():
            return True
        if cmds is None:
            return False
        from mayatk.env_utils._env_utils import EnvUtils

        try:
            EnvUtils.load_plugin("mtoa")
        except ValueError:
            return False
        return cls.arnold_available()

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
        # Membership in a set: a Scene-scope resolve walks every mesh, several
        # times a bake and once per Undo through the Exclude label, and a list
        # scan per node made that quadratic.
        seen: set = set()
        for node in cmds.ls(ptk.make_iterable(objects), long=True) or []:
            # A component ("pCube1.f[0]") or a shape both resolve through their
            # transform, so a face selection bakes the object it belongs to.
            transform = node.split(".")[0]
            if cmds.objectType(transform, isAType="shape"):
                parent = cmds.listRelatives(transform, parent=True, fullPath=True)
                transform = parent[0] if parent else transform
            if transform in seen:
                continue
            seen.add(transform)
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
        size: Optional[Any] = None,
        shader: Optional[str] = None,
        batch: bool = False,
        claims: Optional[Any] = None,
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
            size: Per-object bake size resolver -- ``{long_name: px}`` dict,
                ``callable(long_name) -> px``, or ``None`` (the square
                :attr:`resolution` for every object). RTT renders one SQUARE
                per call, so a ``(w, h)`` pair resolves to the square that
                holds it. Bake cost is linear in texels, so a caller that
                already knows an object will occupy only part of an atlas
                bakes it at that footprint instead of paying for a full map it
                is about to downscale away (see
                :meth:`LightmapBaker.bake_atlas`). Anything unresolved falls
                back to :attr:`resolution`, so a partial map is safe.
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
                shading assignment -- so with a shader, instanced targets
                always bake per-object and only uninstanced ones batch.
                Ignored (warned) by convertSolidTx.
            batch: Bake in as FEW ``arnoldRenderToTexture`` calls as the
                objects allow, instead of one per object. The per-object loop
                re-translates the whole scene N times; batching amortizes it
                (measured 7.45x on 8 objects in a 40-object scene -- one
                translation measured 19s in a production room). A single RTT
                call carries ONE ``uv_set`` flag and ONE ``resolution``, so the
                objects are PARTITIONED on exactly those two and each part gets
                a call: a room whose meshes reuse differently named lightmap
                sets (which used to abandon batching altogether), or an atlas
                bake sizing each object to its footprint, still pays one
                translation per part rather than per object. Requires the
                Arnold backend and distinct RTT output names
                (:meth:`_rtt_stem`: two different shapes must not write the
                same file) -- when either fails, this falls back to the
                per-object loop with a warning. With a *shader*, instanced
                targets bake per-object regardless (see *shader*).
                Cancellation lands between parts rather than between objects.
            claims: File names (``"crate_lightmap.exr"``, compared without
                case) mapped to the objects that read each -- what
                ``LightmapRecords.claims`` returns. A name only the object
                being baked reads stays its own (a re-bake keeps its map's
                name); a name anything else reads is never written over, since
                that would hand the reader this bake's pixels, and the output
                takes the next free ``_<k>`` spelling, exactly as a collision
                within the bake does. A plain collection of names claims each
                one outright.
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
        batch_objects, per_object = self._route(objects, batch, shader)
        self.logger.info(
            "Baking %d object(s) -> %s (backend=%s, %s)",
            len(objects),
            output_dir,
            backend,
            "sized per object"
            if size is not None
            else f"{self.resolution}x{self.resolution}",
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
            if backend == "arnold" and self._renders_on_gpu():
                self._log_gpu_budget()
            offset = 0
            if batch_objects:

                def batch_tick(done, _part_total, name):
                    # The batch reports its own share; progress stays on ONE
                    # scale across both halves, and a cancel there stops the
                    # per-object half too.
                    nonlocal cancelled, last_leaf
                    last_leaf = name
                    keep = self._tick(on_progress, done, total, name)
                    cancelled = cancelled or not keep
                    return keep

                batched = self._bake_with_arnold_batch(
                    batch_objects,
                    output_dir,
                    prefix,
                    suffix,
                    uv_set,
                    batch_tick if on_progress is not None else None,
                    stem,
                    fmt,
                    shader,
                    size,
                    claims,
                )
                if batched is None and self._batch_cancelled:
                    cancelled = True  # a stopped render: nothing re-renders
                elif batched is None:
                    # Unbatchable (colliding RTT filenames) -> per-object loop.
                    per_object = list(objects)
                else:
                    results.update(batched)
                    # The per-object half names against the same folder.
                    used.update(batched.values())
                    # A LATER part stopped after an earlier one rendered: the
                    # batch hands back what it has, and the members it never
                    # reached must not go round again per object.
                    cancelled = cancelled or self._batch_cancelled
                    # Whatever the batch rendered but could not place (an RTT
                    # filename no rule predicted), or lost to a failed call,
                    # goes round again per-object: that path finds its file
                    # by dir-diff, so a naming quirk costs one scene
                    # translation, never the map.
                    missed = [
                        o
                        for o in batch_objects
                        if (cmds.ls(o, long=True) or [o])[0] not in batched
                    ]
                    if missed and not cancelled:
                        self.logger.warning(
                            "%d object(s) re-bake one per call: %s",
                            len(missed),
                            ", ".join(o.rsplit("|", 1)[-1] for o in missed),
                        )
                        per_object = missed + per_object
                    offset = len(batch_objects) - len(missed)
            for i, obj in enumerate([] if cancelled else per_object, start=offset):
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
                out_path = self._unique_path(
                    output_dir, name, used, fmt, claims, owner=long_name
                )
                target_set = (
                    uv_set.get(long_name) if isinstance(uv_set, dict) else uv_set
                )
                try:
                    written = self._bake_one(
                        long_name,
                        output_dir,
                        out_path,
                        target_set,
                        backend,
                        shader,
                        size,
                        used,
                    )
                except Exception as e:
                    self.logger.error("Bake failed for %s: %s", long_name, e)
                    continue
                if written:
                    results[long_name] = written
                    self.logger.info("Baked %s -> %s", leaf, written)
                    continue
                # The render returned without writing the map: stopped from
                # its own window (which returns normally), or failed before
                # writing. Going on would start the next object's render for
                # the user to stop again -- measured on the production room,
                # one Esc became a warning per wall -- so the bake stops here
                # and says why.
                left = len(per_object) - (i - offset) - 1
                self.logger.warning(
                    "Arnold wrote no map for %s (render cancelled, or failed "
                    "before writing); the bake stops here%s.",
                    leaf,
                    f" with {left} object(s) left" if left > 0 else "",
                )
                cancelled = True
                break

        # Final completion tick so a determinate progress bar reaches 100%
        # (the per-object ticks above report the count STARTED, i.e. 0..N-1).
        if not cancelled and total:
            self._tick(on_progress, total, total, last_leaf)

        return results

    def _route(
        self, objects: List[str], batch: bool, shader: Optional[str]
    ) -> Tuple[List[str], List[str]]:
        """``(batched, per_object)``: which objects share RTT calls, which bake alone.

        Without *batch* every object bakes in a call of its own. With it, an
        INSTANCED target still does when a *shader* override rides the bake:
        it bakes where :meth:`_forced_shader` guarantees the card. Arnold drops
        ``-shader`` on the instance(s) that own a shared mesh's shading
        assignment (that tile bakes its real material -- see
        :meth:`_forced_shader`), and the owner cannot be identified up front:
        ``instObjGroups`` connections are reported relative to whatever DAG
        path you query through, so every instance claims ownership. Carding the
        whole batch up front is no answer either -- it kills the neighbour
        colour bleed the override exists to keep. The batch used to keep
        instances and re-bake afterwards the tiles whose MEAN strayed from
        their instance group's median; measured on a production room (46
        instanced targets, quest) against an all-per-object reference, that
        test flagged 33 correct tiles -- instances stand in different light --
        and missed three hot ones (+13% / +29% / +54%: bright wall panels in
        the WebXR preview; one owner per mesh was its premise), in 428s
        against 281s per-object. Uninstanced targets never lose the flag and
        still batch.
        """
        if not batch:
            return [], list(objects)
        if not shader:
            return list(objects), []
        alone = [
            o for o in objects if NodeUtils.get_instanced_shapes(o, intermediate=False)
        ]
        if alone:
            self.logger.info(
                "%d instanced target(s) bake one per call under the shader "
                "override; %d batch.",
                len(alone),
                len(objects) - len(alone),
            )
        routed = set(alone)
        return [o for o in objects if o not in routed], alone

    def _log_gpu_budget(self) -> None:
        """Say how a GPU bake spends its samples: Arnold's GPU ignores the GI samples."""
        ceiling = self._gpu_budget()
        if self._sampling_settings().get("enable_adaptive_sampling"):
            self.logger.info(
                "GPU bake: adaptive AA %d..%d -- the preset's AA on every "
                "texel, its GI budget where the noise needs it.",
                self._camera_samples(),
                ceiling,
            )
        elif ceiling != max(1, int(self.samples)):
            self.logger.info(
                "GPU bake: Arnold's GPU ignores the GI diffuse samples, "
                "so the camera samples carry them -- AA %d (%d x %d).",
                ceiling,
                self.samples,
                ceiling // max(1, int(self.samples)),
            )

    def _bake_one(
        self,
        long_name: str,
        output_dir: str,
        out_path: str,
        target_set: Optional[str],
        backend: str,
        shader: Optional[str],
        size: Optional[Any],
        used: set,
    ) -> Optional[str]:
        """Render *long_name* on its own; the path its map landed at, or ``None``.

        ``None`` means the render wrote nothing -- stopped from Arnold's own
        window, which returns normally, or failed before writing. That is
        judged by what THIS render produced, never by whether *out_path*
        exists: a re-bake names its map after the one it replaces, so the old
        file is already there, and taking it for the new one reported a
        cancelled render as a bake -- and the loop went on to the next object.
        Raises when the render fails outright.
        """
        prev_uv: Dict[str, str] = {}
        try:
            if target_set:
                # Validation + convertSolidTx targeting. Arnold does NOT read
                # the current set (see _rtt_kwargs) -- for it this is only the
                # missing-set warning; the real targeting is the uv_set flag
                # passed below.
                prev_uv = self._set_current_uv_set(long_name, target_set)
            if backend == "arnold":
                # Arnold names the file after the mesh shape, so the written
                # path is detected by _bake_with_arnold (dir-diff) rather than
                # assumed, then placed under our prefixed name.
                with self._forced_shader(long_name, shader):
                    written = self._bake_with_arnold(
                        long_name,
                        output_dir,
                        shader,
                        uv_set=self._uv_set_flag(long_name, target_set),
                        resolution=self._resolve_size(long_name, size),
                    )
                if not written:
                    return None
                placed = self._place_output(written, out_path, used)
                used.add(placed)
                return placed
            before = self._mtime(out_path)
            self._bake_with_convert_solid_tx(long_name, out_path)
            after = self._mtime(out_path)
            return out_path if after is not None and after != before else None
        finally:
            self._restore_uv_sets(prev_uv)

    @staticmethod
    def _mtime(path: str) -> Optional[float]:
        """*path*'s modification time, or ``None`` when there is no file."""
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _resolve_size(self, long_name: str, size: Optional[Any]) -> int:
        """Square bake size (px) for *long_name* -- the ``stem`` resolver shapes.

        ``{long_name: px}`` dict, ``callable(long_name) -> px``, a bare number,
        or ``None``. A ``(w, h)`` pair collapses to the square that HOLDS it:
        RTT bakes square, and the atlas assembler resizes the tile into its
        (non-square) cell anyway -- taking the smaller axis would resample a
        map that was never rendered at the density its cell wants.

        Anything unresolved falls back to :attr:`resolution`, so a partial map
        is safe: an object the caller had no plan for still gets a full map
        rather than a 1px one.
        """
        value = size.get(long_name) if isinstance(size, dict) else size
        if callable(value):
            try:
                value = value(long_name)
            except Exception as e:  # a resolver must never break a bake
                self.logger.warning("size resolver failed for %s: %s", long_name, e)
                value = None
        if value is None:
            value = self.resolution
        if not isinstance(value, (int, float)):
            value = max(value)
        return max(1, int(value))

    @staticmethod
    def _rtt_stem(long_name: str, shape: str) -> str:
        """The filename stem ``arnoldRenderToTexture`` will write for *shape*.

        The shape's SHORTEST UNIQUE DAG path -- the name Maya's ``ls`` gives
        that path, which mtoa names the Arnold node after -- with ``|`` and
        ``:`` flattened to ``_``. So a sole-path shape with a unique leaf is its
        bare leaf; an INSTANCED one is qualified by its transform
        (``<transform>_<shapeLeaf>``: multiple DAG paths force it, even for a
        single instance baked alone); and a shape whose LEAF recurs elsewhere
        in the scene is qualified as far up as uniqueness takes -- measured on
        the production room (mtoa 5.5), whose two machine bodies wrote
        ``MACHINE_A_BODY_BODYShape.exr`` and ``MACHINE_B_BODY_BODY_BODYShape.exr``,
        where the leaf-or-transform prediction found neither and a batch of
        either one alone dropped it. Predicting it is what makes the batch's
        collision test exact (instances of one shape do NOT collide) and its
        results findable. *long_name* is the transform the shape is baked
        through; *shape* its full path under it.
        """
        unique = (cmds.ls(shape) or [shape])[0]
        return unique.lstrip("|").replace("|", "_").replace(":", "_")

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
        an authored bridge (``MAT_ROOM_ENV_ai`` -- this tool's own naming),
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
                    surf = (cmds.listConnections(f"{sg}.surfaceShader") or [None])[0]
                    if (
                        not surf
                        or cmds.nodeType(surf) not in self._UNTRANSLATABLE_SHADER_TYPES
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
                    bridge.remove(materials=[m for m in bridged if cmds.objExists(m)])

    @contextlib.contextmanager
    def _forced_shader(self, obj: str, shader: Optional[str]):
        """Make *obj* actually render with *shader* for the duration of its bake.

        Arnold's ``-shader`` flag is a per-bake override and holds for ordinary
        objects, but it is silently LOST on the one instance that owns a shared
        mesh's shading-group membership: that instance renders its assigned
        material, so a lighting-only bake comes back as albedo x lighting while
        every sibling comes back correct. MEASURED on a 24-instance wall (mtoa
        5.4.5, ROOM_ENV): the owning tile baked 16% hot with a 10-17% step at
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
        exactly that, so :meth:`bake` keeps the batch un-carded and routes
        every INSTANCED target -- the only kind that can lose the flag --
        through this guarantee, one per call, instead of the batch.

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
                    "flag alone.",
                    shader,
                    obj,
                    exc_info=True,
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
                        for sg in (
                            cmds.listConnections(shader, type="shadingEngine") or []
                        ):
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
            resolved = (
                stem.get(long_name) if isinstance(stem, dict) else stem(long_name)
            )
        except Exception:
            self.logger.debug(
                "stem resolver raised for %s; using leaf.", long_name, exc_info=True
            )
            return leaf
        return resolved or leaf

    def _unique_path(
        self,
        output_dir: str,
        name: str,
        used: set,
        fmt: Optional[str] = None,
        claims: Optional[Any] = None,
        owner: Optional[str] = None,
    ) -> str:
        """Collision-free output path for *name*, tracking *used* across the bake.

        Objects that share a material (texture-set stem) or have duplicate leaf
        names would otherwise resolve to the same file and overwrite each other;
        the second gets ``{name}_1``, the third ``{name}_2``, and so on. A name
        *claims* gives a reader other than *owner* (see :meth:`bake`) is
        skipped the same way. *fmt* is the backend's effective format (Arnold
        is always EXR); default ``file_format``. The rule is
        :meth:`ptk.FileUtils.unique_path`.
        """
        return ptk.FileUtils.unique_path(
            output_dir,
            name,
            fmt or self.file_format,
            used,
            claims=claims,
            owners=(owner,) if owner else (),
        )

    def _resolve_backend(self, requested: str) -> str:
        if requested == "auto":
            return "arnold" if self.arnold_available() else "convertSolidTx"
        if requested == "arnold":
            # Asked for BY NAME, so load it rather than fall back past an
            # installed-but-unloaded plugin. ``auto`` keeps the non-loading
            # probe: it means "whatever this session has".
            if not self.ensure_arnold():
                self.logger.warning(
                    "Arnold backend requested but mtoa could not be loaded; "
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

    @staticmethod
    def gpu_available() -> bool:
        """True when Arnold has a GPU it can render on in this session.

        Arnold's own device query (``AiDeviceGetIds``), the one mtoa's render
        settings list the GPUs with. Not cached: a session that loads mtoa
        after a first ask must not keep the stale answer.
        """
        try:
            import arnold as ai

            ids = ai.AiDeviceGetIds(ai.AI_DEVICE_TYPE_GPU)
            return bool(ids) and ai.AiArrayGetNumElements(ids) > 0
        except Exception:
            return False

    def _device_settings(self) -> Dict[str, Any]:
        """``defaultArnoldRenderOptions`` values for :attr:`device` (``{}`` = leave it).

        * ``None`` -- bake on whatever the scene is set to render on.
        * ``"CPU"`` / ``"GPU"`` -- force that device.
        * ``"AUTO"`` -- the GPU wherever Arnold has one (:meth:`gpu_available`),
          with its own CPU fallback pinned on for a GPU that fails at render
          time; the CPU otherwise. Resolved HERE rather than left to that
          fallback, because the device decides how the sample budget is spent
          (:meth:`_sampling_settings`) and that must match the device.

        AUTO prefers the GPU at every size, which is NOT what the Blender
        twin's AUTO does (Cycles rebuilds a session per object, so a small tile
        is cheaper on the CPU). Arnold translates the scene once per RTT call
        and the GPU is faster at both halves: at 64px, where setup is nearly
        all of it, it won 32.6s to 8.9s, and at the same ray budget a 256px
        production floor took 3.4s against 24.2s. (The 25.9x once recorded
        here compared one PRESET on both devices, and the GPU drops the
        preset's GI samples -- it was tracing 1/16 of the rays.)
        """
        device = str(self.device or "").upper()
        if device in ("", "SCENE", "NONE"):
            return {}
        if device == "CPU":
            return {"renderDevice": 0}
        if device == "GPU":
            return {"renderDevice": 1}
        if device == "AUTO":
            if not self.gpu_available():
                self.logger.info(
                    "Device AUTO: Arnold reports no GPU; baking on the CPU."
                )
                return {"renderDevice": 0}
            # Probed on mtoa 5.5: the fallback attribute is snake_case where
            # renderDevice beside it is camelCase, and its enum is "Error:CPU"
            # -- so 1 means a GPU that fails at render time falls back to the
            # CPU instead of failing the bake. (renderDevice's own enum is
            # "CPU:GPU", hence the 0/1 above.)
            return {"renderDevice": 1, "render_device_fallback": 1}
        self.logger.warning(
            "Unknown device %r; baking on the scene's own render device.",
            self.device,
        )
        return {}

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
        # The device rides the same pin: it is a render option, and restoring
        # it with everything else is what keeps a GPU bake from leaving the
        # user's scene set to render on the GPU.
        settings = dict(self.render_settings or {})
        settings.update(self._device_settings())
        if backend != "arnold":
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
        # than only on the attributes module logger. The sampling rides a
        # second pin because it depends on the device and GI samples IN FORCE,
        # which the first one decides -- and it is pinned on every bake, OFF
        # where it does not apply, or a scene rendered with adaptive sampling
        # would carry its own into the bake.
        with Attributes.pinned(
            "defaultArnoldRenderOptions", _logger=self.logger, **settings
        ):
            # Evaluated only now, with the first pin in force.
            sampling = self._sampling_settings()
            with Attributes.pinned(
                "defaultArnoldRenderOptions", _logger=self.logger, **sampling
            ):
                yield

    #: Arnold's adaptive-sampling threshold for a GPU bake: Arnold's own
    #: default, pinned so a scene's render setting never reaches the bake.
    #: Measured on the production floors at quest (AA 4..16): 0.008 bought
    #: 10% less shadow noise for 11% more time -- the default is the trade.
    ADAPTIVE_THRESHOLD: float = 0.015

    @staticmethod
    def _renders_on_gpu() -> bool:
        """Is the render device IN FORCE the GPU (call inside the pin)?"""
        try:
            return cmds.getAttr("defaultArnoldRenderOptions.renderDevice") == 1
        except Exception:
            return False

    def _gpu_budget(self) -> int:
        """:attr:`samples` x the GI diffuse samples in force: a preset's CPU ray budget.

        Arnold's GPU renderer ignores the ray-type sample counts and traces ONE
        diffuse ray per camera sample -- measured on a production floor, GI 4
        and GI 8 baked bit-identical maps there. So a preset's
        ``GIDiffuseSamples`` only ever existed on the CPU: the quest preset
        (AA 4, GI 4) put AA^2 = 16 first-bounce rays into each texel on the
        GPU against AA^2 x GI^2 = 256 on the CPU, and baked 5.1x the per-texel
        noise (0.638 vs 0.124, 2026-09-21) -- the splotches the baked floors
        showed in the WebXR preview. In camera samples, AA x GI is the same
        first-bounce ray count as the CPU's: measured 0.161 against 0.124,
        still 7x faster. The ceiling a GPU bake samples up to
        (:meth:`_sampling_settings`).
        """
        samples = max(1, int(self.samples))
        try:
            diffuse = int(cmds.getAttr("defaultArnoldRenderOptions.GIDiffuseSamples"))
        except Exception:
            return samples
        return samples * max(1, diffuse)

    def _camera_samples(self) -> int:
        """RTT's ``aa_samples``: the samples EVERY texel gets.

        :attr:`samples` on the CPU and on an adaptive GPU bake (whose ceiling
        is :meth:`_gpu_budget`); the whole budget on a GPU bake with
        :attr:`adaptive` off. Read off the render options IN FORCE, so call it
        inside :meth:`_pinned_render_settings`.
        """
        if self._renders_on_gpu() and not self._sampling_settings().get(
            "enable_adaptive_sampling"
        ):
            return self._gpu_budget()
        return max(1, int(self.samples))

    def _sampling_settings(self) -> Dict[str, Any]:
        """``defaultArnoldRenderOptions`` sampling pins for the device in force.

        On a GPU with :attr:`adaptive` on: Arnold's adaptive sampler, every
        texel taking :attr:`samples` and a texel whose noise needs it going on
        up to the preset's whole ray budget (:meth:`_gpu_budget`, AA x GI).
        Measured on the production floors under the table (quest, tiles at 4x
        their cell, two AA seeds; shipped noise after the shrink): the budget
        on every texel -- AA 16 -- took 381s for 1.06% shadow noise and 0.18%
        lit; adaptive 4..16 took 73s for 1.31% and 0.84%. A lit texel stops at
        the floor, where its noise was already below sight (AA 4: 0.16% at
        the 5-texel scale), and the shadows -- where splotches read, and what
        no small-window denoiser removes (the same mottle before and after
        it) -- get the rays: shadow mottle 0.29% against the fixed AA 16's
        0.25% and plain AA 4's 1.06%. A ceiling of AA 32 bought 0.19% for
        2.3x the time; a floor of AA 8, lit 0.39% for 1.9x.

        Anywhere else -- the CPU, which honours the GI samples on its own, or
        :attr:`adaptive` off -- adaptive sampling is pinned OFF. Read off the
        render options IN FORCE, so call it inside the first pin.
        """
        floor = max(1, int(self.samples))
        if not (self.adaptive and self._renders_on_gpu()):
            return {"enable_adaptive_sampling": False}
        ceiling = self._gpu_budget()
        if ceiling <= floor:  # no GI budget above the floor to adapt into
            return {"enable_adaptive_sampling": False}
        return {
            "enable_adaptive_sampling": True,
            "AA_samples_max": ceiling,
            "AA_adaptive_threshold": self.ADAPTIVE_THRESHOLD,
        }

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
        shapes = (
            cmds.listRelatives(obj, shapes=True, noIntermediate=True, fullPath=True)
            or []
        )
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
                uv_set,
                obj,
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
        kwargs.update(
            {
                "resolutionX": self.resolution,
                "resolutionY": self.resolution,
                "fileImageName": out_path,
                "fileFormat": self.file_format,
            }
        )
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
        resolution: Optional[int] = None,
    ) -> Dict[str, Any]:
        """The ``arnoldRenderToTexture`` call args (single source for both paths).

        *resolution* overrides :attr:`resolution` for this one call -- what lets
        an atlas bake render each object at the footprint it will occupy (RTT
        renders one square per call, so the size is per call, not per object).
        """
        kwargs: Dict[str, Any] = dict(
            folder=output_dir,
            resolution=int(resolution or self.resolution),
            # The samples every texel gets on the device in force -- a GPU
            # ignores the GI samples, so its budget rides the camera samples:
            # as the adaptive ceiling, or here (see _sampling_settings).
            aa_samples=self._camera_samples(),
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
            # Pin the pixel filter (see __init__ for why the measured default
            # is gaussian 2.0, not the usual box 1.0 for a bake). GI
            # depth/samples are already pinned via render_settings; unpinned,
            # the filter rode the SCENE's render setting -- silently varying
            # island-edge quality between users and sessions.
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
        resolution: Optional[int] = None,
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
                **self._rtt_kwargs(output_dir, shader, uv_set, resolution)
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
        shapes = (
            cmds.listRelatives(obj, shapes=True, noIntermediate=True, fullPath=True)
            or []
        )
        leaves = {s.rsplit("|", 1)[-1].rsplit(":", 1)[-1] for s in shapes}
        matches = [p for p in new if os.path.splitext(os.path.basename(p))[0] in leaves]
        self.logger.warning(
            "%s wrote %d maps (multi-shape transform); keeping %s.",
            obj,
            len(new),
            os.path.basename((matches or new)[-1]),
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
        size: Optional[Any] = None,
        claims: Optional[Any] = None,
    ) -> Optional[Dict[str, str]]:
        """Bake the objects in as few RTT calls as they allow; map files to objects.

        The objects are partitioned by ``(uv_set flag, bake size)`` -- the two
        things one RTT call cannot vary -- and each part is one call.

        Returns the results dict; ``None`` when the selection can't be
        batched at all (namespaced shapes, or two different shapes whose
        RTT filenames -- :meth:`_rtt_stem` -- would collide and silently
        overwrite each other), when the caller falls back to the per-object
        loop -- or when a render was cancelled before it wrote anything,
        when it must not: the two are told apart by
        :attr:`_batch_cancelled`, set here.
        """
        self._batch_cancelled = False
        longs: List[str] = []
        leaves: Dict[str, List[str]] = {}
        shape_paths: Dict[str, List[str]] = {}
        for obj in objects:
            long_name = cmds.ls(obj, long=True)
            if not long_name:
                self.logger.warning("Skipping unknown object: %s", obj)
                continue
            long_name = long_name[0]
            shapes = (
                cmds.listRelatives(
                    long_name, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            shape_paths[long_name] = shapes
            raw_leaves = [s.rsplit("|", 1)[-1] for s in shapes]
            leaves[long_name] = [raw.rsplit(":", 1)[-1] for raw in raw_leaves]
            if any(":" in raw for raw in raw_leaves):
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
        predicted: Dict[str, List[str]] = {
            long_name: [self._rtt_stem(long_name, s) for s in shape_paths[long_name]]
            for long_name in longs
        }
        stems = [s for names in predicted.values() for s in names]
        if len(set(stems)) != len(stems):
            self.logger.warning(
                "Two targets would write the same RTT filename; falling back "
                "to per-object bakes."
            )
            return None

        total = len(longs)
        last_leaf = longs[-1].rsplit("|", 1)[-1].replace(":", "_")

        # ONE RTT call carries one uv_set flag and one resolution (the command
        # ignores the scene's current set -- see _rtt_kwargs), so the objects
        # are PARTITIONED on exactly those two instead of the whole batch
        # surrendering when they disagree. A production room's meshes reuse
        # differently named lightmap sets (UV2 / lightmapUV / ...), so the old
        # all-or-nothing test abandoned batching on precisely the scenes it
        # was written for -- measured 19s of scene translation per call, then
        # paid once per object. Grouped, each part pays it once.
        groups: Dict[Tuple[Optional[str], int], List[str]] = {}
        for long_name in longs:
            key = (
                self._uv_set_flag(
                    long_name,
                    uv_set.get(long_name) if isinstance(uv_set, dict) else uv_set,
                ),
                self._resolve_size(long_name, size),
            )
            groups.setdefault(key, []).append(long_name)
        if len(groups) > 1:
            self.logger.info(
                "Batching %d object(s) as %d RTT call(s) (grouped by UV set "
                "and bake size).",
                total,
                len(groups),
            )

        pattern = os.path.join(output_dir, "*.exr")
        by_stem: Dict[str, str] = {}
        prev_sel = cmds.ls(selection=True, long=True) or []
        started = 0
        cancelled = False
        try:
            for (flag, resolution), members in groups.items():
                leaf = members[0].rsplit("|", 1)[-1].replace(":", "_")
                if not self._tick(on_progress, started, total, leaf):
                    self.logger.info(
                        "Bake cancelled by caller at %d/%d.", started, total
                    )
                    cancelled = True
                    break
                before = self._output_snapshot(pattern)
                cmds.select(members, replace=True)
                try:
                    cmds.arnoldRenderToTexture(
                        **self._rtt_kwargs(output_dir, shader, flag, resolution)
                    )
                except Exception as e:
                    # One part failing must not discard the parts that DID
                    # render -- the per-object loop's "never lose a bake"
                    # guarantee, applied per call.
                    self.logger.error(
                        "Batch bake failed for %d object(s) at %dpx: %s",
                        len(members),
                        resolution,
                        e,
                    )
                    continue
                finally:
                    started += len(members)
                written = self._new_outputs(pattern, before)
                if not written:
                    # A call that returned without writing a single map was
                    # stopped -- Esc / Cancel on Arnold's render window, which
                    # returns normally -- or failed wholesale. Either way the
                    # next part would start another render the user has to
                    # stop again, and re-baking the members one per call
                    # (in ``bake``) would start one per object: measured on
                    # the production room, a cancelled batch was followed by
                    # a per-object render of every wall, each cancelled in
                    # turn and each reported as "output missing". Stop here.
                    self.logger.warning(
                        "Arnold wrote no map for the %d object(s) in this call "
                        "(render cancelled, or failed before writing); the "
                        "bake stops here.",
                        len(members),
                    )
                    cancelled = self._batch_cancelled = True
                    break
                by_stem.update(
                    (os.path.splitext(os.path.basename(p))[0], p) for p in written
                )
        finally:
            if prev_sel:
                cmds.select(prev_sel, replace=True)
            else:
                cmds.select(clear=True)

        if not by_stem:
            # Nothing rendered at all: mirror the per-object path's guarantee
            # that a determinate progress bar still reaches 100% -- unless the
            # render was CANCELLED, which that path does not tick either.
            if not cancelled:
                self._tick(on_progress, total, total, last_leaf)
            return None if self._batch_cancelled else {}
        # RTT names a file after the Arnold node (see _rtt_stem). Claims are
        # EXCLUSIVE and every object's predicted stem goes first; only a file
        # nobody predicted is left to the older spellings -- bare leaf,
        # "<transform>_<leaf>" -- the net for a naming rule not yet met. A net
        # that ran per object in turn could hand one object ANOTHER's file,
        # shipping its lighting with no warning; unclaimed, it re-bakes.
        free = dict(by_stem)
        files_of: Dict[str, List[str]] = {}
        for long_name in longs:
            files_of[long_name] = [s for s in predicted[long_name] if s in free]
            for s in files_of[long_name]:
                free.pop(s)
        for long_name in longs:
            if files_of[long_name]:
                continue
            leaf = long_name.rsplit("|", 1)[-1].replace(":", "_")
            net = dict.fromkeys(
                s for bare in leaves[long_name] for s in (bare, f"{leaf}_{bare}")
            )
            files_of[long_name] = [s for s in net if s in free]
            for s in files_of[long_name]:
                free.pop(s)

        results: Dict[str, str] = {}
        used: set = set()
        for long_name in longs:
            leaf = long_name.rsplit("|", 1)[-1].replace(":", "_")
            matches = files_of[long_name]
            if not matches:
                # bake() re-bakes it per-object, whose dir-diff needs no name.
                self.logger.warning(
                    "Batch bake found no output for %s (expected %s).",
                    long_name,
                    ", ".join(f"{s}.exr" for s in predicted[long_name]),
                )
                continue
            if len(matches) > 1:
                # Match the per-object path's multi-shape transparency: only
                # the first shape's map is claimed under the object's name.
                self.logger.warning(
                    "%s wrote %d maps (multi-shape transform); claiming %s, "
                    "leaving %s in %s.",
                    long_name,
                    len(matches),
                    matches[0],
                    ", ".join(matches[1:]),
                    output_dir,
                )
            raw = by_stem[matches[0]]
            name = ptk.StrUtils.apply_affix(
                self._resolve_stem(stem, long_name, leaf), prefix, suffix
            )
            out_path = self._unique_path(
                output_dir, name, used, fmt, claims, owner=long_name
            )
            out_path = self._place_output(raw, out_path, used)
            used.add(out_path)
            results[long_name] = out_path
            self.logger.info("Baked %s -> %s", leaf, out_path)

        if not cancelled:
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
        shapes = (
            cmds.listRelatives(obj, shapes=True, noIntermediate=True, fullPath=True)
            or []
        )
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
                    mat,
                    cmds.nodeType(mat),
                )
                continue

            # Remember whatever's currently driving the color so we can
            # restore it later. Two shapes:
            #  - incoming connection -> capture the source plug
            #  - static value        -> capture the tuple of raw floats
            incoming = (
                cmds.listConnections(
                    color_attr, plugs=True, source=True, destination=False
                )
                or []
            )
            static_value: Optional[tuple] = None
            if not incoming:
                raw = cmds.getAttr(color_attr)
                # Color attrs come back as [(r, g, b)] from cmds.
                static_value = raw[0] if isinstance(raw, list) else raw
            self._restore_state.append(
                (
                    color_attr,
                    incoming[0] if incoming else "",
                    static_value,
                    path,
                )
            )
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
                current = (
                    cmds.listConnections(
                        color_attr, plugs=True, source=True, destination=False
                    )
                    or []
                )
                # Disconnect whatever assign_to_diffuse hooked up.
                for src in current:
                    cmds.disconnectAttr(src, color_attr)
                # Reconnect the original driver, or restore the static value.
                if prev_source and cmds.objExists(prev_source.split(".")[0]):
                    cmds.connectAttr(prev_source, color_attr, force=True)
                elif prev_static is not None:
                    cmds.setAttr(color_attr, *prev_static, type="double3")
            except RuntimeError as e:
                self.logger.warning("Could not restore %s: %s", color_attr, e)

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
