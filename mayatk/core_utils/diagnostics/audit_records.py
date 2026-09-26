# !/usr/bin/python
# coding=utf-8
"""Scene-audit data contract: profiles, per-asset records, and the SceneReport tree.

This module is deliberately Maya-free — every class here is a plain dataclass (or
constant registry) so reports can be built, serialized, and inspected without a
Maya runtime. The engine that populates these records lives in the sibling
``scene_audit`` module (:class:`~mayatk.core_utils.diagnostics.scene_audit.SceneAnalyzer`);
the repair helpers live in ``scene_diag``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Any, Tuple

import pythontk as ptk


@dataclass
class AuditProfile:
    """Thresholds for scene analysis."""

    max_tris: int = 20000
    max_slots: int = 4
    max_tex_res: int = 4096
    # UV sets that ship per mesh. Two: the texture UVs plus a lightmap UV --
    # TEXCOORD_1 is where every engine reads a lightmap, so a second set is the
    # norm, not a cost. Leftover ``_uv_snap_*`` backups are judged separately.
    max_uvs: int = 2
    name: str = "Standard"
    texture_compression: str = "BC7"  # BC7, ASTC, None
    adaptive_tris: bool = False
    # World-space bounding-box diagonal (cm) at which ``max_tris`` applies.
    reference_diag: float = 200.0
    min_tris: int = 500  # Floor for adaptive budget
    # Scene-wide texture memory, as block-compressed GPU megabytes (mips
    # included) -- the estimate a game build lands near.
    max_texture_mb: float = 512.0


# --------------------------------------------------------------- #
# Structured records returned by SceneAnalyzer.
# --------------------------------------------------------------- #
# Severity / kind strings are plain literals (no enum) so the
# dataclasses serialize cleanly via ``dataclasses.asdict`` for the
# machine-readable ``SceneReport.to_dict`` path. Callers can compare
# against the constants below for stable matching.
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"

#: Material transparency modes, cheapest first. ``masked`` is an alpha test
#: (cutout); ``blend`` is sorted, alpha-blended overdraw.
TRANSPARENCY_OPAQUE = "opaque"
TRANSPARENCY_MASKED = "masked"
TRANSPARENCY_BLEND = "blend"


@dataclass
class MeshRecord:
    """Per-mesh statistics for a single shape node."""

    shape_name: str
    tris: int
    verts: int
    uv_sets: int
    has_colors: bool
    instanced: bool
    # World-space bounding-box diagonal in centimeters (Maya's internal unit,
    # whatever the UI unit), of the LARGEST in-scope instance.
    bounds_diag: float
    uv_set_names: List[str] = field(default_factory=list)
    ngons: int = 0
    non_manifold_edges: int = 0
    lamina_faces: int = 0
    vertex_bytes: int = 0
    # ``_uv_snap_*`` backups an interrupted unwrap left behind (they ship as
    # real UV sets), and the set recognized as the lightmap, if any.
    uv_snapshot_sets: List[str] = field(default_factory=list)
    lightmap_uv_set: Optional[str] = None


@dataclass
class MaterialRecord:
    """Per-shape material usage summary (aggregated across slots)."""

    # Shading slots on the busiest in-scope instance (instances can be
    # shaded per-instance, so this is a max, not a union).
    slot_count: int
    uses_transparency: bool
    materials: List[str]
    texture_count: int = 0
    max_res: int = 0
    total_tex_size_mb: float = 0.0
    est_gpu_size_mb: float = 0.0
    unpacked_pbr: bool = False
    missing_textures: int = 0
    max_samplers: int = 0
    unique_paths_local: int = 0
    max_res_is_unique: bool = False
    # Draw calls across every in-scope instance: slots per instance, at least
    # one for any mesh that renders.
    draw_calls: int = 0
    # Strongest mode among the materials (TRANSPARENCY_*).
    transparency: str = TRANSPARENCY_OPAQUE
    # Most slots one instance spends on a material it already wears (two
    # shading engines of one material): each merges away for free.
    redundant_slots: int = 0


@dataclass
class Finding:
    """An observation about an asset (negative or risk-flagged)."""

    severity: str  # SEVERITY_LOW / SEVERITY_MEDIUM / SEVERITY_HIGH
    kind: str  # e.g. "high_poly", "ngons", "non_manifold", "uv_snapshots"
    message: str  # human-readable summary; data lives in ``detail``
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FixAction:
    """A recommended remediation step."""

    severity: str  # SEVERITY_LOW / SEVERITY_MEDIUM / SEVERITY_HIGH
    kind: str  # e.g. "decimate", "reduce_slots", "remove_uv_sets", "relink_textures"
    message: str
    target: Optional[str] = None  # transform path, material name, or texture path
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BudgetDelta:
    """How far an asset exceeds the profile budget along each axis."""

    tris_over: int = 0
    slots_over: int = 0
    uvs_over: int = 0
    max_tex_res_over: int = 0

    def is_over_budget(self) -> bool:
        return any(
            (
                self.tris_over > 0,
                self.slots_over > 0,
                self.uvs_over > 0,
                self.max_tex_res_over > 0,
            )
        )

    def summary(self) -> str:
        """Pre-rendered ``"tris +N | slots +M | …"`` string used by the
        text renderer. Data layer keeps the raw numbers so callers can
        sort / threshold / serialize without re-parsing."""
        parts: List[str] = []
        if self.tris_over:
            parts.append(f"tris +{self.tris_over}")
        if self.slots_over:
            parts.append(f"slots +{self.slots_over}")
        if self.uvs_over:
            parts.append(f"uv +{self.uvs_over}")
        if self.max_tex_res_over:
            parts.append(f"maxTex +{self.max_tex_res_over}")
        return " | ".join(parts)


@dataclass
class AssetRecord:
    """Combined per-asset record produced by analyze().

    One record per unique mesh SHAPE; ``instance_count`` is how many of its
    DAG paths (instances) are in scope and ``transforms`` names them.
    """

    transform: str
    mesh: MeshRecord
    material: MaterialRecord
    score: float = 0.0
    perf_score: float = 0.0
    risk_score: float = 0.0
    findings: List[Finding] = field(default_factory=list)
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    instance_count: int = 0
    tri_percent: float = 0.0
    delta: BudgetDelta = field(default_factory=BudgetDelta)
    fix_plan: List[FixAction] = field(default_factory=list)
    target_tris: int = 0
    transforms: List[str] = field(default_factory=list)


@dataclass
class ParetoEntry:
    """One row of a Pareto ranking (top contributor + cumulative %)."""

    target: str
    value: int  # raw count for the metric this list ranks (tris or slots)
    cum_percent: float  # cumulative percentage at this row


@dataclass
class TextureFile:
    """A texture file referenced by the scene, with usage stats."""

    path: str
    size_mb: float
    width: int
    height: int
    material_count: int
    materials: List[str]
    mesh_count: int
    instance_count: int
    # Canonical map type (``ptk.MapFactory`` taxonomy), or "" when neither the
    # filename nor the shader slot names one.
    map_type: str = ""
    # Block-compressed GPU estimate, mips included (ptk.MapRegistry.estimate_gpu_bytes).
    gpu_mb: float = 0.0
    tiles: int = 1  # files a UDIM / tile pattern expands to
    # "material" -- a surface map, counted; "other" -- not a surface map
    # (StingrayPBS's IBL cube maps and BRDF LUT, utility textures), listed but
    # not counted.
    role: str = "material"
    bundled: bool = False  # lives in Maya's install, not the project


@dataclass
class MissingTexture:
    """A texture referenced by a material but not present on disk."""

    path: str
    material_count: int
    materials: List[str]


@dataclass
class SharedTexture:
    """A texture used by more than one mesh."""

    path: str
    mesh_count: int


@dataclass
class MaterialSplit:
    """A material correlated with high-slot meshes (draw-call splits)."""

    material: str
    unique_mesh_count: int
    over_budget_count: int
    avg_slots: float


@dataclass
class MaterialAudit:
    """One material as the scope uses it: who wears it and what it costs.

    Texture costs are paid once per material (per file, really), not once per
    mesh -- so they are judged here rather than repeated on every asset that
    happens to wear the material.
    """

    name: str
    node_type: str = ""
    shading_engines: List[str] = field(default_factory=list)
    mesh_count: int = 0  # unique shapes in scope wearing it
    instance_count: int = 0  # in-scope instances wearing it
    transparency: str = TRANSPARENCY_OPAQUE
    textures: List[str] = field(default_factory=list)  # surface maps (resolved)
    map_types: List[str] = field(default_factory=list)
    max_res: int = 0
    disk_mb: float = 0.0
    gpu_mb: float = 0.0
    missing: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)


@dataclass
class SceneOverview:
    """Scene-wide facts, independent of the audit scope: the file, its units
    and time setup, and what the DAG / DG holds."""

    scene_path: str = ""  # "" for a scene that was never saved
    file_size_mb: float = 0.0
    linear_unit: str = ""
    up_axis: str = ""
    fps: float = 0.0
    playback_range: Tuple[float, float] = (0.0, 0.0)
    animation_range: Tuple[float, float] = (0.0, 0.0)
    # Node census, e.g. {"transforms": 2727, "mesh_shapes": 486, "joints": 378}.
    counts: Dict[str, int] = field(default_factory=dict)
    namespaces: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    unloaded_references: List[str] = field(default_factory=list)
    unknown_plugins: List[str] = field(default_factory=list)
    unknown_nodes: int = 0


@dataclass
class SlotStats:
    """Distribution stats for material slots-per-mesh."""

    avg: float
    avg_unique: float
    median: int
    p90: int
    max: int


@dataclass
class InstanceStats:
    """Mesh / instance counts."""

    unique_meshes: int
    instanced_shapes: int
    total_instances: int


@dataclass
class BudgetBuckets:
    """Histogram of overage severity per dimension."""

    # Bucket label strings are stable: "0-10%" / "10-50%" / "50%+" for
    # tris, "1-2" / "3-5" / "6+" for slots. Kept as Dict here because
    # the labels also drive UI rendering — promoting to a dataclass
    # would just duplicate them.
    tris: Dict[str, int] = field(default_factory=dict)
    slots: Dict[str, int] = field(default_factory=dict)


@dataclass
class ComplianceStats:
    """Percentage of scene over budget per dimension."""

    tris_pct: float = 0.0
    slots_pct: float = 0.0


@dataclass
class MissingTextureImpact:
    """Downstream effect of missing textures on the asset list."""

    affected_meshes: List[str] = field(default_factory=list)
    affected_materials: List[str] = field(default_factory=list)
    top_offenders: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.affected_meshes


@dataclass
class SummaryStats:
    """High-level scene counters surfaced by the Executive Summary."""

    total_meshes: int = 0
    total_tris: int = 0
    total_verts: int = 0
    raw_total_tris: int = 0
    instance_stats: InstanceStats = field(
        default_factory=lambda: InstanceStats(0, 0, 0)
    )
    scene_health_flags: List[str] = field(default_factory=list)
    multi_slot_meshes: int = 0
    transparent_meshes: int = 0
    non_manifold_count: int = 0
    lamina_count: int = 0
    ngon_count: int = 0
    high_poly_count: int = 0
    meshes_with_transparency: int = 0
    meshes_with_extra_uvs: int = 0
    meshes_with_high_slots: int = 0
    draw_calls: int = 0
    materials_in_use: int = 0
    uv_snapshot_meshes: int = 0
    unassigned_meshes: int = 0
    blend_meshes: int = 0
    masked_meshes: int = 0


@dataclass
class BudgetStats:
    """Budget / compliance / savings figures."""

    total_target_tris: int = 0
    total_slots: int = 0
    meshes_over_tri_threshold: int = 0
    meshes_over_slot_threshold: int = 0
    total_slots_over_budget: int = 0
    savings_draw_calls_total: int = 0
    savings_tris_total: int = 0
    savings_draw_calls_budget: int = 0
    savings_tris_budget: int = 0
    slot_stats: Optional[SlotStats] = None
    compliance: ComplianceStats = field(default_factory=ComplianceStats)
    buckets: BudgetBuckets = field(default_factory=BudgetBuckets)


@dataclass
class TextureStats:
    """Texture-side aggregates (surface maps only; see ``other``)."""

    total_size_mb: float = 0.0
    est_gpu_mb: float = 0.0
    est_gpu_mb_compressed: float = 0.0
    max_resolution: int = 0
    large_texture_count: int = 0
    unique_paths: int = 0
    dim_histogram: Dict[str, int] = field(default_factory=dict)
    type_breakdown: Dict[str, float] = field(default_factory=dict)
    class_estimates: Dict[str, float] = field(default_factory=dict)
    shared_4k: List[SharedTexture] = field(default_factory=list)
    single_use_4k_count: int = 0
    shared_4k_count: int = 0
    heaviest: List[TextureFile] = field(default_factory=list)
    budget_mb: float = 0.0
    # Maps a half-resolution copy would serve: 4K+ and not resolution-critical
    # in the map taxonomy (AO / roughness / metallic ...), and what halving
    # them all would free.
    downscale_candidates: int = 0
    downscale_savings_mb: float = 0.0
    # Referenced but not surface maps -- listed, never counted.
    other: List[TextureFile] = field(default_factory=list)


@dataclass
class PipelineStats:
    """Pipeline integrity findings (missing textures + impact)."""

    integrity_warnings: List[str] = field(default_factory=list)
    missing_project: List[MissingTexture] = field(default_factory=list)
    missing_presets: List[MissingTexture] = field(default_factory=list)
    impact: MissingTextureImpact = field(default_factory=MissingTextureImpact)
    unassigned_meshes: List[str] = field(default_factory=list)


@dataclass
class OffenderLists:
    """Top-N rankings across various dimensions.

    All lists are slices of the same underlying ``assets`` collection
    re-sorted and truncated. ``by_effective_score`` replaces the
    former ``top_repeated_offenders`` / ``top_by_effective_score``
    alias pair — they were the same list under two names.
    """

    by_score: List[AssetRecord] = field(default_factory=list)
    by_tris: List[AssetRecord] = field(default_factory=list)
    by_slots: List[AssetRecord] = field(default_factory=list)
    by_max_res: List[AssetRecord] = field(default_factory=list)
    by_risk: List[AssetRecord] = field(default_factory=list)
    by_transparency: List[AssetRecord] = field(default_factory=list)
    by_effective_score: List[AssetRecord] = field(default_factory=list)
    top_materials: List[Tuple[str, int]] = field(default_factory=list)
    savings_draw_calls: List[AssetRecord] = field(default_factory=list)
    savings_tris: List[AssetRecord] = field(default_factory=list)
    pareto_tris: List[ParetoEntry] = field(default_factory=list)
    pareto_slots: List[ParetoEntry] = field(default_factory=list)
    materials_causing_splits: List[MaterialSplit] = field(default_factory=list)


@dataclass
class AnalysisManifest:
    """What was analyzed, how, and how long it took.

    Surfaces ``analyze()``-time observability: the requested section
    set, the scope, which collectors actually ran, and timings /
    counts so callers can correlate a SceneReport with the run that
    produced it.
    """

    scope: str  # "selection" | "all" | "custom"
    sections_requested: List[str] = field(default_factory=list)
    materials_collected: bool = True
    textures_collected: bool = True
    # Topology / UV checks (SceneInfoSection._NEEDS_MESH_CHECKS) ran.
    mesh_checks_collected: bool = True
    profile: AuditProfile = field(default_factory=AuditProfile)
    started_at: float = 0.0  # unix timestamp
    duration_ms: int = 0
    shape_count: int = 0
    shading_engine_count: int = 0
    file_node_count: int = 0


@dataclass
class SceneReport:
    """Top-level result of ``SceneAnalyzer.generate_report``.

    Groups related metrics into typed sub-records (it replaced a single
    70-field bag) and exposes a machine-readable export via :meth:`to_dict`.
    ``overview`` is scene-wide; everything else covers the audited scope.
    """

    manifest: AnalysisManifest
    summary: SummaryStats = field(default_factory=SummaryStats)
    budget: BudgetStats = field(default_factory=BudgetStats)
    textures: TextureStats = field(default_factory=TextureStats)
    pipeline: PipelineStats = field(default_factory=PipelineStats)
    offenders: OffenderLists = field(default_factory=OffenderLists)
    fix_actions: List[FixAction] = field(default_factory=list)
    assets: List[AssetRecord] = field(default_factory=list)
    materials: List[MaterialAudit] = field(default_factory=list)
    overview: Optional[SceneOverview] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the report to a nested plain-dict tree.

        Suitable for JSON / pickle / dashboard ingestion. Nested
        dataclasses (including AssetRecord and its mesh/material
        children) are converted recursively via
        :func:`dataclasses.asdict`. ``profile`` flattens to its own
        dict; sets become lists when present (``MissingTextureImpact``
        keeps lists internally, so no conversion required).
        """
        from dataclasses import asdict

        return asdict(self)


