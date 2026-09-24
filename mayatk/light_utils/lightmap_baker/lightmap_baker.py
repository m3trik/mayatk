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
* :class:`LightmapRecords` -- the scene record a bake leaves: markers, the
  export manifest, and the files they name

**One bake level, and it is real lightmapping.** :meth:`LightmapBaker.bake`
bakes white-card irradiance (lighting only) onto a separate UV channel (index 1)
and records it. The object's full PBR material and its texture UV0 are **kept
untouched** -- the engine composites ``albedo x lightmap``. A per-object map is
self-contained (mesh UV2 samples it directly, any engine); an atlas
additionally carries one scaleOffset rect per object -- per INSTANCE -- on the
marker, applied at sample time (Unity ``lightmapScaleOffset`` / glTF
``KHR_texture_transform``). A small manifest rides the FBX on the shared
``data_export`` carrier (no sidecar file) so Unity's *native* lightmap slots
can be auto-bound by the optional unitytk editor helper.

:meth:`LightmapBaker.bake` is the whole workflow, the same one the panel runs:
the scene checks, the bake, the record, and a verdict on the result.
:meth:`bake_separated` / :meth:`bake_atlas` are its two bake mechanisms, for a
caller that records the maps itself; :meth:`revert` undoes a bake. A *fused
unlit* level (albedo x lighting flattened onto UV0 behind a stock unlit shader)
was removed: it is not lightmapping, it discards every other map, and it only
ever added a mode to choose wrongly from.

