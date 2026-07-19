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


@dataclass
class AuditProfile:
    """Thresholds for scene analysis."""

    max_tris: int = 20000
    max_slots: int = 4
    max_tex_res: int = 4096
    max_uvs: int = 1
    name: str = "Standard"
    texture_compression: str = "BC7"  # BC7, ASTC, None
    adaptive_tris: bool = False
    reference_diag: float = 200.0  # Size in units where max_tris applies
    min_tris: int = 500  # Floor for adaptive budget


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


@dataclass
class MeshRecord:
    """Per-mesh statistics for a single shape node."""

    shape_name: str
    tris: int
    verts: int
    uv_sets: int
    has_colors: bool
    instanced: bool
    bounds_diag: float
    uv_set_names: List[str] = field(default_factory=list)
    ngons: int = 0
    non_manifold_edges: int = 0
    lamina_faces: int = 0
    vertex_bytes: int = 0


@dataclass
class MaterialRecord:
    """Per-shape material usage summary (aggregated across slots)."""

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


@dataclass
class Finding:
    """An observation about an asset (negative or risk-flagged)."""

    severity: str  # SEVERITY_LOW / SEVERITY_MEDIUM / SEVERITY_HIGH
    kind: str      # e.g. "high_poly", "vert_bloat", "ngons", "non_manifold", "extra_uv_sets"
    message: str   # human-readable summary; data lives in ``detail``
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FixAction:
    """A recommended remediation step."""

    severity: str  # SEVERITY_LOW / SEVERITY_MEDIUM / SEVERITY_HIGH
    kind: str      # e.g. "decimate", "reduce_slots", "remove_uv_sets", "relink_textures"
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
    """Combined per-asset record produced by analyze()."""

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


@dataclass
class ParetoEntry:
    """One row of a Pareto ranking (top contributor + cumulative %)."""

    target: str
    value: int          # raw count for the metric this list ranks (tris or slots)
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
    """Texture-side aggregates."""

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


@dataclass
class PipelineStats:
    """Pipeline integrity findings (missing textures + impact)."""

    integrity_warnings: List[str] = field(default_factory=list)
    missing_project: List[MissingTexture] = field(default_factory=list)
    missing_presets: List[MissingTexture] = field(default_factory=list)
    impact: MissingTextureImpact = field(default_factory=MissingTextureImpact)


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
    profile: AuditProfile = field(default_factory=AuditProfile)
    started_at: float = 0.0  # unix timestamp
    duration_ms: int = 0
    shape_count: int = 0
    shading_engine_count: int = 0
    file_node_count: int = 0


@dataclass
class SceneReport:
    """Top-level result of ``SceneAnalyzer.generate_report``.

    Replaces the legacy ``SceneOverview`` mega-dataclass. Groups
    related metrics into typed sub-records and exposes a
    machine-readable export via :meth:`to_dict`.
    """

    manifest: AnalysisManifest
    summary: SummaryStats = field(default_factory=SummaryStats)
    budget: BudgetStats = field(default_factory=BudgetStats)
    textures: TextureStats = field(default_factory=TextureStats)
    pipeline: PipelineStats = field(default_factory=PipelineStats)
    offenders: OffenderLists = field(default_factory=OffenderLists)
    fix_actions: List[FixAction] = field(default_factory=list)
    assets: List[AssetRecord] = field(default_factory=list)

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

    SUMMARY = "summary"
    FIX_FIRST = "fix_first"
    PARETO = "pareto"
    OFFENDERS = "offenders"
    CATEGORIES = "categories"
    TEXTURES = "textures"
    PIPELINE = "pipeline"
    ASSUMPTIONS = "assumptions"

    ALL: Tuple[str, ...] = (
        SUMMARY,
        FIX_FIRST,
        PARETO,
        OFFENDERS,
        CATEGORIES,
        TEXTURES,
        PIPELINE,
        ASSUMPTIONS,
    )

    LABELS: Dict[str, str] = {
        SUMMARY: "Executive Summary",
        FIX_FIRST: "Fix First (High Impact)",
        PARETO: "Pareto View",
        OFFENDERS: "Top Issues by Asset",
        CATEGORIES: "Top Offenders by Category",
        TEXTURES: "Textures",
        PIPELINE: "Pipeline Integrity",
        ASSUMPTIONS: "Data Assumptions",
    }

    # Material-cache phase is needed for anything that touches slots,
    # transparency, draw-call estimates or texture aggregates.
    _NEEDS_MATERIALS: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        PARETO,
        OFFENDERS,
        CATEGORIES,
        TEXTURES,
        PIPELINE,
    }

    # Texture file IO (os.path.getsize + cmds.getAttr outSize per file
    # node) is the heaviest single cost; only walk it if the sections
    # the caller asked for actually surface that data.
    _NEEDS_TEXTURES: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        TEXTURES,
        PIPELINE,
    }

    @classmethod
    def normalize(cls, sections: Optional[List[str]]) -> List[str]:
        """Coerce a caller-supplied sections argument to a stable,
        de-duped list of valid keys.

        ``None`` expands to all sections in :attr:`ALL` order. Unknown
        keys are dropped silently — that matches "best effort"
        semantics and means a downstream UI can pass through whatever
        the user picked without pre-filtering.

        Caller order is preserved so an option-box exposing section
        reordering would Just Work without touching this code.
        """
        if sections is None:
            return list(cls.ALL)
        valid = set(cls.ALL)
        seen: Set[str] = set()
        out: List[str] = []
        for key in sections:
            if key in valid and key not in seen:
                out.append(key)
                seen.add(key)
        return out
