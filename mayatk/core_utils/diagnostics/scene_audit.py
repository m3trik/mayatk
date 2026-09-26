# !/usr/bin/python
# coding=utf-8
"""Scene audit engine — game-readiness analysis over meshes, materials, and textures.

The audit surface is split across three sibling modules, one responsibility each:

- ``audit_records`` — the Maya-free data contract (:class:`AuditProfile`,
  per-asset records, :class:`SceneReport`, :class:`SceneInfoSection`).
- ``scene_audit`` (this module) — the :class:`SceneAnalyzer` engine that
  collects, scores, and renders those records.
- ``scene_diag`` — scene *repair* helpers (:class:`SceneDiagnostics`).

Public entry points: :meth:`SceneAnalyzer.run_audit` (analyze + print),
:meth:`SceneAnalyzer.format_audit_text` / :meth:`SceneAnalyzer.format_audit_html`
(section-keyed output for UIs), and the two-phase :meth:`SceneAnalyzer.analyze` +
:meth:`SceneAnalyzer.generate_report` API. Each report section is built once as a
``ptk.ReportDoc`` and rendered either as HTML (the viewer) or as plain text.
"""

from __future__ import annotations

import os
import math
import time
from typing import List, Dict, Optional, Set, Any, Tuple, Callable, Iterable

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except Exception:  # Maya-free import: registry, docs tooling, mock tests
    cmds = om = None
import pythontk as ptk

from mayatk.core_utils.diagnostics.audit_records import (
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SEVERITY_HIGH,
    TRANSPARENCY_OPAQUE,
    TRANSPARENCY_MASKED,
    TRANSPARENCY_BLEND,
    AnalysisManifest,
    AssetRecord,
    AuditProfile,
    BudgetBuckets,
    BudgetDelta,
    BudgetStats,
    ComplianceStats,
    Finding,
    FixAction,
    InstanceStats,
    MaterialAudit,
    MaterialRecord,
    MaterialSplit,
    MeshRecord,
    MissingTexture,
    MissingTextureImpact,
    OffenderLists,
    ParetoEntry,
    PipelineStats,
    SceneInfoSection,
    SceneOverview,
    SceneReport,
    SharedTexture,
    SlotStats,
    SummaryStats,
    TextureFile,
    TextureStats,
)

Doc = ptk.ReportDoc