class SceneInfoSection:
    """Report-section identifiers used to gate analyze() work and report output.

    ``ALL`` is the canonical render order. The dependency sets drive
    ``SceneAnalyzer.analyze`` so that unchecked sections skip the
    corresponding collection phase (material caches, texture file IO).
    """

    OVERVIEW = "overview"
    SUMMARY = "summary"
    FIX_FIRST = "fix_first"
    PARETO = "pareto"
    OFFENDERS = "offenders"
    MATERIALS = "materials"
    TEXTURES = "textures"
    PIPELINE = "pipeline"
    ASSUMPTIONS = "assumptions"
    #: RETIRED (until 2026-09-24) -- the section was a five-row "materials
    #: correlated with high-slot meshes" table; :attr:`MATERIALS` replaced it.
    #: :meth:`normalize` still resolves it, warning until 0.21.0.
    CATEGORIES = "categories"

    ALL: Tuple[str, ...] = (
        OVERVIEW,
        SUMMARY,
        FIX_FIRST,
        PARETO,
        OFFENDERS,
        MATERIALS,
        TEXTURES,
        PIPELINE,
        ASSUMPTIONS,
    )

    LABELS: Dict[str, str] = {
        OVERVIEW: "Scene Overview",
        SUMMARY: "Executive Summary",
        FIX_FIRST: "Fix First",
        PARETO: "Top Contributors",
        OFFENDERS: "Top Issues by Asset",
        MATERIALS: "Materials",
        TEXTURES: "Textures",
        PIPELINE: "Pipeline Integrity",
        ASSUMPTIONS: "Notes & Assumptions",
    }

    # Material-cache phase is needed for anything that touches slots,
    # transparency, draw-call estimates or texture aggregates.
    _NEEDS_MATERIALS: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        PARETO,
        OFFENDERS,
        MATERIALS,
        TEXTURES,
        PIPELINE,
    }

    # Texture file IO (a header read + a size stat per unique file) only runs
    # when a requested section surfaces texture data -- the offenders' too: a
    # mesh's oversized unique texture set is one of its issues.
    _NEEDS_TEXTURES: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        OFFENDERS,
        MATERIALS,
        TEXTURES,
        PIPELINE,
    }

    # Per-mesh topology and UV checks (n-gons, non-manifold edges, lamina
    # faces, leftover UV snapshots, UV-set counts) -- counts and sizes are
    # measured regardless.
    _NEEDS_MESH_CHECKS: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        OFFENDERS,
        PIPELINE,
    }

    _resolve_retired = staticmethod(
        ptk.Deprecation.values(
            {"categories": "materials"},
            what="SceneInfoSection key",
            remove_in="0.21.0",
            since="2026-09-24",
            reason="The Materials section lists every material with its cost.",
        )
    )

    @classmethod
    def normalize(cls, sections: Optional[List[str]]) -> List[str]:
        """Coerce a caller-supplied sections argument to a stable,
        de-duped list of valid keys.

        ``None`` expands to all sections in :attr:`ALL` order. Unknown
        keys are dropped silently — that matches "best effort"
        semantics and means a downstream UI can pass through whatever
        the user picked without pre-filtering. A retired key resolves to
        its replacement (with a deprecation warning).

        Caller order is preserved so an option-box exposing section
        reordering would Just Work without touching this code.
        """
        if sections is None:
            return list(cls.ALL)
        valid = set(cls.ALL)
        seen: Set[str] = set()
        out: List[str] = []
        for key in sections:
            key = cls._resolve_retired(key)
            if key in valid and key not in seen:
                out.append(key)
                seen.add(key)
        return out
