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
Its panel is tentacle's Lightmap Baker (``slots/tools/lightmap_baker.py``): one Slots
class over this engine and blendertk's.
"""

import contextlib
import math
import os
import shutil
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.mat_utils.texture_baker import TextureBaker
from mayatk.mat_utils.bake_sets import LightmapExcludeSet, LightmapIsolateSet
from mayatk.light_utils._light_utils import LightUtils
from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
from mayatk.light_utils.lightmap_baker._probe_placement import ProbePlacement
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.display_utils._display_utils import DisplayUtils
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
        retired: Map files the bake superseded, set aside when the scene is
            saved (the Recycle Bin, or the ``superseded`` temp store) -- what
            its objects read before, that nothing reads now
            (:meth:`LightmapRecords.superseding`).
        refused: Why nothing was baked, as a sentence for the artist, or
            ``None``.
        verdict: A warning about the finished maps' level (an unlit or a
            blown-out bake), as a sentence, or ``None``.
        probe: The reflection probe the bake captured of the room it lit
            (:meth:`LightmapBaker.bake_probe`), or ``None``.
        in_place: Objects written back into the cells they own in the atlas
            they read (:meth:`LightmapBaker._in_place_plan`): every other
            object reading it keeps every texel it had.
        repacked: ``{atlas file name: why}`` -- the atlases whose whole
            group re-baked into a fresh layout rather than in place, and why
            (a cell that no longer fits, a member new to it, one baked before
            cells were recorded).
        report: What :meth:`LightmapBaker.rebake_changed` found changed and
            re-baked (``ptk.StaleReport``), its ``in_place`` / ``repack`` as
            the bake did them; ``None`` for any other bake.
        cancelled: The artist cancelled it (``on_progress`` answered
            ``False``): what it finished is kept, nothing after it ran.
    """

    maps: Dict[str, str] = field(default_factory=dict)
    rects: Dict[str, List[float]] = field(default_factory=dict)
    excluded: List[str] = field(default_factory=list)
    hidden: List[str] = field(default_factory=list)
    unbaked: List[str] = field(default_factory=list)
    retired: List[str] = field(default_factory=list)
    refused: Optional[str] = None
    verdict: Optional[str] = None
    probe: Optional[str] = None
    in_place: List[str] = field(default_factory=list)
    repacked: Dict[str, str] = field(default_factory=dict)
    report: Optional["ptk.StaleReport"] = None
    cancelled: bool = False

    def __bool__(self) -> bool:
        return bool(self.maps)

    @property
    def files(self) -> List[str]:
        """The distinct map files, sorted: an atlas 40 objects share counts once."""
        return sorted(set(self.maps.values()))

    @property
    def folders(self) -> List[str]:
        """The distinct folders the maps go in, compared the way the disk does
        -- a map waiting in the session for the save, its home's."""
        spelled: Dict[str, str] = {}
        for path in self.maps.values():
            folder = os.path.dirname(LightmapRecords._home(path))
            spelled.setdefault(os.path.normcase(os.path.abspath(folder)), folder)
        return sorted(spelled.values())


@dataclass
class _BakeLights:
    """Which of the scene's light shapes one bake renders with.

    :meth:`LightmapBaker._bake_lights` answers it once for the bake, its
    reflection probe, the all-lights-off refusal and the light audit, so none
    of them can disagree about what lights a map. Mirror of blendertk's.

    Attributes:
        lights: Every light the bake renders with, on or off as authored: the
            scene's fixtures, or the Scope's with Include Scene Lighting off,
            and the environment's with Include Environment on.
        outside: The fixtures the Scope leaves out (Include Scene Lighting
            off), switched off for the bake.
        hidden: The *lights* switched off as authored, at a non-zero
            intensity, that Include Hidden Lights switches on (empty with it
            off).
    """

    lights: List[str] = field(default_factory=list)
    outside: List[str] = field(default_factory=list)
    hidden: List[str] = field(default_factory=list)


@dataclass
class _InPlacePlan:
    """Which of a bake's targets write back into their own atlas cells.

    :meth:`LightmapBaker._in_place_plan` decides it once per bake: the one
    decision point between writing an atlas in place and re-packing its whole
    group. Mirror of blendertk's.

    Attributes:
        in_place: ``{atlas path: [targets]}`` -- written into their cells.
        readers: ``{atlas path: [objects]}`` -- every object reading each of
            those atlases, the targets among them.
        entries: ``{object: baseline entry}`` for every reader -- its cell,
            extent and size (``ptk.BakeBaseline``).
        rects: ``{object: [sx, sy, ox, oy]}`` -- each reader's published
            binding (its marker's ``scaleOffset``): where a held cell's seam
            is read.
        repack: ``{atlas file name: why}`` -- the atlases whose whole group
            re-bakes instead, and why.
        expand: The members those groups add to the bake.
        forks: ``{atlas path: the scene file that wrote it}`` -- in-place
            atlases another scene file wrote (a Save As copy's source): their
            cells are written into this scene's own copy of the atlas.
    """

    in_place: Dict[str, List[str]] = field(default_factory=dict)
    readers: Dict[str, List[str]] = field(default_factory=dict)
    entries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    rects: Dict[str, List[float]] = field(default_factory=dict)
    repack: Dict[str, str] = field(default_factory=dict)
    expand: List[str] = field(default_factory=list)
    forks: Dict[str, str] = field(default_factory=dict)

    @property
    def placed(self) -> List[str]:
        """Every target written in place."""
        return [obj for objs in self.in_place.values() for obj in objs]


@dataclass
class _AtlasFiles:
    """The atlases a scene's objects read, each owned by an atlas group --
    what :meth:`LightmapBaker._atlas_files` reads once for the in-place plan
    and the split check.

    Attributes:
        markers: ``{reader: its marker record}``.
        entries: ``{object: baseline entry}`` of every object still recorded.
        by_file: ``{normcased path: [readers]}``, readers sorted.
        spelled: ``{normcased path: the path as read}``.
        lone: The one-reader maps an atlas bake wrote (a group of one), and
            those no record speaks for beside an atlas their group owns.
        group: ``{object: atlas group}`` (:meth:`LightmapBaker._atlas_keys`)
            for every reader and every object asked about.
        owned: ``{atlas group: {normcased paths}}`` -- each atlas belongs to
            the group most of its readers are in.
    """

    markers: Dict[str, Dict[str, Any]]
    entries: Dict[str, Dict[str, Any]]
    by_file: Dict[str, List[str]]
    spelled: Dict[str, str]
    lone: set
    group: Dict[str, str]
    owned: Dict[str, set]