class SceneAnalyzer(ptk.LoggingMixin):
    """Analyzes scene objects for performance expectations in game engines.

    Collection is bulk and instance-aware: each unique mesh SHAPE is measured
    once and weighted by how many of its instances are in scope, and each
    material and texture file is read once however many meshes share it.
    """

    #: Scopes :meth:`analyze` resolves itself when it is given no ``objects``.
    SCOPES = ("selection", "all")

    #: Shading engines every Maya scene carries; not the scene's own.
    _DEFAULT_SHADING_ENGINES = ("initialShadingGroup", "initialParticleSE")

    #: Surface-map types that pack into one ORM texture when authored apart.
    _LOOSE_PBR_TYPES = ("Ambient_Occlusion", "Roughness", "Metallic")

    #: Texels a map needs per meter of an object's world diagonal before it is
    #: judged oversized for that object (a 2 m prop -> ~1K).
    TEXELS_PER_METER = 512

    #: Rows a ranked table shows before folding the rest into a footer.
    TABLE_ROWS = 12

    def __init__(self):
        super().__init__()
        self.logger.hide_logger_name(True)
        # Representative shape path -> {"paths": in-scope instance shape paths,
        # "transforms": the matching instance transforms}.
        self._targets: Dict[str, Dict[str, List[str]]] = {}
        self._path_owner: Dict[str, str] = {}  # instance shape path -> representative
        self._shading_map: Dict[str, Set[str]] = {}  # representative -> SEs (union)
        self._path_shading: Dict[str, Set[str]] = {}  # instance shape path -> SEs
        self._material_map: Dict[str, str] = {}  # shading engine -> material
        self._material_flags: Dict[str, Dict[str, Any]] = {}  # material -> flags
        self._texture_info: Dict[str, Dict[str, Any]] = {}  # texture key -> file facts
        self._raw_keys: Dict[str, str] = {}  # stored path -> its texture key
        # Scene-wide use of each texture file: mesh NODES, instance paths and
        # materials. Judges "is this texture unique to one mesh?".
        self._global_texture_usage: Dict[str, Dict[str, Set[str]]] = {}
        self._overview: Optional[SceneOverview] = None
        self.scope = "selection"
        self.profile: Any = AuditProfile()
        # Populated by ``analyze`` so renderers can hide sections /
        # lines whose underlying data was deliberately skipped.
        self.collected_sections: Set[str] = set(SceneInfoSection.ALL)
        self.materials_collected: bool = True
        self.textures_collected: bool = True
        self.mesh_checks_collected: bool = True
        # Observability — populated by ``analyze``. Surfaced via
        # :class:`AnalysisManifest` on the SceneReport.
        self._analysis_started_at: float = 0.0
        self._analysis_duration_ms: int = 0
        self._shading_engine_count: int = 0
        self._file_node_count: int = 0

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    @classmethod
    def run_audit(cls, adaptive: bool = False, verbose: bool = True) -> None:
        """
        Run a full scene audit and print the report.

        Args:
            adaptive: If True, use adaptive budgeting based on object size.
            verbose: If True, print the report to the script editor.
        """
        analyzer, report = cls._build_report(adaptive=adaptive)
        if verbose:
            analyzer.print_report(report)

    @staticmethod
    def _profile(adaptive: bool) -> AuditProfile:
        """The profile behind the Adaptive (Game Ready) / Generic choice."""
        profile = AuditProfile(adaptive_tris=adaptive)
        if adaptive:
            profile.name = "Adaptive (Game Ready)"
        else:
            profile.name = "Generic"
        return profile

    @classmethod
    def _build_report(
        cls,
        adaptive: bool,
        objects: Optional[List[Any]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        sections: Optional[List[str]] = None,
        scope: Optional[str] = None,
    ) -> Tuple["SceneAnalyzer", SceneReport]:
        """Run the analyze + generate_report pipeline once for the
        given audit settings, returning the (analyzer, report) pair.

        Shared by ``format_audit_text`` / ``format_audit_html`` so
        the heavy work only happens once per user-facing call
        regardless of which output shape they ask for. ``sections``
        is forwarded to ``analyze`` so the right collection phases
        are skipped.
        """
        analyzer = cls()
        records = analyzer.analyze(
            profile=cls._profile(adaptive),
            objects=objects,
            progress_callback=progress_callback,
            sections=sections,
            scope=scope,
        )
        report = analyzer.generate_report(records)
        return analyzer, report

    def _build_manifest(self, shape_count: int = 0) -> AnalysisManifest:
        """Snapshot of the most recent ``analyze`` run for the
        SceneReport. Pulls timing / counts from analyzer state and
        falls back to defaults when ``analyze`` was never called."""
        return AnalysisManifest(
            scope=self.scope,
            sections_requested=sorted(self.collected_sections),
            materials_collected=self.materials_collected,
            textures_collected=self.textures_collected,
            mesh_checks_collected=self.mesh_checks_collected,
            profile=self.profile
            if isinstance(self.profile, AuditProfile)
            else AuditProfile(),
            started_at=self._analysis_started_at,
            duration_ms=self._analysis_duration_ms,
            shape_count=shape_count,
            shading_engine_count=self._shading_engine_count,
            file_node_count=self._file_node_count,
        )

    @classmethod
    def format_audit_text(
        cls,
        adaptive: bool = False,
        objects: Optional[List[Any]] = None,
        sections: Optional[List[str]] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, str]:
        """Run the audit and return the formatted report as a
        section-keyed dict of plain text.

        Sibling to :meth:`run_audit` — same analysis, returned as
        per-section strings instead of printed. Used by callers that
        want to display (or partially display) the report somewhere
        other than the script editor.

        Parameters:
            adaptive: Apply the Adaptive (Game Ready) profile.
            objects: Forwarded to :meth:`analyze`. ``None`` resolves
                *scope* (the current selection by default).
            sections: Iterable of ``SceneInfoSection`` keys. ``None``
                means "all sections".
            scope: Forwarded to :meth:`analyze`.

        Returns:
            ``dict[str, str]`` keyed by section name (insertion order
            matches the requested ``sections``). A special ``"_header"``
            entry holds the report title and its context line. Every
            requested section is present; one with nothing to render
            maps to an empty string.
        """
        return cls._format_audit("to_text", adaptive, objects, None, sections, scope)

    @classmethod
    def format_audit_html(
        cls,
        adaptive: bool = False,
        objects: Optional[List[Any]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        sections: Optional[List[str]] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, str]:
        """Run the audit and return a section-keyed dict of HTML
        chunks suitable for concatenation into a viewer dialog.

        Each section is real HTML (headings, tables, key/value grids)
        built by ``ptk.ReportDoc``: every scene-derived string is
        escaped, object and material names are ``action://select``
        links (a viewer given ``mayatk.UiUtils.dispatch_log_link``
        selects them), and texture files are ``file:///`` links.

        Parameters:
            adaptive: Apply the Adaptive (Game Ready) profile.
            objects: Forwarded to :meth:`analyze`.
            progress_callback: Forwarded to :meth:`analyze`.
            sections: Iterable of ``SceneInfoSection`` keys. ``None``
                means "all sections".
            scope: Forwarded to :meth:`analyze`.

        Returns:
            ``dict[str, str]`` keyed by section name (insertion order
            matches the requested ``sections``). A special
            ``"_header"`` entry holds the title; the tentacle viewer
            joins values in iteration order.
        """
        return cls._format_audit(
            "to_html", adaptive, objects, progress_callback, sections, scope
        )

    @classmethod
    def _format_audit(
        cls, render, adaptive, objects, progress_callback, sections, scope
    ) -> Dict[str, str]:
        """Shared body of :meth:`format_audit_text` / :meth:`format_audit_html`."""
        selected = SceneInfoSection.normalize(sections)
        analyzer, report = cls._build_report(
            adaptive=adaptive,
            objects=objects,
            progress_callback=progress_callback,
            sections=selected,
            scope=scope,
        )
        docs = analyzer._section_docs(report, selected)
        result: Dict[str, str] = {
            "_header": getattr(analyzer._doc_header(report), render)()
        }
        for section in selected:
            doc = docs.get(section)
            result[section] = getattr(doc, render)() if doc else ""
        return result

    def analyze(
        self,
        objects: List[Any] = None,
        fast_mode: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        profile: AuditProfile = None,
        sections: Optional[List[str]] = None,
        scope: Optional[str] = None,
    ) -> List[AssetRecord]:
        """
        Main entry point for analysis.

        Args:
            objects: Objects to analyze -- transforms, groups (their mesh
                descendants), mesh shapes, components (their mesh) or object
                sets (their members). ``None`` resolves *scope* instead.
            fast_mode: Reserved.
            progress_callback: Optional callback(current, total, message).
            profile: Target profile settings.
            sections: Iterable of ``SceneInfoSection`` keys controlling
                which report sections will be rendered. The analyzer
                uses the set to skip work the unselected sections don't
                need — the material caches, the texture file reads and the
                mesh checks. (The scene overview is always read: it is cheap,
                the header names the scene, and Fix First / Pipeline read its
                unknown nodes.) ``None`` means "all sections".
            scope: Used only when *objects* is None: ``"selection"``
                (default) or ``"all"`` -- every mesh instance in the scene.

        Returns:
            List of AssetRecord objects (one per unique mesh shape) sorted
            by score (descending).
        """
        if profile is None:
            profile = AuditProfile()
        self.profile = profile
        if objects is not None:
            self.scope = "custom"
        else:
            self.scope = scope if scope in self.SCOPES else "selection"

        # Normalize via the canonical helper so a typo'd or
        # alternately-ordered list from a direct caller gets the same
        # filtering as the format_audit_* paths.
        selected_sections = set(SceneInfoSection.normalize(sections))
        needs_materials = bool(selected_sections & SceneInfoSection._NEEDS_MATERIALS)
        needs_textures = bool(selected_sections & SceneInfoSection._NEEDS_TEXTURES)
        needs_checks = bool(selected_sections & SceneInfoSection._NEEDS_MESH_CHECKS)
        # Recorded on the analyzer so generate_report / renderers can
        # hide texture/material lines that have no underlying data.
        self.collected_sections = selected_sections
        self.materials_collected = needs_materials
        self.textures_collected = needs_textures
        self.mesh_checks_collected = needs_checks

        # Observability — reset per-run counters; the AnalysisManifest
        # on the eventual SceneReport reads these.
        self._analysis_started_at = time.time()
        self._shading_engine_count = 0
        self._file_node_count = 0
        _start_perf = time.perf_counter()

        def tick(pct, message):
            if progress_callback:
                progress_callback(pct, 100, message)

        # Scene-wide facts first: cheap (a census of batched queries) and
        # independent of the scope. Always collected -- the header names the
        # scene, and Fix First / Pipeline read its unknown nodes.
        tick(0, "Reading scene overview...")
        self._overview = self._collect_overview()

        # Phase A: resolve targets (a handful of batched queries).
        PHASE_A_END = 5
        tick(1, "Resolving targets...")
        shape_map = self._resolve_targets(objects)
        self._clear_caches()
        if not shape_map:
            self._analysis_duration_ms = int((time.perf_counter() - _start_perf) * 1000)
            return []

        shapes = list(shape_map.keys())
        total_shapes = len(shapes)

        # Weight the Phase B/C split by item counts so the bar tracks
        # wall-clock progress: Phase B walks every shading engine in the
        # scene, Phase C every unique shape in scope.
        phase_b_count = (
            len(cmds.ls(type="shadingEngine") or []) if needs_materials else 0
        )
        phase_b_end = self._phase_b_end(PHASE_A_END, phase_b_count, total_shapes)

        # Phase B: bulk-collect material data (skipped entirely when no
        # selected section needs slot / transparency / texture data).
        if needs_materials:
            tick(PHASE_A_END, "Collecting material data...")
            self._build_material_caches(
                shape_map,
                progress_callback=progress_callback,
                pct_start=PHASE_A_END,
                pct_end=phase_b_end,
                collect_textures=needs_textures,
            )
        else:
            phase_b_end = PHASE_A_END  # give Phase B's bar range back to C

        # Phase C: analyze and score each unique shape.
        records = []
        phase_c_span = max(1, 100 - phase_b_end)
        for i, shape in enumerate(shapes):
            tick(
                phase_b_end + int((i / total_shapes) * phase_c_span),
                f"Analyzing {shape.rsplit('|', 1)[-1]} ({i + 1}/{total_shapes})",
            )
            paths = self._targets[shape]["paths"]
            mesh_rec = self._analyze_mesh(shape, paths, checks=needs_checks)
            mat_rec = (
                self._analyze_material(shape)
                if needs_materials
                else MaterialRecord(
                    slot_count=0,
                    uses_transparency=False,
                    materials=[],
                    draw_calls=len(paths),
                )
            )

            (
                score,
                perf_score,
                risk_score,
                findings,
                breakdown,
                delta,
                fix_plan,
                target_tris,
            ) = self._calculate_score(mesh_rec, mat_rec)

            transforms = shape_map[shape]
            records.append(
                AssetRecord(
                    transform=transforms[0] if transforms else shape,
                    mesh=mesh_rec,
                    material=mat_rec,
                    score=score,
                    perf_score=perf_score,
                    risk_score=risk_score,
                    findings=findings,
                    score_breakdown=breakdown,
                    instance_count=len(transforms),
                    delta=delta,
                    fix_plan=fix_plan,
                    target_tris=target_tris,
                    transforms=list(transforms),
                )
            )

        # Post-process: each asset's share of the rendered triangles.
        total_tris = sum(r.mesh.tris * r.instance_count for r in records)
        if total_tris > 0:
            for r in records:
                r.tri_percent = ((r.mesh.tris * r.instance_count) / total_tris) * 100.0

        records.sort(key=lambda x: x.score, reverse=True)
        tick(100, "Done")
        self._analysis_duration_ms = int((time.perf_counter() - _start_perf) * 1000)
        return records

    # ------------------------------------------------------------------ #
    # Report assembly
    # ------------------------------------------------------------------ #
    def generate_report(self, records: List[AssetRecord]) -> SceneReport:
        """Build a :class:`SceneReport` from per-asset records.

        Scene totals weight each unique shape by its in-scope instances;
        texture costs are counted once per FILE across the materials the
        records wear, so a map shared by 450 meshes costs what one copy
        costs.
        """
        if not records:
            report = SceneReport(
                manifest=self._build_manifest(), overview=self._overview
            )
            # No mesh in scope -- the scene's own hazards still apply (its
            # geometry may be the unloaded reference that left the scope empty).
            report.fix_actions = self._scene_fix_actions(report)
            return report

        profile = self.profile
        total_meshes = len(records)
        total_instances = sum(r.instance_count for r in records)
        total_tris = sum(r.mesh.tris * r.instance_count for r in records)
        total_verts = sum(r.mesh.verts * r.instance_count for r in records)
        total_slots = sum(r.material.slot_count * r.instance_count for r in records)
        draw_calls = sum(r.material.draw_calls for r in records)
        max_slots = max((r.material.slot_count for r in records), default=0)
        avg_slots = total_slots / total_instances if total_instances else 0.0

        multi_slot_meshes = sum(1 for r in records if r.material.slot_count > 1)
        meshes_over_slot_threshold = sum(
            1 for r in records if r.material.slot_count > profile.max_slots
        )
        high_poly = [r for r in records if r.mesh.tris > r.target_tris]
        total_slots_over_budget = sum(
            max(0, r.material.slot_count - profile.max_slots) * r.instance_count
            for r in records
        )

        # --- materials + textures (scope = what the records wear) -------------
        materials = self._material_audits(records)
        used_materials = {m.name for m in materials}
        textures = self._texture_stats(used_materials)

        # --- missing textures ------------------------------------------------
        missing_map: Dict[str, Set[str]] = {}
        for mat_name in used_materials:
            for path in self._material_flags.get(mat_name, {}).get("missing_paths", []):
                missing_map.setdefault(path, set()).add(mat_name)
        from mayatk.mat_utils._mat_utils import MatUtils

        missing_project: List[MissingTexture] = []
        missing_presets: List[MissingTexture] = []
        for path, mats in sorted(missing_map.items(), key=lambda kv: -len(kv[1])):
            entry = MissingTexture(
                path=path, material_count=len(mats), materials=sorted(mats)
            )
            (
                missing_presets
                if MatUtils.is_bundled_texture(path)
                else missing_project
            ).append(entry)

        impact_meshes: List[str] = []
        impact_materials: Set[str] = set()
        if missing_map:
            for r in records:
                hit = [
                    m
                    for m in r.material.materials
                    if m in used_materials
                    and any(m in mats for mats in missing_map.values())
                ]
                if hit:
                    impact_meshes.append(r.transform)
                    impact_materials.update(hit)
        impact = MissingTextureImpact(
            affected_meshes=sorted(impact_meshes),
            affected_materials=sorted(impact_materials),
            top_offenders=impact_meshes[:5],
        )

        # Not collected is not "unassigned".
        unassigned = (
            [r.transform for r in records if not r.material.materials]
            if self.materials_collected
            else []
        )
        pipeline_warnings = []
        if missing_project:
            pipeline_warnings.append(f"{len(missing_project)} missing project files")
        if missing_presets:
            pipeline_warnings.append(
                f"{len(missing_presets)} missing preset files (low priority)"
            )
        if unassigned:
            pipeline_warnings.append(f"{len(unassigned)} meshes without a material")
        pipeline = PipelineStats(
            integrity_warnings=pipeline_warnings,
            missing_project=missing_project,
            missing_presets=missing_presets,
            impact=impact,
            unassigned_meshes=unassigned,
        )

        # --- rankings ----------------------------------------------------------
        def eff_score(r):
            return r.score * max(1, r.instance_count)

        top_by_effective = sorted(records, key=eff_score, reverse=True)
        mat_usage: Dict[str, int] = {}
        for r in records:
            for mat in r.material.materials:
                mat_usage[mat] = mat_usage.get(mat, 0) + max(1, r.instance_count)

        savings_dc = [r for r in records if r.material.slot_count > 1]
        savings_tris = high_poly

        pareto_tris: List[ParetoEntry] = []
        running = 0
        for r in sorted(
            records, key=lambda r: r.mesh.tris * r.instance_count, reverse=True
        )[:10]:
            eff = r.mesh.tris * r.instance_count
            running += eff
            pareto_tris.append(
                ParetoEntry(
                    target=r.transform,
                    value=eff,
                    cum_percent=(running / total_tris * 100.0) if total_tris else 0.0,
                )
            )
        pareto_slots: List[ParetoEntry] = []
        running = 0
        for r in sorted(records, key=lambda r: r.material.draw_calls, reverse=True)[
            :10
        ]:
            running += r.material.draw_calls
            pareto_slots.append(
                ParetoEntry(
                    target=r.transform,
                    value=r.material.draw_calls,
                    cum_percent=(running / draw_calls * 100.0) if draw_calls else 0.0,
                )
            )

        splits: Dict[str, Dict[str, Any]] = {}
        for r in records:
            for m in r.material.materials:
                s = splits.setdefault(m, {"unique": 0, "over": 0, "slots": []})
                s["unique"] += 1
                s["slots"].append(r.material.slot_count)
                if r.material.slot_count > profile.max_slots:
                    s["over"] += 1
        materials_causing_splits = sorted(
            (
                MaterialSplit(
                    material=m,
                    unique_mesh_count=s["unique"],
                    over_budget_count=s["over"],
                    avg_slots=sum(s["slots"]) / len(s["slots"]),
                )
                for m, s in splits.items()
                if sum(s["slots"]) / len(s["slots"]) >= 4 or s["over"] > 5
            ),
            key=lambda s: s.unique_mesh_count,
            reverse=True,
        )[:5]

        offenders = OffenderLists(
            by_score=records[:20],
            by_tris=sorted(records, key=lambda x: x.mesh.tris, reverse=True)[:10],
            by_slots=sorted(records, key=lambda x: x.material.slot_count, reverse=True)[
                :10
            ],
            by_max_res=sorted(records, key=lambda x: x.material.max_res, reverse=True)[
                :10
            ],
            by_risk=sorted(records, key=lambda x: x.risk_score, reverse=True)[:10],
            by_transparency=sorted(
                (r for r in records if r.material.uses_transparency),
                key=lambda x: x.score,
                reverse=True,
            )[:10],
            by_effective_score=top_by_effective[:10],
            top_materials=sorted(mat_usage.items(), key=lambda x: x[1], reverse=True)[
                :10
            ],
            savings_draw_calls=sorted(
                savings_dc,
                key=lambda x: (x.material.slot_count - 1) * x.instance_count,
                reverse=True,
            )[:5],
            savings_tris=sorted(
                savings_tris,
                key=lambda x: (x.mesh.tris - x.target_tris) * x.instance_count,
                reverse=True,
            )[:5],
            pareto_tris=pareto_tris,
            pareto_slots=pareto_slots,
            materials_causing_splits=materials_causing_splits,
        )

        # --- budget -------------------------------------------------------------
        buckets = {
            "tris": {"0-10%": 0, "10-50%": 0, "50%+": 0},
            "slots": {"1-2": 0, "3-5": 0, "6+": 0},
        }
        for r in high_poly:
            over = (r.mesh.tris - r.target_tris) / max(1, r.target_tris)
            key = "0-10%" if over <= 0.1 else "10-50%" if over <= 0.5 else "50%+"
            buckets["tris"][key] += 1
        for r in records:
            over = r.material.slot_count - profile.max_slots
            if over > 0:
                key = "1-2" if over <= 2 else "3-5" if over <= 5 else "6+"
                buckets["slots"][key] += 1

        total_target_tris = sum(r.target_tris * r.instance_count for r in records)
        savings_tris_total = sum(
            (r.mesh.tris - r.target_tris) * r.instance_count for r in high_poly
        )
        slot_counts = sorted(r.material.slot_count for r in records)
        budget = BudgetStats(
            total_target_tris=total_target_tris,
            total_slots=total_slots,
            meshes_over_tri_threshold=len(high_poly),
            meshes_over_slot_threshold=meshes_over_slot_threshold,
            total_slots_over_budget=total_slots_over_budget,
            savings_draw_calls_total=sum(
                (r.material.slot_count - 1) * r.instance_count for r in savings_dc
            ),
            savings_tris_total=savings_tris_total,
            savings_draw_calls_budget=total_slots_over_budget,
            savings_tris_budget=savings_tris_total,
            slot_stats=SlotStats(
                avg=avg_slots,
                avg_unique=sum(slot_counts) / len(slot_counts),
                median=int(slot_counts[len(slot_counts) // 2]),
                p90=int(slot_counts[int(len(slot_counts) * 0.9)]),
                max=int(max_slots),
            ),
            compliance=ComplianceStats(
                tris_pct=(total_tris / total_target_tris * 100.0)
                if total_target_tris
                else 0.0,
                # Both sides instance-weighted, as total_slots is.
                slots_pct=(total_slots / (total_instances * profile.max_slots) * 100.0)
                if total_instances and profile.max_slots
                else 0.0,
            ),
            buckets=BudgetBuckets(tris=buckets["tris"], slots=buckets["slots"]),
        )

        # --- summary -----------------------------------------------------------
        health = []
        if total_tris > 10_000_000:
            health.append("Extreme poly count (>10M rendered triangles)")
        if draw_calls > 5000:
            health.append("High draw-call count (>5k)")
        if textures.unique_paths > 500:
            health.append("Many unique textures (>500)")
        transparency_of = {m.name: m.transparency for m in materials}
        summary = SummaryStats(
            total_meshes=total_meshes,
            total_tris=total_tris,
            total_verts=total_verts,
            raw_total_tris=sum(r.mesh.tris for r in records),
            instance_stats=InstanceStats(
                unique_meshes=total_meshes,
                instanced_shapes=sum(1 for r in records if r.instance_count > 1),
                total_instances=total_instances,
            ),
            scene_health_flags=health,
            multi_slot_meshes=multi_slot_meshes,
            transparent_meshes=sum(1 for r in records if r.material.uses_transparency),
            non_manifold_count=sum(1 for r in records if r.mesh.non_manifold_edges),
            lamina_count=sum(1 for r in records if r.mesh.lamina_faces),
            ngon_count=sum(1 for r in records if r.mesh.ngons),
            high_poly_count=len(high_poly),
            meshes_with_transparency=sum(
                1 for r in records if r.material.uses_transparency
            ),
            meshes_with_extra_uvs=sum(
                1
                for r in records
                if r.mesh.uv_sets - len(r.mesh.uv_snapshot_sets) > profile.max_uvs
            ),
            meshes_with_high_slots=multi_slot_meshes,
            draw_calls=draw_calls,
            materials_in_use=len(materials),
            uv_snapshot_meshes=sum(1 for r in records if r.mesh.uv_snapshot_sets),
            unassigned_meshes=len(unassigned),
            blend_meshes=sum(
                1
                for r in records
                if any(
                    transparency_of.get(m) == TRANSPARENCY_BLEND
                    for m in r.material.materials
                )
            ),
            masked_meshes=sum(
                1
                for r in records
                if any(
                    transparency_of.get(m) == TRANSPARENCY_MASKED
                    for m in r.material.materials
                )
            ),
        )

        report = SceneReport(
            manifest=self._build_manifest(shape_count=total_meshes),
            summary=summary,
            budget=budget,
            textures=textures,
            pipeline=pipeline,
            offenders=offenders,
            assets=records,
            materials=materials,
            overview=self._overview,
        )
        report.fix_actions = self._scene_fix_actions(report)
        return report

    def _material_audits(self, records: List[AssetRecord]) -> List[MaterialAudit]:
        """One :class:`MaterialAudit` per material the records wear, costliest first."""
        wearers: Dict[str, List[AssetRecord]] = {}
        for r in records:
            for mat in r.material.materials:
                wearers.setdefault(mat, []).append(r)

        audits = []
        for mat, users in wearers.items():
            flags = self._material_flags.get(mat, {})
            maps = self._surface_maps(mat)
            infos = [self._texture_info[k] for k in maps if k in self._texture_info]
            audit = MaterialAudit(
                name=mat,
                node_type=flags.get("type", ""),
                shading_engines=sorted(
                    se for se, m in self._material_map.items() if m == mat
                ),
                mesh_count=len(users),
                instance_count=sum(
                    sum(
                        1
                        for p in self._targets.get(r.mesh.shape_name, {}).get(
                            "paths", []
                        )
                        if any(
                            self._material_map.get(se) == mat
                            for se in self._path_shading.get(p, ())
                        )
                    )
                    for r in users
                ),
                transparency=flags.get("transparency", TRANSPARENCY_OPAQUE),
                textures=[i["path"] for i in infos],
                map_types=sorted({i["map_type"] for i in infos if i["map_type"]}),
                max_res=max((max(i["width"], i["height"]) for i in infos), default=0),
                disk_mb=sum(i["size_bytes"] for i in infos) / 2**20,
                gpu_mb=sum(i["gpu_bytes"] for i in infos) / 2**20,
                missing=list(flags.get("missing_paths", [])),
            )
            audit.findings = self._material_findings(audit, flags)
            audits.append(audit)
        audits.sort(key=lambda a: (a.gpu_mb, a.instance_count), reverse=True)
        return audits

    def _material_findings(
        self, audit: MaterialAudit, flags: Dict[str, Any]
    ) -> List[Finding]:
        """Texture-side observations, judged once per material."""
        findings = []
        if audit.missing:
            findings.append(
                Finding(
                    SEVERITY_HIGH,
                    "missing_textures",
                    self._count(len(audit.missing), "missing texture file"),
                    {"paths": list(audit.missing)},
                )
            )
        if audit.max_res > self.profile.max_tex_res:
            findings.append(
                Finding(
                    SEVERITY_HIGH,
                    "max_tex_dim",
                    f"{audit.max_res}px map (budget {self.profile.max_tex_res}px)",
                    {"res": audit.max_res, "budget": self.profile.max_tex_res},
                )
            )
        if audit.transparency == TRANSPARENCY_BLEND:
            findings.append(
                Finding(
                    SEVERITY_MEDIUM,
                    "transparency",
                    "Alpha-blended (sorted, overdraw on every pixel it covers)",
                )
            )
        elif audit.transparency == TRANSPARENCY_MASKED:
            findings.append(
                Finding(SEVERITY_LOW, "transparency", "Alpha-tested (masked)")
            )
        from mayatk.mat_utils._mat_utils import MatUtils

        alpha_maps = [t for t in audit.map_types if t in MatUtils.OPACITY_MAP_TYPES]
        if alpha_maps and audit.transparency == TRANSPARENCY_OPAQUE:
            findings.append(
                Finding(
                    SEVERITY_LOW,
                    "unused_alpha",
                    f"Has an {alpha_maps[0]} map but renders opaque -- its alpha is ignored",
                    {"map_type": alpha_maps[0]},
                )
            )
        # Loose AO / roughness / metallic maps are not flagged: the Scene
        # Exporter packs them into one ORM map for a GLB, so in the scene they
        # are the norm. (``MaterialRecord.unpacked_pbr`` still carries it.)
        if len(audit.textures) > 8:
            findings.append(
                Finding(
                    SEVERITY_LOW,
                    "texture_samplers",
                    f"{len(audit.textures)} texture samplers",
                    {"samplers": len(audit.textures)},
                )
            )
        return findings

    def _texture_stats(self, used_materials: Set[str]) -> TextureStats:
        """Aggregate every texture file the used materials reference, once per file."""
        stats = TextureStats(budget_mb=float(self.profile.max_texture_mb))
        if not self.textures_collected:
            return stats

        users: Dict[str, Set[str]] = {}  # texture key -> materials (in scope)
        other: Dict[str, Set[str]] = {}
        for mat in used_materials:
            for entry in self._material_flags.get(mat, {}).get("textures", []):
                bucket = users if entry["role"] == "material" else other
                bucket.setdefault(entry["key"], set()).add(mat)

        hist = {"4k+": 0, "2k": 0, "1k": 0, "512": 0, "<512": 0}
        files: List[TextureFile] = []
        registry = ptk.MapRegistry()
        for key, mats in users.items():
            info = self._texture_info.get(key)
            if not info or not info["exists"]:
                continue
            record = self._texture_file(key, mats)
            files.append(record)
            stats.total_size_mb += record.size_mb
            stats.est_gpu_mb += info["raw_bytes"] / 2**20
            stats.est_gpu_mb_compressed += record.gpu_mb
            label = record.map_type or "Other"
            stats.type_breakdown[label] = (
                stats.type_breakdown.get(label, 0.0) + record.size_mb
            )
            stats.class_estimates[label] = (
                stats.class_estimates.get(label, 0.0) + record.gpu_mb
            )
            dim = max(record.width, record.height)
            if dim >= 4096:
                hist["4k+"] += 1
                if record.mesh_count > 1:
                    stats.shared_4k_count += 1
                    stats.shared_4k.append(
                        SharedTexture(record.path, record.mesh_count)
                    )
                else:
                    stats.single_use_4k_count += 1
                if record.map_type and not registry.is_resolution_critical(
                    record.map_type
                ):
                    stats.downscale_candidates += 1
                    stats.downscale_savings_mb += record.gpu_mb * 0.75
            elif dim >= 2048:
                hist["2k"] += 1
            elif dim >= 1024:
                hist["1k"] += 1
            elif dim >= 512:
                hist["512"] += 1
            else:
                hist["<512"] += 1

        stats.dim_histogram = hist
        stats.unique_paths = len(files)
        stats.max_resolution = max((max(f.width, f.height) for f in files), default=0)
        stats.large_texture_count = sum(
            1 for f in files if max(f.width, f.height) > 2048
        )
        stats.shared_4k.sort(key=lambda s: s.mesh_count, reverse=True)
        stats.shared_4k = stats.shared_4k[:5]
        stats.heaviest = sorted(
            files, key=lambda f: (f.gpu_mb, f.size_mb), reverse=True
        )
        stats.other = sorted(
            (self._texture_file(k, m) for k, m in other.items() if k not in users),
            key=lambda f: f.path,
        )
        return stats

    def _texture_file(self, key: str, mats: Set[str]) -> TextureFile:
        """The :class:`TextureFile` view of one cached texture."""
        info = self._texture_info[key]
        usage = self._global_texture_usage.get(key, {})
        return TextureFile(
            path=info["path"],
            size_mb=info["size_bytes"] / 2**20,
            width=int(info["width"]),
            height=int(info["height"]),
            material_count=len(mats),
            materials=sorted(mats),
            mesh_count=len(usage.get("meshes", ())),
            instance_count=len(usage.get("instances", ())),
            map_type=info["map_type"] or "",
            gpu_mb=info["gpu_bytes"] / 2**20,
            tiles=info["tiles"],
            role=info["role"],
            bundled=info["bundled"],
        )

    def _scene_fix_actions(self, report: SceneReport) -> List[FixAction]:
        """The scene-level "do these first" list, most severe (then largest) first."""
        actions: List[FixAction] = []
        textures, pipeline, records = report.textures, report.pipeline, report.assets

        def add(severity, kind, message, targets=(), **detail):
            actions.append(
                FixAction(
                    severity=severity,
                    kind=kind,
                    message=message,
                    target=targets[0] if targets else None,
                    detail={"targets": list(targets), **detail},
                )
            )

        if pipeline.missing_project:
            # The project files' materials: a missing Maya preset is listed apart.
            relink = {m for entry in pipeline.missing_project for m in entry.materials}
            add(
                SEVERITY_HIGH,
                "relink_textures",
                f"Relink {self._count(len(pipeline.missing_project), 'missing texture file')} "
                f"used by {self._count(len(relink), 'material')}.",
                [m.path for m in pipeline.missing_project],
                count=len(pipeline.missing_project),
            )
        snap = [r for r in records if r.mesh.uv_snapshot_sets]
        if snap:
            add(
                SEVERITY_HIGH,
                "uv_snapshots",
                f"Leftover _uv_snap_* UV sets on {self._count(len(snap), 'mesh', 'meshes')}: "
                "an interrupted unwrap's backups, which export as real UV sets -- the "
                "second is TEXCOORD_1, the lightmap channel. Remove with "
                "mtk.UvUtils.discard_uv_snapshot(mtk.UvUtils.find_uv_snapshots(objects)).",
                [r.transform for r in snap],
                count=len(snap),
            )
        if textures.est_gpu_mb_compressed > textures.budget_mb > 0:
            n = textures.downscale_candidates
            maps = (
                "the non-detail 4K map" if n == 1 else f"the {n:,} non-detail 4K maps"
            )
            hint = (
                f" Halving {maps} (AO / roughness / metallic ...) frees "
                f"~{textures.downscale_savings_mb:,.0f} MB -- the Scene Exporter's "
                "Secondary Map Size does it for a GLB."
                if n
                else " Downscale the largest maps (see Textures)."
            )
            add(
                SEVERITY_HIGH,
                "texture_memory",
                f"Texture memory ~{textures.est_gpu_mb_compressed:,.0f} MB GPU "
                f"(budget {textures.budget_mb:,.0f} MB).{hint}",
                gpu_mb=textures.est_gpu_mb_compressed,
                savings_mb=textures.downscale_savings_mb,
            )
        broken = [
            r for r in records if r.mesh.non_manifold_edges or r.mesh.lamina_faces
        ]
        if broken:
            add(
                SEVERITY_HIGH,
                "geometry_errors",
                "Non-manifold edges or lamina faces on "
                f"{self._count(len(broken), 'mesh', 'meshes')}: Mesh > Cleanup "
                "before export.",
                [r.transform for r in broken],
                count=len(broken),
            )
        over = sorted(
            (r for r in records if r.mesh.tris > r.target_tris),
            key=lambda r: (r.mesh.tris - r.target_tris) * r.instance_count,
            reverse=True,
        )
        if over:
            excess = sum((r.mesh.tris - r.target_tris) * r.instance_count for r in over)
            add(
                SEVERITY_HIGH if excess > 100_000 else SEVERITY_MEDIUM,
                "decimate_scene",
                f"Triangle budget exceeded on {self._count(len(over), 'mesh', 'meshes')}: "
                f"{excess:,} rendered triangles to cut (decimate / retopo).",
                [r.transform for r in over],
                tris_to_save=excess,
            )
        split = [r for r in records if r.material.slot_count > self.profile.max_slots]
        if split:
            add(
                SEVERITY_MEDIUM,
                "reduce_slots_scene",
                f"More than {self.profile.max_slots} material slots on "
                f"{self._count(len(split), 'mesh', 'meshes')}: merging saves "
                f"{self._count(report.budget.total_slots_over_budget, 'draw call')}.",
                [r.transform for r in split],
                slots_to_reduce=report.budget.total_slots_over_budget,
            )
        if pipeline.unassigned_meshes:
            add(
                SEVERITY_MEDIUM,
                "unassigned_materials",
                "No material on "
                f"{self._count(len(pipeline.unassigned_meshes), 'mesh', 'meshes')}: "
                "the engine picks its own default.",
                pipeline.unassigned_meshes,
                count=len(pipeline.unassigned_meshes),
            )
        oversized = [
            r for r in records if any(f.kind == "oversized_texture" for f in r.findings)
        ]
        if oversized:
            add(
                SEVERITY_MEDIUM,
                "oversized_textures",
                "Oversized unique texture sets on "
                f"{self._count(len(oversized), 'mesh', 'meshes')}: more resolution "
                "than the object's size can show (see Top Issues by Asset).",
                [r.transform for r in oversized],
                count=len(oversized),
            )
        blend = [m for m in report.materials if m.transparency == TRANSPARENCY_BLEND]
        if blend:
            # Distinct instances: one wearing two blended materials is one.
            names = {m.name for m in blend}
            instances = sum(
                1
                for r in records
                for path in self._targets.get(r.mesh.shape_name, {}).get("paths", ())
                if any(
                    self._material_map.get(se) in names
                    for se in self._path_shading.get(path, ())
                )
            )
            add(
                SEVERITY_LOW,
                "blend_materials",
                f"{self._count(len(blend), 'alpha-blended material')} on "
                f"{self._count(instances, 'instance')}: alpha-test (masked) is "
                "cheaper wherever hard edges will do.",
                [m.name for m in blend],
                count=len(blend),
            )
        ngons = [r for r in records if r.mesh.ngons]
        if ngons:
            add(
                SEVERITY_LOW,
                "ngons",
                f"N-gons on {self._count(len(ngons), 'mesh', 'meshes')} "
                f"({self._count(sum(r.mesh.ngons for r in ngons), 'face')}): "
                "triangulation at export can shade them differently.",
                [r.transform for r in ngons],
                count=len(ngons),
            )
        extra = [
            r
            for r in records
            if r.mesh.uv_sets - len(r.mesh.uv_snapshot_sets) > self.profile.max_uvs
        ]
        if extra:
            add(
                SEVERITY_LOW,
                "extra_uv_sets",
                f"More than {self.profile.max_uvs} UV sets on "
                f"{self._count(len(extra), 'mesh', 'meshes')}.",
                [r.transform for r in extra],
                count=len(extra),
            )
        overview = report.overview
        if overview and (overview.unknown_nodes or overview.unknown_plugins):
            add(
                SEVERITY_LOW,
                "unknown_nodes",
                f"{self._count(overview.unknown_nodes, 'unknown node')} and "
                f"{self._count(len(overview.unknown_plugins), 'unknown plugin requirement')}: "
                "Scene > Fix > Cleanup Unknown.",
                count=overview.unknown_nodes,
                plugins=list(overview.unknown_plugins),
            )
        if overview and overview.unloaded_references:
            n = len(overview.unloaded_references)
            add(
                SEVERITY_LOW,
                "unloaded_references",
                f"{self._count(n, 'unloaded reference')}: "
                f"{'its' if n == 1 else 'their'} meshes are in neither this audit "
                "nor an export (Reference Editor > Load).",
                count=n,
                references=list(overview.unloaded_references),
            )

        rank = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
        actions.sort(key=lambda a: rank.get(a.severity, 3))
        return actions

    @staticmethod
    def _phase_b_end(
        phase_a_end: int,
        phase_b_count: int,
        phase_c_count: int,
    ) -> int:
        """Split the post–Phase-A bar range between B and C by item
        ratio. Returns the cumulative bar position where Phase B ends
        and Phase C begins (i.e. ``pct_end`` for the
        :meth:`_build_material_caches` slice).
        """
        remaining = 100 - phase_a_end
        total_bc = phase_b_count + phase_c_count
        if total_bc <= 0:
            return phase_a_end
        phase_b_weight = int(remaining * phase_b_count / total_bc)
        return phase_a_end + phase_b_weight

    # ------------------------------------------------------------------ #
    # Collection
    # ------------------------------------------------------------------ #
    def _clear_caches(self) -> None:
        """Forget everything a previous ``analyze`` collected (targets excepted)."""
        self._shading_map.clear()
        self._path_shading.clear()
        self._material_map.clear()
        self._material_flags.clear()
        self._texture_info.clear()
        self._raw_keys.clear()
        self._global_texture_usage.clear()

    @staticmethod
    def _node_key(path: str) -> str:
        """One string per DAG NODE, whichever of its paths names it.

        Instances of a shape share the node, so they share this key (the
        node's first path). Not ``cmds.ls(uuid=True)``: a batched query
        collapses instance paths (the order stops lining up), and a file
        referenced twice carries duplicate UUIDs.
        """
        try:
            sel = om.MSelectionList()
            sel.add(path)
            return om.MFnDagNode(sel.getDependNode(0)).fullPathName()
        except Exception:  # noqa: BLE001 -- unresolvable: its own key
            return path

    @staticmethod
    def _expand_object_sets(objects: Iterable[Any]) -> List[str]:
        """*objects* with every object set replaced by its members (recursively)."""
        out: List[str] = []
        seen: Set[str] = set()
        stack = [str(o) for o in ptk.make_iterable(objects)][::-1]
        while stack:
            name = stack.pop()
            if "|" in name or "." in name:
                is_set = False  # a DAG path or a component; set names hold neither
            else:
                try:
                    is_set = cmds.objectType(name, isAType="objectSet")
                except Exception:  # noqa: BLE001 -- not a node
                    is_set = False
            if not is_set:
                out.append(name)
            elif name not in seen:
                seen.add(name)
                stack.extend((cmds.sets(name, q=True) or [])[::-1])
        return out

    def _resolve_targets(
        self,
        objects: Optional[List[Any]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        pct_start: int = 0,
        pct_end: int = 10,
    ) -> Dict[str, List[str]]:
        """Resolve inputs to ``{representative shape path: [instance transform paths]}``.

        One entry per unique mesh SHAPE, however many of its instances the
        input names; the list holds every in-scope instance's transform. The
        instance-specific walk is ``Components._mesh_transform_shapes`` (groups
        name their mesh descendants, shapes their every parent, components the
        instance they were picked on); Entire Scene pairs every instance path
        with its parent directly. Instances are then gathered by the node they
        share.

        ``progress_callback`` / ``pct_*`` are accepted for signature stability;
        resolution is a few batched queries and reports no intermediate ticks.
        """
        from mayatk.core_utils.components import _ComponentsInternal

        self._targets = {}
        self._path_owner = {}
        if objects is None and self.scope == "all":
            # Every instance PATH of every live mesh (a shape-typed ls names an
            # instanced shape once). A path's parent IS its transform, so pair
            # them directly: the general walk maps each path to all of its
            # shape's parents, O(n^2) for a shape instanced thousands of times.
            paths = (
                cmds.ls(
                    type="mesh", dag=True, allPaths=True, noIntermediate=True, long=True
                )
                or []
            )
            pairs = [(path.rsplit("|", 1)[0], path) for path in paths]
        else:
            if objects is None:
                objects = cmds.ls(selection=True, long=True) or []
            objects = self._expand_object_sets(objects)
            if not objects:
                return {}
            pairs = _ComponentsInternal._mesh_transform_shapes(objects)

        groups: Dict[str, Dict[str, List[str]]] = {}
        for xform, shape in pairs:
            key = self._node_key(shape)
            entry = groups.setdefault(
                key, {"shape": shape, "paths": [], "transforms": []}
            )
            entry["paths"].append(shape)
            entry["transforms"].append(xform)

        for entry in groups.values():
            rep = entry.pop("shape")
            self._targets[rep] = entry
            for path in entry["paths"]:
                self._path_owner[path] = rep
        return {rep: list(e["transforms"]) for rep, e in self._targets.items()}

    def _member_shape_paths(self, nodes: List[str]) -> List[str]:
        """Mesh shape PATHS a shading engine's member names resolve to.

        Members come back shortest-unique and instance-specific
        (``box_i1|boxShape``); a per-face member names its TRANSFORM
        (``box.f[0:2]``), which is resolved to that path's mesh shape.
        """
        paths: List[str] = []
        # noIntermediate: an Orig shape a scene file connected into the engine
        # (FBX imports write it) is no second mesh wearing the material.
        for node in cmds.ls(nodes, long=True, noIntermediate=True) or []:
            try:
                node_type = cmds.nodeType(node)
            except Exception:  # noqa: BLE001
                continue
            if node_type == "mesh":
                paths.append(node)
            elif node_type == "transform":
                paths.extend(
                    cmds.listRelatives(
                        node,
                        shapes=True,
                        type="mesh",
                        noIntermediate=True,
                        fullPath=True,
                    )
                    or []
                )
        return paths

    def _build_material_caches(
        self,
        shape_map: Dict[str, List[str]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        pct_start: int = 10,
        pct_end: int = 20,
        collect_textures: bool = True,
    ):
        """Build the shared material caches by walking shading engines -> members.

        Inverts the per-object graph walk: each shading engine is read once,
        its members mapped onto the in-scope instance paths they shade
        (members are per instance, so two instances of one shape can wear
        different materials), and each material's flags and textures are
        read once however many engines use it. Components are stripped
        rather than flattened -- ``ls -flatten`` on a per-face assignment
        expands every face into its own string.

        ``collect_textures`` gates the texture file reads for a
        sections-filtered run that surfaces no texture data.
        """
        shading_engines = cmds.ls(type="shadingEngine") or []
        self._shading_engine_count = len(shading_engines)

        total = len(shading_engines)
        span = max(1, pct_end - pct_start)
        for index, se in enumerate(shading_engines):
            if progress_callback and total:
                progress_callback(
                    pct_start + int((index / total) * span),
                    100,
                    f"Collecting material data ({index + 1}/{total})",
                )
            members = cmds.sets(se, q=True) or []
            if not members:
                continue
            shader = (
                cmds.listConnections(
                    f"{se}.surfaceShader", source=True, destination=False
                )
                or [None]
            )[0]
            material = shader or se  # an engine with no shader shows as itself
            self._material_map[se] = material
            if material not in self._material_flags:
                self._material_flags[material] = self._analyze_material_node(
                    shader, collect_textures=collect_textures
                )

            nodes = list(dict.fromkeys(m.split(".", 1)[0] for m in members))
            mesh_nodes: Set[str] = set()
            instance_paths: List[str] = []
            for path in self._member_shape_paths(nodes):
                mesh_nodes.add(self._node_key(path))
                instance_paths.append(path)
                owner = self._path_owner.get(path)
                if owner is None:
                    continue
                self._path_shading.setdefault(path, set()).add(se)
                self._shading_map.setdefault(owner, set()).add(se)

            # Scene-wide texture usage (not just the scope): "is this map unique
            # to one mesh?" must see the meshes outside the selection too.
            for entry in self._material_flags[material].get("textures", []):
                usage = self._global_texture_usage.setdefault(
                    entry["key"],
                    {"meshes": set(), "instances": set(), "materials": set()},
                )
                usage["meshes"].update(mesh_nodes)
                usage["instances"].update(instance_paths)
                usage["materials"].add(material)

    def _analyze_material_node(
        self,
        mat_node: Optional[str],
        collect_textures: bool = True,
    ) -> Dict[str, Any]:
        """Flags for one material: type, transparency mode, texture entries.

        ``textures`` holds one entry per distinct file the material's network
        reads: ``{"key", "node", "map_type", "role"}``, with the file's own
        facts cached once in ``self._texture_info[key]``. ``role`` is
        ``"material"`` for a surface map -- one the ``ptk.MapFactory`` taxonomy
        names from its filename, or from the shader slot it drives
        (``ShaderAttributeMap``) -- and ``"other"`` for the rest: StingrayPBS's
        IBL cube maps and BRDF LUT, which Maya wires onto every such material
        and no export carries.
        """
        flags: Dict[str, Any] = {
            "type": "",
            "transparent": False,
            "transparency": TRANSPARENCY_OPAQUE,
            "unpacked_pbr": False,
            "textures": [],
            "missing_textures": 0,
            "missing_paths": [],
        }
        if not mat_node:
            return flags

        mat_type = cmds.nodeType(mat_node)
        flags["type"] = mat_type
        mode = self._transparency_mode(mat_node, mat_type)
        flags["transparency"] = mode
        flags["transparent"] = mode != TRANSPARENCY_OPAQUE

        if not collect_textures:
            return flags

        try:
            # pruneDagObjects: never on through a uvChooser / place3dTexture into
            # a mesh and its deformer / rig history (MatUtils.get_file_nodes too).
            history = cmds.listHistory(mat_node, pruneDagObjects=True) or []
            file_nodes = cmds.ls(history, type="file") or [] if history else []
        except Exception:  # noqa: BLE001
            file_nodes = []
        self._file_node_count += len(file_nodes)

        seen: Set[str] = set()
        slot_types: Optional[Dict[str, str]] = None  # walked on demand, once
        for fn in file_nodes:
            try:
                raw = cmds.getAttr(f"{fn}.fileTextureName") or ""
            except Exception:  # noqa: BLE001
                continue
            if not raw:
                continue
            info = self._texture_record(raw, fn)
            key = info["key"]
            if not info["exists"]:
                if info["path"] not in flags["missing_paths"]:
                    flags["missing_paths"].append(info["path"])
            if key in seen:
                continue
            seen.add(key)
            map_type = info["map_type"]
            if not map_type:
                if slot_types is None:
                    slot_types = self._slot_map_types(mat_node, mat_type)
                map_type = slot_types.get(fn)
            if map_type and not info["map_type"]:
                info["map_type"] = map_type
                info["gpu_bytes"], info["raw_bytes"] = (
                    ptk.MapRegistry().estimate_gpu_bytes(
                        info["width"], info["height"], map_type, info["tiles"]
                    )
                )
            role = "material" if map_type else "other"
            if role == "material":
                info["role"] = "material"
            flags["textures"].append(
                {"key": key, "node": fn, "map_type": map_type, "role": role}
            )

        flags["missing_textures"] = len(flags["missing_paths"])
        # Loose masks no packed map already carries: an Albedo_Transparency
        # map is packed too, but it carries opacity, not occlusion / roughness.
        types = {t["map_type"] for t in flags["textures"] if t["role"] == "material"}
        registry = ptk.MapRegistry()
        carried: Set[str] = set()
        for map_type in types:
            definition = registry.get(map_type) if map_type else None
            if getattr(definition, "is_packed", False):
                carried.update(definition.carried_types())
        loose = [t for t in self._LOOSE_PBR_TYPES if t in types and t not in carried]
        flags["unpacked_pbr"] = len(loose) >= 2
        return flags

    def _surface_maps(self, material: str) -> List[str]:
        """Texture keys of *material*'s surface maps (role ``material``)."""
        return [
            t["key"]
            for t in self._material_flags.get(material, {}).get("textures", [])
            if t["role"] == "material"
        ]

    def _texture_record(self, raw: str, file_node: str) -> Dict[str, Any]:
        """Facts about one texture FILE, read once and cached.

        Resolution is Maya's own order (``MatUtils.resolve_path(search=False)``:
        env vars, project root, then the sourceImages rule), once per stored
        path; a tile token counts every tile, a frame token one frame.
        Dimensions come from the image HEADER
        (``ptk.ImgUtils.get_image_size``) -- reading the file node's
        ``outSize`` makes Maya decode the whole image, ~0.25 s per 4K PNG,
        which was 98% of an audit's time; it remains the fallback for a
        format the header reader cannot parse.
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        # Resolved once per stored path: every file node reading it asks again.
        key = self._raw_keys.get(raw)
        if key is not None:
            return self._texture_info[key]
        resolved = MatUtils.resolve_path(raw, search=False)
        key = self._raw_keys[raw] = os.path.normcase(os.path.normpath(resolved or raw))
        if key in self._texture_info:
            return self._texture_info[key]

        info: Dict[str, Any] = {
            "key": key,
            "path": (resolved or raw).replace("\\", "/"),
            "exists": resolved is not None,
            "size_bytes": 0,
            "width": 0,
            "height": 0,
            "tiles": 1,
            "map_type": None,
            "role": "other",
            "bundled": MatUtils.is_bundled_texture(resolved or raw),
            "gpu_bytes": 0.0,
            "raw_bytes": 0.0,
        }
        if resolved is not None:
            files = [resolved]
            if MatUtils.has_path_token(resolved):
                files = MatUtils.texture_tiles(resolved)
                if MatUtils.is_frame_sequence(resolved):
                    files = files[:1]  # loaded a frame at a time; tiles all at once
            files = files or [MatUtils.probe_texture_path(resolved) or resolved]
            info["tiles"] = max(1, len(files))
            for f in files:
                try:
                    info["size_bytes"] += os.path.getsize(f)
                except OSError:
                    pass
            size = ptk.ImgUtils.get_image_size(files[0]) or self._out_size(file_node)
            if size:
                info["width"], info["height"] = int(size[0]), int(size[1])
        try:
            info["map_type"] = ptk.MapFactory.resolve_map_type(raw) or None
        except Exception:  # noqa: BLE001 -- not a path-like value
            info["map_type"] = None
        if info["map_type"]:
            info["role"] = "material"
        info["gpu_bytes"], info["raw_bytes"] = ptk.MapRegistry().estimate_gpu_bytes(
            info["width"], info["height"], info["map_type"], info["tiles"]
        )
        self._texture_info[key] = info
        return info

    @staticmethod
    def _out_size(file_node: str) -> Optional[Tuple[int, int]]:
        """The file node's decoded ``outSize`` -- the slow fallback."""
        try:
            value = cmds.getAttr(f"{file_node}.outSize")
        except Exception:  # noqa: BLE001
            return None
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            value = value[0]
        try:
            width, height = int(value[0]), int(value[1])
        except (TypeError, ValueError, IndexError):
            return None
        return (width, height) if width and height else None

    @staticmethod
    def _slot_map_types(material: str, material_type: str) -> Dict[str, str]:
        """``{file node: map type}`` for the files *material*'s surface slots read.

        For a file whose NAME carries no map-type token. Each slot
        ``ShaderAttributeMap`` names for the shader type is traced UPSTREAM to
        its file node by ``MatUtils.get_texture_file_node`` (through bump /
        normal-map / colour-correct nodes, and a packed map's per-channel
        wiring), and the slot's logical channel resolves through
        ``ptk.MapRegistry`` -- never overriding a filename classification (see
        its ``LOGICAL_CHANNEL_TYPES`` note). A file no slot reaches (Stingray's
        ``TEX_global_*`` IBL inputs) is absent.
        """
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        attrs = ShaderAttributeMap.SHADER_ATTRS.get(material_type)
        if attrs is None:
            return {}
        found: Dict[str, str] = {}
        for channel in ShaderAttributeMap.logical_channels():
            slot = getattr(attrs, channel)
            node = MatUtils.get_texture_file_node(material, slot[0]) if slot else None
            map_type = ptk.MapRegistry.resolve_type_from_channel(channel)
            if node and map_type and node not in found:
                found[node] = map_type
        return found

    @staticmethod
    def _transparency_mode(material: str, material_type: str) -> str:
        """``opaque`` / ``masked`` / ``blend`` for *material*.

        StingrayPBS reads its loaded ShaderFX graph
        (``MatUtils.get_stingray_opacity_mode``: the transparent graph blends,
        the masked graph alpha-tests). Other shaders blend when a
        transparency / transmission input is driven or non-zero, or an
        opacity input is driven or below one.
        """
        if material_type == "StingrayPBS":
            from mayatk.mat_utils._mat_utils import MatUtils

            mode = MatUtils.get_stingray_opacity_mode(material)
            return {
                "transparent": TRANSPARENCY_BLEND,
                "masked": TRANSPARENCY_MASKED,
            }.get(mode, TRANSPARENCY_OPAQUE)

        def value_of(attr):
            try:
                if not cmds.attributeQuery(attr, node=material, exists=True):
                    return None
            except Exception:  # noqa: BLE001
                return None
            if cmds.listConnections(
                f"{material}.{attr}", source=True, destination=False
            ):
                return "driven"
            try:
                value = cmds.getAttr(f"{material}.{attr}")
            except Exception:  # noqa: BLE001
                return None
            if (
                isinstance(value, list)
                and len(value) == 1
                and isinstance(value[0], tuple)
            ):
                value = value[0]
            return value

        for attr in ("transparency", "transmission"):
            value = value_of(attr)
            if value == "driven":
                return TRANSPARENCY_BLEND
            if isinstance(value, (int, float)) and value > 0.001:
                return TRANSPARENCY_BLEND
            if isinstance(value, (tuple, list)) and any(c > 0.001 for c in value):
                return TRANSPARENCY_BLEND
        for attr in ("opacity", "cutout_opacity", "geometryOpacity"):
            value = value_of(attr)
            if value == "driven":
                return TRANSPARENCY_BLEND
            if isinstance(value, (int, float)) and value < 0.999:
                return TRANSPARENCY_BLEND
            if isinstance(value, (tuple, list)) and any(c < 0.999 for c in value):
                return TRANSPARENCY_BLEND
        return TRANSPARENCY_OPAQUE

    @staticmethod
    def _world_diag(path: str) -> float:
        """World-space bounding-box diagonal of one instance, in centimeters.

        The API answers in Maya's internal unit (cm) whatever the UI unit is,
        and the box is transformed by the instance's own world matrix -- so a
        prop modelled small and scaled up is measured at the size it renders.
        ``polyEvaluate -boundingBox`` is object-space and in UI units, which in
        a meter scene shrank every budget 100x.
        """
        try:
            sel = om.MSelectionList()
            sel.add(path)
            dag = sel.getDagPath(0)
            box = om.MFnDagNode(dag).boundingBox
            matrix = dag.inclusiveMatrix()
        except Exception:  # noqa: BLE001
            return 0.0
        points = [
            om.MPoint(x, y, z) * matrix
            for x in (box.min.x, box.max.x)
            for y in (box.min.y, box.max.y)
            for z in (box.min.z, box.max.z)
        ]
        return math.sqrt(
            sum(
                (max(p[i] for p in points) - min(p[i] for p in points)) ** 2
                for i in range(3)
            )
        )

    def _analyze_mesh(
        self,
        shape: str,
        paths: Optional[List[str]] = None,
        checks: bool = True,
    ) -> MeshRecord:
        """Measure one unique shape; *paths* are its in-scope instance paths.

        ``checks`` runs the topology and UV checks (n-gons, non-manifold edges,
        lamina faces, UV snapshots, the lightmap set, skinning) -- the bulk of a
        shape's cost, skipped when no requested section shows them
        (``SceneInfoSection._NEEDS_MESH_CHECKS``).
        """
        from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics
        from mayatk.uv_utils._uv_utils import UvUtils

        paths = paths or [shape]
        counts = cmds.polyEvaluate(shape, triangle=True, vertex=True)
        tris = counts.get("triangle", 0) if isinstance(counts, dict) else 0
        verts = counts.get("vertex", 0) if isinstance(counts, dict) else 0

        uv_set_names = list(cmds.polyUVSet(shape, q=True, allUVSets=True) or [])
        color_sets = cmds.polyColorSet(shape, q=True, allColorSets=True) or []

        has_skin = False
        lightmap = None
        snapshots: List[str] = []
        ngons = non_manifold_edges = lamina_faces = 0
        if checks:
            try:
                history = cmds.listHistory(shape) or []
                has_skin = (
                    bool(cmds.ls(history, type="skinCluster")) if history else False
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                lightmap = UvDiagnostics.find_lightmap_uv_set(
                    shape, all_sets=uv_set_names
                )
            except Exception:  # noqa: BLE001
                pass
            snapshots = [snap for _s, _c, snap in UvUtils.find_uv_snapshots([shape])]
            try:
                sel = om.MSelectionList()
                sel.add(shape)
                vertex_counts, _ = om.MFnMesh(sel.getDagPath(0)).getVertices()
                ngons = sum(1 for count in vertex_counts if count > 4)
                non_manifold_edges = len(
                    cmds.polyInfo(shape, nonManifoldEdges=True) or []
                )
                lamina_faces = len(cmds.polyInfo(shape, laminaFaces=True) or [])
            except Exception:  # noqa: BLE001
                pass

        # Vertex payload estimate: Pos(12) + Norm(4) + Tan(4) + UV(8/set)
        # + Color(4) + Skin(8).
        v_bytes = 20 + (len(uv_set_names) * 8)
        if color_sets:
            v_bytes += 4
        if has_skin:
            v_bytes += 8

        return MeshRecord(
            shape_name=shape,
            tris=tris,
            verts=verts,
            uv_sets=len(uv_set_names),
            uv_set_names=uv_set_names,
            has_colors=bool(color_sets),
            instanced=len(paths) > 1
            or len(cmds.listRelatives(shape, allParents=True) or []) > 1,
            bounds_diag=max(self._world_diag(p) for p in paths),
            ngons=ngons,
            non_manifold_edges=non_manifold_edges,
            lamina_faces=lamina_faces,
            vertex_bytes=v_bytes,
            uv_snapshot_sets=snapshots,
            lightmap_uv_set=lightmap,
        )

    def _analyze_material(self, shape: str) -> MaterialRecord:
        """Summarize the materials one shape's in-scope instances wear."""
        paths = self._targets.get(shape, {}).get("paths", [shape])
        per_instance = [self._path_shading.get(p, set()) for p in paths]
        shading_engines = self._shading_map.get(shape, set())
        materials = sorted(
            {
                self._material_map[se]
                for se in shading_engines
                if se in self._material_map
            }
        )

        rank = {TRANSPARENCY_OPAQUE: 0, TRANSPARENCY_MASKED: 1, TRANSPARENCY_BLEND: 2}
        transparency = TRANSPARENCY_OPAQUE
        unpacked = False
        missing = 0
        max_samplers = 0
        keys: List[str] = []
        for mat in materials:
            flags = self._material_flags.get(mat, {})
            mode = flags.get("transparency", TRANSPARENCY_OPAQUE)
            if rank.get(mode, 0) > rank[transparency]:
                transparency = mode
            unpacked = unpacked or bool(flags.get("unpacked_pbr"))
            missing += flags.get("missing_textures", 0)
            surface = self._surface_maps(mat)
            max_samplers = max(max_samplers, len(surface))
            keys.extend(k for k in surface if k not in keys)

        max_res = 0
        max_res_is_unique = False
        unique_local = 0
        total_mb = 0.0
        gpu_mb = 0.0
        for key in keys:
            info = self._texture_info.get(key)
            if not info:
                continue
            is_unique = (
                len(self._global_texture_usage.get(key, {}).get("meshes", ())) == 1
            )
            unique_local += int(is_unique)
            dim = max(info["width"], info["height"])
            if dim > max_res:
                max_res, max_res_is_unique = dim, is_unique
            elif dim == max_res and is_unique:
                max_res_is_unique = True
            total_mb += info["size_bytes"] / 2**20
            gpu_mb += info["gpu_bytes"] / 2**20

        return MaterialRecord(
            slot_count=max((len(s) for s in per_instance), default=0),
            uses_transparency=transparency != TRANSPARENCY_OPAQUE,
            materials=materials,
            texture_count=len(keys),
            max_res=int(max_res),
            total_tex_size_mb=total_mb,
            est_gpu_size_mb=gpu_mb,
            unpacked_pbr=unpacked,
            missing_textures=missing,
            max_samplers=max_samplers,
            unique_paths_local=unique_local,
            max_res_is_unique=max_res_is_unique,
            draw_calls=sum(max(1, len(s)) for s in per_instance),
            transparency=transparency,
            redundant_slots=max(
                (
                    len(ses) - len({self._material_map.get(se, se) for se in ses})
                    for ses in per_instance
                ),
                default=0,
            ),
        )

    def _calculate_score(
        self, mesh: MeshRecord, mat: MaterialRecord
    ) -> Tuple[
        float,
        float,
        float,
        List[Finding],
        Dict[str, float],
        BudgetDelta,
        List[FixAction],
        int,
    ]:
        """Score one asset on what is ITS OWN to fix: geometry, UV sets,
        material slots, a missing material, and a unique texture set bigger
        than its size needs. Material-wide costs (texture memory, blending,
        missing files) are judged once per material instead -- see
        :meth:`_material_findings` -- rather than repeated on every mesh
        that wears the material.

        Returns:
            ``(total_score, perf_score, risk_score, findings, breakdown,
            delta, fix_plan, target_tris)``.
        """
        profile = self.profile
        perf_score = 0.0
        risk_score = 0.0
        findings: List[Finding] = []
        breakdown: Dict[str, float] = {}
        fix_plan: List[FixAction] = []

        # Adaptive budget: linear in world size up to the reference diagonal.
        target_tris = profile.max_tris
        if profile.adaptive_tris and profile.reference_diag > 0:
            ratio = min(1.0, mesh.bounds_diag / profile.reference_diag)
            target_tris = max(profile.min_tris, int(profile.max_tris * ratio))

        shipped_uvs = mesh.uv_sets - len(mesh.uv_snapshot_sets)
        delta = BudgetDelta(
            tris_over=max(0, mesh.tris - target_tris),
            slots_over=max(0, mat.slot_count - profile.max_slots),
            uvs_over=max(0, shipped_uvs - profile.max_uvs),
            max_tex_res_over=max(0, mat.max_res - profile.max_tex_res),
        )

        if mesh.tris > target_tris:
            over = mesh.tris - target_tris
            penalty = over / 1000.0  # 1 point per 1k over
            perf_score += penalty
            breakdown["High Poly"] = penalty
            findings.append(
                Finding(
                    SEVERITY_HIGH,
                    "high_poly",
                    f"High poly: {mesh.tris:,} tris (budget {target_tris:,}, +{over:,})",
                    {"tris": mesh.tris, "budget": target_tris, "over": over},
                )
            )
            fix_plan.append(
                FixAction(
                    SEVERITY_HIGH,
                    "decimate",
                    f"Reduce tris {mesh.tris:,} -> {target_tris:,} (decimate / retopo)",
                    detail={"from_tris": mesh.tris, "to_tris": target_tris},
                )
            )

        if mesh.uv_snapshot_sets:
            penalty = 10.0 * len(mesh.uv_snapshot_sets)
            risk_score += penalty
            breakdown["UV Snapshots"] = penalty
            findings.append(
                Finding(
                    SEVERITY_HIGH,
                    "uv_snapshots",
                    f"{self._count(len(mesh.uv_snapshot_sets), 'leftover UV snapshot set')} "
                    f"({', '.join(mesh.uv_snapshot_sets)})",
                    {"sets": list(mesh.uv_snapshot_sets)},
                )
            )
            fix_plan.append(
                FixAction(
                    SEVERITY_HIGH,
                    "remove_uv_snapshots",
                    "Discard the leftover _uv_snap_* sets (mtk.UvUtils.discard_uv_snapshot)",
                    detail={"sets": list(mesh.uv_snapshot_sets)},
                )
            )

        if shipped_uvs > profile.max_uvs:
            over = shipped_uvs - profile.max_uvs
            penalty = over * 5.0
            perf_score += penalty
            breakdown["Extra UV Sets"] = penalty
            names = [n for n in mesh.uv_set_names if n not in mesh.uv_snapshot_sets]
            findings.append(
                Finding(
                    SEVERITY_LOW,
                    "extra_uv_sets",
                    f"{shipped_uvs} UV sets ({', '.join(names)}; budget "
                    f"{profile.max_uvs})",
                    {
                        "uv_sets": shipped_uvs,
                        "uv_names": names,
                        "budget": profile.max_uvs,
                        "over": over,
                    },
                )
            )
            fix_plan.append(
                FixAction(
                    SEVERITY_LOW,
                    "remove_uv_sets",
                    f"Remove {self._count(over, 'extra UV set')} (if not required by export/profile)",
                    detail={"remove_count": over},
                )
            )

        if mesh.ngons > 0:
            penalty = mesh.ngons * 0.1
            risk_score += penalty
            breakdown["N-gons"] = penalty
            findings.append(
                Finding(
                    SEVERITY_HIGH
                    if mesh.ngons > 100 or mesh.tris > target_tris
                    else SEVERITY_LOW,
                    "ngons",
                    self._count(mesh.ngons, "n-gon"),
                    {"ngons": mesh.ngons, "tris": mesh.tris},
                )
            )
            fix_plan.append(
                FixAction(
                    SEVERITY_LOW,
                    "triangulate_ngons",
                    "Triangulate or quadrangulate n-gons",
                )
            )

        if mesh.non_manifold_edges > 0:
            penalty = mesh.non_manifold_edges * 2.0
            risk_score += penalty
            breakdown["Non-Manifold"] = penalty
            findings.append(
                Finding(
                    SEVERITY_HIGH,
                    "non_manifold",
                    self._count(mesh.non_manifold_edges, "non-manifold edge"),
                    {"non_manifold_edges": mesh.non_manifold_edges},
                )
            )
            fix_plan.append(
                FixAction(
                    SEVERITY_HIGH, "fix_non_manifold", "Clean up non-manifold geometry"
                )
            )

        if mesh.lamina_faces > 0:
            penalty = mesh.lamina_faces * 2.0
            risk_score += penalty
            breakdown["Lamina Faces"] = penalty
            findings.append(
                Finding(
                    SEVERITY_HIGH,
                    "lamina_faces",
                    self._count(mesh.lamina_faces, "lamina face"),
                    {"lamina_faces": mesh.lamina_faces},
                )
            )
            fix_plan.append(
                FixAction(SEVERITY_HIGH, "remove_lamina", "Remove lamina faces")
            )

        if mat.slot_count > profile.max_slots:
            over = mat.slot_count - profile.max_slots
            penalty = over * 10.0
            perf_score += penalty
            breakdown["Draw Call Split"] = penalty
            # Per instance: a union over instances hides one wearing a material twice.
            redundant = mat.redundant_slots
            findings.append(
                Finding(
                    SEVERITY_HIGH,
                    "draw_call_split",
                    f"{mat.slot_count} material slots"
                    + (f" ({redundant} redundant)" if redundant > 0 else "")
                    + f" (budget {profile.max_slots})",
                    {
                        "slot_count": mat.slot_count,
                        "redundant_slots": redundant,
                        "budget": profile.max_slots,
                        "over": over,
                    },
                )
            )
            if redundant > 0:
                fix_plan.append(
                    FixAction(
                        SEVERITY_HIGH,
                        "consolidate_slots",
                        f"Consolidate {self._count(redundant, 'redundant slot')} (assign the "
                        "same material to all their faces)",
                        detail={"redundant_slots": redundant},
                    )
                )
            else:
                fix_plan.append(
                    FixAction(
                        SEVERITY_HIGH,
                        "reduce_slots",
                        f"Reduce slots {mat.slot_count} -> {profile.max_slots} "
                        "(merge materials: atlas the textures or use vertex colors)",
                        detail={
                            "from_slots": mat.slot_count,
                            "to_slots": profile.max_slots,
                        },
                    )
                )

        if self.materials_collected and not mat.materials:
            risk_score += 10.0
            breakdown["No Material"] = 10.0
            findings.append(
                Finding(SEVERITY_MEDIUM, "unassigned", "No material assigned")
            )

        # A texture set nobody else wears, at more resolution than the object's
        # size can show. Shared sets are judged in the Textures section instead:
        # their cost is paid once however many meshes wear them.
        ideal = (mesh.bounds_diag / 100.0) * self.TEXELS_PER_METER
        if mat.max_res_is_unique and mat.max_res >= 2048 and mat.max_res > ideal * 2.0:
            suggested = 1 << max(9, int(math.ceil(math.log2(max(ideal, 1.0)))))
            penalty = 10.0
            perf_score += penalty
            breakdown["Oversized Texture"] = penalty
            findings.append(
                Finding(
                    SEVERITY_MEDIUM,
                    "oversized_texture",
                    f"Unique {mat.max_res}px texture set on a "
                    f"{mesh.bounds_diag:,.0f} cm object (~{suggested}px suffices)",
                    {
                        "res": mat.max_res,
                        "ideal_res": int(ideal),
                        "suggested": suggested,
                    },
                )
            )
            fix_plan.append(
                FixAction(
                    SEVERITY_MEDIUM,
                    "downscale_textures",
                    f"Downscale its textures to {suggested}px",
                    detail={"target_res": suggested},
                )
            )

        seen: Set[Tuple[str, str]] = set()
        deduped: List[FixAction] = []
        for action in fix_plan:
            key = (action.kind, action.message)
            if key not in seen:
                seen.add(key)
                deduped.append(action)

        return (
            perf_score + risk_score,
            perf_score,
            risk_score,
            findings,
            breakdown,
            delta,
            deduped,
            target_tris,
        )

    def _collect_overview(self) -> SceneOverview:
        """Scene-wide facts: the file, units and time setup, and a node census."""
        from mayatk.env_utils._env_utils import EnvUtils
        from mayatk.mat_utils._mat_utils import MatUtils

        def count(**kwargs) -> int:
            try:
                return len(cmds.ls(**kwargs) or [])
            except Exception:  # noqa: BLE001
                return 0

        path = EnvUtils.saved_scene_path()
        try:
            size_mb = os.path.getsize(path) / 2**20 if path else 0.0
        except OSError:
            size_mb = 0.0
        try:
            settings = EnvUtils.scene_settings()
        except Exception:  # noqa: BLE001
            settings = {}

        cameras: List[str] = []
        startup: List[str] = []  # persp / top / front / side: every scene has them
        for camera in cmds.ls(cameras=True, long=True) or []:
            is_startup = cmds.camera(camera, q=True, startupCamera=True)
            (startup if is_startup else cameras).append(camera)
        startup_xforms = set(
            cmds.listRelatives(startup, parent=True, fullPath=True) or []
            if startup
            else ()
        )
        shading_engines = [
            s
            for s in cmds.ls(type="shadingEngine") or []
            if s not in self._DEFAULT_SHADING_ENGINES
        ]
        layers = [
            layer
            for layer in cmds.ls(type="displayLayer") or []
            if layer != "defaultLayer"
        ]
        try:
            materials = len(MatUtils.get_scene_mats() or [])
        except Exception:  # noqa: BLE001
            materials = 0

        references, unloaded = [], []
        for ref in cmds.file(q=True, reference=True) or []:
            references.append(ref)
            try:
                if not cmds.referenceQuery(ref, isLoaded=True):
                    unloaded.append(ref)
            except Exception:  # noqa: BLE001
                pass
        namespaces = [
            ns
            for ns in cmds.namespaceInfo(":", listOnlyNamespaces=True, recurse=True)
            or []
            if ns not in ("UI", "shared")
        ]

        return SceneOverview(
            scene_path=path,
            file_size_mb=size_mb,
            linear_unit=cmds.currentUnit(q=True, linear=True),
            up_axis=cmds.upAxis(q=True, axis=True),
            fps=float(settings.get("fps", 0.0) or 0.0),
            playback_range=(
                settings.get("frame_start", 0.0),
                settings.get("frame_end", 0.0),
            ),
            animation_range=(
                settings.get("anim_start", 0.0),
                settings.get("anim_end", 0.0),
            ),
            counts={
                "transforms": len(
                    set(cmds.ls(exactType="transform", long=True) or [])
                    - startup_xforms
                ),
                "mesh_shapes": count(type="mesh", noIntermediate=True),
                "mesh_instances": count(
                    type="mesh", dag=True, allPaths=True, noIntermediate=True
                ),
                "curves": count(type="nurbsCurve", noIntermediate=True),
                "locators": count(type="locator"),
                "joints": count(type="joint"),
                "skin_clusters": count(type="skinCluster"),
                "blend_shapes": count(type="blendShape"),
                "constraints": count(type="constraint"),
                "anim_curves": count(type="animCurve"),
                "expressions": count(type="expression"),
                "lights": count(lights=True),
                "cameras": len(cameras),
                "display_layers": len(layers),
                "materials": materials,
                "shading_engines": len(shading_engines),
                "file_textures": count(type="file"),
            },
            namespaces=namespaces,
            references=references,
            unloaded_references=unloaded,
            unknown_plugins=list(cmds.unknownPlugin(q=True, list=True) or []),
            unknown_nodes=count(type=["unknown", "unknownDag", "unknownTransform"]),
        )

    # ------------------------------------------------------------------ #
    # Rendering — every section is one ptk.ReportDoc built from the report.
    # ------------------------------------------------------------------ #
    _SEVERITY_TONES = {
        SEVERITY_HIGH: "error",
        SEVERITY_MEDIUM: "warn",
        SEVERITY_LOW: "dim",
    }

    def print_report(
        self,
        report: SceneReport,
        sections: Optional[List[str]] = None,
    ):
        """Print the formatted scene-audit report to the logger.

        ``sections`` chooses which sections to render and in what
        order. ``None`` means "all sections". The context header (title +
        scope line) is always emitted first.
        """
        selected = SceneInfoSection.normalize(sections)
        docs = self._section_docs(report, selected)
        text = [self._doc_header(report).to_text()]
        text.extend(docs[key].to_text() for key in selected if docs.get(key))
        self.logger.log_raw("\n\n".join(text))

    def _section_docs(
        self, report: SceneReport, sections: Optional[List[str]] = None
    ) -> Dict[str, "ptk.ReportDoc"]:
        """``{section key: ReportDoc}`` for the requested sections, in order."""
        builders = {
            SceneInfoSection.OVERVIEW: self._doc_overview,
            SceneInfoSection.SUMMARY: self._doc_summary,
            SceneInfoSection.FIX_FIRST: self._doc_fix_first,
            SceneInfoSection.PARETO: self._doc_pareto,
            SceneInfoSection.OFFENDERS: self._doc_offenders,
            SceneInfoSection.MATERIALS: self._doc_materials,
            SceneInfoSection.TEXTURES: self._doc_textures,
            SceneInfoSection.PIPELINE: self._doc_pipeline,
            SceneInfoSection.ASSUMPTIONS: self._doc_assumptions,
        }
        return {
            key: builders[key](report)
            for key in SceneInfoSection.normalize(sections)
            if key in builders
        }

    # ---- rendering helpers ------------------------------------------------
    @staticmethod
    def _display_names(paths: Iterable[str]) -> Dict[str, str]:
        """The shortest trailing ``|`` segments that tell *paths* apart.

        ``WIRE_LOOM_B`` alone when it is the only one listed;
        ``ITA_DA2_CFG_A_LOC|WIRE_LOOMS|WIRE_LOOM_B`` style only as deep as it
        takes to separate two rows that share a leaf.
        """
        paths = list(dict.fromkeys(paths))
        parts = {p: [s for s in p.split("|") if s] or [p] for p in paths}
        depth = {p: 1 for p in paths}
        while True:
            by_name: Dict[str, List[str]] = {}
            for p in paths:
                by_name.setdefault("|".join(parts[p][-depth[p] :]), []).append(p)
            grew = False
            for clash in (c for c in by_name.values() if len(c) > 1):
                for p in clash:
                    if depth[p] < len(parts[p]):
                        depth[p] += 1
                        grew = True
            if not grew:
                break
        return {p: "|".join(parts[p][-depth[p] :]) for p in paths}

    @staticmethod
    def _node_link(name: str, node: str) -> "ptk.ReportDoc.Inline":
        """*name* as a link that selects *node* in the scene."""
        return Doc.action(name, "select", node=node)

    @staticmethod
    def _count(n: int, noun: str, plural: Optional[str] = None) -> str:
        """``"1 mesh"`` / ``"1,505 meshes"``."""
        return f"{n:,} {noun if n == 1 else (plural or noun + 's')}"

    @staticmethod
    def _mb(value: float) -> str:
        return f"{value:,.1f} MB" if value < 100 else f"{value:,.0f} MB"

    @staticmethod
    def _file_link(path: str, text: str) -> "ptk.ReportDoc.Inline":
        """*text* linking a texture file that opens: a tile / frame set links
        its first real file (the ``<UDIM>`` spelling names nothing on disk, so
        the link raised the OS's "cannot find" dialog), and a file that is not
        there reads as plain text rather than a link to nowhere."""
        from mayatk.mat_utils._mat_utils import MatUtils

        target = (
            MatUtils.probe_texture_path(path) if MatUtils.has_path_token(path) else path
        )
        if not target or not os.path.isfile(target):
            return Doc.span(text)
        return Doc.file(target, text)

    def _severity(self, severity: str) -> "ptk.ReportDoc.Inline":
        return Doc.span(
            severity.upper(), tone=self._SEVERITY_TONES.get(severity), bold=True
        )

    def _scope_phrase(self, report: SceneReport) -> str:
        scope = report.manifest.scope
        return {"all": "Entire scene", "selection": "Selection"}.get(scope, "Objects")

    # ---- sections -----------------------------------------------------------
    def _doc_header(self, report: SceneReport) -> "ptk.ReportDoc":
        """Report title and the one-line context: scope, profile, timing."""
        doc = Doc()
        overview = report.overview
        scene = (
            os.path.basename(overview.scene_path)
            if overview and overview.scene_path
            else ""
        )
        doc.heading(f"Scene Info — {scene}" if scene else "Scene Info", level=1)
        manifest = report.manifest
        summary = report.summary
        parts = [
            self._scope_phrase(report),
            f"{manifest.profile.name} profile",
        ]
        if summary.instance_stats.total_instances:
            parts.append(
                self._count(summary.instance_stats.total_instances, "mesh instance")
            )
        parts.append(f"analyzed in {manifest.duration_ms / 1000.0:.1f} s")
        doc.text(" · ".join(parts), tone="dim")
        return doc

    def _doc_overview(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.OVERVIEW])
        o = report.overview
        if o is None:
            return doc.text("Scene overview was not collected.", tone="dim")
        c = o.counts

        def group(*pairs):
            items = [
                self._count(c.get(key, 0), noun, plural)
                for key, noun, plural in pairs
                if c.get(key)
            ]
            return " · ".join(items)

        rows: List[Tuple[str, Any]] = []
        if o.scene_path:
            rows.append(
                (
                    "File",
                    [
                        Doc.file(
                            os.path.dirname(o.scene_path),
                            os.path.basename(o.scene_path),
                        ),
                        f"  ({self._mb(o.file_size_mb)})",
                    ],
                )
            )
        else:
            rows.append(("File", Doc.span("untitled (never saved)", tone="warn")))
        rows.append(
            (
                "Units",
                f"{o.linear_unit} · {o.up_axis.upper()}-up · {o.fps:g} fps",
            )
        )
        rows.append(
            (
                "Frames",
                f"playback {o.playback_range[0]:g}–{o.playback_range[1]:g} · "
                f"animation {o.animation_range[0]:g}–{o.animation_range[1]:g}",
            )
        )
        rows.append(
            (
                "Geometry",
                f"{self._count(c.get('mesh_instances', 0), 'mesh instance')} of "
                + self._count(c.get("mesh_shapes", 0), "mesh shape"),
            )
        )
        dag = group(
            ("transforms", "transform", None),
            ("curves", "curve", None),
            ("locators", "locator", None),
        )
        if dag:
            rows.append(("DAG", dag))
        rig = group(
            ("joints", "joint", None),
            ("skin_clusters", "skin cluster", None),
            ("blend_shapes", "blend shape", None),
            ("constraints", "constraint", None),
        )
        if rig:
            rows.append(("Rigging", rig))
        anim = group(
            ("anim_curves", "anim curve", None), ("expressions", "expression", None)
        )
        if anim:
            rows.append(("Animation", anim))
        shading = group(
            ("materials", "material", None),
            ("shading_engines", "shading engine", None),
            ("file_textures", "file texture", None),
        )
        if shading:
            rows.append(("Shading", shading))
        other = group(
            ("lights", "light", None),
            ("cameras", "camera", None),
            ("display_layers", "display layer", None),
        )
        if other:
            rows.append(("Scene", other))
        if o.namespaces:
            rows.append(("Namespaces", ", ".join(o.namespaces)))
        if o.references:
            refs = [os.path.basename(r) for r in o.references]
            text = ", ".join(refs)
            if o.unloaded_references:
                rows.append(
                    (
                        "References",
                        [
                            text,
                            Doc.span(
                                f"  ({len(o.unloaded_references)} unloaded)",
                                tone="warn",
                            ),
                        ],
                    )
                )
            else:
                rows.append(("References", text))
        if o.unknown_nodes or o.unknown_plugins:
            parts = []
            if o.unknown_nodes:
                parts.append(self._count(o.unknown_nodes, "unknown node"))
            if o.unknown_plugins:
                parts.append("plugins: " + ", ".join(o.unknown_plugins))
            rows.append(("Unknown", Doc.span(" · ".join(parts), tone="warn")))
        return doc.fields(rows)

    def _doc_summary(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.SUMMARY])
        s = report.summary
        if not s.total_meshes:
            return doc.text("No meshes in scope.", tone="warn")
        inst = s.instance_stats
        rows: List[Tuple[str, Any]] = [
            (
                "Meshes",
                f"{self._count(inst.total_instances, 'instance')} of "
                f"{self._count(inst.unique_meshes, 'unique shape')}"
                + (
                    f" ({inst.instanced_shapes:,} instanced)"
                    if inst.instanced_shapes
                    else ""
                ),
            ),
            (
                "Triangles",
                f"{s.total_tris:,} rendered · {s.raw_total_tris:,} unique",
            ),
            ("Vertices", f"{s.total_verts:,} rendered"),
        ]
        m = report.manifest
        if m.materials_collected:
            rows.append(
                (
                    "Draw calls",
                    f"~{s.draw_calls:,} (one per material slot per instance, "
                    "before batching)",
                )
            )
            mats = self._count(s.materials_in_use, "material") + " in use"
            extra = []
            if s.blend_meshes:
                extra.append(
                    f"{self._count(s.blend_meshes, 'mesh', 'meshes')} alpha-blended"
                )
            if s.masked_meshes:
                extra.append(f"{s.masked_meshes:,} alpha-tested")
            rows.append(
                ("Materials", mats + (f" ({', '.join(extra)})" if extra else ""))
            )
        t = report.textures
        if m.textures_collected and t.unique_paths:
            over = t.est_gpu_mb_compressed > t.budget_mb > 0
            memory = Doc.span(
                f"~{self._mb(t.est_gpu_mb_compressed)} GPU (budget {self._mb(t.budget_mb)})",
                tone="error" if over else None,
            )
            rows.append(
                (
                    "Textures",
                    [
                        f"{self._count(t.unique_paths, 'map')} · "
                        f"{t.dim_histogram.get('4k+', 0):,} at 4K+ · "
                        f"{self._mb(t.total_size_mb)} on disk · ",
                        memory,
                    ],
                )
            )
        b = report.budget
        over_parts = [
            f"{self._count(b.meshes_over_tri_threshold, 'mesh', 'meshes')} over "
            "the triangle budget",
        ]
        if m.materials_collected:
            over_parts.append(
                f"{b.meshes_over_slot_threshold:,} over "
                f"{report.manifest.profile.max_slots} material slots"
            )
        rows.append(("Budget", " · ".join(over_parts)))
        doc.fields(rows)
        if s.scene_health_flags:
            doc.items(
                [Doc.span(f, tone="warn") for f in s.scene_health_flags], tone="warn"
            )
        return doc

    def _doc_fix_first(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.FIX_FIRST])
        if not report.fix_actions:
            if not report.assets:
                return doc.text("No meshes in scope.", tone="dim")
            return doc.text(
                "Nothing over budget and no pipeline hazards found.", tone="ok"
            )
        if not report.assets:  # the scene's own hazards, with nothing in scope
            doc.text("No meshes in scope; the scene itself:", tone="dim")
        rows = []
        names = self._display_names(
            t
            for a in report.fix_actions
            for t in a.detail.get("targets", [])[:3]
            if str(t).startswith("|")
        )
        for index, action in enumerate(report.fix_actions, 1):
            targets = action.detail.get("targets", [])
            links = []
            for target in targets[:3]:
                if target in names:  # a DAG path
                    links.append(self._node_link(names[target], target))
                elif action.kind == "blend_materials":  # a material node
                    links.append(self._node_link(target, target))
            cell: List[Any] = [action.message]
            if links:
                cell += [Doc.span("  e.g. ", tone="dim"), Doc.join(links)]
                if len(targets) > len(links):
                    cell.append(
                        Doc.span(f" +{len(targets) - len(links):,} more", tone="dim")
                    )
            rows.append([index, self._severity(action.severity), cell])
        return doc.table(["#", "Severity", "Issue"], rows, align="rll")

    def _doc_pareto(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.PARETO])
        records = report.assets
        if not records:
            return doc.text("No meshes in scope.", tone="dim")
        total = report.summary.total_tris or 1
        ranked = sorted(
            records, key=lambda r: r.mesh.tris * r.instance_count, reverse=True
        )[: self.TABLE_ROWS]
        names = self._display_names(r.transform for r in ranked)
        running = 0
        rows = []
        for index, r in enumerate(ranked, 1):
            rendered = r.mesh.tris * r.instance_count
            running += rendered
            rows.append(
                [
                    index,
                    self._node_link(names[r.transform], r.transform),
                    f"{r.mesh.tris:,}",
                    f"×{r.instance_count:,}",
                    f"{rendered:,}",
                    f"{rendered / total * 100:.1f}%",
                    f"{running / total * 100:.1f}%",
                ]
            )
        top = (
            "the top mesh carries"
            if len(ranked) == 1
            else f"the top {len(ranked)} carry"
        )
        doc.table(
            ["#", "Mesh", "Tris", "Instances", "Rendered", "Share", "Cumulative"],
            rows,
            align="rlrrrrr",
            title=f"Rendered triangles, heaviest first: {top} "
            f"{running / total * 100:.0f}%",
        )
        if report.manifest.materials_collected and report.summary.multi_slot_meshes:
            # Filtered, THEN cut: single-slot meshes out-drawing the multi-slot
            # ones must not push them all off the table.
            ranked = sorted(
                (r for r in records if r.material.slot_count > 1),
                key=lambda r: r.material.draw_calls,
                reverse=True,
            )[: self.TABLE_ROWS]
            names = self._display_names(r.transform for r in ranked)
            calls = report.summary.draw_calls or 1
            doc.table(
                ["Mesh", "Slots", "Instances", "Draw calls", "Share"],
                [
                    [
                        self._node_link(names[r.transform], r.transform),
                        r.material.slot_count,
                        f"×{r.instance_count:,}",
                        f"{r.material.draw_calls:,}",
                        f"{r.material.draw_calls / calls * 100:.1f}%",
                    ]
                    for r in ranked
                ],
                align="lrrrr",
                title="Multi-material meshes by draw calls",
            )
        return doc

    def _doc_offenders(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.OFFENDERS])
        flagged = sorted(
            (r for r in report.assets if r.findings),
            key=lambda r: (r.score * max(1, r.instance_count), r.mesh.tris),
            reverse=True,
        )
        if not flagged:
            if report.assets:
                return doc.text("No mesh-level issues.", tone="ok")
            return doc.text("No meshes in scope.", tone="dim")
        shown = flagged[: self.TABLE_ROWS]
        names = self._display_names(r.transform for r in shown)
        rows = []
        for r in shown:
            issues = Doc.join(
                [
                    Doc.span(f.message, tone=self._SEVERITY_TONES.get(f.severity))
                    for f in r.findings
                ],
                sep="; ",
            )
            rows.append(
                [
                    self._node_link(names[r.transform], r.transform),
                    f"×{r.instance_count:,}",
                    f"{r.mesh.tris:,} / {r.target_tris:,}",
                    issues,
                ]
            )
        rest = len(flagged) - len(shown)
        return doc.table(
            ["Mesh", "Inst", "Tris / budget", "Issues"],
            rows,
            align="lrrl",
            title=f"{self._count(len(flagged), 'mesh', 'meshes')} with issues, "
            "worst first (score × instances)",
            footer=f"… {rest:,} more" if rest > 0 else None,
        )

    def _doc_materials(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.MATERIALS])
        if not report.manifest.materials_collected:
            return doc.text("Material data was not collected.", tone="dim")
        if not report.materials:
            return doc.text("No materials in scope.", tone="dim")
        textures = report.manifest.textures_collected
        rows = []
        for m in report.materials:
            notes = Doc.join(
                [
                    Doc.span(f.message, tone=self._SEVERITY_TONES.get(f.severity))
                    for f in m.findings
                ],
                sep="; ",
            )
            row = [
                self._node_link(m.name, m.name),
                Doc.span(m.node_type, tone="dim"),
                f"{m.mesh_count:,}",
                f"{m.instance_count:,}",
            ]
            if textures:
                row += [
                    f"{len(m.textures):,}",
                    f"{m.max_res:,}" if m.max_res else "–",
                    self._mb(m.gpu_mb) if m.gpu_mb else "–",
                ]
            rows.append(row + [notes])
        headers = ["Material", "Type", "Meshes", "Instances"]
        if textures:
            headers += ["Maps", "Max px", "GPU (est.)"]
        headers.append("Notes")
        return doc.table(
            headers,
            rows,
            align="llrr" + ("rrr" if textures else "") + "l",
            title=f"{self._count(len(report.materials), 'material')} in scope, "
            + ("by texture memory" if textures else "by use"),
        )

    def _doc_textures(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.TEXTURES])
        t = report.textures
        if not report.manifest.textures_collected:
            return doc.text("Texture data was not collected.", tone="dim")
        if not t.unique_paths:
            doc.text("No surface maps in scope.", tone="dim")
        else:
            hist = t.dim_histogram
            doc.fields(
                [
                    (
                        "Memory",
                        f"~{self._mb(t.est_gpu_mb_compressed)} GPU compressed · "
                        f"{self._mb(t.est_gpu_mb)} uncompressed · "
                        f"{self._mb(t.total_size_mb)} on disk",
                    ),
                    (
                        "Resolution",
                        " · ".join(
                            f"{label} {hist.get(key, 0):,}"
                            for key, label in (
                                ("4k+", "4K+"),
                                ("2k", "2K"),
                                ("1k", "1K"),
                                ("512", "512"),
                                ("<512", "<512"),
                            )
                        ),
                    ),
                ]
            )
            if t.downscale_candidates:
                doc.text(
                    f"{self._count(t.downscale_candidates, 'non-detail map')} at 4K+ "
                    "(AO / roughness / metallic ...) would read the same at half "
                    f"resolution: ~{self._mb(t.downscale_savings_mb)} GPU saved.",
                    tone="warn" if t.est_gpu_mb_compressed > t.budget_mb > 0 else None,
                )
            shown = t.heaviest[: self.TABLE_ROWS]
            rest = len(t.heaviest) - len(shown)
            doc.table(
                ["File", "Type", "Size", "Disk", "GPU (est.)", "Materials", "Meshes"],
                [
                    [
                        self._file_link(f.path, os.path.basename(f.path)),
                        f.map_type.replace("_", " "),
                        f"{f.width}×{f.height}"
                        + (f" ×{f.tiles}" if f.tiles > 1 else ""),
                        self._mb(f.size_mb),
                        self._mb(f.gpu_mb),
                        f"{f.material_count:,}",
                        f"{f.mesh_count:,}",
                    ]
                    for f in shown
                ],
                align="llrrrrr",
                title="Heaviest maps by GPU memory",
                footer=f"… {rest:,} more" if rest > 0 else None,
            )
        if t.other:
            doc.text(
                [
                    Doc.span(
                        f"Not counted: {self._count(len(t.other), 'non-surface map')} "
                        "(environment / utility images, e.g. StingrayPBS's IBL cube "
                        "maps and BRDF LUT): ",
                        tone="dim",
                    ),
                    Doc.join(
                        self._file_link(f.path, name)
                        for name, f in {
                            os.path.basename(f.path): f for f in t.other
                        }.items()
                    ),
                ]
            )
        return doc

    def _doc_pipeline(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.PIPELINE])
        if not report.assets:
            doc.text("No meshes in scope.", tone="dim")
            self._doc_scene_hazards(doc, report.overview)
            return doc
        p = report.pipeline
        problems = 0
        if report.manifest.textures_collected:
            missing = p.missing_project + p.missing_presets
            if missing:
                problems += 1
                doc.table(
                    ["Missing file", "Materials"],
                    [
                        [Doc.span(m.path, tone="error"), ", ".join(m.materials)]
                        for m in missing[: self.TABLE_ROWS]
                    ],
                    align="ll",
                    wrap=[0, 1],
                    title=f"Unresolved texture files: {len(missing):,} (Maya's own "
                    "lookup: project root, then the sourceImages rule)",
                )
            bundled = [f for f in report.textures.heaviest if f.bundled]
            if bundled:
                problems += 1
                doc.text(
                    "Read from Maya's install tree, so not travelling with the "
                    f"project: {self._count(len(bundled), 'surface map')}.",
                    tone="warn",
                )
        if p.unassigned_meshes:
            problems += 1
            names = self._display_names(p.unassigned_meshes[: self.TABLE_ROWS])
            doc.text(
                [
                    Doc.span(
                        f"{self._count(len(p.unassigned_meshes), 'mesh', 'meshes')} "
                        "without a material: ",
                        tone="warn",
                    ),
                    Doc.join(self._node_link(names[x], x) for x in names),
                ]
            )
        snap = [r for r in report.assets if r.mesh.uv_snapshot_sets]
        if snap:
            problems += 1
            names = self._display_names(r.transform for r in snap[: self.TABLE_ROWS])
            doc.text(
                [
                    Doc.span(
                        "Leftover _uv_snap_* UV sets on "
                        f"{self._count(len(snap), 'mesh', 'meshes')}: ",
                        tone="error",
                    ),
                    Doc.join(self._node_link(names[x], x) for x in names),
                    Doc.span(
                        f" +{len(snap) - len(names):,} more"
                        if len(snap) > len(names)
                        else "",
                        tone="dim",
                    ),
                ]
            )
        if self._doc_scene_hazards(doc, report.overview):
            problems += 1
        if not problems:
            # Only what this run checked is claimed clean.
            m, files = report.manifest, report.textures.unique_paths
            clean = []
            if m.textures_collected and files:
                clean.append(
                    "the texture file resolves"
                    if files == 1
                    else f"all {files:,} texture files resolve"
                )
            if m.materials_collected:
                clean.append("every mesh has a material")
            if m.mesh_checks_collected:
                clean.append("no leftover UV snapshots")
            if clean:
                text = "; ".join(clean) + "."
                doc.text(text[0].upper() + text[1:], tone="ok")
        return doc

    def _doc_scene_hazards(
        self, doc: "ptk.ReportDoc", overview: Optional[SceneOverview]
    ) -> bool:
        """Scene-wide hazards (unknown nodes / plugins, unloaded references) as
        one warning line on *doc* -- whatever the scope. True when there were any."""
        o = overview
        if not (o and (o.unknown_nodes or o.unknown_plugins or o.unloaded_references)):
            return False
        parts = []
        if o.unknown_nodes or o.unknown_plugins:
            parts.append(
                f"{self._count(o.unknown_nodes, 'unknown node')} / "
                f"{self._count(len(o.unknown_plugins), 'unknown plugin')} "
                "(Scene > Fix > Cleanup Unknown)"
            )
        if o.unloaded_references:
            parts.append(
                self._count(len(o.unloaded_references), "unloaded reference")
                + " (not audited)"
            )
        doc.text(" · ".join(parts), tone="warn")
        return True

    def _doc_assumptions(self, report: SceneReport) -> "ptk.ReportDoc":
        doc = Doc().heading(SceneInfoSection.LABELS[SceneInfoSection.ASSUMPTIONS])
        p = report.manifest.profile
        if p.adaptive_tris:
            budget = (
                f"Adaptive triangle budget: {p.max_tris:,} at a {p.reference_diag:g} cm "
                "world-space diagonal, scaled linearly with size (largest instance), "
                f"floor {p.min_tris:,}."
            )
        else:
            budget = f"Generic triangle budget: a flat {p.max_tris:,} per mesh."
        return doc.items(
            [
                "Rendered counts weigh each unique shape by its instances in scope; "
                "unique counts measure each shape once.",
                "Draw calls: one per material slot per instance, before engine "
                "batching or GPU instancing.",
                budget,
                "GPU memory: a block-compressed estimate with a full mip chain -- "
                "BC4 / BC1 (0.5 B/px) for single-channel and RGB maps, BC5 / BC7 "
                "(1 B/px) for normal maps and maps with alpha. Uncompressed RGBA8 "
                "is shown for reference.",
                "Surface maps are classified by the ptk map taxonomy (filename "
                "first, shader slot second); images neither names are listed but "
                "not counted.",
                "Texture costs are counted once per file and judged per material, "
                "not per mesh that wears it; a UDIM set costs every tile, a frame "
                "sequence one frame. Loose AO / roughness / metallic maps are "
                "costed as the separate files they are; a GLB export packs them "
                "into one ORM map.",
            ]
        )