Quality tiers come from :meth:`from_preset` (pythontk ``PresetStore``). HDR EXR
throughout; 8-bit/encoded targets are a later (mostly engine-side) stage. For the
bake primitive alone (no lightmap workflow), use :class:`TextureBaker` directly.
The panel is :class:`~mayatk.light_utils.lightmap_baker.lightmap_baker_slots.LightmapBakerSlots`.
"""

import contextlib
import math
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

import pythontk as ptk

from mayatk.mat_utils.texture_baker import TextureBaker
from mayatk.mat_utils.bake_sets import LightmapExcludeSet
from mayatk.light_utils._light_utils import LightUtils
from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics


@dataclass
class LightmapBakeResult:
    """What one :meth:`LightmapBaker.bake` did -- the same shape in mayatk and blendertk.

    Attributes:
        maps: ``{object: map path}``, every map the bake wrote and recorded.
        rects: ``{object: [scaleX, scaleY, offsetX, offsetY]}``, each object's
            engine binding into its map (the identity for a map of its own).
        excluded: Objects the scene's lightmap exclusion set left out. They
            keep any map they already had.
        hidden: Objects left out because Arnold renders nothing of them --
            hidden or templated, by their own flags or an ancestor's
            (:meth:`LightmapBaker.bake_targets`). They keep any map they
            already had. Always empty in blendertk, whose bakes include
            hidden objects.
        unbaked: Objects the bake was asked for and produced nothing for (a
            cancel, a failed render). They keep any map they already had.
        retired: Map files the bake superseded and deleted -- what its
            objects read before, that nothing reads now
            (:meth:`LightmapRecords.superseding`).
        refused: Why nothing was baked, as a sentence for the artist, or
            ``None``.
        verdict: A warning about the finished maps' level (an unlit or a
            blown-out bake), as a sentence, or ``None``.
    """

    maps: Dict[str, str] = field(default_factory=dict)
    rects: Dict[str, List[float]] = field(default_factory=dict)
    excluded: List[str] = field(default_factory=list)
    hidden: List[str] = field(default_factory=list)
    unbaked: List[str] = field(default_factory=list)
    retired: List[str] = field(default_factory=list)
    refused: Optional[str] = None
    verdict: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(self.maps)

    @property
    def files(self) -> List[str]:
        """The distinct map files, sorted: an atlas 40 objects share counts once."""
        return sorted(set(self.maps.values()))

    @property
    def folders(self) -> List[str]:
        """The distinct folders the maps landed in, compared the way the disk does."""
        spelled: Dict[str, str] = {}
        for path in self.maps.values():
            folder = os.path.dirname(path)
            spelled.setdefault(os.path.normcase(os.path.abspath(folder)), folder)
        return sorted(spelled.values())


class LightmapBaker(ptk.LoggingMixin):
    """Orchestrate the lightmap workflow: check -> bake -> dilate -> record.

    Usage::

        baker = LightmapBaker.from_preset("desktop")          # or (resolution=)
        result = baker.bake(objects)                          # atlas by material
        result.maps                                           # {obj: exr_path}
        # The object keeps its full PBR material; the lightmap rides UV channel 1
        # and the wiring rides the FBX on the data_export carrier -- nothing is
        # destroyed, and baker.revert() clears it (the marker lives on the
        # transform, so revert works across save/reload and from a fresh baker).

    The injected/created :class:`TextureBaker` must emit EXR (the default does);
    the alpha-driven seam dilation depends on Arnold's float RGBA output.
    """

    # The scene record's names, kept here for the callers that read them off
    # the baker; the record itself is :class:`LightmapRecords`.
    LIGHTMAP_INFO_ATTR: str = LightmapRecords.LIGHTMAP_INFO_ATTR
    LIGHTMAP_METADATA: str = LightmapRecords.LIGHTMAP_METADATA
    LIGHTMAP_METADATA_VERSION: int = LightmapRecords.LIGHTMAP_METADATA_VERSION
    FOUND_BY_HINT: str = LightmapRecords.FOUND_BY_HINT
    FOUND_BY_SEARCH: str = LightmapRecords.FOUND_BY_SEARCH

    def __init__(
        self,
        resolution: int = 1024,
        samples: int = 5,
        baker: Optional[TextureBaker] = None,
        gi_depth: int = 3,
        gi_samples: int = 4,
        device: Optional[str] = None,
        include_environment: bool = True,
        denoise: bool = True,
        adaptive: Optional[bool] = None,
        beside_textures: bool = False,
    ):
        super().__init__()
        self.resolution = resolution
        self.samples = samples
        # Save each finished map in the folder its material's texture maps
        # live in, named after that texture set, instead of all of them in one
        # output folder (see :meth:`_texture_homes`). A material without file
        # textures has no such folder, so its map falls back to the bake's
        # ``output_dir``.
        self.beside_textures = bool(beside_textures)
        # Denoise every map at the resolution it SHIPS at -- the object's own
        # map, or the atlas cell a tile is shrunk into (see _finish_tile) --
        # with ImgUtils.denoise_image. Arnold's bake path has none of its own
        # (RTT ignores imagers), so a map shipped its sampling noise as-is:
        # measured on a production floor, 9% per texel in the cell, read as
        # splotches in the WebXR preview; denoised, 1.8%. The Blender twin's
        # knob of the same name runs Blender's own denoiser over its bakes.
        self.denoise = bool(denoise)
        # Bake the scene's environment (HDRI skydome) along with its lights.
        # ON is the scene as authored -- the historical behaviour. OFF mutes
        # the domes for the duration (see :meth:`_muted_environment`): an HDRI
        # is often a backdrop / look-dev convenience rather than the room's
        # real lighting, and baking it in is a flat ambient lift that cannot
        # be removed afterwards.
        self.include_environment = bool(include_environment)
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
            device=device,
            adaptive=True if adaptive is None else adaptive,
            render_settings={
                "GIDiffuseDepth": gi_depth,
                "GIDiffuseSamples": gi_samples,
            },
        )
        if baker is not None:
            # An INJECTED baker keeps its own resolution/samples/render_settings
            # (documented above), but an explicit device= / adaptive= was asked
            # for HERE and the properties read the baker back -- leaving it
            # would make the argument and the property disagree in silence.
            if device is not None:
                self.baker.device = device
            if adaptive is not None:
                self.baker.adaptive = bool(adaptive)
        # One no-lights warning per baker instance (a bake fans out to N
        # single-object passes; warning on each would spam the log).
        self._warned_no_lights = False
        # The scene reads one bake repeats per object, held for that bake
        # only (see :meth:`_cached_reads`); ``None`` outside a bake.
        self._reads: Optional[Dict[str, Dict[str, Any]]] = None

    @property
    def device(self) -> Optional[str]:
        """Which device Arnold bakes on -- ``"GPU"``, ``"CPU"``, ``"AUTO"``, or
        ``None`` for the scene's own setting. Lives on the baker primitive
        (:meth:`TextureBaker._device_settings`); mirrored here so the workflow
        reads and writes it like ``resolution``, and so an INJECTED baker's own
        choice is what answers."""
        return getattr(self.baker, "device", None)

    @device.setter
    def device(self, value: Optional[str]) -> None:
        self.baker.device = value

    @property
    def adaptive(self) -> bool:
        """Whether a GPU bake spends its samples adaptively: the preset's AA on
        every texel, up to AA x GI samples where the noise needs them. Lives on
        the baker primitive (:meth:`TextureBaker._sampling_settings`, which
        also records what it measured); a CPU bake ignores it. Mirrored here
        like :attr:`device`."""
        return bool(getattr(self.baker, "adaptive", False))

    @adaptive.setter
    def adaptive(self, value: bool) -> None:
        self.baker.adaptive = bool(value)

    # ------------------------------------------------------------------
    # Quality-tier presets (pythontk PresetStore: built-in + user tiers)
    # ------------------------------------------------------------------

    #: What a preset may carry, by type: the quality dials, then the switches.
    #: The panel's preset template saves exactly these (plus ``packing``, the
    #: panel's choice between :meth:`bake_separated` and :meth:`bake_atlas`,
    #: which no constructor takes), so a preset saved in the panel builds the
    #: same baker through :meth:`from_preset`. The device is deliberately not
    #: one: it names one machine's hardware, and a preset travels.
    PRESET_INT_KEYS: Tuple[str, ...] = (
        "resolution",
        "samples",
        "gi_depth",
        "gi_samples",
    )
    PRESET_BOOL_KEYS: Tuple[str, ...] = (
        "adaptive",
        "include_environment",
        "denoise",
        "beside_textures",
    )
    #: Retired built-in tier names -> the current one, warning until they go.
    #: ``"quest"`` (until 2026-09-23) named one headset for a tier that serves
    #: every mobile / standalone-VR target; a script or the panel's preset
    #: pointer may still say it. :meth:`from_preset` resolves only a name the
    #: store lacks, so a user preset saved under a retired name still wins.
    _resolve_retired_preset = staticmethod(
        ptk.Deprecation.values(
            {"quest": "mobile"},
            what="LightmapBaker preset",
            remove_in="0.21.0",
            since="2026-09-23",
            reason="The tier was renamed; its settings are unchanged.",
        )
    )

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

        A preset is a small JSON dict of :attr:`PRESET_INT_KEYS` (the quality
        dials every built-in stores) and :attr:`PRESET_BOOL_KEYS` (the
        switches a preset saved from the panel adds) -- the rest of the
        pipeline derives from resolution (gutter padding, dilation width) or
        has a sound default. ``overrides`` win over the preset (e.g.
        ``from_preset("mobile", resolution=1536)``); extra preset keys
        (``description``, the panel's ``packing``) are ignored.

        Built-ins: ``preview`` (256/2), ``mobile`` (1024/4), ``desktop`` (2048/8).
        A retired tier name (``quest``) still resolves, with a notice.
        """
        store = cls.preset_store()
        if not store.exists(name):
            name = cls._resolve_retired_preset(name)
        if not store.exists(name):
            raise ValueError(
                f"Unknown lightmap preset {name!r}. Available: {store.list()}"
            )
        data = {**store.load(name), **overrides}
        # Pass only the keys the preset provides; absent ones fall back to the
        # constructor's own defaults (no duplicated default literals to drift).
        kwargs: Dict[str, Any] = {
            k: int(data[k]) for k in cls.PRESET_INT_KEYS if k in data
        }
        kwargs.update({k: bool(data[k]) for k in cls.PRESET_BOOL_KEYS if k in data})
        # ...and the knobs a preset never stores but an override legitimately
        # passes. Filtering to the preset keys alone silently DROPPED them, so
        # from_preset("mobile", device="GPU") built a baker on the scene's
        # device and said nothing (the Blender twin had the same hole).
        for key in ("device", "baker"):
            if key in overrides:
                kwargs[key] = overrides[key]
        return cls(**kwargs)

    @classmethod
    def bake_targets(cls, objects: Optional[List[str]] = None) -> List[str]:
        """The meshes a bake of *objects* acts on (default: the selection).

        :meth:`TextureBaker.resolve_meshes` -- the one definition of a
        bakeable mesh -- minus the scene's :class:`LightmapExcludeSet`, groups
        counting their descendants. An excluded mesh is only left without a
        map of its own: it stays in the render, so it still casts shadows and
        bounces light onto the meshes that bake. Every bake entry point
        filters through here, so the panel, a headless bake and a preset run
        of the same scene all skip the same objects.

        A mesh Arnold renders nothing of is left out too, and named: one
        HIDDEN or TEMPLATED by its own flags, its shape's or an ancestor's --
        visibility, level-of-detail visibility, a display layer
        (:meth:`DisplayUtils.is_visible`). Its bake returns normally with no
        map -- which the bake reads as the render having been stopped, ending
        the whole bake at the first such mesh of a Scene-scope bake. The
        Blender bridge's lightmap leg drops hidden ones by the same rule
        (``BlenderBridge._bakeable``).
        """
        meshes = TextureBaker.resolve_meshes(objects)
        if not meshes or cmds is None:
            return meshes
        return cls._partition(meshes)[0]

    @classmethod
    def _partition(cls, meshes: List[str]) -> Tuple[List[str], List[str], List[str]]:
        """``(targets, hidden, excluded)``: *meshes* as :meth:`bake_targets` sorts them.

        Kept apart because only one of the two is the Exclude set's doing:
        counted together, a Scene bake of the production room reported its
        two hidden props as excluded. A mesh the set names counts as excluded
        whether or not it renders; each group left out is logged by name.
        """
        from mayatk.display_utils._display_utils import DisplayUtils

        def listed(names: List[str]) -> str:
            return ", ".join(n.rsplit("|", 1)[-1] for n in names[:8]) + (
                " ..." if len(names) > 8 else ""
            )

        def rendered(mesh: str) -> bool:
            # Read from the SHAPE's path up: a shape hidden or templated under
            # a shown transform renders nothing either, and a walk from the
            # transform never reads it. The path is the instance's own.
            try:
                shapes = cmds.listRelatives(
                    mesh, shapes=True, fullPath=True, noIntermediate=True, type="mesh"
                )
                return any(DisplayUtils.is_visible(s) for s in shapes or [mesh])
            except (RuntimeError, ValueError, TypeError):
                return True  # unreadable: this gate saves work, it never costs a bake

        in_set = set(LightmapExcludeSet.meshes())
        excluded = [m for m in meshes if m in in_set]
        hidden = [m for m in meshes if m not in in_set and not rendered(m)]
        if hidden:
            cls.logger.warning(
                "Skipping %d hidden or templated mesh(es): Arnold renders "
                "neither, so it could not bake a map. Show them to bake them: %s",
                len(hidden),
                listed(hidden),
            )
        if excluded:
            cls.logger.info(
                "Skipping %d object(s) in the lightmap exclusion set (%s); they "
                "still light the bake: %s",
                len(excluded),
                LightmapExcludeSet.SET_NAME,
                listed(excluded),
            )
        left_out = set(hidden) | in_set
        return [m for m in meshes if m not in left_out], hidden, excluded

    @staticmethod
    def _nothing_to_bake(hidden: List[str], excluded: List[str]) -> str:
        """Why a bake whose every mesh was left out refused, as a sentence."""

        def are(count: int) -> str:
            return "the object is" if count == 1 else f"all {count} objects are"

        if not hidden:
            return f"Nothing to bake: {are(len(excluded))} in the Exclude set."
        if not excluded:
            return (
                f"Nothing to bake: {are(len(hidden))} hidden or templated, and "
                "Arnold renders neither. Show them to bake them."
            )
        return (
            f"Nothing to bake: {len(excluded)} in the Exclude set, "
            f"{len(hidden)} hidden or templated."
        )

    # ------------------------------------------------------------------
    # The workflow -- what the panel runs, and what a script should
    # ------------------------------------------------------------------

    #: Said when mtoa cannot be loaded. There is no non-Arnold path: every
    #: lightmap bake renders under an Arnold shader override (the white card),
    #: so TextureBaker would drop to convertSolidTx and then refuse the override.
    _NO_ARNOLD = "Arnold (mtoa) could not be loaded — lightmaps bake with Arnold only."

    def bake(
        self,
        objects: Optional[List[str]] = None,
        packing: str = "atlas",
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        intensity: float = 1.0,
        **kwargs,
    ) -> LightmapBakeResult:
        """Bake *objects*' lightmaps and record them: the whole workflow, as the panel runs it.

        1. The meshes in *objects* (default: the selection), minus the scene's
           :class:`LightmapExcludeSet` and the meshes Arnold renders nothing
           of (:meth:`bake_targets`), reported apart.
        2. :meth:`preflight`: Arnold loaded, the tool's own authored lights
           upgraded, and a refusal when the scene has lights and none of them
           can light it.
        3. The bake: :meth:`bake_atlas` (``packing="atlas"``, one shared map
           per material) or :meth:`bake_separated` (``"per_object"``). Both
           migrate the targets' legacy markers first
           (:meth:`LightmapRecords.migrate_legacy`).
        4. *intensity*, when not 1.0, scaled into the maps this bake just
           wrote -- once, so re-recording them can never apply it twice.
        5. :meth:`LightmapRecords.commit` records each map with its rect, and
           the maps the baked objects read before -- when this scene wrote
           them and nothing reads them now -- are deleted
           (:meth:`LightmapRecords.superseding`): a bake after an output
           option changed leaves no old maps behind.
        6. :meth:`bake_verdict` reads the finished maps' level.

        Nothing is reverted first. An object the bake does not finish (a
        cancel, a failed render) keeps the map it had, and that map is intact:
        a bake never writes a file another object reads
        (:meth:`LightmapRecords.claims`), so the only file it replaces is one
        read by the very objects it rewrote -- and the only files it deletes
        are ones no object reads any more.

        Parameters:
            objects: Mesh transforms, their shapes or components; ``None``
                for the selection. A group bakes nothing: only a transform with
                a mesh of its own does (:meth:`TextureBaker.resolve_meshes`).
            packing: ``"atlas"`` or ``"per_object"``.
            output_dir: Where the maps go (see :meth:`bake_separated`).
            prefix / suffix: Name affix around each map's texture-set stem.
            on_progress: ``(done, total, name) -> bool`` per object; return
                ``False`` to cancel the rest.
            intensity: A multiplier baked into the texels. 1.0 matches the
                Maya render; ``math.pi`` matches Unity's realtime-light
                convention (Arnold bakes ``albedo x E / pi``, Unity lights
                ``NdotL x color``). Recorded in the markers, informationally.
            kwargs: Forwarded to the bake mechanism.

        Returns:
            :class:`LightmapBakeResult`: the maps and rects, the
            excluded, hidden and unbaked objects, and the ``refused`` / ``verdict``
            sentences for the artist.

        Raises:
            ValueError: *packing* is neither ``"atlas"`` nor ``"per_object"``.
        """
        if packing not in ("atlas", "per_object"):
            raise ValueError(
                f"packing must be 'atlas' or 'per_object', got {packing!r}"
            )
        result = LightmapBakeResult()
        if cmds is None:
            result.refused = "maya.cmds is not available."
            return result
        scoped = TextureBaker.resolve_meshes(objects)
        if not scoped:
            result.refused = "Nothing to bake: no mesh among the given objects."
            return result
        # The Exclude set, and what Arnold renders nothing of, come off BEFORE
        # anything else touches the scene.
        targets, result.hidden, result.excluded = self._partition(scoped)
        if not targets:
            result.refused = self._nothing_to_bake(result.hidden, result.excluded)
            return result
        result.refused = self.preflight()
        if result.refused:
            return result

        # Atlas packing is chosen BEFORE baking, not after: bake_atlas plans
        # the layout up front so each object bakes at a bounded multiple of the
        # size it will occupy in the atlas, instead of rendering a full map per
        # object and downscaling most of it away (a 50-object room: 6.7 hours
        # full-size on the CPU).
        common = dict(
            output_dir=output_dir,
            prefix=prefix,
            suffix=suffix,
            on_progress=on_progress,
            **kwargs,
        )
        if packing == "atlas":
            packed = self.bake_atlas(targets, **common)
        else:
            packed = {
                o: (path, None)
                for o, path in self.bake_separated(targets, **common).items()
            }
        result.maps = {o: path for o, (path, _rect) in packed.items()}
        result.rects = {
            o: [float(v) for v in (rect or self._IDENTITY_SCALE_OFFSET)]
            for o, (_path, rect) in packed.items()
        }
        result.unbaked = [o for o in targets if o not in result.maps]
        if result.unbaked:
            self.logger.warning(
                "%d object(s) were not baked (cancelled, or failed); they keep "
                "the lightmap they had: %s",
                len(result.unbaked),
                ", ".join(o.rsplit("|", 1)[-1] for o in result.unbaked[:8])
                + (" ..." if len(result.unbaked) > 8 else ""),
            )
        if not result.maps:
            return result
        if float(intensity) != 1.0:
            self._apply_intensity(result.maps.values(), intensity)
        with LightmapRecords.superseding(result.maps) as retired:
            LightmapRecords.commit(
                result.maps, scale_offsets=result.rects, intensity=intensity
            )
        result.retired = retired
        result.verdict = self.bake_verdict(result.maps.values())
        return result

    def preflight(self) -> Optional[str]:
        """Why this scene cannot bake now, or ``None``; fixes what it can on the way.

        Each check was added after a production bake that paid its full cost
        for nothing, and each ran only from the panel until it moved here --
        so a scripted :meth:`bake` skipped all three:

        * **Arnold.** Loaded when it is installed but not yet loaded (mtoa is
          often not auto-loaded); a machine without it is refused, because
          there is no non-Arnold lightmap. An injected baker without
          ``ensure_arnold`` owns its own backend and is not asked.
        * **Authored lights.** A saved scene keeps the authored lights'
          marker but not the session that made them: lights authored before
          per-area emission reopen NORMALIZED and bake ~100x dim, and a manual
          Normalize fix evaporates with every reopen. The tool's OWN lights
          are upgraded (:meth:`LightUtils.upgrade_authored_lights`);
          hand-authored ones are never touched.
        * **All lights off.** Refused only on the unambiguous case: the scene
          HAS lights and not one can contribute. Four correctly configured
          area lights with their transforms hidden is not a look, it is a
          mistake, and it costs a full bake to discover (measured on ROOM_ENV
          2026-08-12: the atlas came back 147x dimmer than the same room's
          previous bake). "No lights at all" is NOT refused: emissive
          materials light an Arnold bake with an empty light list
          (:meth:`TextureBaker.arnold_translation_guard`), so it warns and
          proceeds, and :meth:`bake_verdict` covers a genuinely dead result.
          With :attr:`include_environment` off the render mutes every sky
          dome, so a visible dome is no light here either (blendertk's twin
          already read it that way): hidden fixtures beside a visible HDRI
          dome were baked at full cost, unlit.

        Both sides of the last check come from :meth:`LightUtils.all_lights`,
        never a local ``ls``: it counts Arnold lights, which
        ``cmds.ls(lights=True)`` does not report at all (probed -- an
        aiAreaLight inherits THlocatorShape, not light). A local query would
        read an Arnold-lit room that still holds one legacy hidden native
        light as "lights exist, none contribute", and refuse the bake it is
        the whole point of this workflow to run.
        """
        ensure_arnold = getattr(self.baker, "ensure_arnold", None)
        if ensure_arnold is not None and not ensure_arnold():
            return self._NO_ARNOLD
        upgraded = LightUtils.upgrade_authored_lights()
        if upgraded:
            self.logger.warning(
                "Upgraded %d authored light(s) to per-area emission "
                "(Normalize off): %s",
                len(upgraded),
                ", ".join(n.rsplit("|", 1)[-1] for n in upgraded),
            )
        all_lights = LightUtils.all_lights()
        contributing = LightUtils.contributing_lights()
        if not self.include_environment:
            muted = set(LightUtils.environment_lights())
            all_lights = [light for light in all_lights if light not in muted]
            contributing = [light for light in contributing if light not in muted]
        if all_lights and not contributing:
            self.logger.warning(
                "Bake refused: all %d light(s) in the scene are hidden or at "
                "intensity 0, so Arnold would render no direct light and the "
                "maps would come back essentially unlit. Visibility is "
                "INHERITED -- a light whose own flag is on is still off under "
                "a hidden group.\nScene lights:\n%s",
                len(all_lights),
                self._light_audit(),
            )
            return (
                f"Bake skipped: all {len(all_lights)} scene light(s) are "
                "hidden or at intensity 0 (see Script Editor)."
            )
        return None

    # A finished bake whose brightest map's mean sits below this is not a dark
    # look, it is an unlit render. The line separates two MEASURED
    # populations rather than merely clearing the darkest case seen so far:
    #   unlit  0.008  (room lit only by intensity-1 NORMALIZED area lights)
    #          0.0283 (ROOM_ENV 2026-08-12 13:22)
    #   lit    1.0+   (the same room lit properly)
    #          4.14   (ROOM_ENV 2026-08-12 13:07, reconstructed linear mean)
    # 0.2 sits ~7x above the brightest measured failure and ~5x below the
    # dimmest measured success -- almost exactly their geometric midpoint. The
    # previous 0.02 was calibrated against the 0.008 case alone, so the 0.0283
    # re-bake of an ALREADY-SHIPPED room cleared it by a hair and went out
    # silently; it read in the WebXR preview as "the lightmaps are gone".
    UNLIT_BAKE_MEAN: float = 0.2

    #: ...and at or above which it is not a bright room but a unit error
    #: upstream. A lightmap is scene-relative irradiance, so a correctly lit
    #: room lands within a few multiples of 1.0 whatever its exposure; this
    #: sits ~2 orders above a hot-but-real bake. blendertk's value, for the
    #: failure it was written for: an area light that reached a bake at
    #: 5.4e8 W saturated every atlas at the half-float ceiling and reported
    #: success (mayatk CHANGELOG 2026-08-29).
    BLOWN_BAKE_MEAN: float = 64.0

    @classmethod
    def map_levels(cls, paths) -> Dict[str, Tuple[float, float]]:
        """``{path: (mean RGB, fraction of channels at the half-float ceiling)}``.

        The measurement behind every "is this bake usable" question
        (:meth:`bake_verdict`). A bake has no correct ABSOLUTE level, so
        measuring the RESULT is the only thing that separates a dark look from
        an unlit scene, or a bright room from a broken unit upstream. Mirror of
        blendertk's, which reads through bpy where this reads through cv2.

        An unreadable map is SKIPPED rather than raised on (and without cv2
        nothing is readable): this runs after a finished bake and must never be
        what loses it. Duplicates collapse, so an atlas 46 objects share is
        read once.
        """
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        try:
            import cv2
        except ImportError:
            return {}
        levels: Dict[str, Tuple[float, float]] = {}
        for path in sorted(set(paths or ())):
            try:
                img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
                if img is None:
                    continue
                rgb = img[..., :3] if img.ndim == 3 else img
                if rgb.size:
                    levels[path] = (
                        float(rgb.mean()),
                        float((rgb >= cls.HALF_FLOAT_MAX).mean()),
                    )
            except Exception:
                continue
        return levels

    @classmethod
    def peak_level(cls, paths) -> Optional[Tuple[str, float, float]]:
        """``(path, mean, saturated)`` for the BRIGHTEST map, or ``None`` if none reads.

        Both level checks judge a bake by its brightest map: an unlit one
        because a single lit map disproves "unlit", a blown one because the
        worst offender is what the artist has to be shown.
        """
        levels = cls.map_levels(paths)
        if not levels:
            return None
        path = max(levels, key=lambda p: levels[p][0])
        return (path, *levels[path])

    def bake_verdict(self, paths) -> Optional[str]:
        """A warning about a finished bake's level, or ``None`` when it is plausible.

        The bake renders whatever light the scene supplies, so an unlit or a
        blown-out result is FAITHFUL: nothing upstream errors, and the artist
        otherwise finds out in the web preview, where it reads as a pipeline
        bug (measured: generated area lights left at intensity 1 baked a
        0.008-mean atlas that shipped all the way to a black WebXR room). Each
        verdict is logged with a light audit, so a bad result carries its own
        diagnosis.

        Phrased as UNLIT rather than black: at :attr:`UNLIT_BAKE_MEAN` the maps
        this catches are dim but plainly non-zero, and telling an artist
        staring at visible texels that the bake is "black" sends them looking
        for the wrong failure.
        """
        try:
            peak = self.peak_level(paths)
        except Exception:  # the check must never break a finished bake
            return None
        if peak is None:
            return None
        _path, mean, saturated = peak
        if mean < self.UNLIT_BAKE_MEAN:
            self.logger.warning(
                "Bake is essentially UNLIT (brightest map mean %.4f, expected "
                "1.0+). The bake renders the scene's own lights: a NORMALIZED "
                "area light at fixture scale bakes ~100x dimmer than its "
                "intensity suggests -- turn Normalize OFF on area lights "
                "(per-area emission; the bake does this automatically for "
                "lights the tool authored, so a normalized light here is "
                "hand-made) -- and check lights are visible/unmuted. "
                "StingrayPBS emissive lights a bake only when the translation "
                "guard bridges it to Arnold (TextureBaker."
                "arnold_translation_guard, on by default).\n"
                "Scene lights at bake time:\n%s",
                mean,
                self._light_audit(),
            )
            return (
                "bake is essentially UNLIT — check light intensities "
                "(see Script Editor)."
            )
        if mean >= self.BLOWN_BAKE_MEAN:
            self.logger.warning(
                "Bake is BLOWN OUT (brightest map mean %.4g%s). A lightmap is "
                "scene-relative irradiance and should land within a few "
                "multiples of 1.0 whatever the exposure, so this is a "
                "light-intensity problem rather than a bright room.\n"
                "Scene lights at bake time:\n%s",
                mean,
                ", %.0f%% of it at the half-float ceiling -- data lost"
                % (saturated * 100.0)
                if saturated > 0.001
                else "",
                self._light_audit(),
            )
            return "bake is BLOWN OUT — check light intensities (see Script Editor)."
        return None

    @staticmethod
    def _light_audit() -> str:
        """One line per scene light: the attrs that decide whether a bake is lit.

        Attached to the unlit verdict and to the pre-bake refusal so a dark
        result carries its own diagnosis -- intensity, exposure, normalize,
        emitter scale and visibility are exactly the dials a black bake was
        traced to in production, and none of them are visible in the bake
        output itself.

        ``visible`` is the INHERITED answer
        (:meth:`mayatk.DisplayUtils.is_visible`), the same one
        :meth:`mayatk.LightUtils.contributing_lights` gates the refusal on.
        Reporting the transform's own flag instead would contradict the
        refusal that points here: a light hidden by a grandparent group would
        be listed ``visible=True`` beside a message saying every light is
        hidden.
        """
        from mayatk.display_utils._display_utils import DisplayUtils

        rows = []
        # The same population the refusal counts (Maya's AND Arnold's lights):
        # ``ls(lights=True)`` does not report an aiAreaLight at all, so an
        # Arnold-lit room would be refused over an audit reading "<no lights>".
        for shape in LightUtils.all_lights():
            try:
                t = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
                sx, sy, _sz = cmds.getAttr(f"{t}.scale")[0]
                bits = [
                    f"intensity={cmds.getAttr(f'{shape}.intensity'):g}",
                    f"scale={sx:g}x{sy:g}",
                    f"visible={DisplayUtils.is_visible(shape, consider_templated_visible=True)}",
                ]
                # mtoa spells these ``ai*`` on a native light and bare on its
                # own light nodes; report whichever the shape carries.
                for label, spellings in (
                    ("exposure", ("aiExposure", "exposure")),
                    ("normalize", ("aiNormalize", "normalize")),
                ):
                    for attr in spellings:
                        if cmds.attributeQuery(attr, node=shape, exists=True):
                            bits.append(f"{label}={cmds.getAttr(f'{shape}.{attr}'):g}")
                            break
                rows.append(f"  {t.rsplit('|', 1)[-1]}: " + "  ".join(bits))
            except Exception:
                rows.append(f"  {shape}: <unreadable>")
        return "\n".join(rows) or "  <no lights in the scene>"

    # ------------------------------------------------------------------
    # The bake mechanisms
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def _cached_reads(self):
        """Hold the per-object scene reads one bake repeats, for that bake only.

        A bake asked each object for its texture set two or three times (the
        stem that names its map, the folder :attr:`beside_textures` puts it
        in, its atlas's name) and read its lightmap UV layout three times, two
        of them by switching the current UV set (the tile size plan, the
        coverage mask, the crop). Neither changes during a bake -- nothing in
        it edits a material or a UV -- so each is read once. Re-entrant: an
        inner bake step shares the outer one's reads, and nothing outlives the
        outermost, so a scene edited between two bakes is read afresh.
        """
        if getattr(self, "_reads", None) is not None:
            yield
            return
        self._reads = {"texture_set": {}, "uv_layout": {}}
        try:
            yield
        finally:
            self._reads = None

    def _cached(self, kind: str, obj: str, read: Callable[[str], Any]) -> Any:
        """*read(obj)*, once per bake while :meth:`_cached_reads` holds."""
        reads = getattr(self, "_reads", None)
        if reads is None:
            return read(obj)
        cache = reads[kind]
        if obj not in cache:
            cache[obj] = read(obj)
        return cache[obj]

    def _bake_to_lightmap_uvs(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        uv_set: Optional[str] = None,
        map_size: Optional[int] = None,
        create_uvs: bool = True,
        size: Optional[Any] = None,
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
        keep_coverage: bool = False,
        claims: Optional[Dict[str, Any]] = None,
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
            size: Per-object bake size resolver forwarded to
                :meth:`TextureBaker.bake` -- ``{long_name: px}``,
                ``callable(long_name) -> px``, or ``None`` (every object at the
                full square ``resolution``). :meth:`bake_atlas` passes each
                object's atlas footprint times a bounded supersample, so a map
                that is about to be downscaled into 1/50th of an atlas is not
                rendered at 50x the texels it will keep -- only at the few it
                needs to average its noise down.
            dilate: Edge-pad island gutters, keeping only the texels the
                object's lightmap UV layout fully covers and refilling the
                rest (border slivers, gutters, background) from them. See
                :meth:`_dilate_lightmap` for why the UV layout -- not RTT's
                alpha -- is what separates an island from the edge extension
                baked past its border.
            dilate_iterations: Smooth-averaged gutter ring width in px.
                ``None`` -> scaled to each map's OWN size (an atlas bake gives
                every object its footprint, so one figure for the run would
                over-dilate the small tiles); ``-1`` -> flood the whole
                background with the averaging kernel instead. Either
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
            backend: Forwarded to :meth:`TextureBaker.bake`; ``"arnold"`` in
                practice. Every caller here passes a white-card *shader*, an
                Arnold override, so without mtoa the primitive drops to
                convertSolidTx and then refuses the bake -- there is no
                non-Arnold lightmap.
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
            batch: Share RTT calls between objects (forwarded to
                :meth:`TextureBaker.bake`; measured 7.45x on multi-object
                scenes). With a *shader*, instanced objects still bake one per
                call -- the only place the override is guaranteed to land.
            keep_coverage: Write each map with its island coverage as alpha
                and leave the denoise for later: the map is an atlas TILE,
                about to be shrunk into its cell, and the cell is where both
                belong (see :meth:`_finish_tile`). Off, a map is denoised here
                (:attr:`denoise`) and written opaque -- it ships at this size.
            claims: :meth:`LightmapRecords.claims` -- the file names other
                objects read, which no map of this bake may take (forwarded to
                :meth:`TextureBaker.bake`). ``None`` for maps that are not
                deliverables: an atlas's tiles, baked into a work dir.

        Returns:
            ``{long_object_name: lightmap_path}`` for each successful bake.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        # Resolve to bakeable meshes HERE, not just inside TextureBaker.bake: the UV
        # generation and the legacy migration below run first, and handing either a
        # light or a locator is a warning per object for a node that was never going
        # to bake. One definition of bakeable, shared with blendertk's twin.
        objects = TextureBaker.resolve_meshes(objects)
        if not objects:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        # A LEGACY atlas commit (pre rect-binding) squeezed the lightmap UVs
        # into an atlas rect; restore the unit square before baking (else the
        # bake would fill only that fraction of the map), folding the rect into
        # the binding so the object's current map still samples right. No-op
        # on scenes packed by the rect-binding code, which never edits UVs.
        LightmapRecords.migrate_legacy(objects)

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

        with self._muted_environment():
            result = self.baker.bake(
                objects,
                output_dir=output_dir,
                prefix=prefix,
                suffix=suffix,
                backend=backend,
                uv_set=targets,
                on_progress=on_progress,
                # Name the lightmap after the object's material texture set
                # by default (a callable -- the real materials stay assigned
                # even during a shader-override bake, so it resolves
                # correctly).
                stem=stem if stem is not None else self._stem_of,
                size=size,
                shader=shader,
                batch=batch,
                claims=claims,
            )

        if dilate and result:
            for name, path in result.items():
                try:
                    self._dilate_lightmap(
                        path,
                        alpha_threshold,
                        dilate_iterations,
                        uv_triangles=self._lightmap_uv_triangles(name),
                        denoise=self.denoise and not keep_coverage,
                        keep_coverage=keep_coverage,
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
        defaults to True: the UNINSTANCED objects share as few RTT calls as
        their UV sets and sizes allow (measured 7.45x over per-object calls;
        falls back automatically on colliding filenames), while every
        INSTANCED object bakes in a call of its own, the only place the white
        card is guaranteed to land (:meth:`TextureBaker.bake`).

        The objects go through :meth:`bake_targets`, so a member of the
        scene's :class:`LightmapExcludeSet` gets no map (it still lights the
        rest). No map takes a file name another object reads
        (:meth:`LightmapRecords.claims`); an object's own map keeps its name.
        With :attr:`beside_textures` the maps are baked into a swept work dir
        and each is then placed in its texture set's folder
        (:meth:`_texture_homes`), *output_dir* taking any object without one
        -- placed, not baked there, so a bake that fails partway leaves no
        stray file in a texture folder.

        Returns:
            ``{long_object_name: lightmap_path}`` for each successful bake.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        # Resolved before the white card is created so a selection with nothing
        # bakeable in it doesn't leave a stray card node behind.
        objects = self.bake_targets(objects)
        if not objects:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}
        claims = LightmapRecords.claims()
        with self._cached_reads():
            if not self.beside_textures:
                return self._bake_white_card(
                    objects,
                    output_dir=output_dir,
                    prefix=prefix,
                    batch=batch,
                    claims=claims,
                    **kwargs,
                )

            homes = self._texture_homes(objects)
            output_dir = output_dir or self.baker.default_output_dir("baked_lighting")
            with ptk.TempArtifacts("lightmap_bake", policy="scoped") as tmp:
                baked = self._bake_white_card(
                    objects,
                    output_dir=tmp.dir_path(),
                    prefix=prefix,
                    batch=batch,
                    claims=claims,
                    **kwargs,
                )
                # Moved out before the work dir is swept on exit.
                placed = self._place_unpacked(
                    {name: (path, None) for name, path in baked.items()},
                    output_dir,
                    homes,
                    claims=claims,
                )
            return {name: path for name, (path, _rect) in placed.items()}

    def _bake_white_card(self, objects: List[str], **kwargs) -> Dict[str, str]:
        """Bake *objects* under a fresh white card; the card never outlives it.

        The bake itself behind :meth:`bake_separated` (which decides where the
        maps end up) and :meth:`bake_atlas`'s tiles (which are packed first):
        :meth:`_bake_to_lightmap_uvs` with the card as the per-shape shader
        override, torn down with the shading group it made. *kwargs* go to that
        core (``output_dir``, ``prefix``, ``batch``, ...).
        """
        card = self._create_white_card()
        try:
            return self._bake_to_lightmap_uvs(objects, shader=card, **kwargs)
        finally:
            self._delete_white_card(card)

    def _delete_white_card(self, card: str) -> None:
        """Delete the bake's card WITH the shading group assigning it made.

        The per-object path wears the card by assignment
        (``TextureBaker._forced_shader`` -> ``MatUtils.assign_mat``), which
        wraps it in a shading group; deleting the lambert alone left that
        group behind, empty and shaderless, one more per bake (the production
        room held three: ``lm_whitecardSG`` .. ``SG2``). A group that
        still holds members is KEPT and reported, and so is the card itself:
        a restore that did not land must not leave faces with no material at
        all -- nor in a group whose shader was deleted out from under it.
        """
        if not cmds.objExists(card):
            return
        groups = list(
            dict.fromkeys(
                cmds.listConnections(f"{card}.outColor", type="shadingEngine") or []
            )
        )
        kept = False
        for sg in groups:
            if not cmds.objExists(sg):
                continue
            members = cmds.sets(sg, query=True) or []
            if members:
                self.logger.warning(
                    "The bake's white card still holds %d member(s) of %s after "
                    "the restore; kept so they are not left unassigned: %s",
                    len(members),
                    sg,
                    ", ".join(members[:5]),
                )
                kept = True
                continue
            infos = cmds.listConnections(f"{sg}.message", type="materialInfo") or []
            cmds.delete([sg] + infos)
        if not kept:
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
    def _texture_set(obj: str) -> Optional[Tuple[str, str]]:
        """``(stem, folder)`` of *obj*'s existing texture set, or ``None``.

        The stem (e.g. ``Plants_Metal_Base_01``) is what a baked lightmap is
        named after (``<base>_Lightmap``) instead of the object's often long,
        import-namespaced node name; the folder is where
        :attr:`beside_textures` puts it. One answer for both, so a map never
        takes its name from one texture set and its folder from another.
        Strips the map-type suffix (``_BaseColor`` / ``_Normal`` / …) via
        ``ptk.MapFactory.get_base_texture_name`` -- the same helper
        ``game_shader`` uses.

        The vote is :meth:`MapFactory.dominant_texture_set`, the one rule
        blendertk's twin uses too: only a real MATERIAL MAP votes (an
        environment cube once named a production bake ``diffuse_cube_LightMap``),
        and the majority set wins, so neither a stray map nor the order Maya
        lists them in decides. Maya's own bundled textures are dropped up
        front: its install tree is no place to write a map.

        Returns ``None`` when nothing qualifies, so the bake falls back to the
        object leaf name -- unique per object, and therefore always safer than
        a shared one.
        """
        try:
            paths = MatUtils.get_texture_paths(
                objects=[obj], absolute=True, exclude_bundled=True
            )
        except Exception:
            return None
        return ptk.MapFactory.dominant_texture_set(paths or [])

    @classmethod
    def _texture_set_stem(cls, obj: str) -> Optional[str]:
        """Base name of *obj*'s texture set (:meth:`_texture_set`), or ``None``."""
        found = cls._texture_set(obj)
        return found[0] if found else None

    def _texture_set_of(self, obj: str) -> Optional[Tuple[str, str]]:
        """:meth:`_texture_set`, read once per bake (:meth:`_cached_reads`)."""
        return self._cached("texture_set", obj, self._texture_set)

    def _stem_of(self, obj: str) -> Optional[str]:
        """:meth:`_texture_set_stem`, read once per bake -- the default map name."""
        found = self._texture_set_of(obj)
        return found[0] if found else None

    def _texture_homes(self, objects: List[str]) -> Dict[str, str]:
        """``{object: folder}`` -- where :attr:`beside_textures` puts each map.

        The folder of the object's texture set (:meth:`_texture_set`), so
        ``<set>_Lightmap.exr`` lands beside ``<set>_BaseColor.png``. Only a
        folder that exists on THIS machine qualifies: a texture path that
        resolves nowhere (another drive, a moved library) must not have a bake
        create it. An object without one is left out, and its map takes the
        bake's ``output_dir``.

        Read BEFORE the bake: during it an instanced target wears the white
        card (``TextureBaker._forced_shader``), and a query then would find
        the card's (absent) textures.
        """
        homes: Dict[str, str] = {}
        for obj in objects:
            found = self._texture_set_of(obj)
            if found and os.path.isdir(found[1]):
                homes[obj] = found[1]
        if len(homes) != len(objects):
            self.logger.info(
                "Beside textures: %d of %d object(s) have no texture folder on "
                "disk; their maps go to the output folder.",
                len(objects) - len(homes),
                len(objects),
            )
        return homes

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Engine consumption (lighting-only -> keep maps + metadata bridge)
    # ------------------------------------------------------------------

    # Identity atlas transform: the object's 0-1 lightmap UVs map to the whole
    # texture (the per-object, non-atlased case). The record's constant.
    _IDENTITY_SCALE_OFFSET: Tuple[float, float, float, float] = (
        LightmapRecords.IDENTITY_SCALE_OFFSET
    )

    #: Bake sizes are rounded UP to a multiple of this so near-equal cells
    #: share one ``arnoldRenderToTexture`` call. One RTT call carries one
    #: resolution, and a call costs a full scene translation (measured 19.1s in
    #: a production room) against ~0.11ms per sample-texel -- so paying a few
    #: percent more texels to halve the number of translations is the trade
    #: that wins. Instanced copies of one mesh have equal area anyway and land
    #: in a single call regardless.
    _ATLAS_BAKE_QUANTUM: int = 32

    #: How many times its cell, per axis, an atlas tile is rendered at (never
    #: above :attr:`resolution`) before the assembler's INTER_AREA resize
    #: takes it down into the cell. RTT ignores imagers, so Arnold denoises
    #: nothing, and a tile rendered AT its cell keeps every sample's noise at
    #: full strength; the shrink averages it first, and :attr:`denoise` then
    #: works on what survives, at the cell (:meth:`_finish_tile`).
    #: Measured on a production room at mobile (1024 / 4 samples), with two AA
    #: seeds so the difference is pure sampling noise: a floor cell's shadow
    #: carried 21.9% relative noise rendered at the cell, 15.2% at 2x and 9.5%
    #: at 4x -- 4x being, for those 256px cells, exactly the full-size render
    #: the pre-2026-09-01 bake-full-then-pack gave them. At the cell it read as
    #: splotches in the WebXR preview. The cost is the square of this over the
    #: plan's own texels (4 floors: 10s -> 63s on the GPU) and never more than
    #: a full map per object, i.e. never above the old path's.
    #:
    #: The rays the shrink averages are not wasted: they buy exactly what the
    #: same rays spent as camera samples at the cell would. Measured at one
    #: budget per SHIPPED texel (the four production floors, GPU, two seeds):
    #: 4x at AA 8 took 99s for 2.18% shadow noise, 2x at AA 16 117s for 2.10%,
    #: 1x at AA 32 125s for 1.98% -- the same noise, and the texels the
    #: cheaper way to spend it on a GPU. What the budget IS, and where it
    #: goes, is the texture baker's (:meth:`TextureBaker._sampling_settings`).
    _ATLAS_SUPERSAMPLE: int = 4

    def bake_atlas(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        **kwargs,
    ) -> Dict[str, Tuple[str, List[float]]]:
        """Bake a material-atlased lighting-only lightmap set -- plan first, then bake to plan.

        The whole "Atlas by Material" path in one call, and the one to prefer
        over :meth:`bake_separated` + :meth:`pack_atlas`. The layout depends
        only on **surface area and material assignment**, both known before a
        single ray is traced, so the plan is computed up front and each object
        is baked at a size derived from the pixel footprint it will occupy.
        Baking every object at the full atlas resolution and then downscaling
        it -- what the two-call form does -- spends N times the rays on the
        objects that share an atlas, which are exactly the ones whose maps
        shrink most: measured in a production room (Arnold, 8 objects), bake
        time is 19.1s of scene translation per RTT call plus ~0.11ms per
        sample-texel on the CPU, so 50 objects sharing a 1024 atlas at 4
        samples cost ~6.7 HOURS baked full-size.

        Not all of those rays are waste, though: the downscale into the cell
        averages this path's sampling noise -- RTT ignores imagers, so Arnold
        denoises nothing (blendertk's twin bakes to the bare plan because its
        Cycles bakes go through Blender's denoiser). Tiles baked AT their cell
        shipped 2.3x the shadow noise of the full-size bake and read as
        splotches in the WebXR preview, so each tile renders at
        :attr:`_ATLAS_SUPERSAMPLE` times its cell, never above a full map
        (:meth:`_plan_bake_sizes`): exactly the old path's noise for every cell
        spanning at least 1/:attr:`_ATLAS_SUPERSAMPLE` of the atlas, that same
        per-texel noise for every smaller one (the old path over-served those),
        and a fraction of its rays. What survives the shrink is then denoised
        at the cell (:attr:`denoise`, :meth:`_finish_tile`): each tile is baked
        with its island coverage for that.

        The lightmap UVs are built here rather than inside the bake, each
        object's against its OWN size: the island gutter is packed in UV space
        against a target map size, so a set packed for the atlas and then baked
        at a fiftieth of it would carry a gutter fifty times too thin in
        texels. A mesh that already has a valid lightmap set keeps it
        (``create_lightmap_uvs`` reuses rather than repacks), exactly as the
        per-object path does -- an artist's tuned unwrap is not this method's
        to throw away. The UVs are packed against the CELL while
        :meth:`_plan_bake_sizes` renders above it, which is safe in that
        direction only -- and it is the only direction that method moves
        (coverage divides by a fraction, the supersample multiplies, the
        quantum rounds up), so the packed gutter can end up wider in texels
        than asked for but never thinner.

        Intermediates never reach *output_dir*: the per-object tiles are baked
        into a tracked temp dir and only the finished maps are placed, so a
        bake cannot litter a project's texture folder with files the caller has
        no use for. Anything the pack could not consolidate (cv2 missing, a
        group that failed) is still moved out of the work dir before it is
        swept -- a finished bake is never lost to a packing problem. With
        :attr:`beside_textures` the atlases are packed in the work dir too and
        each is placed in its material group's texture folder
        (:meth:`_texture_homes`), *output_dir* taking a group without one.

        The plan resolves through :meth:`bake_targets`, so members of the
        scene's :class:`LightmapExcludeSet` take no cell (they still light the
        rest). Extra ``kwargs`` are forwarded to the bake core
        (:meth:`_bake_to_lightmap_uvs`). *prefix* / *suffix* name both the
        tiles and the atlas. Returns :meth:`pack_atlas`'s
        ``{object: (atlas_path, rect)}``.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        # The plan resolves the input too, so it doubles as the "is there
        # anything to bake" answer.
        plan = self.atlas_plan(objects)
        planned = [name for entries in plan.values() for name, _rect in entries]
        if not planned:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        output_dir = output_dir or self.baker.default_output_dir("baked_lighting")
        # Before anything reads the lightmap UVs: a LEGACY atlas marker's
        # squeezed UVs would plan every tile from its cell's fraction of the
        # layout (see _bake_to_lightmap_uvs, which runs the same migration).
        LightmapRecords.migrate_legacy(planned)
        claims = LightmapRecords.claims()
        with self._cached_reads():
            homes = self._texture_homes(planned) if self.beside_textures else None
            sizes = self.plan_sizes(plan)
            uv_set = kwargs.pop("uv_set", None) or UvDiagnostics.LIGHTMAP_UV_SET
            if kwargs.pop("create_uvs", True):
                by_size: Dict[int, List[str]] = {}
                for name, (w, h) in sizes.items():
                    by_size.setdefault(max(w, h), []).append(name)
                for px, members in by_size.items():
                    UvUtils.create_lightmap_uvs(
                        members, uv_set=uv_set, map_size=px, quiet=True
                    )
            bake_sizes = self._plan_bake_sizes(sizes)

            with ptk.TempArtifacts("lightmap_bake", policy="scoped") as tmp:
                baked = self._bake_white_card(
                    planned,
                    output_dir=tmp.dir_path(),
                    prefix=prefix,
                    batch=kwargs.pop("batch", True),
                    suffix=suffix,
                    uv_set=uv_set,
                    create_uvs=False,  # built above, at each object's own size
                    size=bake_sizes,
                    # Tiles, not deliverables: each keeps its coverage for the
                    # pack, which denoises it at the cell it ships in. (Still
                    # named after its texture set: a group the pack cannot
                    # consolidate ships its tiles as they are.)
                    keep_coverage=True,
                    **kwargs,
                )
                if not baked:
                    return {}
                try:
                    packed = self.pack_atlas(
                        baked,
                        # Beside the textures, the atlases are staged in a
                        # folder of their own -- never among the tiles, whose
                        # names they share -- and placed per group below.
                        output_dir=(
                            os.path.join(tmp.dir_path(), "atlas")
                            if homes is not None
                            else output_dir
                        ),
                        prefix=prefix,
                        suffix=suffix,
                        plan=plan,
                        claims=claims,
                    )
                except Exception as e:  # cv2 missing, or an unforeseen pack error
                    self.logger.warning(
                        "Atlas packing failed (%s); keeping per-object maps.", e
                    )
                    packed = {
                        n: (p, list(self._IDENTITY_SCALE_OFFSET))
                        for n, p in baked.items()
                    }
                # The work dir is swept on exit, so nothing may still point
                # into it.
                return self._place_unpacked(packed, output_dir, homes, claims=claims)

    def atlas_plan(
        self, objects: Optional[List[str]] = None
    ) -> Dict[str, List[Tuple[str, List[float]]]]:
        """``{material: [(object, rect), ...]}`` -- the atlas layout, decided before baking.

        Groups the meshes by primary material and gives each an area-weighted,
        gutter-inset, texel-snapped rect (a solo group keeps the identity rect:
        it is already its own atlas). Instanced transforms are FIRST-CLASS:
        every copy shares one shape / UV set but stands somewhere different and
        receives different light, so each gets its own rect over the one shared
        0-1 unwrap -- the rect travels as the per-instance ``scaleOffset``
        binding, never into the shared UVs. Weights are per-instance world
        surface area, so a scaled copy earns proportional texels.

        Pure bookkeeping -- nothing is baked, read from disk or written, and it
        reads only geometry and material assignment, which is what lets
        :meth:`bake_atlas` size each bake from it before the lightmap UVs even
        exist. The meshes come from :meth:`bake_targets`, so an excluded one
        takes no cell.
        """
        objects = self.bake_targets(objects)
        names = sorted({(cmds.ls(o, long=True) or [None])[0] for o in objects} - {None})
        groups: Dict[str, List[str]] = {}
        for name in names:  # deterministic rect order (matches pack_atlas)
            key = self._primary_material(name) or "__no_material__"
            groups.setdefault(key, []).append(name)

        gutter = self._atlas_gutter()
        plan: Dict[str, List[Tuple[str, List[float]]]] = {}
        for key, group in groups.items():
            if len(group) == 1:
                plan[key] = [(group[0], list(self._IDENTITY_SCALE_OFFSET))]
                continue
            weights = [self._surface_area(o) for o in group]
            # Free a pixel gutter around every rect (content is inset, then the
            # atlas is dilated into the freed border) so mip levels and
            # bilinear taps can't bleed across neighboring objects. The cell is
            # SNAPPED to the texel grid so placement (assemble_atlas writes at
            # rounded pixel edges) and the published rect derive from the same
            # integer window; publishing then re-aims each rect at border-texel
            # centers so edge taps never straddle into a neighbor.
            rects = ptk.ImgUtils.snap_atlas_rects(
                ptk.ImgUtils.inset_atlas_rects(
                    ptk.ImgUtils.compute_atlas_layout(weights), self.resolution, gutter
                ),
                self.resolution,
            )
            plan[key] = [(n, [float(v) for v in rect]) for n, rect in zip(group, rects)]
        return plan

    def plan_sizes(
        self, plan: Dict[str, List[Tuple[str, List[float]]]]
    ) -> Dict[str, Tuple[int, int]]:
        """``{object: (width, height)}`` -- the pixel footprint each object occupies.

        The size that makes an :meth:`atlas_plan` exact: the assembler resizes
        each tile into these dimensions anyway, so producing them at any other
        size is work thrown away. Derived through
        ``ptk.ImgUtils.atlas_pixel_rects``, the same rounding SSoT
        :meth:`_pack_group` places with, so a tile never needs rescaling.
        """
        sizes: Dict[str, Tuple[int, int]] = {}
        for entries in plan.values():
            pixel_rects = ptk.ImgUtils.atlas_pixel_rects(
                [rect for _n, rect in entries], self.resolution
            )
            for (name, _rect), (row0, row1, col0, col1) in zip(entries, pixel_rects):
                sizes[name] = (max(1, col1 - col0), max(1, row1 - row0))
        return sizes

    def _plan_bake_sizes(self, sizes: Dict[str, Tuple[int, int]]) -> Dict[str, int]:
        """``{object: px}`` -- the square each object is actually rendered at.

        The cell's own size, with three corrections:

        * **Island coverage.** :meth:`_pack_group` crops a partial-coverage map
          to its island bbox and folds the crop into the published rect, so a
          map whose islands fill 60% of the unwrap contributes only 60% of its
          texels to the cell. Rendering the cell size flat would hand the
          assembler a tile it has to UPSCALE, so the size is divided by the
          coverage the crop will take.
        * **Supersampling** by :attr:`_ATLAS_SUPERSAMPLE`: the resize into the
          cell is the only thing that averages this path's sampling noise, so
          the tile renders above its cell (see that attribute for the
          measurement). Capped at :attr:`resolution`: a full map is the ceiling
          either way.
        * **Quantization** to :attr:`_ATLAS_BAKE_QUANTUM`, so near-equal cells
          share one RTT call (see that attribute).
        """
        out: Dict[str, int] = {}
        quantum = max(1, int(self._ATLAS_BAKE_QUANTUM))
        supersample = max(1, int(self._ATLAS_SUPERSAMPLE))
        for name, (width, height) in sizes.items():
            px = max(width, height)
            bbox = self._lightmap_uv_bbox(name)
            if bbox:
                u0, v0, u1, v1 = bbox
                # The bake is square, so the axis needing the most
                # magnification decides. Mirrors _crop_to_island's own test:
                # it crops unless BOTH axes are already near-full.
                extent = min(u1 - u0, v1 - v0)
                if 0.0 < extent < self._CROP_MAX_COVERAGE:
                    px = int(math.ceil(px / extent))
            px *= supersample
            px = min(int(self.resolution), -(-px // quantum) * quantum)
            out[name] = max(1, px)
        return out

    def _place_unpacked(
        self,
        packed: Dict[str, Tuple[str, Optional[List[float]]]],
        output_dir: str,
        homes: Optional[Dict[str, str]] = None,
        claims: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Tuple[str, Optional[List[float]]]]:
        """Move each map still outside its folder into it; return the fixed mapping.

        A map's folder is its object's entry in *homes* (the texture folders
        :attr:`beside_textures` places maps in), else *output_dir*.
        :meth:`bake_atlas` stages its tiles in a swept temp dir, so a map the
        pack left un-consolidated (a failed group keeps its per-object map, and
        a missing cv2 keeps all of them) would otherwise be handed back as a
        path that is about to stop existing. One move per unique file -- an
        atlas shared by six objects is not moved six times, and the first of
        them (plan order) decides its folder. No file lands on a name *claims*
        (:meth:`LightmapRecords.claims`) gives to an object the file is not
        placed for, nor over a file on disk the objects it is placed for do
        not ALL read (see below).

        A map that cannot be placed at all is left out of the returned
        mapping -- the caller reports its objects unbaked, and they keep the
        map and marker they had. Handing back its work-dir path instead
        pointed the marker at a file the caller was about to sweep.
        """
        homes = homes or {}
        claims = claims or {}
        failed: set = set()
        # The objects each source file is placed for: a name only they read is
        # theirs to replace, a name anyone else reads is not.
        placed_for: Dict[str, set] = {}
        for name, (path, _rect) in packed.items():
            placed_for.setdefault(os.path.abspath(path), set()).add(name)
        moved: Dict[str, str] = {}
        out: Dict[str, Tuple[str, Optional[List[float]]]] = {}
        for name, (path, rect) in packed.items():
            source = os.path.abspath(path)
            if source in moved:
                out[name] = (moved[source], rect)
                continue
            if source in failed:
                continue
            folder = homes.get(name) or output_dir
            if os.path.normcase(os.path.dirname(source)) == os.path.normcase(
                os.path.abspath(folder)
            ):
                moved[source] = path
                out[name] = (path, rect)
                continue
            stem, ext = os.path.splitext(os.path.basename(path))
            os.makedirs(folder, exist_ok=True)
            # An adjacent name rather than a refusal when the destination is
            # held open (the DCC's own texture cache, or a cloud sync indexing
            # the fresh render) -- the same policy as
            # ``TextureBaker._place_output``, and here it is not merely
            # cosmetic: the source sits in a work dir the caller is about to
            # sweep, so refusing loses the bake outright. A name is this map's
            # only when no one else reads it, and a file already THERE only
            # when the objects it is placed for are all its readers -- a
            # re-bake over its own map. A file no marker in this scene claims
            # is someone else's: a texture folder other scenes share (Beside
            # Material Textures) holds their maps under the same names, and
            # replacing one handed that scene this one's lighting. Nothing
            # placed earlier in this call is replaced at all.
            placed = {os.path.normcase(p) for p in moved.values()}
            owners = placed_for.get(source, set())
            dst, error = None, None
            k = attempts = 0
            while attempts < 4:
                candidate = os.path.join(
                    folder, f"{stem}{ext}" if k == 0 else f"{stem}_{k}{ext}"
                )
                k += 1
                readers = claims.get(os.path.basename(candidate).lower())
                mine = bool(readers) and set(readers) <= owners
                if (
                    os.path.normcase(candidate) in placed
                    or (readers and not mine)
                    or (os.path.exists(candidate) and not mine)
                ):
                    continue
                attempts += 1
                try:
                    self._move_into_place(source, candidate)
                    dst = candidate
                    break
                except OSError as e:
                    error = e
            if dst is None:
                self.logger.error(
                    "Could not place %s in %s (%s); %s keep the map they had.",
                    os.path.basename(path),
                    folder,
                    error,
                    ", ".join(sorted(o.rsplit("|", 1)[-1] for o in owners)) or name,
                )
                failed.add(source)
                continue
            if os.path.basename(dst) != f"{stem}{ext}":
                self.logger.warning(
                    "%s%s is held by another process; wrote %s instead.",
                    stem,
                    ext,
                    os.path.basename(dst),
                )
            moved[source] = dst
            out[name] = (dst, rect)
        return out

    @staticmethod
    def _move_into_place(source: str, destination: str) -> None:
        """Move *source* onto *destination*, never deleting what is there first.

        Staged beside the destination, then swapped in by one ``os.replace``,
        so a failure anywhere leaves the destination's old file as it was --
        the object keeps its map. Deleting first and moving second lost both
        when the move failed (a full disk, a folder the user cannot write). A
        swap that fails puts the source back, for the caller's next name.

        Raises:
            OSError: The move or the swap failed; *destination* is untouched.
        """
        stem, ext = os.path.splitext(os.path.basename(destination))
        staged = os.path.join(
            os.path.dirname(destination), f".{stem}.{os.getpid()}.part{ext}"
        )
        shutil.move(source, staged)
        try:
            os.replace(staged, destination)
        except OSError:
            try:
                shutil.move(staged, source)
            except OSError:
                pass
            raise

    def _atlas_gutter(self) -> int:
        """Bleed margin (px) freed around each rect, scaled to the atlas resolution."""
        return max(2, self.resolution // 256)

    def pack_atlas(
        self,
        mapping: Dict[str, str],
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        keep_sources: bool = False,
        plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None,
        claims: Optional[Dict[str, Any]] = None,
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
        per-object texture explosion. Re-packing only PART of a group is the one
        exception: the members left out -- or failed -- still sample the old
        atlas, so its name stays theirs (:meth:`LightmapRecords.claims`) and
        the new one takes the next free ``_<k>``. A single-object group is left
        as its own map with an identity rect.

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
            plan: An :meth:`atlas_plan` computed earlier -- pass the one the
                sources were BAKED against (:meth:`bake_atlas` does) so the
                layout cannot be re-derived differently from the one that
                decided each map's size. ``None`` computes it here, which is
                the bake-full-then-pack path.
            claims: :meth:`LightmapRecords.claims` as the bake read it;
                ``None`` reads the scene's now.

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
        # Sources may live anywhere (``bake_atlas`` stages them in a temp dir),
        # so the destination is not guaranteed to exist yet -- and every branch
        # below writes, moves or copies into it.
        os.makedirs(output_dir, exist_ok=True)

        # The layout: material groups and their area-weighted rects. Taken from
        # the caller's plan when it has one, so the rects a bake was SIZED
        # against are the rects it is packed into. Objects the plan knows but
        # this mapping does not (a bake that failed) simply leave their cell
        # empty -- the gutter fill covers it -- rather than re-flowing the
        # layout into rects nothing was rendered for.
        plan = plan if plan is not None else self.atlas_plan(list(mapping))
        groups: Dict[str, List[Tuple[str, List[float]]]] = {}
        for key, entries in plan.items():
            kept = [(n, rect) for n, rect in entries if n in mapping]
            if kept:
                groups[key] = kept
        # A map the layout does not name still has to come out the other side:
        # this method's contract is that a bake is never lost. It reaches here
        # when a name no longer resolves to a mesh (deleted between bake and
        # pack) or when a caller hands in a plan built from a different set --
        # both of which the layout walk above would otherwise drop in silence.
        # Each becomes its own single-object group, i.e. its own map with the
        # identity rect, which is exactly what a solo group already means.
        laid_out = {n for entries in groups.values() for n, _rect in entries}
        orphans = [n for n in sorted(mapping) if n not in laid_out]
        if orphans:
            self.logger.warning(
                "Atlas: %d map(s) are not in the layout; keeping each as its "
                "own map (identity rect): %s",
                len(orphans),
                ", ".join(o.rsplit("|", 1)[-1] for o in orphans[:5]),
            )
            for name in orphans:
                groups[name] = [(name, list(self._IDENTITY_SCALE_OFFSET))]

        # Every source map, so an atlas name can't land on a *different* group's
        # not-yet-consumed source (e.g. duplicated materials sharing a texture
        # set -> same stem, different group). A group may overwrite its OWN
        # sources (read into memory first), so those are excluded per group.
        all_sources = {os.path.abspath(p) for p in mapping.values()}
        # ...and never on an atlas an object outside the group still samples:
        # a partial re-bake of a group gets an atlas of its own.
        if claims is None:
            claims = LightmapRecords.claims()

        out: Dict[str, Tuple[str, List[float]]] = {}
        used: set = set()
        with self._cached_reads():
            for key, entries in groups.items():
                objs = [name for name, _rect in entries]
                try:
                    self._pack_group(
                        key,
                        entries,
                        mapping,
                        all_sources,
                        output_dir,
                        prefix,
                        suffix,
                        out,
                        used,
                        keep_sources,
                        claims,
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
        entries: List[Tuple[str, List[float]]],
        mapping: Dict[str, str],
        all_sources: set,
        output_dir: str,
        prefix: str,
        suffix: str,
        out: Dict[str, Tuple[str, List[float]]],
        used: set,
        keep_sources: bool = False,
        claims: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Pack one material group's maps into its atlas (see :meth:`pack_atlas`).

        Consolidates the group's per-object maps into one shared EXR and
        records each object's ``(atlas_path, rect)`` into *out* (mutated;
        *used* tracks atlas paths taken this pack, *claims* the objects
        reading each file name). UVs are never edited -- the rect is the
        engine binding. Split out so :meth:`pack_atlas` can guard each group
        independently -- a group-level failure falls back to per-object maps
        without poisoning other groups.

        *entries* is the group's ``[(object, rect)]`` slice of the plan,
        pre-sorted by the caller; instanced siblings each pack their own map
        into their own rect.
        """
        import cv2
        import numpy as np

        objs = [name for name, _rect in entries]
        foreign = all_sources - {os.path.abspath(mapping[o]) for o in objs}
        base = self._stem_of(objs[0]) or key.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        name = ptk.StrUtils.apply_affix(base, prefix, suffix)

        if len(objs) == 1:
            atlas_path = self._unique_atlas_path(
                output_dir, name, used, foreign, claims, owners=objs
            )
            # A one-object group is its own atlas (identity rect): adopt the
            # texture-set name without a re-encode -- unless the map still
            # carries exact-zero texels (legacy bakes / no-alpha sources that
            # skipped the dilate rescue: rendered-dead geometry, unfilled
            # background), which every mip level would average into the
            # island as a dark halo, or is a TILE carrying its coverage
            # (bake_atlas), finished at its own size -- the whole map is its
            # cell -- and written opaque.
            src = mapping[objs[0]]
            img = cv2.imread(src, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
            tile = img is not None and img.ndim == 3 and img.shape[2] == 4
            if tile:
                img = self._finish_tile(img, img.shape[1::-1])
            rgb = img[..., :3] if img is not None and img.ndim == 3 else None
            empty = None if rgb is None else ~(rgb > 0).any(axis=2)
            heal = empty is not None and empty.any() and not empty.all()
            if tile or heal:
                if heal:
                    img = ptk.ImgUtils.fill_empty_texels(img[..., :3], mask=~empty)
                self._write_lightmap_exr(atlas_path, img)
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

        # The cells come from the plan (see :meth:`atlas_plan` for how they
        # are weighted, gutter-inset and texel-snapped) -- the same rects the
        # bake was sized against, never re-derived here.
        gutter = self._atlas_gutter()
        rects = [rect for _name, rect in entries]

        images: List[Any] = []
        cells: List[List[float]] = []  # placement rects (the layout's cells)
        placed: List[Tuple[str, List[float]]] = []  # published (engine) rects
        for obj, rect in zip(objs, rects):
            img = cv2.imread(mapping[obj], cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
            if img is None:
                self.logger.warning("Atlas: unreadable map for %s; skipping.", obj)
                continue
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
            # At the size it will occupy, denoised there when the tile carries
            # its coverage -- the SAME rounding the assembler places with, so
            # its resize is then an identity.
            row0, row1, col0, col1 = ptk.ImgUtils.atlas_pixel_rects(
                [cell], self.resolution
            )[0]
            size = (max(1, col1 - col0), max(1, row1 - row0))
            images.append(self._finish_tile(img, size))
            cells.append(cell)
            placed.append((obj, published))
        if not images:
            return
        # Reserved for the members actually IN it: a member whose map could
        # not be read gets no new record and keeps sampling its old file with
        # its old rect, so counted as an owner it let the group write over the
        # very atlas it still reads (blendertk's twin had the same order).
        atlas_path = self._unique_atlas_path(
            output_dir, name, used, foreign, claims, owners=[o for o, _so in placed]
        )

        # The coverage mask is exact (the placed pixel rects) -- a luminance
        # mask would treat valid near-black texels as empty. Bounds are
        # clamped BOTH ways: a rect edge that rounds past the canvas would
        # otherwise leave the atlas frame outside the mask and never dilated.
        mask = np.zeros((self.resolution, self.resolution), dtype=bool)
        h, w = mask.shape
        for row0, row1, col0, col1 in ptk.ImgUtils.atlas_pixel_rects(
            cells, self.resolution
        ):
            mask[
                max(row0, 0) : min(max(row1, 0), h), max(col0, 0) : min(max(col1, 0), w)
            ] = True
        atlas = ptk.ImgUtils.assemble_atlas(images, cells, self.resolution)
        # Fill the gutters from the placed content ...
        atlas = ptk.ImgUtils.dilate_image(atlas, mask=mask, iterations=gutter + 1)
        # ... then EVERYTHING still exactly zero -- background beyond the
        # dilation ring AND any zero that arrived INSIDE a cell (legacy
        # sources that skipped the dilate rescue: geometry below the floor
        # slab / behind trim bakes full-coverage black -- the 12:45 room
        # atlas shipped 1440 such texels, banded along the wall/floor
        # junctions; 0 after this). Cell rects are deliberately NOT
        # blanket-trusted as content; only genuinely non-zero texels
        # spread, and real near-black shadow (> 0) is untouched.
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

    #: A texel of a shrunk tile is the object's own when its coverage
    #: survived the resize WHOLE; a partial one mixes in the gutter.
    _COVERAGE_OWN: float = 0.999

    def _finish_tile(self, img: Any, size: Tuple[int, int]) -> Any:
        """*img* as it ships in its cell: opaque RGB, at *size* ``(w, h)``.

        A tile from :meth:`bake_atlas` carries its island coverage as alpha
        (``keep_coverage``); that is what lets it be denoised HERE, at the
        cell's resolution, rather than where it was rendered. At the render's
        4x supersample the per-texel noise is too high to tell a shadow edge
        from grain, and the shrink averages most of it anyway -- what ships is
        the cell. The coverage is the island's GEOMETRY, not the bake step's
        dead-texel verdict: a noisy contact shadow has texels near zero, and a
        mask that dropped them would refill the shadow from the lit floor
        around it. The texels the shrink left partly covered are refilled
        from the denoised ones, so the island border matches its interior.

        A map without coverage (the two-call form's full-size maps, already
        denoised at their own size) comes back as its RGB, for the assembler
        to resize as it always has.
        """
        import cv2

        rgb = img[..., :3] if img.ndim == 3 else img
        if not (self.denoise and img.ndim == 3 and img.shape[2] == 4):
            return rgb
        shrunk = cv2.resize(img, tuple(size), interpolation=cv2.INTER_AREA)
        own = shrunk[..., 3] >= self._COVERAGE_OWN
        rgb = shrunk[..., :3]
        if not own.any():
            return rgb
        rgb = ptk.ImgUtils.denoise_image(rgb, mask=own)
        if not own.all():
            rgb = ptk.ImgUtils.dilate_image(rgb, mask=own)
        return rgb

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
    #: leak-through measured inside occluded corridors (ROOM_ENV walls).
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

    def _lightmap_uv_bbox(
        self, obj: str
    ) -> Optional[Tuple[float, float, float, float]]:
        """``(u0, v0, u1, v1)`` of *obj*'s lightmap-set islands, or ``None``.

        The bounds of the triangles the bake RENDERED
        (:meth:`_lightmap_uv_triangles`), so a crop can never disagree with the
        coverage mask or the layout the engine samples -- and the one read
        serves both. It used to be a read of its own, through a current-UV-set
        switch and ``polyEvaluate -boundingBox2d``, twice per atlas tile. A UV
        no face uses no longer widens it, which is also what the crop wants:
        nothing renders there. ``None`` -- no shape, no lightmap set, or any
        query failure -- means "don't crop"; the pack must never lose a bake
        to a diagnostic.
        """
        triangles = self._lightmap_uv_triangles(obj)
        if triangles is None:
            return None
        try:
            flat = triangles.reshape(-1, 2)
            (u0, v0), (u1, v1) = flat.min(axis=0), flat.max(axis=0)
            return (float(u0), float(v0), float(u1), float(v1))
        except Exception:
            return None

    def _lightmap_uv_triangles(self, obj: str):
        """*obj*'s lightmap-set UV triangles ``(N, 3, 2)``, or ``None``.

        The layout the bake RENDERED, so a coverage mask built from this
        cannot disagree with the image it masks. ``None`` -- no shape, no
        lightmap set, an empty layout, or any query failure -- means "no
        coverage evidence", and the refill falls back to alpha alone: a bake
        must never be lost to a diagnostic. Read once per bake
        (:meth:`_cached_reads`): the tile plan, the coverage mask and the crop
        all ask.
        """
        return self._cached("uv_layout", obj, self._read_lightmap_uv_triangles)

    @classmethod
    def _read_lightmap_uv_triangles(cls, obj: str):
        """:meth:`_lightmap_uv_triangles`, uncached."""
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
        cls,
        img: Any,
        bbox: Optional[Tuple[float, float, float, float]],
        cell: List[float],
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
        output_dir: str,
        name: str,
        used: set,
        avoid: "set" = frozenset(),
        claims: Optional[Dict[str, Any]] = None,
        owners: Any = (),
    ) -> str:
        """Atlas path for *name*, unique within one pack and clear of *avoid*.

        Re-running a bake should overwrite the same per-material atlas (the whole
        point of consolidation), so a name only the group's own *owners* read
        stays theirs; two groups resolving to the same name in a single pack
        (``used``), a name landing on another group's not-yet-consumed source
        map (*avoid*, a set of abspaths), or a file name anyone else reads
        (*claims* -- see :meth:`LightmapRecords.claims`) are disambiguated
        (``{name}_1`` ...). The rule is :meth:`ptk.FileUtils.unique_path`.
        """
        return ptk.FileUtils.unique_path(
            output_dir, name, ".exr", used, claims=claims, owners=owners, avoid=avoid
        )

    def _apply_intensity(self, paths, intensity: float) -> None:
        """Scale each unique lightmap file's texels by *intensity*, once.

        Files shared by several objects (an atlas) are deduped by abspath so
        they scale exactly once per call. :meth:`bake` calls it on the maps it
        has just written, which is what makes it once per map; so, until
        0.20.0, does :meth:`commit_lightmap`'s deprecated ``intensity``. A file
        that can't be read is left untouched and logged -- the record is worth
        more than the multiplier.
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

    # ------------------------------------------------------------------
    # The record -- LightmapRecords, reached through the baker
    # ------------------------------------------------------------------
    #
    # What a bake leaves in the scene is LightmapRecords'. The workflow verbs a
    # baker is asked for stay here as delegates. The dependency and manifest
    # calls moved there outright and warn here until 0.20.0: they never needed
    # a baker, and building one just to read markers is what six modules did.

    @ptk.Deprecation.parameter(
        "uv_rects",
        remove_in="0.20.0",
        since="2026-09-23",
        reason="Only a pre-0.17 atlas pack squeezed UVs into a rect, and "
        "LightmapRecords.migrate_legacy now restores those losslessly.",
    )
    @ptk.Deprecation.parameter(
        "intensity",
        remove_in="0.20.0",
        since="2026-09-23",
        reason="Pass intensity to bake(), which scales the maps it has just "
        "written exactly once; committing a map twice here scaled it twice.",
    )
    def commit_lightmap(
        self,
        mapping: Dict[str, str],
        intensity: float = 1.0,
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        uv_rects: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, str]:
        """Record maps baked elsewhere: :meth:`LightmapRecords.commit`.

        :meth:`bake` records its own maps; this is for a caller that ran
        :meth:`bake_separated` / :meth:`bake_atlas` itself. *mapping* is
        ``{object: lightmap path}`` and *scale_offsets* each object's atlas
        rect (see :meth:`pack_atlas`).

        Two parameters are deprecated (removed in 0.20.0) and keep their old
        behaviour until then. ``intensity`` other than 1.0 is scaled into the
        texels -- each unique file once per call, so committing a map again
        scales it again; :meth:`bake`'s ``intensity`` applies it where the map
        is written. ``uv_rects`` records a remap an old pack had already
        squeezed INTO the UVs (the marker's ``uvRect``).

        Returns:
            ``{object: lightmap path}`` for each object recorded.
        """
        recorded = LightmapRecords.commit(
            mapping, scale_offsets=scale_offsets, intensity=intensity
        )
        for obj, rect in (uv_rects or {}).items():
            if obj in recorded:
                LightmapRecords._stamp_uv_rect(obj, rect)
        # Scale texels only once a marker resolved: a commit that records
        # nothing must not mutate files on disk (the retry would re-apply the
        # multiplier on top).
        if recorded and float(intensity) != 1.0:
            self._apply_intensity(recorded.values(), intensity)
        return recorded

    def revert(self, objects: Optional[List[str]] = None) -> List[str]:
        """Take the lightmaps off *objects*, or off every baked object for ``None``.

        :meth:`LightmapRecords.revert`: the markers go and the manifest is
        republished, in one undo chunk. The materials were never changed and
        the EXR files stay on disk. Returns the nodes cleared.
        """
        return LightmapRecords.revert(objects)

    def revert_lightmap(self, objects: Optional[List[str]] = None) -> List[str]:
        """:meth:`revert`, under its original name."""
        return LightmapRecords.revert(objects)

    def baked_objects(self, objects: Optional[List[str]] = None) -> List[str]:
        """The objects :meth:`revert` would take the lightmap from.

        :meth:`LightmapRecords.baked_objects`: those of *objects* carrying a
        lightmap marker, or every marked transform for ``None``.
        """
        return LightmapRecords.baked_objects(objects)

    @ptk.Deprecation.symbol(
        "LightmapRecords.lightmap_dependencies", remove_in="0.20.0", since="2026-09-23"
    )
    def lightmap_dependencies(
        self,
        objects: Optional[List[str]] = None,
        search_dirs: Optional[List[str]] = None,
        walk: bool = True,
    ) -> List[Dict[str, Any]]:
        """Moved to :meth:`LightmapRecords.lightmap_dependencies`."""
        return LightmapRecords.lightmap_dependencies(objects, search_dirs, walk)

    @classmethod
    @ptk.Deprecation.symbol(
        "LightmapRecords.search_dirs", remove_in="0.20.0", since="2026-09-23"
    )
    def search_dirs(cls, objects: Optional[List[str]] = None) -> List[str]:
        """Moved to :meth:`LightmapRecords.search_dirs`."""
        return LightmapRecords.search_dirs(objects)

    @ptk.Deprecation.symbol(
        "LightmapRecords.heal_lightmap_paths", remove_in="0.20.0", since="2026-09-23"
    )
    def heal_lightmap_paths(
        self, objects: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Moved to :meth:`LightmapRecords.heal_lightmap_paths`."""
        return LightmapRecords.heal_lightmap_paths(objects)

    @ptk.Deprecation.symbol(
        "LightmapRecords.relocate_lightmaps", remove_in="0.20.0", since="2026-09-23"
    )
    def relocate_lightmaps(
        self,
        dest_dir: str,
        source_dir: str = "",
        mode: str = "copy",
        objects: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Moved to :meth:`LightmapRecords.relocate_lightmaps`."""
        return LightmapRecords.relocate_lightmaps(
            dest_dir, source_dir, mode, objects, dry_run
        )

    @ptk.Deprecation.symbol(
        "LightmapRecords.repath_lightmaps", remove_in="0.20.0", since="2026-09-23"
    )
    def repath_lightmaps(
        self,
        dirs_by_map: Dict[str, str],
        objects: Optional[List[str]] = None,
        relative: bool = True,
    ) -> int:
        """Moved to :meth:`LightmapRecords.repath_lightmaps`."""
        return LightmapRecords.repath_lightmaps(dirs_by_map, objects, relative)

    @ptk.Deprecation.symbol(
        "LightmapRecords.normalize_lightmap_paths",
        remove_in="0.20.0",
        since="2026-09-23",
    )
    def normalize_lightmap_paths(
        self, objects: Optional[List[str]] = None, relative: bool = True
    ) -> int:
        """Moved to :meth:`LightmapRecords.normalize_lightmap_paths`."""
        return LightmapRecords.normalize_lightmap_paths(objects, relative)

    @classmethod
    @ptk.Deprecation.symbol(
        "LightmapRecords.export_record", remove_in="0.20.0", since="2026-09-23"
    )
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """Moved to :meth:`LightmapRecords.export_record`."""
        return LightmapRecords.export_record(ctx)

    @classmethod
    @ptk.Deprecation.symbol(
        "LightmapRecords.refresh_export_metadata",
        remove_in="0.20.0",
        since="2026-09-23",
    )
    def refresh_export_metadata(cls) -> Optional[str]:
        """Moved to :meth:`LightmapRecords.refresh_export_metadata`."""
        return LightmapRecords.refresh_export_metadata()

    @contextlib.contextmanager
    def _muted_environment(self):
        """Hide the scene's environment lights for the bake when asked to.

        ``include_environment=False`` means "bake the room's own lights, not
        the world": the HDRI skydome is hidden for the duration and restored
        after, so the scene is handed back exactly as it was found. Hiding the
        TRANSFORM is Maya's own "switched off" for a light, and it is what
        :meth:`LightUtils.light_contributes` already reads, so the mute and the
        "is this scene lit" question cannot disagree.

        A dome whose visibility is locked or connected is left alone with a
        warning rather than failing the bake -- and is then reported by the
        unlit check like any other light that is on.
        """
        restore: Dict[str, Any] = {}
        if not self.include_environment and cmds is not None:
            for shape in LightUtils.environment_lights():
                for node in cmds.listRelatives(shape, parent=True, fullPath=True) or [
                    shape
                ]:
                    attr = f"{node}.visibility"
                    try:
                        was = cmds.getAttr(attr)
                        cmds.setAttr(attr, 0)
                    except Exception as e:
                        self.logger.warning(
                            "Could not mute environment light %s: %s", node, e
                        )
                        continue
                    restore[attr] = was
            if restore:
                self.logger.info(
                    "Include Environment is off: %d environment light(s) muted "
                    "for this bake.",
                    len(restore),
                )
        try:
            yield
        finally:
            for attr, was in restore.items():
                try:
                    cmds.setAttr(attr, was)
                except Exception as e:  # never leave the scene changed silently
                    self.logger.error("Could not restore %s: %s", attr, e)

    def _warn_if_unlit_scene(self) -> None:
        """Warn (once per instance) when the scene has no light source to bake.

        A lightless bake silently produces a black lightmap -- worth a loud
        hint. Emissive-material-only scenes still trip this; it is a warning,
        not a gate.
        """
        if self._warned_no_lights or cmds is None:
            return
        # Maya's AND Arnold's lights, from the one enumeration the panel's
        # refusal also reads (a local ``ls`` drifted: it lacked aiLightPortal).
        # An environment the bake is about to MUTE is not a light source for
        # it, so a room lit only by an HDRI still gets the warning when
        # Include Environment is off -- which is exactly when it is needed.
        lights = set(LightUtils.all_lights())
        if not self.include_environment:
            lights -= set(LightUtils.environment_lights())
        if lights:
            return
        self._warned_no_lights = True
        self.logger.warning(
            "No lights found in the scene -- the lightmap will bake black "
            "(unless emissive materials are the only light source)."
            if self.include_environment
            else "No lights found in the scene other than the environment, "
            "which Include Environment is set to leave out -- the lightmap "
            "will bake black (unless emissive materials light it)."
        )

    # Sanitize + write policy for every lightmap EXR this pipeline emits.
    # Irradiance is non-negative and the maps are consumed as half-precision
    # (Unity BC6H is half), so values are clamped to [0, 65504] -- a float32
    # firefly above half-max would otherwise become inf in the half encode.
    HALF_FLOAT_MAX: float = 65504.0

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
            bgr = np.nan_to_num(bgr, nan=0.0, posinf=cls.HALF_FLOAT_MAX, neginf=0.0)
        np.clip(bgr, 0.0, cls.HALF_FLOAT_MAX, out=bgr)
        # A destination that does not exist yet is not an error to discover
        # from cv2 ("can't write data: unknown exception"): the atlas path is
        # the CALLER's output dir, and ``bake_atlas`` stages its tiles in a
        # temp dir, so the first thing ever written there is this file.
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        # cv2 returns False (no exception) when EXR write support is missing:
        # callers delete per-object maps once this returns, so a silent failure
        # would destroy the source with no atlas on disk -- raise to enforce it.
        # Written beside the path and swapped in: a re-bake writes over its own
        # map, and a write that failed part way used to leave it truncated.
        stem, ext = os.path.splitext(os.path.basename(path))
        staged = os.path.join(parent or ".", f".{stem}.{os.getpid()}.part{ext}")
        try:
            ok = cv2.imwrite(
                staged, bgr, [cv2.IMWRITE_EXR_TYPE, cv2.IMWRITE_EXR_TYPE_HALF]
            )
            if not ok:
                raise RuntimeError(f"failed to write EXR: {path}")
            os.replace(staged, path)
        finally:
            if os.path.exists(staged):
                os.remove(staged)

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
        iterations: Optional[int] = None,
        uv_triangles: Optional[Any] = None,
        denoise: bool = False,
        keep_coverage: bool = False,
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
        and a partial-coverage alpha would be misread as transparency -- unless
        *keep_coverage*: an atlas tile keeps the island's coverage (alpha and
        UV layout, never the dead-texel verdict) as a 0/1 alpha for the pack
        (:meth:`_finish_tile`).

        *denoise* runs ``ImgUtils.denoise_image`` over the texels the bake
        owns, BEFORE the refill, so the gutters are grown from the denoised
        lighting rather than from the grain.

        Returns False (a no-op) when the image has no alpha channel.
        """
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2
        import numpy as np

        img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        if img is None:
            raise RuntimeError(f"unreadable EXR: {path}")
        if img.ndim != 3 or img.shape[2] < 4:
            return False  # no coverage channel -> nothing safe to dilate from
        if iterations is None:
            # A bounded gutter is enough for mip safety; a full flood (-1) is
            # opt-in. The ring is a width in TEXELS of the image in hand, so
            # it is sized from that image and nowhere else: an atlas bake
            # renders each object at its own footprint, and one figure taken
            # from the baker would over-dilate every small tile.
            # 512 -> 8, 1024 -> 16, 4096 -> 64.
            iterations = max(8, max(img.shape[:2]) // 64)

        alpha = img[..., 3]
        mask = alpha > alpha_threshold
        # A copy, so the map on disk is rewritten only at the end.
        bgr = np.array(img[..., :3], dtype=np.float32)
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
        # The island itself, before any value verdict: what a tile's coverage
        # alpha carries (a dark shadow is still the island's own texels).
        island = mask.copy()
        # Alpha alone is not sufficient: RTT can write alpha == 1.0 across
        # the WHOLE frame (measured: ROOM_ENV walls, mtoa 5.5), and a texel
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
        finished = cls._pad_texels(bgr, mask, iterations, denoise)
        # Opaque RGB, or a tile's coverage.
        if keep_coverage:
            finished = np.dstack([finished, island.astype(finished.dtype)])
        cls._write_lightmap_exr(path, finished)
        return True

    @staticmethod
    def _pad_texels(bgr: Any, mask: Any, iterations: int, denoise: bool) -> Any:
        """One image of a map through the denoise, the gutter ring and the fill."""
        if denoise and mask.any():
            bgr = ptk.ImgUtils.denoise_image(bgr, mask=mask)
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
        return bgr