@dataclass
class _AtlasBake:
    """What :meth:`LightmapBaker._bake_atlas` did -- what the public
    :meth:`~LightmapBaker.bake_atlas` returns of it is :attr:`packed`.

    Attributes:
        packed: ``{object: (map path, rect)}``.
        cells: ``{object: {"cell", "extent", "size", "packing"}}`` -- where
            in its map each object was written, for the bake baseline
            (:meth:`LightmapRecords.commit`'s ``cells``).
        in_place: Objects written back into their own cells.
        repacked: ``{atlas file name: why}`` -- groups re-packed whole.
    """

    packed: Dict[str, Tuple[str, List[float]]] = field(default_factory=dict)
    cells: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    in_place: List[str] = field(default_factory=list)
    repacked: Dict[str, str] = field(default_factory=dict)


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
    #: The kind folder lightmaps go to under the scene project's texture
    #: folder when no output directory is given -- by the API and by the
    #: panel's empty field alike, in both DCCs (``TextureBaker.default_output_dir``).
    OUTPUT_SUBDIR: str = "lightmaps"
    LIGHTMAP_INFO_ATTR: str = LightmapRecords.LIGHTMAP_INFO_ATTR
    #: The lightmaps record's key under its old name, retired here: read the
    #: declaration's (``ptk.SceneRecords.LIGHTMAPS.key``).
    LIGHTMAP_METADATA = ptk.Deprecation.class_attribute(
        ptk.SceneRecords.LIGHTMAPS.key,
        "ptk.SceneRecords.LIGHTMAPS.key",
        remove_in="0.29.0",
        since="2026-10-24",
    )
    #: The record's schema version -- the registry's
    #: (``ptk.SceneRecords.LIGHTMAPS.version``), retired here.
    LIGHTMAP_METADATA_VERSION = ptk.Deprecation.class_attribute(
        ptk.SceneRecords.LIGHTMAPS.version,
        "ptk.SceneRecords.LIGHTMAPS.version",
        remove_in="0.29.0",
        since="2026-10-24",
    )
    FOUND_BY_HINT: str = LightmapRecords.FOUND_BY_HINT
    FOUND_BY_SEARCH: str = LightmapRecords.FOUND_BY_SEARCH

    #: Whether the bake under way records its maps (:meth:`bake`, a
    #: recording baker): its maps then wait in the session until the scene is
    #: saved (``LightmapRecords._stage``). Off for a mechanism called alone
    #: (:meth:`bake_separated`, :meth:`bake_atlas`, :meth:`bake_probe`), which
    #: writes into the folder it is given -- the Blender bridge's bake, a
    #: script that places its maps itself. A cell written in place, Combine
    #: and a transfer record as they write, and always stage. blendertk's twin.
    _staging: bool = False

    @contextlib.contextmanager
    def _staged(self, on: bool = True):
        """:attr:`_staging` set to *on* for the block (re-entrant)."""
        was, self._staging = self._staging, bool(on) or self._staging
        try:
            yield
        finally:
            self._staging = was

    def __init__(
        self,
        resolution: int = 1024,
        samples: int = 4,
        baker: Optional[TextureBaker] = None,
        gi_depth: int = 4,
        gi_samples: int = 2,
        device: Optional[str] = None,
        include_environment: bool = True,
        denoise: bool = True,
        adaptive: Optional[bool] = None,
        beside_textures: bool = False,
        reflection_probe: bool = True,
        include_scene_lights: bool = True,
        include_hidden_lights: bool = False,
        refuse_unlit: bool = True,
        record: bool = True,
    ):
        super().__init__()
        # Whether a bake records itself in the scene (:meth:`bake` steps 5
        # and 7): off for a measurement -- a light layer, a portability check
        # -- whose maps and probe land in the folder it is given: no marker
        # re-pointed, no record written, no map the scene reads set aside as
        # superseded, no atlas cell written in place. (A legacy marker is
        # still migrated on the way in: lossless, as every bake does.)
        # A layer bake that recorded itself set a scene's real lightmaps
        # aside (2026-10-17).
        self.record = bool(record)
        # Whether a bake whose lights are all off is refused as a mistake
        # (:meth:`preflight`). Off when that is the point: a bake meant to be
        # lit by emission alone (an emission light layer, LightLayerBaker).
        self.refuse_unlit = bool(refuse_unlit)
        self.resolution = resolution
        self.samples = samples
        # Which of the scene's lights light a bake (see :meth:`_bake_lights`).
        # Every one, wherever it sits, by default: a room's lights are rarely
        # parented inside what is baked. Off, only the lights in the objects
        # handed to the bake or under them -- the rest are switched off for
        # the bake and handed back as found. The environment answers to
        # ``include_environment`` alone, master or not.
        self.include_scene_lights = bool(include_scene_lights)
        # Switch the hidden lights of that set ON for the bake, and back off
        # after -- a light under a hidden group too, without showing what else
        # the group hides (see :meth:`_switched_on`). Off by default: a hidden
        # light is usually hidden on purpose.
        self.include_hidden_lights = bool(include_hidden_lights)
        # Capture the room each bake lit as a reflection probe too (see
        # :meth:`bake_probe`): what a lightmap cannot hold -- the reflections
        # that are a baked metal's whole look, and the light on everything
        # the bake left out. Its size follows the tier (half the resolution,
        # within :attr:`PROBE_WIDTH_RANGE`).
        self.reflection_probe = bool(reflection_probe)
        # Save each finished map in the folder its material's texture maps
        # live in, named after that texture set, instead of all of them in one
        # output folder (see :meth:`_texture_homes`). A material without file
        # textures has no such folder, so its map falls back to the bake's
        # ``output_dir``.
        self.beside_textures = bool(beside_textures)
        # While a re-bake updates a bake (:meth:`_keeping_folders`), each map
        # it rewrites goes back to the folder it is in -- over the output
        # folder and the texture folders alike (see :meth:`_homes`).
        self._keep_folders = False
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
            # None -> AUTO, as adaptive's None -> True: the default for a baker
            # made here, while an injected one keeps its own (below).
            device="AUTO" if device is None else device,
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
        # The lights the bake in progress renders with (see :meth:`_lighting`);
        # ``None`` outside a bake. The last bake's stay readable for the light
        # audit a verdict on its maps carries.
        self._lit: Optional[_BakeLights] = None
        self._last_lit: Optional[_BakeLights] = None
        # The recorded lighting rule a re-bake lights by (see :meth:`_lit_as`);
        # ``None``: the switches above and the Scope a bake is handed.
        self._rule: Optional[Dict[str, Any]] = None

    @property
    def device(self) -> Optional[str]:
        """Which device Arnold bakes on -- ``"AUTO"`` (the default: the GPU
        wherever Arnold has one), ``"GPU"``, ``"CPU"``, or ``"SCENE"`` for the
        scene's own setting (the constructor's ``None`` means AUTO, and leaves an
        injected baker's own choice alone). ``AUTO`` is the default because the
        scene's own was the CPU on the production office (saved that way), where
        a scripted bake took 48 minutes for 9 of its 49 objects; the panel always
        defaulted to ``AUTO``. Lives on the baker primitive
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
        "include_scene_lights",
        "include_environment",
        "include_hidden_lights",
        "denoise",
        "beside_textures",
        "reflection_probe",
    )
    #: The Samples range a panel offers, in this renderer's units: Arnold
    #: camera (AA) samples, squared, so a few hundred is already extreme.
    #: blendertk's counts Cycles paths, in the thousands.
    SAMPLES_RANGE: Tuple[int, int] = (1, 256)
    #: The processors (:attr:`device` values) that sample adaptively: Arnold's
    #: GPU only -- a CPU bake ignores :attr:`adaptive`. Cycles adapts on every
    #: one (blendertk's).
    ADAPTIVE_DEVICES: Tuple[str, ...] = ("AUTO", "GPU")
    #: Retired built-in tier names -> the current one, warning until they go.
    #: ``"quest"`` (until 2026-09-23) named one headset for a tier that serves
    #: every mobile / standalone-VR target; a script or the panel's preset
    #: pointer may still say it. :meth:`from_preset` resolves only a name the
    #: store lacks, so a user preset saved under a retired name still wins.
    #: Re-dated from 0.21.0 on 2026-10-24: a saved preset pointer is saved
    #: data, which gets the longest window (CODE_STANDARD section 0).
    _resolve_retired_preset = staticmethod(
        ptk.Deprecation.values(
            {"quest": "mobile"},
            what="LightmapBaker preset",
            remove_in="0.30.0",
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
    def preset_name(cls, name: str) -> str:
        """*name* as the preset store knows it now: itself when the store has
        it -- a user preset saved under a retired tier name still wins -- else
        the tier a retired name became (with a notice), else unchanged. What
        :meth:`from_preset` loads, and the panel's Preset pointer follows.
        Mirror of blendertk's."""
        if cls.preset_store().exists(name):
            return name
        return cls._resolve_retired_preset(name)

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

        Built-ins (resolution / samples; the JSON in ``presets/`` is the
        source): ``preview`` (256/2), ``mobile`` (1024/4, the default tier),
        ``desktop`` (2048/4) and ``hero`` (4096/6). A retired tier name
        (``quest``) still resolves, with a notice.
        """
        store = cls.preset_store()
        name = cls.preset_name(name)
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
        bakeable mesh -- minus the scene's :class:`LightmapExcludeSet`, a group
        or locator counting the meshes below it. An excluded mesh is only left without a
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
        Blender bridge's lightmap leg keys on what the FBX carries instead
        (``BlenderBridge._bakeable``: the visibility flag alone -- a mesh a
        display layer hides still ships, and Cycles bakes it).
        """
        return cls._targets(objects)[0]

    @classmethod
    def _targets(
        cls, objects: Optional[List[str]] = None
    ) -> Tuple[List[str], List[str], List[str]]:
        """``(targets, hidden, excluded)`` for *objects*: the one scoping every
        bake entry runs -- :meth:`TextureBaker.resolve_meshes` (sets, groups,
        locators), then :meth:`_partition`. :meth:`bake_targets` is its first
        list; :meth:`bake` keeps all three for its report."""
        meshes = TextureBaker.resolve_meshes(objects, descendants=True)
        if not meshes or cmds is None:
            return meshes, [], []
        return cls._partition(meshes)

    @classmethod
    def _partition(cls, meshes: List[str]) -> Tuple[List[str], List[str], List[str]]:
        """``(targets, hidden, excluded)``: *meshes* as :meth:`bake_targets` sorts them.

        Kept apart because only one of the two is the Exclude set's doing:
        counted together, a Scene bake of the production room reported its
        two hidden props as excluded. A mesh the set names counts as excluded
        whether or not it renders; each group left out is logged by name.
        """

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
           upgraded, and a refusal when the bake has lights and none of them
           can light it.
        3. The bake: :meth:`bake_atlas` (``packing="atlas"``, one shared map
           per material) or :meth:`bake_separated` (``"per_object"``). An
           atlas bake of part of a material group writes those objects back
           into the cells they own in the atlas the rest of the group reads
           (:meth:`_in_place_plan`, ``in_place``), every other cell kept
           texel for texel; a target that no longer fits its cell, or is new
           to the group, re-bakes the whole group into a fresh layout
           instead (``repacked``, logged with why). Both
           migrate the targets' legacy markers first
           (:meth:`LightmapRecords.migrate_legacy`), and both bake each
           :class:`LightmapIsolateSet` member in a pass of its own, out of
           every other pass's render (:meth:`_isolation_passes`). The scene's
           lights are set for it as :meth:`_bake_lights` says -- every one, or
           the Scope's (:attr:`include_scene_lights`), the environment by its
           own switch, the hidden ones switched on
           (:attr:`include_hidden_lights`); inside :meth:`_lit_as`, as a
           recorded rule says -- and handed back after.
        4. *intensity*, when not 1.0, scaled into the maps this bake just
           wrote -- once, so re-recording them can never apply it twice; a
           cell written in place is scaled as its tile, never the atlas
           around it.
        5. :meth:`LightmapRecords.commit` records each map with its rect, and
           in the bake baseline its cell, the lighting it rendered with and
           the rule that chose those lights (:meth:`_lighting_state`) -- what
           :meth:`rebake_changed` diffs against and re-lights by, and the
           lights the manifest ships for highlights. Every map waits in the
           session until the scene is saved (``LightmapRecords._stage``): an
           unsaved bake changes no file the saved scene reads. The maps the
           baked objects read before -- when this scene wrote them and
           nothing reads them now -- are set aside at that save
           (:meth:`LightmapRecords.superseding`): a bake after an output
           option changed leaves no old maps behind.
        6. :meth:`bake_verdict` reads the finished maps' level.
        7. :meth:`bake_probe` captures the room as a reflection probe
           (:attr:`reflection_probe`) -- not after a cancel, and a probe that
           fails is logged, never raised: the bake is committed by then.

        Nothing is reverted first. An object the bake does not finish (a
        cancel, a failed render) keeps the map it had, and that map is intact:
        a bake never changes the texels another object reads. It replaces a
        file only the objects it rewrote read (:meth:`LightmapRecords.claims`),
        or writes the cells those objects own in an atlas they share -- and
        the only files it sets aside are ones no object reads any more.

        Parameters:
            objects: Mesh transforms, their shapes or components; ``None``
                for the selection. A group, a locator or any other node
                that is not a mesh bakes the meshes under it
                (:meth:`TextureBaker.resolve_meshes` ``descendants``). A light
                among them, or under them, is the Scope's own: what lights
                the bake with :attr:`include_scene_lights` off.
            packing: ``"atlas"`` or ``"per_object"``.
            output_dir: Where the maps go (see :meth:`bake_separated`).
            prefix / suffix: Name affix around each map's texture-set stem.
            on_progress: ``(done, total, name) -> bool`` per object; return
                ``False`` to cancel the rest (and the probe).
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
        # Read once: the Scope's lights are found in what the bake was handed,
        # and the bake itself selects and deselects along the way.
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        # The Exclude set, and what Arnold renders nothing of, come off BEFORE
        # anything else touches the scene.
        targets, result.hidden, result.excluded = self._targets(objects)
        if not (targets or result.hidden or result.excluded):
            result.refused = "Nothing to bake: no mesh among the given objects."
            return result
        if not targets:
            result.refused = self._nothing_to_bake(result.hidden, result.excluded)
            return result
        result.refused = self.preflight(objects)
        if result.refused:
            return result

        # A cancel ends the bake, not just its renders: the maps it finished
        # are committed, but no reflection probe follows -- one more render,
        # and one the artist cannot stop.
        cancelled = False

        def tick(done: int, total: int, name: str) -> bool:
            nonlocal cancelled
            keep = on_progress(done, total, name)
            cancelled = cancelled or keep is False
            return keep

        # Atlas packing is chosen BEFORE baking, not after: bake_atlas plans
        # the layout up front so each object bakes at a bounded multiple of the
        # size it will occupy in the atlas, instead of rendering a full map per
        # object and downscaling most of it away (a 50-object room: 6.7 hours
        # full-size on the CPU).
        common = dict(
            output_dir=output_dir,
            prefix=prefix,
            suffix=suffix,
            on_progress=tick if on_progress is not None else None,
            **kwargs,
        )
        # The lighting the maps are about to see, as the baseline records it
        # (:meth:`_lighting_state`): read as authored, before the bake
        # switches anything, and after preflight upgraded the tool's lights.
        try:
            lighting = self._lighting_state(objects)
        except Exception as e:  # the baseline is never what loses a bake
            self.logger.warning("Bake lighting not recorded: %s", e)
            lighting = None
        # Lit for what the bake was HANDED: the mechanisms below see meshes
        # alone, and keep this (re-entrant) rather than re-reading the Scope.
        with self._lighting(objects), self._staged(self.record):
            if packing == "atlas":
                atlas = self._bake_atlas(targets, intensity=intensity, **common)
                packed, cells = atlas.packed, atlas.cells
                result.in_place, result.repacked = atlas.in_place, atlas.repacked
            else:
                packed = {
                    o: (path, None)
                    for o, path in self.bake_separated(targets, **common).items()
                }
                cells = {
                    o: {"packing": "per_object", "size": int(self.resolution)}
                    for o in packed
                }
        result.maps = {o: path for o, (path, _rect) in packed.items()}
        result.rects = {
            o: [float(v) for v in (rect or self._IDENTITY_SCALE_OFFSET)]
            for o, (_path, rect) in packed.items()
        }
        result.unbaked = [o for o in targets if o not in result.maps]
        result.cancelled = cancelled
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
        # An atlas bake scaled its own maps -- a cell written in place, into
        # the tile alone (:meth:`_bake_atlas`).
        if float(intensity) != 1.0 and packing != "atlas":
            self._apply_intensity(result.maps.values(), intensity)
        if self.record:
            with LightmapRecords.superseding(result.maps) as retired:
                LightmapRecords.commit(
                    result.maps,
                    scale_offsets=result.rects,
                    intensity=intensity,
                    cells={o: cells[o] for o in result.maps if o in cells},
                    lighting=lighting,
                )
            result.retired = retired
        result.verdict = self.bake_verdict(result.maps.values())
        if self.reflection_probe and not cancelled:
            # After the commit, so it must never be what loses the bake: a
            # render that fails, or a probe file held open, costs the probe.
            # Lit as the maps were: a probe of other lights is another room.
            try:
                with self._lighting(objects), self._staged(self.record):
                    result.probe = self.bake_probe(result.maps)
            except Exception as e:
                self.logger.warning("Reflection probe not captured: %s", e)
        return result

    # ------------------------------------------------------------------
    # Re-bake Changed -- what changed since the last bake, and only that
    # ------------------------------------------------------------------

    def stale_report(
        self,
        objects: Optional[List[str]] = None,
        reach: Optional[float] = None,
        output_dir: Optional[str] = None,
        relocate: bool = False,
    ) -> "ptk.StaleReport":
        """What a re-bake of the changed objects would redo, and why -- the dry run.

        The scene (:meth:`LightmapRecords.scene_state`) read against what each
        map was baked against (:meth:`LightmapRecords.bake_state`) by
        ``ptk.BakeBaseline.assess``, the stale rule both DCCs share. Stale: an
        object whose own state changed (moved, deformed, remodelled, its
        lightmap UVs re-laid, its material regrouped, shown or hidden), whose
        map is missing, or whose lighting changed (:meth:`_lighting_state`:
        the lights its bake rendered with, the environment when it was
        included, and every setting that changes the light) -- and every
        static object a change reaches, a neighbour. Each map's lighting is
        read again by the rule it was baked by (:meth:`_lit_as`: its switches
        and, Scope-lit, its lights by name) whatever this baker's switches
        say, so a light renamed, deleted, changed, or added where the rule
        reaches is a lighting change for what it lit. The report's packing
        plan (:meth:`_planned`) says which targets would be written back into
        their atlas cells -- a Save As copy's into its own copy of its
        source's atlas (``forked``) -- which atlases re-pack whole and what
        they bring in (``joining``); ``out_of_bake`` names the visible meshes
        no bake renders (:meth:`_out_of_bake`). Nothing is rendered or
        written.

        Not seen: an emissive material's strength and an albedo edit change
        the bounce, not any state the baseline records (:meth:`rebake` redoes
        given objects for those).

        Parameters:
            objects: Report only these objects and what lies under them;
                ``None`` reports the scene. Causes come from the whole scene
                either way. A lighting recorded before rules were is read by
                this baker's switches over them (every baked object for
                ``None``), and reads as changed once.
            reach: A multiplier on how far a change reaches its neighbours
                (``ptk.BakeBaseline.STALE_REACH``); 0 names none. ``None``: 1.
            output_dir: The folder the re-bake would write to, as
                :meth:`rebake_changed` is handed it; ``None``: the bake's
                default.
            relocate: As :meth:`rebake_changed`.

        Returns:
            ``ptk.StaleReport`` -- ``summary()`` for a footer, ``lines()`` for
            a log, ``doc()`` for a viewer.
        """
        with self._keeping_folders(not relocate):
            return self._stale_report(objects, reach, output_dir=output_dir)

    def _stale_report(
        self,
        objects: Optional[List[str]] = None,
        reach: Optional[float] = None,
        lighting: bool = True,
        intensity: Optional[float] = None,
        output_dir: Optional[str] = None,
    ) -> "ptk.StaleReport":
        """:meth:`stale_report`; *lighting* ``False`` leaves the lights out,
        *intensity* is the one the re-bake applies (``None``: not asked)."""
        if cmds is None:
            return ptk.StaleReport()
        ids = None
        if objects is not None:
            meshes = TextureBaker.resolve_meshes(list(objects), descendants=True)
            ids = (cmds.ls(meshes, long=True) or []) if meshes else []
        scope = (
            list(objects) if objects is not None else LightmapRecords.baked_objects()
        )

        def lit(rule):
            # The lighting now, read by a recorded rule as a re-bake lights by
            # it; ``None`` (recorded without one): this baker's over *scope*.
            with self._lit_as(rule):
                return self._lighting_state(scope)

        current = LightmapRecords.scene_state()
        report = ptk.BakeBaseline.assess(
            LightmapRecords.bake_state(),
            current,
            lighting=lit if lighting else None,
            missing=LightmapRecords._missing_maps(),
            reach=1.0 if reach is None else float(reach),
            objects=ids,
        )
        report = replace(
            report, out_of_bake=tuple(self._out_of_bake(ids)), **self._split_fields()
        )
        return self._planned(report, intensity, output_dir)

    def _planned(
        self,
        report: "ptk.StaleReport",
        intensity: Optional[float] = None,
        output_dir: Optional[str] = None,
    ) -> "ptk.StaleReport":
        """*report* with the packing plan its targets would bake by
        (:meth:`_in_place_plan`, into the folders :meth:`_bake_folders`
        names): ``in_place``, ``repack``, ``joining`` and ``forked``.
        Read-only."""
        if not report.targets:
            return report
        targets = list(report.targets)
        with self._cached_reads():
            try:
                folders = self._bake_folders(targets, output_dir)
            except ValueError:  # no project: the bake refuses before it plans
                folders = None
            placing = self._in_place_plan(targets, intensity=intensity, folders=folders)
        return replace(
            report,
            in_place=tuple(sorted(placing.placed)),
            repack=dict(placing.repack),
            joining=tuple(sorted(placing.expand)),
            forked={os.path.basename(a): w for a, w in placing.forks.items()},
        )

    def _out_of_bake(self, ids: Optional[List[str]] = None) -> List[str]:
        """The visible meshes no bake renders, sorted: neither baked nor in
        the Exclude set -- what :meth:`_out_of_render` switches off. Under
        *ids* (long names) when given."""
        static = (
            cmds.ls(
                LightmapRecords.baked_objects() + LightmapExcludeSet.meshes(),
                long=True,
            )
            or []
        )
        unbaked, _shared = self._unbaked_visible(static)
        if ids is not None:
            wanted = set(ids)
            unbaked = [n for n in unbaked if n in wanted]
        return sorted(unbaked)

    def rebake_changed(
        self,
        packing: Optional[str] = None,
        reach: Optional[float] = None,
        lighting: bool = True,
        relocate: bool = False,
        **bake_kwargs,
    ) -> LightmapBakeResult:
        """Re-bake what changed since the last bake, and what that change shades.

        :meth:`stale_report`'s targets through :meth:`bake`, so every check,
        the record and the verdict are the bake's own. Each target re-bakes
        the way it was RECORDED as baked (:meth:`_rebake`): packed so
        (*packing* forces one way for all), lit so -- by the rule its lighting
        recorded (:meth:`_lit_as`), whatever this baker's lighting switches
        say, so the next dry run reads what it rendered -- and written back to
        the folder its map is in (:meth:`_keeping_folders`). An atlas target
        is written back into the cell it owns where it still fits
        (:meth:`_in_place_plan`), so each material keeps ONE atlas and every
        other cell keeps its texels; a Save As copy writes its source's atlas
        into a copy of its own (``forked``), the source's untouched. Each way
        of baking is one :meth:`bake` call, the reflection probe captured
        after the last. A lighting change stales everything lit by it, said
        before anything renders.

        Parameters:
            packing: ``"atlas"`` or ``"per_object"`` for every target;
                ``None`` re-bakes each the way it was baked (an atlas when no
                record says).
            reach: As :meth:`stale_report`.
            lighting: Whether a lighting change counts. ``False`` re-bakes
                only what changed in the scene itself.
            relocate: Write every map it rewrites to ``output_dir`` (the
                bake's default when not given) instead of the folder it is
                in -- an atlas elsewhere then re-packs whole into it.
            bake_kwargs: Forwarded to :meth:`bake` (``output_dir``,
                ``prefix``, ``suffix``, ``on_progress``, ``intensity``, ...);
                ``output_dir`` takes only a map with no file yet unless
                *relocate*.

        Returns:
            :class:`LightmapBakeResult` -- its ``report`` the dry run it acted
            on, ``in_place`` / ``repack`` as done. With nothing changed:
            no maps, and ``refused`` says so.

        Raises:
            ValueError: *packing* is not ``None``, ``"atlas"`` or
                ``"per_object"``.
        """
        self._check_rebake_packing(packing)
        with self._keeping_folders(not relocate):
            report = self._stale_report(
                reach=reach,
                lighting=lighting,
                intensity=float(bake_kwargs.get("intensity", 1.0)),
                output_dir=bake_kwargs.get("output_dir"),
            )
            return self._rebake(report, packing, "Re-bake Changed", bake_kwargs)

    def rebake_report(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        relocate: bool = False,
        intensity: Optional[float] = None,
    ) -> "ptk.StaleReport":
        """What :meth:`rebake` of *objects* would redo -- its dry run.

        The baked meshes among *objects* (default: the selection; a group
        stands for the meshes under it), with the packing plan they would
        bake by (:meth:`_planned`). Nothing is rendered or written.
        """
        if cmds is None:
            return ptk.StaleReport()
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        meshes = (
            TextureBaker.resolve_meshes(list(objects), descendants=True)
            if objects
            else []
        )
        baked = LightmapRecords.baked_objects(
            (cmds.ls(meshes, long=True) or []) if meshes else []
        )
        with self._keeping_folders(not relocate):
            return self._planned(
                ptk.StaleReport(targets=tuple(sorted(set(baked)))),
                intensity,
                output_dir,
            )

    def rebake(
        self,
        objects: Optional[List[str]] = None,
        packing: Optional[str] = None,
        relocate: bool = False,
        **bake_kwargs,
    ) -> LightmapBakeResult:
        """Re-bake *objects* the way each was baked, changed or not.

        What the bake baseline cannot see -- an albedo or emissive edit, a
        noisy render -- redone for exactly these objects, as
        :meth:`rebake_changed` redoes what it can see: each packed, lit and
        placed as recorded (:meth:`_rebake`), an atlas member written back
        into its own cell where it fits. Objects with no lightmap yet are
        left out: :meth:`bake` bakes them.

        Parameters:
            objects: Default: the selection (:meth:`rebake_report`).
            packing / relocate / bake_kwargs: As :meth:`rebake_changed`.

        Returns:
            :class:`LightmapBakeResult`; ``refused`` when none of *objects*
            has a lightmap.

        Raises:
            ValueError: *packing* is not ``None``, ``"atlas"`` or
                ``"per_object"``.
        """
        self._check_rebake_packing(packing)
        with self._keeping_folders(not relocate):
            report = self.rebake_report(
                objects,
                output_dir=bake_kwargs.get("output_dir"),
                relocate=relocate,
                intensity=float(bake_kwargs.get("intensity", 1.0)),
            )
            if not report.targets:
                return LightmapBakeResult(
                    report=report,
                    refused="None of these objects is baked yet; Bake them first.",
                )
            return self._rebake(report, packing, "Re-bake", bake_kwargs)

    @staticmethod
    def _check_rebake_packing(packing: Optional[str]) -> None:
        if packing not in (None, "atlas", "per_object"):
            raise ValueError(
                f"packing must be None, 'atlas' or 'per_object', got {packing!r}"
            )

    def _rebake(
        self,
        report: "ptk.StaleReport",
        packing: Optional[str],
        label: str,
        bake_kwargs: Dict[str, Any],
    ) -> LightmapBakeResult:
        """Bake *report*'s targets the way each was RECORDED as baked.

        One :meth:`bake` per way of baking -- packed (atlases first; *packing*
        forces one way) and lit (the recorded rule, :meth:`_lit_as`; ``None``:
        none recorded) -- the reflection probe after the last. The shared body
        of :meth:`rebake_changed` and :meth:`rebake`; the caller holds the
        folders (:meth:`_keeping_folders`).
        """
        result = LightmapBakeResult(report=report)
        if not report.targets:
            result.refused = report.summary()
            return result
        self.logger.info("%s: %s", label, report.summary())
        for line in report.lines():
            self.logger.info("  %s", line)
        recorded = LightmapRecords.bake_state()
        rules = ptk.BakeBaseline.lighting_rules(recorded)
        passes: List[Tuple[str, Optional[Dict[str, Any]], List[str]]] = []
        for target in report.targets:
            chosen = packing or (recorded["objects"].get(target) or {}).get("packing")
            chosen = chosen if chosen in ("atlas", "per_object") else "atlas"
            rule = rules.get(target)
            same = next((p for p in passes if p[:2] == (chosen, rule)), None)
            if same is None:
                passes.append((chosen, rule, [target]))
            else:
                same[2].append(target)
        passes.sort(key=lambda p: p[0] != "atlas")

        bake_kwargs = dict(bake_kwargs)
        on_progress = bake_kwargs.pop("on_progress", None)
        cancelled = False

        def tick(done: int, total: int, name: str) -> bool:
            nonlocal cancelled
            keep = on_progress(done, total, name)
            cancelled = cancelled or keep is False
            return keep

        probe = self.reflection_probe
        try:
            for i, (chosen, rule, targets) in enumerate(passes):
                # One probe, of the room as the LAST bake leaves it.
                self.reflection_probe = probe and i == len(passes) - 1
                with self._lit_as(rule):
                    part = self.bake(
                        targets,
                        packing=chosen,
                        on_progress=tick if on_progress is not None else None,
                        **bake_kwargs,
                    )
                self._merge_result(result, part)
                if cancelled or (part.refused and not part.maps):
                    break
        finally:
            self.reflection_probe = probe
        if result.maps:
            result.refused = None
        result.report = replace(
            report, in_place=tuple(result.in_place), repack=dict(result.repacked)
        )
        return result

    def map_folders(self, objects: Optional[List[str]] = None) -> Dict[str, List[str]]:
        """``{folder: [map file names]}`` -- where the maps *objects* read are
        on disk (default: every baked object). A missing map is in none. What
        a panel weighs an Output Directory against before a re-bake
        (``relocate``)."""
        wanted = None if objects is None else set(objects)
        out: Dict[str, List[str]] = {}
        for transform, name, path in LightmapRecords._reads():
            if path and (wanted is None or transform in wanted):
                names = out.setdefault(os.path.normpath(os.path.dirname(path)), [])
                if name not in names:
                    names.append(name)
        return out

    def bake_breakdown(
        self, output_dir: Optional[str] = None, relocate: bool = False
    ) -> "ptk.ReportDoc":
        """The scene's bake state, map by map -- the Bake State viewer.

        Every lightmap the scene reads and its reflection probe: the file and
        its folder, who reads it, its size, which scene file wrote it, and
        whether it is up to date, missing, or re-bakes (and how) -- with what
        :meth:`stale_report` would redo and why, and the visible meshes out of
        the bake (``ptk.StaleReport.doc``). Read-only.

        Parameters:
            output_dir / relocate: As :meth:`stale_report`.
        """
        report = self.stale_report(output_dir=output_dir, relocate=relocate)
        return report.doc(self._map_records())

    def bake_maps(
        self,
        report: Optional["ptk.StaleReport"] = None,
        output_dir: Optional[str] = None,
        relocate: bool = False,
    ) -> List[Dict[str, Any]]:
        """The lightmaps the scene reads, each with its state -- the Lightmap
        Baker's Manage table (``ptk.StaleReport.map_rows``).

        Parameters:
            report: The dry run the states read; default :meth:`stale_report`
                with *output_dir* / *relocate*.
            output_dir / relocate: As :meth:`stale_report`.

        Returns:
            One dict per map: ``map``, ``path`` (``None`` when missing),
            ``folder``, ``objects`` (its readers), ``writer`` (the other scene
            file that wrote it, or ``None``), ``size``, ``probe``, ``state``,
            ``level`` and ``stale``.
        """
        if report is None:
            report = self.stale_report(output_dir=output_dir, relocate=relocate)
        return report.map_rows(self._map_records())

    @classmethod
    def _map_records(cls) -> List[Dict[str, Any]]:
        """Every lightmap the scene reads, as :meth:`ptk.StaleReport.doc` takes
        it: file, folder, readers, writer and size; the probe has no
        readers. A map waiting in the session for the save
        (``LightmapRecords._stage``) gives the file it becomes then
        (``saves_to``), and that one's folder."""
        if cmds is None:
            return []
        writers = LightmapRecords._writers()
        rows = []
        for dep in LightmapRecords.lightmap_dependencies() or []:
            path = dep.get("path") or None
            readers = list(dep.get("objects") or [])
            home = LightmapRecords._home(path) if path else None
            saves_to = home if home != path else None
            rows.append(
                {
                    "map": dep["map"],
                    "path": path,
                    "saves_to": saves_to,
                    "folder": LightmapRecords._portable_dir(saves_to)
                    if saves_to
                    else dep.get("dir"),
                    "objects": readers,
                    "writer": LightmapRecords.foreign_writer(dep["map"], writers),
                    "size": ptk.ImgUtils.get_image_size(path) if path else None,
                    "probe": not readers,
                }
            )
        return rows

    @classmethod
    def _atlas_keys(cls, objects: List[str]) -> Dict[str, str]:
        """``{object: its atlas group}`` -- the objects one atlas holds share a key.

        The key is what the object's primary shading group
        (:meth:`_primary_material`) renders, not the node: shading groups
        that shade alike, or whose materials duplicate each other, are one
        group (:meth:`_duplicate_groups`), keyed by the one they fold into.
        So one material in two shading groups (an FBX import's habit) or a
        duplicate of it bakes one atlas, as if the duplicate never existed --
        without touching the shading the exporter reads. ``__no_material__``
        for an object with none.

        The one rule the atlas plan (:meth:`atlas_plan`), the in-place plan
        (:meth:`_in_place_plan`) and the split check (:meth:`_splits`)
        group by. Read over *objects* together: a group is a duplicate only of
        another one among them.
        """
        sgs = {obj: cls._primary_material(obj) for obj in objects}
        into = cls._duplicate_groups(list(sgs.values())) if cmds is not None else {}
        return {
            obj: into.get(sg, sg) if sg else "__no_material__"
            for obj, sg in sgs.items()
        }

    @classmethod
    def _duplicate_groups(cls, groups: List[Optional[str]]) -> Dict[str, str]:
        """``{duplicate: kept}`` among atlas groups -- shading groups, one entry
        per object in it (``None`` skipped).

        Two groups are one when they shade alike (the same shaders in every
        slot: one material split over two shading groups, as an FBX import
        leaves it) or their materials duplicate each other
        (``MatUtils.find_materials_with_duplicate_textures``). Of groups that
        shade alike, the one more objects are in is kept; a default node is
        never a duplicate.
        """
        counts: Dict[str, int] = {}
        for group in groups:
            if group and cmds.objExists(group):
                counts[group] = counts.get(group, 0) + 1
        if len(counts) < 2:
            return {}
        defaults = set(cmds.ls(defaultNodes=True) or [])
        slots = MatUtils._SG_SHADER_SLOTS + MatUtils._SG_EXTRA_SHADER_SLOTS
        alike: Dict[Tuple, List[str]] = {}
        for group in counts:
            shading = tuple(
                tuple(sorted(MatUtils._sg_shaders(group, slots=(slot,))))
                for slot in slots
            )
            if shading[0]:  # no surface shader: nothing to compare
                alike.setdefault(shading, []).append(group)
        kept: Dict[str, str] = {}
        for members in alike.values():
            members.sort(key=lambda g: (g not in defaults, -counts[g], g))
            kept.update((group, members[0]) for group in members)
        by_shader: Dict[str, str] = {}
        for head in sorted(set(kept.values())):
            by_shader.setdefault(cls._surface_shader(head), head)
        if len(by_shader) > 1:
            found = MatUtils.find_materials_with_duplicate_textures(sorted(by_shader))
            into = {dup: by_shader[k] for k, dups in found.items() for dup in dups}
            for group in kept:
                kept[group] = into.get(cls._surface_shader(group), kept[group])
        return {d: k for d, k in kept.items() if d != k and d not in defaults}

    @staticmethod
    def _surface_shader(group: str) -> str:
        """*group*'s surface shader (the shading group itself when it has none)."""
        return next(iter(MatUtils._sg_shaders(group)), group)

    @staticmethod
    def _merge_result(into: LightmapBakeResult, part: LightmapBakeResult) -> None:
        """Fold one of :meth:`rebake_changed`'s bakes into its result."""
        into.maps.update(part.maps)
        into.rects.update(part.rects)
        for name in ("excluded", "hidden", "unbaked", "retired", "in_place"):
            getattr(into, name).extend(getattr(part, name))
        into.repacked.update(part.repacked)
        into.refused = into.refused or part.refused
        into.cancelled = into.cancelled or part.cancelled
        into.verdict = part.verdict or into.verdict
        into.probe = part.probe or into.probe

    def _lighting_state(self, scope: Optional[List[str]] = None) -> Dict[str, Any]:
        """The lighting a bake of *scope* renders with, as the bake baseline records it.

        ``ptk.BakeBaseline.lighting_state`` over the lights
        :meth:`_bake_lights` resolves -- the one answer the bake, its probe
        and its refusal read, so the baseline fingerprints exactly the lights
        the maps saw -- each by its transform (:meth:`_light_state`): world
        matrix, keyable values, the file feeding it, and whether it lights the
        bake (on as authored, or switched on by Include Hidden Lights). The
        environment's lights ride apart while :attr:`include_environment`
        takes them in; beside them the setting that changes the light,
        :attr:`gi_depth`, and the rule that chose the lights
        (:meth:`_light_rule`). Read as authored -- call it outside
        :meth:`_lighting`, which switches lights for the bake.
        """
        plan = self._bake_lights(scope)
        settings = {"gi_depth": int(self.gi_depth)}
        if cmds is None:
            return ptk.BakeBaseline.lighting_state(
                {}, None, settings, rule=self._light_rule([])
            )
        domes = set(LightUtils.environment_lights())
        on = set(LightUtils.contributing_lights())
        lights: Dict[str, Any] = {}
        environment: Dict[str, Any] = {}
        for light in plan.lights:
            lit = light in on or (
                light in plan.hidden and self._hiding_path(light) is not None
            )
            key, state = self._light_state(light, lit)
            (environment if light in domes else lights)[key] = state
        return ptk.BakeBaseline.lighting_state(
            lights,
            environment if self.include_environment else None,
            settings,
            rule=self._light_rule(lights),
        )

    #: The lighting switches a recorded rule carries, by attribute name.
    _RULE_SWITCHES: Tuple[str, ...] = (
        "include_scene_lights",
        "include_environment",
        "include_hidden_lights",
    )

    def _light_rule(self, ids) -> Dict[str, Any]:
        """The rule a bake lights by, as the baseline records it (``@rule``).

        The lighting switches, and with Include Scene Lighting off the
        Scope's lights by id (*ids*: those the bake resolved, the environment
        not among them) -- what :meth:`_lit_as` re-applies, so a re-bake and
        a dry run read the lights this bake did. Inside :meth:`_lit_as`, the
        rule in force, as recorded. Mirror of blendertk's: one shape in both.
        """
        held = getattr(self, "_rule", None)
        if held is not None:
            return dict(held)
        rule: Dict[str, Any] = {k: bool(getattr(self, k)) for k in self._RULE_SWITCHES}
        rule["scope"] = None if self.include_scene_lights else sorted(ids)
        return rule

    @contextlib.contextmanager
    def _lit_as(self, rule: Optional[Dict[str, Any]]):
        """Light the bakes in this block by a recorded *rule* -- handed back after.

        Its switches (:attr:`_RULE_SWITCHES`), whatever this baker's say, and
        with Include Scene Lighting off its Scope's lights by id
        (:meth:`_bake_lights`), whatever a bake is handed: how a re-bake
        lights each object the way it was baked, and a dry run reads the
        lights that bake used. ``None`` (no rule recorded): this baker's own.
        Mirror of blendertk's.
        """
        if not rule:
            yield
            return
        was = {k: getattr(self, k) for k in self._RULE_SWITCHES}
        held = getattr(self, "_rule", None)
        try:
            for k in self._RULE_SWITCHES:
                if k in rule:
                    setattr(self, k, bool(rule[k]))
            self._rule = dict(rule)
            yield
        finally:
            for k, value in was.items():
                setattr(self, k, value)
            self._rule = held

    @contextlib.contextmanager
    def _keeping_folders(self, keep: bool = True):
        """Place each map the bakes in this block rewrite in the folder it is
        in now -- handed back after. How a re-bake updates a bake where it
        lies: the output folder takes only a map with no file yet
        (:meth:`_homes`). *keep* ``False`` leaves the bake's folders as they
        are (a re-bake told to relocate)."""
        held = self._keep_folders
        self._keep_folders = bool(keep)
        try:
            yield
        finally:
            self._keep_folders = held

    @staticmethod
    def _light_state(shape: str, lit: bool) -> Tuple[str, Dict[str, Any]]:
        """``(transform, state)`` of one light for :meth:`_lighting_state`.

        The state: the transform's world matrix, every keyable scalar of the
        shape (intensity, colour, exposure, normalize, every Arnold dial),
        the files upstream of it (an HDRI), and *lit*.
        """
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or [shape]
        transform = parents[0]
        values: Dict[str, Any] = {}
        for attr in sorted(set(cmds.listAttr(shape, keyable=True, scalar=True) or [])):
            try:
                value = cmds.getAttr(f"{shape}.{attr}")
            except (RuntimeError, ValueError, TypeError):
                continue
            if isinstance(value, (bool, int, float)):
                values[attr] = value
        files = sorted(
            {
                str(cmds.getAttr(f"{node}.fileTextureName") or "")
                for node in cmds.ls(cmds.listHistory(shape) or [], type="file") or []
            }
            - {""}
        )
        matrix = cmds.xform(transform, query=True, matrix=True, worldSpace=True)
        return transform, {
            "matrix": [float(v) for v in matrix],
            "attrs": values,
            "files": files,
            "on": bool(lit),
        }

    # ------------------------------------------------------------------
    # The reflection probe -- the room the bake lit, as an HDR
    # ------------------------------------------------------------------

    #: The probe's width bounds in texels (its height is half): half the bake's
    #: resolution, so it follows the tier -- 512 for mobile, 1024 for desktop.
    PROBE_WIDTH_RANGE: Tuple[int, int] = (256, 1024)

    def bake_probe(self, maps: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Capture the room the scene's bake lit as a reflection probe, and record it.

        What a lightmap cannot hold. A lightmap is diffuse irradiance with no
        direction, so a baked METAL -- which has no diffuse -- shows only what
        it reflects, and an object the bake left out (one that moves) is lit
        by nothing of the room at all. Measured on a production soldering
        table against Arnold's render of the same room: under the WebXR
        preview's studio, its metal button housing at 0.12 of Arnold's
        luminance and its unbaked magnifier up to 2.8x too bright; reflecting
        and lit by this probe, 0.62 and 0.65-1.45x (2026-10-03, end to end).

        An equirectangular HDR Arnold renders from one point with this baker's
        own settings (:meth:`TextureBaker.render_panorama`), placed by
        :class:`ProbePlacement` so it holds in any scene the baker meets:

        * **Where.** In open air -- never inside a solid (the middle of what
          is baked is inside the car, the statue or the column standing
          there) nor within 0.3 m of a surface -- over what moves in the room
          the bake makes, else the room's middle, else (an open scene) the
          baked objects that are not its ground; at half the room's height,
          at most 2 m above its floor.
        * **What it reflects onto.** The box its reflections project onto is
          the room around that point, measured from it face by face against
          what the render sees: an open face (no ceiling outdoors, a ground
          with no walls) reads as distant while the rest still project, and
          with every face open the probe is read as distant.
        * **What it sees.** The room is what is baked and the scene's
          :class:`LightmapExcludeSet`. The other visible meshes move, and are
          out of its render as they are out of the bake's
          (:meth:`_out_of_render`) -- a probe that carried them, or their
          shadows, would show them where they no longer are; unbaked meshes
          that surround the bake (a sky dome, a ground nobody baked) are the
          room's surroundings and stay in it, and so does an unbaked instance
          of a baked mesh (the render hides a mesh, and with it every
          instance). Every light is visible to it, an Arnold light too; an
          environment the bake left out (:attr:`include_environment`) is left
          out of it as well.

        A Z-up scene gets no probe: its deliverables (the WebXR preview,
        Unity) read a probe in Y-up axes, and one captured in the scene's
        would reflect the room on its side. Written beside the bake's maps as
        ``<scene>_Probe.exr`` and recorded (:meth:`LightmapRecords.commit_probe`);
        the lightmap manifest carries it into the GLB
        (``ptk.MeshConvert.apply_glb_lightmaps``) and Unity (unitytk's
        ``LightmapMetadataApplier``).

        Parameters:
            maps: ``{object: map path}`` this bake wrote; the probe lands in the
                folder most of them did. Default: the folder the scene's
                recorded maps are most found in.

        Returns:
            The probe's path, or ``None`` when nothing is baked, the scene is
            Z-up, or Arnold wrote nothing (logged).
        """
        if cmds is None:
            return None
        # What the render sees: a hidden baked object is no wall, and an
        # isolated one moves (LightmapIsolateSet) -- out of the probe as it is
        # out of every other object's bake.
        isolated = set(cmds.ls(LightmapIsolateSet.meshes(), long=True) or [])
        baked = [
            b
            for b in cmds.ls(LightmapRecords.baked_objects(), long=True) or []
            if b not in isolated and DisplayUtils.is_visible(b)
        ]
        if not baked:
            return None
        if str(cmds.upAxis(query=True, axis=True)).lower() != "y":
            self.logger.warning(
                "No reflection probe: the scene is Z-up, and the WebXR preview "
                "and Unity read a probe in Y-up axes."
            )
            return None
        # The Exclude set is the room too: static, in the bake's render.
        baked += [
            m
            for m in LightmapExcludeSet.meshes()
            if m not in baked and m not in isolated and DisplayUtils.is_visible(m)
        ]
        unbaked, shared = self._unbaked_visible(baked)
        lights = LightUtils.all_lights()
        # Lit as the maps were (:meth:`_lighting` -- inside :meth:`bake`, by
        # what that bake was handed; called alone, by what is baked): an
        # environment the bake left out stays out of its probe, muted for the
        # placement (a muted dome is no sky to see) and the render (no sky lift
        # the maps lack), and so do the lights outside its Scope.
        with self._lighting(baked):
            # An unbaked instance of a baked mesh renders with it, so the
            # probe's rays see it as the room (see _unbaked_visible).
            site = ProbePlacement(baked + shared, unbaked).place()
            if site is None:
                return None
            folder = self._probe_folder(maps)
            if not folder:
                self.logger.warning(
                    "No reflection probe: no folder holds this bake's maps."
                )
                return None
            stem = os.path.splitext(
                os.path.basename(cmds.file(query=True, sceneName=True))
            )[0]
            path = os.path.join(folder, f"{stem or 'untitled'}_Probe.exr")
            if self._staging:  # home at the save, as the maps (_stage)
                path = LightmapRecords._stage(path)
            low, high = self.PROBE_WIDTH_RANGE
            width = max(low, min(high, int(self.resolution) // 2))
            # Their own meshes: a transform would hide its baked children.
            # Out of the render, not only see-through: what moves shades
            # neither the maps nor the room they reflect. ``hide`` stays for a
            # mesh whose visibility is locked: see-through at least.
            moving = self._mesh_shapes(site.hide)
            with self._switched_off(moving, "unbaked mesh"):
                written = self.baker.render_panorama(
                    site.position,
                    path,
                    width=width,
                    hide=moving,
                    # Only when there are any: an injected backend need not
                    # know it.
                    **({"lights": lights} if lights else {}),
                )
        if not written:
            return None
        if self.record:  # a measurement's probe is its own file, recorded nowhere
            LightmapRecords.commit_probe(written, site.position, site.box)
        if site.box is None:
            reflects = "an open scene, read as distant"
        elif site.open_faces:
            reflects = (
                f"projected onto the room, {site.open_faces} open "
                f"face{'s' if site.open_faces > 1 else ''} read as distant"
            )
        else:
            reflects = "projected onto the room"
        self.logger.info(
            "Reflection probe: %s (%d px, %s at %s, %s).",
            os.path.basename(written),
            width,
            site.reason,
            [round(c, 2) for c in site.position],
            reflects,
        )
        for note in site.notes:
            self.logger.info("Reflection probe: %s.", note)
        return written

    @staticmethod
    def _unbaked_visible(baked: List[str]) -> Tuple[List[str], List[str]]:
        """``(unbaked, shared)``: the visible mesh transforms no bake marks --
        what moves, and what a probe lights in full, or what surrounds the
        bake (a dome, a ground) -- and apart, those whose mesh a baked
        transform shares.

        Every instance is a transform of its own (``allPaths``: a plain mesh
        listing names an instanced mesh by its first path alone). The render
        hides a mesh, though, not an instance (``primaryVisibility`` is the
        shape's), so hiding an instance of a baked mesh took its baked twin
        out of the probe: such an instance is never hidden -- it renders, and
        the probe's rays see it with the room.
        """
        marked = set(cmds.ls(baked, long=True) or [])
        meshes = LightmapBaker._mesh_shapes(list(marked))
        baked_meshes = set(cmds.ls(meshes, uuid=True) or []) if meshes else set()
        unbaked: List[str] = []
        shared: List[str] = []
        for shape in (
            cmds.ls(
                type="mesh", dag=True, allPaths=True, noIntermediate=True, long=True
            )
            or []
        ):
            parent = shape.rpartition("|")[0]
            if not parent or parent in marked or not DisplayUtils.is_visible(parent):
                continue
            mesh = (cmds.ls(shape, uuid=True) or [None])[0]
            (shared if mesh in baked_meshes else unbaked).append(parent)
        shared = list(dict.fromkeys(shared))
        return [n for n in dict.fromkeys(unbaked) if n not in shared], shared

    @staticmethod
    def _probe_folder(maps: Optional[Dict[str, str]]) -> Optional[str]:
        """The folder most of *maps* landed in, else the scene's first lightmap folder."""
        from collections import Counter

        folders = Counter(os.path.dirname(path) for path in (maps or {}).values())
        if folders:
            return folders.most_common(1)[0][0]
        found = LightmapRecords.search_dirs()
        return found[0] if found else None

    def preflight(self, objects: Optional[List[str]] = None) -> Optional[str]:
        """Why this scene cannot bake *objects* now, or ``None``; fixes what it can on the way.

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
          It reads the lights the bake renders with (:meth:`_bake_lights`),
          not the scene's: with :attr:`include_environment` off the render
          mutes every sky dome, so a visible dome is no light here either
          (hidden fixtures beside a visible HDRI dome were baked at full cost,
          unlit); with :attr:`include_scene_lights` off a light outside the
          Scope is switched off, so it cannot stand in for the Scope's; and
          with :attr:`include_hidden_lights` on a hidden light the bake can
          switch on is a light.

        Both sides of the last check come from :meth:`LightUtils.all_lights`,
        never a local ``ls``: it counts Arnold lights, which
        ``cmds.ls(lights=True)`` does not report at all (probed -- an
        aiAreaLight inherits THlocatorShape, not light). A local query would
        read an Arnold-lit room that still holds one legacy hidden native
        light as "lights exist, none contribute", and refuse the bake it is
        the whole point of this workflow to run.

        Parameters:
            objects: What the bake is handed (``None``: the selection) -- its
                lights are the Scope's own (:meth:`_scope_lights`), all that
                counts with :attr:`include_scene_lights` off.
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
        plan = self._bake_lights(objects)
        on = set(LightUtils.contributing_lights())
        contributing = [
            light
            for light in plan.lights
            if light in on
            or (light in plan.hidden and self._hiding_path(light) is not None)
        ]
        if plan.lights and not contributing and self.refuse_unlit:
            where = "in the scene" if self.include_scene_lights else "in the Scope"
            self.logger.warning(
                "Bake refused: all %d light(s) %s are hidden or at intensity 0, "
                "so Arnold would render no direct light and the maps would "
                "come back essentially unlit. Visibility is INHERITED -- a light "
                "whose own flag is on is still off under a hidden group. "
                "Include Hidden Lights switches them on for the bake.\n"
                "Lights the bake renders with:\n%s",
                len(plan.lights),
                where,
                self._light_audit(plan),
            )
            return (
                f"Bake skipped: all {len(plan.lights)} light(s) {where} are "
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
    #: The staging folder's prefix (``ptk.TempArtifacts``: ``<temp>/<prefix>/
    #: <prefix>_<tag>``, so it counts twice). Short on purpose: Arnold writes
    #: each map there under its shape's DAG path, and Maya and Arnold keep to
    #: 260 characters -- ``lightmap_bake`` cost a production room's trays
    #: their maps at 265 (2026-10-15; ``TextureBaker._writable_by_arnold``
    #: names what still cannot fit).
    STAGING_PREFIX: str = "lmb"

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
        levels: Dict[str, Tuple[float, float]] = {}
        for path in sorted(set(paths or ())):
            try:
                img = TextureBaker._read_exr(path)
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

    def _light_audit(self, plan: Optional[_BakeLights] = None) -> str:
        """One line per light the bake renders with: the attrs that decide whether it is lit.

        Attached to the unlit verdict and to the pre-bake refusal so a dark
        result carries its own diagnosis -- intensity, exposure, normalize,
        emitter scale and visibility are exactly the dials a black bake was
        traced to in production, and none of them are visible in the bake
        output itself.

        The lights of *plan* (:meth:`_bake_lights`; default: the last bake's,
        else the scene's as this baker would light it) -- the set the refusal
        counts and the render sees -- with the ones it leaves out named on a
        line of their own, so a light the Scope switched off reads as left
        out rather than as a light that failed.

        ``visible`` is the INHERITED answer
        (:meth:`mayatk.DisplayUtils.is_visible`), the same one
        :meth:`mayatk.LightUtils.contributing_lights` gates the refusal on.
        Reporting the transform's own flag instead would contradict the
        refusal that points here: a light hidden by a grandparent group would
        be listed ``visible=True`` beside a message saying every light is
        hidden.
        """
        if plan is None:
            plan = getattr(self, "_last_lit", None) or self._bake_lights()
        rows = []
        # The same population the refusal counts (Maya's AND Arnold's lights):
        # ``ls(lights=True)`` does not report an aiAreaLight at all, so an
        # Arnold-lit room would be refused over an audit reading "<no lights>".
        for shape in plan.lights:
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
                if shape in plan.hidden:
                    bits.append("switched on for the bake (Include Hidden Lights)")
                rows.append(f"  {t.rsplit('|', 1)[-1]}: " + "  ".join(bits))
            except Exception:
                rows.append(f"  {shape}: <unreadable>")
        if not rows:
            rows.append(
                "  <no lights in the scene>"
                if self.include_scene_lights
                else "  <no lights in the Scope>"
            )
        if plan.outside:
            rows.append(
                f"  ({len(plan.outside)} light(s) outside the Scope, switched off "
                "for the bake -- Include Scene Lighting is off: "
                + ", ".join(CoreUtils.leaf_name(s) for s in plan.outside[:8])
                + (" ..." if len(plan.outside) > 8 else "")
                + ")"
            )
        return "\n".join(rows)

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
        self._reads = {
            "texture_set": {},
            "uv_layout": {},
            "uv_stretch": {},
            "boundary": {},
        }
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
        region: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
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
                ``<scene project texture folder>/lightmaps``
                (:attr:`OUTPUT_SUBDIR`, ``TextureBaker.default_output_dir``).
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
            region: ``{long_name: (u0, v0, u1, v1)}`` -- the part of the
                lightmap layout each map covers (:meth:`TextureBaker.bake`'s
                *region*); its coverage is rasterized in that frame. Absent
                objects bake the whole square.

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

        # Every target in the render of every other's: a bake's whole list
        # reaches here once, whether its maps ship apart or as one atlas --
        # save the isolated ones (:class:`LightmapIsolateSet`), each baked in
        # a pass of its own and out of every other pass's render.
        passes, isolated = self._isolation_passes(list(targets))
        result: Dict[str, str] = {}
        done_before = 0
        cancelled = False

        def tick(done: int, _total: int, name: str) -> bool:
            # One count across the passes, so a progress bar runs once.
            nonlocal cancelled
            keep = on_progress(done_before + done, len(targets), name)
            cancelled = cancelled or keep is False
            return keep

        # The scene's lights as this bake renders them (:meth:`_lighting`):
        # already set by the entry point that was handed the Scope, else set
        # here from the meshes (and what is under them).
        with self._lighting(objects):
            for members, keep in passes:
                if cancelled:
                    break
                with self._out_of_render(list(targets), isolated, keep):
                    result.update(
                        self.baker.bake(
                            members if len(passes) > 1 else objects,
                            output_dir=output_dir,
                            prefix=prefix,
                            suffix=suffix,
                            backend=backend,
                            uv_set={m: targets[m] for m in members},
                            on_progress=(
                                tick
                                if on_progress is not None and len(passes) > 1
                                else on_progress
                            ),
                            # Name the lightmap after the object's material
                            # texture set by default (a callable -- the real
                            # materials stay assigned even during a
                            # shader-override bake, so it resolves correctly).
                            stem=stem if stem is not None else self._stem_of,
                            size=size,
                            # The card the receiver shows the bake: its
                            # camera's alone, so its own bounces keep their
                            # real materials (TextureBaker's camera_shader --
                            # as an override, a concave object lit itself as
                            # if it were white).
                            camera_shader=shader,
                            batch=batch,
                            # The earlier passes' maps are taken: a door that
                            # shares its cabinet's texture set would otherwise
                            # be given -- and overwrite -- the cabinet's name.
                            claims=self._claimed_by(claims, result),
                            # Only when there is one: an injected backend need
                            # not know it.
                            **({"region": region} if region else {}),
                        )
                        or {}
                    )
                done_before += len(members)

        if dilate and result:
            for name, path in result.items():
                try:
                    self._dilate_lightmap(
                        path,
                        alpha_threshold,
                        dilate_iterations,
                        uv_triangles=self._in_region(
                            self._lightmap_uv_triangles(name), (region or {}).get(name)
                        ),
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
        every other object in the render -- baked, baked before or excluded;
        what moves is out of it (:meth:`_out_of_render`) -- keeps its real
        material during that shape's render, so indirect light carries the true scene
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
        With :attr:`beside_textures`, or under a re-bake that keeps folders
        (:meth:`_keeping_folders`), the maps are baked into a swept work dir
        and each is then placed in its own folder (:meth:`_homes`),
        *output_dir* taking any object without one -- placed, not baked
        there, so a bake that fails partway leaves no stray file in a texture
        folder.

        Returns:
            ``{long_object_name: lightmap_path}`` for each successful bake.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        # Resolved before the white card is created so a selection with nothing
        # bakeable in it doesn't leave a stray card node behind. What was
        # handed stays the Scope its lights are read from (:meth:`_lighting`).
        scope = cmds.ls(selection=True, long=True) if objects is None else objects
        objects = self.bake_targets(scope)
        if not objects:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}
        claims = LightmapRecords.claims()
        with self._cached_reads(), self._lighting(scope):
            homes = self._homes(objects)
            # A recorded bake places its maps itself, in the session until the
            # save (_place_unpacked): never rendered onto a file the scene on
            # disk reads.
            if homes is None and not self._staging:
                return self._bake_white_card(
                    objects,
                    output_dir=output_dir,
                    prefix=prefix,
                    batch=batch,
                    claims=claims,
                    **kwargs,
                )

            output_dir = output_dir or self.baker.default_output_dir(self.OUTPUT_SUBDIR)
            with ptk.TempArtifacts(self.STAGING_PREFIX, policy="scoped") as tmp:
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
        core (``output_dir``, ``prefix``, ``batch``, ...). The selection is
        left as found: the new card took it, and deleting the card after left
        nothing selected.
        """
        with CoreUtils.preserved_selection():
            card = self._create_white_card()
            try:
                return self._bake_to_lightmap_uvs(objects, shader=card, **kwargs)
            finally:
                self._delete_white_card(card)

    def _delete_white_card(self, card: str) -> None:
        """Delete the bake's card WITH the shading group assigning it made.

        The card normally rides the bake through ``TextureBaker``'s temporary
        ray switches (``camera_shader``), which take it with them; the
        override fallback (no ``aiRaySwitch``) wears it by assignment
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

    def _bake_folders(
        self,
        targets: List[str],
        output_dir: Optional[str] = None,
        homes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """``{target: folder}`` -- where a bake of *targets* writes each map.

        Its entry in *homes* (:meth:`_homes`, read here when ``None``: the
        folder its map is in while a re-bake keeps folders, else its texture
        folder under :attr:`beside_textures`), else *output_dir* (``None``:
        the bake's default). One answer for the bake's in-place plan and the
        dry run's (:meth:`_in_place_plan`'s *folders*).

        Raises:
            ValueError: *output_dir* is ``None`` and the scene has no project.
        """
        output_dir = output_dir or self.baker.default_output_dir(self.OUTPUT_SUBDIR)
        if homes is None:
            homes = self._homes(targets)
        return {t: (homes or {}).get(t) or output_dir for t in targets}

    def _homes(self, objects: List[str]) -> Optional[Dict[str, str]]:
        """``{object: folder}`` a bake places each map in, or ``None``: every
        map in the bake's output folder.

        While a re-bake keeps folders (:meth:`_keeping_folders`), the folder
        of the map each object reads now; else, with :attr:`beside_textures`,
        its texture set's (:meth:`_texture_homes`). An object with neither
        takes the output folder -- as does one whose map is outside the
        scene's project and not this scene's own (a referenced module's, a
        Save As source's in another project): a re-bake there wrote this
        scene's maps into a folder it does not own, which a copy of the
        project alone then lacked.
        """
        if not (self._keep_folders or self.beside_textures):
            return None
        homes = self._texture_homes(objects) if self.beside_textures else {}
        if self._keep_folders:
            wanted = set(objects)
            project = LightmapRecords.project_root()
            writers = LightmapRecords._writers()
            # A map waiting in the session for the save is placed by its home.
            homes.update(
                (t, os.path.dirname(p))
                for t, n, p in (
                    (t, n, LightmapRecords._home(p) if p else p)
                    for t, n, p in LightmapRecords._reads()
                )
                if p
                and t in wanted
                and (
                    not project
                    or ptk.FileUtils.is_under(os.path.abspath(p), project)
                    or LightmapRecords.wrote(n, writers)
                )
            )
        return homes

    def _texture_homes(self, objects: List[str]) -> Dict[str, str]:
        """``{object: folder}`` -- where :attr:`beside_textures` puts each map.

        The folder of the object's texture set (:meth:`_texture_set`), so
        ``<set>_Lightmap.exr`` lands beside ``<set>_BaseColor.png``. Only a
        folder that exists on THIS machine qualifies: a texture path that
        resolves nowhere (another drive, a moved library) must not have a bake
        create it. Nor one outside the scene's PROJECT
        (:meth:`LightmapRecords.project_root`): a texture library two projects
        share would take both projects'
        lightmaps under one name, each bake replacing the other's -- a
        production soldering assembly's room maps beside another project's
        room textures, where that project's own room lightmap lives, and a
        PLAYGROUND bake's table atlas in the soldering project (2026-10-03).
        An object without one is left out, and its map takes the bake's
        ``output_dir``.

        Read BEFORE the bake: during it an instanced target wears the white
        card (``TextureBaker._forced_shader``), and a query then would find
        the card's (absent) textures.
        """
        project = LightmapRecords.project_root() or ""
        homes: Dict[str, str] = {}
        for obj in objects:
            found = self._texture_set_of(obj)
            if (
                found
                and os.path.isdir(found[1])
                and ptk.FileUtils.is_under(os.path.abspath(found[1]), project)
            ):
                homes[obj] = found[1]
        if len(homes) != len(objects):
            self.logger.info(
                "Beside textures: %d of %d object(s) have no texture folder on "
                "disk inside the project; their maps go to the output folder.",
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
    #: Measured on a production room at 1024 / 4 samples, with two AA seeds
    #: so the difference is pure sampling noise: a floor cell's shadow carried
    #: 21.9% relative noise rendered at the cell, 15.2% at 2x and 9.5% at 4x.
    #: At the cell it read as splotches in the WebXR preview. The cost is the
    #: square of this over the plan's own texels, and never more than a full
    #: map per object.
    #:
    #: The rays the shrink averages are not wasted: they buy what the same
    #: rays spent as camera samples at the cell would (at one budget per
    #: SHIPPED texel, 4x at AA 8, 2x at AA 16 and 1x at AA 32 baked 2.18 /
    #: 2.10 / 1.98% shadow noise). 2x, not the 4x it was, since the tiers
    #: bake four bounces (2026-10-01): on a production floor at AA 4 / GI 4,
    #: 4x took 75 s for 0.39% shadow mottle and 2x 22 s for 0.52%, under a
    #: third of the time for a third more mottle. What the budget IS, and
    #: where it goes, is the texture baker's
    #: (:meth:`TextureBaker._sampling_settings`).
    _ATLAS_SUPERSAMPLE: int = 2

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

        The lightmap UVs are built here rather than inside the bake, and
        BEFORE the plan: the plan sizes and shapes each cell from the layout
        that renders, and a layout built after it -- none before, or one that
        failed ``UvDiagnostics.is_bakeable_lightmap`` -- was planned from the
        layout it replaced (a missing one assumed :attr:`_UNWRAP_FILL`
        covered; a rebuilt one squeezed into a cell shaped for a stretch it no
        longer has). Nothing in the build needs the plan: the islands' gutter
        is a fixed fraction of the layout (``UvUtils.calculate_uv_padding``,
        normalized), whatever map it is built for. A mesh that already has a
        valid lightmap set keeps it (``create_lightmap_uvs`` reuses rather
        than repacks), exactly as the per-object path does -- an artist's
        tuned unwrap is not this method's to throw away.

        Intermediates never reach *output_dir*: the per-object tiles are baked
        into a tracked temp dir and only the finished maps are placed, so a
        bake cannot litter a project's texture folder with files the caller has
        no use for. Anything the pack could not consolidate (cv2 missing, a
        group that failed) is still moved out of the work dir before it is
        swept -- a finished bake is never lost to a packing problem. With
        :attr:`beside_textures` the atlases are packed in the work dir too and
        each is placed in its material group's texture folder
        (:meth:`_texture_homes`), *output_dir* taking a group without one.

        **Part of a group** already in an atlas -- a Selected bake of some of
        its objects, or :meth:`rebake_changed`'s -- is written back into the
        cells those objects own in it (:meth:`_in_place_plan`,
        :meth:`_pack_in_place`): baked at its cell's size, the atlas read,
        their cells replaced, the seams they share with the rest stitched
        one-sided, and every other object's texels kept. So each material
        keeps ONE atlas at one texel density. A target that no longer fits
        its cell (its atlas extent drifted past :attr:`_IN_PLACE_TOLERANCE`),
        one new to the group, an atlas baked before cells were recorded or
        split by an earlier partial bake: the WHOLE group re-bakes into a
        fresh layout instead, logged with why, once -- the next partial bake
        goes in place. A member that cannot bake now (hidden, excluded)
        keeps reading the old atlas, and the rest take the next free name.

        The plan resolves through :meth:`bake_targets`, so members of the
        scene's :class:`LightmapExcludeSet` take no cell (they still light the
        rest). Extra ``kwargs`` are forwarded to the bake core
        (:meth:`_bake_to_lightmap_uvs`). *prefix* / *suffix* name both the
        tiles and the atlas. Returns :meth:`pack_atlas`'s
        ``{object: (atlas_path, rect)}``.
        """
        return self._bake_atlas(
            objects, output_dir=output_dir, prefix=prefix, suffix=suffix, **kwargs
        ).packed

    def _bake_atlas(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        intensity: float = 1.0,
        **kwargs,
    ) -> _AtlasBake:
        """:meth:`bake_atlas`, with what :meth:`bake` records beside the maps.

        *intensity* is scaled into what this writes, once: an atlas laid out
        anew after it is written, a cell written in place as its tile before
        it lands -- the atlas around it carries it already.
        """
        done = _AtlasBake()
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return done

        # What was handed stays the Scope its lights are read from (:meth:`_lighting`).
        scope = cmds.ls(selection=True, long=True) if objects is None else objects
        targets = self.bake_targets(scope)
        if not targets:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return done
        # The layouts are settled BEFORE the plan reads them: the plan sizes
        # and shapes each cell from the lightmap layout that renders. A LEGACY
        # atlas marker's squeezed UVs would plan every tile from its cell's
        # fraction of the layout (see _bake_to_lightmap_uvs, which runs the
        # same migration), and a layout built here -- none before, or one that
        # failed the bakeable check -- is not the one there was to plan from.
        LightmapRecords.migrate_legacy(targets)
        uv_set = kwargs.pop("uv_set", None) or UvDiagnostics.LIGHTMAP_UV_SET
        create_uvs = kwargs.pop("create_uvs", True)
        if create_uvs:
            UvUtils.create_lightmap_uvs(
                targets, uv_set=uv_set, map_size=self.resolution, quiet=True
            )

        output_dir = output_dir or self.baker.default_output_dir(self.OUTPUT_SUBDIR)
        claims = LightmapRecords.claims()
        with self._cached_reads(), self._lighting(scope):
            homes = self._homes(targets)
            # Part of a group already in an atlas goes back into the cells it
            # owns there; a group that cannot re-bakes whole (_in_place_plan).
            placing = (
                self._in_place_plan(
                    targets,
                    intensity=intensity,
                    folders=self._bake_folders(targets, output_dir, homes),
                )
                if self.record
                else _InPlacePlan()  # a measurement writes no file the scene reads
            )
            for name, why in placing.repack.items():
                self.logger.info("%s: re-packed whole -- %s", name, why)
            if placing.expand:
                extra, hidden, excluded = self._partition(placing.expand)
                if hidden or excluded:
                    self.logger.warning(
                        "%d member(s) of an atlas that re-packs whole cannot bake "
                        "now (hidden, or in the Exclude set) and keep reading it, "
                        "so the rest of their group takes the next free name: %s",
                        len(hidden) + len(excluded),
                        ", ".join(o.rsplit("|", 1)[-1] for o in hidden + excluded),
                    )
                if extra:
                    LightmapRecords.migrate_legacy(extra)
                    if create_uvs:
                        UvUtils.create_lightmap_uvs(
                            extra, uv_set=uv_set, map_size=self.resolution, quiet=True
                        )
                    if homes is not None:
                        homes.update(self._homes(extra) or {})
                    targets = targets + extra
            done.repacked = dict(placing.repack)
            in_place = set(placing.placed)
            rest = [t for t in targets if t not in in_place]
            plan = self.atlas_plan(rest) if rest else {}
            planned = [n for entries in plan.values() for n, _r in entries]
            planned += placing.placed
            sizes = self.plan_sizes(plan)
            for obj in placing.placed:
                row0, row1, col0, col1 = ptk.ImgUtils.atlas_pixel_rects(
                    [placing.entries[obj]["cell"]], self.resolution
                )[0]
                sizes[obj] = (max(1, col1 - col0), max(1, row1 - row0))
            bake_sizes = self._plan_bake_sizes(sizes)
            regions = self._plan_regions(bake_sizes)

            with ptk.TempArtifacts(self.STAGING_PREFIX, policy="scoped") as tmp:
                baked = self._bake_white_card(
                    planned,
                    output_dir=tmp.dir_path(),
                    prefix=prefix,
                    batch=kwargs.pop("batch", True),
                    suffix=suffix,
                    uv_set=uv_set,
                    create_uvs=False,  # built above, at each object's own size
                    size=bake_sizes,
                    region=regions,
                    # Tiles, not deliverables: each keeps its coverage for the
                    # pack, which denoises it at the cell it ships in. (Still
                    # named after its texture set: a group the pack cannot
                    # consolidate ships its tiles as they are.)
                    keep_coverage=True,
                    **kwargs,
                )
                if not baked:
                    return done
                # A fork names its copy against these claims, read before it
                # existed: each name taken is recorded in them (and in *taken*)
                # before the next fork or pack_atlas names anything.
                taken: set = set()
                for atlas, objs in placing.in_place.items():
                    tiles = {o: baked[o] for o in objs if o in baked}
                    if not tiles:
                        continue
                    try:
                        written = self._write_in_place(
                            atlas, tiles, placing, intensity, regions, claims, taken
                        )
                    except Exception as e:  # held open, unreadable: keep the old
                        self.logger.error(
                            "%s could not be written in place (%s); %s keep the "
                            "map they had.",
                            os.path.basename(atlas),
                            e,
                            ", ".join(o.rsplit("|", 1)[-1] for o in tiles),
                        )
                        continue
                    self.logger.info(
                        "%s: %d cell(s) written in place, every other one kept: %s",
                        os.path.basename(atlas),
                        len(written),
                        ", ".join(o.rsplit("|", 1)[-1] for o in written),
                    )
                    done.packed.update(written)
                    done.in_place.extend(written)
                    for obj in written:
                        entry = placing.entries[obj]
                        done.cells[obj] = {
                            key: entry[key]
                            for key in ("cell", "extent", "size", "packing")
                        }
                anew = {o: p for o, p in baked.items() if o not in in_place}
                if not anew:
                    return done
                try:
                    packed = self.pack_atlas(
                        anew,
                        # Beside the textures, the atlases are staged in a
                        # folder of their own -- never among the tiles, whose
                        # names they share -- and placed per group below.
                        # Otherwise into the session, home at the save
                        # (LightmapRecords._stage).
                        output_dir=(
                            os.path.join(tmp.dir_path(), "atlas")
                            if homes is not None
                            else LightmapRecords._pending().folder(output_dir)
                            if self._staging
                            else output_dir
                        ),
                        prefix=prefix,
                        suffix=suffix,
                        plan=plan,
                        claims=claims,
                        regions=regions,
                    )
                except Exception as e:  # cv2 missing, or an unforeseen pack error
                    self.logger.warning(
                        "Atlas packing failed (%s); keeping per-object maps.", e
                    )
                    # A tile rendered over its region maps that region onto
                    # its 0-1: bound through it, not across the whole layout.
                    packed = {
                        n: (
                            p,
                            self._onto_layout(self._IDENTITY_SCALE_OFFSET, n, regions),
                        )
                        for n, p in anew.items()
                    }
                # The work dir is swept on exit, so nothing may still point
                # into it.
                packed = self._place_unpacked(packed, output_dir, homes, claims=claims)
            if packed and float(intensity) != 1.0:
                self._apply_intensity([p for p, _rect in packed.values()], intensity)
            done.packed.update(packed)
            done.cells.update(self._plan_cells(plan, packed))
        return done

    def _plan_cells(
        self,
        plan: Dict[str, List[Tuple[str, List[float]]]],
        packed: Dict[str, Tuple[str, List[float]]],
    ) -> Dict[str, Dict[str, Any]]:
        """``{object: {"cell", "extent", "size", "packing"}}`` for the bake baseline.

        Each packed object's plan cell, the :meth:`_atlas_extent` it was sized
        for and the atlas size -- what a later bake checks before writing it
        back in place (:meth:`_in_place_plan`). A group whose members did not
        land in one file (its pack failed, each kept its own map) is recorded
        per-object, with no cell.
        """
        cells: Dict[str, Dict[str, Any]] = {}
        for entries in plan.values():
            files = {packed[n][0] for n, _rect in entries if n in packed}
            for name, rect in entries:
                if name not in packed:
                    continue
                if len(files) == 1:
                    cells[name] = {
                        "cell": [float(v) for v in rect],
                        "extent": [float(v) for v in self._atlas_extent(name)],
                        "size": int(self.resolution),
                        "packing": "atlas",
                    }
                else:
                    cells[name] = {
                        "packing": "per_object",
                        "size": int(self.resolution),
                    }
        return cells

    #: How far, per axis, a re-baked object's atlas extent may drift from the
    #: one its cell was sized for and still be written back into it
    #: (``ptk.BakeBaseline.cell_fits``); ``None`` is the baseline's own
    #: ``CELL_TOLERANCE`` (10%).
    _IN_PLACE_TOLERANCE: Optional[float] = None

    def _in_place_plan(
        self,
        targets: List[str],
        intensity: Optional[float] = None,
        folders: Optional[Dict[str, str]] = None,
        expand: bool = True,
    ) -> _InPlacePlan:
        """Which *targets* write back into their own atlas cells -- the one decision point.

        Each atlas group (:meth:`_atlas_keys`) is decided whole, against the
        atlases it owns (:meth:`_atlas_files`) -- an atlas is the group's
        most of whose readers are. One that owns none bakes as any group
        does. One that owns ONE goes in place when every target of it can
        (:meth:`_in_place_refusal`): the atlas is on disk at this
        bake's :attr:`resolution` and in the folder the bake writes the group
        to, every object reading it has a recorded cell (the bake baseline,
        :meth:`LightmapRecords.bake_state`) at the intensity this bake
        applies, and each target reads it already and still fits its cell.
        An atlas another scene file wrote (a Save As copy's source,
        :meth:`LightmapRecords.foreign_writer`) goes in place too, into this
        scene's own copy of it (``forks``): the source keeps its file.

        Otherwise -- or when the group owns two atlases, a split an earlier
        partial bake left, or one material in two shading groups baked before
        they were one group (a map an atlas bake gave a group of one counts;
        :meth:`_splits`) -- its WHOLE group re-bakes into a
        fresh layout: with *expand*, every object reading those atlases whose
        primary material is still the group's joins the bake (``repack`` says
        why, once). That heals a legacy split, and a legacy atlas baked before
        cells were recorded, once; their next partial re-bake goes in place.
        A Blender-bridge bake records its cells too
        (``BlenderBridge.reassemble_lightmaps``); one baked by a blendertk too
        old to return them re-packs its group the first time, as a legacy
        atlas does.

        Read-only: nothing is baked or written.

        Parameters:
            targets: The meshes the bake renders (long names).
            intensity: The multiplier the bake applies; ``None`` does not ask
                (the dry run, which does not know it).
            folders: ``{target: folder}`` the bake writes each to; ``None``
                does not ask.
            expand: Whether a group that cannot go in place brings its other
                members into the bake (a pack of maps already rendered cannot).
        """
        plan = _InPlacePlan()
        if cmds is None or not targets:
            return plan
        targets = list(dict.fromkeys(targets))
        found = self._atlas_files(targets)
        if found is None:
            return plan  # every map is its object's own
        markers, entries, group = found.markers, found.entries, found.group
        by_file, spelled, lone = found.by_file, found.spelled, found.lone
        chosen = set(targets)
        for key in dict.fromkeys(group[t] for t in targets):
            files = sorted(found.owned.get(key, ()))
            if not files or (len(files) == 1 and files[0] in lone):
                continue
            members = [t for t in targets if group[t] == key]
            if len(files) > 1:
                why = (
                    f"{len(files)} atlases split this material ("
                    + ", ".join(os.path.basename(spelled[f]) for f in files)
                    + ")"
                )
            else:
                why = self._in_place_refusal(
                    spelled[files[0]],
                    members,
                    by_file[files[0]],
                    entries,
                    markers,
                    intensity,
                    folders,
                )
            if why is None:
                atlas = spelled[files[0]]
                plan.in_place[atlas] = members
                plan.readers[atlas] = list(by_file[files[0]])
                writer = LightmapRecords.foreign_writer(os.path.basename(atlas))
                if writer:  # a Save As copy's source still reads it
                    plan.forks[atlas] = writer
                for reader in by_file[files[0]]:
                    plan.entries[reader] = entries[reader]
                    plan.rects[reader] = [
                        float(v)
                        for v in markers[reader].get(
                            "scaleOffset", self._IDENTITY_SCALE_OFFSET
                        )
                    ]
                continue
            extra = list(
                dict.fromkeys(
                    r
                    for f in files
                    for r in by_file[f]
                    if r not in chosen and group[r] == key
                )
            )
            if expand and extra:
                n = len(extra)
                why += f"; its {n} other member{'s' if n != 1 else ''} re-bake with it"
                plan.expand.extend(r for r in extra if r not in plan.expand)
            for f in files:
                plan.repack[os.path.basename(spelled[f])] = why
        return plan

    def _atlas_files(self, objects: List[str] = ()) -> Optional[_AtlasFiles]:
        """The atlases the scene's objects read and the atlas group owning
        each, with *objects*' groups read beside theirs; ``None`` when every
        map is its object's own (nothing to write into, nothing split).

        Each atlas is the group's most of its readers belong to: a reader
        whose material changed since (Texture Combine's merge, a reassigned
        object) is new to its NEW group's atlas, never the owner of its old.
        Groups are :meth:`_atlas_keys`', so a material in two shading groups
        owns both maps they baked -- a split. Read-only.
        """
        markers = dict(LightmapRecords._marker_records())
        path_of = {t: p for t, _n, p in LightmapRecords._reads() if p}
        by_file: Dict[str, List[str]] = {}
        spelled: Dict[str, str] = {}
        for reader in sorted(markers):
            path = path_of.get(reader)
            if path:
                key = os.path.normcase(os.path.abspath(path))
                by_file.setdefault(key, []).append(reader)
                spelled.setdefault(key, path)
        atlases = {f for f, readers in by_file.items() if len(readers) > 1}
        record = LightmapRecords.bake_state()["objects"]
        entries = {k: e for k, e in record.items() if e["removed_at"] is None}
        # An atlas bake gives a group of one its own map. Alone it is no atlas
        # to write into, but a group reading it beside another map is split
        # all the same: one material in two shading groups, or a duplicate.
        lone = {
            f
            for f, readers in by_file.items()
            if len(readers) == 1
            and (entries.get(readers[0]) or {}).get("packing") == "atlas"
        }
        if not atlases and len(lone) < 2:
            return None
        # A one-reader map whose packing no record speaks for (baked before
        # change tracking, then tracked as it stood) is a split-off only
        # beside an atlas its group owns: a group that owns none was baked per
        # object, and stays so.
        unknown = {
            f
            for f, readers in by_file.items()
            if len(readers) == 1 and not (entries.get(readers[0]) or {}).get("packing")
        }
        readers = [r for f in sorted(atlases | lone | unknown) for r in by_file[f]]
        group = self._atlas_keys(list(dict.fromkeys(readers + list(objects))))
        owned: Dict[str, set] = {}
        for f in atlases:
            votes: Dict[str, int] = {}
            for reader in by_file[f]:
                votes[group[reader]] = votes.get(group[reader], 0) + 1
            owned.setdefault(min(votes, key=lambda g: (-votes[g], g)), set()).add(f)
        for f in sorted(unknown):
            if group[by_file[f][0]] in owned:
                lone.add(f)
        for f in lone:
            owned.setdefault(group[by_file[f][0]], set()).add(f)
        return _AtlasFiles(markers, entries, by_file, spelled, lone, group, owned)

    def _splits(self) -> Dict[str, Tuple[List[str], List[str]]]:
        """``{atlas group: (map paths, objects)}`` -- each group that reads
        more than one map (one material in two shading groups, or a
        duplicate, baked apart before :meth:`_atlas_keys` grouped them): its
        maps, most-read first, and the group's objects reading them. What
        :meth:`combine` packs into one atlas, and what the Manage table names
        *split*. Read-only."""
        found = self._atlas_files()
        if found is None:
            return {}
        out: Dict[str, Tuple[List[str], List[str]]] = {}
        for key, files in found.owned.items():
            if len(files) < 2:
                continue
            files = sorted(files, key=lambda f: (-len(found.by_file[f]), f))
            readers = sorted(
                r for f in files for r in found.by_file[f] if found.group[r] == key
            )
            out[key] = ([found.spelled[f] for f in files], readers)
        return out

    def _split_fields(self) -> Dict[str, Dict[str, Tuple[str, ...]]]:
        """``ptk.StaleReport``'s ``split`` and ``split_materials`` from
        :meth:`_splits`: each split map's group maps, and the surface shaders
        its objects use -- more than one is a duplicate still in the scene."""
        split: Dict[str, Tuple[str, ...]] = {}
        materials: Dict[str, Tuple[str, ...]] = {}
        for paths, readers in self._splits().values():
            names = tuple(os.path.basename(p) for p in paths)
            sgs = {LightmapRecords._primary_material(r) for r in readers} - {None}
            shaders = tuple(sorted({self._surface_shader(sg) for sg in sgs}))
            for name in names:
                split[name], materials[name] = names, shaders
        return {"split": split, "split_materials": materials}

    def _in_place_refusal(
        self,
        atlas: str,
        members: List[str],
        readers: List[str],
        entries: Dict[str, Dict[str, Any]],
        markers: Dict[str, Dict[str, Any]],
        intensity: Optional[float],
        folders: Optional[Dict[str, str]],
    ) -> Optional[str]:
        """Why *members* cannot be written back into their cells of *atlas*, or ``None``.

        The checks :meth:`_in_place_plan` documents, cheapest first, the
        first that fails worded for the log and the report.
        """
        name = os.path.basename(atlas)
        res = int(self.resolution)
        size = ptk.ImgUtils.get_image_size(atlas)
        if not size:
            return f"{name} cannot be read"
        if tuple(size) != (res, res):
            return f"it is {size[0]} x {size[1]} px, this bake {res} px"
        if folders:
            home = os.path.normcase(
                os.path.dirname(os.path.abspath(LightmapRecords._home(atlas)))
            )
            wanted = {
                os.path.normcase(os.path.abspath(folders[m]))
                for m in members
                if folders.get(m)
            }
            if wanted and wanted != {home}:
                return "this bake writes its maps to another folder"
        untracked = [r for r in readers if not (entries.get(r) or {}).get("cell")]
        if untracked:
            return (
                f"{len(untracked)} of its {len(readers)} readers have no recorded "
                "cell (baked before change tracking, or by a Blender bridge "
                "whose blendertk returns none)"
            )
        other = [
            r
            for r in readers
            if entries[r].get("packing") != "atlas"
            or entries[r].get("size") != res
            or entries[r].get("map") != name.lower()
        ]
        if other:
            return f"{len(other)} of its readers were recorded against another map"
        if intensity is not None:
            for reader in readers:
                was = float(markers[reader].get("intensity", 1.0))
                if abs(was - float(intensity)) > 1e-6 * max(1.0, abs(was)):
                    return f"it holds intensity {was:g}, this bake {float(intensity):g}"
        for member in members:
            leaf = member.rsplit("|", 1)[-1]
            if member not in readers:
                return f"{leaf} is new to it"
            recorded = entries[member]["extent"]
            now = [float(v) for v in self._atlas_extent(member)]
            if not ptk.BakeBaseline.cell_fits(recorded, now, self._IN_PLACE_TOLERANCE):
                area = (now[0] * now[1]) / max(
                    (recorded or [1.0, 1.0])[0] * (recorded or [1.0, 1.0])[1], 1e-12
                )
                return f"{leaf}: its cell no longer fits ({area - 1.0:+.0%} area)"
        return None

    def _write_in_place(
        self,
        atlas: str,
        tiles: Dict[str, str],
        placing: _InPlacePlan,
        intensity: float = 1.0,
        regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
        claims: Optional[Dict[str, FrozenSet[str]]] = None,
        used: Optional[set] = None,
    ) -> Dict[str, Tuple[str, List[float]]]:
        """:meth:`_pack_in_place` -- into this scene's own copy of an atlas
        another scene file wrote (:attr:`_InPlacePlan.forks`, a Save As
        copy's source), whose file stays as it was: every reader here then
        reads the copy (``LightmapRecords.fork_lightmap``). The one way every
        in-place write goes, so none writes a file another scene reads -- nor
        the one the scene file on disk reads: the atlas, cells and all, is
        written to the session until the scene is saved
        (``LightmapRecords._stage``).

        A fork's name is the bake's once chosen: taken in *used* (the paths
        this bake has named) and claimed by its readers in *claims* (mutated),
        which were read before the copy existed -- so neither the next fork
        nor :meth:`pack_atlas`'s groups, handed the same two, can name an
        atlas of their own onto it. Twin of blendertk's.

        Raises:
            Whatever :meth:`_pack_in_place` raises; nothing is re-stamped.
        """
        dest = LightmapRecords._stage(atlas)
        forked = atlas in placing.forks
        if forked:
            if claims is None:
                claims = LightmapRecords.claims()
            dest = self._unique_atlas_path(
                LightmapRecords._pending().folder(os.path.dirname(atlas)),
                os.path.splitext(os.path.basename(atlas))[0],
                set() if used is None else used,
                claims=claims,
                owners=placing.readers[atlas],
            )
        written = self._pack_in_place(
            atlas, tiles, placing, intensity, regions, dest=dest
        )
        if written and forked:
            key = os.path.basename(dest).lower()
            claims[key] = claims.get(key, frozenset()) | frozenset(
                placing.readers[atlas]
            )
        if written and os.path.basename(dest) != os.path.basename(atlas):
            LightmapRecords.fork_lightmap(
                os.path.basename(atlas), os.path.basename(dest)
            )
            self.logger.info(
                "%s was written by %s: copied to %s, which its readers here "
                "now read; the original is as it was.",
                os.path.basename(atlas),
                placing.forks[atlas],
                os.path.basename(dest),
            )
        return written

    def _pack_in_place(
        self,
        atlas: str,
        tiles: Dict[str, str],
        placing: _InPlacePlan,
        intensity: float = 1.0,
        regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
        dest: Optional[str] = None,
    ) -> Dict[str, Tuple[str, List[float]]]:
        """Write *tiles* back into their own cells of *atlas*; every other cell stays.

        Each tile is finished for its recorded cell exactly as a fresh pack
        finishes one (:meth:`_cell_tile`) and scaled by *intensity* there --
        the atlas around it carries it already -- then placed onto the atlas
        as read (``ptk.ImgUtils.assemble_atlas`` ``canvas``). The seams it
        shares with a cell left as it was are stitched ONE-SIDED
        (:meth:`_seam_pairs` ``hold``, ``ptk.ImgUtils.stitch_seams`` ``hold``):
        the new cell meets the held one, which keeps every texel. The gutters
        are then refilled as a fresh pack fills them, and the atlas is
        swapped in whole (:meth:`_write_lightmap_exr`). A bake never changes
        the texels another object reads.

        Parameters:
            atlas: The atlas's path, as :meth:`_in_place_plan` named it.
            tiles: ``{object: tile path}`` -- this bake's renders of its
                targets there.
            placing: The plan (cells, readers, published rects).
            intensity: The multiplier the atlas already carries.
            regions: As :meth:`pack_atlas`.
            dest: Where the result is written; ``None``: *atlas* itself. A
                Save As copy writes its source's atlas into its own copy
                (:attr:`_InPlacePlan.forks`), the source's file untouched.

        Returns:
            ``{object: (the file written, published rect)}`` for each cell
            written.

        Raises:
            RuntimeError: The atlas cannot be read or is another size.
            OSError: It cannot be written (held open): nothing is.
        """
        import numpy as np

        res = int(self.resolution)
        img = TextureBaker._read_exr(atlas)
        if img is None:
            raise RuntimeError(f"cannot read {os.path.basename(atlas)}")
        img = np.asarray(img, dtype=np.float32)
        canvas = img[..., :3] if img.ndim == 3 else np.dstack([img] * 3)
        if canvas.shape[:2] != (res, res):
            raise RuntimeError(
                f"{os.path.basename(atlas)} is {canvas.shape[1]} x "
                f"{canvas.shape[0]} px, this bake {res} px"
            )
        readers = placing.readers[atlas]
        cell_of = {r: [float(v) for v in placing.entries[r]["cell"]] for r in readers}
        images: List[Any] = []
        cells: List[List[float]] = []
        placed: List[Tuple[str, List[float]]] = []
        for obj in readers:
            if obj not in tiles:
                continue
            src = TextureBaker._read_exr(tiles[obj])
            if src is None:
                self.logger.warning("Atlas: unreadable map for %s; skipping.", obj)
                continue
            tile, published = self._cell_tile(obj, src, cell_of[obj], regions)
            if float(intensity) != 1.0:
                tile = tile * float(intensity)
            images.append(tile)
            cells.append(cell_of[obj])
            placed.append((obj, published))
        if not images:
            return {}
        # Held: every reader not written, unless it shares a written cell (a
        # rebound copy reads its source's cell, and follows it).
        written = {obj for obj, _rect in placed}
        written_cells = {tuple(c) for c in cells}
        held = [
            r
            for r in readers
            if r not in written and tuple(cell_of[r]) not in written_cells
        ]
        out = ptk.ImgUtils.assemble_atlas(images, cells, res, canvas=canvas)
        pairs = self._seam_pairs(
            placed + [(r, placing.rects[r]) for r in held], res, hold=held
        )
        if len(pairs):
            out = ptk.ImgUtils.stitch_seams(
                out, pairs, hold=self._cells_mask([cell_of[r] for r in held])
            )
        # The gutters as a fresh pack fills them (_pack_group): from the
        # cells, then every texel still empty.
        union = self._cells_mask(list(cell_of.values()))
        out[~union] = 0.0
        out = ptk.ImgUtils.dilate_image(
            out, mask=union, iterations=self._atlas_gutter() + 1
        )
        out = ptk.ImgUtils.fill_empty_texels(out, mask=(out > 0).any(axis=2))
        dest = dest or atlas
        self._write_lightmap_exr(dest, out)
        return {obj: (dest, published) for obj, published in placed}

    def _cells_mask(self, cells: List[List[float]]) -> Any:
        """``(resolution, resolution)`` bool: the texels of *cells* -- exact
        (``ptk.ImgUtils.atlas_pixel_rects``, the rounding placement uses),
        clamped to the frame both ways: a rect edge that rounds past it would
        otherwise leave the frame outside the mask."""
        import numpy as np

        mask = np.zeros((self.resolution, self.resolution), dtype=bool)
        if not cells:
            return mask
        h, w = mask.shape
        for row0, row1, col0, col1 in ptk.ImgUtils.atlas_pixel_rects(
            cells, self.resolution
        ):
            mask[
                max(row0, 0) : min(max(row1, 0), h), max(col0, 0) : min(max(col1, 0), w)
            ] = True
        return mask

    def atlas_plan(
        self, objects: Optional[List[str]] = None
    ) -> Dict[str, List[Tuple[str, List[float]]]]:
        """``{material: [(object, rect), ...]}`` -- the atlas layout, decided before baking.

        Groups the meshes by what their primary material renders
        (:meth:`_atlas_keys`: a material in two shading groups, or a duplicate
        of it, is one group) and PACKS each group into its
        atlas (``ptk.ImgUtils.pack_atlas_rects``): every object a cell of its
        layout box's shape ON THE SURFACE, at one texel density for the whole
        group -- the largest at which the group fits -- each cell a whole number of texels
        at least :meth:`_atlas_gutter` from every neighbour and from the atlas
        edge, its padded slot on whole :attr:`_ATLAS_BLOCK` blocks. A solo group
        keeps the identity rect: it is already its own atlas. Instanced
        transforms are FIRST-CLASS: every copy shares one shape / UV set but
        stands somewhere different and receives different light, so each gets
        its own cell over the one shared unwrap -- the rect travels as the
        per-instance ``scaleOffset`` binding, never into the shared UVs.
        Copies take equal cells in name order, however Maya's single-precision
        areas differ between them (:attr:`pythontk.RectPacker.TIE_TOLERANCE`).

        A cell's size is :meth:`_atlas_extent`: the object's world surface
        area over its layout's island coverage, so a scaled copy earns
        proportional texels and a sparse layout keeps its islands' density;
        its shape undoes the layout's stretch (:meth:`_lightmap_stretch`), and
        one too long for the atlas at the group's density is folded toward
        square rather than lowering that density for everyone. It
        replaced a squarified treemap, which tiled the whole square by bending
        each cell away from its content's shape (two texel densities per
        object) and inset its gutter only as far as a cell could afford: at a
        low resolution small cells met their neighbours with none, and a cell
        under a texel was never placed while its object still sampled there.
        A group too big to fit even at :attr:`_ATLAS_MIN_TEXELS` per cell falls
        back to that tiled layout, warned, rather than losing the bake.

        Pure bookkeeping -- nothing is baked, read from disk or written. It
        reads geometry, material assignment and any lightmap layout the
        objects already carry; an object that has none yet (the bake builds it
        after planning, against its cell) is sized as an unwrap covering
        :attr:`_UNWRAP_FILL` of its square. The meshes come from
        :meth:`bake_targets`, so an excluded one takes no cell.
        """
        objects = self.bake_targets(objects)
        names = sorted({(cmds.ls(o, long=True) or [None])[0] for o in objects} - {None})
        keys = self._atlas_keys(names)
        groups: Dict[str, List[str]] = {}
        for name in names:  # deterministic rect order (matches pack_atlas)
            groups.setdefault(keys[name], []).append(name)

        gutter = self._atlas_gutter()
        plan: Dict[str, List[Tuple[str, List[float]]]] = {}
        for key, group in groups.items():
            if len(group) == 1:
                plan[key] = [(group[0], list(self._IDENTITY_SCALE_OFFSET))]
                continue
            # The gutter is the room the pack's dilation fills, so bilinear
            # taps and mip levels read each object's own light; the cells are
            # whole texels, so placement (assemble_atlas) and the published
            # rect derive from one integer window, which publishing then
            # re-aims at border-texel centers (_pack_group).
            rects = ptk.ImgUtils.pack_atlas_rects(
                [self._atlas_extent(o) for o in group],
                self.resolution,
                gutter=gutter,
                min_texels=self._ATLAS_MIN_TEXELS,
                align=self._ATLAS_BLOCK,
            )
            if rects is None:
                self.logger.warning(
                    "Atlas: the %d objects of %s do not fit a %d px atlas even at "
                    "%d texels each; tiling them instead (cells may lose their "
                    "gutter). Bake them at a higher resolution, or split them "
                    "across materials.",
                    len(group),
                    key.rsplit("|", 1)[-1],
                    self.resolution,
                    self._ATLAS_MIN_TEXELS,
                )
                rects = ptk.ImgUtils.snap_atlas_rects(
                    ptk.ImgUtils.inset_atlas_rects(
                        ptk.ImgUtils.compute_atlas_layout(
                            [self._surface_area(o) for o in group]
                        ),
                        self.resolution,
                        gutter,
                    ),
                    self.resolution,
                )
            plan[key] = [(n, [float(v) for v in rect]) for n, rect in zip(group, rects)]
        return plan

    #: Smallest atlas cell, in texels per axis: under this an object's
    #: lighting is a single colour, and the published rect needs two texels
    #: per axis to aim at border-texel centers.
    _ATLAS_MIN_TEXELS: int = 4

    #: The slot grid every atlas cell's padded slot lies on, in texels: the
    #: 4 x 4 blocks BC6H (Unity's HDR lightmaps) and UASTC / ASTC (the web
    #: deliverable's KTX2) encode, so no block mixes two objects' light, and
    #: the first two mip levels with it.
    _ATLAS_BLOCK: int = 4

    #: Island coverage assumed for an object with no lightmap layout yet --
    #: ``create_lightmap_uvs`` packs one after the plan, against the cell.
    _UNWRAP_FILL: float = 0.75

    def _atlas_extent(self, obj: str) -> Tuple[float, float]:
        """*obj*'s natural atlas extent: what gives it the group's texel density.

        ``ptk.ImgUtils.atlas_layout_extent`` of its world surface area and its
        lightmap layout's triangles -- the box the pack places (the islands'
        bbox when :meth:`_pack_group` will crop to it, by the same
        :attr:`_CROP_MAX_COVERAGE`) and their coverage -- turned to that box's
        shape on the surface (:meth:`_lightmap_stretch`). No layout yet: a
        square covered to :attr:`_UNWRAP_FILL`.
        """
        return ptk.ImgUtils.atlas_layout_extent(
            self._surface_area(obj),
            self._lightmap_uv_triangles(obj),
            stretch=self._lightmap_stretch(obj),
            max_coverage=self._CROP_MAX_COVERAGE,
            fill=self._UNWRAP_FILL,
        )

    def _lightmap_stretch(self, obj: str) -> float:
        """How much more surface a unit of u spans than a unit of v on *obj*'s layout.

        ``su / sv`` of ``ptk.ImgUtils.uv_axis_scale`` over the lightmap set's
        triangles and their world corners: 1 for a layout in the mesh's own
        proportions. A layout that fills its square whatever the mesh's shape
        reads its stretch -- every wall and baseboard of a production room
        (WALL_N, u across its 131 cm and v up its 394: 1/3; BASEBOARD_A
        13 x 395 cm: 1/27) -- and
        :meth:`_atlas_extent` gives its cell the surface's shape, so its
        texels are square on the wall rather than in uv. 1.0 when unreadable.
        Read once per bake (:meth:`_cached_reads`).
        """
        return self._cached("uv_stretch", obj, self._read_lightmap_stretch)

    @classmethod
    def _read_lightmap_stretch(cls, obj: str) -> float:
        """:meth:`_lightmap_stretch`, uncached."""
        resolved = cls._lightmap_set(obj)
        if resolved is None:
            return 1.0
        try:
            uvs, points = UvUtils.get_uv_world_triangles(obj, resolved[1])
            scale = ptk.ImgUtils.uv_axis_scale(points, uvs)
        except Exception:
            return 1.0
        return scale[0] / scale[1] if scale else 1.0

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

        The cell's own size, with two corrections:

        * **Supersampling** by :attr:`_ATLAS_SUPERSAMPLE`: the resize into the
          cell is the only thing that averages this path's sampling noise, so
          the tile renders above its cell (see that attribute for the
          measurement) -- ``supersample^2`` render texels per cell texel, which
          for an elongated cell (one square frame either way) means the frame
          is sized from the cell's AREA, never under its long side. Capped at
          :attr:`resolution`: a full map is the ceiling either way.
        * **Quantization** to :attr:`_ATLAS_BAKE_QUANTUM`, so near-equal cells
          share one RTT call (see that attribute).

        A layout covering only part of its square needs no third correction:
        it renders just its island's region (:meth:`_plan_regions`), the whole
        tile spread over it. It used to render the whole square magnified by
        the axis it covered least -- a reused artist layout at u 0..0.33 drew
        3x the texels it shipped, a baseboard strip at u 0..0.05 a full map
        for a sliver -- and the crop threw the rest away: 342 of a production
        room's 624 render-seconds went to its walls and baseboards (mobile,
        2026-10-02).
        """
        out: Dict[str, int] = {}
        quantum = max(1, int(self._ATLAS_BAKE_QUANTUM))
        supersample = max(1, int(self._ATLAS_SUPERSAMPLE))
        for name, (width, height) in sizes.items():
            # One square frame per tile, so an ELONGATED cell (a cropped layout
            # the pack keeps at its own aspect) is served unevenly: its short
            # axis gets the frame's every texel. Sized for the same render
            # texels per shipped texel as a square cell of its area -- the
            # supersample's noise averaging -- rather than for its long side:
            # a 1:3 wall cell renders what its near-square tiled cell did,
            # where supersampling the long side would have cost 3x. The long
            # side never drops under one render texel per cell texel.
            px = max(max(width, height), supersample * math.sqrt(width * height))
            px = int(math.ceil(px - 1e-9))
            px = min(int(self.resolution), -(-px // quantum) * quantum)
            out[name] = max(1, px)
        return out

    #: Render texels a region is padded by past its island on every side, so
    #: the island stays clear of the image frame -- the border the coverage
    #: erode, the extension and the filter treat exactly as on a whole square
    #: (whose generated islands sit about a texel in from its edges). At 1,
    #: Arnold's pixel filter corrupted the island's outermost row near the
    #: frame (+3.3% / -1.4% on a production room's wall tiles: a 1-2%
    #: dip-and-ridge at every panel seam at 2K, where Arnold's own camera
    #: render is smooth). At 4 the unclamped side came back clean; the sides
    #: the unit square clamped kept a FRACTION of a texel, so the region is
    #: no longer clamped (:meth:`_plan_regions`).
    _REGION_PAD: float = 4.0

    def _plan_regions(
        self, bake_sizes: Dict[str, int]
    ) -> Dict[str, Tuple[float, float, float, float]]:
        """``{object: (u0, v0, u1, v1)}`` -- the part of its layout each partial tile renders.

        The objects whose lightmap island leaves real dead space in its square
        (``ptk.ImgUtils.uv_crop_extent`` -- the same test the crop takes),
        each its island's bbox padded by :attr:`_REGION_PAD` texels of the
        size it renders at on EVERY side -- past the unit square where the
        island touches its edge: RTT renders a region beyond it (measured:
        u/v_start -0.25, scale 1.5 maps exactly), and a side clamped to it kept
        only a fraction of a texel. A layout that fills its square (every
        generated one) is absent: it renders whole.
        """
        out: Dict[str, Tuple[float, float, float, float]] = {}
        for name, px in bake_sizes.items():
            bbox = self._lightmap_uv_bbox(name)
            if ptk.ImgUtils.uv_crop_extent(bbox, self._CROP_MAX_COVERAGE) == (1.0, 1.0):
                continue
            u0, v0, u1, v1 = (min(max(float(v), 0.0), 1.0) for v in bbox)
            grow = 1.0 / max(1.0, px - 2.0 * self._REGION_PAD)
            pu = (u1 - u0) * grow * self._REGION_PAD
            pv = (v1 - v0) * grow * self._REGION_PAD
            out[name] = (u0 - pu, v0 - pv, u1 + pu, v1 + pv)
        return out

    @staticmethod
    def _to_region_rect(region: Tuple[float, float, float, float]) -> List[float]:
        """The ``scaleOffset`` taking layout uv into *region*'s own 0-1 frame."""
        u0, v0, u1, v1 = region
        su, sv = u1 - u0, v1 - v0
        return [1.0 / su, 1.0 / sv, -u0 / su, -v0 / sv]

    @classmethod
    def _in_region(cls, triangles, region):
        """*triangles* (``(N, 3, 2)`` layout uv) in *region*'s frame -- as given without one."""
        if triangles is None or region is None:
            return triangles
        sx, sy, ox, oy = cls._to_region_rect(region)
        return triangles * (sx, sy) + (ox, oy)

    @classmethod
    def _onto_layout(cls, rect, obj, regions) -> List[float]:
        """*rect*, which maps the map of *obj*'s own 0-1 -- its REGION's, when
        *regions* names it -- as the binding the engine applies to LAYOUT uv
        (the layout -> region step folded in). Twin of blendertk's."""
        if not regions or obj not in regions:
            return list(rect)
        return list(ptk.ImgUtils.compose_rect(rect, cls._to_region_rect(regions[obj])))

    def _keep_unpacked(
        self,
        src: str,
        output_dir: str,
        used: set,
        claims: Optional[Dict[str, Any]],
        obj: str,
        keep_sources: bool,
        avoid: "set" = frozenset(),
    ) -> Optional[str]:
        """A failed group's per-object map, kept in *output_dir*: its path there.

        Moved in (copied with *keep_sources*) under its own name, made unique
        by the atlas naming rule (:meth:`_unique_atlas_path`: never onto
        another scene's file of that name, ``LightmapRecords.strangers``),
        unless it is there already: a caller's sources may sit in a folder it
        sweeps on exit (Texture Combine's re-pack stages its crops), and a
        binding into it is a map that stops existing. ``None`` -- it could not
        be placed (logged); the object keeps the map it had.
        """
        source = os.path.abspath(src)
        if os.path.normcase(os.path.dirname(source)) == os.path.normcase(
            os.path.abspath(output_dir)
        ):
            return src
        stem, ext = os.path.splitext(os.path.basename(source))
        ext = ext or ".exr"
        # A session folder is named for as its home (_unique_atlas_path).
        home = LightmapRecords._home(output_dir)
        # Claims know only this scene's readers: another scene's map of this
        # name, sharing the folder, was replaced. The source is never its own.
        strangers = {
            p
            for p in LightmapRecords.strangers(home, stem, ext, claims)
            if os.path.normcase(p) != os.path.normcase(source)
        }
        dst = ptk.FileUtils.unique_path(
            home,
            stem,
            ext,
            used,
            claims=claims,
            owners=(obj,),
            avoid={LightmapRecords._home(p) for p in avoid} | strangers,
        )
        if home != output_dir:
            dst = LightmapRecords._stage(dst)
        try:
            if keep_sources:
                shutil.copy2(source, dst)
            else:
                self._move_into_place(source, dst)
        except OSError as error:
            self.logger.error(
                "Atlas: %s's map could not be placed in %s (%s); it keeps the map "
                "it had.",
                obj.rsplit("|", 1)[-1],
                output_dir,
                error,
            )
            return None
        return dst

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

        A recorded bake (:attr:`_staging`) places each map in the session
        instead (``LightmapRecords._stage``), named as above for its folder,
        which it reaches when the scene is saved.
        """
        homes = homes or {}
        claims = claims or {}

        def staged(path: str) -> str:
            return LightmapRecords._stage(path) if self._staging else path

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
            if os.path.normcase(
                os.path.dirname(LightmapRecords._home(source))
            ) == os.path.normcase(os.path.abspath(folder)):
                moved[source] = path
                out[name] = (path, rect)
                continue
            stem, ext = os.path.splitext(os.path.basename(path))
            if not self._staging:  # a session folder is made as it is staged
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
            placed = {os.path.normcase(LightmapRecords._home(p)) for p in moved.values()}
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
                    target = staged(candidate)
                    self._move_into_place(source, target)
                    dst = target
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

        ``ptk.FileUtils.move_file`` replaces it in one rename (staged and swapped
        in only across volumes), so a failure leaves the destination's old file as
        it was -- the object keeps its map -- and a swap that fails puts the source
        back, for the caller's next name.

        Raises:
            OSError: The move or the swap failed; *destination* is untouched.
        """
        destination = os.path.abspath(destination)
        ptk.FileUtils.move_file(
            source, os.path.dirname(destination), new_name=os.path.basename(destination)
        )

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
        regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
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

        *regions* (``{object: (u0, v0, u1, v1)}``, what :meth:`bake_atlas`
        renders a partial layout over -- :meth:`_plan_regions`) names the maps
        that cover only that part of their layout: each is read in its
        region's frame and its rect composed back onto the layout.

        One EXR + one scaleOffset per object means re-running with more objects of
        the same material reuses the same texture-set name (the atlas is named
        ``<texture-set-base><suffix>``, deterministic per group), so there is no
        per-object texture explosion. Called without a *plan* (the two-call
        form) by a baker that records (:attr:`record`), objects whose atlas
        cells are recorded are written back into them, in the atlas they read
        wherever it is
        (:meth:`_in_place_plan`, :meth:`_pack_in_place` -- *output_dir* is for
        the rest), every other object's texels kept. Otherwise re-packing only
        PART of a group is the one exception: the members left out -- or
        failed -- still sample the old atlas, so its name stays theirs
        (:meth:`LightmapRecords.claims`) and the new one takes the next free
        ``_<k>`` (:meth:`bake_atlas` re-bakes the whole group instead). A
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
            plan: An :meth:`atlas_plan` computed earlier -- pass the one the
                sources were BAKED against (:meth:`bake_atlas` does) so the
                layout cannot be re-derived differently from the one that
                decided each map's size. ``None`` computes it here, which is
                the bake-full-then-pack path -- after writing back in place
                what can be.
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
        # Fail fast (before ANY side effects) when cv2 is unavailable -- the
        # caller's fallback then commits the per-object maps untouched.
        import cv2  # noqa: F401  (availability gate; used in _pack_group)

        output_dir = output_dir or os.path.dirname(next(iter(mapping.values())))
        # Sources may live anywhere (``bake_atlas`` stages them in a temp dir),
        # so the destination is not guaranteed to exist yet -- and every branch
        # below writes, moves or copies into it.
        os.makedirs(output_dir, exist_ok=True)

        out: Dict[str, Tuple[str, List[float]]] = {}
        if plan is None and self.record:
            # The two-call form re-packs maps rendered whole: what owns a
            # recorded cell goes back into it, at intensity 1 (a pack applies
            # none), and only the rest is laid out below. A measurement
            # (``record=False``) writes no file the scene reads.
            with self._cached_reads():
                placing = self._in_place_plan(
                    list(mapping), intensity=1.0, expand=False
                )
                for atlas, objs in placing.in_place.items():
                    try:
                        written = self._write_in_place(
                            atlas, {o: mapping[o] for o in objs}, placing, 1.0, regions
                        )
                    except Exception as e:
                        self.logger.warning(
                            "Atlas: %s could not be written in place (%s); its "
                            "objects are packed anew.",
                            os.path.basename(atlas),
                            e,
                        )
                        continue
                    out.update(written)
                    for obj in () if keep_sources else written:
                        try:
                            os.remove(mapping[obj])
                        except OSError:
                            pass
            mapping = {o: p for o, p in mapping.items() if o not in out}
            if not mapping:
                return out

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
                        regions,
                    )
                except Exception as e:
                    # Never lose a bake or leave a half-consumed group: a source
                    # map is only deleted after its object landed in a written
                    # atlas, so everything this group didn't finish still has its
                    # per-object map -- keep it, in the output folder (a source
                    # may sit in a folder its caller sweeps) and bound through
                    # its region (identity otherwise). Objects already
                    # consolidated (in ``out``) stay valid: their atlas was
                    # written before any of their side effects. Other groups are
                    # unaffected.
                    self.logger.warning(
                        "Atlas: packing group %r failed (%s); keeping per-object "
                        "maps for its unfinished objects.",
                        key,
                        e,
                    )
                    own = {os.path.abspath(mapping[o]) for o in objs}
                    placed: Dict[str, str] = {}  # one move per unique file
                    for o in objs:
                        source = os.path.abspath(mapping[o])
                        if o in out or (
                            source not in placed and not os.path.exists(source)
                        ):
                            continue
                        kept = placed.get(source) or self._keep_unpacked(
                            mapping[o],
                            output_dir,
                            used,
                            claims,
                            o,
                            keep_sources,
                            avoid=all_sources - own,
                        )
                        if kept is not None:
                            placed[source] = kept
                            out[o] = (
                                kept,
                                self._onto_layout(
                                    self._IDENTITY_SCALE_OFFSET, o, regions
                                ),
                            )
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
        regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
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
        into their own rect. A map in *regions* covers only that part of its
        layout (:meth:`_plan_regions`): it is read in the region's frame and
        its published rect composed back onto the layout.
        """
        regions = regions or {}

        def onto_layout(rect, obj):
            # A region tile's rect maps the REGION's 0-1; the engine samples
            # layout uv, so fold the layout -> region step in.
            return self._onto_layout(rect, obj, regions)

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
            img = TextureBaker._read_exr(src)
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
                    ptk.FileUtils.replace_file(src, atlas_path)
            out[objs[0]] = (
                atlas_path,
                onto_layout(self._IDENTITY_SCALE_OFFSET, objs[0]),
            )
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
            img = TextureBaker._read_exr(mapping[obj])
            if img is None:
                self.logger.warning("Atlas: unreadable map for %s; skipping.", obj)
                continue
            cell = [float(v) for v in rect]
            tile, published = self._cell_tile(obj, img, cell, regions)
            images.append(tile)
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
        # mask would treat valid near-black texels as empty.
        mask = self._cells_mask(cells)
        atlas = ptk.ImgUtils.assemble_atlas(images, cells, self.resolution)
        # Members that meet at a 3D edge in one plane -- a wall's grid of
        # panels, a floor's tiles, instances of one -- are separate cells with
        # their own sampling noise, so each edge they share read as a step
        # however well each cell was finished. Stitched: both read one value.
        pairs = self._seam_pairs(placed, self.resolution)
        if len(pairs):
            atlas = ptk.ImgUtils.stitch_seams(atlas, pairs)
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

    def _cell_tile(
        self,
        obj: str,
        img: Any,
        cell: List[float],
        regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
    ) -> Tuple[Any, List[float]]:
        """``(tile, published rect)``: *obj*'s map finished for its atlas *cell*.

        The one per-cell step of every pack -- a fresh layout
        (:meth:`_pack_group`) and a cell written back in place
        (:meth:`_pack_in_place`) alike, so a cell re-baked in place is the
        cell a fresh pack would have made: cropped to the island's bounds,
        its rect published on border-texel centers, finished at the size it
        ships (:meth:`_finish_tile`), and bound onto the layout through its
        region when it was rendered over one.
        """
        import numpy as np

        regions = regions or {}
        # A partial-coverage lightmap island wastes its cell on dead space,
        # and the lit signal gets only coverage-fraction of the cell's
        # texels. Crop the source to the island's bbox and fold the crop
        # into the published rect: the engine's uv*scale+offset lands
        # identically, at full-cell density.
        bbox = self._lightmap_uv_bbox(obj)
        if bbox is not None and obj in regions:
            bbox = tuple(
                self._in_region(np.asarray([[bbox[:2], bbox[2:]]]), regions[obj])
                .reshape(-1)
                .tolist()
            )
        img, published, bounds = ptk.ImgUtils.crop_to_uv_bbox(
            img, bbox, cell, self._CROP_MAX_COVERAGE
        )
        # Publish the rect aimed at border-texel CENTERS: a cell edge
        # published on a texel BOUNDARY makes every engine tap along a shared
        # 3D edge blend onto the neighboring cell's gutter -- up to half its
        # weight on another object's lighting. Aimed at the bounds that map
        # onto the cell (NOT the island bbox, whose sub-texel overhang past a
        # crop would aim outside the cell); placement still uses the snapped
        # cell, only sampling re-aims.
        published = list(
            ptk.ImgUtils.inset_rects_to_texel_centers(
                [published], self.resolution, bboxes=[bounds]
            )[0]
        )
        # At the size it will occupy, its edges on the border-texel centers
        # the rect above publishes, denoised there when the tile carries its
        # coverage -- the SAME rounding the assembler places with, so its
        # resize is then an identity.
        row0, row1, col0, col1 = ptk.ImgUtils.atlas_pixel_rects(
            [cell], self.resolution
        )[0]
        size = (max(1, col1 - col0), max(1, row1 - row0))
        # A region tile's rect maps the REGION's 0-1; the engine samples
        # layout uv, so the layout -> region step is folded in.
        return (
            self._finish_tile(img, size, edge_centers=True),
            self._onto_layout(published, obj, regions),
        )

    #: A texel of a shrunk tile is the object's own when ANY of its footprint
    #: is island: the resample into the cell is coverage-weighted
    #: (``ptk.ImgUtils.resize_into_cell``), so a texel the island only partly
    #: covers already holds the island's own lighting, not a mix with the
    #: gutter. This only keeps float dust (a footprint that grazes the island
    #: by a rounding error) out of the denoise.
    _COVERAGE_OWN: float = 1e-3

    def _finish_tile(
        self, img: Any, size: Tuple[int, int], edge_centers: bool = False
    ) -> Any:
        """*img* as it ships in its cell: opaque RGB, at *size* ``(w, h)``.

        A tile from :meth:`bake_atlas` carries its island coverage as alpha
        (``keep_coverage``); that is what lets it be denoised HERE, at the
        cell's resolution, rather than where it was rendered. At the render's
        supersample the per-texel noise is too high to tell a shadow edge
        from grain, and the shrink averages most of it anyway -- what ships is
        the cell. The coverage is the island's GEOMETRY, not the bake step's
        dead-texel verdict: a noisy contact shadow has texels near zero, and a
        mask that dropped them would refill the shadow from the lit floor
        around it.

        The shrink is ``ptk.ImgUtils.resize_into_cell``: coverage-weighted, so
        a border texel the island only partly covers keeps the island's OWN
        lighting and is not refilled -- only texels with no island in their
        footprint are -- and, with *edge_centers* (an atlas cell, whose rect
        :meth:`_pack_group` publishes with its edges on border-texel
        centers), with the tile's edges landing on those centers. Resized edge
        to edge with the border ring refilled from the ring inside it, every
        edge sample read its lighting 1.5 texels in from the edge: two
        coplanar wall panels of a production room met with a 5-12% step under
        a smooth light field (a measured 21% on the synthetic pair in the
        tests), the room a patchwork of per-panel levels.

        A map without coverage (the two-call form's full-size maps, already
        denoised and refilled at their own size) is only resampled.
        """
        rgb = img[..., :3] if img.ndim == 3 else img
        coverage = img[..., 3] if img.ndim == 3 and img.shape[2] == 4 else None
        if not edge_centers and tuple(rgb.shape[1::-1]) == tuple(size):
            shrunk, covered = rgb, coverage
        else:
            shrunk, covered = ptk.ImgUtils.resize_into_cell(
                rgb, size, coverage=coverage, edge_centers=edge_centers
            )
        if coverage is None:
            return shrunk
        own = covered > self._COVERAGE_OWN
        if not own.any():
            return shrunk
        shrunk, own = self._denoise_and_continue(shrunk, own, self.denoise)
        if not own.all():
            shrunk = ptk.ImgUtils.dilate_image(shrunk, mask=own)
        return shrunk

    #: Texels a denoise window reaches past its centre: ``denoise_image``'s
    #: radius-2 windows fit a line per window, then average the lines of every
    #: window over a texel -- two radii. The island is continued this far
    #: before the denoise so no window an edge would truncate is.
    _DENOISE_REACH: int = 4

    @classmethod
    def _denoise_and_continue(
        cls, rgb: Any, own: Any, denoise: bool
    ) -> Tuple[Any, Any]:
        """Denoise *own*'s texels and grow it one ring, both CONTINUING its slope.

        A denoise window an island's edge truncates averages the light from
        inside the island, and a gutter texel refilled by averaging holds the
        light from further in still: across a 3D edge two cells share, each
        side then shows its own interior, a step. So the island is first
        continued by its own slope (``ptk.ImgUtils.extrapolate_fill``) under
        the denoise -- every window then sees the ramp go on -- and the first
        ring past it, the texels the bake's coverage erode left empty, is
        continued the same way rather than copied. The far gutter stays the
        caller's dilation.

        Returns:
            ``(rgb, own)`` -- *own* grown by the continued ring.
        """
        import numpy as np

        if denoise and own.any():
            padded, reach = ptk.ImgUtils.extrapolate_fill(
                rgb, own, rings=cls._DENOISE_REACH
            )
            clean = ptk.ImgUtils.denoise_image(padded, mask=reach)
            rgb = np.where(own[..., None] if rgb.ndim == 3 else own, clean, rgb)
        return ptk.ImgUtils.extrapolate_fill(rgb, own, rings=1)

    @staticmethod
    def _primary_material(obj: str) -> Optional[str]:
        """The shading group covering the most faces of *obj* (its dominant material).

        A whole-object (single-material) assignment wins outright; otherwise the
        per-face group with the most faces. Used to group objects that should
        share one lightmap atlas. Returns ``None`` when nothing is assigned.
        The rule is :meth:`LightmapRecords._primary_material`, which the bake
        baseline records as each object's group.
        """
        return LightmapRecords._primary_material(obj)

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

    #: Seam matching (:meth:`_seam_pairs`): two open edges are one 3D edge
    #: when both of one's endpoints lie this close to the other's line -- as a
    #: fraction of the atlas group's extent, and never more than a thousandth
    #: of its median edge -- so float noise in an instance's transform passes
    #: and a baseboard standing ~1 cm in front of a wall's edge (0.05% of a
    #: production room) does not. Their faces must also face the same way
    #: within :attr:`_SEAM_COPLANAR` (a wall meeting a floor shares an edge
    #: but not its light) and lie on OPPOSITE sides of the edge (a decal along
    #: a panel's border is not its neighbour).
    _SEAM_TOLERANCE: float = 1e-4
    _SEAM_COPLANAR: float = 0.99

    def _boundary_edges(self, obj: str):
        """*obj*'s open mesh edges in world space with their lightmap UVs, or ``None``.

        ``(E, 16)`` rows ``p0(3) p1(3) uv0(2) uv1(2) normal(3) inward(3)`` --
        *inward* points from the edge into its face, in the face's plane. Per
        INSTANCE path, so each copy of a shared mesh stands where it stands.
        Read once per bake (:meth:`_cached_reads`).
        """
        return self._cached("boundary", obj, self._read_boundary_edges)

    @classmethod
    def _read_boundary_edges(cls, obj: str):
        """:meth:`_boundary_edges`, uncached."""
        import maya.api.OpenMaya as om
        import numpy as np

        resolved = cls._lightmap_set(obj)
        if resolved is None:
            return None
        shape, uv_set = resolved
        try:
            # The INSTANCE's shape path (this transform's, not the shape's
            # first): an instanced mesh is one node under many transforms.
            selection = om.MSelectionList()
            selection.add(f"{obj}|{shape.rsplit('|', 1)[-1]}")
            dag = selection.getDagPath(0)
            # Arrays, never MItMeshPolygon: its getNormal() access-violates in
            # PolyEngine on a mesh carrying invalid components (the production
            # soldering table, 2026-10-02) -- so the open edges are counted off
            # the face loops and each face's normal is its own (Newell's).
            mesh = om.MFnMesh(dag)
            points = np.array(
                [(p.x, p.y, p.z) for p in mesh.getPoints(om.MSpace.kWorld)]
            )
            counts, verts = mesh.getVertices()
            uv_counts, uv_ids = mesh.getAssignedUVs(uv_set)
            us, vs = mesh.getUVs(uv_set)
            loops, offset, uv_offset = [], 0, 0
            uses: Dict[Tuple[int, int], int] = {}
            for count, uv_count in zip(counts, uv_counts):
                loop = list(verts[offset : offset + count])
                uv_loop = list(uv_ids[uv_offset : uv_offset + uv_count])
                offset += count
                uv_offset += uv_count
                for k, a in enumerate(loop):
                    key = tuple(sorted((a, loop[(k + 1) % count])))
                    uses[key] = uses.get(key, 0) + 1
                if uv_count == count:
                    loops.append((loop, uv_loop))
            rows = []
            for loop, uv_loop in loops:
                corners = points[loop]
                normal = np.cross(corners, np.roll(corners, -1, axis=0)).sum(axis=0)
                if not np.linalg.norm(normal) > 0:
                    continue  # degenerate: faces no way
                centre = corners.mean(axis=0)
                for k, a in enumerate(loop):
                    nxt = (k + 1) % len(loop)
                    b = loop[nxt]
                    if uses.get(tuple(sorted((a, b)))) != 1:
                        continue
                    pa, pb = points[a], points[b]
                    along = (pb - pa) / max(float(np.linalg.norm(pb - pa)), 1e-12)
                    inward = (centre - pa) - ((centre - pa) @ along) * along
                    ia, ib = uv_loop[k], uv_loop[nxt]
                    rows.append(
                        (*pa, *pb, us[ia], vs[ia], us[ib], vs[ib], *normal, *inward)
                    )
            return np.array(rows, dtype=np.float64) if rows else None
        except Exception:
            return None

    def _seam_pairs(self, placed: List[Tuple[str, List[float]]], size: int, hold=()):
        """``(N, 4)`` atlas pixel pairs where two members sample one 3D point of a shared edge.

        For :meth:`pythontk.ImgUtils.stitch_seams`. Every open edge of one
        member that runs along an open edge of another (collinear,
        overlapping, faces in one plane on opposite sides --
        :attr:`_SEAM_TOLERANCE`, :attr:`_SEAM_COPLANAR`) is sampled twice per
        atlas texel along the overlap, each sample the same 3D point read
        through each member's own lightmap UVs and published rect. The
        matching is DCC-agnostic and shared with blendertk's twin
        (``ptk.ImgUtils.atlas_seam_pairs``); only the edges are read here
        (:meth:`_boundary_edges`). Empty when nothing is shared.

        *hold* names members whose cells are left as they are (an atlas
        written in place, :meth:`_pack_in_place`): their seams with each other
        were stitched when they were packed, so only a seam with at least one
        free member is paired -- the held members are matched as ONE member,
        each edge already in atlas space.
        """
        hold = set(hold or ())
        edges = [self._boundary_edges(obj) for obj, _rect in placed]
        rects = [list(rect) for _obj, rect in placed]
        if hold:
            free, held = [], []
            for (obj, rect), member in zip(placed, edges):
                if member is not None:
                    (held if obj in hold else free).append(
                        self._edges_in_atlas(member, rect)
                    )
            import numpy as np

            edges = free + ([np.concatenate(held)] if held else [])
            rects = [list(self._IDENTITY_SCALE_OFFSET)] * len(edges)
        return ptk.ImgUtils.atlas_seam_pairs(
            edges,
            rects,
            size,
            tolerance=self._SEAM_TOLERANCE,
            coplanar=self._SEAM_COPLANAR,
        )

    @staticmethod
    def _edges_in_atlas(edges, rect):
        """:meth:`_boundary_edges` rows with their UVs taken through *rect* --
        atlas space, so members with different rects can be matched as one."""
        sx, sy, ox, oy = (float(v) for v in rect)
        out = edges.copy()
        out[:, [6, 8]] = edges[:, [6, 8]] * sx + ox
        out[:, [7, 9]] = edges[:, [7, 9]] * sy + oy
        return out

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

        A session folder (``LightmapRecords._stage``) is named for as its home
        -- the folder the name lands in at the save -- and the path handed
        back is in the session folder.
        """
        home = LightmapRecords._home(output_dir)
        # Nor onto another scene's file the claims cannot see (one sharing
        # this folder and texture set): LightmapRecords.strangers.
        avoid = {LightmapRecords._home(p) for p in avoid} | LightmapRecords.strangers(
            home, name, ".exr", claims
        )
        path = ptk.FileUtils.unique_path(
            home, name, ".exr", used, claims=claims, owners=owners, avoid=avoid
        )
        return path if home == output_dir else LightmapRecords._stage(path)

    def _apply_intensity(self, paths, intensity: float) -> None:
        """Scale each unique lightmap file's texels by *intensity*, once.

        Files shared by several objects (an atlas) are deduped by abspath so
        they scale exactly once per call. :meth:`bake` calls it on the maps it
        has just written, which is what makes it once per map. A file that
        can't be read is left untouched and logged -- the record is worth more
        than the multiplier.
        """
        if TextureBaker._cv2() is None:
            self.logger.warning(
                "Intensity %.3f NOT applied: cv2 is not installed.", intensity
            )
            return

        for path in {os.path.abspath(p) for p in paths}:
            try:
                img = TextureBaker._read_exr(path)
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
    # calls moved there outright: they never needed a baker, and building one
    # just to read markers is what six modules did.

    def commit_lightmap(
        self,
        mapping: Dict[str, str],
        *,
        scale_offsets: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, str]:
        """Record maps baked elsewhere: :meth:`LightmapRecords.commit`.

        :meth:`bake` records its own maps; this is for a caller that ran
        :meth:`bake_separated` / :meth:`bake_atlas` itself. *mapping* is
        ``{object: lightmap path}`` and *scale_offsets* each object's atlas
        rect (see :meth:`pack_atlas`).

        Returns:
            ``{object: lightmap path}`` for each object recorded.
        """
        return LightmapRecords.commit(mapping, scale_offsets=scale_offsets)

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

    def track(self, objects: Optional[List[str]] = None) -> List[str]:
        """Start tracking *objects*' untracked maps as they stand -- no render.

        :meth:`LightmapRecords.track`, under the lighting this baker would
        bake *objects* with (:meth:`_lighting_state`): for maps known to match
        the scene, so their changes from here on are detected. A map that may
        be out of date wants :meth:`rebake` instead. Returns the transforms
        now tracked.
        """
        return LightmapRecords.track(objects, lighting=self._lighting_state(objects))

    # ------------------------------------------------------------------
    # Combine -- a split group's maps packed into one atlas, no render
    # ------------------------------------------------------------------

    #: The largest atlas :meth:`combine_sizes` offers, in px.
    COMBINE_MAX_SIZE: int = 8192

    #: The share of every object's cell edge a size must keep to count as
    #: keeping all its detail (:meth:`combine_sizes`): rounding to whole
    #: texels and block-aligned slots loses a hair of it at any size.
    COMBINE_KEEP: float = 0.97

    def combine_sizes(
        self, maps: Optional[List[str]] = None
    ) -> List[Tuple[int, float]]:
        """``[(size, kept)]`` -- the atlas sizes :meth:`combine` of *maps* can
        take, smallest first: from the largest map the groups read now, by
        powers of two, up to the first that keeps every object's detail
        (``kept``: the least of any object's new cell edge over its old one,
        1.0 at most), at most :attr:`COMBINE_MAX_SIZE`. The last keeps the
        most. Laid out as :meth:`combine` lays it; nothing written.
        """
        groups = self._combine_groups(maps)
        if not groups:
            return []
        readers = [r for _paths, objs in groups for r in objs]
        dims = {p: self._map_dims(p) for paths, _objs in groups for p in paths}
        old = self._cell_texels(readers, dims)
        # A map that cannot be read sizes nothing: the baker's own resolution.
        largest = max(max(d) for d in dims.values()) or int(self.resolution)
        size = 1 << max(0, int(math.ceil(math.log2(max(largest, 1)))))
        out: List[Tuple[int, float]] = []
        while True:
            with self._at_resolution(size):
                new = self.plan_sizes(self.atlas_plan(readers))
            kept = min(
                (
                    min(1.0, new[o][0] / max(old[o][0], 1.0), new[o][1] / max(old[o][1], 1.0))
                    for o in new
                    if o in old
                ),
                default=1.0,
            )
            out.append((size, kept))
            if kept >= self.COMBINE_KEEP or size >= self.COMBINE_MAX_SIZE:
                return out
            size *= 2

    def combine(
        self,
        maps: Optional[List[str]] = None,
        size: Optional[int] = None,
        merge_materials: bool = True,
        on_progress: Optional[Callable[[int, int, str], Any]] = None,
    ) -> LightmapBakeResult:
        """Pack each split group *maps* belong to into one atlas -- no render.

        A split (:meth:`_splits`): one material in two shading groups, or a
        duplicate of it, baked apart. Each object's lighting is cut from the
        map it reads now (:meth:`_reused_tile`: its cell, through its
        binding) and packed as a fresh bake packs it (:meth:`atlas_plan`,
        :meth:`pack_atlas`: one texel density, gutters filled, seams
        stitched) into the group's own folder -- under a name no other scene
        file reads, so a map another scene shares is never written -- when
        the scene is saved; until then it waits in the session
        (``LightmapRecords._stage``), and an unsaved Combine leaves the saved
        scene's maps as they were. The maps nothing reads any more are set
        aside at that save (:meth:`LightmapRecords.superseding`); each object keeps when, and
        under what lighting, it was baked. One undo chunk for the scene's part.

        Parameters:
            maps: Map file names (or paths) whose groups combine; ``None``:
                every split.
            size: The atlas size, px; ``None``: the last of
                :meth:`combine_sizes` (every object's detail kept).
            merge_materials: Merge the group's duplicate materials into one
                (``MatUtils.reassign_duplicate_materials``): the duplicates
                are what split the maps. Last, once every object is cut: a
                cancel, or a map that cannot be read, merges nothing.
            on_progress: ``(done, total, object) -> bool`` per object cut, as
                :meth:`bake`'s; ``False`` cancels before anything is written.

        Returns:
            :class:`LightmapBakeResult` -- ``refused`` when nothing is split,
            or the combine was cancelled.
        """
        result = LightmapBakeResult()
        groups = self._combine_groups(maps) if cmds is not None else []
        if not groups:
            result.refused = "Nothing to combine: no map is split."
            return result
        with CoreUtils.undo_chunk("Combine Lightmaps"):
            for paths, readers in groups:
                names = [os.path.basename(p) for p in paths]
                # An earlier group's merge can mend a later one: no longer
                # split, it has nothing to combine and no size to offer.
                if not self._combine_groups(names):
                    continue
                chosen = size or self.combine_sizes(names)[-1][0]
                part = self._combine_group(
                    paths, readers, chosen, merge_materials, on_progress
                )
                self._merge_result(result, part)
                if part.refused and not part.maps:
                    break
        if not result.maps and not result.refused:
            result.refused = "Nothing to combine: no map is split."
        return result

    def _combine_groups(
        self, maps: Optional[List[str]] = None
    ) -> List[Tuple[List[str], List[str]]]:
        """The :meth:`_splits` groups ``(maps, objects)`` reading any of *maps*."""
        groups = list(self._splits().values())
        if maps is None:
            return groups
        wanted = {os.path.basename(str(m)).lower() for m in maps}
        return [
            (paths, readers)
            for paths, readers in groups
            if wanted & {os.path.basename(p).lower() for p in paths}
        ]

    def _combine_group(
        self,
        paths: List[str],
        readers: List[str],
        size: int,
        merge: bool,
        on_progress: Optional[Callable[[int, int, str], Any]] = None,
    ) -> LightmapBakeResult:
        """:meth:`combine` of one split group, into a *size* px atlas."""
        result = LightmapBakeResult()
        # A legacy marker's squeezed UVs become its binding first, as a bake
        # migrates them: the cut reads each cell through its binding.
        LightmapRecords.migrate_legacy(readers)
        # Every map is read first: one that does not decode (cut short, gone,
        # no cv2) leaves its objects their map -- named below -- and holds
        # back the merge, which deletes the duplicates.
        reads = LightmapRecords._reads()
        path_of = {t: p for t, _n, p in reads if p}
        cache: Dict[str, Any] = {
            p: TextureBaker._read_exr(p) for p in {path_of.get(r) for r in readers} if p
        }
        group = set(readers)
        unread = sorted(
            {n for t, n, _p in reads if t in group and cache.get(path_of.get(t)) is None}
        )
        before = LightmapRecords.bake_state()
        markers = dict(LightmapRecords._marker_records())
        levels = {r: float(markers[r].get("intensity", 1.0)) for r in readers}
        # One intensity per atlas: a minority cell is rescaled to the most
        # common one, which the marker then records.
        values = list(levels.values())
        intensity = max(set(values), key=values.count)
        with self._at_resolution(size), ptk.TempArtifacts(
            self.STAGING_PREFIX, policy="scoped"
        ) as tmp:
            plan = self.atlas_plan(readers)
            sizes = self.plan_sizes(plan)
            tiles: Dict[str, str] = {}
            for i, obj in enumerate(sorted(sizes)):
                name = obj.rsplit("|", 1)[-1]
                if on_progress is not None and on_progress(i, len(sizes), name) is False:
                    result.refused = "Combine cancelled: nothing written."
                    result.cancelled = True
                    return result
                image = cache.get(path_of.get(obj))
                if image is None:  # unreadable: it keeps its map (below)
                    continue
                tile = self._reused_tile(
                    image, self._binding(markers[obj]), obj, sizes[obj]
                )
                tiles[obj] = os.path.join(tmp.dir_path(), f"tile_{i}.exr")
                # A map baked at intensity 0 holds no light to rescale.
                scale = intensity / levels[obj] if levels[obj] else 1.0
                self._write_lightmap_exr(tiles[obj], tile * scale)
                self._dilate_lightmap(
                    tiles[obj],
                    0.05,
                    uv_triangles=self._lightmap_uv_triangles(obj),
                    keep_coverage=True,
                )
            # Merged last: the plan already groups the duplicates as one
            # (_atlas_keys), so nothing above needs it, and a cancel or a map
            # that cannot be read leaves every material as it was.
            if merge and unread:
                self.logger.warning(
                    "Combine: no material merged -- %s could not be read.",
                    ", ".join(unread),
                )
            elif merge:
                self._merge_duplicates(readers)
            # A map this scene did not write (another scene's, a module's, or
            # one older than the writer record) is never written over: the
            # atlas takes a name of its own, and the old file stays.
            claims = LightmapRecords.claims()
            for path in paths:
                key = os.path.basename(path).lower()
                if not LightmapRecords.wrote(key):
                    claims[key] = claims.get(key, frozenset()) | {LightmapRecords.FOREIGN}
            # Into the session, its own name kept: the file the scene on disk
            # reads is untouched until the scene is saved (_stage).
            packed = self.pack_atlas(
                tiles,
                output_dir=LightmapRecords._pending().folder(os.path.dirname(paths[0])),
                plan=plan,
                claims=claims,
            )
            cells = self._plan_cells(plan, packed)
        left = sorted(set(readers) - set(packed))
        if left:
            self.logger.warning(
                "Combine: %d object(s) keep their map (hidden, excluded or "
                "unreadable): %s",
                len(left),
                ", ".join(o.rsplit("|", 1)[-1] for o in left),
            )
        result.maps = {o: p for o, (p, _rect) in packed.items()}
        result.rects = {o: [float(v) for v in r] for o, (_p, r) in packed.items()}
        if not result.maps:
            result.refused = "Combine wrote nothing (see Script Editor)."
            return result
        with LightmapRecords.superseding(result.maps) as retired:
            LightmapRecords.commit(
                result.maps, scale_offsets=result.rects, intensity=intensity, cells=cells
            )
        result.retired = retired
        # Packed, not re-baked: each keeps when and how it was lit.
        LightmapRecords._keep_bake_state(before, list(result.maps))
        return result

    def _reused_tile(
        self, image: Any, scale_offset: List[float], obj: str, cell: Tuple[int, int]
    ) -> Any:
        """*obj*'s lighting cut from *image*, the map it reads: its whole
        layout (uv 0-1) through its binding *scale_offset*, as a bake renders
        a tile -- at the old cell's density or the new *cell*'s, whichever is
        finer (``ptk.ImgUtils.warp_quad``, supersampled where it shrinks)."""
        height, width = image.shape[:2]
        sx, sy, ox, oy = (float(v) for v in scale_offset)
        u0, v0, u1, v1 = self._lightmap_uv_bbox(obj) or (0.0, 0.0, 1.0, 1.0)
        span_u, span_v = max(u1 - u0, 1e-6), max(v1 - v0, 1e-6)
        cap = self.COMBINE_MAX_SIZE
        out = (
            int(min(cap, math.ceil(max(sx * width, cell[0] / span_u)))),
            int(min(cap, math.ceil(max(sy * height, cell[1] / span_v)))),
        )

        def at(u, v):  # layout uv -> the map's pixel-edge coordinates, rows down
            return ((u * sx + ox) * width, (1.0 - (v * sy + oy)) * height)

        corners = [at(0.0, 1.0), at(1.0, 1.0), at(1.0, 0.0), at(0.0, 0.0)]
        return ptk.ImgUtils.warp_quad(image[..., :3], corners, out)

    def _cell_texels(
        self, objects: List[str], dims: Dict[str, Tuple[int, int]]
    ) -> Dict[str, Tuple[float, float]]:
        """``{object: (w, h)}`` -- the texels of its cropped cell in the map it
        reads now (*dims*: ``{path: (width, height)}``): its binding's span of
        the map over its island's span."""
        markers = dict(LightmapRecords._marker_records())
        path_of = {t: p for t, _n, p in LightmapRecords._reads() if p}
        out: Dict[str, Tuple[float, float]] = {}
        for obj in objects:
            path = path_of.get(obj)
            if path not in dims or obj not in markers:
                continue
            width, height = dims[path]
            sx, sy = self._binding(markers[obj])[:2]
            u0, v0, u1, v1 = self._lightmap_uv_bbox(obj) or (0.0, 0.0, 1.0, 1.0)
            out[obj] = (sx * width * (u1 - u0), sy * height * (v1 - v0))
        return out

    @classmethod
    def _binding(cls, marker: Dict[str, Any]) -> List[float]:
        """A marker's ``scaleOffset`` -- the identity for one that predates it."""
        return [float(v) for v in marker.get("scaleOffset") or cls._IDENTITY_SCALE_OFFSET]

    @staticmethod
    def _map_dims(path: str) -> Tuple[int, int]:
        """``(width, height)`` of the map at *path* -- ``(0, 0)`` unread."""
        image = TextureBaker._read_exr(path)
        return (0, 0) if image is None else (image.shape[1], image.shape[0])

    @contextlib.contextmanager
    def _at_resolution(self, size: int):
        """This baker at *size* px for the block -- its plan and pack."""
        was = self.resolution
        self.resolution = int(size)
        try:
            yield
        finally:
            self.resolution = was

    @classmethod
    def _merge_duplicates(cls, objects: List[str]) -> None:
        """Merge the duplicate materials *objects* use into one
        (``MatUtils.reassign_duplicate_materials``: verified, the duplicates
        deleted) -- what split their maps."""
        sgs = {LightmapRecords._primary_material(o) for o in objects} - {None}
        shaders = sorted({cls._surface_shader(sg) for sg in sgs})
        if len(shaders) > 1:
            MatUtils.reassign_duplicate_materials(shaders, delete=True)

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
        domes: List[str] = []
        if not self.include_environment and cmds is not None:
            for shape in LightUtils.environment_lights():
                domes += cmds.listRelatives(shape, parent=True, fullPath=True) or [
                    shape
                ]
        with self._switched_off(domes, "environment light") as muted:
            if muted:
                self.logger.info(
                    "Include Environment is off: %d environment light(s) muted "
                    "for this bake.",
                    len(muted),
                )
            yield

    # ------------------------------------------------------------------
    # Which lights light the bake (Include Scene Lighting / Hidden Lights)
    # ------------------------------------------------------------------

    def _bake_lights(self, scope: Optional[List[str]] = None) -> _BakeLights:
        """Which of the scene's lights a bake of *scope* renders with.

        One answer for the bake, its probe, :meth:`preflight`'s refusal and
        :meth:`_light_audit`:

        * the scene's fixtures -- every one with :attr:`include_scene_lights`,
          else the Scope's own (:meth:`_scope_lights`; *scope* ``None`` reads
          the selection; inside :meth:`_lit_as`, the recorded rule's, by
          transform) -- the rest *outside*, switched off for the bake;
        * the environment (:meth:`LightUtils.environment_lights`) by
          :attr:`include_environment` alone: it is the world, in the Scope or
          not (:meth:`_muted_environment` leaves it out);
        * with :attr:`include_hidden_lights`, those of them switched off as
          authored -- inherited visibility (:meth:`DisplayUtils.is_visible`),
          at a non-zero intensity -- which the bake switches on
          (:meth:`_switched_on`).
        """
        plan = _BakeLights()
        if cmds is None:
            return plan
        environment = set(LightUtils.environment_lights())
        held = (getattr(self, "_rule", None) or {}).get("scope")
        if self.include_scene_lights:
            inside = None
        elif held is not None:
            # A re-bake lit as recorded (:meth:`_lit_as`): the Scope's lights
            # by transform -- one renamed or deleted since is no longer among
            # them.
            ids = set(held)
            inside = {
                light
                for light in LightUtils.all_lights()
                if (cmds.listRelatives(light, parent=True, fullPath=True) or [light])[0]
                in ids
            }
        else:
            inside = set(self._scope_lights(scope))
        for light in LightUtils.all_lights():
            if light in environment:
                if self.include_environment:
                    plan.lights.append(light)
            elif inside is None or light in inside:
                plan.lights.append(light)
            else:
                plan.outside.append(light)
        if self.include_hidden_lights:
            plan.hidden = [
                light for light in plan.lights if self._off_as_authored(light)
            ]
        return plan

    @staticmethod
    def _off_as_authored(light: str) -> bool:
        """Whether *light* is hidden -- by its own flags or a parent's -- at a
        non-zero intensity: a light Include Hidden Lights would switch on.
        Unreadable is not hidden (nothing to switch on)."""
        try:
            if DisplayUtils.is_visible(light, consider_templated_visible=True):
                return False
            return float(cmds.getAttr(f"{light}.intensity")) != 0.0
        except (RuntimeError, ValueError, TypeError):
            return False

    @staticmethod
    def scope_meshes(scope: str = "selected") -> List[str]:
        """The meshes a Scope word names, before the Exclude set: what the
        Lightmap Baker panel's Bake hands :meth:`bake` (its lights ride beside
        them, :meth:`scope_lights`).

        The words are :class:`pythontk.HandoffScope`'s -- ``selected``,
        ``visible``, ``scene`` (its synonym for ``all``); anything unknown
        reads the selection. ``visible`` and ``scene`` gather across the scene
        so a bake needn't be preceded by a manual select-all; ``selected``
        takes the selection, a group standing for the meshes under it (a bake
        set's members are often groups). Every word resolves through
        :meth:`TextureBaker.resolve_meshes` -- the one definition of
        "bakeable" -- so a selection that also holds the room's lights bakes
        the geometry instead of asking Arnold to render a light, and a
        lights-only selection reads as nothing to bake. Mirror of blendertk's
        (mesh objects there).
        """
        if cmds is None:
            return []

        def scene_meshes():
            # Every DAG PATH of every mesh: an instanced shape is ONE node
            # under several transforms, and a listing of nodes names it once
            # -- the production room's Scene scope reached 21 of its 61
            # visible meshes, its 40 instanced walls and props missed (a
            # group instance hides from a transform listing the same way).
            # ``or []``: an empty scene is an answer, not "can't enumerate".
            return (
                cmds.ls(
                    type="mesh", dag=True, allPaths=True, noIntermediate=True, long=True
                )
                or []
            )

        def visible_meshes():
            return [path for path in scene_meshes() if DisplayUtils.is_visible(path)]

        pool = ptk.HandoffScope.resolve(
            scope,
            selected=lambda: cmds.ls(selection=True, long=True),
            all=scene_meshes,
            visible=visible_meshes,
        )
        return TextureBaker.resolve_meshes(pool or [], descendants=True)

    @classmethod
    def scope_lights(cls, scope: str = "selected") -> List[str]:
        """The light shapes a Scope word holds: what lights a bake of
        :meth:`scope_meshes` with :attr:`include_scene_lights` off.

        The same words, read for lights: the lights selected or under the
        selection (:meth:`_scope_lights`), every visible one, or every one in
        the scene. Mirror of blendertk's (light objects there).
        """
        if cmds is None:
            return []
        return list(
            ptk.HandoffScope.resolve(
                scope,
                selected=lambda: cls._scope_lights(None),
                all=LightUtils.all_lights,
                visible=lambda: [
                    light
                    for light in LightUtils.all_lights()
                    if DisplayUtils.is_visible(light, consider_templated_visible=True)
                ],
            )
            or []
        )

    @staticmethod
    def select(objects) -> List[str]:
        """Make *objects* the selection -- those that exist, by name or DAG
        path, hidden ones too -- or clear it when none does: what the Lightmap
        Baker panel's set rows and map picks select with. Returns the long
        names selected. Mirror of blendertk's, which leaves out what its view
        layer cannot select.
        """
        if cmds is None:
            return []
        names = [str(obj) for obj in objects or () if obj]
        # ``cmds.ls([])`` would list the whole scene: no names, nothing selected.
        nodes = (cmds.ls(names, long=True) or []) if names else []
        if nodes:
            cmds.select(nodes, replace=True)
        else:
            cmds.select(clear=True)
        return nodes

    @staticmethod
    def _scope_lights(objects: Optional[List[str]] = None) -> List[str]:
        """The light shapes in *objects* or under them: the Scope's own lights.

        What a bake renders with Include Scene Lighting off, read off what the
        bake was HANDED rather than its meshes: ``TextureBaker.resolve_meshes``
        keeps surfaces only. A set stands for its members
        (:meth:`NodeUtils.expand_sets`) and every node for everything under it
        -- a light parented under a lamp's mesh is the lamp's. ``None`` reads
        the selection. Matched by node, not path, so an instanced light is
        found whichever path names it.
        """
        if cmds is None:
            return []
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        nodes = (
            cmds.ls(NodeUtils.expand_sets(objects), long=True, objectsOnly=True)
            if objects
            else []
        ) or []
        if not nodes:
            return []
        below = cmds.listRelatives(nodes, allDescendents=True, fullPath=True) or []
        held = set(cmds.ls(nodes + below, uuid=True) or [])
        return [
            light
            for light in LightUtils.all_lights()
            if (cmds.ls(light, uuid=True) or [None])[0] in held
        ]

    @contextlib.contextmanager
    def _lighting(self, scope: Optional[List[str]] = None):
        """Light the scene for a bake of *scope* as :meth:`_bake_lights` says;
        hand it back as found after, however the bake ends.

        The environment muted while :attr:`include_environment` is off
        (:meth:`_muted_environment`), the lights outside the Scope switched
        off while :attr:`include_scene_lights` is (by their transforms, as the
        dome is: Maya's own "switched off" for a light, and what
        :meth:`LightUtils.light_contributes` reads), and the hidden ones
        switched on while :attr:`include_hidden_lights` is
        (:meth:`_switched_on`). Re-entrant: the outermost entry decides, so
        :meth:`bake` sets it from what it was HANDED -- lights and groups
        included -- and the mechanisms below it, which see meshes alone, keep
        that. Yields the :class:`_BakeLights` in force.
        """
        if getattr(self, "_lit", None) is not None:
            yield self._lit
            return
        plan = self._bake_lights(scope)
        outside = [
            (cmds.listRelatives(light, parent=True, fullPath=True) or [light])[0]
            for light in plan.outside
        ]
        self._lit = self._last_lit = plan
        try:
            with (
                self._muted_environment(),
                self._switched_off(outside, "light outside the Scope") as off,
                self._switched_on(plan.hidden),
            ):
                if off:
                    self.logger.info(
                        "Include Scene Lighting is off: %d light(s) outside the "
                        "Scope switched off for this bake: %s",
                        len(off),
                        ", ".join(CoreUtils.leaf_name(n) for n in off[:8])
                        + (" ..." if len(off) > 8 else ""),
                    )
                yield plan
        finally:
            self._lit = None

    @staticmethod
    def _hiding_plugs(node: str) -> List[str]:
        """The plugs that switch *node* itself off -- what
        :meth:`DisplayUtils.is_visible` reads on each node of a path: its
        visibility, its level-of-detail visibility, and an enabled drawing
        override's visibility."""
        plugs = [
            f"{node}.{attr}"
            for attr in ("visibility", "lodVisibility")
            if not cmds.getAttr(f"{node}.{attr}")
        ]
        if cmds.getAttr(f"{node}.overrideEnabled") and not cmds.getAttr(
            f"{node}.overrideVisibility"
        ):
            plugs.append(f"{node}.overrideVisibility")
        return plugs

    @staticmethod
    def _writable(plug: str) -> bool:
        """Whether a ``setAttr`` can move *plug*: not locked, and not driven --
        an animation curve drives a visibility, a display layer the whole
        drawing override (its compound, so its children read as free)."""
        try:
            if cmds.getAttr(plug, lock=True) or cmds.connectionInfo(
                plug, isDestination=True
            ):
                return False
            node, attr = plug.split(".", 1)
            if attr.startswith("override"):
                return not cmds.connectionInfo(
                    f"{node}.drawOverride", isDestination=True
                )
            return True
        except (RuntimeError, ValueError):
            return False

    def _hiding_path(self, light: str) -> Optional[Tuple[List[str], List[str]]]:
        """``(opened, plugs)`` for a hidden *light*, or ``None`` when one cannot be written.

        *plugs* are every :meth:`_hiding_plugs` on its path, the light's own
        and each parent's; *opened* is the path from the light up to the
        highest node one of them hides -- the nodes switching it on shows.
        ``None`` when a plug is locked or driven (:meth:`_writable`).
        """
        try:
            path = [light]
            while True:
                parent = cmds.listRelatives(path[-1], parent=True, fullPath=True)
                if not parent:
                    break
                path.append(parent[0])
            hiders = [
                (index, plug)
                for index, node in enumerate(path)
                for plug in self._hiding_plugs(node)
            ]
        except (RuntimeError, ValueError, TypeError):
            return None
        if not all(self._writable(plug) for _index, plug in hiders):
            return None
        top = max((index for index, _plug in hiders), default=-1)
        return path[: top + 1], [plug for _index, plug in hiders]

    @contextlib.contextmanager
    def _switched_on(self, lights: List[str]):
        """Switch the hidden *lights* on for the duration -- and nothing else with them.

        A light is off by a plug on its path (:meth:`_hiding_plugs`): its own,
        or a group's above it. Each is turned on, and every other child of a
        node that turning shows -- a mesh the same group hides, a light outside
        the set -- is switched off by its own visibility, so the render gains
        the light alone. Handed back as found, however the body ends. A light
        behind a plug that cannot be written (locked; driven by a display
        layer or an animation), or whose group holds a child that cannot be
        switched off, is named and left off: never a failed bake.

        Yields the lights it switched on.
        """
        opened: Dict[str, Tuple[List[str], List[str]]] = {}
        for light in dict.fromkeys(lights or []):
            path = self._hiding_path(light)
            if path is None:
                self.logger.warning(
                    "Include Hidden Lights: could not switch on %s -- its "
                    "visibility, or a parent group's, is locked or driven (a "
                    "display layer, an animation). It stays off for this bake.",
                    CoreUtils.leaf_name(light),
                )
            elif path[1]:
                opened[light] = path
        hide: List[str] = []
        while opened:
            shown = {node for segment, _plugs in opened.values() for node in segment}
            hide, blocked = [], {}
            for light, (segment, _plugs) in opened.items():
                for node in segment[1:]:
                    for child in (
                        cmds.listRelatives(node, children=True, fullPath=True) or []
                    ):
                        if child in shown or not cmds.getAttr(f"{child}.visibility"):
                            continue
                        if self._writable(f"{child}.visibility"):
                            hide.append(child)
                        else:
                            blocked.setdefault(light, child)
            if not blocked:
                break
            for light, child in blocked.items():
                self.logger.warning(
                    "Include Hidden Lights: could not switch on %s -- it would "
                    "show %s, whose visibility is locked or driven. It stays "
                    "off for this bake.",
                    CoreUtils.leaf_name(light),
                    CoreUtils.leaf_name(child),
                )
                del opened[light]
        if not opened:
            hide = []  # every light left off: nothing is opened, so nothing hides
        restore: List[Tuple[str, Any]] = []

        def write(plug: str, value: Any) -> None:
            try:
                was = cmds.getAttr(plug)
                if bool(was) != bool(value):
                    cmds.setAttr(plug, value)
                    restore.append((plug, was))
            except Exception as e:  # never fail a bake over a switch
                self.logger.warning("Could not set %s: %s", plug, e)

        try:
            for child in dict.fromkeys(hide):
                write(f"{child}.visibility", False)
            for _segment, plugs in opened.values():
                for plug in plugs:
                    write(plug, True)
            if opened:
                self.logger.info(
                    "Include Hidden Lights: %d hidden light(s) switched on for "
                    "this bake: %s",
                    len(opened),
                    ", ".join(CoreUtils.leaf_name(n) for n in list(opened)[:8])
                    + (" ..." if len(opened) > 8 else ""),
                )
            yield list(opened)
        finally:
            for plug, was in reversed(restore):
                try:
                    cmds.setAttr(plug, was)
                except Exception as e:  # never leave the scene changed silently
                    self.logger.error("Could not restore %s: %s", plug, e)

    @staticmethod
    def _claimed_by(claims, outputs: Dict[str, str]):
        """*claims* plus each map *outputs* (``{object: path}``) wrote, read by its object.

        What a later pass of one bake (:meth:`_isolation_passes`) hands
        :meth:`TextureBaker.bake`: its names stay clear of the earlier passes'
        maps as one batch's stay clear of each other. *claims* is a
        ``{file name: readers}`` map or ``None``; returned as given when there
        is nothing to add.
        """
        if not outputs:
            return claims
        return ptk.FileDependencies.claims(
            [
                (reader, name)
                for name, readers in (claims or {}).items()
                for reader in readers
            ]
            + list(outputs.items())
        )

    def _isolation_passes(
        self, targets: List[str]
    ) -> Tuple[List[Tuple[List[str], List[str]]], "set"]:
        """``(passes, isolated)``: *targets* as render passes, and every isolated mesh.

        Each pass is ``(bake, render)``: the targets it bakes, and the
        isolated meshes it keeps in its render beside them. The targets no
        :class:`LightmapIsolateSet` member covers go together, then those of
        each member alone, with every mesh of that member in the render
        (:meth:`LightmapIsolateSet.groups` -- a door with the glass under it,
        baked or not). One pass, *targets* as given, when none of them is
        isolated. *isolated* holds the long names of the set's meshes, targets
        or not: :meth:`_out_of_render` takes each pass's others out.
        """
        groups = [
            cmds.ls(group, long=True) or [] for group in LightmapIsolateSet.groups()
        ]
        isolated = {m for group in groups for m in group}
        alone = [([t for t in targets if t in group], group) for group in groups]
        # By name: a set's member order is Maya's, and a log should repeat.
        alone = sorted((bake, group) for bake, group in alone if bake)
        if not alone:
            return [(list(targets), [])], isolated
        rest = [t for t in targets if t not in isolated]
        self.logger.info(
            "%d isolated object(s) (%s) bake in passes of their own, out of "
            "every other object's render: %s",
            len(alone),
            LightmapIsolateSet.SET_NAME,
            ", ".join(CoreUtils.leaf_name(bake[0]) for bake, _ in alone[:8])
            + (" ..." if len(alone) > 8 else ""),
        )
        return ([(rest, [])] if rest else []) + alone, isolated

    @contextlib.contextmanager
    def _out_of_render(
        self,
        targets: List[str],
        isolated: "set" = frozenset(),
        keep: List[str] = (),
    ):
        """Take what moves out of the render of a bake of *targets*.

        A bake renders what it bakes, what was baked before
        (:meth:`LightmapRecords.baked_objects`) and the scene's
        :class:`LightmapExcludeSet` -- the static scene. Any other visible
        mesh moves (an interactive prop, never baked), and a shadow or bounce
        it cast would stay in the maps wherever it went: props on a production
        cart shadowed the cart's map (2026-10-07). So does every *isolated*
        mesh (:class:`LightmapIsolateSet`, long names) but the pass's own
        member's (*keep*: a door and the glass under it), a target of this
        bake or baked before alike: a locker door's shadow on its cabinet. Each
        is switched off by its own mesh's ``visibility``
        (:meth:`_unbaked_visible`: never an instance of a mesh that stays,
        never a transform with something below it) for the bake, and handed
        back as found. Yields the shapes taken out.
        """
        others = set(isolated) - set(keep)
        static = cmds.ls(
            list(targets)
            + list(keep)
            + LightmapRecords.baked_objects()
            + LightmapExcludeSet.meshes(),
            long=True,
        )
        movers, _shared = self._unbaked_visible(
            [m for m in static or [] if m not in others]
        )
        with self._switched_off(self._mesh_shapes(movers), "unbaked mesh") as off:
            # Named by what was switched off: a locked one was warned of apart.
            named = list(dict.fromkeys(s.rpartition("|")[0] for s in off))
            if others:
                named = [n for n in named if n not in others]
            if named:
                self.logger.info(
                    "%d unbaked mesh(es) are out of this bake's render: neither "
                    "baked now, baked before, nor in the Exclude set (%s), they "
                    "move, so they cast no shadow into the maps. Add a static "
                    "one to the Exclude set to keep its shadow: %s",
                    len(named),
                    LightmapExcludeSet.SET_NAME,
                    ", ".join(CoreUtils.leaf_name(m) for m in named[:8])
                    + (" ..." if len(named) > 8 else ""),
                )
            yield off

    @staticmethod
    def _mesh_shapes(nodes: List[str]) -> List[str]:
        """The mesh shapes of *nodes* -- their own, never a child's."""
        return [
            shape
            for node in nodes
            for shape in cmds.listRelatives(
                node, shapes=True, noIntermediate=True, fullPath=True, type="mesh"
            )
            or []
        ]

    @contextlib.contextmanager
    def _switched_off(self, nodes: List[str], what: str):
        """Switch *nodes*' ``visibility`` off for the duration; yield those it took.

        Restored as found on the way out, however the body ends. A node whose
        visibility is locked or connected is left alone with a warning naming
        it as *what*, rather than failing the bake.
        """
        restore: Dict[str, Any] = {}
        for node in dict.fromkeys(nodes):
            attr = f"{node}.visibility"
            try:
                was = cmds.getAttr(attr)
                if was:
                    cmds.setAttr(attr, 0)
            except Exception as e:
                self.logger.warning("Could not switch off %s %s: %s", what, node, e)
                continue
            if was:
                restore[attr] = was
        try:
            yield [attr.rpartition(".")[0] for attr in restore]
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
        # The lights this bake renders with (:meth:`_bake_lights`, the set the
        # refusal reads): Maya's AND Arnold's, from the one enumeration (a
        # local ``ls`` drifted: it lacked aiLightPortal). An environment the
        # bake is about to MUTE is not a light source for it, so a room lit
        # only by an HDRI still gets the warning when Include Environment is
        # off -- which is exactly when it is needed -- and neither is a light
        # outside the Scope with Include Scene Lighting off.
        plan = getattr(self, "_lit", None) or self._bake_lights()
        if plan.lights:
            return
        self._warned_no_lights = True
        if not self.include_scene_lights:
            message = (
                "No lights in the Scope, and Include Scene Lighting is off: "
                "the scene's other lights are switched off for the bake -- the "
                "lightmap will bake black (unless emissive materials"
                + (
                    " or the environment light it)."
                    if self.include_environment
                    else " light it)."
                )
            )
        elif self.include_environment:
            message = (
                "No lights found in the scene -- the lightmap will bake black "
                "(unless emissive materials are the only light source)."
            )
        else:
            message = (
                "No lights found in the scene other than the environment, "
                "which Include Environment is set to leave out -- the lightmap "
                "will bake black (unless emissive materials light it)."
            )
        self.logger.warning(message)

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
        # The atlas path writes into the CALLER's output dir, whose first file
        # may be this one; the write is staged and raises rather than return
        # False (callers delete per-object maps once it returns).
        TextureBaker._write_exr(path, bgr)

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

        Both cuts are per-texel, so the fallbacks are per ISLAND: an island
        the erosion empties (thinner than the filter) keeps its fully covered
        texels, and one with no fully covered texel at all keeps its most
        covered ones. Never dropped: a dropped island is refilled from
        whichever island lies nearest in the layout -- on a production
        device, hundreds of key-sized islands at 1024 baked as
        dilation blobs while Cycles kept their lighting. An empty raster (a
        layout that missed the map entirely) returns ``None``: a mask that
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
        touched = cover > 0
        if not touched.any():
            return None
        full = cover >= cls._COVERAGE_FULL
        keep = full
        if cls._COVERAGE_ERODE > 0:
            # Beyond the frame is NOT island: a texel on the frame collects
            # what lies past it through the filter tail as any border texel
            # does, so it is eroded and continued from inside as they are.
            # cv2's default border value (+inf) exempted the frame, and an
            # island running to u/v 0 or 1 kept its outermost ring: on
            # a production room a baseboard strip's end runs ~1 cm into the wall it
            # meets, its frame row leaked the light (1332-16787 at one sample
            # against a 1.9 strip; a ROW of spikes is its own 3x3 median, so
            # the denoiser passes it), and the delivered atlas's joints read
            # 7-23x their strip -- stitched across each joint, dilated into
            # the gutter. The per-island fallback below still keeps an island
            # the erosion empties.
            keep = cv2.erode(
                full.astype(np.uint8),
                np.ones((3, 3), np.uint8),
                iterations=cls._COVERAGE_ERODE,
                borderType=cv2.BORDER_CONSTANT,
                borderValue=0,
            ).astype(bool)
        count, labels = cv2.connectedComponents(touched.astype(np.uint8), 8)
        if count > 1:
            # Islands (connected touched texels) the cuts left nothing of.
            has_keep = np.bincount(labels[keep], minlength=count) > 0
            has_full = np.bincount(labels[full], minlength=count) > 0
            best = np.zeros(count, cover.dtype)
            np.maximum.at(best, labels[touched], cover[touched])
            lost = ~has_keep[labels] & touched
            keep = keep | (lost & full)
            keep |= lost & ~has_full[labels] & (cover >= best[labels])
        return keep

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
        import numpy as np

        img = TextureBaker._read_exr(path)
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

    @classmethod
    def _pad_texels(cls, bgr: Any, mask: Any, iterations: int, denoise: bool) -> Any:
        """One image of a map through the denoise, the gutter ring and the fill."""
        if mask.any():
            bgr, mask = cls._denoise_and_continue(bgr, mask, denoise)
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
